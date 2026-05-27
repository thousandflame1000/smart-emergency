# 🏘️ 鄰里守望平台

> **平時照顧長者，災時守護社區**  
> 2026 第十屆全國慈悲科技創新競賽 參賽作品

[![Railway](https://img.shields.io/badge/deployed-Railway-blueviolet)](https://smart-emergency-production-d744.up.railway.app)
[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110-green)](https://fastapi.tiangolo.com)

---

## 🎯 解決的核心問題

| 族群 | 痛點 | 我們的解法 |
|------|------|-----------|
| 獨居長者 | 身體不適時無法即時求救 | LINE 每日打卡 + 兩小時內無回應自動通知家屬 |
| 志工 | 災時找不到附近物資 | 即時地圖顯示社區物資 / 庇護所 |
| 志工 | 不確定急救 / 長照 SOP | AI 助手（RAG）直接在 LINE 回答 |
| 管理員 | 缺乏統一指揮介面 | 即時儀表板 + 一鍵切換緊急模式 |

---

## 🚀 系統架構

```
LINE Bot ──> FastAPI (Railway)
               ├── 打卡 Scheduler (APScheduler, 每天 08:00)
               ├── 未回應偵測 (每 15 分鐘)
               ├── 物資 API
               ├── RAG 查詢 API (Gemini Embedding + Cosine Search + Gemini Flash)
               ├── 管理員儀表板 (Bootstrap 5 + Leaflet)
               └── PostgreSQL (Railway)
```

---

## ✨ 核心功能

### 1. 長者日常打卡
- 每天早上 08:00 自動推送 Flex Message 給所有已綁定長者
- 長者按「✅ 我很好」→ 打卡成功，記錄到 DB
- 1 小時未回應 → 自動通知家屬
- 3 小時未回應 → 自動通知志工上門探視

### 2. 一鍵求助
- 長者按「🆘 需要幫忙」→ 立即觸發警報，通知所有關聯家屬 + 志工

### 3. AI 急救 / 長照助手
- 知識庫涵蓋：**CPR、哈姆立克法、中風辨識、低血糖急救、失溫處理、地震應變、颱風準備**
- 志工只需在 LINE 輸入問題，幾秒內得到精準 SOP 回答
- 使用 `gemini-embedding-001`（3072 維向量）+ numpy cosine similarity

### 4. 社區物資地圖
- 管理員後台管理庇護所、飲用水、急救箱、食物等物資點
- Leaflet 地圖即時顯示，可用 / 不可用一目了然

### 5. 緊急模式
- 管理員一鍵切換 → 儀表板閃紅色警示 → 廣播通知
- 物資地圖顯示所有緊急站點
- 恢復後自動回到日常模式

---

## 📱 LINE Bot 使用方式

| 指令 | 說明 |
|------|------|
| `我很好` / `好` | 回覆今日打卡 |
| `需要幫忙` / `救命` | 觸發緊急求助通知 |
| `狀態` | 查看目前系統模式 |
| `幫助` | 顯示所有指令 |
| 任何 8 字以上的問題（志工/家屬） | AI 急救知識查詢 |

---

## 🖥️ Demo 連結

| 頁面 | URL |
|------|-----|
| 主儀表板 | https://smart-emergency-production-d744.up.railway.app |
| 管理後台 | https://smart-emergency-production-d744.up.railway.app/admin |
| API 文件 | https://smart-emergency-production-d744.up.railway.app/docs |

---

## 🛠️ 技術選型

| 層次 | 技術 | 理由 |
|------|------|------|
| 後端框架 | FastAPI | 高效能、自動生成 API 文件 |
| ORM | SQLAlchemy 2.0 | 支援 SQLite/PostgreSQL 雙環境 |
| 資料庫 | PostgreSQL (Railway) | 穩定、免費額度夠用 |
| 向量搜尋 | numpy cosine | SQLite 相容，無需 pgvector |
| Embedding | gemini-embedding-001 | 免費、3072 維高品質向量 |
| 生成模型 | gemini-2.0-flash-lite | 免費 tier、繁中支援好 |
| LINE SDK | line-bot-sdk v3 | 官方最新版 |
| 排程 | APScheduler | 輕量、內嵌於 FastAPI |
| 部署 | Railway | 一鍵部署、免費 PostgreSQL |
| 前端 | Bootstrap 5 + Leaflet | 無需打包、快速開發 |

---

## 📦 本地開發

```bash
# 1. 安裝依賴
pip install -r requirements.txt

# 2. 建立 .env（參考 .env.example）
cp .env.example .env
# 填入 LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN, GEMINI_API_KEY

# 3. 啟動
uvicorn app.main:app --reload --port 8080

# 4. 開另一個終端，載入測試資料
python seed.py
python ingest_kb.py

# 5. 開啟 http://localhost:8080
```

---

## 📁 專案結構

```
smart-emergency/
├── app/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 環境變數設定
│   ├── database.py          # SQLAlchemy 引擎
│   ├── scheduler.py         # APScheduler 定時任務
│   ├── models/              # SQLAlchemy Models
│   ├── routers/             # API 路由
│   │   ├── linebot.py       # LINE Webhook
│   │   ├── dashboard.py     # 儀表板 API
│   │   ├── resources.py     # 物資 API
│   │   └── rag.py           # RAG 查詢 API
│   ├── services/            # 業務邏輯
│   │   ├── checkin.py       # 打卡服務
│   │   ├── alert.py         # 警報服務
│   │   ├── rag.py           # RAG 服務
│   │   └── line_notify.py   # LINE 推播
│   └── static/              # 前端頁面
│       ├── index.html       # 主儀表板
│       └── admin.html       # 管理後台
├── seed.py                  # Demo 資料
├── ingest_kb.py             # 知識庫載入
├── startup.py               # Railway 啟動腳本
├── railway.toml             # Railway 部署設定
└── requirements.txt
```

---

## 👥 作品說明

本系統結合三大功能：

1. **日常關懷**：取代人工電話問候，讓志工能同時照顧更多長者
2. **AI 知識庫**：降低志工進入門檻，新手也能快速找到正確 SOP
3. **災時協調**：統一指揮介面 + 物資地圖，避免資源錯配

技術創新點：使用 **Local RAG**（無需付費 pgvector）實現高品質語義搜尋，同時保持零成本部署。
