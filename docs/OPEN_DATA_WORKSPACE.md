# 開放資料工作區

入口：`/workspace`，亦可從管理中心或儀表板開啟。首次開啟顯示選區視窗；每個工作區具有獨立名稱、圖資料與版本，不依賴花東示範情境。

## 選擇地區

輸入縣市、行政區或地標名稱並搜尋，選擇結果及中心周邊距離，再按「建立地區工作區」。系統載入該中心周邊矩形範圍的真實 OSM 道路與公開設施，自動儲存為新工作區，不覆蓋既有工作區。這不是整個行政區邊界的匯入；沒有資料時會提示調整範圍，不建立空白工作區。

公開設施包含醫療、消防、警政、學校、社福與商店位置。保留原始標籤；建物與關係使用 OSM 回傳的包圍框中心，並非已查證入口。設施的營運狀態與容量標示為未知、數量設為零，不推定為可供應物資。人員名冊與物資庫存須另外匯入，可使用圖層旁的匯入按鈕。

瀏覽器記住最近儲存或開啟的工作區，再次開啟 `/workspace` 會恢復；網址的 `?id=` 優先。後續拓樸編輯仍須按儲存。

## 資料格式

- GeoJSON：WGS84（EPSG:4326），座標順序為經度、緯度。支援 Point、LineString、MultiLineString。線段轉為道路，點資料可指定為人員、物資、設施或自訂物件。Polygon 等其他幾何會在預覽中列出略過數量。
- CSV：UTF-8、逗號分隔、首列欄位名稱。匯入預覽可對應識別碼、名稱、緯度、經度、數量。沒有座標的物件仍能在關係圖操作；只缺少一個座標的資料會拒絕匯入。
- 關係 CSV：對應起點、終點識別碼，必須對應已存在的物件。可使用 `road`、`access`、`assignment`、`supplies`、`custom` 關係類型，另有自由填寫的關係名稱。
- OpenStreetMap JSON：支援 Overpass `out body` 回傳的 nodes 與 ways。保留共用節點、單行方向與原始道路標籤。
- 工作區 JSON：使用匯出功能取得，包含完整節點、連線、來源與原始屬性，可匯回或另存副本。

單次檔案上限 5 MB，工作區上限 12,000 個節點、24,000 條連線。大範圍資料可分成多個工作區。標籤不是識別碼；重複識別碼會拒絕匯入。重疊 OSM 範圍則依 OSM 識別碼去重並保留既有編輯。

GeoJSON 道路只連接同一次匯入中共用的座標頂點，並區分 `layer`；不會自動把線段交叉處認定為路口。跨檔案路段可手動連接。台灣 TWD97 等投影座標須先轉換成 WGS84。

## 編輯與分析

地圖可新增、拖曳與刪除物件；關係圖可拖動排列、連接物件、修改關係。道路可設為單向、正常、緩行或封閉，並設定路速與延遲倍率。刪除物件會一併刪除其連線。復原與重做保留最近 25 次圖資料編輯。

「連接鄰近道路」依指定公尺上限，為有座標且尚無接駁的人員、物資、設施與自訂物件建立最近道路節點的接駁。這是直線接駁假設，可手動修改或刪除，並非已查證道路。

分析採用 NetworkX 的加權最短路徑、多來源 Dijkstra、連通分量、橋接邊與割點：

- 只有道路與接駁可作為通行路徑，單行方向與封閉狀態會影響可達性。
- 供應來源為可用且數量大於零的物資或設施；可達不代表品項相符或供應足量，不執行配給或派遣。
- 時間依道路端點距離、設定路速與緩行倍率估算，沒有即時交通或道路容量模型。
- 橋接邊與割點採忽略方向的路網結構；平行道路不會誤判為單一橋接瓶頸。
- 缺少座標的道路不納入路徑計算，會列在分析回應的 `ignored_edges`。

試算直接使用尚未儲存的圖資料；儲存才更新工作區。版本衝突會拒絕覆蓋，可另存副本。工作區不會自動更動既有正式派遣、LINE 通知或花東示範路網。

## API

| 方法 | 路徑 | 用途 |
| --- | --- | --- |
| GET / POST | `/api/workspaces` | 列出／新增工作區 |
| GET / PUT | `/api/workspaces/{id}` | 讀取／依 revision 儲存 |
| POST | `/api/workspaces/import-preview` | 匯入驗證與預覽，不儲存 |
| POST | `/api/workspaces/analyze` | 分析傳入的圖資料，不儲存 |
| GET | `/api/workspaces/places?q=` | 依中文或其他地名搜尋中心座標 |
| POST | `/api/workspaces/openstreetmap` | 取得目前範圍道路；`include_facilities: true` 同時載入公開設施，不儲存 |

線上道路查詢的經緯度跨度各以 0.12 度為限；依序嘗試 VK Maps、FOSSGIS、Private.coffee 三個公開 Overpass 服務，全部不可用時回傳中文錯誤。成功使用的服務名稱、來源與授權會寫入資料。查詢僅送出範圍與道路／設施篩選條件，不會送出工作區內的人員或物資資料。既有專案的 DemoAuth 設定同樣適用於工作區頁面及 API。

地名搜尋使用 Nominatim，僅在提交搜尋時送出查詢文字，不提供逐字自動完成。單一服務程序將上游請求間隔限制為至少 1.1 秒，快取最多 128 個查詢、每個 6 小時。可用 `NOMINATIM_SEARCH_URL` 切換相容服務；擴增程序或副本前需改用集中限流或自有地理編碼服務，避免超過公開服務限制。使用政策見 [Nominatim Usage Policy](https://operations.osmfoundation.org/policies/nominatim/)。

參考：[GeoJSON 標準](https://datatracker.ietf.org/doc/html/rfc7946)、[OpenStreetMap 公開查詢服務](https://wiki.openstreetmap.org/wiki/Overpass_API)、[OSM 授權](https://www.openstreetmap.org/copyright)、[NetworkX 路徑演算法](https://networkx.org/documentation/stable/reference/algorithms/shortest_paths.html)。
