"""Code for Korea archive import preset for the shared RSS pipeline."""
from __future__ import annotations

from .feed_import import run_import

FEEDS = (
    'https://codefor.kr/boards/news.xml',
    'https://codefor.kr/boards/civic-tech-projects.xml',
)


def cmd_import(cfg, args):
    return run_import(cfg, args, feeds=FEEDS, language='ko', community='codeforkorea')


def register_commands(sub):
    parser = sub.add_parser('import-korea', help='import Korean archive entries into the website/RSS using basic deduplication')
    parser.add_argument('--days', type=int, default=3650)
    parser.add_argument('--limit', type=int, default=10)
    parser.add_argument('--summarize', action='store_true', help='generate summaries with the configured LLM')
    parser.set_defaults(func=cmd_import)
