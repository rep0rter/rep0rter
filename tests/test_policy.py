import json

from rep0rter import policy
from rep0rter.store import Container, Event, Post, Store


def event(event_id='slack:C:1', author='U1', ts=100, parent=None, meta=None):
    return Event(event_id, 'slack', 'thread_reply' if parent else 'message', 'slack:C', ts,
                 author_id=author, author_name='Renamable name', text='Private original text',
                 parent_id=parent, meta=meta or {})


def test_stable_user_scope_cannot_be_bypassed_by_rename_or_override(tmp_path):
    with Store(tmp_path / 'db.sqlite') as store:
        policy.add_rule(store, 'user', 'slack:U1', now=150)
        value = event(meta={'editorial_approved': True})
        value.author_name = 'Completely different name'
        assert not policy.event_allowed(store, value)
        assert store.upsert_events([value]) == 0
        assert not store.user_names('slack')
        policy.remove_rule(store, 'user', 'slack:U1', now=200)
        assert not policy.event_allowed(store, value)
        assert policy.event_allowed(store, event('slack:C:2', ts=201))


def test_channel_and_event_rules_use_stable_ids(tmp_path):
    with Store(tmp_path / 'db.sqlite') as store:
        store.upsert_container(Container('slack:C', 'slack', 'first'))
        policy.add_rule(store, 'container', 'slack:C')
        store.upsert_container(Container('slack:C', 'slack', 'renamed'))
        assert not policy.container_allowed(store, 'slack:C')
        assert not policy.event_allowed(store, event())
        policy.remove_rule(store, 'container', 'slack:C', now=50)
        assert policy.container_allowed(store, 'slack:C')
        policy.add_rule(store, 'event', 'slack:C:1')
        assert not policy.event_allowed(store, event())
        assert policy.event_allowed(store, event('slack:C:2'))


def test_root_reply_cross_channel_forward_and_unknown_reference_blocked(tmp_path):
    with Store(tmp_path / 'db.sqlite') as store:
        root = event()
        reply = event('slack:C:2', author='U2', parent=root.id)
        forward = event('slack:OTHER:3', author='U3', meta={'references': [{'event_id':root.id,
            'container_id':'slack:C','author_id':'U1','public':True,'ts':100}]})
        forward.container_id = 'slack:OTHER'
        store.upsert_events([root, reply, forward])
        policy.add_rule(store, 'user', 'slack:U1')
        assert not policy.event_allowed(store, reply)
        assert not policy.event_allowed(store, forward)
        assert set(policy.affected(store)) == {root.id, reply.id, forward.id}
        unverified = event('slack:C:4', author='U4', meta={'references':[{'event_id':'slack:X:4','container_id':'slack:X','public':True}]})
        assert not policy.event_allowed(store, unverified)
        unverified.meta['references'][0]['author_id'] = 'U9'
        unverified.meta['references'][0].pop('public')
        assert not policy.event_allowed(store, unverified)


def test_tombstone_recollection_and_old_database_restore_cannot_revive(tmp_path):
    path = tmp_path / 'db.sqlite'
    original = event()
    with Store(path) as store:
        store.upsert_events([original])
        post_id = store.add_post(Post(original.id, 110, 7, 'Private headline', 'Private summary', translations={'en':{'headline':'Secret','summary':'Secret'}}))
        policy.redact(store, [original.id])
        assert store.get_event(original.id).text == ''
        assert store.upsert_events([original]) == 0
        assert store.recent_posts() == []
        assert store.conn.execute('SELECT status FROM retractions WHERE post_id=?',(post_id,)).fetchone()[0] == 'pending'
        # Simulate an old DB restored beside the still-current external ledger.
        store.conn.execute('DELETE FROM event_tombstones')
        store.conn.execute("UPDATE events SET text='Old backup private text' WHERE id=?",(original.id,))
        store.conn.commit()
    with Store(path) as store:
        assert store.get_event(original.id).text == ''
        assert store.upsert_events([original]) == 0


def test_redaction_removes_evidence_audits_dependent_stories_and_media(tmp_path):
    with Store(tmp_path / 'db.sqlite') as store:
        original = event()
        story = event('slack:C:story', author='U2')
        store.upsert_events([original, story])
        store.conn.execute('CREATE TABLE writer_audits (event_id TEXT, evidence_snapshot TEXT, evidence_ids TEXT)')
        store.conn.execute('INSERT INTO writer_audits VALUES (?,?,?)',(story.id,
            json.dumps({'evidence':[{'id':original.id,'text':'Private original text'}]}), json.dumps([story.id])))
        store.conn.commit()
        for directory in ('image-cache','site/cards','site-releases/old/cards'):
            path = tmp_path / directory
            path.mkdir(parents=True)
            (path/'private.png').write_bytes(b'private-original-image')
        assert set(policy.redact(store, [original.id])) == {original.id, story.id}
        assert store.conn.execute('SELECT count(*) FROM writer_audits').fetchone()[0] == 0
        assert store.get_event(story.id).text == ''
        assert not (tmp_path/'image-cache').exists()
        assert not (tmp_path/'site/cards').exists()
        assert not (tmp_path/'site-releases/old/cards').exists()


def test_no_exclusions_does_not_sweep_malformed_audits(tmp_path):
    with Store(tmp_path / 'db.sqlite') as store:
        store.upsert_events([event()])
        store.conn.execute('CREATE TABLE writer_audits (event_id TEXT,evidence_snapshot TEXT)')
        store.conn.execute("INSERT INTO writer_audits VALUES ('slack:C:1', 'malformed-json')")
        store.conn.commit()
        assert policy.affected(store) == []


def test_second_optout_updates_request_time_and_policy_version(tmp_path):
    with Store(tmp_path / 'db.sqlite') as store:
        policy.add_rule(store,'user','slack:U1',now=100)
        policy.remove_rule(store,'user','slack:U1',now=200)
        policy.add_rule(store,'user','slack:U1',now=300)
        row = store.conn.execute('SELECT * FROM policy_rules').fetchone()
        assert row['requested_at'] == 300 and row['effective_at'] == 300
        assert row['removed_at'] is None and row['version'] == 3


def test_new_policy_is_not_overwritten_by_stale_store_save(tmp_path):
    with Store(tmp_path / 'db.sqlite') as store:
        policy.add_rule(store,'user','slack:U1',now=100)
        path=policy.policy_path(store)
        current=json.loads(path.read_text())
        current['rules'][0]['version']=50
        current['rules'][0]['removed_at']=200
        path.write_text(json.dumps(current))
        policy.save(store)
        assert json.loads(path.read_text())['rules'][0]['version'] == 50
        policy.initialize(store)
        assert policy.event_allowed(store,event(ts=201))


def test_sent_outbox_payload_is_scrubbed_without_losing_delivery_mapping(tmp_path):
    from rep0rter.delivery import DeliveryOutbox
    with Store(tmp_path / 'db.sqlite') as store:
        value=event()
        store.upsert_events([value])
        post=store.add_post(Post(value.id,200,7,'Private','Private',delivery={'telegram':{'chat_id':'news','message_id':99,'outbox_id':1}}))
        DeliveryOutbox(store)
        store.conn.execute("INSERT INTO delivery_jobs(post_id,status,payload,content_hash,created_at,updated_at) VALUES (?,'sent',?, 'secret-hash',100,100)",(post,json.dumps({'caption':'Private text','photo':'/private.png'})))
        store.conn.commit()
        policy.redact(store,[value.id])
        job=store.conn.execute('SELECT * FROM delivery_jobs').fetchone()
        assert job['status']=='sent' and json.loads(job['payload'])=={} and job['content_hash'] is None
        assert json.loads(store.conn.execute('SELECT delivery FROM posts').fetchone()[0])['telegram']['message_id']==99


def test_stale_store_new_optout_advances_latest_external_version(tmp_path):
    with Store(tmp_path / 'db.sqlite') as store:
        policy.add_rule(store,'user','slack:U1',now=100)
        path=policy.policy_path(store)
        current=json.loads(path.read_text())
        current['rules'][0].update(version=50,removed_at=200)
        path.write_text(json.dumps(current))
        policy.add_rule(store,'user','slack:U1',now=300)
        row=json.loads(path.read_text())['rules'][0]
        assert row['version']==51 and row['removed_at'] is None
        assert not policy.event_allowed(store,event(ts=301))
