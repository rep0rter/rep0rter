from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from cloudflare.check_threads import check
from rep0rter import cli, threads_delivery
from rep0rter.config import Config
from rep0rter.publishers import threads


@pytest.mark.parametrize('status,error,reason', [
    (400, {'code': 190, 'error_subcode': 463, 'message': 'sensitive details'}, 'token_expired'),
    (400, {'code': 190, 'message': 'Session has expired. secret-token'}, 'token_expired'),
    (401, {'code': 190, 'message': 'Invalid secret-token'}, 'token_invalid'),
    (403, {'code': 10, 'message': 'secret-token'}, 'permission_denied'),
    (429, {'message': 'secret-token'}, 'rate_limited'),
    (400, {'message': 'secret-token', 'code': 'secret-token', 'error_subcode': {'private': 'text'}}, 'request_rejected'),
])
def test_api_rejection_retains_only_safe_diagnostic_fields(monkeypatch, status, error, reason):
    response = SimpleNamespace(status_code=status, headers={'Retry-After': '120'},
                               json=lambda: {'error': error})
    monkeypatch.setattr(threads.requests, 'request', lambda *args, **kwargs: response)
    with pytest.raises(threads.ThreadsRejected) as rejected:
        threads.get_account(replace(Config(), threads_access_token='secret-token'))
    exc = rejected.value
    assert exc.status == status and exc.reason == reason and exc.retry_after == 120
    assert 'secret-token' not in str(exc)
    assert 'sensitive details' not in str(exc)
    assert type(exc.code) is int or exc.code is None
    assert type(exc.subcode) is int or exc.subcode is None


def test_non_json_rejection_remains_a_definite_rejection(monkeypatch):
    def invalid():
        raise ValueError('private response body')
    monkeypatch.setattr(threads.requests, 'request', lambda *args, **kwargs:
                        SimpleNamespace(status_code=403, headers={}, json=invalid))
    with pytest.raises(threads.ThreadsRejected) as rejected:
        threads.get_account(replace(Config(), threads_access_token='secret-token'))
    assert rejected.value.reason == 'permission_denied'
    assert 'private' not in str(rejected.value)


@pytest.mark.parametrize('remote_id,expected', [('123', 'ok'), ('456', 'blocked')])
def test_authentication_check_only_reads_identity(monkeypatch, tmp_path, remote_id, expected):
    calls = []
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return SimpleNamespace(status_code=200, headers={}, json=lambda: {'id': remote_id, 'username': 'private-name'})
    monkeypatch.setattr(threads.requests, 'request', request)
    cfg = replace(Config(), data_dir=tmp_path, threads_access_token='secret-token', threads_user_id='123')
    result = check(cfg)
    assert result['authentication'] == expected
    assert len(calls) == 1 and calls[0][0] == 'GET'
    assert calls[0][1] == threads.API + '/me'
    assert calls[0][2]['headers'] == {'Authorization': 'Bearer secret-token'}
    assert calls[0][2]['params'] == {'fields': 'id,username'}
    assert calls[0][2]['allow_redirects'] is False
    assert not list(tmp_path.iterdir())
    assert 'secret-token' not in json.dumps(result) and 'private-name' not in json.dumps(result)


def test_expired_token_diagnostic_is_actionable_without_disclosing_response(monkeypatch):
    def rejected(cfg):
        raise threads.ThreadsRejected(400, code=190, subcode=463, reason='token_expired')
    monkeypatch.setattr(threads, 'get_account', rejected)
    cfg = replace(Config(), threads_access_token='secret-token', threads_user_id='123')
    assert check(cfg) == {'authentication': 'blocked', 'reason': 'token_expired',
                          'http_status': 400, 'error_code': 190, 'error_subcode': 463}


def test_missing_configuration_does_not_call_api(monkeypatch):
    monkeypatch.setattr(threads, 'get_account', lambda cfg: pytest.fail('no request without a token'))
    assert check(replace(Config(), threads_access_token=None, threads_user_id='123'))['reason'] == 'missing_configuration'


def test_transport_errors_do_not_expose_exception_text(monkeypatch):
    def failed(cfg):
        raise RuntimeError('secret-token private response body')
    monkeypatch.setattr(threads, 'get_account', failed)
    cfg = replace(Config(), threads_access_token='secret-token', threads_user_id='123')
    assert check(cfg) == {'authentication': 'unknown', 'reason': 'transport_or_response_error'}


def test_reporting_warns_about_authentication_failure_and_still_continues(monkeypatch, caplog, capsys):
    monkeypatch.setattr(threads_delivery, 'enqueue_missing', lambda *args: None)
    def failed(*args):
        raise threads.ThreadsRejected(400, code=190, subcode=463, reason='token_expired')
    monkeypatch.setattr(threads_delivery, 'deliver_pending', failed)
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    cfg = replace(Config(), threads_enabled=True, threads_access_token='secret-token')
    assert cli._deliver_threads(cfg, None) == {}
    assert 'token_expired' in caplog.text and 'HTTP 400' in caplog.text
    out = capsys.readouterr().out
    assert '::warning title=Threads delivery blocked::' in out
    assert 'secret-token' not in caplog.text + out
