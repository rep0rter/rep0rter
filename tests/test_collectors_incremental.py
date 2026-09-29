import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock
import pytest
from rep0rter.store import Store, Event
from rep0rter.collectors import slack_incremental as inc
from rep0rter.collectors.state import Metrics, persist, read_state, BudgetSession, BudgetExceeded


def published_slack_event(store, channel='C', ts='1790682542.850799', published_at=1):
    from rep0rter.store import Container, Post
    event, _ = inc.api.to_event({'ts': ts, 'text': 'Public civic technology meeting'}, channel, {channel})
    store.upsert_container(Container('slack:' + channel, 'slack', channel))
    store.upsert_events([event], now=1)
    post = Post(event.id, published_at, 10, 'A public meeting', 'Participants discussed civic technology.')
    post.id = store.add_post(post)
    return event, post


def test_permalink_reconciliation_updates_saved_reports_and_survives_collection(tmp_path, monkeypatch):
    from rep0rter.config import Config
    from rep0rter.publishers import site
    from PIL import Image
    cfg = Config(data_dir=tmp_path)
    now = 1790698508
    fragment = 'ts-1790682542.8508'
    get = Mock(return_value={'slack:C:1790682542.850799': fragment,
                             'slack:C:1790682541.850799': 'ts-1790682541.8508'})
    monkeypatch.setattr(inc.api, 'verified_month_fragments', get)
    with Store(cfg.db_path) as store:
        event, post = published_slack_event(store)
        original_url = event.url
        before = tuple(store.conn.execute('SELECT text,meta,last_seen FROM events WHERE id=?', (event.id,)).fetchone())
        fragments = inc.reconcile_permalinks(store, BudgetSession(Mock(headers={}), Metrics()), {'C'}, now)
        assert set(fragments) == {event.id}  # Do not cache unrelated raw month messages.
        saved = store.get_event(event.id)
        assert saved.url.endswith('#' + fragment)
        assert tuple(store.conn.execute('SELECT text,meta,last_seen FROM events WHERE id=?', (event.id,)).fetchone()) == before
        # A normal JSON overlap/root refresh must not replace the verified link.
        event, _ = inc.api.to_event({'ts': '1790682542.850799', 'text': saved.text}, 'C', {'C'})
        inc.apply_verified_permalink(event, fragments)
        persist(store, 'slack:C', {}, [event], Metrics(), now)
        assert store.get_event(event.id).url == saved.url
        inc.reconcile_permalinks(store, BudgetSession(Mock(headers={}), Metrics()), {'C'}, now + 3600)
        assert get.call_count == 1

        class Cards:
            def __init__(self, config):
                self.path = config.site_dir / 'cards' / 'test.png'
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def render(self, *args, **kwargs):
                self.path.parent.mkdir(parents=True, exist_ok=True)
                Image.new('RGB', (2, 2)).save(self.path)
                return self.path
            render_report = render
        monkeypatch.setattr(site, 'CardRenderer', Cards)
        site.build(store, cfg)
        for suffix in ('', '.zh-TW', '.ja', '.ko'):
            for path in (cfg.site_dir / f'index{suffix}.html',
                         cfg.site_dir / 'posts' / str(post.id) / f'index{suffix}.html',
                         cfg.site_dir / f'feed{suffix}.xml'):
                text = path.read_text()
                assert saved.url in text
                assert original_url not in text


def test_permalink_reconciliation_shares_month_and_respects_public_policy(tmp_path, monkeypatch):
    get = Mock(return_value={'slack:C:1790682542.850799': 'ts-1790682542.8508',
                             'slack:C:1790671707.830729': 'ts-1790671707.8307'})
    monkeypatch.setattr(inc.api, 'verified_month_fragments', get)
    with Store(tmp_path / 'db') as store:
        first, _ = published_slack_event(store)
        second, _ = published_slack_event(store, ts='1790671707.830729')
        private, _ = published_slack_event(store, channel='PRIVATE', published_at=3)
        inc.reconcile_permalinks(store, BudgetSession(Mock(headers={}), Metrics()), {'C'}, 1790698508)
        assert get.call_count == 1 and get.call_args.args[1:] == ('C', '2026-09')
        assert store.get_event(first.id).url.endswith('#ts-1790682542.8508')
        assert store.get_event(second.id).url.endswith('#ts-1790671707.8307')
        assert store.get_event(private.id).url == private.url


def test_permalink_reconciliation_preserves_budget_and_unverified_fallback(tmp_path, monkeypatch):
    import requests
    monkeypatch.setattr(inc.api, 'sleep', lambda _: None)
    from rep0rter.collectors import state
    monkeypatch.setattr(state, 'sleep', lambda _: None)
    with Store(tmp_path / 'db') as store:
        event, _ = published_slack_event(store)
        transport = Mock(headers={})
        transport.get.side_effect = requests.Timeout('offline timeout')
        budget = BudgetSession(transport, Metrics(), limit=20, interval=0)
        inc.reconcile_permalinks(store, budget, {'C'}, 1790698508)
        assert budget.metrics.requests == 2 and budget.remaining == 18
        assert store.get_event(event.id).url == event.url
        # A tiny remaining allowance is entirely retained for content collection.
        small = BudgetSession(transport, Metrics(), limit=7, interval=0)
        inc.reconcile_permalinks(store, small, {'C'}, 1790698508)
        assert small.metrics.requests == 0


@pytest.mark.parametrize('scope', ['container', 'event'])
def test_permalink_reconciliation_never_fetches_excluded_public_content(tmp_path, monkeypatch, scope):
    from rep0rter import policy
    get = Mock()
    monkeypatch.setattr(inc.api, 'verified_month_fragments', get)
    with Store(tmp_path / 'db') as store:
        event, _ = published_slack_event(store)
        policy.add_rule(store, scope, event.container_id if scope == 'container' else event.id, now=2)
        inc.reconcile_permalinks(store, BudgetSession(Mock(headers={}), Metrics()), {'C'}, 1790698508)
        get.assert_not_called()


def test_cached_permalink_cannot_restore_withdrawn_event_url(tmp_path, monkeypatch):
    from rep0rter import policy
    from rep0rter.collectors.state import write_state
    get = Mock()
    monkeypatch.setattr(inc.api, 'verified_month_fragments', get)
    with Store(tmp_path / 'db') as store:
        event, _ = published_slack_event(store)
        with store.conn:
            write_state(store, 'slack:permalinks', {'version': inc.PERMALINK_VERSION,
                'fragments': {event.id: 'ts-1790682542.8508'}, 'failed_at': {}})
        policy.redact(store, [event.id])
        assert store.get_event(event.id).url == ''
        assert inc.reconcile_permalinks(store, BudgetSession(Mock(headers={}), Metrics()), {'C'}, 1790698508) == {}
        assert store.get_event(event.id).url == ''
        get.assert_not_called()


def test_permalink_timeout_debt_does_not_starve_untried_older_month(tmp_path, monkeypatch):
    import requests
    from rep0rter.collectors import state
    monkeypatch.setattr(inc.api, 'sleep', lambda _: None)
    monkeypatch.setattr(state, 'sleep', lambda _: None)
    with Store(tmp_path / 'db') as store:
        published_slack_event(store, channel='OLD', published_at=1)
        published_slack_event(store, channel='NEW', published_at=2)
        transport = Mock(headers={})
        transport.get.side_effect = requests.Timeout('offline timeout')
        inc.reconcile_permalinks(store, BudgetSession(transport, Metrics(), limit=8, interval=0),
                                 {'OLD', 'NEW'}, 1790698508)
        assert read_state(store, 'slack:permalinks')['failed_at']['NEW/2026-09'] == 1790698508
        get = Mock(return_value={})
        monkeypatch.setattr(inc.api, 'verified_month_fragments', get)
        inc.reconcile_permalinks(store, BudgetSession(Mock(headers={}), Metrics(), limit=8),
                                 {'OLD', 'NEW'}, 1790698508 + 3600)
        assert get.call_count == 1 and get.call_args.args[1] == 'OLD'


def test_permalink_reconciliation_repairs_two_latest_channels_with_19_requests(tmp_path, monkeypatch):
    def fragments(session, channel, month):
        session.metrics.requests += 1
        return {f'slack:{channel}:1790682542.850799': 'ts-1790682542.8508'}
    get = Mock(side_effect=fragments)
    monkeypatch.setattr(inc.api, 'verified_month_fragments', get)
    with Store(tmp_path / 'db') as store:
        for i in range(4):
            published_slack_event(store, channel=f'C{i}', published_at=i)
        inc.reconcile_permalinks(store, BudgetSession(Mock(headers={}), Metrics(), limit=19),
                                 {'C0', 'C1', 'C2', 'C3'}, 1790698508)
        assert [call.args[1] for call in get.call_args_list] == ['C3', 'C2']


def test_permalink_reconciliation_handles_synthetic_reports_and_skips_unknown_ids(tmp_path, monkeypatch):
    from dataclasses import replace
    from rep0rter.store import Post
    identity = 'slack:C:1790682542.850799'
    get = Mock(return_value={identity: 'ts-1790682542.8508'})
    monkeypatch.setattr(inc.api, 'verified_month_fragments', get)
    with Store(tmp_path / 'db') as store:
        source, _ = published_slack_event(store)
        synthetic = replace(source, id='story-update:example:digest', kind='story_update')
        legacy = replace(source, id='slack:C:source-example', meta={})
        unknown = replace(synthetic, id='story-update:unknown:digest', meta={})
        store.upsert_events([synthetic, legacy, unknown])
        for index, event in enumerate((synthetic, legacy, unknown), start=2):
            store.add_post(Post(event.id, index, 10, 'Published report', 'A source update'))
        inc.reconcile_permalinks(store, BudgetSession(Mock(headers={}), Metrics()), {'C'}, 1790698508)
        assert store.get_event(synthetic.id).url.endswith('#ts-1790682542.8508')
        assert store.get_event(legacy.id).url == source.url
        assert store.get_event(unknown.id).url == source.url
        assert get.call_count == 1


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
    monkeypatch.setattr(inc.api, 'root_from_html', Mock(return_value=None))
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


def test_missing_exact_root_stays_unhealthy_through_backoff_and_clears_on_recovery(tmp_path, monkeypatch):
    from rep0rter.collectors.state import write_state
    now = 1000000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda session: [_scheduled_channel('C', 1000000)])
    child = Event('slack:C:990000', 'slack', 'thread_reply', 'slack:C', 990000,
                  text='Reply', parent_id='slack:C:500000', meta={'context_incomplete': True})
    get = pages(monkeypatch, [[], [{'ts': '500000', 'text': 'Recovered exact parent'}]])
    fallback = Mock(return_value=None)
    monkeypatch.setattr(inc.api, 'root_from_html', fallback)
    with Store(tmp_path/'db') as store:
        with store.conn:
            write_state(store, 'slack:C', {'last_complete_ts': '990000', 'last_posted': now,
                'total_messages': 10, 'last_refresh': now, 'last_reconciliation': now})
        persist(store, 'fixture', {}, [child], Metrics(), now-100)
        # An unattempted missing root is already a known context gap.
        inc.collect(store, session=BudgetSession(Mock(headers={}), Metrics(), limit=0))
        assert not read_state(store, 'slack:health')['history_complete']
        get.assert_not_called()
        inc.collect(store, session=Mock(headers={}))
        health = read_state(store, 'slack:health')
        assert not health['healthy'] and health['available']
        assert 'exact parent' in health['reasons'][child.parent_id]
        assert not read_state(store, 'slack:root_refresh')[child.parent_id]['resolved']
        fallback.assert_called_once()
        now += 60
        inc.collect(store, session=Mock(headers={}))
        assert get.call_count == 1
        assert not read_state(store, 'slack:health')['healthy']
        now += inc.REFRESH_INTERVAL
        # Isolate the root retry from the periodic channel scan.
        with store.conn:
            state = read_state(store, 'slack:C')
            state.update(last_refresh=now, last_reconciliation=now)
            write_state(store, 'slack:C', state)
        inc.collect(store, session=Mock(headers={}))
        assert get.call_count == 2
        assert read_state(store, 'slack:health')['healthy']
        assert store.get_event(child.parent_id).text == 'Recovered exact parent'
        assert not store.get_event(child.id).meta['context_incomplete']


def test_new_reply_refreshes_existing_old_root_before_unrelated_sweep(tmp_path, monkeypatch):
    from rep0rter.collectors.state import write_state
    now = 1000000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda session: [_scheduled_channel('C', now)])
    path = tmp_path/'db'
    with Store(path) as store:
        with store.conn:
            write_state(store, 'slack:C', {'last_complete_ts': '990000', 'last_posted': now,
                'total_messages': 10, 'last_refresh': now, 'last_reconciliation': now})
        # More old roots than the two-entry background sweep; target is newest.
        for ts in ['100000', '200000', '300000', '500000']:
            persist(store, 'fixture', {}, [Event('slack:C:'+ts, 'slack', 'message', 'slack:C',
                float(ts), text='Old context')], Metrics(), now-10000+int(ts)/1000)
        child = Event('slack:C:990000', 'slack', 'thread_reply', 'slack:C', 990000,
            text='New material update', parent_id='slack:C:500000')
        persist(store, 'fixture', {}, [child], Metrics(), now-100)
        # Persist pending work even when all requests have already been spent.
        exhausted = BudgetSession(Mock(headers={}), Metrics(), limit=0)
        inc.collect(store, session=exhausted)
        queue = read_state(store, 'slack:root_refresh')
        assert queue[child.parent_id]['reply_pending']
        assert 'attempted_at' not in queue[child.parent_id]
    # A fresh process gets only one request: it must serve the new reply, not
    # either unrelated older root, and must not advance the channel watermark.
    get = pages(monkeypatch, [[{'ts': '500000', 'text': 'Updated root context'}]])
    with Store(path) as store:
        transport = BudgetSession(Mock(headers={}), Metrics(), limit=1)
        # The mock API bypasses BudgetSession; charge the actual lookup here.
        def refresh(session, channel, ts, public_ids):
            session.metrics.requests += 1
            return original_refresh(session, channel, ts, public_ids)
        original_refresh = inc.refresh_root
        monkeypatch.setattr(inc, 'refresh_root', refresh)
        inc.collect(store, session=transport)
        assert get.call_count == 1
        assert get.call_args.kwargs['before'] == '500001'
        assert store.get_event(child.parent_id).text == 'Updated root context'
        assert read_state(store, 'slack:C')['last_complete_ts'] == '990000'
        assert not read_state(store, 'slack:root_refresh')[child.parent_id]['reply_pending']
        # Re-observing the reply through the overlap does not schedule it again.
        persist(store, 'fixture', {}, [child], Metrics(), now+1)
        inc.collect(store, session=BudgetSession(Mock(headers={}), Metrics(), limit=0))
        assert not read_state(store, 'slack:root_refresh')[child.parent_id]['reply_pending']


def test_reply_refresh_queue_is_bounded_and_drains_unattempted_first(tmp_path, monkeypatch):
    from rep0rter.collectors.state import write_state
    now = 1000000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda session: [_scheduled_channel('C', now)])
    monkeypatch.setattr(inc, 'MAX_REPLY_REFRESH_QUEUE', 2)
    with Store(tmp_path/'db') as store:
        with store.conn:
            write_state(store, 'slack:C', {'last_complete_ts': '990000', 'last_posted': now,
                'total_messages': 10, 'last_refresh': now, 'last_reconciliation': now})
        for index in range(3):
            root_id = 'slack:C:'+str(500000+index)
            persist(store, 'fixture', {}, [Event(root_id, 'slack', 'message', 'slack:C',
                500000+index, text='Existing root')], Metrics(), now-10000)
            persist(store, 'fixture', {}, [Event('slack:C:'+str(990000+index), 'slack',
                'thread_reply', 'slack:C', 990000+index, text='New reply', parent_id=root_id)],
                Metrics(), now-100+index)
        inc.collect(store, session=BudgetSession(Mock(headers={}), Metrics(), limit=0))
        pending = lambda: [k for k,v in read_state(store,'slack:root_refresh').items() if v.get('reply_pending')]
        assert len(pending()) == 2
        attempts = []
        def refresh(session, channel, ts, public_ids):
            session.metrics.requests += 1
            attempts.append(ts)
            if ts == '500000':
                raise RuntimeError('temporary upstream failure')
            return Event('slack:C:'+ts,'slack','message','slack:C',float(ts),text='Refreshed')
        monkeypatch.setattr(inc, 'refresh_root', refresh)
        inc.collect(store, session=BudgetSession(Mock(headers={}), Metrics(), limit=1))
        inc.collect(store, session=BudgetSession(Mock(headers={}), Metrics(), limit=1))
        assert attempts == ['500000', '500001']
        assert len(pending()) == 1
        # The third durable reply refills the bounded queue on the next run.
        inc.collect(store, session=BudgetSession(Mock(headers={}), Metrics(), limit=1))
        assert attempts == ['500000', '500001', '500002']
        assert pending() == ['slack:C:500000']


def test_filtered_bot_root_is_confirmed_excluded_without_retaining_content(tmp_path, monkeypatch):
    from rep0rter.collectors.state import write_state
    now = 1000000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda session: [_scheduled_channel('C', 1000000)])
    get = pages(monkeypatch, [[], []])
    fallback = Mock(side_effect=[{'ts': '500000', 'text': 'Excluded bot content',
                                 'subtype': 'bot_message', 'bot_id': 'BOT', 'app_id': 'APP'}, None])
    monkeypatch.setattr(inc.api, 'root_from_html', fallback)
    with Store(tmp_path/'db') as store:
        with store.conn:
            write_state(store, 'slack:C', {'last_complete_ts': '990000', 'last_posted': now,
                'total_messages': 10, 'last_refresh': now, 'last_reconciliation': now})
            write_state(store, 'slack:root_refresh', {'slack:C:500000': {
                'attempted_at': now-inc.REFRESH_INTERVAL, 'resolved': False, 'error': 'previous unknown root'}})
        child = Event('slack:C:990000', 'slack', 'thread_reply', 'slack:C', 990000,
                      text='Human reply', parent_id='slack:C:500000', meta={'context_incomplete': True})
        persist(store, 'fixture', {}, [child], Metrics(), now-100)
        inc.collect(store, session=Mock(headers={}))
        queue = read_state(store, 'slack:root_refresh')
        assert queue[child.parent_id]['policy_excluded']
        assert 'error' not in queue[child.parent_id]
        assert store.get_event(child.parent_id) is None
        assert store.get_event(child.id).meta['context_incomplete']
        assert 'Excluded bot content' not in json.dumps(queue)
        assert read_state(store, 'slack:health')['healthy']
        # Confirmed exclusions do not consume refresh slots every six hours.
        now += inc.REFRESH_INTERVAL
        with store.conn:
            state = read_state(store, 'slack:C')
            state.update(last_refresh=now, last_reconciliation=now)
            write_state(store, 'slack:C', state)
        inc.collect(store, session=Mock(headers={}))
        assert get.call_count == 1 and fallback.call_count == 1
        # If revalidation can no longer establish the root, restore the gap.
        now += 7*86400
        with store.conn:
            state = read_state(store, 'slack:C')
            state.update(last_refresh=now, last_reconciliation=now)
            write_state(store, 'slack:C', state)
        inc.collect(store, session=Mock(headers={}))
        assert not read_state(store, 'slack:health')['healthy']
        assert 'exact parent' in read_state(store, 'slack:health')['reasons'][child.parent_id]


def test_root_html_fallback_obeys_shared_request_budget(monkeypatch):
    monkeypatch.setattr(inc.api, 'sleep', lambda seconds: None)
    response = Mock(content=b'{"messages":[]}', json=lambda: {'messages': []})
    session = Mock(headers={}, get=Mock(return_value=response))
    metrics = Metrics()
    budget = BudgetSession(session, metrics, limit=1, interval=0)
    with pytest.raises(BudgetExceeded):
        inc.refresh_root(budget, 'C', '1789785807.859239', {'C'})
    assert session.get.call_count == 1 and metrics.requests == 1


def _seed_legacy_root_retry(store, now, queue_state, root_ts='500000'):
    from rep0rter.collectors.state import write_state
    root_id = 'slack:C:' + root_ts
    child = Event('slack:C:reply-'+root_ts, 'slack', 'thread_reply', 'slack:C', now-100,
                  text='Human reply', parent_id=root_id, meta={'context_incomplete': True})
    persist(store, 'fixture', {}, [child], Metrics(), now-100)
    with store.conn:
        write_state(store, 'slack:C', {'last_complete_ts': str(now-100), 'last_posted': now,
            'total_messages': 10, 'last_refresh': now, 'last_reconciliation': now})
        queue = read_state(store, 'slack:root_refresh')
        queue[root_id] = dict(queue_state)
        write_state(store, 'slack:root_refresh', queue)
    return root_id


@pytest.mark.parametrize('outcome', [None, RuntimeError('upstream unavailable')])
def test_legacy_failed_root_gets_one_strategy_upgrade_retry_then_normal_backoff(tmp_path, monkeypatch, outcome):
    now = 1000000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda session: [_scheduled_channel('C', 1000000)])
    refresh = Mock(side_effect=[outcome])
    monkeypatch.setattr(inc, 'refresh_root', refresh)
    with Store(tmp_path/'db') as store:
        root_id = _seed_legacy_root_retry(store, now, {
            'attempted_at': now-60, 'resolved': False, 'error': 'old JSON-only lookup failed'})
        inc.collect(store, session=Mock(headers={}))
        refresh.assert_called_once()
        state = read_state(store, 'slack:root_refresh')[root_id]
        assert state['lookup_version'] == inc.ROOT_LOOKUP_VERSION
        assert state['attempted_at'] == now and not state['resolved']
        assert not read_state(store, 'slack:health')['healthy']
        now += 60
        inc.collect(store, session=Mock(headers={}))
        refresh.assert_called_once()
        assert not read_state(store, 'slack:health')['healthy']


@pytest.mark.parametrize('budget', [0, 1])
def test_lookup_upgrade_is_not_consumed_by_no_budget_or_partial_json_lookup(tmp_path, monkeypatch, budget):
    now = 1000000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    monkeypatch.setattr(inc.api, 'sleep', lambda seconds: None)
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda session: [_scheduled_channel('C', now)])
    transport = Mock(headers={})
    transport.get.return_value = Mock(content=b'{"messages":[]}', json=lambda: {'messages': []})
    with Store(tmp_path/'db') as store:
        old = {'attempted_at': now-60, 'resolved': False, 'error': 'old JSON-only lookup failed'}
        root_id = _seed_legacy_root_retry(store, now, old)
        metrics = Metrics()
        inc.collect(store, session=BudgetSession(transport, metrics, limit=budget, interval=0))
        assert metrics.requests == transport.get.call_count == budget
        assert read_state(store, 'slack:root_refresh')[root_id] == old
        assert not read_state(store, 'slack:health')['healthy']
        transport.get.return_value = Mock(content=b'root', json=lambda: {
            'messages': [{'ts': '500000', 'text': 'Recovered exact root'}]})
        metrics = Metrics()
        inc.collect(store, session=BudgetSession(transport, metrics, limit=1, interval=0))
        assert metrics.requests == 1
        assert store.get_event(root_id).text == 'Recovered exact root'
        assert read_state(store, 'slack:root_refresh')[root_id]['lookup_version'] == inc.ROOT_LOOKUP_VERSION
        assert read_state(store, 'slack:health')['healthy']


@pytest.mark.parametrize('queue_state', [
    {'resolved': True},
    {'resolved': False},
    {'resolved': False, 'error': 'already used the new lookup', 'lookup_version': inc.ROOT_LOOKUP_VERSION},
])
def test_lookup_upgrade_does_not_force_resolved_unattempted_or_current_strategy_roots(tmp_path, monkeypatch, queue_state):
    now = 1000000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda session: [_scheduled_channel('C', now)])
    refresh = Mock()
    monkeypatch.setattr(inc, 'refresh_root', refresh)
    with Store(tmp_path/'db') as store:
        root_id = _seed_legacy_root_retry(store, now, dict(queue_state, attempted_at=now-60))
        if queue_state.get('resolved'):
            persist(store, 'fixture', {}, [Event(root_id, 'slack', 'message', 'slack:C',
                    500000, text='Already recovered')], Metrics(), now)
        inc.collect(store, session=Mock(headers={}))
        refresh.assert_not_called()
        assert read_state(store, 'slack:root_refresh')[root_id].get('lookup_version') == queue_state.get('lookup_version')


def test_strategy_upgrade_still_refreshes_at_most_four_roots_per_round(tmp_path, monkeypatch):
    now = 1000000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda session: [_scheduled_channel('C', now)])
    refresh = Mock(return_value=None)
    monkeypatch.setattr(inc, 'refresh_root', refresh)
    with Store(tmp_path/'db') as store:
        roots = [_seed_legacy_root_retry(store, now, {
            'attempted_at': now-60, 'resolved': False, 'error': 'old JSON-only lookup failed'}, str(500000+i))
            for i in range(5)]
        inc.collect(store, session=Mock(headers={}))
        assert refresh.call_count == 4
        queue = read_state(store, 'slack:root_refresh')
        assert sum(queue[root].get('lookup_version') == inc.ROOT_LOOKUP_VERSION for root in roots) == 4
        inc.collect(store, session=Mock(headers={}))
        assert refresh.call_count == 5
        assert not read_state(store, 'slack:health')['healthy']


def test_missing_root_upgrade_precedes_unattempted_routine_refreshes(tmp_path, monkeypatch):
    now = 1000000
    monkeypatch.setattr(inc.time, 'time', lambda: now)
    monkeypatch.setattr(inc.api, 'fetch_channels', lambda session: [_scheduled_channel('C', now)])
    attempted = []
    def refresh(session, channel, ts, public_ids):
        attempted.append(ts)
        return None
    monkeypatch.setattr(inc, 'refresh_root', refresh)
    with Store(tmp_path/'db') as store:
        root_id = _seed_legacy_root_retry(store, now, {
            'attempted_at': now-60, 'resolved': False, 'error': 'old JSON-only lookup failed'})
        for stamp in range(990000, 990004):
            persist(store, 'fixture', {}, [Event('slack:C:'+str(stamp), 'slack', 'message',
                    'slack:C', stamp, text='Routine root without a context gap')], Metrics(), now-100)
        inc.collect(store, session=Mock(headers={}))
        assert attempted[0] == '500000'
        assert len(attempted) == 4
        assert read_state(store, 'slack:root_refresh')[root_id]['lookup_version'] == inc.ROOT_LOOKUP_VERSION
        assert not read_state(store, 'slack:health')['healthy']
