from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.services import rag as rag_svc
from app.security import require_admin

router = APIRouter()


class QueryRequest(BaseModel):
    question: str


class IngestRequest(BaseModel):
    content: str
    source: str
    category: str
    version: str | None = None


class ChunkUpdateRequest(BaseModel):
    content: str | None = None
    source: str | None = None
    category: str | None = None
    version: str | None = None


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
def ingest(req: IngestRequest, _admin: dict = Depends(require_admin)):
    """
    載入文件到知識庫（管理員用）
    """
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="content 不可為空")

    inserted = rag_svc.ingest_document(
        content  = req.content,
        source   = req.source,
        category = req.category,
        version  = req.version,
    )
    return {"message": f"成功載入 {inserted} 個 chunks", "inserted": inserted}


@router.post("/sync")
def sync_builtin(_admin: dict = Depends(require_admin)):
    """只補上資料庫缺少的預設文件，不刪除也不覆蓋（重新載入會清空後台編輯過的內容）。"""
    return rag_svc.sync_builtin_documents()


_ingest_status = {"running": False, "done": False, "chunks": 0, "error": None}

@router.post("/ingest_all")
def ingest_all(_admin: dict = Depends(require_admin)):
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


@router.get("/chunks")
def list_chunks(
    source: str | None = None,
    category: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """
    列出知識庫文件（管理員後台用），可查閱目前有哪些內容、來源與版本標記。
    """
    from app.database import SessionLocal
    from app.models.knowledge import KnowledgeChunk

    db = SessionLocal()
    try:
        q = db.query(KnowledgeChunk)
        if source:
            q = q.filter(KnowledgeChunk.source == source)
        if category:
            q = q.filter(KnowledgeChunk.category == category)
        total = q.count()
        chunks = (
            q.order_by(KnowledgeChunk.source, KnowledgeChunk.created_at)
            .offset(offset).limit(limit).all()
        )
        return {
            "total": total,
            "chunks": [
                {
                    "id":             str(c.id),
                    "content":        c.content,
                    "content_preview": (c.content[:80] + "…") if len(c.content) > 80 else c.content,
                    "source":         c.source,
                    "category":       c.category,
                    "version":        c.version,
                    "has_embedding":  c.embedding is not None,
                    "created_at":     str(c.created_at),
                }
                for c in chunks
            ],
        }
    finally:
        db.close()


@router.get("/chunks/{chunk_id}")
def get_chunk(chunk_id: str):
    """取得單一 chunk 完整內容（編輯用）"""
    from app.database import SessionLocal
    from app.models.knowledge import KnowledgeChunk

    db = SessionLocal()
    try:
        c = db.query(KnowledgeChunk).filter(KnowledgeChunk.id == chunk_id).first()
        if not c:
            raise HTTPException(status_code=404, detail="找不到此筆知識庫內容")
        return {
            "id": str(c.id), "content": c.content,
            "source": c.source, "category": c.category,
            "version": c.version,
            "created_at": str(c.created_at),
        }
    finally:
        db.close()


@router.put("/chunks/{chunk_id}")
def update_chunk(chunk_id: str, req: ChunkUpdateRequest, _admin: dict = Depends(require_admin)):
    """
    更新一筆知識庫內容（管理員後台用）。
    若內容有變更會重新向量化，確保搜尋結果與最新文字一致。
    """
    from app.database import SessionLocal
    from app.models.knowledge import KnowledgeChunk
    import json

    db = SessionLocal()
    try:
        c = db.query(KnowledgeChunk).filter(KnowledgeChunk.id == chunk_id).first()
        if not c:
            raise HTTPException(status_code=404, detail="找不到此筆知識庫內容")

        content_changed = req.content is not None and req.content.strip() and req.content != c.content
        if content_changed:
            c.content = req.content.strip()
            c.embedding = json.dumps(rag_svc._embed(c.content))
        if req.source is not None and req.source.strip():
            c.source = req.source.strip()
        if req.category is not None and req.category.strip():
            c.category = req.category.strip()
        if req.version is not None and req.version.strip():
            c.version = req.version.strip()

        db.commit()
        return {"message": "更新成功", "id": chunk_id, "re_embedded": content_changed}
    finally:
        db.close()


@router.delete("/chunks/{chunk_id}")
def delete_chunk(chunk_id: str, _admin: dict = Depends(require_admin)):
    """刪除一筆知識庫內容（管理員後台用）"""
    from app.database import SessionLocal
    from app.models.knowledge import KnowledgeChunk

    db = SessionLocal()
    try:
        c = db.query(KnowledgeChunk).filter(KnowledgeChunk.id == chunk_id).first()
        if not c:
            raise HTTPException(status_code=404, detail="找不到此筆知識庫內容")
        db.delete(c)
        db.commit()
        return {"message": "刪除成功", "id": chunk_id}
    finally:
        db.close()


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
