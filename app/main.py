from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
import os

from app.database import engine, Base
from app.scheduler import start_scheduler, shutdown_scheduler
from app.rate_limit import limiter
from app.demo_auth import DemoAuthMiddleware
from app.routers import linebot, dashboard, resources, rag, scenario
# 確保所有 model 被 import，Base.metadata.create_all 才會建表
import app.models.resource_point  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 啟動時
    Base.metadata.create_all(bind=engine)
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
app.add_middleware(SlowAPIMiddleware)

app.include_router(linebot.router,   prefix="/webhook",       tags=["LINE Bot"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(resources.router, prefix="/api/resources", tags=["Resources"])
app.include_router(rag.router,       prefix="/api/rag",       tags=["RAG"])
app.include_router(scenario.router,  prefix="/api/scenario",  tags=["Scenario"])


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
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
