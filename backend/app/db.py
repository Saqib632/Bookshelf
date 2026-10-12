from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from .config import get_settings


class Database:
    def __init__(self) -> None:
        self.client: AsyncIOMotorClient | None = None
        self.database: AsyncIOMotorDatabase | None = None

    async def connect(self) -> None:
        settings = get_settings()
        self.client = AsyncIOMotorClient(settings.mongodb_uri)
        self.database = self.client[settings.mongodb_database]
        await self.database.command("ping")
        await self._create_indexes()

    async def close(self) -> None:
        if self.client:
            self.client.close()

    async def _create_indexes(self) -> None:
        assert self.database is not None
        await self.database.users.create_index("email", unique=True)
        await self.database.users.create_index("google_id", unique=True, sparse=True)
        await self.database.books.create_index([("user_id", 1), ("created_at", -1)])
        await self.database.documents.create_index([("book_id", 1), ("user_id", 1)])
        await self.database.document_chunks.create_index([("user_id", 1), ("book_id", 1)])
        await self.database.conversations.create_index([("user_id", 1), ("updated_at", -1)])
        await self.database.messages.create_index([("conversation_id", 1), ("created_at", 1)])
        await self.database.processing_jobs.create_index([("book_id", 1), ("user_id", 1), ("status", 1)])


database = Database()


def get_database() -> AsyncIOMotorDatabase:
    if database.database is None:
        raise RuntimeError("Database is not connected")
    return database.database
