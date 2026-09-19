import json
import pytest
from dataclasses import replace

from rep0rter.config import Config
from rep0rter.reporter import Candidate
from rep0rter.store import Event, Post, Store
from rep0rter import threads_delivery
from rep0rter.publishers import threads


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
    monkeypatch.setattr(threads_delivery.threads, 'get_account', lambda cfg: {'id': cfg.threads_user_id})
    monkeypatch.setattr(threads_delivery.threads, 'get_post',
                        lambda cfg, remote_id: {'id': remote_id, 'permalink': 'https://www.threads.com/@rep0rter.tw/post/42',
                                                'text': json.loads(store.conn.execute('SELECT payload FROM threads_jobs WHERE status="sending"').fetchone()[0])['text']})
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


def test_enqueue_missing_is_idempotent_and_format_uses_site_url(tmp_path, monkeypatch):
    cfg, store, candidate, post = _setup(tmp_path, monkeypatch)
    post.id = store.add_post(post)
    assert threads_delivery.enqueue_missing(cfg, store) == 1
    assert threads_delivery.enqueue_missing(cfg, store) == 0
    text = threads.format_text(cfg, post)
    assert f'https://example.org/posts/{post.id}/' in text
    assert 'https://example.org/source' not in text
    assert threads._units(text) <= 500


def test_threads_ambiguous_send_stays_unknown(tmp_path, monkeypatch):
    cfg, store, candidate, post = _setup(tmp_path, monkeypatch)
    threads_delivery.prepare_posts(cfg, store, [(candidate, post)])
    monkeypatch.setattr(threads_delivery.threads, 'get_account', lambda cfg: {'id': cfg.threads_user_id})
    monkeypatch.setattr(threads_delivery, 'publish_post',
                        lambda *args: (_ for _ in ()).throw(TimeoutError('unknown')))
    assert threads_delivery.deliver_pending(cfg, store) == {}
    row = store.conn.execute('SELECT status FROM threads_jobs WHERE post_id=?', (post.id,)).fetchone()
    assert row['status'] == 'unknown'
    assert threads_delivery.deliver_pending(cfg, store) == {}


def test_threads_batch_limit(tmp_path, monkeypatch):
    cfg, store, candidate, post = _setup(tmp_path, monkeypatch)
    cfg = replace(cfg, threads_batch_size=1)
    for number in range(2):
        event = replace(candidate.event, id=f'threads:{number}')
        store.upsert_events([event])
        item = replace(post, event_id=event.id)
        threads_delivery.prepare_posts(cfg, store, [(replace(candidate, event=event), item)])
    monkeypatch.setattr(threads_delivery.threads, 'get_account', lambda cfg: {'id': cfg.threads_user_id})
    monkeypatch.setattr(threads_delivery.threads, 'get_post',
                        lambda cfg, remote_id: {'id': remote_id, 'permalink': 'https://www.threads.com/@rep0rter.tw/post/42',
                                                'text': json.loads(store.conn.execute('SELECT payload FROM threads_jobs WHERE status="sending"').fetchone()[0])['text']})
    monkeypatch.setattr(threads_delivery, 'publish_post', lambda *args: {'id': 'remote-42'})
    assert len(threads_delivery.deliver_pending(cfg, store)) == 1
    statuses = [row[0] for row in store.conn.execute('SELECT status FROM threads_jobs ORDER BY id')]
    assert statuses == ['sent', 'prepared']


def test_threads_transport_uses_bearer_header_and_autopublish(monkeypatch):
    cfg = replace(Config(), threads_access_token='example-secret')
    calls = []
    class Response:
        status_code = 200
        headers = {}
        def json(self):
            return {'id': 'remote-42'}
    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return Response()
    monkeypatch.setattr(threads.requests, 'request', fake_request)
    assert threads.publish_post(cfg, 'user-1', 'hello') == 'remote-42'
    method, url, kwargs = calls[0]
    assert method == 'POST'
    assert url == 'https://graph.threads.net/v1.0/user-1/threads'
    assert kwargs['headers'] == {'Authorization': 'Bearer example-secret'}
    assert kwargs['data'] == {'media_type': 'TEXT', 'text': 'hello', 'auto_publish_text': 'true'}
    assert 'example-secret' not in url and 'example-secret' not in str(kwargs['data'])


def test_threads_format_counts_utf16_units(tmp_path, monkeypatch):
    cfg, store, candidate, post = _setup(tmp_path, monkeypatch)
    post.id = 12
    post.summary = '😀' * 300
    text = threads.format_text(cfg, post)
    assert threads._units(text) <= 500
    assert text.endswith('/posts/12/' + threads.page_name('zh-TW'))


def test_remote_id_survives_permalink_failure_without_resending(tmp_path, monkeypatch):
    cfg, store, candidate, post = _setup(tmp_path, monkeypatch)
    threads_delivery.prepare_posts(cfg, store, [(candidate, post)])
    monkeypatch.setattr(threads, 'get_account', lambda cfg: {'id': cfg.threads_user_id})
    calls = []
    monkeypatch.setattr(threads_delivery, 'publish_post', lambda *args: calls.append(1) or 'known-remote')
    def failed_read(*args):
        assert store.conn.execute('SELECT thread_id FROM threads_jobs').fetchone()[0] == 'known-remote'
        raise TimeoutError()
    monkeypatch.setattr(threads, 'get_post', failed_read)
    with pytest.raises(RuntimeError, match='manual reconciliation'):
        threads_delivery.deliver_pending(cfg, store)
    job = store.conn.execute('SELECT * FROM threads_jobs').fetchone()
    assert job['status'] == 'unknown' and job['thread_id'] == 'known-remote'
    assert threads_delivery.deliver_pending(cfg, store) == {}
    assert calls == [1]


def test_server_error_is_ambiguous_and_token_is_not_in_repr(monkeypatch):
    from types import SimpleNamespace
    cfg = replace(Config(), threads_access_token='secret-do-not-show')
    assert 'secret-do-not-show' not in repr(cfg)
    monkeypatch.setattr(threads.requests, 'request', lambda *args, **kwargs: SimpleNamespace(status_code=503))
    with pytest.raises(RuntimeError, match='uncertain') as error:
        threads.publish_post(cfg, '123', 'hello')
    assert not isinstance(error.value, threads.ThreadsRejected)


def test_format_prefers_traditional_chinese_edition(tmp_path, monkeypatch):
    cfg, store, candidate, post = _setup(tmp_path, monkeypatch)
    post.id = 12
    post.translations = {'zh-TW': {'headline': '繁體中文標題', 'summary': '繁體中文摘要'}}
    assert threads.format_text(cfg, post).startswith('繁體中文標題\n\n繁體中文摘要')


def test_withdrawal_scrubs_threads_queue(tmp_path, monkeypatch):
    from rep0rter.policy import _scrub
    cfg, store, candidate, post = _setup(tmp_path, monkeypatch)
    threads_delivery.prepare_posts(cfg, store, [(candidate, post)])
    box = threads_delivery.ThreadsOutbox(store)
    job = box.claim(cfg.threads_user_id)
    box.payload(job, 'private after withdrawal')
    with store.conn:
        _scrub(store, candidate.event.id)
    row = store.conn.execute('SELECT * FROM threads_jobs').fetchone()
    assert row['payload'] == '{}' and row['status'] == 'unknown'
    assert box.claim(cfg.threads_user_id) is None
