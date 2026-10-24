"""Step 2.8 (stretch): transparent bill estimate from base cost and insurance rules."""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.clinical import ProcedureCost
from app.schemas.common import RequestContext
from app.schemas.tools import ToolResult, ToolStatus
from app.services.common import BILLING
from app.services.insurance import check_insurance_eligibility, match_procedure

ESTIMATE_NOTE = "Estimate only. The final bill depends on the services actually provided."


def estimate_bill(db: Session, ctx: RequestContext, procedure: str) -> ToolResult:
    tool = "estimate_bill"
    costs = {c.procedure: c.base_cost for c in db.scalars(select(ProcedureCost))}
    matched = match_procedure(list(costs), procedure)
    if matched is None:
        return ToolResult.not_found(tool, BILLING, f"No standard cost is on record for '{procedure}'.")
    base = costs[matched]

    insurance = check_insurance_eligibility(db, ctx, matched)
    basis = [f"Standard cost of {matched}: INR {base:,.2f}."]
    if insurance.status == ToolStatus.OK and insurance.data["eligible"]:
        copay = Decimal(str(insurance.data["copay_pct"]))
        patient_pays = (base * copay / 100).quantize(Decimal("0.01"))
        basis.append(f"{insurance.data['plan_name']} covers it with a {copay:g}% co-pay.")
        if not insurance.data["cashless_authorized"]:
            basis.append("Coverage is subject to pre-authorisation approval.")
    else:
        patient_pays = base
        basis.append("Not covered by your insurance plan, so the full cost applies.")

    return ToolResult.ok(
        tool,
        BILLING,
        {
            "procedure": matched,
            "currency": "INR",
            "base_cost": float(base),
            "insurer_pays": float(base - patient_pays),
            "patient_pays": float(patient_pays),
            "basis": basis,
            "note": ESTIMATE_NOTE,
        },
    )
