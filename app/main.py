from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from app.database import engine, Base
from app.scheduler import start_scheduler, shutdown_scheduler
from app.routers import linebot, dashboard, resources, rag


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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(linebot.router,   prefix="/webhook",       tags=["LINE Bot"])
app.include_router(dashboard.router, prefix="/api/dashboard", tags=["Dashboard"])
app.include_router(resources.router, prefix="/api/resources", tags=["Resources"])
app.include_router(rag.router,       prefix="/api/rag",       tags=["RAG"])


@app.get("/admin")
def admin_page():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "admin.html"))


@app.get("/health")
def health():
    return {"status": "ok", "service": "鄰里守望平台"}

@app.get("/")
def dashboard():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))

# 靜態資源
app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")), name="static")
