from dataclasses import replace

import pytest

from rep0rter import reporter
from rep0rter.config import Config
from rep0rter.store import Event, Store
from rep0rter.writer_contract import WriteResult, evidence_bundle


NOW = 1789736400.0


def test_held_candidates_do_not_starve_other_stories(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path)
    cfg.editorial_mode = "active"
    cfg.max_items_per_run = 1
    clock = [NOW]
    monkeypatch.setattr(reporter.time, "time", lambda: clock[0])
    attempted = []

    def write(candidate, llm, now):
        attempted.append(candidate.event.id)
        held = candidate.event.id != "publishable"
        return WriteResult(headline="公開資料工作坊", summary="工作坊開放報名",
                           needs_review=held, review_reason="ambiguous source" if held else "",
                           bundle=evidence_bundle(candidate, now))

    monkeypatch.setattr(reporter, "write", write)
    with Store(cfg.db_path) as store:
        events = [Event(key, "slack", "message", "slack:C", NOW - index,
                        text=f"9/{19 + index} {subject}工作坊開放報名，歡迎參與探索與協作 https://example.test/{key}")
                  for index, (key, subject) in enumerate([
                      ("held-first", "公民科技"), ("held-second", "社區園藝"), ("publishable", "天文觀測")])]
        store.upsert_events(events)
        results = []
        for _ in range(3):
            results.extend(reporter.draft_posts(store, cfg, use_llm=False))
            clock[0] += 3600
        assert attempted == ["held-first", "held-second", "publishable"]
        assert [post.event_id for _, post in results] == ["publishable"]
        selected = store.conn.execute("SELECT event_id FROM editorial_decisions WHERE selected=1 ORDER BY id").fetchall()
        assert [row[0] for row in selected] == attempted


def test_unchanged_held_stories_retry_in_rotation_with_same_budget(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path)
    cfg.editorial_mode = "active"
    cfg.max_items_per_run = 1
    clock = [NOW]
    monkeypatch.setattr(reporter.time, "time", lambda: clock[0])
    attempted = []

    def write(candidate, llm, now):
        attempted.append(candidate.event.id)
        return WriteResult(needs_review=True, bundle=evidence_bundle(candidate, now))

    monkeypatch.setattr(reporter, "write", write)
    with Store(cfg.db_path) as store:
        store.upsert_events([
            Event("a", "slack", "message", "slack:C", NOW,
                  text="9/19 公民科技工作坊開放報名，歡迎一起參與 https://example.test/a"),
            Event("b", "slack", "message", "slack:C", NOW - 1,
                  text="9/20 社區園藝工作坊開放報名，歡迎一起參與 https://example.test/b"),
        ])
        for run in range(4):
            assert reporter.draft_posts(store, cfg, use_llm=False) == []
            assert len(attempted) == run + 1
            clock[0] += 3600
        assert attempted == ["a", "b", "a", "b"]


@pytest.mark.parametrize("changed_evidence", ["root", "reply"])
def test_updated_evidence_restores_held_story_priority(tmp_path, monkeypatch, changed_evidence):
    cfg = Config(data_dir=tmp_path)
    cfg.editorial_mode = "active"
    cfg.max_items_per_run = 1
    clock = [NOW]
    monkeypatch.setattr(reporter.time, "time", lambda: clock[0])
    attempted = []

    def write(candidate, llm, now):
        attempted.append(candidate.event.id)
        return WriteResult(needs_review=True, bundle=evidence_bundle(candidate, now))

    monkeypatch.setattr(reporter, "write", write)
    with Store(cfg.db_path) as store:
        root = Event("a", "slack", "message", "slack:C", NOW,
                     text="9/19 公民科技工作坊開放報名，歡迎一起參與 https://example.test/a")
        store.upsert_events([root, Event("b", "slack", "message", "slack:C", NOW - 1,
                              text="9/20 社區園藝工作坊開放報名，歡迎一起參與 https://example.test/b")])
        assert reporter.draft_posts(store, cfg, use_llm=False) == []
        clock[0] += 3600
        if changed_evidence == "root":
            store.upsert_events([replace(root, text=root.text + "，確定於市民會館舉行")])
        else:
            store.upsert_events([Event("reply", "slack", "thread_reply", "slack:C", NOW + 1,
                                       parent_id=root.id, text="補充：工作坊確定於市民會館舉行")])
        assert reporter.draft_posts(store, cfg, use_llm=False) == []
        assert attempted == ["a", "a"]
