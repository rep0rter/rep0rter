"""Synthetic recovery at the native/runner and engine/frontend boundaries."""
import asyncio
import base64
import json
import sqlite3
from types import SimpleNamespace

import pytest

from rep0rter import policy
from rep0rter.config import Config
from rep0rter.runtime import DurabilityError, services
from rep0rter.store import Store
from test_native_publication import native, populate, png
from test_runner_transfer import engine


def test_expired_runner_refreshes_warm_native_store_without_losing_lease_state(native, tmp_path):
    runtime, engine = native.runtime, native.engine
    cfg = Config(data_dir=runtime.root)
    populate(cfg, 1)
    token = services.set(runtime)
    try:
        with Store(cfg.db_path):
            pass
    finally:
        services.reset(token)
    initial = cfg.db_path.read_bytes()
    engine.runtime.pages = runtime.pages[:]
    runner_db = tmp_path / 'runner.sqlite'
    runner_db.write_bytes(initial)
    with sqlite3.connect(runner_db) as conn:
        conn.execute("UPDATE posts SET headline='NEW CHECKPOINTED HEADLINE' WHERE id=1")
    conn.close()
    updated = runner_db.read_bytes()
    runtime.set_meta('runner_until', '99999999999')
    reply = engine.request('checkpoint', {'sequence': 1, 'count': len(updated) // 4096,
        'pages': [[offset // 4096, base64.b64encode(updated[offset:offset+4096]).decode()]
                  for offset in range(0, len(updated), 4096)
                  if updated[offset:offset+4096] != initial[offset:offset+4096]]})
    assert reply.status == 200
    assert cfg.db_path.read_bytes() == initial
    assert runtime.meta('native_cache_stale') == 'true'
    token = services.set(runtime)
    try:
        with pytest.raises(DurabilityError, match='Reporting update'):
            Store(cfg.db_path)
        runtime.set_meta('runner_until', '0')  # Runner dies without /finish.
        runtime.set_meta('runner_upload_committed', 'preserve-upload')
        before = {key: runtime.meta(key) for key in
                  ('runner_lease', 'runner_sequence', 'runner_payload_hash', 'runner_upload_committed')}
        with Store(cfg.db_path) as store:
            assert store.conn.execute('SELECT headline FROM posts WHERE id=1').fetchone()[0] == 'NEW CHECKPOINTED HEADLINE'
            store.set_kv('native-write', 'retained')
        assert before == {key: runtime.meta(key) for key in before}
        assert runtime.meta('native_cache_stale') == 'false'
        assert runtime.native_generation == 1
        assert engine.request('checkpoint', {'sequence': 2}).status == 409
        durable = b''.join(row[0] for row in engine.conn.execute('SELECT data FROM db_pages ORDER BY number'))
        durable_path = tmp_path / 'verify-durable.sqlite'
        durable_path.write_bytes(durable)
        check = sqlite3.connect(durable_path)
        assert check.execute('SELECT headline FROM posts WHERE id=1').fetchone()[0] == 'NEW CHECKPOINTED HEADLINE'
        assert check.execute("SELECT value FROM kv WHERE key='native-write'").fetchone()[0] == 'retained'
        check.close()
    finally:
        services.reset(token)


def test_account_active_lease_retries_then_refreshes_cached_application(native, monkeypatch):
    runtime = native.runtime
    cfg = Config(data_dir=runtime.root)
    populate(cfg, 1)
    token = services.set(runtime)
    try:
        with Store(cfg.db_path):
            pass
    finally:
        services.reset(token)
    namespace = type(runtime).ensure_native.__globals__
    reporter_class = namespace['Reporter']
    owner = reporter_class.__new__(reporter_class)
    owner.lock = asyncio.Lock()
    owner.runtime = runtime
    owner.env = SimpleNamespace()
    owner.app, new_app = object(), object()
    owner.app_generation = 0
    seen = []
    class Response:
        def __init__(self, body=None, status=200, headers=None):
            self.status, self.headers = status, headers or {}
    async def fetch(app, request, env):
        seen.append(app)
        assert owner.lock.locked()
        return Response()
    monkeypatch.setitem(namespace, 'Response', Response)
    monkeypatch.setitem(namespace, 'wsgi', SimpleNamespace(fetch=fetch))
    from rep0rter import web
    monkeypatch.setattr(web, 'create_app', lambda: new_app)
    request = SimpleNamespace(url='https://example.test/projects', method='GET')
    runtime.set_meta('native_cache_stale', 'true')
    runtime.set_meta('runner_until', '99999999999')
    response = asyncio.run(owner.fetch(request))
    assert response.status == 503 and response.headers['Retry-After'] == '60'
    assert not seen and runtime.meta('native_cache_stale') == 'true'
    runtime.set_meta('runner_until', '0')
    assert asyncio.run(owner.fetch(request)).status == 200
    assert seen == [new_app]
    assert runtime.meta('native_cache_stale') == 'false'


@pytest.mark.parametrize('failure_phase', ['staging', 'sql', 'frontend', 'lost_ack'])
@pytest.mark.parametrize('restart', [False, True])
def test_pending_withdrawal_replays_after_failure_and_restart(native, monkeypatch, failure_phase, restart):
    runtime, engine = native.runtime, native.engine
    cfg = Config(data_dir=runtime.root)
    populate(cfg, 2)
    secret_link = 'https://old.test/private-source'
    engine.seed({
        'site/index.html': ('<html><article id="1"><p class="meta"><a rel="noopener" href="' + secret_link + '">Source</a></p>PRIVATE SENTINEL</article></html>').encode(),
        'site/feed.xml': b'<rss><channel><item><guid>1</guid><title>PRIVATE SENTINEL</title></item></channel></rss>',
        'site/posts/2/index.html': ('<html><article id="2">Keep</article><a href="' + secret_link + '">private link</a></html>').encode(),
        'site/cards/private.png': png(), 'image-cache/private.png': png()})
    frontend = dict(engine.read_files())
    attempts = []
    failing = True

    def publish():
        attempts.append(1)
        if failing and failure_phase == 'frontend':
            raise OSError('synthetic frontend failure')
        frontend.clear()
        frontend.update(engine.read_files())
        if failing and failure_phase == 'lost_ack':
            raise OSError('synthetic lost acknowledgement')

    runtime.publish_public = publish
    put = native.module.NativePublication.put
    def fail_put(self, name, content):
        if failing and failure_phase == 'staging' and name == 'site/index.html':
            raise OSError('synthetic staging failure')
        return put(self, name, content)
    monkeypatch.setattr(native.module.NativePublication, 'put', fail_put)
    set_meta = runtime.set_meta
    def fail_meta(key, value):
        if failing and failure_phase == 'sql' and key == 'site_built_at':
            raise OSError('synthetic SQL rollback')
        set_meta(key, value)
    runtime.set_meta = fail_meta
    token = services.set(runtime)
    try:
        with Store(cfg.db_path) as store:
            with pytest.raises(OSError, match='synthetic'):
                policy.redact(store, ['slack:C:0'])
            assert store.get_event('slack:C:0').text == ''
            assert secret_link in {row[0] for row in store.conn.execute('SELECT url FROM public_withdrawals')}
        ledger = json.loads((runtime.root / 'exclusions.json').read_text())
        assert secret_link in ledger['public_withdrawals']
        if failure_phase in ('staging', 'sql', 'frontend'):
            assert b'PRIVATE SENTINEL' in frontend['site/index.html']
        if failure_phase in ('staging', 'sql'):
            assert b'PRIVATE SENTINEL' in engine.read_files()['site/index.html']
        else:
            assert b'PRIVATE SENTINEL' not in engine.read_files()['site/index.html']
        failing = False
        calls_before = len(attempts)
        if restart:
            # New warm cache from the same durable SQL, without relying on any
            # local file or Python-side obligation surviving isolate eviction.
            # Also restore a DB without its queue: the separate policy ledger
            # must retain the cross-page URLs and replay the obligation.
            with sqlite3.connect(cfg.db_path) as conn:
                conn.execute('DELETE FROM public_withdrawals')
            conn.close()
            runtime.commit_database(cfg.db_path.read_bytes())
            restarted = type(runtime).__new__(type(runtime))
            for name in ('sql', 'transaction', 'meta', 'set_meta'):
                setattr(restarted, name, getattr(runtime, name))
            restarted.root = runtime.root.parent / 'restarted'
            restarted.root.mkdir()
            restarted.pages, restarted.poisoned = [], False
            restarted.publish_public = publish
            services.reset(token)
            runtime = restarted
            token = services.set(runtime)
            runtime.set_meta('native_cache_stale', 'true')
            cfg = Config(data_dir=runtime.root)
        with Store(cfg.db_path) as store:
            assert store.get_event('slack:C:0').text == ''
            assert store.conn.execute('SELECT count(*) FROM public_withdrawals').fetchone()[0] == 0
            assert store.conn.execute('SELECT count(*) FROM event_tombstones').fetchone()[0] == 1
        assert len(attempts) == calls_before + 1
        assert b'PRIVATE SENTINEL' not in frontend['site/index.html']
        assert secret_link.encode() not in frontend['site/posts/2/index.html']
        assert b'Keep' in frontend['site/posts/2/index.html']
        assert not any(name.startswith(('site/cards/', 'image-cache/')) for name in frontend)
        assert json.loads((runtime.root / 'exclusions.json').read_text())['public_withdrawals'] == []
        with Store(cfg.db_path):
            pass
        assert len(attempts) == calls_before + 1  # Acknowledged work stays clear.
    finally:
        services.reset(token)
