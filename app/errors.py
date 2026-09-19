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
from fastapi.responses import JSONResponse


class ApiError(HTTPException):
    def __init__(self, status_code: int, message: str):
        super().__init__(status_code=status_code, detail=message)


def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, str):
        message = detail
    elif isinstance(detail, dict):
        message = detail.get("message") or detail.get("error") or "請求失敗"
    else:
        message = "請求失敗"
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
