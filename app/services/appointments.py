"""Steps 2.1–2.2: appointments, availability and two-phase rescheduling.

Rescheduling never happens in one call: propose_reschedule stores a pending action, and only
execute_reschedule (reached through /actions/confirm) changes data, after re-validating
ownership, expiry and slot availability under row locks.
"""

import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.clinical import Appointment, Doctor, Slot
from app.models.platform import PendingAction
from app.schemas.common import RequestContext
from app.schemas.tools import ToolResult
from app.services.common import SCHEDULING, hospital_tz, local_iso, normalize, now, require_patient, today

ACTION_RESCHEDULE = "reschedule_appointment"
ACTION_TTL = timedelta(minutes=5)
DEFAULT_RANGE_DAYS = 14
MAX_RANGE_DAYS = 30
MAX_SLOTS = 10


def _appointment_view(appt: Appointment, doctor: Doctor) -> dict:
    return {
        "appointment_id": appt.id,
        "start_time": local_iso(appt.start_time),
        "department": appt.department,
        "appointment_type": appt.appointment_type,
        "doctor": doctor.full_name,
        "status": appt.status,
    }


def _slot_view(slot: Slot, doctor: Doctor) -> dict:
    return {
        "slot_id": slot.id,
        "start_time": local_iso(slot.start_time),
        "doctor": doctor.full_name,
        "department": doctor.department,
    }


def _upcoming_appointments(db: Session, patient_id: str, department: str | None) -> list[tuple[Appointment, Doctor]]:
    stmt = (
        select(Appointment, Doctor)
        .join(Doctor, Doctor.id == Appointment.doctor_id)
        .where(
            Appointment.patient_id == patient_id,
            Appointment.status == "scheduled",
            Appointment.start_time >= now(),
        )
        .order_by(Appointment.start_time)
    )
    if department:
        stmt = stmt.where(func.lower(Appointment.department) == normalize(department))
    return list(db.execute(stmt).tuples())


def get_appointments(db: Session, ctx: RequestContext, department: str | None = None) -> ToolResult:
    tool = "get_appointments"
    rows = _upcoming_appointments(db, require_patient(ctx), department)
    if not rows:
        return ToolResult.not_found(tool, SCHEDULING, "No upcoming appointments found.")
    return ToolResult.ok(tool, SCHEDULING, [_appointment_view(a, d) for a, d in rows])


def get_available_slots(
    db: Session,
    ctx: RequestContext,
    department: str | None = None,
    doctor_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    exclude_slot_id: int | None = None,
) -> ToolResult:
    tool = "get_available_slots"
    if not department and not doctor_id:
        return ToolResult.error(tool, SCHEDULING, "Specify a department or a doctor.")

    start = date_from or today()
    end = date_to or start + timedelta(days=DEFAULT_RANGE_DAYS)
    if start < today():
        return ToolResult.error(tool, SCHEDULING, "Cannot search for slots in the past.")
    if end < start or (end - start).days > MAX_RANGE_DAYS:
        return ToolResult.error(tool, SCHEDULING, f"Date range must be between 0 and {MAX_RANGE_DAYS} days.")

    tz = hospital_tz()
    window_start = max(now(), datetime.combine(start, time.min, tzinfo=tz))
    window_end = datetime.combine(end, time.max, tzinfo=tz)

    stmt = (
        select(Slot, Doctor)
        .join(Doctor, Doctor.id == Slot.doctor_id)
        .where(
            Slot.is_booked.is_(False),
            Slot.slot_type == "consultation",
            Slot.start_time >= window_start,
            Slot.start_time <= window_end,
        )
        .order_by(Slot.start_time)
        .limit(MAX_SLOTS)
    )
    if department:
        stmt = stmt.where(func.lower(Doctor.department) == normalize(department))
    if doctor_id:
        stmt = stmt.where(Slot.doctor_id == doctor_id)
    if exclude_slot_id:
        stmt = stmt.where(Slot.id != exclude_slot_id)

    rows = db.execute(stmt).tuples().all()
    if not rows:
        return ToolResult.not_found(tool, SCHEDULING, "No free slots found for that search.")
    return ToolResult.ok(tool, SCHEDULING, [_slot_view(s, d) for s, d in rows])


def propose_reschedule(
    db: Session,
    ctx: RequestContext,
    appointment_id: int | None = None,
    department: str | None = None,
    slot_id: int | None = None,
) -> ToolResult:
    """Validate a reschedule request and store it as a pending action. Changes no appointment."""
    tool = "propose_reschedule"
    patient_id = require_patient(ctx)

    rows = _upcoming_appointments(db, patient_id, department)
    if appointment_id is not None:
        rows = [(a, d) for a, d in rows if a.id == appointment_id]
    if not rows:
        return ToolResult.not_found(tool, SCHEDULING, "No matching upcoming appointment was found for you.")
    if len(rows) > 1:
        return ToolResult.ok(
            tool,
            SCHEDULING,
            {"needs_clarification": True, "options": [_appointment_view(a, d) for a, d in rows]},
            "Several appointments match. Ask which one to reschedule.",
        )
    appt, doctor = rows[0]

    if slot_id is None:
        slots = get_available_slots(db, ctx, department=appt.department, exclude_slot_id=appt.slot_id)
        return ToolResult.ok(
            tool,
            SCHEDULING,
            {
                "needs_choice": True,
                "appointment": _appointment_view(appt, doctor),
                "options": slots.data or [],
            },
            "Ask the patient to choose one of the available slots." if slots.data else "No free slots are available.",
        )

    slot_row = db.execute(select(Slot, Doctor).join(Doctor, Doctor.id == Slot.doctor_id).where(Slot.id == slot_id)).first()
    if slot_row is None:
        return ToolResult.not_found(tool, SCHEDULING, "That slot does not exist.")
    slot, slot_doctor = slot_row
    if slot.id == appt.slot_id:
        return ToolResult.error(tool, SCHEDULING, "That is already the current appointment time.")
    if slot.is_booked or slot.start_time <= now() or slot.slot_type != "consultation":
        return ToolResult.error(tool, SCHEDULING, "That slot is no longer available.")
    if normalize(slot_doctor.department) != normalize(appt.department):
        return ToolResult.error(tool, SCHEDULING, "The slot belongs to a different department.")

    summary = (
        f"Move your {appt.appointment_type} with {doctor.full_name} from "
        f"{local_iso(appt.start_time)} to {local_iso(slot.start_time)} with {slot_doctor.full_name}."
    )
    action = PendingAction(
        id=str(uuid.uuid4()),
        user_id=ctx.user_id,
        patient_id=patient_id,
        action_type=ACTION_RESCHEDULE,
        payload={"appointment_id": appt.id, "from_slot_id": appt.slot_id, "to_slot_id": slot.id},
        summary=summary,
        status="pending",
        trace_id=ctx.trace_id,
        expires_at=now() + ACTION_TTL,
    )
    db.add(action)
    db.commit()
    return ToolResult.ok(
        tool,
        SCHEDULING,
        {
            "action_id": action.id,
            "summary": summary,
            "expires_at": local_iso(action.expires_at),
            "appointment": _appointment_view(appt, doctor),
            "new_slot": _slot_view(slot, slot_doctor),
        },
        "Awaiting explicit patient confirmation.",
    )


def _fail(db: Session, action: PendingAction, tool: str, message: str, status: str = "failed") -> ToolResult:
    action.status = status
    action.resolved_at = now()
    db.commit()
    return ToolResult.error(tool, SCHEDULING, message)


def execute_reschedule(db: Session, ctx: RequestContext, action_id: str, decision: str) -> ToolResult:
    """Confirm or cancel a pending reschedule. Re-validates everything inside one transaction."""
    tool = "execute_reschedule"
    patient_id = require_patient(ctx)

    action = db.scalars(select(PendingAction).where(PendingAction.id == action_id).with_for_update()).first()
    # Someone else's action looks exactly like a missing one: no existence leak.
    if action is None or action.patient_id != patient_id or action.user_id != ctx.user_id:
        db.rollback()
        return ToolResult.not_found(tool, SCHEDULING, "No pending request was found.")
    if action.action_type != ACTION_RESCHEDULE:
        db.rollback()
        return ToolResult.error(tool, SCHEDULING, "Unsupported action.")
    if action.status != "pending":
        db.rollback()
        return ToolResult.error(tool, SCHEDULING, f"This request was already {action.status}.")

    if decision == "cancel":
        action.status = "cancelled"
        action.resolved_at = now()
        db.commit()
        return ToolResult.ok(tool, SCHEDULING, {"cancelled": True}, "Reschedule cancelled. Nothing was changed.")
    if decision != "confirm":
        db.rollback()
        return ToolResult.error(tool, SCHEDULING, "Decision must be confirm or cancel.")
    if action.expires_at <= now():
        return _fail(db, action, tool, "This confirmation has expired. Please request the change again.", "expired")

    payload = action.payload
    appt = db.scalars(
        select(Appointment)
        .where(Appointment.id == payload["appointment_id"], Appointment.patient_id == patient_id)
        .with_for_update()
    ).first()
    if appt is None or appt.status != "scheduled" or appt.slot_id != payload["from_slot_id"]:
        return _fail(db, action, tool, "The appointment has changed since this was proposed.")

    slots = {
        s.id: s
        for s in db.scalars(
            select(Slot).where(Slot.id.in_([payload["from_slot_id"], payload["to_slot_id"]])).with_for_update()
        )
    }
    old_slot, new_slot = slots.get(payload["from_slot_id"]), slots.get(payload["to_slot_id"])
    if new_slot is None or old_slot is None or new_slot.is_booked or new_slot.start_time <= now():
        return _fail(db, action, tool, "The chosen slot is no longer available.")

    old_start = appt.start_time
    old_slot.is_booked = False
    new_slot.is_booked = True
    appt.slot_id = new_slot.id
    appt.doctor_id = new_slot.doctor_id
    appt.start_time = new_slot.start_time
    action.status = "executed"
    action.resolved_at = now()
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        failed = db.get(PendingAction, action_id)
        return _fail(db, failed, tool, "The chosen slot was just taken. Please pick another.")

    doctor = db.get(Doctor, appt.doctor_id)
    return ToolResult.ok(
        tool,
        SCHEDULING,
        {
            "appointment_id": appt.id,
            "previous_start": local_iso(old_start),
            "new_start": local_iso(appt.start_time),
            "doctor": doctor.full_name,
            "department": appt.department,
        },
        "Appointment rescheduled.",
    )
