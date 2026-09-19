"""Conservative, deterministic topic hints derived only from published text.

These are navigation hints, not editorial labels. Keep identifiers stable across
editions and require explicit terms rather than inferring personal attributes.
"""
from __future__ import annotations

import re
import unicodedata

# Order is intentional: a report may belong to several topics.
TOPICS = {
    "events": {
        "labels": {"en": "Events", "zh-TW": "活動", "ja": "イベント", "ko": "행사"},
        "terms": ("event", "events", "conference", "meetup", "hackathon", "workshop",
                  "活動", "研討會", "聚會", "工作坊", "黑客松", "イベント", "カンファレンス",
                  "ハッカソン", "ワークショップ", "행사", "컨퍼런스", "밋업", "해커톤", "워크숍"),
    },
    "development": {
        "labels": {"en": "Development", "zh-TW": "程式開發", "ja": "開発", "ko": "개발"},
        "terms": ("software", "programming", "developer", "developers", "development", "github", "api", "sdk",
                  "程式", "軟體", "開發", "開発", "ソフトウェア", "プログラミング", "개발", "소프트웨어", "프로그래밍"),
    },
    "open-data": {
        "labels": {"en": "Open data", "zh-TW": "開放資料", "ja": "オープンデータ", "ko": "공개 데이터"},
        "terms": ("open data", "dataset", "datasets", "data set", "data sets", "開放資料", "資料集",
                  "オープンデータ", "データセット", "공개 데이터", "공공데이터", "데이터셋"),
    },
    "education": {
        "labels": {"en": "Learning", "zh-TW": "學習教育", "ja": "学習・教育", "ko": "학습·교육"},
        "terms": ("education", "course", "courses", "tutorial", "tutorials", "training", "lecture",
                  "教育", "課程", "教學", "培訓", "學習", "講座", "学習", "チュートリアル", "교육", "강좌", "학습", "튜토리얼"),
    },
    "community": {
        "labels": {"en": "Community", "zh-TW": "社群", "ja": "コミュニティ", "ko": "커뮤니티"},
        "terms": ("community", "volunteer", "volunteers", "contributor", "contributors",
                  "社群", "志工", "貢獻者", "コミュニティ", "ボランティア", "커뮤니티", "자원봉사", "기여자"),
    },
    "security": {
        "labels": {"en": "Security", "zh-TW": "資訊安全", "ja": "セキュリティ", "ko": "정보 보안"},
        "terms": ("cybersecurity", "cyber security", "information security", "vulnerability", "vulnerabilities", "ctf",
                  "資安", "資訊安全", "漏洞", "セキュリティ", "脆弱性", "보안", "취약점"),
    },
    "other": {
        "labels": {"en": "Other", "zh-TW": "其他", "ja": "その他", "ko": "기타"},
        "terms": (),
    },
}


def _pattern(term: str) -> re.Pattern:
    escaped = re.escape(term).replace(r"\ ", r"\s+")
    # Latin abbreviations must not match inside words (API in capital, for example).
    if term.isascii():
        escaped = rf"(?<![a-z0-9_]){escaped}(?![a-z0-9_])"
    return re.compile(escaped)


_PATTERNS = {key: tuple(_pattern(term) for term in value["terms"])
             for key, value in TOPICS.items() if key != "other"}


def classify_topics(*public_text: str) -> list[str]:
    """Return stable multi-label IDs; unmatched text receives only ``other``."""
    text = unicodedata.normalize("NFKC", "\n".join(public_text)).casefold()
    # Link paths and hostnames are not evidence of a report's subject.
    text = re.sub(r"https?://[^\s<>]+", " ", text)
    matches = [key for key, patterns in _PATTERNS.items()
               if any(pattern.search(text) for pattern in patterns)]
    return matches or ["other"]


def localized_topics(topic_ids: list[str], language: str) -> list[dict[str, str]]:
    return [{"id": key, "label": TOPICS[key]["labels"].get(language, TOPICS[key]["labels"]["en"])}
            for key in topic_ids]
