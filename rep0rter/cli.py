"""Command line entry point: ``python -m rep0rter <command>``."""

from __future__ import annotations

import argparse
import logging
import sys
import time

from . import __version__
from .collectors import slack_archive
from .config import Config, load_config
from .publishers import site as site_publisher
from .publishers import telegram
from .reporter import draft_posts
from .store import Store

log = logging.getLogger("rep0rter")


def cmd_collect(cfg: Config, args) -> int:
    with Store(cfg.db_path) as store:
        n = slack_archive.collect(store, days=args.days or cfg.collect_days, max_channels=args.max_channels)
    print(f"collected {n} events")
    return 0


def cmd_report(cfg: Config, args) -> int:
    """Select + write + publish new items. Persists posts unless --dry-run."""
    with Store(cfg.db_path) as store:
        drafts = draft_posts(store, cfg, use_llm=not args.no_llm)
        if not drafts:
            log.info("nothing newsworthy this run")
            return 0
        for c, p in drafts:
            log.info("selected %.1f %s | %s", c.score, c.event.url, p.headline)
        ids = telegram.publish(cfg, drafts, dry_run=args.dry_run)
        if args.dry_run:
            print(f"dry run: {len(drafts)} item(s) would be posted")
            return 0
        for i, (c, p) in enumerate(drafts):
            p.delivery = {"telegram": ids[0] if ids else None}
            store.add_post(p)
        site_publisher.build(store, cfg)
    print(f"posted {len(drafts)} item(s)")
    return 0


def cmd_build_site(cfg: Config, args) -> int:
    with Store(cfg.db_path) as store:
        out = site_publisher.build(store, cfg)
    print(f"site written to {out}")
    return 0


def run_once(cfg: Config, dry_run: bool = False, no_llm: bool = False, days: int | None = None) -> tuple[int, int]:
    """One full cycle: collect -> report -> publish -> build site."""
    with Store(cfg.db_path) as store:
        run_id = store.start_run()
        collected = posted = 0
        try:
            collected = slack_archive.collect(store, days=days or cfg.collect_days)
            drafts = draft_posts(store, cfg, use_llm=not no_llm)
            if drafts:
                ids = telegram.publish(cfg, drafts, dry_run=dry_run)
                if not dry_run:
                    for c, p in drafts:
                        p.delivery = {"telegram": ids[0] if ids else None}
                        store.add_post(p)
                    posted = len(drafts)
            if not dry_run:
                site_publisher.build(store, cfg)
            store.finish_run(run_id, collected, posted)
        except Exception as exc:
            store.finish_run(run_id, collected, posted, error=repr(exc))
            raise
    return collected, posted


def cmd_run(cfg: Config, args) -> int:
    collected, posted = run_once(cfg, dry_run=args.dry_run, no_llm=args.no_llm, days=args.days)
    print(f"run finished: collected {collected}, posted {posted}")
    return 0


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
        print(f"llm: {'configured (' + str(cfg.ai_model) + ')' if cfg.llm_enabled else 'NOT configured'}")
        if last:
            print(f"last run: started {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last['started_at']))}, "
                  f"collected {last['collected']}, posted {last['posted']}, error={last['error']}")
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
    p.add_argument("--dry-run", action="store_true", help="print instead of sending; do not persist")
    p.add_argument("--no-llm", action="store_true")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("build-site", help="regenerate the static site from the store")
    p.set_defaults(func=cmd_build_site)

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

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    cfg = load_config()
    cfg.ensure_dirs()
    return args.func(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
