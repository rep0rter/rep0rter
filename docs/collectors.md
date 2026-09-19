# 增量採集與公開來源

`rep0rter.collectors.registry.collect_all` 是排程入口。Slack 維持預設來源；GitHub、Mastodon 必須明列允許來源，不會自動擴張追蹤範圍。

```dotenv
REP0RTER_GITHUB_REPOS=owner/repository,another/project
REP0RTER_MASTODON_ACCOUNTS=https://social.example/@civic
REP0RTER_COLLECT_REQUEST_BUDGET=80
REP0RTER_COLLECT_DAILY_BUDGET=1500
```

GitHub 使用公開無認證 REST API：先確認 repository `private=false`，擷取已發布 release、具有明確協作標籤（`help wanted`、`good first issue`、`collaboration` 等）的 issue，以及有 `Impact`／`Outcome`／`成果`／`影響` 段落的 merged PR。release 至少需要 80 字元實質說明；issue 除標籤外需要 40 字元以上正文及明確協作邀請；PR 至少需要 40 字元的影響說明；日文／韓文／英文 Summary 類段落則須同時具備具體公民用途與功能效益、至少 100 字元。所有原始候選仍經編輯政策，標籤不是自動推播許可。Mastodon 只接受配置 URL 完全匹配的本機 account 與 `public` 原創 status；保留 CW，boost 不建立獨立候選，回覆保存為不同 kind，不獨立當新聞。物件 ID 以完整 canonical URI 計算，不會讓不同 instance 的 numeric ID 碰撞。

帳號顯示名稱、repo stars、followers 不作通用新聞分數。事件 metadata 帶 `source_instance`、`external_id`、`canonical_object_id`、`visibility`、`content_format`、`plain_text`、`updated_at`、`observed_at`、typed `engagement`、`relations`。Slack mrkdwn、GitHub Markdown、Mastodon HTML 分別正規化。未知可見性拒絕；來源排除在請求前處理，作者／引用排除在入庫前再檢查。Mastodon 每輪循環重查最多 4 個已存 status；404/410 或變成非公開會清空文字並記錄刪除狀態，供共同撤回流程移除網站與 delivery。

## 自動化雜訊

依使用者偏好，Slack GitHub integration 與其他 bot/app 訊息、GitHub `type=Bot`、`[bot]`、dependabot／renovate／github-actions，以及 dependencies／CI／chore 等自動維護標籤或標題預設在入庫前排除，不送給 LLM。Slack 原始 `user.is_bot`、`bot_id`、`app_id`、`subtype` 會保留以供一致判斷；舊資料的 GitHub integration 顯示名亦被阻擋。Mastodon bot account 同樣不作新聞。

若編輯確實要納入某筆 bot 發布的實質成果，可用 `REP0RTER_GITHUB_EDITORIAL_OVERRIDES=github:owner/repo:release:123` 指定完整、單筆事件 ID。這只略過自動化來源排除，不會繞過可見性、使用者退出、內容資格或編輯選稿。

## Slack 游標與補抓

每個 channel 的 `collector:v1:slack:<id>` JSON 保存 `last_complete_ts`（十進位原始字串）、首頁計數與時間、上次嘗試／成功／回掃、缺口原因。第一次從兩天回填；後續從完整高水位往前重疊兩小時。第一頁 `after=L`，下一頁改成 `before=min(ts)`，不會同時送兩者。短頁繼續、重複 ID 合併、游標必須遞減。

事件與完成高水位在同一個 SQLite transaction 寫入。每個 scan 最多 30 頁，並按本輪剩餘請求與待抓頻道數限制分頁，避免單一繁忙頻道耗盡全部額度；請求／分頁 budget 用完時保存 pending lower/high/before，下一輪續掃，完整高水位不會提前前進。程序在提交前中断不留半個游標。完整同步之後不會把游標直接跳成現在。額度不足屬於待補工作，下一輪立即重試，不套用來源錯誤的一小時退避；尚未發出 HTTP 的頻道保留原本嘗試／回掃時間，按最久未嘗試排序優先補抓。回掃時間只在完成時更新，所有未完成缺口仍列入健康狀態，不會因延後而變綠。

首頁訊息數或 last-posted 改變會觸發增量，不以 last-synced 代表可見性。近期頻道每六小時回掃 48 小時；另以固定上限每輪挑兩個最久未檢查的舊頻道回掃七天。未解析的 thread parent 與近期／較舊 root 共用持久 bounded queue，每輪最多 4 次精確 timestamp 補抓；找不到 root 留 `context_incomplete`，不拿鄰近訊息替代。已觀測的 reaction 可以下降，歷史高點另存在 `engagement_high_water`。

上游在 SQL limit 之後才移除 subtype，而且沒有提供 raw-page bounds：空頁無法區分真正結尾與「整頁被過濾」。這種情況會保存已看到的事件、記錄 gap、不推高水位，來源健康顯示 degraded。重複同時間戳群、無進展頁亦然。這是上游協定限制，無法靠客戶端保證穿越；需要上游提供 stable opaque cursor 或 raw bounds。有限重疊／七天抽查也不能保證找到任意晚到、從未觀測過的舊訊息。

## 節流、健康及延遲量測

所有來源共用至少 0.5 秒間隔與每輪／每日 budget；已送出的失敗請求、重試都計入。每日 quota 在 HTTP 之前持久預留，程序崩潰不會退回配額。已配置來源平分剩餘每輪 budget，單一來源失敗隔離；GitHub/Mastodon 的 rate-limit reset/Retry-After 保存後延至下一輪，不阻塞排程睡眠。

`collector_health` 包含 `healthy`、`last_healthy_at`、`last_attempt_at`、`finished_at`、`containers`、`failed_channels`、逐來源健康與 `metrics`：requests、bytes、pages、new_events、updated_events、duplicate_payloads。每輪同時存到 `collector_metrics:<epoch_ms>`，保留 30 天供比較。首頁零頻道、有效頻道數突然跌到最後有效目錄的一半以下，都不會被視為成功，且不推進游標；最後健康時間不會被失敗覆蓋。事件另存 bootstrap、recovery、fetched_at。沒有捏造 archive_first_seen，總採集延遲不能解讀成純上游同步延遲。

兩週真實請求／漏事件比較需要部署後持續觀測；此實作沒有用預估節省量代替實測，也沒有更改每小時採集週期。

## 官方契約與離線驗證

- [GitHub releases API](https://docs.github.com/en/rest/releases/releases)
- [GitHub issues API](https://docs.github.com/en/rest/issues/issues#list-repository-issues)
- [GitHub pull requests API](https://docs.github.com/en/rest/pulls/pulls#list-pull-requests)
- [Mastodon account statuses](https://docs.joinmastodon.org/methods/accounts/#statuses)
- [Mastodon Status entity](https://docs.joinmastodon.org/entities/Status/)
- [Archive upstream controller](https://github.com/ronnywang/g0v-slack-archive/blob/00dbb3d6dee294c1da3f124def7f68beadf921d7/webdata/controllers/IndexController.php)

`tests/test_collectors_incremental.py` 與 `tests/test_public_sources.py` 使用合成 API 回應與離線 transport，覆蓋多於 100 筆、短頁、after/before、晚到與重複、空 subtype／0 回應、resume、交易中斷、reaction 撤回、舊 root 補抓、來源隔離、每日 quota、公開性、CW、boost、跨 instance、退出與刪除。

已核對的小型日韓來源與實際 HTTP 驗證見 [FtO 來源](fto-sources.md)。Slack 頭貼支援 24/32/48/72/192/512/1024/original，明確 is_custom_image=false 時使用名字縮寫，不冒用預設圖案。
