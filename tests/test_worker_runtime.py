"""Persistence failures must never become successful publications."""
import sqlite3
from types import SimpleNamespace
import pytest

from rep0rter.runtime import connect, services, DurabilityError


@pytest.fixture
def runtime():
    state = SimpleNamespace(poisoned=False, durable=None, commits=0)
    def persist(data):
        state.durable = data
        state.commits += 1
    state.commit_database = persist
    token = services.set(state)
    try:
        yield state
    finally:
        services.reset(token)


def recovered(runtime):
    result = sqlite3.connect(':memory:')
    result.deserialize(runtime.durable)
    return result


def test_committed_transaction_survives_loss_of_local_files(runtime, tmp_path):
    db = connect(tmp_path / 'db.sqlite')
    db.execute('CREATE TABLE posts(id INTEGER PRIMARY KEY, title TEXT)')
    with db:
        db.execute('INSERT INTO posts VALUES (1,?)', ('First',))
        db.execute('INSERT INTO posts VALUES (2,?)', ('Second',))
        assert recovered(runtime).execute('SELECT count(*) FROM posts').fetchone()[0] == 0
    db.close()
    (tmp_path / 'db.sqlite').unlink()
    restored = recovered(runtime)
    assert restored.execute('SELECT * FROM posts').fetchall() == [(1, 'First'), (2, 'Second')]


def test_rollback_is_not_persisted(runtime, tmp_path):
    db = connect(tmp_path / 'db.sqlite')
    db.execute('CREATE TABLE posts(id INTEGER PRIMARY KEY, title TEXT)')
    with pytest.raises(sqlite3.IntegrityError):
        with db:
            db.execute("INSERT INTO posts VALUES (1,'First')")
            db.execute("INSERT INTO posts VALUES (1,'Duplicate')")
    assert db.execute('SELECT count(*) FROM posts').fetchone()[0] == 0
    assert recovered(runtime).execute('SELECT count(*) FROM posts').fetchone()[0] == 0
    db.close()


def test_failed_flush_aborts_invocation_even_through_exception_handlers(runtime, tmp_path):
    db = connect(tmp_path / 'db.sqlite')
    db.execute('CREATE TABLE posts(id INTEGER PRIMARY KEY)')
    def fail(data):
        raise OSError('Storage unavailable')
    runtime.commit_database = fail
    with pytest.raises(DurabilityError):
        with db:
            db.execute('INSERT INTO posts VALUES (1)')
    assert runtime.poisoned
    with pytest.raises(DurabilityError):
        db.execute('SELECT * FROM posts')
    assert recovered(runtime).execute('SELECT count(*) FROM posts').fetchone()[0] == 0
    db.close()


def test_explicit_commit_and_scripts_are_persistent(runtime, tmp_path):
    db = connect(tmp_path / 'db.sqlite')
    db.executescript('CREATE TABLE posts(id INTEGER PRIMARY KEY); INSERT INTO posts VALUES (1);')
    assert recovered(runtime).execute('SELECT * FROM posts').fetchall() == [(1,)]
    db.execute('INSERT INTO posts VALUES (2)')
    db.commit()
    assert recovered(runtime).execute('SELECT * FROM posts').fetchall() == [(1,), (2,)]
    db.close()


def test_uncommitted_close_does_not_publish(runtime, tmp_path):
    db = connect(tmp_path / 'db.sqlite')
    db.execute('CREATE TABLE posts(id INTEGER PRIMARY KEY)')
    db.execute('INSERT INTO posts VALUES (1)')
    db.close()
    assert recovered(runtime).execute('SELECT count(*) FROM posts').fetchone()[0] == 0


def test_wal_snapshot_can_be_reopened_after_restart(runtime, tmp_path):
    db = connect(tmp_path / 'db.sqlite')
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE posts(id INTEGER PRIMARY KEY)')
    with db:
        db.execute('INSERT INTO posts VALUES (1)')
    restored = tmp_path / 'restored.sqlite'
    restored.write_bytes(runtime.durable)
    with sqlite3.connect(restored) as check:
        assert check.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert check.execute('SELECT * FROM posts').fetchall() == [(1,)]
    db.close()


def test_withdrawal_publishes_scrubbed_site_before_any_rebuild(tmp_path):
    from rep0rter import policy
    from rep0rter.store import Store, Event, Post
    class Runtime:
        def __init__(self):
            self.ledger = None
            self.published = None
        def persist_policy(self, path):
            self.ledger = path.read_text()
        def hydrate_site(self):
            site = tmp_path / 'site'
            site.mkdir(exist_ok=True)
            (site / 'index.html').write_text('<html><body><article data-post-id="1">Secret</article></body></html>')
            (site / 'cards').mkdir(exist_ok=True)
            (site / 'cards/private.png').write_bytes(b'private')
        def publish_site(self, cfg):
            self.published = (tmp_path / 'site/posts/1/index.html').read_text()
            assert not (tmp_path / 'site/cards').exists()
    runtime = Runtime()
    with Store(tmp_path / 'db.sqlite') as store:
        event = Event('slack:C:1', 'slack', 'message', 'slack:C', 100, text='Secret')
        store.upsert_events([event])
        store.add_post(Post(event.id, 110, 7, 'Secret', 'Private summary'))
        token = services.set(runtime)
        try:
            policy.redact(store, [event.id])
        finally:
            services.reset(token)
    assert 'slack:C:1' in runtime.ledger
    assert runtime.published and 'Secret' not in runtime.published


def test_browser_xml_viewer_unwrap_preserves_feed_namespaces():
    from rep0rter.collectors.browser import unwrap_document
    from xml.etree import ElementTree as ET
    wrapped = '<html xmlns="http://www.w3.org/1999/xhtml"><body><div id="webkit-xml-viewer-source-xml"><rss xmlns="" xmlns:atom="http://www.w3.org/2005/Atom" version="2.0"><channel><title>A &amp; B</title><atom:link href="https://example.com/feed"/></channel></rss></div></body></html>'
    root = ET.fromstring(unwrap_document(wrapped))
    assert root.tag == 'rss'
    assert root.findtext('channel/title') == 'A & B'
    assert root.find('channel/{http://www.w3.org/2005/Atom}link').get('href') == 'https://example.com/feed'
    assert unwrap_document('<rss/>') == '<rss/>'
