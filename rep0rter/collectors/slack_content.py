"""Recover visible Slack text without losing the identity of quoted sources."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from ..slack_text import to_plain


def _text(value) -> str:
    return value if isinstance(value, str) else ""


def _contains_image(value) -> bool:
    if isinstance(value, list):
        return any(_contains_image(v) for v in value)
    if isinstance(value, dict):
        return value.get("type") == "image" or _contains_image(value.get("elements")) or _contains_image(value.get("accessory"))
    return False


def rich_text(value) -> str:
    """Read supported text nodes only; do not turn image alt text into a claim."""
    if isinstance(value, list):
        return "\n".join(filter(None, (rich_text(v) for v in value)))
    if not isinstance(value, dict):
        return ""
    kind = value.get("type", "")
    if kind in {"text", "plain_text", "mrkdwn"}:
        return _text(value.get("text"))
    if kind == "link":
        url = _text(value.get("url"))
        return f"<{url}|{value['text']}>" if value.get("text") else f"<{url}>"
    if kind == "user":
        return f"<@{value.get('user_id', '')}>"
    if kind == "channel":
        return f"<#{value.get('channel_id', '')}|{value.get('channel_id', '')}>"
    if kind == "emoji":
        return f":{value.get('name', '')}:"
    if kind == "rich_text_section":
        return "".join(rich_text(v) for v in value.get("elements", []) if isinstance(v, dict))
    if kind in {"rich_text", "rich_text_list", "rich_text_quote", "rich_text_preformatted", "context"}:
        return rich_text(value.get("elements", []))
    if kind == "section":
        return "\n".join(filter(None, [rich_text(value.get("text")), rich_text(value.get("fields", []))]))
    return ""


def _share_reference(attachment: dict) -> tuple[str, str, str]:
    channel = _text(attachment.get("channel_id"))
    ts = str(attachment.get("ts") or "")
    url = _text(attachment.get("from_url") or attachment.get("original_url"))
    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname.endswith(".slack.com"):
        match = re.search(r"/archives/([A-Z0-9]+)/p(\d{10})(\d{6})", parsed.path)
        if match:
            channel = channel or match[1]
            ts = ts or f"{match[2]}.{match[3]}"
    return channel, ts, url


def recover_content(raw: dict, public_channel_ids: set[str] | None, permalink) -> tuple[str, dict]:
    original = _text(raw.get("text"))
    main = original or rich_text(raw.get("blocks", []))
    meta = {"original_text": original, "references": [], "quoted_authors": [], "withheld_reference_count": 0}
    if raw.get("subtype") in {"message_deleted", "tombstone"}:
        return "", {**meta, "content_status": "deleted"}
    parts = [main] if main.strip() else []
    seen = {" ".join(to_plain(main).split())} if main.strip() else set()
    attachments = raw.get("attachments") or []
    for attachment in attachments if isinstance(attachments, list) else []:
        if not isinstance(attachment, dict):
            continue
        channel, ts, from_url = _share_reference(attachment)
        is_share = bool(attachment.get("is_share") or channel or (urlparse(from_url).hostname or "").endswith(".slack.com"))
        content = (_text(attachment.get("text")) or rich_text(attachment.get("blocks", []))
                   or _text(attachment.get("fallback")))
        normalized = " ".join(to_plain(content).split())
        author_id = _text(attachment.get("author_id"))
        author_name = _text(attachment.get("author_name") or attachment.get("author_subname"))
        reference = None
        if is_share:
            public = bool(channel and channel in (public_channel_ids or set()) and re.fullmatch(r"\d+\.\d+", ts))
            canonical_url = ""
            if public:
                try:
                    canonical_url = permalink(channel, ts)
                except (ValueError, OverflowError, OSError):
                    public = False
            reference = {"container_id": f"slack:{channel}" if channel else "", "author_id": f"slack:{author_id}" if author_id else "",
                         "author_name": author_name, "event_id": f"slack:{channel}:{ts}" if channel and ts else "",
                         "channel_id": channel, "ts": ts, "from_url": from_url, "public": public,
                         "url": canonical_url}
            if reference not in meta["references"]:
                meta["references"].append(reference)
            # Slack can repeat the attachment verbatim as its top-level fallback.
            # Preserve only the attributed quote, not a second apparent statement
            # by the sharer (and do not expose an unverified quote via fallback).
            if normalized and normalized == " ".join(to_plain(main).split()) and main in parts:
                parts.remove(main)
                seen.discard(normalized)
            if not public:
                meta["withheld_reference_count"] += 1
                continue
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        if reference:
            attribution = author_name or author_id or "unknown author"
            parts.append(f"[Quoted public Slack message by {attribution}; source: {reference['url']}]\n{content}")
            meta["quoted_authors"].append({"id": reference["author_id"], "name": author_name})
        else:
            label = author_name or _text(attachment.get("service_name")) or "linked source"
            parts.append(f"[Attachment from {label}]\n{content}")
    text = "\n\n".join(parts)
    meta["content_status"] = ("text" if text else "unverified_reference" if meta["withheld_reference_count"]
                              else "media_only" if raw.get("files") or attachments or _contains_image(raw.get("blocks")) else "empty")
    return text, meta
