"""Deterministic specialist nodes. No LLM here: each one validates its task and calls services.

Specialists return short status summaries for the Supervisor; raw document text never goes to the
planner, so a poisoned document cannot steer routing (R8).
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.graph.plan import Task
from app.graph.slot_choice import match_slot
from app.rag.search import search_policies
from app.schemas.citations import Evidence
from app.schemas.common import RequestContext
from app.schemas.tools import ToolResult, ToolStatus
from app.services.appointments import get_appointments, propose_reschedule
from app.services.billing import estimate_bill
from app.services.care import planned_surgery
from app.services.insurance import check_insurance_eligibility
from app.services.labs import get_lab_results
from app.services.pharmacy import drug_interaction_check

CARE_COORDINATION = "care_coordination"


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
    run: Callable[[Session, RequestContext, Task, str], SpecialistOutput] | None


def _summary(name: str, result: ToolResult, ok_text: str) -> str:
    return f"{name}: {ok_text}" if result.status == ToolStatus.OK else f"{name}: {result.status} ({result.message})"


def _procedure(db: Session, ctx: RequestContext, task: Task) -> str | None:
    """The named procedure, else the patient's planned surgery (resolved in code, not by the LLM)."""
    if task.procedure:
        return task.procedure
    surgery = planned_surgery(db, ctx.patient_id)
    return surgery.procedure if surgery else None


# --- knowledge ------------------------------------------------------------------------------

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


# --- appointments ---------------------------------------------------------------------------

def _appointments(db: Session, ctx: RequestContext, task: Task, question: str) -> SpecialistOutput:
    if task.action != "reschedule":
        result = get_appointments(db, ctx, task.department)
        count = len(result.data) if result.status == ToolStatus.OK else 0
        return SpecialistOutput([result], _summary("appointments", result, f"{count} upcoming appointment(s)"))

    result = propose_reschedule(db, ctx, appointment_id=task.appointment_id, department=task.department)
    data = result.data if isinstance(result.data, dict) else {}
    if result.status != ToolStatus.OK:
        return SpecialistOutput([result], _summary("appointments", result, ""))
    if data.get("needs_clarification"):
        return SpecialistOutput([result], "appointments: several appointments match", clarification="Which appointment would you like to reschedule?")

    options = data.get("options", [])
    if not options:
        return SpecialistOutput([result], "appointments: no free slots available for rescheduling")
    slot_id = match_slot(task.slot_preference, options) if task.slot_preference else None
    if slot_id is None:
        ask = "Which of these available times would you like?"
        if task.slot_preference:
            ask = f"I couldn't match '{task.slot_preference}' to exactly one available time. " + ask
        return SpecialistOutput([result], f"appointments: {len(options)} free slots offered", clarification=ask)

    proposal = propose_reschedule(db, ctx, appointment_id=data["appointment"]["appointment_id"], slot_id=slot_id)
    if proposal.status != ToolStatus.OK:
        return SpecialistOutput([proposal], _summary("appointments", proposal, ""))
    return SpecialistOutput(
        [proposal],
        "appointments: reschedule proposed, awaiting patient confirmation",
        pending_action_id=proposal.data["action_id"],
    )


# --- insurance, labs, medication, billing ---------------------------------------------------

def _insurance(db: Session, ctx: RequestContext, task: Task, question: str) -> SpecialistOutput:
    procedure = _procedure(db, ctx, task)
    if procedure is None:
        return SpecialistOutput([], "insurance: procedure unknown", clarification="Which procedure would you like me to check coverage for?")
    result = check_insurance_eligibility(db, ctx, procedure)
    ok = f"{procedure} eligible={result.data['eligible']}" if result.status == ToolStatus.OK else ""
    return SpecialistOutput([result], _summary("insurance", result, ok))


def _labs(db: Session, ctx: RequestContext, task: Task, question: str) -> SpecialistOutput:
    if not task.test:
        return SpecialistOutput([], "labs: test unknown", clarification="Which lab test result would you like to see?")
    result = get_lab_results(db, ctx, task.test)
    if result.status == ToolStatus.OK and result.data.get("needs_clarification"):
        options = ", ".join(result.data["options"])
        return SpecialistOutput([result], "labs: ambiguous test name", clarification=f"Did you mean {options}?")
    test_name = result.data.get("test_name") if result.status == ToolStatus.OK else ""
    return SpecialistOutput([result], _summary("labs", result, f"{test_name} result found"))


def _medication(db: Session, ctx: RequestContext, task: Task, question: str) -> SpecialistOutput:
    result = drug_interaction_check(db, ctx, task.medications)
    ok = f"{len(result.data['interactions'])} interaction(s) found" if result.status == ToolStatus.OK else ""
    return SpecialistOutput([result], _summary("medication", result, ok))


def _billing(db: Session, ctx: RequestContext, task: Task, question: str) -> SpecialistOutput:
    procedure = _procedure(db, ctx, task)
    if procedure is None:
        return SpecialistOutput([], "billing: procedure unknown", clarification="Which procedure would you like a cost estimate for?")
    result = estimate_bill(db, ctx, procedure)
    return SpecialistOutput([result], _summary("billing", result, f"estimate for {procedure} computed"))


REGISTRY: dict[str, Specialist] = {
    "knowledge": Specialist(
        "knowledge",
        "Hospital policies and procedures: visiting hours, admission, discharge, how to schedule a test, "
        "pre-operative protocols, insurance agreement terms, emergency procedures. Args: query.",
        _knowledge,
    ),
    "appointments": Specialist(
        "appointments",
        "The patient's own appointments. action=view lists upcoming ones (optional department). "
        "action=reschedule starts a reschedule: give department or appointment_id; when the patient picks "
        "a time from offered options, pass their words in slot_preference.",
        _appointments,
    ),
    "insurance": Specialist(
        "insurance",
        "Coverage and pre-authorisation for a procedure under the patient's own plan. Args: procedure "
        "(omit to use the patient's planned surgery).",
        _insurance,
    ),
    "labs": Specialist(
        "labs",
        "The patient's own lab results (values, units, reference ranges). Args: test (the name they used).",
        _labs,
    ),
    "medication": Specialist(
        "medication",
        "Interaction check of medicines the patient asks about against their current medicines. Args: medications.",
        _medication,
    ),
    "billing": Specialist(
        "billing",
        "Cost estimate for a procedure after insurance. Args: procedure (omit to use the planned surgery).",
        _billing,
    ),
    CARE_COORDINATION: Specialist(
        CARE_COORDINATION,
        "Surgery readiness: checks pending pre-operative tests, insurance pre-authorisation and surgeon "
        "availability in parallel, then applies the protocol checklist. Use for 'can I have my surgery', "
        "'what pre-op tests are pending', 'am I ready for surgery'. Args: procedure (optional).",
        None,  # runs as parallel graph branches, see app.graph.care
    ),
}


def run_task(db: Session, ctx: RequestContext, task: Task, question: str) -> SpecialistOutput:
    specialist = REGISTRY.get(task.specialist)
    if specialist is None or specialist.run is None:
        return SpecialistOutput([], f"{task.specialist}: not available")
    return specialist.run(db, ctx, task, question)
