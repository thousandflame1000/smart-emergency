from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import ontology, reasoning

router = APIRouter()


@router.get("/schema")
def get_schema():
    return ontology.schema()


@router.get("/graph")
def get_graph(limit: int = 80, db: Session = Depends(get_db)):
    return ontology.graph(db, limit=limit)


@router.get("/objects/{object_type}/{object_id}")
def get_object_context(object_type: str, object_id: str, db: Session = Depends(get_db)):
    return ontology.object_context(db, object_type, object_id)


@router.get("/needs/{need_id}/decision-context")
def get_need_decision_context(need_id: str, db: Session = Depends(get_db)):
    return ontology.decision_context(db, need_id)


@router.get("/reasoning/operational-risks")
def get_operational_risks(limit: int = 30, db: Session = Depends(get_db)):
    return reasoning.operational_risks(db, limit=limit)
