"""Step 6.1: explicit patient confirmation of a proposed action (§2 action request).

This is the only path that changes an appointment. execute_reschedule re-validates ownership,
expiry and slot availability under row locks inside one transaction.
"""

import time

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session, sessionmaker

from app.api.ratelimit import CHAT_LIMIT, limiter
from app.audit.writer import AuditRecord, write_audit
from app.auth.deps import require_patient
from app.db import get_db, get_session_factory
from app.schemas.api import ChatResponse, ConfirmRequest, ToolInvocation
from app.schemas.citations import SystemCitation
from app.schemas.common import AnswerType, RequestContext
from app.schemas.tools import ToolStatus
from app.services.appointments import execute_reschedule
from app.services.conversation import save_turn

router = APIRouter(prefix="/actions", tags=["actions"])


def _reply(result) -> tuple[str, AnswerType]:
    if result.status == ToolStatus.OK and result.data.get("cancelled"):
        return "Okay, I've cancelled that request. Your appointment has not been changed.", AnswerType.ACTION_COMPLETED
    if result.status == ToolStatus.OK:
        d = result.data
        return (
            f"Done. Your {d['department']} appointment has moved from {d['previous_start']} to {d['new_start']} "
            f"with {d['doctor']}.",
            AnswerType.ACTION_COMPLETED,
        )
    if result.status == ToolStatus.NOT_FOUND:
        return "I couldn't find that pending request for your account.", AnswerType.REFUSED
    # Expired, slot taken, or appointment changed: the patient needs to choose again.
    return f"{result.message} Nothing was changed.", AnswerType.CLARIFICATION_NEEDED


@router.post("/confirm", response_model=ChatResponse)
@limiter.limit(CHAT_LIMIT)
def confirm_action(
    request: Request,
    body: ConfirmRequest,
    ctx: RequestContext = Depends(require_patient),
    db: Session = Depends(get_db),
    session_factory: sessionmaker = Depends(get_session_factory),
) -> ChatResponse:
    started = time.perf_counter()
    result = execute_reschedule(db, ctx, body.action_id, body.decision)
    answer, answer_type = _reply(result)
    latency = round((time.perf_counter() - started) * 1000)

    if body.session_id:
        save_turn(db, body.session_id, ctx.user_id, "user", f"[{body.decision}]")
        save_turn(db, body.session_id, ctx.user_id, "assistant", answer, answer_type.value)

    write_audit(
        session_factory,
        AuditRecord(
            trace_id=ctx.trace_id,
            endpoint="/actions/confirm",
            user_id=ctx.user_id,
            role=ctx.role.value,
            patient_id=ctx.patient_id,
            question=f"{body.decision} action {body.action_id}",
            answer_type=answer_type.value,
            tools=[{"tool": result.tool, "status": result.status, "message": result.message}],
            privacy_checks=[{"check": "action_ownership", "passed": result.status != ToolStatus.NOT_FOUND}],
            latency_ms=latency,
            error=None if result.status == ToolStatus.OK else result.message,
        ),
    )
    return ChatResponse(
        trace_id=ctx.trace_id,
        session_id=body.session_id or "",
        answer=answer,
        answer_type=answer_type,
        citations=[SystemCitation(system=result.source_system)] if result.status == ToolStatus.OK else [],
        tools_invoked=[ToolInvocation(tool=result.tool, status=result.status)],
        explanation="Executed by the hospital scheduling workflow after your explicit confirmation.",
        latency_ms=latency,
    )
