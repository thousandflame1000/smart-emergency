import json
from datetime import UTC, datetime
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request as URLRequest, urlopen
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.workspace import TopologyWorkspace
from app.services.workspace import ComparisonBaseline, GraphDocument, ImportRequest, analyze, import_document
from app.services.workspace_comparison import compare
from app.services.workspace_allocation import AllocationRequest, plan_allocation
from app.services.places import search_places
from app.services.workspace_bridge import is_db_id, merge_database, operational_snapshot, _item
from app.services.workspace_inventory import (InventoryCommand, InventoryConflict, apply_inventory,
                                             preview_inventory, quantity_parts, row_version)

router = APIRouter()

OVERPASS_SERVICES = (
    ("VK Maps", "https://maps.mail.ru/osm/tools/overpass/api/interpreter"),
    ("FOSSGIS", "https://overpass-api.de/api/interpreter"),
    ("Private.coffee", "https://overpass.private.coffee/api/interpreter"),
)


class WorkspaceWrite(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    graph: GraphDocument = Field(default_factory=GraphDocument)
    revision: int = Field(default=0, ge=0)
    baseline: ComparisonBaseline | None = None


class AnalysisRequest(BaseModel):
    graph: GraphDocument
    start: str | None = None
    end: str | None = None


class ComparisonRequest(AnalysisRequest):
    baseline: GraphDocument


class DatabaseMergeRequest(BaseModel):
    graph: GraphDocument = Field(default_factory=GraphDocument)


class AssignmentIn(BaseModel):
    supply_id: str = Field(max_length=200)
    demand_id: str = Field(max_length=200)
    quantity: int = Field(default=1, ge=1)
    supply_version: str | None = None
    demand_version: str | None = None


class ApplyAllocationRequest(BaseModel):
    assignments: list[AssignmentIn] = Field(max_length=500)


class BoundsRequest(BaseModel):
    south: float = Field(ge=-85, le=85, allow_inf_nan=False)
    north: float = Field(ge=-85, le=85, allow_inf_nan=False)
    west: float = Field(ge=-180, le=180, allow_inf_nan=False)
    east: float = Field(ge=-180, le=180, allow_inf_nan=False)
    base: GraphDocument = Field(default_factory=GraphDocument)
    include_facilities: bool = False


def timestamp():
    return datetime.now(UTC).isoformat()


def serialize(row, detail=True):
    result = {"id": row.id, "name": row.name, "revision": row.revision, "updated_at": row.updated_at}
    if detail:
        document = json.loads(row.document)
        if "graph" in document:
            result.update(graph=document["graph"], baseline=document.get("baseline"))
        else:
            result.update(graph=document, baseline=None)
    return result


@router.get("")
def list_workspaces(db: Session = Depends(get_db)):
    return [serialize(row, False) for row in db.query(TopologyWorkspace).order_by(TopologyWorkspace.updated_at.desc()).all()]


@router.post("", status_code=201)
def create_workspace(body: WorkspaceWrite, db: Session = Depends(get_db)):
    row = TopologyWorkspace(id=uuid4().hex, name=body.name.strip() or "未命名工作區",
                            document=body.model_dump_json(include={"graph", "baseline"}), revision=1, updated_at=timestamp())
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
    if body.include_facilities:
        query += (
            f'(nwr["amenity"~"^(hospital|clinic|pharmacy|fire_station|police|school|community_centre|social_facility|shelter)$"]({bbox});'
            f'nwr["shop"~"^(supermarket|convenience)$"]({bbox}););out center;'
        )
    data = None
    provider = None
    for service_name, endpoint in OVERPASS_SERVICES:
        request = URLRequest(endpoint, data=urlencode({"data": query}).encode(),
                             headers={"User-Agent": "SmartEmergency/1.0", "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=22) as response:
                data = response.read(5_000_001)
            provider = service_name
            break
        except (URLError, TimeoutError):
            continue
    if data is None:
        raise HTTPException(502, "OpenStreetMap 暫時無法回應，可稍後重試或匯入本機檔案")
    try:
        if len(data) > 5_000_000:
            raise ValueError("資料過大，請縮小地圖範圍")
        result = import_document(ImportRequest(format="osm", content=data.decode("utf-8"), base=body.base,
                                              source=f"OpenStreetMap contributors / ODbL / {provider} / {timestamp()}"))
        result["provider"] = provider
        return result
    except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/compare")
def compare_graphs(body: ComparisonRequest):
    try:
        return compare(body.baseline, body.graph, body.start, body.end)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/places")
def places(q: str = Query(min_length=2, max_length=120)):
    try:
        return {"places": search_places(q), "source": "OpenStreetMap / Nominatim"}
    except (URLError, TimeoutError) as exc:
        raise HTTPException(502, "地區搜尋暫時無法回應，請稍後重試；也可移動地圖後載入範圍道路") from exc
    except (ValueError, TypeError, KeyError) as exc:
        raise HTTPException(400, "無法解析地區資料，請換個地區名稱重試") from exc


@router.post("/allocate")
def allocate_graph(body: AllocationRequest):
    try:
        return plan_allocation(body)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/database-merge")
def database_merge(body: DatabaseMergeRequest, db: Session = Depends(get_db)):
    """把平台資料庫的長者、需求、志工物資、資源點併入傳來的圖資料（不儲存）。"""
    try:
        graph, counts = merge_database(body.graph, db)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"graph": graph.model_dump(), "counts": counts}


@router.get("/operational-data")
def get_operational_data(db: Session = Depends(get_db)):
    """Read-only projection. No graph upload, persistence, notifications or stock mutations."""
    return operational_snapshot(db)


@router.post("/database-diff")
def database_diff(body: InventoryCommand, db: Session = Depends(get_db)):
    return preview_inventory(body, db)


@router.post("/database-push")
def database_push(body: InventoryCommand, db: Session = Depends(get_db)):
    from app.services.admin_session import current_admin
    try:
        return apply_inventory(body, db, current_admin.get())
    except InventoryConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/apply-allocation")
def apply_allocation(body: ApplyAllocationRequest, db: Session = Depends(get_db)):
    """把分配試算的結果寫成派遣建議（待確認）。不通知志工、不扣庫存，仍須在調度畫面確認。"""
    from app.services.admin_session import current_admin
    from app.services.dispatch import propose_manual
    from app.models.need import CommunityNeed
    from app.models.resource import CommunityResource
    admin = current_admin.get()
    proposed, skipped, used_needs = [], [], set()
    for a in sorted(body.assignments, key=lambda x: -x.quantity):
        if not (is_db_id(a.supply_id) and is_db_id(a.demand_id)):
            skipped.append({"supply_id": a.supply_id, "demand_id": a.demand_id, "reason": "這筆不是資料庫來源的供應或需求"})
            continue
        try:
            resource_id = a.supply_id.split("db:res:", 1)[1]
            need_id = a.demand_id.split("db:need:", 1)[1]
        except IndexError:
            skipped.append({"supply_id": a.supply_id, "demand_id": a.demand_id, "reason": "識別碼格式不正確"})
            continue
        if need_id in used_needs:
            skipped.append({"supply_id": a.supply_id, "demand_id": a.demand_id,
                            "reason": "同一筆需求已由另一份物資建議，系統一筆需求對應一份物資"})
            continue
        need, resource = db.get(CommunityNeed, need_id), db.get(CommunityResource, resource_id)
        reason = None
        if not need or not resource:
            reason = "找不到對應的需求或物資"
        elif a.supply_version != row_version(resource) or a.demand_version != row_version(need):
            reason = "資料已變動或缺少版本，請同步現況並重新試算"
        else:
            supply, demand = quantity_parts(resource.quantity), quantity_parts(need.quantity)
            if not supply or not demand or supply[1] != demand[1] or _item(resource.resource_type) != _item(need.need_type):
                reason = "登記品項、數量或單位不一致，不能建立派遣"
            elif a.quantity != demand[0] or supply[0] < a.quantity:
                reason = "目前派遣以整筆需求為單位，不接受部分供應或拆單；請先調整正式需求"
        if reason:
            skipped.append({"supply_id": a.supply_id, "demand_id": a.demand_id, "reason": reason})
            continue
        result = propose_manual(need_id, resource_id, db, actor_id=admin["id"] if admin else None,
                                actor_label=f"admin:{admin['name']}" if admin else "workspace:分配試算")
        if result.get("error"):
            skipped.append({"supply_id": a.supply_id, "demand_id": a.demand_id, "reason": result["error"]})
        else:
            used_needs.add(need_id)
            proposed.append({"need_id": need_id, "resource_id": resource_id})
    return {"proposed": proposed, "skipped": skipped}


@router.get("/{workspace_id}")
def get_workspace(workspace_id: str, db: Session = Depends(get_db)):
    row = db.get(TopologyWorkspace, workspace_id)
    if not row:
        raise HTTPException(404, "找不到工作區")
    return serialize(row)


@router.put("/{workspace_id}")
def save_workspace(workspace_id: str, body: WorkspaceWrite, db: Session = Depends(get_db)):
    if "baseline" not in body.model_fields_set:
        row = db.get(TopologyWorkspace, workspace_id)
        previous = json.loads(row.document).get("baseline") if row else None
        if previous:
            body.baseline = ComparisonBaseline.model_validate(previous)
    changed = db.query(TopologyWorkspace).filter(TopologyWorkspace.id == workspace_id,
                                                TopologyWorkspace.revision == body.revision).update({
        "name": body.name.strip() or "未命名工作區", "document": body.model_dump_json(include={"graph", "baseline"}),
        "revision": body.revision + 1, "updated_at": timestamp(),
    }, synchronize_session=False)
    if not changed:
        db.rollback()
        raise HTTPException(409, "工作區已有新版本或已移除；請另存副本，或重新載入後編輯")
    db.commit()
    return serialize(db.get(TopologyWorkspace, workspace_id))
