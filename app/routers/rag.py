from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import rag as rag_svc

router = APIRouter()


class QueryRequest(BaseModel):
    question: str


class IngestRequest(BaseModel):
    content: str
    source: str
    category: str


@router.post("/query")
def query_rag(req: QueryRequest):
    """
    RAG 查詢：根據問題從知識庫找相關段落，用 Gemini 生成回答
    """
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question 不可為空")

    result = rag_svc.query(req.question)
    return result


@router.post("/ingest")
def ingest(req: IngestRequest):
    """
    載入文件到知識庫（管理員用）
    """
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="content 不可為空")

    inserted = rag_svc.ingest_document(
        content  = req.content,
        source   = req.source,
        category = req.category,
    )
    return {"message": f"成功載入 {inserted} 個 chunks", "inserted": inserted}


@router.get("/stats")
def rag_stats():
    """
    知識庫統計
    """
    from app.database import SessionLocal
    from app.models.knowledge import KnowledgeChunk

    db = SessionLocal()
    try:
        total = db.query(KnowledgeChunk).count()
        has_emb = db.query(KnowledgeChunk).filter(
            KnowledgeChunk.embedding != None
        ).count()
        categories = db.query(KnowledgeChunk.category).distinct().all()
        return {
            "total_chunks": total,
            "chunks_with_embedding": has_emb,
            "categories": [c[0] for c in categories],
        }
    finally:
        db.close()
