"""Bounded runner file transfer; complete generations stay in durable SQLite."""
import hashlib
import json
import re
import time
from urllib.parse import unquote

from workers import Response

MAX_FILE = 1_900_000
MAX_TOTAL = 160 * 1024 * 1024
MAX_SITE = 128 * 1024 * 1024
MAX_FILES = 10000
TTL = 1200


def rows(cursor):
    result = cursor.toArray()
    return result.to_py() if hasattr(result, 'to_py') else result


def raw(value):
    if hasattr(value, 'to_bytes'):
        return value.to_bytes()
    return bytes(value.to_py()) if hasattr(value, 'to_py') else bytes(value)


def valid_path(path):
    return (isinstance(path, str) and len(path) <= 2048 and '\\' not in path and '\x00' not in path
            and all(part not in ('', '.', '..') for part in path.split('/'))
            and (path == 'exclusions.json' or path.startswith(('site/', 'image-cache/'))))


def validate(manifest):
    if not isinstance(manifest, list) or len(manifest) > MAX_FILES:
        raise ValueError('Invalid file manifest')
    envelopes = ({'publication': 'f' * 32, 'files': manifest},
                 {'protocol': 2, 'database_size': 32 * 1024 * 1024, 'database_digest': 'f' * 64, 'files': manifest})
    if any(len(json.dumps(envelope).encode()) > 2 * 1024 * 1024 for envelope in envelopes):
        raise ValueError('File manifest exceeds transfer budget')
    seen, total, public = set(), 0, 0
    for item in manifest:
        if not isinstance(item, dict):
            raise ValueError('Invalid file manifest')
        path, size, digest = item.get('path'), item.get('size'), item.get('digest')
        if (not valid_path(path) or path in seen or type(size) is not int or not 0 <= size <= MAX_FILE
                or not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)):
            raise ValueError('Invalid file manifest')
        seen.add(path)
        total += size
        public += size if path.startswith('site/') else 0
    if any('/'.join(parts[:index]) in seen for path in seen
           for parts in [path.split('/')] for index in range(1, len(parts))):
        raise ValueError('Conflicting file and directory paths')
    if not {'site/index.html', 'site/feed.xml', 'exclusions.json'} <= seen:
        raise ValueError('Incomplete site generation')
    if total > MAX_TOTAL or public > MAX_SITE:
        raise ValueError('Site generation exceeds storage budget')


def prepare(runtime):
    runtime.sql.exec('CREATE TABLE IF NOT EXISTS runner_files(path TEXT PRIMARY KEY,digest TEXT NOT NULL,size INTEGER NOT NULL,data BLOB)')
    if float(runtime.meta('runner_upload_until', '0')) <= time.time():
        runtime.sql.exec('DELETE FROM runner_files')
        runtime.set_meta('runner_upload', '')


def discard(runtime):
    runtime.sql.exec('DELETE FROM runner_files')
    runtime.set_meta('runner_upload', '')
    runtime.set_meta('runner_upload_until', '0')


def snapshot(runtime):
    manifest = [dict(row) for row in rows(runtime.sql.exec('SELECT path,digest,length(data) AS size FROM files ORDER BY path'))]
    validate(manifest)
    digest = hashlib.sha256()
    for page in runtime.pages:
        digest.update(page)
    return {'protocol': 2, 'database_size': sum(map(len, runtime.pages)),
            'database_digest': digest.hexdigest(), 'files': manifest}


def handle(runtime, request, path, body):
    prepare(runtime)
    if path == '/__runner/snapshot':
        data = json.loads(body)
        name = data.get('path')
        if name == 'rep0rter.sqlite':
            offset, length = data.get('offset'), data.get('length')
            if (type(offset) is not int or type(length) is not int or offset < 0 or offset % 4096
                    or not 0 < length <= 1024 * 1024 or length % 4096
                    or offset + length > sum(map(len, runtime.pages))):
                return Response('Invalid database range', status=400)
            runtime.set_meta('runner_until', time.time() + TTL)
            return Response(b''.join(runtime.pages[offset // 4096:(offset + length) // 4096]), headers={'Cache-Control': 'no-store'})
        if not valid_path(name):
            return Response('Invalid snapshot path', status=400)
        found = rows(runtime.sql.exec('SELECT data FROM files WHERE path=?', name))
        if not found:
            return Response('Not found', status=404)
        runtime.set_meta('runner_until', time.time() + TTL)
        return Response(raw(found[0]['data']), headers={'Cache-Control': 'no-store'})
    if path == '/__runner/publish-begin':
        data = json.loads(body)
        token, manifest = data.get('publication'), data.get('files')
        if not isinstance(token, str) or not re.fullmatch('[a-f0-9]{32}', token):
            return Response('Invalid publication token', status=400)
        try:
            validate(manifest)
        except ValueError as error:
            return Response(str(error), status=400)
        digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        if runtime.meta('runner_upload') == token:
            if runtime.meta('runner_upload_digest') != digest:
                return Response('Publication token reused with different manifest', status=409)
        else:
            if token == runtime.meta('runner_upload_committed'):
                return Response('Publication already committed', status=409)
            def begin():
                discard(runtime)
                for item in manifest:
                    runtime.sql.exec('INSERT INTO runner_files VALUES (?,?,?,(SELECT data FROM files WHERE path=? AND digest=? AND length(data)=?))',
                                     item['path'], item['digest'], item['size'], item['path'], item['digest'], item['size'])
                runtime.set_meta('runner_upload', token)
                runtime.set_meta('runner_upload_digest', digest)
                runtime.set_meta('runner_upload_committed', '')
            runtime.transaction(begin)
        runtime.set_meta('runner_upload_until', time.time() + TTL)
        runtime.set_meta('runner_until', time.time() + TTL)
        missing = [row['path'] for row in rows(runtime.sql.exec('SELECT path FROM runner_files WHERE data IS NULL ORDER BY path'))]
        return Response.json({'missing': missing})
    token = request.headers.get('X-Publication') or ''
    if path == '/__runner/publish-commit' and token and token == runtime.meta('runner_upload_committed'):
        # The engine commit may have succeeded while the frontend transfer or
        # response failed. Retrying must finish that same public generation.
        runtime.publish_public()
        return Response.json({'published': True})
    if not token or token != runtime.meta('runner_upload'):
        return Response('Publication expired or replaced', status=409)
    if path == '/__runner/publish-file':
        name = unquote(request.headers.get('X-Asset-Path') or '')
        found = rows(runtime.sql.exec('SELECT digest,size FROM runner_files WHERE path=?', name))
        if (not found or len(body) > MAX_FILE or len(body) != found[0]['size']
                or hashlib.sha256(body).hexdigest() != found[0]['digest']):
            return Response('Asset does not match manifest', status=400)
        runtime.sql.exec('UPDATE runner_files SET data=? WHERE path=?', body, name)
        runtime.set_meta('runner_upload_until', time.time() + TTL)
        runtime.set_meta('runner_until', time.time() + TTL)
        return Response.json({'stored': True})
    if path == '/__runner/publish-commit':
        if rows(runtime.sql.exec('SELECT path FROM runner_files WHERE data IS NULL LIMIT 1')):
            return Response('Incomplete site generation', status=409)
        def commit():
            runtime.sql.exec('DELETE FROM files WHERE path NOT IN (SELECT path FROM runner_files)')
            runtime.sql.exec('INSERT OR REPLACE INTO files SELECT path,data,digest FROM runner_files WHERE NOT EXISTS (SELECT 1 FROM files WHERE files.path=runner_files.path AND files.digest=runner_files.digest)')
            runtime.set_meta('site_built_at', time.time())
            runtime.set_meta('runner_upload_committed', token)
            runtime.set_meta('runner_until', time.time() + TTL)
            discard(runtime)
        runtime.transaction(commit)
        runtime.publish_public()
        return Response.json({'published': True})
    return Response('Not found', status=404)
