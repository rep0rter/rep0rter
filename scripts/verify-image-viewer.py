#!/usr/bin/env python3
"""Optional isolated Chromium checks against a running local preview.

python scripts/verify-image-viewer.py --url http://127.0.0.1:8765/index.html?lang=ZH
Requires Playwright + Chromium. Does not alter site data or production services.
"""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8765/index.html?lang=ZH')
    parser.add_argument('--browser', type=Path)
    args = parser.parse_args()
    mac = Path.home() / 'Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'
    executable = args.browser or (mac if mac.exists() else None)
    results, errors = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(**({'executable_path': str(executable)} if executable else {}))
        for width, height in [(320, 568), (390, 844), (667, 375), (768, 1024), (1440, 900)]:
            for theme in ['light', 'dark']:
                context = browser.new_context(viewport={'width': width, 'height': height}, color_scheme=theme,
                                              has_touch=width < 900)
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(args.url, wait_until='networkidle')
                button = page.locator('[data-image-view]').first
                button.scroll_into_view_if_needed()
                page.wait_for_timeout(100)
                before = page.evaluate('({y:scrollY, url:location.href, width:document.documentElement.clientWidth})')
                button.click()
                page.wait_for_function("document.querySelector('#image-viewer').dataset.imageState === 'ready'")
                page.wait_for_timeout(600)
                geometry = page.locator('#image-viewer').evaluate('''e => ({
                  rect:e.getBoundingClientRect().toJSON(), scroll:e.scrollHeight, client:e.clientHeight,
                  background:getComputedStyle(e).backgroundColor, color:getComputedStyle(e).color,
                  horizontal:document.documentElement.scrollWidth > innerWidth,
                  focus:document.activeElement.matches('[data-viewer-close]')
                })''')
                assert not geometry['horizontal'], (width, theme, geometry)
                assert geometry['rect']['top'] >= 0 and geometry['rect']['bottom'] <= height + 1, geometry
                assert geometry['focus']
                assert geometry['scroll'] <= geometry['client'] + 1, (width, theme, geometry)
                assert page.locator('[data-viewer-image]').evaluate('e=>e.getBoundingClientRect().width > e.getBoundingClientRect().height')
                assert page.locator('[data-viewer-download]').get_attribute('href') == button.get_attribute('data-image-' + theme)
                assert button.locator('img').evaluate('e=>e.currentSrc').endswith(button.get_attribute('data-image-' + theme))
                assert page.locator('[data-viewer-image]').get_attribute('src') == button.get_attribute('data-image-' + theme)
                assert page.evaluate('scrollY') == before['y'], (width, 'open scroll jump')
                page.mouse.wheel(0, 600)
                page.wait_for_timeout(120)
                assert page.evaluate('scrollY') == before['y'], 'background scroll'
                page.keyboard.press('Tab')
                assert page.locator('#image-viewer').evaluate('e=>e.contains(document.activeElement)')
                page.keyboard.press('Escape')
                page.wait_for_function("!document.querySelector('#image-viewer').open")
                assert button.evaluate('e=>e === document.activeElement')
                after = page.evaluate('({y:scrollY, url:location.href, width:document.documentElement.clientWidth})')
                assert after == before, (width, theme, before, after)
                # No drift after repeated opens; backdrop hit testing also closes.
                for _ in range(3):
                    button.click()
                    page.wait_for_timeout(90)
                    page.keyboard.press('Escape')
                    page.wait_for_function("!document.querySelector('#image-viewer').open")
                button.click()
                page.wait_for_timeout(500)
                page.mouse.click(2, 2)
                page.wait_for_function("!document.querySelector('#image-viewer').open")
                assert page.evaluate('scrollY') == before['y']
                # Pointer tilt returns to rest. Touch does not cancel native pan/pinch.
                button.click()
                page.wait_for_timeout(500)
                stage = page.locator('[data-viewer-stage]')
                stage.dispatch_event('pointerdown', {'pointerType': 'touch', 'isPrimary': True, 'pointerId': 2, 'clientX': 100, 'clientY': 100})
                stage.dispatch_event('pointermove', {'pointerType': 'touch', 'isPrimary': True, 'pointerId': 2, 'clientX': 120, 'clientY': 120})
                page.wait_for_timeout(150)
                assert page.locator('[data-viewer-card]').evaluate("e=>getComputedStyle(e).transform !== 'matrix(1, 0, 0, 1, 0, 0)'")
                assert stage.evaluate("e=>getComputedStyle(e).touchAction") == 'pan-y pinch-zoom'
                assert stage.evaluate("e=>e.dispatchEvent(new Event('touchmove',{bubbles:true,cancelable:true}))"), 'touch gesture cancelled'
                stage.dispatch_event('pointercancel', {'pointerType': 'touch', 'pointerId': 2})
                page.wait_for_timeout(650)
                assert abs(float(page.locator('[data-viewer-card]').evaluate("e=>e.style.getPropertyValue('--card-light')"))) < .001
                # Live theme changes update viewer material as well as page chrome.
                page.evaluate("document.documentElement.dataset.theme = 'dark'" if theme == 'light' else "document.documentElement.dataset.theme = 'light'")
                page.wait_for_timeout(350)
                assert page.locator('#image-viewer').evaluate('e=>getComputedStyle(e).backgroundColor') != geometry['background']
                page.wait_for_function("document.querySelector('#image-viewer').dataset.imageState === 'ready'")
                other = 'dark' if theme == 'light' else 'light'
                assert page.locator('[data-viewer-image]').get_attribute('src') == button.get_attribute('data-image-' + other)
                assert page.locator('[data-viewer-download]').get_attribute('href') == button.get_attribute('data-image-' + other)
                # Long captions remain reachable on the smallest landscape screen.
                page.locator('[data-viewer-caption]').evaluate("e=>e.textContent='Long caption 測試 '.repeat(150)")
                page.locator('#image-viewer').hover()
                page.mouse.wheel(0, 500)
                page.wait_for_timeout(200)
                assert page.locator('#image-viewer').evaluate('e=>e.scrollTop > 0'), 'modal cannot scroll'
                assert page.evaluate('scrollY') == before['y']
                results.append(f'{width}x{height} {theme}: geometry, focus, close/reopen, scroll, touch, theme OK')
                context.close()

        context = browser.new_context(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until='networkidle')
        button = page.locator('[data-image-view]').first
        button.click()
        assert page.locator('[data-viewer-card]').evaluate("e=>getComputedStyle(e).transform") == 'none'
        assert page.locator('.image-viewer-foil').evaluate("e=>getComputedStyle(e).display") == 'none'
        page.keyboard.press('Escape')
        assert not page.locator('#image-viewer').evaluate('e=>e.open')
        # Delayed and failed requests keep the same frame geometry and expose status.
        page.route('**/viewer-broken.png', lambda route: route.abort())
        button.evaluate("e=>e.dataset.imageSrc='/viewer-broken.png'")
        button.click()
        page.wait_for_function("document.querySelector('#image-viewer').dataset.imageState === 'error'")
        assert page.locator('[data-viewer-status]').inner_text()
        assert not page.locator('[data-viewer-download]').is_visible()
        page.keyboard.press('Escape')
        original = button.locator('img').get_attribute('src')
        button.evaluate('(e,src)=>e.dataset.imageSrc=src', original)
        button.click()
        page.wait_for_function("document.querySelector('#image-viewer').dataset.imageState === 'ready'")
        assert page.locator('[data-viewer-status]').inner_text() == ''
        page.keyboard.press('Escape')
        # Rebind after the site's same-document locale navigation.
        for locale in ['en', 'ja', 'ko', 'zh-TW']:
            page.locator('.language-menu > summary').click()
            page.locator('.language-menu [data-language="' + locale + '"]').click()
            page.wait_for_function('(locale)=>document.documentElement.lang === locale', arg=locale)
            page.locator('[data-image-view]').first.click()
            page.wait_for_function("document.querySelector('#image-viewer').dataset.imageState === 'ready'")
            page.keyboard.press('Escape')
        results.append('Reduced motion, failed image/recovery, four locales OK')
        context.close()
        browser.close()
    assert not errors, errors
    print(json.dumps({'checks': results, 'console_errors': errors}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
