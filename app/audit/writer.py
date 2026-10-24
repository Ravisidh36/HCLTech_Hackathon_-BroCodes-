"""Step 4.5: append-only audit records (R10).

Writes in its own session so the audit row survives even when the request's transaction fails.
"""

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.models.platform import AuditLog
from app.safety.pii import redact_for_audit

log = logging.getLogger(__name__)


@dataclass
class AuditRecord:
    trace_id: str
    endpoint: str
    user_id: int | None = None
    role: str | None = None
    patient_id: str | None = None
    question: str | None = None
    answer_type: str | None = None
    sources: list[Any] = field(default_factory=list)
    tools: list[Any] = field(default_factory=list)
    privacy_checks: list[Any] = field(default_factory=list)
    node_timings: list[Any] = field(default_factory=list)
    model: str | None = None
    token_usage: list[Any] = field(default_factory=list)
    latency_ms: int | None = None
    error: str | None = None


def write_audit(session_factory: sessionmaker[Session], record: AuditRecord) -> bool:
    """Persist one audit row. Never raises: auditing must not break the user-facing response."""
    values = asdict(record)
    question = values.pop("question")
    try:
        with session_factory() as db:
            db.add(AuditLog(**values, question_redacted=redact_for_audit(question) if question else None))
            db.commit()
        return True
    except Exception:
        log.exception("Audit write failed for trace %s", record.trace_id)
        return False
