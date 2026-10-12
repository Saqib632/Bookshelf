import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pwdlib import PasswordHash

from .config import get_settings
from .db import get_database

password_hash = PasswordHash.recommended()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
logger = logging.getLogger("book_rag.auth")


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return password_hash.verify(password, hashed)
    except Exception:
        logger.exception("auth stage=password_verification_failed")
        return False


def create_access_token(user_id: str) -> str:
    settings = get_settings()
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub": user_id, "exp": expires}, settings.secret_key, algorithm="HS256")


async def current_user_id(token: Annotated[str, Depends(oauth2_scheme)]) -> str:
    try:
        payload = jwt.decode(token, get_settings().secret_key, algorithms=["HS256"])
        user_id = payload.get("sub")
        if not user_id:
            raise ValueError
    except (jwt.PyJWTError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc
    user = await get_database().users.find_one({"user_id": user_id}, {"_id": 1})
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return user_id
