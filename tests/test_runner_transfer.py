"""Exercise the runner/engine boundary with SQLite, corruption and lost replies."""
import asyncio
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import time
from types import SimpleNamespace
from urllib.parse import quote

import pytest

ROOT = Path(__file__).resolve().parents[1]


class Response:
    def __init__(self, body=None, status=200, **kwargs):
        self.body, self.status = body, status

    @classmethod
    def json(cls, body, **kwargs):
        return cls(json.dumps(body).encode(), **kwargs)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'workers', SimpleNamespace(Response=Response))
    monkeypatch.setitem(sys.modules, 'revision', SimpleNamespace(SOURCE_COMMIT='current'))
    transfer = load('runner_transfer', ROOT / 'cloudflare/src/runner_transfer.py')
    monkeypatch.setitem(sys.modules, 'runner_transfer', transfer)
    api = load('runner_api', ROOT / 'cloudflare/src/runner_api.py')
    conn = sqlite3.connect(tmp_path / 'durable.sqlite', isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE files(path TEXT PRIMARY KEY,data BLOB,digest TEXT)')
    conn.execute('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)')
    conn.execute('CREATE TABLE db_pages(number INTEGER PRIMARY KEY,data BLOB)')
    def execute(query, *args):
        cursor = conn.execute(query, args)
        return SimpleNamespace(toArray=lambda: [dict(row) for row in cursor.fetchall()])
    def get_meta(key, default=None):
        row = conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row['value'] if row else default
    def set_meta(key, value):
        conn.execute('INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, str(value)))
    def transaction(callback):
        conn.execute('BEGIN')
        try:
            callback()
            conn.execute('COMMIT')
        except BaseException:
            conn.execute('ROLLBACK')
            raise
    public = {}
    runtime = SimpleNamespace(sql=SimpleNamespace(exec=execute), pages=[b'x' * 4096],
                              meta=get_meta, set_meta=set_meta, transaction=transaction)
    runtime.publish_status = lambda: None
    runtime.publish_public = lambda: public.update(read_files())
    def read_files():
        return {row['path']: row['data'] for row in conn.execute('SELECT path,data FROM files')}
    def seed(files):
        conn.executemany('INSERT OR REPLACE INTO files VALUES (?,?,?)',
                         [(path, data, hashlib.sha256(data).hexdigest()) for path, data in files.items()])
    initial = {'site/index.html': b'old home', 'site/feed.xml': b'old feed',
               'site/withdrawn.html': b'withdrawn', 'exclusions.json': b'{"schema":1,"rules":[],"tombstones":[]}'}
    seed(initial)
    public.update(initial)
    set_meta('imported', 'true')
    set_meta('runner_lease', 'a' * 32)
    set_meta('runner_until', time.time() + 100)
    owner = SimpleNamespace(runtime=runtime, env=SimpleNamespace(RUNNER_TOKEN='secret', RUNNER_ENABLED='true'))
    def request(action, data=None, *, body=None, token=None, name=None, lease='a' * 32):
        headers = {'Authorization': 'Bearer secret', 'X-Runner-Lease': lease}
        if token is not None:
            headers['X-Publication'] = token
        if name is not None:
            headers['X-Asset-Path'] = quote(name, safe='')
        class Request:
            method = 'POST'
            async def bytes(self):
                return body if body is not None else json.dumps(data or {}).encode()
        req = Request()
        req.headers = headers
        return asyncio.run(api.handle(owner, req, '/__runner/' + action))
    yield SimpleNamespace(conn=conn, runtime=runtime, request=request, public=public,
                          transfer=transfer, read_files=read_files, seed=seed, initial=initial)
    conn.close()


def manifest(files):
    return [{'path': path, 'size': len(data), 'digest': hashlib.sha256(data).hexdigest()}
            for path, data in files.items()]


def generation(**extra):
    return {'site/index.html': b'next home', 'site/feed.xml': b'next feed',
            'exclusions.json': b'policy', **extra}


def begin(engine, files, token='b' * 32):
    result = engine.request('publish-begin', {'publication': token, 'files': manifest(files)})
    assert result.status == 200
    return json.loads(result.body)['missing']


def stage(engine, files, token='b' * 32):
    for name in begin(engine, files, token):
        assert engine.request('publish-file', body=files[name], token=token, name=name).status == 200


def test_partial_retry_and_commit_keep_previous_publication_until_complete(engine):
    files = generation(**{'site/cards/圖.png': b'card', 'site/unchanged': b'reused'})
    engine.seed({'site/unchanged': b'reused'})
    missing = begin(engine, files)
    assert 'site/unchanged' not in missing
    name = missing[0]
    assert engine.request('publish-file', body=files[name], token='b' * 32, name=name).status == 200
    assert name not in begin(engine, files)  # lost begin reply does not erase staging
    assert engine.request('publish-commit', token='b' * 32).status == 409
    assert engine.public == engine.initial
    stage(engine, files)
    # Repeating a successful file PUT is harmless.
    assert engine.request('publish-file', body=files[name], token='b' * 32, name=name).status == 200
    assert engine.request('publish-commit', token='b' * 32).status == 200
    assert engine.read_files() == files
    assert engine.public['site/index.html'] == files['site/index.html']
    assert 'site/withdrawn.html' not in engine.read_files()
    assert engine.conn.execute('SELECT count(*) FROM runner_files').fetchone()[0] == 0


def test_public_transfer_failure_and_lost_commit_reply_are_recoverable(engine):
    files = generation()
    stage(engine, files)
    publish = engine.runtime.publish_public
    engine.runtime.publish_public = lambda: (_ for _ in ()).throw(OSError('interrupted'))
    with pytest.raises(OSError, match='interrupted'):
        engine.request('publish-commit', token='b' * 32)
    assert engine.public == engine.initial
    assert engine.read_files() == files
    engine.runtime.publish_public = publish
    assert engine.request('publish-commit', token='b' * 32).status == 200
    assert engine.request('publish-commit', token='b' * 32).status == 200
    assert engine.public['site/index.html'] == b'next home'
    begin(engine, generation(), 'c' * 32)
    assert engine.request('publish-commit', token='b' * 32).status == 409


def test_database_commit_failure_rolls_back_files_and_generation_token(engine):
    files = generation()
    stage(engine, files)
    original = engine.runtime.set_meta
    def fail(key, value):
        if key == 'runner_upload_committed':
            raise OSError('storage failure')
        original(key, value)
    engine.runtime.set_meta = fail
    with pytest.raises(OSError, match='storage failure'):
        engine.request('publish-commit', token='b' * 32)
    assert engine.public == engine.initial
    assert engine.read_files() == engine.initial
    engine.runtime.set_meta = original
    assert engine.request('publish-commit', token='b' * 32).status == 200


@pytest.mark.parametrize('path', ['site/../private', '/site/a', 'site//a', 'site/./a', 'site/a\\b', 'site/a\x00b', 'private'])
def test_invalid_paths_and_unknown_names_are_rejected(engine, path):
    data = manifest(generation(**{path: b'bad'}))
    assert engine.request('publish-begin', {'publication': 'b' * 32, 'files': data}).status == 400
    assert engine.request('snapshot', {'path': path}).status == 400
    assert engine.read_files() == engine.initial


def test_hash_size_duplicate_and_storage_budgets_are_hard_limits(engine):
    valid = manifest(generation())
    for bad in [valid + valid[:1], valid[:1], {item['path']: item for item in valid},
                [{**valid[0], 'size': True}, *valid[1:]],
                [{**valid[0], 'digest': 'bad'}, *valid[1:]],
                [{**valid[0], 'size': 1900001}, *valid[1:]]]:
        assert engine.request('publish-begin', {'publication': 'b' * 32, 'files': bad}).status == 400
    oversized = valid + [{'path': f'site/{i}.bin', 'size': 1900000, 'digest': 'a' * 64} for i in range(71)]
    assert engine.request('publish-begin', {'publication': 'b' * 32, 'files': oversized}).status == 400
    total = valid + [{'path': f'image-cache/{i}.bin', 'size': 1900000, 'digest': 'a' * 64} for i in range(89)]
    assert engine.request('publish-begin', {'publication': 'b' * 32, 'files': total}).status == 400
    begin(engine, generation())
    assert engine.request('publish-file', token='b' * 32, name='site/index.html', body=b'corrupt!!').status == 400
    assert engine.request('publish-file', token='b' * 32, name='site/unknown', body=b'x').status == 400
    assert engine.request('publish-file', token='b' * 32, name='site/index.html', body=b'x' * (2 * 1024 * 1024 + 1)).status == 413
    assert engine.read_files() == engine.initial


def test_manifest_budget_counts_complete_upload_and_snapshot_envelopes(engine):
    runner = load('envelope_runner', ROOT / 'cloudflare/github_runner.py')
    files = manifest(generation())
    for index in range(6400):
        files.append({'path': f'site/{index:04}/' + 'a' * 216, 'size': 0,
                      'digest': hashlib.sha256(b'').hexdigest()})
    def snapshot_body():
        return json.dumps({'protocol': 2, 'database_size': 32 * 1024 * 1024,
                           'database_digest': 'f' * 64, 'files': files}).encode()
    target = 2 * 1024 * 1024
    while len(snapshot_body()) > target:
        files.pop()
    padding = target - len(snapshot_body())
    while padding:
        if padding == 1:
            files[-1]['path'] += 'p'
            padding -= 1
        else:
            length = min(200, padding - 1)
            files[-1]['path'] += '/' + 'p' * length
            padding -= length + 1
    assert len(snapshot_body()) == target
    runner.RunnerRuntime.validate_manifest(files)
    engine.transfer.validate(files)
    upload = json.dumps({'publication': 'b' * 32, 'files': files}).encode()
    assert len(upload) <= target
    assert engine.request('publish-begin', body=upload).status == 200
    files[-1]['path'] += 'x'
    assert len(json.dumps(files).encode()) < target
    for validate in (runner.RunnerRuntime.validate_manifest, engine.transfer.validate):
        with pytest.raises(ValueError, match='transfer budget'):
            validate(files)
    assert engine.request('publish-begin', {'publication': 'c' * 32, 'files': files}).status == 400


@pytest.mark.parametrize('name', ['site/index.html/child', 'image-cache/a/child'])
def test_file_directory_prefix_conflicts_never_commit_an_unrestorable_snapshot(engine, name):
    runner = load('prefix_runner', ROOT / 'cloudflare/github_runner.py')
    files = generation(**{name: b'x'})
    if name.startswith('image-cache/'):
        files['image-cache/a'] = b'parent'
    for validate in (runner.RunnerRuntime.validate_manifest, engine.transfer.validate):
        with pytest.raises(ValueError, match='Conflicting'):
            validate(manifest(files))
    assert engine.request('publish-begin', {'publication': 'b' * 32, 'files': manifest(files)}).status == 400
    assert engine.read_files() == engine.initial


@pytest.mark.parametrize('name', ['site/' + 'a' * 256, 'site/' + '字' * 86, 'site/\ud800'])
def test_snapshot_paths_must_be_representable_as_utf8_filesystem_components(engine, name):
    runner = load('filename_runner', ROOT / 'cloudflare/github_runner.py')
    files = manifest(generation(**{name: b'x'}))
    for validate in (runner.RunnerRuntime.validate_manifest, engine.transfer.validate):
        with pytest.raises(ValueError, match='Invalid'):
            validate(files)
    assert engine.request('publish-begin', {'publication': 'b' * 32, 'files': files}).status == 400


def test_expiry_new_lease_and_replacement_fence_pending_publications(engine):
    files = generation()
    begin(engine, files)
    assert engine.request('publish-begin', {'publication': 'b' * 32, 'files': manifest(generation(**{'site/new': b'x'}))}).status == 409
    begin(engine, files, 'c' * 32)
    assert engine.request('publish-commit', token='b' * 32).status == 409
    engine.runtime.set_meta('runner_upload_until', '0')
    assert engine.request('publish-commit', token='c' * 32).status == 409
    assert engine.conn.execute('SELECT count(*) FROM runner_files').fetchone()[0] == 0
    begin(engine, files)
    engine.runtime.set_meta('runner_until', '0')
    assert engine.request('publish-commit', token='b' * 32).status == 409
    start = engine.request('start', {'lease': 'd' * 32, 'source_commit': 'current', 'mode': 'build', 'protocol': 2})
    assert start.status == 200 and json.loads(start.body)['protocol'] == 2
    assert engine.conn.execute('SELECT count(*) FROM runner_files').fetchone()[0] == 0
    assert engine.request('publish-commit', token='b' * 32).status == 409


def test_v2_snapshot_download_checks_hash_and_uses_bounded_database_ranges(engine, tmp_path):
    runner = load('bounded_runner', ROOT / 'cloudflare/github_runner.py')
    runtime = runner.RunnerRuntime('https://example.test', 'secret', tmp_path, 'd' * 32)
    engine.runtime.set_meta('runner_until', '0')
    calls = []
    def request(action, body=b'', content_type='application/json', headers=None):
        response = engine.request(action, body=body, lease=runtime.lease)
        assert response.status == 200
        calls.append((action, len(response.body)))
        return response.body
    runtime.request = request
    runtime.start('current', 'build')
    assert runtime.pages == engine.runtime.pages
    assert (tmp_path / 'rep0rter.sqlite').read_bytes() == b''.join(runtime.pages)
    for name, content in engine.initial.items():
        assert (tmp_path / name).read_bytes() == content
    assert max(size for _, size in calls) <= 2 * 1024 * 1024
    assert engine.request('snapshot', {'path': 'rep0rter.sqlite', 'offset': -4096, 'length': 4096}, lease=runtime.lease).status == 400
    assert engine.request('snapshot', {'path': 'rep0rter.sqlite', 'offset': 0, 'length': 2 * 1024 * 1024}, lease=runtime.lease).status == 400


def test_legacy_zip_snapshot_still_works_and_unknown_protocol_is_rejected(engine):
    import io
    import zipfile
    engine.runtime.set_meta('runner_until', '0')
    invalid = {'lease': 'd' * 32, 'source_commit': 'current', 'mode': 'build', 'protocol': 3}
    assert engine.request('start', invalid).status == 400
    invalid.pop('protocol')
    response = engine.request('start', invalid)
    with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
        assert archive.read('rep0rter.sqlite') == b'x' * 4096
        assert archive.read('site/index.html') == b'old home'


def test_runner_uploads_83_mib_generation_without_a_whole_site_request(engine, tmp_path, capsys):
    runner = load('large_runner', ROOT / 'cloudflare/github_runner.py')
    runtime = runner.RunnerRuntime('https://example.test', 'secret', tmp_path, 'a' * 32)
    runtime.pages = engine.runtime.pages
    files = generation()
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    folder = tmp_path / 'site/cards'
    folder.mkdir()
    block = b'x' * 1024 * 1024
    for index in range(83):
        (folder / f'{index}.png').write_bytes(block)
    calls = []
    # Mirror only public metadata here so the fake frontend does not materialize
    # the entire generation. Real frontend transactions are tested separately.
    engine.runtime.publish_public = lambda: engine.public.update({'ready': True})
    def request(action, body=b'', content_type='application/json', headers=None):
        calls.append((action, len(body)))
        name = None
        if action == 'publish-file':
            # The production request carries an encoded path header.
            from urllib.parse import unquote
            name = unquote(headers['X-Asset-Path'])
        response = engine.request(action, body=body, token=(headers or {}).get('X-Publication'),
                                  name=name, lease=runtime.lease)
        assert response.status == 200
        return response.body
    runtime.request = request
    runtime.publish_site(None)
    assert max(size for _, size in calls) <= 2 * 1024 * 1024
    assert engine.conn.execute("SELECT sum(length(data)) FROM files WHERE path LIKE 'site/%'").fetchone()[0] > 64 * 1024 * 1024
    assert engine.public['ready']
    # Unchanged assets are reused after a retry/new complete publication.
    calls.clear()
    runtime.publish_site(None)
    assert [action for action, _ in calls] == ['publish-begin', 'publish-commit']
