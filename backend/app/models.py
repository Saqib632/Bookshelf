from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class AuthRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)


class GoogleAuthRequest(BaseModel):
    credential: str = Field(min_length=1)


class TokenResponse(BaseModel):
    user_id: str
    email: str
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class BookResponse(BaseModel):
    book_id: str
    user_id: str
    title: str
    author: str | None = None
    file_name: str
    file_type: str
    file_size: int
    processing_status: str
    processing_error: str | None = None
    page_count: int | None = None
    chunk_count: int = 0
    created_at: datetime
    updated_at: datetime


class JobResponse(BaseModel):
    job_id: str
    book_id: str
    status: str
    current_stage: str
    progress: int
    error: str | None = None


class CitationResponse(BaseModel):
    citation_id: str
    chunk_id: str
    page_number: int | None = None
    chapter: str | None = None
    section: str | None = None
    source_text: str


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    book_id: str | None = None
    book_ids: list[str] = Field(default_factory=list)
    message: str = Field(min_length=1, max_length=4000)
    stream: bool = False


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    content: str
    citations: list[CitationResponse]
    retrieval: dict


class ConversationResponse(BaseModel):
    conversation_id: str
    title: str
    book_id: str | None
    book_ids: list[str]
    updated_at: datetime
