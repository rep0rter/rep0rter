"""Overlap catch-up, bounded root refresh and honest gaps for the public archive."""
from __future__ import annotations
import hashlib
import logging
import time
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from . import slack_archive as api
from .state import BudgetExceeded, BudgetSession, Metrics, persist, read_state, write_state
from ..store import Container
from ..sources import automated
from ..slack_text import mention_names_from_html

log = logging.getLogger(__name__)
OVERLAP = 7200
REFRESH_INTERVAL = 21600
MAX_REPLY_REFRESH_QUEUE = 100


def _budget_debt(state):
    # Older deployed states have only the error string. Budget debt is work for
    # the next run, not an upstream failure needing the one-hour backoff.
    error = state.get('error', '')
    return bool(state.get('gap') and ('budget exhausted' in error or 'bounded scan exhausted' in error))


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
        # The archive turns query floats back into PHP strings before SQL,
        # losing fractional precision at Slack-sized timestamps. Widen the wire
        # bounds to whole seconds; exact Decimal bounds below retain correctness.
        params['before' if before is not None else 'after'] = str(
            Decimal(before).to_integral_value(rounding=ROUND_CEILING) if before is not None
            else lower.to_integral_value(rounding=ROUND_FLOOR))
        try:
            payload = api._get(session, api.BASE_URL + '/index/getmessage', **params).json()
        except BudgetExceeded:
            return list(collected.values()), False, 'request budget exhausted', before
        if not isinstance(payload, dict) or not isinstance(payload.get('messages'), list):
            raise RuntimeError('unavailable channel or invalid archive response')
        if not payload['messages'] and before is None and not collected:
            # An unbounded head page can establish an older raw bound for a quiet
            # channel. The homepage last-posted alone cannot prove this.
            try:
                payload = api._get(session, api.BASE_URL + '/index/getmessage', channel=channel_id, count=100).json()
            except BudgetExceeded:
                return [], False, 'request budget exhausted while verifying empty after page', None
            if not isinstance(payload, dict) or not isinstance(payload.get('messages'), list):
                raise RuntimeError('unavailable channel or invalid empty-page verification response')
        if not payload['messages']:
            if before is None and not collected:
                try:
                    if api.channel_is_older_than(session, channel_id, lower):
                        return [], True, '', None
                except BudgetExceeded:
                    return [], False, 'request budget exhausted while verifying quiet channel HTML', None
            return list(collected.values()), False, 'ambiguous empty page: upstream exposes no raw cursor', None
        page = {}
        for raw in payload['messages']:
            if not isinstance(raw, dict) or 'ts' not in raw:
                raise RuntimeError('malformed archive message')
            ts = api._timestamp(raw['ts'])
            page[ts] = api._merge_duplicate(page[ts], raw) if ts in page else raw
        oldest = min(page)
        if before is not None and oldest >= Decimal(before):
            if Decimal(before) != Decimal(before).to_integral_value() and len(payload['messages']) < 100:
                try:
                    if api.channel_window_is_complete(session, channel_id, lower, collected):
                        return list(collected.values()), True, '', None
                except BudgetExceeded:
                    return list(collected.values()), False, 'request budget exhausted while verifying repeated page', before
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
                       before=str(Decimal(root_ts).to_integral_value(rounding=ROUND_FLOOR) + 1), count=100).json()
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
    metrics = metrics or (session.metrics if isinstance(session, BudgetSession) else Metrics())
    session = session or api.make_session()
    if not isinstance(session, BudgetSession):
        session = BudgetSession(session, metrics, limit=request_budget)
    now = time.time()
    channels = api.fetch_channels(session)
    previous_health = read_state(store, 'slack:health')
    previous_count = previous_health.get('last_good_directory_count', previous_health.get('containers', 0))
    if previous_count and len(channels) < previous_count * .5:
        raise RuntimeError(f'archive public directory suddenly shrank from {previous_count} to {len(channels)}; retaining prior state')
    public_ids = {ch.id for ch in channels}
    failures, total, transport_failures = 0, 0, 0
    reasons = {}
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
        if (not state and recent) or (state and changed) or (_budget_debt(state) or (state.get('error') and now-state.get('last_attempt', 0)>=3600)) or (recent and due):
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
    for position, (channel, state, due) in enumerate(selected):
        if not session.remaining:
            # No HTTP happened: preserve attempt/refresh timestamps and pending
            # cursors. The debt stays visible and is first in line next run.
            with store.conn:
                for deferred_channel, deferred_state, _ in selected[position:]:
                    cid = 'slack:' + deferred_channel.id
                    deferred_state = dict(deferred_state)
                    deferred_state.update(gap=True, error=deferred_state.get('error') or 'request budget exhausted')
                    write_state(store, cid, deferred_state)
                    reasons[cid] = deferred_state['error']
            break
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
            # Share pages across the remaining channels. A busy channel can
            # retain its cursor without consuming the entire source allowance.
            page_allowance = min(30, max(1, session.remaining // (len(selected) - position)))
            raws, complete, error, resume_before = scan(session, channel.id, lower,
                max_pages=page_allowance, start_before=state.get('pending_before'))
            events, authors = [], []
            for raw in raws:
                event, author = api.to_event(raw, channel.id, public_ids)
                event.meta.update(bootstrap=bootstrap, recovery=recovery, fetched_at=time.time(), observed_at=now)
                if event.parent_id:
                    event.meta['context_incomplete'] = store.get_event(event.parent_id) is None
                if not _allowed(store, event=event):
                    continue
                events.append(event)
                if author and event.meta.get('content_status') != 'deleted':
                    authors.append(author)
            next_state.update(error=error, gap=not complete, fetched_at=time.time())
            high = max([Decimal(str(raw['ts'])) for raw in raws] + [Decimal(state.get('pending_high') or old_high or str(lower))])
            if complete:
                next_state.update(last_complete_ts=str(high), last_success=time.time(), pending_before=None, pending_lower=None, pending_high=None)
            else:
                failures += 1
                reasons[cid] = error
                next_state.update(pending_before=resume_before, pending_lower=str(lower) if resume_before else None, pending_high=str(high) if resume_before else None)
            if due and complete:
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
            reasons[cid] = str(exc)
            with store.conn:
                write_state(store, cid, next_state)
            failures += 1
            break
        except Exception as exc:
            log.warning('Slack channel %s failed: %s', channel.id, exc)
            transport_failures += 1
            next_state.update(error=str(exc), gap=True)
            reasons[cid] = str(exc)
            with store.conn:
                write_state(store, cid, next_state)
            failures += 1
    # Missing parents and active root snapshots share a persistent, bounded queue.
    queue = read_state(store, 'slack:root_refresh')
    # A newly stored reply can revive an existing old thread. Derive pending
    # work from durable event timestamps, so a crash between channel commit and
    # queue insertion cannot lose it. last_seen proves the root was observed
    # after that reply; repeated overlap payloads do not create new work.
    pending = set()
    for root_id, state in queue.items():
        if not state.get('reply_pending'):
            continue
        root = store.get_event(root_id)
        if (root is None or root.container_id.removeprefix('slack:') not in public_ids
                or root.meta.get('deleted_at') or not _allowed(store, event=root)):
            state['reply_pending'] = False
            continue
        last_seen = store.conn.execute('SELECT last_seen FROM events WHERE id=?', (root_id,)).fetchone()[0]
        if last_seen >= state.get('requested_at', 0):
            state['reply_pending'] = False
            continue
        pending.add(root_id)
    reply_roots = store.conn.execute("""SELECT root.id, max(reply.first_seen) AS requested_at
        FROM events reply JOIN events root ON root.id=reply.parent_id
        WHERE reply.source='slack' AND root.source='slack'
          AND NOT EXISTS(SELECT 1 FROM event_tombstones t WHERE t.event_id=root.id)
          AND NOT EXISTS(SELECT 1 FROM event_tombstones t WHERE t.event_id=reply.id)
          AND json_extract(root.meta,'$.deleted_at') IS NULL
          AND json_extract(reply.meta,'$.deleted_at') IS NULL
        GROUP BY root.id HAVING max(reply.first_seen)>root.last_seen
        ORDER BY requested_at,root.id LIMIT ?""", (MAX_REPLY_REFRESH_QUEUE,)).fetchall()
    for root_id, requested_at in reply_roots:
        if root_id in pending or len(pending) >= MAX_REPLY_REFRESH_QUEUE:
            continue
        root = store.get_event(root_id)
        if not _allowed(store, event=root):
            continue
        queue[root_id] = dict(queue.get(root_id, {}), reply_pending=True, requested_at=requested_at)
        pending.add(root_id)
    with store.conn:
        write_state(store, 'slack:root_refresh', queue)
    missing = store.conn.execute("SELECT DISTINCT e.parent_id FROM events e LEFT JOIN events root ON root.id=e.parent_id WHERE e.source='slack' AND e.parent_id IS NOT NULL AND root.id IS NULL").fetchall()
    tracked = store.conn.execute("SELECT e.id FROM events e WHERE e.source='slack' AND e.kind='message' AND e.ts>=? AND NOT EXISTS(SELECT 1 FROM event_tombstones t WHERE t.event_id=e.id) AND json_extract(e.meta,'$.deleted_at') IS NULL ORDER BY e.last_seen LIMIT 100", (now - 48*3600,)).fetchall()
    older = store.conn.execute("SELECT e.id FROM events e WHERE e.source='slack' AND e.kind='message' AND e.ts<? AND NOT EXISTS(SELECT 1 FROM event_tombstones t WHERE t.event_id=e.id) AND json_extract(e.meta,'$.deleted_at') IS NULL ORDER BY e.last_seen LIMIT 2", (now - 48*3600,)).fetchall()
    due_ids = pending | {r[0] for r in missing} | {r[0] for r in tracked} | {r[0] for r in older}
    root_attempts = 0
    for root_id in sorted(due_ids, key=lambda root: (root not in pending, queue.get(root, {}).get('attempted_at', 0), queue.get(root, {}).get('requested_at', 0), root)):
        last = queue.get(root_id, {})
        if now - last.get('attempted_at', 0) < REFRESH_INTERVAL:
            continue
        if store.conn.execute('SELECT 1 FROM event_tombstones WHERE event_id=?', (root_id,)).fetchone():
            continue
        existing_root = store.get_event(root_id)
        if existing_root and (existing_root.meta.get('deleted_at') or existing_root.meta.get('content_status') == 'deleted' or not _allowed(store, event=existing_root)):
            continue
        _, channel_id, root_ts = root_id.split(':', 2)
        if channel_id not in public_ids or not _allowed(store, container_id='slack:' + channel_id):
            continue
        if root_attempts >= 4 or not session.remaining:
            break
        try:
            root_attempts += 1
            root = refresh_root(session, channel_id, root_ts, public_ids)
            queue[root_id] = dict(last, attempted_at=now, resolved=root is not None)
            if root and _allowed(store, event=root):
                queue[root_id].pop('error', None)
                queue[root_id]['reply_pending'] = False
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
            queue[root_id] = dict(last, attempted_at=now, resolved=False, error=str(exc))
            reasons[root_id] = str(exc)
            transport_failures += 1
            failures += 1
    # Unresolved debt must not disappear from health merely because a retry is not due.
    for channel in channels:
        cid = 'slack:' + channel.id
        state = read_state(store, cid)
        if _allowed(store, container_id=cid) and state.get('gap') and state.get('error'):
            reasons.setdefault(cid, state['error'])
    # Root transport failures are unresolved debt too; skipping a six-hour
    # refresh must not turn the very next hourly health check green.
    for root_id in due_ids:
        root_state = queue.get(root_id, {})
        if not root_state.get('error'):
            continue
        _, channel_id, _ = root_id.split(':', 2)
        if channel_id not in public_ids or not _allowed(store, container_id='slack:' + channel_id):
            continue
        if store.conn.execute('SELECT 1 FROM event_tombstones WHERE event_id=?', (root_id,)).fetchone():
            continue
        root = store.get_event(root_id)
        if root and (root.meta.get('deleted_at') or root.meta.get('content_status') == 'deleted' or not _allowed(store, event=root)):
            continue
        reasons.setdefault(root_id, root_state['error'])
    failures = max(failures, len(reasons))
    queue = {k:v for k,v in queue.items() if k in due_ids or now-v.get('attempted_at',0)<7*86400}
    with store.conn:
        write_state(store, 'slack:root_refresh', queue)
        write_state(store, 'slack:health', {'last_attempt_at': now, 'containers': len(channels), 'failed_channels': failures,
                   'healthy': failures == 0, 'available': transport_failures == 0, 'history_complete': failures == 0, 'reasons': reasons, 'last_good_directory_count': len(channels), 'fingerprint': hashlib.sha256(repr(channels).encode()).hexdigest(),
                   'metrics': asdict(metrics)})
    from .registry import record_source_error
    for channel, reason in reasons.items():
        record_source_error(store, channel, reason)
    return total
