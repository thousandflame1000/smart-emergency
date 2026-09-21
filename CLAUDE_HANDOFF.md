# Project Handoff

## Current Direction

這是以居民、志工、決策者三種角色運作的社區應變平台。目標是建立可靠的事件資料與處置流程，不是做出外觀像 Palantir 的展示頁。

固定花東路網沙盒、颱風夜模擬、獨立社區地圖、道路範圍載入及其專用 API/模型/測試已移除。不要重新加入，也不要另開一套物資、志工、調度或地圖入口。

決策者的單一操作入口是 `/workspace`「事件處置工作區」。主站 `/` 以角色導覽連到居民照護、資料管理、AI 知識庫與事件處置。

## Data Contract

- 正式物件 ID：`db:person:*`、`db:need:*`、`db:res:*`、`db:point:*`。
- 正式關係 ID：`db:edge:*`。需求、物資、持有人、照護與派遣皆使用同一資料庫來源。
- 物件、關係、物資紀錄 ID 必須唯一；關係端點必須存在且不可自連。
- `database-merge` 重建正式資料投影，只保留手動事件物件、手動關係與版面。
- 物資寫回先預覽，再以 row version 整批套用；任一衝突就整批拒絕。
- 新物資擁有者只能是啟用中的志工或管理員，後端不得只信任前端選單。
- 舊 `road_node` / `road` / `access` 資料讀取時會轉成一般 legacy 物件/關係，不再有道路語意。

## Decision Support

- 關聯分析找資料群組、未連結物件、橋接關係與關鍵物件，不聲稱現場中斷。
- 物資分配以明確品項、單位、整數數量、優先級、出貨上限與直線距離計算。
- 直線距離只做候選排序，不是道路里程、通行狀態或 ETA。
- 試算不扣庫存、不建派遣、不發 LINE；送到調度後只建立 `suggested`，管理員確認才通知。

## Role Interfaces

- 居民：LINE 平安回報、求助、需求表單、AI 知識查詢。
- 志工：LINE 申請、登記物資、可承接需求、任務接受與回報。
- 決策者：Web 事件處置、資料維護、關係/物資審查、待核准派遣與稽核。
- Rich Menu 有三套角色版本，重建功能預設關閉；不要在本機測試觸發正式 LINE 寫入。

## Deployment Status

2026-09-21 使用者確認目前網路狀況良好，原本約 22 分鐘的上傳限制暫不構成阻塞。本輪可在本機完整驗證後做一次集中 push/deploy；仍不要無意義地反覆重建 LINE Rich Menu。

本機整合預覽使用 `tests/serve_integrated_workspace.py` 與 `.workspace-preview.db`，假憑證且不啟動 scheduler。

本輪驗證：`410 passed, 1 skipped`；隔離瀏覽器流程通過物資版本寫回、同源唯一性、直線距離分配、待核准派遣、確認派遣，以及 1440x1000 / 390x844 無水平溢位與無 JS 錯誤。

## Key Files

- `app/services/workspace.py`：一般事件圖資料、匯入、唯一性與關聯分析。
- `app/services/workspace_bridge.py`：正式資料庫投影。
- `app/services/workspace_inventory.py`：物資差異、版本與寫回。
- `app/services/workspace_allocation.py`：容量、優先級與直線距離分配。
- `app/static/workspace*.js`：事件處置 UI。
- `docs/EVENT_WORKSPACE.md`：現行工作區契約。

目前是 Palantir-inspired 的事件處置原型，不是 Palantir level。仍缺細粒度授權、完整任務狀態機、數量式庫存流水帳、離線同步、來源治理與正式場域驗證。
