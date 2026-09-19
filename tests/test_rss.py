"""RSS ingestion through storage, source policy, and editorial selection."""
import json
from unittest.mock import Mock
from xml.etree import ElementTree as ET

import pytest

from rep0rter.collectors import registry, rss
from rep0rter.collectors.state import Metrics, read_state
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
