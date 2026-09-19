"""The Cloudflare serving layer must keep the URLs and headers Caddy serves today."""

import json
import re
from pathlib import Path

import pytest

from rep0rter.i18n import LANGUAGES, feed_aliases, feed_name, page_aliases, page_name

DEPLOY = Path(__file__).resolve().parent.parent / "deploy" / "cloudflare"
CADDYFILE = Path(__file__).resolve().parent.parent / "Caddyfile"


def wrangler() -> dict:
    text = DEPLOY.joinpath("wrangler.jsonc").read_text(encoding="utf-8")
    # Strip whole-line comments only; "https://" inside values must survive.
    return json.loads(re.sub(r"^\s*//.*$", "", text, flags=re.MULTILINE))


def headers_rules() -> dict[str, dict[str, str]]:
    rules: dict[str, dict[str, str]] = {}
    current = None
    for line in DEPLOY.joinpath("_headers").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith((" ", "\t")):
            current = line.strip()
            rules[current] = {}
        elif line.strip().startswith("!"):
            rules[current][line.strip()[1:].strip()] = None
        else:
            name, _, value = line.strip().partition(":")
            rules[current][name.strip()] = value.strip()
    return rules


def test_html_handling_never_redirects_explicit_html_urls():
    """Any other mode 307s /index.ja.html away and breaks every shared link."""
    assert wrangler()["assets"]["html_handling"] == "none"


@pytest.mark.parametrize("language", list(LANGUAGES))
def test_every_edition_and_feed_name_is_a_plain_file(language):
    """html_handling "none" serves explicit files, so every published name needs one."""
    for name in (page_name(language), *page_aliases(language),
                 feed_name(language), *feed_aliases(language)):
        assert name.endswith((".html", ".xml")) and "/" not in name


def test_every_feed_keeps_its_rss_content_type():
    rules = headers_rules()
    for language in LANGUAGES:
        for feed in (feed_name(language), *feed_aliases(language)):
            assert rules[f"/{feed}"]["Content-Type"] == "application/rss+xml; charset=utf-8"


def test_cache_headers_match_the_caddyfile():
    rules = headers_rules()
    assert rules["/*"]["Cache-Control"] == "no-cache"
    assert rules["/assets/*"]["Cache-Control"] == "public, max-age=31536000, immutable"


def test_specific_rules_drop_the_inherited_header_before_setting_it():
    """_headers appends by default: without `!` an asset gets "no-cache, public, ..."."""
    text = DEPLOY.joinpath("_headers").read_text(encoding="utf-8")
    for block in text.split("\n\n"):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        pattern = next((line for line in lines if line.startswith("/")), None)
        if pattern in (None, "/*") or pattern.startswith("#"):
            continue
        set_here = {line.split(":")[0].strip() for line in lines[1:] if not line.startswith("!")}
        dropped = {line[1:].strip() for line in lines[1:] if line.startswith("!")}
        assert set_here <= dropped, f"{pattern} sets {set_here - dropped} without dropping it first"


def test_worker_forwards_exactly_the_paths_caddy_reverse_proxies():
    """Drift guard: the Caddyfile matcher is still the source of truth for step 1."""
    matcher = re.search(r"@accounts path (.+)", CADDYFILE.read_text(encoding="utf-8"))
    worker = DEPLOY.joinpath("worker.js").read_text(encoding="utf-8")
    for path in matcher.group(1).split():
        assert path.rstrip("*") in worker, f"{path} is proxied by Caddy but not by worker.js"
