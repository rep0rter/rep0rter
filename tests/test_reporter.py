import time

from rep0rter.config import Config
from rep0rter.reporter import score_event, select_candidates
from rep0rter.store import Container, Event, Store


def _cfg(tmp_path) -> Config:
    cfg = Config(data_dir=tmp_path)
    cfg.telegram_bot_token = None
    cfg.editorial_mode = "active"
    return cfg


def test_quiet_small_channel_message_is_not_newsworthy(tmp_path):
    cfg = _cfg(tmp_path)
    now = time.time()
    e = Event(id="slack:C1:1", source="slack", kind="message", container_id="slack:C1", ts=now - 3600, text="hi")
    score, reasons = score_event(e, Container(id="slack:C1", source="slack", name="x", num_members=20), cfg, now)
    assert score < cfg.score_threshold
    assert reasons == ["readable_text_below_12"]


def test_announcement_with_keyword_and_link_is_newsworthy(tmp_path):
    cfg = _cfg(tmp_path)
    now = time.time()
    text = "大家好，這週六的黑客松開放報名了！" + "詳細資訊請看 <https://g0v.tw/hackath0n|活動頁>。" * 6
    e = Event(id="slack:C1:2", source="slack", kind="message", container_id="slack:C1", ts=now - 600, text=text)
    score, reasons = score_event(e, Container(id="slack:C1", source="slack", name="general", num_members=15000), cfg, now)
    assert score >= cfg.score_threshold
    assert any("關鍵字" in r for r in reasons)


def test_engagement_cannot_make_low_information_item_newsworthy(tmp_path):
    cfg = _cfg(tmp_path)
    now = time.time()
    e = Event(id="slack:C1:3", source="slack", kind="message", container_id="slack:C1", ts=now - 7200, text="想問一下這個怎麼做")
    c = Container(id="slack:C1", source="slack", name="x", num_members=50)
    assert score_event(e, c, cfg, now)[0] < cfg.score_threshold
    e.reply_count = 4
    assert score_event(e, c, cfg, now)[0] < cfg.score_threshold


def test_select_candidates_skips_posted_and_old(tmp_path):
    cfg = _cfg(tmp_path)
    now = time.time()
    with Store(cfg.db_path) as store:
        store.upsert_container(Container(id="slack:C1", source="slack", name="x", num_members=100))
        fresh = Event(id="slack:C1:10", source="slack", kind="message", container_id="slack:C1", ts=now - 600,
                      text="黑客松報名開放中，歡迎一起來參與 https://example.test", reply_count=5)
        old = Event(id="slack:C1:11", source="slack", kind="message", container_id="slack:C1",
                    ts=now - 10 * 24 * 3600, text="黑客松報名開放中，歡迎一起來參與 https://example.test", reply_count=50)
        reply = Event(id="slack:C1:12", source="slack", kind="thread_reply", container_id="slack:C1",
                      ts=now - 300, text="黑客松報名開放中，歡迎一起來參與 https://example.test", reply_count=50, parent_id="slack:C1:10")
        store.upsert_events([fresh, old, reply])
        picked = select_candidates(store, cfg, now)
        assert [c.event.id for c in picked] == ["slack:C1:10"]
        from rep0rter.store import Post
        store.add_post(Post(event_id="slack:C1:10", published_at=now, score=9, headline="h", summary="s"))
        assert select_candidates(store, cfg, now) == []


def test_upsert_keeps_max_engagement(tmp_path):
    cfg = _cfg(tmp_path)
    with Store(cfg.db_path) as store:
        e = Event(id="slack:C1:20", source="slack", kind="message", container_id="slack:C1", ts=1.0, reply_count=3)
        store.upsert_events([e])
        e.reply_count = 1
        store.upsert_events([e])
        assert store.get_event("slack:C1:20").reply_count == 3


def test_unverified_or_missing_source_text_cannot_be_reported_from_replies(tmp_path):
    cfg = _cfg(tmp_path)
    now = time.time()
    with Store(cfg.db_path) as store:
        store.upsert_events([
            Event(id='empty', source='slack', kind='message', container_id='slack:C', ts=now, text='', reply_count=99),
            Event(id='withheld', source='slack', kind='message', container_id='slack:C', ts=now,
                  text='cannot verify original', reply_count=99, meta={'content_status':'unverified_reference'}),
        ])
        assert select_candidates(store, cfg, now) == []


def test_shadow_observes_without_enabling_proposed_low_information_gate(tmp_path):
    cfg=_cfg(tmp_path);cfg.editorial_mode='shadow'
    now=time.time()
    with Store(cfg.db_path) as store:
        store.upsert_events([Event('low','slack','message','slack:C',now,text='一般聊天，但大家很熱烈回應',reply_count=10)])
        assert [c.event.id for c in select_candidates(store,cfg,now)]==['low']
        row=store.conn.execute('SELECT * FROM editorial_decisions').fetchone()
        import json
        assert row['mode']=='shadow' and row['selected']==1
        assert not json.loads(row['decision'])['eligible']
        cfg.editorial_mode='active'
        assert select_candidates(store,cfg,now)==[]


def test_shadow_still_excludes_internal_project_channel(tmp_path):
    cfg=_cfg(tmp_path);cfg.editorial_mode='shadow'
    now=time.time()
    with Store(cfg.db_path) as store:
        store.upsert_container(Container('slack:C','slack','rep0rter'))
        store.upsert_events([Event('internal','slack','message','slack:C',now,text='這個專案開源發布，歡迎幫忙 https://example.test',reply_count=99)])
        assert select_candidates(store,cfg,now)==[]


def test_stored_github_bots_are_hard_excluded_even_in_shadow(tmp_path):
    cfg=_cfg(tmp_path);cfg.editorial_mode='shadow'
    now=time.time()
    with Store(cfg.db_path) as store:
        store.upsert_events([Event('bot','github','release','github:C',now,author_name='dependabot[bot]',
            text='Release published with new public dataset https://example.test',reply_count=99,
            meta={'visibility':'public','eligible':True})])
        assert select_candidates(store,cfg,now)==[]
        assert store.event_count()==0  # Automation is rejected before persistence or LLM/audit evidence.


def test_stored_slack_github_integration_is_not_news(tmp_path):
    cfg=_cfg(tmp_path);cfg.editorial_mode='shadow'
    now=time.time()
    with Store(cfg.db_path) as store:
        store.upsert_events([Event('integration','slack','message','slack:C',now,author_name='GitHub',
            text='Release a new pull request fixing the CI dependency https://example.test',reply_count=99)])
        assert select_candidates(store,cfg,now)==[]


def test_human_korean_github_pr_has_no_slack_legacy_requirement(tmp_path):
    cfg=_cfg(tmp_path);cfg.editorial_mode='shadow'
    now=time.time()
    with Store(cfg.db_path) as store:
        pr=Event('github:repo:pr:1','github','pull_request','github:repo',now,author_name='human-contributor',
            text='시민들이 지역 환경 데이터를 쉽게 확인하도록 접근성과 검색 기능을 개선했습니다',
            url='https://github.com/community/project/pull/1',meta={'visibility':'public','eligible':True,'content_format':'plain'})
        store.upsert_events([pr])
        picked=select_candidates(store,cfg,now)
        assert [c.event.id for c in picked]==[pr.id]
        assert picked[0].score==6
