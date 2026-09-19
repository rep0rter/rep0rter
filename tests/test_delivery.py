import json
from dataclasses import replace

import pytest
import requests

from rep0rter import delivery
from rep0rter.config import Config
from rep0rter.i18n import page_name
from rep0rter.publishers import telegram
from rep0rter.reporter import Candidate
from rep0rter.store import Event, Post, Store


@pytest.fixture
def setup_delivery(tmp_path, monkeypatch):
    cfg = replace(Config(), data_dir=tmp_path, telegram_bot_token='test-only', telegram_chat_id='test-chat',
                  telegram_use_test_chat=False, telegram_test_chat_id=None, telegram_language='en',
                  site_url='https://example.org')
    store = Store(tmp_path / 'test.sqlite')
    photo = tmp_path / 'source.png'
    photo.write_bytes(b'fake-image-for-mocked-transport')

    class Renderer:
        def __init__(self, cfg): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def render(self, *args): return photo

    monkeypatch.setattr(delivery, 'CardRenderer', Renderer)
    items = []
    for i in range(3):
        event = Event(id=f'e:{i}', source='slack', kind='message', container_id='c', ts=1,
                      text='原文', url='https://example.org/source')
        post = Post(event_id=event.id, published_at=2, score=9, headline='中文', summary='摘要',
                    translations={'en': {'headline': f'News {i}', 'summary': 'English summary'}})
        store.upsert_events([event])
        items.append((Candidate(event, None, 9), post))
    yield cfg, store, items
    store.close()


def jobs(store):
    return list(store.conn.execute('SELECT * FROM delivery_jobs ORDER BY id'))


def test_per_photo_persistence_and_partial_retry(setup_delivery, monkeypatch):
    cfg, store, items = setup_delivery
    delivery.prepare_posts(cfg, store, items)
    calls = []

    def send(*args):
        calls.append(args)
        if len(calls) == 2:
            raise telegram.TelegramRejected(429, 30)
        return 100 + len(calls)

    monkeypatch.setattr(telegram, 'send_photo', send)
    assert delivery.deliver_pending(cfg, store) == {items[0][1].id: 101}
    assert [j['status'] for j in jobs(store)] == ['sent', 'failed', 'prepared']
    with store.conn:
        store.conn.execute('UPDATE delivery_jobs SET next_attempt=0 WHERE status="failed"')
    assert delivery.deliver_pending(cfg, store) == {items[1][1].id: 103, items[2][1].id: 104}
    assert len(calls) == 4
    for row, expected in zip(store.conn.execute('SELECT * FROM posts ORDER BY id'), [101, 103, 104]):
        assert json.loads(row['delivery'])['telegram']['message_id'] == expected
        assert json.loads(row['delivery'])['telegram']['chat_id'] == 'test-chat'
    assert delivery.deliver_pending(cfg, store) == {}


def test_timeout_never_automatically_retried(setup_delivery, monkeypatch):
    cfg, store, items = setup_delivery
    delivery.prepare_posts(cfg, store, items[:1])
    calls = []

    def send(*args):
        calls.append(1)
        raise requests.Timeout('must not log token-containing URL')

    monkeypatch.setattr(telegram, 'send_photo', send)
    delivery.deliver_pending(cfg, store)
    delivery.deliver_pending(cfg, store)
    assert jobs(store)[0]['status'] == 'unknown'
    assert calls == [1]
    delivery.DeliveryOutbox(store).reconcile(jobs(store)[0]['id'], message_id=987)
    assert jobs(store)[0]['status'] == 'sent'
    assert json.loads(store.conn.execute('SELECT delivery FROM posts').fetchone()[0])['telegram']['message_id'] == 987


def test_post_send_database_failure_becomes_unknown(setup_delivery, monkeypatch):
    cfg, store, items = setup_delivery
    delivery.prepare_posts(cfg, store, items[:1])
    monkeypatch.setattr(telegram, 'send_photo', lambda *args: 456)

    def fail(*args): raise RuntimeError('disk unavailable')

    monkeypatch.setattr(delivery.DeliveryOutbox, 'sent', fail)
    with pytest.raises(RuntimeError, match='manual reconciliation'):
        delivery.deliver_pending(cfg, store)
    assert jobs(store)[0]['status'] == 'unknown'
    assert '456' in jobs(store)[0]['error']


def test_missing_configuration_keeps_pending_and_test_chat_never_falls_back(setup_delivery, monkeypatch):
    cfg, store, items = setup_delivery
    cfg = replace(cfg, telegram_use_test_chat=True)
    assert cfg.telegram_target is None
    delivery.prepare_posts(cfg, store, items)
    monkeypatch.setattr(telegram, 'send_photo', lambda *args: pytest.fail('must not send'))
    assert delivery.deliver_pending(cfg, store) == {}
    assert [j['status'] for j in jobs(store)] == ['prepared'] * 3
    assert store.post_count() == 3


def test_atomic_draft_and_outbox_and_duplicate_prepare(setup_delivery):
    cfg, store, items = setup_delivery
    delivery.prepare_posts(cfg, store, items)
    delivery.prepare_posts(cfg, store, items)
    assert store.post_count() == len(jobs(store)) == 3
    assert all(post.id for _, post in items)


def test_two_connections_cannot_claim_same_job_and_expired_lease_unknown(setup_delivery):
    cfg, store, items = setup_delivery
    delivery.prepare_posts(cfg, store, items[:1])
    first = delivery.DeliveryOutbox(store)
    with Store(store.path) as second_store:
        second = delivery.DeliveryOutbox(second_store)
        job = first.claim('test-chat', now=10)
        assert job is not None
        assert second.claim('test-chat', now=11) is None
        second.recover(now=10 + delivery.LEASE_SECONDS + 1)
        assert jobs(store)[0]['status'] == 'unknown'
        assert second.claim('test-chat') is None
        second.reconcile(job['id'], retry=True)
        assert second.claim('test-chat') is not None


def test_caption_localization_urls_limits_and_unsafe_scheme(setup_delivery):
    cfg, _, items = setup_delivery
    c, p = items[0]
    p.id = 12
    p.translations['en']['summary'] = '<&😀' * 5000
    c.event.url = 'javascript:alert(1)'
    result = telegram.format_caption(cfg, c, p)
    assert '<b>News 0</b>' in result
    assert 'javascript:' not in result
    assert len(result.encode('utf-16-le')) // 2 <= 1024
    assert '/posts/12/index.ko.html' in result
    assert '/posts/12/index.ja.html' in result
    assert f'/posts/12/{page_name("zh-TW")}' in result
    assert '/posts/12/index.html' in result
    c.event.url = 'https://example.org/?x=" onclick="bad'
    assert 'href="https://example.org/?x=&quot; onclick=&quot;bad"' in telegram.format_item(c, p)


def test_oversized_single_legacy_text_rejected(setup_delivery):
    _, _, items = setup_delivery
    items[0][1].translations['en']['summary'] = 'x' * 5000
    with pytest.raises(ValueError, match='4096'):
        telegram.format_messages(items[:1])


def test_send_photo_transport_uses_multipart_and_structured_rejection(setup_delivery, monkeypatch):
    cfg, store, _ = setup_delivery
    received = []

    class Response:
        status_code = 429
        def json(self): return {'ok': False, 'error_code': 429, 'parameters': {'retry_after': 12}}

    def fake_post(url, **kwargs):
        received.append((url, kwargs))
        assert kwargs['files']['photo'][1].read().startswith(b'fake-image')
        return Response()

    monkeypatch.setattr(telegram.requests, 'post', fake_post)
    with pytest.raises(telegram.TelegramRejected) as exc:
        telegram.send_photo(cfg, 'test-chat', cfg.data_dir / 'source.png', '<b>caption</b>')
    assert exc.value.retry_after == 12
    assert received[0][0].endswith('/sendPhoto')
    assert received[0][1]['data']['chat_id'] == 'test-chat'


def test_failed_prepare_rolls_back_posts_and_jobs(setup_delivery):
    cfg, store, items = setup_delivery
    items[1][1].headline = None
    with pytest.raises(Exception):
        delivery.prepare_posts(cfg, store, items)
    assert store.post_count() == 0
    assert jobs(store) == []


def test_explicit_rejection_needs_manual_retry(setup_delivery, monkeypatch):
    cfg, store, items = setup_delivery
    delivery.prepare_posts(cfg, store, items[:1])
    calls = []

    def reject(*args):
        calls.append(1)
        raise telegram.TelegramRejected(400)

    monkeypatch.setattr(telegram, 'send_photo', reject)
    delivery.deliver_pending(cfg, store)
    delivery.deliver_pending(cfg, store)
    assert calls == [1]
    assert jobs(store)[0]['status'] == 'failed'
    assert jobs(store)[0]['next_attempt'] is None
