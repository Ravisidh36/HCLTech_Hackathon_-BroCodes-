"""Step 2.3: deterministic insurance eligibility (R5). The LLM never decides coverage."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.clinical import CoverageRule, InsurancePlan, Patient, PreAuthorization
from app.schemas.common import RequestContext
from app.schemas.tools import ToolResult
from app.services.common import INSURANCE, normalize, require_patient, today


def match_procedure(names: list[str], requested: str) -> str | None:
    """Exact match first, then a single unambiguous containment match."""
    query = normalize(requested)
    for name in names:
        if normalize(name) == query:
            return name
    contained = [n for n in names if normalize(n) in query or query in normalize(n)]
    return contained[0] if len(contained) == 1 else None


def check_insurance_eligibility(db: Session, ctx: RequestContext, procedure: str) -> ToolResult:
    tool = "check_insurance_eligibility"
    patient = db.get(Patient, require_patient(ctx))
    if patient is None or patient.insurance_plan_id is None:
        return ToolResult.not_found(tool, INSURANCE, "No insurance plan is on record for you.")
    plan = db.get(InsurancePlan, patient.insurance_plan_id)

    rules = {r.procedure: r for r in db.scalars(select(CoverageRule).where(CoverageRule.plan_id == plan.id))}
    matched = match_procedure(list(rules), procedure)
    if matched is None:
        return ToolResult.not_found(tool, INSURANCE, f"'{procedure}' is not listed in the {plan.name} coverage schedule.")
    rule = rules[matched]

    days_enrolled = (today() - patient.plan_start_date).days if patient.plan_start_date else 0
    waiting_met = days_enrolled >= rule.waiting_period_days
    eligible = rule.covered and waiting_met

    preauth_status = "not_required"
    preauth_valid_until = None
    if rule.preauth_required:
        preauth = db.scalars(
            select(PreAuthorization)
            .where(
                PreAuthorization.patient_id == patient.id,
                func.lower(PreAuthorization.procedure) == normalize(matched),
            )
            .order_by(PreAuthorization.id.desc())
        ).first()
        if preauth is None:
            preauth_status = "not_requested"
        elif preauth.valid_until is not None and preauth.valid_until < today():
            preauth_status = "expired"
        else:
            preauth_status = preauth.status
            preauth_valid_until = preauth.valid_until.isoformat() if preauth.valid_until else None

    cashless_authorized = eligible and preauth_status in ("not_required", "approved")

    reasons = []
    reasons.append(f"{matched} is {'covered' if rule.covered else 'not covered'} under {plan.name}.")
    if rule.covered:
        if rule.waiting_period_days:
            reasons.append(
                f"Waiting period of {rule.waiting_period_days} days is {'met' if waiting_met else 'not yet met'}."
            )
        if rule.preauth_required:
            reasons.append(f"Pre-authorisation is required; current status: {preauth_status.replace('_', ' ')}.")
        reasons.append(f"Co-pay: {rule.copay_pct:g}%.")
    if rule.notes:
        reasons.append(rule.notes)

    return ToolResult.ok(
        tool,
        INSURANCE,
        {
            "procedure": matched,
            "plan_name": plan.name,
            "covered": rule.covered,
            "waiting_period_days": rule.waiting_period_days,
            "waiting_period_met": waiting_met,
            "eligible": eligible,
            "copay_pct": float(rule.copay_pct),
            "preauth_required": rule.preauth_required,
            "preauth_status": preauth_status,
            "preauth_valid_until": preauth_valid_until,
            "cashless_authorized": cashless_authorized,
            "reasons": reasons,
        },
    )
