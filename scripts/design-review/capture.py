#!/usr/bin/env python3
"""Capture every design-review state listed in docs/design-review/states.md.

  python scripts/design-review/capture.py --base http://127.0.0.1:8811 \
      --empty http://127.0.0.1:8815 --disabled http://127.0.0.1:8816 --out /tmp/review/new

Only local previews are accepted. Form states post to the local Flask preview,
never to production. Run against a freshly seeded preview: the final states
publish fictional community posts into that preview database.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

SIZES = {"desktop": dict(viewport={"width": 2560, "height": 1440}, device_scale_factor=1),
         "mobile": dict(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                        is_mobile=True, has_touch=True)}

STORY = {"title": "Neighbourhood repair café publishes its first parts inventory",
         "description": "Volunteers at the Daan repair café catalogued 230 spare parts and published the list as a shared spreadsheet so other repair groups can trade components.",
         "take_part": "Drop by on Sunday afternoons or add parts to the shared sheet.",
         "hashtags": "#repair #opendata", "evidence": "https://example.org/repair-cafe/inventory"}


def fill_story(page, evidence=None):
    page.fill("#project_name", "Daan Repair Café")
    page.fill("#title", STORY["title"])
    page.fill("#description", STORY["description"])
    page.fill("#take_part", STORY["take_part"])
    page.fill("#hashtags", STORY["hashtags"])
    page.fill("#evidence", evidence if evidence is not None else STORY["evidence"])
    page.check("input[name=owner_confirmed]")


def fill_project(page, url="https://github.com/example/repair-inventory"):
    page.fill("#title", "Repair Inventory")
    page.fill("#url", url)
    page.fill("#description", "A shared spreadsheet and small web app that helps repair cafés trade spare parts across neighbourhoods.")
    page.check("input[name=owner_confirmed]")


def fill_news(page, source="https://github.com/example/repair-inventory"):
    page.fill("#title", "Repair Inventory")
    page.fill("#url", "https://example.org/repair-inventory")
    page.fill("#source_url", source)


def no_validate(page):
    page.evaluate("document.querySelectorAll('form').forEach(f => f.noValidate = true)")


def scroll_to(selector):
    # scrollIntoView honours the page's scroll-padding, as a reader's anchor
    # navigation does, so sticky headers never cover the captured target.
    return lambda page: page.evaluate(
        "s => document.querySelector(s).scrollIntoView({block: 'start', behavior: 'instant'})", selector)


def type_search(term):
    def run(page):
        page.locator("#story-search").fill(term)
        page.wait_for_timeout(400)
        page.evaluate("scrollTo({top:0,behavior:'instant'})")
    return run


def open_filters(page):
    page.locator("[data-filters] > summary").click()
    page.wait_for_timeout(700)
    scroll_to("[data-filters]")(page)


def invalid_range(page):
    open_filters(page)
    page.select_option('[data-filter="period"]', "custom")
    page.wait_for_timeout(500)
    page.fill('[data-filter="from"]', "2026-11-20")
    page.fill('[data-filter="to"]', "2026-11-01")
    page.locator('[data-filter="to"]').dispatch_event("change")
    page.wait_for_timeout(500)
    scroll_to("[data-filters]")(page)


def filter_source(page):
    open_filters(page)
    values = page.eval_on_selector_all('[data-filter="source"] option', "o => o.map(x => x.value).filter(Boolean)")
    page.select_option('[data-filter="source"]', values[0])
    page.wait_for_timeout(600)
    scroll_to("[data-filters]")(page)


def open_menu(selector):
    def run(page):
        page.locator(selector).first.click()
        page.wait_for_timeout(400)
    return run


def stall(pattern, then=None, wait=700):
    def run(page):
        page.route(pattern, lambda route: None)
        then(page)
        page.wait_for_timeout(wait)
    return run


def abort(pattern, then, wait=1200):
    def run(page):
        page.route(pattern, lambda route: route.abort())
        then(page)
        page.wait_for_timeout(wait)
    return run


def click(selector, wait=900):
    def run(page):
        page.locator(selector).first.click()
        page.wait_for_timeout(wait)
    return run


def change_language(code):
    return lambda page: page.locator(f'.language-menu summary').first.click() or page.wait_for_timeout(250) or \
        page.locator(f'.language-menu [data-language="{code}"]').first.click()


def first_link(selector):
    def run(page):
        page.goto(page.eval_on_selector(selector, "a => a.href"), wait_until="networkidle")
        page.wait_for_timeout(800)
    return run


def ppt_slide(count):
    def run(page):
        for _ in range(count):
            page.keyboard.press("ArrowRight")
            page.wait_for_timeout(900)
    return run


def submit(action_value, fill, stall_post=False, arm=None, novalidate=False):
    def run(page):
        fill(page)
        if novalidate:
            no_validate(page)
        if arm:
            origin = "{0.scheme}://{0.netloc}".format(urlsplit(page.url))
            page.request.get(origin + arm)
        if stall_post:
            page.route(re.compile(r".*/(write|submit|projects)(\?.*)?$"),
                       lambda route: None if route.request.method == "POST" else route.continue_())
            page.locator(f'button[value="{action_value}"], form button[type=submit]:not([value])').last.click(no_wait_after=True)
            page.wait_for_timeout(600)
            page.raw_screenshot = True
            return
        page.locator(f'button[value="{action_value}"], form button[type=submit]:not([value])').last.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(700)
        if action_value == "preview":
            page.evaluate("scrollTo({top:0,behavior:'instant'})")
    return run


# (id, server, path, setup, options)
STATES = [
    ("home-en", "base", "/", None, {}),
    ("home-en-feed", "base", "/", scroll_to("#news"), {}),
    ("home-en-footer", "base", "/", lambda p: p.evaluate("scrollTo({top: 1e9, behavior: 'instant'})"), {}),
    ("home-zh-TW", "base", "/index.zh-TW.html", None, {}),
    ("home-ja", "base", "/index.ja.html", None, {}),
    ("home-ko", "base", "/index.ko.html", None, {}),
    ("home-en-dark", "base", "/", None, {"color_scheme": "dark"}),
    ("home-en-dark-feed", "base", "/", scroll_to("#news"), {"color_scheme": "dark"}),
    ("home-entrance", "base", "/", None, {"entrance": True}),
    ("home-language-loading", "base", "/", stall("**/index.ja.html", change_language("ja")), {}),
    ("home-language-failed", "base", "/", stall("**/index.ja.html", change_language("ja"), wait=9500), {}),
    ("home-search-results", "base", "/", type_search("map"), {}),
    ("home-search-no-results", "base", "/", type_search("zzqx"), {}),
    ("home-filters-open", "base", "/", open_filters, {}),
    ("home-filters-invalid-range", "base", "/", invalid_range, {}),
    ("home-filter-source", "base", "/", filter_source, {}),
    ("menu-language", "base", "/", open_menu(".language-menu summary"), {}),
    ("menu-appearance", "base", "/", open_menu(".theme-menu summary"), {}),
    ("login-dialog", "base", "/", click("[data-sign-in]", 1200), {}),
    ("login-dialog-loading", "base", "/", stall("**/auth/sign-in?*", click("[data-sign-in]", 0), 900), {}),
    ("login-dialog-error", "base", "/", abort("**/auth/sign-in?*", click("[data-sign-in]", 0), 1500), {}),
    ("home-empty", "empty", "/", None, {}),
    ("story-en", "base", "/posts/1/index.html", None, {}),
    ("story-zh-TW", "base", "/posts/1/index.zh-TW.html", None, {}),
    ("story-en-dark", "base", "/posts/1/index.html", None, {"color_scheme": "dark"}),
    ("story-superseded", "base", "/posts/13/index.html", None, {}),
    ("story-revision-latest", "base", "/posts/14/index.html", scroll_to(".story-card"), {}),
    ("story-translation-pending", "base", "/posts/11/index.html", None, {}),
    ("story-untranslated-ko", "base", "/posts/12/index.ko.html", None, {}),
    ("image-viewer", "base", "/posts/1/index.html", click("[data-image-view]", 1500), {}),
    ("image-viewer-error", "base", "/posts/1/index.html", abort("**/cards/*.png", click("[data-image-view]", 0), 1500), {"no_eager": True}),
    ("source-timeline", "base", "/", first_link(".story-card .channel"), {}),
    ("tag-timeline", "base", "/tags/opendata/index.html", None, {}),
    ("withdrawn", "base", "/posts/9999/index.html", None, {}),
    ("presentation-start", "base", "/ppt", None, {}),
    ("presentation-demo", "base", "/ppt", ppt_slide(2), {}),
    ("sign-in-page", "base", "/auth/sign-in?lang=en", None, {}),
    ("sign-in-zh-TW", "base", "/auth/sign-in?lang=zh-TW", None, {}),
    ("sign-in-error", "base", "/auth/google/callback?lang=en", None, {}),
    ("sign-in-unavailable", "disabled", "/auth/sign-in?lang=en", None, {}),
    ("signed-in-card", "base", "/__preview/sign-in?next=/auth/sign-in%3Flang%3Den", None, {}),
    ("write-empty", "base", "/__preview/sign-in?next=/write%3Flang%3Den", None, {}),
    ("write-ja", "base", "/__preview/sign-in?next=/write%3Flang%3Dja", None, {}),
    ("write-filled", "base", "/__preview/sign-in?next=/write%3Flang%3Den",
     lambda p: (fill_story(p), p.evaluate("scrollTo({top: 1e9, behavior:'instant'})")), {}),
    ("write-preview", "base", "/__preview/sign-in?next=/write%3Flang%3Den", submit("preview", fill_story), {}),
    ("write-error", "base", "/__preview/sign-in?next=/write%3Flang%3Den",
     submit("publish", lambda p: fill_story(p, evidence="not a link")), {}),
    ("write-submitting", "base", "/__preview/sign-in?next=/write%3Flang%3Den", submit("publish", fill_story, stall_post=True), {}),
    ("submit-empty", "base", "/__preview/sign-in?next=/submit%3Flang%3Den", None, {}),
    ("submit-error", "base", "/__preview/sign-in?next=/submit%3Flang%3Den",
     submit("", lambda p: fill_project(p, url="ftp://example.org/files"), novalidate=True), {}),
    ("submit-submitting", "base", "/__preview/sign-in?next=/submit%3Flang%3Den", submit("", fill_project, stall_post=True), {}),
    ("projects-empty", "base", "/__preview/sign-in?next=/projects%3Flang%3Den", None, {}),
    ("projects-preview", "base", "/__preview/sign-in?next=/projects%3Flang%3Den",
     submit("preview", lambda p: (fill_news(p), p.check("input[name=owner_confirmed]"))), {}),
    ("projects-error", "base", "/__preview/sign-in?next=/projects%3Flang%3Den",
     submit("save", lambda p: (fill_news(p, source="https://example.org/not-a-feed"), p.check("input[name=owner_confirmed]"))), {}),
    # Mutating states last: each publishes into the local preview database.
    ("write-saved-build-failed", "base", "/__preview/sign-in?next=/write%3Flang%3Den",
     submit("publish", fill_story, arm="/__preview/fail-next-build"), {}),
    ("write-success", "base", "/__preview/sign-in?next=/write%3Flang%3Den", submit("publish", fill_story), {}),
    ("submit-success", "base", "/__preview/sign-in?next=/submit%3Flang%3Den", submit("", fill_project), {}),
    ("projects-saved", "base", "/__preview/sign-in?next=/projects%3Flang%3Den",
     submit("save", lambda p: (fill_news(p), p.check("input[name=enabled]"), p.check("input[name=owner_confirmed]"))), {}),
]


def capture(servers, out: Path, only=None, redesign=False):
    out.mkdir(parents=True, exist_ok=True)
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for state, server, path, setup, options in STATES:
            if only and state not in only:
                continue
            for size, size_options in SIZES.items():
                context = browser.new_context(**size_options, color_scheme=options.get("color_scheme", "light"),
                                              locale="en-US", timezone_id="Asia/Taipei")
                if not options.get("entrance"):
                    context.add_init_script("try { localStorage.setItem('rep0rter-brand-dismissed', '1') } catch (e) {}")
                if redesign:
                    context.add_init_script("try { localStorage.setItem('rep0rter-redesign', '1') } catch (e) {}")
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                try:
                    page.goto(servers[server] + path, wait_until="networkidle")
                    if options.get("entrance"):
                        page.wait_for_timeout(1100)
                    else:
                        page.wait_for_function("document.documentElement.getAttribute('aria-busy') !== 'true'", timeout=10000)
                        page.wait_for_timeout(900)
                        if not options.get("no_eager"):
                            page.evaluate("document.querySelectorAll('img[loading=lazy]').forEach(i => i.loading = 'eager')")
                            page.wait_for_load_state("networkidle")
                    if setup:
                        setup(page)
                    page.wait_for_timeout(500)
                    target = out / f"{state}--{size}.png"
                    if getattr(page, "raw_screenshot", False):
                        # A pending navigation blocks Playwright's screenshot; ask CDP directly.
                        import base64
                        session = context.new_cdp_session(page)
                        data = session.send("Page.captureScreenshot", {"format": "png"})["data"]
                        target.write_bytes(base64.b64decode(data))
                    else:
                        page.screenshot(path=str(target))
                except Exception as exc:  # keep capturing; report at the end
                    failures.append(f"{state}/{size}: {exc}")
                if errors:
                    failures.append(f"{state}/{size} page errors: {errors}")
                context.close()
        browser.close()
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--empty", required=True)
    parser.add_argument("--disabled", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--redesign", action="store_true", help="turn the New interface (beta) switch on")
    args = parser.parse_args()
    servers = {"base": args.base.rstrip("/"), "empty": args.empty.rstrip("/"), "disabled": args.disabled.rstrip("/")}
    for url in servers.values():
        if urlsplit(url).hostname not in ("127.0.0.1", "localhost", "::1"):
            parser.error("Capture local previews only; never production.")
    failures = capture(servers, args.out, args.only, args.redesign)
    print("\n".join(failures) or "all states captured")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
