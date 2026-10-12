from __future__ import annotations

import re
import logging
from datetime import datetime, timezone
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase

from .providers import RetrievedChunk, embedding_provider, llm_provider, vector_store

logger = logging.getLogger("book_rag.retrieval")
STOP_WORDS = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "i", "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "what", "when", "where", "which", "who", "why", "with"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_filename(filename: str) -> str:
    basename = filename.replace("\\", "/").split("/")[-1]
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", basename).strip("._")
    return sanitized[:180] or "document"


def extension_for(filename: str) -> str:
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix not in {"pdf", "docx"}:
        raise ValueError("Only PDF and DOCX files are supported")
    return suffix


def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    step = max(1, size - overlap)
    return [" ".join(words[index : index + size]) for index in range(0, len(words), step)]


async def search_chunks(
    database: AsyncIOMotorDatabase,
    user_id: str,
    book_ids: list[str],
    question: str,
    limit: int = 5,
) -> list[RetrievedChunk]:
    terms = {term.lower() for term in re.findall(r"[a-zA-Z0-9]{3,}", question) if term.lower() not in STOP_WORDS}
    query: dict = {"user_id": user_id}
    if book_ids:
        query["book_id"] = {"$in": book_ids}
    candidates = await database.document_chunks.find(query).to_list(length=500)
    scored: list[RetrievedChunk] = []
    for item in candidates:
        words = set(re.findall(r"[a-zA-Z0-9]{3,}", item["chunk_text"].lower()))
        score = len(terms & words) / max(1, len(terms))
        if score > 0:
            scored.append(RetrievedChunk(item["chunk_id"], item["book_id"], item["document_id"], item["chunk_text"], item.get("page_number"), item.get("chapter"), item.get("section"), score))
    lexical_by_id = {item.chunk_id: item for item in scored}
    try:
        query_vector = await embedding_provider.embed(question)
        vector_hits = await vector_store.search(query_vector, user_id, book_ids, limit=limit)
        logger.info("retrieval stage=vector user_id=%s book_ids=%s hits=%s scores=%s", user_id, book_ids, len(vector_hits), [round(hit.score, 4) for hit in vector_hits])
        candidates_by_id = {item["chunk_id"]: item for item in candidates}
        for hit in vector_hits:
            item = candidates_by_id.get(str(hit.id))
            if not item:
                continue
            vector_score = float(hit.score)
            existing = lexical_by_id.get(item["chunk_id"])
            if existing:
                existing.score = max(existing.score, vector_score)
            else:
                scored.append(RetrievedChunk(item["chunk_id"], item["book_id"], item["document_id"], item["chunk_text"], item.get("page_number"), item.get("chapter"), item.get("section"), vector_score))
    except Exception:
        logger.exception("retrieval stage=vector_failed user_id=%s book_ids=%s", user_id, book_ids)
        if not scored:
            raise ConnectionError("Vector search is unavailable")
    result = sorted(scored, key=lambda item: item.score, reverse=True)[:limit]
    logger.info("retrieval stage=combined user_id=%s book_ids=%s chunks=%s scores=%s", user_id, book_ids, len(result), [round(item.score, 4) for item in result])
    return result


async def run_crag(database: AsyncIOMotorDatabase, user_id: str, book_ids: list[str], question: str):
    first = await search_chunks(database, user_id, book_ids, question)
    logger.info("retrieval stage=crag attempt=1 user_id=%s accepted=%s threshold=0.15", user_id, bool(first and first[0].score >= 0.15))
    if first and first[0].score >= 0.15:
        return first, 1
    corrected = " ".join(word for word in question.split() if len(word) > 2)
    second = await search_chunks(database, user_id, book_ids, corrected)
    logger.info("retrieval stage=crag attempt=2 user_id=%s accepted=%s threshold=0.15", user_id, bool(second and second[0].score >= 0.15))
    if second and second[0].score >= 0.15:
        return second, 2
    return [], 2


async def create_chat_response(database, user_id: str, request):
    now = utcnow()
    conversation_id = request.conversation_id or str(uuid4())
    book_ids = list(dict.fromkeys(request.book_ids or ([request.book_id] if request.book_id else [])))
    if not book_ids:
        raise ValueError("A book scope is required")
    owned = await database.books.count_documents({"user_id": user_id, "book_id": {"$in": book_ids}})
    if owned != len(book_ids):
        raise PermissionError("Book scope is not available")
    existing = await database.conversations.find_one({"conversation_id": conversation_id, "user_id": user_id})
    if not existing:
        await database.conversations.insert_one({"conversation_id": conversation_id, "user_id": user_id, "book_id": book_ids[0] if len(book_ids) == 1 else None, "book_ids": book_ids, "title": request.message[:80], "summary": None, "created_at": now, "updated_at": now})
    evidence, attempts = await run_crag(database, user_id, book_ids, request.message)
    history_items = await database.messages.find(
        {"conversation_id": conversation_id, "user_id": user_id},
        {"role": 1, "content": 1, "_id": 0},
    ).sort("created_at", -1).to_list(length=6)
    history = "\n".join(f"{item['role']}: {item['content']}" for item in reversed(history_items))
    content = await llm_provider.answer(request.message, evidence, history)
    message_id = str(uuid4())
    await database.messages.insert_one({"message_id": message_id, "conversation_id": conversation_id, "user_id": user_id, "role": "user", "content": request.message, "sources": [], "created_at": now})
    citations = []
    if evidence:
        for chunk in evidence:
            citation_id = str(uuid4())
            citations.append({"citation_id": citation_id, "chunk_id": chunk.chunk_id, "page_number": chunk.page_number, "chapter": chunk.chapter, "section": chunk.section, "source_text": chunk.text[:1000]})
            await database.citations.insert_one({**citations[-1], "message_id": message_id, "user_id": user_id, "book_id": chunk.book_id, "document_id": chunk.document_id, "created_at": now})
    await database.messages.insert_one({"message_id": str(uuid4()), "conversation_id": conversation_id, "user_id": user_id, "role": "assistant", "content": content, "sources": [{"citation_id": item["citation_id"], "chunk_id": item["chunk_id"]} for item in citations], "retrieval_metadata": {"attempt_count": attempts, "sufficient": bool(evidence)}, "created_at": now})
    await database.conversations.update_one({"conversation_id": conversation_id, "user_id": user_id}, {"$set": {"updated_at": now}})
    return conversation_id, message_id, content, citations, attempts
