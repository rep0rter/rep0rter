"""Offline regressions for localized feeds, durable URLs, and generated assets."""

import hashlib
import json
import time
from dataclasses import replace
from urllib.parse import unquote, urljoin, urlsplit
from xml.etree import ElementTree

import pytest
from bs4 import BeautifulSoup
from PIL import Image

from rep0rter.config import Config
from rep0rter.publishers import site
from rep0rter.store import Container, Event, Post, Store


EDITIONS = {
    "zh-TW": ("index.html", "feed.xml"),
    "ko": ("index.ko.html", "feed.ko.xml"),
    "ja": ("index.ja.html", "feed.ja.xml"),
    "en": ("index.en.html", "feed.en.xml"),
}


@pytest.fixture
def published_site(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path, site_url="https://example.test/reporter")
    rendered_ids = []

    def render(renderer, event, container, names):
        rendered_ids.append(event.id)
        filename = hashlib.sha256(event.id.encode()).hexdigest() + ".png"
        path = renderer.cfg.site_dir / "cards" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (2, 2), "white").save(path)
        return path

    monkeypatch.setattr(site.CardRenderer, "render", render)
    with Store(cfg.db_path) as store:
        container = Container(id="slack:C_TEST", source="slack", name="Original channel")
        store.upsert_container(container)
        entries = []
        for index in range(2):
            event = Event(
                id=f"slack:C_TEST:{1700000000 + index}", source="slack", kind="message",
                container_id=container.id, ts=1700000000 + index,
                author_name='Author <script>alert("author")</script>',
                text='Original <script>alert("text")</script> & story',
                url=f"https://source.example.test/messages/{index}",
            )
            store.upsert_events([event])
            translations = {
                language: {
                    "headline": f'{language}: <img src=x onerror="alert(1)"> & title {index}',
                    "summary": f'{language}: <script>alert("summary")</script> & summary {index}',
                }
                for language in EDITIONS
            }
            post = Post(
                event_id=event.id, published_at=1700000100 + index, score=10,
                headline=f"Saved title {index}", summary=f"Saved summary {index}",
                translations=translations,
            )
            post.id = store.add_post(post)
            entries.append((post, event))
        site.build(store, cfg, limit=1)
        yield store, cfg, container, entries, rendered_ids


def html(path):
    return BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")


def local_path(cfg, url):
    """Resolve like a static web server: URL escapes are decoded before lookup."""
    base = urlsplit(cfg.site_url.rstrip("/") + "/")
    parsed = urlsplit(url)
    assert parsed.netloc == base.netloc
    assert parsed.path.startswith(base.path)
    return cfg.site_dir / unquote(parsed.path[len(base.path):])


def test_four_editions_escape_content_and_preserve_legacy_feed_guids(published_site):
    _, cfg, _, entries, rendered_ids = published_site
    latest, latest_event = entries[-1]
    assert set(rendered_ids) == {event.id for _, event in entries}
    for language, (page, feed) in EDITIONS.items():
        doc = html(cfg.site_dir / page)
        assert doc.html["lang"] == language
        assert doc.select_one("h3").get_text() == latest.translations[language]["headline"]
        assert doc.select_one("p.summary").get_text() == latest.translations[language]["summary"]
        assert len(doc.select("article")) == 1
        assert not doc.select("article script, article img[onerror]")
        assert set(a["data-language"] for a in doc.select("a[data-language]")) == set(EDITIONS)
        assert doc.select_one('[aria-current="page"]')["data-language"] == language

        tree = ElementTree.parse(cfg.site_dir / feed)
        assert tree.findtext("channel/language") == language
        items = tree.findall("channel/item")
        assert len(items) == 1
        item = items[0]
        assert item.findtext("title") == latest.translations[language]["headline"]
        assert latest.translations[language]["summary"] in item.findtext("description")
        # Deployed readers know numeric GUIDs; fixing Event.id must not replay old stories.
        assert item.findtext("guid") == str(latest.id)
        assert item.find("guid").get("isPermaLink") == "false"
        assert item.findtext("guid") != latest_event.id
        assert local_path(cfg, item.findtext("link")).is_file()
        enclosure = item.find("enclosure")
        assert enclosure.get("type") == "image/png"
        assert local_path(cfg, enclosure.get("url")).stat().st_size == int(enclosure.get("length"))


def test_older_stories_keep_permanent_pages_outside_homepage_window(published_site):
    _, cfg, _, entries, _ = published_site
    oldest, original = entries[0]
    for language, (page, _) in EDITIONS.items():
        assert not html(cfg.site_dir / page).find("article", id=str(oldest.id))
        permanent = cfg.site_dir / "posts" / str(oldest.id) / page
        doc = html(permanent)
        assert doc.select_one("article")["id"] == str(oldest.id)
        assert doc.select_one("blockquote").get_text() == original.text
        assert doc.select_one("details").has_attr("open")
        assert doc.select_one('meta[property="og:type"]')["content"] == "article"


def test_source_rename_keeps_same_url_and_source_history(published_site):
    store, cfg, container, entries, _ = published_site
    before = html(cfg.site_dir / "index.html").select_one("a.channel")["href"]
    store.upsert_container(replace(container, name="Renamed 日本 한국"))
    site.build(store, cfg, limit=1)
    channel = html(cfg.site_dir / "index.html").select_one("a.channel")
    assert channel["href"] == before
    source_page = local_path(cfg, urljoin(cfg.site_url + "/", channel["href"]))
    doc = html(source_page)
    assert "Renamed 日本 한국" in doc.title.get_text()
    assert {node["id"] for node in doc.select("article")} == {str(post.id) for post, _ in entries}


def test_all_local_links_and_branding_assets_exist(published_site):
    _, cfg, _, _, _ = published_site
    for path in cfg.site_dir.rglob("*.html"):
        doc = html(path)
        page_url = cfg.site_url + "/" + path.relative_to(cfg.site_dir).as_posix()
        for node in doc.select("a[href], link[href], script[src], img[src]"):
            url = urljoin(page_url, node.get("href") or node.get("src"))
            if urlsplit(url).netloc == urlsplit(cfg.site_url).netloc:
                assert local_path(cfg, url).is_file(), (path, url)
        assert doc.select_one('link[rel="icon"]')
        assert doc.select_one("a.wordmark img")
        image = doc.select_one('meta[property="og:image"]')["content"]
        assert local_path(cfg, image).is_file()
        for node in doc.select('meta[property="og:url"], link[rel="canonical"]'):
            assert local_path(cfg, node.get("content") or node.get("href")).is_file()


def test_failed_generation_keeps_complete_previous_release(published_site,monkeypatch):
    store,cfg,_,_,_=published_site
    old_target=cfg.site_dir.resolve()
    old_page=(cfg.site_dir/'index.html').read_bytes()
    def broken(*args,**kwargs):
        raise RuntimeError('renderer unavailable')
    monkeypatch.setattr(site.CardRenderer,'render',broken)
    with pytest.raises(RuntimeError): site.build(store,cfg)
    assert cfg.site_dir.resolve()==old_target
    assert (cfg.site_dir/'index.html').read_bytes()==old_page
    assert len(list((cfg.data_dir/'.site-releases').iterdir()))==1


@pytest.mark.parametrize('state', ['unknown', 'partial', 'stale', 'complete'])
def test_source_freshness_is_not_confused_with_site_generation(published_site, state):
    from rep0rter.i18n import COPY
    store, cfg, _, _, _ = published_site
    last_complete = time.time() - (10000 if state == 'stale' else 60)
    health = {} if state == 'unknown' else {
        'healthy': state != 'partial', 'last_healthy_at': last_complete,
    }
    store.set_kv('collector_health', json.dumps(health))
    site.build(store, cfg)
    for language, (page, _) in EDITIONS.items():
        doc = html(cfg.site_dir / page)
        notice = doc.select_one('.source-update-status')
        if state == 'complete':
            assert notice is None
        else:
            assert notice.get_text() == COPY[language]['collection_delayed']
        footer = doc.footer.get_text()
        assert COPY[language]['updated'] in footer
        if state == 'unknown':
            assert COPY[language]['healthy'] not in footer
        else:
            assert COPY[language]['healthy'] + ' ' + site._fmt_local(last_complete) in footer


def link_revisions(store, entries):
    from rep0rter import stories
    stories.ensure(store)
    old, original = entries[0]
    with store.conn:
        store.conn.execute('INSERT INTO stories VALUES(?,?,?)', ('test-revisions', 1, original.id))
        for revision, (post, event) in enumerate(entries, 1):
            event = replace(event, text=event.text + f' Revision {revision}')
            store.upsert_events([event])
            store.conn.execute('INSERT INTO story_events VALUES(?,?,?,?)',
                               (event.id, 'test-revisions', stories.fingerprint(event), 1))
            store.conn.execute('INSERT INTO story_posts VALUES(?,?,?,?,?,?)',
                               ('test-revisions', revision, post.id, stories.fingerprint(event), json.dumps([event.id]), post.published_at))


def test_superseded_pages_cards_and_feeds_link_latest_without_repeating_stale_og(published_site):
    from rep0rter.i18n import COPY
    store, cfg, _, entries, _ = published_site
    link_revisions(store, entries)
    old, latest = entries[0][0], entries[-1][0]
    site.build(store, cfg)
    for language, (page, feed) in EDITIONS.items():
        old_path = cfg.site_dir / 'posts' / str(old.id) / page
        old_url = cfg.site_url + f'/posts/{old.id}/{page}'
        expected = cfg.site_url + f'/posts/{latest.id}/{page}'
        doc = html(old_path)
        notice = doc.select_one('.revision-notice')
        assert COPY[language]['superseded'] in notice.get_text()
        assert urljoin(old_url, notice.a['href']) == expected
        assert not doc.select_one('details.previous-summary').has_attr('open')
        assert doc.select_one('details.previous-summary .summary').get_text() == old.translations[language]['summary']
        assert COPY[language]['previous_report'] in doc.title.get_text()
        for node in doc.select('meta[name="description"], meta[property="og:description"]'):
            assert COPY[language]['superseded'] in node['content']
            assert latest.translations[language]['summary'] in node['content']
            assert old.translations[language]['summary'] not in node['content']
        assert not html(cfg.site_dir / 'posts' / str(latest.id) / page).select_one('.revision-notice')
        old_card = html(cfg.site_dir / page).find('article', id=str(old.id))
        assert old_card.select_one('.revision-notice a')['href'] == f'posts/{latest.id}/{page}'
        items = ElementTree.parse(cfg.site_dir / feed).findall('channel/item')
        assert {item.findtext('guid') for item in items} == {str(old.id), str(latest.id)}
        description = next(item.findtext('description') for item in items if item.findtext('guid') == str(old.id))
        assert description.startswith(COPY[language]['superseded'])
        assert expected in description


def test_latest_revision_link_does_not_point_to_withdrawn_content(published_site):
    from rep0rter.policy import add_rule, redact
    store, cfg, _, entries, _ = published_site
    link_revisions(store, entries)
    old, latest_event = entries[0][0], entries[-1][1]
    add_rule(store, 'event', latest_event.id)
    redact(store, [latest_event.id])
    site.build(store, cfg)
    for _, (page, _) in EDITIONS.items():
        old_doc = html(cfg.site_dir / 'posts' / str(old.id) / page)
        assert not old_doc.select_one('.revision-notice')
        assert old_doc.select_one('.summary')


def test_withdrawal_removes_all_editions_feeds_assets_and_prior_releases(published_site):
    from rep0rter.policy import add_rule,redact
    store,cfg,_,entries,_=published_site
    removed,event=entries[-1]
    add_rule(store,'event',event.id)
    redact(store,[event.id])
    site.build(store,cfg)
    assert len(list((cfg.data_dir/'.site-releases').iterdir()))==1
    for _,(page,feed) in EDITIONS.items():
        doc=html(cfg.site_dir/'posts'/str(removed.id)/page)
        assert not doc.select('article,blockquote,meta[property="og:image"]')
        assert removed.headline not in doc.get_text()
        assert not html(cfg.site_dir/page).find('article',id=str(removed.id))
        assert str(removed.id) not in [item.findtext('guid') for item in ElementTree.parse(cfg.site_dir/feed).findall('channel/item')]
    assert not (cfg.site_dir/'cards'/(hashlib.sha256(event.id.encode()).hexdigest()+'.png')).exists()
