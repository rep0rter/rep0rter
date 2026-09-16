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


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)

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
        with self.conn:
            self.conn.execute(
                """INSERT INTO users (id, name, real_name) VALUES (?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name = excluded.name, real_name = excluded.real_name""",
                (user_id, name, real_name),
            )

    def upsert_user_name(self, user_id: str, name: str) -> None:
        """Record a username seen in a mention without clobbering a known real_name."""
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
        return {r["id"][len(prefix):]: (r["real_name"] or r["name"]) for r in rows}

    # ---- events -----------------------------------------------------------
    def upsert_events(self, events: Iterable[Event], now: float | None = None) -> int:
        now = now or time.time()
        n = 0
        with self.conn:
            for e in events:
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
        return Event(
            id=row["id"], source=row["source"], kind=row["kind"], container_id=row["container_id"],
            ts=row["ts"], author_id=row["author_id"], author_name=row["author_name"], text=row["text"],
            html=row["html"], url=row["url"], parent_id=row["parent_id"], reply_count=row["reply_count"],
            reaction_count=row["reaction_count"], meta=json.loads(row["meta"] or "{}"),
        )

    def get_event(self, event_id: str) -> Event | None:
        row = self.conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        return self._row_to_event(row) if row else None

    def unposted_root_events(self, since_ts: float) -> list[Event]:
        """Top-level events newer than since_ts that have not been published."""
        rows = self.conn.execute(
            """SELECT e.* FROM events e
               LEFT JOIN posts p ON p.event_id = e.id
               WHERE e.kind = 'message' AND e.ts >= ? AND p.id IS NULL
               ORDER BY e.ts DESC""",
            (since_ts,),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def thread_replies(self, root_id: str, limit: int = 20) -> list[Event]:
        rows = self.conn.execute(
            "SELECT * FROM events WHERE parent_id = ? ORDER BY ts ASC LIMIT ?", (root_id, limit)
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def reply_count_seen(self, root_id: str) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM events WHERE parent_id = ?", (root_id,)).fetchone()[0]

    def event_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    # ---- posts ------------------------------------------------------------
    def add_post(self, post: Post) -> int:
        with self.conn:
            cur = self.conn.execute(
                """INSERT INTO posts (event_id, published_at, score, headline, summary, reasons, delivery)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (post.event_id, post.published_at, post.score, post.headline, post.summary,
                 json.dumps(post.reasons, ensure_ascii=False), json.dumps(post.delivery, ensure_ascii=False)),
            )
            return int(cur.lastrowid)

    def update_post_delivery(self, post_id: int, delivery: dict[str, Any]) -> None:
        with self.conn:
            self.conn.execute("UPDATE posts SET delivery = ? WHERE id = ?", (json.dumps(delivery), post_id))

    def recent_posts(self, limit: int = 200) -> list[tuple[Post, Event, Container | None]]:
        rows = self.conn.execute(
            """SELECT p.id AS post_id, p.*, e.* FROM posts p JOIN events e ON e.id = p.event_id
               ORDER BY p.published_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        result = []
        for r in rows:
            post = Post(
                id=r["post_id"], event_id=r["event_id"], published_at=r["published_at"], score=r["score"],
                headline=r["headline"], summary=r["summary"], reasons=json.loads(r["reasons"] or "[]"),
                delivery=json.loads(r["delivery"] or "{}"),
            )
            event = self._row_to_event(r)
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
