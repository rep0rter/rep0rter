import json
from decimal import Decimal
from pathlib import Path
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
        assert get.call_args_list[-1].kwargs['before']=='500001'
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


def test_initial_empty_after_verified_by_unbounded_older_head(monkeypatch):
    get=pages(monkeypatch,[[],[{'ts':'80','text':'older'}]])
    rows,complete,error,resume=inc.scan(Mock(),'C',Decimal('90'))
    assert rows==[] and complete and not error
    assert 'after' in get.call_args_list[0].kwargs
    assert 'after' not in get.call_args_list[1].kwargs and 'before' not in get.call_args_list[1].kwargs


def test_persisted_gap_has_reason_and_available_history_split(tmp_path,monkeypatch):
    channel=inc.api.ChannelRow('C','public','','',10,100,None)
    monkeypatch.setattr(inc.api,'fetch_channels',lambda session:[channel])
    monkeypatch.setattr(inc.api, 'channel_is_older_than', lambda *args: False)
    pages(monkeypatch,[[],[]])
    with Store(tmp_path/'db') as store:
        inc.collect(store,session=Mock(headers={}))
        health=read_state(store,'slack:health')
        assert health['available'] and not health['history_complete'] and not health['healthy']
        assert 'ambiguous empty page' in health['reasons']['slack:C']
        assert 'ambiguous empty page' in json.loads(store.get_kv('collector_errors'))['slack:C']['error']


def quiet_html():
    return (Path(__file__).parent / 'fixtures/slack_archive/quiet_channel.html').read_text()


def test_filtered_empty_head_verified_by_unfiltered_old_month(monkeypatch):
    get = Mock(side_effect=[Mock(json=lambda: {'messages': []}),
                           Mock(json=lambda: {'messages': []}), Mock(text=quiet_html())])
    monkeypatch.setattr(inc.api, '_get', get)
    lower = Decimal('1789603200')
    assert inc.scan(Mock(), 'CQUIET', lower) == ([], True, '', None)
    assert get.call_count == 3
    assert get.call_args.args[1].endswith('/index/channel/CQUIET')


@pytest.mark.parametrize('html', [
    '<html>Login required</html>',
    quiet_html().replace('CQUIET', 'OTHER'),
    quiet_html().replace('2025-11', '2026-09'),
    quiet_html().replace('class="message"', 'class="unknown"'),
    quiet_html().replace('&quot;ts&quot;', '&quot;missing_timestamp&quot;'),
    quiet_html().replace('class="nav-link active"', 'class="nav-link"'),
    quiet_html().replace('id="ts-1763598768.516209"', 'id="ts-1763598768.516210"'),
    quiet_html().replace('class="dropdown-item" href="/index/channel/CQUIET/2025-10"',
                         'class="dropdown-item" href="/index/channel/CQUIET/2026-08"'),
])
def test_missing_or_inconsistent_html_keeps_empty_head_gap(monkeypatch, html):
    monkeypatch.setattr(inc.api, '_get', Mock(side_effect=[
        Mock(json=lambda: {'messages': []}), Mock(json=lambda: {'messages': []}), Mock(text=html)]))
    _, complete, error, _ = inc.scan(Mock(), 'CQUIET', Decimal('1789603200'))
    assert not complete and 'ambiguous empty page' in error


def test_recent_month_cannot_prove_quiet_even_if_visible_message_is_old(monkeypatch):
    monkeypatch.setattr(inc.api, '_get', Mock(return_value=Mock(text=quiet_html())))
    # A lower bound within the displayed month requires actual pagination,
    # not an assumption based on the visible sample or homepage date.
    assert not inc.api.channel_is_older_than(Mock(), 'CQUIET', Decimal('1763685168'))


def test_quiet_html_proof_clears_persisted_gap_without_ingesting_join(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from rep0rter.collectors.state import write_state
    now = 1789776000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    channel = inc.api.ChannelRow('CQUIET', 'quiet', '', '', 10, 362,
                                datetime.fromtimestamp(1763598768, timezone.utc))
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda session: [channel])
    session, transport = _budgeted_pages(monkeypatch, [], 3)
    session.get.side_effect = [Mock(content=b'{}', json=lambda: {'messages': []}),
                              Mock(content=b'{}', json=lambda: {'messages': []}),
                              Mock(content=quiet_html().encode(), text=quiet_html())]
    with Store(tmp_path / 'db') as store:
        with store.conn:
            write_state(store, 'slack:CQUIET', {'last_attempt': now - 7200,
                'gap': True, 'error': 'ambiguous empty page: upstream exposes no raw cursor',
                'last_posted': 1763598768, 'total_messages': 362})
        assert inc.collect(store, session=transport) == 0
        state = read_state(store, 'slack:CQUIET')
        assert state['gap'] is False and state['error'] == ''
        assert state['last_success'] == now and state['last_reconciliation'] == now
        assert Decimal(state['last_complete_ts']) == Decimal(now - 2 * 86400)
        assert read_state(store, 'slack:health')['healthy']
        assert not store.event_count()
        assert transport.metrics.requests == 3
        assert inc.collect(store, session=transport) == 0
        assert session.get.call_count == 3  # No new hourly probe of the quiet channel.


def test_quiet_html_verification_respects_shared_budget(monkeypatch):
    session, transport = _budgeted_pages(monkeypatch, [[], []], 2)
    _, complete, error, _ = inc.scan(transport, 'CQUIET', Decimal('1789603200'))
    assert not complete and 'request budget exhausted' in error
    assert session.get.call_count == 2


def test_slack_tombstone_root_is_not_refreshed(tmp_path,monkeypatch):
    from datetime import datetime,timezone
    now=1000000
    monkeypatch.setattr(inc.time,'time',lambda:now)
    channel=inc.api.ChannelRow('C','public','','',10,100,datetime.fromtimestamp(now,timezone.utc))
    monkeypatch.setattr(inc.api,'fetch_channels',lambda session:[channel])
    get=pages(monkeypatch,[[{'ts':'800000'}]])
    with Store(tmp_path/'db') as store:
        with store.conn:
            store.conn.execute('INSERT INTO event_tombstones VALUES(?,?,?)',('slack:C:900000',900000,'excluded'))
        # A recent root is retained as a blank record after redaction.
        store.conn.execute("INSERT INTO events(id,source,kind,container_id,ts,first_seen,last_seen,meta) VALUES(?,?,?,?,?,?,?,?)",('slack:C:900000','slack','message','slack:C',900000,900000,900000,'{}'));store.conn.commit()
        inc.collect(store,session=Mock(headers={}))
        assert get.call_count==1


def _scheduled_channel(channel_id, now):
    from datetime import datetime, timezone
    return inc.api.ChannelRow(channel_id, channel_id, '', '', 10, 10,
                              datetime.fromtimestamp(now, timezone.utc))


def _budgeted_pages(monkeypatch, values, limit):
    session = Mock(headers={})
    session.get.side_effect = [Mock(content=b'{}', json=Mock(return_value={'messages': rows})) for rows in values]
    wrapped = BudgetSession(session, Metrics(), limit=limit, interval=0)
    monkeypatch.setattr(inc.api, '_get', lambda transport, url, **params: transport.get(url, params=params))
    return session, wrapped


def test_budget_gap_retries_next_run_without_upstream_error_backoff(tmp_path, monkeypatch):
    from rep0rter.collectors.state import write_state
    now = 1000000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    channel = _scheduled_channel('C', now)
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda session: [channel])
    session, transport = _budgeted_pages(monkeypatch, [[{'ts': '800000'}]], 2)
    with Store(tmp_path/'db') as store:
        with store.conn:
            write_state(store, 'slack:C', {'last_complete_ts': '900000', 'last_posted': now,
                'total_messages': 10, 'last_refresh': now, 'last_reconciliation': now,
                'last_attempt': now - 3580, 'gap': True, 'error': 'request budget exhausted'})
        inc.collect(store, session=transport)
        assert session.get.call_count == 1
        assert read_state(store, 'slack:C')['gap'] is False
        assert read_state(store, 'slack:health')['healthy']


def test_unattempted_channels_keep_timestamps_and_catch_up_first(tmp_path, monkeypatch):
    from rep0rter.collectors.state import write_state
    now = 1000000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    channels = [_scheduled_channel('A', now), _scheduled_channel('B', now)]
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda session: channels)
    _, transport = _budgeted_pages(monkeypatch, [[{'ts': '800000'}]], 1)
    with Store(tmp_path/'db') as store:
        with store.conn:
            write_state(store, 'slack:B', {'last_complete_ts': '900000', 'last_attempt': now - 50000,
                'last_refresh': now - 50000, 'last_reconciliation': now - 50000})
        inc.collect(store, session=transport)
        deferred = read_state(store, 'slack:B')
        assert deferred['last_attempt'] == deferred['last_refresh'] == now - 50000
        assert deferred['last_complete_ts'] == '900000'
        assert deferred['gap']
        assert not read_state(store, 'slack:health')['history_complete']
        now += 60
        session, transport = _budgeted_pages(monkeypatch, [[{'ts': '800000'}]], 1)
        inc.collect(store, session=transport)
        assert session.get.call_args.kwargs['params']['channel'] == 'B'
        assert read_state(store, 'slack:B')['gap'] is False
        assert read_state(store, 'slack:health')['healthy']


def test_busy_channel_preserves_cursor_without_starving_quiet_channel(tmp_path, monkeypatch):
    now = 1000000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    channels = [_scheduled_channel('A', now), _scheduled_channel('B', now)]
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda session: channels)
    bot = lambda ts: {'ts': ts, 'text': 'automation', 'user': {'id': 'BOT', 'is_bot': True}}
    session, transport = _budgeted_pages(monkeypatch,
        [[bot('999900'), bot('999800')], [bot('999700'), bot('999600')], [{'ts': '800000'}]], 4)
    with Store(tmp_path/'db') as store:
        inc.collect(store, session=transport)
        assert [call.kwargs['params']['channel'] for call in session.get.call_args_list] == ['A', 'A', 'B']
        a, b = read_state(store, 'slack:A'), read_state(store, 'slack:B')
        assert a['pending_before'] == '999600' and a['pending_high'] == '999900'
        assert 'last_complete_ts' not in a and 'last_refresh' not in a
        assert b['gap'] is False
        assert read_state(store, 'slack:health')['failed_channels'] == 1
        now += 60
        session, transport = _budgeted_pages(monkeypatch, [[{'ts': '800000'}]], 2)
        inc.collect(store, session=transport)
        assert session.get.call_args.kwargs['params']['before'] == '999600'
        assert read_state(store, 'slack:A')['last_complete_ts'] == '999900'
        assert read_state(store, 'slack:health')['healthy']


def test_fractional_bounds_do_not_skip_microsecond_neighbors(monkeypatch):
    get = pages(monkeypatch, [
        [{'ts': '1789571778.900005'}, {'ts': '1789571777.383129'}],
        [{'ts': '1789571777.383129'}, {'ts': '1789571777.383128'}, {'ts': '1789571777.100001'}],
    ])
    rows, complete, error, resume = inc.scan(Mock(), 'C', Decimal('1789571777.200001'))
    assert complete and not error and resume is None
    assert {row['ts'] for row in rows} == {'1789571778.900005', '1789571777.383129', '1789571777.383128'}
    assert get.call_args_list[0].kwargs['after'] == '1789571777'
    assert get.call_args_list[1].kwargs['before'] == '1789571778'


def test_resumed_fractional_cursor_uses_wide_bound_and_exact_progress(monkeypatch):
    get = pages(monkeypatch, [[{'ts': '1789571777.383129'}, {'ts': '1789571777.383128'}, {'ts': '1789571777.100001'}]])
    rows, complete, error, resume = inc.scan(Mock(), 'C', Decimal('1789571777.200001'),
                                           start_before='1789571777.383129')
    assert complete and not error and resume is None
    assert get.call_args.kwargs['before'] == '1789571778'
    assert '1789571777.383128' in {row['ts'] for row in rows}


def test_same_second_page_saturation_remains_a_gap(monkeypatch):
    rows = [{'ts': '1789571777.' + str(900000 - n)} for n in range(100)]
    pages(monkeypatch, [rows, rows])
    saved, complete, error, resume = inc.scan(Mock(), 'C', Decimal('1789571776'))
    assert not complete and len(saved) == 100
    assert 'did not advance' in error and resume is None


def recent_month_html():
    return '''<nav role="pagination">
    <a class="nav-link active" href="/index/channel/C/2026-09">2026-09</a>
    <a class="dropdown-item" href="/index/channel/C/2026-09">2026-09<span class="badge">1</span></a>
    </nav><section role="feed"><div class="message" id="ts-1789571777.3831">
    <span class="message-time" title='{"ts":"1789571777.383129","text":"A real update"}'></span>
    </div></section>'''


def test_repeated_fractional_head_is_verified_against_complete_raw_month(monkeypatch):
    row = {'ts': '1789571777.383129'}
    get = Mock(side_effect=[Mock(json=lambda: {'messages': [row]}),
                           Mock(json=lambda: {'messages': [row]}), Mock(text=recent_month_html())])
    monkeypatch.setattr(inc.api, '_get', get)
    rows, complete, error, cursor = inc.scan(Mock(), 'C', Decimal('1789500000'))
    assert complete and rows == [row] and not error and cursor is None
    assert get.call_args.args[1].endswith('/index/channel/C')


@pytest.mark.parametrize('document', [
    recent_month_html().replace('class="badge">1', 'class="badge">2'),
    recent_month_html().replace('1789571777.383129', '1789571777.383128'),
    recent_month_html().replace('class="nav-link active"', 'class="nav-link"'),
    recent_month_html().replace('/channel/C/', '/channel/OTHER/'),
    recent_month_html().replace('id="ts-1789571777.3831"', 'id="ts-1789571777.3832"'),
])
def test_recent_month_proof_rejects_missing_rows_and_changed_markup(monkeypatch, document):
    monkeypatch.setattr(inc.api, '_get', Mock(return_value=Mock(text=document)))
    assert not inc.api.channel_window_is_complete(Mock(), 'C', Decimal('1789500000'), {Decimal('1789571777.383129')})


def test_recent_month_proof_cannot_cover_previous_month_or_ignore_budget(monkeypatch):
    monkeypatch.setattr(inc.api, '_get', Mock(return_value=Mock(text=recent_month_html())))
    assert not inc.api.channel_window_is_complete(Mock(), 'C', Decimal('1788000000'), {Decimal('1789571777.383129')})
    row = {'ts': '1789571777.383129'}
    monkeypatch.setattr(inc.api, '_get', Mock(side_effect=[
        Mock(json=lambda: {'messages': [row]}), Mock(json=lambda: {'messages': [row]}), BudgetExceeded('budget')]))
    _, complete, error, cursor = inc.scan(Mock(), 'C', Decimal('1789500000'))
    assert not complete and 'budget exhausted' in error and cursor == row['ts']


def test_root_lookup_widens_float_boundary_but_matches_only_exact_id(monkeypatch):
    ts = '1789571777.383129'
    get = pages(monkeypatch, [[{'ts': '1789571777.900000', 'text': 'other'}, {'ts': ts, 'text': 'exact root'}]])
    root = inc.refresh_root(Mock(), 'C', ts, {'C'})
    assert root.id == 'slack:C:' + ts and root.text == 'exact root'
    assert get.call_args.kwargs['before'] == '1789571778'


def test_ineligible_missing_roots_do_not_consume_refresh_slots(tmp_path, monkeypatch):
    from rep0rter.collectors.state import write_state
    now = 1000000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    channel = _scheduled_channel('C', now)
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda session: [channel])
    get = pages(monkeypatch, [[{'ts': '500000', 'text': 'Recovered parent'}]])
    with Store(tmp_path/'db') as store:
        with store.conn:
            write_state(store, 'slack:C', {'last_complete_ts': '900000', 'last_posted': now,
                'total_messages': 10, 'last_refresh': now, 'last_reconciliation': now})
        for index, ts in enumerate(['100000', '100001', '100002', '100003', '500000']):
            parent_id = 'slack:C:' + ts
            child = Event('slack:C:' + str(990000 + index), 'slack', 'thread_reply', 'slack:C',
                          990000 + index, text='Reply', parent_id=parent_id, meta={'context_incomplete': True})
            persist(store, 'fixture', {}, [child], Metrics(), now)
            if index < 4:
                with store.conn:
                    store.conn.execute('INSERT INTO event_tombstones VALUES(?,?,?)', (parent_id, now, 'excluded'))
        inc.collect(store, session=Mock(headers={}))
        assert get.call_count == 1
        assert store.get_event('slack:C:500000').text == 'Recovered parent'
        assert store.get_event('slack:C:990004').meta['context_incomplete'] is False


def test_root_transport_error_stays_degraded_between_refresh_attempts(tmp_path, monkeypatch):
    from rep0rter.collectors.state import write_state
    now = 1000000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    channel = _scheduled_channel('C', now)
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda session: [channel])
    get = Mock(return_value=Mock(json=lambda: 0))
    monkeypatch.setattr(inc.api, '_get', get)
    with Store(tmp_path/'db') as store:
        with store.conn:
            write_state(store, 'slack:C', {'last_complete_ts': '900000', 'last_posted': now,
                'total_messages': 10, 'last_refresh': now, 'last_reconciliation': now})
        root = Event('slack:C:500000', 'slack', 'message', 'slack:C', 500000, text='Original root')
        persist(store, 'fixture', {}, [root], Metrics(), now)
        inc.collect(store, session=Mock(headers={}))
        assert not read_state(store, 'slack:health')['healthy']
        now += 60
        inc.collect(store, session=Mock(headers={}))
        assert get.call_count == 1
        health = read_state(store, 'slack:health')
        assert not health['healthy'] and not health['history_complete']
        assert 'invalid root refresh' in health['reasons'][root.id]
