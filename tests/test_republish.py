import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from rep0rter import policy, republish, retractions
from rep0rter.config import Config
from rep0rter.store import Event, Post, Store


@pytest.fixture
def setup(tmp_path, monkeypatch):
    cfg = replace(Config(), data_dir=tmp_path, telegram_bot_token='test-only', telegram_chat_id='news',
                  telegram_use_test_chat=False, telegram_language='en', site_url='https://example.org')
    photo = tmp_path / 'report.png'
    photo.write_bytes(b'English card')
    class Renderer:
        def __init__(self, cfg): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def render_report(self, *args): return photo
    monkeypatch.setattr(republish, 'CardRenderer', Renderer)
    with Store(tmp_path / 'test.sqlite') as store:
        republish.ensure(store)
        for number in range(1, 4):
            event = Event(f'slack:C:{number}', 'slack', 'message', 'slack:C', number,
                          author_id=f'U{number}', author_name='Author', text='Original text', url='https://example.org/source')
            store.upsert_events([event])
            post = Post(event.id, number, 9, '原文', '摘要',
                        translations={'en': {'headline': f'Headline {number}', 'summary': 'English summary'}})
            post_id = store.add_post(post)
            cursor = store.conn.execute("INSERT INTO delivery_jobs(post_id,target,status,message_id,created_at,updated_at) VALUES(?,'news','sent',?,1,1)", (post_id, number + 100))
            store.conn.commit()
            store.update_post_delivery(post_id, {'telegram': {'chat_id': 'news', 'message_id': number + 100, 'outbox_id': cursor.lastrowid}})
        yield store, cfg


def response(ok=True, **fields):
    return SimpleNamespace(status_code=200 if ok else 400, json=lambda: {'ok': ok, **fields})


def test_plan_preflight_latest_only_and_delete_all_without_redacting(setup, monkeypatch):
    store, cfg = setup
    with store.conn:
        store.conn.execute("INSERT INTO stories VALUES('story',1,'slack:C:1')")
        for number in (1, 2):
            store.conn.execute("INSERT INTO story_posts VALUES('story',?,?,?,'[]',1)", (number, number, str(number)))
    batch = republish.plan(store, cfg)
    assert batch['manifest']['selected_posts'] == [2, 3]
    assert len(batch['manifest']['old_mappings']) == 3
    assert 'English summary' not in json.dumps(batch)
    calls = []
    monkeypatch.setattr(retractions.requests, 'post', lambda url, **kw: calls.append((url, kw)) or response())
    result = republish.apply(store, cfg, batch['id'])
    assert result['status'] == 'staged'
    assert len(calls) == 3 and all(url.endswith('/deleteMessage') for url, _ in calls)
    assert [r[0] for r in store.conn.execute('SELECT status FROM delivery_jobs ORDER BY id')] == ['sent', 'prepared', 'prepared']
    assert store.post_count() == 3
    assert store.conn.execute('SELECT COUNT(*) FROM retractions').fetchone()[0] == 0
    assert all('telegram' not in json.loads(r[0]) for r in store.conn.execute('SELECT delivery FROM posts'))
    # Once replacements have been sent, resuming this batch cannot reset them.
    with store.conn:
        store.conn.execute("UPDATE delivery_jobs SET status='sent',message_id=999")
    assert republish.apply(store, cfg, batch['id'])['status'] == 'staged'
    assert len(calls) == 3
    assert all(r[0] == 'sent' for r in store.conn.execute('SELECT status FROM delivery_jobs'))


def test_deletion_failure_blocks_all_replacements_and_resume_is_safe(setup, monkeypatch):
    store, cfg = setup
    batch = republish.plan(store, cfg)
    def send(url, **kw):
        if kw['json']['message_id'] == 102:
            return response(False, error_code=400, description="Bad Request: message can't be deleted")
        return response()
    monkeypatch.setattr(retractions.requests, 'post', send)
    result = republish.apply(store, cfg, batch['id'])
    assert result['status'] == 'deleting'
    assert all(r[0] == 'sent' for r in store.conn.execute('SELECT status FROM delivery_jobs'))
    assert all('telegram' in json.loads(r[0]) for r in store.conn.execute('SELECT delivery FROM posts'))
    # An operator may confirm the old message is gone, then resume the batch.
    with store.conn:
        store.conn.execute("UPDATE telegram_mutations SET status='sent' WHERE status='failed'")
    assert republish.apply(store, cfg, batch['id'])['status'] == 'staged'
    assert all(r[0] == 'prepared' for r in store.conn.execute('SELECT status FROM delivery_jobs'))


def test_unknown_delivery_and_unconfirmed_legacy_mapping_block_plan(setup):
    store, cfg = setup
    with store.conn:
        store.conn.execute("UPDATE delivery_jobs SET status='unknown' WHERE id=1")
    with pytest.raises(ValueError, match='Reconcile'):
        republish.plan(store, cfg)
    with store.conn:
        store.conn.execute("UPDATE delivery_jobs SET status='sent' WHERE id=1")
    store.update_post_delivery(1, {'telegram': 101})
    with pytest.raises(ValueError, match='legacy'):
        republish.plan(store, cfg)


def test_missing_english_or_image_blocks_before_any_delete(setup, monkeypatch):
    store, cfg = setup
    batch = republish.plan(store, cfg)
    with store.conn:
        store.conn.execute("UPDATE posts SET translations='{}' WHERE id=1")
    monkeypatch.setattr(retractions.requests, 'post', lambda *a, **kw: pytest.fail('No deletion before English preflight'))
    with pytest.raises(ValueError, match='translation'):
        republish.apply(store, cfg, batch['id'])
    assert store.conn.execute('SELECT COUNT(*) FROM telegram_mutations').fetchone()[0] == 0


def test_exclusion_omits_replacement_but_keeps_proven_old_mapping(setup):
    store, cfg = setup
    policy.add_rule(store, 'user', 'slack:U2')
    batch = republish.plan(store, cfg)
    assert batch['manifest']['selected_posts'] == [1, 3]
    assert [m['post_id'] for m in batch['manifest']['old_mappings']] == [1, 2, 3]


def test_legacy_confirmed_group_deletes_once_and_creates_missing_jobs(setup, monkeypatch):
    store, cfg = setup
    with store.conn:
        store.conn.execute('DELETE FROM delivery_jobs')
    retractions.confirm_group(store, [1, 2, 3], 'news', 99)
    batch = republish.plan(store, cfg)
    calls = []
    monkeypatch.setattr(retractions.requests, 'post', lambda url, **kw: calls.append(kw) or response())
    result = republish.apply(store, cfg, batch['id'])
    assert result['status'] == 'staged' and len(calls) == 1
    assert len(result['manifest']['replacement_jobs']) == 3


def test_changed_mapping_or_selected_posts_blocks_approved_batch(setup):
    store, cfg = setup
    batch = republish.plan(store, cfg)
    retractions.confirm_group(store, [1], 'news', 999)
    with pytest.raises(ValueError, match='changed'):
        republish.apply(store, cfg, batch['id'])
    with pytest.raises(ValueError, match='existing'):
        republish.plan(store, cfg)
