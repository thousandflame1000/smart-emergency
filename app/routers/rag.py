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


@router.post("/ingest_all")
def ingest_all():
    """載入所有內建 SOP 知識庫（管理員用，可重複執行）"""
    import sys, os
    from app.database import SessionLocal
    from app.models.knowledge import KnowledgeChunk

    # 先清空舊的
    db = SessionLocal()
    db.query(KnowledgeChunk).delete()
    db.commit()
    db.close()

    # 確保根目錄在 sys.path
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)

    try:
        import importlib, traceback
        ingest_kb = importlib.import_module("ingest_kb")
        importlib.reload(ingest_kb)
        total = ingest_kb.run()
        return {"message": "知識庫載入完成", "chunks": total}
    except Exception as e:
        return {"error": str(e), "traceback": traceback.format_exc()[-1000:]}


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
