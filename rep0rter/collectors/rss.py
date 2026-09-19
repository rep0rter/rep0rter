"""Allowlisted public RSS 2.0 feeds, with stable IDs and excerpt-only evidence."""
from __future__ import annotations

import hashlib
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from ..sources import normalize_text
from ..store import Container, Event
from .state import check_response, persist, read_state


def public_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme not in ('https', 'http') or not parsed.hostname
            or parsed.username or parsed.password):
        raise ValueError('RSS links must be public HTTP(S) URLs')
    return value


def object_id(feed_url, guid):
    # GUIDs only need to be unique within their feed.
    return 'rss:' + hashlib.sha256((feed_url + '\n' + guid).encode()).hexdigest()


def to_event(item, feed_url, source_name):
    url = public_url((item.findtext('link') or '').strip())
    guid = (item.findtext('guid') or '').strip() or url
    published = parsedate_to_datetime(item.findtext('pubDate') or '')
    if published.tzinfo is None:
        raise ValueError('RSS pubDate must include a timezone')
    title = normalize_text(item.findtext('title') or '', 'html')
    description = normalize_text(item.findtext('description') or '', 'html')
    text = '\n\n'.join(part for part in (title, description) if part)
    author = (item.findtext('author') or item.findtext('{http://purl.org/dc/elements/1.1/}creator') or '').strip()
    parsed = urlsplit(feed_url)
    return Event(
        id=object_id(feed_url, guid), source='rss', kind='article',
        container_id='rss-feed:' + feed_url, ts=published.timestamp(),
        author_id=object_id(feed_url, 'author:' + author) if author else '',
        author_name=author, text=text, url=url,
        meta={'source_instance': parsed.scheme + '://' + parsed.netloc,
              'source_name': source_name, 'external_id': guid,
              'canonical_object_id': url, 'feed_url': feed_url,
              'visibility': 'public', 'content_format': 'plain', 'plain_text': text,
              'content_scope': 'feed_excerpt', 'eligible': bool(text),
              'container_kind': 'feed', 'raw_version': 1})


def collect(store, feeds, session, metrics, days=2):
    from ..policy import container_allowed, event_allowed
    from .registry import record_source_error

    total = 0
    for feed_url in dict.fromkeys(feeds):
        cid = 'rss-feed:' + feed_url
        if not container_allowed(store, cid):
            continue
        state = read_state(store, cid)
        now = time.time()
        if now < state.get('retry_at', 0):
            record_source_error(store, cid, 'waiting for upstream rate limit reset')
            continue
        try:
            public_url(feed_url)
            response = session.get(feed_url, timeout=30)
            check_response(response)
            root = ET.fromstring(response.content)
            channel = root.find('channel')
            if root.tag != 'rss' or root.get('version') != '2.0' or channel is None:
                raise ValueError('Expected an RSS 2.0 channel')
            source_name = normalize_text(channel.findtext('title') or '', 'html') or urlsplit(feed_url).hostname
            events = []
            lower = min(now - days * 86400, state.get('last_success', now) - 7200)
            for item in channel.findall('item'):
                event = to_event(item, feed_url, source_name)
                # Re-read existing items for edits even after their publication window.
                if event.ts < lower and not store.get_event(event.id):
                    continue
                if event_allowed(store, event):
                    event.meta.update(observed_at=now, fetched_at=now,
                                      bootstrap=not state, recovery=bool(state.get('error')))
                    events.append(event)
            store.upsert_container(Container(id=cid, source='rss', name=source_name,
                                             url=feed_url))
            persist(store, cid, dict(state, last_attempt=now, last_success=now,
                                    error='', retry_at=0), events, metrics, now)
            total += len(events)
            # A rolling feed dropping an item does not prove that it was deleted.
        except Exception as exc:
            persist(store, cid, dict(state, last_attempt=now, error=str(exc),
                                    retry_at=getattr(exc, 'retry_at', 0)), [], metrics, now)
            record_source_error(store, cid, exc)
    return total
