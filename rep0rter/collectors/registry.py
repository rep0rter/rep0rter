"""Source-isolated collection with one global HTTP budget and observable health."""
from __future__ import annotations
import json
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timezone

from . import github, mastodon, slack_archive, slack_incremental
from .state import BudgetExceeded, BudgetSession, Metrics, read_state

log = logging.getLogger(__name__)


def record_source_error(store, source, error):
    log.warning('collector %s failed: %s', source, error)
    errors = json.loads(store.get_kv('collector_errors', '{}'))
    errors[source] = {'at': time.time(), 'error': str(error)}
    store.set_kv('collector_errors', json.dumps(errors))


def _allowlist(value):
    return [item.strip() for item in value.split(',') if item.strip()]


def collect_all(store, days=2, max_channels=None, config=None, *, session=None):
    """Keep Slack defaults; other sources need explicit allowlists, never implicit scraping."""
    started = time.time()
    previous = json.loads(store.get_kv('collector_health', '{}'))
    day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    daily = json.loads(store.get_kv('collector_daily_budget', '{}'))
    if daily.get('day') != day:
        daily = {'day': day, 'requests': 0}
    run_limit = max(1, int(os.getenv('REP0RTER_COLLECT_REQUEST_BUDGET', '80')))
    daily_limit = max(1, int(os.getenv('REP0RTER_COLLECT_DAILY_BUDGET', '1500')))
    def reserve():
        # Reserve before network I/O: crashes/retries still consume the daily budget.
        with store.conn:
            store.conn.execute('BEGIN IMMEDIATE')
            current = json.loads(store.get_kv('collector_daily_budget', '{}'))
            current_day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
            if current.get('day') != current_day:
                current = {'day': current_day, 'requests': 0}
            if current['requests'] >= daily_limit:
                raise BudgetExceeded('daily collector request budget exhausted')
            current['requests'] += 1
            store.conn.execute('INSERT INTO kv(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                               ('collector_daily_budget', json.dumps(current)))
    metrics = Metrics()
    transport = BudgetSession(session or slack_archive.make_session(), metrics,
                              limit=min(run_limit, max(0, daily_limit - daily['requests'])), reserve=reserve)
    total, failures, containers = 0, 0, 0
    sources = {}
    store.set_kv('collector_errors', '{}')
    jobs = [('slack', lambda: slack_incremental.collect(store, days, max_channels, transport, metrics=metrics))]
    repos = _allowlist(os.getenv('REP0RTER_GITHUB_REPOS', ''))
    accounts = _allowlist(os.getenv('REP0RTER_MASTODON_ACCOUNTS', ''))
    if repos:
        jobs.append(('github', lambda: github.collect(store, repos, transport, metrics, days)))
    if accounts:
        jobs.append(('mastodon', lambda: mastodon.collect(store, accounts, transport, metrics, days)))
    total_budget = transport.limit
    for index, (name, collect) in enumerate(jobs):
        # Reserve a fair remaining share for each independent source.
        transport.limit = metrics.requests + max(0, (total_budget - metrics.requests) // (len(jobs)-index))
        try:
            n = collect()
            total += n
            state = read_state(store, 'slack:health') if name == 'slack' else {}
            errors = json.loads(store.get_kv('collector_errors', '{}'))
            count = state.get('failed_channels', sum(1 for source in errors if source.startswith(name)))
            failures += count
            containers += state.get('containers', 0)
            sources[name] = {'healthy': count == 0, 'events': n, 'failed_channels': count}
        except Exception as exc:
            failures += 1
            record_source_error(store, name, exc)
            sources[name] = {'healthy': False, 'error': str(exc)}
    healthy = failures == 0
    health = {'healthy': healthy, 'last_attempt_at': started, 'finished_at': time.time(),
              'last_healthy_at': time.time() if healthy else previous.get('last_healthy_at'),
              'containers': containers, 'failed_channels': failures, 'sources': sources, 'metrics': asdict(metrics)}
    store.set_kv('collector_health', json.dumps(health))
    store.set_kv('collector_metrics:' + str(int(started * 1000)), json.dumps(health))
    # Bounded history, enough for two-week hourly before/after comparisons.
    with store.conn:
        store.conn.execute("DELETE FROM kv WHERE key LIKE 'collector_metrics:%' AND key < ?",
                           ('collector_metrics:' + str(int((started - 30*86400)*1000)),))
    return total
