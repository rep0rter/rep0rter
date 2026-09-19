"""Facets keep stable identities and conservative, locale-independent topics."""
from dataclasses import replace
import hashlib

import pytest

from rep0rter.publishers.site import _filter_metadata
from rep0rter.store import Container, Event
from rep0rter.topics import classify_topics, localized_topics


@pytest.mark.parametrize("text", [
    "Community workshop: open data API tutorial and CTF",
    "社群工作坊：開放資料 API 教學與資安",
    "コミュニティのワークショップ：オープンデータ API チュートリアルとセキュリティ",
    "커뮤니티 워크숍: 공개 데이터 API 튜토리얼 및 보안",
])
def test_topics_support_multiple_explicit_signals_in_every_edition(text):
    assert classify_topics(text) == ["events", "development", "open-data", "education", "community", "security"]


@pytest.mark.parametrize("text", [
    "Taiwan capital, rapid change, APIary and eventful weekend",
    "A private conversation about everyday life",
    "Read https://github.com/community/workshop/security for the announcement",
    "",
])
def test_topics_do_not_match_inside_latin_words_or_urls(text):
    assert classify_topics(text) == ["other"]


def test_topic_ids_stay_stable_when_labels_are_localized():
    ids = classify_topics("ＣＴＦ", "Open   data course")
    assert ids == ["open-data", "education", "security"]
    labels = []
    for language in ("en", "zh-TW", "ja", "ko"):
        topics = localized_topics(ids, language)
        assert [topic["id"] for topic in topics] == ids
        labels.append([topic["label"] for topic in topics])
    assert len({tuple(row) for row in labels}) == 4
    assert localized_topics(["other"], "zh-TW") == [{"id": "other", "label": "其他"}]


def test_author_identity_survives_display_name_and_channel_changes():
    event = Event("event-1", "slack", "message", "channel-a", 1,
                  author_id="account-123", author_name="Alice")
    first = _filter_metadata(event, None)
    renamed = _filter_metadata(replace(event, author_name="Alice Chen", container_id="channel-b"), None)
    other_source = _filter_metadata(replace(event, source="discourse"), None)
    other_account = _filter_metadata(replace(event, author_id="account-456"), None)
    assert first["filter_author"] == renamed["filter_author"]
    assert len({first["filter_author"], other_source["filter_author"], other_account["filter_author"]}) == 3
    assert first["filter_author_label"] == "Alice"
    assert renamed["filter_author_label"] == "Alice Chen"
    assert event.author_id not in str(first)


def test_missing_author_ids_do_not_merge_names_across_sources_or_channels():
    event = Event("event-1", "slack", "message", "channel-a", 1, author_name="Alex")
    base = _filter_metadata(event, None)
    same_name_other_channel = _filter_metadata(replace(event, container_id="channel-b"), None)
    same_name_other_source = _filter_metadata(replace(event, source="discourse"), None)
    assert len({row["filter_author"] for row in (base, same_name_other_channel, same_name_other_source)}) == 3
    assert _filter_metadata(event, None) == base


def test_source_facet_matches_existing_permanent_source_identifier_and_visible_fallback():
    event = Event("event-1", "slack", "message", "channel-a", 1)
    container = Container("channel-a", "slack", "Public community")
    metadata = _filter_metadata(event, container)
    assert metadata["filter_source"] == hashlib.sha256(b"slack:channel-a").hexdigest()[:20]
    assert metadata["filter_author_label"] == container.name
    assert _filter_metadata(event, None)["filter_author_label"] == event.container_id
