"""Static site + RSS publisher. Regenerated from the store after every run."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from ..config import TAIPEI, Config
from ..store import Store

log = logging.getLogger(__name__)

env = Environment(
    loader=PackageLoader("rep0rter", "templates"),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _fmt_local(ts: float) -> str:
    return datetime.fromtimestamp(ts, TAIPEI).strftime("%Y-%m-%d %H:%M")


def _fmt_rfc822(ts: float) -> str:
    return format_datetime(datetime.fromtimestamp(ts, timezone.utc))


def build(store: Store, cfg: Config, limit: int = 300) -> Path:
    cfg.ensure_dirs()
    rows = store.recent_posts(limit=limit)
    items = []
    for post, event, container in rows:
        items.append({
            "post": post, "event": event, "container": container,
            "channel": container.name if container else event.container_id,
            "published_local": _fmt_local(post.published_at),
            "published_rfc822": _fmt_rfc822(post.published_at),
            "event_local": _fmt_local(event.ts),
            "day": datetime.fromtimestamp(post.published_at, TAIPEI).strftime("%Y-%m-%d"),
        })
    days: dict[str, list] = {}
    for it in items:
        days.setdefault(it["day"], []).append(it)

    last_run = store.last_run()
    ctx = {
        "cfg": cfg,
        "items": items,
        "days": days,
        "generated_local": _fmt_local(datetime.now(timezone.utc).timestamp()),
        "generated_rfc822": _fmt_rfc822(datetime.now(timezone.utc).timestamp()),
        "event_count": store.event_count(),
        "post_count": store.post_count(),
        "last_run": dict(last_run) if last_run else None,
    }
    (cfg.site_dir / "index.html").write_text(env.get_template("index.html").render(ctx), encoding="utf-8")
    (cfg.site_dir / "feed.xml").write_text(env.get_template("feed.xml").render(ctx), encoding="utf-8")
    (cfg.site_dir / "style.css").write_text(env.get_template("style.css").render(ctx), encoding="utf-8")
    log.info("site written to %s (%d posts)", cfg.site_dir, len(items))
    return cfg.site_dir
