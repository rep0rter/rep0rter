from rep0rter.slack_text import excerpt, links_in, to_plain


def test_links_and_mentions():
    text = "hi <@U1> 看看 <https://a.example/x|這個> 和 <https://b.example> &amp; <#C1|general> <!here>"
    out = to_plain(text, {"U1": "小明"})
    assert out == "hi @小明 看看 這個 (https://a.example/x) 和 https://b.example & #general @here"
    assert links_in(text) == ["https://a.example/x", "https://b.example"]


def test_unknown_user_is_anonymised():
    assert to_plain("<@U999> 你好") == "@某人 你好"


def test_excerpt_trims():
    assert excerpt("a" * 200, 10) == "a" * 9 + "…"
    assert excerpt("short   text\nhere") == "short text here"
