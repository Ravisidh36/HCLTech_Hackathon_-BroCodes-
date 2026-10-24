"""Care Coordination as parallel graph branches (§4 multi-step scenario, R6).

fan-out → [pre-op tests ‖ insurance pre-authorisation ‖ surgeon schedule] → readiness join.
The three reads are independent and run concurrently; each branch opens its own DB session because
parallel LangGraph nodes run on separate threads. The join applies the same deterministic
evaluate_readiness rules as the service layer and attaches the protocol sections as citations.
"""

import logging
from typing import Any

from langchain_core.runnables import RunnableConfig
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.graph.nodes import timed
from app.models.knowledge import Chunk, Document
from app.rag.retrieve import Candidate, _to_evidence
from app.schemas.state import GraphState
from app.schemas.tools import ToolResult, ToolStatus
from app.services.care import evaluate_readiness, get_pending_preop_tests, get_surgeon_schedule, planned_surgery
from app.services.common import EMR
from app.services.insurance import check_insurance_eligibility

log = logging.getLogger(__name__)

PREOP, INSURANCE, SURGEON = "get_pending_preop_tests", "check_insurance_eligibility", "get_surgeon_schedule"
BRANCH_NODES = ["care_preop", "care_insurance", "care_surgeon"]


def _procedure(state: GraphState) -> str | None:
    task = state.get("care_task")
    return task.procedure if task is not None else None


def _branch(name: str, tool: str, call) -> Any:
    @timed(name)
    def node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
        ctx = state["ctx"]
        with config["configurable"]["session_factory"]() as db:  # own session per thread
            try:
                result = call(db, ctx, state)
            except Exception as exc:
                log.exception("Care branch %s failed", name)
                result = ToolResult.error(tool, EMR, f"Could not be checked: {type(exc).__name__}")
        return {"results": [result], "status_summaries": [f"care/{tool}: {result.status}"]}

    return node


def _preop(db: Session, ctx, state: GraphState) -> ToolResult:
    return get_pending_preop_tests(db, ctx, _procedure(state))


def _insurance(db: Session, ctx, state: GraphState) -> ToolResult:
    procedure = _procedure(state)
    if procedure is None:
        surgery = planned_surgery(db, ctx.patient_id)
        if surgery is None:
            return ToolResult.not_found(INSURANCE, EMR, "No planned surgery was found to check coverage for.")
        procedure = surgery.procedure
    return check_insurance_eligibility(db, ctx, procedure)


def _surgeon(db: Session, ctx, state: GraphState) -> ToolResult:
    return get_surgeon_schedule(db, ctx, _procedure(state))


care_preop = _branch("care_preop", PREOP, _preop)
care_insurance = _branch("care_insurance", INSURANCE, _insurance)
care_surgeon = _branch("care_surgeon", SURGEON, _surgeon)


@timed("care_fanout")
def care_fanout(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    return {}


def _latest(state: GraphState, tool: str) -> ToolResult | None:
    return next((r for r in reversed(state.get("results", [])) if r.tool == tool), None)


def _protocol_evidence(db: Session, protocol: dict | None, sections: list[str]):
    """The protocol sections the readiness rules come from, as citable evidence (R2)."""
    if not protocol:
        return []
    rows = db.execute(
        select(Chunk, Document)
        .join(Document)
        .where(Document.doc_id == protocol["doc_id"], Document.version == protocol["version"])
        .order_by(Chunk.id)
    ).tuples().all()
    return [
        _to_evidence(Candidate(chunk, doc, score=0.0))
        for chunk, doc in rows
        if any(chunk.section_path.startswith(section) for section in sections)
    ]


@timed("care_readiness")
def care_readiness(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    db, ctx, task = config["configurable"]["db"], state["ctx"], state["care_task"]
    preop, insurance, surgeon = _latest(state, PREOP), _latest(state, INSURANCE), _latest(state, SURGEON)

    surgery = planned_surgery(db, ctx.patient_id, task.procedure)
    if surgery is None:
        readiness = ToolResult.not_found("surgery_readiness_check", EMR, "No planned surgery was found for you.")
        evidence = []
    else:
        readiness = evaluate_readiness(
            surgery.procedure, surgery.planned_date, surgery.consent_signed, preop, insurance, surgeon
        )
        protocol = preop.data.get("protocol") if preop and preop.status == ToolStatus.OK else None
        evidence = _protocol_evidence(db, protocol, ["2. Required Pre-operative Tests", "3. Readiness Checklist > 3.1"])

    update: dict[str, Any] = {
        "results": [readiness],
        "evidence": evidence,
        "auto_cite": [e.chunk_id for e in evidence],
        "completed_tasks": [task],
        "care_done": True,
        "status_summaries": [
            f"care_coordination: ready={readiness.data['ready']}, blocking={readiness.data['blocking_items']}"
            if readiness.status == ToolStatus.OK
            else f"care_coordination: {readiness.status} ({readiness.message})"
        ],
    }
    if not state.get("stop_reason") and state.get("final_step", True):
        update["stop_reason"] = "done"
    return update
