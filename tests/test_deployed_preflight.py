"""Exercise the real hourly entrypoint with an offline Worker transport."""
import importlib.util
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error


class DeployedPreflightTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            'deployed_preflight', Path(__file__).parents[1] / 'cloudflare/check_deployed.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.health = {'source_commit': 'tested', 'ready': True,
                       'runtime': 'cloudflare-workers', 'scheduler': 'github-actions', 'scheduled': True}

    def response(self, **changes):
        return io.BytesIO(json.dumps({**self.health, **changes}).encode())

    def error(self, code):
        body = io.BytesIO(b'private upstream body must not be printed')
        return urllib.error.HTTPError('https://rep0rter-engine.sky-hong.workers.dev/healthz', code, 'failure', {}, body)

    def execute(self, effects):
        opened = patch.object(self.module.urllib.request, 'urlopen', side_effect=effects)
        with opened as transport, patch.object(self.module.time, 'sleep') as sleep, \
                patch.dict('os.environ', {'GITHUB_SHA': 'tested'}, clear=True), patch('sys.stdout', new_callable=io.StringIO) as stdout:
            self.module.main()
            return transport, sleep, stdout.getvalue()

    def test_natural_http500_recovers_then_requires_same_deployed_worker(self):
        error = self.error(500)
        transport, sleep, output = self.execute([error, self.response()])
        self.assertEqual(transport.call_count, 2)
        sleep.assert_called_once_with(2)
        self.assertTrue(error.fp.closed)
        self.assertNotIn('private upstream', output)
        self.assertIn('matches the ready native Worker', output)
        for call in transport.call_args_list:
            self.assertEqual(call.args[0].full_url, 'https://rep0rter-engine.sky-hong.workers.dev/healthz')
            self.assertEqual(call.args[0].get_method(), 'GET')
            self.assertEqual(call.kwargs, {'timeout': 10})

    def test_recovered_response_cannot_bypass_commit_or_scheduler_guards(self):
        for changes, message in [({'source_commit': 'old'}, 'Deployment drift'),
                                 ({'ready': False}, 'not ready'),
                                 ({'runtime': 'other'}, 'not ready'),
                                 ({'scheduler': 'native-cron'}, 'GitHub Actions'),
                                 ({'scheduled': False}, 'GitHub Actions')]:
            with self.subTest(changes=changes), self.assertRaisesRegex(RuntimeError, message):
                self.execute([self.error(500), self.response(**changes)])

    def test_persistent_worker_failure_exhausts_exactly_three_gets(self):
        errors = [self.error(code) for code in (500, 502, 503)]
        with patch.object(self.module.urllib.request, 'urlopen', side_effect=errors) as transport, \
                patch.object(self.module.time, 'sleep') as sleep, patch('sys.stdout', new_callable=io.StringIO) as stdout:
            with self.assertRaisesRegex(RuntimeError, r'after 3 attempts \(HTTP 503\)'):
                self.module.main()
            self.assertEqual(transport.call_count, 3)
            self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 5])
            self.assertNotIn('private upstream', stdout.getvalue())
        self.assertTrue(all(error.fp.closed for error in errors))

    def test_permanent_http_errors_stop_without_retry(self):
        for code in (401, 403, 404, 429):
            with self.subTest(code=code), patch.object(self.module.urllib.request, 'urlopen', side_effect=self.error(code)) as transport, \
                    patch.object(self.module.time, 'sleep') as sleep:
                with self.assertRaisesRegex(RuntimeError, f'HTTP {code}'):
                    self.module.main()
                self.assertEqual(transport.call_count, 1)
                sleep.assert_not_called()

    def test_transient_timeout_recovers(self):
        for error in (TimeoutError(), urllib.error.URLError(TimeoutError()), urllib.error.URLError(ConnectionResetError())):
            with self.subTest(error=type(error).__name__):
                transport, _, _ = self.execute([error, self.response()])
                self.assertEqual(transport.call_count, 2)

    def test_unknown_transport_error_is_not_retried_or_disclosed(self):
        for reason in ('private reason', ConnectionRefusedError('private refusal'), BrokenPipeError('private pipe')):
            with self.subTest(reason=type(reason).__name__), patch.object(self.module.urllib.request, 'urlopen', side_effect=urllib.error.URLError(reason)) as transport, \
                    patch.object(self.module.time, 'sleep') as sleep:
                with self.assertRaisesRegex(RuntimeError, '^Worker health preflight transport failed.$'):
                    self.module.main()
                self.assertEqual(transport.call_count, 1)
                sleep.assert_not_called()

    def test_connection_reset_during_body_read_closes_response_and_recovers(self):
        class ResetBody(io.BytesIO):
            def read(self, *args):
                raise ConnectionResetError('private reset details')
        response = ResetBody()
        transport, sleep, output = self.execute([response, self.response()])
        self.assertTrue(response.closed)
        self.assertEqual(transport.call_count, 2)
        sleep.assert_called_once_with(2)
        self.assertNotIn('private reset', output)

    def test_persistent_direct_body_resets_exhaust_get_limit(self):
        class ResetBody(io.BytesIO):
            def read(self, *args):
                raise ConnectionResetError('private reset details')
        responses = [ResetBody() for _ in range(3)]
        with patch.object(self.module.urllib.request, 'urlopen', side_effect=responses) as transport, \
                patch.object(self.module.time, 'sleep'), patch('sys.stdout', new_callable=io.StringIO):
            with self.assertRaisesRegex(RuntimeError, 'after 3 attempts'):
                self.module.main()
            self.assertEqual(transport.call_count, 3)
        self.assertTrue(all(response.closed for response in responses))

    def test_invalid_or_oversized_success_body_never_enters_reporting(self):
        for body in (b'invalid-json', b'x' * 65537):
            with self.subTest(length=len(body)), patch.object(self.module.urllib.request, 'urlopen', return_value=io.BytesIO(body)) as transport, \
                    patch.object(self.module.time, 'sleep') as sleep:
                with self.assertRaises((json.JSONDecodeError, RuntimeError)):
                    self.module.main()
                self.assertEqual(transport.call_count, 1)
                sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
