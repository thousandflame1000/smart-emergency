import json
from datetime import UTC, datetime
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request as URLRequest, urlopen
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.workspace import TopologyWorkspace
from app.services.workspace import GraphDocument, ImportRequest, analyze, import_document

router = APIRouter()


class WorkspaceWrite(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    graph: GraphDocument = Field(default_factory=GraphDocument)
    revision: int = Field(default=0, ge=0)


class AnalysisRequest(BaseModel):
    graph: GraphDocument
    start: str | None = None
    end: str | None = None


class BoundsRequest(BaseModel):
    south: float = Field(ge=-85, le=85, allow_inf_nan=False)
    north: float = Field(ge=-85, le=85, allow_inf_nan=False)
    west: float = Field(ge=-180, le=180, allow_inf_nan=False)
    east: float = Field(ge=-180, le=180, allow_inf_nan=False)
    base: GraphDocument = Field(default_factory=GraphDocument)


def timestamp():
    return datetime.now(UTC).isoformat()


def serialize(row, detail=True):
    result = {"id": row.id, "name": row.name, "revision": row.revision, "updated_at": row.updated_at}
    if detail:
        result["graph"] = json.loads(row.document)
    return result


@router.get("")
def list_workspaces(db: Session = Depends(get_db)):
    return [serialize(row, False) for row in db.query(TopologyWorkspace).order_by(TopologyWorkspace.updated_at.desc()).all()]


@router.post("", status_code=201)
def create_workspace(body: WorkspaceWrite, db: Session = Depends(get_db)):
    row = TopologyWorkspace(id=uuid4().hex, name=body.name.strip() or "未命名工作區",
                            document=body.graph.model_dump_json(), revision=1, updated_at=timestamp())
    db.add(row)
    db.commit()
    return serialize(row)


@router.post("/import-preview")
def preview_import(body: ImportRequest):
    try:
        return import_document(body)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        detail = "資料格式或欄位不正確" if isinstance(exc, ValidationError) else str(exc)
        raise HTTPException(400, detail=detail) from exc


@router.post("/analyze")
def analyze_graph(body: AnalysisRequest):
    try:
        return analyze(body.graph, body.start, body.end)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.post("/openstreetmap")
def openstreetmap(body: BoundsRequest):
    if not (0 < body.north - body.south <= 0.12 and 0 < body.east - body.west <= 0.12):
        raise HTTPException(400, "請放大地圖：單次範圍的經度與緯度跨度須小於 0.12 度，可分區追加")
    bbox = f"{body.south},{body.west},{body.north},{body.east}"
    query = f'[out:json][timeout:15];way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|unclassified|service|living_street|.*_link)$"]({bbox});(._;>;);out body;'
    data = None
    for endpoint in ("https://overpass-api.de/api/interpreter", "https://overpass.private.coffee/api/interpreter"):
        request = URLRequest(endpoint, data=urlencode({"data": query}).encode(),
                             headers={"User-Agent": "SmartEmergency/1.0", "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=22) as response:
                data = response.read(5_000_001)
            break
        except (URLError, TimeoutError):
            continue
    if data is None:
        raise HTTPException(502, "OpenStreetMap 暫時無法回應，可稍後重試或匯入本機檔案")
    try:
        if len(data) > 5_000_000:
            raise ValueError("資料過大，請縮小地圖範圍")
        return import_document(ImportRequest(format="osm", content=data.decode("utf-8"), base=body.base,
                                              source=f"OpenStreetMap contributors / ODbL / {timestamp()}"))
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/{workspace_id}")
def get_workspace(workspace_id: str, db: Session = Depends(get_db)):
    row = db.get(TopologyWorkspace, workspace_id)
    if not row:
        raise HTTPException(404, "找不到工作區")
    return serialize(row)


@router.put("/{workspace_id}")
def save_workspace(workspace_id: str, body: WorkspaceWrite, db: Session = Depends(get_db)):
    changed = db.query(TopologyWorkspace).filter(TopologyWorkspace.id == workspace_id,
                                                TopologyWorkspace.revision == body.revision).update({
        "name": body.name.strip() or "未命名工作區", "document": body.graph.model_dump_json(),
        "revision": body.revision + 1, "updated_at": timestamp(),
    }, synchronize_session=False)
    if not changed:
        db.rollback()
        raise HTTPException(409, "工作區已有新版本或已移除；請另存副本，或重新載入後編輯")
    db.commit()
    return serialize(db.get(TopologyWorkspace, workspace_id))
