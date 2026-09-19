"""Anonymous public Notion pages and embedded databases (unofficial web JSON).

Only configured pages and their embedded collections are followed. No workspace
credentials, user records, backlinks, or arbitrary linked pages are collected.
"""
from __future__ import annotations

import re
import time
from urllib.parse import urlsplit
from uuid import UUID

from ..store import Container, Event
from .state import check_response, persist, read_state

PAGE_ID = re.compile(r'([0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$', re.I)
MAX_CHUNKS = 5
MAX_ROWS = 1000
PROPERTY_TYPES = {'text', 'url', 'select', 'multi_select', 'status', 'number', 'checkbox', 'date'}


def is_notion_url(url):
    try:
        host = (urlsplit(url).hostname or '').lower()
    except ValueError:
        return False
    return host.endswith('.notion.site') or host in {'notion.so', 'www.notion.so'}


def parse_url(url):
    parsed = urlsplit(url)
    match = PAGE_ID.search(parsed.path.rstrip('/').split('/')[-1])
    if (not is_notion_url(url) or parsed.scheme != 'https' or parsed.username
            or parsed.password or parsed.port not in (None, 443) or not match):
        raise ValueError('Use a public HTTPS notion.site/notion.so page URL with a page ID')
    return 'https://' + parsed.hostname, str(UUID(match[1]))


def records(data, kind):
    raw = data.get('recordMap')
    if not isinstance(raw, dict):
        raise ValueError('Notion response has no recordMap')
    result = {}
    for key, record in raw.get(kind, {}).items():
        value = record.get('value', {})
        role = record.get('role')
        if isinstance(value, dict) and 'value' in value:
            role = value.get('role')
            value = value['value']
        if (role in {'reader', 'commenter', 'read_and_write', 'editor'}
                and isinstance(value, dict) and value.get('id') == key):
            result[key] = value
    return result


def rich_text(value):
    """Visible text, links, and dates; never resolve user/permission records."""
    parts = []
    for segment in value or []:
        if not isinstance(segment, list) or not segment or not isinstance(segment[0], str):
            continue
        text = segment[0].replace('\ufffc', '')
        for mark in segment[1] if len(segment) > 1 and isinstance(segment[1], list) else []:
            if not isinstance(mark, list) or len(mark) < 2:
                continue
            if mark[0] == 'a' and isinstance(mark[1], str) and mark[1].startswith(('https://', 'http://')):
                text += ' (' + mark[1] + ')'
            elif mark[0] == 'd' and isinstance(mark[1], dict):
                text += ' '.join(str(mark[1][key]) for key in ('start_date', 'start_time', 'end_date', 'end_time') if mark[1].get(key))
        parts.append(text)
    return ''.join(parts).strip()


def page_text(page, blocks, schema=None):
    props = page.get('properties') or {}
    lines = [rich_text(props.get('title'))]
    for key, definition in (schema or {}).items():
        if definition.get('type') in PROPERTY_TYPES:
            value = rich_text(props.get(key))
            if value:
                lines.append(definition.get('name', key) + ': ' + value)
    seen = {page['id']}
    pending = list(reversed(page.get('content', [])))
    incomplete = False
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        block = blocks.get(key)
        if block is None:
            incomplete = True
            continue
        if not block.get('alive', True) or block.get('type') in {'page', 'collection_view', 'collection_view_page'}:
            continue
        values = block.get('properties') or {}
        for field in ('title', 'caption'):
            text = rich_text(values.get(field))
            if text:
                lines.append(text)
        if block.get('type') in {'bookmark', 'embed', 'video', 'audio', 'file'}:
            source = rich_text(values.get('source'))
            if source.startswith(('https://', 'http://')):
                lines.append(source)
        pending.extend(reversed(block.get('content', [])))
    return '\n\n'.join(line for line in lines if line), incomplete


def to_event(page, blocks, origin, container_id, source_name, schema=None):
    created = page.get('created_time')
    edited = page.get('last_edited_time', created)
    if not isinstance(created, (int, float)) or created <= 0 or not isinstance(edited, (int, float)):
        raise ValueError('Notion page is missing creation/edit timestamps')
    text, incomplete = page_text(page, blocks, schema)
    pid = str(UUID(page['id']))
    url = origin + '/' + pid.replace('-', '')
    return Event(id='notion:' + pid, source='notion', kind='article',
                 container_id=container_id, ts=created / 1000, text=text, url=url,
                 meta={'source_instance': origin, 'source_name': source_name,
                       'external_id': pid, 'canonical_object_id': 'notion:' + pid,
                       'visibility': 'public', 'content_format': 'plain', 'plain_text': text,
                       'content_scope': 'database_properties' if schema is not None else 'notion_page',
                       'context_incomplete': incomplete, 'updated_at': edited / 1000,
                       'eligible': bool(text), 'container_kind': 'page', 'raw_version': 1})


def post_json(session, origin, endpoint, body):
    # Explicitly suppress session cookies/auth: only anonymously readable data belongs here.
    response = session.post(origin + '/api/v3/' + endpoint, json=body, timeout=30,
                            headers={'Cookie': '', 'Authorization': ''}, auth=lambda request: request)
    check_response(response)
    data = response.json()
    if not isinstance(data, dict) or data.get('errorId'):
        raise ValueError('Notion returned an invalid or inaccessible public page')
    return data


def load_page(session, origin, page_id):
    cursor, blocks, views, collections, seen = {'stack': []}, {}, {}, {}, set()
    for _ in range(MAX_CHUNKS):
        data = post_json(session, origin, 'loadCachedPageChunkV2',
                         {'page': {'id': page_id}, 'cursor': cursor, 'verticalColumns': False})
        blocks.update(records(data, 'block'))
        views.update(records(data, 'collection_view'))
        collections.update(records(data, 'collection'))
        cursors = data.get('cursors')
        if cursors == []:
            root = blocks.get(page_id)
            if not root or not root.get('alive', True):
                raise ValueError('Notion page is not anonymously readable')
            return root, blocks, views, collections
        if not isinstance(cursors, list) or len(cursors) != 1:
            raise ValueError('Unsupported Notion page pagination; snapshot incomplete')
        cursor = cursors[0]
        marker = repr(cursor)
        if marker in seen:
            raise ValueError('Notion page pagination did not advance')
        seen.add(marker)
    raise ValueError('Notion page chunk limit reached; snapshot incomplete')


def embedded_collections(root, blocks, views, collections):
    """Follow content children, not ancillary records or unrelated linked databases."""
    pending, seen, found = [root['id']], set(), {}
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        block = blocks.get(key, {})
        if block.get('type') in {'collection_view', 'collection_view_page'}:
            candidates = [views[vid] for vid in block.get('view_ids', []) if vid in views]
            # A table/list avoids a calendar view's implicit current-month filter.
            candidates.sort(key=lambda view: view.get('type') not in {'table', 'list'})
            if not candidates:
                raise ValueError('Notion embedded database has no readable view')
            view = candidates[0]
            cid = (view.get('format') or {}).get('collection_pointer', {}).get('id') or block.get('collection_id')
            if cid not in collections:
                raise ValueError('Notion embedded database has no readable collection')
            found.setdefault(cid, (block, view, collections[cid]))
        elif key == root['id'] or block.get('type') != 'page':
            pending.extend(block.get('content', []))
    return list(found.values())


def query_collection(session, origin, view, collection):
    # Same read-only reducer request used by the public web client. Bound large
    # databases explicitly; never claim a truncated response is a complete sync.
    data = post_json(session, origin, 'queryCollection', {
        'clientType': 'notion_app',
        'source': {'type': 'collection', 'id': collection['id'], 'spaceId': collection['space_id']},
        'collectionView': {'id': view['id'], 'spaceId': collection['space_id']},
        'loader': {'reducers': {'collection_group_results': {'type': 'results', 'limit': MAX_ROWS,
                                                           'loadContentCover': False}},
                   'searchQuery': '', 'archiveStatus': 'NON_ARCHIVED', 'userTimeZone': 'Asia/Taipei'}})
    result = data.get('result', {}).get('reducerResults', {}).get('collection_group_results', {})
    if not isinstance(result.get('blockIds'), list) or not isinstance(result.get('hasMore'), bool):
        raise ValueError('Unsupported Notion database response')
    return result['blockIds'], records(data, 'block'), result['hasMore']


def collect(store, feeds, session, metrics, days=2):
    from ..policy import container_allowed, event_allowed
    from .registry import record_source_error

    total, seen = 0, set()
    for configured in feeds:
        # Invalid configured URLs still receive an isolated source health record.
        cid = 'notion-page:' + configured
        state, now = {}, time.time()
        try:
            origin, page_id = parse_url(configured)
            cid = 'notion-page:' + page_id
            if cid in seen or not container_allowed(store, cid):
                continue
            seen.add(cid)
            state = read_state(store, cid)
            if now < state.get('retry_at', 0):
                record_source_error(store, cid, 'waiting for upstream rate limit reset')
                continue
            root, blocks, views, collections = load_page(session, origin, page_id)
            name = rich_text((root.get('properties') or {}).get('title')) or origin
            lower = min(now - days * 86400, state.get('last_success', now) - 7200)
            events, incomplete, databases = {}, False, embedded_collections(root, blocks, views, collections)
            def add(page, page_blocks, schema=None):
                if not page.get('alive', True) or page.get('is_template'):
                    return
                event = to_event(page, page_blocks, origin, cid, name, schema)
                if max(event.ts, event.meta['updated_at']) < lower and not store.get_event(event.id):
                    return
                if event_allowed(store, event):
                    event.meta.update(observed_at=now, fetched_at=now, bootstrap=not state,
                                      recovery=bool(state.get('error')))
                    events[event.id] = event
            if not databases:
                add(root, blocks)
            for _, view, collection in databases:
                ids, rows, more = query_collection(session, origin, view, collection)
                incomplete |= more
                templates = collection.get('template_pages', [])
                for key in ids:
                    page = rows.get(key)
                    if not page or page.get('type') != 'page' or page.get('parent_id') != collection['id']:
                        raise ValueError('Notion database row is missing or has unexpected ancestry')
                    if key not in templates:
                        add(page, rows, collection.get('schema', {}))
            error = 'Notion database row limit reached; snapshot incomplete' if incomplete else ''
            store.upsert_container(Container(id=cid, source='notion', name=name, url=origin + '/' + page_id.replace('-', '')))
            new_state = dict(state, last_attempt=now, error=error, retry_at=0,
                             database_count=len(databases), history_complete=not incomplete)
            if not incomplete:
                new_state['last_success'] = now
            persist(store, cid, new_state, list(events.values()), metrics, now)
            total += len(events)
            if error:
                record_source_error(store, cid, error)
            # Missing rows are not deletion evidence (views and permissions can change).
        except Exception as exc:
            retry_at = getattr(exc, 'retry_at', 0)
            # Public Notion's HTML 429 responses often omit Retry-After.
            if retry_at:
                retry_at = max(retry_at, now + 3600)
            persist(store, cid, dict(state, last_attempt=now, error=str(exc), retry_at=retry_at,
                                    history_complete=False), [], metrics, now)
            record_source_error(store, cid, exc)
    return total
