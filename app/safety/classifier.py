"""R9: answer_type is decided by code from what actually happened, never by the LLM."""

from app.schemas.common import AnswerType
from app.schemas.state import GraphState
from app.schemas.tools import ToolStatus

KNOWLEDGE_TOOL = "search_policies"


def has_system_data(state: GraphState) -> bool:
    return any(
        r.tool != KNOWLEDGE_TOOL
        and r.status == ToolStatus.OK
        and not (isinstance(r.data, dict) and (r.data.get("needs_clarification") or r.data.get("needs_choice")))
        for r in state.get("results", [])
    )


def classify(state: GraphState) -> AnswerType:
    """Priority: refused → clarification_needed → conflict_flagged → action_completed →
    calculated → retrieved_fact → not_found."""
    stop = state.get("stop_reason")
    if stop == "refused":
        return AnswerType.REFUSED
    if stop == "clarification" or state.get("pending_action_id"):
        # A proposed action awaiting the patient's confirmation also needs their input.
        return AnswerType.CLARIFICATION_NEEDED
    if state.get("conflict"):
        return AnswerType.CONFLICT_FLAGGED
    if any(r.tool == "execute_reschedule" and r.status == ToolStatus.OK for r in state.get("results", [])):
        return AnswerType.ACTION_COMPLETED
    if not state.get("grounding_ok", False):
        return AnswerType.NOT_FOUND
    if has_system_data(state):
        return AnswerType.CALCULATED
    if state.get("cited_labels"):
        return AnswerType.RETRIEVED_FACT
    return AnswerType.NOT_FOUND
