"""Command line entry point: ``python -m rep0rter <command>``."""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import logging
import sys
import time
import json

from . import __version__
from .collectors import slack_archive
from .collectors.registry import collect_all
from .config import Config, load_config
from .delivery import DeliveryOutbox, deliver_pending, prepare_posts
from .i18n import LANGUAGES
from .llm import LLM
from .publishers import site as site_publisher
from .publishers import telegram
from . import threads_delivery
from .reporter import draft_posts, missing_languages, translate_post
from .store import Store

log = logging.getLogger("rep0rter")


def cmd_collect(cfg: Config, args) -> int:
    with Store(cfg.db_path) as store:
        n = collect_all(store, days=args.days or cfg.collect_days, max_channels=args.max_channels,config=cfg)
        _reconcile_exclusions(store,cfg)
        healthy=json.loads(store.get_kv('collector_health','{}')).get('healthy',True)
    print(f"collected {n} events")
    return 0 if healthy else 1


def cmd_report(cfg: Config, args) -> int:
    """Select + write + publish new items. Persists posts unless --dry-run."""
    with Store(cfg.db_path) as store:
        if not args.dry_run:
            _reconcile_exclusions(store,cfg)
        drafts = draft_posts(store, cfg, use_llm=not args.no_llm)
        for c, p in drafts:
            log.info("selected %.1f %s | %s", c.score, c.event.url, p.headline)
        if args.dry_run:
            telegram.publish(cfg, drafts, dry_run=True)
            print(f"dry run: {len(drafts)} item(s) would be posted")
            return 0
        prepare_posts(cfg, store, drafts)
        from .project_automation import run_due
        automatic_posts = run_due(store)
        if not args.no_llm:
            _backfill_translations(store, cfg)
        site_publisher.build(store, cfg)
        deliver_pending(cfg, store)
        if cfg.threads_enabled:
            threads_delivery.enqueue_missing(cfg, store)
        threads_delivery.deliver_pending(cfg, store)
        from .retractions import process
        process(store,cfg)
    print(f"posted {len(drafts) + automatic_posts} item(s)")
    return 0


def cmd_build_site(cfg: Config, args) -> int:
    with Store(cfg.db_path) as store:
        _reconcile_exclusions(store,cfg)
        out = site_publisher.build(store, cfg)
    print(f"site written to {out}")
    return 0


def cmd_translate(cfg: Config, args) -> int:
    """Backfill missing editions without re-publishing to Telegram."""
    if not cfg.llm_enabled:
        print("translation requires AI_BASE_URL / AI_API_KEY / AI_MODEL", file=sys.stderr)
        return 2
    if args.limit < 1:
        print("--limit must be positive", file=sys.stderr)
        return 2
    attempted = updated = incomplete = 0
    with Store(cfg.db_path) as store:
        _reconcile_exclusions(store,cfg)
        llm = LLM(cfg)
        languages = args.language or list(LANGUAGES)
        for post, event, _ in store.recent_posts(limit=-1):
            if not missing_languages(post, languages):
                continue
            if attempted >= args.limit:
                break
            attempted += 1
            if translate_post(post, llm, languages=languages, source_ts=event.ts):
                updated += int(store.update_post_translations(post))
            if missing_languages(post, languages):
                incomplete += 1
            elif not missing_languages(post):
                with store.conn:
                    store.conn.execute('DELETE FROM kv WHERE key=?', (f'translation_retry:{post.id}',))
        site_publisher.build(store, cfg)
    print(f"translation: attempted {attempted}, updated {updated}, incomplete {incomplete}")
    return 1 if incomplete else 0


def _backfill_translations(store: Store, cfg: Config, limit: int = 3) -> dict:
    """Repair saved editions without preparing any new publications or deliveries."""
    from .policy import event_allowed
    if not cfg.llm_enabled:
        return {'attempted': 0, 'updated': 0, 'incomplete': 0}
    now = time.time()
    pending = []
    review_required = 0
    for post, _, _ in store.recent_posts(limit=-1):
        if missing_languages(post):
            state = json.loads(store.get_kv(f'translation_retry:{post.id}', '{}'))
            fingerprint = hashlib.sha256(json.dumps([post.headline, post.summary]).encode()).hexdigest()
            if state.get('source_hash') != fingerprint:
                state = {}
            if int(state.get('attempts', 0)) >= 6:
                review_required += 1
                continue
            pending.append((float(state.get('next_attempt_at', 0)), post.published_at, post, fingerprint))
    pending.sort(key=lambda item: (item[0], item[1], item[2].id))
    report = {'attempted': 0, 'updated': 0, 'incomplete': len(pending) + review_required,
              'review_required': review_required, 'checked_at': now}
    llm = None
    for due, _, post, fingerprint in pending:
        if report['attempted'] >= limit or due > now:
            break
        key = f'translation_retry:{post.id}'
        # Reserve a bounded retry before the model call; another run or a crash
        # must not repeatedly spend on the same failing edition.
        with store.conn:
            store.conn.execute('BEGIN IMMEDIATE')
            event = store.get_event(post.event_id)
            if not event or not event_allowed(store, event):
                continue
            state = json.loads(store.get_kv(key, '{}'))
            if state.get('source_hash') != fingerprint:
                state = {}
            if float(state.get('next_attempt_at', 0)) > now:
                continue
            attempts = int(state.get('attempts', 0)) + 1
            state = {'attempts': attempts, 'last_attempt_at': now, 'source_hash': fingerprint,
                     'next_attempt_at': now + min(86400, 3600 * 2 ** min(attempts - 1, 5))}
            store.conn.execute('INSERT INTO kv(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                               (key, json.dumps(state)))
        report['attempted'] += 1
        # Six calls at most, each with a 45-second timeout. Persistent failures
        # require an explicit `translate` run after six scheduled attempts.
        llm = llm or LLM(replace(cfg, ai_timeout_seconds=min(cfg.ai_timeout_seconds, 45)))
        if translate_post(post, llm, source_ts=event.ts):
            report['updated'] += int(store.update_post_translations(post))
        if not missing_languages(post):
            report['incomplete'] -= 1
            with store.conn:
                store.conn.execute('DELETE FROM kv WHERE key=?', (key,))
    store.set_kv('translation_backfill', json.dumps(report))
    if report['attempted']:
        log.info('saved translation recovery: attempted %d, updated %d, incomplete %d',
                 report['attempted'], report['updated'], report['incomplete'])
    return report


def cmd_outbox(cfg: Config, args) -> int:
    with Store(cfg.db_path) as store:
        outbox = DeliveryOutbox(store)
        outbox.recover()
        if args.job is not None:
            outbox.reconcile(args.job, message_id=args.message_id, retry=args.retry)
        elif args.message_id is not None or args.retry:
            raise ValueError("--message-id/--retry requires --job")
        for row in store.conn.execute("SELECT id,post_id,target,status,message_id,error FROM delivery_jobs ORDER BY id"):
            print(dict(row))
    return 0


def cmd_threads(cfg: Config, args) -> int:
    from .policy import event_allowed
    from .publishers import threads
    from .store import Post
    with Store(cfg.db_path) as store:
        has_jobs = store.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='threads_jobs'").fetchone()
        outbox = None
        if args.threads_command in ('outbox', 'reconcile'):
            outbox = threads_delivery.ThreadsOutbox(store)
            has_jobs = True
        if args.threads_command == 'list':
            for post, event, _ in store.recent_posts(limit=args.limit):
                if not event_allowed(store, event):
                    continue
                job = (store.conn.execute('SELECT status,permalink FROM threads_jobs WHERE post_id=?',
                                          (post.id,)).fetchone() if has_jobs else None)
                print(f"{post.id}: {job['status'] if job else 'unposted'} | {post.headline}"
                      f" | {job['permalink'] or '' if job else ''}")
        elif args.threads_command == 'send':
            selected = []
            for post_id in dict.fromkeys(args.post):
                row = store.conn.execute('SELECT * FROM posts WHERE id=?', (post_id,)).fetchone()
                if row is None:
                    raise ValueError(f'Post {post_id} does not exist')
                event = store.get_event(row['event_id'])
                if event is None or not event_allowed(store, event):
                    raise ValueError(f'Post {post_id} is withdrawn or excluded')
                post = Post(id=row['id'], event_id=row['event_id'], published_at=row['published_at'],
                            score=row['score'], headline=row['headline'], summary=row['summary'])
                job = (store.conn.execute('SELECT status FROM threads_jobs WHERE post_id=?',
                                          (post_id,)).fetchone() if has_jobs else None)
                if job:
                    print(f'{post_id}: already {job["status"]}; skipped')
                    continue
                print(f'Post {post_id} preview:\n{threads.format_text(cfg, post)}\n')
                selected.append(post_id)
            if args.apply and selected:
                if not cfg.threads_enabled:
                    raise ValueError('Enable Threads before queuing posts')
                print(f'Queued {threads_delivery.enqueue_missing(cfg, store, selected)} post(s)')
        elif args.threads_command == 'deliver':
            threads_delivery.enqueue_missing(cfg, store)
            sent = threads_delivery.deliver_pending(cfg, store)
            print(f'Delivered {len(sent)} post(s)')
        elif args.threads_command == 'outbox':
            outbox.recover()
            for row in store.conn.execute('SELECT id,post_id,status,thread_id,permalink,error FROM threads_jobs ORDER BY id'):
                print(dict(row))
        elif args.threads_command == 'reconcile':
            row = store.conn.execute('SELECT id,status,payload FROM threads_jobs WHERE post_id=?', (args.post,)).fetchone()
            if row is None:
                raise ValueError('Post has no Threads job')
            if args.remote_id:
                remote = threads.get_post(cfg, args.remote_id)
                permalink = remote.get('permalink')
                expected = json.loads(row['payload'] or '{}').get('text')
                if (str(remote.get('id')) != args.remote_id or not expected or remote.get('text') != expected or
                        not permalink or not threads._valid_url(permalink)):
                    raise ValueError('Remote Threads post could not be verified')
                outbox.reconcile(row['id'], thread_id=args.remote_id, permalink=permalink)
            else:
                outbox.reconcile(row['id'], retry=True)
            print(f'Reconciled post {args.post}')
    return 0


def _reconcile_exclusions(store,cfg):
    from .policy import affected,redact
    from .retractions import plan_remote,queue
    existing={r[0] for r in store.conn.execute('SELECT event_id FROM event_tombstones')}
    ids=[event_id for event_id in affected(store) if event_id not in existing]
    if ids:
        post_ids=[r[0] for r in store.conn.execute('SELECT id FROM posts WHERE event_id IN (%s)' % ','.join('?' for _ in ids),ids)]
        plans=plan_remote(store,cfg,post_ids)
        redact(store,ids)
        queue(store,plans)
    pending=[r[0] for r in store.conn.execute("SELECT post_id FROM retractions WHERE status='pending'")]
    if pending:
        queue(store,plan_remote(store,cfg,pending))


def run_once(cfg: Config, dry_run: bool = False, no_llm: bool = False, days: int | None = None) -> tuple[int, int]:
    """One full cycle: collect -> report -> publish -> build site."""
    with Store(cfg.db_path) as store:
        run_id = store.start_run()
        collected = posted = 0
        try:
            collected = collect_all(store, days=days or cfg.collect_days,config=cfg)
            if not dry_run:
                _reconcile_exclusions(store,cfg)
            drafts = draft_posts(store, cfg, use_llm=not no_llm)
            if dry_run:
                telegram.publish(cfg, drafts, dry_run=True)
            else:
                prepare_posts(cfg, store, drafts)
                posted = len(drafts)
                from .project_automation import run_due
                posted += run_due(store)
                if not no_llm:
                    _backfill_translations(store, cfg)
                site_publisher.build(store, cfg)
                deliver_pending(cfg, store)
                if cfg.threads_enabled:
                    threads_delivery.enqueue_missing(cfg, store)
                threads_delivery.deliver_pending(cfg, store)
                from .retractions import process
                process(store,cfg)
            health=json.loads(store.get_kv('collector_health','{}'))
            store.finish_run(run_id, collected, posted,error='degraded collection: see collector_health' if health.get('healthy') is False else None)
        except Exception as exc:
            store.finish_run(run_id, collected, posted, error=repr(exc))
            raise
    return collected, posted


def cmd_run(cfg: Config, args) -> int:
    collected, posted = run_once(cfg, dry_run=args.dry_run, no_llm=args.no_llm, days=args.days)
    print(f"run finished: collected {collected}, posted {posted}")
    with Store(cfg.db_path) as store:
        health = json.loads(store.get_kv('collector_health', '{}'))
    return 1 if health.get('healthy') is False else 0


def cmd_loop(cfg: Config, args) -> int:
    interval = args.interval
    log.info("loop: running every %ds", interval)
    while True:
        started = time.time()
        try:
            collected, posted = run_once(cfg, no_llm=args.no_llm)
            log.info("run finished: collected %d, posted %d", collected, posted)
        except Exception:  # noqa: BLE001 - keep the loop alive
            log.exception("run failed")
        elapsed = time.time() - started
        time.sleep(max(60, interval - elapsed))


def cmd_export(cfg: Config, args) -> int:
    with Store(cfg.db_path) as store:
        result = slack_archive.export_json(store, days=args.days, out_path=args.out)
    print(f"wrote {args.out}: {result['channel_count']} channels, {result['message_count']} messages")
    return 0


def cmd_status(cfg: Config, args) -> int:
    with Store(cfg.db_path) as store:
        last = store.last_run()
        print(f"db: {cfg.db_path}")
        print(f"events: {store.event_count()}  posts: {store.post_count()}")
        print(f"telegram: {'configured' if cfg.telegram_bot_token and cfg.telegram_target else 'NOT configured'}"
              f"{' (test chat)' if cfg.telegram_use_test_chat else ''}")
        print(f"threads: {'configured' if cfg.threads_enabled and cfg.threads_user_id and cfg.threads_access_token else 'NOT configured'}")
        print(f"llm: {'configured (' + str(cfg.ai_model) + ')' if cfg.llm_enabled else 'NOT configured'}")
        if last:
            print(f"last run: started {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last['started_at']))}, "
                  f"collected {last['collected']}, posted {last['posted']}, error={last['error']}")
    return 0


def cmd_serve(cfg: Config, args) -> int:
    from .web import create_app
    create_app(cfg).run(host=args.host, port=args.port, debug=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rep0rter", description="g0v virtual reporter")
    parser.add_argument("--version", action="version", version=f"rep0rter {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("collect", help="fetch recent events into the store")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--max-channels", type=int, default=None)
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("report", help="select, write and publish new items")
    p.add_argument("--dry-run", action="store_true", help="preview without posts/delivery; decision audits are saved")
    p.add_argument("--no-llm", action="store_true")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("build-site", help="regenerate the static site from the store")
    p.set_defaults(func=cmd_build_site)

    p = sub.add_parser("translate", help="backfill missing languages and rebuild the site; never sends Telegram")
    p.add_argument("--limit", type=int, default=300, help="maximum posts to translate")
    p.add_argument("--language", action="append", choices=list(LANGUAGES), help="limit languages (repeatable)")
    p.set_defaults(func=cmd_translate)

    p = sub.add_parser("outbox", help="inspect Telegram delivery; explicitly reconcile failed/unknown jobs")
    p.add_argument("--job", type=int)
    action = p.add_mutually_exclusive_group()
    action.add_argument("--message-id", type=int, help="record a delivery confirmed in Telegram")
    action.add_argument("--retry", action="store_true", help="requeue only after confirming it was not delivered")
    p.set_defaults(func=cmd_outbox)

    p = sub.add_parser('threads', help='preview, queue, deliver and inspect Threads posts')
    actions = p.add_subparsers(dest='threads_command', required=True)
    action = actions.add_parser('list', help='list saved posts and Threads status')
    action.add_argument('--limit', type=int, default=30)
    action.set_defaults(func=cmd_threads, readonly=True)
    action = actions.add_parser('send', help='preview selected posts; --apply queues them')
    action.add_argument('--post', type=int, action='append', required=True)
    action.add_argument('--apply', action='store_true')
    action.set_defaults(func=cmd_threads)
    action = actions.add_parser('deliver', help='send at most the configured batch size')
    action.set_defaults(func=cmd_threads)
    action = actions.add_parser('outbox', help='inspect durable Threads jobs')
    action.set_defaults(func=cmd_threads)
    action = actions.add_parser('reconcile', help='resolve an uncertain Threads send')
    action.add_argument('--post', type=int, required=True)
    choice = action.add_mutually_exclusive_group(required=True)
    choice.add_argument('--remote-id', help='verified remote post ID')
    choice.add_argument('--confirm-absent', action='store_true', help='requeue after checking no remote post exists')
    action.set_defaults(func=cmd_threads)

    p = sub.add_parser("run", help="collect + report + build site, once")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--days", type=int, default=None)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("loop", help="run forever at a fixed interval")
    p.add_argument("--interval", type=int, default=3600, help="seconds between runs (default 3600)")
    p.add_argument("--no-llm", action="store_true")
    p.set_defaults(func=cmd_loop)

    p = sub.add_parser("export", help="write the legacy messages.json")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--out", default="messages.json")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("status", help="show store and configuration status")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("serve", help="run Google login and project submissions locally")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    from . import operations,policy,retractions,editorial,republish,feed_tools,notion_discovery
    for module in (operations,policy,retractions,editorial,republish,feed_tools,notion_discovery):
        module.register_commands(sub)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    for handler in logging.getLogger().handlers:
        handler.setFormatter(operations.RedactingFormatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    cfg = load_config()
    if not getattr(args,'readonly',False):
        cfg.ensure_dirs()
    return args.func(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
