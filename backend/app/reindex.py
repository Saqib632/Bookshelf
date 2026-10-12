"""Re-embed MongoDB chunks into the configured Qdrant collection."""

import asyncio

from .config import get_settings
from .db import database
from .providers import embedding_provider, vector_store


async def reindex() -> None:
    settings = get_settings()
    await database.connect()
    try:
        assert database.database is not None
        chunks = database.database.document_chunks.find({}, {"_id": 0})
        batch = []
        total = 0
        async for chunk in chunks:
            vector = await embedding_provider.embed(chunk["chunk_text"])
            batch.append((chunk["chunk_id"], vector, {
                "user_id": chunk["user_id"],
                "book_id": chunk["book_id"],
                "document_id": chunk["document_id"],
                "chunk_id": chunk["chunk_id"],
                "page_number": chunk.get("page_number"),
                "chapter": chunk.get("chapter"),
                "section": chunk.get("section"),
                "chunk_index": chunk.get("chunk_index", 0),
            }))
            if len(batch) >= 64:
                await vector_store.upsert(batch)
                total += len(batch)
                batch = []
                print(f"Indexed {total} chunks")
        if batch:
            await vector_store.upsert(batch)
            total += len(batch)
        print(f"Reindexed {total} chunks into {settings.vector_db_collection}")
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(reindex())