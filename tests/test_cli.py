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


def test_scheduled_translation_recovery_is_bounded_fair_and_does_not_republish(tmp_path, monkeypatch):
    import json
    from rep0rter.i18n import LANGUAGES
    cfg = Config(data_dir=tmp_path, ai_base_url='https://example.test', ai_api_key='test', ai_model='test')
    monkeypatch.setattr(cli.time, 'time', lambda: 10000)
    monkeypatch.setattr(cli, 'LLM', lambda cfg: object())
    attempted = []
    def translate(post, llm):
        attempted.append(post.id)
        return False  # The failing oldest item must not starve unattempted items.
    monkeypatch.setattr(cli, 'translate_post', translate)
    with Store(cfg.db_path) as store:
        for n in range(5):
            event = Event(f'slack:C:{n}', 'slack', 'message', 'slack:C', n, text='Synthetic source')
            store.upsert_events([event])
            store.add_post(Post(event.id, n, 8, '標題', '摘要', delivery={'telegram': n+100}))
        first = cli._backfill_translations(store, cfg)
        assert first == {'attempted': 3, 'updated': 0, 'incomplete': 5, 'review_required': 0, 'checked_at': 10000}
        assert attempted == [1, 2, 3]
        assert json.loads(store.get_kv('translation_retry:1'))['next_attempt_at'] == 13600
        second = cli._backfill_translations(store, cfg)
        assert second['attempted'] == 2
        assert attempted == [1, 2, 3, 4, 5]
        assert cli._backfill_translations(store, cfg)['attempted'] == 0
        monkeypatch.setattr(cli.time, 'time', lambda: 13600)
        def succeed(post, llm):
            post.translations = {lang: {'headline': 'Title', 'summary': 'Summary'} for lang in LANGUAGES}
            return True
        monkeypatch.setattr(cli, 'translate_post', succeed)
        recovered = cli._backfill_translations(store, cfg)
        assert (recovered['updated'], recovered['incomplete']) == (3, 2)
        assert store.get_kv('translation_retry:1') is None
        assert store.post_count() == 5
        assert [p.delivery['telegram'] for p, _, _ in store.recent_posts()] == [104, 103, 102, 101, 100]
        assert store.conn.execute("SELECT name FROM sqlite_master WHERE name='delivery_jobs'").fetchone() is None


def test_scheduled_translation_runs_before_build_and_skips_no_llm(tmp_path, monkeypatch):
    cfg = Config(data_dir=tmp_path)
    monkeypatch.setattr(cli, 'collect_all', lambda *a, **kw: 0)
    monkeypatch.setattr(cli, 'draft_posts', lambda *a, **kw: [])
    calls = []
    monkeypatch.setattr(cli, '_backfill_translations', lambda *_: calls.append('translate'))
    monkeypatch.setattr(cli.site_publisher, 'build', lambda *_: calls.append('build'))
    monkeypatch.setattr(cli, 'deliver_pending', lambda *_: calls.append('deliver'))
    cli.run_once(cfg)
    assert calls == ['translate', 'build', 'deliver']
    calls.clear()
    cli.run_once(cfg, no_llm=True)
    assert calls == ['build', 'deliver']


def test_run_command_returns_failure_for_degraded_collection(tmp_path, monkeypatch):
    import json
    cfg = Config(data_dir=tmp_path)
    def run(*args, **kwargs):
        with Store(cfg.db_path) as store:
            store.set_kv('collector_health', json.dumps({'healthy': False}))
        return (0, 0)
    monkeypatch.setattr(cli, 'run_once', run)
    assert cli.cmd_run(cfg, SimpleNamespace(dry_run=False, no_llm=True, days=None)) == 1


def test_translation_retries_stop_until_manual_retry_or_copy_changes(tmp_path, monkeypatch):
    import json
    cfg = Config(data_dir=tmp_path, ai_base_url='https://example.test', ai_api_key='test', ai_model='test')
    monkeypatch.setattr(cli, 'LLM', lambda cfg: object())
    monkeypatch.setattr(cli, 'translate_post', lambda *args: False)
    with Store(cfg.db_path) as store:
        event = Event('slack:C:1', 'slack', 'message', 'slack:C', 1, text='Synthetic source')
        store.upsert_events([event])
        store.add_post(Post(event.id, 1, 8, 'Title', 'Summary'))
        for n in range(6):
            monkeypatch.setattr(cli.time, 'time', lambda n=n: 10000 + n * 86400)
            assert cli._backfill_translations(store, cfg)['attempted'] == 1
        monkeypatch.setattr(cli.time, 'time', lambda: 10000 + 6 * 86400)
        stopped = cli._backfill_translations(store, cfg)
        assert stopped['attempted'] == 0 and stopped['review_required'] == 1
        with store.conn:
            store.conn.execute("UPDATE posts SET summary='Corrected summary'")
        assert cli._backfill_translations(store, cfg)['attempted'] == 1
        assert json.loads(store.get_kv('translation_retry:1'))['attempts'] == 1
