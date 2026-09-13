from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import road_network

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
