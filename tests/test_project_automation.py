import json
import socket
import time
from types import SimpleNamespace

import pytest

from rep0rter import cli, project_automation as automation, project_sources, projects
from rep0rter.config import Config
from rep0rter.store import Store


@pytest.fixture
def managed(tmp_path, monkeypatch):
    now = time.time()
    monkeypatch.setattr(automation.time, 'time', lambda: now)
    store = Store(tmp_path / 'rep0rter.sqlite')
    with store.conn:
        store.conn.execute('INSERT INTO project_accounts VALUES (?, ?, ?, ?)', ('owner', 'google-owner', 'Owner', now))
    values = automation.validate(dict(title='Civic Map', description='unused', author='Owner',
        url='https://example.test/map', language='en', owner_confirmed='yes', source_kind='github',
        source_url='example/map', headline_template='$project: $title', summary_template='$summary\n$url',
        interval_hours='1', enabled='yes'))
    project_id = automation.save(store, 'owner', values)
    yield store, project_id, values, now
    store.close()


def release(id, now, **changes):
    from datetime import datetime, timezone
    return dict(id=id, name='New maps', tag_name='v1.0', body='Find public spaces and contribute open data.',
                html_url=f'https://github.com/example/map/releases/{id}', draft=False, prerelease=False,
                published_at=datetime.fromtimestamp(now, timezone.utc).isoformat(), **changes)


def test_github_new_releases_use_template_and_are_deduplicated(managed, monkeypatch):
    store, project_id, values, now = managed
    calls = []
    rows = [release(1, now - 100), release(2, now + 1)]
    def fetch(url):
        calls.append(url)
        return json.dumps(rows).encode()
    monkeypatch.setattr(project_sources, 'fetch', fetch)
    assert automation.run_due(store, now=now + 2) == 1
    post, event, _ = store.recent_posts()[0]
    assert post.headline == 'Civic Map: New maps'
    assert post.summary.endswith('/releases/2')
    assert event.author_name == 'Owner' and event.meta['automated_project']
    assert automation.run_due(store, now=now + 100) == 0
    assert len(calls) == 1  # Respect schedule
    assert automation.run_due(store, now=now + 3603) == 0
    assert len(calls) == 2 and store.post_count() == 1
    assert calls[0] == 'https://api.github.com/repos/example/map/releases?per_page=30'


def test_pause_or_edit_during_fetch_prevents_stale_publication(managed, monkeypatch):
    store, project_id, values, now = managed
    def fetch(url):
        automation.save(store, 'owner', dict(values, enabled=0), project_id)
        return json.dumps([release(1, now + 1)]).encode()
    monkeypatch.setattr(project_sources, 'fetch', fetch)
    assert automation.run_due(store, now=now + 2) == 0
    assert store.post_count() == 0


def test_rss_source_is_formatted_and_old_items_are_skipped(managed, monkeypatch):
    from datetime import datetime, timezone
    from email.utils import format_datetime
    store, project_id, values, now = managed
    automation.save(store, 'owner', dict(values, source_kind='rss', source_url='https://example.test/feed.xml'), project_id)
    date = format_datetime(datetime.fromtimestamp(now + 1, timezone.utc))
    xml = f'''<rss version="2.0"><channel><title>Project news</title><item><guid>new</guid>
        <title>Map launch</title><description>&lt;p&gt;New open data.&lt;/p&gt;</description>
        <link>https://example.test/news/launch</link><pubDate>{date}</pubDate></item></channel></rss>'''
    monkeypatch.setattr(project_sources, 'fetch', lambda url: xml.encode())
    assert automation.run_due(store, now=now + 2) == 1
    post = store.recent_posts()[0][0]
    assert post.headline == 'Civic Map: Map launch'
    assert post.summary == 'New open data.\nhttps://example.test/news/launch'


def test_template_validation_and_owner_isolation(managed):
    store, project_id, values, now = managed
    for invalid in ('$unknown', '${project.__class__}', '$', '${project[0]}'):
        with pytest.raises(projects.SubmissionError):
            automation.render_template_text(invalid, {'project': 'Map'})
    assert automation.render_template_text('$$5 for $project', {'project': 'Map'}) == '$5 for Map'
    with pytest.raises(projects.SubmissionError, match='not found'):
        automation.save(store, 'different-owner', values, project_id)
    for source in ('../../private', 'https://attacker.test/repo', 'owner/repo?secret=x'):
        with pytest.raises(projects.SubmissionError):
            automation.validate(dict(values, source_url=source, interval_hours='1', owner_confirmed='yes'))


def test_rate_limit_is_shared_by_manual_and_automatic_posts(managed, monkeypatch):
    store, project_id, values, now = managed
    monkeypatch.setattr(project_sources, 'fetch', lambda url: json.dumps([release(n, now + 1) for n in range(8)]).encode())
    assert automation.run_due(store, now=now + 2) == 5
    assert 'five stories or projects' in store.conn.execute('SELECT last_error FROM managed_projects').fetchone()[0]
    with pytest.raises(projects.SubmissionError) as exc:
        projects.publish(store, 'owner', 'manual', dict(title='Test', description='Test', author='Owner', language='en', url='https://example.test'))
    assert exc.value.status == 429
    assert store.event_count() == store.post_count() == 5


def test_failures_are_visible_and_retry_on_next_check(managed, monkeypatch):
    store, project_id, values, now = managed
    def fail(url):
        raise OSError('an external error with private details')
    monkeypatch.setattr(project_sources, 'fetch', fail)
    assert automation.run_due(store, now=now + 2) == 0
    row = store.conn.execute('SELECT * FROM managed_projects').fetchone()
    assert row['last_error'].startswith('Could not read') and 'private details' not in row['last_error']
    assert row['next_run'] == now + 3602
    monkeypatch.setattr(project_sources, 'fetch', lambda url: b'[]')
    assert automation.run_due(store, now=now + 3603) == 0
    assert store.conn.execute('SELECT last_error FROM managed_projects').fetchone()[0] == ''


def test_oversized_item_does_not_starve_later_news(managed, monkeypatch):
    store, project_id, values, now = managed
    too_large = release(1, now + 1)
    too_large['body'] = 'x' * 4000
    monkeypatch.setattr(project_sources, 'fetch', lambda url: json.dumps([too_large, release(2, now + 2)]).encode())
    assert automation.run_due(store, now=now + 3) == 1
    assert 'Description' in store.conn.execute('SELECT last_error FROM managed_projects').fetchone()[0]


def test_post_transaction_rolls_back_source_when_post_insert_fails(managed):
    import sqlite3
    store, project_id, values, now = managed
    store.conn.executescript("CREATE TRIGGER reject_post BEFORE INSERT ON posts BEGIN SELECT RAISE(ABORT, 'test failure'); END;")
    with pytest.raises(sqlite3.IntegrityError, match='test failure'):
        projects.publish(store, 'owner', 'atomic', dict(title='Test', description='Body', url='https://example.test', author='Owner', language='en'))
    assert store.event_count() == store.post_count() == 0
    assert store.conn.execute('SELECT COUNT(*) FROM project_submissions').fetchone()[0] == 0


def test_worker_runs_automation_before_build_and_dry_run_skips_it(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path)
    calls = []
    monkeypatch.setattr(cli, 'collect_all', lambda *args, **kwargs: 0)
    monkeypatch.setattr(cli, 'draft_posts', lambda *args, **kwargs: [])
    monkeypatch.setattr(automation, 'run_due', lambda store: calls.append('automatic') or 2)
    monkeypatch.setattr(cli.site_publisher, 'build', lambda *args: calls.append('build'))
    monkeypatch.setattr(cli, 'deliver_pending', lambda *args: None)
    monkeypatch.setattr(cli.telegram, 'publish', lambda *args, **kwargs: None)
    assert cli.run_once(cfg, no_llm=True) == (0, 2)
    assert calls == ['automatic', 'build']
    calls.clear()
    cli.run_once(cfg, no_llm=True, dry_run=True)
    assert calls == []


@pytest.mark.parametrize('address', ['127.0.0.1', '10.1.2.3', '169.254.169.254', '::1', 'fd00::1'])
def test_private_sources_are_rejected(monkeypatch, address):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [(None, None, None, None, (address, 443))])
    with pytest.raises(ValueError, match='public'):
        project_sources.public_address('https://source.test/feed')


def test_mixed_dns_and_unsafe_urls_are_rejected(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [
        (None, None, None, None, ('8.8.8.8', 443)), (None, None, None, None, ('127.0.0.1', 443))])
    for url in ('http://example.test/feed', 'https://user:pass@example.test/feed',
                'https://example.test:8080/feed', 'https://example.test/feed'):
        with pytest.raises(ValueError):
            project_sources.public_address(url)


def test_source_fetch_pins_dns_and_rejects_private_redirect(monkeypatch):
    addresses, requests = [], []
    def resolve(url):
        addresses.append(url)
        if len(addresses) > 1:
            raise ValueError('private redirect')
        return 'source.test', '8.8.8.8'
    monkeypatch.setattr(project_sources, 'public_address', resolve)
    class Pool:
        def __init__(self, host, **kwargs):
            assert host == '8.8.8.8' and kwargs['assert_hostname'] == 'source.test'
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def urlopen(self, method, target, **kwargs):
            requests.append(target)
            assert kwargs['redirect'] is False and kwargs['headers']['Host'] == 'source.test'
            return SimpleNamespace(status=302, headers={'Location': 'https://127.0.0.1/private'}, close=lambda: None)
    monkeypatch.setattr(project_sources.urllib3, 'HTTPSConnectionPool', Pool)
    with pytest.raises(ValueError, match='private redirect'):
        project_sources.fetch('https://source.test/feed')
    assert len(requests) == 1 and addresses[-1] == 'https://127.0.0.1/private'
