"""Atom 1.0 snapshots (RFC 4287), without fetching linked content."""
from copy import deepcopy
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

from ..sources import normalize_text
from .feed_common import iso_date, make_event, public_url

ATOM = '{http://www.w3.org/2005/Atom}'
XML_BASE = '{http://www.w3.org/XML/1998/namespace}base'


def _text(element):
    if element is None or element.get('src'):
        return ''
    kind = element.get('type', 'text').lower().split(';', 1)[0].strip()
    if kind in ('text', 'text/plain'):
        return normalize_text(element.text or '', 'plain').strip()
    if kind in ('html', 'text/html'):
        return normalize_text(element.text or '', 'html').strip()
    if kind in ('xhtml', 'application/xhtml+xml'):
        # ElementTree prefixes namespaced tags on serialization. Strip namespaces
        # on a copy so the common HTML sanitizer recognizes script/style tags.
        copy = deepcopy(element)
        for node in copy.iter():
            node.tag = node.tag.rsplit('}', 1)[-1]
        return normalize_text(ET.tostring(copy, encoding='unicode'), 'html').strip()
    # Binary, XML, or other out-of-line content is not article text.
    return ''


def _author(element):
    return (element.findtext(ATOM + 'author/' + ATOM + 'name') or '').strip()


def _article_url(entry, base):
    links = [link for link in entry.findall(ATOM + 'link')
             if link.get('rel', 'alternate') in (
                 'alternate', 'http://www.iana.org/assignments/relation/alternate')
             and (link.get('href') or '').strip()]
    links.sort(key=lambda link: link.get('type', '').split(';', 1)[0].strip()
               not in ('', 'text/html', 'application/xhtml+xml'))
    for link in links:
        target = urljoin(urljoin(base, link.get(XML_BASE, '')), link.get('href').strip())
        try:
            return public_url(target)
        except ValueError:
            continue
    raise ValueError('Atom entry requires a public alternate article link')


def parse(root, feed_url):
    public_url(feed_url)
    if root.tag != ATOM + 'feed':
        raise ValueError('Expected an Atom 1.0 feed')
    source_name = _text(root.find(ATOM + 'title')) or urlsplit(feed_url).hostname
    base = urljoin(feed_url, root.get(XML_BASE, ''))
    events = []
    for entry in root.findall(ATOM + 'entry'):
        url = _article_url(entry, urljoin(base, entry.get(XML_BASE, '')))
        published = entry.find(ATOM + 'published')
        basis = 'published' if published is not None else 'updated'
        stamp = published if published is not None else entry.find(ATOM + 'updated')
        ts = iso_date((stamp.text or '').strip() if stamp is not None else '')
        title = _text(entry.find(ATOM + 'title'))
        content = _text(entry.find(ATOM + 'content'))
        summary = _text(entry.find(ATOM + 'summary'))
        author = _author(entry)
        source = entry.find(ATOM + 'source')
        if not author and source is not None:
            author = _author(source)
        extra = {'timestamp_basis': basis}
        if basis == 'updated':
            extra['eligible'] = False
        events.append(make_event(
            feed_url=feed_url, source_name=source_name,
            external_id=(entry.findtext(ATOM + 'id') or '').strip() or url,
            url=url, published=ts, title=title, body=content or summary,
            author=author or _author(root), feed_format='atom',
            content_scope='feed_content' if content else 'feed_excerpt', extra=extra))
    return source_name, events
