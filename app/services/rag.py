# -*- coding: utf-8 -*-
"""
RAG 服務
SQLite 環境：embedding 存 JSON，查詢時用 numpy cosine similarity
Supabase 環境：之後可換成 pgvector
"""
import json
import logging
import math
import re

import numpy as np
from google import genai
from google.genai import types

from app.database import SessionLocal
from app.config import settings

log = logging.getLogger(__name__)

EMBED_MODEL        = "gemini-embedding-001"   # 3072-dim
GENERATE_MODEL     = "gemini-2.5-flash"
SIMILARITY_THRESHOLD = 0.70
TOP_K              = 3
_client_instance = None
GEMINI_TIMEOUT_MS = 20_000


def _client():
    global _client_instance
    if not settings.EXTERNAL_AI_ENABLED:
        raise RuntimeError("External AI processing is disabled")
    if _client_instance is None:
        # SDK 預設不設逾時：Gemini 卡住時 LINE 那一則會一直等到回覆權杖過期，使用者什麼都收不到。
        # 逾時後走下面的例外處理，改回知識庫原文摘錄。查一次是 embedding＋生成，最壞約 40 秒。
        from google.genai import types
        _client_instance = genai.Client(api_key=settings.GEMINI_API_KEY,
                                        http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS))
    return _client_instance


# ──────────────────────────────────────────────
# 主要查詢
# ──────────────────────────────────────────────
# ──────────────────────────────────────────────
# 不靠外部 AI 的關鍵字檢索：AI 未啟用、額度用完或斷線時，志工仍查得到 SOP
# ──────────────────────────────────────────────
_QUESTION_WORDS = ("怎麼辦", "怎麼做", "怎麼", "如何", "什麼", "請問", "應該", "可以", "需要", "要不要", "是不是")


def plain_text(text: str) -> str:
    """LINE 不顯示 Markdown；模型偶爾還是會輸出 **粗體** 或 * 條列，轉成純文字。"""
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"(?m)^\s*#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^(\s*)[*\-•]\s+", r"\1・", text)
    text = re.sub(r"(?m)^(\s*\d+\.)\s{2,}", r"\1 ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _bigrams(text: str) -> set[str]:
    text = re.sub(r"[\s\W_]+", "", text.lower())
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _keyword_search(question: str, top_k: int = TOP_K) -> list[dict]:
    """中文沒有空白斷詞，用兩字詞重疊計分，並依稀有度加權（越少段落出現的詞越有鑑別力）。"""
    for word in _QUESTION_WORDS:
        question = question.replace(word, " ")
    wanted = _bigrams(question)
    if not wanted:
        return []
    db = SessionLocal()
    try:
        from app.models.knowledge import KnowledgeChunk
        rows = db.query(KnowledgeChunk.content, KnowledgeChunk.source).all()
    finally:
        db.close()
    grams = [_bigrams(content) for content, _ in rows]
    # 只拿知識庫裡出現過的詞計分；「阿嬤」這種口語詞不在任何段落，算進分母只會稀釋掉「跌倒」。
    df = {g: sum(g in doc for doc in grams) for g in wanted}
    known = {g for g, n in df.items() if n}
    if not known:
        return []
    weight = {g: math.log((len(rows) + 1) / (1 + df[g])) + 1 for g in known}
    total = sum(weight.values())
    scored = []
    for (content, source), doc in zip(rows, grams):
        hits = known & doc
        in_title = bool(hits & _bigrams(content.strip().split("\n", 1)[0]))
        # 標題命中（通常就是在講這件事）或至少三個詞重疊才算；「我很無聊」不該撈到打卡 SOP。
        if hits and (in_title or len(hits) >= 3):
            score = sum(weight[g] for g in hits) / total + (0.2 if in_title else 0)
            scored.append((score, content, source))
    scored.sort(key=lambda row: row[0], reverse=True)
    return [{"content": c, "source": src, "score": round(sc, 3)} for sc, c, src in scored[:top_k] if sc >= 0.35]


def _keyword_answer(question: str) -> dict:
    results = _keyword_search(question)
    if not results:
        return {"answer": "❌ 系統內無相關資料，請聯繫專業人員或撥打 1966。", "sources": [], "has_answer": False}
    return {"answer": "以下為知識庫最相關段落（未經 AI 整理）：\n\n" + results[0]["content"][:600],
            "sources": list(dict.fromkeys(r["source"] for r in results)), "has_answer": True,
            "chunks_used": len(results), "mode": "keyword"}


def query(question: str) -> dict:
    if not settings.EXTERNAL_AI_ENABLED:
        return _keyword_answer(question)
    try:
        embedding = _embed(question)
    except Exception:
        log.exception("Gemini embedding failed; falling back to keyword search")
        return _keyword_answer(question)
    results = _search(embedding)

    if not results:
        return {
            "answer":     "❌ 系統內無相關資料，請聯繫專業人員或撥打 1966。",
            "sources":    [],
            "has_answer": False,
        }

    context = "\n\n---\n\n".join(r["content"] for r in results)

    prompt = f"""你是社區居民與志工的照護與急救助手。
只能根據以下提供的資料回答，不可超出範圍、不可自行推測。
回答要簡短、具體、實用，適合在緊急情況下用手機快速閱讀，控制在 300 字以內。
回答會顯示在 LINE，不支援 Markdown：不要用 **、#、* 這類符號，用純文字與 1. 2. 3. 條列。
若資料不足，請如實說明並建議撥打 1966 或 119。

參考資料：
{context}

問題：{question}"""

    try:
        response = _client().models.generate_content(
            model=GENERATE_MODEL,
            contents=prompt,
        )
        answer   = plain_text(response.text or "")
    except Exception:
        # 任何錯誤都 fallback 回傳最相關 chunk，但要留下原因，否則線上壞了查不到
        log.exception("Gemini generate_content failed; falling back to top chunk")
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
def ingest_document(content: str, source: str, category: str, version: str | None = None,
                    skip_existing: bool = False) -> int:
    chunks   = _chunk_text(content)
    db       = SessionLocal()
    inserted = 0

    try:
        from app.models.knowledge import KnowledgeChunk
        have = set()
        if skip_existing:
            have = {row[0] for row in db.query(KnowledgeChunk.content).filter(KnowledgeChunk.source == source).all()}
        for chunk in chunks:
            if chunk in have:
                continue
            emb = _embed(chunk)
            kc  = KnowledgeChunk(
                content   = chunk,
                embedding = json.dumps(emb),   # SQLite: 存 JSON string
                source    = source,
                category  = category,
                version   = version,
            )
            db.add(kc)
            inserted += 1
        db.commit()
    finally:
        db.close()

    return inserted


def _builtin_documents() -> list[dict]:
    import importlib
    import os
    import sys
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.reload(importlib.import_module("ingest_kb")).DOCUMENTS


def rebuild_builtin_documents() -> int:
    """Replace the whole table with the shipped documents.

    Every embedding is computed before anything is deleted, and the swap is one transaction:
    if Gemini fails halfway the old knowledge base stays intact instead of ending up empty."""
    from app.models.knowledge import KnowledgeChunk
    rows = [KnowledgeChunk(content=chunk, embedding=json.dumps(_embed(chunk)), source=doc["source"],
                           category=doc["category"], version=doc.get("version", "1.0"))
            for doc in _builtin_documents() for chunk in _chunk_text(doc["content"])]
    db = SessionLocal()
    try:
        db.query(KnowledgeChunk).delete()
        db.add_all(rows)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return len(rows)


def sync_builtin_documents() -> dict:
    """Add the shipped documents that are missing from the database, and never delete or overwrite.

    "Reload everything" wipes the table, which also throws away anything an admin edited in the
    console. This only fills gaps, so it is safe to run at every startup and from the console."""
    added, failed = 0, 0
    documents = _builtin_documents()
    for doc in documents:
        try:
            added += ingest_document(doc["content"], doc["source"], doc["category"],
                                     version=doc.get("version", "1.0"), skip_existing=True)
        except Exception:
            failed += 1
    return {"added": added, "failed": failed, "documents": len(documents)}


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
    result = _client().models.embed_content(
        model=EMBED_MODEL,
        contents=text_input,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    if not result.embeddings:
        raise RuntimeError("Gemini returned no embedding")
    return list(result.embeddings[0].values or [])


def _chunk_text(text: str, chunk_size: int = 400, overlap: int = 50) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start:start + chunk_size].strip())
        start += chunk_size - overlap
    return [c for c in chunks if len(c) > 30]
