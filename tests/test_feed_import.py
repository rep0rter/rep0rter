import time

from rep0rter.korea import FEEDS
from rep0rter.feed_import import import_posts, unique_events
from rep0rter.store import Event, Store


def test_import_preserves_dates_dedupes_and_creates_no_delivery(tmp_path):
    now = time.time()
    with Store(tmp_path / 'test.sqlite') as store:
        events = [Event(id=f'rss:{i}', source='rss', kind='article',
                        container_id='rss-feed:' + FEEDS[i % 2], ts=now - 86400 * (100 + i),
                        url=f'https://codefor.kr/posts/{i // 2}', text=f'프로젝트 {i}\n\n공공 데이터를 함께 살펴봅니다.',
                        meta={'visibility': 'public', 'eligible': True, 'content_format': 'plain'})
                  for i in range(3)]
        store.upsert_events(events)
        assert import_posts(store, FEEDS, language='ko', community='codeforkorea') == 2
        assert import_posts(store, FEEDS, language='ko', community='codeforkorea') == 0
        rows = store.recent_posts()
        assert len(rows) == 2
        for post, event, _ in rows:
            assert post.published_at == event.ts
            assert post.translations['ko']['headline'].startswith('프로젝트')
            assert 'en' not in post.translations
            assert post.delivery == {}
            assert event.meta['archive_import'] is True
        assert not store.conn.execute("SELECT name FROM sqlite_master WHERE name='delivery_jobs'").fetchone()


def test_import_honors_window_and_source_eligibility(tmp_path):
    with Store(tmp_path / 'test.sqlite') as store:
        store.upsert_events([Event(id='rss:old', source='rss', kind='article',
            container_id='rss-feed:' + FEEDS[0], ts=time.time() - 86400 * 100,
            url='https://codefor.kr/posts/old', text='Old Korean entry',
            meta={'visibility': 'public', 'eligible': True})])
        assert import_posts(store, FEEDS, language='ko', community='codeforkorea', days=2) == 0
        store.conn.execute("UPDATE events SET meta=?", ('{"visibility":"private","eligible":true}',))
        store.conn.commit()
        assert import_posts(store, FEEDS, language='ko', community='codeforkorea') == 0


def test_dedupe_across_feeds_keeps_distinct_articles(tmp_path):
    with Store(tmp_path / 'dedupe.sqlite') as store:
        for index, (url, text) in enumerate([
            ('https://codefor.kr/article/1?utm_source=rss', 'First project'),
            ('https://codefor.kr/article/1', 'Updated description'),
            ('https://codefor.kr/article/2', 'Different project'),
            ('https://codefor.kr/article/3', 'Different project'),
        ]):
            store.upsert_events([Event(
                id=f'rss:{index}', source='rss', kind='article',
                container_id='rss-feed:' + FEEDS[index % 2], ts=index + 1,
                url=url, text=text,
                meta={'visibility': 'public', 'eligible': True, 'content_format': 'plain'})])
        assert [event.id for event in unique_events(store, FEEDS)] == ['rss:3', 'rss:1']


def test_shared_import_uses_supplied_source_language_and_community(tmp_path):
    feed = 'https://example.org/news.xml'
    with Store(tmp_path / 'shared.sqlite') as store:
        store.upsert_events([Event(
            id='rss:shared', source='rss', kind='article',
            container_id='rss-feed:' + feed, ts=time.time() - 100,
            url='https://example.org/news/project', text='公開データ\n\n新しい資料です',
            meta={'visibility': 'public', 'eligible': True, 'content_format': 'plain'})])
        assert import_posts(store, FEEDS, language='ko', community='codeforkorea') == 0
        assert import_posts(store, [feed], language='ja', community='example') == 1
        post, event, _ = store.recent_posts()[0]
        assert set(post.translations) == {'ja'}
        assert event.meta['community'] == 'example'
