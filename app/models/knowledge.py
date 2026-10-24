"""Approved hospital knowledge base: documents and their embedded chunks (R1, R2, R4, R11)."""

from datetime import date, datetime

from sqlalchemy import (
    REAL,
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("doc_id", "version"), {"schema": "knowledge"})

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(40), index=True)
    title: Mapped[str] = mapped_column(String(200))
    topic: Mapped[str] = mapped_column(String(60), index=True)
    version: Mapped[str] = mapped_column(String(16))
    effective_date: Mapped[date] = mapped_column(Date)
    authority: Mapped[str] = mapped_column(String(32))
    authority_rank: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), index=True)  # active | superseded | draft
    file_hash: Mapped[str] = mapped_column(String(64), unique=True)
    source_filename: Mapped[str] = mapped_column(String(200))
    uploaded_by: Mapped[str] = mapped_column(String(64))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    embedding_model: Mapped[str] = mapped_column(String(80))
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    injection_flags: Mapped[int] = mapped_column(Integer, default=0)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        {"schema": "knowledge"},
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)  # {doc_id}:{version}:{n}
    document_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge.documents.id", ondelete="CASCADE"), index=True
    )
    section_path: Mapped[str] = mapped_column(String(300))
    chunk_type: Mapped[str] = mapped_column(String(8))  # text | table
    page: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    injection_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    # BGE-M3 dense vector (1024-d, normalized). Exact cosine search in numpy; pgvector is the upgrade path.
    embedding: Mapped[list[float]] = mapped_column(ARRAY(REAL))
    # Keyword half of hybrid retrieval, maintained by Postgres.
    tsv: Mapped[str] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', section_path || ' ' || content)", persisted=True)
    )
