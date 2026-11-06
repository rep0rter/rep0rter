#!/usr/bin/env python3
"""Capture read-only reference screenshots of comparable news sites.

  python scripts/design-review/competitors.py --out /tmp/review/reference

Only loads public pages and types into search boxes; never submits a form
that would contact a person or create an account.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from capture import SIZES

# brief v2 reference set: sites rep0rter's civic-tech readers actually compare
# it with, none behind bot or connection-verification walls.
SITES_V2 = {
    "themarkup": {
        "home": "https://themarkup.org/",
        "article": "https://themarkup.org/privacy/2026/10/15/mychart-health-data-opt-out-epic-notary-hipaa",
        "search": "https://themarkup.org/search?s=open+data",
        "search-no-results": "https://themarkup.org/search?s=zzqxzzqx",
        "newsletter": "https://themarkup.org/newsletters",
    },
    "civictechguide": {
        "home": "https://civictech.guide/",
        "article": "https://civictech.guide/projects/open-facilitation-library",
        "search": "https://civictech.guide/search?q=open%20data",
        "search-no-results": "https://civictech.guide/search?q=zzqxzzqx",
        "topic": "https://app.civictech.guide/category/data/r/recGnL44NUs4wOQk9",
        "submit-form": "https://app.civictech.guide/add",
    },
    "code4japan": {
        "home": "https://www.code4japan.org/",
        "news": "https://www.code4japan.org/news",
        "news-filtered": "https://www.code4japan.org/news/announcement",
        "article": "https://www.code4japan.org/news/civic-tech-fun-fun-report-2026-10",
        "language": "https://www.code4japan.org/en",
    },
}

# brief v1 reference set (rounds 1–9).
SITES = {
    "restofworld": {
        "home": "https://restofworld.org/",
        "search": "https://restofworld.org/?s=open+data",
        "search-no-results": "https://restofworld.org/?s=zzqxzzqx",
        "language": "https://restofworld.org/zh/",
        "newsletter": "https://restofworld.org/newsletters/",
        "article": "https://restofworld.org/2026/ai-tokenizer-tax-non-english-languages-cost-inequity/",
    },
    "themarkup": {
        "home": "https://themarkup.org/",
        "search": "https://themarkup.org/search?s=open+data",
        "search-no-results": "https://themarkup.org/search?s=zzqxzzqx",
        "newsletter": "https://themarkup.org/newsletters",
    },
    "semafor": {
        "home": "https://www.semafor.com/",
        "newsletter": "https://www.semafor.com/newsletters",
        "article": "https://www.semafor.com/article/11/04/2026/trumps-politics-meet-reality",
    },
}


def first_article(page):
    for selector in ("main article a[href]", "article a[href]", "main h2 a[href]", "main h3 a[href]"):
        links = page.eval_on_selector_all(selector, "as => as.map(a => a.href).filter(h => /\\/20\\d\\d\\/|\\/\\d{4}\\/\\d{2}\\/|\\/article/.test(h))")
        if links:
            return links[0]
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only", nargs="*", help="site/state pairs, e.g. semafor/article")
    parser.add_argument("--v2", action="store_true", help="capture the brief v2 reference set")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for site, pages in (SITES_V2 if args.v2 else SITES).items():
            for size, options in SIZES.items():
                # Playwright's default UA; a spoofed phone UA triggers bot walls.
                context = browser.new_context(**options, locale="en-US")
                page = context.new_page()
                article = None
                for state, url in pages.items():
                    if args.only and f"{site}/{state}" not in args.only:
                        continue
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(4000)
                        page.screenshot(path=str(args.out / f"{site}--{state}--{size}.png"))
                        if state == "home":
                            article = first_article(page)
                            page.evaluate("scrollTo({top: innerHeight * 1.5, behavior: 'instant'})")
                            page.wait_for_timeout(1500)
                            page.screenshot(path=str(args.out / f"{site}--home-feed--{size}.png"))
                            page.evaluate("scrollTo({top: document.body.scrollHeight, behavior: 'instant'})")
                            page.wait_for_timeout(1500)
                            page.screenshot(path=str(args.out / f"{site}--home-footer--{size}.png"))
                    except Exception as exc:
                        failures.append(f"{site}/{state}/{size}: {exc}")
                if "article" in pages or args.only:
                    pass
                elif article:
                    try:
                        page.goto(article, wait_until="domcontentloaded", timeout=45000)
                        page.wait_for_timeout(4000)
                        page.screenshot(path=str(args.out / f"{site}--article--{size}.png"))
                    except Exception as exc:
                        failures.append(f"{site}/article/{size}: {exc}")
                else:
                    failures.append(f"{site}/article/{size}: no article link found")
                context.close()
        browser.close()
    print("\n".join(failures) or "all reference pages captured")


if __name__ == "__main__":
    sys.exit(main())
