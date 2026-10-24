import operator
from typing import Annotated, Any, TypedDict

from app.schemas.citations import Evidence
from app.schemas.common import AnswerType, RequestContext
from app.schemas.tools import ToolResult


class PlannedTask(TypedDict):
    specialist: str
    args: dict[str, Any]


class GraphState(TypedDict, total=False):
    """
    State for one chat request through the LangGraph workflow.

    List fields that parallel branches (Care Coordination) write to use
    operator.add so concurrent updates are merged instead of overwritten.
    """

    # Input
    question: str
    ctx: RequestContext
    history: list[dict[str, str]]

    # Guard
    guard_verdict: str  # "allowed" | "refused"
    guard_reason: str

    # Supervisor loop
    plan: list[PlannedTask]
    completed_tasks: Annotated[list[PlannedTask], operator.add]
    step_count: int
    stop_reason: str
    clarification_question: str

    # Collected work
    results: Annotated[list[ToolResult], operator.add]
    evidence: Annotated[list[Evidence], operator.add]
    status_summaries: Annotated[list[str], operator.add]
    pending_action_id: str | None
    conflict: bool

    # Output
    draft_answer: str
    explanation: str
    assumptions: list[str]
    answer_type: AnswerType

    # Observability
    node_timings: Annotated[list[dict[str, Any]], operator.add]
    privacy_checks: Annotated[list[dict[str, Any]], operator.add]
    token_usage: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[str], operator.add]
