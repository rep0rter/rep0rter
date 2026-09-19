"""Code for Japan's dated news index embedded in its public Next.js page."""
from datetime import datetime, timedelta, timezone
import json
import re
from urllib.parse import quote, urlsplit

from bs4 import BeautifulSoup

from .feed_common import iso_date, make_event, public_url

URL = 'https://www.code4japan.org/news'
NAME = 'Code for Japan'
JST = timezone(timedelta(hours=9))


def _text(row, key):
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Code for Japan news requires {key}')
    return value.strip()


def _article_url(slug):
    parsed = urlsplit(slug)
    if parsed.scheme:
        return public_url(slug)
    if parsed.netloc or slug.startswith(('/', '\\')):
        raise ValueError('Code for Japan news requires a local slug or absolute HTTP(S) URL')
    return URL + '/' + quote(slug, safe='')


def parse(content, feed_url):
    """Parse a complete supplied listing snapshot; never fetch article bodies."""
    if feed_url.rstrip('/') != URL:
        raise ValueError('Unexpected Code for Japan news URL')
    soup = BeautifulSoup(content, 'html.parser')
    scripts = soup.find_all('script', id='__NEXT_DATA__')
    if len(scripts) != 1:
        raise ValueError('Expected one Code for Japan __NEXT_DATA__ snapshot')
    try:
        payload = json.loads(scripts[0].get_text())
        page = payload['props']['pageProps']
        rows, maximum = page['data'], page['max']
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('Malformed Code for Japan news snapshot') from exc
    if not isinstance(rows, list) or type(maximum) is not int or maximum < 0:
        raise ValueError('Malformed Code for Japan news data or max')

    unique = {}
    events = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Malformed Code for Japan news row')
        external_id = _text(row, 'id')
        if external_id in unique:
            if unique[external_id] != row:
                raise ValueError('Conflicting duplicate Code for Japan news ID')
            continue
        unique[external_id] = row
        title = _text(row, 'title')
        url = _article_url(_text(row, 'slug'))
        date = _text(row, 'date')
        extra = {}
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
            published = datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=JST).timestamp()
            extra['date_precision'] = 'day'
        else:
            published = iso_date(date)
        tags = row.get('tags', [])
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ValueError('Malformed Code for Japan news tags')
        tags = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
        extra['tags'] = tags
        events.append(make_event(
            feed_url=feed_url, source_name=NAME, external_id=external_id,
            url=url, published=published, title=title, body=', '.join(tags),
            content_scope='feed_listing', feed_format='code4japan-json', extra=extra))
    if len(unique) != maximum:
        raise ValueError('Incomplete Code for Japan news snapshot: '
                         f'expected {maximum} distinct records, received {len(unique)}')
    return NAME, events
