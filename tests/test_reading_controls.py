"""Execute the shipped progressive-enhancement scripts without a browser dependency."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


TEMPLATES = Path(__file__).resolve().parents[1] / "rep0rter" / "templates"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="Node.js required for browser script regressions")


DOM = """
const assert = require('node:assert/strict');
const vm = require('node:vm');
function element(properties = {}) {
  const listeners = {};
  return Object.assign({
    hidden: false, dataset: {}, attributes: {}, textContent: '',
    addEventListener(type, handler) { (listeners[type] ??= []).push(handler); },
    removeEventListener(type, handler) { listeners[type] = (listeners[type] ?? []).filter(item => item !== handler); },
    emit(type, event = {}) { for (const handler of listeners[type] ?? []) handler(event); },
    setAttribute(name, value) { this.attributes[name] = value; },
    focus() { this.focused = true; },
  }, properties);
}
"""


def run_javascript(script):
    result = subprocess.run([NODE, "-"], input=DOM + script, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def theme_setup(saved=None, system_dark=False, storage_blocked=False):
    source = json.dumps((TEMPLATES / "theme.js").read_text())
    options = json.dumps({"saved": saved, "systemDark": system_dark, "blocked": storage_blocked})
    return """
const options = OPTIONS;
const root = element();
const choices = ['light', 'dark', 'system'].map(mode => element({dataset: {themeChoice: mode}, closest: () => null}));
const controls = element({hidden: true});
const media = element({matches: options.systemDark});
const reducedMotion = element({matches: false});
const writes = [];
const storage = {
  getItem(key) {
    assert.equal(key, 'rep0rter-theme');
    if (options.blocked) throw new Error('Storage blocked');
    return options.saved;
  },
  setItem(key, value) {
    if (options.blocked) throw new Error('Storage blocked');
    writes.push([key, value]);
  },
};
const document = element({documentElement: root, querySelectorAll(selector) {
  if (selector === '[data-theme-choice]') return choices;
  if (selector === '[data-theme-controls]') return [controls];
  if (selector === '[data-theme-trigger]' || selector === '.preference-menu') return [];
  throw new Error('Unexpected selector: ' + selector);
}});
const window = element({matchMedia(query) {
  if (query === '(prefers-reduced-motion: reduce)') return reducedMotion;
  assert.equal(query, '(prefers-color-scheme: dark)');
  return media;
}});
vm.runInNewContext(SOURCE, {document, window, localStorage: storage});
function selected() { return choices.filter(button => button.attributes['aria-pressed'] === 'true').map(button => button.dataset.themeChoice); }
function choose(mode) { choices.find(button => button.dataset.themeChoice === mode).emit('click'); }
function systemChanges(dark) { media.matches = dark; media.emit('change'); }
""".replace("OPTIONS", options).replace("SOURCE", source)


@pytest.mark.parametrize(("saved", "system_dark", "expected", "selected"), [
    ("light", True, "light", "light"),
    ("dark", False, "dark", "dark"),
    ("system", True, "dark", "system"),
    ("system", False, "light", "system"),
    (None, True, "dark", "system"),
    ("obsolete-mode", False, "light", "system"),
])
def test_appearance_applies_saved_choice_before_dom_ready(saved, system_dark, expected, selected):
    run_javascript(theme_setup(saved, system_dark) + f"""
assert.equal(root.dataset.theme, {json.dumps(expected)});
assert.equal(controls.hidden, true);
document.emit('DOMContentLoaded');
assert.equal(controls.hidden, false);
assert.deepEqual(selected(), [{json.dumps(selected)}]);
assert.deepEqual(writes, []);
""")


def test_appearance_can_change_and_track_system_when_storage_is_blocked():
    run_javascript(theme_setup(storage_blocked=True) + """
assert.equal(root.dataset.theme, 'light');
document.emit('DOMContentLoaded');
choose('dark');
assert.equal(root.dataset.theme, 'dark');
assert.deepEqual(selected(), ['dark']);
choose('system');
systemChanges(true);
assert.equal(root.dataset.theme, 'dark');
systemChanges(false);
assert.equal(root.dataset.theme, 'light');
assert.deepEqual(selected(), ['system']);
""")


def test_appearance_saves_user_choice_and_only_system_choice_tracks_os():
    run_javascript(theme_setup("system") + """
document.emit('DOMContentLoaded');
systemChanges(true);
assert.equal(root.dataset.theme, 'dark');
choose('light');
systemChanges(true);
assert.equal(root.dataset.theme, 'light');
choose('dark');
systemChanges(false);
assert.equal(root.dataset.theme, 'dark');
choose('system');
assert.equal(root.dataset.theme, 'light');
systemChanges(true);
assert.equal(root.dataset.theme, 'dark');
assert.deepEqual(selected(), ['system']);
assert.deepEqual(writes, [
  ['rep0rter-theme', 'light'], ['rep0rter-theme', 'dark'], ['rep0rter-theme', 'system'],
]);
""")


def test_appearance_syncs_other_tabs_and_recovers_from_removed_or_invalid_settings():
    run_javascript(theme_setup("dark") + """
document.emit('DOMContentLoaded');
window.emit('storage', {key: 'another-setting', newValue: 'light'});
assert.equal(root.dataset.theme, 'dark');
window.emit('storage', {key: 'rep0rter-theme', newValue: 'light'});
assert.equal(root.dataset.theme, 'light');
assert.deepEqual(selected(), ['light']);
systemChanges(true);
window.emit('storage', {key: 'rep0rter-theme', newValue: 'system'});
assert.equal(root.dataset.theme, 'dark');
assert.deepEqual(selected(), ['system']);
window.emit('storage', {key: 'rep0rter-theme', newValue: 'dark'});
systemChanges(false);
assert.equal(root.dataset.theme, 'dark');
window.emit('storage', {key: 'rep0rter-theme', newValue: null});
assert.equal(root.dataset.theme, 'light');
assert.deepEqual(selected(), ['system']);
window.emit('storage', {key: 'rep0rter-theme', newValue: 'unknown'});
assert.deepEqual(selected(), ['system']);
choose('dark');
window.emit('storage', {key: null, newValue: null});
assert.equal(root.dataset.theme, 'light');
assert.deepEqual(selected(), ['system']);
""")


def search_setup(with_articles=True):
    source = json.dumps((TEMPLATES / "reading.js").read_text())
    return """
const input = element({value: ''});
const form = element({hidden: true, querySelector(selector) {
  assert.equal(selector, 'input'); return input;
}});
const clear = element({hidden: true});
const status = element({hidden: true, dataset: {resultTemplate: '{count} reports found'}});
const empty = element({hidden: true});
const reset = element();
const articles = WITH_ARTICLES ? [
  element({dataset: {search: 'Open Data / 地圖 / Slack #maps'}}),
  element({dataset: {search: 'Open Source workshop / GitHub'}}),
  element({dataset: {search: 'Community data / Mastodon'}}),
] : [];
const days = [articles.slice(0, 2), articles.slice(2)].map(stories => element({
  count: element({textContent: String(stories.length)}),
  querySelectorAll(selector) { assert.equal(selector, 'article'); return stories; },
  querySelector(selector) { if (selector === '.story-grid') return null; assert.equal(selector, '.day-count'); return this.count; },
}));
const single = {
  '[data-search-form]': form, '[data-search-clear]': clear, '#search-status': status,
  '[data-no-results]': empty, '[data-search-reset]': reset,
  '[data-filters]': null, '[data-filter-count]': null, '[data-filter-error]': null, '[data-filters-reset]': null, '#news': null,
};
const document = element({
  documentElement: {lang: 'en', dataset: {}},
  querySelector(selector) { assert.ok(selector in single, selector); return single[selector]; },
  querySelectorAll(selector) {
    if (selector === '[data-author-avatar]') return [];
    if (selector === '.day') return days;
    if (selector === 'article[data-search]') return articles;
    throw new Error('Unexpected selector: ' + selector);
  },
});
const window = element({location: {href: 'https://example.test/'}, history: {state: null, replaceState(state, title, url) {window.location.href = url;}}});
vm.runInNewContext(SOURCE, {document, window, URL, Intl});
function search(value) { input.value = value; input.emit('input'); }
function visible() { return articles.map(article => !article.hidden); }
""".replace("SOURCE", source).replace("WITH_ARTICLES", json.dumps(with_articles))


def test_search_normalizes_text_and_matches_all_terms_in_current_edition():
    run_javascript(search_setup() + """
assert.equal(form.hidden, false);
assert.deepEqual(visible(), [true, true, true]);
search(' ＯＰＥＮ  data ');
assert.deepEqual(visible(), [true, false, false]);
assert.equal(status.textContent, '1 reports found');
assert.equal(status.hidden, false);
assert.equal(clear.hidden, false);
search('地圖 slack');
assert.deepEqual(visible(), [true, false, false]);
search('GITHUB workshop');
assert.deepEqual(visible(), [false, true, false]);
search('data');
assert.deepEqual(visible(), [true, false, true]);
assert.equal(status.textContent, '2 reports found');
assert.deepEqual(days.map(day => day.hidden), [false, false]);
assert.deepEqual(days.map(day => Number(day.count.textContent)), [1, 1]);
""")


def test_search_hides_empty_days_and_offers_recovery_from_no_results():
    run_javascript(search_setup() + """
search('mastodon');
assert.deepEqual(days.map(day => day.hidden), [true, false]);
assert.deepEqual(days.map(day => Number(day.count.textContent)), [0, 1]);
search('missing report');
assert.deepEqual(visible(), [false, false, false]);
assert.deepEqual(days.map(day => day.hidden), [true, true]);
assert.equal(status.textContent, '0 reports found');
assert.equal(empty.hidden, false);
reset.emit('click');
assert.deepEqual(visible(), [true, true, true]);
assert.deepEqual(days.map(day => day.hidden), [false, false]);
assert.deepEqual(days.map(day => Number(day.count.textContent)), [2, 1]);
assert.equal(input.value, '');
assert.equal(input.focused, true);
assert.equal(empty.hidden, true);
assert.equal(status.hidden, true);
assert.equal(clear.hidden, true);
""")


def test_search_clear_escape_and_submit_preserve_reading_without_navigation():
    run_javascript(search_setup() + """
search('open');
input.emit('keydown', {key: 'Enter'});
assert.equal(input.value, 'open');
input.emit('keydown', {key: 'Escape'});
assert.equal(input.value, '');
assert.equal(input.focused, true);
assert.deepEqual(visible(), [true, true, true]);
search('data');
input.focused = false;
clear.emit('click');
assert.equal(input.value, '');
assert.equal(input.focused, true);
assert.deepEqual(visible(), [true, true, true]);
input.value = 'workshop';
let prevented = false;
form.emit('submit', {preventDefault() { prevented = true; }});
assert.equal(prevented, true);
assert.deepEqual(visible(), [false, true, false]);
search('   ');
assert.deepEqual(visible(), [true, true, true]);
assert.equal(status.hidden, true);
""")


def test_search_keeps_controls_hidden_when_there_are_no_reports():
    run_javascript(search_setup(with_articles=False) + """
assert.equal(form.hidden, true);
assert.equal(empty.hidden, true);
assert.equal(status.hidden, true);
""")


def test_search_is_safe_on_detail_and_withdrawal_pages_without_search_form():
    source = json.dumps((TEMPLATES / "reading.js").read_text())
    run_javascript("""
const document = element({querySelectorAll() { return []; }, querySelector(selector) {
  assert.equal(selector, '[data-search-form]'); return null;
}});
vm.runInNewContext(SOURCE, {document});
""".replace("SOURCE", source))


def test_author_avatar_failure_falls_back_without_search_and_rebinds_on_language():
    source = json.dumps((TEMPLATES / "reading.js").read_text())
    run_javascript("""
let avatars = [element({complete: true, naturalWidth: 192}), element({complete: true, naturalWidth: 0}), element({complete: false, naturalWidth: 0})];
const document = element({querySelector() { return null; }, querySelectorAll(selector) {
  assert.equal(selector, '[data-author-avatar]'); return avatars;
}});
vm.runInNewContext(SOURCE, {document});
assert.deepEqual(avatars.map(image => image.hidden), [false, true, false]);
avatars[2].emit('error');
assert.equal(avatars[2].hidden, true);
const old = avatars[0];
document.emit('rep0rter:before-language');
old.emit('error');
assert.equal(old.hidden, false);
avatars = [element({complete: false, naturalWidth: 0})];
document.emit('rep0rter:language-applied');
avatars[0].emit('error');
assert.equal(avatars[0].hidden, true);
""".replace("SOURCE", source))
