from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
import logging
import os

from app.config import settings
from app.database import engine
from app.schema_migrations import ensure_additive_schema
from app.scheduler import start_scheduler, shutdown_scheduler
from app.rate_limit import limiter
from app.errors import http_exception_handler, validation_error_handler
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from app.demo_auth import DemoAuthMiddleware
from app.routers import webform, linebot, dashboard, resources, rag, scenario, ontology, road_network, workspace, tasks
# 確保所有 model 被 import，Base.metadata.create_all 才會建表
import app.models.resource_point  # noqa: F401
import app.models.dispatch_event  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 啟動時
    ensure_additive_schema(engine)
    # 生產環境：自動 seed + ingest（只在空 DB 執行）
    import os
    if os.getenv("APP_ENV", "development") == "production":
        try:
            import sys
            sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
            import startup
            startup.run()
        except Exception as e:
            print(f"[startup] 警告：{e}")
    if os.getenv("APP_ENV", "development") == "production":
        import threading

        def _sync_kb():
            try:
                from app.services import rag
                result = rag.sync_builtin_documents()
                logging.getLogger("startup").info("knowledge base sync: %s", result)
            except Exception:
                logging.getLogger("startup").exception("knowledge base sync failed")

        threading.Thread(target=_sync_kb, daemon=True).start()
    from app.demo_auth import auth_mode
    if os.getenv("APP_ENV", "development") == "production" and auth_mode() == "open":
        # 用 logging 而不是 print：print 在非 UTF-8 的 console（例如 Windows cp950）遇到
        # emoji 會直接丟 UnicodeEncodeError，讓「只是一句提醒」把整個服務啟動搞掛。
        logging.getLogger("security").warning(
            "The admin console and every API are publicly readable/writable, including elder names, "
            "addresses and coordinates. Bind an admin to LINE (login link via the bot) or set DEMO_PASSWORD.")
    start_scheduler()
    yield
    # 關閉時
    shutdown_scheduler()


app = FastAPI(
    title="鄰里守望平台",
    version="0.1.0",
    description="平時照顧長者，災時守護社區",
    lifespan=lifespan,
)


# 決賽展演期間的臨時密碼閘（見 app/demo_auth.py）——刻意放在最前面加，
# 讓它成為最外層 middleware，沒有密碼的請求在碰到 CORS/限流邏輯之前
# 就先被擋掉。沒設定 DEMO_PASSWORD 環境變數時完全不啟用。
app.add_middleware(DemoAuthMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # 系統沒有 cookie/session 機制，allow_credentials=True 跟萬用 origin
    # 同時開是規範上互斥的組合（瀏覽器規格禁止 credentialed 請求搭配
    # Access-Control-Allow-Origin: *），而且完全沒用到就沒必要留著。
    allow_methods=["*"],
    allow_headers=["*"],
)

# 系統目前沒有登入驗證，先用限流擋掉「短時間內狂打同一個 IP」的濫用
# 情境（見 app/rate_limit.py 的說明）。/health 特別排除在外，因為
# Railway 靠這個路徑判斷服務是否存活（railway.toml healthcheckPath），
# 一旦被限流回 429，Railway 會誤判服務掛掉而重啟部署。
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_middleware(SlowAPIMiddleware)

app.include_router(linebot.router,   prefix="/webhook",       tags=["LINE Bot"])
app.include_router(webform.router,   prefix="/f",             tags=["Web Form"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(resources.router, prefix="/api/resources", tags=["Resources"])
app.include_router(rag.router,       prefix="/api/rag",       tags=["RAG"])
app.include_router(scenario.router,  prefix="/api/scenario",  tags=["Scenario"])
app.include_router(ontology.router,  prefix="/api/ontology",  tags=["Ontology"])
app.include_router(road_network.router, prefix="/api/road-network", tags=["Road Network"])
app.include_router(tasks.router, prefix="/api/tasks", tags=["Tasks"])
app.include_router(workspace.router, prefix="/api/workspaces", tags=["開放資料工作區"])


@app.get("/workspace")
def workspace_page():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "workspace.html"))


@app.get("/admin")
def admin_page():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "admin.html"))


@app.get("/health")
@limiter.exempt
def health():
    return {"status": "ok", "service": "鄰里守望平台"}

@app.get("/")
def dashboard():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))

# 靜態資源
@app.post("/api/system/rich-menu/install", tags=["System"])
def install_rich_menu():
    """用部署環境的 LINE token 重建兩張 Rich Menu（一般版設為預設、志工版綁給現有志工）。"""
    from app.database import SessionLocal
    from app.services.rich_menu import install_menus
    db = SessionLocal()
    try:
        return install_menus(db)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"LINE 建立選單失敗：{exc}")
    finally:
        db.close()


@app.get("/admin/login", include_in_schema=False)
def admin_login(t: str = ""):
    """Exchange the one-time link the bot sent an admin for a 12-hour session cookie."""
    from fastapi.responses import HTMLResponse, RedirectResponse
    from app.database import SessionLocal
    from app.models.user import User
    from app.services import admin_session
    uid = admin_session.verify_login_token(t)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == uid).first() if uid else None
        if not user or not user.roles or "admin" not in user.roles or user.is_active is False:
            return HTMLResponse("登入連結已過期或無效。請回 LINE 傳「後台」取得新的連結。", status_code=401)
    finally:
        db.close()
    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(admin_session.COOKIE_NAME, admin_session.make_session(uid),
                        max_age=admin_session.SESSION_TTL, httponly=True, samesite="lax",
                        secure=settings.APP_ENV == "production")
    return response


@app.get("/admin/logout", include_in_schema=False)
def admin_logout():
    from fastapi.responses import RedirectResponse
    from app.services import admin_session
    response = RedirectResponse("/admin", status_code=303)
    response.delete_cookie(admin_session.COOKIE_NAME)
    return response


@app.get("/api/system/security", tags=["System"])
def security_status():
    """Tell the console whether the deployment is publicly readable.

    On a production deployment with no DEMO_PASSWORD, every page and every write API is
    open to anyone with the URL — including the elder list with names, addresses and
    coordinates. The admin console shows a red banner when this is true."""
    from app.config import settings as _settings
    from app.demo_auth import auth_mode
    from app.services.admin_session import current_admin
    mode = auth_mode()
    admin = current_admin.get()
    return {
        "app_env": _settings.APP_ENV,
        "auth_mode": mode,
        "auth_enabled": mode != "open",
        "public_admin": _settings.APP_ENV == "production" and mode == "open",
        "admin": admin["name"] if admin else None,
    }


app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
