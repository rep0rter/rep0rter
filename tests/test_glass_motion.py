"""Check the browser-independent spring lifecycle, not CSS implementation details."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="Node.js required for spring regressions")
SOURCE = Path(__file__).resolve().parents[1] / "rep0rter" / "templates" / "glass-motion.js"


def run_motion(assertions, reduced=False):
    setup = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
let now = 0, nextFrame = 0;
const frames = new Map();
const listeners = [];
const media = {matches: REDUCED, addEventListener(type, callback) { listeners.push(callback); }};
const window = {matchMedia(query) { return query.includes('reduced-motion') ? media : {matches:false}; }};
// Leave DOMContentLoaded pending: these tests target the public physics API.
const document = {readyState:'loading', addEventListener() {}};
vm.runInNewContext(SOURCE, {
  window, document, performance: {now: () => now},
  requestAnimationFrame(callback) { frames.set(++nextFrame, callback); return nextFrame; },
  cancelAnimationFrame(id) { frames.delete(id); },
});
const spring = window.Rep0rterMotion.spring;
function advance(count = 1, milliseconds = 1000 / 60) {
  for (let step = 0; step < count; step++) {
    now += milliseconds;
    const batch = [...frames.values()]; frames.clear();
    batch.forEach(callback => callback(now));
  }
}
function reduceMotion() { media.matches = true; listeners.forEach(callback => callback()); }
""".replace("REDUCED", json.dumps(reduced)).replace("SOURCE", json.dumps(SOURCE.read_text()))
    result = subprocess.run([NODE, "-"], input=setup + assertions, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("rate", [60, 120])
def test_spring_settles_with_small_overshoot_and_stops_requesting_frames(rate):
    run_motion(f"""
const values = []; let completed = 0;
spring({{from:0,to:1,onUpdate: value => values.push(value),onComplete: () => completed++}});
assert.equal(values.length, 0);
advance({rate}, 1000 / {rate});
assert.equal(values.at(-1), 1);
assert.equal(completed, 1);
assert(values.every(value => Number.isFinite(value) && value >= 0 && value < 1.04));
assert(values.some(value => value > 0 && value < 1));
assert.equal(frames.size, 0, 'Settled UI must not keep a rendering loop alive');
advance(20);
assert.equal(completed, 1);
""")


def test_cancelled_spring_cannot_overwrite_a_replacement_or_complete_late():
    run_motion("""
let position = 0, speed = 0, staleCompleted = 0, replacementCompleted = 0;
const old = spring({from:0,to:1,onUpdate(value, velocity) { position=value; speed=velocity; },onComplete() { staleCompleted++; }});
advance(4);
assert(position > 0 && position < 1);
old.cancel();
spring({from:position,to:0,velocity:speed,onUpdate(value) { position=value; },onComplete() { replacementCompleted++; }});
advance(60);
assert.equal(position, 0);
assert.equal(staleCompleted, 0);
assert.equal(replacementCompleted, 1);
assert.equal(frames.size, 0);
""")


def test_motion_preference_change_finishes_concurrent_springs_exactly_once():
    run_motion("""
const positions = [0,0], completed = [0,0];
[1,120].forEach((target,index) => spring({from:0,to:target,onUpdate(value) { positions[index]=value; },onComplete() { completed[index]++; }}));
advance(3);
assert(positions[0] > 0 && positions[0] < 1);
reduceMotion();
assert.deepEqual(positions, [1,120]);
assert.deepEqual(completed, [1,1]);
assert.equal(frames.size, 0);
advance(60);reduceMotion();
assert.deepEqual(completed, [1,1]);
""")


def test_reduced_motion_completes_synchronously_without_scheduling_animation():
    run_motion("""
let position = 0, completed = 0;
const handle = spring({from:0,to:1,onUpdate(value) { position=value; },onComplete() { completed++; }});
assert.equal(position, 1);
assert.equal(completed, 1);
assert.equal(frames.size, 0);
handle.cancel();advance(60);
assert.equal(completed, 1);
""", reduced=True)
