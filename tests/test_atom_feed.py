"""Atom format evidence, identity, and the existing collector storage path."""
from unittest.mock import Mock
from xml.etree import ElementTree as ET

import pytest

from rep0rter.collectors import rss
from rep0rter.collectors.feed_common import iso_date, object_id
from rep0rter.collectors.state import Metrics, read_state
from rep0rter.sources import eligible
from rep0rter.store import Store

FEED = 'https://example.test/feeds/atom.xml'
ENTRY = '''<entry xml:base="../articles/">
  <id>tag:example.test,2026:article-1</id>
  <title type="html">Community &lt;b&gt;projects&lt;/b&gt;</title>
  <link rel="self" href="article.xml"/>
  <link rel="enclosure" href="audio.mp3"/>
  <link href="one" xml:base="local/"/>
  <published>2026-09-18T09:00:00+09:00</published>
  <updated>2026-09-19T12:00:00Z</updated>
  <content type="html">&lt;p&gt;Local projects.&lt;/p&gt;&lt;script&gt;bad()&lt;/script&gt;</content>
  <summary>Summary not preferred over content.</summary>
</entry>'''


def document(entry=ENTRY, extra=''):
    return ('<feed xmlns="http://www.w3.org/2005/Atom" xml:base="https://example.test/news/">'
            '<title>Civic news</title><author><name>Feed Author</name></author>'
            + extra + entry + '</feed>').encode()


def test_atom_content_identity_dates_and_relative_link():
    name, events = rss.parse_feed(document(), FEED)
    event, = events
    assert name == 'Civic news'
    assert event.url == 'https://example.test/articles/local/one'
    assert event.id == object_id(FEED, 'tag:example.test,2026:article-1')
    assert event.ts == iso_date('2026-09-18T00:00:00Z')
    assert event.author_name == 'Feed Author'
    assert event.text == 'Community\nprojects\n\nLocal projects.'
    assert event.meta['feed_format'] == 'atom'
    assert event.meta['content_scope'] == 'feed_content'
    assert event.meta['timestamp_basis'] == 'published'
    assert eligible(event)


def test_atom_xhtml_sanitized_and_entry_author_preferred():
    entry = ENTRY.replace('<content type="html">&lt;p&gt;Local projects.&lt;/p&gt;&lt;script&gt;bad()&lt;/script&gt;</content>',
                          '<content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">'
                          '<p>Project <a href="https://example.test/details">details</a></p>'
                          '<script>bad()</script><style>badstyle</style></div></content>')
    entry = entry.replace('<id>', '<author><name>Article Author</name></author><id>')
    _, (event,) = rss.parse_feed(document(entry), FEED)
    assert 'details (https://example.test/details)' in event.text
    assert 'bad' not in event.text
    assert event.author_name == 'Article Author'


def test_atom_plain_text_preserved_and_out_of_line_content_uses_summary():
    entry = ENTRY.replace('type="html">Community &lt;b&gt;projects&lt;/b&gt;',
                          'type="text">Community &lt;b&gt;projects&lt;/b&gt;')
    entry = entry.replace('<content type="html">', '<content type="html" src="https://example.test/full">')
    _, (event,) = rss.parse_feed(document(entry), FEED)
    assert '<b>projects</b>' in event.text
    assert 'Summary not preferred' in event.text
    assert 'Local projects' not in event.text
    assert event.meta['content_scope'] == 'feed_excerpt'


def test_updated_only_is_retained_but_not_reported_as_new_publication():
    entry = ENTRY.replace('<published>2026-09-18T09:00:00+09:00</published>', '')
    _, (event,) = rss.parse_feed(document(entry), FEED)
    assert event.ts == iso_date('2026-09-19T12:00:00Z')
    assert event.meta['timestamp_basis'] == 'updated'
    assert not eligible(event)


@pytest.mark.parametrize('stamp', ['', '2026-09-18', '2026-09-18T09:00:00', 'not-a-date'])
def test_bad_publication_date_never_falls_back_to_updated(stamp):
    with pytest.raises(ValueError):
        rss.parse_feed(document(ENTRY.replace('2026-09-18T09:00:00+09:00', stamp)), FEED)


def test_missing_dates_rejected_and_missing_id_uses_article_url():
    entry = ENTRY.replace('<id>tag:example.test,2026:article-1</id>', '')
    _, (event,) = rss.parse_feed(document(entry), FEED)
    assert event.id == object_id(FEED, event.url)
    entry = entry.replace('<published>2026-09-18T09:00:00+09:00</published>', '')
    entry = entry.replace('<updated>2026-09-19T12:00:00Z</updated>', '')
    with pytest.raises(ValueError):
        rss.parse_feed(document(entry), FEED)


def test_malformed_xml_and_wrong_namespace_are_not_accepted_as_atom():
    with pytest.raises(ET.ParseError):
        rss.parse_feed(document()[:-7], FEED)
    with pytest.raises(ValueError):
        rss.parse_feed(document().replace(b'http://www.w3.org/2005/Atom',
                                          b'https://example.test/not-atom'), FEED)


@pytest.mark.parametrize('link', ['', '<link rel="alternate" href="javascript:alert(1)"/>',
                                 '<link href="https://user:secret@example.test/article"/>'])
def test_self_enclosure_and_unsafe_links_are_not_article_links(link):
    entry = ENTRY.replace('<link href="one" xml:base="local/"/>', link)
    with pytest.raises(ValueError, match='alternate article link'):
        rss.parse_feed(document(entry), FEED)


def test_collector_stores_atom_and_deduplicates_without_secondary_requests(tmp_path, monkeypatch):
    monkeypatch.setattr(rss.time, 'time', lambda: iso_date('2026-09-19T12:00:00Z'))
    session = Mock()
    session.get.return_value = Mock(status_code=200, content=document(), headers={})
    metrics = Metrics()
    with Store(tmp_path / 'db') as store:
        assert rss.collect(store, [FEED], session, metrics) == 1
        assert rss.collect(store, [FEED], session, metrics) == 1
        assert store.event_count() == 1
        assert metrics.duplicate_payloads == 1
        assert read_state(store, 'rss-feed:' + FEED)['error'] == ''
    assert session.get.call_count == 2
    session.get.assert_called_with(FEED, timeout=30)
