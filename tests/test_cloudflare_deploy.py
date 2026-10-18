"""Native deployment prerequisite and ordering checks without external tools."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('cloudflare_deployment',
                                             Path(__file__).parents[1] / 'cloudflare/deploy.py')
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)
SHA = 'a' * 40


class DeploymentPrerequisites(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.version = b'uv 0.12.3 (release 2026-08-07)\n'
        self.failure = None
        self.missing = None
        self.dirty = False
        self.remote = SHA
        self.prepared = False
        self.changed_main = False
        self.runs = [dict(id=1, head_sha=SHA, head_branch='main', event='push',
                          head_repository={'full_name': 'rep0rter/rep0rter'},
                          status='completed', conclusion='success')]
        self.which = self.enterContext(patch.object(deployment.shutil, 'which', side_effect=self.find_tool))
        self.run = self.enterContext(patch.object(deployment.subprocess, 'run', side_effect=self.run_tool))
        self.output = self.enterContext(patch.object(deployment, 'output', side_effect=self.read_gate))

    def find_tool(self, name):
        return None if name == self.missing else '/tools/' + name

    def run_tool(self, argv, **kwargs):
        if argv == ['/tools/uv', '--version']:
            self.events.append('uv-version')
            if self.failure:
                raise self.failure
            return subprocess.CompletedProcess(argv, 0, self.version)
        if argv == [sys.executable, str(deployment.root / 'cloudflare/prepare.py')]:
            self.events.append('prepare'); self.prepared = True
        elif argv[0] == 'npm':
            self.events.append('frontend')
        elif argv[0] == 'pywrangler':
            self.events.append('engine')
        else:
            self.fail('unexpected external tool')
        return subprocess.CompletedProcess(argv, 0)

    def read_gate(self, *command):
        self.events.append(command[:2])
        if command == ('git', 'status', '--porcelain'):
            return ' M tracked-file' if self.dirty else ''
        if command == ('git', 'rev-parse', 'HEAD'):
            return SHA
        if command == ('git', 'ls-remote', 'origin', 'refs/heads/main'):
            return ('b' * 40 if self.prepared and self.changed_main else self.remote) + '\trefs/heads/main'
        if command[:2] == ('gh', 'api'):
            return json.dumps({'workflow_runs': self.runs})
        self.fail('unexpected external gate')

    def invoke(self, production=True):
        args = ['deploy.py'] + (['--production'] if production else [])
        with patch.object(sys, 'argv', args), contextlib.redirect_stdout(io.StringIO()):
            deployment.main()

    def refused_before_preparation(self):
        errors = io.StringIO()
        with contextlib.redirect_stderr(errors), self.assertRaises(SystemExit) as result:
            self.invoke()
        self.assertEqual(result.exception.code, 2)
        self.assertNotIn('prepare', self.events)
        self.assertNotIn('frontend', self.events)
        self.assertNotIn('engine', self.events)
        return errors.getvalue()

    def test_missing_tools_stop_before_any_gates_or_preparation(self):
        for tool in ('git', 'gh', 'node', 'npm', 'npx', 'pywrangler', 'uv'):
            with self.subTest(tool=tool):
                self.missing = tool
                self.assertIn('Missing deployment tools: ' + tool, self.refused_before_preparation())
                self.run.assert_not_called()
                self.output.assert_not_called()

    def test_old_uv_stops_before_preparation_or_frontend(self):
        for version in (b'uv 0.5.23\n', b'uv 0.11.99\n', b'uv 0.12.2\n'):
            with self.subTest(version=version):
                self.version = version
                self.assertIn('uv >= 0.12.3', self.refused_before_preparation())
                self.output.assert_not_called()

    def test_supported_tools_preserve_all_gates_and_frontend_first_order(self):
        self.invoke()
        self.assertEqual(self.events, ['uv-version', ('git', 'status'), ('git', 'rev-parse'),
                                      ('git', 'ls-remote'), ('gh', 'api'), 'prepare',
                                      ('git', 'ls-remote'), 'frontend', 'engine'])
        self.assertEqual(self.run.call_args_list[0].kwargs,
                         dict(check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, timeout=5))
        frontend = self.run.call_args_list[2]
        self.assertEqual(frontend.args[0], ['npm', 'exec', '--yes', '--package=wrangler@4.135.0',
                                         '--', 'wrangler', 'deploy', '--config', 'wrangler-web-production.jsonc'])
        self.assertEqual(self.run.call_args_list[3].args[0],
                         ['pywrangler', 'deploy', '--config', 'wrangler-production.jsonc'])

    def test_preview_keeps_its_existing_config_selection(self):
        self.invoke(production=False)
        self.assertEqual(self.run.call_args_list[2].args[0][-1], 'wrangler-web.jsonc')
        self.assertEqual(self.run.call_args_list[3].args[0][-1], 'wrangler.jsonc')

    def test_newer_uv_and_plain_version_output_are_supported(self):
        self.version = b'uv 0.13.0\n'
        self.invoke()
        self.assertEqual(self.events[-2:], ['frontend', 'engine'])

    def test_uv_timeout_nonzero_or_os_failure_stops_without_raw_error(self):
        canary = 'PRIVATE_ARG_ENV_CANARY'
        for failure in (subprocess.TimeoutExpired(canary, 5, output=canary),
                        subprocess.CalledProcessError(1, canary, output=canary),
                        OSError(canary)):
            with self.subTest(failure=type(failure).__name__):
                self.failure = failure
                errors = self.refused_before_preparation()
                self.assertIn('Could not check uv version', errors)
                self.assertNotIn(canary, errors)
                self.output.assert_not_called()

    def test_malformed_prerelease_or_oversized_output_fails_closed(self):
        for output in (b'', b'not uv 0.12.3', b'uv 0.12.3rc1\n', b'uv 0.12\n',
                       b'uv 0.12.3\nPRIVATE_CANARY', b'x' * 257):
            with self.subTest(output=output[:24]):
                self.version = output
                errors = self.refused_before_preparation()
                self.assertIn('Unrecognized uv version', errors)
                self.assertNotIn('PRIVATE_CANARY', errors)
                self.output.assert_not_called()

    def test_dirty_checkout_still_refuses(self):
        self.dirty = True
        self.assertIn('Commit all changes', self.refused_before_preparation())

    def test_other_remote_main_still_refuses(self):
        self.remote = 'b' * 40
        self.assertIn('Only the current remote main', self.refused_before_preparation())

    def test_latest_ci_must_still_pass_for_exact_main(self):
        self.runs.append(dict(self.runs[0], id=2, conclusion='failure'))
        self.assertIn('latest Offline tests push run', self.refused_before_preparation())

    def test_main_change_after_prepare_still_blocks_frontend(self):
        self.changed_main = True
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.invoke()
        self.assertIn('prepare', self.events)
        self.assertNotIn('frontend', self.events)
        self.assertNotIn('engine', self.events)


if __name__ == '__main__':
    unittest.main()
