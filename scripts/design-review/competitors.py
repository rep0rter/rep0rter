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
        "search": "https://themarkup.org/search?q=open+data",
        "search-no-results": "https://themarkup.org/search?q=zzqxzzqx",
        "newsletter": "https://themarkup.org/newsletters",
    },
    "semafor": {
        "home": "https://www.semafor.com/",
        "search": "https://www.semafor.com/search?q=open%20data",
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
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for site, pages in SITES.items():
            for size, options in SIZES.items():
                context = browser.new_context(**options, locale="en-US",
                                              user_agent=None if size == "desktop" else
                                              "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
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
