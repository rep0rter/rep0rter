"""Behavioral regressions for combined, shareable local report search."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest

NODE = shutil.which("node")
SCRIPT = Path(__file__).resolve().parents[1] / "rep0rter/templates/reading.js"
pytestmark = pytest.mark.skipif(not NODE, reason="Node.js required for browser script regressions")


def run_js(checks, query="", duplicate_names=False):
    harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
function element(properties = {}) {
  return Object.assign({hidden:false, dataset:{}, value:'', attrs:{}, children:[], listeners:{},
    addEventListener(type, handler) { (this.listeners[type] ??= []).push(handler); },
    removeEventListener(type, handler) { this.listeners[type] = (this.listeners[type] ?? []).filter(item => item !== handler); },
    emit(type, event = {}) { for (const handler of this.listeners[type] ?? []) handler(event); },
    setAttribute(key, value) { this.attrs[key] = value; },
    append(child) { this.appendCount = (this.appendCount || 0) + 1; this.children = this.children.filter(item => item !== child); this.children.push(child); },
    focus() { this.focused = true; },
  }, properties);
}
const input = element();
const form = element({hidden:true, querySelector() {return input;}});
const controls = Object.fromEntries(['period','from','to','topic','author','source','sort'].map(key => [key, element()]));
controls.period.value='all'; controls.sort.value='newest';
const customDates = element({hidden:true});
const filters = element({hidden:true, querySelector(selector) {
  if (selector === '[data-custom-dates]') return customDates;
  return controls[selector.match(/"(.*?)"/)[1]];
}});
const clear=element(), status=element({dataset:{resultTemplate:'{count} reports found'}}), empty=element(), reset=element(), allReset=element(), error=element(), badge=element();
const definitions = [
  ['19','2026-09-19','a','slack','civic','Civic tech','Open map data'],
  ['18','2026-09-19','b','mastodon','event','Events','Community workshop'],
  ['13','2026-09-13','a','slack','event','Events','Open source workshop'],
  ['12','2026-09-12','b','slack','civic','Civic tech','Open data archive'],
  ['8','2026-08-20','b','mastodon','event','Events','Older community event'],
];
const articles = definitions.map(([id,date,author,source,topic,label,search]) => element({dataset:{
  postId:id,date,timestamp:String(Date.parse(date+'T12:00:00Z')/1000),author,authorLabel:author==='a'?'Alice':'Bob',
  source,sourceLabel:source==='slack'?'Slack':'Mastodon',topics:JSON.stringify([{id:topic,label}]),search,
}}));
const container = element();
const days = [...new Set(definitions.map(row=>row[1]))].map(date => {
  const stories=articles.filter(article=>article.dataset.date===date);
  const grid=element({children:[...stories]}), count=element();
  const day=element({date,grid,count,parentElement:container,contains(article){return stories.includes(article);},
    querySelector(selector){return selector==='.story-grid'?grid:count;},
  });
  container.append(day); return day;
});
const news=element({scrollIntoView(){this.scrolled=true;}});
const single={'[data-search-form]':form,'[data-search-clear]':clear,'#search-status':status,'[data-no-results]':empty,
 '[data-filters]':filters,'[data-filter-count]':badge,'[data-filter-error]':error,'[data-search-reset]':reset,'[data-filters-reset]':allReset,'#news':news};
const document = element({documentElement:{lang:'en',dataset:{}}, createElement(){return element();},
 querySelector(selector){assert.ok(selector in single,selector);return single[selector];},
 querySelectorAll(selector){if(selector==='.day')return days;if(selector==='article[data-search]')return articles;throw Error(selector);},
});
const window=element({location:{href:'https://example.test/index.html'+QUERY},history:{state:{retained:true},replaceState(state,title,url){assert.equal(state.retained,true);window.location.href=url;}}});
class FrozenDate extends Date {constructor(...args){super(...(args.length?args:['2026-09-18T17:00:00Z']));}}
vm.runInNewContext(SCRIPT,{document,window,URL,Date:FrozenDate,Intl});
function choose(name,value){controls[name].value=value;controls[name].emit('change');}
function search(value){input.value=value;input.emit('input');}
function visible(){return articles.filter(article=>!article.hidden).map(article=>article.dataset.postId);}
function params(){return new URL(window.location.href).searchParams;}
""".replace("QUERY", json.dumps(query)).replace("SCRIPT", json.dumps(SCRIPT.read_text()))
    if duplicate_names:
        harness = harness.replace("author==='a'?'Alice':'Bob'", "'Alex'")
    result = subprocess.run([NODE, "-"], input=harness + checks, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_filters_compose_with_query_and_reset_without_navigation():
    run_js("""
assert.equal(form.hidden,false);assert.equal(filters.hidden,false);
choose('topic','event');choose('source','slack');choose('author','a');search('ＯＰＥＮ workshop');
assert.deepEqual(visible(),['13']);assert.equal(status.textContent,'1 reports found');
assert.equal(document.documentElement.dataset.searchActive,'true');
assert.equal(badge.textContent,'3');
clear.emit('click');assert.equal(input.value,'');assert.deepEqual(visible(),['13']);
choose('author','b');assert.deepEqual(visible(),[]);assert.equal(empty.hidden,false);
reset.emit('click');assert.equal(visible().length,5);assert.equal(input.focused,true);
assert.equal(status.hidden,true);assert.equal(badge.hidden,true);assert.equal(params().toString(),'');
assert.equal(document.documentElement.dataset.searchActive,'false');
form.emit('submit',{preventDefault(){}});assert.equal(news.scrolled,true);
""")


def test_recent_periods_use_taipei_calendar_days_and_custom_range_is_inclusive():
    run_js("""
choose('period','7');assert.deepEqual(visible(),['19','18','13']);
choose('period','30');assert.deepEqual(visible(),['19','18','13','12']);
choose('period','90');assert.equal(visible().length,5);
choose('period','custom');assert.equal(customDates.hidden,false);assert.equal(controls.from.disabled,false);
choose('from','2026-09-12');choose('to','2026-09-13');assert.deepEqual(visible(),['13','12']);
choose('from','2026-09-14');assert.equal(error.hidden,false);assert.equal(controls.from.attrs['aria-invalid'],'true');assert.deepEqual(visible(),[]);
choose('to','2026-09-19');assert.equal(error.hidden,true);assert.deepEqual(visible(),['19','18']);
choose('period','all');assert.equal(customDates.hidden,true);assert.equal(controls.from.disabled,true);
assert.equal(params().has('from'),false);assert.equal(params().has('to'),false);
""")


def test_urls_restore_filters_preserve_unrelated_values_and_respond_to_history():
    run_js("""
assert.equal(filters.open,true);assert.deepEqual(visible(),['13']);
search('source');assert.equal(params().get('utm_source'),'friend');assert.equal(new URL(window.location.href).hash,'#news');
assert.equal(params().get('q'),'source');assert.equal(params().get('author'),'a');
window.location.href='https://example.test/index.html?source=mastodon&sort=oldest';window.emit('popstate');
assert.equal(input.value,'');assert.equal(controls.author.value,'');assert.deepEqual(visible(),['18','8']);
assert.deepEqual(container.children.map(day=>day.date),['2026-08-20','2026-09-12','2026-09-13','2026-09-19']);
assert.deepEqual(days[0].grid.children.map(article=>article.dataset.postId),['18','19']);
choose('sort','newest');assert.deepEqual(days[0].grid.children.map(article=>article.dataset.postId),['19','18']);
""", "?q=workshop&author=a&period=7&utm_source=friend#news")


def test_unknown_or_withdrawn_facets_are_not_reconstructed_from_url():
    run_js("""
assert.equal(visible().length,5);assert.equal(controls.author.value,'');assert.equal(controls.topic.value,'');
assert.equal(controls.source.value,'');assert.equal(controls.period.value,'all');assert.equal(controls.sort.value,'newest');
assert.equal(controls.from.value,'');
assert.deepEqual(controls.author.children.map(option=>option.textContent),['Alice','Bob']);
assert.deepEqual(controls.topic.children.map(option=>option.value),['civic','event']);
search('open');assert.equal(params().has('author'),false);assert.equal(params().has('topic'),false);
""", "?author=withdrawn-person&topic=withdrawn-topic&source=unknown&period=bad&sort=bad&from=2026-02-30")


def test_accounts_with_matching_display_names_are_distinguishable():
    run_js("""
assert.deepEqual(controls.author.children.map(option=>option.textContent),['Alex · Mastodon / Slack','Alex · Slack']);
choose('author','a');assert.deepEqual(visible(),['19','13']);
""", duplicate_names=True)


def test_typing_and_changing_facets_do_not_reinsert_reports():
    run_js("""
const counts = () => [container.appendCount, ...days.map(day=>day.grid.appendCount)];
const initial = counts();
search('open');choose('topic','civic');clear.emit('click');
assert.deepEqual(counts(),initial);
choose('sort','oldest');assert.notDeepEqual(counts(),initial);
const afterSort=counts();search('data');assert.deepEqual(counts(),afterSort);
""")


def test_language_replacement_disposes_stale_search_listeners_and_rebinds():
    run_js("""
search('Open');
assert.equal(window.listeners.popstate.length, 1);
document.emit('rep0rter:before-language');
assert.equal(window.listeners.popstate.length, 0);
assert.equal(input.listeners.input.length, 0);
assert.equal(form.listeners.submit.length, 0);
// Simulate translated metadata and input replacing the previous document.
articles[0].dataset.search = 'Translated title';
window.location.href = 'https://example.test/?lang=JA&q=Translated';
document.emit('rep0rter:language-applied');
assert.equal(window.listeners.popstate.length, 1);
assert.equal(input.listeners.input.length, 1);
assert.deepEqual(visible(), ['19']);
assert.equal(news.scrolled, undefined);
search('Community');
assert.deepEqual(visible(), ['18', '8']);
assert.equal(params().get('lang'), 'JA');
document.emit('rep0rter:before-language');
document.emit('rep0rter:language-applied');
assert.equal(window.listeners.popstate.length, 1);
assert.equal(input.listeners.input.length, 1);
""")
