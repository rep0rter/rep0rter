# 四語與來源圖卡：2026-09-18 實作範圍

本次讓新報導一次產生 zh-TW／ko／ja／en，保存有效版本，缺少的翻譯可分批補齊。
網站、RSS、永久文章頁與來源頁使用同一份 SQLite；語言切換保留文章位置。
每篇圖卡只呈現原文摘錄、作者與來源，公開頭貼／logo 缺少時使用縮寫。

圖片流程參考 [chumei](https://github.com/skyhong2002/chumei/blob/main/scripts/render_source_covers.py)
的內容區塊截圖、快取與備援思路；此處獨立實作本機 HTML 排版，不直接執行外站 HTML。
工作區既有 assets/logo.png 納入本站 header、favicon 和分享預覽。

## 架構取捨

按使用者要求，已透過 Claude CLI 取得第二意見。採納原文圖卡、固定文章網址、逐語言
驗證與缺少標示、發送結果不明需人工核對、優先資料正確性與發布可靠性等建議。
維持既有數字 post ID 與根目錄繁中網址，避免更動已發布連結／RSS GUID；翻譯先以
posts.translations JSON 儲存，讓既有部署可以小幅 migration。日後若需要逐語言編輯審核、
版本追蹤，再拆分翻譯資料表。此次沒有新增 Discord、GitHub、Mastodon collector。

## Issue 對應與剩餘工作

| Issue | 本次完成 | 尚未完成 |
|---|---|---|
| [#3](https://github.com/rep0rter/rep0rter/issues/3) | rich_text／附件文字還原、公開引用來源與作者識別、不確定公開性的引用不擷取 | 主題合併、討論串新版本、缺 root 補抓、退出規則傳播 |
| [#6](https://github.com/rep0rter/rep0rter/issues/6) | 精確時間戳、重複頁與無進度防護、跨頁重複資料合併 | 持久化 after 游標、重疊回掃、舊主文更新排程 |
| [#11](https://github.com/rep0rter/rep0rter/issues/11) | 修正 JOIN ID 碰撞，離線 fixture、migration／多語／圖卡／發布失敗測試，Python 3.12 CI | 完整編輯品質 golden corpus、所有故障情境與長期回放 |
| [#12](https://github.com/rep0rter/rep0rter/issues/12) | 既有 logo／favicon／OG、四語永久單篇頁與來源頁、Telegram/RSS 回站入口、手機／鍵盤／深淺色 | 配合退出機制撤回舊頁／OG／卡片快取 |
| [#13](https://github.com/rep0rter/rep0rter/issues/13) | 一篇一圖、正確 chat/message mapping、持久 outbox、lease、防盲目重送、測試 chat 不退回正式、長度與 HTML 檢查 | 舊合併訊息遷移、Telegram 編輯／刪除操作與撤回重建 |

以上是部分 issue 的可交付修正，不能將整張 issue 都標為完成。#2 選稿重設、#4 主題去重、
#5 採集延遲量測、#7 完整退出／撤回、#8 完整 LLM 證據契約、#9 備份／健康告警、
#10 多來源模型仍需各自完成；新增 CI 與結構驗證不等於已完成其全部需求。

## 驗證方式

自動測試全程使用暫存資料、合成 fixture 與 mocked transport；在載入應用程式前停用
.env，封鎖實際網路。CI 指定 Python 3.12。瀏覽器版以本機臨時快照驗證四語切換、
圖片、手機排版及永久網址，未推播正式 Telegram。翻譯和建站都可以獨立操作。
