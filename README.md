# rep0rter

g0v 的虛擬記者。匯集 Slack（之後還有 GitHub、HackMD、Mastodon 等）協作場域的紀錄，
每小時挑出值得大家知道的動態，推播到 Telegram，並發布在 [rep0rter.observe.tw](https://rep0rter.observe.tw)。

目標是降低資訊落差和協作門檻，讓多中心的社群彼此看見、彼此幫忙。

## 架構

```
collectors/  ->  store (SQLite)  ->  reporter  ->  publishers/
slack_archive    events, posts       select +      telegram
(more later)     containers, runs    write (LLM)   site (HTML + RSS)
```

- **collectors** 把每個來源抓成統一的「事件」寫進 SQLite。目前只有 Slack，資料來自
  Ronny Wang 維護的 [g0v Slack 公開存檔](https://g0v-slack-archive.g0v.ronny.tw/)。
- **store** 是所有東西的共同資料庫：事件、頻道、已發布的報導、執行紀錄。
- **reporter** 用規則替每則事件打分（回覆數、reaction、關鍵字、頻道規模、新鮮度），
  超過門檻的用 LLM 寫成短訊；沒有設定 LLM 時退回純文字摘錄。
- **publishers** 把報導送到 Telegram，並重新產生靜態網站與 RSS。

每小時跑一次完整流程。有值得報的就推，沒有就靜默。同一則事件只會報一次，
但互動數會持續更新，所以一則訊息可以在幾小時後因為討論熱起來而被選中。

## 本機執行

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # 填 Telegram 與 LLM 設定；留空也能跑

python -m rep0rter run --dry-run     # 抓資料、挑選、把會推播的內容印出來，不落地
python -m rep0rter run               # 真的推播並更新網站
python -m rep0rter status            # 看資料庫與設定狀態
python -m pytest -q tests
```

其他子命令：`collect`（只抓資料）、`report`（只挑選與推播）、`build-site`、
`loop --interval 3600`（常駐）、`export --out messages.json`（舊格式匯出）。

## 設定

全部用環境變數，見 `.env.example`。重點：

| 變數 | 說明 |
|---|---|
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | 推播用。bot 要是頻道管理員 |
| `TELEGRAM_TEST_CHAT_ID` + `REP0RTER_TELEGRAM_TEST=1` | 開發時推到測試頻道 |
| `AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL` | OpenAI 相容端點。沒設就用純文字摘錄 |
| `REP0RTER_SCORE_THRESHOLD` | 門檻，預設 6。調低會報更多 |
| `REP0RTER_MAX_ITEMS_PER_RUN` | 每小時最多幾則，預設 5 |
| `REP0RTER_KEYWORDS` | 逗號分隔，覆蓋內建的活動/徵人/發布關鍵字 |

## 部署

`compose.yaml` 有兩個容器：`worker` 每小時跑一輪，`web` 用 Caddy 提供
`data/site/` 的靜態檔在 `127.0.0.1:18090`。公開網址由 Cloudflare tunnel
轉到這個 port。

```sh
cp .env.example .env && $EDITOR .env
docker compose up -d --build
docker compose logs -f worker
```

資料（SQLite 與產出的網站）都在 `./data/`，可以用 `REP0RTER_DATA_HOST_DIR` 改位置。

## 資料來源細節：Slack 存檔

- `GET /`：首頁 HTML 的「Public Channels」表格。每列 `a.channel` 的 `title` 屬性是完整的
  Slack 頻道 JSON，另有訊息數、成員數、最後發文時間。用最後發文時間跳過安靜的頻道。
- `GET /index/getmessage?channel=<id>&count=100&before=<ts>`：JSON，由新到舊。伺服器會
  濾掉有 `subtype` 的訊息（加入頻道、bot 等），所以一頁可能不滿 100 則。

## 原則

- 只讀公開頻道，每則報導都附原文連結，摘要以原文為準。
- 不希望被報導的訊息，到 g0v Slack 的 #rep0rter 說一聲。
- 對存檔網站的請求有間隔與重試，請勿把頻率調得太高。
