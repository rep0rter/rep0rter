"""Same-document locale changes preserve the current page until translation is ready."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

SOURCE = (Path(__file__).resolve().parents[1] / 'rep0rter/templates/language.js').read_text()
NODE = shutil.which('node')

HARNESS = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
class Element {
  constructor(properties = {}) { Object.assign(this, {dataset: {}, attributes: {}, listeners: {}}, properties); }
  addEventListener(name, callback) { (this.listeners[name] ??= []).push(callback); }
  emit(name, event = {}) { for (const callback of this.listeners[name] || []) callback(event); }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  hasAttribute(name) { return name in this.attributes; }
  getAttribute(name) { return this.attributes[name] ?? null; }
  focus(options) { this.focused = true; assert.equal(options.preventScroll, true); }
}
function page(options = {}) {
  const location = new URL(options.url || 'https://example.test/index.html?q=moon&topic=space#story-days');
  const calls = [], writes = [];
  const root = new Element({lang: options.language || 'en'});
  const trigger = new Element();
  const menu = new Element({open: true, querySelector: () => trigger});
  const locale = options.locale || 'ja';
  const link = new Element({href: new URL('index.ja.html', location).href, dataset: {language: locale}, target: options.target || ''});
  link.attributes.href = link.href;
  if (options.download) link.attributes.download = '';
  link.closest = selector => selector === 'a[data-language]' ? link : null;
  const links = [link];
  let status = null;
  const body = new Element({unchanged: true, append(element) { status = element; }});
  const document = new Element({documentElement: root, body, readyState: 'complete',
    querySelector(selector) {
      if (selector === '.language-menu') return menu;
      if (selector === '[data-language-status]') return status;
      throw Error('Unexpected selector: ' + selector);
    },
    querySelectorAll(selector) {
      if (selector === 'a[href]' || selector === 'a[data-language]') return links;
      if (selector === 'form[role="search"]') return [];
      throw Error('Unexpected selector: ' + selector);
    },
    createElement() {return new Element();},
    dispatchEvent(event) {this.emit(event.type, event);}
  });
  const window = new Element({history: {state: {retained: true}, replaceState(state, title, url) {
    assert.equal(state.retained, true);
    writes.push(String(url)); location.href = String(url);
  }}, scrollTo() {assert.fail('No viewport movement before a successful body replacement');}});
  const fetch = (url, init) => new Promise((resolve, reject) => {
    calls.push({url: String(url), init, resolve, reject});
  });
  class DOMParser {parseFromString() {return {documentElement: {lang: 'wrong'}, querySelector() {return null;}};}}
  vm.runInNewContext(SOURCE, {window, document, location, URL, Element, AbortController, fetch, DOMParser,
    CustomEvent: class {constructor(type, options) {this.type = type;this.detail = options.detail;}}
  });
  const click = (event = {}) => {
    let prevented = false;
    document.emit('click', {target: link, button: 0, preventDefault() {prevented = true;}, ...event});
    return prevented;
  };
  return {window, document, root, link, links, location, calls, writes, menu, trigger, body, click, status: () => status};
}
const flush = () => new Promise(resolve => setImmediate(resolve));
(async () => { CASE })().catch(error => {console.error(error);process.exitCode = 1;});
'''

CASES = {
    'canonical_query_urls_preserve_search_hash_and_nested_paths': r'''
const source = page();
assert.equal(source.location.href, 'https://example.test/index.html?q=moon&topic=space&lang=EN#story-days');
assert.equal(source.link.href, 'https://example.test/index.html?q=moon&topic=space&lang=JA#story-days');
const canonical = source.window.Rep0rterLanguage.publicURL('/posts/42/index.zh-TW.html?q=test#original', 'ko');
assert.equal(canonical.href, 'https://example.test/posts/42/index.html?q=test&lang=KO#original');
assert.equal(source.window.Rep0rterLanguage.publicURL('https://outside.test/index.html', 'ja').href, 'https://outside.test/index.html');
assert.equal(source.window.Rep0rterLanguage.publicURL('/image.png', 'ja').pathname, '/image.png');
for (const code of ['ZH', 'zh', 'zh-TW']) {
  const local = page({language: 'zh-TW', url: 'https://example.test/index.zh-TW.html?lang=' + code});
  assert.equal(local.location.href, 'https://example.test/index.html?lang=ZH');
  assert.equal(local.calls.length, 0);
}
''',
    'normal_and_keyboard_clicks_fetch_without_navigating_or_reloading': r'''
for (const detail of [0, 1]) {
  const source = page();
  const originalURL = source.location.href;
  assert.equal(source.click({detail}), true);
  assert.equal(source.menu.open, false);
  assert.equal(source.trigger.focused, true);
  assert.equal(source.calls.length, 1);
  assert.equal(source.calls[0].url, 'https://example.test/index.ja.html');
  assert.equal(source.calls[0].init.headers.Accept, 'text/html');
  assert.equal(source.calls[0].init.cache, 'no-cache');
  assert.equal(source.location.href, originalURL);
  assert.equal(source.body.unchanged, true);
  assert.equal(source.root.attributes['aria-busy'], 'true');
}
''',
    'current_choice_closes_menu_without_fetch_or_viewport_change': r'''
const source = page({locale: 'en'});
const before = source.location.href;
assert.equal(source.link.attributes['aria-current'], 'page');
assert.equal(source.click(), true);
assert.equal(source.calls.length, 0);
assert.equal(source.menu.open, false);
assert.equal(source.trigger.focused, true);
assert.equal(source.location.href, before);
''',
    'modified_clicks_new_tabs_downloads_and_unrelated_targets_stay_native': r'''
for (const event of [{button: 1}, {button: 2}, {metaKey: true}, {ctrlKey: true},
  {shiftKey: true}, {altKey: true}, {defaultPrevented: true}, {target: {}}]) {
  const source = page();
  assert.equal(source.click(event), false);
  assert.equal(source.calls.length, 0);
  assert.equal(source.menu.open, true);
}
for (const options of [{target: '_blank'}, {download: true}]) {
  const source = page(options);
  assert.equal(source.click(), false);
  assert.equal(source.calls.length, 0);
}
''',
    'failed_or_invalid_translation_preserves_page_filters_and_offers_retry': r'''
for (const failure of ['network', 'http', 'invalid-html']) {
  const source = page();
  const before = source.location.href;
  source.click();
  if (failure === 'network') source.calls[0].reject(Error('offline'));
  else source.calls[0].resolve({ok: failure !== 'http', status: 503, text: async () => '<html>invalid</html>'});
  await flush();
  assert.equal(source.location.href, before);
  assert.equal(source.root.lang, 'en');
  assert.equal(source.body.unchanged, true);
  assert.equal(source.root.attributes['aria-busy'], undefined);
  assert.match(source.status().textContent, /current page is unchanged/);
  assert.equal(source.status().attributes.role, 'status');
  source.click();
  assert.equal(source.calls.length, 2);
}
''',
    'latest_choice_aborts_stale_request_and_current_choice_clears_pending_state': r'''
const source = page();
source.click();
const first = source.calls[0];
const second = source.window.Rep0rterLanguage.change('ko');
assert.equal(first.init.signal.aborted, true);
assert.equal(source.calls[1].url, 'https://example.test/index.ko.html');
first.reject(Error('stale network error'));
await flush();
assert.equal(source.root.attributes['aria-busy'], 'true');
assert.equal(source.status().textContent, 'Loading language…');
await source.window.Rep0rterLanguage.change('en');
assert.equal(source.calls[1].init.signal.aborted, true);
assert.equal(source.root.attributes['aria-busy'], undefined);
assert.equal(source.status().hidden, true);
source.calls[1].reject(Object.assign(Error('aborted'), {name: 'AbortError'}));
await second;
assert.equal(source.status().hidden, true);
assert.equal(source.root.lang, 'en');
''',
    'initial_query_locale_fetches_quietly_and_failure_reverts_only_locale': r'''
const source = page({url: 'https://example.test/index.html?lang=KO&q=stars&author=alice#report'});
assert.equal(source.calls.length, 1);
assert.equal(source.calls[0].url, 'https://example.test/index.ko.html');
assert.equal(source.trigger.focused, undefined);
source.calls[0].reject(Error('offline'));
await flush();
assert.equal(source.location.search, '?lang=EN&q=stars&author=alice');
assert.equal(source.location.hash, '#report');
assert.equal(source.root.lang, 'en');
assert.equal(source.body.unchanged, true);
''',
    'language_link_refresh_preserves_latest_filters_for_native_new_tabs': r'''
for (const type of ['pointerdown', 'focusin', 'click']) {
  const source = page();
  source.location.search = '?q=stars&author=alice&sort=oldest&lang=EN';
  source.location.hash = '#latest-report';
  if (type === 'click') assert.equal(source.click({metaKey: true}), false);
  else source.document.emit(type, {target: source.link});
  assert.equal(source.link.href, 'https://example.test/index.html?q=stars&author=alice&sort=oldest&lang=JA#latest-report');
  assert.equal(source.calls.length, 0);
  assert.equal(source.menu.open, true);
}
''',
    'choosing_current_language_cancels_initial_query_and_normalizes_url': r'''
const source = page({url: 'https://example.test/index.html?lang=ZH&q=stars#report'});
assert.equal(source.calls.length, 1);
await source.window.Rep0rterLanguage.change('en');
assert.equal(source.calls[0].init.signal.aborted, true);
assert.equal(source.location.href, 'https://example.test/index.html?lang=EN&q=stars#report');
assert.equal(source.root.lang, 'en');
assert.equal(source.root.attributes['aria-busy'], undefined);
assert.equal(source.status().hidden, true);
source.calls[0].reject(Object.assign(Error('aborted'), {name: 'AbortError'}));
await flush();
assert.equal(source.status().hidden, true);
''',
    'pagehide_aborts_pending_work_without_late_error_updates': r'''
const source = page();
source.click();
source.window.emit('pagehide');
assert.equal(source.calls[0].init.signal.aborted, true);
const before = source.status().textContent;
source.calls[0].reject(Error('request finished after leaving'));
await flush();
assert.equal(source.status().textContent, before);
''',
}


@pytest.mark.skipif(not NODE, reason='Node.js required for language navigation regression')
@pytest.mark.parametrize('case', CASES)
def test_same_page_language_navigation(case):
    script = HARNESS.replace('SOURCE', json.dumps(SOURCE)).replace('CASE', CASES[case])
    result = subprocess.run([NODE, '-'], input=script, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
