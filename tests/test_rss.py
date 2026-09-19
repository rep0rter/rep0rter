"""RSS ingestion through storage, source policy, and editorial selection."""
import json
from unittest.mock import Mock
from xml.etree import ElementTree as ET

import pytest

from rep0rter.collectors import registry, rss
from rep0rter.collectors.state import BudgetExceeded, BudgetSession, Metrics, read_state
from rep0rter.config import Config
from rep0rter.policy import add_rule
from rep0rter.reporter import select_candidates
from rep0rter.sources import eligible
from rep0rter.store import Store

FEED = 'https://codefor.kr/boards/news.xml'
CID = 'rss-feed:' + FEED
NOW = 1789776000  # 2026-09-19 00:00 UTC
ITEM = '''<item>
<title>시민 해커톤 참가자 모집</title>
<description>&lt;p&gt;공개 데이터로 시민을 위한 교통 지도를 함께 만드는 해커톤입니다.
한국, 일본, 대만의 시민기술자들이 경험과 프로젝트를 나누고 협업합니다.
참가자 여러분의 프로젝트와 아이디어를 함께 나누어 주세요.&lt;/p&gt;
&lt;script&gt;unsafe()&lt;/script&gt;</description>
<link>https://codefor.kr/posts/example</link>
<guid>codefor-news-1</guid>
<pubDate>Fri, 18 Sep 2026 09:00:00 +0900</pubDate>
</item>'''


def response(items=ITEM, status=200, content=None, headers=None):
    body = content if content is not None else (
        '<rss version="2.0"><channel><title>코드포코리아 - 뉴스</title>'
        + items + '</channel></rss>').encode()
    return Mock(status_code=status, content=body, headers=headers or {}, raise_for_status=Mock())


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(rss.time, 'time', lambda: NOW)


def test_feed_ingestion_dedup_edits_and_editorial_selection(tmp_path):
    with Store(tmp_path / 'db') as store:
        session = Mock(); session.get.return_value = response()
        metrics = Metrics()
        assert rss.collect(store, [FEED, FEED], session, metrics) == 1
        assert session.get.call_count == 1
        event = store.get_event(rss.object_id(FEED, 'codefor-news-1'))
        assert eligible(event)
        assert event.ts == NOW - 86400
        assert 'unsafe' not in event.text and '<p>' not in event.text
        assert event.meta['content_scope'] == 'feed_excerpt'
        assert event.author_name == ''  # Do not invent an article author from the feed title.
        assert store.get_container(CID).name == '코드포코리아 - 뉴스'
        candidates = select_candidates(store, Config(), now=NOW)
        assert [c.event.id for c in candidates] == [event.id]
        rss.collect(store, [FEED], session, metrics)
        assert store.event_count() == 1 and metrics.duplicate_payloads == 1
        session.get.return_value = response(ITEM.replace('교통 지도', '재난 지도'))
        rss.collect(store, [FEED], session, metrics)
        assert store.event_count() == 1 and metrics.updated_events == 1
        assert '재난 지도' in store.get_event(event.id).text


def test_scoped_guid_and_link_fallback():
    item = ET.fromstring(ITEM)
    first = rss.to_event(item, FEED, 'News')
    assert first.id != rss.to_event(item, 'https://example.test/feed.xml', 'News').id
    item.remove(item.find('guid'))
    assert rss.to_event(item, FEED, 'News').id == rss.object_id(FEED, first.url)


def test_medium_encoded_content_and_author_preserve_publication_date():
    item = ET.fromstring(ITEM)
    ET.SubElement(item, '{http://purl.org/rss/1.0/modules/content/}encoded').text = (
        '<p>Article body from Medium.</p><script>unsafe()</script>')
    ET.SubElement(item, '{http://purl.org/dc/elements/1.1/}creator').text = 'Code for Korea'
    ET.SubElement(item, '{http://www.w3.org/2005/Atom}updated').text = '2026-09-19T00:00:00Z'
    event = rss.to_event(item, FEED, 'Medium')
    assert 'Article body from Medium.' in event.text
    assert '교통 지도' not in event.text and 'unsafe' not in event.text
    assert event.meta['content_scope'] == 'feed_content'
    assert event.author_name == 'Code for Korea'
    assert event.ts == NOW - 86400
    item.find('{http://purl.org/rss/1.0/modules/content/}encoded').text = '<p> </p>'
    fallback = rss.to_event(item, FEED, 'Medium')
    assert '교통 지도' in fallback.text
    assert fallback.meta['content_scope'] == 'feed_excerpt'


def test_odf_project_archive_filters_sitewide_feed_and_charges_budget(tmp_path, monkeypatch):
    from rep0rter.collectors import browser
    project = ITEM.replace('https://codefor.kr/posts/example',
                           'https://www.odf.or.kr/archive-project/?idx=123&amp;bmode=view')
    items = project + project.replace('/archive-project/', '/notice/') + project.replace(
        'www.odf.or.kr', 'unrelated.example')
    fetch = Mock(return_value=response(items))
    monkeypatch.setattr(browser, 'get_document', fetch)
    metrics = Metrics()
    reserve = Mock()
    session = BudgetSession(Mock(headers={}), metrics, limit=1, interval=0, reserve=reserve)
    with Store(tmp_path / 'db') as store:
        assert rss.collect(store, [rss.ODF_PROJECTS], session, metrics) == 1
        assert store.event_count() == 1
        event = store.get_event(rss.object_id(rss.ODF_PROJECTS, 'codefor-news-1'))
        assert event.meta['content_scope'] == 'feed_listing'
        assert event.meta['feed_url'] == rss.ODF_RSS
        assert event.meta['source_name'].endswith(' - 프로젝트')
        assert event.ts == NOW - 86400
        assert metrics.requests == 1 and metrics.bytes == len(response(items).content)
        reserve.assert_called_once()
        fetch.assert_called_once_with(rss.ODF_RSS, timeout=30)
        session.session.get.assert_not_called()
        with pytest.raises(BudgetExceeded):
            session.get_browser(rss.ODF_RSS)
        assert fetch.call_count == 1


def test_odf_browser_error_preserves_success_and_reports_failure(tmp_path, monkeypatch):
    from rep0rter.collectors import browser
    fetch = Mock(return_value=response(''))
    monkeypatch.setattr(browser, 'get_document', fetch)
    metrics = Metrics()
    session = BudgetSession(Mock(headers={}), metrics, interval=0)
    cid = 'rss-feed:' + rss.ODF_PROJECTS
    with Store(tmp_path / 'db') as store:
        rss.collect(store, [rss.ODF_PROJECTS], session, metrics)
        fetch.side_effect = RuntimeError('browser unavailable')
        rss.collect(store, [rss.ODF_PROJECTS], session, metrics)
        assert read_state(store, cid)['last_success'] == NOW
        assert read_state(store, cid)['error'] == 'browser unavailable'
        assert cid in json.loads(store.get_kv('collector_errors'))
        assert metrics.requests == 2


@pytest.mark.parametrize('scope,subject', [
    ('container', CID), ('event', rss.object_id(FEED, 'codefor-news-1')),
])
def test_exclusions_respected(tmp_path, scope, subject):
    with Store(tmp_path / 'db') as store:
        add_rule(store, scope, subject)
        session = Mock(); session.get.return_value = response()
        assert rss.collect(store, [FEED], session, Metrics()) == 0
        assert store.event_count() == 0
        if scope == 'container':
            session.get.assert_not_called()


def test_old_bootstrap_items_skipped_existing_items_refreshed_and_absence_not_deletion(tmp_path, monkeypatch):
    with Store(tmp_path / 'db') as store:
        session = Mock(); session.get.return_value = response()
        rss.collect(store, [FEED], session, Metrics())
        eid = rss.object_id(FEED, 'codefor-news-1')
        monkeypatch.setattr(rss.time, 'time', lambda: NOW + 30 * 86400)
        session.get.return_value = response(ITEM.replace('교통 지도', '재난 지도'))
        rss.collect(store, [FEED], session, Metrics())
        assert '재난 지도' in store.get_event(eid).text
        session.get.return_value = response('')
        rss.collect(store, [FEED], session, Metrics())
        assert eligible(store.get_event(eid))
    with Store(tmp_path / 'fresh' / 'db') as store:
        session.get.return_value = response()
        assert rss.collect(store, [FEED], session, Metrics()) == 0
        assert read_state(store, CID)['error'] == ''


@pytest.mark.parametrize('body', [
    b'<html>maintenance</html>', b'<rss version="2.0"><channel>',
    ('<rss version="2.0"><channel>' + ITEM + ITEM.replace('Fri, 18 Sep 2026 09:00:00 +0900', 'invalid') + '</channel></rss>').encode(),
    ('<rss version="2.0"><channel>' + ITEM.replace('https://codefor.kr/posts/example', 'javascript:alert(1)') + '</channel></rss>').encode(),
])
def test_bad_feed_keeps_last_success_and_does_not_commit_partial_items(tmp_path, body):
    with Store(tmp_path / 'db') as store:
        session = Mock(); session.get.return_value = response('')
        rss.collect(store, [FEED], session, Metrics())
        session.get.return_value = response(content=body)
        assert rss.collect(store, [FEED], session, Metrics()) == 0
        assert store.event_count() == 0
        state = read_state(store, CID)
        assert state['last_success'] == NOW and state['error']
        assert CID in json.loads(store.get_kv('collector_errors'))


def test_rate_limit_and_feed_failure_are_isolated(tmp_path):
    other = 'https://example.test/feed.xml'
    with Store(tmp_path / 'db') as store:
        session = Mock(); session.get.side_effect = [response(status=429, headers={'Retry-After': '3600'}), response()]
        assert rss.collect(store, [FEED, other], session, Metrics()) == 1
        assert read_state(store, CID)['retry_at'] == NOW + 3600
        session.reset_mock()
        rss.collect(store, [FEED], session, Metrics())
        session.get.assert_not_called()


def test_registry_wires_allowlisted_feed_and_shared_budget(tmp_path, monkeypatch):
    monkeypatch.setenv('REP0RTER_RSS_FEEDS', FEED)
    monkeypatch.setattr(registry.slack_incremental, 'collect', Mock(return_value=0))
    with Store(tmp_path / 'db') as store:
        session = Mock(headers={}); session.get.return_value = response()
        assert registry.collect_all(store, session=session) == 1
        health = json.loads(store.get_kv('collector_health'))
        assert health['sources']['rss']['healthy']
        assert health['metrics']['requests'] == 1
        assert health['metrics']['new_events'] == 1
        assert json.loads(store.get_kv('collector_daily_budget'))['requests'] == 1
        monkeypatch.delenv('REP0RTER_RSS_FEEDS')
        session.reset_mock()
        registry.collect_all(store, session=session)
        session.get.assert_not_called()


def test_bounded_feeds_leave_unused_budget_for_slack_backlog(tmp_path, monkeypatch):
    feeds = [f'https://example.test/feed-{n}.xml' for n in range(15)]
    monkeypatch.setenv('REP0RTER_FEEDS', ','.join(feeds))
    monkeypatch.setenv('REP0RTER_COLLECT_REQUEST_BUDGET', '80')
    def slack_collect(store, days, max_channels, session, *, metrics):
        assert metrics.requests == 15
        assert session.remaining == 65
        for _ in range(40):
            session.get('https://archive.example.test/channel')
        return 0
    monkeypatch.setattr(registry.slack_incremental, 'collect', slack_collect)
    monkeypatch.setattr('rep0rter.collectors.state.time.sleep', lambda _: None)
    with Store(tmp_path / 'db') as store:
        session = Mock(headers={})
        session.get.return_value = response()
        assert registry.collect_all(store, session=session) == 15
        assert session.get.call_count == 55
        assert json.loads(store.get_kv('collector_daily_budget'))['requests'] == 55
        assert json.loads(store.get_kv('collector_health'))['healthy']


def test_future_feed_item_waits_for_its_original_publication_time(tmp_path, monkeypatch):
    session = Mock()
    session.get.return_value = response(ITEM.replace('18 Sep', '20 Sep'))
    with Store(tmp_path / 'db') as store:
        assert rss.collect(store, [FEED], session, Metrics()) == 0
        assert store.event_count() == 0
        monkeypatch.setattr(rss.time, 'time', lambda: NOW + 86400)
        assert rss.collect(store, [FEED], session, Metrics()) == 1
        assert store.event_count() == 1


@pytest.mark.parametrize('mode', ['shadow', 'active'])
def test_korean_feeds_use_shared_story_writer_and_delivery(tmp_path, monkeypatch, mode):
    from rep0rter import reporter
    from rep0rter.delivery import prepare_posts
    from rep0rter.writer_contract import WriteResult

    cfg = Config(data_dir=tmp_path, editorial_mode=mode)
    feeds = [FEED, 'https://codefor.kr/boards/civic-tech-projects.xml']
    with Store(cfg.db_path) as store:
        session = Mock()
        session.get.return_value = response()
        rss.collect(store, feeds, session, Metrics())
        assert store.event_count() == 2
        assert store.post_count() == 0
        writer = Mock(return_value=WriteResult(
            headline='시민 해커톤', summary='공개 데이터로 교통 지도를 만듭니다',
            translations={'ko': {'headline': '시민 해커톤', 'summary': '공개 데이터로 교통 지도를 만듭니다'}}))
        monkeypatch.setattr(reporter, 'write', writer)
        drafts = reporter.draft_posts(store, cfg, use_llm=False)
        assert len(drafts) == 1  # Shared story logic merges the crossposted article.
        writer.assert_called_once()
        candidate, post = drafts[0]
        assert candidate.event.source == 'rss'
        assert post.published_at == NOW
        assert candidate.event.ts == NOW - 86400
        prepare_posts(cfg, store, drafts)
        assert store.post_count() == 1
        assert store.conn.execute('SELECT COUNT(*) FROM story_posts').fetchone()[0] == 1
        assert store.conn.execute('SELECT COUNT(*) FROM delivery_jobs').fetchone()[0] == 1
        assert store.conn.execute('SELECT COUNT(*) FROM writer_audits').fetchone()[0] == 1
        assert reporter.draft_posts(store, cfg, use_llm=False) == []
        assert writer.call_count == 1


def test_korean_archive_and_low_information_items_keep_shared_selection_rules(tmp_path):
    with Store(tmp_path / 'db') as store:
        session = Mock()
        session.get.return_value = response(ITEM.replace('18 Sep', '01 Sep'))
        rss.collect(store, [FEED], session, Metrics(), days=30)
        assert store.event_count() == 1  # Collection alone does not publish archives.
        assert select_candidates(store, Config(), now=NOW) == []
        session.get.return_value = response(ITEM.replace(
            '<title>시민 해커톤 참가자 모집</title>', '<title>안녕하세요</title>').replace(
            ITEM.split('<description>')[1].split('</description>')[0], '반갑습니다').replace(
            'codefor-news-1', 'codefor-news-low-information'))
        rss.collect(store, [FEED], session, Metrics())
        fresh = store.get_event(rss.object_id(FEED, 'codefor-news-low-information'))
        assert fresh.ts == NOW - 86400
        assert select_candidates(store, Config(), now=NOW) == []
