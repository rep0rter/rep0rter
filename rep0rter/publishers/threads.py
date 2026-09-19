"""Threads text publishing transport. Never log the access token or response body."""
from __future__ import annotations

from urllib.parse import quote, urlsplit

import requests

from ..config import Config
from ..i18n import page_name
from ..store import Post

API = "https://graph.threads.net/v1.0"
MAX_LEN = 500


class ThreadsRejected(RuntimeError):
    def __init__(self, status: int, retry_after: int | None = None):
        super().__init__(f"Threads rejected delivery (HTTP {status})")
        self.status = status
        self.retry_after = retry_after


def _units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _truncate(text: str, limit: int) -> str:
    if _units(text) <= limit:
        return text
    if limit < 2:
        raise ValueError("Threads URL leaves no room for content")
    while text and _units(text) > limit - 1:
        text = text[:-1]
    return text.rstrip() + "…"


def _valid_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username and not parsed.password
    except ValueError:
        return False


def format_text(cfg: Config, post: Post) -> str:
    root = (cfg.site_url or "").rstrip("/")
    url = f"{root}/posts/{post.id}/{page_name('zh-TW')}"
    if post.id is None or not _valid_url(url):
        raise ValueError("A saved post and HTTPS site URL are required for Threads")
    body = "\n\n".join(part for part in ((post.headline or "").strip(), (post.summary or "").strip()) if part)
    if not body:
        raise ValueError("Threads post has no title or summary")
    suffix = "\n\n" + url
    return _truncate(body, MAX_LEN - _units(suffix)) + suffix


def _request(method: str, url: str, token: str, **kwargs) -> dict:
    try:
        response = requests.request(method, url, headers={"Authorization": f"Bearer {token}"},
                                    timeout=(10, 40), **kwargs)
    except requests.RequestException as exc:
        raise RuntimeError("Threads transport result is unknown") from exc
    if response.status_code >= 400:
        retry = response.headers.get("Retry-After")
        raise ThreadsRejected(response.status_code, int(retry) if retry and retry.isdigit() else None)
    try:
        data = response.json()
    except ValueError:
        raise RuntimeError("Threads returned an ambiguous response") from None
    if not isinstance(data, dict) or data.get("error"):
        raise RuntimeError("Threads returned an ambiguous response")
    return data


def get_account(cfg: Config) -> dict:
    if not cfg.threads_access_token:
        raise ValueError("Threads token is required")
    return _request("GET", API + "/me", cfg.threads_access_token, params={"fields": "id,username"})


def get_post(cfg: Config, remote_id: str) -> dict:
    if not cfg.threads_access_token:
        raise ValueError("Threads token is required")
    return _request("GET", API + "/" + quote(remote_id, safe=""), cfg.threads_access_token,
                    params={"fields": "id,text,permalink"})


def publish_post(cfg: Config, target: str, text: str) -> str:
    if not cfg.threads_access_token or not target:
        raise ValueError("Threads token and user id are required")
    body = _request("POST", API + "/" + quote(target, safe="") + "/threads",
                    cfg.threads_access_token,
                    data={"media_type": "TEXT", "text": text, "auto_publish_text": "true"})
    if not body.get("id"):
        raise RuntimeError("Threads publish response missing id")
    return str(body["id"])
