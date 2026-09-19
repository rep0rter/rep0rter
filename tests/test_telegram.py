from dataclasses import replace

import pytest

from rep0rter.config import Config
from rep0rter.i18n import page_name
from rep0rter.publishers.telegram import MAX_LEN, format_caption, format_item, format_messages
from rep0rter.reporter import Candidate
from rep0rter.store import Container, Event, Post


def _item(i: int, size: int = 100):
    e = Event(id=f"slack:C:{i}", source="slack", kind="message", container_id="slack:C", ts=1.0,
              author_name="a<b>", url="https://x/?a=1&b=2", reply_count=2)
    c = Candidate(event=e, container=Container(id="slack:C", source="slack", name="general"), score=9)
    p = Post(event_id=e.id, published_at=1.0, score=9, headline="標題 <tag>", summary="原始中文",
             translations={"en": {"headline": "English <tag>", "summary": "x" * size}})
    return c, p


def test_format_item_escapes_html():
    out = format_item(*_item(1))
    assert "&lt;tag&gt;" in out and "a&lt;b&gt;" in out
    assert 'href="https://x/?a=1&amp;b=2"' in out
    assert "💬 2" in out
    assert "English &lt;tag&gt;" in out and "View source" in out
    assert "標題" not in out and "原始中文" not in out


def test_messages_split_at_limit():
    items = [_item(i, size=1500) for i in range(5)]
    msgs = format_messages(items)
    assert len(msgs) >= 2
    assert all(len(m) <= MAX_LEN for m in msgs)


def test_caption_english_navigation_and_required_translation():
    candidate, post = _item(1)
    post.id = 7
    cfg = replace(Config(), telegram_language="en", site_url="https://example.org")
    caption = format_caption(cfg, candidate, post)
    labels = ["English", "Chinese", "Japanese", "Korean"]
    assert [caption.index(f">{label}</a>") for label in labels] == sorted(caption.index(f">{label}</a>") for label in labels)
    for language in ("en", "zh-TW", "ja", "ko"):
        assert f'/posts/7/{page_name(language)}' in caption
    assert "繁體中文" not in caption and "日本語" not in caption and "한국어" not in caption
    post.translations = {}
    with pytest.raises(ValueError, match="translation is not available"):
        format_caption(cfg, candidate, post)
    with pytest.raises(ValueError, match="translation is not available"):
        format_item(candidate, post)


def test_explicit_chinese_preview_remains_available():
    candidate, post = _item(1)
    assert "原始中文" in format_item(candidate, post, "zh-TW")
