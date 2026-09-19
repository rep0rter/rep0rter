"""JSON Feed normalization and atomic ingestion through the common collector."""
import json
from unittest.mock import Mock

import pytest

from rep0rter.collectors import json_feed, rss
from rep0rter.collectors.feed_common import object_id
from rep0rter.collectors.state import Metrics, read_state
from rep0rter.sources import eligible
from rep0rter.store import Store

FEED = 'https://civic.example/feed.json'
NOW = 1789776000


def item(**changes):
    return dict({'id': 'post-1', 'url': 'https://civic.example/posts/1',
                 'title': 'Civic <data> &amp; maps', 'content_text': 'Plain <text> &amp; links.',
                 'date_published': '2026-09-18T00:00:00Z'}, **changes)


def feed(items=None, **changes):
    return dict({'version': 'https://jsonfeed.org/version/1.1', 'title': 'Civic network',
                 'items': [item()] if items is None else items}, **changes)


def test_plain_text_publication_and_scoped_identity():
    data = feed([item(date_modified='2026-09-19T00:00:00Z', content_html='<p>Other version</p>')])
    name, events = rss.parse_feed(json.dumps(data).encode(), FEED)
    event = events[0]
    assert name == 'Civic network'
    assert event.text == 'Civic <data> &amp; maps\n\nPlain <text> &amp; links.'
    assert event.meta['feed_format'] == 'json_feed'
    assert event.meta['content_scope'] == 'feed_content'
    assert event.meta['timestamp_basis'] == 'published'
    assert event.meta['plain_text'] == event.text
    assert event.ts == NOW - 86400 and eligible(event)
    assert event.id == object_id(FEED, 'post-1')
    assert json_feed.parse(data, 'https://other.example/feed.json')[1][0].id != event.id


def test_html_cleaning_summary_and_external_url_fallback():
    html = '<p>Public <a href="https://example.org/data">data</a>.</p><script>unsafe()</script>'
    event = json_feed.parse(feed([item(url='', external_url='https://example.org/project',
                                      content_text='', content_html=html)]), FEED)[1][0]
    assert event.url == 'https://example.org/project'
    assert 'data (https://example.org/data)' in event.text
    assert '<p>' not in event.text and 'unsafe' not in event.text
    summary = json_feed.parse(feed([item(content_text='', content_html='<p> </p>',
                                         summary='Plain <summary> &amp; entities')]), FEED)[1][0]
    assert summary.text.endswith('Plain <summary> &amp; entities')
    assert summary.meta['content_scope'] == 'feed_excerpt'


@pytest.mark.parametrize('version', ['1', '1.1'])
def test_author_versions_and_inheritance(version):
    data = feed(version='https://jsonfeed.org/version/' + version, author={'name': 'Feed author'})
    assert json_feed.parse(data, FEED)[1][0].author_name == 'Feed author'
    data['items'][0]['author'] = {'name': 'Item author'}
    assert json_feed.parse(data, FEED)[1][0].author_name == 'Item author'
    data['items'][0]['authors'] = [{'name': 'One'}, {'name': 'Two'}, {'name': 'One'}]
    assert json_feed.parse(data, FEED)[1][0].author_name == 'One, Two'
    data['items'][0]['authors'] = []
    assert json_feed.parse(data, FEED)[1][0].author_name == ''
    del data['items'][0]['authors'], data['items'][0]['author']
    data['authors'] = [{'name': 'New feed author'}]
    assert json_feed.parse(data, FEED)[1][0].author_name == 'New feed author'


def test_modified_only_is_not_publication_evidence():
    entry = item(date_modified='2026-09-19T00:00:00+00:00')
    del entry['date_published']
    event = json_feed.parse(feed([entry]), FEED)[1][0]
    assert event.ts == NOW
    assert event.meta['timestamp_basis'] == 'modified'
    assert event.meta['eligible'] is False and not eligible(event)
    del entry['date_modified']
    with pytest.raises(ValueError, match='requires date_published'):
        json_feed.parse(feed([entry]), FEED)


@pytest.mark.parametrize('changes', [
    {'id': ''}, {'id': '  '}, {'id': 123}, {'id': None},
    {'url': 'javascript:alert(1)'}, {'url': '/relative'},
    {'url': 'https://user:password@example.org/post'}, {'url': None},
    {'date_published': 'yesterday'}, {'date_published': '2026-09-18'},
    {'date_published': '2026-09-18T00:00:00'}, {'date_published': None},
    {'content_text': {}}, {'content_html': []}, {'summary': 12}, {'title': []},
    {'authors': {}}, {'authors': ['name']}, {'author': None},
])
def test_invalid_entries_are_rejected(changes):
    with pytest.raises(ValueError):
        json_feed.parse(feed([item(**changes)]), FEED)


@pytest.mark.parametrize('data', [
    [], {}, {'version': 'https://jsonfeed.org/version/2'},
    feed(items={}), feed(items=['bad']), feed(title=[]), feed(version=[]),
    {'version': 'https://jsonfeed.org/version/1', 'items': []},
])
def test_invalid_feed_shape(data):
    with pytest.raises(ValueError):
        json_feed.parse(data, FEED)


def response(data):
    return Mock(content=json.dumps(data).encode(), status_code=200, headers={}, raise_for_status=Mock())


def test_collector_dedup_edits_and_no_pagination(tmp_path, monkeypatch):
    monkeypatch.setattr(rss.time, 'time', lambda: NOW)
    data = feed(next_url='https://other.example/next.json')
    session = Mock()
    session.get.return_value = response(data)
    metrics = Metrics()
    with Store(tmp_path / 'db') as store:
        assert rss.collect(store, [FEED, FEED], session, metrics) == 1
        session.get.assert_called_once_with(FEED, timeout=30)
        rss.collect(store, [FEED], session, metrics)
        assert store.event_count() == 1 and metrics.duplicate_payloads == 1
        data['items'][0]['content_text'] = 'Corrected article'
        session.get.return_value = response(data)
        rss.collect(store, [FEED], session, metrics)
        assert store.event_count() == 1 and metrics.updated_events == 1
        assert 'Corrected article' in store.get_event(object_id(FEED, 'post-1')).text


def test_bad_snapshot_does_not_store_partial_items_or_advance_success(tmp_path, monkeypatch):
    monkeypatch.setattr(rss.time, 'time', lambda: NOW)
    session = Mock()
    session.get.return_value = response(feed([]))
    cid = 'rss-feed:' + FEED
    with Store(tmp_path / 'db') as store:
        rss.collect(store, [FEED], session, Metrics())
        monkeypatch.setattr(rss.time, 'time', lambda: NOW + 60)
        session.get.return_value = response(feed([item(), item(id='broken', date_published='invalid')]))
        assert rss.collect(store, [FEED], session, Metrics()) == 0
        assert store.event_count() == 0
        state = read_state(store, cid)
        assert state['last_success'] == NOW and state['error']
        assert cid in json.loads(store.get_kv('collector_errors'))
