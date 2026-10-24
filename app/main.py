from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db import check_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_settings()
    # Later phases load BGE-M3, the reranker, Docling and compile the graph here.
    yield


app = FastAPI(
    title="Hospital Patient Services Assistant",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    db_ok = check_db()
    return {"status": "ok" if db_ok else "degraded", "db": "ok" if db_ok else "unavailable"}
