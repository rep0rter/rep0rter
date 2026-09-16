from rep0rter.publishers.telegram import MAX_LEN, format_item, format_messages
from rep0rter.reporter import Candidate
from rep0rter.store import Container, Event, Post


def _item(i: int, size: int = 100):
    e = Event(id=f"slack:C:{i}", source="slack", kind="message", container_id="slack:C", ts=1.0,
              author_name="a<b>", url="https://x/?a=1&b=2", reply_count=2)
    c = Candidate(event=e, container=Container(id="slack:C", source="slack", name="general"), score=9)
    p = Post(event_id=e.id, published_at=1.0, score=9, headline="標題 <tag>", summary="x" * size)
    return c, p


def test_format_item_escapes_html():
    out = format_item(*_item(1))
    assert "&lt;tag&gt;" in out and "a&lt;b&gt;" in out
    assert 'href="https://x/?a=1&amp;b=2"' in out
    assert "💬 2" in out


def test_messages_split_at_limit():
    items = [_item(i, size=1500) for i in range(5)]
    msgs = format_messages(items)
    assert len(msgs) >= 2
    assert all(len(m) <= MAX_LEN for m in msgs)
