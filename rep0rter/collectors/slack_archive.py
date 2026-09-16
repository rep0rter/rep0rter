"""Collector for the public g0v Slack archive.

Data source: https://g0v-slack-archive.g0v.ronny.tw/ (maintained by Ronny Wang,
source at https://github.com/ronnywang/g0v-slack-archive).

Two endpoints are used:

* ``GET /``
    Server-rendered HTML. The "Public Channels" table lists every public
    channel. Each row's ``<a class="channel">`` carries the full Slack channel
    object in its ``title`` attribute, and the cells give message count,
    member count and the last-posted time.

* ``GET /index/getmessage?channel=<id>&count=<1..100>&before=<ts>``
    JSON. Returns messages newest-first, strictly older than ``before``.
    Messages that have a Slack ``subtype`` (channel_join, bot_message, ...)
    are silently dropped by the server, so a page can contain fewer than
    ``count`` items. Each message includes the resolved ``user`` object and
    a pre-rendered ``html_content``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

import requests
from bs4 import BeautifulSoup

from ..slack_text import mention_names_from_html
from ..store import Container, Event, Store

SOURCE = "slack"
BASE_URL = "https://g0v-slack-archive.g0v.ronny.tw"
USER_AGENT = "rep0rter/0.2 (+https://github.com/rep0rter/rep0rter)"
PAGE_SIZE = 100          # server-side maximum
REQUEST_DELAY = 0.5      # seconds between requests, be nice to Ronny's server
ARCHIVE_TZ = timezone(timedelta(hours=8))  # site renders dates in Asia/Taipei

log = logging.getLogger(__name__)


@dataclass
class ChannelRow:
    id: str
    name: str
    topic: str
    purpose: str
    num_members: int
    total_messages: int
    last_posted_at: datetime | None


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def _get(session: requests.Session, url: str, **params) -> requests.Response:
    for attempt in range(3):
        try:
            resp = session.get(url, params=params, timeout=60)
            resp.raise_for_status()
            time.sleep(REQUEST_DELAY)
            return resp
        except requests.RequestException as exc:
            wait = 2 ** attempt
            log.warning("%s failed (%s); retrying in %ss", url, exc, wait)
            time.sleep(wait)
    raise RuntimeError(f"giving up on {url}")


def _parse_archive_datetime(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y/%m/%d %H:%M:%S").replace(tzinfo=ARCHIVE_TZ)
    except ValueError:
        return None


def fetch_channels(session: requests.Session) -> list[ChannelRow]:
    """Scrape the public channel table from the homepage."""
    html = _get(session, BASE_URL + "/").text
    soup = BeautifulSoup(html, "html.parser")
    rows: list[ChannelRow] = []
    for tr in soup.select("table tbody tr"):
        link = tr.select_one("a.channel")
        if link is None or not link.get("title"):
            continue
        data = json.loads(link["title"])
        if data.get("is_private"):
            continue
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        # cells: topic, purpose, created, messages, members, last posted, last synced
        rows.append(
            ChannelRow(
                id=data["id"],
                name=data["name"],
                topic=(data.get("topic") or {}).get("value", ""),
                purpose=(data.get("purpose") or {}).get("value", ""),
                num_members=int(data.get("num_members") or 0),
                total_messages=int(cells[3]) if len(cells) > 3 and cells[3].isdigit() else 0,
                last_posted_at=_parse_archive_datetime(cells[5]) if len(cells) > 5 else None,
            )
        )
    return rows


def iter_messages(session: requests.Session, channel_id: str, since: datetime) -> Iterator[dict]:
    """Yield raw messages for a channel, newest first, until older than ``since``."""
    before: str | None = None
    since_ts = since.timestamp()
    while True:
        params = {"channel": channel_id, "count": PAGE_SIZE}
        if before:
            params["before"] = before
        payload = _get(session, BASE_URL + "/index/getmessage", **params).json()
        if not isinstance(payload, dict):  # server returns 0 for unknown/private channels
            return
        page = payload.get("messages") or []
        if not page:
            # Either the start of the channel, or a whole page of subtype
            # messages the server filtered out. We cannot page past the latter
            # because the server never tells us their timestamps.
            return
        for raw in page:
            if float(raw["ts"]) < since_ts:
                return
            yield raw
        before = min(page, key=lambda m: float(m["ts"]))["ts"]


def channel_url(channel_id: str) -> str:
    return f"{BASE_URL}/index/channel/{channel_id}"


def permalink(channel_id: str, ts: str) -> str:
    month = datetime.fromtimestamp(float(ts), ARCHIVE_TZ).strftime("%Y-%m")
    return f"{channel_url(channel_id)}/{month}#ts-{ts}"


def event_id(channel_id: str, ts: str) -> str:
    return f"{SOURCE}:{channel_id}:{ts}"


def to_event(raw: dict, channel_id: str) -> tuple[Event, tuple[str, str, str] | None]:
    """Convert a raw archive message into an Event (+ the author for the users table)."""
    user = raw.get("user") or {}
    if not isinstance(user, dict):  # occasionally a bare user id string
        user = {"id": str(user)}
    ts = raw["ts"]
    thread_ts = raw.get("thread_ts")
    is_reply = bool(thread_ts and thread_ts != ts)
    profile = user.get("profile") or {}
    display = user.get("real_name") or profile.get("display_name") or user.get("name") or ""
    reactions = [{"name": r.get("name"), "count": int(r.get("count") or 0)} for r in raw.get("reactions") or []]
    files = [{"name": f.get("name"), "title": f.get("title"), "mimetype": f.get("mimetype")} for f in raw.get("files") or []]
    event = Event(
        id=event_id(channel_id, ts),
        source=SOURCE,
        kind="thread_reply" if is_reply else "message",
        container_id=f"{SOURCE}:{channel_id}",
        ts=float(ts),
        author_id=f"{SOURCE}:{user.get('id', '')}",
        author_name=display,
        text=raw.get("text", "") or "",
        html=raw.get("html_content", "") or "",
        url=permalink(channel_id, ts),
        parent_id=event_id(channel_id, thread_ts) if is_reply else None,
        reply_count=int(raw.get("reply_count") or 0),
        reaction_count=sum(r["count"] for r in reactions),
        meta={"reactions": reactions, "files": files, "reply_users_count": raw.get("reply_users_count", 0)},
    )
    author = (f"{SOURCE}:{user['id']}", user.get("name", ""), display) if user.get("id") else None
    return event, author


def collect(store: Store, days: int = 2, max_channels: int | None = None, session: requests.Session | None = None) -> int:
    """Fetch every public channel that posted in the window and upsert its messages."""
    session = session or make_session()
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)

    channels = fetch_channels(session)
    log.info("archive lists %d public channels", len(channels))

    # Skip channels that were quiet for the whole window, with a one-day margin
    # because "last posted" on the homepage is only as fresh as the last sync.
    margin = since - timedelta(days=1)
    active = [c for c in channels if c.last_posted_at and c.last_posted_at >= margin]
    active.sort(key=lambda c: c.last_posted_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    if max_channels:
        active = active[:max_channels]
    log.info("%d channels posted within the last %d days", len(active), days)

    total = 0
    for i, ch in enumerate(active, 1):
        store.upsert_container(Container(
            id=f"{SOURCE}:{ch.id}", source=SOURCE, name=ch.name, topic=ch.topic, purpose=ch.purpose,
            num_members=ch.num_members, url=channel_url(ch.id),
        ))
        events: list[Event] = []
        for raw in iter_messages(session, ch.id, since):
            event, author = to_event(raw, ch.id)
            events.append(event)
            if author:
                store.upsert_user(*author)
            for uid, name in mention_names_from_html(event.text, event.html).items():
                store.upsert_user_name(f"{SOURCE}:{uid}", name)
        if not events:
            continue
        n = store.upsert_events(events)
        total += n
        log.info("[%d/%d] #%s: %d messages", i, len(active), ch.name, n)
    log.info("collected %d slack events", total)
    return total


def export_json(store: Store, days: int, out_path: str) -> dict:
    """Write the legacy messages.json shape (used by the GitHub Actions backup)."""
    since_ts = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    rows = store.conn.execute(
        "SELECT * FROM events WHERE source = ? AND ts >= ? ORDER BY container_id, ts DESC", (SOURCE, since_ts)
    ).fetchall()
    by_container: dict[str, list] = {}
    for r in rows:
        by_container.setdefault(r["container_id"], []).append(r)
    channels = []
    for cid, msgs in by_container.items():
        c = store.get_container(cid)
        channels.append({
            "id": cid.split(":", 1)[1], "name": c.name if c else cid, "topic": c.topic if c else "",
            "purpose": c.purpose if c else "", "num_members": c.num_members if c else 0,
            "url": c.url if c else "",
            "messages": [{
                "ts": str(r["ts"]), "posted_at": datetime.fromtimestamp(r["ts"], timezone.utc).isoformat(),
                "user_id": r["author_id"].split(":", 1)[-1], "user_name": r["author_name"], "text": r["text"],
                "permalink": r["url"], "is_thread_reply": r["kind"] == "thread_reply",
                "reply_count": r["reply_count"], "reaction_count": r["reaction_count"],
            } for r in msgs],
        })
    result = {
        "source": BASE_URL, "generated_at": datetime.now(timezone.utc).isoformat(),
        "since": datetime.fromtimestamp(since_ts, timezone.utc).isoformat(),
        "channel_count": len(channels), "message_count": len(rows), "channels": channels,
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)
    return result
