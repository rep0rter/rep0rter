"""Durable, conservative Threads publication outbox.

The app can publish to Threads without changing the Telegram delivery lifecycle.
Each post gets one Threads job and a dedicated lease so duplicate sends cannot
accidentally stack during a retry or crash.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any

from .config import Config
from .policy import event_allowed
from .publishers import threads
from .reporter import Candidate
from .store import Post, Store

log = logging.getLogger(__name__)
LEASE_SECONDS = 300


def _normalize_thread_id(value) -> str:
    if isinstance(value, dict):
        if 'id' not in value:
            raise ValueError('Threads publish response missing id')
        return str(value['id'])
    if value is None:
        raise ValueError('Threads publish response missing id')
    return str(value)


def publish_post(cfg: Config, target: str, text: str) -> str:
    """Compatibility wrapper used by tests and the queued publish path."""
    return _normalize_thread_id(threads.publish_post(cfg, target, text))


SCHEMA = """
CREATE TABLE IF NOT EXISTS threads_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL UNIQUE REFERENCES posts(id),
    target TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'prepared'
        CHECK(status IN ('prepared','sending','sent','failed','unknown','cancelled')),
    payload TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT,
    thread_id TEXT,
    permalink TEXT,
    lease_token TEXT,
    lease_until REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt REAL,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


class ThreadsOutbox:
    def __init__(self, store: Store):
        self.store = store
        self.conn = store.conn
        self.conn.executescript(SCHEMA)
        columns = {row['name'] for row in self.conn.execute('PRAGMA table_info(threads_jobs)')}
        if 'permalink' not in columns:
            self.conn.execute('ALTER TABLE threads_jobs ADD COLUMN permalink TEXT')

    def recover(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self.conn:
            self.conn.execute(
                """UPDATE threads_jobs SET status='unknown', error='send lease expired; reconcile before retry',
                lease_token=NULL, lease_until=NULL, updated_at=?
                WHERE status='sending' AND lease_until < ?""",
                (now, now),
            )

    def claim(self, target: str, now: float | None = None):
        now = time.time() if now is None else now
        token = uuid.uuid4().hex
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            row = self.conn.execute(
                """SELECT * FROM threads_jobs WHERE
                (status='prepared' OR (status='failed' AND next_attempt IS NOT NULL AND next_attempt <= ?))
                AND target IN ('', ?) ORDER BY id LIMIT 1""",
                (now, target),
            ).fetchone()
            if row is None:
                return None
            self.conn.execute(
                """UPDATE threads_jobs SET status='sending', target=?, lease_token=?, lease_until=?,
                attempts=attempts+1, updated_at=? WHERE id=?""",
                (target, token, now + LEASE_SECONDS, now, row['id']),
            )
        return self.conn.execute('SELECT * FROM threads_jobs WHERE id=?', (row['id'],)).fetchone()

    def payload(self, job, text: str) -> None:
        data = json.dumps({'text': text}, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(data.encode()).hexdigest()
        with self.conn:
            cursor = self.conn.execute(
                """UPDATE threads_jobs SET payload=?, content_hash=?, updated_at=?
                WHERE id=? AND status='sending' AND lease_token=?""",
                (data, digest, time.time(), job['id'], job['lease_token']),
            )
            if cursor.rowcount != 1:
                raise RuntimeError('Threads lease lost before send')

    def failed(self, job, error: str, *, unknown: bool = False, retry_after: int | None = None) -> None:
        now = time.time()
        with self.conn:
            self.conn.execute(
                """UPDATE threads_jobs SET status=?, error=?, next_attempt=?, lease_token=NULL,
                lease_until=NULL, updated_at=? WHERE id=? AND status='sending' AND lease_token=?""",
                ('unknown' if unknown else 'failed', error, now + max(1, retry_after) if retry_after is not None else None,
                 now, job['id'], job['lease_token']),
            )

    def sent(self, job, thread_id: str, permalink: str) -> None:
        with self.conn:
            cursor = self.conn.execute(
                """UPDATE threads_jobs SET status='sent', thread_id=?, permalink=?, error=NULL, next_attempt=NULL,
                lease_token=NULL, lease_until=NULL, updated_at=? WHERE id=?
                AND status='sending' AND lease_token=?""",
                (thread_id, permalink, time.time(), job['id'], job['lease_token']),
            )
            if cursor.rowcount != 1:
                raise RuntimeError('Threads lease lost after send; reconcile manually')
            row = self.conn.execute('SELECT delivery FROM posts WHERE id=?', (job['post_id'],)).fetchone()
            delivery = json.loads(row['delivery'] or '{}')
            delivery['threads'] = {'user_id': job['target'], 'thread_id': thread_id,
                                   'permalink': permalink, 'outbox_id': job['id']}
            self.conn.execute('UPDATE posts SET delivery=? WHERE id=?', (json.dumps(delivery), job['post_id']))

    def reconcile(self, job_id: int, *, thread_id: str | None = None,
                  permalink: str | None = None, retry: bool = False) -> None:
        if (thread_id is not None) == retry:
            raise ValueError('Choose exactly one of observed thread_id or explicit retry')
        if thread_id is not None and not permalink:
            raise ValueError('Verified permalink is required with thread_id')
        row = self.conn.execute('SELECT * FROM threads_jobs WHERE id=?', (job_id,)).fetchone()
        if row is None or row['status'] not in ('unknown', 'failed'):
            raise ValueError('Only unknown/failed Threads jobs can be reconciled')
        with self.conn:
            token = uuid.uuid4().hex
            cursor = self.conn.execute(
                """UPDATE threads_jobs SET status=?, lease_token=?, lease_until=?, error=NULL,
                next_attempt=NULL, updated_at=? WHERE id=? AND status IN ('unknown','failed')""",
                ('prepared' if retry else 'sending', None if retry else token,
                 None if retry else time.time() + LEASE_SECONDS, time.time(), job_id),
            )
            if cursor.rowcount != 1:
                raise RuntimeError('Threads job changed while reconciling')
        if thread_id is not None:
            row = self.conn.execute('SELECT * FROM threads_jobs WHERE id=?', (job_id,)).fetchone()
            self.sent(row, thread_id, permalink)


def enqueue_missing(cfg: Config, store: Store, post_ids: list[int] | None = None) -> int:
    """Queue saved, eligible site posts once; an existing job is never reset."""
    ThreadsOutbox(store)
    if not cfg.threads_user_id:
        return 0
    now = time.time()
    added = 0
    rows = store.conn.execute('SELECT id,event_id,delivery FROM posts ORDER BY id').fetchall()
    selected = set(post_ids) if post_ids is not None else None
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        for row in rows:
            if selected is not None and row['id'] not in selected:
                continue
            event = store.get_event(row['event_id'])
            if event is None or not event_allowed(store, event):
                continue
            delivery = json.loads(row['delivery'] or '{}')
            if delivery.get('threads'):
                continue
            cursor = store.conn.execute(
                'INSERT INTO threads_jobs(post_id,target,created_at,updated_at) '
                'VALUES (?,?,?,?) ON CONFLICT(post_id) DO NOTHING',
                (row['id'], cfg.threads_user_id, now, now))
            added += cursor.rowcount
    return added


def prepare_posts(cfg: Config, store: Store, items: list[tuple[Candidate, Post]]) -> None:
    outbox = ThreadsOutbox(store)
    now = time.time()
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        for candidate, post in items:
            if not event_allowed(store, candidate.event):
                continue
            values = {**post.__dict__}
            cursor = store.conn.execute(
                """INSERT INTO posts
                (event_id,published_at,score,headline,summary,reasons,delivery,translations)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING""",
                (post.event_id, post.published_at, post.score, post.headline, post.summary,
                 json.dumps(values['reasons'], ensure_ascii=False), json.dumps(values['delivery'], ensure_ascii=False),
                 json.dumps(values['translations'], ensure_ascii=False)),
            )
            row = store.conn.execute('SELECT id FROM posts WHERE event_id=?', (post.event_id,)).fetchone()
            if row is None:
                continue
            post.id = int(row['id'])
            if cursor.rowcount:
                store.conn.execute(
                    """INSERT INTO threads_jobs(post_id,target,created_at,updated_at)
                    VALUES(?,?,?,?) ON CONFLICT(post_id) DO NOTHING""",
                    (post.id, cfg.threads_user_id or '', now, now),
                )


def _job_item(store: Store, post_id: int):
    row = store.conn.execute('SELECT * FROM posts WHERE id=?', (post_id,)).fetchone()
    if row is None:
        raise ValueError('Threads post no longer exists')
    post = Post(**{key: row[key] for key in ('id', 'event_id', 'published_at', 'score', 'headline', 'summary')},
                translations=json.loads(row['translations'] or '{}'))
    event = store.get_event(post.event_id)
    if event is None:
        raise ValueError('Threads event no longer exists')
    return Candidate(event=event, container=store.get_container(event.container_id), score=post.score), post


def deliver_pending(cfg: Config, store: Store) -> dict[str, str]:
    outbox = ThreadsOutbox(store)
    outbox.recover()
    if not (cfg.threads_enabled and cfg.threads_user_id and cfg.threads_access_token):
        log.warning('Threads disabled or unconfigured; delivery remains pending')
        return {}
    account = threads.get_account(cfg)
    if str(account.get('id')) != cfg.threads_user_id:
        raise ValueError('Threads token belongs to a different user ID')
    delivered: dict[str, str] = {}
    for _ in range(max(0, cfg.threads_batch_size)):
        job = outbox.claim(cfg.threads_user_id)
        if job is None:
            break
        try:
            candidate, post = _job_item(store, job['post_id'])
            if not event_allowed(store, candidate.event):
                outbox.failed(job, 'Excluded or withdrawn before delivery')
                continue
            text = threads.format_text(cfg, post)
            outbox.payload(job, text)
            job = outbox.conn.execute('SELECT * FROM threads_jobs WHERE id=?', (job['id'],)).fetchone()
        except Exception as exc:
            outbox.failed(job, f'Preparation failed: {type(exc).__name__}')
            log.error('Threads job %s preparation failed (%s)', job['id'], type(exc).__name__)
            continue
        try:
            latest = store.get_event(candidate.event.id)
            if latest is None or not event_allowed(store, latest):
                outbox.failed(job, 'Excluded or withdrawn before transport')
                continue
            thread_id = _normalize_thread_id(publish_post(cfg, job['target'], json.loads(job['payload'])['text']))
        except threads.ThreadsRejected as exc:
            outbox.failed(job, str(exc), retry_after=(exc.retry_after or 60) if exc.status == 429 else None)
            log.warning('Threads job %s explicitly rejected (%s)', job['id'], exc.status)
            if exc.status == 429:
                break
            continue
        except Exception as exc:
            outbox.failed(job, f'Ambiguous send: {type(exc).__name__}', unknown=True)
            log.error('Threads job %s has unknown delivery; manual reconciliation required', job['id'])
            continue
        try:
            remote = threads.get_post(cfg, thread_id)
            permalink = remote.get('permalink')
            if (str(remote.get('id')) != thread_id or
                    remote.get('text') != json.loads(job['payload'])['text'] or
                    not permalink or not threads._valid_url(permalink)):
                raise RuntimeError('Threads post verification is incomplete')
            outbox.sent(job, thread_id, permalink)
        except Exception:
            try:
                outbox.failed(job, f'Remote send succeeded (thread {thread_id}); local commit failed', unknown=True)
            except Exception:
                pass
            log.error('Threads job %s sent as thread %s but save failed; reconcile before retry', job['id'], thread_id)
            raise RuntimeError('Threads sent but delivery commit failed; manual reconciliation required') from None
        delivered[candidate.event.id] = thread_id
    return delivered
