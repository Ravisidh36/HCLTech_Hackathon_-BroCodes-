"""Step 2.4: lab results. Reports values, units and reference-range flags only; never a diagnosis."""

import re
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.clinical import LabResult, LabTest, Order
from app.schemas.common import RequestContext
from app.schemas.tools import ToolResult
from app.services.common import LAB, local_iso, normalize, require_patient

RANGE_NOTE = (
    "Reference-range flags are informational. Discuss any value outside the range with your doctor; "
    "this is not a diagnosis."
)


def resolve_tests(catalogue: list[LabTest], query: str) -> list[LabTest]:
    """Match a free-text test name to catalogue entries using the longest matching alias.

    Returns several tests when the best alias is shared (e.g. "sugar"), so the caller can ask
    the patient to clarify instead of guessing.
    """
    q = normalize(query)
    best: list[tuple[int, LabTest]] = []
    for test in catalogue:
        names = {test.code.lower(), normalize(test.name), *(normalize(a) for a in test.aliases)}
        lengths = [len(n) for n in names if n == q or re.search(rf"\b{re.escape(n)}\b", q)]
        if lengths:
            best.append((max(lengths), test))
    if not best:
        return []
    top = max(length for length, _ in best)
    return [test for length, test in best if length == top]


def _flag(value: Decimal | None, low: Decimal | None, high: Decimal | None) -> str | None:
    if value is None or low is None or high is None:
        return None
    if value < low:
        return "below_range"
    if value > high:
        return "above_range"
    return "within_range"


def get_lab_results(db: Session, ctx: RequestContext, test: str, history: bool = False) -> ToolResult:
    tool = "get_lab_results"
    patient_id = require_patient(ctx)

    matches = resolve_tests(list(db.scalars(select(LabTest))), test)
    if not matches:
        return ToolResult.not_found(tool, LAB, f"No lab test matching '{test}' exists in the laboratory catalogue.")
    if len(matches) > 1:
        return ToolResult.ok(
            tool,
            LAB,
            {"needs_clarification": True, "options": [t.name for t in matches]},
            f"'{test}' could refer to several tests. Ask which one.",
        )
    lab_test = matches[0]

    results = db.scalars(
        select(LabResult)
        .where(LabResult.patient_id == patient_id, LabResult.test_code == lab_test.code)
        .order_by(LabResult.collected_at.desc())
        .limit(5 if history else 1)
    ).all()
    pending_order = (
        db.scalars(
            select(Order.id).where(
                Order.patient_id == patient_id, Order.test_code == lab_test.code, Order.status == "pending"
            )
        ).first()
        is not None
    )
    if not results:
        message = f"No {lab_test.name} result is on record."
        if pending_order:
            message += " A test order is pending."
        return ToolResult.not_found(tool, LAB, message)

    return ToolResult.ok(
        tool,
        LAB,
        {
            "test_code": lab_test.code,
            "test_name": lab_test.name,
            "unit": lab_test.unit,
            "reference_range": (
                {"low": float(lab_test.ref_low), "high": float(lab_test.ref_high)}
                if lab_test.ref_low is not None and lab_test.ref_high is not None
                else None
            ),
            "results": [
                {
                    "value": float(r.value) if r.value is not None else r.value_text,
                    "collected_at": local_iso(r.collected_at),
                    "flag": _flag(r.value, lab_test.ref_low, lab_test.ref_high),
                }
                for r in results
            ],
            "pending_order": pending_order,
            "note": RANGE_NOTE,
        },
    )
