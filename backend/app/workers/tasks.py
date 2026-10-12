import dramatiq
import logging
import os
from pathlib import Path
from dramatiq.brokers.redis import RedisBroker
from datetime import datetime, timezone
from uuid import uuid4

from ..config import get_settings
from ..db import database
from ..processing import build_chunks, extract_document
from ..providers import embedding_provider, vector_store

settings = get_settings()
logger = logging.getLogger("book_rag.processing")
dramatiq.set_broker(RedisBroker(url=settings.redis_url))


@dramatiq.actor(max_retries=3, time_limit=15 * 60 * 1000)
def process_document(job_id: str) -> None:
    import asyncio

    asyncio.run(_process_document(job_id, manage_connection=True))


async def _process_document(job_id: str, manage_connection: bool = True) -> None:
    if manage_connection:
        await database.connect()
    try:
        db = database.database
        assert db is not None
        job = await db.processing_jobs.find_one({"job_id": job_id})
        if not job:
            return
        now = datetime.now(timezone.utc)
        await db.processing_jobs.update_one({"job_id": job_id}, {"$set": {"status": "extracting", "current_stage": "extracting", "progress": 15, "started_at": now}})
        temp_path = Path(job["temp_path"])
        document = await db.documents.find_one({"document_id": job["document_id"], "user_id": job["user_id"]})
        if not document or not temp_path.is_file():
            raise FileNotFoundError("Temporary upload is unavailable")
        pages = extract_document(temp_path.read_bytes(), document["file_type"])
        await db.processing_jobs.update_one({"job_id": job_id}, {"$set": {"status": "chunking", "current_stage": "chunking", "progress": 45}})
        chunks = build_chunks(pages, settings.chunk_size, settings.chunk_overlap)
        if not chunks:
            raise ValueError("No readable text was found in the document")
        await db.processing_jobs.update_one({"job_id": job_id}, {"$set": {"status": "embedding", "current_stage": "embedding", "progress": 65}})
        documents = []
        vector_points = []
        for chunk in chunks:
            chunk_id = str(uuid4())
            documents.append({"chunk_id": chunk_id, "book_id": job["book_id"], "document_id": job["document_id"], "user_id": job["user_id"], **chunk, "metadata": {}, "vector_reference": {"provider": "qdrant", "collection": settings.vector_db_collection, "point_id": chunk_id}, "created_at": now})
            vector = await embedding_provider.embed(chunk["chunk_text"])
            vector_points.append((chunk_id, vector, {"user_id": job["user_id"], "book_id": job["book_id"], "document_id": job["document_id"], "chunk_id": chunk_id, "page_number": chunk["page_number"], "chapter": chunk["chapter"], "section": chunk["section"], "chunk_index": chunk["chunk_index"]}))
        await vector_store.upsert(vector_points)
        if documents:
            await db.document_chunks.insert_many(documents)
        await db.processing_jobs.update_one({"job_id": job_id}, {"$set": {"status": "completed", "current_stage": "completed", "progress": 100, "completed_at": datetime.now(timezone.utc)}})
        await db.books.update_one({"book_id": job["book_id"], "user_id": job["user_id"]}, {"$set": {"processing_status": "completed", "chunk_count": len(documents), "updated_at": datetime.now(timezone.utc)}})
    except Exception as exc:
        logger.exception("processing stage=failed job_id=%s", job_id)
        await database.database.processing_jobs.update_one({"job_id": job_id}, {"$set": {"status": "failed", "current_stage": "failed", "error": str(exc)[:500]}})
        if "job" in locals() and job:
            await database.database.books.update_one({"book_id": job["book_id"], "user_id": job["user_id"]}, {"$set": {"processing_status": "failed", "processing_error": str(exc)[:500], "updated_at": datetime.now(timezone.utc)}})
        raise
    finally:
        temp_path = job.get("temp_path") if "job" in locals() and job else None
        if temp_path:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
        if manage_connection:
            await database.close()
