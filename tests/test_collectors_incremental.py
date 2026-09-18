import json
from decimal import Decimal
from unittest.mock import Mock
import pytest
from rep0rter.store import Store, Event
from rep0rter.collectors import slack_incremental as inc
from rep0rter.collectors.state import Metrics, persist, read_state, BudgetSession, BudgetExceeded


def pages(monkeypatch, values):
    get = Mock(side_effect=[Mock(json=Mock(return_value={'messages': rows})) for rows in values])
    monkeypatch.setattr(inc.api, '_get', get)
    return get


def test_large_backlog_short_pages_and_after_before_exclusive(monkeypatch):
    get = pages(monkeypatch, [[{'ts': str(n)} for n in range(300,200,-1)], [{'ts': str(n)} for n in range(200,100,-1)], [{'ts':'100'},{'ts':'80'}]])
    rows, complete, error, resume = inc.scan(Mock(), 'C', Decimal('90'))
    assert complete and not error and len(rows)==201
    assert get.call_args_list[0].kwargs['after']=='90'
    assert [x.kwargs.get('before') for x in get.call_args_list]==[None,'201','101']
    assert all(not ('before' in x.kwargs and 'after' in x.kwargs) for x in get.call_args_list)


def test_empty_subtype_and_same_ts_do_not_advance(monkeypatch):
    pages(monkeypatch, [[{'ts':'100'}],[]])
    rows, complete, error, resume=inc.scan(Mock(),'C',Decimal('90'))
    assert not complete and len(rows)==1 and 'ambiguous' in error and resume is None
    pages(monkeypatch, [[{'ts':'100'}],[{'ts':'100'}]])
    assert 'did not advance' in inc.scan(Mock(),'C',Decimal('90'))[2]


def test_bounded_backlog_can_resume_without_moving_watermark(monkeypatch):
    pages(monkeypatch, [[{'ts':'300'},{'ts':'200'}]])
    rows,complete,error,resume=inc.scan(Mock(),'C',Decimal('90'),max_pages=1)
    assert not complete and resume=='200'
    get=pages(monkeypatch, [[{'ts':'150'},{'ts':'80'}]])
    assert inc.scan(Mock(),'C',Decimal('90'),start_before=resume)[1]
    assert get.call_args.kwargs['before']=='200' and 'after' not in get.call_args.kwargs


def test_overlap_catches_late_messages_and_duplicate_merge(monkeypatch):
    pages(monkeypatch, [[{'ts':'110','text':'old'},{'ts':'110','text':'new','edited':{'ts':'120'}},{'ts':'95'}],[{'ts':'80'}]])
    rows,complete,_,_=inc.scan(Mock(),'C',Decimal('90'))
    assert complete and len(rows)==2 and next(r for r in rows if r['ts']=='110')['text']=='new'


def test_event_cursor_atomic_and_latest_counter_can_decrease(tmp_path, monkeypatch):
    with Store(tmp_path/'db') as store:
        event=Event('slack:C:100','slack','message','slack:C',100,text='text',reaction_count=5,meta={'observed_at':100})
        metrics=Metrics()
        persist(store,'slack:C',{'last_complete_ts':'100'},[event],metrics,100)
        event.reaction_count=2;event.meta['observed_at']=101
        persist(store,'slack:C',{'last_complete_ts':'101'},[event],metrics,101)
        assert store.get_event(event.id).reaction_count==2
        assert store.get_event(event.id).meta['engagement_high_water']['reactions']==5
        from rep0rter.collectors import state
        monkeypatch.setattr(state,'write_state',Mock(side_effect=RuntimeError('crash')))
        event.reaction_count=0;event.meta['observed_at']=102
        with pytest.raises(RuntimeError):
            persist(store,'slack:C',{'last_complete_ts':'102'},[event],metrics,102)
        assert store.get_event(event.id).reaction_count==2
        assert read_state(store,'slack:C')['last_complete_ts']=='101'


def test_budget_counts_failed_requests_and_limits(monkeypatch):
    session=Mock(headers={});session.get.side_effect=RuntimeError('failed')
    metrics=Metrics();wrapped=BudgetSession(session,metrics,limit=1,interval=0)
    with pytest.raises(RuntimeError): wrapped.get('https://example.test')
    with pytest.raises(BudgetExceeded): wrapped.get('https://example.test')
    assert metrics.requests==1


def test_root_lookup_does_not_substitute_adjacent_message(monkeypatch):
    pages(monkeypatch, [[{'ts':'99','text':'other'}]])
    assert inc.refresh_root(Mock(),'C','100',{'C'}) is None
    pages(monkeypatch, [[{'ts':'100','text':'root'}]])
    assert inc.refresh_root(Mock(),'C','100',{'C'}).id=='slack:C:100'


def test_collect_recovers_old_root_and_skips_unchanged_channel(tmp_path,monkeypatch):
    from datetime import datetime, timezone
    now=1000000
    monkeypatch.setattr(inc.time,'time',lambda:now)
    channel=inc.api.ChannelRow('C','public','','',10,123,datetime.fromtimestamp(now,timezone.utc))
    monkeypatch.setattr(inc.api,'fetch_channels',lambda session:[channel])
    get=pages(monkeypatch, [[{'ts':'900000','thread_ts':'500000','text':'Important update','user':{'id':'U'}}],[{'ts':'800000'}],[{'ts':'500000','text':'Original root','user':{'id':'U'}}]])
    with Store(tmp_path/'db') as store:
        assert inc.collect(store,session=Mock(headers={}))==1
        assert store.get_event('slack:C:500000').text=='Original root'
        state=read_state(store,'slack:C')
        assert state['last_complete_ts']=='900000' and state['gap'] is False
        assert get.call_args_list[-1].kwargs['before']=='500000.000001'
        count=get.call_count
        assert inc.collect(store,session=Mock(headers={}))==0
        assert get.call_count==count


def test_zero_response_does_not_advance_cursor(tmp_path,monkeypatch):
    from datetime import datetime, timezone
    from rep0rter.collectors.state import write_state
    monkeypatch.setattr(inc.time,'time',lambda:1000000)
    channel=inc.api.ChannelRow('C','public','','',10,124,datetime.fromtimestamp(1000000,timezone.utc))
    monkeypatch.setattr(inc.api,'fetch_channels',lambda session:[channel])
    monkeypatch.setattr(inc.api,'_get',lambda *a,**kw:Mock(json=lambda:0))
    with Store(tmp_path/'db') as store:
        with store.conn: write_state(store,'slack:C',{'last_complete_ts':'900000','total_messages':123})
        inc.collect(store,session=Mock(headers={}))
        assert read_state(store,'slack:C')['last_complete_ts']=='900000'
        assert read_state(store,'slack:C')['gap']
        assert not read_state(store,'slack:health')['healthy']


def test_slack_github_integration_never_persists_events_or_user(tmp_path,monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(inc.time,'time',lambda:1000000)
    channel=inc.api.ChannelRow('C','public','','',10,100,datetime.fromtimestamp(1000000,timezone.utc))
    monkeypatch.setattr(inc.api,'fetch_channels',lambda session:[channel])
    pages(monkeypatch, [[{'ts':'900000','text':'Issue opened','user':{'id':'BOT','real_name':'GitHub','is_bot':True},'app_id':'APP'}],[{'ts':'800000'}]])
    with Store(tmp_path/'db') as store:
        assert inc.collect(store,session=Mock(headers={}))==0
        assert store.event_count()==0
        assert store.conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]==0
        assert read_state(store,'slack:C')['last_complete_ts']=='900000'


def test_slack_deletion_sentinel_scrubs_existing_content_and_avatar(tmp_path):
    with Store(tmp_path/'db') as store:
        original=Event('slack:C:100','slack','message','slack:C',100,author_id='slack:U',author_name='Original author',text='Original content',html='<p>Original</p>',meta={'avatar_url':'https://example.test/avatar','original_text':'Original'})
        persist(store,'slack:C',{},[original],Metrics(),100)
        deleted,_=inc.api.to_event({'ts':'100','subtype':'message_deleted','text':'deleted original','user':{'id':'U','real_name':'Original author'}},'C')
        persist(store,'slack:C',{},[deleted],Metrics(),101)
        saved=store.get_event(original.id)
        assert saved.text==saved.html==saved.author_name==''
        assert saved.author_id=='slack:U'
        assert saved.meta['deleted_at']==101 and saved.meta['visibility']=='deleted'
        assert 'avatar_url' not in saved.meta and 'original_text' not in saved.meta


def test_partial_homepage_drop_fails_before_any_cursor_or_directory_write(tmp_path,monkeypatch):
    from rep0rter.collectors.state import write_state
    channel=inc.api.ChannelRow('C','public','','',10,100,None)
    monkeypatch.setattr(inc.api,'fetch_channels',lambda session:[channel])
    with Store(tmp_path/'db') as store:
        with store.conn:
            write_state(store,'slack:health',{'containers':100,'last_good_directory_count':100})
            write_state(store,'slack:C',{'last_complete_ts':'100'})
        with pytest.raises(RuntimeError,match='suddenly shrank'):
            inc.collect(store,session=Mock(headers={}))
        assert read_state(store,'slack:C')['last_complete_ts']=='100'
        assert read_state(store,'slack:health')['last_good_directory_count']==100
        assert not store.event_count()
