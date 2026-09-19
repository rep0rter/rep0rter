"""Versioned collector state and atomic snapshots; no cursor can outrun events."""
from __future__ import annotations
import json
import time
from dataclasses import asdict, dataclass


@dataclass
class Metrics:
    requests: int = 0
    bytes: int = 0
    pages: int = 0
    new_events: int = 0
    updated_events: int = 0
    duplicate_payloads: int = 0


def read_state(store, key):
    return json.loads(store.get_kv('collector:v1:' + key, '{}'))


def write_state(store, key, state):
    store.conn.execute('INSERT INTO kv(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                       ('collector:v1:' + key, json.dumps(state)))


def persist(store, key, state, events, metrics, now=None):
    """Commit a whole bounded channel snapshot and its cursor in one transaction."""
    now = time.time() if now is None else now
    delta = Metrics()
    with store.conn:
        for event in events:
            from ..policy import event_allowed
            from ..sources import automated
            old = store.get_event(event.id)
            deleted = bool(event.meta.get('deleted_at') or event.meta.get('content_status') == 'deleted'
                           or event.meta.get('visibility') in {'deleted', 'withdrawn'})
            if deleted:
                # Retain stable identity for policy dependency matching, never retain deleted content.
                identity_keys = ('source_instance', 'external_id', 'canonical_object_id', 'source_name')
                identity = {key: event.meta[key] for key in identity_keys if key in event.meta}
                identity.update(deleted_at=event.meta.get('deleted_at') or now, observed_at=now,
                                visibility='withdrawn' if event.meta.get('visibility') == 'withdrawn' else 'deleted', content_status='deleted', eligible=False, plain_text='')
                event.meta = identity
                event.text = event.html = event.author_name = ''
                event.reply_count = event.reaction_count = 0
                if old:
                    event.author_id = old.author_id
            # Tombstones for existing rows must persist so withdrawals can propagate.
            if (automated(event) or not event_allowed(store, event)) and not (old and event.meta.get('deleted_at')):
                continue
            event.meta.setdefault('observed_at', now)
            event.meta.setdefault('fetched_at', now)
            if old:
                old_observed = old.meta.get('observed_at', 0)
                if old_observed > event.meta['observed_at']:
                    continue
                if (old.text, old.reply_count, old.reaction_count) == (event.text, event.reply_count, event.reaction_count):
                    delta.duplicate_payloads += 1
                else:
                    delta.updated_events += 1
                event.meta['engagement_high_water'] = {
                    'replies': max(old.reply_count, event.reply_count, old.meta.get('engagement_high_water', {}).get('replies', 0)),
                    'reactions': max(old.reaction_count, event.reaction_count, old.meta.get('engagement_high_water', {}).get('reactions', 0))}
            else:
                delta.new_events += 1
            values = asdict(event)
            # Only stable Event fields enter SQL; optional future model fields live in metadata.
            values['meta'] = json.dumps(event.meta, ensure_ascii=False)
            values['now'] = now
            store.conn.execute('''INSERT INTO events
                (id,source,kind,container_id,author_id,author_name,text,html,url,ts,parent_id,reply_count,reaction_count,meta,first_seen,last_seen)
                VALUES (:id,:source,:kind,:container_id,:author_id,:author_name,:text,:html,:url,:ts,:parent_id,:reply_count,:reaction_count,:meta,:now,:now)
                ON CONFLICT(id) DO UPDATE SET text=excluded.text,html=excluded.html,author_name=excluded.author_name,
                reply_count=excluded.reply_count,reaction_count=excluded.reaction_count,meta=excluded.meta,last_seen=excluded.last_seen''', values)
        write_state(store, key, state)
    metrics.duplicate_payloads += delta.duplicate_payloads
    metrics.updated_events += delta.updated_events
    metrics.new_events += delta.new_events


class BudgetExceeded(RuntimeError):
    pass


class BudgetSession:
    """Global per-run/daily request budget shared by all sources, retries included."""
    def __init__(self, session, metrics, limit=80, interval=.5, reserve=None):
        self.session, self.metrics, self.limit, self.interval = session, metrics, limit, interval
        self.last = 0.0
        self.reserve = reserve
        self.headers = session.headers

    @property
    def remaining(self):
        return max(0, self.limit - self.metrics.requests)

    def get(self, *args, **kwargs):
        return self._request(self.session.get, *args, **kwargs)

    def post(self, *args, **kwargs):
        return self._request(self.session.post, *args, **kwargs)

    def _request(self, send, *args, **kwargs):
        if self.metrics.requests >= self.limit:
            raise BudgetExceeded('collector request budget exhausted')
        wait = self.interval - (time.monotonic() - self.last)
        if wait > 0:
            time.sleep(wait)
        if self.reserve:
            self.reserve()
        self.metrics.requests += 1
        try:
            response = send(*args, **kwargs)
            self.metrics.bytes += len(response.content)
            self.metrics.pages += 1
            return response
        finally:
            self.last = time.monotonic()


class RateLimited(RuntimeError):
    def __init__(self, retry_at):
        self.retry_at = retry_at
        super().__init__('upstream rate limit; retry after ' + str(int(retry_at)))


def check_response(response):
    """Honor upstream rate limits without sleeping inside a collection run."""
    now = time.time()
    headers = response.headers
    if response.status_code == 429 or (response.status_code == 403 and headers.get('X-RateLimit-Remaining') == '0'):
        try:
            retry_at = now + float(headers.get('Retry-After', '60'))
        except (TypeError, ValueError):
            from email.utils import parsedate_to_datetime
            try:
                retry_at = parsedate_to_datetime(headers['Retry-After']).timestamp()
            except (TypeError, ValueError, KeyError):
                retry_at = now + 60
        try:
            retry_at = max(retry_at, float(headers.get('X-RateLimit-Reset', 0)))
        except (TypeError, ValueError):
            pass
        raise RateLimited(max(now + 1, min(now + 86400, retry_at)))
    response.raise_for_status()
