"""Four-language static site, permanent story/source pages, original cards and RSS."""
from __future__ import annotations

import hashlib
import logging
import tempfile
import shutil
import os
import uuid
import json
import fcntl
from urllib.parse import urlsplit
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape
from PIL import Image

from ..config import TAIPEI, Config
from ..i18n import COPY, LANGUAGES, feed_aliases, feed_name, page_aliases, page_name, post_text
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


def _safe_url(url):
    try:
        p=urlsplit(url)
        return url if p.scheme in ('http','https') and p.hostname and not p.username and not p.password else ''
    except ValueError:
        return ''


def _build(store: Store, cfg: Config, limit: int = 300) -> Path:
    cfg.ensure_dirs()
    # Homepage is bounded; permanent pages survive falling out of that window.
    rows = store.recent_posts(limit=-1)
    items = []
    names = store.user_names("slack")
    from ..sources import plain_text
    from ..stories import info
    with CardRenderer(cfg) as cards:
        for post, event, container in rows:
            image = cards.render(event, container, names)
            source_key = hashlib.sha256((event.source + ":" + event.container_id).encode()).hexdigest()[:20]
            source_path = f"sources/{source_key}/"
            items.append({
                "post": post, "event": event, "container": container,
                "channel": container.name if container else event.container_id,
                "source_label": ('#' if event.source == 'slack' else '') + (container.name if container else event.container_id),
                "source_url": _safe_url(event.url),
                "story": info(store, post.id),
                "content_warning": event.meta.get('content_warning', ''),
                "published_local": _fmt_local(post.published_at),
                "published_rfc822": _fmt_rfc822(post.published_at),
                "event_local": _fmt_local(event.ts),
                "day": datetime.fromtimestamp(post.published_at, TAIPEI).strftime("%Y-%m-%d"),
                "image": image.relative_to(cfg.site_dir).as_posix(), "image_size": image.stat().st_size,
                "original": to_plain(event.text, names) if event.source == "slack" else plain_text(event),
                "source_path": source_path,
            })
    now = datetime.now(timezone.utc).timestamp()
    health = json.loads(store.get_kv('collector_health', '{}'))
    last_healthy_at = health.get('last_healthy_at')
    collection_complete = bool(health.get('healthy') and last_healthy_at
                               and 0 <= now - last_healthy_at <= 9000)
    ctx = {"cfg": cfg, "languages": LANGUAGES, "page_name": page_name,
           "generated_local": _fmt_local(now), "generated_rfc822": _fmt_rfc822(now),
           "event_count": store.event_count(), "post_count": store.post_count(),
           "branding": _branding(cfg),
           "last_healthy": _fmt_local(last_healthy_at) if last_healthy_at else None,
           "collection_complete": collection_complete}
    template = env.get_template("index.html")

    def render_page(language: str, page_items: list[dict], relative_path: str, prefix: str, title: str = "", detail=False):
        days = {}
        for item in page_items:
            days.setdefault(item["day"], []).append(item)
        canonical = cfg.site_url.rstrip("/") + "/" + relative_path
        context = {**ctx, "language": language, "copy": COPY[language], "items": page_items, "days": days,
                   "prefix": prefix, "canonical": canonical, "page_title": title or COPY[language]["title"],
                   "description": page_items[0]["page_description"] if detail else COPY[language]["intro"],
                   "og_image": page_items[0]["image"] if detail else ctx["branding"].get("social", ""),
                   "detail": detail, "feed": feed_name(language),
                   "stats": COPY[language]["stats"].format(events=ctx["event_count"], posts=ctx["post_count"])}
        _write(cfg.site_dir / relative_path, template.render(context))
        for alias in page_aliases(language):
            _write((cfg.site_dir / relative_path).with_name(alias), template.render(context))
        return context

    for asset in ("style.css", "language.js"):
        _write(cfg.site_dir / asset, env.get_template(asset).render())
    root_outputs = []
    visible_posts = {item['post'].id: item['post'] for item in items}
    for language in LANGUAGES:
        localized = []
        for item in items:
            headline, summary, translated = post_text(item["post"], language)
            newer = [version for version in item['story']['versions']
                     if version['revision'] > item['story']['revision'] and version['id'] in visible_posts]
            latest = max(newer, key=lambda version: version['revision']) if newer else None
            latest_path = f"posts/{latest['id']}/{page_name(language)}" if latest else None
            latest_summary = post_text(visible_posts[latest['id']], language)[1] if latest else None
            localized.append({**item, "headline": headline, "summary": summary, "translated": translated,
                              "latest_path": latest_path,
                              "page_description": COPY[language]['superseded'] + ' ' + latest_summary if latest else summary,
                              "story_path": f"posts/{item['post'].id}/{page_name(language)}"})
        sources = {}
        for item in localized:
            title = COPY[language]['previous_report'] + ' · ' + item['headline'] if item['latest_path'] else item['headline']
            render_page(language, [item], item["story_path"], "../../", title, detail=True)
            sources.setdefault(item["source_path"], []).append(item)
        for source_path, source_items in sources.items():
            render_page(language, source_items, source_path + page_name(language), "../../", source_items[0]["source_label"])
        root_outputs.append((language, localized[:limit]))
    # Publish discovery pages after every linked story and asset exists.
    for language, localized in root_outputs:
        root_ctx = render_page(language, localized, page_name(language), "")
        _write(cfg.site_dir / feed_name(language), env.get_template("feed.xml").render(root_ctx))
        for alias in feed_aliases(language):
            _write(cfg.site_dir / alias, env.get_template("feed.xml").render(root_ctx))
        for row in store.conn.execute('SELECT post_id FROM retractions'):
            for filename in (page_name(language), *page_aliases(language)):
                _write(cfg.site_dir / 'posts' / str(row['post_id']) / filename,
                       env.get_template('withdrawn.html').render(language=language,copy=COPY[language],languages=LANGUAGES,page_name=page_name))
    keep={Path(item['image']).name for item in items}
    for path in (cfg.site_dir/'cards').glob('*.png'):
        if path.name not in keep: path.unlink()
    log.info("site written to %s (%d posts, four languages)", cfg.site_dir, len(items))
    return cfg.site_dir


class _StagingConfig:
    def __init__(self,cfg,path):
        self._cfg=cfg
        self.site_dir=path
    def __getattr__(self,name):
        return getattr(self._cfg,name)
    def ensure_dirs(self):
        self.site_dir.mkdir(parents=True,exist_ok=True)


def build(store: Store, cfg: Config, limit: int = 300) -> Path:
    """Publish a complete generation via relative symlink; mount data parent in Docker."""
    cfg.data_dir.mkdir(parents=True,exist_ok=True)
    releases=cfg.data_dir/'.site-releases'
    releases.mkdir(exist_ok=True)
    with (cfg.data_dir/'site-build.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        staged=releases/uuid.uuid4().hex
        staged.mkdir()
        try:
            if (cfg.site_dir/'cards').exists():
                shutil.copytree(cfg.site_dir/'cards',staged/'cards',copy_function=os.link)
            _build(store,_StagingConfig(cfg,staged),limit)
            pointer=cfg.data_dir/('.site-'+uuid.uuid4().hex)
            pointer.symlink_to(staged.relative_to(cfg.data_dir),target_is_directory=True)
            if cfg.site_dir.exists() and not cfg.site_dir.is_symlink():
                cfg.site_dir.rename(releases/('legacy-'+uuid.uuid4().hex))
            pointer.replace(cfg.site_dir)
        except BaseException:
            shutil.rmtree(staged,ignore_errors=True)
            raise
        # Withdrawals purge every older generation so old private assets cannot linger.
        old=sorted((p for p in releases.iterdir() if p!=staged),key=lambda p:p.stat().st_mtime,reverse=True)
        retained=0 if store.conn.execute('SELECT 1 FROM retractions LIMIT 1').fetchone() else 1
        for path in old[retained:]: shutil.rmtree(path)
    return cfg.site_dir
