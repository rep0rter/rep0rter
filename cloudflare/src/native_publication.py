"""Spool a native build into SQLite instead of keeping its files in WASM FS."""
import hashlib
import io
import gc
import shutil
import time

from PIL import Image

from runner_transfer import MAX_FILE, MAX_FILES, MAX_TOTAL, MAX_SITE, rows, raw, validate


class NativePublication:
    def __init__(self, runtime, directory, *, preserve_images=True):
        self.runtime, self.directory = runtime, directory
        self.preserve_images = preserve_images
        runtime.sql.exec('CREATE TABLE IF NOT EXISTS native_files(path TEXT PRIMARY KEY,data BLOB NOT NULL,digest TEXT NOT NULL)')
        runtime.sql.exec('DELETE FROM native_files')
        self.count = self.total = self.public = 0
        self.since_collection = 0

    def collect(self, size):
        # Bound delayed JS/Python bridge garbage without scanning the Python
        # heap for every small generated page.
        self.since_collection += size
        if self.since_collection >= 2 * 1024 * 1024:
            gc.collect()
            self.since_collection = 0

    def reserve(self, name, size):
        existing = rows(self.runtime.sql.exec('SELECT length(data) AS size FROM native_files WHERE path=?', name))
        delta = size - (existing[0]['size'] if existing else 0)
        count = self.count + (0 if existing else 1)
        total = self.total + delta
        public = self.public + (delta if name.startswith('site/') else 0)
        if count > MAX_FILES or total > MAX_TOTAL or public > MAX_SITE:
            raise ValueError('Native generation exceeds storage budget')
        self.count, self.total, self.public = count, total, public

    def copy(self, name):
        found = rows(self.runtime.sql.exec('SELECT length(data) AS size FROM files WHERE path=?', name))
        if found:
            self.reserve(name, found[0]['size'])
            self.runtime.sql.exec('INSERT OR REPLACE INTO native_files SELECT * FROM files WHERE path=?', name)

    def name(self, path):
        try:
            return 'site/' + path.relative_to(self.directory).as_posix()
        except ValueError:
            return None

    def put(self, name, content):
        if len(content) > MAX_FILE:
            raise ValueError('Generated file exceeds durable storage row limit')
        self.reserve(name, len(content))
        self.runtime.sql.exec('INSERT INTO native_files VALUES (?,?,?) ON CONFLICT(path) DO UPDATE SET data=excluded.data,digest=excluded.digest',
                              name, content, hashlib.sha256(content).hexdigest())
        self.collect(len(content))

    def text(self, path, content):
        name = self.name(path)
        if name is None or path.suffix not in ('.html', '.xml'):
            return False
        self.put(name, content.encode('utf-8'))
        return True

    def cached_card(self, path):
        name = self.name(path)
        if not name or not name.startswith('site/cards/'):
            return False
        if rows(self.runtime.sql.exec('SELECT path FROM native_files WHERE path=?', name)):
            return True
        existing = rows(self.runtime.sql.exec('SELECT data,length(data) AS size FROM files WHERE path=?', name))
        if not existing:
            return False
        try:
            # Verify one cached image at a time, retaining the previous cache's
            # corruption recovery without putting all card bytes in the FS.
            with Image.open(io.BytesIO(raw(existing[0]['data']))) as image:
                image.verify()
        except (OSError, ValueError):
            return False
        size = existing[0]['size']
        del existing
        self.collect(size)
        self.copy(name)
        return True

    def card(self, path):
        name = self.name(path)
        if not name or not name.startswith('site/cards/'):
            return False
        self.put(name, path.read_bytes())
        path.unlink()
        return True

    def size(self, path):
        name = self.name(path)
        found = rows(self.runtime.sql.exec('SELECT length(data) AS size FROM native_files WHERE path=?', name)) if name else []
        return found[0]['size'] if found else path.stat().st_size

    def keep_cards(self, keep):
        for item in rows(self.runtime.sql.exec("SELECT path FROM native_files WHERE path LIKE 'site/cards/%'")):
            if item['path'].rsplit('/', 1)[-1] not in keep:
                existing = rows(self.runtime.sql.exec('SELECT length(data) AS size FROM native_files WHERE path=?', item['path']))[0]
                self.count -= 1
                self.total -= existing['size']
                self.public -= existing['size']
                self.runtime.sql.exec('DELETE FROM native_files WHERE path=?', item['path'])

    def cache_image(self, path):
        try:
            name = path.relative_to(self.runtime.root).as_posix()
        except ValueError:
            return
        if name.startswith('image-cache/'):
            self.put(name, path.read_bytes())
            path.unlink()

    def commit(self):
        runtime = self.runtime
        if self.preserve_images:
            # Cache rows are copied within SQLite. No complete media cache is
            # transferred through Python or duplicated in the virtual FS.
            for item in rows(runtime.sql.exec("SELECT path FROM files WHERE path LIKE 'image-cache/%' AND path NOT IN (SELECT path FROM native_files)")):
                self.copy(item['path'])
        for prefix in ('site', 'image-cache'):
            if prefix == 'image-cache' and not self.preserve_images:
                continue
            folder = self.directory if prefix == 'site' else runtime.root / prefix
            if folder.exists():
                for path in folder.rglob('*'):
                    if path.is_file():
                        if path.stat().st_size > MAX_FILE:
                            raise ValueError('Generated file exceeds durable storage row limit')
                        self.put(prefix + '/' + path.relative_to(folder).as_posix(), path.read_bytes())
        policy = runtime.root / 'exclusions.json'
        if policy.exists():
            self.put('exclusions.json', policy.read_bytes())
        manifest = rows(runtime.sql.exec('SELECT path,digest,length(data) AS size FROM native_files'))
        validate(manifest)
        def commit():
            runtime.sql.exec('DELETE FROM files WHERE path NOT IN (SELECT path FROM native_files)')
            runtime.sql.exec('INSERT OR REPLACE INTO files SELECT * FROM native_files WHERE NOT EXISTS (SELECT 1 FROM files WHERE files.path=native_files.path AND files.digest=native_files.digest)')
            runtime.sql.exec('DELETE FROM native_files')
            runtime.set_meta('site_built_at', time.time())
        runtime.transaction(commit)

    def discard(self):
        self.runtime.sql.exec('DELETE FROM native_files')

    def scrub(self, store, withdrawn_urls):
        """Apply the existing conservative withdrawal sanitizer one file at a time."""
        from bs4 import BeautifulSoup
        from rep0rter import policy
        from rep0rter.i18n import LANGUAGES, page_name, page_aliases

        runtime = self.runtime
        withdrawn = {str(row['id']) for row in store.conn.execute(
            'SELECT p.id FROM posts p JOIN retractions r ON p.id=r.post_id')}
        links = set(withdrawn_urls)
        paths = [item['path'] for item in rows(runtime.sql.exec(
            "SELECT path FROM files WHERE path LIKE 'site/%' AND path NOT LIKE 'site/cards/%'"))]
        for name in paths:
            if name.endswith('.html'):
                content = raw(rows(runtime.sql.exec('SELECT data FROM files WHERE path=?', name))[0]['data'])
                document = BeautifulSoup(content, 'html.parser')
                for article in document.find_all('article'):
                    if str(article.get('id')) in withdrawn:
                        links.update(link['href'] for link in article.select('p.meta a[rel~=noopener][href]'))
                del content, document
                gc.collect()
        # Source links can live only in the generation being replaced. Retain
        # them durably before its commit, so frontend retries still scrub peers.
        policy.queue_public_withdrawal(store, links)
        for name in paths:
            if not name.endswith(('.html', '.xml')):
                self.copy(name)
                continue
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / name.removeprefix('site/')
            path.parent.mkdir(parents=True, exist_ok=True)
            content = raw(rows(runtime.sql.exec('SELECT data FROM files WHERE path=?', name))[0]['data'])
            path.write_bytes(content)
            del content
            policy._sanitize_site(store, self.directory, links, tombstones=False)
            self.put(name, path.read_bytes())
            shutil.rmtree(self.directory)
        # Every edition/alias gets its tombstone even when an interrupted older
        # generation did not have that particular permanent page yet.
        for post_id in withdrawn:
            for language in LANGUAGES:
                content = policy._withdrawn_page(language).encode()
                for filename in (page_name(language), *page_aliases(language)):
                    self.put(f'site/posts/{post_id}/{filename}', content)
        self.directory.mkdir(parents=True, exist_ok=True)
