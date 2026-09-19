"""Real story publication, Unicode timelines, OAuth return paths and withdrawals."""
import time
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup
import pytest

from rep0rter import hashtags
from rep0rter.i18n import LANGUAGES, page_name
from rep0rter.policy import add_rule, redact
from rep0rter.publishers import site
from rep0rter.store import Container, Event, Post, Store
from test_project_web import fields, login, post_form, web


def story_form(client, **changes):
    return dict(fields(client.get('/write')), title='Our first accessible map',
                description='We mapped step-free community spaces.', author='Community mapper',
                project_name='Open Civic Map', take_part='Help us survey your neighbourhood.',
                evidence='https://example.test/map\nhttps://example.test/notes',
                original='一起來畫地圖！', event_date='2026-01-15',
                hashtags='#CivicTech #開放資料 #civictech', owner_confirmed='yes', **changes)


def test_tag_normalization_and_extraction():
    assert hashtags.parse('＃CivicTech, #開放資料 #CIVICTECH, #시민기술 #café') == ['civictech', '開放資料', '시민기술', 'café']
    assert hashtags.extract('PR #123 https://example.test/#notatag #open-data #café. #開放資料！') == ['open-data', 'café', '開放資料']
    for invalid in ('../../x', '#', '#123', '#bad/tag', '#<script>', '#a' * 70, ' '.join(f'tag{i}' for i in range(9))):
        with pytest.raises(ValueError):
            hashtags.parse(invalid)


def test_google_returns_to_writer_with_tag_and_rejects_external_destination(web, monkeypatch):
    client = web[0].test_client()
    response = client.get('/write?tag=開放資料')
    data = fields(response)
    assert data['destination'] == '/write' and data['tag'] == '#開放資料'
    client, response = login(web, monkeypatch, client=client, login_data=data)
    assert unquote(response.location) == '/write?tag=開放資料'
    assert BeautifulSoup(client.get(response.location).data, 'html.parser').select_one('#hashtags')['value'] == '#開放資料'
    _, response = login(web, monkeypatch, login_data={'destination': 'https://evil.test'})
    assert response.location == '/projects'


def test_preview_publish_rss_timelines_and_duplicate_retry(web, monkeypatch):
    client, _ = login(web, monkeypatch)
    data = story_form(client)
    preview = client.post('/write', data=dict(data, action='preview'))
    assert preview.status_code == 200 and b'PREVIEW' in preview.data
    with Store(web[1].db_path) as store:
        assert store.post_count() == 0
    response = client.post('/write', data=data)
    assert response.status_code == 303
    assert client.post('/write', data=data).location == response.location
    cfg = web[1]
    story = BeautifulSoup(client.get(response.location).data, 'html.parser')
    assert 'Self-reported' in story.get_text()
    assert data['take_part'] in story.get_text()
    assert data['original'] in story.select_one('blockquote').get_text()
    assert {a.get_text() for a in story.select('a[rel=tag]')} == {'#civictech', '#開放資料'}
    assert story.select_one('a[href="https://example.test/notes"]')
    for language in LANGUAGES:
        path = cfg.site_dir / 'tags' / '開放資料' / page_name(language)
        doc = BeautifulSoup(path.read_text(), 'html.parser')
        assert doc.h1.get_text() == '#開放資料'
        assert doc.select_one('.story-timeline') and doc.select_one('a[href*="write?tag="]')
        assert doc.select_one('.day-heading time')['datetime'] == '2026-01-15'
        assert client.get('/tags/%E9%96%8B%E6%94%BE%E8%B3%87%E6%96%99/' + page_name(language)).status_code == 200
    categories = ElementTree.parse(cfg.site_dir / 'feed.xml').findall('./channel/item/category')
    assert {'#civictech', '#開放資料', 'Self-reported'} <= {c.text for c in categories}
    with Store(cfg.db_path) as store:
        assert store.post_count() == store.event_count() == 1
        event = store.get_event(store.conn.execute('SELECT event_id FROM posts').fetchone()[0])
        assert event.meta['self_reported'] and event.meta['hashtags'] == ['civictech', '開放資料']


def test_no_evidence_is_honest_and_text_is_escaped(web, monkeypatch):
    client, _ = login(web, monkeypatch)
    data = story_form(client)
    data.update(title='<script>alert(1)</script>', description='<img src=x onerror=alert(1)> #maps',
                evidence='', original='', take_part='<script>bad()</script>')
    response = client.post('/write', data=data)
    assert response.status_code == 303
    doc = BeautifulSoup(client.get(response.location).data, 'html.parser')
    assert not doc.select('article script, article img[onerror]')
    assert 'No evidence link supplied' in doc.get_text()
    assert '#maps' in [a.get_text() for a in doc.select('[rel=tag]')]


@pytest.mark.parametrize('changes', [
    {'evidence': 'javascript:alert(1)'}, {'evidence': 'https://user:secret@host.test'},
    {'evidence': 'https://host:bad'}, {'title': ''}, {'description': 'x' * 3001},
    {'original': 'x' * 5001}, {'hashtags': '#bad/tag'}, {'event_date': '2999-01-01'},
    {'event_date': 'not-a-date'}, {'owner_confirmed': ''}, {'csrf': 'wrong'},
])
def test_invalid_story_is_not_published_and_retains_form(web, monkeypatch, changes):
    client, _ = login(web, monkeypatch)
    data = story_form(client)
    data.update(changes)
    response = client.post('/write', data=data)
    assert response.status_code == 400
    with Store(web[1].db_path) as store:
        assert store.post_count() == 0


def test_story_requires_login_account_bound_token_and_shared_quota(web, monkeypatch):
    assert web[0].test_client().post('/write').status_code == 401
    client, _ = login(web, monkeypatch)
    other, _ = login(web, monkeypatch, claims={'sub': 'another-person'})
    stolen = story_form(client)
    stolen['csrf'] = fields(other.get('/write'))['csrf']
    assert other.post('/write', data=stolen).status_code == 403
    wrong_kind = story_form(client)
    wrong_kind['submission_token'] = fields(client.get('/submit'))['submission_token']
    assert client.post('/write', data=wrong_kind).status_code == 400
    monkeypatch.setattr(site, 'build', lambda *a: None)
    for _ in range(5):
        assert client.post('/write', data=story_form(client)).status_code == 303
    assert client.post('/submit', data=post_form(client)).status_code == 429
    assert client.post('/write', data=story_form(client)).status_code == 429


def test_failed_build_retries_without_duplicate(web, monkeypatch):
    client, _ = login(web, monkeypatch)
    data = story_form(client)
    real_build = site.build
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(site, 'build', fail)
    result = client.post('/write', data=data)
    assert result.status_code == 503 and b'story is saved' in result.data
    assert fields(result)['submission_token'] == data['submission_token']
    monkeypatch.setattr(site, 'build', real_build)
    assert client.post('/write', data=data).status_code == 303
    with Store(web[1].db_path) as store:
        assert store.post_count() == 1


def test_timelines_include_history_cross_sources_and_sort_by_event_date(web):
    cfg = web[1]
    with Store(cfg.db_path) as store:
        for index, (source, day) in enumerate((('slack', 300), ('github', 100), ('slack', 200))):
            container = Container(f'{source}:channel', source, 'civictech')
            store.upsert_container(container)
            event = Event(f'{source}:{index}', source, 'message' if source == 'slack' else 'pull_request', container.id, day,
                          text='Update #Shared #開放資料', author_name='Reporter',
                          meta={'visibility': 'public', 'eligible': True})
            store.upsert_events([event])
            store.add_post(Post(event.id, 1000 + index, 0, f'Story {index}', 'Summary',
                                translations={'en': {'headline': f'Story {index}', 'summary': 'Summary'}}))
        site.build(store, cfg, limit=1)
        doc = BeautifulSoup((cfg.site_dir / 'tags/shared/index.html').read_text(), 'html.parser')
        assert [a['data-post-id'] for a in doc.select('article')] == ['1', '3', '2']
        assert len(BeautifulSoup((cfg.site_dir / 'index.html').read_text(), 'html.parser').select('article')) == 1
        assert len(BeautifulSoup((cfg.site_dir / 'tags/civictech/index.html').read_text(), 'html.parser').select('article')) == 2
        add_rule(store, 'container', 'github:channel')
        redact(store, ['github:1'])
        assert len(BeautifulSoup((cfg.site_dir / 'tags/shared/index.html').read_text(), 'html.parser').select('article')) == 2
        add_rule(store, 'container', 'slack:channel')
        redact(store, ['slack:0', 'slack:2'])
        assert 'civictech' not in (cfg.site_dir / 'tags/civictech/index.html').read_text()
        assert not BeautifulSoup((cfg.site_dir / 'index.html').read_text(), 'html.parser').select('.hashtag-discovery')
        site.build(store, cfg)
        assert not (cfg.site_dir / 'tags').exists()
