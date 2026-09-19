import inspect
import json
from unittest.mock import Mock

import pytest

from rep0rter import cli, notion_discovery
from rep0rter.collectors import registry

PORTAL = 'https://example-space.notion.site/Home-9dd9cd85f07942c1bd5f6ef73efdb122'
PAGE_ID = '9dd9cd85-f079-42c1-bd5f-6ef73efdb122'
SPACE_ID = '6bd3b2ca-1999-4886-a253-beb5b6d498da'
COLLECTION = 'f1c41b21-a503-4f99-9629-ca4cedef9523'
VIEW = '6c2f0fee-b826-479f-85cb-ddbe73e89d0e'


def response(value, code=200):
    return Mock(status_code=code, json=Mock(return_value=value))


def wrap(value):
    return {'value': {'value': value}}


def schema(**changes):
    value = {
        'title': {'name': '名前', 'type': 'title'},
        'aB1c': {'name': 'GitHub', 'type': 'url'},
        'dE2f': {'name': 'ステータス', 'type': 'select'},
        'gH3i': {'name': '😀 メンバー/Members', 'type': 'person'},
        'jK4l': {'name': '連絡先', 'type': 'email'},
    }
    value.update(changes)
    return value


def row(title='Civic project', github='https://github.com/example/civic', **extra):
    properties = {'title': [[title]], 'aB1c': [[github]], 'dE2f': [['アクティブ']],
                  'gH3i': [['Some Person']], 'jK4l': [['person@example.test']]}
    properties.update(extra)
    return wrap({'type': 'page', 'parent_id': COLLECTION, 'properties': properties})


def portal_session(rows=None, size_hint=None, page_blocks=None, repos=None, schema_value=None):
    """Offline double for the two Notion endpoints and the GitHub verification."""
    rows = [row()] if rows is None else rows
    blocks = {str(index): value for index, value in enumerate(rows)}
    page = page_blocks if page_blocks is not None else {
        'root': wrap({'type': 'page'}),
        'view': wrap({'type': 'collection_view', 'view_ids': [VIEW],
                      'format': {'collection_pointer': {'id': COLLECTION}}}),
    }

    def post(url, json=None, timeout=None):
        if 'getPublicPageData' in url:
            return response({'spaceId': SPACE_ID, 'spaceName': 'Portal', 'requireLogin': False,
                             'isPublicShareLink': False, 'isDeleted': False, 'publicAccessRole': 'reader'})
        if 'loadCachedPageChunkV2' in url:
            return response({'recordMap': {'block': page}})
        if 'queryCollection' in url:
            return response({'result': {'sizeHint': len(rows) if size_hint is None else size_hint},
                             'recordMap': {'block': blocks, 'collection': {COLLECTION: wrap(
                                 {'name': [['プロジェクト']],
                                  'schema': schema() if schema_value is None else schema_value})}}})
        raise AssertionError('unexpected notion endpoint ' + url)

    def get(url, timeout=None, headers=None):
        name = url.rsplit('/repos/', 1)[-1]
        if repos is not None and name not in repos:
            return response({}, code=404)
        info = (repos or {}).get(name, {})
        return response({'full_name': name, 'private': False, 'visibility': 'public',
                         'archived': info.get('archived', False),
                         'pushed_at': info.get('pushed_at', '2026-09-18T00:00:00Z'),
                         'stargazers_count': info.get('stars', 1), 'description': ''})

    return Mock(post=Mock(side_effect=post), get=Mock(side_effect=get))


def test_portal_url_must_be_one_published_notion_space():
    assert notion_discovery.parse_portal(PORTAL) == ('example-space', PAGE_ID)
    for bad in ['http://example-space.notion.site/Home-' + PAGE_ID.replace('-', ''),
                'https://example-space.notion.site/Home',
                'https://notion.site/Home-9dd9cd85f07942c1bd5f6ef73efdb122',
                'https://evil.test/example-space.notion.site/Home-9dd9cd85f07942c1bd5f6ef73efdb122',
                'https://user@example-space.notion.site/Home-9dd9cd85f07942c1bd5f6ef73efdb122', '']:
        with pytest.raises(ValueError):
            notion_discovery.parse_portal(bad)


@pytest.mark.parametrize('change', [{'requireLogin': True}, {'isDeleted': True},
                                    {'publicAccessRole': None}, {'publicAccessRole': 'none'},
                                    {'publicAccessRole': 'editor'}, {'spaceId': ''}])
def test_non_public_portals_are_refused(change):
    payload = {'spaceId': SPACE_ID, 'spaceName': 'Portal', 'requireLogin': False,
               'isPublicShareLink': False, 'isDeleted': False, 'publicAccessRole': 'reader'}
    payload.update(change)
    session = Mock(post=Mock(return_value=response(payload)))
    with pytest.raises(ValueError):
        notion_discovery.public_page_data(session, 'example-space', PAGE_ID)


def test_published_site_is_public_even_though_it_is_not_a_share_link():
    """A published site reports isPublicShareLink false; the role is the signal."""
    payload = {'spaceId': SPACE_ID, 'spaceName': 'Portal', 'requireLogin': False,
               'isPublicShareLink': False, 'isDeleted': False, 'publicAccessRole': 'reader'}
    session = Mock(post=Mock(return_value=response(payload)))
    assert notion_discovery.public_page_data(session, 'example-space', PAGE_ID)['space_id'] == SPACE_ID


def test_empty_result_with_a_size_hint_is_a_failure_not_an_empty_database():
    """The documented silent failure: HTTP 200, a row count, and no rows."""
    session = portal_session(rows=[], size_hint=113)
    with pytest.raises(ValueError, match='113'):
        notion_discovery.collection_rows(session, 'example-space', SPACE_ID, COLLECTION, VIEW)
    # A genuinely empty database reports no rows and no size hint.
    empty = notion_discovery.collection_rows(portal_session(rows=[], size_hint=0),
                                             'example-space', SPACE_ID, COLLECTION, VIEW)
    assert empty['rows'] == []


def test_person_and_contact_fields_are_never_read():
    collection = notion_discovery.collection_rows(portal_session(), 'example-space', SPACE_ID, COLLECTION, VIEW)
    serialized = json.dumps(collection, ensure_ascii=False)
    assert 'Some Person' not in serialized and 'person@example.test' not in serialized
    assert '😀 メンバー/Members' not in collection['rows'][0] and '連絡先' not in collection['rows'][0]
    assert collection['skipped_property_types'] == ['email', 'person']


def test_properties_resolve_by_schema_name_and_a_renamed_column_fails_closed():
    moved = {'title': {'name': '名前', 'type': 'title'},
             'zZ9y': {'name': 'GitHub', 'type': 'url'}}
    rows = [wrap({'type': 'page', 'parent_id': COLLECTION,
                  'properties': {'title': [['Civic']], 'zZ9y': [['https://github.com/example/civic']]}})]
    collection = notion_discovery.collection_rows(portal_session(rows=rows, schema_value=moved),
                                                 'example-space', SPACE_ID, COLLECTION, VIEW)
    assert notion_discovery.github_repos(collection) == {'example/civic': 'Civic'}
    # A column retyped to a value we refuse to read drops out instead of leaking.
    retyped = dict(moved, zZ9y={'name': 'GitHub', 'type': 'person'})
    dropped = notion_discovery.collection_rows(portal_session(rows=rows, schema_value=retyped),
                                               'example-space', SPACE_ID, COLLECTION, VIEW)
    assert notion_discovery.github_repos(dropped) == {}


def test_repository_extraction_keeps_whole_names_and_skips_reserved_paths():
    collection = {'title_property': '名前', 'rows': [
        {'名前': 'Fiware', 'GitHub': 'https://github.com/makeOurCity/Fiwarecraft'},
        {'名前': 'Suffix', 'GitHub': 'https://www.github.com/example/kazaguruma-transit.git'},
        {'名前': 'Profile', 'GitHub': 'https://github.com/users/someone'},
        {'名前': 'Deep', 'GitHub': 'https://github.com/example/civic/pull/17'},
    ]}
    found = notion_discovery.github_repos(collection)
    assert 'makeOurCity/Fiwarecraft' in found and 'example/kazaguruma-transit' in found
    assert 'example/civic' in found
    assert not any(repo.startswith('users/') for repo in found)


def test_discover_separates_active_candidates_from_rejected_links():
    rows = [row('Active', 'https://github.com/example/active'),
            row('Dormant', 'https://github.com/example/dormant'),
            row('Archived', 'https://github.com/example/archived'),
            row('Dead', 'https://github.com/example/missing')]
    session = portal_session(rows=rows, repos={
        'example/active': {'pushed_at': '2026-09-18T00:00:00Z'},
        'example/dormant': {'pushed_at': '2024-01-01T00:00:00Z'},
        'example/archived': {'pushed_at': '2026-09-18T00:00:00Z', 'archived': True},
    })
    report = notion_discovery.discover(session, PORTAL, now=1789785600.0)
    assert [item['repository'] for item in report['candidates']] == ['example/active']
    assert {item['repository']: item['reason'] for item in report['rejected']} == {
        'example/archived': 'archived',
        'example/dormant': 'no push since 2024-01-01',
        'example/missing': 'HTTP 404',
    }
    assert report['collections'][0]['rows'] == 4
    assert report['candidates'][0]['notion_project'] == 'Active'


def test_a_partial_read_is_reported_rather_than_claimed_complete():
    """The row count and the returned rows can disagree; never imply completeness."""
    rows = [row('Civic', 'https://github.com/example/civic')]
    session = portal_session(rows=rows, size_hint=3)
    collection = notion_discovery.collection_rows(session, 'example-space', SPACE_ID, COLLECTION, VIEW)
    assert collection['complete'] is False
    report = notion_discovery.discover(portal_session(rows=rows, size_hint=3), PORTAL, now=1789785600.0)
    assert report['incomplete_collections'] == ['プロジェクト']
    assert report['collections'][0]['complete'] is False
    # A full read says so.
    assert notion_discovery.discover(portal_session(rows=rows), PORTAL,
                                     now=1789785600.0)['incomplete_collections'] == []


def test_discovery_is_not_wired_into_collection_or_the_hourly_cycle():
    """Acceptance: unreachable from collect/report/run and absent from the registry."""
    assert 'notion' not in inspect.getsource(registry)
    for name in ('cmd_collect', 'cmd_report', 'run_once', 'cmd_run', 'cmd_loop'):
        assert 'notion' not in inspect.getsource(getattr(cli, name))
    assert 'notion_discovery' not in inspect.getsource(registry.collect_all)


def test_command_is_readonly_and_never_writes_the_allowlist():
    parser_source = inspect.getsource(notion_discovery.register_commands)
    assert 'readonly=True' in parser_source
    assert 'REP0RTER_GITHUB_REPOS' not in inspect.getsource(notion_discovery).replace(
        '# Proposals never update REP0RTER_GITHUB_REPOS; a human reviews and edits it.', '')


def test_a_throttled_lookup_is_unresolved_not_a_dead_link():
    """A rate-limited verification must never masquerade as a missing repository."""
    rows = [row('First', 'https://github.com/example/aaa'),
            row('Second', 'https://github.com/example/bbb')]
    session = portal_session(rows=rows)
    session.get = Mock(return_value=Mock(status_code=403, headers={'X-RateLimit-Remaining': '0',
                                                                  'X-RateLimit-Reset': '1789787000'},
                                         json=Mock(return_value={})))
    report = notion_discovery.discover(session, PORTAL, now=1789785600.0)
    assert report['candidates'] == [] and report['rejected'] == []
    assert [item['repository'] for item in report['unresolved']] == ['example/aaa', 'example/bbb']
    assert all('rate limit' in item['reason'] for item in report['unresolved'])
    assert report['complete'] is False
    # Only the first lookup is attempted; throttling stops the rest.
    assert session.get.call_count == 1


def test_a_missing_repository_is_still_rejected_outright():
    session = portal_session(rows=[row('Gone', 'https://github.com/example/gone')], repos={})
    report = notion_discovery.discover(session, PORTAL, now=1789785600.0)
    assert report['unresolved'] == []
    assert report['rejected'][0]['reason'] == 'HTTP 404'


def test_a_server_error_is_unresolved_for_that_repository_only():
    rows = [row('Broken', 'https://github.com/example/aaa'),
            row('Fine', 'https://github.com/example/bbb')]
    session = portal_session(rows=rows)
    original = session.get.side_effect

    def flaky(url, **kwargs):
        if url.endswith('/example/aaa'):
            return Mock(status_code=500, headers={}, json=Mock(return_value={}),
                        raise_for_status=Mock(side_effect=RuntimeError('500 Server Error')))
        return original(url, **kwargs)

    session.get = Mock(side_effect=flaky)
    report = notion_discovery.discover(session, PORTAL, now=1789785600.0)
    assert [item['repository'] for item in report['unresolved']] == ['example/aaa']
    assert [item['repository'] for item in report['candidates']] == ['example/bbb']
    assert report['complete'] is False


def test_a_github_token_is_optional_and_never_leaks_into_the_report(monkeypatch):
    session = portal_session(rows=[row('Civic', 'https://github.com/example/civic')])
    sent = {}

    def capture(url, timeout=None, headers=None):
        sent.update(headers or {})
        return response({'full_name': 'example/civic', 'private': False, 'visibility': 'public',
                         'archived': False, 'pushed_at': '2026-09-18T00:00:00Z',
                         'stargazers_count': 1, 'description': ''})

    session.get = Mock(side_effect=capture)
    monkeypatch.delenv('GITHUB_TOKEN', raising=False)
    report = notion_discovery.discover(session, PORTAL, now=1789785600.0)
    assert 'Authorization' not in sent and report['candidates'][0]['repository'] == 'example/civic'

    monkeypatch.setenv('GITHUB_TOKEN', 'secret-value')
    report = notion_discovery.discover(session, PORTAL, now=1789785600.0)
    assert sent['Authorization'] == 'Bearer secret-value'
    assert 'secret-value' not in json.dumps(report, ensure_ascii=False)
