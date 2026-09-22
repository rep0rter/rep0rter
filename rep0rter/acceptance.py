"""Aggregate rollout evidence without exposing source text or credentials."""
import json
import os
import time
from datetime import datetime, timezone

from .editorial import evaluation_report


def report(store, now=None):
    now = time.time() if now is None else now
    observations = []
    for row in store.conn.execute("SELECT value FROM kv WHERE key LIKE 'collector_metrics:%'"):
        item = json.loads(row[0])
        at = item.get('last_attempt_at')
        if isinstance(at, (int, float)) and now - 30 * 86400 <= at <= now:
            observations.append(item)
    times = [item['last_attempt_at'] for item in observations]
    dates = {datetime.fromtimestamp(at, timezone.utc).date().isoformat() for at in times}
    span = max(times) - min(times) if times else 0
    totals = {key: sum(item.get('metrics', {}).get(key, 0) for item in observations)
              for key in ('requests', 'bytes', 'pages', 'new_events', 'updated_events', 'duplicate_payloads')}
    health = json.loads(store.get_kv('collector_health', '{}'))
    # Only fixed source families and scalar aggregates enter public workflow logs.
    sources = {key: {field: value.get(field) for field in ('healthy', 'available', 'history_complete', 'events', 'failed_channels')}
               for key, value in health.get('sources', {}).items()
               if key in ('slack', 'github', 'rss', 'notion', 'mastodon')}
    errors = json.loads(store.get_kv('collector_errors', '{}'))
    error_counts = {}
    for value in errors.values():
        error = str(value.get('error', '')).lower()
        category = next((name for phrase, name in (
            ('budget exhausted', 'request_budget'), ('rate limit', 'upstream_rate_limit'),
            ('429', 'upstream_rate_limit'), ('ambiguous empty page', 'unverifiable_empty_page'),
            ('403', 'upstream_forbidden'), ('timeout', 'upstream_timeout')) if phrase in error), 'other')
        error_counts[category] = error_counts.get(category, 0) + 1
    return {
        'editorial': evaluation_report(store, now),
        'collection': {'first_observation': min(times) if times else None,
                       'last_observation': max(times) if times else None,
                       'observed_utc_dates': len(dates), 'span_days': round(span / 86400, 2),
                       'runs': len(times), 'degraded_runs': sum(not item.get('healthy', False) for item in observations),
                       'two_week_observation_complete': span >= 14 * 86400 and len(dates) >= 14,
                       'totals': totals, 'latest_sources': sources, 'latest_error_counts': error_counts,
                       'missed_events': None,
                       'note': 'Requests include failures and retries. Unknown upstream omissions are not zero; elapsed observations alone do not establish completeness.'},
        'limits': {'http_requests_per_run': max(1, int(os.getenv('REP0RTER_COLLECT_REQUEST_BUDGET', '80'))),
                   'http_requests_per_utc_day': max(1, int(os.getenv('REP0RTER_COLLECT_DAILY_BUDGET', '1500'))),
                   'max_new_reports_per_run': int(os.getenv('REP0RTER_MAX_ITEMS_PER_RUN', '5')),
                   'note': 'These are request and article limits, not a currency budget or a model spending cap.'}}


def register_commands(sub):
    parser = sub.add_parser('acceptance-report', help='aggregate real editorial and collection observation evidence')
    parser.set_defaults(func=cmd_report)


def cmd_report(cfg, args):
    from .store import Store
    with Store(cfg.db_path) as store:
        print(json.dumps(report(store), ensure_ascii=False, indent=2))
    return 0
