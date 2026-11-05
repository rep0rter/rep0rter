"""The New interface (beta) switch: off by default, local only, privacy-neutral."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from rep0rter.i18n import COPY, LANGUAGES, page_name
from rep0rter.publishers import site
from rep0rter.policy import add_rule, redact
from tests.test_site import published_site  # noqa: F401  (shared fixture)

THEME = (Path(__file__).resolve().parents[1] / 'rep0rter/templates/theme.js').read_text()
NODE = shutil.which('node')


def html(path):
    return BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')


@pytest.mark.skipif(not NODE, reason='Node.js required for script regression')
@pytest.mark.parametrize('stored,blocked,expected', [(None, False, False), ('1', False, True), ('1', True, False)])
def test_switch_applies_saved_preference_before_paint(stored, blocked, expected):
    script = r'''
const vm = require('node:vm');
const [source, stored, blocked] = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const listeners = {};
const root = {dataset: {}, style: {setProperty() {}}, classList: {contains: () => false, add() {}, remove() {}},
  clientWidth: 1000, getAttribute() { return null; }, setAttribute() {}, removeAttribute() {}};
const storage = {getItem(key) { if (blocked) throw Error('blocked'); return key === 'rep0rter-redesign' ? stored : null; },
  setItem() { if (blocked) throw Error('blocked'); }, removeItem() {}};
const document = {documentElement: root, readyState: 'loading', querySelectorAll: () => [],
  addEventListener(name, fn) { (listeners[name] ??= []).push(fn); }};
const context = {document, localStorage: storage, innerWidth: 1000, getComputedStyle: () => ({zoom: '1'}),
  matchMedia: () => ({matches: false, addEventListener() {}}), addEventListener() {}, window: {},
  setTimeout, clearTimeout, requestAnimationFrame: fn => fn()};
context.window = context;
vm.runInNewContext(source, context);
process.stdout.write(JSON.stringify('redesign' in root.dataset));
'''
    result = subprocess.run([NODE, '-e', script], input=json.dumps([THEME, stored, blocked]),
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) is expected


def test_every_edition_ships_the_hidden_switch_and_both_story_variants(published_site):
    _, cfg, _, entries, _ = published_site
    for language in LANGUAGES:
        home = html(cfg.site_dir / page_name(language))
        toggle = home.select_one('[data-redesign-toggle]')
        assert toggle and toggle.has_attr('hidden') and toggle['aria-pressed'] == 'false'
        assert COPY[language]['redesign_label'] in toggle.get_text()
        story = html(cfg.site_dir / 'posts' / str(entries[-1][0].id) / page_name(language))
        assert story.select_one('.detail-intro .legacy-only .page-intro-title')
        article = story.select_one('article')
        # Variant elements that name the story or its source live inside the removable article.
        for variant in story.select('.redesign-only, .legacy-only'):
            if variant.select_one('a[href*="sources/"], .story-date') or 'story-follow' in variant.get('class', []):
                assert article in variant.parents
        assert len(story.select('h1')) == 1 and article.select_one('h1')


def test_withdrawal_removes_story_content_from_both_variants(published_site):
    store, cfg, _, entries, _ = published_site
    removed, event = entries[-1]
    headline = removed.translations['en']['headline']
    add_rule(store, 'event', event.id)
    redact(store, [event.id])
    site.build(store, cfg)
    for path in cfg.site_dir.rglob('*.html'):
        text = path.read_text(encoding='utf-8')
        assert headline not in text, path
        doc = BeautifulSoup(text, 'html.parser')
        assert not doc.select(f'[data-preview-post-id="{removed.id}"]'), path
