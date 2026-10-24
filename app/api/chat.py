import logging
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker

from app.api.ratelimit import CHAT_LIMIT, limiter
from app.audit.writer import AuditRecord, write_audit
from app.auth.deps import require_patient
from app.db import get_db, get_session_factory
from app.graph.build import get_graph
from app.graph.nodes import MAX_STEPS
from app.llm.provider import model_label
from app.models.platform import PendingAction
from app.schemas.api import ChatRequest, ChatResponse, PendingActionOut, ToolInvocation
from app.schemas.common import RequestContext
from app.services.conversation import recent_turns, save_turn

log = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


def _sources(state: dict) -> list[dict]:
    return [
        {"chunk_id": e.chunk_id, "doc_id": e.doc_id, "version": e.citation.version, "score": e.score}
        for e in state.get("evidence", [])
    ]


@router.post("/chat", response_model=ChatResponse)
@limiter.limit(CHAT_LIMIT)
def chat(
    request: Request,
    body: ChatRequest,
    ctx: RequestContext = Depends(require_patient),
    db: Session = Depends(get_db),
    session_factory: sessionmaker = Depends(get_session_factory),
) -> ChatResponse:
    started = time.perf_counter()
    session_id = body.session_id or uuid.uuid4().hex
    state: dict = {}
    error: str | None = None
    try:
        state = get_graph().invoke(
            {"question": body.message, "ctx": ctx, "history": recent_turns(db, session_id, ctx.user_id)},
            config={"configurable": {"db": db}, "recursion_limit": 4 * MAX_STEPS + 10},
        )
        pending = None
        if state.get("pending_action_id"):
            action = db.get(PendingAction, state["pending_action_id"])
            pending = PendingActionOut(action_id=action.id, summary=action.summary, expires_at=action.expires_at)

        response = ChatResponse(
            trace_id=ctx.trace_id,
            session_id=session_id,
            answer=state["answer"],
            answer_type=state["answer_type"],
            citations=state.get("citations", []),
            tools_invoked=[ToolInvocation(tool=r.tool, status=r.status) for r in state.get("results", [])],
            explanation=state.get("explanation", ""),
            assumptions=state.get("assumptions", []),
            pending_action=pending,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        save_turn(db, session_id, ctx.user_id, "user", body.message)
        save_turn(db, session_id, ctx.user_id, "assistant", response.answer, response.answer_type.value)
        return response
    except Exception as exc:
        db.rollback()
        error = f"{type(exc).__name__}: {exc}"[:500]
        log.exception("Chat request %s failed", ctx.trace_id)
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            {"message": "Something went wrong. Please try again.", "trace_id": ctx.trace_id},
        ) from None
    finally:
        write_audit(
            session_factory,
            AuditRecord(
                trace_id=ctx.trace_id,
                endpoint="/chat",
                user_id=ctx.user_id,
                role=ctx.role.value,
                patient_id=ctx.patient_id,
                question=body.message,
                answer_type=str(state["answer_type"]) if state.get("answer_type") else None,
                sources=_sources(state),
                tools=[{"tool": r.tool, "status": r.status} for r in state.get("results", [])],
                privacy_checks=state.get("privacy_checks", []),
                node_timings=state.get("node_timings", []),
                model=model_label(),
                token_usage=state.get("token_usage", []),
                latency_ms=round((time.perf_counter() - started) * 1000),
                error=error or ("; ".join(state.get("errors", [])) or None),
            ),
        )
