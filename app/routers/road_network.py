from fastapi import APIRouter, Depends, HTTPException
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import road_network
from app.services.road_replanning import (
    RoadReplanningError,
    RoadReplanningService,
    TaskRouteService,
)

router = APIRouter()


class EdgePatch(BaseModel):
    status: str = "normal"
    multiplier: float | None = None


class NodePatch(BaseModel):
    lat: float
    lng: float


class NodeCreate(BaseModel):
    id: str
    lat: float
    lng: float
    label: str | None = None


class EdgeCreate(BaseModel):
    a: str
    b: str
    status: str = "normal"
    multiplier: float | None = None


class ObservationCreate(BaseModel):
    road_segment_id: str
    state: str
    source: str
    observed_at: datetime | None = None
    details: dict = Field(default_factory=dict)


class TaskRouteCreate(BaseModel):
    start_node: str
    end_node: str
    proposal_id: str | None = None


def _bad_request(exc: ValueError):
    raise HTTPException(status_code=400, detail=str(exc))


@router.get("/sandbox")
def get_sandbox(db: Session = Depends(get_db)):
    return road_network.sandbox_snapshot(db)


@router.post("/sandbox/reset")
def reset_sandbox(db: Session = Depends(get_db)):
    return road_network.reset_sandbox(db)


@router.put("/sandbox/edges/{edge_id}")
def update_edge(edge_id: str, patch: EdgePatch, db: Session = Depends(get_db)):
    try:
        return road_network.set_edge_status(db, edge_id, patch.status, patch.multiplier)
    except ValueError as exc:
        _bad_request(exc)


@router.delete("/sandbox/edges/{edge_id}")
def delete_edge(edge_id: str, db: Session = Depends(get_db)):
    return road_network.delete_edge(db, edge_id)


@router.post("/sandbox/edges")
def create_edge(edge: EdgeCreate, db: Session = Depends(get_db)):
    try:
        return road_network.add_edge(db, edge.a, edge.b, edge.status, edge.multiplier)
    except ValueError as exc:
        _bad_request(exc)


@router.put("/sandbox/nodes/{node_id}")
def update_node(node_id: str, patch: NodePatch, db: Session = Depends(get_db)):
    try:
        return road_network.move_node(db, node_id, patch.lat, patch.lng)
    except ValueError as exc:
        _bad_request(exc)


@router.post("/sandbox/nodes")
def create_node(node: NodeCreate, db: Session = Depends(get_db)):
    try:
        return road_network.add_node(db, node.id, node.lat, node.lng, node.label)
    except ValueError as exc:
        _bad_request(exc)


@router.delete("/sandbox/nodes/{node_id}")
def delete_node(node_id: str, db: Session = Depends(get_db)):
    return road_network.delete_node(db, node_id)


@router.get("/route")
def route(start: str, end: str, db: Session = Depends(get_db)):
    result = road_network.route_between_nodes(db, start, end)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/observations")
def create_observation(item: ObservationCreate, db: Session = Depends(get_db)):
    try:
        return RoadReplanningService(db).observe(
            road_segment_id=item.road_segment_id,
            state=item.state,
            source=item.source,
            observed_at=item.observed_at,
            details=item.details,
        )
    except RoadReplanningError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ValueError as exc:
        _bad_request(exc)


@router.get("/segments/{road_segment_id}/state")
def segment_state(road_segment_id: str, db: Session = Depends(get_db)):
    state = road_network.current_road_state(db, road_segment_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Road segment not found")
    return {"road_segment_id": road_segment_id, "state": state}


@router.post("/tasks/{task_id}/route")
def attach_task_route(
    task_id: str,
    item: TaskRouteCreate,
    db: Session = Depends(get_db),
):
    try:
        route = TaskRouteService(db).attach_route(
            task_id=task_id,
            start_node=item.start_node,
            end_node=item.end_node,
            proposal_id=item.proposal_id,
        )
        return {
            "route_id": str(route.id),
            "task_id": str(route.task_id),
            "status": route.status,
            "path": route.node_path,
            "segment_ids": route.segment_ids,
            "distance_km": route.distance_km,
        }
    except RoadReplanningError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
