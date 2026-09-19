from unittest.mock import Mock

import pytest

from rep0rter.collectors import rss
from rep0rter.collectors.state import Metrics, read_state
from rep0rter.store import Store

FEED = 'https://example.test/rss.rdf'
XML = b'''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns="http://purl.org/rss/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:content="http://purl.org/rss/1.0/modules/content/" xml:base="https://example.test/news/">
 <channel rdf:about="https://example.test/rss.rdf"><title>Civic news</title></channel>
 <item rdf:about="one"><title>Open map</title><link>one</link>
 <dc:date>2026-09-18T09:00:00+09:00</dc:date><dc:creator>Community</dc:creator>
 <description>Excerpt</description><content:encoded>&lt;p&gt;Body&lt;/p&gt;&lt;script&gt;unsafe&lt;/script&gt;</content:encoded>
 </item></rdf:RDF>'''


def test_rdf_date_identity_body_and_dedup(tmp_path):
    name, events = rss.parse_feed(XML, FEED)
    event, = events
    assert name == 'Civic news'
    assert event.url == 'https://example.test/news/one'
    assert event.meta['external_id'] == event.url
    assert event.ts == 1789689600
    assert event.text == 'Open map\n\nBody'
    assert event.author_name == 'Community'
    assert event.meta['feed_format'] == 'rss1'
    with Store(tmp_path / 'db') as store:
        session = Mock()
        session.get.return_value = Mock(content=XML, status_code=200, headers={})
        metrics = Metrics()
        rss.collect(store, [FEED], session, metrics, days=3650)
        rss.collect(store, [FEED], session, metrics, days=3650)
        assert store.event_count() == 1 and metrics.duplicate_payloads == 1
        assert read_state(store, 'rss-feed:' + FEED)['error'] == ''


@pytest.mark.parametrize('body', [
    XML.replace(b'2026-09-18T09:00:00+09:00', b'2026-09-18T09:00:00'),
    XML.replace(b'<link>one</link>', b'<link>javascript:alert(1)</link>'),
    XML.replace(b'<link>one</link>', b''),
])
def test_rdf_rejects_unusable_dates_and_links(body):
    with pytest.raises(ValueError):
        rss.parse_feed(body, FEED)
