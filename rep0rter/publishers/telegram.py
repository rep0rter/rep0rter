"""Telegram Bot API publisher.

Only a bot token and a chat id are needed. The bot must be an administrator
of the target channel with permission to post messages.
"""

from __future__ import annotations

import html
import logging

import requests

from ..config import Config
from ..reporter import Candidate
from ..store import Post

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4096  # Telegram hard limit per message


def _esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def format_item(c: Candidate, post: Post) -> str:
    channel = c.container.name if c.container else c.event.container_id
    stats = []
    if c.event.reply_count or c.event.reaction_count:
        if c.event.reply_count:
            stats.append(f"💬 {c.event.reply_count}")
        if c.event.reaction_count:
            stats.append(f"👍 {c.event.reaction_count}")
    meta = f"#{_esc(channel)} · {_esc(c.event.author_name)}"
    if stats:
        meta += " · " + " ".join(stats)
    return (
        f"<b>{_esc(post.headline)}</b>\n"
        f"{_esc(post.summary)}\n"
        f"{meta} · <a href=\"{_esc(c.event.url)}\">原文</a>"
    )


def format_messages(items: list[tuple[Candidate, Post]]) -> list[str]:
    """Bundle items into as few messages as the length limit allows."""
    blocks = [format_item(c, p) for c, p in items]
    messages: list[str] = []
    current = ""
    for block in blocks:
        joined = block if not current else current + "\n\n" + block
        if len(joined) > MAX_LEN and current:
            messages.append(current)
            current = block
        else:
            current = joined
    if current:
        messages.append(current)
    return messages


def send_message(cfg: Config, text: str) -> int:
    resp = requests.post(
        API.format(token=cfg.telegram_bot_token, method="sendMessage"),
        json={
            "chat_id": cfg.telegram_target,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"telegram sendMessage failed: {resp.status_code} {resp.text[:300]}")
    return int(resp.json()["result"]["message_id"])


def publish(cfg: Config, items: list[tuple[Candidate, Post]], dry_run: bool = False) -> list[int]:
    """Send the items. Returns Telegram message ids (empty on dry run)."""
    if not items:
        return []
    messages = format_messages(items)
    if dry_run or not (cfg.telegram_bot_token and cfg.telegram_target):
        if not dry_run:
            log.warning("Telegram not configured (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID); printing instead")
        for m in messages:
            print("----- telegram message -----")
            print(html.unescape(m))
        return []
    ids = []
    for m in messages:
        ids.append(send_message(cfg, m))
    log.info("sent %d telegram message(s) to %s", len(ids), cfg.telegram_target)
    return ids
