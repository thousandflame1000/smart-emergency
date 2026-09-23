import json
from datetime import UTC, datetime
from urllib.error import URLError
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.workspace import TopologyWorkspace
from app.models.zone import GENERAL_ZONE_ID
from app.services.workspace import ComparisonBaseline, GraphDocument, ImportRequest, analyze, import_document
from app.services.workspace_comparison import compare
from app.services.workspace_allocation import AllocationRequest, plan_allocation
from app.services.places import search_places
from app.security import require_admin, require_staff
from app.services.workspace_bridge import is_db_id, merge_database, operational_snapshot, _item
from app.services.workspace_inventory import (InventoryCommand, InventoryConflict, apply_inventory,
                                             preview_inventory, quantity_parts, row_version)

router = APIRouter()

class WorkspaceWrite(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    graph: GraphDocument = Field(default_factory=GraphDocument)
    revision: int = Field(default=0, ge=0)
    baseline: ComparisonBaseline | None = None
    zone_id: str = Field(default=GENERAL_ZONE_ID, max_length=180)


class AnalysisRequest(BaseModel):
    graph: GraphDocument


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


def timestamp():
    return datetime.now(UTC).isoformat()


def serialize(row, detail=True):
    result = {"id": row.id, "name": row.name, "revision": row.revision, "updated_at": row.updated_at,
              "zone_id": row.zone_id}
    if detail:
        document = json.loads(row.document)
        if "graph" in document:
            graph = GraphDocument.model_validate(document["graph"]).model_dump(mode="json")
            baseline = (ComparisonBaseline.model_validate(document["baseline"]).model_dump(mode="json")
                        if document.get("baseline") is not None else None)
            result.update(graph=graph, baseline=baseline)
        else:
            result.update(graph=GraphDocument.model_validate(document).model_dump(mode="json"), baseline=None)
    return result


@router.get("")
def list_workspaces(zone_id: str | None = None, db: Session = Depends(get_db)):
    q = db.query(TopologyWorkspace)
    if zone_id is not None:
        q = q.filter(TopologyWorkspace.zone_id == zone_id)
    return [serialize(row, False) for row in q.order_by(TopologyWorkspace.updated_at.desc()).all()]


@router.post("", status_code=201)
def create_workspace(body: WorkspaceWrite, db: Session = Depends(get_db)):
    from app.models.zone import Zone
    if not db.get(Zone, body.zone_id):
        raise HTTPException(400, "找不到這個分區")
    row = TopologyWorkspace(id=uuid4().hex, name=body.name.strip() or "未命名工作區",
                            document=body.model_dump_json(include={"graph", "baseline"}), revision=1,
                            updated_at=timestamp(), zone_id=body.zone_id)
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
        return analyze(body.graph)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc


@router.post("/compare")
def compare_graphs(body: ComparisonRequest):
    try:
        return compare(body.baseline, body.graph)
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
def database_merge(body: DatabaseMergeRequest, zone_id: str | None = None, db: Session = Depends(get_db)):
    """把平台資料庫的長者、需求、志工物資、資源點併入傳來的圖資料（不儲存）。

    帶 zone_id 時只併入該分區的物資／需求；不帶則維持併入全系統資料（例如管理員總覽）。"""
    try:
        graph, counts = merge_database(body.graph, db, zone_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"graph": graph.model_dump(), "counts": counts}


@router.get("/operational-data")
def get_operational_data(zone_id: str | None = None, db: Session = Depends(get_db)):
    """Read-only projection. No graph upload, persistence, notifications or stock mutations."""
    return operational_snapshot(db, zone_id)


@router.post("/database-diff")
def database_diff(body: InventoryCommand, db: Session = Depends(get_db),
                  _principal: dict | None = Depends(require_staff)):
    return preview_inventory(body, db)


@router.post("/database-push")
def database_push(body: InventoryCommand, db: Session = Depends(get_db),
                  _principal: dict | None = Depends(require_staff)):
    from app.services.admin_session import current_admin
    try:
        return apply_inventory(body, db, current_admin.get())
    except InventoryConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/apply-allocation")
def apply_allocation(body: ApplyAllocationRequest, db: Session = Depends(get_db),
                     _principal: dict | None = Depends(require_admin)):
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
    row = db.get(TopologyWorkspace, workspace_id)
    if "baseline" not in body.model_fields_set:
        previous = json.loads(row.document).get("baseline") if row else None
        if previous:
            body.baseline = ComparisonBaseline.model_validate(previous)
    if "zone_id" not in body.model_fields_set and row:
        body.zone_id = row.zone_id
    elif "zone_id" in body.model_fields_set:
        from app.models.zone import Zone
        if not db.get(Zone, body.zone_id):
            raise HTTPException(400, "找不到這個分區")
    changed = db.query(TopologyWorkspace).filter(TopologyWorkspace.id == workspace_id,
                                                TopologyWorkspace.revision == body.revision).update({
        "name": body.name.strip() or "未命名工作區", "document": body.model_dump_json(include={"graph", "baseline"}),
        "revision": body.revision + 1, "updated_at": timestamp(), "zone_id": body.zone_id,
    }, synchronize_session=False)
    if not changed:
        db.rollback()
        raise HTTPException(409, "工作區已有新版本或已移除；請另存副本，或重新載入後編輯")
    db.commit()
    return serialize(db.get(TopologyWorkspace, workspace_id))
