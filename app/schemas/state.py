import operator
from typing import Annotated, Any, TypedDict

from app.schemas.citations import Evidence
from app.schemas.common import AnswerType, RequestContext
from app.schemas.tools import ToolResult


class GraphState(TypedDict, total=False):
    """
    State for one chat request through the LangGraph workflow.

    List fields that several nodes (or parallel Care Coordination branches) append to use
    operator.add so updates are merged instead of overwritten.
    """

    # Input
    question: str
    ctx: RequestContext
    history: list[dict[str, str]]

    # Guard
    guard_category: str
    guard_reason: str

    # Supervisor loop (plan holds app.graph.plan.Task objects)
    plan: list[Any]
    final_step: bool
    completed_tasks: Annotated[list[Any], operator.add]
    step_count: int
    stop_reason: str
    clarification_question: str

    # Collected work
    results: Annotated[list[ToolResult], operator.add]
    evidence: Annotated[list[Evidence], operator.add]
    status_summaries: Annotated[list[str], operator.add]
    pending_action_id: str | None
    conflict: bool
    care_task: Any  # app.graph.plan.Task for the parallel Care Coordination branches
    care_done: bool
    auto_cite: Annotated[list[str], operator.add]  # chunk ids always cited (e.g. readiness protocol)

    # Output
    draft_answer: str
    explanation: str
    assumptions: list[str]
    cited_labels: list[str]
    grounding_ok: bool
    answer: str
    answer_type: AnswerType
    citations: list[Any]

    # Observability
    node_timings: Annotated[list[dict[str, Any]], operator.add]
    privacy_checks: Annotated[list[dict[str, Any]], operator.add]
    token_usage: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[str], operator.add]
