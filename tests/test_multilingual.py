import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rep0rter.cli import cmd_translate
from rep0rter.config import Config
from rep0rter.i18n import LANGUAGES, post_text
from rep0rter.reporter import Candidate, missing_languages, translate_post, write_multilingual_item
from rep0rter.store import Event, Post, Store
from rep0rter.writer_contract import TZ


def translations():
    return {lang: {'headline': name + ' title', 'summary': name + ' summary'} for lang, name in LANGUAGES.items()}


def response():
    return {"translations": translations(), "evidence_ids": ["slack:C:1"], "event_date": None,
            "participation_url": None, "needs_review": False, "review_reason": ""}


def candidate():
    return Candidate(Event('slack:C:1', 'slack', 'message', 'slack:C', 1, text='9/19 工作坊開放報名，歡迎一起參與'), None, 8, plain_text='9/19 工作坊開放報名')


def test_four_languages_are_written_in_one_call_and_survive_store(tmp_path):
    llm = Mock()
    llm.chat_json.return_value = response()
    headline, summary, editions = write_multilingual_item(candidate(), llm)
    assert llm.chat_json.call_count == 1
    assert set(editions) == set(LANGUAGES)
    with Store(tmp_path / 'db') as store:
        store.upsert_events([candidate().event])
        store.add_post(Post('slack:C:1', 2, 8, headline, summary, translations=editions))
        loaded, event, _ = store.recent_posts()[0]
        assert loaded.translations == editions
        assert event.id == 'slack:C:1'
        assert post_text(loaded, 'ko') == ('한국어 title', '한국어 summary', True)


def test_invalid_languages_are_missing_not_coerced_or_truncated():
    llm = Mock()
    data = translations()
    data['ko']['headline'] = ['not text']
    data['en']['summary'] = 'x' * 501
    data['ja']['summary'] = ' '
    llm.chat_json.return_value = {**response(), 'translations': data}
    headline, summary, editions = write_multilingual_item(candidate(), llm)
    assert set(editions) == {'zh-TW'}
    post = Post('x', 1, 9, headline, summary, translations=editions)
    assert post_text(post, 'en') == (headline, summary, False)


def test_offline_and_llm_failure_keep_original_excerpt_without_fake_translations():
    llm = Mock()
    llm.chat_json.side_effect = ValueError('malformed output')
    for writer in (None, llm):
        headline, summary, editions = write_multilingual_item(candidate(), writer)
        assert headline and summary
        assert editions == {}


def test_backfill_only_missing_languages_is_idempotent():
    post = Post('x', 1, 9, '既有標題', '既有摘要', translations={'ja': {'headline': '既存', 'summary': '既存の要約'}})
    llm = Mock()
    llm.chat_json.return_value = translations()
    assert translate_post(post, llm)
    sent = json.loads(llm.chat_json.call_args.args[1])
    assert sent['languages'] == ['en', 'zh-TW', 'ko']
    assert post.translations['ja']['headline'] == '既存'
    assert post.headline == '既有標題'
    assert not translate_post(post, llm)
    assert llm.chat_json.call_count == 1


def test_backfill_retries_only_rejected_editions_with_length_feedback():
    saved = {'ja': {'headline': '既存', 'summary': '既存の要約'}}
    post = Post('x', 1, 9, '既有標題', '既有摘要', translations=saved.copy())
    first = translations()
    first['en'] = {'headline': 'A' * 31, 'summary': 'B' * 91}
    corrected = {'headline': 'Traccar sync merged', 'summary': 'A merged PR adds automatic Traccar device creation during vehicle registration.'}
    second = {lang: {'headline': 'Unrequested change', 'summary': 'Do not replace saved text'} for lang in LANGUAGES}
    second['en'] = corrected
    llm = Mock()
    llm.chat_json.side_effect = [first, second]

    assert translate_post(post, llm)
    retry = json.loads(llm.chat_json.call_args_list[1].args[1])
    assert retry['languages'] == ['en']
    assert retry['headline'] == '既有標題'
    assert retry['validation_feedback']['en'] == {
        'errors': ['headline:length', 'summary:length'],
        'rejected_text': first['en'],
        'lengths': {'headline': 31, 'summary': 91},
        'limits': {'headline': 30, 'summary': 90},
    }
    assert post.translations['en'] == corrected
    assert post.translations['ja'] == saved['ja']
    assert post.translations['ko'] == first['ko']
    assert post.translations['zh-TW'] == first['zh-TW']
    assert not missing_languages(post)


def test_backfill_keeps_partial_success_if_retry_fails():
    post = Post('x', 1, 9, '既有標題', '既有摘要')
    llm = Mock()
    llm.chat_json.side_effect = [{'ko': translations()['ko']}, ValueError('invalid JSON')]
    assert translate_post(post, llm)
    assert post.translations == {'ko': translations()['ko']}
    assert missing_languages(post) == ['en', 'zh-TW', 'ja']
    assert llm.chat_json.call_count == 2


def test_backfill_recovers_after_first_request_error():
    post = Post('x', 1, 9, '既有標題', '既有摘要')
    llm = Mock()
    llm.chat_json.side_effect = [ValueError('invalid JSON'), translations()]
    assert translate_post(post, llm)
    assert not missing_languages(post)
    assert llm.chat_json.call_count == 2


def test_backfill_dates_use_original_source_day_across_publication_midnight():
    source_ts=datetime(2026,9,16,16,59,tzinfo=TZ).timestamp()
    published_at=datetime(2026,9,17,0,7,tzinfo=TZ).timestamp()
    saved_ja={'headline':'保存済みのタイトル','summary':'保存済みの要約'}
    post=Post('x',published_at,9,'今晚小松討論活動成效','今天晚上討論活動成果',
              translations={'ja':saved_ja.copy()},delivery={'telegram':123})
    llm=Mock()
    bad=translations();bad['en']['headline']='x'*31
    llm.chat_json.side_effect=[bad,{'en':{'headline':'2026-09-16 meetup','summary':'The meetup discusses activity results'}}]
    assert translate_post(post,llm,source_ts=source_ts)
    assert llm.chat_json.call_count==2
    for call in llm.chat_json.call_args_list:
        payload=json.loads(call.args[1])
        assert payload['headline']=='2026-09-16小松討論活動成效'
        assert payload['summary']=='2026-09-16討論活動成果'
        assert payload['metadata']=={'source_time':'2026-09-16T16:59:00+08:00','timezone':'Asia/Taipei',
                                     'previous_week_start':'2026-09-07','previous_week_end':'2026-09-13'}
    assert post.headline=='今晚小松討論活動成效'
    assert post.summary=='今天晚上討論活動成果'
    assert post.translations['ja']==saved_ja
    assert post.delivery=={'telegram':123}


@pytest.mark.parametrize('wrong,right', [
    ('Meetup on 2026-09-19','Meetup on 2026-09-16'),
    ('Meetup on 2027-09-16','Meetup on 2026-09-16'),
    ('9月19日的小松','9月16日的小松'),
    ('9월 19일 모임','9월 16일 모임'),
    ('Meetup on September 19','Meetup on September 16'),
    ('Meetup on 19 Sept','Meetup on 16 Sept'),
    ('Meetup on Sept 16–19','Meetup on Sept 16'),
])
def test_backfill_rejects_invented_localized_date_then_saves_correct_date(wrong,right):
    source_ts=datetime(2026,9,16,16,59,tzinfo=TZ).timestamp()
    post=Post('x',source_ts+30000,9,'今晚小松討論活動成效','今天晚上討論活動成果')
    llm=Mock()
    llm.chat_json.side_effect=[{'en':{'headline':'Meetup','summary':wrong}},
                               {'en':{'headline':'Meetup','summary':right}}]
    assert translate_post(post,llm,languages=['en'],source_ts=source_ts)
    assert post.translations['en']['summary']==right
    retry=json.loads(llm.chat_json.call_args_list[1].args[1])
    assert 'calendar_date_not_in_source_text' in retry['validation_feedback']['en']['errors']


@pytest.mark.parametrize('summary', ['Community meetup in 2026','The meetup reviews activity results',
                                    'Meetup on September 16, 2026'])
def test_backfill_date_guard_accepts_omission_year_only_and_faithful_full_date(summary):
    source_ts=datetime(2026,9,16,16,59,tzinfo=TZ).timestamp()
    post=Post('x',source_ts,9,'今晚小松','今天晚上討論活動成果')
    llm=Mock();llm.chat_json.return_value={'en':{'headline':'Meetup','summary':summary}}
    assert translate_post(post,llm,languages=['en'],source_ts=source_ts)
    assert llm.chat_json.call_count==1


def test_backfill_date_guard_preserves_known_range_endpoints():
    source_ts=datetime(2026,9,16,16,59,tzinfo=TZ).timestamp()
    post=Post('x',source_ts,9,'9/16–18工作坊','工作坊於9/16至18日舉辦')
    llm=Mock();llm.chat_json.return_value={'en':{'headline':'Workshop','summary':'Workshop on Sept 16–18'}}
    assert translate_post(post,llm,languages=['en'],source_ts=source_ts)
    assert llm.chat_json.call_count==1


def test_backfill_invalid_date_never_saved_after_retry_budget_exhausted():
    source_ts=datetime(2026,9,16,16,59,tzinfo=TZ).timestamp()
    post=Post('x',source_ts,9,'今晚小松','今天晚上討論活動成果')
    llm=Mock();llm.chat_json.return_value={'en':{'headline':'Meetup','summary':'Meetup on September 19'}}
    assert not translate_post(post,llm,languages=['en'],source_ts=source_ts)
    assert post.translations=={}
    assert llm.chat_json.call_count==2


def test_backfill_previous_week_range_is_grounded_only_when_source_mentions_it():
    source_ts=datetime(2026,9,16,16,59,tzinfo=TZ).timestamp()
    post=Post('x',source_ts,9,'今晚小松','今晚討論上週活動成果')
    llm=Mock()
    llm.chat_json.side_effect=[{'en':{'headline':'Meetup','summary':'Reviewing activities from Sept 7–25'}},
                               {'en':{'headline':'Meetup','summary':'Reviewing activities from Sept 7–13'}}]
    assert translate_post(post,llm,languages=['en'],source_ts=source_ts)
    assert post.translations['en']['summary']=='Reviewing activities from Sept 7–13'
    retry=json.loads(llm.chat_json.call_args_list[1].args[1])
    assert 'calendar_date_not_in_source_text' in retry['validation_feedback']['en']['errors']
    unrelated=Post('y',source_ts,9,'今晚小松','今天晚上討論活動成果')
    llm=Mock();llm.chat_json.return_value={'en':{'headline':'Meetup','summary':'Reviewing activities from Sept 7–13'}}
    assert not translate_post(unrelated,llm,languages=['en'],source_ts=source_ts)
    assert unrelated.translations=={}


def test_backfill_does_not_invent_calendar_date_for_undated_source():
    source_ts=datetime(2026,9,16,16,59,tzinfo=TZ).timestamp()
    post=Post('x',source_ts,9,'資料集發布','新資料集提供學區邊界資訊')
    llm=Mock();llm.chat_json.return_value={'en':{'headline':'Dataset released','summary':'Dataset released on September 16'}}
    assert not translate_post(post,llm,languages=['en'],source_ts=source_ts)
    assert post.translations=={}


def test_backfill_repairs_invalid_saved_editions_but_bounds_retries():
    post = Post('x', 1, 9, '既有標題', '既有摘要',
                translations={**translations(), 'en': {'headline': 'x' * 31, 'summary': 'Valid summary'}})
    llm = Mock()
    llm.chat_json.return_value = {'en': {'headline': 'x' * 31, 'summary': 'Valid summary'}}
    assert missing_languages(post) == ['en']
    assert not translate_post(post, llm)
    assert llm.chat_json.call_count == 2
    assert post.translations['en']['headline'] == 'x' * 31
    # Subsequent attempts can recover without overwriting any valid saved edition.
    llm.chat_json.return_value = translations()
    assert translate_post(post, llm)
    assert not missing_languages(post)


def test_backfill_retries_malformed_response_and_reports_non_text_fields():
    post = Post('x', 1, 9, '既有標題', '既有摘要')
    llm = Mock()
    llm.chat_json.side_effect = [[], {'en': {'headline': ['invalid'], 'summary': 'Fine'}}]
    assert not translate_post(post, llm, ['en'])
    retry = json.loads(llm.chat_json.call_args_list[1].args[1])
    assert retry['validation_feedback']['en']['errors'] == [
        'headline:nonempty_string_required', 'summary:nonempty_string_required']
    assert post.translations == {}
    assert llm.chat_json.call_count == 2


def test_backfill_cli_does_not_send_or_change_delivery(tmp_path, monkeypatch):
    import rep0rter.cli as cli
    cfg = Config(data_dir=tmp_path, ai_base_url='https://example.test', ai_api_key='test', ai_model='test')
    with Store(cfg.db_path) as store:
        store.upsert_events([candidate().event])
        store.add_post(Post('slack:C:1', 2, 8, '標題', '摘要', delivery={'telegram': 123}))
    llm = Mock()
    llm.chat_json.return_value = translations()
    monkeypatch.setattr(cli, 'LLM', lambda _: llm)
    monkeypatch.setattr(cli.site_publisher, 'build', lambda *_: None)
    def forbidden(*_):
        raise AssertionError('translation must not publish')
    monkeypatch.setattr(cli, 'deliver_pending', forbidden)
    assert cmd_translate(cfg, SimpleNamespace(limit=1, language=None)) == 0
    with Store(cfg.db_path) as store:
        post, _, _ = store.recent_posts()[0]
        assert post.delivery == {'telegram': 123}
        assert set(post.translations) == set(LANGUAGES)
    assert cmd_translate(cfg, SimpleNamespace(limit=1, language=None)) == 0
    assert llm.chat_json.call_count == 1
