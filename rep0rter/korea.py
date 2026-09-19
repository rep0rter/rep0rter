"""Import Code for Korea feed entries into the shared reader and RSS feeds."""
from __future__ import annotations

import json
import time

from .collectors import rss, slack_archive
from .collectors.state import BudgetSession, Metrics
from .llm import LLM
from .policy import event_allowed
from .publishers import site
from .reporter import Candidate
from .sources import eligible, plain_text
from .store import Store
from .stories import canonical_url
from .writer_contract import record_write, write


FEEDS = (
    'https://codefor.kr/boards/news.xml',
    'https://codefor.kr/boards/civic-tech-projects.xml',
)


def unique_events(store):
    """Keep the newest allowed item per article URL or exact text, across feeds."""
    urls, texts = set(), set()
    for row in store.conn.execute('SELECT * FROM events ORDER BY ts DESC, id'):
        event = store._row_to_event(row)
        if event.container_id not in {'rss-feed:' + url for url in FEEDS}:
            continue
        if not event_allowed(store, event) or not eligible(event):
            continue
        url = canonical_url(event.url) or event.url
        text = ' '.join(plain_text(event).split())
        duplicate = url in urls or text in texts
        urls.add(url)
        texts.add(text)
        if not duplicate:
            yield event


def import_posts(store, *, limit=10, days=3650, llm=None):
    """Keep source dates and exact identity; archive imports create no bot jobs."""
    now = time.time()
    urls, texts = set(), set()
    for _, event, _ in store.recent_posts(limit=-1):
        urls.add(canonical_url(event.url) or event.url)
        texts.add(' '.join(plain_text(event).split()))
    count = 0
    for event in unique_events(store):
        if count >= limit:
            break
        url = canonical_url(event.url) or event.url
        text = plain_text(event)
        key = ' '.join(text.split())
        if event.ts < now - days * 86400 or event.ts > now or url in urls or key in texts:
            continue
        headline, _, summary = text.partition('\n\n')
        summary = summary or text
        translations = {'ko': {'headline': headline, 'summary': summary}}
        mode = 'source_excerpt'
        if llm:
            candidate = Candidate(event, store.get_container(event.container_id), 0, plain_text=text)
            result = write(candidate, llm, now)
            record_write(store, candidate, result, now)
            if result.needs_review:
                continue
            if result.translations:
                headline, summary = result.headline, result.summary
                translations = result.translations
                mode = 'llm'
        # Serialize publication and recheck identity after any model request.
        with store.conn:
            store.conn.execute('BEGIN IMMEDIATE')
            if not event_allowed(store, event):
                continue
            existing = store.conn.execute('SELECT e.* FROM events e JOIN posts p ON p.event_id=e.id').fetchall()
            if any((canonical_url(other.url) or other.url) == url or
                   ' '.join(plain_text(other).split()) == key
                   for other in (store._row_to_event(row) for row in existing)):
                continue
            event.meta.update(community='codeforkorea', archive_import=True, presentation=mode)
            store.conn.execute('UPDATE events SET meta=? WHERE id=?',
                               (json.dumps(event.meta, ensure_ascii=False), event.id))
            store.conn.execute('''INSERT INTO posts
                (event_id,published_at,score,headline,summary,reasons,delivery,translations)
                VALUES(?,?,?,?,?,?,?,?)''',
                (event.id, event.ts, 0, headline, summary,
                 json.dumps(['archive_import', 'basic_deduplication', mode]), '{}',
                 json.dumps(translations, ensure_ascii=False)))
        urls.add(url)
        texts.add(key)
        count += 1
    return count


def cmd_import(cfg, args):
    if args.limit < 1 or args.days < 1:
        raise ValueError('--limit and --days must be positive')
    if args.summarize and not cfg.llm_enabled:
        raise ValueError('--summarize requires AI_BASE_URL, AI_API_KEY, and AI_MODEL')
    with Store(cfg.db_path) as store:
        store.set_kv('collector_errors', '{}')
        metrics = Metrics()
        with slack_archive.make_session() as session:
            rss.collect(store, FEEDS, BudgetSession(session, metrics, limit=2), metrics, args.days)
        if json.loads(store.get_kv('collector_errors', '{}')):
            raise RuntimeError('Code for Korea collection failed; inspect collector_errors')
        count = import_posts(store, limit=args.limit, days=args.days,
                             llm=LLM(cfg) if args.summarize else None)
        output = site.build(store, cfg)
    print(f'Imported {count} Code for Korea entries into {output}; Korean feed: feed.ko.xml')
    return 0


def register_commands(sub):
    parser = sub.add_parser('import-korea', help='import Korean archive entries into the website/RSS using basic deduplication')
    parser.add_argument('--days', type=int, default=3650)
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--summarize', action='store_true', help='generate summaries with the configured LLM')
    parser.set_defaults(func=cmd_import)
