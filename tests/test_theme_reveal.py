"""Behavior checks for theme snapshots, interrupted transitions, and fallbacks."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = shutil.which('node')
THEME = Path(__file__).resolve().parents[1] / 'rep0rter/templates/theme.js'

# Controllable browser transitions let callbacks run in adversarial order. This
# checks final user-visible state without depending on how theme.js is organized.
HARNESS = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const flush = async () => { for (let i=0;i<12;i++) await Promise.resolve(); };
function deferred() {
  let resolve, reject;
  const promise = new Promise((yes,no) => { resolve=yes; reject=no; });
  return {promise,resolve,reject};
}
function element(values={}) {
  const handlers = {};
  return Object.assign({dataset:{}, attributes:{}, hidden:false, open:false,
    addEventListener(name,fn) { (handlers[name] ??= []).push(fn); },
    emit(name,event={}) { (handlers[name] ?? []).forEach(fn => fn(event)); },
    dispatchEvent(event) { this.emit(event.type,event); return true; },
    setAttribute(name,value) { this.attributes[name]=value; },
    removeAttribute(name) {
      delete this.attributes[name];
      if (name.startsWith('data-')) delete this.dataset[name.slice(5).replace(/-([a-z])/g, (_,c)=>c.toUpperCase())];
    },
    focus(options) { assert.equal(options.preventScroll,true); this.focused=true; },
  },values);
}
function setup({reduced=false,api=true,blocked=false,saved=null,deviceDark=false}={}) {
  const animations=[],transitions=[],events=[],writes=[];
  const root=element({clientWidth:1000,clientHeight:800,style:{
    values:{},setProperty(key,value){this.values[key]=value;},removeProperty(key){delete this.values[key];}
  }});
  const trigger=element({dataset:{themeLabel:'Appearance',themeLight:'Light',themeDark:'Dark',themeSystem:'System'},
    querySelectorAll:()=>[], getBoundingClientRect:()=>({left:920,top:20,width:40,height:40})});
  const menu=element({querySelector:()=>trigger,contains:target=>target===trigger});
  const choices=['light','dark','system'].map(themeChoice=>element({dataset:{themeChoice},closest:()=>menu}));
  const device=element({matches:deviceDark}),motion=element({matches:reduced});
  const document=element({documentElement:root,querySelectorAll(selector) {
    return {'[data-theme-choice]':choices,'[data-theme-trigger]':[trigger],'[data-theme-controls]':[menu],'.preference-menu':[menu]}[selector] ?? [];
  }});
  const window=element({innerWidth:1000,innerHeight:800,matchMedia:q=>q.includes('reduced-motion')?motion:device,
    scrollTo(){ assert.fail('Theme must not scroll'); }});
  root.animate=(frames,options)=>{
    const ended=deferred();
    const animation={frames,options,finished:ended.promise,cancelled:false,
      cancel(){ this.cancelled=true; ended.reject(Error('animation cancelled')); },
      complete(){ ended.resolve(); }};
    animations.push(animation);return animation;
  };
  if(api) document.startViewTransition=callback=>{
    const ready=deferred(),updated=deferred(),finished=deferred();
    const transition={oldTheme:root.dataset.theme,menuOpen:menu.open,
      ready:ready.promise,updateCallbackDone:updated.promise,finished:finished.promise,skipped:false,
      skipTransition(){ this.skipped=true; ready.reject(Error('transition skipped')); finished.resolve(); },
      async begin(){ await callback(); this.newTheme=root.dataset.theme;updated.resolve();ready.resolve();await flush(); },
      complete(){finished.resolve();},
      async fail(){await callback();updated.resolve();ready.reject(Error('snapshot unavailable'));finished.resolve();await flush();},
    };
    transitions.push(transition);return transition;
  };
  const storage={getItem(){if(blocked)throw Error('blocked');return saved;},setItem(key,value){if(blocked)throw Error('blocked');writes.push([key,value]);}};
  class CustomEvent { constructor(type,options={}) {this.type=type;this.detail=options.detail;} }
  document.addEventListener('rep0rter:theme-reveal',event=>events.push(event));
  vm.runInNewContext(SOURCE,{document,window,localStorage:storage,CustomEvent,Math,Promise,setTimeout,clearTimeout});
  document.emit('DOMContentLoaded');
  const choose=mode=>{menu.open=true;choices.find(choice=>choice.dataset.themeChoice===mode).emit('click');};
  return {root,menu,trigger,choices,device,motion,document,window,animations,transitions,events,writes,choose};
}
'''


def run_case(body):
    script = HARNESS.replace('SOURCE', json.dumps(THEME.read_text()))
    script += '\n(async()=>{\n' + body + '\nawait flush();})().catch(error=>{console.error(error);process.exitCode=1;});'
    result = subprocess.run([NODE, '--unhandled-rejections=strict', '-'], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


pytestmark = pytest.mark.skipif(not NODE, reason='Node.js required')


def test_reveal_snapshots_old_and_new_theme_after_menu_closes():
    run_case(r'''
const s=setup();
assert.equal(s.root.dataset.theme,'light');
assert.equal(s.transitions.length,0,'First paint must not animate');
s.choose('dark');
assert.equal(s.menu.open,false);
assert.equal(s.trigger.focused,true);
assert.equal(s.transitions.length,1);
assert.equal(s.transitions[0].menuOpen,false,'No open-menu snapshot');
assert.equal(s.transitions[0].oldTheme,'light','Old snapshot keeps old palette');
assert.equal(s.root.style.values['--theme-reveal-x'],'940px','Initial CSS mask uses button origin');
assert.equal(s.root.style.values['--theme-reveal-y'],'40px');
assert.equal(s.root.dataset.theme,'light','Palette changes inside callback');
await s.transitions[0].begin();
assert.equal(s.root.dataset.theme,'dark');
assert.equal(s.animations.length,1);
const {frames,options}=s.animations[0];
assert.equal(options.duration,500);
assert.equal(options.easing,'ease-in-out');
assert.equal(options.pseudoElement,'::view-transition-new(root)');
assert.match(JSON.stringify(frames),/circle\(0(?:px)? at 940px 40px\)/);
assert.equal(s.events.length,1);
const detail=s.events[0].detail;
assert.equal(detail.x,940);assert.equal(detail.y,40);
assert.ok(detail.radius>=Math.hypot(940,760),'Reveal must cover farthest corner');
s.animations[0].complete();s.transitions[0].complete();await flush();
assert.equal(s.root.dataset.themeTransition,undefined);
s.choose('light');await s.transitions[1].begin();
assert.equal(s.root.dataset.theme,'light');
assert.equal(s.animations.length,2,'Reverse direction also reveals');
s.animations[1].complete();s.transitions[1].complete();await flush();
assert.equal(s.root.dataset.themeTransition,undefined);
''')


def test_delayed_obsolete_callback_cannot_overwrite_last_choice():
    run_case(r'''
const s=setup();s.choose('dark');
const obsolete=s.transitions[0];
s.choose('light');
assert.equal(obsolete.skipped,true);
await obsolete.begin();await flush();
assert.equal(s.root.dataset.theme,'light');
assert.equal(s.trigger.dataset.themePreference,'light');
assert.equal(s.root.dataset.themeTransition,undefined);
assert.equal(s.animations.length,0);
assert.deepEqual(s.writes.at(-1),['rep0rter-theme','light']);
''')


def test_running_animation_cancellation_preserves_newer_transition_marker():
    run_case(r'''
const s=setup();s.choose('dark');await s.transitions[0].begin();
const first=s.transitions[0],firstAnimation=s.animations[0];
s.choose('light');await flush();
assert.equal(first.skipped,true);assert.equal(firstAnimation.cancelled,true);
assert.equal(s.transitions.length,2);
await s.transitions[1].begin();
assert.equal(s.root.dataset.theme,'light');
first.complete();await flush();
assert.equal(s.root.dataset.themeTransition,'reveal','Old cleanup cannot remove new transition scope');
s.animations[1].complete();s.transitions[1].complete();await flush();
assert.equal(s.root.dataset.themeTransition,undefined);
''')


@pytest.mark.parametrize('options', [{'api': False}, {'reduced': True}, {'api': False, 'blocked': True}])
def test_direct_fallbacks_keep_menu_and_preference_functional(options):
    run_case('const s=setup(' + json.dumps(options) + r''');
s.choose('dark');
assert.equal(s.root.dataset.theme,'dark');assert.equal(s.menu.open,false);
assert.equal(s.trigger.dataset.themePreference,'dark');
assert.equal(s.transitions.length,0);assert.equal(s.animations.length,0);
assert.equal(s.root.dataset.themeTransition,undefined);
''')


def test_same_effective_palette_system_changes_and_storage_do_not_animate():
    run_case(r'''
const s=setup({saved:'dark',deviceDark:true});
s.choose('system');
assert.equal(s.root.dataset.theme,'dark');
assert.equal(s.trigger.dataset.themePreference,'system');
assert.equal(s.transitions.length,0);
s.device.matches=false;s.device.emit('change');
assert.equal(s.root.dataset.theme,'light');assert.equal(s.transitions.length,0);
s.window.emit('storage',{key:'rep0rter-theme',newValue:'dark'});
assert.equal(s.root.dataset.theme,'dark');assert.equal(s.transitions.length,0);
s.window.emit('storage',{key:'rep0rter-theme',newValue:null});
assert.equal(s.trigger.dataset.themePreference,'system');assert.equal(s.root.dataset.theme,'light');
''')


@pytest.mark.parametrize('interruption', ['storage', 'pagehide', 'motion', 'language'])
def test_external_interruptions_remove_overlay_and_handle_rejected_animation(interruption):
    interrupt = {
        'storage': "s.window.emit('storage',{key:'rep0rter-theme',newValue:'light'});",
        'pagehide': "s.window.emit('pagehide');",
        'language': "s.document.emit('rep0rter:before-language');",
        'motion': "s.motion.matches=true;s.motion.emit('change');",
    }[interruption]
    run_case(r'''
const s=setup();s.choose('dark');await s.transitions[0].begin();
''' + interrupt + r'''
await flush();assert.equal(s.transitions[0].skipped,true);
assert.equal(s.animations[0].cancelled,true);
assert.equal(s.root.dataset.themeTransition,undefined);
''')


def test_failed_snapshot_keeps_requested_theme_and_clears_transition_scope():
    run_case(r'''
const s=setup();s.choose('dark');await s.transitions[0].fail();
assert.equal(s.root.dataset.theme,'dark');
assert.equal(s.trigger.dataset.themePreference,'dark');
assert.equal(s.root.dataset.themeTransition,undefined);
assert.equal(s.animations.length,0);
''')


def test_storage_blocking_does_not_disable_reveal_or_current_session_preference():
    run_case(r'''
const s=setup({blocked:true});s.choose('dark');await s.transitions[0].begin();
assert.equal(s.root.dataset.theme,'dark');assert.equal(s.animations.length,1);
assert.equal(s.trigger.dataset.themePreference,'dark');
s.animations[0].complete();s.transitions[0].complete();await flush();
s.device.matches=true;s.device.emit('change');
assert.equal(s.trigger.dataset.themePreference,'dark');
assert.equal(s.transitions.length,1,'Explicit choice ignores device changes');
''')


@pytest.mark.parametrize('failure', ['capture', 'animation'])
def test_platform_animation_errors_still_apply_readable_final_theme(failure):
    setup_failure = (
        "s.document.startViewTransition=()=>{throw Error('capture unavailable');};"
        if failure == 'capture' else
        "s.root.animate=()=>{throw Error('pseudo-element animation unavailable');};"
    )
    run_case('const s=setup();' + setup_failure + r'''
s.choose('dark');
if(s.transitions.length) await s.transitions[0].begin();
await flush();
assert.equal(s.root.dataset.theme,'dark');
assert.equal(s.trigger.dataset.themePreference,'dark');
assert.equal(s.root.dataset.themeTransition,undefined);
''')


def test_repeated_language_initialization_does_not_duplicate_handlers():
    run_case(r'''
const s=setup();
s.document.emit('rep0rter:language-applied');
s.document.emit('rep0rter:language-applied');
s.choose('dark');await s.transitions[0].begin();
assert.equal(s.transitions.length,1);assert.equal(s.writes.length,1);
assert.equal(s.animations.length,1);
''')
