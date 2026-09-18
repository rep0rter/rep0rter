from datetime import datetime
import json
from types import SimpleNamespace

import pytest

from rep0rter.editorial import evaluate, record_decision, review_decision, evaluation_report, TZ
from rep0rter.store import Container, Event, Store

NOW = datetime(2026, 9, 19, 0, 30, tzinfo=TZ).timestamp()
CFG = SimpleNamespace(score_threshold=6, max_item_age_hours=48, keywords=['松', '徵', 'release'])


def event(text, **kwargs):
    return Event('slack:C:1', 'slack', 'message', 'slack:C', NOW-3600, text=text, **kwargs)


@pytest.mark.parametrize('text', [
    '松鼠的特徵很有意思，今天看了很輕鬆 https://example.test',
    'This immigrant grants access freely https://example.test',
    'A migration document available here https://example.test/release',
    'https://example.test',
    '這個工具尚未發布，我們還在討論 https://example.test',
    '> 這個工具發布了，歡迎一起來試用 https://example.test',
])
def test_no_keyword_substring_negated_quoted_or_pure_url_admission(text):
    decision = evaluate(event(text, reply_count=999, reaction_count=999), None, CFG, NOW)
    assert not decision.eligible
    assert decision.score == 0


def test_short_dated_announcement_does_not_need_large_channel():
    e = event('9/19 週六食農講座，探討蜂蜜與活魚 https://example.test')
    small = evaluate(e, Container('x','slack','food',num_members=1), CFG, NOW)
    large = evaluate(e, Container('x','slack','food',num_members=100000), CFG, NOW)
    assert small.eligible and small.score == 6
    assert small.score == large.score
    assert small.components['members'] == 0


def test_interaction_is_capped_and_cannot_bypass_gate():
    accepted = evaluate(event('工作坊開放報名，歡迎一起來參與 https://example.test', reply_count=999, reaction_count=999), None, CFG, NOW)
    rejected = evaluate(event('嗨', reply_count=999, reaction_count=999), None, CFG, NOW)
    assert accepted.components['interaction'] == 2
    assert accepted.score == 8
    assert not rejected.eligible


def test_tonight_after_midnight_is_held():
    result = evaluate(event('今晚有小松活動，歡迎大家來參加 https://example.test'), None, CFG, NOW)
    assert result.reasons == ['tonight_crossed_source_day']


def test_snapshot_is_immutable_and_review_report_honest(tmp_path):
    e = event('工作坊開放報名，歡迎一起來參與 https://example.test')
    with Store(tmp_path/'db') as store:
        decision = evaluate(e, None, CFG, NOW)
        key = record_decision(store,e,None,CFG,NOW,decision,True)
        e.text = 'edited'; e.reaction_count = 100
        saved = json.loads(store.conn.execute('SELECT snapshot FROM editorial_decisions').fetchone()[0])
        assert saved['event']['text'].startswith('工作坊')
        assert saved['event']['reaction_count'] == 0
        review_decision(store,key,'reject','missing context',NOW+1)
        report = evaluation_report(store,NOW+86400)
        assert report['false_positive_labels'] == 1
        assert not report['observation_complete']
        assert report['observed_days'] == 1


def test_anonymous_27_root_fixture_preserves_seven_editorial_verdicts():
    from pathlib import Path
    data=json.loads((Path(__file__).parent/'fixtures/editorial/reviewed_roots.json').read_text())
    cases=data['cases']
    assert len(cases)==27
    assert sorted(c['reviewed_post'] for c in cases if c['reviewed_post'])==list(range(1,8))
    accepted=[]
    for case in cases:
        e=event(case['text'])
        if case['name'].startswith('internal_'):
            e.container_id='slack:C069ARZJNE9'
        decision=evaluate(e,None,CFG,NOW)
        assert decision.eligible is case['eligible'], (case['name'],decision)
        if case['reason']:
            assert decision.reasons==[case['reason']],case['name']
        if decision.eligible:
            accepted.append(case['name'])
    assert len(accepted)==6


def test_replay_uses_saved_time_and_counters(tmp_path):
    from rep0rter.editorial import replay_decision
    e=event('工作坊開放報名，歡迎一起來參與 https://example.test',reaction_count=3)
    with Store(tmp_path/'db') as store:
        decision=evaluate(e,None,CFG,NOW)
        key=record_decision(store,e,None,CFG,NOW,decision,True)
        e.text='deleted';e.reaction_count=999
        store.upsert_events([e])
        replay=replay_decision(store,key)
        assert replay['replayed']==replay['recorded']
