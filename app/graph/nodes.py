"""LangGraph nodes: guard → supervisor ⇄ specialists → composer → finalize.

Only supervisor and composer call the LLM. Stop rules, allowlists, grounding checks, PII masking and
answer classification are all code.
"""

import json
import logging
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from sqlalchemy.orm import Session

from app.graph import prompts
from app.graph.plan import ComposerOutput, SupervisorPlan, Task
from app.graph.specialists import REGISTRY, run_task
from app.llm.provider import get_structured_model
from app.safety.classifier import KNOWLEDGE_TOOL, classify
from app.safety.grounding import unsupported_numbers
from app.safety.input_guard import check_input
from app.safety.pii import mask_pii
from app.schemas.citations import DocCitation, SystemCitation
from app.schemas.common import NOT_FOUND_MESSAGE, AnswerType
from app.schemas.state import GraphState
from app.schemas.tools import ToolStatus

log = logging.getLogger(__name__)

MAX_STEPS = 5
MAX_TASKS_PER_STEP = 3

Node = Callable[[GraphState, RunnableConfig], dict[str, Any]]


def _db(config: RunnableConfig) -> Session:
    return config["configurable"]["db"]


def timed(name: str) -> Callable[[Node], Node]:
    """Record per-node latency for the audit log (R10)."""

    def decorate(fn: Node) -> Node:
        @wraps(fn)
        def wrapper(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
            started = time.perf_counter()
            update = fn(state, config)
            update["node_timings"] = [{"node": name, "ms": round((time.perf_counter() - started) * 1000)}]
            return update

        return wrapper

    return decorate


def _usage(node: str, raw: Any) -> list[dict[str, Any]]:
    meta = getattr(raw, "usage_metadata", None) or {}
    return [{"node": node, "input_tokens": meta.get("input_tokens"), "output_tokens": meta.get("output_tokens")}]


# --- guard ----------------------------------------------------------------------------------

@timed("guard")
def guard_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    verdict = check_input(_db(config), state["ctx"], state["question"])
    update: dict[str, Any] = {"privacy_checks": verdict.checks, "guard_category": verdict.category}
    if not verdict.allowed:
        update |= {"stop_reason": "refused", "guard_reason": verdict.reason}
    return update


# --- supervisor -----------------------------------------------------------------------------

def keyword_route(question: str) -> SupervisorPlan:
    """Deterministic fallback when the planner LLM is unavailable or returns nothing usable."""
    return SupervisorPlan(tasks=[Task(specialist="knowledge", query=question)])


def _history_text(history: list[dict[str, str]]) -> str:
    return "\n".join(f"{t['speaker']}: {t['content']}" for t in history) or "(none)"


def _plan(state: GraphState) -> tuple[SupervisorPlan, list[dict[str, Any]], str | None]:
    specialists = "\n".join(f"- {s.name}: {s.description}" for s in REGISTRY.values())
    messages = [
        SystemMessage(prompts.SUPERVISOR_SYSTEM.format(specialists=specialists)),
        HumanMessage(
            prompts.SUPERVISOR_USER.format(
                history=_history_text(state.get("history", [])),
                question=state["question"],
                completed="\n".join(state.get("status_summaries", [])) or "(nothing yet)",
            )
        ),
    ]
    try:
        out = get_structured_model(SupervisorPlan).invoke(messages)
        if out["parsed"] is None:
            raise ValueError(f"unparseable plan: {out.get('parsing_error')}")
        return out["parsed"], _usage("supervisor", out["raw"]), None
    except Exception as exc:  # LLM down, quota, malformed output: never block the patient
        log.warning("Supervisor fell back to keyword routing: %s", exc)
        return keyword_route(state["question"]), [], f"supervisor_fallback: {type(exc).__name__}"


@timed("supervisor")
def supervisor_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    step = state.get("step_count", 0)
    if step >= MAX_STEPS:
        return {"stop_reason": "step_limit"}

    plan, usage, error = _plan(state)
    update: dict[str, Any] = {"token_usage": usage, "errors": [error] if error else []}

    tasks = [t for t in plan.tasks if t.specialist in REGISTRY][:MAX_TASKS_PER_STEP]
    if plan.needs_clarification and plan.question_for_user and not tasks:
        return update | {"stop_reason": "clarification", "clarification_question": plan.question_for_user}

    done = {t.key() for t in state.get("completed_tasks", [])}
    new = [t for t in tasks if t.key() not in done]
    if not new:
        # Nothing new to do: either the work is complete or the planner is repeating itself.
        return update | {"stop_reason": "done" if done else "no_task"}
    return update | {"plan": new, "final_step": plan.final_step, "step_count": step + 1}


# --- specialists ----------------------------------------------------------------------------

@timed("specialists")
def specialists_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    db, ctx = _db(config), state["ctx"]
    outputs = [run_task(db, ctx, task, state["question"]) for task in state["plan"]]

    update: dict[str, Any] = {
        "completed_tasks": list(state["plan"]),
        "results": [r for o in outputs for r in o.results],
        "evidence": [e for o in outputs for e in o.evidence],
        "status_summaries": [o.summary for o in outputs],
    }
    if any(o.conflict for o in outputs):
        update["conflict"] = True
    pending = next((o.pending_action_id for o in outputs if o.pending_action_id), None)
    clarification = next((o.clarification for o in outputs if o.clarification), None)

    if pending:
        update |= {"pending_action_id": pending, "stop_reason": "pending_action"}
    elif clarification:
        update |= {"stop_reason": "clarification", "clarification_question": clarification}
    elif update.get("conflict"):
        update["stop_reason"] = "conflict"
    elif state.get("final_step", True):
        update["stop_reason"] = "done"
    return update


# --- composer -------------------------------------------------------------------------------

def _evidence_block(state: GraphState) -> str:
    blocks = []
    for i, e in enumerate(state.get("evidence", []), start=1):
        content = e.content.replace("</evidence", "</ evidence")  # cannot close the tag early
        c = e.citation
        blocks.append(
            f'<evidence id="E{i}" document="{c.document}" section="{c.section}" version="{c.version}" '
            f'effective="{c.effective_date}" injection_flagged="{str(e.injection_flag).lower()}">\n'
            f"{content}\n</evidence>"
        )
    return "\n".join(blocks) or "(none)"


def _policy_notes(state: GraphState) -> str:
    notes = []
    for r in state.get("results", []):
        if r.tool == KNOWLEDGE_TOOL and r.status == ToolStatus.OK:
            notes += [f"- Policy precedence: {o['reason']} It overrides {', '.join(o['overridden'])}." for o in r.data["overrides"]]
            notes += [f"- CONFLICT: {', '.join(c['doc_ids'])} disagree and have equal authority and date." for c in r.data["conflicts"]]
    return ("Policy notes:\n" + "\n".join(notes) + "\n\n") if notes else ""


def _system_data(state: GraphState) -> list[dict[str, Any]]:
    return [
        {"tool": r.tool, "source_system": r.source_system, "status": r.status, "message": r.message, "data": r.data}
        for r in state.get("results", [])
        if r.tool != KNOWLEDGE_TOOL
    ]


def _not_found(state: GraphState, explanation: str) -> dict[str, Any]:
    messages = [r.message for r in state.get("results", []) if r.status == ToolStatus.NOT_FOUND and r.message]
    detail = " ".join(m for m in messages if m != NOT_FOUND_MESSAGE)
    return {"draft_answer": NOT_FOUND_MESSAGE, "explanation": detail or explanation, "grounding_ok": False}


@timed("composer")
def composer_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    stop = state.get("stop_reason")
    if stop == "refused":
        return {
            "draft_answer": state["guard_reason"],
            "explanation": "Refused by the input guard before any patient data or document was accessed.",
            "grounding_ok": True,
        }
    if stop == "clarification" and not state.get("results"):
        return {"draft_answer": state["clarification_question"], "explanation": "More detail is needed.", "grounding_ok": True}

    evidence = state.get("evidence", [])
    system_data = _system_data(state)
    if not evidence and not any(d["status"] == ToolStatus.OK for d in system_data):
        return _not_found(state, "No authorised hospital source contains this information.")

    tool_json = json.dumps(system_data, default=str, indent=1) if system_data else "(none)"
    user = prompts.COMPOSER_USER.format(
        question=state["question"],
        policy_notes=_policy_notes(state),
        evidence=_evidence_block(state),
        tool_data=tool_json,
    )
    if stop == "clarification":
        user += f"\n\nAsk the patient this clarifying question: {state['clarification_question']}"
    support = "\n".join([state["question"], _evidence_block(state), tool_json])

    usage: list[dict[str, Any]] = []
    feedback = ""
    for _ in range(2):
        messages = [SystemMessage(prompts.COMPOSER_SYSTEM), HumanMessage(user + feedback)]
        try:
            out = get_structured_model(ComposerOutput).invoke(messages)
        except Exception as exc:
            log.warning("Composer failed: %s", exc)
            return _not_found(state, "The answer could not be generated safely.") | {"errors": [f"composer: {type(exc).__name__}"]}
        usage += _usage("composer", out["raw"])
        parsed: ComposerOutput | None = out["parsed"]
        if parsed is None:
            feedback = "\n\nReturn the answer in the required structured format."
            continue
        unsupported = unsupported_numbers(parsed.answer, support)
        if not unsupported:
            labels = {f"E{i}" for i in range(1, len(evidence) + 1)}
            cited = [label for label in parsed.cited_evidence if label in labels]
            if evidence and not cited and not system_data:
                cited = sorted(labels)  # knowledge-only answer: cite the governing passages supplied
            return {
                "draft_answer": parsed.answer,
                "explanation": parsed.explanation,
                "assumptions": parsed.assumptions,
                "cited_labels": cited,
                "grounding_ok": True,
                "token_usage": usage,
            }
        feedback = (
            f"\n\nYour previous answer contained values not present in the sources: {unsupported}. "
            "Rewrite it using only values that appear in the sources."
        )
    return _not_found(state, "The drafted answer could not be verified against the sources.") | {
        "token_usage": usage,
        "errors": ["grounding_check_failed"],
    }


# --- finalize -------------------------------------------------------------------------------

def _citations(state: GraphState, answer_type: AnswerType) -> list[DocCitation | SystemCitation]:
    if answer_type in (AnswerType.REFUSED, AnswerType.NOT_FOUND):
        return []
    evidence = state.get("evidence", [])
    by_label = {f"E{i}": e for i, e in enumerate(evidence, start=1)}
    citations: list[DocCitation | SystemCitation] = []
    seen: set[tuple] = set()
    for label in state.get("cited_labels", []):
        c = by_label[label].citation
        key = (c.doc_id, c.version, c.section)
        if key not in seen:
            seen.add(key)
            citations.append(c)
    for system in dict.fromkeys(
        r.source_system for r in state.get("results", []) if r.tool != KNOWLEDGE_TOOL and r.status == ToolStatus.OK
    ):
        citations.append(SystemCitation(system=system))
    return citations


@timed("finalize")
def finalize_node(state: GraphState, config: RunnableConfig) -> dict[str, Any]:
    answer_type = classify(state)
    answer = NOT_FOUND_MESSAGE if answer_type == AnswerType.NOT_FOUND else mask_pii(state.get("draft_answer", ""))
    return {"answer": answer, "answer_type": answer_type, "citations": _citations(state, answer_type)}


# --- routing --------------------------------------------------------------------------------

def route_after_guard(state: GraphState) -> str:
    return "composer" if state.get("stop_reason") else "supervisor"


def route_after_supervisor(state: GraphState) -> str:
    return "composer" if state.get("stop_reason") else "specialists"


def route_after_specialists(state: GraphState) -> str:
    return "composer" if state.get("stop_reason") else "supervisor"
