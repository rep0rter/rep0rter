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
def test_language_link_updates_filters_without_inventing_an_article_anchor():
    script = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const handlers = {};
const link = new URL('https://example.test/index.zh-TW.html');
link.addEventListener = (name, callback) => { handlers[name] = callback; };
link.hasAttribute = () => false;
link.getAttribute = () => null;
const document = {querySelectorAll: () => [link]};
const location = new URL('https://example.test/index.html?q=moon&author=alice&topic=space');
const sessionStorage = new Proxy({}, {get() { assert.fail('Language selection must not store a viewport'); }});
const window = {scrollX: 0, scrollY: 450, addEventListener() {}};
vm.runInNewContext(SOURCE, {document, window, location, sessionStorage});
assert.equal(link.hash, '');
assert.equal(link.search, '?q=moon&author=alice&topic=space');
location.search = '?q=stars&sort=oldest';
window.scrollY = 0; // Returning to the top must not retain an earlier article.
handlers.click({button: 0});
assert.equal(link.hash, '');
assert.equal(link.search, '?q=stars&sort=oldest');
assert.equal(link.href, 'https://example.test/index.zh-TW.html?q=stars&sort=oldest');
location.hash = '#news';
handlers.pointerdown();
assert.equal(link.hash, '');
'''.replace('SOURCE', json.dumps((TEMPLATES / 'language.js').read_text()))
    result = subprocess.run([NODE, '-'], input=script, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
