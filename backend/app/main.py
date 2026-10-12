import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api import router
from .config import get_settings
from .db import database

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("book_rag")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await database.connect()
    yield
    await database.close()


app = FastAPI(title="Chat With Your Book API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.warning("validation stage=%s path=%s errors=%s", request.method, request.url.path, exc.errors())
    return JSONResponse(status_code=400, content={"detail": "Invalid request data", "errors": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("unexpected stage=request method=%s path=%s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "The server could not complete this request. Check backend logs."})


@app.get("/health")
async def health():
    return {"status": "ok"}
