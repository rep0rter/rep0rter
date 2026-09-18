"""Turn Slack mrkdwn into plain text that humans and LLMs can read."""

from __future__ import annotations

import html
import re

_LINK_WITH_LABEL = re.compile(r"<(https?://[^|>]+)\|([^>]+)>")
_LINK_BARE = re.compile(r"<(https?://[^|>]+)>")
_MAILTO = re.compile(r"<mailto:([^|>]+)(?:\|[^>]+)?>")
_USER = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]+)?>")
_CHANNEL = re.compile(r"<#([A-Z0-9]+)\|([^>]*)>")
_SPECIAL = re.compile(r"<!(here|channel|everyone)(?:\|[^>]+)?>")
_SUBTEAM = re.compile(r"<!subteam\^[A-Z0-9]+(?:\|@?([^>]+))?>")


def to_plain(text: str, user_names: dict[str, str] | None = None) -> str:
    """Convert Slack mrkdwn into readable plain text (keeps *bold* and links)."""
    user_names = user_names or {}
    def link_text(match):
        url, label = match.group(1), match.group(2)
        # Slack often uses the URL itself (with or without scheme) as its label.
        normalize = lambda value: re.sub(r"^https?://", "", value).rstrip("/")
        return url if normalize(url) == normalize(label) else f"{label} ({url})"
    out = _LINK_WITH_LABEL.sub(link_text, text)
    out = _LINK_BARE.sub(r"\1", out)
    out = _MAILTO.sub(r"\1", out)
    out = _USER.sub(lambda m: "@" + user_names.get(m.group(1), "某人"), out)
    out = _CHANNEL.sub(lambda m: "#" + (m.group(2) or m.group(1)), out)
    out = _SPECIAL.sub(lambda m: "@" + m.group(1), out)
    out = _SUBTEAM.sub(lambda m: "@" + (m.group(1) or "group"), out)
    out = html.unescape(out)
    return out.strip()


_HTML_MENTION = re.compile(r"<b>@([^<]+)</b>")
_USER_ID = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]+)?>")


def mention_names_from_html(text: str, rendered_html: str) -> dict[str, str]:
    """Pair ``<@Uxxx>`` ids in mrkdwn with the ``<b>@name</b>`` the archive rendered.

    The archive resolves mentions server-side, so this recovers names for
    people who never posted in our window. Only trusts the pairing when both
    sides have the same number of mentions, in the same order.
    """
    ids = _USER_ID.findall(text or "")
    names = _HTML_MENTION.findall(rendered_html or "")
    if not ids or len(ids) != len(names):
        return {}
    return {uid: html.unescape(name) for uid, name in zip(ids, names) if name and name != uid}


_URL_WITH_LABEL = re.compile(r"(\S+) \((https?://\S+)\)")
_URL = re.compile(r"https?://\S+")
_LEADING_MENTIONS = re.compile(r"^(?:@\S+\s*)+")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?])\s*|\n+")
_GREETING = re.compile(
    r"^(?:大家|各位|hi|hello|哈囉|嗨|嗨嗨|安安|早安|午安|晚安|大家好|大家早安|大家午安|大家晚安)"
    r"[^，,、:：\s]{0,8}[，,、:：\s]*"
)


_MRKDWN_LEADING_MENTIONS = re.compile(r"^(?:\s*<@[A-Z0-9]+(?:\|[^>]+)?>\s*)+")


def _clean_for_headline(text: str, user_names: dict[str, str] | None = None) -> str:
    """Accepts raw mrkdwn or already-plain text; returns plain text without URLs or leading mentions."""
    out = _MRKDWN_LEADING_MENTIONS.sub("", text or "")   # before names (which may contain spaces) are resolved
    out = to_plain(out, user_names)
    out = _URL_WITH_LABEL.sub(r"\1", out)
    out = _URL.sub("", out)
    out = _LEADING_MENTIONS.sub("", out.strip())
    return " ".join(out.split())


def sentences(text: str, user_names: dict[str, str] | None = None) -> list[str]:
    """Content sentences of a message: cleaned, greetings dropped, empties removed."""
    result = []
    for raw in _SENTENCE_SPLIT.split(_clean_for_headline(text, user_names)):
        s = raw.strip(" \t*_~`-•·")
        if not s:
            continue
        stripped = _GREETING.sub("", s).strip()
        if len(stripped) < 4:
            continue
        result.append(stripped)
    return result


def rule_headline(text: str, user_names: dict[str, str] | None = None, limit: int = 30) -> str:
    """Best-effort one-line headline without an LLM. ``text`` may be raw mrkdwn."""
    sents = sentences(text, user_names) or [excerpt(_clean_for_headline(text, user_names), limit)]
    head = sents[0].rstrip("。！？!?，,：:、")
    if len(head) > limit:
        cut = head[:limit]
        for sep in ("，", ",", "、", "：", ":", " "):
            idx = cut.rfind(sep)
            if idx >= limit // 2:
                cut = cut[:idx]
                break
        head = cut.rstrip("，,：:、 ") + "…"
    return head


def rule_summary(text: str, user_names: dict[str, str] | None = None, limit: int = 90) -> str:
    """Best-effort short summary without an LLM: leading sentences up to ``limit``."""
    sents = sentences(text, user_names)
    if not sents:
        return excerpt(_clean_for_headline(text, user_names), limit)
    out = ""
    for s in sents:
        candidate = (out + " " + s).strip() if out else s
        if len(candidate) > limit:
            break
        out = candidate
    return out or excerpt(sents[0], limit)


def links_in(text: str) -> list[str]:
    """All http(s) URLs mentioned in a Slack message."""
    urls = [m.group(1) for m in _LINK_WITH_LABEL.finditer(text)]
    urls += [m.group(1) for m in _LINK_BARE.finditer(text)]
    seen: list[str] = []
    for u in urls:
        if u not in seen:
            seen.append(u)
    return seen


def excerpt(text: str, limit: int = 140) -> str:
    """First line-ish of a message, trimmed to ``limit`` characters."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1].rstrip() + "…"
