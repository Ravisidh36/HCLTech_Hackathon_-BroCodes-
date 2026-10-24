import logging
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api import auth, chat
from app.api.ratelimit import limiter
from app.config import get_settings
from app.db import SessionLocal, check_db

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    if settings.warm_models:
        # Load models once at startup so the first user request and the first live upload are fast.
        from app.rag.embed import check_embedding_model, get_embedder
        from app.rag.parse import warm_docling
        from app.rag.rerank import get_reranker

        started = time.time()
        get_embedder()
        get_reranker()
        warm_docling()
        with SessionLocal() as db:
            check_embedding_model(db)
        log.info("Models ready in %.1fs", time.time() - started)
    yield


app = FastAPI(title="Hospital Patient Services Assistant", version="0.4.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def trace_requests(request: Request, call_next):
    """Every request gets a trace id (R10), returned in the X-Trace-Id header."""
    request.state.trace_id = f"HSP-{secrets.token_hex(5).upper()}"
    request.state.started = time.perf_counter()
    response = await call_next(request)
    response.headers["X-Trace-Id"] = request.state.trace_id
    return response


app.include_router(auth.router)
app.include_router(chat.router)


@app.get("/health")
def health() -> dict:
    db_ok = check_db()
    return {"status": "ok" if db_ok else "degraded", "db": "ok" if db_ok else "unavailable"}
