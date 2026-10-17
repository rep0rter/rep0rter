"""Cold Worker restore uses bounded synthetic SQL cursors, never live bindings."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


PAGE = b'x' * 4096
LEDGER = b'{"schema":1,"rules":[],"tombstones":[]}'


class FakeSQL:
    def __init__(self, count=65, imported='true'):
        self.pages = dict.fromkeys(range(count), PAGE)
        self.meta = {'imported': imported, 'site_built_at': '1',
                     'runner_until': '0', 'native_cache_stale': 'true',
                     'runner_lease': 'keep-lease', 'runner_sequence': '7',
                     'runner_payload_hash': 'keep-hash', 'runner_mode': 'build',
                     'runner_upload': 'keep-upload', 'runner_upload_digest': 'keep-digest',
                     'runner_upload_committed': 'keep-committed', 'runner_upload_until': '8'}
        self.ledger = LEDGER
        self.queries, self.ranges = [], []
        self.max_materialized = 0
        self.summary_override = {}
        self.summary_fault = False
        self.range_fault = None
        self.ledger_fault = None
        self.meta_fault = None
        self.in_transaction = False
        self.transactions = 0
        self.destroyed = 0
        self.native_staged = True

    def exec(self, query, *args):
        self.queries.append((query, args))
        if query.startswith('CREATE TABLE'):
            result = []
        elif query == 'DELETE FROM native_files':
            self.native_staged = False
            result = []
        elif query.startswith('SELECT count(*) AS page_count'):
            assert self.in_transaction
            if self.summary_fault:
                raise OSError('synthetic summary failure')
            numbers = list(self.pages)
            result = [{'page_count': len(numbers), 'byte_count': sum(map(len, self.pages.values())),
                       'first_page': min(numbers, default=None), 'last_page': max(numbers, default=None),
                       'invalid_pages': sum(len(p) != 4096 for p in self.pages.values()),
                       **self.summary_override}]
        elif query == 'SELECT number,data FROM db_pages WHERE number>=? AND number<? ORDER BY number':
            assert self.in_transaction
            start, end = args
            assert 0 <= start < end and end - start <= 64
            self.ranges.append((start, end))
            result = [{'number': n, 'data': self.pages[n]} for n in sorted(self.pages) if start <= n < end]
            if self.range_fault:
                result = self.range_fault(start, result)
            self.max_materialized = max(self.max_materialized, sum(len(row['data']) for row in result))
            assert len(result) <= 64 and self.max_materialized <= 256 * 1024
        elif query == "SELECT data FROM files WHERE path='exclusions.json'":
            assert self.in_transaction
            if self.ledger_fault:
                raise OSError('synthetic ledger read failure')
            result = [] if self.ledger is None else [{'data': self.ledger}]
        elif query == 'SELECT value FROM meta WHERE key=?':
            result = [{'value': self.meta[args[0]]}] if args[0] in self.meta else []
        elif query.startswith('INSERT INTO meta VALUES'):
            if self.meta_fault:
                raise OSError('synthetic metadata write failure')
            self.meta[args[0]] = str(args[1])
            result = []
        else:
            raise AssertionError('Unexpected SQL: ' + query)
        return SimpleNamespace(toArray=lambda: result)

    def transactionSync(self, proxy):
        assert not self.in_transaction
        self.in_transaction = True
        self.transactions += 1
        try:
            return proxy()
        finally:
            self.in_transaction = False


@pytest.fixture
def worker(tmp_path, monkeypatch):
    class DurableObject:
        def __init__(self, ctx, env):
            self.ctx, self.env = ctx, env

    class Response:
        def __init__(self, body=None, status=200, headers=None):
            self.body, self.status, self.headers = body, status, headers or {}

        @classmethod
        def json(cls, body, **kwargs):
            return cls(body, **kwargs)

    class Proxy:
        def __init__(self, fn):
            self.fn = fn

        def __call__(self):
            return self.fn()

        def destroy(self):
            sql.destroyed += 1

    sql = FakeSQL()
    ctx = SimpleNamespace(storage=SimpleNamespace(sql=sql, transactionSync=sql.transactionSync))
    env = SimpleNamespace(REP0RTER_CONFIG='{}', AI_MODEL='gpt-6-luna',
                          RUN_ENABLED='false', RUNNER_ENABLED='true')
    monkeypatch.setitem(sys.modules, 'js', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'revision', SimpleNamespace(SOURCE_COMMIT='startup-test'))
    monkeypatch.setitem(sys.modules, 'pyodide', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'pyodide.ffi', SimpleNamespace(
        create_proxy=Proxy, run_sync=lambda x: x, to_js=lambda x, **kwargs: x))
    monkeypatch.setitem(sys.modules, 'workers', SimpleNamespace(
        DurableObject=DurableObject, WorkerEntrypoint=object, Response=Response, wsgi=None))
    spec = importlib.util.spec_from_file_location(
        'startup_entry', Path(__file__).parents[1] / 'cloudflare/src/entry.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'os', SimpleNamespace(environ={}))
    root = tmp_path / 'native'
    monkeypatch.setattr(module, 'Path', lambda value: root if value == '/tmp/rep0rter' else Path(value))
    # Only the health model observation is needed; importing Config must not
    # touch dotenv or unrelated host settings in these startup-focused tests.
    monkeypatch.setitem(sys.modules, 'rep0rter.config', SimpleNamespace(
        Config=lambda: SimpleNamespace(ai_model=module.os.environ.get('AI_MODEL') or 'gpt-6-luna')))
    return SimpleNamespace(module=module, sql=sql, ctx=ctx, env=env, root=root)


def original_root(worker):
    worker.root.mkdir()
    (worker.root / 'rep0rter.sqlite').write_bytes(b'old-db')
    (worker.root / 'exclusions.json').write_bytes(b'old-ledger')
    (worker.root / 'obsolete').write_bytes(b'old-cache')


def runtime_without_restore(worker):
    runtime = worker.module.Runtime.__new__(worker.module.Runtime)
    runtime.ctx, runtime.env, runtime.sql = worker.ctx, worker.env, worker.sql
    runtime.root, runtime.pages, runtime.poisoned = worker.root, [b'old-page'], False
    runtime.native_generation = 4
    return runtime


@pytest.mark.parametrize('count', [63, 64, 65])
def test_actual_constructor_restores_in_bounded_ranges(worker, count):
    worker.sql.pages = {n: bytes([n % 256]) * 4096 for n in range(count)}
    original_root(worker)
    before = dict(worker.sql.meta)
    runtime = worker.module.Runtime(worker.ctx, worker.env)
    assert runtime.pages == list(worker.sql.pages.values())
    assert (worker.root / 'rep0rter.sqlite').read_bytes() == b''.join(runtime.pages)
    assert (worker.root / 'exclusions.json').read_bytes() == LEDGER
    assert not (worker.root / 'obsolete').exists()
    assert worker.sql.ranges == [(n, min(n + 64, count)) for n in range(0, count, 64)]
    assert worker.sql.max_materialized <= 256 * 1024
    assert worker.sql.meta == before and not worker.sql.native_staged
    assert worker.sql.transactions == worker.sql.destroyed == 1
    assert not runtime.poisoned
    assert not worker.root.with_name('native.restore').exists()
    assert not worker.root.with_name('native.previous').exists()


@pytest.mark.parametrize('imported', [None, 'false', 'true'])
def test_empty_database_bootstrap_and_imported_state(worker, imported):
    worker.sql.pages = {}
    if imported is None:
        worker.sql.meta.pop('imported')
    else:
        worker.sql.meta['imported'] = imported
    if imported == 'true':
        runtime = runtime_without_restore(worker)
        with pytest.raises(ValueError, match='Invalid durable database'):
            runtime._restore_native_files()
        assert runtime.poisoned and not worker.root.exists()
    else:
        runtime = worker.module.Runtime(worker.ctx, worker.env)
        assert runtime.pages == [] and not (worker.root / 'rep0rter.sqlite').exists()
        assert (worker.root / 'exclusions.json').read_bytes() == LEDGER
    assert not worker.sql.ranges


@pytest.mark.parametrize('fault', ['gap', 'negative', 'nonzero', 'short', 'oversize', 'count-mismatch', 'size-mismatch'])
def test_invalid_page_metadata_rejected_before_blob_ranges(worker, fault):
    if fault == 'gap':
        del worker.sql.pages[5]
    elif fault == 'negative':
        worker.sql.pages[-1] = worker.sql.pages.pop(0)
    elif fault == 'nonzero':
        worker.sql.pages = {n + 1: page for n, page in worker.sql.pages.items()}
    elif fault == 'short':
        worker.sql.pages[0] = b'short'
    elif fault == 'oversize':
        worker.sql.pages = dict.fromkeys(range(8193), PAGE)
    elif fault == 'count-mismatch':
        worker.sql.summary_override['page_count'] = 64
    else:
        worker.sql.summary_override['byte_count'] = 1
    original_root(worker)
    runtime = runtime_without_restore(worker)
    before = dict(worker.sql.meta)
    with pytest.raises(ValueError, match='Invalid durable database'):
        runtime.ensure_native()
    assert runtime.poisoned and runtime.pages == [b'old-page'] and runtime.native_generation == 4
    assert (worker.root / 'rep0rter.sqlite').read_bytes() == b'old-db'
    assert worker.sql.meta == before and worker.sql.ranges == []


def test_exact_page_cap_uses_bounded_ranges_without_full_size_fixture(worker):
    assert worker.module.MAX_DATABASE // worker.module.PAGE_SIZE == 8192
    # Metadata for the exact cap is accepted, then the first bounded read stops
    # deliberately. This is a structural cap proof, not a 32 MiB restore test.
    worker.sql.summary_override = {'page_count': 8192, 'byte_count': 32 * 1024 * 1024,
                                   'first_page': 0, 'last_page': 8191, 'invalid_pages': 0}
    worker.sql.range_fault = lambda start, rows: (_ for _ in ()).throw(OSError('stop at first range'))
    runtime = runtime_without_restore(worker)
    with pytest.raises(OSError, match='stop at first range'):
        runtime._restore_native_files()
    assert worker.sql.ranges == [(0, 64)] and runtime.poisoned


@pytest.mark.parametrize('fault', ['summary', 'sql', 'conversion', 'missing', 'order', 'size', 'ledger-read',
                                  'write', 'short-write', 'replace-old', 'replace', 'ledger-write',
                                  'short-ledger-write', 'meta-write'])
def test_failed_refresh_keeps_old_cache_and_runner_metadata(worker, monkeypatch, fault):
    original_root(worker)
    runtime = runtime_without_restore(worker)
    before = dict(worker.sql.meta)

    def corrupt(start, result):
        if start == 0:
            return result
        if fault == 'sql':
            raise OSError('synthetic range failure')
        if fault == 'conversion':
            class BadBuffer:
                def __len__(self):
                    return 4096

                def to_py(self):
                    raise ValueError('synthetic conversion failure')
            result[0]['data'] = BadBuffer()
        elif fault == 'missing':
            result = []
        elif fault == 'order':
            result[0]['number'] = 63
        elif fault == 'size':
            result[0]['data'] = b'short'
        return result

    worker.sql.range_fault = corrupt
    worker.sql.summary_fault = fault == 'summary'
    worker.sql.ledger_fault = fault == 'ledger-read'
    worker.sql.meta_fault = fault == 'meta-write'
    real_open, real_write, real_replace = Path.open, Path.write_bytes, Path.replace

    class BadWriter:
        def __init__(self, output):
            self.output = output

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.output.close()

        def write(self, data):
            if fault == 'write':
                raise OSError('synthetic local write failure')
            self.output.write(data[:10])
            return 10

    def open_file(path, *args, **kwargs):
        output = real_open(path, *args, **kwargs)
        return BadWriter(output) if fault in ('write', 'short-write') and path.parent.name == 'native.restore' else output

    def write_ledger(path, data):
        if fault == 'ledger-write' and path.parent.name == 'native.restore':
            raise OSError('synthetic ledger write failure')
        if fault == 'short-ledger-write' and path.parent.name == 'native.restore':
            real_write(path, data[:10])
            return 10
        return real_write(path, data)

    def replace(path, target):
        if fault == 'replace-old' and path.name == 'native':
            raise OSError('synthetic old-directory replace failure')
        if fault == 'replace' and path.name == 'native.restore':
            raise OSError('synthetic directory replace failure')
        return real_replace(path, target)

    monkeypatch.setattr(Path, 'open', open_file)
    monkeypatch.setattr(Path, 'write_bytes', write_ledger)
    monkeypatch.setattr(Path, 'replace', replace)
    with pytest.raises((OSError, ValueError)):
        runtime.ensure_native()
    assert runtime.poisoned and runtime.pages == [b'old-page'] and runtime.native_generation == 4
    assert worker.sql.meta == before
    assert not worker.root.with_name('native.restore').exists()
    if fault == 'meta-write':
        # The complete directory can be installed before a metadata-write fault;
        # its poisoned Runtime must never select old pages or claim readiness.
        assert (worker.root / 'rep0rter.sqlite').stat().st_size == 65 * 4096
    else:
        assert (worker.root / 'rep0rter.sqlite').read_bytes() == b'old-db'
        assert (worker.root / 'exclusions.json').read_bytes() == b'old-ledger'
    assert worker.sql.transactions == worker.sql.destroyed == 1


def test_constructor_failure_is_observable_and_poisoned(worker):
    worker.sql.ledger_fault = True
    runtime = worker.module.Runtime.__new__(worker.module.Runtime)
    with pytest.raises(OSError, match='synthetic ledger'):
        runtime.__init__(worker.ctx, worker.env)
    assert runtime.poisoned and runtime.pages == [] and not worker.root.exists()


def test_constructor_preserves_optional_absent_ledger(worker):
    worker.sql.ledger = None
    runtime = worker.module.Runtime(worker.ctx, worker.env)
    assert len(runtime.pages) == 65 and not (worker.root / 'exclusions.json').exists()


def test_failed_directory_rollback_retains_previous_and_poisoned_cache(worker, monkeypatch):
    original_root(worker)
    runtime = runtime_without_restore(worker)
    before = dict(worker.sql.meta)
    real_replace = Path.replace

    def replace(path, target):
        if path.name in ('native.restore', 'native.previous'):
            raise OSError('synthetic replacement/rollback failure')
        return real_replace(path, target)

    monkeypatch.setattr(Path, 'replace', replace)
    with pytest.raises(OSError, match='rollback failure'):
        runtime.ensure_native()
    assert runtime.poisoned and runtime.pages == [b'old-page'] and runtime.native_generation == 4
    assert worker.sql.meta == before and not worker.root.exists()
    assert (worker.root.with_name('native.previous') / 'rep0rter.sqlite').read_bytes() == b'old-db'
    assert not worker.root.with_name('native.restore').exists()


def test_cleanup_after_promotion_failure_keeps_complete_new_directory_and_poison(worker, monkeypatch):
    original_root(worker)
    runtime = runtime_without_restore(worker)
    before = dict(worker.sql.meta)
    real_remove = worker.module.shutil.rmtree

    def remove(path):
        if path.name == 'native.previous':
            raise OSError('synthetic previous-directory cleanup failure')
        return real_remove(path)

    monkeypatch.setattr(worker.module.shutil, 'rmtree', remove)
    with pytest.raises(OSError, match='previous-directory cleanup'):
        runtime.ensure_native()
    assert runtime.poisoned and runtime.pages == [b'old-page'] and runtime.native_generation == 4
    assert worker.sql.meta == before
    assert (worker.root / 'rep0rter.sqlite').stat().st_size == 65 * 4096
    assert (worker.root / 'exclusions.json').read_bytes() == LEDGER
    assert (worker.root.with_name('native.previous') / 'rep0rter.sqlite').read_bytes() == b'old-db'


def test_secondary_cleanup_failure_preserves_original_restore_fault(worker, monkeypatch):
    original_root(worker)
    runtime = runtime_without_restore(worker)
    worker.sql.ledger_fault = True
    before = dict(worker.sql.meta)
    real_remove = worker.module.shutil.rmtree

    def remove(path):
        if path.name == 'native.restore':
            raise OSError('synthetic staged-directory cleanup failure')
        return real_remove(path)

    monkeypatch.setattr(worker.module.shutil, 'rmtree', remove)
    with pytest.raises(OSError, match='synthetic ledger read failure') as raised:
        runtime.ensure_native()
    assert isinstance(raised.value.__cause__, OSError)
    assert 'staged-directory cleanup' in str(raised.value.__cause__)
    assert runtime.poisoned and runtime.pages == [b'old-page'] and runtime.native_generation == 4
    assert worker.sql.meta == before
    assert (worker.root / 'rep0rter.sqlite').read_bytes() == b'old-db'


def test_active_lease_prevents_any_restore_or_stale_change(worker):
    runtime = runtime_without_restore(worker)
    worker.sql.meta['runner_until'] = '99999999999'
    before = dict(worker.sql.meta)
    with pytest.raises(worker.module.DurabilityError, match='Reporting update'):
        runtime.ensure_native()
    assert worker.sql.meta == before and worker.sql.transactions == 0
    assert not worker.root.exists() and not runtime.poisoned


def test_successful_refresh_publishes_new_cache_once_after_ledger(worker):
    original_root(worker)
    runtime = runtime_without_restore(worker)
    before = dict(worker.sql.meta)
    assert runtime.ensure_native() is True
    assert runtime.pages == list(worker.sql.pages.values()) and runtime.native_generation == 5
    assert worker.sql.meta == {**before, 'native_cache_stale': 'false'}
    assert runtime.ensure_native() is False and worker.sql.transactions == 1
    assert (worker.root / 'exclusions.json').read_bytes() == LEDGER


@pytest.mark.parametrize('override,secret,expected', [
    ('gpt-6-luna', 'legacy-model', 'gpt-6-luna'),
    ('', 'custom-model', 'custom-model'), (None, '', 'gpt-6-luna')])
def test_constructor_preserves_public_model_override_and_private_string_filter(worker, override, secret, expected):
    worker.env.AI_MODEL = override
    worker.env.REP0RTER_CONFIG = json.dumps({'AI_MODEL': secret, 'AI_API_KEY': 'synthetic-only',
                                          'AI_TIMEOUT_SECONDS': 2, 'OTHER': 'ignored'})
    worker.module.Runtime(worker.ctx, worker.env)
    environment = worker.module.os.environ
    assert (environment.get('AI_MODEL') or 'gpt-6-luna') == expected
    assert environment['AI_API_KEY'] == 'synthetic-only'
    assert 'AI_TIMEOUT_SECONDS' not in environment
    assert environment['REP0RTER_DATA_DIR'] == str(worker.root)


def test_engine_health_still_loads_and_refuses_failed_storage(worker):
    owner = worker.module.Reporter(worker.ctx, worker.env)
    request = SimpleNamespace(url='https://synthetic.test/healthz', method='GET')
    worker.sql.ledger_fault = True
    with pytest.raises(OSError, match='synthetic ledger'):
        asyncio.run(owner.fetch(request))
    assert owner.runtime is None and worker.sql.transactions == 1
    worker.sql.ledger_fault = False
    response = asyncio.run(owner.fetch(request))
    assert response.status == 200
    assert response.body['source_commit'] == 'startup-test'
    assert response.body['ready'] is True and response.body['scheduler'] == 'github-actions'
    assert response.body['scheduled'] is True and response.body['ai_model'] == 'gpt-6-luna'
    owner.runtime.poisoned = True
    worker.sql.ledger_fault = True
    with pytest.raises(OSError, match='synthetic ledger'):
        asyncio.run(owner.fetch(request))
    assert owner.runtime.poisoned
