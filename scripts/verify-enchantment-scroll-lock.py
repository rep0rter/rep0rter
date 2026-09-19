#!/usr/bin/env python3
"""Optional Chromium checks for theme/text animation scrolling locks.

Run with the optional Playwright package and Chromium installed:
    python scripts/verify-enchantment-scroll-lock.py --url http://127.0.0.1:8765/

Uses an existing local preview and isolated fixture pages. Fixtures load the
working-tree assets; no preview files or production services are changed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'rep0rter/templates'
FIXTURE = '''<!doctype html><html lang="zh-TW"><head><meta charset="utf-8"><style>
body { margin:0; background:#f4f1fa; color:#332f3a; font:20px/1.5 sans-serif; }
header { position:fixed; inset:0 0 auto; height:48px; background:#eee; z-index:10; }
main { padding:80px 24px; min-height:2600px; } h2,p { margin:0 0 12px; }
#hidden { display:none; } #far { margin-top:1200px; }
</style></head><body><header><details class="preference-menu" data-theme-controls>
<summary data-theme-trigger data-theme-label="Theme" data-theme-light="Light" data-theme-dark="Dark" data-theme-system="System">Theme</summary>
<button data-theme-choice="light">Light</button><button data-theme-choice="dark">Dark</button><button data-theme-choice="system">System</button>
</details></header><main><h2 id="heading">目前畫面 Visible</h2><p id="multilingual">繁中 English 日本語 한국어 é 👩🏽‍💻 👨‍👩‍👧‍👦</p><p id="hidden">HIDDEN_SECRET</p><details><summary>Details</summary><p>CLOSED_SECRET</p></details><p id="far">OFFSCREEN_SECRET</p></main></body></html>'''


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8765/index.html?lang=ZH')
    parser.add_argument('--browser', type=Path)
    args = parser.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise SystemExit('Install the optional playwright package and Chromium to run this check.') from error
    default_browser = Path.home() / 'Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'
    executable = args.browser or (default_browser if default_browser.exists() else None)
    results = []
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, **({'executable_path': str(executable)} if executable else {}))
        context = browser.new_context(viewport={'width': 900, 'height': 600}, reduced_motion='no-preference')
        context.on('page', lambda page: page.on('pageerror', lambda error: errors.append(str(error))))

        def load_preview(current):
            current.goto(args.url, wait_until='networkidle')
            current.wait_for_function('!!window.Rep0rterEnchantment && !!window.Rep0rterScrollLock')
            current.wait_for_timeout(1100)

        def fixture():
            current = context.new_page()
            current.set_content(FIXTURE)
            for name in ('theme-transition.css', 'enchantment.css'):
                current.add_style_tag(path=str(ASSETS / name))
            for name in ('theme.js', 'enchantment.js'):
                current.add_script_tag(path=str(ASSETS / name))
            current.evaluate("document.dispatchEvent(new CustomEvent('rep0rter:language-applied'))")
            current.evaluate('async () => { await document.fonts.ready; await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); }')
            return current

        page = context.new_page()
        load_preview(page)
        page.evaluate("window.scrollTo({top:400,behavior:'instant'})")
        page.wait_for_timeout(100)
        initial = page.evaluate('({y:scrollY, width:document.documentElement.clientWidth, bodyWidth:document.body.getBoundingClientRect().width})')
        started = page.evaluate("document.querySelector('[data-theme-trigger]').focus({preventScroll:true}); window.Rep0rterEnchantment.playAll(); ({locked:Rep0rterScrollLock.isLocked(),cells:document.querySelectorAll('.enchantment-cell').length,hidden:document.hidden,selection:getSelection().toString()})")
        check(started['locked'], f'Text animation should acquire scroll lock: {started!r}')
        page.mouse.move(600, 450)
        page.mouse.wheel(0, 300)
        page.keyboard.press('PageDown')
        page.wait_for_timeout(80)
        locked = page.evaluate('({y:scrollY, width:document.documentElement.clientWidth, bodyWidth:document.body.getBoundingClientRect().width, locked:Rep0rterScrollLock.isLocked(), cells:document.querySelectorAll(".enchantment-cell").length})')
        check(locked['locked'] and locked['cells'] > 0, 'Focused theme control PageDown should block scrolling without cancelling animation')
        check(locked['y'] == initial['y'], f'Wheel/PageDown moved locked page: {initial} -> {locked}')
        check(locked['width'] == initial['width'] and locked['bodyWidth'] == initial['bodyWidth'], 'Lock must not change layout width')
        page.wait_for_timeout(1100)
        check(page.evaluate('!Rep0rterScrollLock.isLocked() && !document.documentElement.hasAttribute("data-animation-scroll-lock")'), 'Completed text animation leaked scroll lock')
        check(page.evaluate('scrollY') == initial['y'], 'Unlock changed reading position')
        page.mouse.wheel(0, 250)
        page.wait_for_timeout(200)
        check(page.evaluate('scrollY') > initial['y'], 'Wheel scrolling did not resume after animation')
        results.append('real preview wheel/PageDown blocked during text animation, stable position/layout, wheel restored')

        page.evaluate("""() => {
          const current = document.documentElement.dataset.theme;
          document.querySelector(`[data-theme-choice="${current === 'dark' ? 'light' : 'dark'}"]`).click();
        }""")
        page.wait_for_function('document.documentElement.dataset.themeTransition === "reveal" && !!document.querySelector(".enchantment-layer")')
        page.wait_for_function('!document.documentElement.hasAttribute("data-theme-transition")')
        overlap = page.evaluate('({locked:Rep0rterScrollLock.isLocked(), cells:document.querySelectorAll(".enchantment-cell").length})')
        check(overlap['locked'] and overlap['cells'] > 0, 'Theme reveal ending should leave the active text animation lock intact')
        page.wait_for_timeout(1400)
        check(page.evaluate('!Rep0rterScrollLock.isLocked()'), 'Combined theme/text animation leaked lock')
        page.evaluate("for (const mode of ['dark','light','dark','light']) document.querySelector(`[data-theme-choice=\"${mode}\"]`).click()")
        page.wait_for_timeout(1800)
        check(page.evaluate('document.documentElement.dataset.theme === "light" && !Rep0rterScrollLock.isLocked()'), 'Rapid theme changes should settle to latest choice and release lock')
        for reduced, supported in [(True, True), (False, False)]:
            page.emulate_media(reduced_motion='reduce' if reduced else 'no-preference')
            if not supported:
                page.evaluate('document.startViewTransition = undefined')
            page.evaluate("const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; document.querySelector(`[data-theme-choice=\"${next}\"]`).click()")
            check(page.evaluate('!Rep0rterScrollLock.isLocked() && !document.querySelector(".enchantment-layer")'), 'Reduced motion/API fallback should not leave a lock')
        results.append('layered reveal/text ownership, rapid theme replacement, reduced motion and unsupported API')
        page.close()

        page = fixture()
        original = page.locator('#multilingual').inner_html()
        snapshot = page.evaluate('window.Rep0rterEnchantment.playAll(); ({letters:[...document.querySelectorAll(".enchantment-letter")].map(el=>el.textContent), locked:Rep0rterScrollLock.isLocked(), hidden:document.querySelector(".enchantment-layer")?.getAttribute("aria-hidden")})')
        text = ''.join(snapshot['letters'])
        check(snapshot['locked'] and snapshot['hidden'] == 'true', 'Decorative layer should be locked and aria-hidden')
        check('HIDDEN_SECRET' not in text and 'CLOSED_SECRET' not in text and 'OFFSCREEN_SECRET' not in text, 'Nonvisible text should not animate')
        check(all(glyph in snapshot['letters'] for glyph in ['é', '👩🏽‍💻', '👨‍👩‍👧‍👦']), 'Grapheme cluster split')
        check(page.locator('#multilingual').inner_html() == original, 'Animation modified source text DOM')
        page.evaluate("const range=document.createRange();range.selectNodeContents(document.querySelector('#multilingual'));getSelection().removeAllRanges();getSelection().addRange(range)")
        page.wait_for_timeout(50)
        check(page.evaluate('!Rep0rterScrollLock.isLocked() && !document.querySelector(".enchantment-layer")'), 'Selection should cancel and unlock')
        check(page.evaluate('getSelection().toString()') == page.locator('#multilingual').text_content(), 'Selected/copied source text changed')
        page.evaluate('getSelection().removeAllRanges()')
        page.wait_for_timeout(50)
        repeat = page.evaluate('for(let i=0;i<5;i++) Rep0rterEnchantment.playAll(); ({layers:document.querySelectorAll(".enchantment-layer").length,locked:Rep0rterScrollLock.isLocked()})')
        check(repeat['layers'] == 1 and repeat['locked'], 'Rapid repetition should leave one active animation')
        page.wait_for_timeout(1100)
        check(page.evaluate('!Rep0rterScrollLock.isLocked() && !CSS.highlights.has("rep0rter-enchantment")'), 'Rapid repetition leaked lock/highlight')
        page.evaluate("Rep0rterEnchantment.play(document.querySelector('main'));document.querySelector('main').remove()")
        page.wait_for_timeout(100)
        check(page.evaluate('!Rep0rterScrollLock.isLocked()'), 'Removed target leaked scroll lock')
        modal = page.evaluate("const dialog=document.createElement('dialog');dialog.textContent='Modal';document.body.append(dialog);dialog.showModal();Rep0rterEnchantment.playAll();({locked:Rep0rterScrollLock.isLocked(),cells:document.querySelectorAll('.enchantment-cell').length})")
        check(not modal['locked'] and modal['cells'] == 0, 'Modal should suppress background text animation without leaking lock')
        results.append('original visible glyphs/graphemes, accessible source/selection, rapid replay, removal, modal and cleanup')
        page.close()

        mobile = browser.new_context(viewport={'width':390, 'height':844}, is_mobile=True, has_touch=True, reduced_motion='no-preference')
        mobile_page = mobile.new_page()
        mobile_page.on('pageerror', lambda error: errors.append(str(error)))
        load_preview(mobile_page)
        mobile_page.evaluate("window.scrollTo({top:400,behavior:'instant'})")
        mobile_page.wait_for_timeout(100)
        before_touch = mobile_page.evaluate('scrollY')
        cdp = mobile.new_cdp_session(mobile_page)
        mobile_page.evaluate('Rep0rterEnchantment.playAll(); window.touchStarted = performance.now()')
        cdp.send('Input.dispatchTouchEvent', {'type':'touchStart','touchPoints':[{'x':220,'y':650}]})
        for y in (600,550,500,450):
            cdp.send('Input.dispatchTouchEvent', {'type':'touchMove','touchPoints':[{'x':220,'y':y}]})
        cdp.send('Input.dispatchTouchEvent', {'type':'touchEnd','touchPoints':[]})
        mobile_page.wait_for_timeout(80)
        touch = mobile_page.evaluate('({y:scrollY,locked:Rep0rterScrollLock.isLocked(),cells:document.querySelectorAll(".enchantment-cell").length,elapsed:performance.now()-touchStarted})')
        check(touch['y'] == before_touch, f'Touch pan moved locked page: {before_touch} -> {touch}')
        check(touch['locked'] and touch['cells'] > 0, 'Touch pan must not cancel text animation')
        mobile_page.wait_for_timeout(1200)
        check(mobile_page.evaluate('!Rep0rterScrollLock.isLocked()'), 'Touch path leaked lock after completion')
        results.append('mobile emulation real CDP touch pan blocked without cancelling and unlock after completion')
        check(not errors, f'Browser errors: {errors}')
        browser.close()
    print(json.dumps({'passed':len(results), 'checks':results},ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
