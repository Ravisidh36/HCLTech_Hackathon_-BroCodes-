"""Step 3.6: ingest one document into PostgreSQL in a single transaction (R11).

Metadata (version, authority, effective date, status) always comes from the caller — seed
front-matter or the admin upload form — never from the document's own text (R8).
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.knowledge import Chunk, Document
from app.rag.chunk import chunk_markdown
from app.rag.embed import embed_texts
from app.rag.parse import to_markdown
from app.rag.screen import looks_like_injection
from app.schemas.common import AUTHORITY_RANK, Authority, DocStatus
from app.services.common import today


class DocumentMeta(BaseModel):
    doc_id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9-]{1,38}$")
    title: str = Field(min_length=3, max_length=200)
    topic: str = Field(pattern=r"^[a-z0-9_]{2,60}$")
    version: str = Field(pattern=r"^\d+(\.\d+){0,3}$")
    effective_date: date
    authority: Authority
    status: DocStatus = DocStatus.ACTIVE

    @field_validator("version", mode="before")
    @classmethod
    def _version_as_text(cls, value: object) -> str:
        return str(value)


@dataclass
class IngestResult:
    status: str  # indexed | duplicate
    doc_id: str
    version: str
    chunks: int = 0
    injection_flags: int = 0
    superseded: list[str] | None = None
    message: str = ""


class IngestError(ValueError):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(8192), b""):
            digest.update(block)
    return digest.hexdigest()


def _embedding_text(title: str, section_path: str, content: str) -> str:
    # Title and section give short chunks enough context to embed well.
    return f"{title} | {section_path}\n{content}"


def ingest_file(db: Session, path: Path, meta: DocumentMeta, uploaded_by: str) -> IngestResult:
    file_hash = file_sha256(path)
    existing = db.scalars(select(Document).where(Document.file_hash == file_hash)).first()
    if existing is not None:
        return IngestResult(
            "duplicate", existing.doc_id, existing.version, message="Identical file already indexed; skipped."
        )
    if db.scalars(
        select(Document.id).where(Document.doc_id == meta.doc_id, Document.version == meta.version)
    ).first():
        raise IngestError(f"{meta.doc_id} version {meta.version} already exists. Upload a new version number.")

    drafts = chunk_markdown(to_markdown(path))
    if not drafts:
        raise IngestError("The document produced no text chunks.")
    flags = [looks_like_injection(d.content) for d in drafts]
    vectors = embed_texts([_embedding_text(meta.title, d.section_path, d.content) for d in drafts])

    superseded: list[str] = []
    try:
        document = Document(
            doc_id=meta.doc_id,
            title=meta.title,
            topic=meta.topic,
            version=meta.version,
            effective_date=meta.effective_date,
            authority=meta.authority.value,
            authority_rank=AUTHORITY_RANK[meta.authority],
            status=meta.status.value,
            file_hash=file_hash,
            source_filename=path.name,
            uploaded_by=uploaded_by,
            embedding_model=get_settings().embedding_model,
            chunk_count=len(drafts),
            injection_flags=sum(flags),
        )
        db.add(document)
        db.flush()
        safe_id = re.sub(r"[^A-Za-z0-9.-]", "", meta.doc_id)
        db.add_all(
            Chunk(
                id=f"{safe_id}:{meta.version}:{n}",
                document_id=document.id,
                section_path=draft.section_path[:300],
                chunk_type=draft.chunk_type,
                page=None,
                content=draft.content,
                injection_flag=flag,
                embedding=vector,
            )
            for n, (draft, flag, vector) in enumerate(zip(drafts, flags, vectors))
        )
        # A newly effective active version supersedes older active versions of the same document.
        # A future-dated version leaves the current one active; retrieval filters by effective date.
        if meta.status == DocStatus.ACTIVE and meta.effective_date <= today():
            superseded = list(
                db.scalars(
                    update(Document)
                    .where(
                        Document.doc_id == meta.doc_id,
                        Document.id != document.id,
                        Document.status == DocStatus.ACTIVE.value,
                        Document.effective_date <= meta.effective_date,
                    )
                    .values(status=DocStatus.SUPERSEDED.value)
                    .returning(Document.version)
                )
            )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise IngestError(f"Could not store document: {exc.orig}") from exc
    except Exception:
        db.rollback()
        raise

    return IngestResult(
        "indexed",
        meta.doc_id,
        meta.version,
        chunks=len(drafts),
        injection_flags=sum(flags),
        superseded=superseded,
    )
