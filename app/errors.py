# -*- coding: utf-8 -*-
"""API 錯誤：真正的 HTTP 狀態碼，同時保留前端已經在讀的 `error` 欄位。

之前很多端點遇到失敗仍回 200 加 {"error": ...}（例如切換模式填 banana、確認一筆不存在
的媒合），任何以 HTTP 狀態判斷成功與否的呼叫端都會誤以為成功。兩個前端頁面都是讀
`data.error`，所以錯誤內容同時放在 `error` 與 `detail`，前端不用改也能繼續運作。

ApiError 是 HTTPException 的子類別：沒有註冊 handler 的環境（例如測試裡單獨建的
FastAPI app）照樣得到正確的狀態碼；正式 app 在 app/main.py 註冊 handler，把
所有 HTTPException 與欄位驗證錯誤統一成 {"error": ..., "detail": ...}。
"""
from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
import html


class ApiError(HTTPException):
    def __init__(self, status_code: int, message: str):
        super().__init__(status_code=status_code, detail=message)


_API_PREFIXES = ("/api/", "/f/api/", "/webhook")
_ERROR_PAGE = """<!DOCTYPE html><html lang="zh-Hant"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="/favicon.ico">
<title>{title}｜鄰里守望</title><style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f3f6f3;color:#17201c;
font-family:"Noto Sans TC","Microsoft JhengHei",sans-serif}}
main{{max-width:420px;margin:24px;padding:32px 28px;background:#fff;border:1px solid #d7dfda;border-radius:8px;text-align:center}}
h1{{margin:0 0 10px;font-size:20px}}p{{margin:0 0 22px;color:#5f6c65;line-height:1.7}}
a{{display:inline-block;padding:10px 20px;border-radius:6px;background:#13795b;color:#fff;text-decoration:none;font-weight:700}}
</style></head><body><main><h1>{title}</h1><p>{message}</p>{action}</main></body></html>"""


def _wants_page(request: Request) -> bool:
    return (request.method == "GET" and "text/html" in request.headers.get("accept", "")
            and not request.url.path.startswith(_API_PREFIXES))


def _error_page(request: Request, status: int, message: str) -> HTMLResponse:
    """瀏覽器直接開到不存在或失效的網址時，給人看得懂的頁面，不是一段 JSON。"""
    from_line = request.url.path.startswith("/f/")
    title = "找不到這個頁面" if status == 404 else "無法開啟這個頁面"
    if message in ("Not Found", "請求失敗"):
        message = "網址可能打錯，或這個頁面已經不在了。"
    body = html.escape(message) + ("<br>請回到 LINE 重新開啟。" if from_line else "")
    action = "" if from_line else '<a href="/">回到首頁</a>'
    return HTMLResponse(_ERROR_PAGE.format(title=title, message=body, action=action), status_code=status)


def http_exception_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, str):
        message = detail
    elif isinstance(detail, dict):
        message = detail.get("message") or detail.get("error") or "請求失敗"
    else:
        message = "請求失敗"
    if exc.status_code in (404, 410) and _wants_page(request):
        return _error_page(request, exc.status_code, message)
    return JSONResponse(status_code=exc.status_code, content={"error": message, "detail": detail},
                        headers=getattr(exc, "headers", None))


def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", []) if p not in ("query", "body", "path"))
    message = f"欄位「{field}」格式不正確：{first.get('msg', '')}" if field else "欄位格式不正確"
    # exc.errors() 的 ctx 可能夾帶 ValueError 之類無法序列化的物件（工作區驗證就會），
    # 只留下前端與人看得懂的欄位。
    detail = [{"loc": list(e.get("loc", [])), "msg": str(e.get("msg", "")), "type": e.get("type")}
              for e in exc.errors()]
    return JSONResponse(status_code=422, content={"error": message, "detail": detail})


def raise_if_error(result, *, not_found_hint: str = "not found"):
    """Turn a service-layer {"error": ...} dict into a real HTTP error."""
    if isinstance(result, dict) and result.get("error"):
        message = str(result["error"])
        status = 404 if (not_found_hint in message or "找不到" in message) else 409
        raise ApiError(status, message)
    return result
