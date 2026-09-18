import sqlite3
from dataclasses import replace

import pytest

from rep0rter.store import Container, Event, Post, Store


def event(event_id="slack:C_TEST:123.456"):
    return Event(
        id=event_id, source="slack", kind="message", container_id="slack:C_TEST",
        ts=123.456, author_id="slack:U_TEST", author_name="Test author",
        text="A synthetic announcement", url="https://example.test/message",
        meta={"synthetic": True},
    )


def test_published_event_retains_its_source_id(tmp_path):
    original = event()
    with Store(tmp_path / "test.sqlite") as store:
        container = Container(id=original.container_id, source="slack", name="Test")
        store.upsert_container(container)
        store.upsert_events([original], now=100)
        post_id = store.add_post(Post(
            event_id=original.id, published_at=200, score=10, headline="Title", summary="Body",
            translations={"ja": {"headline": "見出し", "summary": "本文"}},
        ))
        post, loaded, loaded_container = store.recent_posts()[0]
        assert loaded == original == store.get_event(original.id)
        assert post.id == post_id
        assert isinstance(post.id, int)
        assert post.event_id == loaded.id
        assert post.translations["ja"]["headline"] == "見出し"
        assert loaded_container == container


def test_event_update_retains_first_seen(tmp_path):
    original = event()
    with Store(tmp_path / "test.sqlite") as store:
        store.upsert_events([original], now=100)
        store.upsert_events([replace(original, text="Updated")], now=200)
        row = store.conn.execute("SELECT * FROM events WHERE id = ?", (original.id,)).fetchone()
        assert (row["first_seen"], row["last_seen"], row["text"]) == (100, 200, "Updated")


def test_event_batch_rolls_back_when_one_event_is_invalid(tmp_path):
    with Store(tmp_path / "test.sqlite") as store:
        with pytest.raises(sqlite3.IntegrityError):
            store.upsert_events([event(), replace(event("invalid"), source=None)], now=100)
        assert store.event_count() == 0


def test_old_posts_survive_translation_schema_migration(tmp_path):
    path = tmp_path / "old.sqlite"
    # This is the pre-multilingual posts schema, populated entirely with synthetic data.
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
            published_at REAL NOT NULL, score REAL NOT NULL, headline TEXT NOT NULL,
            summary TEXT NOT NULL, reasons TEXT DEFAULT '[]', delivery TEXT DEFAULT '{}'
        )""")
        conn.execute(
            "INSERT INTO posts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (42, event().id, 200, 10, "Existing title", "Existing summary", '["reason"]', '{"telegram":123}'),
        )
    with Store(path) as store:
        store.upsert_events([event()], now=100)
        post, loaded, _ = store.recent_posts()[0]
        assert (post.id, post.event_id, loaded.id) == (42, event().id, event().id)
        assert (post.headline, post.summary) == ("Existing title", "Existing summary")
        assert post.reasons == ["reason"]
        assert post.delivery == {"telegram": 123}
        assert post.translations == {}
    # Reopening is idempotent and preserves the legacy post ID used by old RSS GUIDs.
    with Store(path) as store:
        assert store.post_count() == 1
        assert store.recent_posts()[0][0].id == 42


def test_posts_are_unique_per_event(tmp_path):
    with Store(tmp_path / "test.sqlite") as store:
        post = Post(event_id=event().id, published_at=200, score=10, headline="Title", summary="Body")
        store.add_post(post)
        with pytest.raises(sqlite3.IntegrityError):
            store.add_post(post)
        assert store.post_count() == 1


def test_historical_events_resolve_latest_public_avatar_by_stable_author(tmp_path):
    with Store(tmp_path/'avatar.sqlite') as store:
        old=event('old-photo')
        recent=replace(old,id='recent-photo',meta={'avatar_url':'https://public.example.test/portrait.png'})
        store.upsert_events([old],now=100)
        store.upsert_events([recent],now=200)
        assert store.get_event(old.id).meta['avatar_url']=='https://public.example.test/portrait.png'
