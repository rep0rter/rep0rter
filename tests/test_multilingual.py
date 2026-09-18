import json
from types import SimpleNamespace
from unittest.mock import Mock

from rep0rter.cli import cmd_translate
from rep0rter.config import Config
from rep0rter.i18n import LANGUAGES, post_text
from rep0rter.reporter import Candidate, translate_post, write_multilingual_item
from rep0rter.store import Event, Post, Store


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
    assert sent['languages'] == ['zh-TW', 'ko', 'en']
    assert post.translations['ja']['headline'] == '既存'
    assert post.headline == '既有標題'
    assert not translate_post(post, llm)
    assert llm.chat_json.call_count == 1


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
