"""Operational verification is isolated from production and all transports."""
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from rep0rter import operations as ops
from rep0rter.store import Event, Post, Store


@pytest.fixture
def operational_db(tmp_path):
    path = tmp_path / 'rep0rter.sqlite'
    with Store(path) as store:
        store.upsert_events([Event(id='event', source='slack', kind='message', container_id='slack:C', ts=100,
                                  text='release', meta={'bootstrap': False, 'recovery': True})], now=160)
        store.add_post(Post(event_id='event', published_at=180, score=8, headline='Release', summary='Summary'))
        store.set_kv('collector_health', json.dumps({'healthy': True, 'last_healthy_at': 190, 'last_attempt_at': 190}))
        store.conn.execute("CREATE TABLE cursors (id TEXT PRIMARY KEY, value TEXT)")
        store.conn.execute("INSERT INTO cursors VALUES ('slack:C', '100.001')")
        store.conn.execute("CREATE TABLE exclusions (id TEXT PRIMARY KEY)")
        store.conn.execute("INSERT INTO exclusions VALUES ('slack:excluded')")
        store.conn.commit()
    # Latest policy ledger is required separately from database snapshots.
    (tmp_path / 'exclusions.json').write_text(json.dumps({'schema': 1, 'rules': [], 'tombstones': []}))
    return path


def test_backup_captures_wal_and_restores_all_tables(operational_db, tmp_path):
    # Keep writer open so a naive main-file copy would omit WAL contents.
    with sqlite3.connect(operational_db) as writer:
        writer.execute("INSERT INTO kv VALUES ('live-wal', 'present')")
        writer.commit()
        result = ops.backup_database(operational_db, tmp_path / 'backup', now=200)
    assert result['integrity'] == 'ok'
    assert result['tables']['posts'] == 1
    with ops.readonly(result['snapshot']) as conn:
        assert conn.execute("SELECT value FROM kv WHERE key='live-wal'").fetchone()[0] == 'present'
    restored = ops.restore_rehearsal(result['snapshot'], policy_path=tmp_path / 'exclusions.json')
    assert restored['posted_replay_count'] == 0
    assert restored['tables']['cursors'] == 1
    assert restored['tables']['exclusions'] == 1
    assert restored['network_or_delivery_attempted'] is False


def test_restore_rejects_missing_current_policy_and_corruption(operational_db, tmp_path):
    backup = ops.backup_database(operational_db, tmp_path / 'backup', now=200)
    with pytest.raises(FileNotFoundError):
        ops.restore_rehearsal(backup['snapshot'], policy_path=tmp_path / 'missing-policy')
    Path(backup['snapshot']).write_bytes(b'corrupted')
    with pytest.raises(ValueError, match='checksum'):
        ops.restore_rehearsal(backup['snapshot'], policy_path=tmp_path / 'exclusions.json')


def test_retention_is_seven_daily_four_weekly(operational_db, tmp_path):
    destination = tmp_path / 'backups'
    for day in range(40):
        ops.backup_database(operational_db, destination, now=1700000000 + day * 86400)
    assert len(list(destination.glob('daily-*.sqlite'))) == 7
    assert len(list(destination.glob('weekly-*.sqlite'))) == 4
    assert len(list(destination.glob('daily-*.policy.json'))) == 7
    assert len(list(destination.glob('weekly-*.policy.json'))) == 4


def test_offsite_is_explicit_argv_not_shell(operational_db, tmp_path, monkeypatch):
    commands = []
    monkeypatch.setattr(ops.subprocess, 'run', lambda cmd, **kw: commands.append((cmd, kw)))
    ops.backup_database(operational_db, tmp_path / 'backups', now=200, offsite='backup@example.org:/rep0rter')
    assert commands[0][0][-1] == 'backup@example.org:/rep0rter'
    assert commands[0][1].get('shell') is None
    assert ops._read_json(tmp_path / 'backups' / 'offsite.json')['copied_at'] == 200
    with pytest.raises(ValueError, match='Offsite'):
        ops.backup_database(operational_db, tmp_path / 'backups', now=200, offsite='host:/path;uname')


def test_health_is_readonly_and_validates_collection_not_run_exit(operational_db):
    with ops.readonly(operational_db) as conn:
        before = conn.execute('PRAGMA schema_version').fetchone()[0]
    assert ops.check_health(operational_db, now=200, min_free_bytes=0)['healthy']
    with Store(operational_db) as store:
        store.set_kv('collector_health', json.dumps({'healthy': False, 'last_healthy_at': 190, 'containers': 0}))
    report = ops.check_health(operational_db, now=200, min_free_bytes=0)
    assert 'collection_degraded' in report['issues']
    assert 'collection_stale' not in report['issues']
    with ops.readonly(operational_db) as conn:
        assert conn.execute('PRAGMA schema_version').fetchone()[0] == before
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO kv VALUES ('forbidden','write')")


def test_health_failures_stale_backup_disk_and_clock(operational_db, tmp_path):
    with sqlite3.connect(operational_db) as conn:
        for i in range(3):
            conn.execute('INSERT INTO runs(started_at,finished_at,error) VALUES (?,?,?)', (200 + i, 210 + i, 'failed'))
        conn.execute("UPDATE events SET first_seen=90 WHERE id='event'")
        conn.execute('INSERT INTO runs(started_at) VALUES (200)')
    result = ops.check_health(operational_db, now=1000000, backup_dir=tmp_path / 'missing', min_free_bytes=10**30, require_offsite=True)
    assert {'collection_stale', 'backup_stale', 'offsite_backup_stale', 'disk_low', 'run_stuck', 'clock_skew'} <= result['issues'].keys()
    # Current unfinished run does not falsely count as a completed failure.
    assert 'consecutive_failures' not in result['issues']


def test_three_failed_runs_and_recovery_alert_dedup(operational_db):
    with sqlite3.connect(operational_db) as conn:
        for i in range(3):
            conn.execute('INSERT INTO runs(started_at,finished_at,error) VALUES (?,?,?)', (100 + i, 110 + i, 'failed'))
    result = ops.check_health(operational_db, now=200, min_free_bytes=0)
    assert 'consecutive_failures' in result['issues']
    notices, state = ops.alert_transitions(result, {}, now=200)
    assert len(notices) == 1
    assert notices[0]['kind'] == 'alert'
    notices, state = ops.alert_transitions(result, state, now=300)
    assert notices == []
    notices, state = ops.alert_transitions({'issues': {}}, state, now=400)
    assert len(notices) == 1 and notices[0]['kind'] == 'recovery'
    notices, state = ops.alert_transitions({'issues': {}}, state, now=500)
    assert notices == []


def test_health_checks_feed_against_persisted_news_not_current_time(operational_db):
    def get(url, **kwargs):
        body = b'<html></html>' if not url.endswith('.xml') else b'<rss><channel><item><pubDate>Thu, 01 Jan 1970 00:03:00 +0000</pubDate></item></channel></rss>'
        return SimpleNamespace(text=body.decode(), content=body, raise_for_status=lambda: None)
    report = ops.check_health(operational_db, now=200, min_free_bytes=0, site_url='https://site.invalid', get=get)
    assert report['healthy']
    def behind(url, **kwargs):
        body = b'<html></html>' if not url.endswith('.xml') else b'<rss><channel/></rss>'
        return SimpleNamespace(text=body.decode(), content=body, raise_for_status=lambda: None)
    assert 'feed_behind' in ops.check_health(operational_db, now=200, min_free_bytes=0, site_url='https://site.invalid', get=behind)['issues']


def test_admin_destinations_never_fall_back_to_news():
    with pytest.raises(ValueError, match='Explicit'):
        ops.send_admin_alerts([], token='token', target=None)
    with pytest.raises(ValueError, match='differ'):
        ops.send_admin_alerts([], token='token', target='news', public_targets=('news',))
    sent = []
    def post(url, **kwargs):
        sent.append(kwargs['json'])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {'ok': True})
    ops.send_admin_alerts([{'kind': 'recovery', 'key': 'db', 'detail': 'Recovered'}],
                          token='token', target='admin', public_targets=('news',), post=post)
    assert sent[0]['chat_id'] == 'admin'


def test_metrics_unknown_history_and_nearest_rank(operational_db):
    with Store(operational_db) as store:
        store.upsert_events([Event(id='historic', source='slack', kind='thread_reply', container_id='slack:C', ts=100)], now=110)
        store.conn.execute('CREATE TABLE editorial_decisions(event_id TEXT,evaluated_at REAL,selected INTEGER,decision TEXT)')
        store.conn.execute("INSERT INTO editorial_decisions VALUES ('event',170,1,'{\"eligible\":true}')")
        store.conn.commit()
    report = ops.delay_metrics(operational_db)
    assert report['quantile_method'] == 'nearest-rank ceil(p*n)'
    assert report['bootstrap_known'] == 1 and report['bootstrap_unknown'] == 1
    assert report['eligibility_unknown'] == 1
    assert report['delivery_unknown'] == 1
    group = report['groups']['slack|message|bootstrap=False|recovery=True']
    assert group['acquisition']['p95_seconds'] == 60
    assert group['eligibility_after_seen']['p50_seconds'] == 10
    assert group['selection_after_seen']['p50_seconds'] == 10
    assert group['publication_after_seen']['p50_seconds'] == 20
    assert ops._distribution(range(1, 21))['p95_seconds'] == 19


def test_metrics_clock_skew_is_not_zero_clamped(operational_db):
    with sqlite3.connect(operational_db) as conn:
        conn.execute("UPDATE events SET first_seen=90 WHERE id='event'")
    report = ops.delay_metrics(operational_db)
    assert report['clock_skew'][0]['seconds'] == -10
    assert next(iter(report['groups'].values()))['acquisition']['n'] == 0


def test_redacts_bot_tokens_and_api_keys():
    assert '12345:secret_token' not in ops.redact('https://api.telegram.org/bot12345:secret_token/send')
    assert 'secret' not in ops.redact('api_key=secret&foo=visible')


def test_restore_applies_newer_tombstone_without_mutating_snapshot(operational_db, tmp_path):
    result = ops.backup_database(operational_db, tmp_path / 'backups', now=200)
    from rep0rter import policy
    with Store(operational_db) as store:
        policy.redact(store, ['event'])
    restored = ops.restore_rehearsal(result['snapshot'], policy_path=tmp_path / 'exclusions.json')
    assert restored['after_current_policy']['event_tombstones'] == 1
    assert restored['after_current_policy']['retractions'] == 1
    with ops.readonly(result['snapshot']) as conn:
        assert conn.execute("SELECT text FROM events WHERE id='event'").fetchone()[0] == 'release'


def test_missing_backup_file_is_not_healthy_based_only_on_manifest(operational_db, tmp_path):
    directory = tmp_path / 'backups'
    result = ops.backup_database(operational_db, directory, now=200)
    Path(result['snapshot']).unlink()
    report = ops.check_health(operational_db, now=100000, backup_dir=directory, min_free_bytes=0)
    assert 'backup_stale' in report['issues']


def test_maintenance_backup_failure_does_not_advance_success(operational_db, tmp_path, monkeypatch):
    from rep0rter.config import Config
    cfg = Config(data_dir=tmp_path)
    def fail(*args, **kwargs):
        raise OSError('simulated backup failure')
    monkeypatch.setattr(ops, 'backup_database', fail)
    result = ops.maintenance(cfg, now=200000)
    assert result['notifications_sent'] is False
    assert 'backup_failed' in result['health']['issues']
    assert ops._read_json(tmp_path / 'backups' / 'maintenance.json').get('backed_up_at') is None
    assert not (tmp_path / 'backups' / 'alerts.json').exists()


def test_offsite_is_optional_unless_explicitly_configured(operational_db,tmp_path,monkeypatch):
    directory=tmp_path/'backups'
    ops.backup_database(operational_db,directory,now=200)
    local=ops.check_health(operational_db,backup_dir=directory,now=210,min_free_bytes=0)
    assert local['healthy']
    assert 'offsite_backup_stale' not in local['issues']
    required=ops.check_health(operational_db,backup_dir=directory,now=210,min_free_bytes=0,require_offsite=True)
    assert 'offsite_backup_stale' in required['issues']
    monkeypatch.setenv('REP0RTER_BACKUP_OFFSITE','backup@example.invalid:/dedicated')
    configured=ops.check_health(operational_db,backup_dir=directory,now=210,min_free_bytes=0)
    assert 'offsite_backup_stale' in configured['issues']
