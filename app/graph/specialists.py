"""Deterministic specialist nodes. No LLM here: each one validates its task and calls services.

Specialists return short status summaries for the Supervisor; raw document text never goes to the
planner, so a poisoned document cannot steer routing (R8).
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.graph.plan import Task
from app.rag.search import search_policies
from app.schemas.citations import Evidence
from app.schemas.common import RequestContext
from app.schemas.tools import ToolResult, ToolStatus


@dataclass
class SpecialistOutput:
    results: list[ToolResult]
    summary: str
    evidence: list[Evidence] = field(default_factory=list)
    conflict: bool = False
    pending_action_id: str | None = None
    clarification: str | None = None


@dataclass
class Specialist:
    name: str
    description: str
    run: Callable[[Session, RequestContext, Task, str], SpecialistOutput]


def _knowledge(db: Session, ctx: RequestContext, task: Task, question: str) -> SpecialistOutput:
    result = search_policies(db, ctx, task.query or question)
    if result.status != ToolStatus.OK:
        return SpecialistOutput([result], "knowledge: no approved document answers this (not_found)")
    evidence = [Evidence(**e) for e in result.data["evidence"]]
    docs = sorted({f"{e.doc_id} v{e.citation.version}" for e in evidence})
    summary = f"knowledge: {len(evidence)} passages from {', '.join(docs)}"
    if result.data["conflict"]:
        summary += "; UNRESOLVED CONFLICT between documents"
    for override in result.data["overrides"]:
        summary += f"; {override['applied']} overrides {', '.join(override['overridden'])}"
    return SpecialistOutput([result], summary, evidence, conflict=result.data["conflict"])


REGISTRY: dict[str, Specialist] = {
    "knowledge": Specialist(
        "knowledge",
        "Hospital policies and procedures: visiting hours, admission, discharge, how to schedule tests, "
        "pre-operative protocols, insurance agreement terms, emergency procedures. Args: query.",
        _knowledge,
    ),
}


def run_task(db: Session, ctx: RequestContext, task: Task, question: str) -> SpecialistOutput:
    specialist = REGISTRY.get(task.specialist)
    if specialist is None:
        return SpecialistOutput([], f"{task.specialist}: not available")
    return specialist.run(db, ctx, task, question)
