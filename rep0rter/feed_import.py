"""Explicit archive imports through shared collectors, writer, store, and site."""
from __future__ import annotations

import json
import time

from .collectors import rss, slack_archive
from .collectors.state import BudgetSession, Metrics
from .i18n import feed_name
from .llm import LLM
from .policy import event_allowed
from .publishers import site
from .reporter import Candidate
from .sources import eligible, plain_text
from .store import Store
from .stories import canonical_url
from .writer_contract import record_write, write


def unique_events(store, feeds):
    """Keep the newest allowed item per article URL or exact text, across feeds."""
    urls, texts = set(), set()
    containers = {'rss-feed:' + url for url in feeds}
    for row in store.conn.execute('SELECT * FROM events ORDER BY ts DESC, id'):
        event = store._row_to_event(row)
        if event.container_id not in containers:
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


def import_posts(store, feeds, *, language, community, limit=10, days=3650, llm=None):
    """Keep source dates and exact identity; archive imports create no bot jobs."""
    now = time.time()
    urls, texts = set(), set()
    for _, event, _ in store.recent_posts(limit=-1):
        urls.add(canonical_url(event.url) or event.url)
        texts.add(' '.join(plain_text(event).split()))
    count = 0
    for event in unique_events(store, feeds):
        if count >= limit:
            break
        url = canonical_url(event.url) or event.url
        text = plain_text(event)
        key = ' '.join(text.split())
        if event.ts < now - days * 86400 or event.ts > now or url in urls or key in texts:
            continue
        headline, _, summary = text.partition('\n\n')
        summary = summary or text
        translations = {language: {'headline': headline, 'summary': summary}}
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
            event.meta.update(community=community, archive_import=True, presentation=mode)
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


def run_import(cfg, args, *, feeds, language, community):
    if args.limit < 1 or args.days < 1:
        raise ValueError('--limit and --days must be positive')
    if args.summarize and not cfg.llm_enabled:
        raise ValueError('--summarize requires AI_BASE_URL, AI_API_KEY, and AI_MODEL')
    with Store(cfg.db_path) as store:
        store.set_kv('collector_errors', '{}')
        metrics = Metrics()
        with slack_archive.make_session() as session:
            rss.collect(store, feeds, BudgetSession(session, metrics, limit=len(feeds)), metrics, args.days)
        if json.loads(store.get_kv('collector_errors', '{}')):
            raise RuntimeError('Feed collection failed; inspect collector_errors')
        count = import_posts(store, feeds, language=language, community=community,
                             limit=args.limit, days=args.days,
                             llm=LLM(cfg) if args.summarize else None)
        output = site.build(store, cfg)
    print(f'Imported {count} entries into {output}; feed: {feed_name(language)}')
    return 0
