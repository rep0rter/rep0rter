"""Threads publishing transport. Production callers use the durable outbox in threads_delivery."""
from __future__ import annotations

import json
from urllib.parse import urlsplit

import requests

from ..config import Config
from ..reporter import Candidate
from ..store import Post

API = "https://graph.facebook.com/v20.0/{user_id}/threads"
MAX_LEN = 500


class ThreadsRejected(RuntimeError):
    def __init__(self, status: int, retry_after: int | None = None):
        super().__init__(f"Threads rejected delivery (HTTP/API {status})")
        self.status = status
        self.retry_after = retry_after


def _valid_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return parsed.scheme in ("http", "https") and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def format_text(candidate: Candidate, post: Post) -> str:
    headline = (post.headline or '').strip()
    summary = (post.summary or '').strip()
    if headline and summary:
        text = f"{headline}\n\n{summary}"
    elif headline:
        text = headline
    elif summary:
        text = summary
    else:
        text = "rep0rter update"
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > MAX_LEN:
        text = text[: MAX_LEN - 1].rstrip() + "…"
    if candidate.event.url and _valid_url(candidate.event.url):
        text = f"{text}\n\n{candidate.event.url}"
        if len(text) > MAX_LEN:
            suffix = f"\n\n{candidate.event.url}"
            allowed = MAX_LEN - len(suffix)
            text = text[:allowed].rstrip() + "…" + suffix
    return text


def publish_post(cfg: Config, target: str, text: str) -> str:
    if not cfg.threads_access_token or not target:
        raise ValueError("Threads token and user id are required")
    response = requests.post(
        API.format(user_id=target),
        data={"text": text, "access_token": cfg.threads_access_token, "media_type": "TEXT"},
        timeout=(10, 40),
    )
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        raise RuntimeError("Threads returned an ambiguous response") from None
    if body.get("error"):
        code = int(body["error"].get("code", response.status_code))
        retry_after = body["error"].get("retry_after")
        raise ThreadsRejected(code, int(retry_after) if retry_after is not None else None)
    if response.status_code != 200 or not body.get("id"):
        raise RuntimeError("Threads returned an ambiguous response")
    return str(body["id"])
