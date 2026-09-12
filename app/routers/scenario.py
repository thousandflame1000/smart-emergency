# -*- coding: utf-8 -*-
"""決賽展演用情境模擬引擎的 API——見 app/services/scenario.py 的設計說明。"""
from fastapi import APIRouter, Depends

from app.database import get_db
from app.services import scenario

router = APIRouter()


@router.get("/status")
def get_status(db=Depends(get_db)):
    return scenario.status(db)


@router.post("/start")
def start(intensity_scale: float = 1.0, capacity_scale: float = 1.0,
          population_size: int | None = None, db=Depends(get_db)):
    return scenario.start(db, intensity_scale=intensity_scale,
                           capacity_scale=capacity_scale, population_size=population_size)


@router.post("/advance")
def advance(db=Depends(get_db)):
    return scenario.advance(db)


@router.post("/reset")
def reset(db=Depends(get_db)):
    return scenario.reset(db)


@router.post("/autoplay")
def autoplay(enabled: bool, db=Depends(get_db)):
    return scenario.set_autoplay(db, enabled)
