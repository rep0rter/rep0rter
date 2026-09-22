"""Exercise frontend HTTP behavior with real Web Request/Response objects."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = shutil.which('node')
pytestmark = pytest.mark.skipif(NODE is None, reason='Node.js is required')
WORKER = Path(__file__).resolve().parents[1] / 'cloudflare/web/index.js'


def run_worker_checks(checks):
    # Only substitute Cloudflare's base classes; execute the production methods.
    source = WORKER.read_text().replace(
        "import { DurableObject, WorkerEntrypoint } from 'cloudflare:workers';",
        'class DurableObject { constructor(ctx, env) { this.ctx = ctx; this.env = env; } }\n'
        'class WorkerEntrypoint extends DurableObject {}',
    )
    setup = f"""
import assert from 'node:assert/strict';
const {{PublishedSite, default: Frontend}} = await import(
    'data:text/javascript;base64,' + Buffer.from({json.dumps(source)}).toString('base64'));
const assets = new Map();
let health = {{ready: true, runtime: 'cloudflare-workers'}};
const sql = {{exec(query, path) {{
    if (query.startsWith('CREATE TABLE')) return [];
    if (query === 'SELECT data,digest FROM assets WHERE path=?')
        return assets.has(path) ? [assets.get(path)] : [];
    if (query === "SELECT value FROM status WHERE key='health'")
        return [{{value: JSON.stringify(health)}}];
    throw new Error('Unexpected query: ' + query);
}}}};
const site = new PublishedSite({{storage: {{sql}}}}, {{}});
function request(path, options) {{ return new Request('https://rep0rter.example' + path, options); }}
function add(path) {{ assets.set(path, {{data: 'published bytes', digest: 'abc123'}}); }}
"""
    result = subprocess.run(
        [NODE, '--input-type=module', '--unhandled-rejections=strict', '-'],
        input=setup + checks, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_content_addressed_assets_keep_immutable_headers_on_head_and_revalidation():
    run_worker_checks("""
add('assets/fonts/dm-sans-latin-468d56b6b25b.woff2');
const path = '/assets/fonts/dm-sans-latin-468d56b6b25b.woff2';
for (const method of ['GET', 'HEAD']) {
    const response = await site.fetch(request(path, {method}));
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('Cache-Control'), 'public, max-age=31536000, immutable');
    assert.equal(response.headers.get('Content-Type'), 'font/woff2');
    assert.equal(response.headers.get('ETag'), '"abc123"');
    assert.equal(response.headers.get('X-Content-Type-Options'), 'nosniff');
    assert.equal(await response.text(), method === 'HEAD' ? '' : 'published bytes');
    const cached = await site.fetch(request(path, {method, headers: {'If-None-Match': '"abc123"'}}));
    assert.equal(cached.status, 304);
    assert.equal(cached.headers.get('Cache-Control'), 'public, max-age=31536000, immutable');
    assert.equal(cached.headers.get('ETag'), '"abc123"');
    assert.equal(await cached.text(), '');
}
const changed = await site.fetch(request(path, {headers: {'If-None-Match': '"old"'}}));
assert.equal(changed.status, 200);
assert.equal(await changed.text(), 'published bytes');
""")


def test_reports_feeds_and_revocable_cards_continue_to_revalidate():
    run_worker_checks("""
for (const path of ['index.html', 'posts/1/index.html', 'feed.xml', 'sources/japan/index.html',
                    'cards/abc123.png', 'assets-other/page.html']) {
    add(path);
    const response = await site.fetch(request('/' + path));
    assert.equal(response.status, 200);
    assert.equal(response.headers.get('Cache-Control'), 'no-cache');
    const cached = await site.fetch(request('/' + path, {headers: {'If-None-Match': '"abc123"'}}));
    assert.equal(cached.status, 304);
    assert.equal(cached.headers.get('Cache-Control'), 'no-cache');
}
assert.equal((await site.fetch(request('/'))).headers.get('Cache-Control'), 'no-cache');
const missing = await site.fetch(request('/assets/missing.woff2'));
assert.equal(missing.status, 404);
assert.ok(!missing.headers.get('Cache-Control')?.includes('immutable'));
""")


def test_health_and_account_routes_never_acquire_public_asset_cache_policy():
    run_worker_checks("""
for (const ready of [true, false]) {
    health.ready = ready;
    const response = await site.fetch(request('/healthz'));
    assert.equal(response.status, ready ? 200 : 503);
    assert.equal(response.headers.get('Cache-Control'), 'no-store');
}
const delegated = [];
const frontend = new Frontend({}, {
    BACKEND: {fetch(req) {
        delegated.push(new URL(req.url).pathname);
        return new Response('private', {headers: {'Cache-Control': 'no-store'}});
    }},
    SITE: {getByName() { throw new Error('Private route reached public storage'); }},
});
for (const path of ['/auth/login', '/auth/callback', '/projects', '/projects/1', '/submit', '/write', '/__admin/status']) {
    const response = await frontend.fetch(request(path));
    assert.equal(await response.text(), 'private');
    assert.equal(response.headers.get('Cache-Control'), 'no-store');
    assert.equal(delegated.at(-1), path);
}
""")
