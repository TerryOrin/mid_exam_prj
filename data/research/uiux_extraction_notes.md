# UI/UX 萃取筆記（排行榜站點）

資料來源：
- `leaderboard_usr_activity_insights.json`
- `leaderboard_usr_activity_insights.md`

## 觀察到的高頻優點

1. 首屏有明確標題與導覽路徑（Hero）
2. 活動/故事/USR 使用卡片式摘要，閱讀負擔低
3. 活動頁普遍提供時間、地點、摘要三要素
4. 多數站點具備聯絡入口（表單或聯絡頁）
5. 響應式排版（Bootstrap + viewport）已成為基本要求
6. 一部分站點有搜尋或分類，能提升資訊查找效率

## 可落地的設計規則

1. 先看懂再互動：首頁 5 秒內可看到「平台定位、內容分類、可點擊入口」
2. 一致的資訊卡結構：`標籤 → 標題 → 摘要 → 時間/地點 → 行動按鈕`
3. 文字可讀性優先：高對比、字距穩定、行高 >= 1.6
4. 行動裝置優先：卡片可在手機單欄完整瀏覽，按鈕尺寸足夠點擊
5. 後台可維護：前台顯示內容必須直接對應 Event/StoryPost/HeroSlide

## 本次改版映射

1. `core/templates/core/home.html`
  - Hero + KPI + 三大內容區（活動/故事/USR）
  - 新增「改版萃取重點」區塊
2. `core/templates/core/events_list.html`
  - 搜尋欄 + 卡片化活動列表 + 分頁
3. `core/templates/core/usr.html`
  - 分類篩選（全部/USR/體驗活動/AIoT）
4. `core/templates/core/contact.html`
  - 聯絡資訊與表單一致化
5. `static/css/style.css`
  - 統一色彩系統、卡片規範、RWD 斷點、互動樣式

