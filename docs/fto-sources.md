# Facing the Ocean：日本／韓國來源小型允許清單

透過 GitHub 官方 API 逐一核對後啟用的公開 repository。允許清單永遠是明列的；
來源合格不等於可以發布，編輯政策與證據契約照常適用。

第二批來自 Code for Japan 的公開 Notion 入口，並已於 2026-09-19 取得對方同意。

```dotenv
REP0RTER_GITHUB_REPOS=codeforjapan/mapprint,Code-for-Korea/where-is-my-bus,codeforjapan/decidim-cfj,nawashiro/chiyoda_city_main_facilities,codeforjapan/BirdXplorer,codeforjapan/JibungotoPlanet,nawashiro/kazaguruma-transit,nishio/plurality-japanese,ocftw/open-star-ter-village,codeforjapan/Gussuri
REP0RTER_GITHUB_TOKEN=
REP0RTER_FEEDS=https://codefor.kr/boards/news.xml,https://codefor.kr/boards/civic-tech-projects.xml,https://code4japan-community.notion.site/Home-9dd9cd85f07942c1bd5f6ef73efdb122,https://civictech.kr/boards/news.xml,https://www.odf.or.kr/archive-project,https://medium.com/feed/codeforkorea
REP0RTER_COLLECT_DAILY_BUDGET=1800
```

## 第一批（2026-09-19 前已核對）

- **日本紙地圖 Mapprint**：[專案](https://github.com/codeforjapan/mapprint)。2026-08-01 合併的人工作品
  [PR #563](https://github.com/codeforjapan/mapprint/pull/563) 加入熊本災害支援紙地圖，依資料再利用許可與設施
  開放狀態篩選資訊。適合討論災害資訊交換、可印製地圖與來源授權。
- **韓國 Where Is My Bus**：[專案](https://github.com/Code-for-Korea/where-is-my-bus)。
  [PR #17](https://github.com/Code-for-Korea/where-is-my-bus/pull/17)（2026-09-17）加入站點、路線與車輛連接；
  [PR #18](https://github.com/Code-for-Korea/where-is-my-bus/pull/18)（同日）補齊車輛建立與司機 onboarding；
  [PR #19](https://github.com/Code-for-Korea/where-is-my-bus/pull/19)（2026-09-18）改善註冊 PIN 與失敗重試。
  適合討論鄉村／離島交通資訊。

## 其他已啟用來源（RSS／Notion）

以下來源由 `REP0RTER_FEEDS` 設定，與 GitHub 允許清單共用同一套採集、選稿與退出流程。

- **Code for Korea 官方新聞**：[新聞頁](https://codefor.kr/boards/news)、[RSS 2.0](https://codefor.kr/boards/news.xml)。新聞頁 HTML 明列此 feed；2026-09-19 實測無須認證即可取得 XML，含 12 筆文章。description 是截短摘要；採集保留原文連結，不把摘要當全文。透過 `REP0RTER_RSS_FEEDS` 啟用後，與其他來源共用每小時採集及編輯流程。
- **Code for Korea 公民科技專案典藏**：[典藏頁](https://codefor.kr/boards/civic-tech-projects)、[RSS 2.0](https://codefor.kr/boards/civic-tech-projects.xml)。與新聞頁不同，這裡整理韓國公民科技專案的介紹。頁面明列 RSS，2026-09-19 實測回傳 8 筆，最新條目日期為 2026-08-01；不能將典藏日期解讀為專案發布日。兩個 feed 各自保留來源名稱，皆透過 `REP0RTER_FEEDS` 設定。這是追蹤典藏條目的入口，不是整個專案資料庫的完整匯入。
- **Code for Japan 社群入口**：[Home](https://code4japan-community.notion.site/Home-9dd9cd85f07942c1bd5f6ef73efdb122)。`REP0RTER_FEEDS` 自動辨識公開 Notion URL，讀取入口內嵌的活動、募集任務與專案資料庫。2026-09-19 已驗證匿名 JSON POST 可回傳資料；連續驗證後上游回覆 429，因此必須保留退避與 degraded 狀態，不能把 rate limit 當成空清單。無須官方 Notion API token。
- **Civic Tech Network／시민기술네트워크 活動典藏**：[頁面](https://civictech.kr/boards/news)、[RSS](https://civictech.kr/boards/news.xml)。獨立於 Code for Korea；2026-09-19 驗證 8 筆，最新 2026-05-26，保留摘要及原文連結。
- **Open Data Forum／오픈데이터포럼 專案典藏**：[頁面](https://www.odf.or.kr/archive-project)、[全站 RSS](https://www.odf.or.kr/rss)。以典藏頁 URL 設定，只納入 RSS 中同網域的專案分類。2026-09-19 透過標準無登入 Chromium 驗證 50 筆中有 12 筆專案，最新 2026-09-15；只有標題、日期及連結，並無內文。一般 HTTP client 回傳 403，因此採用有共用 budget 的單一 XML 瀏覽器請求。
- **Code for Korea Medium**：[出版頁](https://medium.com/codeforkorea)、[RSS](https://medium.com/feed/codeforkorea)。2026-09-19 驗證 10 筆，最新 2022-10-15；擷取 `content:encoded` 所提供的正文及 `dc:creator`，保留原始發布日期，不把歷史文章當新消息。

## 第二批（2026-09-19 透過 Notion 入口發現）

以 [Notion 來源發現](#notion-來源發現) 取得候選，再逐一以 GitHub 官方 API 核對公開性、
是否封存與最後 push。以下八個在 90 天內有 push：

| repository | 最後 push | 適合討論的題材 |
|---|---|---|
| [codeforjapan/decidim-cfj](https://github.com/codeforjapan/decidim-cfj) | 2026-09-19 | Decidim 參與式民主平台的在地化與市民提案流程 |
| [nawashiro/chiyoda_city_main_facilities](https://github.com/nawashiro/chiyoda_city_main_facilities) | 2026-09-18 | 千代田區福祉交通「風ぐるま」的設施開放資料 |
| [codeforjapan/BirdXplorer](https://github.com/codeforjapan/BirdXplorer) | 2026-09-18 | 跨語言公共討論與 Community Notes 資料分析 |
| [codeforjapan/JibungotoPlanet](https://github.com/codeforjapan/JibungotoPlanet) | 2026-09-16 | 個人碳足跡與氣候行動的公民參與 |
| [nawashiro/kazaguruma-transit](https://github.com/nawashiro/kazaguruma-transit) | 2026-09-16 | 同上路線的轉乘查詢；與設施資料同一專案 |
| [nishio/plurality-japanese](https://github.com/nishio/plurality-japanese) | 2026-08-27 | ⿻數位 Plurality 日文版翻譯；與 g0v 圈直接相連 |
| [ocftw/open-star-ter-village](https://github.com/ocftw/open-star-ter-village) | 2026-07-30 | OCF（台灣）的開源體驗桌遊；台日兩地共同題材 |
| [codeforjapan/Gussuri](https://github.com/codeforjapan/Gussuri) | 2026-07-16 | 睡眠記錄的公共健康資料實作 |

`codeforjapan/mapprint` 不在 Notion 入口內，兩份清單互補而非包含。

### 已核對但不採用

同一次發現共取出 18 個 repository，其餘十個全部因為長期沒有 push 而不採用；
這不是品質判斷，只是它們近期不會產生可報導的協作進展。將來重新核對即可再評估。

| repository | 最後 push | 原因 |
|---|---|---|
| makeOurCity/Fiwarecraft | 2026-06-16 | 剛好落在 90 天窗口外 |
| mopinfish/3dcp-api | 2026-05-08 | 休眠 |
| mopinfish/3dcp-web | 2025-12-26 | 休眠 |
| c-3lab/opendata-dataops-document | 2024-04-01 | 休眠 |
| c-3lab/dim | 2024-01-21 | 休眠 |
| CollectiveReview/academic-github | 2024-01-17 | 休眠；瀏覽器看到的 301 其實是更名，API 能解析 |
| kazutoshifurukawa/jukatsuflex-backend | 2023-11-05 | 休眠 |
| kazutoshifurukawa/jukatsuflex-frontend | 2023-10-30 | 休眠 |
| stats-gender-gap-jp/stats-gender-gap-jp | 2023-10-16 | 休眠 |
| ayuki-joto/nekonige | 2023-01-30 | 休眠 |

### 如何記錄「看過但不納入」

判斷某個候選不適合時，寫進既有的排除台帳，提案就不會再重複提出：

```sh
python -m rep0rter exclusion add --scope container \
  --subject github:owner/repository --reason '為什麼不納入'
```

`github.collect` 本來就會檢查 `container_allowed`，所以同一筆紀錄同時代表
「不採集」與「不再提案」。刻意不另開一份清單：不採集的對象只有一個地方可查，
理由欄位負責區分「當事人要求退出」與「我們判斷不在範圍內」。需要重新評估時用
`exclusion remove` 取消。

`c-3lab/opendata-pdf-to-csv` 只出現在入口的子頁面，基於下節的理由沒有被掃到；
如果要納入，請以人工方式加入清單，不要為此開啟子頁面巡迴。

前次核對時 mapprint、BirdXplorer、JibungotoPlanet、where-is-my-bus、workercare、ansimi 的
GitHub release 清單皆為空；不能把最新 push 或 dependency bot 當成「新版本推出」。
更新入口是各 repo 的公開 issue／merged PR；release endpoint 保留供未來正式公告。
尚未核實正式 Mastodon 帳號，所以不預填。

## Notion 來源發現

`python -m rep0rter notion-candidates --portal <公開頁面 URL>` 從公開的 Notion 入口
取出 repository 候選。它只提出建議，不採集、不寫入允許清單，也不在 `collect`／`report`／`run`
的路徑上。

Notion 沒有提供公開頁面的讀取 API：官方 API 需要 bearer token 與 `Notion-Version` 標頭，
等於要請對方建立並交出 integration。唯一免權限的路徑是 `<space>.notion.site/api/v3/*`，
它是 Notion 自家的第一方端點，但未文書、未版本化，也沒有相容性承諾。

這條路徑可以接受，**只因為發現與發布是隔離的**：沒有任何已發布內容依賴它，端點壞掉時
只是不再有新建議，公開中的流程不受影響，而且每個 repository 仍由人審核後才進清單。

### 端點會以 HTTP 200 靜默失敗

既有用戶端函式庫使用的舊 `queryCollection` loader 形式，現在回傳 HTTP 200、
`sizeHint: 113`，以及**空的** `recordMap`。它不是報錯，而是假裝成功。
因此必須檢查回應的形狀而不是狀態碼：`sizeHint` 非零卻沒有任何 row 一律視為失敗。
回報的行數與實際取回的行數不一致時，明確標示為不完整，不謊稱完整。

### 只讀取指定的那一頁

發現只讀取傳入頁面上內嵌的資料庫，**不追子頁面**。實測這個入口，巡迴會把一意資料庫從
3 個變成 8 個，只多找到 1 個 repository，代價是讀取一份 348 列的成員名錄
（handle、Slack／Twitter／GitHub 帳號、首頁、web3 錢包地址）以及一份活動參加紀錄。

屬性型別在這裡完全擋不住：名錄把上述資料全部存成一般的 `title`／`text`／`url` 屬性。
真正的防線是單頁範圍本身，所以它是刻意的限制，不是尚未完成的功能。

### 其他必須守住的事

- 只讀取不可能承載個人資料的屬性型別；person、email、phone、file 一律不載入。
- 屬性鍵是不透明字串（`tSE{`、`mJgC`、`V_js`），每次都以 schema 的 `name` 解析，
  絕不寫死。欄位改名或改型別時 fail closed，不輸出錯誤資料。
- 回應中含有 `created_by_id`／`last_edited_by_id`（`notion_user` UUID），
  即使公開頁面的畫面上看不到它們；一律不保留。
- 公開性以 `publicAccessRole` 判定。注意已發布網站的 `isPublicShareLink` 是 false，
  那個旗標代表另一種共享連結模式，不代表可公開讀取。
- GitHub 核對遇到限流或伺服器錯誤時記為未解析，**不能當成連結已死**；
  否則會安靜地漏掉真實候選。實測曾因此一次漏掉三個候選。

### 與 Notion collector 的關係

同一個入口現在有兩條各自獨立的路徑，用途不同，不要混淆：

| | `collectors/notion.py` | `notion_discovery.py` |
|---|---|---|
| 輸出 | Event，可能成為報導 | GitHub 允許清單的候選提案 |
| 設定 | `REP0RTER_FEEDS` | `REP0RTER_NOTION_PORTAL` |
| 執行 | 每輪採集 | 手動指令＋maintenance 每週 |
| 寫入 | 事件資料庫 | 不寫入任何地方 |

兩者都讀同一批未文書端點。發現這一側刻意不進入採集路徑，所以端點壞掉時只是不再有
新的 repository 建議，已發布內容與正常採集不受影響。

早期調查曾記錄活動與募集任務資料庫「暫不報導」，理由是供給量薄、與 Peatix／GitHub 重疊、
Notion 是狀態而非事件、含個人欄位，以及缺少日文退出管道。其後 collector 已啟用這些資料庫，
該結論不再適用，但底下的注意事項仍然有效，應由 collector 端持續守住：

- 個人欄位（`わたしやるよ！`、`メンバー`、`連絡先`）不得進入事件或稽核紀錄。
  出現在社群內部名單，不等於同意被公開的多語新聞網站與 Telegram 報導。
- `last_edited_time` 連錯字修正都會變動，編輯舊項目不能變成新報導。
- 入口本文明寫報名在 Peatix，專案列指向 GitHub，同一則消息可能由多個來源進來，
  需依賴既有的主題去重。
- 退出管道目前只有 g0v Slack `#rep0rter`，仍缺日文入口。

## 實際採集驗證

使用目前 adapter、隔離的 SQLite、兩天窗口對十個 repository 進行 HTTP smoke test：
41 次 request、13,510,078 bytes、10 筆來源合格事件、0 個 source error。
事件分別來自 `chiyoda_city_main_facilities` 5 筆、`where-is-my-bus` 3 筆、`decidim-cfj` 2 筆。
沒有 LLM 呼叫、Telegram 推播或 production DB 寫入。來源合格不代表已自動發布。
先前以兩個 repository 的驗證為 8 次 request、2,200,452 bytes、3 筆人工 PR。

Notion 匿名端點初次直連回傳 HTTP 200（入口及資料庫 JSON）；後續遇到上游 429，未繞過限制。使用這些真實公開回應離線驗證 adapter，辨識出 3 個內嵌資料庫，成功正規化 95 筆不同專案／任務列；這不代表已完成全部歷史資料抓取，也沒有寫入 production DB。

RSS adapter 另以獨立暫存 SQLite、擴大時間窗驗證新聞 feed：兩次 HTTP request 均成功，共保存 12 筆文章；第二次確認 12 筆重複、沒有新增重複事件。此測試沒有寫入 production DB。正常採集維持既有兩天窗口，不自動把歷史公告當作新消息。

日文 `概要／内容／変更内容`、韓文 `요약／주요 변경／변경 사항`、英文 `Summary` 段落需要同時有
具體公民用途與功能影響，才通過 PR 來源資格；單純標題或泛泛 Summary 不足。

### 請求預算實測

先前「Slack 會用滿每輪配額」是未經量測的假設，據此推導的結論並不成立。實際數字：

| 項目 | 每輪請求數 |
|---|---|
| Slack 定常（連續兩輪皆同） | 9 |
| Slack bootstrap（用滿 40 配額，仍有 12 個頻道未完成） | 40 |
| GitHub 每個 repository | 4（`decidim-cfj` 為 5） |
| GitHub 十個 repository | 41 |
| 十個 repository 的定常總計 | 50〜63 |

真正的限制不是預算而是認證。`github.get` 原本不送 `Authorization`，未認證的 GitHub
每小時每位址只有 60 次，十個 repository 每小時就要花掉 41 次；實測一輪內有六個
repository 以 `upstream rate limit` 失敗。加上 `REP0RTER_GITHUB_TOKEN` 後同樣的一輪
兩個來源都健康、零限流。token 是選用的，也不會擴大可見範圍：兩種情況都只採集明確公開的 repository。

定常每日用量約 1,200〜1,512 次，對 1,500 的日預算沒有餘裕，用盡後當天剩餘時間會停止採集，
因此把 `REP0RTER_COLLECT_DAILY_BUDGET` 提高到 1,800。GitHub 的每輪成本與活動量無關而近乎固定，
catch-up 時那 41 次是 Slack 用不到的；定常只需 9 次所以日常無虞，故障復原時會變慢，
以 `collector_metrics` 持續觀察即可，不預先最佳化。
