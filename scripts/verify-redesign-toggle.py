#!/usr/bin/env python3
"""Check reader and account pages with the interim new-interface flag off and on.

Requires Playwright + Chromium and a local preview from
scripts/design-review/preview.py (fictional data). Never contacts production.
  python scripts/verify-redesign-toggle.py --url http://127.0.0.1:8811
"""
import argparse
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

PAGES = ['/', '/index.ja.html', '/posts/1/index.html', '/posts/14/index.html', '/posts/9999/index.html',
         '/tags/opendata/index.html', '/auth/sign-in?lang=en', '/__preview/sign-in?next=/write%3Flang%3Den']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8811')
    args = parser.parse_args()
    if urlsplit(args.url).hostname not in ('localhost', '127.0.0.1', '::1'):
        parser.error('Use a local preview, not production.')
    base = args.url.rstrip('/')
    checks = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for redesign in (False, True):
            for width, height in ((390, 844), (1440, 900), (2560, 1440)):
                context = browser.new_context(viewport={'width': width, 'height': height})
                context.add_init_script("try { localStorage.setItem('rep0rter-brand-dismissed', '1') } catch (e) {}")
                if redesign:
                    # Seed once per tab so turning the switch off survives a reload.
                    context.add_init_script("try { if (!sessionStorage.getItem('seeded')) { localStorage.setItem('rep0rter-redesign', '1'); sessionStorage.setItem('seeded', '1'); } } catch (e) {}")
                page = context.new_page()
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                for path in PAGES:
                    page.goto(base + path, wait_until='networkidle')
                    state = page.evaluate("""() => ({
                      on: document.documentElement.hasAttribute('data-redesign'),
                      overflow: document.documentElement.scrollWidth > innerWidth + 1,
                      toggle: document.querySelectorAll('[data-redesign-toggle]').length,
                      h1: document.querySelectorAll('h1').length})""")
                    assert state['on'] is redesign, (path, state)
                    assert not state['overflow'], (path, width, redesign)
                    assert state['h1'] == 1, (path, state)
                    assert state['toggle'] == 0, (path, state)
                # Core interactions behave the same in both interfaces.
                page.goto(base + '/', wait_until='networkidle')
                page.fill('#story-search', 'map')
                page.wait_for_timeout(200)
                assert page.locator('article[data-search]:not([hidden])').count() >= 1
                page.locator('[data-filters] > summary').click()
                page.select_option('[data-filter="period"]', 'custom')
                page.fill('[data-filter="from"]', '2026-12-20')
                page.fill('[data-filter="to"]', '2026-12-01')
                page.locator('[data-filter="to"]').dispatch_event('change')
                assert page.locator('[data-filter-error]').is_visible()
                assert page.locator('[data-invalid-range]').is_visible()
                assert page.locator('[data-no-results]').is_hidden()
                page.goto(base + '/posts/1/index.html', wait_until='networkidle')
                page.locator('[data-image-view]').first.click()
                page.wait_for_timeout(500)
                assert page.locator('#image-viewer').evaluate('d => d.open')
                page.keyboard.press('Escape')
                assert not errors, errors
                checks.append(f"{'on ' if redesign else 'off'} {width}x{height}: {len(PAGES)} pages, search, filters, viewer")
                context.close()
        browser.close()
    print('\n'.join(checks))


if __name__ == '__main__':
    main()
