from rep0rter.slack_text import mention_names_from_html, rule_headline, rule_summary, to_plain


def test_mentions_recovered_from_rendered_html():
    text = "<@U1> <@U2> 我打算把這個專案復活"
    html = "<b>@sean8321</b> <b>@h1030035</b> 我打算把這個專案復活"
    names = mention_names_from_html(text, html)
    assert names == {"U1": "sean8321", "U2": "h1030035"}
    assert to_plain(text, names) == "@sean8321 @h1030035 我打算把這個專案復活"


def test_mentions_ignored_when_counts_disagree():
    assert mention_names_from_html("<@U1> hi", "<b>@a</b> <b>@b</b>") == {}


def test_headline_skips_greeting_and_mentions():
    text = "@sean8321 @h1030035 大家好，這週六的黑客松開放報名了！詳細資訊請看 活動頁 (https://g0v.tw/x)。"
    assert rule_headline(text) == "這週六的黑客松開放報名了"
    assert rule_summary(text) == "這週六的黑客松開放報名了！ 詳細資訊請看 活動頁。"


def test_headline_strips_raw_mentions_with_spaced_names():
    text = "<@U1> <@U2> 我打算在這幾天把這個專案復活，應該會在週末撿回來看看"
    names = {"U1": "Sky Hong", "U2": "Wolf Yuan"}
    assert rule_headline(text, names) == "我打算在這幾天把這個專案復活，應該會在週末撿回來看看"
    assert rule_summary("<@U1> 找 <@U2> 討論", names) == "找 @Wolf Yuan 討論"


def test_headline_cuts_at_punctuation_when_too_long():
    text = "10/02 週五下午線上講座：人工智慧如何為災害風險管理帶來新的契機與可能性？講者：國家災害防救科技中心"
    head = rule_headline(text)
    assert head.endswith("…")
    assert len(head) <= 31
    assert "線上講座" in head


def test_summary_without_sentences_falls_back_to_excerpt():
    assert rule_summary("ok") == "ok"
    assert rule_headline("https://only.a.link/") == ""
