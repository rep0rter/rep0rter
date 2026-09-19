"""Regression coverage for the compact appearance and language controls."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest
from jinja2 import Environment, FileSystemLoader
from bs4 import BeautifulSoup

from rep0rter.i18n import COPY, LANGUAGES, page_name


TEMPLATES = Path(__file__).resolve().parents[1] / 'rep0rter' / 'templates'
NODE = shutil.which('node')


@pytest.mark.parametrize('language', LANGUAGES)
def test_compact_language_menu_has_real_links_and_current_edition(language):
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=True)
    macro = env.get_template('_ui.html').module.language_links
    html = macro(COPY[language], language, LANGUAGES, page_name, compact=True)
    doc = BeautifulSoup(html, 'html.parser')
    menu = doc.select_one('details.language-menu')
    assert menu and not menu.has_attr('open')
    assert LANGUAGES[language] in menu.summary['aria-label']
    links = menu.select('a[data-language]')
    assert len(links) == len(LANGUAGES)
    assert {link['data-language'] for link in links} == set(LANGUAGES)
    for link in links:
        assert link['href'] == page_name(link['data-language'])
        assert link.get('aria-current') == ('page' if link['data-language'] == language else None)
    assert not doc.select('[role="menu"], [role="menuitem"]')


@pytest.mark.skipif(not NODE, reason='Node.js required for script regression')
def test_language_links_and_search_forms_share_canonical_query_editions():
    script = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const location = new URL('https://example.test/index.zh-TW.html?q=moon&author=alice&topic=space#report');
function link(href, dataset = {}, attributes = {}) {
  return {href, dataset, attributes,
    hasAttribute(name) {return name in attributes;},
    getAttribute(name) {return name === 'href' ? this.href : attributes[name];},
    setAttribute(name, value) {attributes[name] = value;},
    removeAttribute(name) {delete attributes[name];}
  };
}
const language = link('https://example.test/index.ja.html', {language: 'ja'});
const home = link('https://example.test/index.zh-TW.html');
const external = link('https://external.test/index.html');
const anchor = link('#news');
const download = link('https://example.test/index.html', {}, {download: ''});
const search = {action: 'https://example.test/index.zh-TW.html', children: [],
  querySelector() {return this.children[0] || null;}, append(child) {this.children.push(child);}
};
const document = {documentElement: {lang: 'zh-TW'}, readyState: 'complete',
  addEventListener() {},
  querySelectorAll(selector) {
    if (selector === 'a[href]') return [language, home, external, anchor, download];
    if (selector === 'a[data-language]') return [language];
    if (selector === 'form[role="search"]') return [search];
    throw Error(selector);
  },
  createElement() {return {};}
};
const window = {addEventListener() {}, history: {state: {preserved: true}, replaceState(state, title, value) {
  assert.equal(state.preserved, true); location.href = String(value);
}}};
vm.runInNewContext(SOURCE, {document, window, location, URL});
assert.equal(language.href, 'https://example.test/index.html?q=moon&author=alice&topic=space&lang=JA#report');
assert.equal(home.href, 'https://example.test/index.html?lang=ZH');
assert.equal(external.href, 'https://external.test/index.html');
assert.equal(anchor.href, '#news');
assert.equal(download.href, 'https://example.test/index.html');
assert.equal(search.action, 'https://example.test/index.html?lang=ZH');
assert.deepEqual(search.children, [{type: 'hidden', name: 'lang', value: 'ZH'}]);
'''.replace('SOURCE', json.dumps((TEMPLATES / 'language.js').read_text()))
    result = subprocess.run([NODE, '-'], input=script, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
