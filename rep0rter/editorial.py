"""Versioned, replayable editorial eligibility and bounded interaction scoring."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import json
import math
import re
from zoneinfo import ZoneInfo

from .slack_text import to_plain

SCORE_VERSION = "eligibility-v2"
TZ = ZoneInfo("Asia/Taipei")
URL = re.compile(r"https?://[^\s<>\]\)]+")
INTENT = re.compile(
    r"黑客松|大松|小松|基礎松|工作坊|分享會|講座|論壇|聚會|座談會|餐敘|報名|徵求|徵件|招募|志工|發布|發佈|上線|出版|開源|新功能|資料集|清單|通報系統|"
    r"ワークショップ|ハッカソン|公開|リリース|募集|워크숍|해커톤|출시|공개|모집|"
    r"\b(?:releases?|released|launch(?:ed)?|workshop|hackathon|call for proposals|open source|dataset|registration|recruiting)\b", re.I,
)
DATE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[月/]\d{1,2}|週[一二三四五六日天]|今天|今晚|明天|後天|today|tonight|tomorrow|本日|明日|오늘|내일", re.I)
NEGATED = re.compile(r"(?:尚未|還沒|未|不會|沒有|不打算|並非|是否|能否).{0,5}(?:發布|發佈|上線|出版|開源|招募)|\b(?:not|never|hasn't|haven't|isn't)\s+(?:yet\s+)?(?:released?|launched?|open)|未公開|未リリース|출시\s*전", re.I)
CANCEL = re.compile(r"取消|延期|暫停|延後|中止|cancel(?:led|ed)?|postponed|中止|延期|취소|연기", re.I)


@dataclass
class Decision:
    eligible: bool
    score: float
    reasons: list[str]
    components: dict[str, float]
    score_version: str = SCORE_VERSION
    details: dict = field(default_factory=dict)


def normalized_body(text: str) -> str:
    text = re.sub(r"<@[^>]+>|<#[^>]+>|:[a-zA-Z0-9_+-]+:", "", text)
    text = URL.sub("", to_plain(text))
    text = re.sub(r"@\S+", "", text)
    return re.sub(r"[\s*_~`()（）<>]", "", text)


def automation_event(event) -> bool:
    from .sources import automated
    return automated(event)


def evaluate(event, container, cfg, now: float, replies_seen: int = 0) -> Decision:
    from .sources import plain_text
    plain = plain_text(event)
    body = normalized_body(plain)
    without_urls = URL.sub("", plain)
    hits = list(dict.fromkeys(INTENT.findall(without_urls)))
    links = URL.findall(plain)
    # GitHub adapters admit only public human-authored substantial releases,
    # collaboration calls, and civic outcome PRs. They have no Slack baseline.
    if event.source == "github" and event.meta.get("eligible") is True and event.kind in {"release", "issue", "pull_request"}:
        hits = hits or ["source_verified:" + event.kind]
        if event.url.startswith(("https://", "http://")):
            links = list(dict.fromkeys([*links, event.url]))
    replies = max(0, event.reply_count, replies_seen)
    reactions = max(0, event.reaction_count)
    parts = {"topic": 4.0 if hits else 0.0, "date_or_link": 2.0 if DATE.search(body) or links else 0.0,
             "substance": 1.0 if len(body) >= 80 else 0.0,
             "interaction": round(min(2, .5 * math.log2(1 + replies) + .25 * math.log2(1 + reactions)), 4),
             "members": 0.0, "files": 0.0, "freshness": 0.0}
    details = {"body_length": len(body), "keywords": hits, "replies": replies, "reactions": reactions,
               "members": container.num_members if container else 0, "has_date": bool(DATE.search(body)), "links": links}
    reason = None
    if automation_event(event):
        reason = "automation_author"
    elif event.parent_id or event.kind == "thread_reply":
        reason = "thread_reply_requires_story_update"
    elif event.meta.get("content_status") in {"deleted", "unverified_reference", "media_only", "empty"} or not plain.strip():
        reason = "missing_verified_root"
    elif event.meta.get("opt_out") or event.meta.get("excluded"):
        reason = "source_opt_out"
    elif (event.container_id == "slack:C069ARZJNE9" or (container and container.name.lstrip("#") == "rep0rter")) and not event.meta.get("editorial_approved"):
        reason = "internal_channel"
    elif now - event.ts > getattr(cfg, "max_item_age_hours", 48) * 3600:
        reason = "outside_candidate_window"
    elif event.ts > now + 300:
        reason = "future_source_timestamp"
    elif len(body) < 12:
        reason = "readable_text_below_12"
    elif not hits or not parts["date_or_link"]:
        reason = "missing_explicit_topic_and_evidence"
    elif NEGATED.search(without_urls):
        reason = "negated_or_uncertain_announcement"
    elif CANCEL.search(without_urls):
        reason = "cancellation_or_delay_requires_review"
    elif re.search(r"今天晚上|今晚|tonight|今夜|오늘\s*밤", plain, re.I) and datetime.fromtimestamp(now, TZ).date() > datetime.fromtimestamp(event.ts, TZ).date():
        reason = "tonight_crossed_source_day"
    elif INTENT.search(without_urls) and all(re.search(r"^[\s>]*[>「『\"]", line) for line in plain.splitlines() if INTENT.search(URL.sub("", line))):
        reason = "quoted_announcement_requires_review"
    if reason:
        return Decision(False, 0.0, [reason], parts, details=details)
    score = round(sum(parts.values()), 2)
    return Decision(True, score, ["關鍵字：" + "、".join(hits), *[f"{name}={value:g}" for name, value in parts.items()]], parts, details=details)


def legacy_score(event, container, cfg, now, replies_seen=0):
    """Frozen baseline, only for paired observations; never mutates snapshots."""
    score = 2 * min(max(event.reply_count, replies_seen), 15) + 1.5 * min(event.reaction_count, 20)
    if any(k.lower() in event.text.lower() for k in cfg.keywords):
        score += 4
    if URL.search(to_plain(event.text)) and len(event.text) >= 150:
        score += 2
    elif len(event.text) >= 400:
        score += 1.5
    score += math.log10((container.num_members if container else 0) + 1)
    score += int(now - event.ts <= 6 * 3600)
    score += .5 if event.meta.get("files") else 0
    return round(score, 2)


def ensure_audit(store):
    store.conn.executescript("""
    CREATE TABLE IF NOT EXISTS editorial_decisions (
      id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, evaluated_at REAL NOT NULL,
      score_version TEXT NOT NULL, mode TEXT NOT NULL, selected INTEGER NOT NULL,
      decision TEXT NOT NULL, snapshot TEXT NOT NULL, legacy_score REAL NOT NULL,
      reviewer_label TEXT, reviewer_note TEXT, reviewed_at REAL
    );
    CREATE INDEX IF NOT EXISTS editorial_event_time ON editorial_decisions(event_id,evaluated_at);
    CREATE TABLE IF NOT EXISTS writer_audits (
      id INTEGER PRIMARY KEY, event_id TEXT NOT NULL, written_at REAL NOT NULL,
      writer_mode TEXT NOT NULL, model TEXT, prompt_version TEXT NOT NULL,
      validation_errors TEXT NOT NULL, evidence_ids TEXT NOT NULL,
      evidence_snapshot TEXT NOT NULL, output TEXT NOT NULL, needs_review INTEGER NOT NULL
    );
    """)


def record_decision(store, event, container, cfg, now, decision, selected, replies_seen=0, mode="active"):
    ensure_audit(store)
    snapshot = {"event": asdict(event), "container": asdict(container) if container else None,
                "replies_seen": replies_seen, "threshold": cfg.score_threshold,
                "max_item_age_hours": cfg.max_item_age_hours}
    with store.conn:
        cur = store.conn.execute("""INSERT INTO editorial_decisions
          (event_id,evaluated_at,score_version,mode,selected,decision,snapshot,legacy_score)
          VALUES (?,?,?,?,?,?,?,?)""", (event.id, now, SCORE_VERSION, mode, int(selected),
          json.dumps(asdict(decision), ensure_ascii=False), json.dumps(snapshot, ensure_ascii=False),
          legacy_score(event, container, cfg, now, replies_seen)))
    return cur.lastrowid


def review_decision(store, decision_id: int, label: str, note: str, now: float):
    if label not in {"publish", "reject", "review"}:
        raise ValueError("label must be publish, reject, or review")
    ensure_audit(store)
    with store.conn:
        cursor = store.conn.execute("UPDATE editorial_decisions SET reviewer_label=?, reviewer_note=?, reviewed_at=? WHERE id=?",
                                   (label, note, now, decision_id))
        if not cursor.rowcount:
            raise ValueError("decision not found")


def evaluation_report(store, now: float) -> dict:
    """Latest observation per event, with explicit observation period and no success claims."""
    ensure_audit(store)
    rows = store.conn.execute("SELECT * FROM editorial_decisions ORDER BY evaluated_at,id").fetchall()
    latest = {r["event_id"]: r for r in rows}
    first = min((r["evaluated_at"] for r in rows), default=now)
    last = max((r["evaluated_at"] for r in rows), default=now)
    observed_dates = {datetime.fromtimestamp(r["evaluated_at"], TZ).date().isoformat() for r in rows}
    reviewed = [r for r in latest.values() if r["reviewer_label"]]
    buckets = {"small_under_100": 0, "medium_under_1000": 0, "large_1000_plus": 0}
    for r in latest.values():
        snapshot = json.loads(r["snapshot"])
        members = (snapshot.get("container") or {}).get("num_members", 0)
        bucket = "small_under_100" if members < 100 else "medium_under_1000" if members < 1000 else "large_1000_plus"
        buckets[bucket] += int(json.loads(r["decision"])["eligible"])
    def proposed_selected(row):
        decision = json.loads(row["decision"])
        snapshot = json.loads(row["snapshot"])
        return decision["eligible"] and decision["score"] >= snapshot["threshold"]
    return {"score_version": SCORE_VERSION, "observed_days": round((now-first)/86400, 2),
            "observation_dates": len(observed_dates), "first_observation": first if rows else None,
            "last_observation": last if rows else None,
            "observation_complete": bool(rows) and last-first >= 14*86400 and len(observed_dates) >= 14,
            "events": len(latest), "observations": len(rows), "reviewed_events": len(reviewed),
            "false_positive_labels": sum(proposed_selected(r) and r["reviewer_label"] == "reject" for r in reviewed),
            "false_negative_labels": sum(not proposed_selected(r) and r["reviewer_label"] == "publish" for r in reviewed),
            "live_selection_false_positive_labels": sum(r["selected"] and r["reviewer_label"] == "reject" for r in reviewed),
            "eligible_channel_size_coverage": buckets,
            "note": "Paired snapshots are prospective observations; unreviewed events are not accuracy evidence."}


def replay_decision(store, decision_id: int) -> dict:
    """Re-evaluate exactly the saved event/counters/time, never today's event row."""
    from types import SimpleNamespace
    from .store import Container, Event
    row = store.conn.execute("SELECT * FROM editorial_decisions WHERE id=?", (decision_id,)).fetchone()
    if row is None:
        raise ValueError("decision not found")
    if row["score_version"] != SCORE_VERSION:
        raise ValueError("replay requires the recorded scoring version")
    saved = json.loads(row["snapshot"])
    cfg = SimpleNamespace(max_item_age_hours=saved["max_item_age_hours"], score_threshold=saved["threshold"])
    result = evaluate(Event(**saved["event"]), Container(**saved["container"]) if saved["container"] else None,
                      cfg, row["evaluated_at"], saved["replies_seen"])
    return {"id": decision_id, "recorded": json.loads(row["decision"]), "replayed": asdict(result)}


def register_commands(subparsers):
    report = subparsers.add_parser("editorial-report", help="Show paired score observations and 14-day review coverage")
    report.set_defaults(func=cmd_report)
    review = subparsers.add_parser("editorial-review", help="Label a saved selection or exclusion for evaluation")
    review.add_argument("decision", type=int)
    review.add_argument("label", choices=["publish", "reject", "review"])
    review.add_argument("--note", required=True)
    review.set_defaults(func=cmd_review)
    replay = subparsers.add_parser("editorial-replay", help="Replay a stored decision using its exact source snapshot")
    replay.add_argument("decision", type=int)
    replay.set_defaults(func=cmd_replay)


def cmd_report(cfg, args):
    import time
    from .store import Store
    with Store(cfg.db_path) as store:
        print(json.dumps(evaluation_report(store, time.time()), ensure_ascii=False, indent=2))
    return 0


def cmd_review(cfg, args):
    import time
    from .store import Store
    with Store(cfg.db_path) as store:
        review_decision(store, args.decision, args.label, args.note, time.time())
    return 0


def cmd_replay(cfg, args):
    from .store import Store
    with Store(cfg.db_path) as store:
        print(json.dumps(replay_decision(store, args.decision), ensure_ascii=False, indent=2))
    return 0
