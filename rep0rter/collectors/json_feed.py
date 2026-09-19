"""JSON Feed 1/1.1 snapshots, without pagination or guessed publication dates."""
from urllib.parse import urlsplit

from ..sources import normalize_text
from .feed_common import iso_date, make_event, public_url

VERSIONS = {'https://jsonfeed.org/version/1', 'https://jsonfeed.org/version/1.1'}


def _string(data, field, default=''):
    value = data.get(field, default)
    if not isinstance(value, str):
        raise ValueError(f'JSON Feed {field} must be a string')
    return value


def _authors(data):
    # The plural field supersedes the deprecated v1 field, even when empty.
    if 'authors' in data:
        authors = data['authors']
        if not isinstance(authors, list):
            raise ValueError('JSON Feed authors must be an array')
    elif 'author' in data:
        authors = [data['author']]
    else:
        return None
    names = []
    for author in authors:
        if not isinstance(author, dict):
            raise ValueError('JSON Feed author must be an object')
        name = _string(author, 'name').strip()
        if name and name not in names:
            names.append(name)
    return ', '.join(names)


def parse(data, feed_url):
    """Return an entirely validated snapshot; malformed items fail atomically."""
    public_url(feed_url)
    if (not isinstance(data, dict) or not isinstance(data.get('version'), str)
            or data['version'] not in VERSIONS):
        raise ValueError('Expected JSON Feed version 1 or 1.1')
    if 'title' not in data:
        raise ValueError('JSON Feed requires a title')
    source_name = _string(data, 'title').strip() or urlsplit(feed_url).hostname
    if not isinstance(data.get('items'), list):
        raise ValueError('JSON Feed items must be an array')
    feed_author = _authors(data) or ''
    events = []
    for item in data['items']:
        if not isinstance(item, dict):
            raise ValueError('JSON Feed item must be an object')
        external_id = _string(item, 'id')
        if not external_id.strip():
            raise ValueError('JSON Feed item requires a stable string id')
        url = public_url(_string(item, 'url') or _string(item, 'external_url'))
        if 'date_published' in item:
            published = iso_date(item['date_published'])
            extra = {'timestamp_basis': 'published'}
        elif 'date_modified' in item:
            published = iso_date(item['date_modified'])
            extra = {'timestamp_basis': 'modified', 'eligible': False}
        else:
            raise ValueError('JSON Feed item requires date_published or date_modified')
        title = _string(item, 'title')
        content_text = _string(item, 'content_text')
        content_html = _string(item, 'content_html')
        summary = _string(item, 'summary')
        body = content_text if content_text.strip() else normalize_text(content_html, 'html')
        scope = 'feed_content' if body.strip() else 'feed_excerpt'
        body = body if body.strip() else summary
        author = _authors(item)
        events.append(make_event(
            feed_url=feed_url, source_name=source_name, external_id=external_id,
            url=url, published=published, title=title, body=body,
            author=feed_author if author is None else author,
            content_scope=scope, feed_format='json_feed', extra=extra))
    return source_name, events
