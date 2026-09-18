# rep0rter

g0v 的虛擬記者。匯集 Slack（之後還有 GitHub、HackMD、Mastodon 等）協作場域的紀錄，
每小時挑出值得大家知道的動態，推播到 Telegram，並發布在 [rep0rter.observe.tw](https://rep0rter.observe.tw)。

每篇報導同時產生台灣繁體中文、韓文、日文、英文版本，網站可自由切換並保留閱讀位置。
每則消息附原文圖卡：公開頭貼／來源 logo、作者、來源與原文摘錄；Telegram 一篇一圖，
附四語完整報導連結。沒有可用翻譯時會明確標示，沒有頭貼時顯示名字縮寫。

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
playwright install chromium
# Linux 另需安裝 Noto CJK 字型；Debian/Ubuntu: sudo apt-get install fonts-noto-cjk
cp .env.example .env        # 填 Telegram 與 LLM 設定；留空也能跑

python -m rep0rter run --dry-run     # 抓資料、挑選、預覽；會存 events/runs，但不新增報導或推播
python -m rep0rter run               # 真的推播並更新網站
python -m rep0rter status            # 看資料庫與設定狀態
python -m pytest -q tests
```

其他子命令：`collect`（只抓資料）、`report`（只挑選與推播）、`build-site`、
`translate`（補缺少的語言）、`outbox`（發布狀態與人工核對）、
`loop --interval 3600`（常駐）、`export --out messages.json`（舊格式匯出）。

## 四語報導與圖卡

設定 LLM 後，新報導使用一次請求產生四種語言；各語言獨立驗證及儲存。
LLM 沒設定或某語言失敗時，仍保留文字與原文入口，不會把原文假稱成已完成的翻譯。
既有報導可分批補齊，只補缺少的版本，**不重新推送 Telegram**：

```sh
python -m rep0rter translate --limit 50
python -m rep0rter translate --limit 10 --language ko --language ja
python -m rep0rter build-site
```

網站提供 `index.html`、`index.ko.html`、`index.ja.html`、`index.en.html`，以及相對應的
`feed.xml`、`feed.ko.xml`、`feed.ja.xml`、`feed.en.xml`。每篇有永久網址
`posts/<post-id>/index[.語言].html`；首頁只列最近 300 篇，較早的文章仍保留永久頁與來源頁。
RSS 保留歷史版本實際使用的數字 post ID 作 GUID，避免修正事件 ID 後舊文被重新訂閱。

圖卡參考 [chumei 的來源截圖流程](https://github.com/skyhong2002/chumei/blob/main/scripts/render_source_covers.py)：
Playwright 將經跳脫的本機 HTML 內容區塊截成 1200×630 PNG，依內容快取；瀏覽器不可用時
改用 Pillow 排版。圖片使用原文語言，長文節錄並保留全文入口，不把 AI 摘要放在作者頭貼旁當成引言。
下載的頭貼僅來自公開 HTTPS，檢查並固定公開 IP、驗證 TLS，限制大小與時間；渲染頁不能連外。
Docker 已包含 Chromium 與 Noto CJK 字型。本機若使用其他字型，可設定 `REP0RTER_CARD_FONT`。

新採集的 Slack 訊息會保留公開頭貼網址；舊資料需在下次採集覆蓋其日期範圍後才有頭貼。
其他 collector 可提供 `event.meta.avatar_url`、`source_logo_url`、`source_name`；非 Slack
來源缺少明確圖片時會嘗試網站 favicon。仍無法取得時，每篇依然有原文與縮寫圖卡。
`assets/logo.png` 是本站識別；來源人物頭貼與來源 logo 不會用本站 logo 冒充。

## 設定

全部用環境變數，見 `.env.example`。重點：

| 變數 | 說明 |
|---|---|
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | 推播用。bot 要是頻道管理員 |
| `TELEGRAM_TEST_CHAT_ID` + `REP0RTER_TELEGRAM_TEST=1` | 開發時推到測試頻道 |
| `REP0RTER_TELEGRAM_LANGUAGE` | 圖片說明語言，`zh-TW`／`ko`／`ja`／`en`；預設繁中 |
| `AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL` | OpenAI 相容端點。沒設就用純文字摘錄 |
| `REP0RTER_SCORE_THRESHOLD` | 門檻，預設 6。調低會報更多 |
| `REP0RTER_MAX_ITEMS_PER_RUN` | 每小時最多幾則，預設 5 |
| `REP0RTER_KEYWORDS` | 逗號分隔，覆蓋內建的活動/徵人/發布關鍵字 |

測試模式未設定測試 chat 時不會改送正式頻道。網站發布和 Telegram 分開記錄；無 Telegram
設定時留下待送工作，設定完成後由 `report` 或 `run` 處理。發送結果不明時不自動重試，
請用 `python -m rep0rter outbox` 檢查，參考 [Telegram 恢復操作](docs/telegram-delivery.md)。

升級既有環境前請先以 SQLite backup API 備份資料庫；啟動時會保留既有文章並新增翻譯欄位。
`translate` 和 `build-site` 不會重送已發布文章。尚未實作完整退出／撤回傳播及舊 Telegram
合併訊息重建，進度界線見 [本次 issue 修正範圍](docs/implementation-notes.md)。

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
