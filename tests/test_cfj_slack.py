"""Synthetic public HTML fixtures; no real archive posts or profile data."""
import json
from unittest.mock import Mock

import pytest

from rep0rter.collectors import cfj_slack as cfj, registry
from rep0rter.collectors.state import BudgetSession, Metrics, read_state
from rep0rter.policy import add_rule
from rep0rter.sources import eligible
from rep0rter.store import Store
from rep0rter.feed_tools import probe

NOW = 1790899200
GEN = '2026-10-01T00:00:00Z'
TS = str(NOW - 100) + '.123456'
OLD = str(NOW - 10 * 86400) + '.000001'


def sidebar(generation=GEN):
    return f'<div id="channels"><div class="generated"><time datetime="{generation}"></time></div></div>'


def homepage(generation=GEN):
    return sidebar(generation).replace('</div></div>', '</div>'
        '<p class="section">Public Channels</p><ul>'
        '<li><a title="public" href="html/CPUBLIC.html">public</a></li>'
        '<li><a title="public" href="html/CPUBLIC.html">public</a></li></ul>'
        '<p class="section">Private Channels</p><ul>'
        '<li><a title="private" href="html/CPRIVATE.html">private</a></li></ul>'
        '<p class="section">DMs</p><ul><li><a href="html/DSECRET.html">secret</a></li></ul></div>')


def message(ts=TS, text='公開 <a href="https://example.test/data">dataset</a>', *, author='UPERSON', replies=''):
    sender = '<span class="sender">Civic Author</span>'
    if author:
        href = 'bots.html' if author == 'BOT' else f'user-{author}.html'
        sender = f'<a class="author-link" href="{href}">{sender}</a>'
    return (f'<div class="message-gutter" id="{ts}">{sender}'
            f'<a class="timestamp" href="#{ts}">time</a><div class="text">{text}</div>'
            f'<div class="reaction"><span>2</span></div>{replies}</div>')


def page(rows='', number=0, next_page=None, generation=GEN):
    pagination = f'<div class="pagination"><a rel="next" href="CPUBLIC-{next_page}.html">Older</a></div>' if next_page is not None else ''
    return sidebar(generation) + '<main id="messages"><div class="header"><h1>public</h1>' + pagination + '</div><div class="messages-list">' + (rows or 'No messages were ever sent!') + '</div></main>'


def response(content):
    return Mock(status_code=200, headers={}, content=content.encode(), raise_for_status=Mock(), url=None)


def session_for(pages):
    session = Mock(headers={})
    def get(url, **kwargs):
        value = response(pages[url])
        value.url = url
        return value
    session.get.side_effect = get
    return session


def test_public_listing_deduplicates_and_ignores_private_dm():
    assert cfj.parse_channels(homepage()) == (GEN, {'CPUBLIC': 'public'})
    with pytest.raises(ValueError, match='Invalid CfJ'):
        cfj.parse_channels(homepage().replace('html/CPUBLIC.html', 'https://elsewhere.test/CPUBLIC.html'))


def test_exact_links_namespaced_identity_threads_and_plain_text():
    events, next_page = cfj.parse_page(page(message(OLD, replies=message())), 'CPUBLIC', 'public', 0, GEN)
    root, reply = events
    assert root.id == cfj.PREFIX + 'CPUBLIC:' + OLD
    assert root.author_id == cfj.PREFIX + 'UPERSON'
    assert reply.parent_id == root.id and reply.kind == 'thread_reply'
    assert root.reply_count == 1 and root.reaction_count == 2
    assert reply.text == '公開\ndataset (https://example.test/data)'
    assert reply.url == cfj.URL + '/html/CPUBLIC-0.html#' + TS
    assert eligible(root) and not eligible(reply)
    assert next_page is None


def test_missing_custom_emoji_span_is_not_a_reaction_counter():
    content = page(message()).replace('<div class="reaction">',
        '<div class="reaction"><span class="emoji-missing">:custom:</span>')
    event = cfj.parse_page(content, 'CPUBLIC', 'public', 0)[0][0]
    assert event.reaction_count == 2


@pytest.mark.parametrize('text,author,kind,allowed', [
    ('<span>@Civic Author</span>さんがチャンネルに参加しました', 'UPERSON', 'notice', False),
    ('Civic Author has joined the channel', 'UPERSON', 'notice', False),
    ('A civic update', '', 'message', False),
    ('A civic update', 'BOT', 'message', False),
    ('A civic update', 'UPERSON', 'message', True),
])
def test_notices_bots_unknown_authors(text, author, kind, allowed):
    event = cfj.parse_page(page(message(text=text, author=author)), 'CPUBLIC', 'public', 0)[0][0]
    assert event.kind == kind and eligible(event) == allowed


@pytest.mark.parametrize('bad', [
    page(message()).replace('#' + TS, '#other'),
    page(message()).replace('id="' + TS + '"', 'id="nan"'),
    page(message() + message()),
    page(message(), next_page=0),
    page(message(), next_page=1).replace('CPUBLIC-1.html', 'https://evil.test/other.html'),
    page(message()).replace('<h1>public</h1>', '<h1>private</h1>'),
    page(message()).replace('class="messages-list"', 'class="messages-list" data-chunks="1"'),
    page(message(), generation='2026-10-02T00:00:00Z'),
    page(message()).replace('class="text"', 'class="gone"'),
])
def test_fail_closed_on_changed_or_ambiguous_markup(bad):
    with pytest.raises(ValueError):
        cfj.parse_page(bad, 'CPUBLIC', 'public', 0, GEN)


def test_budget_resume_rechecks_head_and_keeps_old_root_for_new_reply(tmp_path, monkeypatch):
    monkeypatch.setattr(cfj.time, 'time', lambda: NOW)
    pages = {cfj.URL + '/': homepage(), cfj.URL + '/html/CPUBLIC-0.html': page(message(), next_page=1),
             cfj.URL + '/html/CPUBLIC-1.html': page(message(OLD, replies=message(str(NOW - 10) + '.654321')))}
    with Store(tmp_path / 'db') as store:
        metrics = Metrics()
        session = session_for(pages)
        cfj.collect(store, BudgetSession(session, metrics, limit=2, interval=0), metrics)
        state = read_state(store, cfj.PREFIX + 'CPUBLIC')
        assert state['next_page'] == 1 and state['error'] and 'last_success' not in state
        assert store.event_count() == 1
        metrics = Metrics()
        session = session_for(pages)
        cfj.collect(store, BudgetSession(session, metrics, limit=3, interval=0), metrics)
        assert [call.args[0] for call in session.get.call_args_list] == list(pages)
        state = read_state(store, cfj.PREFIX + 'CPUBLIC')
        assert state['next_page'] is None and state['last_success'] == NOW and not state['error']
        assert store.event_count() == 3
        assert store.get_event(cfj.PREFIX + 'CPUBLIC:' + OLD).reply_count == 1


def test_generation_change_discards_old_resume_and_existing_rows_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(cfj.time, 'time', lambda: NOW)
    url = cfj.URL + '/html/CPUBLIC-0.html'
    pages = {cfj.URL + '/': homepage(), url: page(message(), next_page=1)}
    with Store(tmp_path / 'db') as store:
        metrics = Metrics()
        cfj.collect(store, BudgetSession(session_for(pages), metrics, limit=2, interval=0), metrics)
        generation = '2026-10-02T00:00:00Z'
        pages = {cfj.URL + '/': homepage(generation), url: page(message(text='Edited civic update'), generation=generation)}
        session = session_for(pages)
        cfj.collect(store, session, Metrics())
        assert session.get.call_count == 2
        event = store.get_event(cfj.PREFIX + 'CPUBLIC:' + TS)
        assert event.text == 'Edited civic update' and event.meta['archive_generated_at'] == generation


def test_optouts_apply_before_persistence_and_container_fetch(tmp_path, monkeypatch):
    monkeypatch.setattr(cfj.time, 'time', lambda: NOW)
    pages = {cfj.URL + '/': homepage(), cfj.URL + '/html/CPUBLIC-0.html': page(message())}
    with Store(tmp_path / 'db') as store:
        add_rule(store, 'user', cfj.PREFIX + 'UPERSON')
        cfj.collect(store, session_for(pages), Metrics())
        assert store.event_count() == 0
        add_rule(store, 'container', cfj.PREFIX + 'CPUBLIC')
        session = session_for(pages)
        cfj.collect(store, session, Metrics())
        assert session.get.call_count == 1


def test_registry_routes_configured_archive_and_isolates_failure(tmp_path, monkeypatch):
    monkeypatch.setenv('REP0RTER_FEEDS', cfj.URL + '/,https://example.test/rss')
    monkeypatch.setenv('REP0RTER_RSS_FEEDS', cfj.URL)
    monkeypatch.setattr(registry.rss, 'collect', Mock(return_value=0))
    monkeypatch.setattr(registry.slack_incremental, 'collect', Mock(return_value=0))
    collect = Mock(side_effect=ValueError('invalid archive'))
    monkeypatch.setattr(cfj, 'collect', collect)
    with Store(tmp_path / 'db') as store:
        registry.collect_all(store, session=Mock(headers={}))
        assert collect.call_count == 1
        assert registry.rss.collect.call_args.args[1] == ['https://example.test/rss']
        health = json.loads(store.get_kv('collector_health'))
        assert not health['sources']['cfj_slack']['healthy'] and health['sources']['slack']['healthy']


def test_probe_reports_static_archive_channels():
    result, code = probe(cfj.URL + '/', session=session_for({cfj.URL + '/': homepage()}))
    assert code == 0 and result['format'] == 'slack-archive-html'
    assert result['count'] == 1 and result['channels'] == [{'id': 'CPUBLIC', 'name': 'public'}]


def test_cloudflare_canonical_redirect_is_counted_and_bounded():
    url = cfj.URL + '/html/CPUBLIC-0.html'
    redirect = Mock(status_code=308, headers={'Location': '/html/CPUBLIC-0'}, content=b'')
    final = response(page(message()))
    final.url = url[:-5]
    session = Mock(headers={})
    session.get.side_effect = [redirect, final]
    metrics = Metrics()
    document = cfj._document(BudgetSession(session, metrics, limit=2, interval=0), url)
    assert document.select_one('.message-gutter')['id'] == TS and metrics.requests == 2
    assert all(call.kwargs['allow_redirects'] is False for call in session.get.call_args_list)
    for target in ('https://elsewhere.test/data', '/html/CPRIVATE-0', '/html/CPUBLIC-0?token=secret'):
        session.get.side_effect = [Mock(status_code=308, headers={'Location': target}, content=b'')]
        with pytest.raises(ValueError, match='redirected away'):
            cfj._document(session, url)


def test_wider_backfill_revisits_chunks_skipped_by_a_partial_recent_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(cfj.time, 'time', lambda: NOW)
    pages = {cfj.URL + '/': homepage(),
             cfj.URL + '/html/CPUBLIC-0.html': page(message(), next_page=1),
             cfj.URL + '/html/CPUBLIC-1.html': page(message(OLD), next_page=2),
             cfj.URL + '/html/CPUBLIC-2.html': page()}
    with Store(tmp_path / 'db') as store:
        metrics = Metrics()
        cfj.collect(store, BudgetSession(session_for(pages), metrics, limit=3, interval=0), metrics)
        assert read_state(store, cfj.PREFIX + 'CPUBLIC')['next_page'] == 2
        assert store.get_event(cfj.PREFIX + 'CPUBLIC:' + OLD) is None
        session = session_for(pages)
        cfj.collect(store, session, Metrics(), days=90)
        assert [call.args[0] for call in session.get.call_args_list] == list(pages)
        assert store.get_event(cfj.PREFIX + 'CPUBLIC:' + OLD).ts == float(OLD)
        assert read_state(store, cfj.PREFIX + 'CPUBLIC')['next_page'] is None


def test_registry_limits_backfill_to_cfj_source(tmp_path, monkeypatch):
    monkeypatch.setenv('REP0RTER_FEEDS', cfj.URL + '/')
    monkeypatch.setenv('REP0RTER_CFJ_COLLECT_DAYS', '90')
    collect = Mock(return_value=0)
    slack = Mock(return_value=0)
    monkeypatch.setattr(cfj, 'collect', collect)
    monkeypatch.setattr(registry.slack_incremental, 'collect', slack)
    with Store(tmp_path / 'db') as store:
        registry.collect_all(store, days=2, session=Mock(headers={}))
    assert collect.call_args.args[3] == 90
    assert slack.call_args.args[1] == 2
