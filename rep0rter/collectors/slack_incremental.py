"""Overlap catch-up, bounded root refresh and honest gaps for the public archive."""
from __future__ import annotations
import hashlib
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal

from . import slack_archive as api
from .state import BudgetExceeded, BudgetSession, Metrics, persist, read_state, write_state
from ..store import Container
from ..sources import automated
from ..slack_text import mention_names_from_html

log = logging.getLogger(__name__)
OVERLAP = 7200
REFRESH_INTERVAL = 21600


def _allowed(store, event=None, container_id=None):
    from ..policy import container_allowed, event_allowed
    if event is not None and event.meta.get('content_status') == 'deleted' and store.get_event(event.id):
        return True
    return (not automated(event) and event_allowed(store, event)) if event is not None else container_allowed(store, container_id)


def scan(session, channel_id, lower, *, max_pages=30, start_before=None):
    """Return snapshots + completeness. Empty filtered pages never prove completion."""
    collected, before = {}, start_before
    for _ in range(max_pages):
        params = {'channel': channel_id, 'count': 100}
        params['before' if before is not None else 'after'] = before if before is not None else str(lower)
        try:
            payload = api._get(session, api.BASE_URL + '/index/getmessage', **params).json()
        except BudgetExceeded:
            return list(collected.values()), False, 'request budget exhausted', before
        if not isinstance(payload, dict) or not isinstance(payload.get('messages'), list):
            raise RuntimeError('unavailable channel or invalid archive response')
        if not payload['messages']:
            return list(collected.values()), False, 'ambiguous empty page: upstream exposes no raw cursor', None
        page = {}
        for raw in payload['messages']:
            if not isinstance(raw, dict) or 'ts' not in raw:
                raise RuntimeError('malformed archive message')
            ts = api._timestamp(raw['ts'])
            page[ts] = api._merge_duplicate(page[ts], raw) if ts in page else raw
        oldest = min(page)
        if before is not None and oldest >= Decimal(before):
            return list(collected.values()), False, 'pagination did not advance (duplicate timestamp/page)', None
        for ts, raw in page.items():
            if ts >= lower:
                collected[ts] = api._merge_duplicate(collected[ts], raw) if ts in collected else raw
        if oldest <= lower:
            return list(collected.values()), True, '', None
        before = str(oldest)
    return list(collected.values()), False, 'bounded scan exhausted; cursor retained for catch-up', before


def refresh_root(session, channel_id, root_ts, public_ids):
    """One bounded point lookup; never silently substitute a nearby message."""
    payload = api._get(session, api.BASE_URL + '/index/getmessage', channel=channel_id,
                       before=str(Decimal(root_ts) + Decimal('0.000001')), count=100).json()
    if not isinstance(payload, dict) or not isinstance(payload.get('messages'), list):
        raise RuntimeError('invalid root refresh response')
    matches = [r for r in payload['messages'] if isinstance(r, dict) and str(r.get('ts')) == root_ts]
    if not matches:
        return None
    raw = matches[0]
    for other in matches[1:]:
        raw = api._merge_duplicate(raw, other)
    return api.to_event(raw, channel_id, public_ids)[0]


def collect(store, days=2, max_channels=None, session=None, *, metrics=None, request_budget=80):
    metrics = metrics or Metrics()
    session = session or api.make_session()
    if not isinstance(session, BudgetSession):
        session = BudgetSession(session, metrics, limit=request_budget)
    now = time.time()
    channels = api.fetch_channels(session)
    public_ids = {ch.id for ch in channels}
    failures, total = 0, 0
    selected = []
    quiet = []
    for channel in channels:
        cid = 'slack:' + channel.id
        if not _allowed(store, container_id=cid):
            continue
        state = read_state(store, cid)
        posted = channel.last_posted_at.timestamp() if channel.last_posted_at else None
        changed = state.get('last_posted') != posted or state.get('total_messages') != channel.total_messages
        recent = posted is None or posted >= now - (days + 1) * 86400
        due = now - state.get('last_refresh', 0) >= REFRESH_INTERVAL
        if (not state and recent) or (state and changed) or (state.get('error') and now-state.get('last_attempt', 0)>=3600) or (recent and due):
            selected.append((channel, state, due))
        elif now - state.get('last_reconciliation', 0) >= 86400:
            quiet.append((channel, state, True))
    # Round-robin old channels catches quiet roots/newly discovered old channels without a request storm.
    quiet.sort(key=lambda item: item[1].get('last_reconciliation', 0))
    reconciliation_ids = {item[0].id for item in quiet[:2]}
    selected += quiet[:2]
    selected.sort(key=lambda item: item[1].get('last_attempt', 0))
    if max_channels is not None:
        selected = selected[:max_channels]
    for channel, state, due in selected:
        cid = 'slack:' + channel.id
        start = time.time()
        bootstrap = not state.get('last_complete_ts')
        recovery = bool(state.get('error'))
        lower = Decimal(state['last_complete_ts']) - OVERLAP if not bootstrap else Decimal(str(now - days * 86400))
        if due:
            lower = min(lower, Decimal(str(now - 48 * 3600)))
        if channel.id in reconciliation_ids:
            lower = min(lower, Decimal(str(now - max(days, 7) * 86400)))
        old_high = state.get('last_complete_ts')
        if state.get('pending_before'):
            lower = Decimal(state['pending_lower'])
        next_state = dict(state, last_attempt=start, last_posted=channel.last_posted_at.timestamp() if channel.last_posted_at else None,
                          total_messages=channel.total_messages, last_synced=channel.last_synced_at.timestamp() if channel.last_synced_at else None)
        try:
            raws, complete, error, resume_before = scan(session, channel.id, lower, start_before=state.get('pending_before'))
            events, authors = [], []
            for raw in raws:
                event, author = api.to_event(raw, channel.id, public_ids)
                event.meta.update(bootstrap=bootstrap, recovery=recovery, fetched_at=time.time(), observed_at=now)
                if event.parent_id:
                    event.meta['context_incomplete'] = store.get_event(event.parent_id) is None
                if not _allowed(store, event=event):
                    continue
                events.append(event)
                if author:
                    authors.append(author)
            next_state.update(error=error, gap=not complete, fetched_at=time.time())
            high = max([Decimal(str(raw['ts'])) for raw in raws] + [Decimal(state.get('pending_high') or old_high or str(lower))])
            if complete:
                next_state.update(last_complete_ts=str(high), last_success=time.time(), pending_before=None, pending_lower=None, pending_high=None)
            else:
                failures += 1
                next_state.update(pending_before=resume_before, pending_lower=str(lower) if resume_before else None, pending_high=str(high) if resume_before else None)
            if due:
                next_state['last_refresh'] = now
                next_state['last_reconciliation'] = now
            store.upsert_container(Container(id=cid, source='slack', name=channel.name, topic=channel.topic,
                purpose=channel.purpose, num_members=channel.num_members, url=api.channel_url(channel.id)))
            persist(store, cid, next_state, events, metrics, now)
            for author in authors:
                store.upsert_user(*author)
            for event in events:
                for uid, name in mention_names_from_html(event.text, event.html).items():
                    store.upsert_user_name('slack:' + uid, name)
            total += len(events)
        except BudgetExceeded as exc:
            next_state.update(error=str(exc), gap=True)
            with store.conn:
                write_state(store, cid, next_state)
            failures += 1
            break
        except Exception as exc:
            log.warning('Slack channel %s failed: %s', channel.id, exc)
            next_state.update(error=str(exc), gap=True)
            with store.conn:
                write_state(store, cid, next_state)
            failures += 1
    # Missing parents and active root snapshots share a persistent, bounded queue.
    queue = read_state(store, 'slack:root_refresh')
    missing = store.conn.execute("SELECT DISTINCT e.parent_id FROM events e LEFT JOIN events root ON root.id=e.parent_id WHERE e.source='slack' AND e.parent_id IS NOT NULL AND root.id IS NULL").fetchall()
    tracked = store.conn.execute("SELECT e.id FROM events e WHERE e.source='slack' AND e.kind='message' AND e.ts>=? ORDER BY e.last_seen LIMIT 100", (now - 48*3600,)).fetchall()
    older = store.conn.execute("SELECT e.id FROM events e WHERE e.source='slack' AND e.kind='message' AND e.ts<? ORDER BY e.last_seen LIMIT 2", (now - 48*3600,)).fetchall()
    due_ids = {r[0] for r in missing} | {r[0] for r in tracked} | {r[0] for r in older}
    for root_id in sorted(due_ids, key=lambda root: queue.get(root, {}).get('attempted_at', 0))[:4]:
        last = queue.get(root_id, {})
        if now - last.get('attempted_at', 0) < REFRESH_INTERVAL:
            continue
        _, channel_id, root_ts = root_id.split(':', 2)
        if channel_id not in public_ids or not _allowed(store, container_id='slack:' + channel_id):
            continue
        try:
            root = refresh_root(session, channel_id, root_ts, public_ids)
            queue[root_id] = {'attempted_at': now, 'resolved': root is not None}
            if root and _allowed(store, event=root):
                root.meta.update(observed_at=now, fetched_at=time.time(), bootstrap=False, recovery=False)
                persist(store, 'slack:root_refresh', queue, [root], metrics, now)
                with store.conn:
                    for reply_row in store.conn.execute('SELECT id,meta FROM events WHERE parent_id=?', (root.id,)).fetchall():
                        import json
                        meta = json.loads(reply_row['meta'])
                        meta['context_incomplete'] = False
                        store.conn.execute('UPDATE events SET meta=? WHERE id=?', (json.dumps(meta), reply_row['id']))
        except BudgetExceeded:
            break
        except Exception as exc:
            queue[root_id] = {'attempted_at': now, 'resolved': False, 'error': str(exc)}
            failures += 1
    queue = {k:v for k,v in queue.items() if k in due_ids or now-v.get('attempted_at',0)<7*86400}
    with store.conn:
        write_state(store, 'slack:root_refresh', queue)
        write_state(store, 'slack:health', {'last_attempt_at': now, 'containers': len(channels), 'failed_channels': failures,
                   'healthy': failures == 0, 'fingerprint': hashlib.sha256(repr(channels).encode()).hexdigest(),
                   'metrics': asdict(metrics)})
    return total
