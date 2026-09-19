from datetime import datetime
import json

import pytest

from rep0rter.collectors.code4japan import URL, parse


def row():
    return dict(id='stable-id', title='Public data & civic tech', slug='project-one',
                date='2026-09-18T09:30:00+09:00', tags=['活動レポート'])


def snapshot(rows, maximum=None):
    payload = {'props': {'pageProps': {'data': rows,
                                     'max': len(rows) if maximum is None else maximum}}}
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + '</script>'


def changed(**changes):
    result = row()
    result.update(changes)
    return result


def test_listing_evidence_and_original_timestamp():
    name, events = parse(snapshot([row()]).encode(), URL)
    event, = events
    assert name == 'Code for Japan'
    assert event.text == 'Public data & civic tech\n\n活動レポート'
    assert event.ts == datetime.fromisoformat('2026-09-18T09:30:00+09:00').timestamp()
    assert event.url == URL + '/project-one'
    assert event.meta['feed_format'] == 'code4japan-json'
    assert event.meta['content_scope'] == 'feed_listing'
    assert event.meta['tags'] == ['活動レポート']
    assert event.meta['plain_text'] == event.text
    assert event.author_name == ''


def test_trailing_slash_routes_to_the_same_parser():
    from rep0rter.collectors.rss import parse_feed
    name, events = parse_feed(snapshot([row()]), URL + '/')
    assert name == 'Code for Japan'
    assert len(events) == 1
    assert events[0].container_id == 'rss-feed:' + URL + '/'


def test_day_precision_and_encoded_local_slug():
    event, = parse(snapshot([changed(date='2026-09-18', slug='公共 データ/#1')]), URL)[1]
    assert event.ts == datetime.fromisoformat('2026-09-18T00:00:00+09:00').timestamp()
    assert event.meta['date_precision'] == 'day'
    assert event.url == URL + '/%E5%85%AC%E5%85%B1%20%E3%83%87%E3%83%BC%E3%82%BF%2F%231'


def test_external_links_and_edits_preserve_identity_and_publication_date():
    original, = parse(snapshot([row()]), URL)[1]
    edited, = parse(snapshot([changed(title='Edited headline', slug='https://example.org/article?q=1',
                                      updatedAt='2026-09-19T00:00:00Z', author='Not index evidence')]), URL)[1]
    assert edited.id == original.id
    assert edited.ts == original.ts
    assert edited.url == 'https://example.org/article?q=1'
    assert edited.author_name == ''
    assert edited.text.startswith('Edited headline')


@pytest.mark.parametrize('slug', ['javascript:alert(1)', 'data:text/plain,test',
                                 '//example.org/path', '/other-path', 'https://user:pass@example.org/'])
def test_rejects_invalid_links(slug):
    with pytest.raises(ValueError):
        parse(snapshot([changed(slug=slug)]), URL)


@pytest.mark.parametrize('date', ['', '2026-09-18T09:30:00', '2026-02-30', 'yesterday'])
def test_never_invents_dates(date):
    with pytest.raises(ValueError):
        parse(snapshot([changed(date=date)]), URL)


def test_deduplicates_identical_records_but_checks_distinct_count():
    assert len(parse(snapshot([row(), row()], maximum=1), URL)[1]) == 1
    with pytest.raises(ValueError, match='Incomplete'):
        parse(snapshot([row(), row()], maximum=2), URL)
    with pytest.raises(ValueError, match='Conflicting duplicate'):
        parse(snapshot([row(), changed(title='Conflict')], maximum=1), URL)


@pytest.mark.parametrize('html', [
    '<script>{"props":{}}</script>',
    '<script id="__NEXT_DATA__">not-json</script>',
    '<script id="__NEXT_DATA__">{}</script>',
    '<script id="__NEXT_DATA__">null</script>',
    '<script id="__NEXT_DATA__">{"props":{"pageProps":{"data":[],"max":true}}}</script>',
])
def test_malformed_snapshots_fail_clearly(html):
    with pytest.raises(ValueError, match='Code for Japan'):
        parse(html, URL)


def test_ignores_unrelated_script_and_requires_single_snapshot():
    html = snapshot([row()])
    assert len(parse('<script>throw new Error("not data")</script>' + html, URL)[1]) == 1
    with pytest.raises(ValueError):
        parse(html + html, URL)


@pytest.mark.parametrize('record', [None, {}, changed(id=''), changed(tags='report'), changed(tags=[None])])
def test_malformed_records_fail_clearly(record):
    with pytest.raises(ValueError, match='Code for Japan'):
        parse(snapshot([record]), URL)


def test_missing_rows_fail_and_empty_confirmed_index_is_allowed():
    with pytest.raises(ValueError, match='Incomplete'):
        parse(snapshot([row()], maximum=2), URL)
    assert parse(snapshot([], maximum=0), URL) == ('Code for Japan', [])


def test_json_fields_are_plain_text():
    event, = parse(snapshot([changed(title='Use <data> & tools', tags=['A & B', 'A & B'])]), URL)[1]
    assert event.text == 'Use <data> & tools\n\nA & B'
