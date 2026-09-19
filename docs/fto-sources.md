# Facing the Ocean：日本／韓國來源小型允許清單

2026-09-19（台北）透過 GitHub 官方 API 核對，建議先啟用兩個公開 repository：

```dotenv
REP0RTER_GITHUB_REPOS=codeforjapan/mapprint,Code-for-Korea/where-is-my-bus
REP0RTER_RSS_FEEDS=https://codefor.kr/boards/news.xml
```

- **日本紙地圖 Mapprint**：[專案](https://github.com/codeforjapan/mapprint)。2026-08-01 合併的人工作品 [PR #563](https://github.com/codeforjapan/mapprint/pull/563) 加入熊本災害支援紙地圖，依資料再利用许可與設施開放狀態篩選資訊。適合討論災害資訊交換、可印製地圖與來源授權。
- **韓國 Where Is My Bus**：[專案](https://github.com/Code-for-Korea/where-is-my-bus)。[PR #17](https://github.com/Code-for-Korea/where-is-my-bus/pull/17)（2026-09-17）加入站點、路線與車輛連接；[PR #18](https://github.com/Code-for-Korea/where-is-my-bus/pull/18)（同日）補齊車輛建立與司機 onboarding；[PR #19](https://github.com/Code-for-Korea/where-is-my-bus/pull/19)（2026-09-18）改善註冊 PIN 與失敗重試。適合討論鄉村／離島交通資訊。
- **後續候選 BirdXplorer**：[專案](https://github.com/codeforjapan/BirdXplorer)，可供討論跨語言公共討論與 Community Notes 資料分析。[PR #292](https://github.com/codeforjapan/BirdXplorer/pull/292)（2026-09-16）是人工提交的資料匯入競態修復。這類內部維護仍不應直接當社群新聞。
- **Code for Korea 官方新聞**：[新聞頁](https://codefor.kr/boards/news)、[RSS 2.0](https://codefor.kr/boards/news.xml)。新聞頁 HTML 明列此 feed；2026-09-19 實測無須認證即可取得 XML，含 12 筆文章。description 是截短摘要；採集保留原文連結，不把摘要當全文。透過 `REP0RTER_RSS_FEEDS` 啟用後，與其他來源共用每小時採集及編輯流程。

所核對的 mapprint、BirdXplorer、JibungotoPlanet、where-is-my-bus、workercare、ansimi 目前 GitHub release 清單皆為空；不能把最新 push 或 dependency bot 當成「新版本推出」。更新入口是各 repo 的公開 issue／merged PR；release endpoint 保留供未來正式公告。未核實正式 Mastodon 帳號，所以不預填。

## 實際採集驗證

RSS adapter 另以獨立暫存 SQLite、擴大時間窗驗證新聞 feed：兩次 HTTP request 均成功，共保存 12 筆文章；第二次確認 12 筆重複、沒有新增重複事件。此測試沒有寫入 production DB。正常採集維持既有兩天窗口，不自動把歷史公告當作新消息。

使用目前 adapter、獨立 `/tmp/rep0rter-source-smoke/two-day-current.sqlite`、兩天窗口與上述兩個來源進行 HTTP smoke test：8 次 request、2,200,452 bytes、3 筆人工 PR、0 個 source error；候選均來自 where-is-my-bus #17／#18／#19。沒有 LLM 呼叫、Telegram 推播或 production DB 寫入。

Mapprint 兩天內沒有合格新訊息，因此此輪保持安靜。日文 `概要／内容／変更内容`、韓文 `요약／주요 변경／변경 사항`、英文 `Summary` 段落需要同時有具體公民用途與功能影響，才通過 PR 來源資格；單純標題或泛泛 Summary 不足。仍需後續共同選稿及證據契約，不能把 source eligible 解讀成已自動發布。
