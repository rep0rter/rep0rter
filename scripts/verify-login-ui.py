#!/usr/bin/env python3
"""Check a local login-enabled preview; never submits a Google login.

Requires Playwright + Chromium and a populated four-language local feed.
python scripts/verify-login-ui.py --url http://127.0.0.1:8766
"""
import argparse
import json
import time
from pathlib import Path
from playwright.sync_api import sync_playwright
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--url', default='http://127.0.0.1:8766')
parser.add_argument('--browser', type=Path)
parser.add_argument('--screenshots', type=Path)
args = parser.parse_args()
from urllib.parse import urlsplit
if urlsplit(args.url).hostname not in ('localhost', '127.0.0.1', '::1'):
    parser.error('Use a local preview, not production.')
base = args.url.rstrip('/')
mac = Path.home() / 'Library/Caches/ms-playwright/chromium-1234/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing'
executable = args.browser or (mac if mac.exists() else None)
if args.screenshots:
    args.screenshots.mkdir(parents=True, exist_ok=True)
results=[]
with sync_playwright() as p:
 browser=p.chromium.launch(**({'executable_path': str(executable)} if executable else {}))
 for width,height in [(390,844),(768,1024),(1440,1000),(320,740)]:
  for mode in ['light','dark']:
   ctx=browser.new_context(viewport={'width':width,'height':height},color_scheme=mode)
   page=ctx.new_page(); errors=[]
   page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(base+'/index.html?lang=ZH');page.wait_for_function("document.documentElement.lang==='zh-TW'")
   page.locator('[data-sign-in]').click()
   page.locator('.login-dialog [data-login-form]').wait_for()
   page.wait_for_timeout(350)
   assert page.locator('.login-dialog').evaluate('(e)=>e.open')
   assert page.locator('.login-dialog h1').inner_text()=='你的社群，下一則故事。'
   assert page.locator('.login-dialog input[name=return_to]').input_value()=='/index.html?lang=ZH'
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
   box=page.locator('.login-dialog').bounding_box()
   assert box['x']>=0 and box['y']>=0 and box['width']<=width
   if args.screenshots and width in (390,1440): page.screenshot(path=str(args.screenshots / f'login-{width}-{mode}.png'))
   # Native modal keeps keyboard focus inside the card.
   for _ in range(8):
    page.keyboard.press('Tab')
    assert page.evaluate("document.activeElement===document.body || Boolean(document.activeElement.closest('.login-dialog'))")
   page.keyboard.press('Escape');page.wait_for_function("!document.querySelector('.login-dialog')")
   assert page.locator('[data-sign-in]').evaluate('(e)=>e===document.activeElement')
   # Native dialog may move focus to browser chrome, never to background links.
   # Click sticky controls by viewport coordinates to avoid automation scrolling.
   page.evaluate('scrollTo({top:650,behavior:"instant"})');page.wait_for_timeout(80)
   before=page.evaluate('scrollY')
   trigger_box=page.locator('[data-sign-in]').bounding_box()
   page.mouse.click(trigger_box['x']+trigger_box['width']/2,trigger_box['y']+trigger_box['height']/2)
   page.locator('.login-dialog [data-login-form]').wait_for()
   assert abs(page.evaluate('scrollY')-before)<2
   page.mouse.wheel(0,400);page.wait_for_timeout(80)
   assert abs(page.evaluate('scrollY')-before)<2
   page.keyboard.press('Escape');page.wait_for_function("!document.querySelector('.login-dialog')")
   assert abs(page.evaluate('scrollY')-before)<2
   # Existing picture overlay and edition controls still work after closing login.
   picture=page.locator('[data-image-view]').first
   picture.scroll_into_view_if_needed();picture.click()
   page.wait_for_function("document.querySelector('#image-viewer').open")
   page.keyboard.press('Escape');page.wait_for_function("!document.querySelector('#image-viewer').open")
   page.evaluate("window.Rep0rterLanguage.change('ja')")
   page.wait_for_function("document.documentElement.lang==='ja'")
   page.locator('[data-sign-in]').click();page.locator('.login-google').wait_for()
   assert page.locator('.login-dialog h1').inner_text()=='あなたのコミュニティ、次のストーリー。'
   page.keyboard.press('Escape');page.wait_for_function("!document.querySelector('.login-dialog')")
   # Standalone page applies stored preference before CSS and shares its card.
   page.goto(base+'/auth/sign-in?lang=ZH')
   assert page.locator('html').get_attribute('data-theme')==mode
   page.locator('[data-theme-trigger]').click()
   opposite='dark' if mode=='light' else 'light'
   page.locator(f'[data-theme-choice={opposite}]').click()
   page.wait_for_function(f"document.documentElement.dataset.theme==='{opposite}'")
   page.reload()
   assert page.locator('html').get_attribute('data-theme')==opposite
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
   assert not errors,errors
   results.append({'viewport':width,'theme':mode,'modal_keyboard_scroll_theme':'pass'})
   ctx.close()
 # Slow initial locale fetch must settle before restoring OAuth return scroll.
 ctx=browser.new_context(viewport={'width':390,'height':844})
 page=ctx.new_page();page.goto(base+'/index.html?lang=ZH&q=')
 page.wait_for_function("document.documentElement.lang==='zh-TW'")
 page.evaluate('scrollTo({top:1700,behavior:"instant"})');page.wait_for_timeout(100)
 y=page.evaluate('scrollY')
 page.evaluate("sessionStorage.setItem('rep0rter-login-return',JSON.stringify({url:location.href,y:scrollY,at:Date.now()}))")
 page.route('**/index.zh-TW.html', lambda route:(time.sleep(1),route.continue_()))
 page.reload();page.wait_for_function("document.documentElement.lang==='zh-TW'");page.wait_for_timeout(750)
 assert abs(page.evaluate('scrollY')-y)<2,(y,page.evaluate('scrollY'))
 results.append({'delayed_language_oauth_return':'pass','scrollY':y})
 # Reduced motion and blocked local/session storage remain usable.
 ctx.close();ctx=browser.new_context(reduced_motion='reduce');page=ctx.new_page()
 page.add_init_script("Object.defineProperty(window,'localStorage',{get(){throw new Error('blocked')}});Object.defineProperty(window,'sessionStorage',{get(){throw new Error('blocked')}})")
 page.goto(base+'/index.html?lang=EN');page.locator('[data-sign-in]').click();page.locator('.login-google').wait_for()
 assert page.locator('.login-dialog').evaluate('(e)=>e.getAnimations().length')==0
 page.keyboard.press('Escape');page.wait_for_function("!document.querySelector('.login-dialog')")
 results.append({'reduced_motion_blocked_storage':'pass'})
 # Fetch failure offers retry and a working no-script fallback page.
 page.route('**/auth/sign-in?*',lambda route:route.fulfill(status=503,body='unavailable'))
 page.locator('[data-sign-in]').click();page.locator('.login-status-actions button').wait_for()
 assert page.locator('.login-status-actions a').get_attribute('href').startswith(base+'/auth/sign-in')
 page.unroute('**/auth/sign-in?*');page.locator('.login-status-actions button').click();page.locator('.login-google').wait_for()
 results.append({'failed_request_retry':'pass'})
 ctx.close();ctx=browser.new_context(java_script_enabled=False);page=ctx.new_page();page.goto(base+'/auth/sign-in?lang=ZH')
 assert page.locator('.login-google').is_visible();assert page.locator('[name=csrf]').input_value()
 results.append({'no_javascript':'pass'})
 browser.close()
print(json.dumps(results,ensure_ascii=False,indent=2))
