# 正式營運上線門檻

本文件是部署檢查表，不是法律意見或醫療器材認證。每次正式上線、重大版本更新及事故後都應重新檢查。

## 上線前必做

- 將 `APP_ENV=production`，啟用管理員登入，確認 field staff 無法讀取使用者清單、變更角色、建立分區、使用 RAG 或核准派遣。
- 設定真實的 `PRIVACY_CONTROLLER_NAME`、`PRIVACY_CONTACT` 與新版 `PRIVACY_NOTICE_VERSION`，由台灣個資法顧問或組織法務核准 `/privacy` 內容。
- 建立 PostgreSQL 自動備份、異地副本與還原演練；記錄 RPO/RTO。沒有成功還原過的備份不能視為可用。
- 用測試 LINE 帳號演練 webhook 重送、LINE API 暫時失敗、錯誤 LINE ID、重複派遣與多人同時核准。
- 將 `NOMINATIM_SEARCH_URL` 指向自管或有服務承諾的地理編碼服務。使用公用 Nominatim 時維持每秒最多一次、快取結果，且不可做 autocomplete。
- 將 Railway health check 指向 `/ready`，外部監控同時檢查 `/health`、`/ready`、outbox `FAILED/DEAD` 與 webhook inbox `FAILED/DEAD` 數量。
- 若開啟 `EXTERNAL_AI_ENABLED=true`，先完成供應商資料處理評估；禁止把可直接識別個人的健康、地址、電話或照護資料放進 prompt/知識庫。

## 部署後確認

- 先開後台「系統狀態」：紅色「需處理」逐項照提示修正，黃色「注意」確認是否可接受。以下各項是它背後檢查的內容。
- Railway 由 `master` 自動部署。`GET /health` 回傳的 `commit` 應與剛推送的 commit 前 7 碼相同；不同代表部署失敗或尚未完成。
- `GET /api/system/security` 的 `auth_mode` 不可為 `open`；`open` 代表後台與所有 API 對外公開，必須設 `DEMO_PASSWORD` 或綁定 LINE 管理員。
- `/privacy` 會顯示外部 AI 是否啟用。未啟用時 LINE 問答改以本機關鍵字檢索回覆知識庫原文，功能不中斷。
- 圖文選單（`app/services/rich_menu.py`、`app/static/richmenu/*.png`）有變更時，暫時設 `RICH_MENU_REBUILD_ENABLED=true`，以管理員身分呼叫 `POST /api/system/rich-menu/install`，確認 `cutover_complete: true` 後改回 `false`。
- 新的 LINE 訊息格式上線前，用 `https://api.line.me/v2/bot/message/validate/reply`（或 `/push`）驗證；格式錯誤時 LINE 會整則拒收，使用者什麼都收不到。

## 稽核與故障行為

- 後台與 LINE 上所有會改資料的管理操作都寫入 `admin_audit`，後台「操作紀錄」可查時間、操作者、來源與成功與否。純計算（分析、試算、比對）不記錄。
- 沒有照護聯絡人（或全部通知失敗）的長者，打卡 3 小時未回應時會改通知所有綁定 LINE 的管理員一次。
- 「重新載入知識庫」在外部 AI 未啟用時會拒絕執行；啟用時先產生全部新段落才一次替換，中途失敗保留原內容。

## 個資作業

- 使用者送出居民、志工、物資與任務表單前，系統會要求確認告知事項，並在 `privacy_consents` 記錄版本與時間。
- `webhook_events` 的完成/終止封包依 `WEBHOOK_RETENTION_DAYS` 清除；`outbox_messages` 的完成/終止紀錄依 `OUTBOX_RETENTION_DAYS` 清除。
- 使用者提出查詢、更正、停止使用或刪除請求時，先驗證身分，匯出相關資料，確認沒有處理中的緊急事件與法定保存義務，再由管理員執行並留下處理紀錄。
- 發生疑似外洩時先停用受影響憑證、保存必要鑑識紀錄、界定資料與人數、依主管機關與契約時限通知，完成原因分析與改善追蹤。

## 每月檢查

- 更新並執行完整測試與 `pip-audit -r requirements.txt`。
- 抽查權限、管理員帳號、停用帳號、隱私告知版本與外部服務設定。
- 檢查通知死信、webhook 死信、備份還原結果與 scheduler 是否只有單一實例取得 advisory lock。
- 以桌上演練驗證 119 升級、家屬通知、志工拒單、無網路與外部服務故障流程。
