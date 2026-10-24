"""Steps 6.2–6.3: admin-only document ingestion (R11), document registry and audit lookup (R10)."""

import hashlib
import re
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.audit.writer import AuditRecord, write_audit
from app.auth.deps import require_admin
from app.config import get_settings
from app.db import get_db, get_session_factory
from app.models.knowledge import Document
from app.models.platform import AuditLog
from app.rag.ingest import DocumentMeta, IngestError, ingest_file
from app.rag.parse import SUPPORTED_SUFFIXES
from app.schemas.api import AuditOut, DocumentOut, IngestResponse
from app.schemas.common import Authority, DocStatus, RequestContext

router = APIRouter(prefix="/admin", tags=["admin"])


def _store_upload(content: bytes, filename: str) -> Path:
    """Save under a content-hash name: the client's filename never becomes a filesystem path."""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unsupported file type '{suffix}'.")
    upload_dir = get_settings().upload_dir
    upload_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", Path(filename).stem)[:60] or "document"
    path = upload_dir / f"{hashlib.sha256(content).hexdigest()[:16]}_{stem}{suffix}"
    path.write_bytes(content)
    return path


@router.post("/documents", response_model=IngestResponse)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    doc_id: str = Form(...),
    title: str = Form(...),
    topic: str = Form(...),
    version: str = Form(...),
    effective_date: date = Form(...),
    authority: Authority = Form(...),
    doc_status: DocStatus = Form(DocStatus.ACTIVE, alias="status"),
    ctx: RequestContext = Depends(require_admin),
    db: Session = Depends(get_db),
    session_factory: sessionmaker = Depends(get_session_factory),
) -> IngestResponse:
    settings = get_settings()
    content = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    error: str | None = None
    result = None
    try:
        if len(content) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"File exceeds {settings.max_upload_mb} MB.")
        if not content.strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "The file is empty.")
        try:
            meta = DocumentMeta(
                doc_id=doc_id, title=title, topic=topic, version=version,
                effective_date=effective_date, authority=authority, status=doc_status,
            )
        except ValidationError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, exc.errors(include_url=False)) from None

        path = _store_upload(content, file.filename or "document")
        try:
            result = ingest_file(db, path, meta, uploaded_by=ctx.username)
        except (IngestError, ValueError) as exc:
            raise HTTPException(status.HTTP_409_CONFLICT if "already exists" in str(exc) else 400, str(exc)) from None
        return IngestResponse(
            status=result.status,
            doc_id=result.doc_id,
            version=result.version,
            chunks=result.chunks,
            injection_flags=result.injection_flags,
            superseded=result.superseded or [],
            message=result.message,
            trace_id=ctx.trace_id,
        )
    except HTTPException as exc:
        error = str(exc.detail)[:500]
        raise
    finally:
        write_audit(
            session_factory,
            AuditRecord(
                trace_id=ctx.trace_id,
                endpoint="/admin/documents",
                user_id=ctx.user_id,
                role=ctx.role.value,
                question=f"upload '{file.filename}' as {doc_id} v{version} ({authority}, {doc_status})",
                sources=[{"doc_id": doc_id, "version": version, "result": result.status if result else None,
                          "chunks": result.chunks if result else 0,
                          "injection_flags": result.injection_flags if result else 0}],
                error=error,
            ),
        )


@router.get("/documents", response_model=list[DocumentOut])
def list_documents(ctx: RequestContext = Depends(require_admin), db: Session = Depends(get_db)) -> list[Document]:
    return list(db.scalars(select(Document).order_by(Document.topic, Document.doc_id, Document.effective_date)))


@router.get("/audit", response_model=list[AuditOut])
def recent_audit(
    limit: int = Query(50, ge=1, le=200),
    endpoint: str | None = Query(None, max_length=80),
    ctx: RequestContext = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)
    if endpoint:
        stmt = stmt.where(AuditLog.endpoint == endpoint)
    return list(db.scalars(stmt))


@router.get("/audit/{trace_id}", response_model=list[AuditOut])
def audit_by_trace(trace_id: str, ctx: RequestContext = Depends(require_admin), db: Session = Depends(get_db)) -> list[AuditLog]:
    rows = list(db.scalars(select(AuditLog).where(AuditLog.trace_id == trace_id).order_by(AuditLog.id)))
    if not rows:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No audit records for that trace id.")
    return rows
