"""Telegram formatting and transport. Durable delivery lives in ``delivery``."""
from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import urlsplit

import requests

from ..config import Config
from ..i18n import COPY, LANGUAGES, page_name, post_text
from ..reporter import Candidate
from ..store import Post

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4096
CAPTION_LEN = 1024


class TranslationPending(ValueError):
    """The chosen edition is not ready; do not publish another language instead."""


class TelegramRejected(RuntimeError):
    """Telegram explicitly rejected the request; it was not delivered."""
    def __init__(self, status: int, retry_after: int | None = None):
        super().__init__(f"Telegram rejected delivery (HTTP/API {status})")
        self.status = status
        self.retry_after = retry_after


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def _link(url: str, label: str) -> str:
    try:
        parsed = urlsplit(url)
        valid = parsed.scheme in ("http", "https") and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        valid = False
    return f'<a href="{_esc(url)}">{_esc(label)}</a>' if valid else _esc(label)


def _localized_text(post: Post, language: str) -> tuple[str, str]:
    if language not in LANGUAGES:
        raise ValueError(f"Unsupported Telegram language: {language}")
    headline, summary, translated = post_text(post, language)
    if not translated and language != "zh-TW":
        raise TranslationPending(f"Telegram {language} translation is not available; keep delivery pending")
    return headline, summary


def format_item(c: Candidate, post: Post, language: str = "en") -> str:
    headline, summary = _localized_text(post, language)
    channel = c.container.name if c.container else c.event.container_id
    stats = []
    if c.event.reply_count:
        stats.append(f"💬 {c.event.reply_count}")
    if c.event.reaction_count:
        stats.append(f"👍 {c.event.reaction_count}")
    label = ('#' if c.event.source == 'slack' else '') + channel
    meta = f"{_esc(label)} · {_esc(c.event.author_name)}"
    if stats:
        meta += " · " + " ".join(stats)
    return f"<b>{_esc(headline)}</b>\n{_esc(summary)}\n{meta} · {_link(c.event.url, COPY[language]['source'])}"


def format_messages(items: list[tuple[Candidate, Post]], language: str = "en") -> list[str]:
    """Legacy text preview. Reject oversized items without breaking HTML."""
    messages, current = [], ""
    for c, p in items:
        block = format_item(c, p, language)
        if len(block.encode("utf-16-le")) // 2 > MAX_LEN:
            raise ValueError("Single Telegram text item exceeds 4096 characters")
        joined = block if not current else current + "\n\n" + block
        if len(joined.encode("utf-16-le")) // 2 > MAX_LEN:
            messages.append(current)
            current = block
        else:
            current = joined
    if current:
        messages.append(current)
    return messages


def format_caption(cfg: Config, c: Candidate, post: Post) -> str:
    """Fit escaped text to Telegram's caption limit; full text stays on site."""
    if post.id is None:
        raise ValueError("Persist posts before preparing Telegram captions")
    language = cfg.telegram_language
    headline, summary = _localized_text(post, language)
    labels = {"en": "English", "zh-TW": "Chinese", "ja": "Japanese", "ko": "Korean"} if language == "en" else LANGUAGES
    links = " · ".join(_link(f"{cfg.site_url.rstrip('/')}/posts/{post.id}/{page_name(lang)}", name)
                       for lang, name in labels.items())
    footer = f"\n{_link(c.event.url, COPY[language]['source'])}\n{links}"
    # Measure encoded HTML conservatively. Never truncate an entity or HTML tag.
    while True:
        caption = f"<b>{_esc(headline)}</b>\n{_esc(summary)}{footer}"
        if len(caption.encode("utf-16-le")) // 2 <= CAPTION_LEN:
            return caption
        if len(summary) > 1:
            summary = summary[:-2].rstrip() + "…"
        elif len(headline) > 1:
            headline = headline[:-2].rstrip() + "…"
        else:
            raise ValueError("Telegram caption links exceed 1024 characters; shorten REP0RTER_SITE_URL")


def send_photo(cfg: Config, target: str, photo: str | Path, caption: str) -> int:
    """Send once. A transport exception may mean delivery happened; never retry here."""
    if not cfg.telegram_bot_token or not target:
        raise ValueError("Telegram token and target are required")
    with Path(photo).open("rb") as image:
        response = requests.post(
            API.format(token=cfg.telegram_bot_token, method="sendPhoto"),
            data={"chat_id": target, "caption": caption, "parse_mode": "HTML"},
            files={"photo": ("source.png", image, "image/png")}, timeout=(10, 40),
        )
    # Do not expose response text, requests exception URLs, or bot tokens in logs.
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        raise RuntimeError("Telegram returned an ambiguous response") from None
    if body.get("ok") is False:
        status = int(body.get("error_code", response.status_code))
        retry_after = body.get("parameters", {}).get("retry_after")
        raise TelegramRejected(status, int(retry_after) if retry_after is not None else None)
    if response.status_code != 200 or body.get("ok") is not True:
        raise RuntimeError("Telegram returned an ambiguous response")
    return int(body["result"]["message_id"])


def publish(cfg: Config, items: list[tuple[Candidate, Post]], dry_run: bool = False) -> list[int]:
    """Preview only; production callers must use delivery.prepare_posts/deliver_pending."""
    if not dry_run:
        raise RuntimeError("Use the durable Telegram outbox for delivery")
    for c, p in items:
        print(format_caption(cfg, c, p) if p.id else format_item(c, p, cfg.telegram_language))
    return []
