# -*- coding: utf-8 -*-
"""決賽展演用情境模擬引擎的 API——見 app/services/scenario.py 的設計說明。"""
import traceback

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.database import get_db
from app.services import scenario

router = APIRouter()


def _debug_call(fn, *args):
    """暫時性：正式環境（Postgres）上 reset/start 500，本機 SQLite 測試
    卻全過，需要看到真正的例外堆疊才能定位問題。修好後要整個移除。"""
    try:
        return fn(*args)
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"error": str(e), "type": type(e).__name__,
                     "traceback": traceback.format_exc()},
        )


@router.get("/status")
def get_status(db=Depends(get_db)):
    return _debug_call(scenario.status, db)


@router.post("/start")
def start(db=Depends(get_db)):
    return _debug_call(scenario.start, db)


@router.post("/advance")
def advance(db=Depends(get_db)):
    return _debug_call(scenario.advance, db)


@router.post("/reset")
def reset(db=Depends(get_db)):
    return _debug_call(scenario.reset, db)


@router.post("/autoplay")
def autoplay(enabled: bool, db=Depends(get_db)):
    return _debug_call(scenario.set_autoplay, db, enabled)
