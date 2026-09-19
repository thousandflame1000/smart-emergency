# -*- coding: utf-8 -*-
"""極小的對話狀態：讓 LINE 可以「一次問一題」，不必要求使用者背格式。

機器人本來是完全無狀態的，每則訊息獨立處理，所以「志工申請」只能靠使用者自己
打出「志工申請 姓名 電話 區域」這種格式，打錯一個空格就失敗。這裡用
SystemConfig 存每位使用者目前進行到哪一步（30 分鐘沒動作自動失效），不需要
新表、也不影響其他功能。
"""
from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy.orm import Session

from app.models.config import SystemConfig
from app.timeutil import now_utc

FLOW_TTL = timedelta(minutes=30)


def _key(line_uid: str) -> str:
    return f"flow:{line_uid}"


def get(db: Session, line_uid: str) -> dict | None:
    row = db.query(SystemConfig).filter(SystemConfig.key == _key(line_uid)).first()
    if not row or not row.value:
        return None
    try:
        state = json.loads(row.value)
    except ValueError:
        return None
    started = state.get("at")
    if not started:
        return None
    from datetime import datetime
    try:
        if now_utc() - datetime.fromisoformat(started) > FLOW_TTL:
            clear(db, line_uid)
            return None
    except ValueError:
        return None
    return state


def start(db: Session, line_uid: str, flow: str, step: str, data: dict | None = None) -> dict:
    return _save(db, line_uid, {"flow": flow, "step": step, "data": data or {}})


def advance(db: Session, line_uid: str, state: dict, step: str, **data) -> dict:
    state = {**state, "step": step, "data": {**state.get("data", {}), **data}}
    return _save(db, line_uid, state)


def clear(db: Session, line_uid: str) -> None:
    row = db.query(SystemConfig).filter(SystemConfig.key == _key(line_uid)).first()
    if row:
        db.delete(row)
        db.commit()


def _save(db: Session, line_uid: str, state: dict) -> dict:
    state = {**state, "at": now_utc().isoformat()}
    payload = json.dumps(state, ensure_ascii=False)
    row = db.query(SystemConfig).filter(SystemConfig.key == _key(line_uid)).first()
    if row:
        row.value = payload
    else:
        db.add(SystemConfig(key=_key(line_uid), value=payload))
    db.commit()
    return state
