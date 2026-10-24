"""Application tables: users, pending actions, conversation turns, audit log."""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

SCHEMA = {"schema": "platform"}


def _json_list() -> Mapped[list[Any]]:
    return mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))


class User(Base):
    __tablename__ = "users"
    __table_args__ = SCHEMA

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    full_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16))  # patient | admin
    patient_id: Mapped[str | None] = mapped_column(ForeignKey("clinical.patients.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class PendingAction(Base):
    """A proposed state change awaiting explicit patient confirmation."""

    __tablename__ = "pending_actions"
    __table_args__ = SCHEMA

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("platform.users.id"))
    patient_id: Mapped[str] = mapped_column(ForeignKey("clinical.patients.id"), index=True)
    action_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    summary: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | executed | cancelled | expired | failed
    trace_id: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChatTurn(Base):
    __tablename__ = "chat_turns"
    __table_args__ = SCHEMA

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("platform.users.id"))
    speaker: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    answer_type: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    """R10. Append-only: the app role has no UPDATE/DELETE on this table."""

    __tablename__ = "audit_log"
    __table_args__ = SCHEMA

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(40), index=True)
    endpoint: Mapped[str] = mapped_column(String(80))
    user_id: Mapped[int | None] = mapped_column(Integer)
    role: Mapped[str | None] = mapped_column(String(16))
    patient_id: Mapped[str | None] = mapped_column(String(16))
    question_redacted: Mapped[str | None] = mapped_column(Text)
    answer_type: Mapped[str | None] = mapped_column(String(32))
    sources: Mapped[list[Any]] = _json_list()
    tools: Mapped[list[Any]] = _json_list()
    privacy_checks: Mapped[list[Any]] = _json_list()
    node_timings: Mapped[list[Any]] = _json_list()
    model: Mapped[str | None] = mapped_column(String(80))
    token_usage: Mapped[list[Any]] = _json_list()
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
