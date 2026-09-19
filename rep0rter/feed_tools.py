"""Read-only discovery and inspection of explicitly supplied public feeds."""
from __future__ import annotations

import ipaddress
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

from .collectors import notion, rss
from .collectors.state import BudgetSession, Metrics, check_response

CATALOG_PATH = Path(__file__).with_name('feed_catalog.json')
MAX_BYTES = 2 * 1024 * 1024
FEED_TYPES = {'application/rss+xml', 'application/atom+xml',
              'application/feed+json', 'application/json'}


def public_url(url):
    """Reject credentials, non-web schemes, and explicitly local destinations."""
    rss.public_url(url)
    parsed = urlsplit(url)
    host = parsed.hostname.lower().rstrip('.')
    if host == 'localhost' or host.endswith(('.localhost', '.local')) or '.' not in host:
        raise ValueError('Use a public HTTP(S) URL without credentials')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError('Use a public HTTP(S) URL without credentials')
    # Validate malformed port values before making a request.
    parsed.port
    return url


class _BoundedSession(requests.Session):
    """Read a bounded response; redirects are counted explicitly by the probe."""
    def __init__(self):
        super().__init__()
        self.trust_env = False  # Never use local .netrc credentials for public discovery.
        self.headers['User-Agent'] = 'rep0rter/feed-probe'

    def get(self, url, **kwargs):
        response = super().get(url, stream=True, allow_redirects=False, **kwargs)
        try:
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError('Response exceeds the 2 MiB probe limit')
                chunks.append(chunk)
            response._content = b''.join(chunks)
            response._content_consumed = True
            return response
        finally:
            response.close()


def discover(content, page_url):
    """Only report publisher-advertised feed links; never guess or follow them."""
    soup = BeautifulSoup(content, 'html.parser')
    candidates = []
    for link in soup.find_all('link'):
        if 'alternate' not in [str(rel).lower() for rel in link.get('rel', [])]:
            continue
        media_type = str(link.get('type', '')).split(';', 1)[0].strip().lower()
        href = link.get('href')
        if media_type not in FEED_TYPES or not href:
            continue
        try:
            url = public_url(urljoin(page_url, href))
        except ValueError:
            continue
        if any(item['url'] == url for item in candidates):
            continue
        candidates.append({'url': url, 'type': media_type,
                           'title': str(link.get('title', ''))[:200]})
        if len(candidates) == 5:
            break
    return candidates


def _date(event):
    # Directory dates retain their declared precision; they are not news dates.
    if event.meta.get('source_date'):
        return event.meta['source_date']
    return datetime.fromtimestamp(event.ts, timezone.utc).isoformat()


def probe(url, *, session=None):
    """Return a JSON-ready report and process status; never open the event store."""
    report = {'status': 'error', 'source': None, 'count': 0, 'latest': None,
              'format': None, 'sample': [], 'discovered': []}
    try:
        public_url(url)
    except (ValueError, TypeError, AttributeError):
        report['error'] = 'Use a public HTTP(S) URL without credentials'
        return report, 1
    report['url'] = url
    if notion.is_notion_url(url):
        try:
            notion.parse_url(url)
        except ValueError:
            report['error'] = 'Notion requires a public HTTPS page URL with its page ID'
            return report, 1
        report.update(status='supported', source='Notion', format='notion', count=None,
                      note='Supported by the public Notion collector; accessibility was not checked')
        return report, 0
    metrics = Metrics()
    owned_session = _BoundedSession() if session is None else None
    transport = session or BudgetSession(owned_session, metrics, limit=2, interval=0)
    try:
        response = rss.fetch_feed(transport, url)
        if response.status_code in (301, 302, 303, 307, 308):
            base = getattr(response, 'url', None) or url
            target = public_url(urljoin(base, response.headers.get('Location', '')))
            if not response.headers.get('Location'):
                raise ValueError('Redirect has no destination')
            response = transport.get(target, timeout=30)
        if 300 <= response.status_code < 400:
            raise ValueError('Probe stopped after one redirect')
        check_response(response)
        content = response.content
        if len(content) > MAX_BYTES:
            raise ValueError('Response exceeds the 2 MiB probe limit')
        try:
            name, events = rss.parse_feed(content, url)
        except Exception:
            page_url = getattr(response, 'url', None) or url
            candidates = discover(content, page_url)
            if candidates:
                report.update(status='discovered', source=urlsplit(url).hostname,
                              format='html', discovered=candidates,
                              note='Advertised candidates only; run feeds probe on a candidate to verify it')
                return report, 0
            report['error'] = 'Response is not a supported feed and advertises no feed links'
            return report, 1
        ordered = sorted(events, key=lambda event: event.ts, reverse=True)
        report.update(status='ok', source=name, count=len(events),
                      latest=_date(ordered[0]) if ordered else None,
                      format=events[0].meta.get('feed_format', 'unknown') if events else _empty_format(content, url),
                      sample=[{'title': event.text.split('\n', 1)[0][:300],
                               'url': event.url, 'date': _date(event)} for event in ordered[:3]])
        return report, 0
    except Exception as exc:
        # Transport exceptions can contain headers or URLs; never echo them to stdout.
        report['error'] = 'Probe failed: ' + type(exc).__name__
        return report, 1
    finally:
        if owned_session is not None:
            owned_session.close()


def _empty_format(content, url):
    special = {'https://www.code4japan.org/news': 'code4japan-json',
               'https://civictech.guide': 'civictech-guide-json'}
    if url.rstrip('/') in special:
        return special[url.rstrip('/')]
    from xml.etree import ElementTree as ET
    if content.lstrip(b'\xef\xbb\xbf \t\r\n').startswith(b'{'):
        return 'json_feed'
    root = ET.fromstring(content)
    return {'rss': 'rss2', '{http://www.w3.org/2005/Atom}feed': 'atom',
            '{http://www.w3.org/1999/02/22-rdf-syntax-ns#}RDF': 'rss1'}.get(root.tag, 'unknown')


def cmd_catalog(cfg, args):
    try:
        catalog = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
        if not isinstance(catalog, list):
            raise ValueError('Catalog must be an array')
    except (OSError, ValueError):
        print(json.dumps({'status': 'error', 'error': 'Feed catalog could not be loaded'}))
        return 1
    print(json.dumps(catalog, ensure_ascii=False, indent=2))
    return 0


def cmd_probe(cfg, args):
    report, code = probe(args.url)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return code


def register_commands(sub):
    parser = sub.add_parser('feeds', help='inspect public feed sources without activating or publishing them')
    commands = parser.add_subparsers(dest='feed_command', required=True)
    catalog = commands.add_parser('catalog', help='show the checked-in public source catalog as JSON')
    catalog.set_defaults(func=cmd_catalog, readonly=True)
    inspect = commands.add_parser('probe', help='inspect a supplied feed or discover advertised feed links')
    inspect.add_argument('url')
    inspect.set_defaults(func=cmd_probe, readonly=True)
