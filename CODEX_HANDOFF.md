# Codex Handoff

## Non-Negotiable Decisions

1. 固定花東路網沙盒、颱風夜模擬、獨立社區地圖與道路範圍載入已退休，不能復活。
2. 決策者只從 `/workspace` 進入事件處置；居民、志工操作維持 LINE-first。
3. 唯一性、來源、版本衝突與狀態轉換比視覺模仿 Palantir 更優先。
4. 2026-09-21 使用者確認目前網路狀況良好，可在本機驗證後做一次集中 push/deploy；LINE Rich Menu 仍只在選單內容確實變更時重建。

## Current Architecture

- `operational_snapshot()` 將 SQL 資料投影成穩定的 `db:*` 物件與 `db:edge:*` 關係。
- `merge_database()` 每次重建正式投影，保留手動事件物件、手動關係與 `_layout`。
- `GraphDocument` 驗證物件、關係與 logistics ID 唯一，並驗證所有關係端點。
- legacy `road_node` / `road` / `access` 只在讀取時正規化成 generic object/relation。
- allocation v2 不讀道路；以明確庫存、需求優先級、出貨上限與 Haversine 直線距離做最小成本流。
- 物資寫回使用 preview/apply 與 row version。新物資 owner 限 active volunteer/admin。
- 派遣試算只建立建議；正式通知仍須管理員確認。

## Product Surfaces

- `/`：角色化 shell。
- `/view/dashboard`：照護總覽、長者狀態、趨勢、AI。
- `/workspace`：事件、需求、物資、人員、照護關係、關聯比較與待核准派遣。
- `/admin`：資料管理與帳號/關係後台，不再暴露舊調度、路網或模擬入口。

## Local Verification

- 隔離預覽：`tests/serve_integrated_workspace.py`，SQLite `.workspace-preview.db`，假 LINE/Gemini 憑證，scheduler 關閉。
- 瀏覽器流程：`tests/workspace_integration_browser_check.py`，可用 `WORKSPACE_PREVIEW_URL` 指定本機 port。
- 完成修改後應跑 `pytest -q`、Python compile/import、JS 瀏覽器錯誤與 1440x1000 / 390x844 overflow 檢查。
- 2026-09-21 已驗證：`410 passed, 1 skipped`；隔離 Playwright 端到端流程通過，OpenAPI 無 scenario、road-network 或 openstreetmap 路由。

## Honest Gap

目前不是 Palantir level。缺少 ABAC/RBAC 欄位級授權、完整任務 FSM、可稽核的數量庫存流水帳、離線衝突合併、完整 provenance 治理、經驗證的最佳化模型及正式場域演練。

現行契約見 `docs/EVENT_WORKSPACE.md`；產品缺口見 `PRODUCT_GAP_ANALYSIS.md`。
