# 鄰里守望平台

平時長者關懷資料，是災時物資調度演算法的輸入——不是兩套系統拼接，是同一份資料在不同情境發揮作用。

[![Railway](https://img.shields.io/badge/deployed-Railway-blueviolet)](https://smart-emergency-production-d744.up.railway.app)
[![Python](https://img.shields.io/badge/python-3.11+-blue)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-green)](https://fastapi.tiangolo.com)

## 系統做什麼

| 功能 | 實作 |
|---|---|
| 長者每日打卡 | LINE Flex Message，1 小時未回應通知家屬、3 小時通知志工 |
| 一鍵求助 | LINE 按鈕觸發警報，依關係層級通知家屬/志工 |
| AI 急救/長照問答 | Gemini embedding + cosine similarity 的本地 RAG，LINE 內直接問答 |
| 物資媒合 | 脆弱度加權評分 + 匈牙利演算法批次最佳指派（非貪婪逐筆） |
| 決賽情境模擬 | 颱風風場模型驅動的災害情境引擎，用於現場展演 |

## 架構

```
LINE Bot ──▶ FastAPI (Railway)
              ├─ 排程：每日打卡 08:00 / 未回應偵測 15min / 自動媒合 30min
              ├─ /api/resources   物資與需求
              ├─ /api/dashboard   管理端點
              ├─ /api/rag         知識庫問答
              ├─ /api/scenario    情境模擬引擎
              └─ PostgreSQL (Railway) / SQLite（本地開發）
```

## 派遣演算法

`app/services/dispatch.py`：

- 評分 = 緊急度 + 脆弱度 + 類型親合度 + 等待時間懲罰 − 距離懲罰 − 志工負荷懲罰
- 脆弱度（0–28）由打卡異常次數、未解警報數、照顧網絡孤立度即時計算，不是使用者自報的數字
- 個人物資（稀缺資源）用批次匈牙利演算法（`app/services/hungarian.py`，純 Python 實作，見下方「已知限制」）求全域最佳指派，取代貪婪逐筆處理——貪婪法會讓先處理的需求搶走對另一筆需求而言唯一可行的資源，即使有更好的整體解
- 資源點（固定設施，非稀缺）維持逐筆獨立比對

方法論依據見 [`RESEARCH_disaster_logistics.md`](RESEARCH_disaster_logistics.md)（13 篇指定文獻的可驗證程度逐一標註，2 篇取得全文、其餘標為摘要層級，不假裝讀過讀不到的內容）。

## 情境模擬引擎

`app/services/hazard.py` + `app/services/scenario.py`：用真實颱風物理模型（Holland 1980 風場模型、Kaplan-DeMaria 1995 登陸衰減模型）驅動需求生成，不是寫死的劇本。路徑錨點取材自 2024 年康芮颱風的公開報導數據；誰通報、何時通報、緊急度多少，由風速與該居民的實際脆弱度分數計算決定。

決賽現場操作：`/admin` → 決賽展演 → 情境模擬 → 開始情境 / 逐步推進 / 自動播放。

## 已知限制

誠實列出，不在提問環節被動承認：

- 單一 Railway 容器，無備援；Railway 停機系統就停機
- `/admin`、`/`（同為操作主控台，兩者都能改資料）僅靠共用密碼保護（`DEMO_PASSWORD` 環境變數），無使用者分級權限
- 志工身份無驗證機制，需社區組織在真實導入時另行把關
- 派遣評分係數是初始 heuristic，尚無真實試辦資料校正
- 情境模擬的颱風路徑逐時座標為內插，非官方最佳路徑資料
- 詳細落差分析見 [`PRODUCT_GAP_ANALYSIS.md`](PRODUCT_GAP_ANALYSIS.md)

## Demo

| 頁面 | URL |
|---|---|
| 主控台 | https://smart-emergency-production-d744.up.railway.app |
| 管理後台 | https://smart-emergency-production-d744.up.railway.app/admin |
| API 文件 | https://smart-emergency-production-d744.up.railway.app/docs |

## 本地開發

```bash
pip install -r requirements.txt
cp .env.example .env   # 填入 LINE/GEMINI 金鑰
uvicorn app.main:app --reload --port 8080
python seed.py              # 基本測試資料
python seed_rich_demo.py    # 決賽用豐富示範資料（附加式，不清空既有資料）
python ingest_kb.py         # 建置知識庫向量
```

## 測試

```bash
pytest tests/ -v
```

無須真的 LINE/Gemini API 金鑰，全部跑在獨立 SQLite 檔案上，不碰正式資料庫。

## 專案結構

```
app/
├── main.py, config.py, database.py, scheduler.py, demo_auth.py
├── models/       SQLAlchemy models
├── routers/      linebot / dashboard / resources / rag / scenario
├── services/     dispatch, hungarian, hazard, scenario, checkin, alert, rag, line_notify
└── static/       index.html（主控台）, admin.html（後台）
```
