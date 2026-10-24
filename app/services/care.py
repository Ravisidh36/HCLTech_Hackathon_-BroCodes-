"""Steps 2.6–2.7: pre-operative tests, surgeon schedule and the surgery-readiness checklist (§4).

evaluate_readiness is a pure function over the three read-only results, so the graph can run the
reads in parallel and then apply the same deterministic rules.
"""

from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.clinical import Doctor, LabResult, LabTest, Order, PreopRequirement, Slot, Surgery
from app.schemas.common import RequestContext
from app.schemas.tools import ToolResult, ToolStatus
from app.services.common import EMR, SCHEDULING, hospital_tz, local_iso, require_patient, today
from app.services.insurance import check_insurance_eligibility, match_procedure

READINESS_SECTION = "3. Readiness Checklist > 3.1 Clearance Conditions"


def planned_surgery(db: Session, patient_id: str, procedure: str | None = None) -> Surgery | None:
    surgeries = db.scalars(
        select(Surgery)
        .where(Surgery.patient_id == patient_id, Surgery.status == "planned", Surgery.planned_date >= today())
        .order_by(Surgery.planned_date)
    ).all()
    if procedure:
        matched = match_procedure([s.procedure for s in surgeries], procedure)
        surgeries = [s for s in surgeries if s.procedure == matched]
    return surgeries[0] if surgeries else None


def get_pending_preop_tests(db: Session, ctx: RequestContext, procedure: str | None = None) -> ToolResult:
    tool = "get_pending_preop_tests"
    patient_id = require_patient(ctx)
    surgery = planned_surgery(db, patient_id, procedure)

    requested = procedure or (surgery.procedure if surgery else None)
    if requested is None:
        return ToolResult.not_found(tool, EMR, "No planned surgery was found. Please name the procedure.")

    known = list(db.scalars(select(PreopRequirement.procedure).distinct()))
    matched = match_procedure(known, requested)
    if matched is None:
        return ToolResult.not_found(tool, EMR, f"No pre-operative protocol is on record for '{requested}'.")

    requirements = db.execute(
        select(PreopRequirement, LabTest)
        .join(LabTest, LabTest.code == PreopRequirement.test_code)
        .where(PreopRequirement.procedure == matched)
        .order_by(PreopRequirement.id)
    ).tuples().all()

    # Results must still be valid on the day of surgery (protocol §2).
    reference_date = surgery.planned_date if surgery and surgery.procedure == matched else today()
    pending_orders = set(
        db.scalars(select(Order.test_code).where(Order.patient_id == patient_id, Order.status == "pending"))
    )

    items, pending = [], []
    for req, lab_test in requirements:
        latest = db.scalars(
            select(LabResult)
            .where(LabResult.patient_id == patient_id, LabResult.test_code == req.test_code)
            .order_by(LabResult.collected_at.desc())
        ).first()
        valid = latest is not None and latest.collected_at.astimezone(hospital_tz()).date() + timedelta(
            days=req.valid_days
        ) >= reference_date
        if valid:
            status = "complete"
        elif req.test_code in pending_orders:
            status = "ordered_pending"
        elif latest is not None:
            status = "expired"
        else:
            status = "missing"
        if status != "complete":
            pending.append(lab_test.name)
        items.append(
            {
                "test_code": req.test_code,
                "test_name": lab_test.name,
                "valid_for_days": req.valid_days,
                "last_result_at": local_iso(latest.collected_at) if latest else None,
                "status": status,
            }
        )

    first = requirements[0][0]
    return ToolResult.ok(
        tool,
        EMR,
        {
            "procedure": matched,
            "reference_date": reference_date.isoformat(),
            "requirements": items,
            "pending": pending,
            "protocol": {"doc_id": first.doc_id, "version": first.doc_version, "section": first.section},
        },
    )


def get_surgeon_schedule(db: Session, ctx: RequestContext, procedure: str | None = None) -> ToolResult:
    tool = "get_surgeon_schedule"
    surgery = planned_surgery(db, require_patient(ctx), procedure)
    if surgery is None:
        return ToolResult.not_found(tool, SCHEDULING, "No planned surgery was found for you.")

    surgeon = db.get(Doctor, surgery.surgeon_id)
    tz = hospital_tz()
    day_start = datetime.combine(surgery.planned_date, time.min, tzinfo=tz)
    on_day = db.scalars(
        select(Slot).where(
            Slot.doctor_id == surgeon.id,
            Slot.slot_type == "surgery",
            Slot.start_time >= day_start,
            Slot.start_time < day_start + timedelta(days=1),
        )
    ).all()
    free_on_day = [s for s in on_day if not s.is_booked]
    alternatives = db.scalars(
        select(Slot)
        .where(
            Slot.doctor_id == surgeon.id,
            Slot.slot_type == "surgery",
            Slot.is_booked.is_(False),
            Slot.start_time >= day_start + timedelta(days=1),
        )
        .order_by(Slot.start_time)
        .limit(3)
    ).all()

    return ToolResult.ok(
        tool,
        SCHEDULING,
        {
            "procedure": surgery.procedure,
            "surgeon": surgeon.full_name,
            "planned_date": surgery.planned_date.isoformat(),
            "available_on_planned_date": bool(free_on_day),
            "theatre_slot": local_iso(free_on_day[0].start_time) if free_on_day else None,
            "alternative_slots": [local_iso(s.start_time) for s in alternatives],
        },
    )


def _item(item: str, passed: bool, detail: str, source: str) -> dict:
    return {"item": item, "passed": passed, "detail": detail, "source": source}


def evaluate_readiness(
    procedure: str,
    planned_date: date,
    consent_signed: bool,
    preop: ToolResult,
    insurance: ToolResult,
    surgeon: ToolResult,
) -> ToolResult:
    """Apply protocol §3.1 clearance conditions to already-fetched results. Pure and deterministic."""
    tool = "surgery_readiness_check"
    checklist = []

    if preop.status == ToolStatus.OK:
        pending = preop.data["pending"]
        checklist.append(
            _item(
                "Pre-operative tests complete and valid",
                not pending,
                "All required tests are complete." if not pending else f"Pending: {', '.join(pending)}.",
                EMR,
            )
        )
    else:
        checklist.append(_item("Pre-operative tests complete and valid", False, f"Could not verify: {preop.message}", EMR))

    if insurance.status == ToolStatus.OK:
        ok = insurance.data["cashless_authorized"]
        checklist.append(
            _item("Insurance pre-authorisation approved", ok, " ".join(insurance.data["reasons"]), insurance.source_system)
        )
    else:
        checklist.append(
            _item("Insurance pre-authorisation approved", False, f"Could not verify: {insurance.message}", insurance.source_system)
        )

    if surgeon.status == ToolStatus.OK:
        ok = surgeon.data["available_on_planned_date"]
        detail = (
            f"{surgeon.data['surgeon']} has a theatre slot at {surgeon.data['theatre_slot']}."
            if ok
            else f"{surgeon.data['surgeon']} has no free theatre slot on {surgeon.data['planned_date']}."
        )
        checklist.append(_item("Surgeon theatre slot confirmed", ok, detail, SCHEDULING))
    else:
        checklist.append(_item("Surgeon theatre slot confirmed", False, f"Could not verify: {surgeon.message}", SCHEDULING))

    checklist.append(
        _item(
            "Signed informed consent on file",
            consent_signed,
            "Consent is on file." if consent_signed else "Consent has not been signed.",
            EMR,
        )
    )

    protocol = preop.data["protocol"] if preop.status == ToolStatus.OK else None
    blocking = [c["item"] for c in checklist if not c["passed"]]
    return ToolResult.ok(
        tool,
        EMR,
        {
            "procedure": procedure,
            "planned_date": planned_date.isoformat(),
            "ready": not blocking,
            "checklist": checklist,
            "blocking_items": blocking,
            "protocol": {**protocol, "section": READINESS_SECTION} if protocol else None,
        },
    )


def surgery_readiness_check(db: Session, ctx: RequestContext, procedure: str | None = None) -> ToolResult:
    """Sequential convenience wrapper; the graph runs the three reads in parallel instead."""
    surgery = planned_surgery(db, require_patient(ctx), procedure)
    if surgery is None:
        return ToolResult.not_found("surgery_readiness_check", EMR, "No planned surgery was found for you.")
    return evaluate_readiness(
        surgery.procedure,
        surgery.planned_date,
        surgery.consent_signed,
        get_pending_preop_tests(db, ctx, surgery.procedure),
        check_insurance_eligibility(db, ctx, surgery.procedure),
        get_surgeon_schedule(db, ctx, surgery.procedure),
    )
