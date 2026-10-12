from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    secret_key: str
    google_client_id: str = ""
    frontend_origin: str = "http://localhost:3000"
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "book_rag"
    vector_db_url: str = "http://localhost:6333"
    vector_db_api_key: str = ""
    vector_db_collection: str = "book_chunks"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""
    embedding_model: str = ""
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_provider: str = "local"
    local_embedding_model: str = "all-MiniLM-L6-v2"
    reranker_api_key: str = ""
    reranker_model: str = ""
    chunk_size: int = 800
    chunk_overlap: int = 120
    max_upload_bytes: int = 52_428_800
    temp_upload_dir: str = ".tmp/uploads"
    redis_url: str = "redis://localhost:6379/0"
    access_token_minutes: int = 30

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
