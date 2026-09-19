"""Language navigation never saves or restores scroll position."""
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
function page(options = {}) {
  const listeners = new Map();
  const handlers = {};
  const location = new URL('https://example.test/index.html?q=moon&topic=space#story-days');
  const link = new URL('index.ja.html#old-report', location);
  const trigger = {focused: false, focus(options) {
    this.focused = true;
    assert.equal(options.preventScroll, true);
  }};
  const menu = {open: true, querySelector: () => trigger};
  link.target = options.target || '';
  link.hasAttribute = name => name === 'download' && !!options.download;
  link.getAttribute = name => name === 'aria-current' && options.current ? 'page' : null;
  link.closest = () => menu;
  link.addEventListener = (name, callback) => { handlers[name] = callback; };
  const forbidden = () => assert.fail('Language navigation must not access storage or scroll');
  const window = {scrollTo: forbidden, addEventListener(name, callback) {
    assert.equal(name, 'pageswap', 'No restoration lifecycle or input listeners');
    listeners.set(name, callback);
  }};
  Object.defineProperties(window, {scrollX: {get: forbidden}, scrollY: {get: forbidden}});
  const document = {querySelectorAll: () => [link], addEventListener: forbidden};
  const storage = new Proxy({}, {get: forbidden});
  vm.runInNewContext(SOURCE, {window, document, location,
    sessionStorage: storage, localStorage: storage, requestAnimationFrame: forbidden, setTimeout: forbidden});
  const swap = event => listeners.get('pageswap')(event);
  return {window, link, location, handlers, menu, trigger, swap};
}
const transition = () => ({skips: 0, skipTransition() { this.skips += 1; }});
CASE
'''

CASES = {
    'native_navigation_preserves_filters_but_removes_all_anchors': r'''
const source = page();
assert.equal(source.link.search, '?q=moon&topic=space');
assert.equal(source.link.hash, '');
source.location.search = '?author=alice&sort=oldest';
source.location.hash = '#another-story';
source.handlers.pointerdown();
assert.equal(source.link.search, '?author=alice&sort=oldest');
assert.equal(source.link.hash, '');
source.handlers.click({button: 0, preventDefault() { assert.fail('Normal links stay native'); }});
assert.equal(source.link.href, 'https://example.test/index.ja.html?author=alice&sort=oldest');
''',
    'current_choice_closes_without_navigation_or_moving_viewport': r'''
const source = page({current: true});
let prevented = false;
source.handlers.click({button: 0, preventDefault() { prevented = true; }});
assert.equal(prevented, true);
assert.equal(source.menu.open, false);
assert.equal(source.trigger.focused, true);
const viewTransition = transition();
source.swap({viewTransition});
assert.equal(viewTransition.skips, 0);
''',
    'modified_and_new_tab_clicks_keep_native_behavior': r'''
for (const event of [{button: 1}, {button: 2}, {button: 0, metaKey: true},
  {button: 0, ctrlKey: true}, {button: 0, shiftKey: true}, {button: 0, altKey: true},
  {button: 0, defaultPrevented: true}]) {
  for (const current of [true, false]) {
    const source = page({current});
    source.handlers.click({...event, preventDefault() { assert.fail('Modified click intercepted'); }});
    assert.equal(source.menu.open, true);
    const viewTransition = transition();
    source.swap({viewTransition});
    assert.equal(viewTransition.skips, 0);
  }
}
for (const options of [{target:'_blank'}, {download:true}]) {
  const source = page(options);
  source.handlers.click({button:0, preventDefault() { assert.fail('Native action intercepted'); }});
  const viewTransition = transition();
  source.swap({viewTransition});
  assert.equal(viewTransition.skips, 0);
}
''',
    'only_language_destination_skips_decorative_transition': r'''
for (const keyboard of [true, false]) {
  const source = page();
  source.handlers.click({button: 0, detail: keyboard ? 0 : 1});
  const viewTransition = transition();
  source.swap({viewTransition, activation: {entry: {url: source.link.href}}});
  assert.equal(viewTransition.skips, 1);
  const next = transition();
  source.swap({viewTransition: next});
  assert.equal(next.skips, 0);
}
const source = page();
source.handlers.click({button: 0});
const unrelated = transition();
source.swap({viewTransition: unrelated, activation: {entry: {url: 'https://example.test/posts/42/'}}});
assert.equal(unrelated.skips, 0);
const native = page();
const nativeTransition = transition();
native.swap({viewTransition: nativeTransition});
assert.equal(nativeTransition.skips, 0);
const older = page();
older.handlers.click({button: 0});
const olderTransition = transition();
older.swap({viewTransition: olderTransition});
assert.equal(olderTransition.skips, 1);
''',
}


@pytest.mark.skipif(not NODE, reason='Node.js required for language navigation regression')
@pytest.mark.parametrize('case', CASES)
def test_language_navigation_has_no_scroll_management(case):
    script = HARNESS.replace('SOURCE', json.dumps(SOURCE)).replace('CASE', CASES[case])
    result = subprocess.run([NODE, '-'], input=script, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
