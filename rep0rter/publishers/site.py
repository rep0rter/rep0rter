"""Four-language static site, permanent story/source pages, original cards and RSS."""
from __future__ import annotations

import hashlib
import logging
import tempfile
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape
from PIL import Image

from ..config import TAIPEI, Config
from ..i18n import COPY, LANGUAGES, feed_name, page_name, post_text
from ..slack_text import to_plain
from ..store import Store
from .cards import CardRenderer

log = logging.getLogger(__name__)
env = Environment(loader=PackageLoader("rep0rter", "templates"),
                  autoescape=select_autoescape(["html", "xml"]), trim_blocks=True, lstrip_blocks=True)


def _fmt_local(ts: float) -> str:
    return datetime.fromtimestamp(ts, TAIPEI).strftime("%Y-%m-%d %H:%M")


def _fmt_rfc822(ts: float) -> str:
    return format_datetime(datetime.fromtimestamp(ts, timezone.utc))


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as output:
        temporary = Path(output.name)
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _branding(cfg: Config) -> dict:
    logo = Path(__file__).resolve().parents[2] / "assets" / "logo.png"
    if not logo.is_file():
        return {}
    version = hashlib.sha256(logo.read_bytes()).hexdigest()[:12]
    folder = cfg.site_dir / "assets"
    folder.mkdir(exist_ok=True)
    with Image.open(logo) as original:
        for name, size in (("logo", 192), ("favicon", 32), ("social", 460)):
            image = original.convert("RGB")
            image.thumbnail((size, size))
            image.save(folder / f"{name}-{version}.png")
    return {name: f"assets/{name}-{version}.png" for name in ("logo", "favicon", "social")}


def build(store: Store, cfg: Config, limit: int = 300) -> Path:
    cfg.ensure_dirs()
    # Homepage is bounded; permanent pages survive falling out of that window.
    rows = store.recent_posts(limit=-1)
    items = []
    names = store.user_names("slack")
    with CardRenderer(cfg) as cards:
        for post, event, container in rows:
            image = cards.render(event, container, names)
            source_key = hashlib.sha256((event.source + ":" + event.container_id).encode()).hexdigest()[:20]
            source_path = f"sources/{source_key}/"
            items.append({
                "post": post, "event": event, "container": container,
                "channel": container.name if container else event.container_id,
                "published_local": _fmt_local(post.published_at),
                "published_rfc822": _fmt_rfc822(post.published_at),
                "event_local": _fmt_local(event.ts),
                "day": datetime.fromtimestamp(post.published_at, TAIPEI).strftime("%Y-%m-%d"),
                "image": image.relative_to(cfg.site_dir).as_posix(), "image_size": image.stat().st_size,
                "original": to_plain(event.text, names) if event.source == "slack" else event.text,
                "source_path": source_path,
            })
    now = datetime.now(timezone.utc).timestamp()
    ctx = {"cfg": cfg, "languages": LANGUAGES, "page_name": page_name,
           "generated_local": _fmt_local(now), "generated_rfc822": _fmt_rfc822(now),
           "event_count": store.event_count(), "post_count": store.post_count(),
           "branding": _branding(cfg)}
    template = env.get_template("index.html")

    def render_page(language: str, page_items: list[dict], relative_path: str, prefix: str, title: str = "", detail=False):
        days = {}
        for item in page_items:
            days.setdefault(item["day"], []).append(item)
        canonical = cfg.site_url.rstrip("/") + "/" + relative_path
        context = {**ctx, "language": language, "copy": COPY[language], "items": page_items, "days": days,
                   "prefix": prefix, "canonical": canonical, "page_title": title or COPY[language]["title"],
                   "description": page_items[0]["summary"] if detail else COPY[language]["intro"],
                   "og_image": page_items[0]["image"] if detail else ctx["branding"].get("social", ""),
                   "detail": detail, "feed": feed_name(language),
                   "stats": COPY[language]["stats"].format(events=ctx["event_count"], posts=ctx["post_count"])}
        _write(cfg.site_dir / relative_path, template.render(context))
        return context

    for asset in ("style.css", "language.js"):
        _write(cfg.site_dir / asset, env.get_template(asset).render())
    root_outputs = []
    for language in LANGUAGES:
        localized = []
        for item in items:
            headline, summary, translated = post_text(item["post"], language)
            localized.append({**item, "headline": headline, "summary": summary, "translated": translated,
                              "story_path": f"posts/{item['post'].id}/{page_name(language)}"})
        sources = {}
        for item in localized:
            render_page(language, [item], item["story_path"], "../../", item["headline"], detail=True)
            sources.setdefault(item["source_path"], []).append(item)
        for source_path, source_items in sources.items():
            render_page(language, source_items, source_path + page_name(language), "../../", "#" + source_items[0]["channel"])
        root_outputs.append((language, localized[:limit]))
    # Publish discovery pages after every linked story and asset exists.
    for language, localized in root_outputs:
        root_ctx = render_page(language, localized, page_name(language), "")
        _write(cfg.site_dir / feed_name(language), env.get_template("feed.xml").render(root_ctx))
    log.info("site written to %s (%d posts, four languages)", cfg.site_dir, len(items))
    return cfg.site_dir
