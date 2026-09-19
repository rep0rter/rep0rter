"""Public Notion ingestion, anonymous transport, bounded scope, and feed routing."""
import json
from unittest.mock import Mock
from uuid import UUID

import pytest

from rep0rter.collectors import notion, registry
from rep0rter.collectors.state import BudgetExceeded, BudgetSession, Metrics, read_state
from rep0rter.config import Config
from rep0rter.policy import add_rule
from rep0rter.reporter import select_candidates
from rep0rter.sources import eligible
from rep0rter.store import Store


def uid(n):
    return str(UUID(int=n))


ROOT, VIEW_BLOCK, VIEW, COLLECTION, ROW, TEXT, UNRELATED = [uid(n) for n in range(1, 8)]
FEED = 'https://community.notion.site/Home-' + ROOT.replace('-', '')
CID = 'notion-page:' + ROOT
NOW = 1789776000
BODY = 'Join our public hackathon to build accessible transport maps using open data. Contributors from local communities can share their projects and collaborate on tools for residents.'


def block(key, kind='page', **kwargs):
    return dict(id=key, type=kind, alive=True, properties={'title': [['Community hackathon']]},
                created_time=(NOW - 3600) * 1000, last_edited_time=(NOW - 1800) * 1000, **kwargs)


def record_map(**kinds):
    return {kind: {key: {'spaceId': uid(100), 'value': {'value': value, 'role': 'reader'}}
                   for key, value in values.items()} for kind, values in kinds.items()}


def page_response(database=True):
    root = block(ROOT, content=[VIEW_BLOCK] if database else [TEXT])
    text = block(TEXT, 'text'); text['properties'] = {'title': [[BODY]]}
    return {'cursors': [], 'recordMap': record_map(
        block={ROOT: root, TEXT: text,
               VIEW_BLOCK: block(VIEW_BLOCK, 'collection_view', view_ids=[VIEW]),
               UNRELATED: block(UNRELATED, 'collection_view', view_ids=[uid(99)])},
        collection_view={VIEW: {'id': VIEW, 'type': 'table', 'format': {'collection_pointer': {'id': COLLECTION}}}},
        collection={COLLECTION: {'id': COLLECTION, 'space_id': uid(100), 'schema': {
            'description': {'type': 'text', 'name': 'Description'},
            'when': {'type': 'date', 'name': 'Event date'},
            'people': {'type': 'person', 'name': 'Private people'},
        }}})}


def query_response(more=False):
    row = block(ROW, parent_id=COLLECTION, parent_table='collection')
    row['properties'].update(description=[[BODY]], people=[['Do not ingest']],
                             when=[['‣', [['d', {'start_date': '2026-10-10'}]]]])
    return {'result': {'reducerResults': {'collection_group_results': {'blockIds': [ROW], 'hasMore': more}}},
            'recordMap': record_map(block={ROW: row, UNRELATED: block(UNRELATED)})}


def response(data=None, status=200, headers=None):
    return Mock(status_code=status, headers=headers or {}, json=Mock(return_value=data),
                content=json.dumps(data).encode(), raise_for_status=Mock())


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr(notion.time, 'time', lambda: NOW)


def test_public_database_dedup_edits_and_candidate_selection(tmp_path):
    with Store(tmp_path / 'db') as store:
        session = Mock(); session.post.side_effect = [response(page_response()), response(query_response())] * 2
        metrics = Metrics()
        assert notion.collect(store, [FEED, FEED + '?pvs=25'], session, metrics) == 1
        event = store.get_event('notion:' + ROW)
        assert eligible(event) and store.event_count() == 1
        assert event.ts == NOW - 3600  # Event date is not publication time.
        assert event.meta['updated_at'] == NOW - 1800
        assert event.meta['content_scope'] == 'database_properties'
        assert '2026-10-10' in event.text and 'Do not ingest' not in event.text
        assert [c.event.id for c in select_candidates(store, Config(), now=NOW)] == [event.id]
        assert notion.collect(store, [FEED], session, metrics) == 1
        assert store.event_count() == 1 and metrics.duplicate_payloads == 1
        changed = query_response()
        changed['recordMap']['block'][ROW]['value']['value']['properties']['description'] = [['Updated workshop and public dataset plans.']]
        session.post.side_effect = [response(page_response()), response(changed)]
        notion.collect(store, [FEED], session, metrics)
        assert 'Updated workshop' in store.get_event(event.id).text
        assert metrics.updated_events == 1
        # Neither unrelated recordMap collections nor unrelated pages become sources.
        assert read_state(store, CID)['database_count'] == 1
        assert not store.get_event('notion:' + UNRELATED)
        for call in session.post.call_args_list:
            assert call.kwargs['headers'] == {'Cookie': '', 'Authorization': ''}
            request = Mock()
            assert call.kwargs['auth'](request) is request


def test_standalone_page_uses_text_blocks_and_links(tmp_path):
    data = page_response(database=False)
    data['recordMap']['block'][TEXT]['value']['value']['properties']['title'] = [[BODY, [['a', 'https://example.test/register']]]]
    with Store(tmp_path / 'db') as store:
        session = Mock(); session.post.return_value = response(data)
        assert notion.collect(store, [FEED], session, Metrics()) == 1
        event = store.get_event('notion:' + ROOT)
        assert BODY in event.text and 'https://example.test/register' in event.text
        assert event.meta['content_scope'] == 'notion_page'
        assert not event.meta['context_incomplete']
        assert session.post.call_count == 1


def test_old_creation_is_not_made_fresh_by_edit(tmp_path):
    data = query_response()
    data['recordMap']['block'][ROW]['value']['value']['created_time'] = (NOW - 100 * 86400) * 1000
    with Store(tmp_path / 'db') as store:
        session = Mock(); session.post.side_effect = [response(page_response()), response(data)]
        assert notion.collect(store, [FEED], session, Metrics()) == 1
        assert store.get_event('notion:' + ROW).ts == NOW - 100 * 86400
        assert select_candidates(store, Config(), now=NOW) == []


@pytest.mark.parametrize('scope,subject', [('container', CID), ('event', 'notion:' + ROW)])
def test_exclusions(tmp_path, scope, subject):
    with Store(tmp_path / 'db') as store:
        add_rule(store, scope, subject)
        session = Mock(); session.post.side_effect = [response(page_response()), response(query_response())]
        assert notion.collect(store, [FEED], session, Metrics()) == 0
        assert store.event_count() == 0
        if scope == 'container':
            session.post.assert_not_called()


def test_rate_limit_preserves_healthy_snapshot_and_honors_backoff(tmp_path):
    with Store(tmp_path / 'db') as store:
        session = Mock(); session.post.side_effect = [response(page_response()), response(query_response()), response(status=429)]
        notion.collect(store, [FEED], session, Metrics())
        notion.collect(store, [FEED], session, Metrics())
        state = read_state(store, CID)
        assert state['last_success'] == NOW and state['retry_at'] >= NOW + 3600
        assert state['error'] and not state['history_complete']
        assert store.event_count() == 1
        session.reset_mock()
        notion.collect(store, [FEED], session, Metrics())
        session.post.assert_not_called()


@pytest.mark.parametrize('bad', [{}, {'recordMap': {}, 'cursors': []}, {'errorId': 'not_found'}])
def test_private_or_changed_response_is_failure_not_empty_success(tmp_path, bad):
    with Store(tmp_path / 'db') as store:
        session = Mock(); session.post.return_value = response(bad)
        assert notion.collect(store, [FEED], session, Metrics()) == 0
        assert read_state(store, CID)['error']
        assert 'last_success' not in read_state(store, CID)


def test_inaccessible_metadata_is_not_ingested(tmp_path):
    data = page_response(False)
    data['recordMap']['block'][ROOT]['value']['role'] = 'none'
    with Store(tmp_path / 'db') as store:
        session = Mock(); session.post.return_value = response(data)
        assert notion.collect(store, [FEED], session, Metrics()) == 0
        assert store.event_count() == 0 and read_state(store, CID)['error']


def test_truncated_collection_keeps_observed_rows_but_reports_gap(tmp_path):
    with Store(tmp_path / 'db') as store:
        session = Mock(); session.post.side_effect = [response(page_response()), response(query_response(more=True))]
        assert notion.collect(store, [FEED], session, Metrics()) == 1
        state = read_state(store, CID)
        assert not state['history_complete'] and 'incomplete' in state['error']
        assert 'last_success' not in state


def test_missing_row_fails_atomically_and_absence_does_not_delete(tmp_path):
    with Store(tmp_path / 'db') as store:
        session = Mock(); session.post.side_effect = [response(page_response()), response(query_response())]
        notion.collect(store, [FEED], session, Metrics())
        bad = query_response(); bad['result']['reducerResults']['collection_group_results']['blockIds'].append(uid(123))
        session.post.side_effect = [response(page_response()), response(bad)]
        assert notion.collect(store, [FEED], session, Metrics()) == 0
        assert read_state(store, CID)['error']
        empty = query_response(); empty['result']['reducerResults']['collection_group_results']['blockIds'] = []
        session.post.side_effect = [response(page_response()), response(empty)]
        assert notion.collect(store, [FEED], session, Metrics()) == 0
        assert eligible(store.get_event('notion:' + ROW))


def test_chunk_continuation_and_repeated_cursor_fail_closed(tmp_path):
    first = page_response(False); first['cursors'] = [{'stack': [['next']]}]
    second = page_response(False)
    with Store(tmp_path / 'db') as store:
        session = Mock(); session.post.side_effect = [response(first), response(second)]
        assert notion.collect(store, [FEED], session, Metrics()) == 1
        assert session.post.call_args_list[1].kwargs['json']['cursor'] == first['cursors'][0]
        session.post.side_effect = [response(first), response(first)]
        assert notion.collect(store, [FEED], session, Metrics()) == 0
        assert 'did not advance' in read_state(store, CID)['error']


def test_get_and_post_share_request_budget_even_when_post_fails():
    metrics = Metrics(); session = Mock(headers={}); reserve = Mock()
    session.get.return_value = response({})
    session.post.side_effect = RuntimeError('network down')
    transport = BudgetSession(session, metrics, limit=2, interval=0, reserve=reserve)
    transport.get('https://example.test/rss')
    with pytest.raises(RuntimeError, match='network down'):
        transport.post('https://example.test/public-json')
    with pytest.raises(BudgetExceeded):
        transport.get('https://example.test/rss')
    assert metrics.requests == 2 and reserve.call_count == 2


def test_unified_list_routes_rss_and_notion_with_isolated_failures(tmp_path, monkeypatch):
    feed = 'https://codefor.kr/boards/news.xml'
    monkeypatch.setenv('REP0RTER_FEEDS', feed + ',' + FEED)
    monkeypatch.setenv('REP0RTER_RSS_FEEDS', feed)
    monkeypatch.setattr(registry.slack_incremental, 'collect', Mock(return_value=0))
    rss_collect = Mock(return_value=2); monkeypatch.setattr(registry.rss, 'collect', rss_collect)
    with Store(tmp_path / 'db') as store:
        session = Mock(headers={}); session.post.return_value = response(status=429)
        assert registry.collect_all(store, session=session) == 2
        assert rss_collect.call_args.args[1] == [feed]
        health = json.loads(store.get_kv('collector_health'))
        assert health['sources']['rss']['healthy'] and not health['sources']['notion']['healthy']
        assert health['metrics']['requests'] == 1
        assert json.loads(store.get_kv('collector_daily_budget'))['requests'] == 1


@pytest.mark.parametrize('url', ['http://community.notion.site/' + ROOT, 'https://notion.site.evil.test/' + ROOT,
                              'https://user:password@community.notion.site/' + ROOT, 'https://community.notion.site/'])
def test_invalid_notion_urls(url):
    with pytest.raises(ValueError):
        notion.parse_url(url)
