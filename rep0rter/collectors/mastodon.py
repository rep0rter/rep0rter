"""Explicit public Mastodon accounts; originals only, stable URI identity and tombstones."""
from __future__ import annotations
import hashlib
import re
import time
from urllib.parse import urlsplit, quote

from ..sources import normalize_text
from ..store import Container, Event
from .github import timestamp
from .state import check_response, persist, read_state


def object_id(uri):
    return 'mastodon:' + hashlib.sha256(uri.encode()).hexdigest()


def to_event(raw, instance, container_id, allowed_actor):
    # A boost is an observation of another object, never an original by this actor.
    if raw.get('reblog'):
        return None
    account = raw.get('account') or {}
    if account.get('url') != allowed_actor or raw.get('visibility') != 'public':
        return None
    uri = raw.get('uri')
    if not isinstance(uri, str) or not uri.startswith(('https://', 'http://')):
        return None
    text = normalize_text(raw.get('content') or '', 'html')
    cw = raw.get('spoiler_text') or ''
    if cw:
        text = '[Content warning: ' + cw + ']\n' + text
    parent = raw.get('in_reply_to_id')
    return Event(id=object_id(uri), source='mastodon', kind='status_reply' if parent else 'status',
        container_id=container_id, ts=timestamp(raw.get('created_at')), author_id=object_id(allowed_actor),
        author_name=account.get('display_name') or account.get('acct', ''), text=text, url=raw.get('url') or uri,
        parent_id='mastodon-local:' + instance + ':' + str(parent) if parent else None,
        reply_count=int(raw.get('replies_count') or 0), reaction_count=int(raw.get('favourites_count') or 0),
        meta={'source_instance': instance, 'external_id': str(raw['id']), 'visibility': 'public',
              'content_format': 'plain', 'plain_text': text, 'canonical_object_id': uri,
              'updated_at': raw.get('edited_at'), 'observed_at': time.time(), 'eligible': not bool(parent), 'is_bot': bool(account.get('bot')),
              'avatar_url': account.get('avatar_static', ''), 'source_name': urlsplit(instance).hostname,
              'container_kind': 'account', 'cw': cw, 'sensitive': bool(raw.get('sensitive')),
              'engagement': {'mastodon_favourites': int(raw.get('favourites_count') or 0), 'mastodon_boosts': int(raw.get('reblogs_count') or 0), 'mastodon_replies': int(raw.get('replies_count') or 0)},
              'relations': {'reply_to_external_id': parent, 'quotes': []}, 'raw_version': 1})


def parse_account(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or
        parsed.port not in (None, 443) or parsed.query or parsed.fragment or not re.fullmatch(r'/@[A-Za-z0-9_.-]+', parsed.path)):
        raise ValueError('Mastodon allowlist must contain https://instance/@account URLs')
    return 'https://' + parsed.hostname, parsed.path[2:]


def collect(store, accounts, session, metrics, days=2):
    from ..policy import event_allowed, container_allowed
    from .registry import record_source_error
    total = 0
    for configured in accounts:
        instance, username = parse_account(configured)
        cid = 'mastodon-account:' + instance + '/@' + username
        if not container_allowed(store, cid):
            continue
        state = read_state(store, cid)
        now = time.time()
        if now < state.get('retry_at', 0):
            from .registry import record_source_error
            record_source_error(store, cid, 'waiting for upstream rate limit reset')
            continue
        def get(path, **params):
            response = session.get(instance + path, params=params, timeout=30)
            check_response(response)
            return response.json()
        try:
            account = get('/api/v1/accounts/lookup', acct=username)
            actor = account.get('url')
            # Lookup must resolve precisely the configured local account, never a remote lookalike.
            if actor != configured or '@' in account.get('acct', ''):
                raise ValueError('Mastodon account lookup does not match configured local actor')
            store.upsert_container(Container(id=cid, source='mastodon', name=account.get('display_name') or username, url=actor))
            events, cursor, finished = [], None, False
            lower = state.get('last_complete_ts', now - days * 86400) - 7200
            for _ in range(10):
                params = {'limit': 40, 'exclude_reblogs': 'true'}
                if cursor:
                    params['max_id'] = cursor
                rows = get('/api/v1/accounts/' + quote(str(account['id']), safe='') + '/statuses', **params)
                if not isinstance(rows, list):
                    raise ValueError('invalid Mastodon statuses response')
                if not rows:
                    finished = True
                    break
                older = False
                for raw in rows:
                    if timestamp(raw.get('created_at')) < lower:
                        older = True
                        continue
                    event = to_event(raw, instance, cid, actor)
                    if event and event_allowed(store, event):
                        event.meta.update(bootstrap=not state, recovery=bool(state.get('error')), fetched_at=now)
                        events.append(event)
                new_cursor = str(rows[-1]['id'])
                if cursor is not None and int(new_cursor) >= int(cursor):
                    raise ValueError('Mastodon pagination did not advance')
                cursor = new_cursor
                if older or len(rows) < 40:
                    finished = True
                    break
            # Resolve same-instance reply edges to canonical IDs, never to a numeric ID alone.
            observed = {event.meta['external_id']: event.id for event in events}
            for event in events:
                parent_external = event.meta.get('relations', {}).get('reply_to_external_id')
                if parent_external is not None:
                    parent_id = observed.get(str(parent_external))
                    if parent_id is None:
                        row = store.conn.execute("SELECT id FROM events WHERE source='mastodon' AND json_extract(meta,'$.source_instance')=? AND json_extract(meta,'$.external_id')=?", (instance, str(parent_external))).fetchone()
                        parent_id = row['id'] if row else None
                    event.meta['context_incomplete'] = parent_id is None
                    if parent_id:
                        event.parent_id = parent_id
            # Bounded rotating rechecks preserve edits and propagate deletions/visibility changes.
            tracked = store.conn.execute("SELECT e.* FROM events e WHERE e.container_id=? AND NOT EXISTS (SELECT 1 FROM event_tombstones t WHERE t.event_id=e.id) AND json_extract(e.meta,'$.deleted_at') IS NULL AND COALESCE(json_extract(e.meta,'$.visibility'),'unknown')='public' AND json_extract(e.meta,'$.external_id') IS NOT NULL ORDER BY e.last_seen LIMIT 4", (cid,)).fetchall()
            for row in tracked:
                old = store._row_to_event(row)
                if not event_allowed(store, old) or not old.meta.get('external_id'):
                    continue
                response = session.get(instance + '/api/v1/statuses/' + quote(old.meta['external_id'], safe=''), timeout=30)
                if response.status_code in (404, 410):
                    old.text = old.html = ''
                    old.meta.update(deleted_at=now, observed_at=now, visibility='deleted', eligible=False, plain_text='')
                    events.append(old)
                else:
                    check_response(response)
                    updated = to_event(response.json(), instance, cid, actor)
                    if updated and event_allowed(store, updated):
                        events.append(updated)
                    elif not updated:
                        old.text = old.html = ''
                        old.meta.update(deleted_at=now, observed_at=now, visibility='withdrawn', eligible=False, plain_text='')
                        events.append(old)
            new_state = dict(state, last_attempt=now, last_success=now if finished else state.get('last_success'),
                             error='' if finished else 'Mastodon pagination budget exhausted')
            if finished:
                new_state['last_complete_ts'] = now
            persist(store, cid, new_state, events, metrics, now)
            total += len(events)
            if new_state['error']:
                from .registry import record_source_error
                record_source_error(store, cid, new_state['error'])
        except Exception as exc:
            persist(store, cid, dict(state, last_attempt=now, error=str(exc), retry_at=getattr(exc, 'retry_at', 0)), [], metrics, now)
            record_source_error(store, cid, exc)
    return total
