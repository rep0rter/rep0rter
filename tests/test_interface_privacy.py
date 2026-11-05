"""Story pages keep story identity inside the removable article; withdrawals scrub it."""

from bs4 import BeautifulSoup

from rep0rter.i18n import LANGUAGES, page_name
from rep0rter.publishers import site
from rep0rter.policy import add_rule, redact
from tests.test_site import published_site  # noqa: F401  (shared fixture)


def html(path):
    return BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')


def test_story_identity_lives_inside_the_article_with_one_heading(published_site):
    _, cfg, _, entries, _ = published_site
    for language in LANGUAGES:
        story = html(cfg.site_dir / 'posts' / str(entries[-1][0].id) / page_name(language))
        article = story.select_one('article')
        assert len(story.select('h1')) == 1 and article.select_one('h1')
        for element in story.select('.story-date, .story-follow, .story-tags-rail, .story-rail, a[href*="sources/"]'):
            assert article in element.parents, element
        assert not story.select('[data-redesign-toggle], .legacy-only, .redesign-only')


def test_withdrawal_removes_story_content_from_every_page(published_site):
    store, cfg, _, entries, _ = published_site
    removed, event = entries[-1]
    headline = removed.translations['en']['headline']
    add_rule(store, 'event', event.id)
    redact(store, [event.id])
    site.build(store, cfg)
    for path in cfg.site_dir.rglob('*.html'):
        text = path.read_text(encoding='utf-8')
        assert headline not in text, path
        assert not BeautifulSoup(text, 'html.parser').select(f'[data-preview-post-id="{removed.id}"]'), path
