# LINE Bot 設定指引

## 步驟 1：建立 LINE Bot

1. 開 https://developers.line.biz/
2. 登入 → Console → Create a new provider（填任意名稱）
3. Create a new channel → **Messaging API**
4. 填寫：
   - Channel name: `鄰里守望`
   - Channel description: 任意
   - Category: 選 `Social good`
   - Email: 你的 email

---

## 步驟 2：取得 API Keys

### Channel Secret
```
Basic settings → Channel secret → 複製
```

### Channel Access Token
```
Messaging API → Channel access token (long-lived) → Issue → 複製
```

---

## 步驟 3：更新 .env

打開 .env，把這兩行換成真實值：

```
LINE_CHANNEL_SECRET=貼上你的 Channel Secret
LINE_CHANNEL_ACCESS_TOKEN=貼上你的 Access Token
```

---

## 步驟 4：開 ngrok 讓 LINE 連到你的本機

```bash
# 開一個新的終端機
ngrok http 8080
```

複製輸出的 HTTPS URL，例如：
```
https://xxxx-xxx-xxx-xxx.ngrok-free.app
```

---

## 步驟 5：設定 Webhook URL

```
LINE Developers Console
→ Messaging API
→ Webhook URL：填入 https://xxxx.ngrok-free.app/webhook/line
→ 點「Verify」→ 應該出現 Success
→ 開啟「Use webhook」
```

---

## 步驟 6：關掉 Auto-reply

```
Messaging API
→ LINE Official Account features
→ Auto-reply messages → Edit → 關閉
→ Greeting messages → Edit → 關閉
```

---

## 步驟 7：重啟 server

```bash
.\venv\Scripts\uvicorn app.main:app --port 8080 --reload
```

---

## 步驟 8：把你的 LINE 帳號加入系統

用 /api/dashboard/users 新增一個 volunteer 身份的用戶，
填入你自己的 LINE User ID。

### 取得你的 LINE User ID
方法 A：
  1. LINE Bot 傳任意訊息給你的 Bot
  2. 看 server log，會印出 line_uid

方法 B：
  1. 開 https://developers.line.biz/console/
  2. 你的 provider → 你的 channel → Basic settings
  3. 用 LINE Login 取得

---

## 完整流程測試

```
1. 用手機加 LINE Bot 好友（掃 QR code 或搜尋 @帳號）
2. 傳訊息「你好」
3. Server 應該印出接收到的 line_uid
4. 用 /api/dashboard/users 新增你自己（角色：elderly 測試）
5. 打開 http://localhost:8080/docs
   POST /api/dashboard/users
   → 填你的 line_uid
6. 呼叫打卡：
   POST /webhook/line/simulate_checkin?elderly_name=你的姓名
   （這只在 dev 模式下有效）
```

---

## 快速確認 Bot 有在運作

```bash
curl -X POST http://localhost:8080/webhook/line/simulate_checkin?elderly_name=陳月英
```

應該回傳：
```json
{"checkin_id": "...", "status": "pending", ...}
```
