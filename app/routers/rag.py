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


_ingest_status = {"running": False, "done": False, "chunks": 0, "error": None}

@router.post("/ingest_all")
def ingest_all():
    """載入所有內建 SOP 知識庫（背景執行，立即回傳）"""
    import threading

    if _ingest_status["running"]:
        return {"message": "正在載入中，請稍後查詢 /api/rag/ingest_status"}

    def _run():
        import sys, os, importlib
        _ingest_status["running"] = True
        _ingest_status["done"]    = False
        _ingest_status["error"]   = None
        try:
            from app.database import SessionLocal
            from app.models.knowledge import KnowledgeChunk
            db = SessionLocal()
            db.query(KnowledgeChunk).delete()
            db.commit()
            db.close()

            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if root not in sys.path:
                sys.path.insert(0, root)

            ingest_kb = importlib.import_module("ingest_kb")
            importlib.reload(ingest_kb)
            total = ingest_kb.run()
            _ingest_status["chunks"] = total
            _ingest_status["done"]   = True
        except Exception as e:
            _ingest_status["error"] = str(e)
        finally:
            _ingest_status["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return {"message": "開始載入知識庫（背景執行），約需 1-2 分鐘，請用 GET /api/rag/ingest_status 查詢進度"}


@router.get("/ingest_status")
def ingest_status():
    """查詢知識庫載入進度"""
    return _ingest_status


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
