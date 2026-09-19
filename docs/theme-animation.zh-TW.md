# 主題揭露、全域符文與同頁語言切換

原版主題揭露與全域逐字動畫已合併。本次依最新要求撤回畫面緩衝、捲動接續與字元排程最佳化，恢復原版逐字效果；另外加入播放期間捲動鎖。預覽使用獨立資料庫與既有公開資料快照，不連接正式發布流程。

本機預覽：<http://127.0.0.1:8765/index.html?lang=ZH>。在右上外觀選單切換深淺色，或使用語言選單；也可以先捲至文章中段再換語言。

## 使用者操作

1. 新配色從右上外觀按鈕的實際中心向外揭露，兩個方向均為 **500 ms、ease-in-out**。捕捉畫面前先關閉選單，初始 CSS 遮罩與動畫使用相同按鈕座標；捲動後仍依視窗座標測量。轉場結束後恢復文章與導覽列原本的具名快照。
2. **全站目前可見文字**會出現符文變換：導覽標籤、標題、摘要、內文、按鈕文字與頁尾都可參與，每個字元的動畫約 **760 ms**、每 **70–110 ms** 才替換一次符號。主題切換時依圓形抵達各字元的位置錯開開始；同頁換語言完成後播放新文字。
3. 網址使用 `?lang=ZH`、`EN`、`JA`、`KO`。語言選單留在同一份 document，以 fetch 取得既有翻譯後更新內容；不整頁重新載入。依目前可見閱讀段落校正不同語言造成的高度差，保留搜尋條件、展開狀態及焦點。更新只增加一次同步位置校正，不保存或重播跨頁捲動狀態。
4. 語言選擇使用 `history.replaceState`，避免偏好切換塞滿瀏覽紀錄。文章／來源頁連結與搜尋表單攜帶目前語言；另開分頁會帶最新搜尋條件。快速操作只採用最後選擇；載入失敗時保留原頁、網址與閱讀位置，並提供可重試的提示。
5. 首次載入、裝置配色變化、跨分頁外觀同步及相同實際配色不播放主題動畫。減少動態、強制色彩、文字選取或啟用控制項時，略過／停止符文。動畫期間滑鼠滾輪、觸控滑動及捲動按鍵暫停作用，結束後自動恢復。輸入內容、選單原生選項、SVG、隱藏內容與開啟中的原生圖片 modal 維持正常呈現。

## 架構與限制

語言切換對使用者呈現為同頁網址；**既有四語 HTML 仍保留為內部內容來源與無 JavaScript 備援**，不新增包含全文的全域翻譯 JSON。這保留原有 RSS、搜尋索引及緊急撤回流程。搜尋及圖片浮層在內容更新前清理舊事件，更新後同步重新綁定，不累積過期節點或監聽器。

字元效果以 CSS Custom Highlight 隱藏精確的文字範圍，再加上不接收操作的裝飾層，原始 DOM、排版、複製內容及輔助科技名稱不變。使用 `Intl.Segmenter` 保留 CJK、組合字及 emoji。保留原版整頁文字走訪，只為畫面中可見且未被遮擋的文字建立動畫，每次最多 1600 個字元，超過範圍維持原文。停止時清除動畫、highlight 與裝飾節點；不會在捲動後自行重播。

缺少 View Transitions 時主題直接切換；缺少 Custom Highlight 或 Segmenter 時維持原文字。JavaScript 停用時使用原生四語連結；`?lang=` 的同頁增強需要 JavaScript。帳戶／投稿頁保留原本禁止 JavaScript 的安全政策。

## 動畫期間暫停捲動

圓形揭露與符文效果各自持有一份捲動鎖，兩者都結束才解除；取消、快速切換或降級也會釋放自己的鎖。`theme.js` 提供共用的 `Rep0rterScrollLock`，`theme-transition.css` 以 root overflow 與穩定 scrollbar gutter 保持閱讀位置及版面寬度，並阻擋滾輪、觸控移動與頁面捲動按鍵。不使用 body fixed 或 scrollTo 補償。觸控滑動不會因 pointerdown 提前取消效果；輸入欄位、真正的控制項操作與文字選取仍保留正常使用方式。

這是固定畫面播放的互動調整，沒有宣稱消除原版逐字動畫的初始化或 GPU 成本。

## 修改位置

| 範圍 | 檔案 |
|---|---|
| 主題狀態、按鈕圓心及取消競態 | [theme.js](../rep0rter/templates/theme.js)、[theme-transition.css](../rep0rter/templates/theme-transition.css) |
| 全域字元測量、符文及清理 | [enchantment.js](../rep0rter/templates/enchantment.js)、[enchantment.css](../rep0rter/templates/enchantment.css) |
| 同頁語言、網址、閱讀位置與載入提示 | [language.js](../rep0rter/templates/language.js)、[language.css](../rep0rter/templates/language.css) |
| 內容更新後的操作生命週期 | [reading.js](../rep0rter/templates/reading.js)、[image-viewer.js](../rep0rter/templates/image-viewer.js) |
| 頁面與資產發布 | [index.html](../rep0rter/templates/index.html)、[withdrawn.html](../rep0rter/templates/withdrawn.html)、[site.py](../rep0rter/publishers/site.py) |

持久回歸測試位於 `tests/test_theme_reveal.py`、`test_language_position.py`、`test_image_viewer_lifecycle.py`、`test_theme_cycle.py`、`test_faceted_search.py`、`test_preference_menus.py`、`test_reading_controls.py` 與 `test_site.py`。配色及減少動態效果使用獨立 MediaQueryList mock；語言測試依最新的同頁、保留位置需求更新。

## 最新驗證

本次復原與鎖定調整：完整 Python 測試 **714 passed（15.82 秒）**。瀏覽器回歸腳本為 [verify-enchantment-scroll-lock.py](../scripts/verify-enchantment-scroll-lock.py)，取代先前的畫面緩衝驗證。主按鈕及側欄間距修改紀錄見 [網頁設計](web-design.zh-TW.md)。

獨立 Chromium 的 **4 組捲動鎖檢查通過**：桌機滾輪／PageDown、手機模擬觸控滑動、動畫期間位置與版寬不變、完成後恢復捲動；另涵蓋圓形與符文各自持鎖、快速切換、減少動態、不支援 API、選取、元件移除及 modal。不代表已用實體 iPhone／Android 驗證。

以下為原版動畫的先前驗證紀錄：

- Python 3.12：**521 passed，15.75 秒**。本機圖卡測試使用現有 Arial Unicode 字型，未修改正式圖卡設定。
- 按鈕圓心：**8 組**手機／桌機、頁首／900px 閱讀位置、深淺雙向；另 **2 組**替換導覽後重新操作檢查。圓心與按鈕中心完全相同。
- 全域符文：**16 組**四語、手機／桌機、頁首／中段；一般畫面 108–622 個可見字元。另驗證長段落、組合字、emoji、連續操作、DOM 變動、選取、減少動態及 API 降級。
- 同頁語言：手機／桌機共 **16 次**四語切換，其中 **8 次**對比目前閱讀段落位置。`performance.timeOrigin` 保持不變；閱讀段落、搜尋值及圖片浮層操作正常。另在 320／768／1440px 各觀察 100 個動畫畫面，閱讀段落最大偏移僅 0.41px，未回彈至頁首。
- 語言邊界：**5 組**包含篩選與原文展開、舊請求晚回覆、HTTP 失敗與重試、文章／搜尋／返回、English 舊別名與撤回頁。

以上是獨立 Chromium 本機檢查；尚未以 Safari、Firefox 或實體手機驗證，也未宣稱達成固定 FPS。

## 參考

圓形效果參考 [skating.tw](https://skating.tw/en/)；新畫面的即時呈現依據 [W3C View Transitions 規格](https://www.w3.org/TR/css-view-transitions-1/)。符文使用簡單幾何字元，沒有使用遊戲資產。
