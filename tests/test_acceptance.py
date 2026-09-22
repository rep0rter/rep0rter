import importlib.util
import json
from pathlib import Path

import pytest

from rep0rter.acceptance import report
from rep0rter.store import Store


def test_observation_requires_real_dates_and_keeps_unknown_misses(tmp_path, monkeypatch):
    monkeypatch.setenv('REP0RTER_COLLECT_DAILY_BUDGET', '1800')
    start = 1700000000
    with Store(tmp_path / 'db') as store:
        for day in range(15):
            item = {'last_attempt_at': start + day * 86400, 'healthy': day != 3,
                    'metrics': {'requests': 10, 'new_events': 2}}
            store.set_kv(f'collector_metrics:{day}', json.dumps(item))
        store.set_kv('collector_health', json.dumps({'sources': {'slack': {
            'healthy': False, 'failed_channels': 1, 'reasons': {'private': 'credential-secret'}},
            'secret-url': {'error': 'credential-secret'}}}))
        result = report(store, start + 14 * 86400)
        assert result['collection']['two_week_observation_complete']
        assert result['collection']['degraded_runs'] == 1
        assert result['collection']['totals']['requests'] == 150
        assert result['collection']['missed_events'] is None
        assert not result['editorial']['observation_complete']
        assert result['limits']['http_requests_per_utc_day'] == 1800
        assert 'credential-secret' not in json.dumps(result) and 'secret-url' not in json.dumps(result)
        assert not report(store, start + 3 * 86400)['collection']['two_week_observation_complete']


def test_repeated_same_day_observations_do_not_satisfy_window(tmp_path):
    with Store(tmp_path / 'db') as store:
        for index in range(100):
            store.set_kv(f'collector_metrics:{index}', json.dumps({'last_attempt_at': 1700000000 + index}))
        result = report(store, 1700000200)
        assert not result['collection']['two_week_observation_complete']
        assert result['collection']['observed_utc_dates'] == 1


def test_deployment_preflight_keeps_commit_and_scheduler_guards(monkeypatch):
    spec = importlib.util.spec_from_file_location('check_deployed', Path(__file__).parents[1] / 'cloudflare/check_deployed.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    health = {'source_commit': 'tested', 'ready': True, 'runtime': 'cloudflare-workers',
              'scheduler': 'github-actions', 'scheduled': True}
    module.check('tested', health)
    import io
    seen = []
    def response(request, timeout):
        seen.append(request.full_url)
        assert request.get_header('User-agent') == 'rep0rter-github-runner/1.0'
        return io.BytesIO(json.dumps(health).encode())
    monkeypatch.setattr(module.urllib.request, 'urlopen', response)
    monkeypatch.setenv('GITHUB_SHA', 'tested')
    module.main()
    assert seen == ['https://rep0rter-engine.sky-hong.workers.dev/healthz']
    with pytest.raises(RuntimeError, match='Actions commit new, Worker commit tested'):
        module.check('new', health)
    with pytest.raises(RuntimeError, match='GitHub Actions'):
        module.check('tested', {**health, 'scheduler': 'native-cron'})
    with pytest.raises(RuntimeError, match='not ready'):
        module.check('tested', {**health, 'ready': False})
