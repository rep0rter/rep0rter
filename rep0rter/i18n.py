"""Shared language identifiers, UI copy, and honest translation fallbacks."""
import re

DEFAULT_LANGUAGE = "en"
LANGUAGES = {"en": "English", "zh-TW": "繁體中文", "ja": "日本語", "ko": "한국어"}
# Names for prompts: unambiguous to a model regardless of the prompt's own language.
LANGUAGE_PROMPT_NAMES = {"en": "English", "zh-TW": "Taiwan Traditional Chinese", "ja": "Japanese", "ko": "Korean"}

_URL = re.compile(r"https?://\S+")
_HANGUL = re.compile("[ᄀ-ᇿ㄰-㆏가-힣]")
_KANA = re.compile("[぀-ヿㇰ-ㇿ]")
_HAN = re.compile("[㐀-䶿一-鿿]")
_LATIN = re.compile("[A-Za-z]")


def detect_language(text: str) -> str | None:
    """Guess which of LANGUAGES a text is written in from its scripts, or None if unclear.

    Script counts only: Hangul means Korean, kana means Japanese, otherwise Han means
    Chinese (reported as zh-TW, the edition this project writes; Simplified is not
    told apart) and Latin means English. Kana-free Japanese made only of kanji is
    indistinguishable from Chinese. Digits, URLs and punctuation carry no signal.
    """
    text = _URL.sub(" ", text or "")
    hangul, kana, han, latin = (len(p.findall(text)) for p in (_HANGUL, _KANA, _HAN, _LATIN))
    total = hangul + kana + han + latin
    if not total:
        return None
    if hangul / total >= 0.3:
        return "ko"
    # Quoted Japanese names must not override predominantly Latin text.
    # Within CJK text, require enough kana to distinguish it from Chinese.
    if kana >= 3 and (kana + han) / total >= 0.3 and kana / (kana + han) >= 0.2:
        return "ja"
    if han / total >= 0.3:
        return "zh-TW"
    if latin / total >= 0.7:
        return "en"
    return None


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
        "language": "閱讀語言", "code": "原始碼", "archive": "g0v Slack 存檔",
        "empty": "還沒有報導。第一則社群消息正在路上。", "source": "查看來源",
        "original": "展開原文", "published": "原文發布", "download": "下載圖卡",
        "image_alt": "原文摘錄圖卡，作者或來源：", "missing": "此語言版本尚未完成，暫時顯示已儲存的文字。",
        "footer": "摘要由程式與語言模型整理，請以連結中的原文為準。圖卡保留原文語言，長文會節錄。",
        "stats": "{events} 則事件 · {posts} 篇報導", "updated": "更新時間（台北）",
        "optout": "不希望某則消息被報導？請在 g0v Slack 的 #rep0rter 告訴我們。",
        "card_label": "原文摘錄", "read": "閱讀消息", "view_details": "查看詳情",
        "appearance": "顯示模式", "theme_light": "淺色模式", "theme_dark": "深色模式", "theme_system": "跟隨裝置設定",
        "brand_note": "串起社群的每一步", "skip_content": "跳至主要內容",
        "eyebrow": "來自 g0v、Code for Korea、Code for Japan 等公民科技社群", "hero_title": "看見社群進展。", "hero_accent": "找到協作起點。",
        "explore": "看看最新消息", "subscribe": "訂閱 RSS", "hero_note": "摘要、原文與參與線索，一起閱讀。",
        "story_heading": "社群報導", "source_heading": "依來源閱讀", "detail_title": "消息與原文",
        "detail_intro": "先讀摘要，再從原文了解完整脈絡。", "source_intro": "這個來源的消息，整理在這裡。",
        "feed_eyebrow": "社群正在發生", "latest": "最新消息",
        "search_label": "搜尋消息", "search_placeholder": "搜尋主題、人物或來源", "clear_search": "清除搜尋",
        "search_results": "找到 {count} 則消息", "empty_title": "期待下一則消息",
        "no_results": "找不到符合的消息", "search_help": "試試其他關鍵字，或清除搜尋查看所有消息。",
        "about": "關於 rep0rter", "about_eyebrow": "為社群而整理", "about_title": "從消息，走向交流。",
        "about_body": "把分散的社群消息整理在一起，讓你從摘要了解近況，再回到原文深入閱讀。",
        "principle_sources": "保留原文與來源線索", "principle_languages": "提供四種閱讀語言", "principle_people": "發現社群裡的人與專案",
        "subscribe_title": "在習慣的地方讀消息", "subscribe_body": "將 RSS 加入你的閱讀器，接收這個語言版本的更新。",
        "aside_note": "摘要協助你掌握重點；完整內容請以原文為準。",
        "resources": "相關資源", "back_home": "回到所有消息", "replies": "回覆數", "view_image": "檢視圖卡", "close_image": "關閉圖片",
        "image_loading": "圖片載入中…", "image_error": "圖片暫時無法載入，請關閉後重試。",
    },
    "ko": {
        "title": "시빅테크 커뮤니티 소식", "tagline": "서로의 진전을 발견하고, 다음 협업을 시작하세요.",
        "intro": "커뮤니티의 소식과 원문, 참여 방법을 함께 전합니다.",
        "language": "읽기 언어", "code": "소스 코드", "archive": "g0v Slack 아카이브",
        "empty": "아직 소식이 없습니다. 첫 소식을 기다려 주세요.", "source": "출처 보기",
        "original": "원문 펼치기", "published": "원문 게시", "download": "이미지 다운로드",
        "image_alt": "원문 발췌 이미지. 작성자 또는 출처: ", "missing": "이 언어의 번역이 아직 없어 저장된 내용을 표시합니다.",
        "footer": "프로그램과 언어 모델이 요약합니다. 정확한 내용은 원문을 확인하세요. 이미지는 원문 언어를 유지하며 긴 글은 발췌합니다.",
        "stats": "이벤트 {events}개 · 소식 {posts}개", "updated": "업데이트 (타이베이 시간)",
        "optout": "소개를 원하지 않는 소식은 g0v Slack #rep0rter에 알려 주세요.",
        "card_label": "원문 발췌", "read": "소식 읽기", "view_details": "자세히 보기",
        "appearance": "화면 모드", "theme_light": "라이트 모드", "theme_dark": "다크 모드", "theme_system": "기기 설정 따르기",
        "brand_note": "커뮤니티의 발걸음을 잇다", "skip_content": "본문으로 이동",
        "eyebrow": "g0v, Code for Korea, Code for Japan 등 시빅테크 커뮤니티의 소식", "hero_title": "소식을 나누고,", "hero_accent": "다음을 함께.",
        "explore": "최신 소식 보기", "subscribe": "RSS 구독", "hero_note": "요약부터 원문, 참여 방법까지 함께 살펴보세요.",
        "story_heading": "커뮤니티 소식", "source_heading": "출처별 소식", "detail_title": "소식과 원문",
        "detail_intro": "요약을 읽고, 원문에서 전체 맥락을 확인하세요.", "source_intro": "이 출처의 소식을 한곳에 모았습니다.",
        "feed_eyebrow": "커뮤니티의 지금", "latest": "최신 소식",
        "search_label": "소식 검색", "search_placeholder": "주제, 사람, 출처 검색", "clear_search": "검색 지우기",
        "search_results": "소식 {count}개를 찾았습니다", "empty_title": "다음 소식을 기다립니다",
        "no_results": "검색 결과가 없습니다", "search_help": "다른 검색어를 입력하거나 검색을 지워 모든 소식을 확인하세요.",
        "about": "rep0rter 소개", "about_eyebrow": "커뮤니티를 위한 소식 모음", "about_title": "소식에서 만남으로.",
        "about_body": "흩어진 커뮤니티 소식을 모아 전합니다. 요약으로 근황을 파악하고, 원문에서 더 자세히 알아보세요.",
        "principle_sources": "원문과 출처를 함께", "principle_languages": "네 가지 언어로 읽기", "principle_people": "커뮤니티의 사람과 프로젝트 발견",
        "subscribe_title": "익숙한 곳에서 읽으세요", "subscribe_body": "RSS를 리더에 추가하면 이 언어로 새 소식을 받아볼 수 있습니다.",
        "aside_note": "요약은 핵심을 파악하는 데 도움을 줍니다. 전체 내용은 원문을 확인하세요.",
        "resources": "관련 자료", "back_home": "모든 소식으로", "replies": "답글 수", "view_image": "이미지 보기", "close_image": "이미지 닫기",
        "image_loading": "이미지 불러오는 중…", "image_error": "이미지를 불러올 수 없습니다. 닫은 후 다시 시도해 주세요.",
    },
    "ja": {
        "title": "シビックテックのコミュニティニュース", "tagline": "お互いの進展を知り、次の協働へ。",
        "intro": "コミュニティの現場から、原文と参加のきっかけを届けます。",
        "language": "表示言語", "code": "ソースコード", "archive": "g0v Slack アーカイブ",
        "empty": "まだニュースはありません。最初のお知らせをお待ちください。", "source": "情報源を見る",
        "original": "原文を表示", "published": "原文の投稿日", "download": "画像をダウンロード",
        "image_alt": "原文の抜粋画像。投稿者または情報源：", "missing": "この言語の翻訳はまだありません。保存済みの文章を表示しています。",
        "footer": "プログラムと言語モデルが要約しています。正確な内容は原文をご確認ください。画像は原文の言語を保ち、長文は抜粋します。",
        "stats": "{events} 件のイベント · {posts} 件のニュース", "updated": "更新日時（台北時間）",
        "optout": "掲載を希望しない場合は、g0v Slack の #rep0rter でお知らせください。",
        "card_label": "原文の抜粋", "read": "ニュースを読む", "view_details": "詳細を見る",
        "appearance": "表示モード", "theme_light": "ライトモード", "theme_dark": "ダークモード", "theme_system": "端末の設定に合わせる",
        "brand_note": "コミュニティの一歩をつなぐ", "skip_content": "本文へ移動",
        "eyebrow": "g0v・Code for Korea・Code for Japan などのシビックテックコミュニティから", "hero_title": "みんなの一歩を、", "hero_accent": "次の協働へ。",
        "explore": "最新ニュースを見る", "subscribe": "RSS を購読", "hero_note": "要約も原文も、参加のきっかけも。",
        "story_heading": "コミュニティニュース", "source_heading": "情報源別に読む", "detail_title": "ニュースと原文",
        "detail_intro": "まずは要約から。詳しい内容や背景は原文で確認できます。", "source_intro": "この情報源からのニュースをまとめています。",
        "feed_eyebrow": "コミュニティの今", "latest": "最新ニュース",
        "search_label": "ニュースを検索", "search_placeholder": "話題・人・情報源を検索", "clear_search": "検索をクリア",
        "search_results": "{count} 件のニュースが見つかりました", "empty_title": "次のお知らせを待っています",
        "no_results": "該当するニュースはありません", "search_help": "別のキーワードを試すか、検索をクリアしてすべてのニュースをご覧ください。",
        "about": "rep0rter について", "about_eyebrow": "コミュニティのために", "about_title": "ニュースから、つながりへ。",
        "about_body": "さまざまな場所にあるコミュニティのニュースをひとつに。要約で近況を知り、原文で詳しく読めます。",
        "principle_sources": "原文と情報源への手がかり", "principle_languages": "4 つの言語で読む", "principle_people": "人やプロジェクトとの出会い",
        "subscribe_title": "いつもの場所で読もう", "subscribe_body": "RSS をリーダーに追加すると、この言語の新着ニュースを受け取れます。",
        "aside_note": "要約は内容をつかむためのものです。詳しい内容は原文をご確認ください。",
        "resources": "関連リンク", "back_home": "すべてのニュースへ", "replies": "返信数", "view_image": "画像を見る", "close_image": "画像を閉じる",
        "image_loading": "画像を読み込み中…", "image_error": "画像を読み込めませんでした。閉じてからもう一度お試しください。",
    },
    "en": {
        "title": "Civic tech community news", "tagline": "See what others are building. Find your next collaboration.",
        "intro": "Updates from the community, with original sources and ways to take part.",
        "language": "Reading language", "code": "Source code", "archive": "g0v Slack archive",
        "empty": "No stories yet. The first community update is on its way.", "source": "View source",
        "original": "Show original text", "published": "Originally posted", "download": "Download image",
        "image_alt": "Original excerpt card. Author or source: ", "missing": "This translation is not available yet. Showing the saved text.",
        "footer": "Summaries and English report images are prepared by rep0rter. Refer to the linked source for accuracy.",
        "stats": "{events} events · {posts} stories", "updated": "Updated (Taipei time)",
        "optout": "To request removal of a story, tell us in #rep0rter on g0v Slack.",
        "card_label": "Original excerpt", "read": "Read story", "view_details": "View details",
        "report_card_label": "English report · Summary by rep0rter",
        "report_image_alt": "English report card. Source: ",
        "pending_headline": "English translation pending",
        "pending_summary": "An English version is not available yet. Open the original or choose another language.",
        "appearance": "Appearance", "theme_light": "Light mode", "theme_dark": "Dark mode", "theme_system": "Use device setting",
        "brand_note": "Connecting the community", "skip_content": "Skip to content",
        "eyebrow": "Civic-tech communities, including g0v, Code for Korea & Code for Japan", "hero_title": "Small updates.", "hero_accent": "Shared possibilities.",
        "explore": "Explore the latest", "subscribe": "Subscribe via RSS", "hero_note": "Summaries, original sources, and ways to take part.",
        "story_heading": "Community story", "source_heading": "Browse by source", "detail_title": "The story & its source",
        "detail_intro": "Start with the summary. Explore the original for the full picture.", "source_intro": "Updates from this source, gathered in one place.",
        "feed_eyebrow": "Around the community", "latest": "Latest stories",
        "search_label": "Search stories", "search_placeholder": "Search topics, people, or sources", "clear_search": "Clear search",
        "search_results": "{count} stories found", "empty_title": "Waiting for the next story",
        "no_results": "No matching stories", "search_help": "Try another keyword, or clear your search to see all stories.",
        "about": "About rep0rter", "about_eyebrow": "Made for the community", "about_title": "A little closer, together.",
        "about_body": "Community updates, brought together. Get the gist from a summary, then follow the original to learn more.",
        "principle_sources": "Original text and source links", "principle_languages": "Four languages to read in", "principle_people": "Discover people and projects",
        "subscribe_title": "Read where you like", "subscribe_body": "Add this RSS feed to your reader for updates in your chosen language.",
        "aside_note": "Summaries help you find the essentials. Refer to the original for the full story.",
        "resources": "Resources", "back_home": "Back to all stories", "replies": "Replies", "view_image": "View image", "close_image": "Close image",
        "image_loading": "Loading image…", "image_error": "The image could not load. Close and try again.",
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

# Reader-controlled filters; dates refer to report publication in Taipei time.
for _language, _values in {
    'zh-TW': {
        'filters': '篩選與排序', 'filter_period': '報導發布時間', 'period_all': '不限時間',
        'period_week': '最近 7 天', 'period_month': '最近 30 天', 'period_quarter': '最近 90 天', 'period_custom': '自訂日期',
        'filter_topic': '主題', 'topic_all': '所有主題', 'filter_author': '作者', 'author_all': '所有作者',
        'filter_source': '來源', 'source_all': '所有來源', 'filter_sort': '排序', 'sort_newest': '最新優先', 'sort_oldest': '最早優先',
        'date_from': '開始日期', 'date_to': '結束日期', 'invalid_range': '開始日期不得晚於結束日期。',
        'filter_scope': '篩選目前頁面的報導；日期以台北時間計算。', 'topics_auto': '主題依內容關鍵字自動分類，可搭配搜尋字詞縮小範圍。',
        'reset_filters': '清除所有條件',
    },
    'en': {
        'filters': 'Filter & sort', 'filter_period': 'Report publication', 'period_all': 'Any time',
        'period_week': 'Last 7 days', 'period_month': 'Last 30 days', 'period_quarter': 'Last 90 days', 'period_custom': 'Custom dates',
        'filter_topic': 'Topic', 'topic_all': 'All topics', 'filter_author': 'Author', 'author_all': 'All authors',
        'filter_source': 'Source', 'source_all': 'All sources', 'filter_sort': 'Sort by', 'sort_newest': 'Newest first', 'sort_oldest': 'Oldest first',
        'date_from': 'From date', 'date_to': 'To date', 'invalid_range': 'The start date must not be later than the end date.',
        'filter_scope': 'Filters apply to reports on this page. Dates use Taipei time.', 'topics_auto': 'Topics are classified automatically from keywords. Combine them with search terms to narrow results.',
        'reset_filters': 'Clear all filters',
    },
    'ja': {
        'filters': '絞り込み・並び替え', 'filter_period': '記事の公開日', 'period_all': 'すべての期間',
        'period_week': '過去 7 日', 'period_month': '過去 30 日', 'period_quarter': '過去 90 日', 'period_custom': '日付を指定',
        'filter_topic': 'トピック', 'topic_all': 'すべてのトピック', 'filter_author': '投稿者', 'author_all': 'すべての投稿者',
        'filter_source': '情報源', 'source_all': 'すべての情報源', 'filter_sort': '並び順', 'sort_newest': '新しい順', 'sort_oldest': '古い順',
        'date_from': '開始日', 'date_to': '終了日', 'invalid_range': '開始日は終了日以前にしてください。',
        'filter_scope': 'このページの記事を絞り込みます。日付は台北時間です。', 'topics_auto': 'トピックはキーワードから自動分類しています。検索語と組み合わせて絞り込めます。',
        'reset_filters': 'すべての条件をクリア',
    },
    'ko': {
        'filters': '필터 및 정렬', 'filter_period': '소식 게시일', 'period_all': '전체 기간',
        'period_week': '최근 7일', 'period_month': '최근 30일', 'period_quarter': '최근 90일', 'period_custom': '날짜 지정',
        'filter_topic': '주제', 'topic_all': '모든 주제', 'filter_author': '작성자', 'author_all': '모든 작성자',
        'filter_source': '출처', 'source_all': '모든 출처', 'filter_sort': '정렬', 'sort_newest': '최신순', 'sort_oldest': '오래된 순',
        'date_from': '시작일', 'date_to': '종료일', 'invalid_range': '시작일은 종료일보다 늦을 수 없습니다.',
        'filter_scope': '현재 페이지의 소식을 필터링합니다. 날짜는 타이베이 시간입니다.', 'topics_auto': '주제는 키워드로 자동 분류됩니다. 검색어와 함께 사용해 범위를 좁힐 수 있습니다.',
        'reset_filters': '모든 조건 지우기',
    },
}.items():
    COPY[_language].update(_values)

for _language, _label in {
    'zh-TW': '依日期瀏覽', 'en': 'Browse by date',
    'ja': '日付で見る', 'ko': '날짜별로 보기',
}.items():
    COPY[_language]['browse_by_date'] = _label

for _language, _values in {
    'en': ('Write a story', 'Sign in with Google to share your own story', 'Hashtags', 'Explore hashtags',
           'Hashtag timeline', 'Source timeline', 'Follow the story, update by update.', '{count} stories',
           'Self-reported', 'No evidence link supplied', 'How to take part'),
    'zh-TW': ('撰寫報導', '使用 Google 登入，分享你的故事', '主題標籤', '探索主題標籤',
              '標籤時間軸', '來源時間軸', '沿著時間軸，了解每一步進展。', '{count} 篇報導',
              '自行投稿', '未提供佐證連結', '如何參與'),
    'ja': ('記事を書く', 'Google でログインして活動を共有', 'ハッシュタグ', 'タグから探す',
           'タグのタイムライン', '情報源のタイムライン', '更新をたどり、活動の歩みを知る。', '{count} 件の記事',
           '本人による投稿', '根拠となるリンクなし', '参加するには'),
    'ko': ('소식 쓰기', 'Google로 로그인하고 소식을 공유하세요', '해시태그', '해시태그 둘러보기',
           '해시태그 타임라인', '출처 타임라인', '업데이트를 따라 활동의 흐름을 살펴보세요.', '소식 {count}개',
           '직접 작성한 소식', '근거 링크 없음', '참여 방법'),
}.items():
    COPY[_language].update(zip(('write_story', 'contribute', 'hashtags', 'explore_hashtags',
                               'hashtag_timeline', 'source_timeline', 'timeline_intro', 'story_count',
                               'self_reported', 'no_evidence', 'take_part'), _values))


for _language, _label in {'en': 'Sign in', 'zh-TW': '登入', 'ja': 'ログイン', 'ko': '로그인'}.items():
    COPY[_language]['sign_in'] = _label


# Shared by the static reader's dialog and server-rendered sign-in fallback.
for _language, _values in {
    'en': ('Welcome to rep0rter', 'Your community. Your next story.',
           'Sign in to share an update or manage your project news.', 'Continue with Google',
           'Your email stays private. Nothing is published without your confirmation.',
           'Keep reading', 'Close sign-in', 'Preparing secure sign-in…',
           'Sign-in could not load. Please try again.', 'Try again', 'Open sign-in page',
           'You’re signed in', 'Choose what you would like to do next.', 'Manage project news',
           'Sign out', 'Sign-in is not available yet', 'Please check back soon.'),
    'zh-TW': ('歡迎回到 rep0rter', '你的社群，下一則故事。',
              '登入後，分享近況或管理你的專案消息。', '使用 Google 繼續',
              '你的電子郵件不會公開。所有內容都會在你確認後才發布。',
              '繼續閱讀', '關閉登入視窗', '正在準備安全登入…',
              '暫時無法載入登入，請再試一次。', '重試', '開啟登入頁',
              '你已登入', '接下來，想做些什麼？', '管理專案消息',
              '登出', '登入功能尚未開放', '請稍後再回來看看。'),
    'ja': ('rep0rter へようこそ', 'あなたのコミュニティ、次のストーリー。',
           'ログインして近況を投稿したり、プロジェクトのニュースを管理できます。', 'Google で続行',
           'メールアドレスは公開されません。確認なしに投稿されることはありません。',
           '閲覧を続ける', 'ログイン画面を閉じる', '安全なログインを準備中…',
           'ログイン画面を読み込めませんでした。もう一度お試しください。', '再試行', 'ログインページを開く',
           'ログイン済みです', '次に何をしますか？', 'プロジェクトニュースを管理',
           'ログアウト', 'ログインはまだ利用できません', 'しばらくしてからご確認ください。'),
    'ko': ('rep0rter에 오신 것을 환영해요', '우리 커뮤니티의 다음 이야기.',
           '로그인하여 근황을 공유하거나 프로젝트 소식을 관리하세요.', 'Google로 계속하기',
           '이메일은 공개되지 않으며 확인 없이 게시되지 않습니다.',
           '계속 읽기', '로그인 닫기', '안전한 로그인을 준비하고 있어요…',
           '로그인을 불러오지 못했습니다. 다시 시도해 주세요.', '다시 시도', '로그인 페이지 열기',
           '로그인되었습니다', '다음으로 무엇을 할까요?', '프로젝트 소식 관리',
           '로그아웃', '아직 로그인할 수 없습니다', '잠시 후 다시 확인해 주세요.'),
}.items():
    COPY[_language].update(zip((
        'login_welcome', 'login_title', 'login_intro', 'login_google', 'login_privacy',
        'login_read', 'login_close', 'login_loading', 'login_error', 'login_retry',
        'login_fallback', 'login_signed_in', 'login_next', 'login_projects',
        'login_logout', 'login_unavailable', 'login_later'), _values))
