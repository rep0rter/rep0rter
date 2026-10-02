import importlib.util
import json
from pathlib import Path
import sqlite3
import pytest

from rep0rter.runtime import connect, services, DurabilityError

spec = importlib.util.spec_from_file_location('github_runner', Path(__file__).parents[1] / 'cloudflare/github_runner.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize('upload_fails', [False, True])
def test_publication_logs_exact_safe_capacity_before_upload(tmp_path, capsys, upload_fails):
    import io
    import zipfile
    runtime = module.RunnerRuntime('https://example.test', 'PRIVATE-TOKEN', tmp_path, 'PRIVATE-LEASE')
    runtime.pages = [b'x' * 4096, b'y' * 4096]
    files = {'site/index.html': b'<html>PRIVATE-CONTENT</html>',
             'site/feed.xml': b'<rss/>', 'site/cards/private-name.png': b'png' * 100,
             'image-cache/private-name.bin': b'cached' * 200,
             'exclusions.json': b'{"private-policy":true}'}
    for name, data in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    captured = {}
    def request(action, body, content_type):
        # The evidence must survive the same size rejection it diagnoses.
        captured['logs'] = capsys.readouterr().out
        captured['body'] = body
        assert action == 'publish' and content_type == 'application/zip'
        if upload_fails:
            raise RuntimeError('Worker publish failed with HTTP 413')
    runtime.request = request
    if upload_fails:
        with pytest.raises(RuntimeError, match='HTTP 413'):
            runtime.publish_site(None)
    else:
        runtime.publish_site(None)
    logs = captured['logs']
    evidence = json.loads(logs.split('Capacity evidence: ', 1)[1].splitlines()[0])
    assert evidence['usage'] == {
        'database_bytes': 8192,
        'zip_bytes': len(captured['body']),
        'expanded_bytes': sum(map(len, files.values())),
        'public_asset_bytes': sum(len(data) for name, data in files.items() if name.startswith('site/')),
        'public_asset_count': 3,
        'largest_file_bytes': 1200,
    }
    with zipfile.ZipFile(io.BytesIO(captured['body'])) as archive:
        assert {name: archive.read(name) for name in archive.namelist()} == files
    assert '::warning' not in logs
    assert all(secret not in logs for secret in ('PRIVATE', 'private-name', 'private-policy', str(tmp_path)))


@pytest.mark.parametrize('name', module.CAPACITY_LIMITS)
def test_capacity_warning_starts_at_eighty_percent_without_changing_limits(name, capsys):
    limit = module.CAPACITY_LIMITS[name]
    threshold = (limit * 4 + 4) // 5
    usage = dict.fromkeys(module.CAPACITY_LIMITS, 0)
    usage[name] = threshold - 1
    module.report_capacity(usage)
    assert '::warning' not in capsys.readouterr().out
    for size in (threshold, limit + 1):
        usage[name] = size
        module.report_capacity(usage)
        output = capsys.readouterr().out
        warnings = [line for line in output.splitlines() if line.startswith('::warning')]
        assert len(warnings) == 1 and f'{name} uses {size} of {limit}' in warnings[0]
        assert json.loads(output.split('Capacity evidence: ', 1)[1].splitlines()[0])['limits'][name] == limit


def test_commits_are_remote_before_return_and_unchanged_pages_are_omitted(tmp_path):
    import base64
    runtime = module.RunnerRuntime('https://example.test', 'private', tmp_path, 'lease')
    remote = []
    calls = []
    def request(action, body=b'', content_type='application/json'):
        assert action == 'checkpoint'
        data = json.loads(body)
        calls.append(data)
        remote.extend([b''] * max(0, data['count'] - len(remote)))
        del remote[data['count']:]
        for number, value in data['pages']:
            remote[number] = base64.b64decode(value)
        return json.dumps({'committed': data['sequence']}).encode()
    runtime.request = request
    token = services.set(runtime)
    try:
        db = connect(tmp_path / 'db.sqlite')
        db.execute('CREATE TABLE sent(id INTEGER PRIMARY KEY)')
        with db:
            db.execute('INSERT INTO sent VALUES (1)')
        snapshot = sqlite3.connect(':memory:')
        snapshot.deserialize(b''.join(remote))
        assert snapshot.execute('SELECT * FROM sent').fetchall() == [(1,)]
        before = len(calls)
        db.commit()
        assert len(calls) == before
        assert [c['sequence'] for c in calls] == list(range(1, len(calls) + 1))
        db.close()
    finally:
        services.reset(token)


def test_threads_secret_overlay_preserves_telegram_configuration(monkeypatch):
    for name in ('TELEGRAM_BOT_TOKEN', 'REP0RTER_THREADS_ENABLED', 'REP0RTER_THREADS_ACCESS_TOKEN',
                 'REP0RTER_THREADS_USER_ID', 'REP0RTER_THREADS_BATCH_SIZE'):
        monkeypatch.setenv(name, '')
    monkeypatch.setenv('REP0RTER_CONFIG', json.dumps({'TELEGRAM_BOT_TOKEN': 'telegram-test',
                                                     'REP0RTER_THREADS_ENABLED': '0'}))
    monkeypatch.setenv('THREADS_ENABLED', '1')
    monkeypatch.setenv('THREADS_ACCESS_TOKEN', 'threads-test')
    monkeypatch.setenv('THREADS_USER_ID', 'account-42')
    monkeypatch.setenv('THREADS_BATCH_SIZE', '10')
    module.install_configuration()
    import os
    assert os.environ['TELEGRAM_BOT_TOKEN'] == 'telegram-test'
    assert os.environ['REP0RTER_THREADS_ENABLED'] == '1'
    assert os.environ['REP0RTER_THREADS_ACCESS_TOKEN'] == 'threads-test'
    assert os.environ['REP0RTER_THREADS_USER_ID'] == 'account-42'


def test_threads_activation_requires_complete_bounded_settings(monkeypatch):
    monkeypatch.setenv('REP0RTER_CONFIG', '{}')
    monkeypatch.setenv('THREADS_ENABLED', '1')
    monkeypatch.setenv('THREADS_ACCESS_TOKEN', '')
    with pytest.raises(ValueError, match='Threads requires'):
        module.install_configuration()


def test_model_overlay_replaces_legacy_model_without_replacing_credentials(monkeypatch):
    import os
    from rep0rter.config import Config
    for name in ('AI_MODEL', 'AI_BASE_URL', 'AI_API_KEY', 'TELEGRAM_BOT_TOKEN'):
        monkeypatch.setenv(name, '')
    monkeypatch.delenv('THREADS_ENABLED', raising=False)
    monkeypatch.setenv('REP0RTER_CONFIG', json.dumps({
        'AI_MODEL': 'legacy-model', 'AI_BASE_URL': 'https://example.test/v1',
        'AI_API_KEY': 'private-test', 'TELEGRAM_BOT_TOKEN': 'telegram-test',
    }))
    monkeypatch.setenv('AI_MODEL', 'gpt-6-luna')
    module.install_configuration()
    assert Config().ai_model == 'gpt-6-luna'
    assert os.environ['AI_BASE_URL'] == 'https://example.test/v1'
    assert os.environ['AI_API_KEY'] == 'private-test'
    assert os.environ['TELEGRAM_BOT_TOKEN'] == 'telegram-test'


@pytest.mark.parametrize('override', [None, ''])
def test_absent_model_overlay_preserves_explicit_secret_model(monkeypatch, override):
    from rep0rter.config import Config
    monkeypatch.delenv('THREADS_ENABLED', raising=False)
    monkeypatch.setenv('REP0RTER_CONFIG', json.dumps({'AI_MODEL': 'custom-model'}))
    monkeypatch.setenv('AI_MODEL', '')
    if override is None:
        monkeypatch.delenv('AI_MODEL')
    module.install_configuration()
    assert Config().ai_model == 'custom-model'


def test_model_default_and_override_are_evaluated_at_configuration_time(monkeypatch):
    from rep0rter.config import Config
    monkeypatch.delenv('AI_MODEL', raising=False)
    assert Config().ai_model == 'gpt-6-luna'
    assert not Config().llm_enabled
    monkeypatch.setenv('AI_MODEL', 'gpt-6-sol')
    assert Config().ai_model == 'gpt-6-sol'


def test_unacknowledged_checkpoint_poisoning_prevents_further_work(tmp_path):
    runtime = module.RunnerRuntime('https://example.test', 'private', tmp_path, 'lease')
    runtime.request = lambda *a: b'{"committed":0}'
    token = services.set(runtime)
    try:
        db = connect(tmp_path / 'db.sqlite')
        with pytest.raises(DurabilityError):
            db.execute('CREATE TABLE sent(id INTEGER PRIMARY KEY)')
        assert runtime.poisoned and runtime.sequence == 0 and runtime.pages == []
        with pytest.raises(DurabilityError):
            db.execute('SELECT 1')
        db.close()
    finally:
        services.reset(token)


def test_worker_fences_expired_leases_and_rejects_replayed_mutations(tmp_path, monkeypatch):
    import asyncio
    import base64
    import sys
    import time
    from types import SimpleNamespace
    class Response:
        def __init__(self, body=None, status=200, **kw): self.body, self.status = body, status
        @classmethod
        def json(cls, body, **kw): return cls(body, **kw)
    monkeypatch.setitem(sys.modules, 'workers', SimpleNamespace(Response=Response))
    monkeypatch.setitem(sys.modules, 'revision', SimpleNamespace(SOURCE_COMMIT='current'))
    monkeypatch.setitem(sys.modules, 'pyodide', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'pyodide.ffi', SimpleNamespace(to_js=lambda x:x))
    api_spec=importlib.util.spec_from_file_location('runner_api_test', Path(__file__).parents[1]/'cloudflare/src/runner_api.py')
    api=importlib.util.module_from_spec(api_spec);api_spec.loader.exec_module(api)
    conn=sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE db_pages(number INTEGER PRIMARY KEY,data BLOB)')
    meta={'runner_lease':'lease','runner_until':str(time.time()+100),'runner_sequence':'0','imported':'true'}
    runtime=SimpleNamespace(pages=[b'a'*4096], sql=SimpleNamespace(exec=lambda query,*params:conn.execute(query,params)),
        meta=lambda key,default=None:meta.get(key,default),set_meta=lambda key,value:meta.update({key:str(value)}))
    runtime.transaction=lambda callback: callback()
    owner=SimpleNamespace(runtime=runtime,env=SimpleNamespace(RUNNER_TOKEN='secret',RUNNER_ENABLED='true'))
    class Request:
        method='POST'
        headers={'Authorization':'Bearer secret','X-Runner-Lease':'lease'}
        def __init__(self,data):self.data=data
        async def bytes(self):return json.dumps(self.data).encode()
    data={'sequence':1,'count':1,'pages':[[0,base64.b64encode(b'b'*4096).decode()]]}
    result=asyncio.run(api.handle(owner,Request(data),'/__runner/checkpoint'))
    assert result.status==200 and runtime.pages==[b'b'*4096]
    assert asyncio.run(api.handle(owner,Request(data),'/__runner/checkpoint')).status==200
    data['pages'][0][1]=base64.b64encode(b'c'*4096).decode()
    assert asyncio.run(api.handle(owner,Request(data),'/__runner/checkpoint')).status==409
    meta['runner_until']='0';data['sequence']=2
    assert asyncio.run(api.handle(owner,Request(data),'/__runner/checkpoint')).status==409
    assert runtime.pages==[b'b'*4096]
    assert asyncio.run(api.handle(owner,Request({'source_commit':'old'}),'/__runner/start')).status==409


def test_additional_public_feeds_preserve_configured_sources_and_credentials(monkeypatch):
    import os
    monkeypatch.delenv('THREADS_ENABLED', raising=False)
    monkeypatch.setenv('REP0RTER_FEEDS', '')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', '')
    monkeypatch.setenv('REP0RTER_CONFIG', json.dumps({
        'REP0RTER_FEEDS': 'https://example.test/feed,https://slack-archive-2fl.pages.dev/',
        'TELEGRAM_BOT_TOKEN': 'telegram-test',
    }))
    monkeypatch.setenv('REPORT_ADDITIONAL_FEEDS', ' https://slack-archive-2fl.pages.dev/ ,https://another.test/rss')
    module.install_configuration()
    assert os.environ['REP0RTER_FEEDS'] == ('https://example.test/feed,'
        'https://slack-archive-2fl.pages.dev/,https://another.test/rss')
    assert os.environ['TELEGRAM_BOT_TOKEN'] == 'telegram-test'


def test_public_cfj_backfill_override_preserves_general_collection_days(monkeypatch):
    import os
    monkeypatch.delenv('THREADS_ENABLED', raising=False)
    monkeypatch.setenv('REP0RTER_CONFIG', json.dumps({
        'REP0RTER_CFJ_COLLECT_DAYS': '2', 'REP0RTER_COLLECT_DAYS': '2'}))
    monkeypatch.setenv('REPORT_CFJ_COLLECT_DAYS', '90')
    monkeypatch.setenv('REP0RTER_CFJ_COLLECT_DAYS', '')
    monkeypatch.setenv('REP0RTER_COLLECT_DAYS', '')
    module.install_configuration()
    assert os.environ['REP0RTER_CFJ_COLLECT_DAYS'] == '90'
    assert os.environ['REP0RTER_COLLECT_DAYS'] == '2'


@pytest.mark.parametrize('days', ['0', '366', 'nonsense'])
def test_public_cfj_backfill_rejects_invalid_days(monkeypatch, days):
    monkeypatch.setenv('REP0RTER_CONFIG', '{}')
    monkeypatch.setenv('REPORT_CFJ_COLLECT_DAYS', days)
    with pytest.raises(ValueError):
        module.install_configuration()
