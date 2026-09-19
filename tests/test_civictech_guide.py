"""Directory provenance and bounded ingestion, separate from fresh news."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from unittest.mock import Mock

import pytest

from rep0rter.collectors import civictech_guide as guide, rss
from rep0rter.collectors.feed_common import object_id
from rep0rter.collectors.state import BudgetSession, Metrics, read_state
from rep0rter.policy import add_rule
from rep0rter.sources import eligible
from rep0rter.store import Store

ROW = {
    'id': '047f2c8f-ffdc-4d05-af56-10b39739cf7a', 'slug': 'public-map',
    'title': 'Public map', 'url': 'https://example.org/map',
    'description': 'Map public transport.',
    'longDescription': '<p>Open community mapping.</p><script>unsafe()</script>',
    'added': '2026-09-18', 'created_at': '2026-09-19T15:10:51.749Z',
    'lastModified': '2026-09-19T16:00:00Z', 'status': 'active', 'status_raw': 'Active',
    'categories': ['Mapping'], 'tags': ['Open data'], 'projectTypes': ['Tool or platform'],
    'organizationType': ['Non-profit'], 'location': {'city': 'Taipei', 'country': 'Taiwan'},
    'raw_data': {'email': 'private@example.org'}, 'socials': {'email': 'private@example.org'},
}
NOW = datetime(2026, 9, 19, tzinfo=timezone.utc).timestamp()
CID = 'rss-feed:' + guide.URL


def body(rows=None, total=9344):
    return json.dumps({'data': [ROW] if rows is None else rows, 'meta': {'total': total}}).encode()


def response(content):
    return Mock(status_code=200, content=content, headers={}, raise_for_status=Mock())


def test_directory_provenance_attribution_and_no_news_eligibility():
    name, [event] = guide.parse(body(), guide.URL)
    assert name == guide.NAME
    assert event.id == object_id(guide.URL, ROW['id'])
    assert event.url == 'https://civictech.guide/projects/public-map'
    assert event.ts == NOW - 86400
    assert event.kind == 'directory_entry' and not eligible(event)
    assert event.meta['timestamp_basis'] == 'directory_added'
    assert event.meta['date_precision'] == 'day'
    assert event.meta['date_timezone'] == 'unspecified'
    assert event.meta['source_date'] == ROW['added']
    assert event.meta['snapshot_total'] == 9344 and not event.meta['history_complete']
    assert event.meta['snapshot_limit'] == 100
    assert event.meta['project_url'] == ROW['url']
    assert event.meta['attribution'] == guide.NAME and event.meta['license'] == 'CC BY 4.0'
    assert event.meta['categories'] == ['Mapping']
    assert 'unsafe' not in event.text and 'private@example.org' not in json.dumps(event.meta)
    assert 'Open community mapping.' in event.text
    assert not event.author_id and not event.author_name


def test_database_date_fallback_is_not_publication_or_modified_date():
    row = dict(ROW, added=None, created_at='2026-09-18T23:10:51.749+09:00')
    _, [event] = guide.parse(body([row]), guide.URL)
    assert event.ts == datetime.fromisoformat(row['created_at']).timestamp()
    assert event.meta['timestamp_basis'] == 'database_created'
    assert event.meta['date_fractional_digits'] == 3
    assert event.meta['date_timezone'] == '+0900'
    assert not eligible(event)


def test_uuid_identity_survives_listing_slug_changes_and_deduplicates():
    _, [first] = guide.parse(body([ROW, deepcopy(ROW)]), guide.URL)
    _, [edited] = guide.parse(body([dict(ROW, slug='renamed-map', title='Renamed map')]), guide.URL)
    assert first.id == edited.id and first.url != edited.url
    other = dict(ROW, id='0e3c2fd7-1662-4814-9234-1cb07a573dfa')
    assert len(guide.parse(body([ROW, other]), guide.URL)[1]) == 2
    with pytest.raises(ValueError, match='Conflicting duplicate'):
        guide.parse(body([ROW, dict(ROW, title='Conflicting')]), guide.URL)


def test_active_filter_and_empty_snapshot():
    assert guide.parse(body([dict(ROW, status_raw='Inactive')]), guide.URL)[1] == []
    assert guide.parse(body([], total=0), guide.URL)[1] == []


def test_root_alias_preserves_configured_feed_identity():
    alias = guide.URL.rstrip('/')
    _, [event] = guide.parse(body(), alias)
    assert event.container_id == 'rss-feed:' + alias
    assert event.id == object_id(alias, ROW['id'])


@pytest.mark.parametrize('change', [
    {'id': 'bad'}, {'slug': '../escape'}, {'url': 'javascript:alert(1)'},
    {'url': 'https://user:secret@example.org/'}, {'added': '2026-02-30'},
    {'added': None, 'created_at': '2026-09-18T00:00:00'},
    {'added': None, 'created_at': None}, {'lastModified': 'invalid'},
    {'categories': 'mapping'}, {'tags': [123]}, {'title': None},
    {'status_raw': 'broken'}, {'repository_url': 'mailto:private@example.org'},
])
def test_invalid_record_fails_whole_snapshot(change):
    row = {**ROW, 'id': '0e3c2fd7-1662-4814-9234-1cb07a573dfa', **change}
    with pytest.raises(ValueError):
        guide.parse(body([ROW, row]), guide.URL)


@pytest.mark.parametrize('payload', [
    b'[]', b'{}', b'{', b'{"data":[],"meta":{"total":true}}',
    b'{"data":[],"meta":{"total":1}}', body([ROW] * 101), body(total=0),
])
def test_malformed_envelope_and_unbounded_response_rejected(payload):
    with pytest.raises(ValueError):
        guide.parse(payload, guide.URL)


def test_collector_budget_dedup_edits_policy_and_atomic_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(rss.time, 'time', lambda: NOW)
    session = Mock()
    session.get.return_value = response(body())
    with Store(tmp_path / 'db') as store:
        metrics = Metrics()
        assert rss.collect(store, [guide.URL, guide.URL], session, metrics) == 1
        session.get.assert_called_once_with(guide.ENDPOINT, timeout=30)
        assert store.event_count() == 1
        assert rss.collect(store, [guide.URL], session, metrics) == 1
        assert store.event_count() == 1 and metrics.duplicate_payloads == 1
        session.get.return_value = response(body([dict(ROW, title='Edited map', slug='renamed-map')]))
        assert rss.collect(store, [guide.URL], session, metrics) == 1
        assert metrics.updated_events == 1
        event = store.get_event(object_id(guide.URL, ROW['id']))
        assert 'Edited map' in event.text and not eligible(event)
        assert event.url == 'https://civictech.guide/projects/renamed-map'
        assert event.ts == NOW - 86400
        session.get.return_value = response(body([ROW, dict(ROW, id='broken')]))
        assert rss.collect(store, [guide.URL], session, metrics) == 0
        assert read_state(store, CID)['error']
        assert read_state(store, CID)['last_success'] == NOW
        assert store.event_count() == 1
        add_rule(store, 'container', CID)
        session.reset_mock()
        assert rss.collect(store, [guide.URL], session, metrics) == 0
        session.get.assert_not_called()


def test_snapshot_uses_one_shared_budget_request_without_pagination(tmp_path, monkeypatch):
    monkeypatch.setattr(rss.time, 'time', lambda: NOW)
    transport = Mock(headers={})
    transport.get.return_value = response(body())
    metrics = Metrics()
    reserve = Mock()
    session = BudgetSession(transport, metrics, limit=1, interval=0, reserve=reserve)
    with Store(tmp_path / 'db') as store:
        assert rss.collect(store, [guide.URL], session, metrics) == 1
        assert metrics.requests == 1 and metrics.bytes == len(body())
        reserve.assert_called_once()
        transport.get.assert_called_once_with(guide.ENDPOINT, timeout=30)
        assert read_state(store, CID)['error'] == ''
