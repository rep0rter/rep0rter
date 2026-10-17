"""Native builds reuse stored media without constructing a complete WASM tree."""
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup
from PIL import Image
import pytest

from rep0rter.config import Config
from rep0rter.runtime import services
from rep0rter.store import Store, Container, Event, Post
from rep0rter.publishers import cards, site

from test_cloudflare_publication import load_runtime
from test_runner_transfer import engine, load, ROOT


def png():
    output = io.BytesIO()
    Image.new('RGB', (12, 12), 'teal').save(output, format='PNG')
    return output.getvalue()


@pytest.fixture
def native(engine, tmp_path, monkeypatch):
    module = load('native_publication', ROOT / 'cloudflare/src/native_publication.py')
    monkeypatch.setitem(__import__('sys').modules, 'native_publication', module)
    runtime_class = load_runtime(monkeypatch)
    runtime = runtime_class.__new__(runtime_class)
    runtime.__dict__.update(engine.runtime.__dict__)
    runtime.root = tmp_path / 'application'
    runtime.root.mkdir()
    runtime.poisoned = False
    runtime.pages = []
    (runtime.root / 'exclusions.json').write_text('{"schema":1,"rules":[],"tombstones":[]}')
    return SimpleNamespace(runtime=runtime, engine=engine, module=module)


def populate(cfg, count=2):
    with Store(cfg.db_path) as store:
        container = Container('slack:C', 'slack', 'Channel')
        store.upsert_container(container)
        for index in range(count):
            event = Event(f'slack:C:{index}', 'slack', 'message', container.id,
                          1700000000 + index, text=f'Source message {index}', author_name='Author',
                          url=f'https://example.test/source/{index}',
                          meta={'avatar_url': 'https://example.test/avatar.png'})
            store.upsert_events([event])
            store.add_post(Post(event.id, 1700000100 + index, 10, f'Headline {index}', f'Summary {index}',
                                translations={'en': {'headline': f'Headline {index}', 'summary': f'Summary {index}'}}))


def test_native_build_spools_pages_and_cards_and_reuses_them_without_rendering(native, monkeypatch):
    cfg = Config(data_dir=native.runtime.root)
    populate(cfg)
    monkeypatch.setattr(cards.CardRenderer, '_page', lambda self: None)
    monkeypatch.setattr(cards, '_fetch_image', lambda url: png())
    token = services.set(native.runtime)
    try:
        with Store(cfg.db_path) as store:
            site.build(store, cfg)
            files = native.engine.read_files()
            assert 'site/index.html' in files and 'site/feed.xml' in files
            assert any(name.startswith('image-cache/') for name in files)
            doc = BeautifulSoup(files['site/index.html'], 'html.parser')
            assert len(doc.select('article')) == 2
            for image in doc.select('article img'):
                assert 'site/' + image['src'] in files
            for language in ('index.html', 'index.zh-TW.html', 'index.ja.html', 'index.ko.html'):
                assert f'site/{language}' in files
                assert f'site/posts/1/{language}' in files
            # The published files live in SQL, not in a second complete FS tree.
            assert not list(cfg.site_dir.rglob('*.html'))
            assert not list(cfg.site_dir.rglob('cards/*.png'))
            assert not list((cfg.data_dir / 'image-cache').glob('*.png'))
            assert native.engine.conn.execute('SELECT count(*) FROM native_files').fetchone()[0] == 0
            monkeypatch.setattr(cards.CardRenderer, '_fallback', lambda *args: pytest.fail('cached card was rendered again'))
            monkeypatch.setattr(cards, '_fetch_image', lambda *args: pytest.fail('cached identity was fetched again'))
            site.build(store, cfg)
            assert BeautifulSoup(native.engine.read_files()['site/index.html'], 'html.parser').select('article')
    finally:
        services.reset(token)


def test_native_failure_does_not_publish_or_leave_staging_rows(native, monkeypatch):
    cfg = Config(data_dir=native.runtime.root)
    populate(cfg)
    def fail(*args, **kwargs):
        raise RuntimeError('rendering failed')
    monkeypatch.setattr(cards.CardRenderer, 'render', fail)
    token = services.set(native.runtime)
    try:
        with Store(cfg.db_path) as store:
            old = native.engine.read_files()
            with pytest.raises(RuntimeError, match='rendering failed'):
                site.build(store, cfg)
    finally:
        services.reset(token)
    assert native.engine.read_files() == old
    assert native.engine.conn.execute('SELECT count(*) FROM native_files').fetchone()[0] == 0
    assert native.runtime.publication is None


def test_native_commit_failure_rolls_back_sql_and_keeps_old_public_generation(native):
    runtime = native.runtime
    folder = runtime.root / 'staging'
    folder.mkdir()
    runtime.begin_site_generation(folder)
    runtime.stage_site_text(folder / 'index.html', 'new')
    runtime.stage_site_text(folder / 'feed.xml', 'feed')
    original = runtime.set_meta
    def fail(key, value):
        if key == 'site_built_at':
            raise OSError('commit failed')
        original(key, value)
    runtime.set_meta = fail
    with pytest.raises(OSError, match='commit failed'):
        runtime.publish_site(None)
    assert native.engine.read_files() == native.engine.initial
    assert native.engine.public == native.engine.initial
    assert native.engine.conn.execute('SELECT count(*) FROM native_files').fetchone()[0] == 0


def test_native_staging_budget_is_enforced_before_rows_accumulate(native, monkeypatch):
    directory = native.runtime.root / 'staged'
    directory.mkdir()
    monkeypatch.setattr(native.module, 'MAX_TOTAL', 16)
    native.runtime.begin_site_generation(directory)
    native.runtime.stage_site_text(directory / 'index.html', '12345678')
    native.runtime.stage_site_text(directory / 'feed.xml', '12345678')
    with pytest.raises(ValueError, match='storage budget'):
        native.runtime.stage_site_text(directory / 'extra.html', 'x')
    assert native.engine.conn.execute('SELECT sum(length(data)) FROM native_files').fetchone()[0] == 16
    native.runtime.abort_site_generation()


def test_withdrawal_does_not_hydrate_or_resurrect_any_old_media(native, monkeypatch):
    from rep0rter import policy
    cfg = Config(data_dir=native.runtime.root)
    populate(cfg, 1)
    native.engine.seed({'site/index.html': b'<html><body><article id="1">Source message 0</article></body></html>',
                        'site/feed.xml': b'<rss><channel><item><guid>1</guid></item></channel></rss>',
                        'site/cards/private.png': png(), 'image-cache/private.png': png()})
    # A failed older build's private local image must not be merged back either.
    (cfg.data_dir / 'image-cache').mkdir()
    (cfg.data_dir / 'image-cache/private.png').write_bytes(png())
    token = services.set(native.runtime)
    try:
        with Store(cfg.db_path) as store:
            policy.redact(store, ['slack:C:0'])
    finally:
        services.reset(token)
    files = native.engine.read_files()
    assert not any(name.startswith(('site/cards/', 'image-cache/')) for name in files)
    assert b'Source message 0' not in files['site/index.html']
    assert b'withdrawn' in files['site/posts/1/index.html'].lower()
    assert not list((cfg.site_dir / 'cards').glob('*'))


def test_native_withdrawal_scrubs_one_file_at_a_time_and_recovers_cross_page_links(native, monkeypatch):
    from rep0rter import policy
    cfg = Config(data_dir=native.runtime.root)
    populate(cfg, 2)
    text = b'x' * (1024 * 1024)
    native.engine.seed({
        'site/index.html': b'<html><body><article id="1"><p class="meta"><a rel="noopener" href="https://old.test/private">Source</a></p>Private</article>' + text + b'</body></html>',
        'site/posts/2/index.html': b'<html><body><article id="2">Keep</article><a href="https://old.test/private">Remove linked source</a>' + text + b'</body></html>',
        'site/other.html': b'<html><body>Keep other' + text + b'</body></html>',
    })
    sanitize = policy._sanitize_site
    observed = []
    def bounded(store, root, *args, **kwargs):
        size = sum(path.stat().st_size for path in root.rglob('*') if path.is_file())
        observed.append(size)
        assert size < 1_900_000
        return sanitize(store, root, *args, **kwargs)
    monkeypatch.setattr(policy, '_sanitize_site', bounded)
    token = services.set(native.runtime)
    try:
        with Store(cfg.db_path) as store:
            with store.conn:
                store.conn.execute('INSERT INTO retractions(post_id,requested_at) VALUES(1,0)')
            native.runtime.scrub_site(store, [])
    finally:
        services.reset(token)
    files = native.engine.read_files()
    assert len(observed) >= 4
    assert b'old.test/private' not in files['site/posts/2/index.html']
    assert b'Keep' in files['site/posts/2/index.html']
    assert b'Private' not in files['site/index.html']
    assert not (cfg.data_dir / '.withdrawal-file').exists()


def test_native_reuses_83_mib_cache_without_hydrating_card_files(native):
    runtime, conn = native.runtime, native.engine.conn
    content = png() + b'x' * (1024 * 1024)
    digest = hashlib.sha256(content).hexdigest()
    conn.executemany('INSERT INTO files VALUES (?,?,?)',
                     [(f'site/cards/{index}.png', content, digest) for index in range(83)])
    directory = runtime.root / 'staged'
    directory.mkdir()
    runtime.begin_site_generation(directory)
    runtime.stage_site_text(directory / 'index.html', 'new home')
    runtime.stage_site_text(directory / 'feed.xml', 'new feed')
    for index in range(83):
        path = directory / 'cards' / f'{index}.png'
        assert runtime.use_cached_card(path)
        assert runtime.asset_size(path) == len(content)
        assert not path.exists()
    runtime.publish_public = lambda: None
    runtime.publish_site(None)
    assert conn.execute("SELECT sum(length(data)) FROM files WHERE path LIKE 'site/cards/%'").fetchone()[0] > 64 * 1024 * 1024
    assert sum(path.stat().st_size for path in directory.rglob('*') if path.is_file()) == 0
