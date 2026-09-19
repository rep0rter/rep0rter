"""Runtime configuration, read from environment variables (and .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()  # no-op when .env is absent

TAIPEI = ZoneInfo("Asia/Taipei")


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = _env(name)
    if raw is None:
        return default
    return [s.strip() for s in raw.split(",") if s.strip()]


DEFAULT_KEYWORDS = [
    # events & calls for participation
    "活動", "報名", "黑客松", "大松", "小松", "基礎松", "松", "工作坊", "分享會", "講座", "論壇", "聚會",
    "徵", "招募", "招人", "找人", "志工", "協作", "提案", "徵求", "徵件", "獎助金", "grant",
    # releases & milestones
    "上線", "發布", "發佈", "release", "launch", "開源", "新專案", "新功能", "beta", "demo",
    # civic tech signals
    "開放資料", "open data", "政府", "立法院", "公聽會", "記者會", "新聞稿", "投票", "選舉",
]


@dataclass
class Config:
    # storage
    data_dir: Path = field(default_factory=lambda: Path(_env("REP0RTER_DATA_DIR", "./data")))
    site_title: str = _env("REP0RTER_SITE_TITLE", "rep0rter") or "rep0rter"
    site_url: str = _env("REP0RTER_SITE_URL", "https://rep0rter.observe.tw") or "https://rep0rter.observe.tw"

    # Owner submissions: optional Google OpenID Connect web application.
    google_client_id: str | None = field(default_factory=lambda: _env("REP0RTER_GOOGLE_CLIENT_ID"))
    google_client_secret: str | None = field(default_factory=lambda: _env("REP0RTER_GOOGLE_CLIENT_SECRET"))
    web_secret_key: str | None = field(default_factory=lambda: _env("REP0RTER_WEB_SECRET_KEY"))

    # collection
    collect_days: int = _env_int("REP0RTER_COLLECT_DAYS", 2)

    # reporter thresholds (see reporter.score for how these are combined)
    score_threshold: float = _env_float("REP0RTER_SCORE_THRESHOLD", 6.0)
    max_items_per_run: int = _env_int("REP0RTER_MAX_ITEMS_PER_RUN", 5)
    max_item_age_hours: int = _env_int("REP0RTER_MAX_ITEM_AGE_HOURS", 48)
    editorial_mode: str = _env("REP0RTER_EDITORIAL_MODE", "shadow") or "shadow"
    keywords: list[str] = field(default_factory=lambda: _env_list("REP0RTER_KEYWORDS", DEFAULT_KEYWORDS))

    # telegram
    telegram_bot_token: str | None = _env("TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = _env("TELEGRAM_CHAT_ID")
    telegram_test_chat_id: str | None = _env("TELEGRAM_TEST_CHAT_ID")
    telegram_use_test_chat: bool = (_env("REP0RTER_TELEGRAM_TEST", "0") or "0").lower() in ("1", "true", "yes")
    telegram_language: str = _env("REP0RTER_TELEGRAM_LANGUAGE", "en") or "en"

    # threads
    threads_enabled: bool = (_env("REP0RTER_THREADS_ENABLED", "0") or "0").lower() in ("1", "true", "yes")
    threads_user_id: str | None = _env("REP0RTER_THREADS_USER_ID")
    threads_access_token: str | None = field(default_factory=lambda: _env("REP0RTER_THREADS_ACCESS_TOKEN"), repr=False)
    threads_batch_size: int = _env_int("REP0RTER_THREADS_BATCH_SIZE", 10)

    # Original-text cards (Noto CJK is installed in the Docker image).
    card_font: str | None = _env("REP0RTER_CARD_FONT")
    chrome_path: str | None = _env("REP0RTER_CHROME_PATH")

    # llm (OpenAI-compatible chat completions)
    ai_base_url: str | None = _env("AI_BASE_URL")
    ai_api_key: str | None = _env("AI_API_KEY")
    ai_model: str | None = _env("AI_MODEL")
    ai_timeout_seconds: int = _env_int("AI_TIMEOUT_SECONDS", 120)

    @property
    def google_login_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret and self.web_secret_key)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "rep0rter.sqlite"

    @property
    def site_dir(self) -> Path:
        return self.data_dir / "site"

    @property
    def telegram_target(self) -> str | None:
        if self.telegram_use_test_chat:
            return self.telegram_test_chat_id
        return self.telegram_chat_id

    @property
    def llm_enabled(self) -> bool:
        return bool(self.ai_base_url and self.ai_api_key and self.ai_model)

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.site_dir.mkdir(parents=True, exist_ok=True)


def load_config() -> Config:
    return Config()
