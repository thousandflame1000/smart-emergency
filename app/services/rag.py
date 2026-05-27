# -*- coding: utf-8 -*-
"""
RAG 服務
SQLite 環境：embedding 存 JSON，查詢時用 numpy cosine similarity
Supabase 環境：之後可換成 pgvector
"""
import json
import numpy as np
import google.generativeai as genai

from app.database import SessionLocal, _is_sqlite
from app.config import settings

genai.configure(api_key=settings.GEMINI_API_KEY)

EMBED_MODEL        = "models/gemini-embedding-001"   # 3072-dim
GENERATE_MODEL     = "models/gemini-2.5-flash"
SIMILARITY_THRESHOLD = 0.70
TOP_K              = 3


# ──────────────────────────────────────────────
# 主要查詢
# ──────────────────────────────────────────────
def query(question: str) -> dict:
    embedding = _embed(question)
    results   = _search(embedding)

    if not results:
        return {
            "answer":     "❌ 系統內無相關資料，請聯繫專業人員或撥打 1966。",
            "sources":    [],
            "has_answer": False,
        }

    context = "\n\n---\n\n".join(r["content"] for r in results)

    prompt = f"""你是志工的照護與急救助手。
只能根據以下提供的資料回答，不可超出範圍、不可自行推測。
回答要簡短、具體、實用，適合在緊急情況下快速閱讀。
若資料不足，請如實說明並建議撥打 1966 或 119。

參考資料：
{context}

問題：{question}"""

    try:
        model    = genai.GenerativeModel(GENERATE_MODEL)
        response = model.generate_content(prompt)
        answer   = response.text
    except Exception as e:
        # 任何錯誤都 fallback 回傳最相關 chunk
        answer = (
            "⚠️ AI 生成暫時無法使用，以下為直接摘錄最相關資料：\n\n"
            + results[0]["content"][:600]
        )

    sources = list({r["source"] for r in results})

    return {
        "answer":     answer,
        "sources":    sources,
        "has_answer": True,
        "chunks_used": len(results),
    }


# ──────────────────────────────────────────────
# 載入文件
# ──────────────────────────────────────────────
def ingest_document(content: str, source: str, category: str) -> int:
    chunks   = _chunk_text(content)
    db       = SessionLocal()
    inserted = 0

    try:
        from app.models.knowledge import KnowledgeChunk
        for chunk in chunks:
            emb = _embed(chunk)
            kc  = KnowledgeChunk(
                content   = chunk,
                embedding = json.dumps(emb),   # SQLite: 存 JSON string
                source    = source,
                category  = category,
            )
            db.add(kc)
            inserted += 1
        db.commit()
    finally:
        db.close()

    return inserted


# ──────────────────────────────────────────────
# 向量搜尋（SQLite 版：numpy cosine）
# ──────────────────────────────────────────────
def _search(query_embedding: list[float]) -> list[dict]:
    db = SessionLocal()
    try:
        from app.models.knowledge import KnowledgeChunk
        chunks = db.query(KnowledgeChunk).filter(
            KnowledgeChunk.embedding != None
        ).all()

        if not chunks:
            return []

        q_vec = np.array(query_embedding, dtype=np.float32)
        scored = []

        for c in chunks:
            try:
                c_vec = np.array(json.loads(c.embedding), dtype=np.float32)
                sim   = float(np.dot(q_vec, c_vec) /
                              (np.linalg.norm(q_vec) * np.linalg.norm(c_vec) + 1e-9))
                if sim >= SIMILARITY_THRESHOLD:
                    scored.append({
                        "content":    c.content,
                        "source":     c.source,
                        "category":   c.category,
                        "similarity": sim,
                    })
            except Exception:
                continue

        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:TOP_K]
    finally:
        db.close()


# ──────────────────────────────────────────────
# 工具
# ──────────────────────────────────────────────
def _embed(text_input: str) -> list[float]:
    result = genai.embed_content(
        model     = EMBED_MODEL,
        content   = text_input,
        task_type = "retrieval_document",
    )
    return result["embedding"]


def _chunk_text(text: str, chunk_size: int = 400, overlap: int = 50) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start:start + chunk_size].strip())
        start += chunk_size - overlap
    return [c for c in chunks if len(c) > 30]
