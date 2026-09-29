#!/usr/bin/env python3
"""Check reader composition, source-date ordering and stalled edition recovery.

Requires Playwright + Chromium and a populated four-language local preview.
Does not change published data or contact production services.
"""

import argparse
import json
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8778/index.html?lang=ZH')
    args = parser.parse_args()
    if urlsplit(args.url).hostname not in ('localhost', '127.0.0.1', '::1'):
        parser.error('Use a local preview, not production.')
    checks, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for width, height in [(390, 844), (1440, 900)]:
            context = browser.new_context(viewport={'width': width, 'height': height}, reduced_motion='reduce')
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(args.url, wait_until='networkidle')
            page.wait_for_function("document.documentElement.getAttribute('aria-busy') !== 'true'")
            results = page.evaluate("""() => {
              const input = document.querySelector('#story-search');
              const visible = () => [...document.querySelectorAll('article[data-search]:not([hidden])')].map(article => article.id);
              const term = document.querySelector('article[data-search] h3').textContent.trim();
              input.value = term;
              input.dispatchEvent(new InputEvent('input', {bubbles:true}));
              const before = visible(), url = location.href;
              input.dispatchEvent(new CompositionEvent('compositionstart', {bubbles:true}));
              input.value = '__unfinished_ime_candidate__';
              input.dispatchEvent(new InputEvent('input', {bubbles:true,isComposing:true}));
              input.dispatchEvent(new KeyboardEvent('keydown', {bubbles:true,key:'Escape',isComposing:true}));
              const during = visible(), duringURL = location.href, candidate = input.value;
              input.value = term;
              input.dispatchEvent(new CompositionEvent('compositionend', {bubbles:true}));
              return {before,during,url,duringURL,candidate,term,after:visible()};
            }""")
            assert results['before'] and results['before'] == results['during'] == results['after'], results
            assert results['url'] == results['duringURL'], results
            assert results['candidate'] == '__unfinished_ime_candidate__', results
            # Search metadata includes published editions, so switching language
            # must retain the query and its matching report IDs.
            page.evaluate("window.Rep0rterLanguage.change('ja')")
            assert page.locator('html').get_attribute('lang') == 'ja'
            assert page.locator('#story-search').input_value() == results['term']
            assert page.locator('article[data-search]:not([hidden])').evaluate_all('(articles)=>articles.map(article=>article.id)') == results['before']
            page.locator('#story-search').press('Escape')
            assert page.locator('#story-search').input_value() == ''
            for order in ['oldest', 'newest']:
                dates = page.evaluate("""order => {
                  const sort = document.querySelector('[data-filter="sort"]');
                  sort.value = order;
                  sort.dispatchEvent(new Event('change', {bubbles:true}));
                  return [...document.querySelectorAll('.day:not([hidden])')].map(day=>day.querySelector('article').dataset.date);
                }""", order)
                assert dates == sorted(dates, reverse=order == 'newest'), (order, dates)
            checks.append(f'{width}x{height}: IME candidates, Escape, cross-language search and calendar ordering')
            if width == 390:
                page.evaluate("scrollTo({top:1000,behavior:'instant'})")
                before = page.evaluate('({url:location.href,y:scrollY,lang:document.documentElement.lang})')
                stalled = []
                page.route('**/index.ko.html', lambda route: stalled.append(route))
                page.evaluate("void window.Rep0rterLanguage.change('ko')")
                page.wait_for_function("document.documentElement.getAttribute('aria-busy') === 'true'")
                page.wait_for_function("document.documentElement.getAttribute('aria-busy') !== 'true'", timeout=12000)
                assert page.evaluate('({url:location.href,y:scrollY,lang:document.documentElement.lang})') == before
                assert page.locator('[data-language-status]').is_visible()
                for route in stalled:
                    route.abort()
                page.unroute('**/index.ko.html')
                page.evaluate("window.Rep0rterLanguage.change('ko')")
                assert page.locator('html').get_attribute('lang') == 'ko'
                checks.append('Stalled edition times out, retains reading position and succeeds on retry')
            context.close()
        browser.close()
    assert not errors, errors
    print(json.dumps({'checks': checks, 'console_errors': errors}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
