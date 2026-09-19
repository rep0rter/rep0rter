"""Offline regressions for localized feeds, durable URLs, and generated assets."""

import hashlib
import io
import json
import re
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
    "zh-TW": ("index.zh-TW.html", "feed.zh-TW.xml"),
    "ko": ("index.ko.html", "feed.ko.xml"),
    "ja": ("index.ja.html", "feed.ja.xml"),
    "en": ("index.html", "feed.xml"),
}


@pytest.fixture
def published_site(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path, site_url="https://example.test/reporter")
    rendered_ids = []

    def render(renderer, event, container, names, *, theme="light"):
        rendered_ids.append(event.id)
        filename = hashlib.sha256(event.id.encode()).hexdigest() + ("-dark" if theme == "dark" else "") + ".png"
        path = renderer.cfg.site_dir / "cards" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (2, 2), "black" if theme == "dark" else "white").save(path)
        return path

    monkeypatch.setattr(site.CardRenderer, "render", render)
    def render_report(renderer, event, container, post, language, *, theme="light"):
        path = renderer.cfg.site_dir / 'cards' / f'report-{post.id}-{language}{"-dark" if theme == "dark" else ""}.png'
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (2, 2), 'navy' if theme == 'dark' else 'blue').save(path)
        return path
    monkeypatch.setattr(site.CardRenderer, "render_report", render_report)
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


def test_author_avatar_publishes_one_local_asset_for_shared_photo(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path)
    buffer = io.BytesIO()
    Image.new("RGB", (12, 12), "teal").save(buffer, format="PNG")
    photo = buffer.getvalue()
    monkeypatch.setattr(site, "identity_image", lambda event, config: photo)
    event = Event("slack:C:1", "slack", "message", "slack:C", 1,
                  meta={"avatar_url": "https://identity.example.test/person.png"})

    asset = site._author_avatar(event, cfg)
    shared_asset = site._author_avatar(replace(event, id="slack:C:2"), cfg)

    assert shared_asset == asset
    assert asset.startswith("cards/avatar-")
    assert (cfg.site_dir / asset).read_bytes() == photo
    assert len(list((cfg.site_dir / "cards").glob("avatar-*.png"))) == 1


def test_author_avatar_without_identity_does_not_fetch_or_publish(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path)
    calls = []
    monkeypatch.setattr(site, "identity_image", lambda event, config: calls.append(event.id))
    event = Event("slack:C:1", "slack", "message", "slack:C", 1)

    assert site._author_avatar(event, cfg) == ""
    assert calls == []
    unavailable = replace(event, meta={"avatar_url": "https://identity.example.test/gone.png"})
    assert site._author_avatar(unavailable, cfg) == ""
    assert calls == [event.id]
    assert not (cfg.site_dir / "cards").exists()


def test_author_byline_preserves_local_photo_and_initial_fallback_across_editions(published_site, monkeypatch):
    store, cfg, _, entries, _ = published_site
    with_photo, without_photo = entries[-1], entries[0]
    photo_event = replace(with_photo[1], meta={"avatar_url": "https://identity.example.test/person.png"})
    store.upsert_events([photo_event])
    buffer = io.BytesIO()
    Image.new("RGB", (12, 12), "teal").save(buffer, format="PNG")
    photo = buffer.getvalue()
    monkeypatch.setattr(site, "identity_image", lambda event, config: photo)

    for _ in range(2):
        site.build(store, cfg)
        # Asset cleanup and subsequent atomic releases must retain byline media.
        assets = list((cfg.site_dir / "cards").glob("avatar-*.png"))
        assert len(assets) == 1
        assert assets[0].read_bytes() == photo
        for _, (page, _) in EDITIONS.items():
            paths = [cfg.site_dir / page,
                     cfg.site_dir / "posts" / str(with_photo[0].id) / page]
            for path in paths:
                article = html(path).find("article", id=str(with_photo[0].id))
                avatar = article.select_one(".byline [data-author-avatar]")
                assert avatar is not None
                assert avatar["alt"] == ""
                assert avatar.parent["aria-hidden"] == "true"
                assert avatar["width"] == avatar["height"] == "29"
                assert not urlsplit(avatar["src"]).netloc
                assert (path.parent / avatar["src"]).resolve() == assets[0].resolve()
                assert article.select_one(".byline").get_text().count(photo_event.author_name) == 1
                assert "identity.example.test" not in str(article)
            fallback_path = cfg.site_dir / "posts" / str(without_photo[0].id) / page
            fallback = html(fallback_path).select_one(".byline .avatar-initial")
            assert not fallback.select("img")
            assert fallback.get_text() == without_photo[1].author_name[0]


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
        preview = doc.select_one('[data-preview-post-id]')
        assert preview['data-preview-post-id'] == str(latest.id)
        assert latest.translations[language]['headline'] in preview.get_text()
        assert latest.translations[language]['summary'] in preview.get_text()
        assert not preview.select('script, img[onerror]')
        links = doc.select(".masthead-actions a[data-language]")
        assert {link['data-language'] for link in links} == set(EDITIONS)
        assert doc.select_one('.language-menu [aria-current=page]')['data-language'] == language

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
        assert doc.select_one("details.original-source").has_attr("open") == (language != 'en')
        assert doc.select_one('meta[property="og:type"]')["content"] == "article"


def test_home_highlights_show_three_latest_localized_stories_before_date_feed(published_site):
    store, cfg, _, entries, _ = published_site
    for index in range(3):
        event = replace(entries[-1][1], id=f'latest-preview:{index}', ts=1700001000 + index,
                        url=f'https://source.example.test/latest/{index}')
        store.upsert_events([event])
        translations = {
            language: {'headline': f'{language}: 最新消息 {index}', 'summary': f'{language}: 重點摘要 {index}'}
            for language in EDITIONS
        }
        post = Post(event_id=event.id, published_at=1700002000 + index, score=10,
                    headline=f'Latest {index}', summary=f'Summary {index}', translations=translations)
        post.id = store.add_post(post)
        entries.append((post, event))
    site.build(store, cfg)
    expected = list(reversed(entries))[:3]
    for language, (page, _) in EDITIONS.items():
        doc = html(cfg.site_dir / page)
        previews = doc.select('.hero-latest [data-preview-post-id]')
        assert [node['data-preview-post-id'] for node in previews] == [str(post.id) for post, _ in expected]
        assert not doc.select('.glass-scene')
        for index, (node, (post, _)) in enumerate(zip(previews, expected)):
            assert post.translations[language]['headline'] in node.get_text()
            assert (post.translations[language]['summary'] in node.get_text()) == (index == 0)
            assert node.select_one(f'a[href="posts/{post.id}/{page}"]')
            assert node.select_one('time')
            assert '#Original channel' in node.get_text()
            assert not node.has_attr('id'), 'Preview must not duplicate the article DOM id'
            assert not node.select('script, img[onerror]')
        assert len(doc.select('.story-days article')) == len(entries)
        assert [node['id'] for node in doc.select('.story-days article')] == [str(post.id) for post, _ in reversed(entries)]
        assert doc.select_one('.hero-latest').find_next(class_='story-days')
        ids = [node['id'] for node in doc.select('[id]')]
        assert len(ids) == len(set(ids))
        for path in (cfg.site_dir / 'posts' / str(expected[0][0].id) / page,
                     local_path(cfg, urljoin(cfg.site_url + '/', doc.select_one('a.channel')['href']))):
            assert not html(path).select('[data-preview-post-id]')


def test_english_default_and_existing_explicit_english_links(published_site):
    _, cfg, _, entries, _ = published_site
    doc = html(cfg.site_dir / "index.html")
    assert doc.html["lang"] == "en"
    links = doc.select("a[data-language]")
    assert len(links) == 4
    chinese = next(link for link in links if link['data-language'] == 'zh-TW')
    assert chinese['data-language'] == 'zh-TW'
    assert chinese["href"] == "index.zh-TW.html"
    assert html(cfg.site_dir / chinese["href"]).html["lang"] == "zh-TW"
    # Existing feed subscriptions, bookmarks, and links to source/history pages
    # stay valid; aliases advertise the canonical English URL.
    for canonical in cfg.site_dir.rglob("index.html"):
        alias = canonical.with_name("index.en.html")
        assert alias.read_bytes() == canonical.read_bytes()
        expected = cfg.site_url + "/" + canonical.relative_to(cfg.site_dir).as_posix()
        assert html(alias).select_one('link[rel="canonical"]')["href"] == expected
    assert (cfg.site_dir / "feed.en.xml").read_bytes() == (cfg.site_dir / "feed.xml").read_bytes()
    for filename in ("feed.xml", "feed.en.xml", "feed.zh-TW.xml"):
        assert ElementTree.parse(cfg.site_dir / filename).findtext("channel/item/guid") == str(entries[-1][0].id)


def test_language_switch_does_not_redirect_using_old_browser_preference(published_site):
    # Execute the actual served script against a minimal DOM; a stored choice
    # from the previous release must never take over the English root URL.
    import shutil
    import subprocess
    if not shutil.which("node"):
        pytest.skip("Node.js required to execute the language navigation script")
    _, cfg, _, _, _ = published_site
    script = """
const assert = require('node:assert/strict');
global.document = {
  documentElement: {lang: 'en'}, readyState: 'complete',
  addEventListener: () => {}, querySelectorAll: () => [],
};
global.location = new URL('https://example.test/');
location.replace = () => assert.fail('unexpected redirect');
global.window = {addEventListener: () => {}, history: {state: null,
  replaceState(state, title, url) {
    assert.equal(new URL(url).searchParams.get('lang'), 'EN');
    location.href = String(url);
  }
}};
global.fetch = () => assert.fail('old stored preference must not fetch another edition');
global.localStorage = {getItem: () => 'zh-TW'};
""" + (cfg.site_dir / "language.js").read_text()
    subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)


def test_english_report_cards_keep_originals_collapsed_and_feed_images_localized(published_site):
    _, cfg, _, entries, _ = published_site
    latest = entries[-1][0]
    for page in (cfg.site_dir / 'index.html', cfg.site_dir / f'posts/{latest.id}/index.html'):
        doc = html(page)
        article = doc.find('article', id=str(latest.id))
        report = article.select_one('figure img')
        assert report['src'].endswith(f'cards/report-{latest.id}-en.png')
        assert report['alt'].startswith('English report card.')
        assert 'Summary by rep0rter' in article.select_one('figcaption').get_text()
        original = article.select_one('details.original-source')
        assert not original.has_attr('open')
        assert original.select_one('img')['src'] != report['src']
        if 'posts' in str(page):
            assert doc.select_one('meta[property="og:image"]')['content'].endswith(report['src'].removeprefix('../../'))
    english_image = ElementTree.parse(cfg.site_dir / 'feed.xml').find('channel/item/enclosure').get('url')
    chinese_image = ElementTree.parse(cfg.site_dir / 'feed.zh-TW.xml').find('channel/item/enclosure').get('url')
    assert english_image != chinese_image
    assert english_image.endswith(f'cards/report-{latest.id}-en.png')


def test_missing_english_translation_uses_honest_pending_copy_without_saving_it(published_site, monkeypatch):
    store, cfg, _, entries, _ = published_site
    latest = entries[-1][0]
    translations = {key: value for key, value in latest.translations.items() if key != 'en'}
    with store.conn:
        store.conn.execute('UPDATE posts SET headline=?,summary=?,translations=? WHERE id=?',
                           ('原本的標題', '原本的摘要', json.dumps(translations), latest.id))
    captured = []
    original_render_report = site.CardRenderer.render_report
    def capture(renderer, event, container, post, language, *, theme="light"):
        if post.id == latest.id:
            captured.append(post.translations['en'])
        return original_render_report(renderer, event, container, post, language, theme=theme)
    monkeypatch.setattr(site.CardRenderer, 'render_report', capture)
    site.build(store, cfg)
    doc = html(cfg.site_dir / 'index.html')
    assert doc.select_one('h3').get_text() == 'English translation pending'
    assert 'Open the original or choose another language.' in doc.select_one('p.summary').get_text()
    assert captured[0]['headline'] == 'English translation pending'
    assert '原本的' not in (cfg.site_dir / 'feed.xml').read_text()
    saved = store.conn.execute('SELECT translations FROM posts WHERE id=?', (latest.id,)).fetchone()[0]
    assert 'en' not in json.loads(saved)


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
                if urlsplit(url).path == urlsplit(cfg.site_url).path.rstrip('/') + '/write':
                    continue  # Dynamic Flask route, covered by test_community.
                assert local_path(cfg, url).is_file(), (path, url)
        assert doc.select_one('link[rel="icon"]')
        assert doc.select_one("a.wordmark img")
        image = doc.select_one('meta[property="og:image"]')["content"]
        assert local_path(cfg, image).is_file()
        for node in doc.select('meta[property="og:url"], link[rel="canonical"]'):
            assert local_path(cfg, node.get("content") or node.get("href")).is_file()


def test_reading_assets_and_self_hosted_fonts_are_published(published_site):
    _, cfg, _, _, _ = published_site
    for asset in ("style.css", "theme-transition.css", "enchantment.css", "language.css", "language.js", "theme.js",
                  "enchantment.js", "reading.js"):
        assert (cfg.site_dir / asset).stat().st_size > 0
    # Font URLs are resolved relative to the stylesheet, including nested pages.
    css = (cfg.site_dir / "style.css").read_text()
    fonts = [url for url in re.findall(r'''url\(["']?([^"')]+)''', css) if url.endswith(".woff2")]
    assert fonts, "The reading fonts must be part of the generated release"
    for font in fonts:
        assert not urlsplit(font).scheme, "Reading should not require an external font service"
        assert (cfg.site_dir / font).read_bytes().startswith(b"wOF2")
    for path in cfg.site_dir.rglob("*.html"):
        doc = html(path)
        theme = doc.select_one('script[src$="theme.js"]')
        stylesheet = doc.select_one('link[rel="stylesheet"]')
        version = hashlib.sha256((cfg.site_dir / 'style.css').read_bytes()).hexdigest()[:12]
        assert urlsplit(stylesheet['href']).query == f'v={version}'
        assert theme and not theme.has_attr("defer") and not theme.has_attr("async")
        assert list(doc.head.children).index(theme) < list(doc.head.children).index(stylesheet)
        reading = doc.select_one('script[src$="reading.js"]')
        language = doc.select_one('script[src$="language.js"]')
        assert reading.has_attr("defer")
        assert language.has_attr("defer")
        assert not language.has_attr("blocking")
        scripts = list(doc.select("script[src]"))
        assert scripts.index(reading) < scripts.index(language)


def test_images_open_with_named_in_page_controls_in_every_edition_and_page(published_site):
    from rep0rter.i18n import COPY

    _, cfg, _, _, _ = published_site
    assert (cfg.site_dir / "image-viewer.js").stat().st_size > 0
    page_kinds = set()
    languages = set()
    for path in cfg.site_dir.rglob("*.html"):
        doc = html(path)
        language = doc.html["lang"]
        languages.add(language)
        page_kinds.update(doc.body.get("class", []))
        page_url = cfg.site_url + "/" + path.relative_to(cfg.site_dir).as_posix()
        script = doc.select_one('script[src$="image-viewer.js"]')
        assert script and script.has_attr("defer")
        assert local_path(cfg, urljoin(page_url, script["src"])).is_file()

        viewers = doc.select("dialog#image-viewer")
        assert len(viewers) == 1
        viewer = viewers[0]
        assert not viewer.has_attr("open")
        assert doc.find(id=viewer["aria-labelledby"]).get_text(strip=True) == COPY[language]["view_image"]
        close = viewer.select_one("button[data-viewer-close]")
        assert close["type"] == "button"
        assert close["aria-label"] == COPY[language]["close_image"]
        # Shared modal content must be populated only on opening: article
        # withdrawal can then remove all source-specific image metadata.
        assert not viewer.select_one("[data-viewer-image]").has_attr("src")
        assert viewer.select_one("[data-viewer-image]")["alt"] == ""
        assert not viewer.select_one("[data-viewer-caption]").get_text(strip=True)
        assert not viewer.select_one("[data-viewer-download]").has_attr("href")
        assert viewer.select_one("[data-viewer-download]").has_attr("download")

        images = doc.select("article img.source-card")
        assert images
        assert len(doc.select("[data-image-view]")) == len(images)
        for image in images:
            assert image.parent.name == "picture"
            trigger = image.parent.parent
            assert trigger.name == "button", "Image clicks must not navigate to raw files"
            assert trigger["type"] == "button"
            assert trigger.has_attr("data-image-view") and trigger.has_attr("disabled")
            assert trigger["aria-haspopup"] == "dialog"
            assert trigger["aria-controls"] == viewer["id"]
            assert trigger["aria-label"].strip()
            assert not trigger.has_attr("href")
            assert trigger["data-image-src"] == image["src"]
            assert trigger["data-image-light"] == image["src"]
            assert trigger["data-image-dark"] != trigger["data-image-light"]
            source = image.parent.select_one("source[data-theme-source]")
            assert source["media"] == "(prefers-color-scheme: dark)"
            assert source["srcset"] == trigger["data-image-dark"]
            for attribute in ("data-image-light", "data-image-dark"):
                assert local_path(cfg, urljoin(page_url, trigger[attribute])).is_file()
        for download in doc.select('article a[download]'):
            assert download["href"] == download["data-image-light"]
            assert download["data-image-dark"] != download["data-image-light"]
            for attribute in ("data-image-light", "data-image-dark"):
                assert local_path(cfg, urljoin(page_url, download[attribute])).is_file()
    assert languages == set(EDITIONS)
    assert page_kinds == {"page-home", "page-source", "page-story"}


def test_emergency_withdrawal_leaves_no_popup_image_metadata_in_cached_pages(published_site):
    from rep0rter.policy import add_rule, redact

    store, cfg, container, entries, rendered_ids = published_site
    image_names = {
        urlsplit(trigger[attribute]).path.rsplit("/", 1)[-1]
        for path in cfg.site_dir.rglob("*.html")
        for trigger in html(path).select("[data-image-view]")
        for attribute in ("data-image-light", "data-image-dark")
    }
    assert image_names
    assert any(name.endswith("-dark.png") for name in image_names)
    assert all((cfg.site_dir / "cards" / name).is_file() for name in image_names)
    release = cfg.site_dir.resolve()
    previous_rendered = list(rendered_ids)
    add_rule(store, "container", container.id)
    redact(store, [event.id for _, event in entries])
    assert cfg.site_dir.resolve() == release
    assert rendered_ids == previous_rendered
    for path in cfg.site_dir.rglob("*.html"):
        cached = path.read_text(encoding="utf-8")
        assert not html(path).select("[data-image-view], [data-image-src], [data-image-light], [data-image-dark], [data-theme-source]")
        assert not any(name in cached for name in image_names), path
    assert all(not (release / "cards" / name).exists() for name in image_names)


def test_reading_is_available_without_javascript_and_controls_are_labeled(published_site):
    from rep0rter.i18n import COPY

    _, cfg, _, _, _ = published_site
    for path in cfg.site_dir.rglob("*.html"):
        doc = html(path)
        copy = COPY[doc.html["lang"]]
        controls = doc.select_one("[data-theme-controls]")
        assert controls.has_attr("hidden")
        assert controls.name == 'details'
        trigger = controls.select_one('summary[data-theme-trigger]')
        assert trigger['aria-label'] == copy['appearance']
        assert not doc.select('[data-theme-cycle], .glass-indicator, .has-glass-indicator')
        assert controls.select_one('[aria-pressed="true"]')['data-theme-choice'] == 'system'
        for mode in ('light', 'dark', 'system'):
            assert trigger['data-theme-' + mode] == copy['theme_' + mode]
            assert controls.select_one(f'[data-theme-choice="{mode}"]').get_text(strip=True) == copy['theme_' + mode]
        assert not doc.select("article[hidden], .day[hidden]")
        assert all(article.select_one("h3").get_text().strip() for article in doc.select("article"))
        if "posts" in path.parts:
            assert doc.select_one("[data-search-form]") is None
            continue
        form = doc.select_one("[data-search-form]")
        assert form.has_attr("hidden")
        input_ = form.select_one('input[type="search"]')
        assert form.find("label", attrs={"for": input_["id"]}).get_text(strip=True) == copy["search_label"]
        assert input_["placeholder"] == copy["search_placeholder"]
        status = doc.find(id=input_["aria-describedby"])
        assert status["aria-live"] == "polite"
        assert status["data-result-template"] == copy["search_results"]
        assert "{count}" in status["data-result-template"]
        assert status.has_attr("hidden")
        assert doc.select_one("[data-no-results]").has_attr("hidden")
        for day in doc.select(".day"):
            assert int(day.select_one(".day-count").get_text()) == len(day.select("article"))


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
        assert [node['data-preview-post-id'] for node in html(cfg.site_dir / page).select('[data-preview-post-id]')] == [str(latest.id)]
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


def test_withdrawn_pages_keep_localized_navigation_and_appearance_assets(published_site):
    from rep0rter.i18n import COPY
    from rep0rter.policy import add_rule, redact

    store, cfg, _, entries, _ = published_site
    removed, event = entries[-1]
    add_rule(store, "event", event.id)
    redact(store, [event.id])
    site.build(store, cfg)
    for language, (page, _) in EDITIONS.items():
        path = cfg.site_dir / "posts" / str(removed.id) / page
        doc = html(path)
        assert doc.html["lang"] == language
        assert doc.h1.get_text() == COPY[language]["withdrawn"]
        assert doc.select_one("[data-theme-controls]").has_attr("hidden")
        assert doc.select_one('meta[name="robots"]')["content"] == "noindex"
        languages = doc.select("a[data-language]")
        assert {link["data-language"] for link in languages} == set(EDITIONS)
        assert doc.select_one('[aria-current="page"]')["data-language"] == language
        assert doc.select_one('script[src$="theme.js"]')
        page_url = cfg.site_url + "/" + path.relative_to(cfg.site_dir).as_posix()
        for node in doc.select("a[href], link[href], script[src]"):
            assert local_path(cfg, urljoin(page_url, node.get("href") or node.get("src"))).is_file()


@pytest.mark.parametrize('remove_all', [False, True])
def test_emergency_withdrawal_scrubs_home_highlights_when_rebuild_fails(published_site, monkeypatch, remove_all):
    from rep0rter.policy import add_rule, redact

    store, cfg, _, entries, _ = published_site
    site.build(store, cfg)
    release = cfg.site_dir.resolve()
    removed = entries if remove_all else entries[-1:]
    for _, event in removed:
        add_rule(store, 'event', event.id)
    redact(store, [event.id for _, event in removed])

    def unavailable(*args, **kwargs):
        raise RuntimeError('Generator unavailable')

    monkeypatch.setattr(site, '_build', unavailable)
    with pytest.raises(RuntimeError, match='Generator unavailable'):
        site.build(store, cfg)
    assert cfg.site_dir.resolve() == release
    for language, (page, _) in EDITIONS.items():
        doc = html(cfg.site_dir / page)
        remaining = doc.select('[data-preview-post-id]')
        assert [node['data-preview-post-id'] for node in remaining] == ([] if remove_all else [str(entries[0][0].id)])
        assert bool(doc.select('.hero-latest')) == (not remove_all)
        for post, _ in removed:
            assert post.translations[language]['headline'] not in doc.get_text()
            assert post.translations[language]['summary'] not in doc.get_text()
            assert not doc.select(f'a[href="posts/{post.id}/{page}"]')
            assert not doc.find('article', id=str(post.id))
        if not remove_all:
            assert doc.find('article', id=str(entries[0][0].id))


def test_source_optout_scrubs_cached_pages_and_keeps_styled_tombstones_without_rebuild(published_site):
    from html import unescape

    from rep0rter.i18n import COPY
    from rep0rter.policy import add_rule, redact

    store, cfg, container, entries, rendered_ids = published_site
    source_pages = {}
    for language, (page, _) in EDITIONS.items():
        source_url = html(cfg.site_dir / page).select_one("a.channel")["href"]
        source_pages[language] = local_path(cfg, urljoin(cfg.site_url + "/", source_url))
        before = unescape(source_pages[language].read_text())
        assert container.name in before
        assert all(post.translations[language]["headline"] in before for post, _ in entries)
    previous_rendered = list(rendered_ids)
    release = cfg.site_dir.resolve()

    # Emergency policy cleanup must protect the existing release even when the
    # normal generator cannot run. The fixture has several reports per source.
    add_rule(store, "container", container.id)
    assert set(redact(store, [event.id for _, event in entries])) == {event.id for _, event in entries}
    assert cfg.site_dir.resolve() == release
    assert rendered_ids == previous_rendered

    for language, (page, _) in EDITIONS.items():
        cached = unescape(source_pages[language].read_text())
        assert container.name not in cached
        assert not html(source_pages[language]).select("article")
        for cached_path in (source_pages[language], cfg.site_dir / page):
            scrubbed = html(cached_path)
            assert not scrubbed.select('[data-author], [data-author-label], [data-source-label], [data-topics]')
            for facet in ('topic', 'author', 'source'):
                assert all(option['value'] == '' for option in scrubbed.select(f'[data-filter="{facet}"] option'))
        for post, event in entries:
            for private_text in (event.author_name, event.text, post.headline, post.summary,
                                 post.translations[language]["headline"], post.translations[language]["summary"]):
                assert private_text not in cached
            path = cfg.site_dir / "posts" / str(post.id) / page
            tombstone = html(path)
            assert tombstone.html["lang"] == language
            assert tombstone.h1.get_text() == COPY[language]["withdrawn"]
            assert tombstone.select_one('meta[name="robots"]')["content"] == "noindex"
            assert not tombstone.select('article, blockquote, meta[property="og:image"]')
            assert tombstone.select_one('script[src$="theme.js"]')
            assert tombstone.select_one('link[rel="stylesheet"][href$="style.css"]')
            languages = tombstone.select("nav a[lang]")
            assert {link["lang"] for link in languages} == set(EDITIONS)
            assert tombstone.select_one('[aria-current="page"]')["lang"] == language
            page_url = cfg.site_url + "/" + path.relative_to(cfg.site_dir).as_posix()
            for node in tombstone.select("a[href], link[href], script[src]"):
                assert local_path(cfg, urljoin(page_url, node.get("href") or node.get("src"))).is_file()


def test_masthead_puts_one_search_between_brand_and_compact_controls(published_site):
    from rep0rter.i18n import page_name

    _, cfg, _, _, _ = published_site
    for path in cfg.site_dir.rglob('*.html'):
        doc = html(path)
        masthead = doc.select_one('.masthead')
        form = masthead.select_one('form[role="search"]')
        assert len(doc.select('#story-search')) == 1
        assert form.select_one('#story-search')['name'] == 'q'
        children = masthead.find_all(recursive=False)
        assert children[0].get('class') == ['wordmark']
        assert children[1] is form
        assert 'masthead-actions' in children[2].get('class', [])
        assert len(children[2].select('details.language-menu')) == 1
        assert len(children[2].select('a[data-language]')) == 4
        assert len(children[2].select('details.theme-menu')) == 1
        assert len(children[2].select('button[data-theme-choice]')) == 3
        page_url = cfg.site_url + '/' + path.relative_to(cfg.site_dir).as_posix()
        assert local_path(cfg, urljoin(page_url, form['action'])) == cfg.site_dir / page_name(doc.html['lang'])
        assert form['method'] == 'get'
        if 'posts' in path.parts:
            # Story search remains a usable native form that returns home.
            assert not form.has_attr('hidden')
            assert not form.has_attr('data-search-form')
        else:
            assert form.has_attr('data-search-form')


def test_facet_metadata_stays_in_removable_articles_and_selects_start_empty(published_site):
    _, cfg, _, entries, _ = published_site
    posts = {str(post.id): (post, event) for post, event in entries}
    metadata = ('data-post-id', 'data-date', 'data-timestamp', 'data-author',
                'data-author-label', 'data-source-label', 'data-topics')
    for path in cfg.site_dir.rglob('*.html'):
        doc = html(path)
        for attribute in metadata:
            assert all(node.name == 'article' for node in doc.select(f'[{attribute}]'))
        for article in doc.select('article'):
            post, event = posts[article['data-post-id']]
            assert float(article['data-timestamp']) == (event.ts if 'sources' in path.parts else post.published_at)
            assert article['data-author-label'] == event.author_name
            assert article['data-author']
            assert article['data-source']
            assert article['data-date'] == article.find_parent('section', class_='day').time['datetime']
            assert isinstance(json.loads(article['data-topics']), list)
        if 'posts' in path.parts:
            assert doc.select_one('[data-filters]') is None
            continue
        filters = doc.select_one('[data-filters]')
        assert filters.has_attr('hidden')
        assert {node['data-filter'] for node in filters.select('[data-filter]')} == {
            'period', 'topic', 'author', 'source', 'sort', 'from', 'to',
        }
        # Populate options only from articles still present after emergency cleanup.
        for facet in ('topic', 'author', 'source'):
            options = filters.select(f'[data-filter="{facet}"] option')
            assert len(options) == 1
            assert options[0]['value'] == ''
