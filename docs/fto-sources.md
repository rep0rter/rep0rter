# Facing the Ocean：日本／韓國來源小型允許清單

2026-09-19（台北）透過 GitHub 官方 API 核對，建議先啟用兩個公開 repository：

```dotenv
REP0RTER_GITHUB_REPOS=codeforjapan/mapprint,Code-for-Korea/where-is-my-bus
REP0RTER_FEEDS=https://codefor.kr/boards/news.xml,https://codefor.kr/boards/civic-tech-projects.xml,https://code4japan-community.notion.site/Home-9dd9cd85f07942c1bd5f6ef73efdb122,https://civictech.kr/boards/news.xml,https://www.odf.or.kr/archive-project,https://medium.com/feed/codeforkorea
```

- **日本紙地圖 Mapprint**：[專案](https://github.com/codeforjapan/mapprint)。2026-08-01 合併的人工作品 [PR #563](https://github.com/codeforjapan/mapprint/pull/563) 加入熊本災害支援紙地圖，依資料再利用许可與設施開放狀態篩選資訊。適合討論災害資訊交換、可印製地圖與來源授權。
- **韓國 Where Is My Bus**：[專案](https://github.com/Code-for-Korea/where-is-my-bus)。[PR #17](https://github.com/Code-for-Korea/where-is-my-bus/pull/17)（2026-09-17）加入站點、路線與車輛連接；[PR #18](https://github.com/Code-for-Korea/where-is-my-bus/pull/18)（同日）補齊車輛建立與司機 onboarding；[PR #19](https://github.com/Code-for-Korea/where-is-my-bus/pull/19)（2026-09-18）改善註冊 PIN 與失敗重試。適合討論鄉村／離島交通資訊。
- **後續候選 BirdXplorer**：[專案](https://github.com/codeforjapan/BirdXplorer)，可供討論跨語言公共討論與 Community Notes 資料分析。[PR #292](https://github.com/codeforjapan/BirdXplorer/pull/292)（2026-09-16）是人工提交的資料匯入競態修復。這類內部維護仍不應直接當社群新聞。
- **Code for Korea 官方新聞**：[新聞頁](https://codefor.kr/boards/news)、[RSS 2.0](https://codefor.kr/boards/news.xml)。新聞頁 HTML 明列此 feed；2026-09-19 實測無須認證即可取得 XML，含 12 筆文章。description 是截短摘要；採集保留原文連結，不把摘要當全文。透過 `REP0RTER_RSS_FEEDS` 啟用後，與其他來源共用每小時採集及編輯流程。
- **Code for Korea 公民科技專案典藏**：[典藏頁](https://codefor.kr/boards/civic-tech-projects)、[RSS 2.0](https://codefor.kr/boards/civic-tech-projects.xml)。與新聞頁不同，這裡整理韓國公民科技專案的介紹。頁面明列 RSS，2026-09-19 實測回傳 8 筆，最新條目日期為 2026-08-01；不能將典藏日期解讀為專案發布日。兩個 feed 各自保留來源名稱，皆透過 `REP0RTER_FEEDS` 設定。這是追蹤典藏條目的入口，不是整個專案資料庫的完整匯入。
- **Code for Japan 社群入口**：[Home](https://code4japan-community.notion.site/Home-9dd9cd85f07942c1bd5f6ef73efdb122)。`REP0RTER_FEEDS` 自動辨識公開 Notion URL，讀取入口內嵌的活動、募集任務與專案資料庫。2026-09-19 已驗證匿名 JSON POST 可回傳資料；連續驗證後上游回覆 429，因此必須保留退避與 degraded 狀態，不能把 rate limit 當成空清單。無須官方 Notion API token。

- **Civic Tech Network／시민기술네트워크 活動典藏**：[頁面](https://civictech.kr/boards/news)、[RSS](https://civictech.kr/boards/news.xml)。獨立於 Code for Korea；2026-09-19 驗證 8 筆，最新 2026-05-26，保留摘要及原文連結。
- **Open Data Forum／오픈데이터포럼 專案典藏**：[頁面](https://www.odf.or.kr/archive-project)、[全站 RSS](https://www.odf.or.kr/rss)。以典藏頁 URL 設定，只納入 RSS 中同網域的專案分類。2026-09-19 透過標準無登入 Chromium 驗證 50 筆中有 12 筆專案，最新 2026-09-15；只有標題、日期及連結，並無內文。一般 HTTP client 回傳 403，因此採用有共用 budget 的單一 XML 瀏覽器請求。
- **Code for Korea Medium**：[出版頁](https://medium.com/codeforkorea)、[RSS](https://medium.com/feed/codeforkorea)。2026-09-19 驗證 10 筆，最新 2022-10-15；擷取 `content:encoded` 所提供的正文及 `dc:creator`，保留原始發布日期，不把歷史文章當新消息。

所核對的 mapprint、BirdXplorer、JibungotoPlanet、where-is-my-bus、workercare、ansimi 目前 GitHub release 清單皆為空；不能把最新 push 或 dependency bot 當成「新版本推出」。更新入口是各 repo 的公開 issue／merged PR；release endpoint 保留供未來正式公告。未核實正式 Mastodon 帳號，所以不預填。

## 實際採集驗證

Notion 匿名端點初次直連回傳 HTTP 200（入口及資料庫 JSON）；後續遇到上游 429，未繞過限制。使用這些真實公開回應離線驗證 adapter，辨識出 3 個內嵌資料庫，成功正規化 95 筆不同專案／任務列；這不代表已完成全部歷史資料抓取，也沒有写入 production DB。

RSS adapter 另以獨立暫存 SQLite、擴大時間窗驗證新聞 feed：兩次 HTTP request 均成功，共保存 12 筆文章；第二次確認 12 筆重複、沒有新增重複事件。此測試沒有寫入 production DB。正常採集維持既有兩天窗口，不自動把歷史公告當作新消息。

使用目前 adapter、獨立 `/tmp/rep0rter-source-smoke/two-day-current.sqlite`、兩天窗口與上述兩個來源進行 HTTP smoke test：8 次 request、2,200,452 bytes、3 筆人工 PR、0 個 source error；候選均來自 where-is-my-bus #17／#18／#19。沒有 LLM 呼叫、Telegram 推播或 production DB 寫入。

Mapprint 兩天內沒有合格新訊息，因此此輪保持安靜。日文 `概要／内容／変更内容`、韓文 `요약／주요 변경／변경 사항`、英文 `Summary` 段落需要同時有具體公民用途與功能影響，才通過 PR 來源資格；單純標題或泛泛 Summary 不足。仍需後續共同選稿及證據契約，不能把 source eligible 解讀成已自動發布。
