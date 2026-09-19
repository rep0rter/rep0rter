"""Leased GitHub runner access; committed database pages stay in Cloudflare."""
import base64
import hashlib
import hmac
import io
import json
import time
import zipfile
from pyodide.ffi import to_js
from workers import Response
from revision import SOURCE_COMMIT


def values(cursor):
    result = cursor.toArray()
    return result.to_py() if hasattr(result, 'to_py') else result


def raw(value):
    if hasattr(value, 'to_bytes'):
        return value.to_bytes()
    return bytes(value.to_py()) if hasattr(value, 'to_py') else bytes(value)


async def handle(owner, request, path):
    runtime = owner.runtime
    secret = getattr(owner.env, 'RUNNER_TOKEN', '')
    if (not secret or request.method != 'POST'
            or not hmac.compare_digest(request.headers.get('Authorization') or '', 'Bearer ' + secret)):
        return Response('Not found', status=404)
    if getattr(owner.env, 'RUNNER_ENABLED', 'false') != 'true':
        return Response('GitHub runner disabled', status=409)
    body = raw(await request.bytes())
    if len(body) > 32 * 1024 * 1024:
        return Response('Payload too large', status=413)
    now = time.time()
    if path == '/__runner/start':
        data = json.loads(body)
        if runtime.meta('imported') != 'true' or data.get('source_commit') != SOURCE_COMMIT:
            return Response('Import and matching deployed source commit required', status=409)
        if float(runtime.meta('runner_until', '0')) > now:
            return Response('Another reporting job is active', status=409)
        lease = data.get('lease', '')
        if not isinstance(lease, str) or len(lease) != 32 or data.get('mode') not in ('build', 'report'):
            return Response('Invalid lease', status=400)
        runtime.set_meta('runner_lease', lease)
        runtime.set_meta('runner_until', now + 1200)
        runtime.set_meta('runner_sequence', '0')
        runtime.set_meta('runner_payload_hash', '')
        runtime.set_meta('runner_mode', data['mode'])
        if data['mode'] == 'report':
            runtime.set_meta('last_started', now)
        runtime.publish_status()
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('rep0rter.sqlite', b''.join(runtime.pages))
            for item in values(runtime.sql.exec('SELECT path FROM files')):
                row = values(runtime.sql.exec('SELECT data FROM files WHERE path=?', item['path']))[0]
                archive.writestr(item['path'], raw(row['data']))
        return Response(output.getvalue(), headers={'Content-Type': 'application/zip', 'Cache-Control': 'no-store'})
    lease = request.headers.get('X-Runner-Lease') or ''
    if (not lease or not hmac.compare_digest(lease, runtime.meta('runner_lease', ''))
            or float(runtime.meta('runner_until', '0')) <= now):
        return Response('Reporting lease expired or replaced', status=409)
    if path == '/__runner/checkpoint':
        data = json.loads(body)
        sequence = int(data['sequence'])
        previous = int(runtime.meta('runner_sequence', '0'))
        digest = hashlib.sha256(body).hexdigest()
        if sequence == previous and digest == runtime.meta('runner_payload_hash'):
            return Response.json({'committed': sequence})
        if sequence != previous + 1:
            return Response('Checkpoint sequence mismatch', status=409)
        count = int(data['count'])
        if not 0 < count <= 8192:
            return Response('Invalid database page count', status=400)
        changes = [(int(number), base64.b64decode(content, validate=True)) for number, content in data['pages']]
        if any(number < 0 or number >= count or len(content) != 4096 for number, content in changes):
            return Response('Invalid database page', status=400)
        updated = runtime.pages[:count] + [b''] * max(0, count - len(runtime.pages))
        for number, content in changes:
            updated[number] = content
        if any(len(page) != 4096 for page in updated):
            return Response('Missing database pages', status=400)
        def commit():
            for number, content in changes:
                runtime.sql.exec('INSERT INTO db_pages VALUES (?,?) ON CONFLICT(number) DO UPDATE SET data=excluded.data', number, to_js(content))
            runtime.sql.exec('DELETE FROM db_pages WHERE number>=?', count)
            runtime.set_meta('runner_sequence', sequence)
            runtime.set_meta('runner_payload_hash', digest)
            runtime.set_meta('runner_until', time.time() + 1200)
        runtime.transaction(commit)
        runtime.pages = updated
        return Response.json({'committed': sequence})
    if path == '/__runner/policy':
        data = json.loads(body)
        if data.get('schema') != 1 or not isinstance(data.get('rules'), list) or not isinstance(data.get('tombstones'), list):
            return Response('Invalid exclusions ledger', status=400)
        (runtime.root / 'exclusions.json').write_bytes(body)
        runtime.persist_policy(runtime.root / 'exclusions.json')
        return Response.json({'saved': True})
    if path == '/__runner/publish':
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            names = archive.namelist()
            if 'site/index.html' not in names or 'site/feed.xml' not in names or len(names) != len(set(names)):
                return Response('Incomplete site generation', status=400)
            if sum(i.file_size for i in archive.infolist()) > 64 * 1024 * 1024:
                return Response('Expanded site too large', status=413)
            for item in archive.infolist():
                name = item.filename
                if (name.startswith('/') or '..' in name.split('/') or '\\' in name
                        or item.file_size > 1_900_000 or item.is_dir()
                        or not (name == 'exclusions.json' or name.startswith(('site/', 'image-cache/')))):
                    return Response('Invalid site path or size', status=400)
            runtime.sql.exec('CREATE TABLE IF NOT EXISTS staged_files(path TEXT PRIMARY KEY,data BLOB NOT NULL,digest TEXT NOT NULL)')
            runtime.sql.exec('DELETE FROM staged_files')
            for item in archive.infolist():
                content = archive.read(item)
                runtime.sql.exec('INSERT INTO staged_files VALUES (?,?,?)', item.filename, to_js(content), hashlib.sha256(content).hexdigest())
            def commit():
                runtime.sql.exec('DELETE FROM files')
                runtime.sql.exec('INSERT INTO files SELECT * FROM staged_files')
                runtime.sql.exec('DELETE FROM staged_files')
                runtime.set_meta('site_built_at', time.time())
                runtime.set_meta('runner_until', time.time() + 1200)
            runtime.transaction(commit)
        del body, archive, content
        runtime.publish_public()
        return Response.json({'published': True})
    if path == '/__runner/finish':
        data = json.loads(body)
        runtime.set_meta('runner_until', '0')
        runtime.set_meta('runner_lease', '')
        runtime.set_meta('last_error', '' if data.get('success') else 'GitHub reporting job failed')
        if data.get('success') and runtime.meta('runner_mode') == 'report':
            runtime.set_meta('last_finished', now)
            runtime.set_meta('collection_healthy', str(bool(data.get('collection_healthy'))).lower())
        runtime.poisoned = True
        runtime.publish_status()
        return Response.json({'released': True})
    return Response('Not found', status=404)
