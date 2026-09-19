"""Cached original-text cards: HTML element screenshots, with a Pillow fallback.

Inspired by chumei's render_source_covers.py. The browser renders our escaped
template, never arbitrary remote pages. Identity images are embedded locally.
"""

from __future__ import annotations

import base64
import hashlib
import io
import ipaddress
import logging
import re
import socket
import tempfile
import ssl
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import certifi
import urllib3
from jinja2 import Environment, PackageLoader, select_autoescape
from PIL import Image, ImageDraw, ImageFont, ImageOps

from ..config import TAIPEI, Config
from ..slack_text import to_plain
from ..store import Container, Event, Post

log = logging.getLogger(__name__)
SIZE = (1200, 630)
MAX_IMAGE_BYTES = 3_000_000
# Keep generated image content aligned with the website without filtering photos.
CARD_PALETTES = {
    "light": {"canvas": "#eef2f6", "surface": "#f9fbfd", "ink": "#202b39",
              "muted": "#536172", "line": "#d9e1eb", "accent": "#2864a6",
              "avatar": "#deebf8"},
    "dark": {"canvas": "#11161e", "surface": "#232b36", "ink": "#f2f5fa",
             "muted": "#b8c5d5", "line": "#3a4759", "accent": "#94bff2",
             "avatar": "#304662"},
}
env = Environment(loader=PackageLoader("rep0rter", "templates"), autoescape=select_autoescape(["html"]))
env.globals["card_palettes"] = CARD_PALETTES


def _public_image_address(url: str) -> tuple[str, str] | None:
    """Resolve once and return a public address plus its original TLS hostname."""
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.port not in (None, 443)):
            return None
        hostname = parsed.hostname.encode("idna").decode("ascii")
        addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        if addresses and all(ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            return hostname, addresses[0][4][0]
    except (ValueError, OSError):
        pass
    return None


def _public_image_url(url: str) -> bool:
    return _public_image_address(url) is not None


def _fetch_image(url: str) -> bytes | None:
    """Pin each public DNS result, verify original TLS identity, and bound reads."""
    deadline = time.monotonic() + 20
    for _ in range(4):
        address = _public_image_address(url)
        remaining = deadline - time.monotonic()
        if address is None or remaining <= 0:
            return None
        hostname, public_ip = address
        parsed = urlsplit(url)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        # A direct pool uses no environment proxies and never resolves the
        # untrusted hostname again. TLS and HTTP still use the original host.
        with urllib3.HTTPSConnectionPool(
            public_ip, port=443, server_hostname=hostname, assert_hostname=hostname,
            cert_reqs=ssl.CERT_REQUIRED, ca_certs=certifi.where(), retries=False,
            timeout=urllib3.Timeout(total=remaining, connect=min(3, remaining), read=min(6, remaining)),
        ) as pool:
            response = pool.urlopen(
                "GET", target, redirect=False, retries=False, preload_content=False,
                decode_content=False,
                headers={"Host": f"[{hostname}]" if ":" in hostname else hostname,
                         "User-Agent": "rep0rter/0.2 public-source-card", "Accept-Encoding": "identity"},
            )
            try:
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get("Location")
                    if not location:
                        return None
                    url = urljoin(url, location)
                    continue
                if response.status != 200 or response.headers.get("Content-Encoding", "identity").lower() != "identity":
                    return None
                data = bytearray()
                while True:
                    if time.monotonic() >= deadline:
                        return None
                    # read1 performs at most one underlying read, so a slow
                    # trickle cannot hide inside a request for a complete chunk.
                    chunk = response.read1(min(32_768, MAX_IMAGE_BYTES + 1 - len(data)), decode_content=False)
                    if time.monotonic() >= deadline:
                        return None
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > MAX_IMAGE_BYTES:
                        return None
            finally:
                response.close()
            with Image.open(io.BytesIO(data)) as original:
                if original.width * original.height > 16_000_000:
                    return None
                original.seek(0)
                image = ImageOps.exif_transpose(original).convert("RGBA")
                image.thumbnail((192, 192))
                out = io.BytesIO()
                image.save(out, "PNG")
                return out.getvalue()
    return None


def identity_image(event: Event, cfg: Config) -> bytes | None:
    """Cache avatars/logos, including failures for one hour to avoid retry storms."""
    candidates = [event.meta.get("avatar_url"), event.meta.get("source_logo_url")]
    if event.source != "slack":
        parsed = urlsplit(event.url)
        if parsed.scheme == "https" and parsed.hostname:
            candidates.append(f"https://{parsed.netloc}/favicon.ico")
    cache = cfg.data_dir / "image-cache"
    for url in candidates:
        if not isinstance(url, str) or not url:
            continue
        key = hashlib.sha256(url.encode()).hexdigest()
        path, failure = cache / f"{key}.png", cache / f"{key}.failed"
        if path.exists():
            try:
                data = path.read_bytes()
                with Image.open(io.BytesIO(data)) as cached:
                    cached.verify()
                return data
            except (OSError, ValueError):
                path.unlink(missing_ok=True)
        if failure.exists() and time.time() - failure.stat().st_mtime < 3600:
            continue
        try:
            data = _fetch_image(url)
            cache.mkdir(parents=True, exist_ok=True)
            if data:
                with tempfile.NamedTemporaryFile(dir=cache, suffix=".png", delete=False) as output:
                    temporary = Path(output.name)
                    output.write(data)
                try:
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
                failure.unlink(missing_ok=True)
                return data
        except (urllib3.exceptions.HTTPError, OSError, ValueError, Image.DecompressionBombError):
            log.info("identity image unavailable for event %s", event.id)
        cache.mkdir(parents=True, exist_ok=True)
        failure.touch()
    return None


def _font(cfg: Config, size: int):
    candidates = [cfg.card_font,
                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
                  "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
                  "/System/Library/Fonts/PingFang.ttc"]
    for path in candidates:
        if path and Path(path).is_file():
            return ImageFont.truetype(path, size=size)
    raise RuntimeError("Card fonts missing: install fonts-noto-cjk or set REP0RTER_CARD_FONT to a CJK font file")


def _wrap(text: str, font, width: int, max_lines: int) -> list[str]:
    """Wrap CJK and long URLs; mark any omitted text with an ellipsis."""
    lines, line = [], ""
    tokens = re.findall(r"[\x21-\x7e]+|[^\x21-\x7e]", text)
    for token in tokens:
        if token == "\n":
            lines.append(line)
            line = ""
            continue
        if font.getlength(token) > width:
            pieces = list(token)
        else:
            pieces = [token]
        for part in pieces:
            if line and font.getlength(line + part) > width:
                lines.append(line.rstrip())
                line = ""
            line += part if line else part.lstrip()
    if line:
        lines.append(line.rstrip())
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and font.getlength(lines[-1] + "…") > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return lines


class CardRenderer:
    """One lazily launched browser for a batch; cached cards need no browser."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.browser = self.playwright = self.page = None
        self.browser_failed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def _page(self):
        if self.browser_failed:
            return None
        if self.page is None:
            try:
                from playwright.sync_api import sync_playwright
                self.playwright = sync_playwright().start()
                options = {"headless": True, "args": ["--disable-background-networking"]}
                if self.cfg.chrome_path:
                    options["executable_path"] = self.cfg.chrome_path
                self.browser = self.playwright.chromium.launch(**options)
                self.page = self.browser.new_page(viewport={"width": 1200, "height": 630}, device_scale_factor=1)
                self.page.route("**/*", lambda route: route.abort())
            except Exception:
                log.warning("Chromium unavailable; rendering original-text cards with Pillow")
                self.browser_failed = True
                return None
        return self.page

    def render(self, event: Event, container: Container | None, names: dict[str, str] | None = None,
               *, theme: str = "light") -> Path:
        if theme not in CARD_PALETTES:
            raise ValueError("Card theme must be light or dark")
        avatar = identity_image(event, self.cfg)
        host = urlsplit(event.url).hostname or event.source
        source = str(event.meta.get("source_name") or ("g0v Slack" if event.source == "slack" else host))
        author = event.author_name or source
        channel = container.name if container else event.container_id
        from ..sources import plain_text
        original = to_plain(event.text, names) if event.source == "slack" else plain_text(event).strip()
        if not original:
            original = "（來源訊息沒有可顯示的文字 / No source text available）"
        ctx = {"theme": theme, "author": author, "source": source, "channel": channel,
               "original": original[:4000] + ("…" if len(original) > 4000 else ""),
               "date": datetime.fromtimestamp(event.ts, TAIPEI).strftime("%Y.%m.%d · %H:%M UTC+8"),
               "initial": author.strip()[:1].upper() or "r", "host": host,
               "url": event.url,
               "brand": self.cfg.site_title,
               "avatar": "data:image/png;base64," + base64.b64encode(avatar).decode() if avatar else ""}
        template = env.get_template("card.html")
        rendered = template.render(ctx)
        return self._render_image(event, ctx, avatar, rendered)

    def render_report(self, event: Event, container: Container | None, post: Post, language: str = "en",
                      *, theme: str = "light") -> Path:
        """Render a translated report, visibly distinguished from source quotations.

        Telegram only publishes English reports. Never fall back to the original
        language when that translation is missing or incomplete.
        """
        if theme not in CARD_PALETTES:
            raise ValueError("Card theme must be light or dark")
        if language != "en":
            raise ValueError("Report cards currently require English")
        translated = post.translations.get(language)
        if not isinstance(translated, dict) or not all(
            isinstance(translated.get(field), str) and translated[field].strip()
            for field in ("headline", "summary")
        ):
            raise ValueError("An English headline and summary are required for a report card")
        avatar = identity_image(event, self.cfg)
        host = urlsplit(event.url).hostname or event.source
        source = {"slack": "g0v Slack", "github": "GitHub", "mastodon": "Mastodon"}.get(event.source, host)
        author = event.author_name or source
        channel = container.name if container else event.container_id
        ctx = {"theme": theme, "author": author, "source": source, "channel": channel,
               "headline": translated["headline"].strip(), "summary": translated["summary"].strip(),
               "date": datetime.fromtimestamp(event.ts, TAIPEI).strftime("%Y.%m.%d · %H:%M UTC+8"),
               "initial": author.strip()[:1].upper() or "r", "host": host, "url": event.url,
               "brand": "rep0rter", "report": True, "owner_submitted": event.meta.get('owner_submitted', False),
               "self_reported": event.meta.get('self_reported', False),
               "avatar": "data:image/png;base64," + base64.b64encode(avatar).decode() if avatar else ""}
        rendered = env.get_template("report-card.html").render(ctx)
        return self._render_image(event, ctx, avatar, rendered, version="report-card-en-v1")

    def _render_image(self, event: Event, ctx: dict, avatar: bytes | None, rendered: str,
                      version: str = "card-v1") -> Path:
        digest = hashlib.sha256((version + rendered + str(self.cfg.card_font)).encode()).hexdigest()[:24]
        path = self.cfg.site_dir / "cards" / f"{digest}.png"
        if path.exists():
            try:
                with Image.open(path) as cached:
                    cached.verify()
                return path
            except (OSError, ValueError):
                path.unlink(missing_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".png", delete=False) as output:
            temporary = Path(output.name)
        try:
            page = self._page()
            if page:
                try:
                    page.set_content(rendered, wait_until="load")
                    page.evaluate("document.fonts.ready")
                    page.locator(".card").screenshot(path=str(temporary), type="png", animations="disabled", timeout=10_000)
                except Exception:
                    log.warning("HTML card render failed for %s; using Pillow", event.id)
                    self._fallback(temporary, ctx, avatar)
            else:
                self._fallback(temporary, ctx, avatar)
            with Image.open(temporary) as complete:
                complete.verify()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return path

    def _fallback(self, path: Path, ctx: dict, avatar: bytes | None):
        palette = CARD_PALETTES[ctx.get("theme", "light")]
        image = Image.new("RGB", SIZE, palette["canvas"])
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((28, 28, 1172, 602), radius=24, fill=palette["surface"])
        draw.rounded_rectangle((60, 60, 142, 142), radius=24, fill=palette["avatar"])
        if avatar:
            with Image.open(io.BytesIO(avatar)) as face:
                face = ImageOps.fit(face.convert("RGBA"), (82, 82))
                mask = Image.new("L", (82, 82))
                ImageDraw.Draw(mask).rounded_rectangle((0, 0, 81, 81), radius=24, fill=255)
                image.paste(face, (60, 60), mask)
        else:
            draw.text((85, 72), ctx["initial"], font=_font(self.cfg, 40), fill=palette["accent"])
        author_label = ("Source: " if ctx.get("report") else "") + ctx["author"]
        for line in _wrap(author_label, _font(self.cfg, 29), 700, 1):
            draw.text((164, 61), line, font=_font(self.cfg, 29), fill=palette["ink"])
        label = f'{ctx["source"]} · #{ctx["channel"]}'
        draw.text((164, 109), _wrap(label, _font(self.cfg, 20), 920, 1)[0], font=_font(self.cfg, 20), fill=palette["muted"])
        draw.line((60, 170, 1140, 170), fill=palette["line"], width=2)
        if ctx.get("report"):
            report_label = ("SELF-REPORTED · Written by the contributor" if ctx.get("self_reported") else
                            "OWNER SUBMISSION · Ownership self-declared" if ctx.get("owner_submitted")
                            else "ENGLISH REPORT · Summary by rep0rter")
            draw.text((68, 190), report_label, font=_font(self.cfg, 18), fill=palette["muted"])
            headline_lines = _wrap(ctx["headline"], _font(self.cfg, 38), 1060, 2)
            for i, line in enumerate(headline_lines):
                draw.text((68, 226 + i * 49), line, font=_font(self.cfg, 38), fill=palette["ink"])
            for i, line in enumerate(_wrap(ctx["summary"], _font(self.cfg, 30), 1060, 3)):
                draw.text((68, 245 + len(headline_lines) * 49 + i * 44), line, font=_font(self.cfg, 30), fill=palette["ink"])
            footer_label = "SOURCE POSTED"
        else:
            font = _font(self.cfg, 34)
            for i, line in enumerate(_wrap(ctx["original"], font, 1060, 6)):
                draw.text((68, 193 + i * 48), line, font=font, fill=palette["ink"])
            footer_label = "ORIGINAL EXCERPT"
        draw.text((64, 524), footer_label + " · " + ctx["date"], font=_font(self.cfg, 16), fill=palette["muted"])
        source_url = _wrap(ctx["url"], _font(self.cfg, 14), 800, 1)
        if source_url:
            draw.text((64, 552), source_url[0], font=_font(self.cfg, 14), fill=palette["muted"])
        brand = _wrap(ctx["brand"], _font(self.cfg, 26), 250, 1)[0]
        draw.text((1130, 535), brand, anchor="ra", font=_font(self.cfg, 26), fill=palette["accent"])
        image.save(path, "PNG")
