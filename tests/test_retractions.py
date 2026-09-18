import json
from types import SimpleNamespace

import pytest
import requests

from rep0rter import policy, retractions as remote
from rep0rter.config import Config
from rep0rter.store import Event, Post, Store


@pytest.fixture
def group(tmp_path):
    with Store(tmp_path / 'rep0rter.sqlite') as store:
        for i in (1,2,3):
            value=Event(f'slack:C:{i}','slack','message','slack:C',100+i,author_id=f'U{i}',
                        author_name=f'Author{i}',text=f'Source{i}',url=f'https://source.invalid/{i}')
            store.upsert_events([value])
            store.add_post(Post(value.id,200+i,7,f'Headline{i}',f'Summary{i}'))
        remote.confirm_group(store,[1,2,3],'news',99)
        yield store


def cfg(store):
    return Config(data_dir=store.path.parent,telegram_bot_token='fake',telegram_chat_id='news')


def withdraw(store,ids):
    plans=remote.plan_remote(store,cfg(store),ids)
    policy.redact(store,[f'slack:C:{i}' for i in ids])
    remote.queue(store,plans)
    return plans


def response(ok=True,status=200,**fields):
    return SimpleNamespace(status_code=status,json=lambda:{'ok':ok,**fields})


def test_shared_group_removes_only_requested_post(group,monkeypatch):
    plans=withdraw(group,[2])
    assert plans[0]['action']=='editMessageText'
    assert 'Headline1' in plans[0]['payload']['text']
    assert 'Headline2' not in plans[0]['payload']['text']
    assert 'Headline3' in plans[0]['payload']['text']
    calls=[]
    monkeypatch.setattr(remote.requests,'post',lambda url,**kwargs:calls.append((url,kwargs)) or response())
    assert remote.process(group,cfg(group))==1
    assert calls[0][0].endswith('/editMessageText')
    assert calls[0][1]['json']['message_id']==99
    assert group.conn.execute('SELECT status FROM retractions WHERE post_id=2').fetchone()[0]=='complete'
    assert [p.id for p,_,_ in group.recent_posts()] == [3,1]
    assert remote.process(group,cfg(group))==0
    assert len(calls)==1


def test_latest_policy_recomputed_before_sending_queued_edit(group,monkeypatch):
    withdraw(group,[1])
    # Another optout arrives while this edit is pending. The saved text is stale.
    policy.add_rule(group,'user','slack:U2')
    policy.add_rule(group,'user','slack:U3')
    calls=[]
    monkeypatch.setattr(remote.requests,'post',lambda url,**kwargs:calls.append((url,kwargs)) or response())
    assert remote.process(group,cfg(group))==1
    assert calls[0][0].endswith('/deleteMessage')
    assert 'text' not in calls[0][1]['json']


def test_legacy_integer_mapping_requires_manual_confirmation(group):
    group.update_post_delivery(1,{'telegram':99})
    plan=remote.plan_remote(group,cfg(group),[1])[0]
    assert plan['status']=='manual'
    assert 'target' not in plan


def test_shared_photo_requires_manual_review(group):
    remote.confirm_group(group,[1,2,3],'news',99,format='photo')
    assert remote.plan_remote(group,cfg(group),[1])[0]['status']=='manual'


def test_all_group_posts_withdrawn_deletes_once(group,monkeypatch):
    withdraw(group,[1,2,3])
    calls=[]
    monkeypatch.setattr(remote.requests,'post',lambda url,**kwargs:calls.append(url) or response())
    assert remote.process(group,cfg(group))==1
    assert calls[0].endswith('/deleteMessage')
    assert [r[0] for r in group.conn.execute('SELECT status FROM retractions')] == ['complete']*3


def test_429_is_due_retry_and_timeout_becomes_unknown(group,monkeypatch):
    withdraw(group,[1])
    monkeypatch.setattr(remote.requests,'post',lambda *args,**kwargs:response(False,429,error_code=429,parameters={'retry_after':3}))
    assert remote.process(group,cfg(group))==0
    row=group.conn.execute('SELECT * FROM telegram_mutations').fetchone()
    assert row['status']=='failed' and row['next_attempt'] is not None
    group.conn.execute('UPDATE telegram_mutations SET next_attempt=0')
    group.conn.commit()
    def timeout(*args,**kwargs):
        raise requests.Timeout('https://api.telegram.org/botSECRET')
    monkeypatch.setattr(remote.requests,'post',timeout)
    assert remote.process(group,cfg(group))==0
    row=group.conn.execute('SELECT * FROM telegram_mutations').fetchone()
    assert row['status']=='unknown' and 'SECRET' not in row['error']
    assert remote.process(group,cfg(group))==0


def test_expired_lease_and_missing_test_destination_never_send(group,monkeypatch):
    withdraw(group,[1])
    group.conn.execute("UPDATE telegram_mutations SET status='sending',lease_until=0")
    group.conn.commit()
    assert remote.process(group,cfg(group))==0
    assert group.conn.execute('SELECT status FROM telegram_mutations').fetchone()[0]=='unknown'
    group.conn.execute("UPDATE telegram_mutations SET status='prepared'")
    group.conn.commit()
    config=cfg(group)
    config.telegram_use_test_chat=True
    config.telegram_test_chat_id=None
    assert remote.process(group,config)==0
    assert group.conn.execute('SELECT status FROM telegram_mutations').fetchone()[0]=='prepared'


def test_server_error_not_claimed_definitively_failed(group,monkeypatch):
    withdraw(group,[1])
    monkeypatch.setattr(remote.requests,'post',lambda *args,**kwargs:response(False,500,error_code=500))
    assert remote.process(group,cfg(group))==0
    assert group.conn.execute('SELECT status FROM telegram_mutations').fetchone()[0]=='unknown'


def test_unchanged_message_confirms_idempotent_edit(group,monkeypatch):
    withdraw(group,[1])
    monkeypatch.setattr(remote.requests,'post',lambda *args,**kwargs:response(False,400,error_code=400,description='Bad Request: message is not modified'))
    assert remote.process(group,cfg(group))==1


def test_queue_deduplicates_same_plan(group):
    plan=withdraw(group,[1])
    remote.queue(group,plan)
    assert group.conn.execute('SELECT count(*) FROM telegram_mutations').fetchone()[0]==1
