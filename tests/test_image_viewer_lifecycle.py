"""Image sheets remain functional after a same-document locale replacement."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="Node.js required for viewer lifecycle regression")


def test_locale_replacement_disposes_open_viewer_and_binds_new_images():
    source = (Path(__file__).resolve().parents[1] / "rep0rter/templates/image-viewer.js").read_text()
    script = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
function element(extra = {}) {
  return Object.assign({listeners: {}, dataset: {}, style: {
    setProperty(name, value) {this[name] = value;}, removeProperty(name) {delete this[name];}
  },
  addEventListener(type, handler) {(this.listeners[type] ??= []).push(handler);},
  removeEventListener(type, handler) {this.listeners[type] = (this.listeners[type] || []).filter(item => item !== handler);},
  emit(type, event = {}) {for (const handler of this.listeners[type] || []) handler(event);},
  removeAttribute(name) {delete this[name];},
  getBoundingClientRect() {return {left: 10, top: 20, width: 320, height: 200};},
  focus() {this.focused = true;},
  }, extra);
}
function page(label) {
  const image = element(), caption = element(), download = element(), close = element();
  const toolbar = element({hasPointerCapture() {return false;}});
  const viewer = element({open: false, showModal() {this.open = true;}, close() {this.open = false;},
    querySelector(selector) {return {
      '[data-viewer-image]': image, '[data-viewer-caption]': caption,
      '[data-viewer-download]': download, '[data-viewer-close]': close,
      '.image-viewer-toolbar': toolbar,
    }[selector];}
  });
  const thumbnail = element({alt: label, naturalWidth: 1200, naturalHeight: 630});
  const button = element({disabled: true, isConnected: true, dataset: {imageSrc: '/' + label + '.png'},
    querySelector() {return thumbnail;}
  });
  return {viewer, button, image, close};
}
const root = element({classList: {add() {}, remove() {}}});
let current = page('English');
const document = element({documentElement: root,
  querySelector() {return current.viewer;}, querySelectorAll() {return [current.button];}
});
let cancellations = 0;
const window = {matchMedia() {return {matches: false};}, Rep0rterMotion: {
  spring({onUpdate}) {onUpdate(.5); return {cancel() {cancellations += 1;}};}
}};
vm.runInNewContext(SOURCE, {document, window});
const old = current;
old.button.emit('click');
assert.equal(old.viewer.open, true);
assert.equal(old.image.src, '/English.png');
document.emit('rep0rter:before-language');
assert.equal(old.viewer.open, false);
assert.equal(old.image.src, undefined);
assert.equal(old.button.focused, undefined);
assert.equal(old.button.listeners.click.length, 0);
assert.equal(old.viewer.listeners.cancel.length, 0);
assert.equal(cancellations, 1);
current = page('Japanese');
document.emit('rep0rter:language-applied');
assert.equal(current.button.disabled, false);
assert.equal(current.button.listeners.click.length, 1);
current.button.emit('click');
assert.equal(current.viewer.open, true);
assert.equal(current.image.src, '/Japanese.png');
assert.equal(old.viewer.open, false);
""".replace("SOURCE", json.dumps(source))
    result = subprocess.run([NODE, "-"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
