"""Development-only competition rehearsal form and response handoff."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from app.config import settings
from app.security import require_admin


router = APIRouter()
PROJECT_ROOT = Path(__file__).resolve().parents[2]
RESPONSE_PATH = PROJECT_ROOT / "REHEARSAL_RESPONSES.json"
PAGE_PATH = PROJECT_ROOT / "app" / "static" / "rehearsal.html"
_NO_CACHE = {"Cache-Control": "no-cache"}


class RouteAnswer(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$", max_length=80)
    status: Literal["untested", "passed", "blocked"] = "untested"
    observation: str = Field(default="", max_length=4000)
    blocker: str = Field(default="", max_length=4000)


class TimingAnswer(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$", max_length=80)
    actual_seconds: int | None = Field(default=None, ge=0, le=1800)
    script_notes: str = Field(default="", max_length=4000)


class RubricAnswer(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$", max_length=80)
    score: int | None = Field(default=None, ge=0, le=5)
    evidence: str = Field(default="", max_length=4000)
    improvement: str = Field(default="", max_length=4000)


class QuestionAnswer(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9-]+$", max_length=80)
    answer: str = Field(default="", max_length=6000)


class RehearsalSubmission(BaseModel):
    version: Literal["competition-rehearsal-v1"] = "competition-rehearsal-v1"
    participant_name: str = Field(default="", max_length=120)
    session_name: str = Field(default="", max_length=120)
    rehearsal_date: date | None = None
    scenario: str = Field(default="", max_length=180)
    route: list[RouteAnswer] = Field(default_factory=list, max_length=40)
    timing: list[TimingAnswer] = Field(default_factory=list, max_length=10)
    rubric: list[RubricAnswer] = Field(default_factory=list, max_length=30)
    questions: list[QuestionAnswer] = Field(default_factory=list, max_length=20)
    overall_notes: str = Field(default="", max_length=8000)
    next_action: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def unique_ids(self):
        for name in ("route", "timing", "rubric", "questions"):
            ids = [item.id for item in getattr(self, name)]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{name} contains duplicate ids")
        return self


def _development_only() -> None:
    if settings.APP_ENV != "development":
        raise HTTPException(status_code=404, detail="找不到這筆資料")


@router.get("/rehearsal", include_in_schema=False)
def rehearsal_page():
    _development_only()
    return FileResponse(PAGE_PATH, headers=_NO_CACHE)


@router.get("/api/rehearsal", include_in_schema=False)
def get_rehearsal(_principal: dict | None = Depends(require_admin)):
    _development_only()
    if not RESPONSE_PATH.exists():
        return {"submission": None}
    try:
        saved = json.loads(RESPONSE_PATH.read_text(encoding="utf-8"))
        RehearsalSubmission.model_validate(saved.get("submission"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail="演練紀錄檔無法讀取") from exc
    return saved


@router.put("/api/rehearsal", include_in_schema=False)
def save_rehearsal(body: RehearsalSubmission,
                   _principal: dict | None = Depends(require_admin)):
    _development_only()
    saved_at = datetime.now(UTC).isoformat()
    payload = {"saved_at": saved_at, "submission": body.model_dump(mode="json")}
    temporary = RESPONSE_PATH.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(RESPONSE_PATH)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="演練紀錄無法寫入 workspace") from exc
    return {"saved": True, "saved_at": saved_at, "path": RESPONSE_PATH.name}
