"""Small explicit source policies and safe, format-aware text normalization."""
from __future__ import annotations
import re
from bs4 import BeautifulSoup
from .slack_text import to_plain

ELIGIBLE_KINDS = {'slack': {'message'}, 'github': {'release', 'issue', 'pull_request'}, 'mastodon': {'status'}, 'rss': {'article'}, 'notion': {'article'}}


def normalize_text(text, content_format):
    if content_format == 'plain':
        return text or ''
    if content_format == 'mrkdwn':
        return to_plain(text)
    if content_format == 'html':
        soup = BeautifulSoup(text or '', 'html.parser')
        for node in soup(['script', 'style', 'iframe']):
            node.decompose()
        for link in soup.find_all('a', href=True):
            href = link['href']
            if href.startswith(('https://', 'http://')):
                link.replace_with(link.get_text() + ' (' + href + ')')
        return soup.get_text('\n', strip=True)
    # Preserve Markdown link evidence; remove markup rather than interpreting HTML.
    text = re.sub(r'!?\[([^\]]*)\]\((https?://[^\s)]+)\)', r'\1 (\2)', text or '')
    text = BeautifulSoup(text, 'html.parser').get_text()
    return re.sub(r'(?m)^\s{0,3}#{1,6}\s+', '', text).replace('**', '').replace('```', '').strip()


def plain_text(event):
    return event.meta.get('plain_text') or normalize_text(event.text, event.meta.get('content_format', 'mrkdwn' if event.source == 'slack' else 'markdown'))


AUTOMATION_NAMES = {'github', 'github-actions', 'dependabot', 'renovate', 'slackbot'}


def status_notice_only(event):
    """Exclude Slack notification-status memes, not substantive Braille text."""
    if event.source != 'slack':
        return False
    # Pasted status memes contain large, dense eight-dot graphics. Preserve
    # ordinary Braille text rather than stripping every Braille sentence.
    text = plain_text(event)
    dots = [ord(char) - 0x2800 for char in text if '\u2801' <= char <= '\u28ff']
    if dots and (len(dots) < 80 or sum(bool(dot & 0xc0) for dot in dots) < .75 * len(dots)):
        return False
    text = re.sub(r'[\u2800-\u28ff*_`~]', '', text).strip()
    notice = re.fullmatch(r'([^.!?;:\n]{1,80}) has (?:paused|resumed) their notifications[.!]?',
                          text, re.IGNORECASE)
    if not notice:
        return False
    # A short display name, not an announcement ending in the same phrase.
    words = notice[1].split()
    return bool(words and all(any(char.isalpha() for char in word) for word in words)
                and (len(words) == 1 or (len(words) <= 4 and all(word[0].isupper() for word in words))))


def automated(event):
    """Default exclusion for integrations and bot actors, before editorial/LLM."""
    if event.meta.get('editorial_override') is True:
        return False
    actor = event.author_name.strip().casefold()
    return bool(event.meta.get('is_bot') or event.meta.get('bot_id') or event.meta.get('app_id') or
                event.meta.get('subtype') == 'bot_message' or event.meta.get('automation') or
                actor in AUTOMATION_NAMES or actor.endswith('[bot]'))


def eligible(event):
    visibility = event.meta.get('visibility', 'public' if event.source == 'slack' else 'unknown')
    return (visibility == 'public' and not event.meta.get('deleted_at') and not automated(event) and
            not status_notice_only(event) and
            event.kind in ELIGIBLE_KINDS.get(event.source, set()) and
            event.meta.get('eligible', event.source == 'slack') and bool(plain_text(event).strip()))
