"""Image cards clean up content and motion across modal/locale lifecycles."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="Node.js required for viewer lifecycle regression")
SOURCE = (Path(__file__).resolve().parents[1] / "rep0rter/templates/image-viewer.js").read_text()

HARNESS = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
function element(extra = {}) {
  return Object.assign({listeners: {}, dataset: {}, textContent: '', style: {
    setProperty(name, value) {this[name] = value;}, removeProperty(name) {delete this[name];}
  },
  addEventListener(type, handler) {(this.listeners[type] ??= []).push(handler);},
  removeEventListener(type, handler) {this.listeners[type] = (this.listeners[type] || []).filter(item => item !== handler);},
  emit(type, event = {}) {for (const handler of [...(this.listeners[type] || [])]) handler(event);},
  matches() {return false;},
  getAttribute(name) {return this[name] ?? null;},
  removeAttribute(name) {delete this[name];},
  getBoundingClientRect() {return {left: 10, top: 20, width: 320, height: 200, right: 330, bottom: 220};},
  focus(options) {this.focused = options;},
  }, extra);
}
function page(label) {
  const image = element({complete: false, naturalWidth: 0});
  const caption = element(), download = element(), close = element();
  const stage = element(), card = element();
  const status = element({dataset: {loading: label + ' loading', error: label + ' error'}});
  // Native close events are queued separately; tests deliver them explicitly.
  const viewer = element({open: false, showModal() {this.open = true;}, close() {this.open = false;},
    querySelector(selector) {return {
      '[data-viewer-image]': image, '[data-viewer-caption]': caption,
      '[data-viewer-download]': download, '[data-viewer-close]': close,
      '[data-viewer-stage]': stage, '[data-viewer-card]': card, '[data-viewer-status]': status,
    }[selector];}
  });
  const thumbnail = element({alt: label, naturalWidth: 1200, naturalHeight: 630});
  const source = element();
  const themePaths = {imageLight: '/' + label + '.png', imageDark: '/' + label + '-dark.png'};
  const articleDownload = element({dataset: {...themePaths}, matches(selector) {return selector === 'a';}});
  const button = element({disabled: true, isConnected: true, dataset: {imageSrc: '/' + label + '.png', ...themePaths},
    querySelector(selector) {return selector === '[data-theme-source]' ? source : thumbnail;}
  });
  return {viewer, button, image, close, stage, card, status, download, caption, thumbnail, source, articleDownload};
}
const classes = new Set();
const root = element({classList: {add(name) {classes.add(name);}, remove(name) {classes.delete(name);}}});
let current = page('English');
const document = element({documentElement: root,
  querySelector() {return current.viewer;},
  querySelectorAll(selector) {return selector === '[data-image-view]' ? [current.button] : [current.button, current.articleDownload];}
});
const motions = new Set();
const reduced = element({matches: false});
const window = element({matchMedia() {return reduced;}, Rep0rterMotion: {
  spring({from, to, onUpdate, onComplete}) {
    const motion = {to, onUpdate, onComplete};
    motions.add(motion);
    onUpdate(from + (to - from) / 2, 0);
    return {cancel() {motions.delete(motion);}};
  }
}, Rep0rterScrollLock: {
  acquire() {throw Error('Animation lock would block modal scrolling and pinch zoom');}
}});
function finishMotions() {
  for (const motion of [...motions]) {
    if (!motions.delete(motion)) continue;
    motion.onUpdate(motion.to, 0);
    motion.onComplete?.();
  }
}
function ready() {
  current.image.complete = true;
  current.image.naturalWidth = 1200;
  current.image.emit('load');
}
function movePointer() {
  current.stage.emit('pointermove', {pointerType: 'mouse', clientX: 300, clientY: 180});
}
const observers = new Set();
class MutationObserver {
  constructor(callback) {this.callback = callback;}
  observe() {observers.add(this);}
  disconnect() {observers.delete(this);}
}
vm.runInNewContext(SOURCE, {document, window, MutationObserver});
"""


def run_viewer(script):
    result = subprocess.run(
        [NODE, "-"], input=HARNESS.replace("SOURCE", json.dumps(SOURCE)) + script,
        text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_locale_replacement_disposes_open_card_and_binds_new_images():
    run_viewer(r"""
const old = current;
old.button.emit('click');
ready();
movePointer();
assert.ok(motions.size > 1, 'Opening and pointer motion must both be active');
document.emit('rep0rter:before-language');
assert.equal(old.viewer.open, false);
assert.equal(old.image.src, undefined);
assert.equal(old.image.alt, '');
assert.equal(old.caption.textContent, '');
assert.equal(old.status.textContent, '');
assert.equal(old.download.href, undefined);
assert.equal(old.download.hidden, true);
assert.equal(old.button.focused, undefined, 'Do not focus a departing locale');
assert.equal(motions.size, 0, 'All opening and tilt motion must stop');
assert.equal(classes.has('image-viewer-open'), false);
for (const target of [old.button, old.viewer, old.image, old.stage, old.close]) {
  assert.equal(Object.values(target.listeners).flat().length, 0);
}
assert.equal(window.listeners.blur.length, 0);
assert.equal(reduced.listeners.change.length, 0);
current = page('Japanese');
document.emit('rep0rter:language-applied');
assert.equal(current.button.disabled, false);
assert.equal(current.button.listeners.click.length, 1);
assert.equal(window.listeners.blur.length, 1);
current.button.emit('click');
assert.equal(current.viewer.open, true);
assert.equal(current.image.src, '/Japanese.png');
assert.equal(current.status.textContent, 'Japanese loading');
old.image.emit('load');
assert.equal(current.viewer.dataset.imageState, 'loading');
assert.equal(old.viewer.open, false);
""")


def test_image_loading_error_and_close_keep_controls_and_content_consistent():
    run_viewer(r"""
current.button.emit('click');
assert.equal(current.viewer.dataset.imageState, 'loading');
assert.equal(current.status.textContent, 'English loading');
assert.equal(current.download.hidden, true);
assert.equal(current.close.focused.preventScroll, true);
assert.equal(classes.has('image-viewer-open'), true);
movePointer();
assert.equal(motions.size, 1, 'A loading card should not tilt');
current.image.emit('error');
assert.equal(current.viewer.dataset.imageState, 'error');
assert.equal(current.status.textContent, 'English error');
assert.equal(current.download.hidden, true);
ready();
assert.equal(current.viewer.dataset.imageState, 'ready');
assert.equal(current.status.textContent, '');
assert.equal(current.download.hidden, false);
assert.equal(current.download.href, '/English.png');
assert.equal(current.image.alt, 'English');
assert.equal(current.caption.textContent, 'English');
let prevented = false;
current.viewer.emit('cancel', {preventDefault() {prevented = true;}});
finishMotions();
assert.equal(prevented, true);
assert.equal(current.viewer.open, false);
assert.equal(current.button.focused.preventScroll, true);
assert.equal(classes.has('image-viewer-open'), false);
assert.equal(current.image.src, undefined);
assert.equal(current.download.hidden, true);
assert.equal(root.style['--viewer-depth'], undefined);
current.image.emit('error');
current.image.emit('load');
assert.equal(current.viewer.dataset.imageState, undefined);
assert.equal(current.status.textContent, '');
""")


def test_queued_close_from_previous_image_does_not_clear_reopened_card():
    run_viewer(r"""
current.button.emit('click');
ready();
current.close.emit('click');
finishMotions();
assert.equal(current.viewer.open, false);
current.button.dataset.imageSrc = '/second.png';
current.thumbnail.alt = 'Second image';
current.button.emit('click');
current.viewer.emit('close');
assert.equal(current.viewer.open, true);
assert.equal(current.image.src, '/second.png');
assert.equal(current.image.alt, 'Second image');
assert.equal(current.caption.textContent, 'Second image');
assert.equal(current.download.href, '/second.png');
assert.equal(classes.has('image-viewer-open'), true);
finishMotions();
assert.equal(current.viewer.style.opacity, '1');
""")


def test_reduced_motion_stops_tilt_and_touch_cancellation_returns_card_to_rest():
    run_viewer(r"""
current.button.emit('click');
ready();
finishMotions();
current.stage.emit('pointerdown', {isPrimary: true, pointerType: 'touch', pointerId: 7, clientX: 300, clientY: 180});
finishMotions();
assert.notEqual(current.card.style['--card-ry'], '0deg');
current.stage.emit('pointercancel', {pointerId: 7});
finishMotions();
assert.equal(current.card.style['--card-ry'], '0deg');
assert.equal(current.card.style['--card-light'], '0');
current.stage.emit('pointermove', {pointerType: 'touch', pointerId: 7, clientX: 300, clientY: 180});
assert.equal(motions.size, 0, 'Cancelled fingers must not reactivate tilt');
movePointer();
assert.ok(motions.size > 0);
reduced.matches = true;
reduced.emit('change');
assert.equal(motions.size, 0);
assert.equal(current.card.style['--card-rx'], '0deg');
assert.equal(current.card.style['--card-ry'], '0deg');
assert.equal(current.card.style['--card-light'], '0');
movePointer();
assert.equal(motions.size, 0);
current.close.emit('click');
assert.equal(current.viewer.open, false, 'Reduced-motion dismissal is immediate');
""")


def test_page_cache_restoration_rebinds_once_after_motion_cleanup():
    run_viewer(r"""
current.button.emit('click');
ready();
movePointer();
window.emit('pagehide');
assert.equal(motions.size, 0);
assert.equal(current.viewer.open, false);
assert.equal(current.image.src, undefined);
assert.equal(classes.has('image-viewer-open'), false);
assert.equal(current.button.listeners.click.length, 0);
window.emit('pageshow', {persisted: true});
window.emit('pageshow', {persisted: true});
assert.equal(current.button.listeners.click.length, 1);
assert.equal(current.image.listeners.load.length, 1);
assert.equal(current.stage.listeners.pointermove.length, 1);
assert.equal(window.listeners.blur.length, 1);
current.button.emit('click');
assert.equal(current.viewer.open, true);
assert.equal(current.image.src, '/English.png');
""")


def test_theme_sync_updates_original_artwork_and_both_downloads_without_reopening():
    run_viewer(r"""
assert.equal(current.source.media, 'not all');
assert.equal(current.articleDownload.href, '/English.png');
assert.equal(observers.size, 1);
current.button.emit('click');
ready();
finishMotions();
const caption = current.caption.textContent;
current.image.complete = false;
current.image.naturalWidth = 0;
root.dataset.theme = 'dark';
for (const observer of observers) observer.callback();
assert.equal(current.source.media, 'all');
assert.equal(current.button.dataset.imageSrc, '/English-dark.png');
assert.equal(current.articleDownload.href, '/English-dark.png');
assert.equal(current.image.src, '/English-dark.png');
assert.equal(current.download.href, '/English-dark.png');
assert.equal(current.viewer.dataset.imageState, 'loading');
assert.equal(current.viewer.open, true);
assert.equal(current.caption.textContent, caption);
assert.equal(motions.size, 0, 'Changing artwork must not replay modal entrance');
ready();
assert.equal(current.download.hidden, false);
root.dataset.theme = 'light';
window.Rep0rterImages.sync();
assert.equal(current.source.media, 'not all');
assert.equal(current.image.src, '/English.png');
assert.equal(current.articleDownload.href, '/English.png');
assert.equal(current.download.href, '/English.png');
document.emit('rep0rter:before-language');
assert.equal(observers.size, 0);
assert.equal(window.Rep0rterImages, undefined);
current = page('Japanese');
root.dataset.theme = 'dark';
document.emit('rep0rter:language-applied');
assert.equal(observers.size, 1);
assert.equal(current.source.media, 'all');
current.button.emit('click');
assert.equal(current.image.src, '/Japanese-dark.png');
""")
