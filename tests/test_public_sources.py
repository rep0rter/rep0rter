import copy
import json
from unittest.mock import Mock
import pytest
from rep0rter.collectors import github, mastodon, registry
from rep0rter.collectors.state import Metrics, read_state
from rep0rter.sources import normalize_text, eligible, plain_text
from rep0rter.store import Store


def release(**changes):
    value={'id':7,'name':'Civic release','body':'Looking for contributors to help publish an accessible public dataset. This release adds district-level download and comparison tools for community researchers.','draft':False,'published_at':'2026-09-18T00:00:00Z','created_at':'2026-09-17T00:00:00Z','html_url':'https://github.com/example/civic/releases/tag/v1','author':{'id':123,'login':'author'}}
    value.update(changes);return value


def status(instance='https://social.example', **changes):
    value={'id':'7','uri':instance+'/users/civic/statuses/7','url':instance+'/@civic/7','content':'<p>公開 <a href="https://example.test/dataset">dataset</a></p>','visibility':'public','account':{'id':'1','url':instance+'/@civic','acct':'civic','display_name':'Civic'},'created_at':'2026-09-18T00:00:00Z','replies_count':1,'favourites_count':2,'reblogs_count':3,'spoiler_text':'災害'}
    value.update(changes);return value


def response(value, code=200):
    return Mock(status_code=code,json=Mock(return_value=value),content=json.dumps(value).encode(),raise_for_status=Mock())


def test_formats_have_distinct_safe_normalization():
    assert normalize_text('<@USER> hi','mrkdwn') != normalize_text('<@USER> hi','html')
    assert normalize_text('[dataset](https://example.test)','markdown')=='dataset (https://example.test)'
    assert 'alert' not in normalize_text('<script>alert(1)</script><p>Hello</p>','html')
    assert normalize_text('<a href="javascript:alert(1)">Hello</a>','html')=='Hello'


def test_github_source_policy_explicit_collaboration_and_impact():
    assert eligible(github.to_event(release(),'example/civic','release'))
    assert not eligible(github.to_event(release(draft=True),'example/civic','release'))
    raw=release(title='Please help',user={'id':123,'login':'author'},labels=[])
    assert not eligible(github.to_event(raw,'example/civic','issue'))
    raw['labels']=[{'name':'help wanted'}]
    assert eligible(github.to_event(raw,'example/civic','issue'))
    raw['merged_at']='2026-09-18T00:00:00Z'
    assert not eligible(github.to_event(raw,'example/civic','pull_request'))
    raw['body']='## Impact\nPeople can now export accessible data.'
    assert eligible(github.to_event(raw,'example/civic','pull_request'))


def test_mastodon_instances_cw_visibility_and_boosts():
    first=mastodon.to_event(status(),'https://social.example','account','https://social.example/@civic')
    second=mastodon.to_event(status('https://another.example'),'https://another.example','account','https://another.example/@civic')
    assert first.id != second.id and first.meta['canonical_object_id'] != second.meta['canonical_object_id']
    assert first.text.startswith('[Content warning: 災害]') and 'https://example.test/dataset' in first.text
    assert eligible(first)
    for visibility in ['private','unlisted','direct',None]:
        assert mastodon.to_event(status(visibility=visibility),'https://social.example','account','https://social.example/@civic') is None
    assert mastodon.to_event(status(reblog=status()),'https://social.example','account','https://social.example/@civic') is None
    reply=mastodon.to_event(status(in_reply_to_id='6'),'https://social.example','account','https://social.example/@civic')
    assert not eligible(reply)


def test_mastodon_deleted_status_scrubs_original(tmp_path,monkeypatch):
    now=1789689600
    monkeypatch.setattr(mastodon.time,'time',lambda:now)
    actor='https://social.example/@civic'
    account={'id':'1','url':actor,'acct':'civic','display_name':'Civic'}
    with Store(tmp_path/'db') as store:
        session=Mock();session.get.side_effect=[response(account),response([status()])]
        mastodon.collect(store,[actor],session,Metrics())
        stored=store.get_event(mastodon.object_id(status()['uri']))
        assert stored and stored.text
        session.get.side_effect=[response(account),response([]),response({},404)]
        mastodon.collect(store,[actor],session,Metrics())
        deleted=store.get_event(stored.id)
        assert deleted.text=='' and deleted.meta['plain_text']=='' and deleted.meta['deleted_at']==now
        assert not eligible(deleted)


def test_mastodon_visibility_change_withdraws(tmp_path,monkeypatch):
    monkeypatch.setattr(mastodon.time,'time',lambda:1789689600)
    actor='https://social.example/@civic';account={'id':'1','url':actor,'acct':'civic'}
    with Store(tmp_path/'db') as store:
        session=Mock();session.get.side_effect=[response(account),response([status()])]
        mastodon.collect(store,[actor],session,Metrics())
        session.get.side_effect=[response(account),response([]),response(status(visibility='private'))]
        mastodon.collect(store,[actor],session,Metrics())
        assert store.get_event(mastodon.object_id(status()['uri'])).meta['visibility']=='withdrawn'


def test_github_private_repository_does_not_ingest(tmp_path):
    with Store(tmp_path/'db') as store:
        session=Mock();session.get.return_value=response({'private':True,'visibility':'private'})
        github.collect(store,['example/private'],session,Metrics())
        assert not store.event_count() and session.get.call_count==1
        assert read_state(store,'github:example/private')['error']


def test_registry_isolates_failed_source_and_preserves_last_healthy(tmp_path,monkeypatch):
    monkeypatch.setenv('REP0RTER_GITHUB_REPOS','example/civic')
    monkeypatch.setattr(registry.slack_incremental,'collect',Mock(side_effect=RuntimeError('bad homepage')))
    github_collect=Mock(return_value=2);monkeypatch.setattr(registry.github,'collect',github_collect)
    with Store(tmp_path/'db') as store:
        store.set_kv('collector_health',json.dumps({'last_healthy_at':123}))
        assert registry.collect_all(store,session=Mock(headers={}))==2
        health=json.loads(store.get_kv('collector_health'))
        assert not health['healthy'] and health['last_healthy_at']==123
        assert health['sources']['github']['healthy'] and github_collect.call_count==1
        assert 'metrics' in health and 'requests' in health['metrics']


def test_excluded_source_never_fetched(tmp_path,monkeypatch):
    from rep0rter import policy
    monkeypatch.setattr(policy,'container_allowed',lambda *args:False)
    with Store(tmp_path/'db') as store:
        session=Mock()
        github.collect(store,['example/civic'],session,Metrics())
        mastodon.collect(store,['https://social.example/@civic'],session,Metrics())
        session.get.assert_not_called()


def test_rate_limit_is_recorded_and_next_run_does_not_retry(tmp_path,monkeypatch):
    with Store(tmp_path/'db') as store:
        limited=response({},429);limited.headers={'Retry-After':'3600'}
        session=Mock();session.get.return_value=limited
        github.collect(store,['example/civic'],session,Metrics())
        assert read_state(store,'github:example/civic')['retry_at']>0
        github.collect(store,['example/civic'],session,Metrics())
        assert session.get.call_count==1


def test_daily_budget_is_reserved_before_failed_http_request(tmp_path,monkeypatch):
    monkeypatch.setenv('REP0RTER_COLLECT_DAILY_BUDGET','1')
    def fail_http(store,days,max_channels,session,metrics):
        session.get('https://example.test')
    monkeypatch.setattr(registry.slack_incremental,'collect',fail_http)
    with Store(tmp_path/'db') as store:
        session=Mock(headers={});session.get.side_effect=RuntimeError('network down')
        registry.collect_all(store,session=session)
        assert json.loads(store.get_kv('collector_daily_budget'))['requests']==1
        registry.collect_all(store,session=session)
        assert session.get.call_count==1


@pytest.mark.parametrize('actor', [{'login':'release-bot','type':'Bot'},{'login':'dependabot[bot]','type':'User'},{'login':'renovate','type':'User'}])
def test_bot_release_never_eligible(actor):
    raw=release(author=actor)
    assert github.is_automation(raw,'release')
    assert not eligible(github.to_event(raw,'example/civic','release'))


@pytest.mark.parametrize('changes', [{'title':'chore(deps): bump package to 2.0','name':''},{'title':'ci: upgrade workflow','name':''},{'labels':[{'name':'dependencies'}]}])
def test_human_dependency_ci_chores_excluded(changes):
    raw=release(user={'login':'human','type':'User'},**changes)
    assert github.is_automation(raw,'issue')
    assert not eligible(github.to_event(raw,'example/civic','issue'))


def test_empty_release_and_label_without_invitation_not_eligible():
    assert not eligible(github.to_event(release(body='v1.2.3'),'example/civic','release'))
    raw=release(body='A normal bug report without an invitation to work together.',labels=[{'name':'help wanted'}])
    assert not eligible(github.to_event(raw,'example/civic','issue'))


def test_bot_not_persisted_and_override_still_respects_optout(tmp_path,monkeypatch):
    monkeypatch.setattr(github.time,'time',lambda:1789689600)
    raw=release(author={'login':'ci[bot]','type':'Bot','id':123})
    repo={'private':False,'visibility':'public','html_url':'https://github.com/example/civic'}
    with Store(tmp_path/'db') as store:
        session=Mock();session.get.side_effect=[response(repo),response([raw]),response([]),response([])]
        github.collect(store,['example/civic'],session,Metrics())
        assert not store.event_count()
        monkeypatch.setenv('REP0RTER_GITHUB_EDITORIAL_OVERRIDES','github:example/civic:release:7')
        from rep0rter.policy import add_rule
        add_rule(store,'user','github:123')
        session.get.side_effect=[response(repo),response([raw]),response([]),response([])]
        github.collect(store,['example/civic'],session,Metrics())
        assert not store.event_count()


@pytest.mark.parametrize('body', [
 '## 요약\nAdmin에서 정류장을 등록하면 위치 안내 서비스에 자동 연결합니다. 승객 화면에서는 노선에 배정된 버스가 여럿일 때 목표 정류장에 가장 가까운 버스를 선택해서 도착 시간을 표시합니다. 이를 통해 시민이 시골 노선버스의 현재 위치를 쉽게 확인할 수 있습니다.',
 '## 内容（2 commits）\n避難所や給水所の公開データを取り込んだ地図を追加します。市民が災害時の支援情報を印刷して持ち歩けるようになります。開設済みの施設だけを表示して、閉鎖された施設への案内を防止します。既存の公開情報と同じ形式で利用できます。',
 '## Summary\nThe bus location service now provides accessible arrival information for rural passengers. We added the public dataset export so residents can compare routes and find available transport options.',
])
def test_native_language_civic_impact_is_eligible(body):
    raw=release(body=body,merged_at='2026-09-18T00:00:00Z',user={'login':'human','type':'User'})
    assert eligible(github.to_event(raw,'example/civic','pull_request'))


def test_generic_summary_is_not_meaningful_public_impact():
    body='## Summary\nFixed the internal helper and improved the code style. Updated tests and adjusted variable names. Added a new CI workflow for dependency builds.'
    assert not github.meaningful_impact(body)


def test_human_fix_ci_scope_is_excluded_even_with_impact_heading():
    raw=release(name='',title='fix(ci): restrict workflow permissions',user={'login':'human','type':'User'},body='## Impact\nPublic dataset workflow security has improved through restricted permissions and build tokens.',merged_at='2026-09-18T00:00:00Z')
    assert github.is_automation(raw,'pull_request')
    assert not eligible(github.to_event(raw,'example/civic','pull_request'))
