"""Publish large generations in bounded batches and switch only when complete."""
import importlib.util
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_publication_checks(checks):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js required')
    source = (ROOT / 'cloudflare/web/index.js').read_text().replace(
        "import { DurableObject, WorkerEntrypoint } from 'cloudflare:workers';",
        'class DurableObject { constructor(ctx,env) { this.ctx=ctx; this.env=env; } }\n'
        'class WorkerEntrypoint extends DurableObject {}')
    setup = """
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {createHash} from 'node:crypto';
const {PublishedSite} = await import('data:text/javascript;base64,' + Buffer.from(SOURCE).toString('base64'));
const db = new DatabaseSync(':memory:');
const storage = {
  sql: {exec(query, ...args) {const stmt=db.prepare(query); return stmt.columns().length ? stmt.all(...args) : (stmt.run(...args), []);}},
  transactionSync(fn) {db.exec('BEGIN'); try {const result=fn(); db.exec('COMMIT'); return result;} catch(e) {db.exec('ROLLBACK'); throw e;}}
};
const site = new PublishedSite({storage}, {});
const first = 'a'.repeat(32), next = 'b'.repeat(32);
function file(path, text) {const data=typeof text === 'string' ? new TextEncoder().encode(text) : text; return {path, data, digest:createHash('sha256').update(data).digest('hex')};}
function manifest(files) {return files.map(({path,data,digest}) => ({path,digest,size:data.byteLength}));}
async function read(path) {return (await site.fetch(new Request('https://example.test/'+path))).text();}
const old=[file('index.html','old'),file('feed.xml','old feed'),file('withdrawn.html','removed')];
site.publishSite(old,{ready:true,version:'old'});
""".replace('SOURCE', json.dumps(source))
    result = subprocess.run([node, '--input-type=module', '-'], input=setup + checks,
                            text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_large_publication_stays_invisible_until_all_batches_arrive():
    run_publication_checks("""
const files=[file('index.html','new'),file('feed.xml','new feed')];
for(let i=0;i<26;i++) files.push(file(`cards/${i}.png`,new Uint8Array(1024*1024).fill(i)));
assert.equal(site.beginPublication(first,manifest(files)).length,28);
for (const f of files.slice(0,-1)) site.stagePublication(first,[f]);
assert.equal(await read('index.html'),'old');
assert.throws(()=>site.commitPublication(first,{ready:true}),/Incomplete/);
assert.equal(await read('withdrawn.html'),'removed');
site.stagePublication(first,[files.at(-1)]);
assert.deepEqual(site.commitPublication(first,{ready:true,version:'new'}),{published:28});
assert.equal(await read('index.html'),'new');
assert.equal((await site.fetch(new Request('https://example.test/withdrawn.html'))).status,404);
assert.equal(JSON.parse(await read('healthz')).version,'new');
assert.equal(db.prepare('SELECT count(*) n FROM pending_assets').get().n,0);
""")


def test_publication_reuses_unchanged_assets_and_fences_abandoned_uploads():
    run_publication_checks("""
const files=[old[0],file('feed.xml','next feed')];
assert.deepEqual(site.beginPublication(first,manifest(files)),['feed.xml']);
site.stagePublication(first,[files[1]]);
// The receiver can restart between RPC calls without losing its manifest.
const resumed=new PublishedSite({storage},{});
assert.equal(await read('index.html'),'old');
assert.deepEqual(resumed.beginPublication(next,manifest(files)),['feed.xml']);
assert.throws(()=>site.stagePublication(first,[files[1]]),/expired or replaced/);
assert.throws(()=>site.commitPublication(first,{}),/expired or replaced/);
resumed.stagePublication(next,[files[1]]);
resumed.stagePublication(next,[files[1]]); // retry is idempotent
resumed.commitPublication(next,{ready:true});
assert.equal(await read('feed.xml'),'next feed');
assert.deepEqual(resumed.beginPublication(first,manifest(files)),[]);
resumed.commitPublication(first,{ready:true});
// An older engine publication must invalidate any pending newer protocol upload.
resumed.beginPublication(next,manifest(files));
site.publishSite(old,{ready:true});
assert.throws(()=>resumed.commitPublication(next,{}),/expired or replaced/);
""")


def test_invalid_batches_never_mutate_the_active_site():
    run_publication_checks(r"""
for(const path of ['../bad','/bad','bad/../bad','bad\\name','bad//name','bad/./name']) {
 assert.throws(()=>site.beginPublication(first,manifest([...old,file(path,'x')])),/Invalid/);
}
assert.throws(()=>site.beginPublication(first,manifest([old[0]])),/Incomplete/);
assert.throws(()=>site.beginPublication(first,manifest([...old,old[0]])),/Invalid/);
const files=[file('index.html','new'),file('feed.xml','new feed'),file('a.png',new Uint8Array(1500000)),file('b.png',new Uint8Array(1500000))];
site.beginPublication(first,manifest(files));
assert.throws(()=>site.stagePublication(first,files.slice(2)),/batch too large/);
assert.throws(()=>site.stagePublication(first,[file('index.html','different')]),/match manifest/);
assert.throws(()=>site.stagePublication(first,[file('unknown','x')]),/match manifest/);
assert.throws(()=>site.commitPublication(first,{}),/Incomplete/);
assert.equal(await read('index.html'),'old');
assert.equal(JSON.parse(await read('healthz')).version,'old');
""")


def test_commit_failure_rolls_back_content_health_and_withdrawals():
    run_publication_checks("""
const files=[file('index.html','new'),file('feed.xml','new feed')];
site.beginPublication(first,manifest(files));
site.stagePublication(first,files);
const update=site.updateStatus;
site.updateStatus=()=>{throw new Error('storage failure');};
assert.throws(()=>site.commitPublication(first,{ready:true,version:'new'}),/storage failure/);
assert.equal(await read('index.html'),'old');
assert.equal(await read('withdrawn.html'),'removed');
assert.equal(JSON.parse(await read('healthz')).version,'old');
site.updateStatus=update;
site.commitPublication(first,{ready:true,version:'new'});
assert.equal(await read('index.html'),'new');
assert.equal((await site.fetch(new Request('https://example.test/withdrawn.html'))).status,404);
""")


def load_runtime(monkeypatch):
    monkeypatch.setitem(sys.modules, 'js', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'revision', SimpleNamespace(SOURCE_COMMIT='test'))
    monkeypatch.setitem(sys.modules, 'pyodide', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'pyodide.ffi', SimpleNamespace(
        create_proxy=lambda fn: fn, run_sync=lambda value: value, to_js=lambda value, **kw: value))
    monkeypatch.setitem(sys.modules, 'workers', SimpleNamespace(
        DurableObject=object, WorkerEntrypoint=object, Response=object, wsgi=None))
    spec = importlib.util.spec_from_file_location('publication_entry', ROOT / 'cloudflare/src/entry.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Runtime


@pytest.mark.parametrize('fail_batch', [False, True])
def test_engine_transfers_large_site_in_bounded_batches_and_never_commits_a_failure(monkeypatch, fail_batch):
    runtime_class = load_runtime(monkeypatch)
    runtime = runtime_class.__new__(runtime_class)
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE files(path TEXT PRIMARY KEY,data BLOB,digest TEXT)')
    for i in range(26):
        conn.execute('INSERT INTO files VALUES (?,?,?)', (f'site/cards/{i}.png', b'x' * 1024 * 1024, str(i)))
    conn.execute('INSERT INTO files VALUES (?,?,?)', ('exclusions.json', b'private policy', 'private'))
    conn.execute('INSERT INTO files VALUES (?,?,?)', ('site/index.html', b'index', 'index'))
    conn.execute('INSERT INTO files VALUES (?,?,?)', ('site/feed.xml', b'feed', 'feed'))
    def execute(query, *args):
        return SimpleNamespace(toArray=lambda: [dict(row) for row in conn.execute(query, args)])
    runtime.sql = SimpleNamespace(exec=execute)
    calls=[]
    def begin(id, manifest):
        calls.append(('begin',len(manifest)))
        assert len(manifest) == 28
        assert all(not row['path'].startswith('site/') for row in manifest)
        return [row['path'] for row in manifest if row['path'] != 'feed.xml']
    def stage(id, files):
        assert sum(len(f['data']) for f in files) <= 2 * 1024 * 1024
        assert all(f['path'] != 'feed.xml' for f in files)
        calls.append(('stage',len(files)))
        if fail_batch:
            raise OSError('Transfer interrupted')
    def commit(id, health):
        calls.append(('commit',health))
    runtime.env = SimpleNamespace(SITE=SimpleNamespace(beginPublication=begin,stagePublication=stage,commitPublication=commit))
    runtime.health = lambda: {'ready':True}
    if fail_batch:
        with pytest.raises(OSError,match='Transfer interrupted'):
            runtime.publish_public()
        assert all(name != 'commit' for name,_ in calls)
    else:
        runtime.publish_public()
        assert calls[-1] == ('commit',{'ready':True})
        assert sum(count for name,count in calls if name=='stage') == 27
    conn.close()
