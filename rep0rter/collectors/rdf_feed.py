"""RSS 1.0 (RDF) snapshots with Dublin Core publication dates."""
from urllib.parse import urljoin, urlsplit

from ..sources import normalize_text
from .feed_common import iso_date, make_event

RSS = '{http://purl.org/rss/1.0/}'
RDF = '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}'
DC = '{http://purl.org/dc/elements/1.1/}'
CONTENT = '{http://purl.org/rss/1.0/modules/content/}'
BASE = '{http://www.w3.org/XML/1998/namespace}base'


def parse(root, feed_url):
    channel = root.find(RSS + 'channel')
    if root.tag != RDF + 'RDF' or channel is None:
        raise ValueError('Expected an RSS 1.0 channel')
    name = normalize_text(channel.findtext(RSS + 'title') or '', 'html') or urlsplit(feed_url).hostname
    base = urljoin(feed_url, root.get(BASE, ''))
    events = []
    for item in root.findall(RSS + 'item'):
        item_base = urljoin(base, item.get(BASE, ''))
        link = (item.findtext(RSS + 'link') or '').strip()
        if not link:
            raise ValueError('RSS 1.0 item requires an article link')
        url = urljoin(item_base, link)
        external = item.get(RDF + 'about') or url
        if item.get(RDF + 'about'):
            external = urljoin(item_base, external)
        content = normalize_text(item.findtext(CONTENT + 'encoded') or '', 'html')
        description = normalize_text(item.findtext(RSS + 'description') or '', 'html')
        events.append(make_event(
            feed_url=feed_url, source_name=name, external_id=external, url=url,
            published=iso_date(item.findtext(DC + 'date')),
            title=normalize_text(item.findtext(RSS + 'title') or '', 'html'),
            body=content or description, author=(item.findtext(DC + 'creator') or '').strip(),
            content_scope='feed_content' if content else 'feed_excerpt', feed_format='rss1'))
    return name, events
