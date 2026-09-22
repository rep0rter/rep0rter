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


@pytest.mark.parametrize('original_text,latest_text,label,false_positives,false_negatives', [
    ('工作坊開放報名，歡迎一起來參與 https://example.test', 'edited, no announcement', 'reject', 1, 0),
    ('not an announcement', '工作坊開放報名，歡迎一起來參與 https://example.test', 'publish', 0, 1),
])
def test_review_survives_new_unreviewed_snapshot_without_label_transfer(
    tmp_path, original_text, latest_text, label, false_positives, false_negatives,
):
    e = event(original_text)
    with Store(tmp_path/'db') as store:
        original = record_decision(store, e, None, CFG, NOW, evaluate(e, None, CFG, NOW), True)
        review_decision(store, original, label, 'Review of the original text', NOW+1)
        e.text = latest_text
        later = record_decision(store, e, None, CFG, NOW+3600,
                                evaluate(e, None, CFG, NOW+3600), False)
        report = evaluation_report(store, NOW+3601)
        assert report['events'] == 1 and report['observations'] == 2
        assert report['reviewed_events'] == report['reviewed_observations'] == 1
        assert report['review_basis'] == 'latest_reviewed_snapshot_per_event'
        assert report['reviewed_decision_ids'] == [original]
        assert report['latest_observation_reviewed_events'] == 0
        assert report['false_positive_labels'] == false_positives
        assert report['false_negative_labels'] == false_negatives
        assert report['live_selection_false_positive_labels'] == int(label == 'reject')
        assert report['eligible_channel_size_coverage']['small_under_100'] == false_negatives
        assert store.conn.execute('SELECT reviewer_label FROM editorial_decisions WHERE id=?',
                                  (later,)).fetchone()[0] is None


def test_latest_reviewed_snapshot_counts_once_and_keeps_its_original_threshold(tmp_path):
    e = event('工作坊開放報名，歡迎一起來參與 https://example.test')
    with Store(tmp_path/'db') as store:
        original = record_decision(store, e, None, CFG, NOW, evaluate(e, None, CFG, NOW), True)
        review_decision(store, original, 'reject', 'Original review', NOW+1)
        high_threshold = SimpleNamespace(**{**vars(CFG), 'score_threshold': 20})
        later = record_decision(store, e, None, high_threshold, NOW+3600,
                                evaluate(e, None, high_threshold, NOW+3600), False)
        review_decision(store, later, 'publish', 'Missed at higher threshold', NOW+3601)
        # Re-reviewing the old decision later must not replace the newer snapshot.
        review_decision(store, original, 'reject', 'Confirmed old review', NOW+3602)
        report = evaluation_report(store, NOW+3603)
        assert report['reviewed_events'] == 1 and report['reviewed_observations'] == 2
        assert report['reviewed_decision_ids'] == [later]
        assert report['latest_observation_reviewed_events'] == 1
        assert report['false_positive_labels'] == 0
        assert report['false_negative_labels'] == 1
        assert report['live_selection_false_positive_labels'] == 0


def test_reviews_of_other_scoring_versions_are_not_current_accuracy_evidence(tmp_path):
    e = event('工作坊開放報名，歡迎一起來參與 https://example.test')
    with Store(tmp_path/'db') as store:
        original = record_decision(store, e, None, CFG, NOW, evaluate(e, None, CFG, NOW), True)
        review_decision(store, original, 'reject', 'Different policy', NOW+1)
        with store.conn:
            store.conn.execute("UPDATE editorial_decisions SET score_version='old-version' WHERE id=?", (original,))
        record_decision(store, e, None, CFG, NOW+3600, evaluate(e, None, CFG, NOW+3600), True)
        report = evaluation_report(store, NOW+3601)
        assert report['other_version_observations'] == 1
        assert report['reviewed_events'] == report['reviewed_observations'] == 0
        assert report['reviewed_decision_ids'] == []
        assert report['latest_observation_reviewed_events'] == 0
        assert report['false_positive_labels'] == report['false_negative_labels'] == 0


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


def test_observation_window_requires_current_version_shadow_days(tmp_path):
    e=event('工作坊開放報名，歡迎一起來參與 https://example.test')
    with Store(tmp_path/'db') as store:
        for day in range(15):
            when=NOW+day*86400
            e.ts=when-60
            decision=evaluate(e,None,CFG,when)
            record_decision(store,e,None,CFG,when,decision,True,mode='active')
        report=evaluation_report(store,NOW+30*86400)
        assert not report['observation_complete']
        assert report['shadow_observations']==0
        assert report['observation_span_days']==14
        store.conn.execute("UPDATE editorial_decisions SET mode='shadow',score_version='old-version'")
        store.conn.commit()
        report=evaluation_report(store,NOW+30*86400)
        assert not report['observation_complete'] and report['events']==0
        assert report['other_version_observations']==15
        for day in range(15):
            when=NOW+day*86400
            e.ts=when-60
            record_decision(store,e,None,CFG,when,evaluate(e,None,CFG,when),True,mode='shadow')
        report=evaluation_report(store,NOW+30*86400)
        assert report['observation_complete']
        assert report['shadow_observation_dates']==15
        assert report['shadow_observation_span_days']==14
        assert report['reviewed_events']==0  # Duration alone is never accuracy evidence.


def test_two_sparse_shadow_dates_do_not_qualify_two_weeks(tmp_path):
    e=event('工作坊開放報名，歡迎一起來參與 https://example.test')
    with Store(tmp_path/'db') as store:
        for day in [0,14]:
            when=NOW+day*86400
            e.ts=when-60
            record_decision(store,e,None,CFG,when,evaluate(e,None,CFG,when),True,mode='shadow')
        report=evaluation_report(store,NOW+30*86400)
        assert report['shadow_observation_span_days']==14
        assert not report['observation_complete']


def test_other_sources_and_future_rows_do_not_qualify_slack_shadow_window(tmp_path):
    e = event('工作坊開放報名，歡迎一起來參與 https://example.test')
    with Store(tmp_path/'db') as store:
        for day in range(15):
            when = NOW + day * 86400
            e.source = 'rss'
            record_decision(store, e, None, CFG, when, evaluate(e, None, CFG, when), True, mode='shadow')
        e.source = 'slack'
        record_decision(store, e, None, CFG, NOW, evaluate(e, None, CFG, NOW), True, mode='shadow')
        record_decision(store, e, None, CFG, NOW+30*86400, evaluate(e, None, CFG, NOW), True, mode='shadow')
        result = evaluation_report(store, NOW+14*86400)
        assert result['shadow_source'] == 'slack'
        assert result['shadow_observations'] == result['shadow_observation_dates'] == 1
        assert result['shadow_observation_span_days'] == 0
        assert not result['observation_complete']


def test_shadow_review_coverage_uses_exact_reviewed_source_mode_and_channel_size(tmp_path):
    e = event('工作坊開放報名，歡迎一起來參與 https://example.test')
    small = Container('slack:C', 'slack', 'small', num_members=10)
    large = Container('slack:C', 'slack', 'large', num_members=1000)
    with Store(tmp_path/'db') as store:
        key = record_decision(store, e, small, CFG, NOW, evaluate(e, small, CFG, NOW), False, mode='shadow')
        review_decision(store, key, 'reject', 'False positive before ranking', NOW+1)
        e.text = 'Changed to an uninformative message'
        record_decision(store, e, large, CFG, NOW+60, evaluate(e, large, CFG, NOW+60), True, mode='shadow')
        # A review in active mode must not displace the shadow review.
        key = record_decision(store, e, large, CFG, NOW+120, evaluate(e, large, CFG, NOW+120), True)
        review_decision(store, key, 'publish', 'Active review', NOW+121)
        e.id = 'slack:C:2'
        key = record_decision(store, e, None, CFG, NOW, evaluate(e, None, CFG, NOW), True, mode='shadow')
        review_decision(store, key, 'publish', 'False negative', NOW+1)
        e.id = 'slack:C:3'
        key = record_decision(store, e, small, CFG, NOW, evaluate(e, small, CFG, NOW), False, mode='shadow')
        review_decision(store, key, 'review', 'Still uncertain', NOW+1)
        e.id, e.source = 'rss:1', 'rss'
        key = record_decision(store, e, small, CFG, NOW, evaluate(e, small, CFG, NOW), True, mode='shadow')
        review_decision(store, key, 'reject', 'Different source', NOW+1)
        result = evaluation_report(store, NOW+180)['shadow_review_coverage']
        assert result['large_1000_plus']['observed_events'] == 1
        assert result['large_1000_plus']['reviewed_events'] == 0
        small_result = result['small_under_100']
        assert small_result['observed_events'] == 1
        assert small_result['reviewed_events'] == 2
        assert small_result['reject_labels'] == small_result['review_labels'] == 1
        assert small_result['proposed_selected_reviewed'] == small_result['live_excluded_reviewed'] == 1
        assert small_result['proposed_excluded_reviewed'] == 0
        assert small_result['false_positive_labels'] == 1
        unknown = result['unknown']
        assert unknown['publish_labels'] == unknown['proposed_excluded_reviewed'] == 1
        assert unknown['live_selected_reviewed'] == unknown['false_negative_labels'] == 1
