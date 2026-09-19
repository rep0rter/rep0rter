"""Allowlisted public RSS 2.0 feeds, with stable IDs and feed-supplied evidence."""
from __future__ import annotations

import hashlib
import json
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from ..sources import normalize_text
from ..store import Container, Event
from .state import check_response, persist, read_state

ODF_PROJECTS = 'https://www.odf.or.kr/archive-project'
ODF_RSS = 'https://www.odf.or.kr/rss'


def fetch_feed(session, feed_url):
    """Resolve only explicitly supported public index URLs, never crawl links."""
    public_url(feed_url)
    if feed_url == ODF_PROJECTS:
        return session.get_browser(ODF_RSS, timeout=30)
    if feed_url.rstrip('/') == 'https://civictech.guide':
        from .civictech_guide import ENDPOINT
        return session.get(ENDPOINT, timeout=30)
    return session.get(feed_url, timeout=30)


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
    content = normalize_text(item.findtext('{http://purl.org/rss/1.0/modules/content/}encoded') or '', 'html')
    description = normalize_text(item.findtext('description') or '', 'html')
    text = '\n\n'.join(part for part in (title, content or description) if part)
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
              'content_scope': 'feed_content' if content else 'feed_excerpt', 'eligible': bool(text),
              'container_kind': 'feed', 'raw_version': 1})


def parse_feed(content, feed_url):
    """Parse one supplied feed snapshot without issuing secondary requests."""
    if feed_url.rstrip('/') == 'https://www.code4japan.org/news':
        from .code4japan import parse
        return parse(content, feed_url)
    if feed_url.rstrip('/') == 'https://civictech.guide':
        from .civictech_guide import parse
        return parse(content, feed_url)
    if isinstance(content, bytes):
        sniff = content.lstrip(b'\xef\xbb\xbf \t\r\n')
    else:
        sniff = content.lstrip('\ufeff \t\r\n').encode()
    if sniff.startswith(b'{'):
        from .json_feed import parse
        return parse(json.loads(content), feed_url)
    root = ET.fromstring(content)
    if root.tag == '{http://www.w3.org/2005/Atom}feed':
        from .atom import parse
        return parse(root, feed_url)
    if root.tag == '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF':
        from .rdf_feed import parse
        return parse(root, feed_url)
    channel = root.find('channel')
    if root.tag != 'rss' or root.get('version') != '2.0' or channel is None:
        raise ValueError('Expected RSS 2.0, RSS 1.0, Atom 1.0, or JSON Feed')
    source_name = normalize_text(channel.findtext('title') or '', 'html') or urlsplit(feed_url).hostname
    if feed_url == ODF_PROJECTS:
        source_name += ' - 프로젝트'
    events = []
    for item in channel.findall('item'):
        if feed_url == ODF_PROJECTS:
            link = urlsplit((item.findtext('link') or '').strip())
            if (link.scheme != 'https' or link.netloc != 'www.odf.or.kr'
                    or link.path.rstrip('/') != '/archive-project'):
                continue
        event = to_event(item, feed_url, source_name)
        event.meta['feed_format'] = 'rss2'
        if feed_url == ODF_PROJECTS:
            event.meta['content_scope'] = 'feed_listing'
            event.meta['feed_url'] = ODF_RSS
        events.append(event)
    return source_name, events


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
            response = fetch_feed(session, feed_url)
            check_response(response)
            source_name, parsed_events = parse_feed(response.content, feed_url)
            events = []
            lower = min(now - days * 86400, state.get('last_success', now) - 7200)
            for event in parsed_events:
                # Scheduled entries are reconsidered next poll, not ingested as
                # acquired before publication (which also triggers clock health).
                if event.ts > now:
                    continue
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
