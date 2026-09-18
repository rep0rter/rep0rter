"""Read-only health/metrics, consistent snapshots, and explicit admin monitoring.

Health never constructs Store (whose constructor performs migrations). Maintenance
writes only backups and its own state; it never starts collection/publication.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import time
from urllib.parse import quote
import xml.etree.ElementTree as ET

import requests


@contextlib.contextmanager
def readonly(path):
    conn = sqlite3.connect(f"file:{quote(str(Path(path).resolve()), safe='/')}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        conn.execute("BEGIN")
        yield conn
    finally:
        conn.close()


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def redact(value):
    """Sanitize exception/log text without ever printing credential-bearing URLs."""
    value = re.sub(r'bot\d+:[A-Za-z0-9_-]+', 'bot[REDACTED]', str(value))
    return re.sub(r'(?i)(api[_-]?key|token|authorization|password)([=:\s]+)[^\s&]+', r'\1\2[REDACTED]', value)


class RedactingFormatter(logging.Formatter):
    def format(self, record):
        return redact(super().format(record))


def _tables(conn):
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _inventory(conn):
    return {name: conn.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
            for name in _tables(conn)}


def backup_database(db_path, backup_dir, *, now=None, offsite=None):
    """SQLite backup API, verified before replace; daily 7 + ISO-weekly 4.

    offsite is an explicitly supplied rsync remote (user@host:/directory). No
    host or destination is guessed. Failed copies are errors, not success.
    """
    now = time.time() if now is None else now
    directory = Path(backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    daily = directory / ('daily-' + stamp.strftime('%Y-%m-%d') + '.sqlite')
    fd, temporary = tempfile.mkstemp(prefix='.snapshot-', suffix='.sqlite', dir=directory)
    os.close(fd)
    try:
        with readonly(db_path) as src, sqlite3.connect(temporary) as dst:
            src.backup(dst)
            integrity = dst.execute('PRAGMA integrity_check').fetchone()[0]
            if integrity != 'ok':
                raise RuntimeError('Backup integrity check failed')
            tables = _inventory(dst)
            schema_version = dst.execute('PRAGMA user_version').fetchone()[0]
            schema = '\n'.join(str(r[0]) for r in dst.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY name"))
        os.chmod(temporary, 0o600)
        os.replace(temporary, daily)
        policy = Path(db_path).parent / 'exclusions.json'
        if policy.exists():
            _atomic_json(daily.with_suffix('.policy.json'), json.loads(policy.read_text()))
        manifest = {'created_at': now, 'integrity': 'ok', 'schema_version': schema_version,
                    'schema_sha256': hashlib.sha256(schema.encode()).hexdigest(), 'tables': tables,
                    'sha256': hashlib.sha256(daily.read_bytes()).hexdigest()}
        _atomic_json(daily.with_suffix('.json'), manifest)
        week = directory / ('weekly-' + stamp.strftime('%G-W%V') + '.sqlite')
        if not week.exists():
            shutil.copy2(daily, week)
            _atomic_json(week.with_suffix('.json'), manifest)
            if daily.with_suffix('.policy.json').exists():
                shutil.copy2(daily.with_suffix('.policy.json'), week.with_suffix('.policy.json'))
        for prefix, keep in [('daily-', 7), ('weekly-', 4)]:
            for old in sorted(directory.glob(prefix + '*.sqlite'), reverse=True)[keep:]:
                old.unlink()
                old.with_suffix('.json').unlink(missing_ok=True)
                old.with_suffix('.policy.json').unlink(missing_ok=True)
        if offsite:
            if not re.fullmatch(r'[A-Za-z0-9_.@-]+:[/A-Za-z0-9_.~-]+', offsite):
                raise ValueError('Offsite must be an explicit rsync host:/path destination')
            subprocess.run(['rsync', '-a', '--', str(directory) + '/', offsite], check=True,
                           timeout=300, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _atomic_json(directory / 'offsite.json', {'copied_at': now, 'snapshot': daily.name})
        return {'snapshot': str(daily), **manifest, 'offsite_copied': bool(offsite)}
    finally:
        Path(temporary).unlink(missing_ok=True)


def restore_rehearsal(snapshot, *, policy_path):
    """Isolated, no-network restore: verify every table and no-repeat selection.

    This deliberately imports neither collectors nor publishers and never
    modifies the source snapshot. Outstanding delivery jobs remain outstanding.
    """
    snapshot = Path(snapshot)
    policy_path = Path(policy_path)
    current_policy = json.loads(policy_path.read_text())  # Fail closed: never revive withdrawn content.
    manifest = _read_json(snapshot.with_suffix('.json'))
    digest = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    if not manifest or manifest.get('sha256') != digest:
        raise ValueError('Backup checksum manifest missing or mismatch')
    with tempfile.TemporaryDirectory(prefix='rep0rter-restore-') as directory:
        restored = Path(directory) / 'rep0rter.sqlite'
        with readonly(snapshot) as src, sqlite3.connect(restored) as dst:
            src.backup(dst)
        with readonly(restored) as conn:
            if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Restored database integrity check failed')
            counts = _inventory(conn)
            if counts != manifest['tables']:
                raise ValueError('Restored inventory differs from backup')
            # A posted root must never be selected again by the unposted query.
            replayed = conn.execute('''SELECT count(*) FROM posts p JOIN events e ON e.id=p.event_id
                WHERE e.id IN (SELECT e2.id FROM events e2 LEFT JOIN posts p2 ON p2.event_id=e2.id
                WHERE e2.kind='message' AND p2.id IS NULL)''').fetchone()[0]
            if replayed:
                raise ValueError('Restored posts would be selected again')
            # Snapshot includes cursors/exclusions/outbox and all future tables,
            # rather than a hand-maintained export subset.
            fingerprints = {}
            for table in _tables(conn):
                rows = [tuple(r) for r in conn.execute('SELECT * FROM "' + table.replace('"', '""') + '"')]
                fingerprints[table] = hashlib.sha256(repr(sorted(rows, key=repr)).encode()).hexdigest()
        with readonly(snapshot) as original:
            for table, expected in fingerprints.items():
                rows = [tuple(r) for r in original.execute('SELECT * FROM "' + table.replace('"', '""') + '"')]
                if hashlib.sha256(repr(sorted(rows, key=repr)).encode()).hexdigest() != expected:
                    raise ValueError('Restored table differs: ' + table)
        _atomic_json(Path(directory) / 'exclusions.json', current_policy)
        from .store import Store
        with Store(restored) as store:
            restored_after_policy = _inventory(store.conn)
    return {'integrity': 'ok', 'tables': counts, 'posted_replay_count': replayed,
            'after_current_policy': restored_after_policy, 'current_policy_applied': True,
            'network_or_delivery_attempted': False}


def _issue(issues, key, detail):
    issues[key] = detail


class _PolicyReadView:
    """Minimal event reader for policy evaluation, with no Store initialization."""
    def __init__(self, conn):
        self.conn = conn

    def get_event(self, event_id):
        from .store import Event
        row = self.conn.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
        if row is None:
            return None
        values = {key: row[key] for key in Event.__dataclass_fields__ if key != 'meta'}
        return Event(**values, meta=json.loads(row['meta'] or '{}'))


def _latest_visible_post_at(conn):
    """Match publication policy without migrating historical DBs or modifying ledgers."""
    from .policy import event_allowed
    view = _PolicyReadView(conn)
    tables = set(_tables(conn))
    policy_available = {'policy_rules', 'event_tombstones'} <= tables
    query = 'SELECT p.event_id,p.published_at FROM posts p'
    if 'retractions' in tables:
        query += ' WHERE NOT EXISTS (SELECT 1 FROM retractions r WHERE r.post_id=p.id)'
    query += ' ORDER BY p.published_at DESC'
    for row in conn.execute(query):
        event = view.get_event(row['event_id'])
        if event is None:
            continue
        if policy_available:
            allowed = event_allowed(view, event)
        else:
            # Older schemas have no policy tables; still honor known deletion and
            # visibility rather than inventing policy rows in a read-only check.
            allowed = (not event.meta.get('deleted_at') and event.meta.get('content_status') != 'deleted'
                       and event.meta.get('visibility', 'public' if event.source == 'slack' else 'unknown') == 'public')
        if allowed:
            return row['published_at']
    return None


def check_health(db_path, *, backup_dir=None, now=None, stale_seconds=9000,
                 min_free_bytes=512 * 1024**2, site_url=None, get=requests.get, require_offsite=None):
    """No migrations, writes, alerts, collection or LLM calls."""
    now = time.time() if now is None else now
    require_offsite = bool(os.environ.get('REP0RTER_BACKUP_OFFSITE')) if require_offsite is None else require_offsite
    issues = {}
    details = {'checked_at': now}
    db_path = Path(db_path)
    try:
        usage = shutil.disk_usage(db_path.parent)
        details['disk_free_bytes'] = usage.free
        if usage.free < min_free_bytes:
            _issue(issues, 'disk_low', 'Free disk below configured reserve')
        with readonly(db_path) as conn:
            conn.execute('SELECT count(*) FROM events').fetchone()
            runs = [dict(r) for r in conn.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 3')]
            raw = conn.execute("SELECT value FROM kv WHERE key='collector_health'").fetchone()
            health = json.loads(raw[0]) if raw else {}
            details['collector'] = health
            healthy_at = health.get('last_healthy_at')
            if not healthy_at or now - float(healthy_at) > stale_seconds:
                _issue(issues, 'collection_stale', 'No verified healthy collection within 2.5 hours')
            if health and not health.get('healthy', False):
                _issue(issues, 'collection_degraded', 'Latest collection did not validate all source checks')
            if len(runs) == 3 and all(r['error'] for r in runs):
                _issue(issues, 'consecutive_failures', 'Three consecutive runs failed')
            running = conn.execute('SELECT started_at FROM runs WHERE finished_at IS NULL ORDER BY started_at DESC LIMIT 1').fetchone()
            if running and now - running[0] > stale_seconds:
                _issue(issues, 'run_stuck', 'An unfinished run exceeds the freshness limit')
            negative = conn.execute('SELECT count(*) FROM events WHERE first_seen < ts - 1').fetchone()[0]
            if negative:
                _issue(issues, 'clock_skew', f'{negative} event(s) have negative acquisition delay')
            latest_post = _latest_visible_post_at(conn)
            details['latest_post_at'] = latest_post
    except (sqlite3.Error, OSError, ValueError, TypeError):
        _issue(issues, 'database_unreadable', 'Database or health metadata could not be read')
    if backup_dir is not None:
        manifests = [_read_json(p) for p in Path(backup_dir).glob('daily-*.json')
                     if p.with_suffix('.sqlite').is_file()]
        newest = max((m.get('created_at', 0) for m in manifests if m.get('integrity') == 'ok'), default=0)
        details['latest_backup_at'] = newest or None
        if not newest or now - newest > 26 * 3600:
            _issue(issues, 'backup_stale', 'No verified backup within 26 hours')
        remote = _read_json(Path(backup_dir) / 'offsite.json').get('copied_at', 0)
        details['latest_offsite_at'] = remote or None
        if require_offsite and (not remote or now - remote > 26 * 3600):
            _issue(issues, 'offsite_backup_stale', 'No verified off-host copy within 26 hours')
    if site_url:
        try:
            home = get(site_url.rstrip('/') + '/', timeout=15)
            home.raise_for_status()
            if '<html' not in home.text.lower():
                raise ValueError('Homepage is not HTML')
            response = get(site_url.rstrip('/') + '/feed.xml', timeout=15)
            response.raise_for_status()
            feed = ET.fromstring(response.content)
            if feed.tag != 'rss' or feed.find('channel') is None:
                raise ValueError('Not an RSS feed')
            from email.utils import parsedate_to_datetime
            dates = [parsedate_to_datetime(e.text).timestamp() for e in feed.findall('./channel/item/pubDate')]
            latest = max(dates, default=0)
            details['feed_latest_post_at'] = latest or None
            # A quiet source is healthy. Compare feed to persisted publications,
            # never flag merely because there has not been news recently.
            if details.get('latest_post_at') and latest + 1 < details['latest_post_at']:
                _issue(issues, 'feed_behind', 'Feed omits the latest persisted publication')
        except (requests.RequestException, ValueError, TypeError, ET.ParseError, OverflowError):
            _issue(issues, 'web_unhealthy', 'Homepage or RSS validation failed')
    return {'healthy': not issues, 'issues': issues, **details}


def alert_transitions(report, state, *, now=None, repeat_seconds=21600):
    """Pure dedup/throttle state machine. Transport is deliberately separate."""
    now = time.time() if now is None else now
    previous = state.get('active', {})
    active = {}
    notifications = []
    for key, detail in report['issues'].items():
        old = previous.get(key, {})
        last = old.get('notified_at')
        if last is None or now - last >= repeat_seconds:
            notifications.append({'kind': 'alert', 'key': key, 'detail': detail})
            last = now
        active[key] = {'since': old.get('since', now), 'notified_at': last}
    for key in previous.keys() - report['issues'].keys():
        notifications.append({'kind': 'recovery', 'key': key, 'detail': 'Condition recovered'})
    return notifications, {'active': active, 'checked_at': now}


def send_admin_alerts(notifications, *, token, target, public_targets=(), post=requests.post):
    if not target or not token:
        raise ValueError('Explicit REP0RTER_ADMIN_CHAT_ID and REP0RTER_ADMIN_BOT_TOKEN are required')
    if target in public_targets:
        raise ValueError('Admin destination must differ from news/test publication destinations')
    for notice in notifications:
        try:
            response = post(f'https://api.telegram.org/bot{token}/sendMessage',
                            json={'chat_id': target, 'text': 'rep0rter ' + notice['kind'] + ': ' + notice['key'] + '\n' + notice['detail']}, timeout=15)
            response.raise_for_status()
            if not response.json().get('ok'):
                raise ValueError('Admin notification rejected')
        except (requests.RequestException, ValueError):
            raise RuntimeError('Admin notification failed; credentials omitted') from None


def _distribution(values):
    values = sorted(values)
    if not values:
        return {'n': 0, 'p50_seconds': None, 'p95_seconds': None}
    return {'n': len(values), 'min_seconds': values[0], 'max_seconds': values[-1],
            'p50_seconds': values[math.ceil(.5 * len(values)) - 1],
            'p95_seconds': values[math.ceil(.95 * len(values)) - 1],
            'over_two_hours_fraction': sum(v > 7200 for v in values) / len(values)}


def delay_metrics(db_path):
    """Nearest-rank distributions; total acquisition != upstream visibility."""
    with readonly(db_path) as conn:
        events = [dict(r) for r in conn.execute('SELECT id,source,kind,ts,first_seen,meta FROM events')]
        posts = {r['event_id']: dict(r) for r in conn.execute('SELECT event_id,id,published_at FROM posts')}
        tables = _tables(conn)
        sent = {r['post_id']: r['updated_at'] for r in conn.execute("SELECT post_id,updated_at FROM delivery_jobs WHERE status='sent'")} if 'delivery_jobs' in tables else {}
        eligibility = {}
        selection = {}
        if 'editorial_decisions' in tables:
            eligibility = {r[0]: r[1] for r in conn.execute("SELECT event_id,min(evaluated_at) FROM editorial_decisions WHERE json_extract(decision,'$.eligible')=1 GROUP BY event_id")}
            selection = {r[0]: r[1] for r in conn.execute('SELECT event_id,min(evaluated_at) FROM editorial_decisions WHERE selected=1 GROUP BY event_id')}
        run_seconds = [r[0] for r in conn.execute('SELECT finished_at-started_at FROM runs WHERE finished_at IS NOT NULL AND finished_at>=started_at')]
    groups = {}
    negative = []
    bootstrap_known = bootstrap_count = eligibility_unknown = delivery_unknown = 0
    for event in events:
        meta = json.loads(event['meta'] or '{}')
        bootstrap = meta.get('bootstrap')
        if isinstance(bootstrap, bool):
            bootstrap_known += 1
            bootstrap_count += bootstrap
        else:
            bootstrap = 'unknown'
        recovery = meta.get('recovery', 'unknown')
        key = f"{event['source']}|{event['kind']}|bootstrap={bootstrap}|recovery={recovery}"
        group = groups.setdefault(key, {'acquisition': [], 'eligibility_after_seen': [], 'selection_after_seen': [], 'publication_after_seen': [], 'delivery_after_publication': []})
        acquisition = event['first_seen'] - event['ts']
        if acquisition < 0:
            negative.append({'event_id': event['id'], 'seconds': acquisition, 'metric': 'acquisition'})
        else:
            group['acquisition'].append(acquisition)
        eligible = eligibility.get(event['id'], meta.get('eligible_at'))
        selected = selection.get(event['id'])
        if selected is not None:
            lag = selected - event['first_seen']
            if lag >= 0:
                group['selection_after_seen'].append(lag)
            else:
                negative.append({'event_id': event['id'], 'seconds': lag, 'metric': 'selection_after_seen'})
        if isinstance(eligible, (float, int)):
            lag = eligible - event['first_seen']
            if lag >= 0:
                group['eligibility_after_seen'].append(lag)
            else:
                negative.append({'event_id': event['id'], 'seconds': lag, 'metric': 'eligibility_after_seen'})
        else:
            eligibility_unknown += 1
        post = posts.get(event['id'])
        if post:
            lag = post['published_at'] - event['first_seen']
            if lag >= 0:
                group['publication_after_seen'].append(lag)
            else:
                negative.append({'event_id': event['id'], 'seconds': lag, 'metric': 'publication_after_seen'})
            if post['id'] in sent:
                lag = sent[post['id']] - post['published_at']
                if lag >= 0:
                    group['delivery_after_publication'].append(lag)
                else:
                    negative.append({'event_id': event['id'], 'seconds': lag, 'metric': 'delivery_after_publication'})
            else:
                delivery_unknown += 1
    return {'quantile_method': 'nearest-rank ceil(p*n)', 'unit': 'seconds',
            'acquisition_definition': 'first_seen - source timestamp; upstream visibility and poll wait cannot be separated',
            'observation_start': min((r['first_seen'] for r in events), default=None),
            'observation_end': max((r['first_seen'] for r in events), default=None),
            'event_count': len(events), 'bootstrap_known': bootstrap_known, 'bootstrap_unknown': len(events) - bootstrap_known,
            'bootstrap_fraction_among_known': bootstrap_count / bootstrap_known if bootstrap_known else None,
            'eligibility_unknown': eligibility_unknown, 'delivery_unknown': delivery_unknown,
            'clock_skew': negative, 'run_duration': _distribution(run_seconds),
            'groups': {key: {metric: _distribution(values) for metric, values in group.items()} for key, group in groups.items()}}


def maintenance(cfg, *, backup_dir=None, offsite=None, send_alerts=False, now=None):
    """Run hourly from a separate maintenance service; daily backup/monthly drill."""
    import fcntl
    now = time.time() if now is None else now
    directory = Path(backup_dir or cfg.data_dir / 'backups')
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.maintenance.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = _read_json(directory / 'maintenance.json')
        backup_error = None
        try:
            if now - state.get('backed_up_at', 0) >= 86400:
                result = backup_database(cfg.db_path, directory, now=now, offsite=offsite)
                state['backed_up_at'] = now
                state['snapshot'] = result['snapshot']
            if now - state.get('rehearsed_at', 0) >= 28 * 86400 and state.get('snapshot'):
                state['rehearsal'] = restore_rehearsal(state['snapshot'], policy_path=cfg.data_dir / 'exclusions.json')
                state['rehearsed_at'] = now
        except (OSError, sqlite3.Error, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            backup_error = type(exc).__name__
        report = check_health(cfg.db_path, backup_dir=directory, now=now, site_url=os.environ.get('REP0RTER_HEALTH_URL'), require_offsite=bool(offsite))
        if backup_error:
            report['issues']['backup_failed'] = 'Backup or restore rehearsal failed: ' + backup_error
            report['healthy'] = False
        previous = _read_json(directory / ('alerts.json' if send_alerts else 'alerts-preview.json'))
        notices, alert_state = alert_transitions(report, previous, now=now)
        if send_alerts and notices:
            send_admin_alerts(notices, token=os.environ.get('REP0RTER_ADMIN_BOT_TOKEN'),
                              target=os.environ.get('REP0RTER_ADMIN_CHAT_ID'),
                              public_targets=(cfg.telegram_chat_id, cfg.telegram_test_chat_id))
            _atomic_json(directory / 'alerts.json', alert_state)
        elif not send_alerts:
            # Dry-run state cannot suppress a future actual alert.
            _atomic_json(directory / 'alerts-preview.json', alert_state)
        _atomic_json(directory / 'maintenance.json', state)
        return {'health': report, 'notifications': notices, 'notifications_sent': bool(send_alerts and notices)}


def cmd_health(cfg, args):
    result = check_health(cfg.db_path, backup_dir=args.backup_dir or cfg.data_dir / 'backups',
                          min_free_bytes=args.min_free_mb * 1024**2, site_url=args.url, require_offsite=args.require_offsite or bool(os.environ.get('REP0RTER_BACKUP_OFFSITE')))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['healthy'] else 1


def cmd_metrics(cfg, args):
    print(json.dumps(delay_metrics(cfg.db_path), ensure_ascii=False, indent=2))
    return 0


def cmd_backup(cfg, args):
    print(json.dumps(backup_database(cfg.db_path, args.directory or cfg.data_dir / 'backups', offsite=args.offsite), indent=2))
    return 0


def cmd_rehearse(cfg, args):
    print(json.dumps(restore_rehearsal(args.snapshot, policy_path=args.policy), indent=2))
    return 0


def cmd_maintenance(cfg, args):
    while True:
        result = maintenance(cfg, offsite=args.offsite, send_alerts=args.send_alerts)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if not args.loop:
            return 0 if result['health']['healthy'] else 1
        time.sleep(3600)


def register_commands(sub):
    p = sub.add_parser('health', help='read-only freshness, backup, disk and optional web health')
    p.add_argument('--url', default=os.environ.get('REP0RTER_HEALTH_URL'))
    p.add_argument('--backup-dir', type=Path)
    p.add_argument('--require-offsite', action='store_true', help='also require an off-host copy within 26 hours')
    p.add_argument('--min-free-mb', type=int, default=512)
    p.set_defaults(func=cmd_health, readonly=True)
    p = sub.add_parser('metrics', help='read-only acquisition/eligibility/publication latency distributions')
    p.set_defaults(func=cmd_metrics, readonly=True)
    p = sub.add_parser('backup', help='consistent SQLite snapshot with retention and optional off-host rsync')
    p.add_argument('--directory', type=Path)
    p.add_argument('--offsite', default=os.environ.get('REP0RTER_BACKUP_OFFSITE'))
    p.set_defaults(func=cmd_backup, readonly=True)
    p = sub.add_parser('restore-rehearsal', help='offline isolated restore verification; never publishes')
    p.add_argument('snapshot', type=Path)
    p.add_argument('--policy', type=Path, required=True, help='current exclusions.json; never restore older policy over current rules')
    p.set_defaults(func=cmd_rehearse, readonly=True)
    p = sub.add_parser('maintenance', help='daily backups/monthly restore drills and explicit admin monitoring')
    p.add_argument('--loop', action='store_true')
    p.add_argument('--offsite', default=os.environ.get('REP0RTER_BACKUP_OFFSITE'))
    p.add_argument('--send-alerts', action='store_true', default=os.environ.get('REP0RTER_SEND_ADMIN_ALERTS') == '1')
    p.set_defaults(func=cmd_maintenance, readonly=True)
