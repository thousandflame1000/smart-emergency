# 事件處置工作區

`/workspace` 是決策者處理居民需求、志工、物資、資源點與照護關係的單一入口。
它提供地理位置與關係圖兩種視圖，但不是獨立的「社區地圖」產品，也不聲稱具備即時道路或交通資料。

## 正式資料投影

`GET /api/workspaces/operational-data` 以穩定識別碼建立唯讀投影：

- `db:person:{user_id}`：居民、家屬、志工與管理員。
- `db:need:{need_id}`：需求單與 SOS。
- `db:res:{resource_id}`：志工或管理員登記的物資。
- `db:point:{point_id}`：資源點。
- `db:edge:*`：持有、提出需求、照護及派遣關係。

`POST /api/workspaces/database-merge` 會重建資料庫投影，同時保留手動建立的事件物件、關係與版面。
資料庫紀錄被刪除後，其投影與相連的手動關係也會移除，不留下幽靈物件。

## 資料完整性

- 物件、關係與物資紀錄的識別碼在同一工作區中必須唯一。
- 關係的起點與終點必須存在，而且不可指向自己。
- JSON、CSV、GeoJSON 點資料匯入遇到重複識別碼會拒絕，不會覆蓋既有物件。
- 舊工作區中的 `road_node`、`road`、`access` 會在讀取時轉成一般 legacy 物件或關係，不再啟用道路語意。
- 正式物資寫回採「預覽後套用」，以資料列版本檢查整批衝突；其中任一筆過期時整批拒絕。
- 新物資的擁有者只能是啟用中的志工或管理員，前端與後端都會檢查。

## 關聯分析與比較

關聯分析只計算啟用物件、啟用關係、資料群組、未連結物件、橋接關係與關鍵物件。
它用來找資料缺口與單點依賴，不代表現場道路、通訊或設施一定中斷。

情境比較固定一份不可變基準，列出物件與關係的新增、移除、修改及連結狀態變化。
資料排序與畫布座標不影響 SHA-256 指紋，刪除或停用物件不會被誤算為完成處置。

## 物資分配

分配引擎只使用明確填寫且品項、單位一致的整數數量：

1. 依需求優先級由 5 至 1 最大化滿足量。
2. 同優先級再最小化供應點與需求點的直線距離總和。
3. 遵守庫存、單筆出貨上限、可用狀態與距離上限。
4. 缺座標、超過距離上限或數量不明的紀錄不會被偷偷推測。

直線距離只供候選排序，不是道路里程、通行狀態或抵達時間。試算本身不扣庫存、不建立派遣、不通知 LINE。
只有使用者把資料庫來源的建議送到調度後，系統才建立 `suggested`；管理員確認後才進入正式派遣與通知流程。

## API

| 方法 | 路徑 | 行為 |
|---|---|---|
| GET/POST | `/api/workspaces` | 列出或建立工作區 |
| GET/PUT | `/api/workspaces/{id}` | 讀取或以 revision 更新工作區 |
| POST | `/api/workspaces/import-preview` | 預覽 CSV、GeoJSON 點或工作區 JSON 匯入 |
| POST | `/api/workspaces/analyze` | 分析關聯完整性，不儲存 |
| POST | `/api/workspaces/compare` | 比較兩份資料快照，不儲存 |
| POST | `/api/workspaces/allocate` | 試算物資分配，不儲存 |
| GET | `/api/workspaces/operational-data` | 讀取正式營運資料投影 |
| POST | `/api/workspaces/database-merge` | 將正式投影併入工作區，不儲存 |
| POST | `/api/workspaces/database-diff` | 預覽正式物資變更 |
| POST | `/api/workspaces/database-push` | 經版本檢查後套用物資變更 |
| POST | `/api/workspaces/apply-allocation` | 建立待核准派遣建議 |

固定花東路網沙盒、道路範圍載入、颱風夜模擬與獨立社區地圖已移除，不應重新加入。
