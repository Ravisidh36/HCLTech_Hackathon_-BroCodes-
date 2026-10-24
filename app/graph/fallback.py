"""Deterministic answers when the composer LLM is unavailable (outage, quota, timeout).

Every sentence is built only from tool data or quoted evidence, so the answer stays grounded; it is
just less fluent. Tools without a template fall back to not_found rather than guessing.
"""

from collections.abc import Callable

from app.schemas.state import GraphState
from app.schemas.tools import ToolResult, ToolStatus


def _readiness(d: dict) -> str:
    if d["ready"]:
        return f"You are cleared for {d['procedure']} on {d['planned_date']}: every protocol condition is met."
    lines = [f"Not yet. These conditions for your {d['procedure']} on {d['planned_date']} are not met:"]
    lines += [f"- {c['item']}: {c['detail']}" for c in d["checklist"] if not c["passed"]]
    return "\n".join(lines)


def _preop(d: dict) -> str:
    if not d["pending"]:
        return f"All pre-operative tests for {d['procedure']} are complete and valid."
    return f"Pending pre-operative tests for {d['procedure']}: {', '.join(d['pending'])}."


def _insurance(d: dict) -> str:
    return " ".join(d["reasons"])


def _labs(d: dict) -> str:
    latest = d["results"][0]
    unit = f" {d['unit']}" if d.get("unit") else ""
    text = f"Your latest {d['test_name']} ({latest['collected_at']}): {latest['value']}{unit}."
    if latest.get("flag") and latest["flag"] != "within_range":
        text += f" This is {latest['flag'].replace('_', ' ')}; please discuss it with your doctor."
    return text


def _appointments(d: list) -> str:
    return "Your upcoming appointments:\n" + "\n".join(
        f"- {a['start_time']}: {a['appointment_type']} with {a['doctor']} ({a['department']})" for a in d
    )


def _interactions(d: dict) -> str:
    if not d["interactions"]:
        return f"No interaction is listed in the approved reference for: {', '.join(d['checked'])}. {d['note']}"
    lines = [f"- {i['drug_a']} + {i['drug_b']} ({i['severity']}): {i['description']}" for i in d["interactions"]]
    return "Interactions found in the approved reference:\n" + "\n".join(lines) + f"\n{d['note']}"


def _bill(d: dict) -> str:
    return " ".join(d["basis"]) + f" Estimated amount you pay: INR {d['patient_pays']:,.2f}. {d['note']}"


def _proposal(d: dict) -> str:
    if "summary" in d:
        return f"Proposed change: {d['summary']} Please confirm it with the Confirm button. Nothing has changed yet."
    if d.get("options"):
        return "Available times:\n" + "\n".join(f"- {o['start_time']} with {o['doctor']}" for o in d["options"])
    return ""


RENDERERS: dict[str, Callable] = {
    "surgery_readiness_check": _readiness,
    "get_pending_preop_tests": _preop,
    "check_insurance_eligibility": _insurance,
    "get_lab_results": _labs,
    "get_appointments": _appointments,
    "drug_interaction_check": _interactions,
    "estimate_bill": _bill,
    "propose_reschedule": _proposal,
}

# When readiness was computed, its parts are already summarised inside it.
_COVERED_BY_READINESS = {"get_pending_preop_tests", "check_insurance_eligibility", "get_surgeon_schedule"}


def render_fallback(state: GraphState) -> str | None:
    results: list[ToolResult] = [r for r in state.get("results", []) if r.status == ToolStatus.OK]
    has_readiness = any(r.tool == "surgery_readiness_check" for r in results)
    parts = []
    for r in results:
        if has_readiness and r.tool in _COVERED_BY_READINESS:
            continue
        render = RENDERERS.get(r.tool)
        if render and isinstance(r.data, (dict, list)) and not (isinstance(r.data, dict) and r.data.get("needs_clarification")):
            parts.append(render(r.data))
    evidence = state.get("evidence", [])
    if evidence and not parts:
        if state.get("conflict"):
            # Never pick a side: show each conflicting document's statement.
            seen, quotes = set(), []
            for e in evidence:
                if e.doc_id not in seen:
                    seen.add(e.doc_id)
                    quotes.append(f"- {e.citation.document} (section {e.citation.section}): {e.content}")
            parts.append(
                "These hospital documents conflict, so please confirm with hospital staff:\n" + "\n".join(quotes)
            )
        else:
            top = evidence[0]
            parts.append(f"From {top.citation.document} (section {top.citation.section}): {top.content}")
    if state.get("clarification_question"):
        parts.append(state["clarification_question"])
    text = "\n\n".join(p for p in parts if p)
    return text or None
