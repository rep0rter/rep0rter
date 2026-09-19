from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import sqlite3
import threading

from rep0rter.config import Config
from rep0rter.delivery import prepare_posts
from rep0rter.reporter import Candidate
from rep0rter.store import Event, Post, Store
from rep0rter import stories

NOW=1789736400.0
TEXT='9/19 公民科技工作坊開放報名，歡迎一起探索開放資料並且參與社群協作 https://example.test/events/workshop'


def event(key='root',text=TEXT,**kwargs):
    return Event(key,'slack','message','slack:C',NOW,author_id='slack:U1',author_name='Person',text=text,url='https://example.test/source/'+key,**kwargs)


def candidates(*events):
    return [Candidate(e,None,6,plain_text=e.text) for e in events]


def setup(tmp_path):
    cfg=Config(data_dir=tmp_path);cfg.telegram_bot_token=None;cfg.telegram_chat_id=None
    return cfg,Store(cfg.db_path)


def publish(cfg,store,candidate):
    post=Post(candidate.event.id,NOW,6,'工作坊','來源公告')
    prepare_posts(cfg,store,[(candidate,post)])
    return post


def test_same_round_original_share_copy_and_tracking_url_are_one_story(tmp_path):
    cfg,store=setup(tmp_path)
    with store:
        root=event()
        share=event('share',TEXT,meta={'references':[{'event_id':'root','container_id':'slack:C','public':True}]})
        copy=event('copy',TEXT+'?utm_source=chat&fbclid=123')
        store.upsert_events([root,share,copy])
        picked=stories.expand_candidates(store,cfg,candidates(root,share,copy),NOW)
        assert len(picked)==1
        publish(cfg,store,picked[0])
        assert store.conn.execute('SELECT COUNT(DISTINCT story_id) FROM story_events').fetchone()[0]==1
        assert stories.expand_candidates(store,cfg,candidates(share,copy),NOW+1)==[]


def test_across_round_copy_and_attributed_share_are_not_revisions(tmp_path):
    cfg,store=setup(tmp_path)
    with store:
        root=event();store.upsert_events([root])
        publish(cfg,store,stories.expand_candidates(store,cfg,candidates(root),NOW)[0])
        copy=event('copy','轉貼：'+TEXT)
        share=event('share','社群轉貼資訊 '+TEXT,meta={'references':[{'event_id':'root','container_id':'slack:C','public':True}]})
        store.upsert_events([copy,share])
        assert stories.expand_candidates(store,cfg,candidates(copy,share),NOW+1)==[]
        assert store.conn.execute('SELECT COUNT(DISTINCT story_id) FROM story_events').fetchone()[0]==1


def test_recurring_dates_and_release_versions_do_not_false_merge(tmp_path):
    cfg,store=setup(tmp_path)
    with store:
        roots=[event('a'),event('b',TEXT.replace('9/19','9/26')),
               event('v1','Release v1.0.0 is published with public datasets and documentation https://example.test/releases/latest'),
               event('v2','Release v2.0.0 is published with public datasets and documentation https://example.test/releases/latest')]
        store.upsert_events(roots)
        assert len(stories.expand_candidates(store,cfg,candidates(*roots),NOW))==4


def test_shared_venue_page_does_not_merge_unrelated_subjects(tmp_path):
    cfg,store=setup(tmp_path)
    with store:
        roots=[event('a','10/1 農業研究工作坊，學習栽種有機蔬菜 https://example.test/venue'),
               event('b','10/1 區塊鏈工作坊，討論加密貨幣治理 https://example.test/venue')]
        store.upsert_events(roots)
        assert len(stories.expand_candidates(store,cfg,candidates(*roots),NOW))==2


def test_material_root_edit_makes_one_revision_and_no_rollback(tmp_path):
    cfg,store=setup(tmp_path)
    with store:
        root=event();store.upsert_events([root])
        original=stories.expand_candidates(store,cfg,candidates(root),NOW)[0]
        post=publish(cfg,store,original)
        edited=replace(root,text=TEXT+'\n更正：活動日期改為9/20')
        store.upsert_events([edited])
        revisions=stories.expand_candidates(store,cfg,[],NOW+1)
        assert len(revisions)==1 and revisions[0].event.kind=='story_update'
        assert revisions[0].event.meta['story_revision']==2
        second=publish(cfg,store,revisions[0])
        assert second.id!=post.id
        assert stories.expand_candidates(store,cfg,[],NOW+2)==[]
        stale=event('stale',TEXT);store.upsert_events([stale])
        assert stories.expand_candidates(store,cfg,candidates(stale),NOW+3)==[]


def test_recovered_empty_share_corrects_existing_story_once_with_canonical_evidence(tmp_path):
    cfg, store = setup(tmp_path)
    with store:
        share = event('share', '')
        store.upsert_events([share])
        old_post = publish(cfg, store, candidates(share)[0])
        stories.bootstrap(store, NOW)
        old_story = stories.info(store, old_post.id)
        original = event('original', '學區描述清單：81 所可由村里範圍組合；78 所涉及鄰界。 https://example.test/schools')
        store.upsert_events([original])
        # Even an original observed/clustered before recovery must join the old report.
        stories.assign(store, original, NOW)
        restored = replace(share, text='Quoted original author: ' + original.text, meta={
            'references': [{'event_id': original.id, 'container_id': original.container_id,
                            'author_id': original.author_id, 'public': True}],
        })
        store.upsert_events([restored])
        updates = stories.expand_candidates(store, cfg, candidates(original), NOW + 1)
        assert len(updates) == 1
        update = updates[0]
        assert update.event.kind == 'story_update'
        assert update.event.meta['story_revision'] == 2
        assert update.reasons == ['recovered_source_context']
        assert update.event.meta['source_context_recovered'] is True
        assert update.event.meta['recovery_previous_post_ids'] == [old_post.id]
        assert {e.id for e in update.evidence_events} == {original.id, share.id}
        assert update.event.text == original.text
        assert store.conn.execute('SELECT COUNT(DISTINCT story_id) FROM story_events').fetchone()[0] == 1
        assert store.conn.execute('SELECT canonical_event_id FROM stories').fetchone()[0] == original.id
        correction = publish(cfg, store, update)
        assert correction.id != old_post.id
        assert stories.info(store, old_post.id)['versions'] == [
            {'id': old_post.id, 'revision': 1}, {'id': correction.id, 'revision': 2},
        ]
        assert old_story['revision'] == 1
        assert stories.expand_candidates(store, cfg, candidates(original, restored), NOW + 2) == []
        assert stories.expand_candidates(store, cfg, [], NOW + 3) == []


def test_recovered_empty_original_without_update_keywords_is_a_single_correction(tmp_path):
    cfg, store = setup(tmp_path)
    with store:
        root = event('empty', '')
        store.upsert_events([root])
        old_post = publish(cfg, store, candidates(root)[0])
        stories.bootstrap(store, NOW)
        root = replace(root, text='社區學區描述清單：村里與鄰界需分別處理。 https://example.test/schools')
        store.upsert_events([root])
        updates = stories.expand_candidates(store, cfg, [], NOW + 1)
        assert len(updates) == 1
        assert updates[0].event.meta['recovery_previous_post_ids'] == [old_post.id]
        publish(cfg, store, updates[0])
        assert stories.expand_candidates(store, cfg, [], NOW + 2) == []


def test_empty_root_recovery_cannot_bypass_optout_or_create_news_from_link_only(tmp_path):
    from rep0rter.policy import add_rule
    cfg, store = setup(tmp_path)
    with store:
        root = event('empty', '')
        store.upsert_events([root])
        publish(cfg, store, candidates(root)[0])
        stories.bootstrap(store, NOW)
        store.upsert_events([replace(root, text='https://example.test/long-path-but-no-content')])
        assert stories.expand_candidates(store, cfg, [], NOW + 1) == []
        store.upsert_events([replace(root, text='學區描述清單：81 所可由村里範圍組合；78 所涉及鄰界。')])
        add_rule(store, 'user', root.author_id)
        assert stories.expand_candidates(store, cfg, [], NOW + 2) == []


def test_recovered_share_does_not_rewrite_two_published_histories(tmp_path):
    cfg, store = setup(tmp_path)
    with store:
        share = event('share', '')
        original = event('original', '學區描述清單：81 所由村里組合；78 所涉及鄰界。 https://example.test/schools')
        store.upsert_events([share, original])
        old_share = publish(cfg, store, candidates(share)[0])
        old_original = publish(cfg, store, candidates(original)[0])
        stories.bootstrap(store, NOW)
        before = {post_id: stories.info(store, post_id)['versions'] for post_id in (old_share.id, old_original.id)}
        store.upsert_events([replace(share, text=original.text, meta={'references': [
            {'event_id': original.id, 'container_id': original.container_id, 'public': True},
        ]})])
        assert stories.expand_candidates(store, cfg, [], NOW + 1) == []
        assert before == {post_id: stories.info(store, post_id)['versions'] for post_id in before}


def test_recovery_waits_for_canonical_original_and_ignores_attribution_boilerplate(tmp_path):
    cfg, store = setup(tmp_path)
    with store:
        share = event('share', '')
        store.upsert_events([share])
        publish(cfg, store, candidates(share)[0])
        stories.bootstrap(store, NOW)
        share = replace(share, text='[Quoted public Slack message by Original author; source: https://example.test/source]\nhttps://example.test/resource', meta={
            'references': [{'event_id': 'original', 'container_id': 'slack:C', 'public': True}],
        })
        store.upsert_events([share])
        assert stories.expand_candidates(store, cfg, [], NOW + 1) == []
        store.upsert_events([event('original', 'https://example.test/resource')])
        assert stories.expand_candidates(store, cfg, [], NOW + 2) == []


def test_collaborative_reply_publishes_once_but_thanks_does_not(tmp_path):
    cfg,store=setup(tmp_path)
    with store:
        root=event();store.upsert_events([root])
        publish(cfg,store,stories.expand_candidates(store,cfg,candidates(root),NOW)[0])
        thanks=replace(event('thanks','謝謝大家的討論'),kind='thread_reply',parent_id='root',ts=NOW+1)
        store.upsert_events([thanks])
        assert stories.expand_candidates(store,cfg,[],NOW+2)==[]
        reply=replace(event('notes','新增協作共筆，歡迎大家共同編輯 https://example.test/notes'),kind='thread_reply',parent_id='root',ts=NOW+3)
        store.upsert_events([reply])
        revisions=stories.expand_candidates(store,cfg,[],NOW+4)
        assert len(revisions)==1
        assert [e.id for e in revisions[0].evidence_events]==['root','notes']
        publish(cfg,store,revisions[0])
        assert stories.expand_candidates(store,cfg,[],NOW+5)==[]
        sources=stories.info(store,1)['sources']
        assert any(s['url'].endswith('/notes') for s in sources)


def test_opted_out_root_references_cannot_revive_story(tmp_path):
    cfg,store=setup(tmp_path)
    with store:
        root=event();store.upsert_events([root])
        publish(cfg,store,stories.expand_candidates(store,cfg,candidates(root),NOW)[0])
        with store.conn:
            store.conn.execute('INSERT INTO event_tombstones VALUES(?,?,?)',('root',NOW,'removed'))
        share=event('share',TEXT,meta={'references':[{'event_id':'root','container_id':'slack:C','public':True}]})
        store.upsert_events([share])
        assert stories.expand_candidates(store,cfg,candidates(share),NOW+1)==[]
        assert stories.info(store,1)['sources']==[]


def test_info_omits_unsafe_schemes_credentials_and_control_characters(tmp_path):
    cfg,store=setup(tmp_path)
    with store:
        root=event();store.upsert_events([root])
        p=publish(cfg,store,stories.expand_candidates(store,cfg,candidates(root),NOW)[0])
        for url in ['javascript:alert(1)','data:text/html,test','https://user:pass@example.test/path','https://example.test/\npath']:
            store.upsert_events([replace(root,url=url)])
            # Upsert intentionally keeps canonical source URLs; inject corrupt historical value.
            with store.conn: store.conn.execute('UPDATE events SET url=? WHERE id=?',(url,root.id))
            assert stories.info(store,p.id)['sources']==[]


def test_tracking_params_normalize_but_content_params_remain_distinct():
    assert stories.canonical_url('https://example.test/item?b=2&utm_source=x&a=1#frag')=='https://example.test/item?a=1&b=2'
    assert stories.canonical_url('https://example.test/item?id=1')!=stories.canonical_url('https://example.test/item?id=2')
    assert stories.canonical_url('https://github.com/org/repo') is None


def test_concurrent_reservation_creates_one_post_and_delivery_job(tmp_path):
    cfg,store=setup(tmp_path)
    with store:
        root=event();store.upsert_events([root])
        candidate=stories.expand_candidates(store,cfg,candidates(root),NOW)[0]
    barrier=threading.Barrier(2)
    def attempt(_):
        with Store(cfg.db_path) as other:
            barrier.wait(timeout=5)
            post=publish(cfg,other,candidate)
            return post.id
    with ThreadPoolExecutor(max_workers=2) as pool:
        result=list(pool.map(attempt,range(2)))
    assert sum(value is not None for value in result)==1
    with Store(cfg.db_path) as store:
        assert store.post_count()==1
        assert store.conn.execute('SELECT COUNT(*) FROM story_posts').fetchone()[0]==1
        assert store.conn.execute('SELECT COUNT(*) FROM delivery_jobs').fetchone()[0]==1


def test_bootstrap_preserves_old_post_ids_and_deliveries(tmp_path):
    cfg,store=setup(tmp_path)
    with store:
        root=event();store.upsert_events([root])
        post_id=store.add_post(Post(root.id,NOW,7,'舊標題','舊摘要',delivery={'telegram':123}))
        stories.bootstrap(store,NOW)
        stories.bootstrap(store,NOW+1)
        row=store.conn.execute('SELECT * FROM posts WHERE id=?',(post_id,)).fetchone()
        assert json.loads(row['delivery'])=={'telegram':123}
        assert stories.info(store,post_id)['revision']==1
        assert store.conn.execute('SELECT COUNT(*) FROM story_posts').fetchone()[0]==1


def test_new_reply_after_prior_revision_produces_only_new_evidence(tmp_path):
    cfg,store=setup(tmp_path)
    with store:
        root=event();store.upsert_events([root])
        publish(cfg,store,stories.expand_candidates(store,cfg,candidates(root),NOW)[0])
        first=replace(event('first','新增共筆 https://example.test/notes/1'),kind='thread_reply',parent_id='root',ts=NOW+1)
        store.upsert_events([first]);publish(cfg,store,stories.expand_candidates(store,cfg,[],NOW+2)[0])
        second=replace(event('second','更正：報名截止延期至9/23'),kind='thread_reply',parent_id='root',ts=NOW+3)
        store.upsert_events([second]);revisions=stories.expand_candidates(store,cfg,[],NOW+4)
        assert len(revisions)==1
        assert revisions[0].event.meta['story_revision']==3
        assert [e.id for e in revisions[0].evidence_events]==['root','second']
        publish(cfg,store,revisions[0])
        assert stories.expand_candidates(store,cfg,[],NOW+5)==[]


def test_automation_reply_and_excluded_reply_never_create_revision(tmp_path):
    cfg,store=setup(tmp_path)
    with store:
        root=event();store.upsert_events([root])
        publish(cfg,store,stories.expand_candidates(store,cfg,candidates(root),NOW)[0])
        replies=[replace(event('bot','新增發布版本 https://example.test/notes/2'),kind='thread_reply',parent_id='root',author_name='GitHub'),
                 replace(event('private','更正：活動延期'),kind='thread_reply',parent_id='root',meta={'visibility':'private'})]
        store.upsert_events(replies)
        assert stories.expand_candidates(store,cfg,[],NOW+1)==[]


def test_candidate_source_reservation_rolls_back_atomically_on_failure(tmp_path,monkeypatch):
    cfg,store=setup(tmp_path)
    with store:
        root=event();store.upsert_events([root])
        publish(cfg,store,stories.expand_candidates(store,cfg,candidates(root),NOW)[0])
        edit=replace(root,text=TEXT+' 更正：活動取消');store.upsert_events([edit])
        revision=stories.expand_candidates(store,cfg,[],NOW+1)[0]
        def fail(*args): raise sqlite3.IntegrityError('forced reservation conflict')
        monkeypatch.setattr(stories,'reserve',fail)
        import pytest
        with pytest.raises(sqlite3.IntegrityError):
            publish(cfg,store,revision)
        assert store.get_event(revision.event.id) is None
        assert store.post_count()==1
        assert store.conn.execute('SELECT COUNT(*) FROM delivery_jobs').fetchone()[0]==1


def test_canonical_object_id_keeps_real_source_as_story_anchor(tmp_path):
    cfg,store=setup(tmp_path)
    with store:
        root=event(meta={'canonical_object_id':'upstream-object-42'})
        store.upsert_events([root])
        candidate=stories.expand_candidates(store,cfg,candidates(root),NOW)[0]
        anchor=store.conn.execute('SELECT canonical_event_id FROM stories').fetchone()[0]
        assert anchor==root.id
        publish(cfg,store,candidate)
        root=replace(root,text=TEXT+' 新增參與者共筆 https://example.test/notes/1')
        store.upsert_events([root])
        assert len(stories.expand_candidates(store,cfg,[],NOW+1))==1


def test_thanks_for_registration_with_link_is_not_material_revision(tmp_path):
    cfg,store=setup(tmp_path)
    with store:
        root=event();store.upsert_events([root])
        publish(cfg,store,stories.expand_candidates(store,cfg,candidates(root),NOW)[0])
        thanks=replace(event('thanks','謝謝大家報名工作坊 https://example.test/thanks'),kind='thread_reply',parent_id='root',ts=NOW+1)
        store.upsert_events([thanks])
        assert stories.expand_candidates(store,cfg,[],NOW+2)==[]


def test_concurrent_crossposts_reserve_one_story_revision(tmp_path):
    cfg,store=setup(tmp_path)
    with store:
        root,copy=event(),event('copy','轉貼：'+TEXT)
        store.upsert_events([root,copy])
        first=stories.expand_candidates(store,cfg,candidates(root),NOW)[0]
        # Simulate a second writer that read the unpublished story before reservation.
        second=stories.expand_candidates(store,cfg,candidates(copy),NOW)[0]
        assert first.event.meta['story_id']==second.event.meta['story_id']
    barrier=threading.Barrier(2)
    def attempt(candidate):
        with Store(cfg.db_path) as store:
            barrier.wait(timeout=5)
            return publish(cfg,store,candidate).id
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids=list(pool.map(attempt,[first,second]))
    assert sum(value is not None for value in ids)==1
    with Store(cfg.db_path) as store:
        assert store.post_count()==1
        assert store.conn.execute('SELECT COUNT(*) FROM story_posts').fetchone()[0]==1


def test_closed_participation_and_new_deadline_are_material_without_links(tmp_path):
    cfg,store=setup(tmp_path)
    with store:
        root=event();store.upsert_events([root])
        publish(cfg,store,stories.expand_candidates(store,cfg,candidates(root),NOW)[0])
        closed=replace(event('closed','更正：這場是閉門座談，沒有開放報名'),kind='thread_reply',parent_id='root',ts=NOW+1)
        deadline=replace(event('deadline','新增報名截止日期為9/23'),kind='thread_reply',parent_id='root',ts=NOW+2)
        store.upsert_events([closed,deadline])
        revisions=stories.expand_candidates(store,cfg,[],NOW+3)
        assert len(revisions)==1
        assert {e.id for e in revisions[0].evidence_events}=={'root','closed','deadline'}


def test_draft_batch_limit_applies_after_dedup_not_to_crosspost_inputs(tmp_path):
    from rep0rter.reporter import draft_posts
    cfg,store=setup(tmp_path);cfg.editorial_mode='active';cfg.max_items_per_run=2
    import time
    now=time.time()
    with store:
        duplicates=[replace(event('copy'+str(i)),ts=now-i) for i in range(4)]
        other=replace(event('other','9/25 開放資料講座介紹社區環境觀測與公民科學，歡迎大家來報名 https://example.test/other'),ts=now-10)
        store.upsert_events([*duplicates,other])
        drafts=draft_posts(store,cfg,use_llm=False)
        assert len(drafts)==2
        assert {c.event.meta['story_id'] for c,p in drafts}.__len__()==2
        rows=store.conn.execute('SELECT event_id,selected FROM editorial_decisions').fetchall()
        assert sum(row['selected'] for row in rows)==2
        assert next(row['selected'] for row in rows if row['event_id']=='other')==1
