# Facing the Ocean：日本／韓國來源小型允許清單

透過 GitHub 官方 API 逐一核對後啟用的公開 repository。允許清單永遠是明列的；
來源合格不等於可以發布，編輯政策與證據契約照常適用。

```dotenv
REP0RTER_GITHUB_REPOS=codeforjapan/mapprint,Code-for-Korea/where-is-my-bus,codeforjapan/decidim-cfj,nawashiro/chiyoda_city_main_facilities,codeforjapan/BirdXplorer,codeforjapan/JibungotoPlanet,nawashiro/kazaguruma-transit,nishio/plurality-japanese,ocftw/open-star-ter-village,codeforjapan/Gussuri
REP0RTER_GITHUB_TOKEN=
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

### 活動／募集任務資料庫：已調查，暫不報導

入口另有「イベント/Events」（111／113 列）與「募集タスク」（57 列）兩個資料庫，
內容確實是活動公告與協作募集，而且 `開催日` 是結構化日期欄位，比從散文推斷更可靠。
即使如此，這一輪不納入報導，理由記錄如下，避免日後從零重新調查：

- 供給量薄。活動大約每月更新一次（Social Hack Day #75 → #76 → #77），
  募集任務資料庫已停滯四個月，量級是每月一到三則。
- 多數可從別處取得。入口本文明寫報名在 Peatix，專案列指向 GitHub，後者已在採集範圍內。
- Notion 是狀態不是事件。`last_edited_time` 連錯字修正都會變動，只有新增列與
  有意義的狀態轉移才可能算事件，等同把 `stories.py` 的修訂判斷套到 wiki 上。
- 兩個資料庫都有個人欄位（`わたしやるよ！`、`メンバー`、`連絡先`）。
  出現在社群內部名單，不等於同意被公開的多語新聞網站與 Telegram 報導。
- 沒有日文的退出管道。README 目前只指向 g0v Slack `#rep0rter`。

等允許清單運作之後，若確實觀察到 GitHub 取不到的日本側公告，再重新評估。

## 實際採集驗證

使用目前 adapter、隔離的 SQLite、兩天窗口對十個 repository 進行 HTTP smoke test：
41 次 request、13,510,078 bytes、10 筆來源合格事件、0 個 source error。
事件分別來自 `chiyoda_city_main_facilities` 5 筆、`where-is-my-bus` 3 筆、`decidim-cfj` 2 筆。
沒有 LLM 呼叫、Telegram 推播或 production DB 寫入。來源合格不代表已自動發布。

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
