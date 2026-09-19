import importlib.util
import json
from pathlib import Path
import sqlite3
import pytest

from rep0rter.runtime import connect, services, DurabilityError

spec = importlib.util.spec_from_file_location('github_runner', Path(__file__).parents[1] / 'cloudflare/github_runner.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


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
