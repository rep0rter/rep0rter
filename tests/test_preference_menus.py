"""Direct appearance choices and native disclosure menu behavior."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from bs4 import BeautifulSoup
from jinja2 import Environment, FileSystemLoader

from rep0rter.i18n import COPY

TEMPLATES = Path(__file__).resolve().parents[1] / 'rep0rter/templates'
NODE = shutil.which('node')


def test_theme_menu_has_explicit_choices_and_system_default():
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=True)
    doc = BeautifulSoup(env.get_template('_ui.html').module.appearance(COPY['en']), 'html.parser')
    menu = doc.select_one('details[data-theme-controls]')
    assert menu.has_attr('hidden')
    assert menu.select_one('summary[data-theme-trigger]')
    assert [button['data-theme-choice'] for button in menu.select('button')] == ['light', 'dark', 'system']
    assert menu.select_one('[aria-pressed="true"]')['data-theme-choice'] == 'system'
    assert not menu.select('[data-theme-cycle], [role="menu"], [role="menuitem"]')


@pytest.mark.skipif(not NODE, reason='Node.js required')
@pytest.mark.parametrize('blocked', [False, True])
def test_menu_choice_closing_focus_device_default_and_storage(blocked):
    script = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
function element(values = {}) {
  const handlers = {};
  return Object.assign({dataset: {}, attributes: {}, hidden: false, open: false,
    addEventListener(name, callback) { (handlers[name] ??= []).push(callback); },
    emit(name, event = {}) { (handlers[name] ?? []).forEach(callback => callback(event)); },
    setAttribute(name, value) { this.attributes[name] = value; },
    focus(options) { this.focused = true; assert.equal(options.preventScroll, true); },
  }, values);
}
const root = element();
const icons = ['light','dark','system'].map(themeIcon => element({dataset: {themeIcon}}));
const trigger = element({dataset: {themeLabel: 'Appearance', themeLight: 'Light', themeDark: 'Dark', themeSystem: 'System'}, querySelectorAll: () => icons});
const languageTrigger = element();
const language = element({querySelector: () => languageTrigger, contains: target => target === languageTrigger});
const theme = element({hidden: true, querySelector: () => trigger, contains: target => target === trigger});
const choices = ['light','dark','system'].map(themeChoice => element({dataset: {themeChoice}, closest: () => theme}));
const media = element({matches: true});
const reducedMotion = element({matches: false});
const document = element({documentElement: root, querySelectorAll(selector) {
  return {'[data-theme-choice]': choices, '[data-theme-trigger]': [trigger], '[data-theme-controls]': [theme], '.preference-menu': [language,theme]}[selector] ?? [];
}});
const window = element({matchMedia(query) {
  if (query === '(prefers-reduced-motion: reduce)') return reducedMotion;
  assert.equal(query, '(prefers-color-scheme: dark)');
  return media;
}});
const writes = [];
const localStorage = {
  getItem() { if (BLOCKED) throw Error('blocked'); return null; },
  setItem(key,value) { if (BLOCKED) throw Error('blocked'); writes.push([key,value]); },
};
vm.runInNewContext(SOURCE, {document,window,localStorage});
assert.equal(root.dataset.theme, 'dark');
document.emit('DOMContentLoaded');
assert.equal(theme.hidden, false);
assert.equal(trigger.attributes['aria-label'], 'Appearance: System');
assert.equal(choices[2].attributes['aria-pressed'], 'true');
// Merely opening the disclosure does not change the preference.
trigger.emit('click');
assert.equal(trigger.dataset.themePreference, 'system');
for (const mode of ['dark','light','system']) {
  theme.open = true;
  choices.find(button => button.dataset.themeChoice === mode).emit('click');
  assert.equal(theme.open, false);
  assert.equal(trigger.focused, true);
  assert.equal(trigger.dataset.themePreference, mode);
  assert.deepEqual(icons.filter(icon => !icon.hidden).map(icon => icon.dataset.themeIcon), [mode]);
}
assert.deepEqual(writes, BLOCKED ? [] : ['dark','light','system'].map(mode => ['rep0rter-theme',mode]));
media.matches = false;
media.emit('change');
assert.equal(root.dataset.theme, 'light');
language.open = true;
trigger.emit('click');
assert.equal(language.open, false);
theme.open = true;
languageTrigger.emit('click');
assert.equal(theme.open, false);
language.open = true;
document.emit('pointerdown', {target: languageTrigger});
assert.equal(language.open, true);
document.emit('pointerdown', {target: root});
assert.equal(language.open, false);
language.open = true;
let prevented = false;
document.emit('keydown', {key:'Escape', preventDefault() { prevented = true; }});
assert.equal(language.open, false);
assert.equal(languageTrigger.focused, true);
assert.equal(prevented, true);
window.emit('storage', {key:'rep0rter-theme', newValue:'dark'});
assert.equal(root.dataset.theme,'dark');
assert.equal(trigger.attributes['aria-label'],'Appearance: Dark');
'''.replace('SOURCE', json.dumps((TEMPLATES / 'theme.js').read_text())).replace('BLOCKED', json.dumps(blocked))
    result = subprocess.run([NODE, '-'], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
