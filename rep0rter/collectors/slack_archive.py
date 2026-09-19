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
    ``after`` is also supported upstream but overrides ``before``; the two
    parameters cannot be combined to request a bounded interval.
    Messages that have a Slack ``subtype`` (channel_join, bot_message, ...)
    are silently dropped by the server, so a page can contain fewer than
    ``count`` items. Each message includes the resolved ``user`` object and
    a pre-rendered ``html_content``.
"""

from __future__ import annotations

import json
import logging
import re
import time
from ..runtime import sleep
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

import requests
from bs4 import BeautifulSoup

from ..slack_text import mention_names_from_html
from ..store import Container, Event, Store
from .slack_content import recover_content

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
    last_synced_at: datetime | None = None


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    return session


def _get(session: requests.Session, url: str, **params) -> requests.Response:
    for attempt in range(3):
        try:
            resp = session.get(url, params=params, timeout=60)
            resp.raise_for_status()
            sleep(REQUEST_DELAY)
            return resp
        except requests.RequestException as exc:
            wait = 2 ** attempt
            log.warning("%s failed (%s); retrying in %ss", url, exc, wait)
            sleep(wait)
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
        try:
            data = json.loads(link["title"])
            if not isinstance(data, dict):
                raise ValueError("channel metadata is not an object")
            if data.get("is_private"):
                continue
            if not all(isinstance(data.get(k), str) and data[k].strip() for k in ("id", "name")):
                raise ValueError("missing channel id/name")
            cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
            if len(cells) < 7:
                raise ValueError("channel table column layout changed")
            last_posted = _parse_archive_datetime(cells[5])
            if cells[5] and last_posted is None:
                log.warning("degraded channel %s: invalid last-posted date %r", data["id"], cells[5])
            rows.append(ChannelRow(
                id=data["id"],
                name=data["name"],
                topic=(data.get("topic") or {}).get("value", ""),
                purpose=(data.get("purpose") or {}).get("value", ""),
                num_members=int(str(data.get("num_members") or 0).replace(",", "")),
                total_messages=int(cells[3].replace(",", "") or 0),
                last_posted_at=last_posted,
                last_synced_at=_parse_archive_datetime(cells[6]),
            ))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            log.warning("degraded channel listing: skipping malformed row (%s)", exc)
    if not rows:
        raise RuntimeError("archive homepage contains no valid public channels; layout or access may have changed")
    return rows


def _timestamp(value) -> Decimal:
    try:
        stamp = Decimal(str(value))
        if not stamp.is_finite() or stamp < 0:
            raise ValueError("invalid timestamp")
        return stamp
    except (InvalidOperation, ValueError) as exc:
        raise RuntimeError("archive returned an invalid message timestamp") from exc


def channel_is_older_than(session, channel_id: str, lower: Decimal) -> bool:
    """Verify a quiet channel using the unfiltered latest-month HTML archive.

    JSON filters subtype rows after its LIMIT, so even a nonempty raw head can
    return no messages. The HTML month index covers raw rows, including joins.
    Only prove an empty window when the entire latest month precedes its start.
    Missing/changed markup is not proof; callers retain their existing gap.
    """
    response = _get(session, BASE_URL + '/index/channel/' + channel_id)
    soup = BeautifulSoup(response.text, 'html.parser')
    feed = soup.select_one('section[role="feed"]')
    nav = soup.select_one('nav[role="pagination"]')
    if feed is None or nav is None:
        return False
    pattern = re.compile(r'/index/channel/' + re.escape(channel_id) + r'/(\d{4}-\d{2})')
    months = []
    for link in nav.select('a.dropdown-item'):
        match = pattern.fullmatch(link.get('href', ''))
        if not match:
            return False
        try:
            months.append(datetime.strptime(match[1], '%Y-%m').replace(tzinfo=ARCHIVE_TZ))
        except ValueError:
            return False
    active = nav.select('a.nav-link.active')
    if not months or len(active) != 1:
        return False
    latest = max(months)
    if active[0].get('href') != '/index/channel/' + channel_id + '/' + latest.strftime('%Y-%m'):
        return False
    end = latest.replace(year=latest.year + 1, month=1) if latest.month == 12 else latest.replace(month=latest.month + 1)
    if Decimal(str(end.timestamp())) > lower:
        return False
    raw_stamps = []
    for node in feed.select('.message[id]'):
        if node.find_parent(class_='message') is not None:
            continue  # Replies rendered under an old root do not define its month.
        metadata = node.select_one('.message-time[title]')
        if metadata is None or metadata.find_parent(class_='message') is not node:
            return False
        try:
            raw = json.loads(metadata['title'])
            stamp = _timestamp(raw['ts'])
            if node.get('id') != 'ts-' + str(raw['ts']):
                return False
        except (ValueError, KeyError, TypeError, RuntimeError):
            return False
        if not Decimal(str(latest.timestamp())) <= stamp < Decimal(str(end.timestamp())):
            return False
        raw_stamps.append(stamp)
    if not raw_stamps:
        return False
    log.info('Verified quiet Slack channel %s: latest raw month %s precedes scan lower %s',
             channel_id, latest.strftime('%Y-%m'), lower)
    return True


def channel_window_is_complete(session, channel_id: str, lower: Decimal, collected) -> bool:
    """Prove a recent JSON window against the complete raw latest-month HTML.

    A widened fractional cursor can repeat the only non-subtype message. The
    month's raw count and timestamps must agree, and every eligible HTML row
    must already be collected. Missing markup or older-month overlap is a gap.
    """
    soup = BeautifulSoup(_get(session, BASE_URL + '/index/channel/' + channel_id).text, 'html.parser')
    feed, nav = soup.select_one('section[role="feed"]'), soup.select_one('nav[role="pagination"]')
    if feed is None or nav is None:
        return False
    pattern = re.compile(r'/index/channel/' + re.escape(channel_id) + r'/(\d{4}-\d{2})')
    months = {}
    try:
        for link in nav.select('a.dropdown-item'):
            match = pattern.fullmatch(link.get('href', ''))
            if not match:
                return False
            month = datetime.strptime(match[1], '%Y-%m').replace(tzinfo=ARCHIVE_TZ)
            months[month] = link
        active = nav.select('a.nav-link.active')
        if not months or len(active) != 1:
            return False
        latest = max(months)
        if active[0].get('href') != months[latest].get('href'):
            return False
        end = latest.replace(year=latest.year + 1, month=1) if latest.month == 12 else latest.replace(month=latest.month + 1)
        start_ts, end_ts = Decimal(str(latest.timestamp())), Decimal(str(end.timestamp()))
        if not start_ts <= lower < end_ts:
            return False
        badge = months[latest].select_one('.badge')
        if badge is None:
            return False
        expected = int(badge.get_text(strip=True))
        rows = [n for n in feed.select('.message[id]') if n.find_parent(class_='message') is None]
        if not rows or len(rows) != expected:
            return False
        for node in rows:
            metadata = node.select_one('.message-time[title]')
            if metadata is None or metadata.find_parent(class_='message') is not node:
                return False
            raw = json.loads(metadata['title'])
            stamp = _timestamp(raw['ts'])
            # PHP formats the numeric row ID with 14 significant digits; the
            # embedded Slack JSON retains the authoritative microseconds.
            valid_ids = {'ts-' + str(raw['ts']), 'ts-' + format(stamp, '.14g')}
            if node.get('id') not in valid_ids or not start_ts <= stamp < end_ts:
                return False
            if not raw.get('subtype') and stamp >= lower and stamp not in collected:
                return False
        return True
    except (ValueError, KeyError, TypeError, RuntimeError):
        return False


def _merge_duplicate(left: dict, right: dict) -> dict:
    """Prefer explicit edit time, then richer snapshots; fill only missing fields.

    Upstream has no observation time for duplicate rows. Counters remain those
    of the selected snapshot, never an invented combination of maxima.
    """
    def rank(raw):
        edited = raw.get("edited")
        edited_ts = edited.get("ts", 0) if isinstance(edited, dict) else 0
        serialized = json.dumps(raw, sort_keys=True, ensure_ascii=False)
        return _timestamp(edited_ts), len(serialized), serialized
    preferred, other = sorted((left, right), key=rank, reverse=True)
    result = dict(other)
    result.update(preferred)
    return result


def iter_messages(session: requests.Session, channel_id: str, since: datetime) -> Iterator[dict]:
    """Yield raw messages for a channel, newest first, until older than ``since``."""
    before: str | None = None
    since_ts = Decimal(str(since.timestamp()))
    collected: dict[Decimal, dict] = {}
    while True:
        params = {"channel": channel_id, "count": PAGE_SIZE}
        if before:
            params["before"] = before
        payload = _get(session, BASE_URL + "/index/getmessage", **params).json()
        if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
            raise RuntimeError(f"archive channel {channel_id} is unavailable or returned an invalid page")
        page = payload["messages"]
        if not page:
            # Either the start of the channel, or a whole page of subtype
            # messages the server filtered out. We cannot page past the latter
            # because the server never tells us their timestamps.
            log.warning("channel %s ended at an ambiguous empty page; archive exposes no raw-page cursor", channel_id)
            break
        merged: dict[Decimal, dict] = {}
        for raw in page:
            if not isinstance(raw, dict) or "ts" not in raw:
                raise RuntimeError(f"archive channel {channel_id} returned a malformed message")
            stamp = _timestamp(raw["ts"])
            merged[stamp] = _merge_duplicate(merged[stamp], raw) if stamp in merged else raw
        oldest = min(merged)
        if before is not None and oldest >= _timestamp(before):
            raise RuntimeError(f"archive pagination made no progress for channel {channel_id}")
        for stamp, raw in merged.items():
            if stamp >= since_ts:
                collected[stamp] = _merge_duplicate(collected[stamp], raw) if stamp in collected else raw
        if oldest < since_ts:
            break
        before = str(merged[oldest]["ts"])
    yield from (collected[stamp] for stamp in sorted(collected, reverse=True))


def channel_url(channel_id: str) -> str:
    return f"{BASE_URL}/index/channel/{channel_id}"


def permalink(channel_id: str, ts: str) -> str:
    month = datetime.fromtimestamp(float(ts), ARCHIVE_TZ).strftime("%Y-%m")
    return f"{channel_url(channel_id)}/{month}#ts-{ts}"


def event_id(channel_id: str, ts: str) -> str:
    return f"{SOURCE}:{channel_id}:{ts}"


def to_event(raw: dict, channel_id: str, public_channel_ids: set[str] | None = None) -> tuple[Event, tuple[str, str, str] | None]:
    """Convert a raw archive message into an Event (+ the author for the users table)."""
    user = raw.get("user") or {}
    if not isinstance(user, dict):  # occasionally a bare user id string
        user = {"id": str(user)}
    ts = str(raw["ts"])
    _timestamp(ts)
    thread_ts = str(raw["thread_ts"]) if raw.get("thread_ts") else None
    is_reply = bool(thread_ts and thread_ts != ts)
    profile = user.get("profile") if isinstance(user.get("profile"), dict) else {}
    display = user.get("real_name") or profile.get("display_name") or user.get("name") or ""
    reactions = [{"name": r.get("name"), "count": int(r.get("count") or 0)} for r in raw.get("reactions") or [] if isinstance(r, dict)]
    files = [{"name": f.get("name"), "title": f.get("title"), "mimetype": f.get("mimetype")} for f in raw.get("files") or [] if isinstance(f, dict)]
    text, content_meta = recover_content(raw, public_channel_ids, permalink)
    event = Event(
        id=event_id(channel_id, ts),
        source=SOURCE,
        kind="thread_reply" if is_reply else "message",
        container_id=f"{SOURCE}:{channel_id}",
        ts=float(ts),
        author_id=f"{SOURCE}:{user.get('id', '')}",
        author_name=display,
        text=text,
        html=(raw.get("html_content", "") or "") if not content_meta["withheld_reference_count"] and content_meta["content_status"] != "deleted" else "",
        url=permalink(channel_id, ts),
        parent_id=event_id(channel_id, thread_ts) if is_reply else None,
        reply_count=int(raw.get("reply_count") or 0),
        reaction_count=sum(r["count"] for r in reactions),
        meta={
            **content_meta,
            "is_bot": bool(user.get("is_bot") or raw.get("bot_id")),
            "bot_id": raw.get("bot_id"), "app_id": raw.get("app_id"), "subtype": raw.get("subtype"),
            "visibility": "public", "source_instance": BASE_URL, "external_id": ts,
            "canonical_object_id": event_id(channel_id, ts), "content_format": "mrkdwn",
            "updated_at": (raw.get("edited") or {}).get("ts") if isinstance(raw.get("edited"), dict) else None,
            "engagement": {"slack_reactions": sum(r["count"] for r in reactions), "slack_replies": int(raw.get("reply_count") or 0)},
            "relations": {"reply_to": event_id(channel_id, thread_ts) if is_reply else None},
            "reactions": reactions, "files": files, "reply_users_count": raw.get("reply_users_count", 0),
            "avatar_url": next((profile.get(k) for k in ("image_192", "image_512", "image_1024", "image_72", "image_48", "image_32", "image_24", "image_original")
                                if profile.get("is_custom_image") is not False and isinstance(profile.get(k), str) and profile[k].startswith("https://")), ""),
            "avatar_is_custom": profile.get("is_custom_image"),
            "source_name": "g0v Slack",
        },
    )
    author = (f"{SOURCE}:{user['id']}", user.get("name", ""), display) if user.get("id") else None
    return event, author


def collect(store: Store, days: int = 2, max_channels: int | None = None, session: requests.Session | None = None) -> int:
    """Collect via durable overlap cursors, bounded refresh and source health."""
    from .slack_incremental import collect as incremental_collect
    return incremental_collect(store, days, max_channels, session)


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
