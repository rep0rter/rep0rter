import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from rep0rter.collectors import slack_archive as archive

FIXTURES = Path(__file__).parent / "fixtures" / "slack_archive"


def fixture(name):
    return (FIXTURES / name).read_text()


def test_channels_public_metadata_and_timezone(monkeypatch):
    monkeypatch.setattr(archive, "_get", lambda *a, **kw: Mock(text=fixture("home_public.html")))
    channels = archive.fetch_channels(Mock())
    assert len(channels) == 1
    channel = channels[0]
    assert (channel.id, channel.topic, channel.num_members, channel.total_messages) == ("CPUBLIC", "Tools & people", 0, 1234)
    assert channel.last_posted_at.isoformat() == "2026-09-18T19:30:00+08:00"


def test_channels_bad_rows_isolated(monkeypatch, caplog):
    monkeypatch.setattr(archive, "_get", lambda *a, **kw: Mock(text=fixture("home_malformed_row.html")))
    channels = archive.fetch_channels(Mock())
    assert [c.id for c in channels] == ["CDATE", "CPUBLIC"]
    assert channels[0].last_posted_at is None
    assert "degraded" in caplog.text


def test_changed_homepage_fails(monkeypatch):
    monkeypatch.setattr(archive, "_get", lambda *a, **kw: Mock(text=fixture("home_empty_or_changed.html")))
    with pytest.raises(RuntimeError, match="no valid public channels"):
        archive.fetch_channels(Mock())


def run_pages(monkeypatch, pages):
    get = Mock(side_effect=[Mock(json=Mock(return_value=p)) for p in pages])
    monkeypatch.setattr(archive, "_get", get)
    messages = list(archive.iter_messages(Mock(), "CPUBLIC", datetime.fromtimestamp(1789571700, timezone.utc)))
    return messages, get


def test_duplicate_pages_merge_edits_and_continue_short_pages(monkeypatch, caplog):
    messages, get = run_pages(monkeypatch, json.loads(fixture("pages_duplicates.json")))
    assert len(messages) == 3
    assert messages[0]["text"] == "edited notice"
    assert messages[0]["user"]["id"] == "UA"
    assert messages[0]["reply_count"] == 1
    assert get.call_args_list[1].kwargs["before"] == "1789571776.383129"
    assert all("after" not in call.kwargs for call in get.call_args_list)
    assert "ambiguous empty page" in caplog.text


def test_repeated_page_fails_without_looping(monkeypatch):
    page = {"messages": [{"ts": "1789571777.383129", "text": "test"}]}
    with pytest.raises(RuntimeError, match="no progress"):
        run_pages(monkeypatch, [page, page])


@pytest.mark.parametrize("page", [0, [], {}, {"messages": "bad"}, {"messages": [{"ts": "NaN"}]}])
def test_unavailable_or_malformed_channel_fails(monkeypatch, page):
    with pytest.raises(RuntimeError):
        run_pages(monkeypatch, [page])


def test_unsorted_page_does_not_drop_new_messages_after_boundary(monkeypatch):
    messages, _ = run_pages(monkeypatch, [{"messages": [{"ts": "1789571600.0"}, {"ts": "1789571777.383129"}]}])
    assert len(messages) == 1


def test_share_preserves_source_and_author_without_impersonation():
    raw = json.loads(fixture("messages_share.json"))[0]
    raw["attachments"] *= 2
    event, author = archive.to_event(raw, "CSHARED", {"CPUBLIC"})
    assert event.text.count("Published a public school district dataset.") == 1
    assert "Quoted public Slack message by Original author" in event.text
    assert event.author_id == "slack:USHARER"
    assert event.meta["references"][0]["event_id"] == "slack:CPUBLIC:1789571767.493169"
    assert event.meta["references"][0]["author_id"] == "slack:UAUTHOR"
    assert event.meta["avatar_url"] == "https://example.test/avatar.png"
    assert author[0] == "slack:USHARER"


def test_unverified_share_withheld_and_flagged():
    raw = json.loads(fixture("messages_share.json"))[0]
    raw["html_content"] = "should not leak quote from HTML"
    event, _ = archive.to_event(raw, "CSHARED")
    assert event.text == event.html == ""
    assert event.meta["content_status"] == "unverified_reference"
    assert event.meta["references"][0]["public"] is False


def test_share_top_level_fallback_keeps_quote_attribution():
    raw = json.loads(fixture("messages_share.json"))[0]
    raw["text"] = raw["attachments"][0]["text"]
    event, _ = archive.to_event(raw, "CSHARED", {"CPUBLIC"})
    assert event.text.startswith("[Quoted public Slack message by Original author")
    assert event.text.count(raw["text"]) == 1
    unverified, _ = archive.to_event(raw, "CSHARED")
    assert unverified.text == ""


def test_blocks_fallback_and_duplicate_attachment():
    raw = {"ts": "1789571777.383129", "blocks": [{"type": "rich_text", "elements": [
        {"type": "rich_text_section", "elements": [{"type": "text", "text": "A new "},
         {"type": "link", "url": "https://example.test", "text": "dataset"}]}]}],
        "attachments": [{"text": "A new <https://example.test|dataset>", "fallback": "duplicate"}]}
    event, _ = archive.to_event(raw, "CPUBLIC")
    assert event.text == "A new <https://example.test|dataset>"


@pytest.mark.parametrize("user", [None, "USTRING", {"id": "UPROFILE", "profile": None}])
def test_mixed_user_shapes_and_thread_identity(user):
    event, _ = archive.to_event({"ts": "1789571777.383129", "thread_ts": "1789571777.383129", "user": user,
        "reactions": [None, {"name": "heart", "count": "2"}], "files": [None, {}]}, "CPUBLIC")
    assert event.kind == "message"
    assert event.reaction_count == 2
    assert event.meta["avatar_url"] == ""
    reply, _ = archive.to_event({"ts": "1789571778.383129", "thread_ts": "1789571777.383129"}, "CPUBLIC")
    assert reply.parent_id == event.id


def test_media_and_deleted_content_are_distinct():
    image, _ = archive.to_event({"ts": "1789571777.383129", "files": [{"mimetype": "image/png"}]}, "CPUBLIC")
    deleted, _ = archive.to_event({"ts": "1789571777.383129", "subtype": "message_deleted", "text": "old text"}, "CPUBLIC")
    assert image.meta["content_status"] == "media_only"
    assert deleted.text == ""
    assert deleted.meta["content_status"] == "deleted"
    image_block, _ = archive.to_event({"ts": "1789571777.383129", "blocks": [{"type": "image", "alt_text": "Not a factual text post"}]}, "CPUBLIC")
    assert image_block.text == ""
    assert image_block.meta["content_status"] == "media_only"


def test_http_failure_has_bounded_retries(monkeypatch):
    session = Mock()
    session.get.side_effect = requests.Timeout("offline synthetic timeout")
    sleep = Mock()
    monkeypatch.setattr(archive.time, "sleep", sleep)
    with pytest.raises(RuntimeError, match="giving up"):
        archive._get(session, "https://example.test")
    assert session.get.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [1, 2, 4]


@pytest.mark.parametrize('size',['32','512','1024','original'])
def test_avatar_sizes_and_default_flag(size):
    raw={'ts':'1789571777.383129','user':{'id':'U','profile':{'image_'+size:'https://example.test/avatar','is_custom_image':True}}}
    assert archive.to_event(raw,'C')[0].meta['avatar_url']=='https://example.test/avatar'
    raw['user']['profile']['is_custom_image']=False
    event,_=archive.to_event(raw,'C')
    assert event.meta['avatar_url']=='' and event.meta['avatar_is_custom'] is False
