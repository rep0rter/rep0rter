"""SQLite event store shared by every collector, the reporter and the publishers.

Everything rep0rter observes is normalised into an *event*: something that
happened somewhere (a Slack message, later a commit, a HackMD edit, a toot),
by someone, at a time, with a permalink. Engagement counters are updated on
every collection run so items can become noteworthy after they were first seen.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS containers (
    id           TEXT PRIMARY KEY,   -- e.g. slack:C02G2SXKX
    source       TEXT NOT NULL,
    name         TEXT NOT NULL,
    topic        TEXT DEFAULT '',
    purpose      TEXT DEFAULT '',
    num_members  INTEGER DEFAULT 0,
    url          TEXT DEFAULT '',
    last_seen    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id             TEXT PRIMARY KEY, -- e.g. slack:C02G2SXKX:1789349869.691889
    source         TEXT NOT NULL,
    kind           TEXT NOT NULL,    -- message | thread_reply
    container_id   TEXT NOT NULL,
    author_id      TEXT DEFAULT '',
    author_name    TEXT DEFAULT '',
    text           TEXT DEFAULT '',
    html           TEXT DEFAULT '',
    url            TEXT DEFAULT '',
    ts             REAL NOT NULL,    -- unix seconds
    parent_id      TEXT,             -- thread root for replies
    reply_count    INTEGER DEFAULT 0,
    reaction_count INTEGER DEFAULT 0,
    meta           TEXT DEFAULT '{}',
    first_seen     REAL NOT NULL,
    last_seen      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS events_container_ts ON events (container_id, ts);
CREATE INDEX IF NOT EXISTS events_parent ON events (parent_id);

CREATE TABLE IF NOT EXISTS users (
    id    TEXT PRIMARY KEY,          -- e.g. slack:U123
    name  TEXT DEFAULT '',
    real_name TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS posts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      TEXT NOT NULL UNIQUE,
    published_at  REAL NOT NULL,
    score         REAL NOT NULL,
    headline      TEXT NOT NULL,
    summary       TEXT NOT NULL,
    translations TEXT DEFAULT '{}', -- language -> {headline, summary}
    reasons       TEXT DEFAULT '[]', -- json list of why it was selected
    delivery      TEXT DEFAULT '{}'  -- json: {"telegram": message_id, ...}
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  REAL NOT NULL,
    finished_at REAL,
    collected   INTEGER DEFAULT 0,
    posted      INTEGER DEFAULT 0,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Private login identities are separate from public source authors.
CREATE TABLE IF NOT EXISTS project_accounts (
    id TEXT PRIMARY KEY,
    google_subject TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS project_sessions (
    token_hash TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES project_accounts(id),
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS project_sessions_expiry ON project_sessions(expires_at);
CREATE TABLE IF NOT EXISTS project_submissions (
    event_id TEXT PRIMARY KEY REFERENCES events(id),
    account_id TEXT NOT NULL REFERENCES project_accounts(id),
    post_id INTEGER NOT NULL UNIQUE REFERENCES posts(id),
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS project_submissions_account ON project_submissions(account_id, created_at);
CREATE TABLE IF NOT EXISTS managed_projects (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES project_accounts(id),
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    author TEXT NOT NULL,
    language TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_url TEXT NOT NULL,
    headline_template TEXT NOT NULL,
    summary_template TEXT NOT NULL,
    interval_hours INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    enabled_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    next_run REAL NOT NULL,
    last_checked REAL,
    last_error TEXT NOT NULL DEFAULT '',
    last_post_id INTEGER REFERENCES posts(id)
);
CREATE INDEX IF NOT EXISTS managed_projects_owner ON managed_projects(account_id);
"""


@dataclass
class Container:
    id: str
    source: str
    name: str
    topic: str = ""
    purpose: str = ""
    num_members: int = 0
    url: str = ""


@dataclass
class Event:
    id: str
    source: str
    kind: str
    container_id: str
    ts: float
    author_id: str = ""
    author_name: str = ""
    text: str = ""
    html: str = ""
    url: str = ""
    parent_id: str | None = None
    reply_count: int = 0
    reaction_count: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Post:
    event_id: str
    published_at: float
    score: float
    headline: str
    summary: str
    reasons: list[str] = field(default_factory=list)
    delivery: dict[str, Any] = field(default_factory=dict)
    id: int | None = None
    translations: dict[str, dict[str, str]] = field(default_factory=dict)


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(posts)")}
        if "translations" not in columns:
            self.conn.execute("ALTER TABLE posts ADD COLUMN translations TEXT DEFAULT '{}'")
            self.conn.commit()
        from .policy import initialize
        initialize(self)
        if self.conn.execute('PRAGMA user_version').fetchone()[0] < 2:
            self.conn.execute('PRAGMA user_version=2')

    # ---- generic ---------------------------------------------------------
    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def get_kv(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_kv(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    # ---- containers / users ---------------------------------------------
    def upsert_container(self, c: Container, now: float | None = None) -> None:
        now = now or time.time()
        with self.conn:
            self.conn.execute(
                """INSERT INTO containers (id, source, name, topic, purpose, num_members, url, last_seen)
                   VALUES (:id, :source, :name, :topic, :purpose, :num_members, :url, :last_seen)
                   ON CONFLICT(id) DO UPDATE SET
                     name = excluded.name, topic = excluded.topic, purpose = excluded.purpose,
                     num_members = excluded.num_members, url = excluded.url, last_seen = excluded.last_seen""",
                {**asdict(c), "last_seen": now},
            )

    def get_container(self, container_id: str) -> Container | None:
        row = self.conn.execute("SELECT * FROM containers WHERE id = ?", (container_id,)).fetchone()
        if not row:
            return None
        return Container(**{k: row[k] for k in Container.__dataclass_fields__})

    def upsert_user(self, user_id: str, name: str, real_name: str) -> None:
        from .policy import user_allowed
        if not user_allowed(self, user_id):
            return
        with self.conn:
            self.conn.execute(
                """INSERT INTO users (id, name, real_name) VALUES (?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     name = CASE WHEN excluded.name != '' THEN excluded.name ELSE users.name END,
                     real_name = CASE WHEN excluded.real_name != '' THEN excluded.real_name ELSE users.real_name END""",
                (user_id, name, real_name),
            )

    def upsert_user_name(self, user_id: str, name: str) -> None:
        """Record a username seen in a mention without clobbering a known real_name."""
        from .policy import user_allowed
        if not user_allowed(self, user_id):
            return
        with self.conn:
            self.conn.execute(
                """INSERT INTO users (id, name, real_name) VALUES (?, ?, '')
                   ON CONFLICT(id) DO UPDATE SET name = CASE WHEN users.name = '' THEN excluded.name ELSE users.name END""",
                (user_id, name),
            )

    def user_names(self, source: str) -> dict[str, str]:
        """Map raw user id -> display name for one source (used to resolve mentions)."""
        prefix = source + ":"
        rows = self.conn.execute("SELECT id, name, real_name FROM users WHERE id LIKE ?", (prefix + "%",))
        from .policy import user_allowed
        return {r["id"][len(prefix):]: (r["real_name"] or r["name"]) for r in rows if user_allowed(self, r['id'])}

    # ---- events -----------------------------------------------------------
    def upsert_events(self, events: Iterable[Event], now: float | None = None) -> int:
        from .policy import event_allowed
        now = now or time.time()
        n = 0
        with self.conn:
            for e in events:
                if not e.source or not e.id or not e.container_id:
                    raise sqlite3.IntegrityError('Event requires source, id and container_id')
                if not event_allowed(self, e):
                    continue
                self.conn.execute(
                    """INSERT INTO events (id, source, kind, container_id, author_id, author_name, text, html, url,
                                           ts, parent_id, reply_count, reaction_count, meta, first_seen, last_seen)
                       VALUES (:id, :source, :kind, :container_id, :author_id, :author_name, :text, :html, :url,
                               :ts, :parent_id, :reply_count, :reaction_count, :meta, :now, :now)
                       ON CONFLICT(id) DO UPDATE SET
                         text = excluded.text, html = excluded.html, author_name = excluded.author_name,
                         reply_count = MAX(events.reply_count, excluded.reply_count),
                         reaction_count = MAX(events.reaction_count, excluded.reaction_count),
                         meta = excluded.meta, last_seen = excluded.last_seen""",
                    {**asdict(e), "meta": json.dumps(e.meta, ensure_ascii=False), "now": now},
                )
                n += 1
        return n

    def _row_to_event(self, row: sqlite3.Row) -> Event:
        meta=json.loads(row['meta'] or '{}')
        if not meta.get('avatar_url') and row['author_id']:
            known=self.conn.execute("""SELECT json_extract(meta,'$.avatar_url') AS avatar FROM events
                WHERE author_id=? AND json_extract(meta,'$.avatar_url') LIKE 'https://%'
                ORDER BY last_seen DESC LIMIT 1""",(row['author_id'],)).fetchone()
            if known:
                meta['avatar_url']=known['avatar']
        return Event(
            id=row["id"], source=row["source"], kind=row["kind"], container_id=row["container_id"],
            ts=row["ts"], author_id=row["author_id"], author_name=row["author_name"], text=row["text"],
            html=row["html"], url=row["url"], parent_id=row["parent_id"], reply_count=row["reply_count"],
            reaction_count=row["reaction_count"], meta=meta,
        )

    def get_event(self, event_id: str) -> Event | None:
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return self._row_to_event(row) if row else None

    def unposted_root_events(self, since_ts: float) -> list[Event]:
        """Top-level events newer than since_ts that have not been published."""
        rows = self.conn.execute(
            """SELECT e.* FROM events e
               LEFT JOIN posts p ON p.event_id = e.id
               WHERE e.kind IN ('message','release','issue','pull_request','status') AND e.ts >= ? AND p.id IS NULL
               ORDER BY e.ts DESC""",
            (since_ts,),
        ).fetchall()
        from .policy import event_allowed
        return [e for r in rows if event_allowed(self, e := self._row_to_event(r))]

    def thread_replies(self, root_id: str, limit: int = 20) -> list[Event]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE parent_id = ? ORDER BY ts ASC LIMIT ?", (root_id, limit)
        ).fetchall()
        from .policy import event_allowed
        return [e for r in rows if event_allowed(self, e := self._row_to_event(r))]

    def reply_count_seen(self, root_id: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM events WHERE parent_id = ?", (root_id,)).fetchone()[0]

    def event_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    # ---- posts ------------------------------------------------------------
    def add_post(self, post: Post) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO posts (event_id, published_at, score, headline, summary, reasons, delivery, translations)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (post.event_id, post.published_at, post.score, post.headline, post.summary,
                 json.dumps(post.reasons, ensure_ascii=False), json.dumps(post.delivery, ensure_ascii=False),
                 json.dumps(post.translations, ensure_ascii=False)),
            )
            return int(cur.lastrowid)

    def update_post_delivery(self, post_id: int, delivery: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute("UPDATE posts SET delivery = ? WHERE id = ?", (json.dumps(delivery), post_id))

    def update_post_translations(self, post: Post) -> bool:
        """Merge valid missing editions against current copy and withdrawal policy."""
        from .policy import event_allowed
        from .writer_contract import text_errors
        from .i18n import LANGUAGES

        def valid(entry, language):
            return isinstance(entry, dict) and not text_errors(entry.get('headline'), entry.get('summary'), language=language)

        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            row = self.conn.execute('SELECT * FROM posts WHERE id=?', (post.id,)).fetchone()
            event = self.get_event(post.event_id)
            if (not row or row['event_id'] != post.event_id or not event or not event_allowed(self, event)
                    or (row['headline'], row['summary']) != (post.headline, post.summary)):
                return False
            current = json.loads(row['translations'] or '{}')
            additions = {language: entry for language, entry in post.translations.items()
                         if language in LANGUAGES and valid(entry, language) and not valid(current.get(language), language)}
            if not additions:
                post.translations = current
                return False
            current.update(additions)
            self.conn.execute(
                "UPDATE posts SET translations = ? WHERE id = ?",
                (json.dumps(current, ensure_ascii=False), post.id),
            )
            post.translations = current
        return True

    def recent_posts(self, limit: int = 200) -> list[tuple[Post, Event, Container | None]]:
        from .policy import event_allowed
        rows = self.conn.execute(
            """SELECT p.id AS post_id, p.event_id, p.published_at, p.score,
                      p.headline, p.summary, p.reasons, p.delivery, p.translations, e.*
               FROM posts p JOIN events e ON e.id = p.event_id
               ORDER BY p.published_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        result = []
        for r in rows:
            post = Post(
                id=r["post_id"], event_id=r["event_id"], published_at=r["published_at"], score=r["score"],
                headline=r["headline"], summary=r["summary"], reasons=json.loads(r["reasons"] or "[]"),
                delivery=json.loads(r["delivery"] or "{}"),
                translations=json.loads(r["translations"] or "{}"),
            )
            event = self._row_to_event(r)
            if event_allowed(self, event):
                result.append((post, event, self.get_container(event.container_id)))
        return result

    def post_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]

    # ---- runs -------------------------------------------------------------
    def start_run(self) -> int:
        with self.conn:
            cur = self.conn.execute("INSERT INTO runs (started_at) VALUES (?)", (time.time(),))
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, collected: int, posted: int, error: str | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET finished_at = ?, collected = ?, posted = ?, error = ? WHERE id = ?",
                (time.time(), collected, posted, error, run_id),
            )

    def last_run(self) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
