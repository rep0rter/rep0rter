import json
from dataclasses import replace

from rep0rter.config import Config
from rep0rter.reporter import Candidate
from rep0rter.store import Event, Post, Store
from rep0rter import threads_delivery


def _setup(tmp_path, monkeypatch):
    cfg = replace(
        Config(),
        data_dir=tmp_path,
        site_url='https://example.org',
        threads_enabled=True,
        threads_user_id='123456789',
        threads_access_token='token-123',
    )
    store = Store(tmp_path / 'threads.sqlite')
    event = Event(id='threads:1', source='slack', kind='message', container_id='c', ts=1, text='hello', url='https://example.org/source')
    post = Post(event_id=event.id, published_at=2, score=9, headline='Threads headline', summary='Threads summary')
    store.upsert_events([event])
    return cfg, store, Candidate(event, None, 9), post


def test_config_threads_defaults_and_status():
    cfg = Config()
    assert cfg.threads_enabled is False
    assert cfg.threads_user_id is None
    assert cfg.threads_access_token is None


def test_threads_prepare_and_retry_cycle(tmp_path, monkeypatch):
    cfg, store, candidate, post = _setup(tmp_path, monkeypatch)
    threads_delivery.prepare_posts(cfg, store, [(candidate, post)])
    threads_delivery.prepare_posts(cfg, store, [(candidate, post)])
    jobs = list(store.conn.execute('SELECT * FROM threads_jobs ORDER BY id'))
    assert len(jobs) == 1
    assert jobs[0]['status'] == 'prepared'

    calls = []
    def fake_publish(*args, **kwargs):
        calls.append(args)
        return {'id': 'threads-post-42'}

    monkeypatch.setattr(threads_delivery, 'publish_post', fake_publish)
    result = threads_delivery.deliver_pending(cfg, store)
    assert result == {post.event_id: 'threads-post-42'}
    assert store.conn.execute('SELECT status FROM threads_jobs WHERE post_id=?', (post.id,)).fetchone()[0] == 'sent'
    payload = json.loads(store.conn.execute('SELECT delivery FROM posts WHERE event_id=?', (post.event_id,)).fetchone()[0])
    assert payload['threads']['thread_id'] == 'threads-post-42'


def test_threads_missing_config_keeps_jobs_pending(tmp_path, monkeypatch):
    cfg, store, candidate, post = _setup(tmp_path, monkeypatch)
    cfg = replace(cfg, threads_enabled=False)
    threads_delivery.prepare_posts(cfg, store, [(candidate, post)])
    monkeypatch.setattr(threads_delivery, 'publish_post', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('must not publish')))
    assert threads_delivery.deliver_pending(cfg, store) == {}
    assert store.conn.execute('SELECT status FROM threads_jobs WHERE post_id=?', (post.id,)).fetchone()[0] == 'prepared'
