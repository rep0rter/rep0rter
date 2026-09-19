"""Shared identities and evidence metadata for public syndication formats."""
from datetime import datetime
import hashlib
from urllib.parse import urlsplit

from ..store import Event


def public_url(value):
    if not isinstance(value, str):
        raise ValueError('Feed links must be public HTTP(S) URLs')
    parsed = urlsplit(value)
    if (parsed.scheme not in ('https', 'http') or not parsed.hostname
            or parsed.username or parsed.password):
        raise ValueError('Feed links must be public HTTP(S) URLs')
    return value


def object_id(feed_url, external_id):
    return 'rss:' + hashlib.sha256((feed_url + '\n' + external_id).encode()).hexdigest()


def iso_date(value):
    if not isinstance(value, str) or 'T' not in value:
        raise ValueError('Feed timestamp must be an ISO datetime with a timezone')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Feed timestamp must include a timezone')
    return parsed.timestamp()


def make_event(*, feed_url, source_name, external_id, url, published,
               title='', body='', author='', content_scope='feed_excerpt',
               feed_format, extra=None):
    """Inputs are already plain text; source='rss' preserves editorial integration."""
    public_url(feed_url)
    public_url(url)
    if not isinstance(external_id, str) or not external_id.strip():
        raise ValueError('Feed entry requires a stable identifier')
    text = '\n\n'.join(part for part in (title, body) if part)
    origin = urlsplit(feed_url)
    meta = {'source_instance': origin.scheme + '://' + origin.netloc,
            'source_name': source_name, 'external_id': external_id,
            'canonical_object_id': url, 'feed_url': feed_url,
            'visibility': 'public', 'content_format': 'plain', 'plain_text': text,
            'content_scope': content_scope, 'eligible': bool(text),
            'container_kind': 'feed', 'raw_version': 1, 'feed_format': feed_format}
    meta.update(extra or {})
    return Event(id=object_id(feed_url, external_id), source='rss', kind='article',
                 container_id='rss-feed:' + feed_url, ts=published,
                 author_id=object_id(feed_url, 'author:' + author) if author else '',
                 author_name=author, text=text, url=url, meta=meta)
