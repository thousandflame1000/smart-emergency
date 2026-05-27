# 鄰里守望平台 — 系統架構圖

```
┌─────────────────────────────────────────────────────────────────┐
│                        使用者端                                   │
│                                                                   │
│   📱 長者 LINE          📱 志工/家屬 LINE       💻 管理員瀏覽器   │
│   └─ 打卡回覆            └─ 警報通知              └─ Dashboard    │
│      求助按鈕               確認安全                  Admin 後台  │
└──────────┬───────────────────────┬────────────────────┬──────────┘
           │ HTTPS                 │ HTTPS              │ HTTPS
           ▼                       ▼                    ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Railway 雲端主機                               │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────┐     │
│  │                  FastAPI Application                     │     │
│  │                                                          │     │
│  │  /webhook/line   ←── LINE Platform Webhook              │     │
│  │  /api/dashboard  ←── Dashboard API                      │     │
│  │  /api/resources  ←── 物資管理 API                        │     │
│  │  /api/rag        ←── RAG 查詢 API                       │     │
│  │  /               ←── 儀表板 HTML                        │     │
│  │  /admin          ←── 管理後台 HTML                      │     │
│  │                                                          │     │
│  │  ┌────────────┐  ┌──────────────┐  ┌────────────────┐  │     │
│  │  │  LINE Bot  │  │  Scheduler   │  │   RAG Engine   │  │     │
│  │  │  Handler   │  │  APScheduler │  │                │  │     │
│  │  │            │  │              │  │ ┌────────────┐ │  │     │
│  │  │ ・打卡回覆  │  │ 每天 08:00   │  │ │  Embed     │ │  │     │
│  │  │ ・求助處理  │  │ 發送打卡訊息  │  │ │  Search    │ │  │     │
│  │  │ ・自動註冊  │  │              │  │ │  Generate  │ │  │     │
│  │  │ ・警報觸發  │  │ 每小時       │  │ └────────────┘ │  │     │
│  │  └────────────┘  │ 檢查未回應   │  └────────────────┘  │     │
│  │                  └──────────────┘                        │     │
│  └─────────────────────────────────────────────────────────┘     │
│                           │                      │                │
│              ┌────────────┘                      └──────────┐    │
│              ▼                                               ▼    │
│  ┌───────────────────────┐              ┌──────────────────────┐ │
│  │   PostgreSQL           │              │   Gemini API (Google)│ │
│  │                        │              │                      │ │
│  │  users                 │              │  gemini-embedding-001│ │
│  │  daily_checkins        │              │  → 3072 維向量       │ │
│  │  care_relations        │              │                      │ │
│  │  alerts                │              │  gemini-2.0-flash    │ │
│  │  community_resources   │              │  → 回答生成          │ │
│  │  community_needs       │              └──────────────────────┘ │
│  │  knowledge_base        │                                        │
│  │  (embedding: TEXT)     │                                        │
│  │  system_config         │                                        │
│  └───────────────────────┘                                        │
└──────────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────┐
│   LINE Platform       │
│                       │
│  Messaging API        │
│  → Webhook 推送       │
│  → Reply Message      │
│  → Push Message       │
│  → Get Profile        │
└──────────────────────┘
```

---

## 資料流程

### 日常打卡流程
```
08:00 Scheduler
  → 查所有 is_active 長者
  → 送 Flex Message（我很好 / 需要幫忙）
  → 建立 DailyCheckin (status=pending)

長者按「我很好」
  → LINE Postback → /webhook/line
  → DailyCheckin.status = ok

10:00 Scheduler 檢查
  → status=pending → 通知家屬（Alert）
  → 12:00 仍未回應 → 通知志工
```

### RAG 查詢流程
```
志工輸入問題
  → POST /api/rag/query
  → Gemini embed_content（問題向量化）
  → numpy cosine similarity（比對知識庫）
  → 取 Top-3 相關 SOP 段落
  → Gemini generate_content（生成回答）
  → 回傳答案 + 來源
```

### 緊急模式流程
```
管理員切換 emergency mode
  → system_config.mode = emergency
  → Dashboard 顯示紅色警示
  → 志工收到廣播通知
  → 資源地圖顯示庇護所/物資站
  → 志工可認領配送任務
```

---

## 技術選型

| 層次 | 技術 | 理由 |
|------|------|------|
| 後端框架 | FastAPI | 高效能、自動生成 API 文件 |
| ORM | SQLAlchemy 2.0 | 支援 SQLite/PostgreSQL 雙環境 |
| 資料庫 | PostgreSQL (Railway) | 穩定、免費額度夠用 |
| 向量搜尋 | numpy cosine | SQLite 相容，無需 pgvector |
| Embedding | gemini-embedding-001 | 免費、3072 維高品質向量 |
| 生成模型 | gemini-2.0-flash | 免費 tier、繁中支援好 |
| LINE SDK | line-bot-sdk v3 | 官方最新版 |
| 排程 | APScheduler | 輕量、內嵌於 FastAPI |
| 部署 | Railway | 一鍵部署、免費 PostgreSQL |
| 前端 | Bootstrap 5 + Leaflet | 無需打包、快速開發 |
