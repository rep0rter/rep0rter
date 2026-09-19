"""Durable, conservative per-post Telegram outbox.

A successful remote send cannot be committed atomically with SQLite. Expired
leases and ambiguous responses require explicit operator reconciliation.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict

from .config import Config
from .publishers import telegram
from .publishers.cards import CardRenderer
from .reporter import Candidate
from .store import Post, Store

log = logging.getLogger(__name__)
LEASE_SECONDS = 300
SCHEMA = """
CREATE TABLE IF NOT EXISTS delivery_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL REFERENCES posts(id),
    publisher TEXT NOT NULL DEFAULT 'telegram',
    target TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'prepared'
        CHECK(status IN ('prepared','sending','sent','failed','unknown')),
    payload TEXT NOT NULL DEFAULT '{}',
    content_hash TEXT,
    message_id INTEGER,
    lease_token TEXT,
    lease_until REAL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt REAL,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE(post_id, publisher)
);
"""


class DeliveryOutbox:
    def __init__(self, store: Store):
        self.store = store
        self.conn = store.conn
        self.conn.executescript(SCHEMA)

    def recover(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self.conn:
            self.conn.execute("""UPDATE delivery_jobs SET status='unknown', error='send lease expired; reconcile before retry',
                lease_token=NULL, lease_until=NULL, updated_at=? WHERE status='sending' AND lease_until < ?""", (now, now))

    def claim(self, target: str, now: float | None = None):
        now = time.time() if now is None else now
        token = uuid.uuid4().hex
        with self.conn:
            # BEGIN IMMEDIATE makes selection and claim atomic across processes.
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute("""SELECT * FROM delivery_jobs WHERE
                (status='prepared' OR (status='failed' AND next_attempt IS NOT NULL AND next_attempt<=?))
                AND target IN ('', ?) ORDER BY id LIMIT 1""", (now, target)).fetchone()
            if row is None:
                return None
            self.conn.execute("""UPDATE delivery_jobs SET status='sending', target=?, lease_token=?, lease_until=?,
                attempts=attempts+1, updated_at=? WHERE id=?""", (target, token, now + LEASE_SECONDS, now, row['id']))
        return self.conn.execute("SELECT * FROM delivery_jobs WHERE id=?", (row['id'],)).fetchone()

    def payload(self, job, caption: str, photo: str) -> None:
        data = json.dumps({"caption": caption, "photo": photo}, ensure_ascii=False, sort_keys=True)
        from pathlib import Path
        digest = hashlib.sha256(data.encode() + Path(photo).read_bytes()).hexdigest()
        with self.conn:
            cursor = self.conn.execute("""UPDATE delivery_jobs SET payload=?,content_hash=?,updated_at=?
                WHERE id=? AND status='sending' AND lease_token=?""",
                (data, digest, time.time(), job['id'], job['lease_token']))
            if cursor.rowcount != 1:
                raise RuntimeError("Delivery lease lost before send")

    def failed(self, job, error: str, *, unknown: bool = False, retry_after: int | None = None) -> None:
        now = time.time()
        with self.conn:
            self.conn.execute("""UPDATE delivery_jobs SET status=?,error=?,next_attempt=?,lease_token=NULL,
                lease_until=NULL,updated_at=? WHERE id=? AND status='sending' AND lease_token=?""",
                ('unknown' if unknown else 'failed', error, now + max(1, retry_after) if retry_after is not None else None,
                 now, job['id'], job['lease_token']))

    def sent(self, job, message_id: int) -> None:
        with self.conn:
            cursor = self.conn.execute("""UPDATE delivery_jobs SET status='sent',message_id=?,error=NULL,next_attempt=NULL,
                lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?
                AND (status='sending' OR (status='failed' AND error='withdrawn')) AND lease_token=?""",
                (message_id, time.time(), job['id'], job['lease_token']))
            if cursor.rowcount != 1:
                raise RuntimeError("Delivery lease lost after send; reconcile Telegram")
            row = self.conn.execute("SELECT delivery FROM posts WHERE id=?", (job['post_id'],)).fetchone()
            delivery = json.loads(row['delivery'] or '{}')
            delivery['telegram'] = {'chat_id': job['target'], 'message_id': message_id, 'outbox_id': job['id']}
            self.conn.execute("UPDATE posts SET delivery=? WHERE id=?", (json.dumps(delivery), job['post_id']))

    def reconcile(self, job_id: int, *, message_id: int | None = None, retry: bool = False) -> None:
        """Operator only: record observed delivery OR confirm absent and requeue."""
        if (message_id is not None) == retry:
            raise ValueError("Choose exactly one of observed message_id or explicit retry")
        row = self.conn.execute("SELECT * FROM delivery_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or row['status'] not in ('unknown', 'failed'):
            raise ValueError("Only unknown/failed jobs can be reconciled")
        with self.conn:
            token = uuid.uuid4().hex
            cursor = self.conn.execute("""UPDATE delivery_jobs SET status=?,lease_token=?,lease_until=?,error=NULL,
                next_attempt=NULL,updated_at=? WHERE id=? AND status IN ('unknown','failed')""",
                ('prepared' if retry else 'sending', None if retry else token,
                 None if retry else time.time() + LEASE_SECONDS, time.time(), job_id))
            if cursor.rowcount != 1:
                raise RuntimeError("Delivery changed while reconciling")
        if message_id is not None:
            row = self.conn.execute("SELECT * FROM delivery_jobs WHERE id=?", (job_id,)).fetchone()
            self.sent(row, message_id)


def prepare_posts(cfg: Config, store: Store, items: list[tuple[Candidate, Post]]) -> None:
    """Persist site posts and Telegram jobs together, before any network send."""
    outbox = DeliveryOutbox(store)
    from . import stories
    from .policy import event_allowed
    stories.ensure(store)
    now = time.time()
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        for candidate, post in items:
            if not event_allowed(store, candidate.event):
                continue
            meta = candidate.event.meta
            if meta.get('story_id') and store.conn.execute(
                'SELECT 1 FROM story_posts WHERE story_id=? AND (revision=? OR fingerprint=?)',
                (meta['story_id'], meta['story_revision'], meta['story_fingerprint'])).fetchone():
                continue
            if candidate.event.kind == 'story_update':
                event_values=asdict(candidate.event)
                event_values['meta']=json.dumps(event_values['meta'],ensure_ascii=False)
                event_values['now']=now
                store.conn.execute('''INSERT OR IGNORE INTO events
                  (id,source,kind,container_id,author_id,author_name,text,html,url,ts,parent_id,
                   reply_count,reaction_count,meta,first_seen,last_seen)
                  VALUES(:id,:source,:kind,:container_id,:author_id,:author_name,:text,:html,:url,:ts,:parent_id,
                   :reply_count,:reaction_count,:meta,:now,:now)''',event_values)
            values = asdict(post)
            cursor = store.conn.execute("""INSERT INTO posts
                (event_id,published_at,score,headline,summary,reasons,delivery,translations)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING""",
                (post.event_id, post.published_at, post.score, post.headline, post.summary,
                 json.dumps(values['reasons'], ensure_ascii=False), json.dumps(values['delivery']),
                 json.dumps(values['translations'], ensure_ascii=False)))
            row = store.conn.execute("SELECT id FROM posts WHERE event_id=?", (post.event_id,)).fetchone()
            post.id = row['id']
            if cursor.rowcount:
                stories.reserve(store, candidate, post.id)
                store.conn.execute("""INSERT INTO delivery_jobs(post_id,target,created_at,updated_at)
                    VALUES(?,?,?,?)""", (post.id, cfg.telegram_target or '', now, now))


def _job_item(store: Store, post_id: int):
    row = store.conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
    if row is None:
        raise ValueError("Delivery post no longer exists")
    post = Post(**{key: row[key] for key in ('id', 'event_id', 'published_at', 'score', 'headline', 'summary')},
                translations=json.loads(row['translations'] or '{}'))
    event = store.get_event(post.event_id)
    if event is None:
        raise ValueError("Delivery event no longer exists")
    return Candidate(event=event, container=store.get_container(event.container_id), score=post.score), post


def deliver_pending(cfg: Config, store: Store) -> dict[int, int]:
    """Attempt due jobs once; completed/ambiguous jobs are never auto-replayed."""
    outbox = DeliveryOutbox(store)
    outbox.recover()
    if not cfg.telegram_bot_token or not cfg.telegram_target:
        log.warning("Telegram unconfigured; delivery remains pending%s",
                    ' (test chat is required in test mode)' if cfg.telegram_use_test_chat else '')
        return {}
    delivered = {}
    with CardRenderer(cfg) as renderer:
        while (job := outbox.claim(cfg.telegram_target)) is not None:
            try:
                candidate, post = _job_item(store, job['post_id'])
                from .policy import event_allowed
                if not event_allowed(store, candidate.event):
                    outbox.failed(job, 'Excluded or withdrawn before delivery')
                    continue
                caption = telegram.format_caption(cfg, candidate, post)
                photo = (renderer.render_report(candidate.event, candidate.container, post)
                         if cfg.telegram_language == 'en' else
                         renderer.render(candidate.event, candidate.container, store.user_names(candidate.event.source)))
                outbox.payload(job, caption, str(photo))
            except telegram.TranslationPending:
                outbox.failed(job, 'Waiting for the configured translation', retry_after=3600)
                continue
            except Exception as exc:
                outbox.failed(job, f"Preparation failed: {type(exc).__name__}")
                log.error("Telegram job %s preparation failed (%s)", job['id'], type(exc).__name__)
                continue
            try:
                latest=store.get_event(candidate.event.id)
                if latest is None or not event_allowed(store,latest):
                    outbox.failed(job,'Excluded or withdrawn before transport')
                    continue
                message_id = telegram.send_photo(cfg, job['target'], photo, caption)
            except telegram.TelegramRejected as exc:
                outbox.failed(job, str(exc), retry_after=(exc.retry_after or 60) if exc.status == 429 else None)
                log.warning("Telegram job %s explicitly rejected (%s)", job['id'], exc.status)
                if exc.status == 429:
                    break
                continue
            except Exception as exc:
                outbox.failed(job, f"Ambiguous send: {type(exc).__name__}", unknown=True)
                log.error("Telegram job %s has unknown delivery; manual reconciliation required", job['id'])
                continue
            try:
                outbox.sent(job, message_id)
            except Exception:
                # If storage is unavailable this also may fail; the expired lease
                # still becomes unknown on the next run, never an automatic retry.
                try:
                    outbox.failed(job, f"Remote send succeeded (message {message_id}); local commit failed", unknown=True)
                except Exception:
                    pass
                log.error("Telegram job %s sent as message %s but save failed; reconcile before retry", job['id'], message_id)
                raise RuntimeError("Telegram sent but delivery commit failed; manual reconciliation required") from None
            delivered[job['post_id']] = message_id
            latest=store.get_event(candidate.event.id)
            if latest is None or not event_allowed(store,latest):
                from .retractions import queue,plan_remote
                queue(store,plan_remote(store,cfg,[job['post_id']]))
    return delivered
