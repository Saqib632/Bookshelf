import logging
import os
import tempfile
from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from google.auth.exceptions import GoogleAuthError
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from pymongo.errors import DuplicateKeyError

from .auth import create_access_token, current_user_id, hash_password, verify_password
from .config import get_settings
from .db import get_database
from .models import AuthRequest, BookResponse, ChatRequest, ChatResponse, ConversationResponse, GoogleAuthRequest, JobResponse, TokenResponse
from .providers import vector_store
from .services import create_chat_response, extension_for, normalize_filename, utcnow
from .workers.tasks import _process_document, process_document

router = APIRouter(prefix="/api")
logger = logging.getLogger("book_rag.api")


def book_response(book: dict) -> BookResponse:
    return BookResponse(**{key: book[key] for key in BookResponse.model_fields if key in book})


@router.post("/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: AuthRequest):
    try:
        database = get_database()
        email = payload.email.strip().lower()
        if await database.users.find_one({"email": email}):
            raise HTTPException(status_code=409, detail="An account with this email already exists")
        user_id = str(uuid4())
        await database.users.insert_one({"user_id": user_id, "email": email, "password_hash": hash_password(payload.password), "is_active": True, "created_at": utcnow(), "updated_at": utcnow()})
        return TokenResponse(user_id=user_id, email=email, access_token=create_access_token(user_id))
    except HTTPException:
        raise
    except DuplicateKeyError as exc:
        logger.warning("auth stage=register_duplicate email=%s", payload.email)
        raise HTTPException(status_code=409, detail="An account with this email already exists") from exc
    except Exception as exc:
        logger.exception("auth stage=register_failed email=%s", payload.email)
        raise HTTPException(status_code=500, detail="Account registration failed. Please try again.") from exc


@router.post("/auth/login", response_model=TokenResponse)
async def login(payload: AuthRequest):
    try:
        database = get_database()
        user = await database.users.find_one({"email": payload.email.strip().lower()})
        if not user or not verify_password(payload.password, user.get("password_hash", "")):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        return TokenResponse(user_id=user["user_id"], email=user["email"], access_token=create_access_token(user["user_id"]))
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("auth stage=login_failed email=%s", payload.email)
        raise HTTPException(status_code=500, detail="Sign in failed. Please try again.") from exc


@router.post("/auth/google", response_model=TokenResponse)
async def google_login(payload: GoogleAuthRequest):
    settings = get_settings()
    if not settings.google_client_id:
        raise HTTPException(status_code=503, detail="Google sign in is not configured")
    try:
        claims = id_token.verify_oauth2_token(
            payload.credential,
            google_requests.Request(),
            settings.google_client_id,
        )
    except (ValueError, GoogleAuthError) as exc:
        logger.warning("auth stage=google_invalid_token")
        raise HTTPException(status_code=401, detail="Invalid or expired Google token") from exc
    except Exception as exc:
        logger.exception("auth stage=google_verification_failed")
        raise HTTPException(status_code=502, detail="Could not verify the Google account") from exc

    google_id = claims.get("sub")
    email = claims.get("email")
    if not google_id or not email or claims.get("email_verified") is not True:
        raise HTTPException(status_code=401, detail="Your Google email could not be verified")

    database = get_database()
    normalized_email = email.strip().lower()
    now = utcnow()
    try:
        user = await database.users.find_one({"email": normalized_email})
        if user:
            update = {
                "$set": {
                    "google_id": google_id,
                    "name": claims.get("name"),
                    "avatar": claims.get("picture"),
                    "auth_provider": user.get("auth_provider", "password"),
                    "updated_at": now,
                }
            }
            await database.users.update_one({"_id": user["_id"]}, update)
            user_id = user["user_id"]
        else:
            user_id = str(uuid4())
            await database.users.insert_one({
                "user_id": user_id,
                "google_id": google_id,
                "email": normalized_email,
                "name": claims.get("name"),
                "avatar": claims.get("picture"),
                "auth_provider": "google",
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            })
        return TokenResponse(user_id=user_id, email=normalized_email, access_token=create_access_token(user_id))
    except DuplicateKeyError as exc:
        logger.warning("auth stage=google_duplicate email=%s", normalized_email)
        raise HTTPException(status_code=409, detail="This Google account is already linked") from exc
    except Exception as exc:
        logger.exception("auth stage=google_database_failed email=%s", normalized_email)
        raise HTTPException(status_code=500, detail="Google sign in could not be completed") from exc


@router.post("/auth/logout")
async def logout(_: str = Depends(current_user_id)):
    return {"status": "ok"}


@router.post("/books/upload", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
async def upload_book(file: UploadFile = File(...), user_id: str = Depends(current_user_id)):
    settings = get_settings()
    filename = normalize_filename(file.filename or "document")
    try:
        extension = extension_for(filename)
    except ValueError as exc:
        logger.warning("upload stage=validation user_id=%s filename=%s", user_id, filename)
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    book_id, document_id, job_id = str(uuid4()), str(uuid4()), str(uuid4())
    os.makedirs(settings.temp_upload_dir, exist_ok=True)
    temp_path = ""
    total_bytes = 0
    try:
        with tempfile.NamedTemporaryFile(dir=settings.temp_upload_dir, prefix=f"{job_id}-", suffix=f".{extension}", delete=False) as temporary_file:
            temp_path = temporary_file.name
            while chunk := await file.read(1024 * 1024):
                total_bytes += len(chunk)
                if total_bytes > settings.max_upload_bytes:
                    raise HTTPException(status_code=413, detail="File is too large")
                temporary_file.write(chunk)
    except HTTPException:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        raise
    now = utcnow()
    database = get_database()
    recent_duplicate = await database.books.find_one({
        "user_id": user_id,
        "file_name": filename,
        "file_size": total_bytes,
        "created_at": {"$gte": now - timedelta(minutes=10)},
    })
    if recent_duplicate:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        logger.warning("upload stage=duplicate user_id=%s filename=%s existing_book_id=%s", user_id, filename, recent_duplicate["book_id"])
        raise HTTPException(status_code=409, detail="This book was already uploaded recently")
    book = {"book_id": book_id, "user_id": user_id, "title": filename.rsplit(".", 1)[0], "author": None, "file_name": filename, "file_type": extension, "file_size": total_bytes, "upload_date": now, "processing_status": "queued", "processing_error": None, "page_count": None, "chunk_count": 0, "created_at": now, "updated_at": now}
    try:
        await database.books.insert_one(book)
        await database.documents.insert_one({"document_id": document_id, "book_id": book_id, "user_id": user_id, "file_name": filename, "file_type": extension, "page_count": None, "processing_status": "queued", "created_at": now, "updated_at": now})
        await database.processing_jobs.insert_one({"job_id": job_id, "user_id": user_id, "book_id": book_id, "document_id": document_id, "temp_path": temp_path, "status": "queued", "current_stage": "queued", "progress": 0, "error": None, "created_at": now})
    except Exception as exc:
        logger.exception("upload stage=database_insert_failed user_id=%s filename=%s", user_id, filename)
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        raise HTTPException(status_code=500, detail="The upload could not be saved. Please try again.") from exc
    processing_status = "queued"
    try:
        process_document.send(job_id)
    except Exception:
        logger.warning("upload stage=queue_unavailable job_id=%s; processing locally", job_id, exc_info=True)
        try:
            await _process_document(job_id, manage_connection=False)
            processing_status = "completed"
        except Exception as exc:
            logger.exception("upload stage=local_processing_failed job_id=%s", job_id)
            processing_status = "failed"
            return {"book_id": book_id, "document_id": document_id, "job_id": job_id, "processing_status": "failed", "processing_error": str(exc)[:200]}
    return {"book_id": book_id, "document_id": document_id, "job_id": job_id, "processing_status": processing_status}


@router.get("/books", response_model=list[BookResponse])
async def list_books(user_id: str = Depends(current_user_id)):
    books = await get_database().books.find({"user_id": user_id}).sort("created_at", -1).to_list(length=100)
    return [book_response(book) for book in books]


@router.get("/books/{book_id}", response_model=BookResponse)
async def get_book(book_id: str, user_id: str = Depends(current_user_id)):
    book = await get_database().books.find_one({"book_id": book_id, "user_id": user_id})
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    return book_response(book)


@router.get("/books/{book_id}/status", response_model=JobResponse)
async def book_status(book_id: str, user_id: str = Depends(current_user_id)):
    job = await get_database().processing_jobs.find_one({"book_id": book_id, "user_id": user_id}, sort=[("created_at", -1)])
    if not job:
        raise HTTPException(status_code=404, detail="Book not found")
    return JobResponse(**{key: job.get(key) for key in JobResponse.model_fields})


@router.delete("/books/{book_id}", status_code=status.HTTP_202_ACCEPTED)
async def delete_book(book_id: str, user_id: str = Depends(current_user_id)):
    database = get_database()
    jobs = await database.processing_jobs.find({"book_id": book_id, "user_id": user_id}, {"temp_path": 1}).to_list(length=100)
    book = await database.books.find_one({"book_id": book_id, "user_id": user_id})
    if not book:
        raise HTTPException(status_code=404, detail="Book not found")
    conversations = await database.conversations.find({"user_id": user_id, "book_ids": book_id}, {"conversation_id": 1, "_id": 0}).to_list(length=1000)
    conversation_ids = [item["conversation_id"] for item in conversations]
    if conversation_ids:
        await database.citations.delete_many({"user_id": user_id, "book_id": book_id})
        await database.messages.delete_many({"user_id": user_id, "conversation_id": {"$in": conversation_ids}})
        await database.conversations.delete_many({"user_id": user_id, "conversation_id": {"$in": conversation_ids}})
    await database.books.delete_one({"book_id": book_id, "user_id": user_id})
    await database.documents.delete_many({"book_id": book_id, "user_id": user_id})
    await database.document_chunks.delete_many({"book_id": book_id, "user_id": user_id})
    await database.processing_jobs.delete_many({"book_id": book_id, "user_id": user_id})
    try:
        await vector_store.delete_book(user_id, book_id)
    except Exception:
        logger.exception("book_delete stage=vector_cleanup_failed user_id=%s book_id=%s", user_id, book_id)
    for job in jobs:
        temp_path = job.get("temp_path")
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
    return {"book_id": book_id, "status": "deleted"}


@router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest, user_id: str = Depends(current_user_id)):
    try:
        result = await create_chat_response(get_database(), user_id, payload)
    except ValueError as exc:
        logger.warning("chat stage=validation user_id=%s error=%s", user_id, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except PermissionError as exc:
        logger.warning("chat stage=book_scope user_id=%s error=%s", user_id, exc)
        raise HTTPException(status_code=404, detail="Book scope not found") from exc
    except (ConnectionError, TimeoutError) as exc:
        logger.exception("chat stage=provider_unavailable user_id=%s", user_id)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("chat stage=failed user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="Chat could not be completed. Please try again.") from exc
    conversation_id, message_id, content, citations, attempts = result
    return ChatResponse(conversation_id=conversation_id, message_id=message_id, content=content, citations=citations, retrieval={"attempts": attempts, "sufficient": bool(citations)})


@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(user_id: str = Depends(current_user_id)):
    items = await get_database().conversations.find({"user_id": user_id}).sort("updated_at", -1).to_list(length=100)
    return [ConversationResponse(**{key: item.get(key) for key in ConversationResponse.model_fields}) for item in items]


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, user_id: str = Depends(current_user_id)):
    database = get_database()
    conversation = await database.conversations.find_one({"conversation_id": conversation_id, "user_id": user_id})
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = await database.messages.find({"conversation_id": conversation_id, "user_id": user_id}, {"_id": 0}).sort("created_at", 1).to_list(length=100)
    return {"conversation": conversation, "messages": messages}


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str, user_id: str = Depends(current_user_id)):
    database = get_database()
    result = await database.conversations.delete_one({"conversation_id": conversation_id, "user_id": user_id})
    if not result.deleted_count:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await database.messages.delete_many({"conversation_id": conversation_id, "user_id": user_id})
    await database.citations.delete_many({"message_id": {"$in": []}, "user_id": user_id})
    return {"conversation_id": conversation_id, "status": "deleted"}
