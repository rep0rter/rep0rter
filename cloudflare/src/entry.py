"""Native Python Worker, SQLite-backed Durable Object, and Browser Run."""
import asyncio
import gc
import hashlib
import hmac
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import sqlite3
import time
from urllib.parse import unquote, urljoin, urlsplit
import zipfile

import js
from revision import SOURCE_COMMIT
from pyodide.ffi import create_proxy, run_sync, to_js
from workers import DurableObject, Response, WorkerEntrypoint, wsgi
from rep0rter.runtime import services, DurabilityError

PAGE_SIZE = 4096
MAX_DATABASE = 32 * 1024 * 1024
MAX_FILE = 1_900_000


def obj(value):
    return to_js(value, dict_converter=js.Object.fromEntries)


def rows(cursor):
    value = cursor.toArray()
    result = value.to_py() if hasattr(value, "to_py") else value
    del cursor, value
    gc.collect()
    return result


def buffer_bytes(value):
    return bytes(value.to_py()) if hasattr(value, 'to_py') else bytes(value)


class Runtime:
    def __init__(self, ctx, env):
        self.ctx, self.env = ctx, env
        self.sql = ctx.storage.sql
        self.poisoned = False
        self.pages = []
        self.root = Path('/tmp/rep0rter')
        self.sql.exec('CREATE TABLE IF NOT EXISTS db_pages (number INTEGER PRIMARY KEY, data BLOB NOT NULL)')
        self.sql.exec('CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, data BLOB NOT NULL, digest TEXT NOT NULL)')
        self.sql.exec('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True)
        for row in rows(self.sql.exec('SELECT data FROM db_pages ORDER BY number')):
            self.pages.append(buffer_bytes(row['data']))
        if self.pages:
            (self.root / 'rep0rter.sqlite').write_bytes(b''.join(self.pages))
        for row in rows(self.sql.exec("SELECT path,data FROM files WHERE path='exclusions.json'")):
            path = self.root / row['path']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(buffer_bytes(row['data']))
        # Bindings are exposed only inside this Worker, never in source or logs.
        configuration = json.loads(getattr(env, 'REP0RTER_CONFIG', '{}'))
        if not isinstance(configuration, dict):
            raise ValueError('Invalid Worker configuration')
        for key, value in configuration.items():
            if key.startswith(('REP0RTER_', 'TELEGRAM_', 'AI_')) and isinstance(value, str):
                os.environ[key] = value
        os.environ['REP0RTER_SITE_URL'] = getattr(env, 'REP0RTER_SITE_URL', 'https://rep0rter.observe.tw')
        os.environ['REP0RTER_DATA_DIR'] = str(self.root)

    def meta(self, key, default=None):
        result = rows(self.sql.exec('SELECT value FROM meta WHERE key=?', key))
        return result[0]['value'] if result else default

    def set_meta(self, key, value):
        self.sql.exec('INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', key, str(value))

    def transaction(self, callback):
        # The Python RPC adapter creates persistent JS proxies for callbacks.
        # Explicitly release this one so it cannot retain complete DB snapshots
        # and generated-site buffers after the synchronous transaction returns.
        proxy = create_proxy(callback)
        try:
            return self.ctx.storage.transactionSync(proxy)
        finally:
            proxy.destroy()

    def commit_database(self, data):
        if len(data) > MAX_DATABASE:
            raise ValueError('Database exceeds configured memory budget')
        current = [data[i:i+PAGE_SIZE] for i in range(0, len(data), PAGE_SIZE)]
        changes = [(n, p) for n, p in enumerate(current) if n >= len(self.pages) or self.pages[n] != p]
        if not changes and len(current) == len(self.pages):
            return
        def write():
            for number, page in changes:
                self.sql.exec('INSERT INTO db_pages VALUES (?,?) ON CONFLICT(number) DO UPDATE SET data=excluded.data', number, to_js(page))
            self.sql.exec('DELETE FROM db_pages WHERE number>=?', len(current))
        self.transaction(write)
        self.pages = current
        gc.collect()

    def persist_policy(self, path):
        data = path.read_bytes()
        self.sql.exec('INSERT INTO files VALUES (?,?,?) ON CONFLICT(path) DO UPDATE SET data=excluded.data,digest=excluded.digest',
                      'exclusions.json', to_js(data), hashlib.sha256(data).hexdigest())

    def hydrate_site(self):
        # Withdrawals scrub the complete durable generation before any render
        # attempt. A later Browser Run failure must not leave old text online.
        directory = self.root / 'site'
        if directory.exists():
            if directory.is_symlink():
                directory.unlink()
            else:
                shutil.rmtree(directory)
        for item in rows(self.sql.exec("SELECT path FROM files WHERE path LIKE 'site/%'")):
            row = rows(self.sql.exec('SELECT data FROM files WHERE path=?', item['path']))[0]
            path = self.root / item['path']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(buffer_bytes(row['data']))
        gc.collect()

    def hydrate_assets(self, cfg):
        # Generated HTML is served straight from durable storage. Only cached
        # media are needed in Python memory to build the next generation.
        for row in rows(self.sql.exec("SELECT path FROM files WHERE path LIKE 'site/cards/%' OR path LIKE 'image-cache/%'")):
            path = self.root / row['path']
            if not path.exists():
                item = rows(self.sql.exec('SELECT data FROM files WHERE path=?', row['path']))[0]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(buffer_bytes(item['data']))
        gc.collect()

    def publish_site(self, cfg):
        # Prepare the whole generation before changing anything durable. Paths
        # removed by a withdrawal are deleted in the same transaction.
        files = {}
        for prefix in ('site', 'image-cache'):
            directory = self.root / prefix
            if directory.exists():
                for path in directory.rglob('*'):
                    if path.is_file():
                        data = path.read_bytes()
                        if len(data) > MAX_FILE:
                            raise ValueError('Generated file exceeds durable storage row limit')
                        files[f'{prefix}/{path.relative_to(directory).as_posix()}'] = data
        policy = self.root / 'exclusions.json'
        if policy.exists():
            files['exclusions.json'] = policy.read_bytes()
        existing = {r['path']: r['digest'] for r in rows(self.sql.exec('SELECT path,digest FROM files'))}
        changed = [(p, data, hashlib.sha256(data).hexdigest()) for p, data in files.items()]
        def write():
            for path, data, digest in changed:
                if existing.get(path) != digest:
                    self.sql.exec('INSERT INTO files VALUES (?,?,?) ON CONFLICT(path) DO UPDATE SET data=excluded.data,digest=excluded.digest', path, to_js(data), digest)
            for path in existing.keys() - files.keys():
                self.sql.exec('DELETE FROM files WHERE path=?', path)
            self.set_meta('site_built_at', time.time())
        self.transaction(write)
        gc.collect()
        self.publish_public()

    def health(self):
        return {'ready': self.meta('imported') == 'true' and self.meta('site_built_at') is not None,
                'runtime': 'cloudflare-workers', 'source_commit': SOURCE_COMMIT,
                'scheduled': getattr(self.env, 'RUN_ENABLED', 'false') == 'true',
                'last_started': self.meta('last_started'), 'last_finished': self.meta('last_finished'),
                'last_error': self.meta('last_error', ''),
                'collection_healthy': self.meta('collection_healthy')}

    def publish_status(self):
        site = getattr(self.env, 'SITE', None)
        if site is not None:
            run_sync(site.updateStatus(self.health()))

    def publish_public(self):
        site = getattr(self.env, 'SITE', None)
        if site is None:
            return
        files = []
        total = 0
        for item in rows(self.sql.exec("SELECT path FROM files WHERE path LIKE 'site/%'")):
            row = rows(self.sql.exec('SELECT data,digest FROM files WHERE path=?', item['path']))[0]
            data = buffer_bytes(row['data'])
            total += len(data)
            if total > 24 * 1024 * 1024:
                raise ValueError('Public site exceeds the RPC transfer budget')
            files.append({'path': item['path'][5:], 'data': data, 'digest': row['digest']})
        run_sync(site.publishSite(files, self.health()))
        gc.collect()

    def render_card(self, html):
        # Browser Run rate limits batches independently of the Worker. Honour
        # Retry-After and yield while waiting instead of losing a whole build.
        for attempt in range(8):
            response = run_sync(self.env.BROWSER.quickAction('screenshot', obj({
                'html': html, 'viewport': {'width': 1200, 'height': 630},
                'screenshotOptions': {'type': 'png', 'clip': {'x': 0, 'y': 0, 'width': 1200, 'height': 630}},
                'setJavaScriptEnabled': False, 'rejectRequestPattern': ['^https?://'],
            })))
            if response.ok:
                return buffer_bytes(run_sync(response.bytes()))
            if response.status != 429 or attempt == 7:
                raise RuntimeError(f'Browser Run screenshot failed ({response.status})')
            try:
                delay = min(120, max(10, float(response.headers.get('Retry-After') or '20')))
            except ValueError:
                delay = 20
            error = run_sync(response.text())
            if 'time limit exceeded' in error.lower():
                raise RuntimeError('Browser Run daily quota exhausted')
            run_sync(asyncio.sleep(delay))
        raise RuntimeError('Browser Run retry limit exceeded')

    def browser_document(self, url, timeout):
        import requests
        response = run_sync(self.env.BROWSER.quickAction('content', obj({
            'url': url, 'setJavaScriptEnabled': False,
            'allowResourceTypes': ['document'], 'allowRequestPattern': ['^' + re.escape(url) + '$'],
            'gotoOptions': {'waitUntil': 'domcontentloaded', 'timeout': min(timeout * 1000, 60000)},
        })))
        if not response.ok:
            raise RuntimeError(f'Browser Run document failed ({response.status})')
        value = json.loads(run_sync(response.text()))
        content = value.get('result') if isinstance(value, dict) else value
        if not isinstance(content, str):
            raise ValueError('Browser Run returned no document')
        result = requests.Response()
        result.status_code = 200
        result.url = url
        from rep0rter.collectors.browser import unwrap_document
        result._content = unwrap_document(content).encode()
        result.encoding = 'utf-8'
        return result

    def public_fetch(self, url, limit):
        # Workers fetch has no host LAN or container network. Resolve public
        # source hosts at the edge; never forward credentials across redirects.
        import ipaddress
        async def read():
            target = url
            for _ in range(4):
                parsed = urlsplit(target)
                if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                        or parsed.port not in (None, 443) or parsed.hostname == 'localhost'
                        or parsed.hostname.endswith(('.localhost', '.local', '.internal'))):
                    raise ValueError('Source must use public HTTPS')
                try:
                    address = ipaddress.ip_address(parsed.hostname)
                except ValueError:
                    address = None
                if address is not None and not address.is_global:
                    raise ValueError('Source must use public HTTPS')
                response = await js.fetch(target, obj({'redirect': 'manual',
                    'signal': js.AbortSignal.timeout(25000),
                    'headers': {'User-Agent': 'rep0rter/0.2 public-source'}}))
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get('Location')
                    if not location:
                        raise ValueError('Invalid source redirect')
                    target = urljoin(target, location)
                    continue
                if response.status != 200:
                    raise ValueError('Source returned an unsuccessful response')
                reader = response.body.getReader()
                data = bytearray()
                try:
                    while True:
                        chunk = await reader.read()
                        if chunk.done:
                            return bytes(data)
                        data.extend(chunk.value.to_bytes())
                        if len(data) > limit:
                            raise ValueError('Source exceeds size limit')
                finally:
                    await reader.cancel()
            raise ValueError('Too many source redirects')
        return run_sync(read())

    def fetch_image(self, url):
        from PIL import Image, ImageOps
        try:
            data = self.public_fetch(url, 3_000_000)
            with Image.open(io.BytesIO(data)) as original:
                if original.width * original.height > 16_000_000:
                    return None
                original.seek(0)
                image = ImageOps.exif_transpose(original).convert('RGBA')
                image.thumbnail((192, 192))
                output = io.BytesIO()
                image.save(output, 'PNG')
                return output.getvalue()
        except Exception:
            return None


class Reporter(DurableObject):
    def __init__(self, ctx, env):
        super().__init__(ctx, env)
        self.lock = asyncio.Lock()
        self.runtime = None
        self.app = None

    def load(self):
        if self.runtime is None or self.runtime.poisoned:
            self.runtime = Runtime(self.ctx, self.env)
            self.app = None
        return self.runtime

    async def schedule(self):
        if getattr(self.env, 'RUN_ENABLED', 'false') != 'true':
            return
        if not await self.ctx.storage.getAlarm():
            await self.ctx.storage.setAlarm(int(time.time()*1000) + 1000)

    async def alarm(self):
        async with self.lock:
            runtime = self.load()
            if getattr(self.env, 'RUN_ENABLED', 'false') != 'true' or runtime.meta('imported') != 'true':
                return
            # Reserve the hourly slot durably. Crash retries do not start another
            # publishing cycle; the delivery outbox retains ambiguous sends.
            if time.time() - float(runtime.meta('last_started', '0')) < 3500:
                return
            runtime.set_meta('last_started', time.time())
            runtime.publish_status()
            token = services.set(runtime)
            try:
                from rep0rter.cli import run_once
                from rep0rter.config import Config
                run_once(Config())
                from rep0rter.store import Store
                with Store(Config().db_path) as store:
                    collection = json.loads(store.get_kv('collector_health', '{}'))
                runtime.set_meta('collection_healthy', str(collection.get('healthy', False)).lower())
                runtime.set_meta('last_finished', time.time())
                runtime.set_meta('last_error', '')
                runtime.publish_status()
            except BaseException as exc:
                runtime.set_meta('last_error', type(exc).__name__)
                runtime.publish_status()
                raise
            finally:
                services.reset(token)

    async def fetch(self, request):
        path = urlsplit(request.url).path
        if path == '/healthz' or (not path.startswith(('/__admin/', '/auth/', '/projects')) and path not in ('/submit', '/write')):
            # Durable site transactions are atomic. Reading their published
            # files does not wait for a slow collector or model request.
            return await self.respond(request, path)
        async with self.lock:
            return await self.respond(request, path)

    async def respond(self, request, path):
        runtime = self.load()
        token = services.set(runtime)
        try:
            if path.startswith('/__admin/'):
                return await self.admin(request, path)
            if path == '/healthz':
                health = runtime.health()
                return Response.json(health, status=200 if health['ready'] else 503)
            if runtime.meta('imported') != 'true':
                return Response('Migration is not yet complete.', status=503)
            if path.startswith(('/auth/', '/projects')) or path in ('/submit', '/write'):
                if self.app is None:
                    from rep0rter.web import create_app
                    self.app = create_app()
                return await wsgi.fetch(self.app, request, self.env)
            if request.method not in ('GET', 'HEAD'):
                return Response('Method not allowed', status=405)
            relative = unquote(path).lstrip('/')
            if '..' in relative.split('/') or '\\' in relative or '\x00' in relative:
                return Response('Not found', status=404)
            if not relative or relative.endswith('/'):
                relative += 'index.html'
            found = rows(runtime.sql.exec('SELECT data,digest FROM files WHERE path=?', 'site/' + relative))
            if not found:
                return Response('Not found', status=404)
            row = found[0]
            mime = 'application/rss+xml; charset=utf-8' if relative.endswith('.xml') else mimetypes.guess_type(relative)[0] or 'application/octet-stream'
            headers = {'Content-Type': mime, 'Cache-Control': 'no-cache', 'ETag': '"' + row['digest'] + '"', 'X-Content-Type-Options': 'nosniff'}
            if request.headers.get('If-None-Match') == headers['ETag']:
                return Response(None, status=304, headers=headers)
            return Response(None if request.method == 'HEAD' else buffer_bytes(row['data']), headers=headers)
        except DurabilityError:
            return Response('Storage unavailable; please retry.', status=503)
        finally:
            services.reset(token)

    async def admin(self, request, path):
        secret = getattr(self.env, 'MIGRATION_TOKEN', '')
        if not secret or not hmac.compare_digest(request.headers.get('Authorization') or '', 'Bearer ' + secret):
            return Response('Not found', status=404)
        runtime = self.runtime
        if path == '/__admin/import' and request.method == 'POST':
            if runtime.meta('imported') == 'true' or getattr(self.env, 'RUN_ENABLED', 'false') == 'true':
                return Response('Import is disabled after activation.', status=409)
            data = await request.bytes()
            data = data.to_bytes() if hasattr(data, 'to_bytes') else bytes(data)
            if len(data) > MAX_DATABASE:
                return Response('Import too large', status=413)
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if sum(i.file_size for i in archive.infolist()) > 64 * 1024 * 1024:
                    return Response('Expanded import too large', status=413)
                for item in archive.infolist():
                    name = item.filename
                    if item.is_dir():
                        continue
                    if (name.startswith('/') or '..' in name.split('/') or '\\' in name
                            or not (name == 'rep0rter.sqlite' or name == 'exclusions.json' or name.startswith(('site/', 'image-cache/')))):
                        return Response('Invalid import path', status=400)
                database = archive.read('rep0rter.sqlite')
                check = sqlite3.connect(':memory:')
                try:
                    check.deserialize(database)
                    if check.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                        return Response('Invalid database', status=400)
                    counts = {t: check.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in ('events', 'posts', 'project_accounts')}
                finally:
                    check.close()
                if len(database) > MAX_DATABASE:
                    return Response('Database too large', status=413)
                runtime.commit_database(database)
                (runtime.root / 'rep0rter.sqlite').write_bytes(database)
                # Until the final imported marker, none of these files can be
                # served. Stream one file at a time to bound migration memory.
                runtime.sql.exec('DELETE FROM files')
                for item in archive.infolist():
                    if item.is_dir() or item.filename == 'rep0rter.sqlite':
                        continue
                    value = archive.read(item)
                    if len(value) > MAX_FILE:
                        return Response('Asset too large', status=413)
                    runtime.sql.exec('INSERT INTO files VALUES (?,?,?)', item.filename,
                                     to_js(value), hashlib.sha256(value).hexdigest())
                    if item.filename == 'exclusions.json':
                        (runtime.root / item.filename).write_bytes(value)
                    del value
                    gc.collect()
            runtime.set_meta('site_built_at', time.time())
            runtime.set_meta('imported', 'true')
            runtime.publish_public()
            return Response.json({'imported': True, 'counts': counts, 'sha256': hashlib.sha256(database).hexdigest()})
        if path == '/__admin/start' and request.method == 'POST':
            if runtime.meta('imported') != 'true' or getattr(self.env, 'RUN_ENABLED', 'false') != 'true':
                return Response('Reporting is not enabled', status=409)
            await self.schedule()
            return Response.json({'scheduled': True})
        if path == '/__admin/check' and request.method == 'POST':
            from rep0rter.config import Config
            from rep0rter.store import Store
            cfg = Config()
            with Store(cfg.db_path) as store:
                counts = {t: store.conn.execute('SELECT count(*) FROM ' + t).fetchone()[0] for t in ('events', 'posts', 'project_accounts')}
                integrity = store.conn.execute('PRAGMA integrity_check').fetchone()[0]
                # Exercise repeated committed writes without publishing or
                # retaining probe data, including Python/JS callback cleanup.
                for number in range(64):
                    store.set_kv('__worker_storage_probe', str(number))
                with store.conn:
                    store.conn.execute("DELETE FROM kv WHERE key='__worker_storage_probe'")
            picture = runtime.render_card('<html><body style="margin:0">rep0rter runtime check</body></html>')
            return Response.json({'counts': counts, 'integrity': integrity, 'browser_png': picture.startswith(b'\x89PNG'),
                'google_configured': cfg.google_login_enabled, 'llm_configured': cfg.llm_enabled})
        if path == '/__admin/rehearse' and request.method == 'POST':
            from rep0rter.config import Config
            from rep0rter.cli import run_once
            cfg = Config()
            if cfg.telegram_bot_token or getattr(self.env, 'RUN_ENABLED', 'false') == 'true':
                return Response('Rehearsal requires a preview without Telegram credentials', status=409)
            collected, posted = run_once(cfg)
            return Response.json({'collected': collected, 'posted': posted, 'telegram': 'disabled'})
        if path == '/__admin/collect' and request.method == 'POST':
            from rep0rter.config import Config
            from rep0rter.store import Store
            from rep0rter.collectors.registry import collect_all
            with Store(Config().db_path) as store:
                collected = collect_all(store, config=Config())
                health = json.loads(store.get_kv('collector_health', '{}'))
            return Response.json({'collected': collected, 'health': health})
        if path == '/__admin/build' and request.method == 'POST':
            from rep0rter.config import Config
            from rep0rter.store import Store
            from rep0rter.publishers.site import build
            cfg = Config()
            with Store(cfg.db_path) as store:
                build(store, cfg)
            return Response.json({'built': True})
        return Response('Not found', status=404)


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return await self.env.REPORTER.getByName('production').fetch(request)

    async def scheduled(self, controller):
        await self.env.REPORTER.getByName('production').schedule()
