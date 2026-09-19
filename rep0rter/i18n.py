"""Shared language identifiers, UI copy, and honest translation fallbacks."""

DEFAULT_LANGUAGE = "en"
LANGUAGES = {"en": "English", "zh-TW": "繁體中文", "ja": "日本語", "ko": "한국어"}


def page_name(language: str) -> str:
    return "index.html" if language == DEFAULT_LANGUAGE else f"index.{language}.html"


def feed_name(language: str) -> str:
    return "feed.xml" if language == DEFAULT_LANGUAGE else f"feed.{language}.xml"


def page_aliases(language: str) -> tuple[str, ...]:
    """Keep explicit English links shared before English became the default."""
    return ("index.en.html",) if language == DEFAULT_LANGUAGE else ()


def feed_aliases(language: str) -> tuple[str, ...]:
    return ("feed.en.xml",) if language == DEFAULT_LANGUAGE else ()


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
        "footer": "Summaries and English report images are prepared by rep0rter. Refer to the linked source for accuracy. Open the original text to see its excerpt image in the source language.",
        "stats": "{events} events · {posts} stories", "updated": "Updated (Taipei time)",
        "optout": "To request removal of a story, tell us in #rep0rter on g0v Slack.",
        "card_label": "Original excerpt", "read": "Read story",
        "report_card_label": "English report · Summary by rep0rter",
        "report_image_alt": "English report card. Source: ",
        "pending_headline": "English translation pending",
        "pending_summary": "An English version is not available yet. Open the original or choose another language.",
    },
}

for _language, _values in {
    'en': ('Post your project', 'Owner submission · Ownership self-declared'),
    'zh-TW': ('發布你的專案', '專案負責人投稿 · 身分由投稿者自行聲明'),
    'ja': ('プロジェクトを投稿', '運営者による投稿 · 所有権は自己申告'),
    'ko': ('프로젝트 게시', '프로젝트 운영자 게시물 · 소유권은 자체 신고'),
}.items():
    COPY[_language].update(zip(('submit_project', 'owner_submission'), _values))

for _language, _values in {
    'zh-TW': ('報導發布','修訂','相關來源','來源最後完整更新（台北）','這篇報導已撤回','內容已移除。第三方訂閱器與轉寄的副本可能仍存在。'),
    'ko': ('소식 게시','수정','관련 출처','모든 출처의 마지막 업데이트 (타이베이)','이 소식은 철회되었습니다','내용이 삭제되었습니다. 외부 구독기나 전달된 사본은 남아 있을 수 있습니다.'),
    'ja': ('記事公開','改訂','関連情報源','全情報源の最終更新（台北）','この記事は撤回されました','内容は削除されました。外部の購読サービスや転送されたコピーは残る場合があります。'),
    'en': ('Reported','Revision','Related sources','Sources last fully updated (Taipei)','This story has been withdrawn','The content has been removed. Copies in third-party readers and forwarded messages may remain.'),
}.items():
    COPY[_language].update(zip(('reported','revision','sources','healthy','withdrawn','withdrawal_notice'),_values))

for _language, _text in {
    'zh-TW': '部分來源更新較慢，消息可能尚未收齊。',
    'ko': '일부 출처의 업데이트가 늦어져 최신 소식이 아직 반영되지 않았을 수 있습니다.',
    'ja': '一部の情報源の更新が遅れているため、最新のニュースがまだ反映されていない場合があります。',
    'en': 'Some sources are updating slowly. Recent news may not appear yet.',
}.items():
    COPY[_language]['collection_delayed'] = _text

for _language, _values in {
    'zh-TW': ('這篇報導已有後續修訂，請以最新版本為準。', '閱讀最新報導', '舊版報導（已修訂）'),
    'ko': ('이 소식은 수정되었습니다. 최신 내용을 확인해 주세요.', '최신 소식 읽기', '이전 소식 (수정됨)'),
    'ja': ('この記事には改訂版があります。最新の内容をご確認ください。', '最新版を読む', '旧版の記事（改訂済み）'),
    'en': ('This story has been revised. Please refer to the latest version.', 'Read the latest story', 'Previous report (superseded)'),
}.items():
    COPY[_language].update(zip(('superseded', 'latest_report', 'previous_report'), _values))
