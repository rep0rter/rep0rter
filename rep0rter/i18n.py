"""Shared language identifiers, UI copy, and honest translation fallbacks."""

LANGUAGES = {"zh-TW": "繁體中文", "ko": "한국어", "ja": "日本語", "en": "English"}


def page_name(language: str) -> str:
    return "index.html" if language == "zh-TW" else f"index.{language}.html"


def feed_name(language: str) -> str:
    return "feed.xml" if language == "zh-TW" else f"feed.{language}.xml"


def post_text(post, language: str) -> tuple[str, str, bool]:
    text = post.translations.get(language)
    if isinstance(text, dict) and text.get("headline") and text.get("summary"):
        return text["headline"], text["summary"], True
    return post.headline, post.summary, False


COPY = {
    "zh-TW": {
        "title": "公民科技社群動態", "tagline": "讓彼此的進展被看見，讓下一次協作更容易。",
        "intro": "來自社群現場的消息，附上原文與參與線索。",
        "language": "閱讀語言", "code": "原始碼", "archive": "Slack 存檔",
        "empty": "還沒有報導。第一則社群消息正在路上。", "source": "查看來源",
        "original": "展開原文", "published": "原文發布", "download": "下載圖卡",
        "image_alt": "原文摘錄圖卡，作者或來源：", "missing": "此語言版本尚未完成，暫時顯示已儲存的文字。",
        "footer": "摘要由程式與語言模型整理，請以連結中的原文為準。圖卡保留原文語言，長文會節錄。",
        "stats": "{events} 則事件 · {posts} 篇報導", "updated": "更新時間（台北）",
        "optout": "不希望某則消息被報導？請在 g0v Slack 的 #rep0rter 告訴我們。",
        "card_label": "原文摘錄", "read": "閱讀消息",
    },
    "ko": {
        "title": "시빅테크 커뮤니티 소식", "tagline": "서로의 진전을 발견하고, 다음 협업을 시작하세요.",
        "intro": "커뮤니티의 소식과 원문, 참여 방법을 함께 전합니다.",
        "language": "읽기 언어", "code": "소스 코드", "archive": "Slack 아카이브",
        "empty": "아직 소식이 없습니다. 첫 소식을 기다려 주세요.", "source": "출처 보기",
        "original": "원문 펼치기", "published": "원문 게시", "download": "이미지 다운로드",
        "image_alt": "원문 발췌 이미지. 작성자 또는 출처: ", "missing": "이 언어의 번역이 아직 없어 저장된 내용을 표시합니다.",
        "footer": "프로그램과 언어 모델이 요약합니다. 정확한 내용은 원문을 확인하세요. 이미지는 원문 언어를 유지하며 긴 글은 발췌합니다.",
        "stats": "이벤트 {events}개 · 소식 {posts}개", "updated": "업데이트 (타이베이 시간)",
        "optout": "소개를 원하지 않는 소식은 g0v Slack #rep0rter에 알려 주세요.",
        "card_label": "원문 발췌", "read": "소식 읽기",
    },
    "ja": {
        "title": "シビックテックのコミュニティニュース", "tagline": "お互いの進展を知り、次の協働へ。",
        "intro": "コミュニティの現場から、原文と参加のきっかけを届けます。",
        "language": "表示言語", "code": "ソースコード", "archive": "Slack アーカイブ",
        "empty": "まだニュースはありません。最初のお知らせをお待ちください。", "source": "情報源を見る",
        "original": "原文を表示", "published": "原文の投稿日", "download": "画像をダウンロード",
        "image_alt": "原文の抜粋画像。投稿者または情報源：", "missing": "この言語の翻訳はまだありません。保存済みの文章を表示しています。",
        "footer": "プログラムと言語モデルが要約しています。正確な内容は原文をご確認ください。画像は原文の言語を保ち、長文は抜粋します。",
        "stats": "{events} 件のイベント · {posts} 件のニュース", "updated": "更新日時（台北時間）",
        "optout": "掲載を希望しない場合は、g0v Slack の #rep0rter でお知らせください。",
        "card_label": "原文の抜粋", "read": "ニュースを読む",
    },
    "en": {
        "title": "Civic tech community news", "tagline": "See what others are building. Find your next collaboration.",
        "intro": "Updates from the community, with original sources and ways to take part.",
        "language": "Reading language", "code": "Source code", "archive": "Slack archive",
        "empty": "No stories yet. The first community update is on its way.", "source": "View source",
        "original": "Show original text", "published": "Originally posted", "download": "Download image",
        "image_alt": "Original excerpt card. Author or source: ", "missing": "This translation is not available yet. Showing the saved text.",
        "footer": "Summaries are prepared by software and language models. Refer to the linked original for accuracy. Images keep the original language; long messages are excerpted.",
        "stats": "{events} events · {posts} stories", "updated": "Updated (Taipei time)",
        "optout": "To request removal of a story, tell us in #rep0rter on g0v Slack.",
        "card_label": "Original excerpt", "read": "Read story",
    },
}
