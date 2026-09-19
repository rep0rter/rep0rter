"""Atomic owner submissions into the existing site/RSS publication store."""
from __future__ import annotations

import json
import time
from urllib.parse import urlsplit

from .i18n import LANGUAGES
from .policy import event_allowed
from .store import Event, Store


class SubmissionError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def validate(form) -> dict[str, str]:
    values = {key: form.get(key, '').strip() for key in ('title', 'description', 'url', 'language', 'author')}
    for key, label, maximum in (('title', 'Project name', 160), ('description', 'Description', 3000),
                                ('author', 'Public display name', 100)):
        if not values[key] or len(values[key]) > maximum:
            raise SubmissionError(f'{label} is required and must be at most {maximum} characters.')
    url = values['url']
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme in ('http', 'https') and parsed.hostname and not parsed.username
                 and not parsed.password and parsed.port != 0)
    except ValueError:
        valid = False
    if not valid or len(url) > 2048 or any(c.isspace() or ord(c) < 32 for c in url) or '\\' in url:
        raise SubmissionError('Enter a valid public project URL starting with https:// or http://.')
    if values['language'] not in LANGUAGES:
        raise SubmissionError('Choose the language your project description is written in.')
    if form.get('owner_confirmed') != 'yes':
        raise SubmissionError('Confirm that you own this project or are authorized to post it publicly.')
    return values


def publish(store: Store, account_id: str, submission_id: str, values: dict[str, str], *, automation=None) -> int:
    """Commit the source, owner and post together; retries cannot duplicate a post.

    Site generation happens after this transaction. If generation fails, the
    same submission can retry it, and the regular worker also rebuilds the site.
    No Telegram delivery is scheduled for owner submissions.
    """
    now = time.time()
    event = Event(id=f'project:{submission_id}', source='project', kind='project',
                  container_id='project:owners', ts=now, author_id=account_id,
                  author_name=values['author'], text=values['title'] + '\n\n' + values['description'],
                  url=values['url'], meta={'visibility': 'public', 'content_format': 'plain',
                                          'owner_submitted': True, 'language': values['language']})
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        if automation:
            current = store.conn.execute('SELECT * FROM managed_projects WHERE id=? AND account_id=?',
                                         (automation['id'], account_id)).fetchone()
            if not current or not current['enabled'] or current['updated_at'] != automation['updated_at']:
                raise SubmissionError('Automation was paused or changed during this check.', 409)
            event.meta.update(automated_project=True, managed_project_id=automation['id'])
        if not event_allowed(store, event):
            raise SubmissionError('This account or project is not allowed to publish.', 403)
        existing = store.conn.execute(
            'SELECT account_id, post_id FROM project_submissions WHERE event_id=?', (event.id,)
        ).fetchone()
        if existing:
            if existing['account_id'] != account_id:
                raise SubmissionError('This submission belongs to another account.', 403)
            return existing['post_id']
        count = store.conn.execute(
            'SELECT COUNT(*) FROM project_submissions WHERE account_id=? AND created_at>?',
            (account_id, now - 86400),
        ).fetchone()[0]
        if count >= 5:
            raise SubmissionError('You can publish up to five projects in 24 hours. Please try again later.', 429)
        store.conn.execute(
            "INSERT OR IGNORE INTO containers (id, source, name, last_seen) VALUES (?, ?, ?, ?)",
            (event.container_id, 'project', 'Owner-submitted projects', now),
        )
        store.conn.execute(
            '''INSERT INTO events (id, source, kind, container_id, author_id, author_name, text,
               url, ts, meta, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (event.id, event.source, event.kind, event.container_id, event.author_id, event.author_name,
             event.text, event.url, now, json.dumps(event.meta), now, now),
        )
        translation = {values['language']: {'headline': values['title'], 'summary': values['description']}}
        cursor = store.conn.execute(
            '''INSERT INTO posts (event_id, published_at, score, headline, summary, translations, reasons)
               VALUES (?, ?, 0, ?, ?, ?, ?)''',
            (event.id, now, values['title'], values['description'], json.dumps(translation, ensure_ascii=False),
             json.dumps(['Submitted directly by a project owner or authorized representative'])),
        )
        post_id = int(cursor.lastrowid)
        store.conn.execute('INSERT INTO project_submissions VALUES (?, ?, ?, ?)',
                           (event.id, account_id, post_id, now))
        if automation:
            store.conn.execute('UPDATE managed_projects SET last_post_id=? WHERE id=?', (post_id, automation['id']))
    return post_id
