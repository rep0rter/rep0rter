"""Read the explicitly configured CfJ public static Slack archive.

Only public sidebar entries and same-channel static pages are followed. Each
page commits with its resume cursor; a changed archive generation restarts the
scan because chunk numbers can move. No raw exports, search DBs or Slack tokens
are needed. Rendered HTML cannot establish all raw Slack subtype/app metadata.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..sources import normalize_text
from ..store import Container, Event
from .state import BudgetExceeded, check_response, persist, read_state

URL = 'https://slack-archive-2fl.pages.dev'
NAME = 'Code for Japan Slack'
PREFIX = 'slack:cfj:'
STAMP = re.compile(r'\d{1,12}\.\d{6}')
CHANNEL = re.compile(r'html/(C[A-Z0-9]+)\.html')


def is_archive_url(url):
    return url.rstrip('/') == URL


def _document(session, url):
    response = session.get(url, timeout=30, allow_redirects=False)
    if response.status_code in (301, 302, 303, 307, 308):
        target = urljoin(url, response.headers.get('Location', ''))
        # Cloudflare Pages canonicalizes static .html routes. Count that
        # request too, and follow only this exact same-page transformation.
        if not url.endswith('.html') or target != url[:-5]:
            raise ValueError('CfJ archive redirected away from the expected page')
        url = target
        response = session.get(url, timeout=30, allow_redirects=False)
    if 300 <= response.status_code < 400:
        raise ValueError('CfJ archive returned an unexpected redirect')
    check_response(response)
    if getattr(response, 'url', url) != url:
        raise ValueError('CfJ archive redirected away from the expected page')
    return BeautifulSoup(response.content, 'html.parser')


def _generation(soup):
    generated = soup.select_one('#channels .generated time[datetime]')
    if generated is None:
        raise ValueError('CfJ archive has no generation timestamp')
    generation = generated['datetime']
    if datetime.fromisoformat(generation.replace('Z', '+00:00')).tzinfo is None:
        raise ValueError('CfJ generation timestamp requires a timezone')
    return generation


def parse_channels(content):
    soup = content if isinstance(content, BeautifulSoup) else BeautifulSoup(content, 'html.parser')
    generation = _generation(soup)
    channels = {}
    for heading in soup.select('#channels p.section'):
        if heading.get_text(strip=True) not in ('Public Channels', 'Archived Public Channels'):
            continue
        listing = heading.find_next_sibling()
        if listing is None or listing.name != 'ul':
            raise ValueError('CfJ public channel listing changed')
        for link in listing.select('a[href]'):
            match = CHANNEL.fullmatch(link['href'])
            name = link.get('title', '').strip()
            if match is None or not name:
                raise ValueError('Invalid CfJ public channel link')
            cid = match[1]
            if cid in channels and channels[cid] != name:
                raise ValueError('Conflicting CfJ channel names')
            channels[cid] = name
    if not channels:
        raise ValueError('CfJ archive has no public channels')
    return generation, channels


def parse_page(content, channel, name, page, generation=None):
    soup = content if isinstance(content, BeautifulSoup) else BeautifulSoup(content, 'html.parser')
    if generation is not None and _generation(soup) != generation:
        raise ValueError('CfJ archive generation changed during collection')
    header = soup.select_one('#messages .header h1')
    listing = soup.select_one('#messages .messages-list')
    if header is None or header.get_text(strip=True) != name or listing is None:
        raise ValueError('CfJ static page has the wrong channel or layout')
    if listing.has_attr('data-chunks'):
        raise ValueError('CfJ scrolling shell is not a static message page')
    nodes = listing.select('.message-gutter[id]')
    if not nodes and 'No messages were ever sent!' not in listing.get_text():
        raise ValueError('CfJ message listing is unexpectedly empty')
    events = {}
    page_url = f'{URL}/html/{channel}-{page}.html'
    for node in nodes:
        ts = node['id']
        if not STAMP.fullmatch(ts):
            raise ValueError('Invalid CfJ message timestamp')
        # Descendant replies must never supply a missing root's fields.
        def own(selector):
            return [item for item in node.select(selector)
                    if item.find_parent(class_='message-gutter') is node]
        timestamps, senders, texts = own('a.timestamp[href]'), own('.sender'), own('.text')
        if (len(timestamps) != 1 or timestamps[0]['href'] != '#' + ts
                or len(senders) != 1 or len(texts) != 1):
            raise ValueError('Malformed CfJ message identity/content')
        text_node = BeautifulSoup(str(texts[0]), 'html.parser')
        for link in text_node.select('a[href]'):
            link['href'] = urljoin(page_url, link['href'])
        for br in text_node.find_all('br'):
            br.replace_with('\n')
        text = normalize_text(str(text_node), 'html')
        author = ''
        bot = False
        for link in own('a.author-link[href]'):
            match = re.fullmatch(r'user-([UW][A-Z0-9]+)\.html', link['href'])
            if match:
                if author and author != match[1]:
                    raise ValueError('Conflicting CfJ author identities')
                author = match[1]
            bot = bot or link['href'] == 'bots.html'
        parent = node.find_parent(class_='message-gutter')
        parent_id = PREFIX + channel + ':' + parent['id'] if parent is not None else None
        notice = bool(re.fullmatch(r'@.+さんがチャンネルに参加しました', re.sub(r'\s+', '', text))
                      or re.fullmatch(r'.+ (?:has joined|joined|has left) the channel[.!]?', ' '.join(text.split())))
        reactions = 0
        for reaction in own('.reaction'):
            # An undownloaded custom emoji also renders as a span; the
            # trailing direct span is the numeric counter.
            spans = reaction.find_all('span', recursive=False)
            count = spans[-1] if spans else None
            if count is None or not count.get_text(strip=True).isdigit():
                raise ValueError('Malformed CfJ reaction count')
            reactions += int(count.get_text(strip=True))
        identity = PREFIX + channel + ':' + ts
        event = Event(
            id=identity, source='slack', kind='notice' if notice else 'thread_reply' if parent_id else 'message',
            container_id=PREFIX + channel, ts=float(ts), author_id=PREFIX + author if author else '',
            author_name=senders[0].get_text(strip=True), text=text,
            url=page_url + '#' + ts, parent_id=parent_id,
            reply_count=len([child for child in node.select('.message-gutter[id]')
                             if child.find_parent(class_='message-gutter') is node]),
            reaction_count=reactions,
            meta={'source_instance': URL, 'source_name': NAME, 'external_id': ts,
                  'canonical_object_id': identity, 'visibility': 'public',
                  'content_format': 'plain', 'plain_text': text, 'content_scope': 'archive_html',
                  'eligible': bool(author and text and not notice and not bot), 'is_bot': bot,
                  'actor_metadata_complete': False, 'raw_version': 1})
        if identity in events:
            raise ValueError('Duplicate CfJ message anchor')
        events[identity] = event
    links = soup.select('#messages .pagination a[rel~=next]')
    if len(links) > 1:
        raise ValueError('Ambiguous CfJ pagination')
    next_page = None
    if links:
        if links[0].get('href') != f'{channel}-{page + 1}.html':
            raise ValueError('CfJ pagination left the channel or made no progress')
        next_page = page + 1
    return list(events.values()), next_page


def collect(store, session, metrics, days=2):
    from ..policy import container_allowed, event_allowed
    from .registry import record_source_error

    if not 1 <= days <= 365:
        raise ValueError('CfJ collection days must be between 1 and 365')
    generation, channels = parse_channels(_document(session, URL + '/'))
    total = 0
    # Rotate channels so a long archive cannot starve a small one.
    ordered = sorted(channels, key=lambda channel: read_state(store, PREFIX + channel).get('last_attempt', 0))
    for channel in ordered:
        cid, name = PREFIX + channel, channels[channel]
        if not container_allowed(store, cid):
            continue
        state, now = read_state(store, cid), time.time()
        if now < state.get('retry_at', 0):
            record_source_error(store, 'cfj_slack:' + channel, 'waiting for upstream rate limit reset')
            continue
        # Keep the lower bound throughout a resumed scan. Read every chunk:
        # a reply to an old root may be recent, regardless of the root's date.
        resume = state.get('next_page') if state.get('generation') == generation else None
        if resume and now - days * 86400 < state.get('lower', now):
            # A wider backfill must revisit intermediate chunks that an
            # unfinished narrow scan already read but did not retain.
            resume = None
        lower = state.get('lower', now - days * 86400) if resume else min(now - days * 86400, state.get('last_success', now) - 7200)
        page = 0
        try:
            store.upsert_container(Container(id=cid, source='slack', name=name,
                                             url=f'{URL}/html/{channel}.html'))
            while True:
                events, next_page = parse_page(_document(session, f'{URL}/html/{channel}-{page}.html'), channel, name, page, generation)
                selected = [event for event in events if event.ts <= now
                            and (event.ts >= lower or store.get_event(event.id)) and event_allowed(store, event)]
                # Preserve available context for a recent reply to an old root.
                parents = {event.parent_id for event in selected if event.parent_id}
                for event in reversed(events):
                    if event.id in parents and event not in selected and event.ts <= now and event_allowed(store, event):
                        selected.append(event)
                        if event.parent_id:
                            parents.add(event.parent_id)
                for event in selected:
                    event.meta.update(archive_generated_at=generation, observed_at=now,
                                      fetched_at=now, bootstrap=not state)
                # Refresh the head before resuming deeper into the same generation.
                next_page = resume if page == 0 and resume else next_page
                resume = None
                state = dict(state, generation=generation, lower=lower, next_page=next_page,
                             last_attempt=now, error='', retry_at=0)
                if next_page is None:
                    state['last_success'] = now
                persist(store, cid, state, selected, metrics, now)
                total += len(selected)
                if next_page is None:
                    break
                page = next_page
        except Exception as exc:
            attempt = {} if isinstance(exc, BudgetExceeded) else {'last_attempt': now}
            persist(store, cid, dict(state, **attempt, error=str(exc),
                                    retry_at=getattr(exc, 'retry_at', 0)), [], metrics, now)
            record_source_error(store, 'cfj_slack:' + channel, exc)
    return total
