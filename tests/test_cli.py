from types import SimpleNamespace

import rep0rter.cli as cli
from rep0rter.config import Config
from rep0rter.reporter import Candidate
from rep0rter.store import Event, Post, Store


def test_run_dry_run_collects_without_creating_posts_jobs_or_site(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path)
    event = Event('slack:C:1', 'slack', 'message', 'slack:C', 1, text='原文')
    def collect(store, **kwargs):
        return store.upsert_events([event])
    monkeypatch.setattr(cli, 'collect_all', collect)
    monkeypatch.setattr(cli, 'draft_posts', lambda *a, **kw: [(Candidate(event, None, 8), Post(event.id, 2, 8, '標題', '摘要'))])
    monkeypatch.setattr(cli.telegram, 'publish', lambda *a, **kw: [])
    def forbidden(*args):
        raise AssertionError('dry run must not publish')
    monkeypatch.setattr(cli.site_publisher, 'build', forbidden)
    monkeypatch.setattr(cli, 'deliver_pending', forbidden)
    assert cli.run_once(cfg, dry_run=True, no_llm=True) == (1, 0)
    with Store(cfg.db_path) as store:
        assert store.event_count() == 1
        assert store.post_count() == 0
        assert store.last_run()['error'] is None
        assert store.conn.execute("SELECT name FROM sqlite_master WHERE name='delivery_jobs'").fetchone() is None


def test_report_with_no_new_posts_still_processes_pending_delivery(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path)
    monkeypatch.setattr(cli, 'draft_posts', lambda *a, **kw: [])
    calls = []
    monkeypatch.setattr(cli.site_publisher, 'build', lambda *_: calls.append('build'))
    monkeypatch.setattr(cli, 'deliver_pending', lambda *_: calls.append('deliver'))
    assert cli.cmd_report(cfg, SimpleNamespace(no_llm=True, dry_run=False)) == 0
    assert calls == ['build', 'deliver']


def test_run_site_failure_keeps_posts_and_unsent_jobs_for_recovery(tmp_path, monkeypatch):
    import pytest
    cfg = Config(data_dir=tmp_path)
    event = Event('slack:C:1', 'slack', 'message', 'slack:C', 1, text='原文')
    def collect(store, **kwargs):
        return store.upsert_events([event])
    monkeypatch.setattr(cli, 'collect_all', collect)
    monkeypatch.setattr(cli, 'draft_posts', lambda *a, **kw: [(Candidate(event, None, 8), Post(event.id, 2, 8, '標題', '摘要'))])
    def broken(*_):
        raise OSError('site unavailable')
    monkeypatch.setattr(cli.site_publisher, 'build', broken)
    with pytest.raises(OSError):
        cli.run_once(cfg, no_llm=True)
    with Store(cfg.db_path) as store:
        assert store.post_count() == 1
        assert store.conn.execute('SELECT status FROM delivery_jobs').fetchone()[0] == 'prepared'
        assert 'site unavailable' in store.last_run()['error']
