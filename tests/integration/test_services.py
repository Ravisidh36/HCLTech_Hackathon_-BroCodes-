"""Phase 2: deterministic services against the seeded test database."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.clinical import Appointment, Slot
from app.models.platform import PendingAction
from app.schemas.tools import ToolStatus
from app.services.appointments import (
    execute_reschedule,
    get_appointments,
    get_available_slots,
    propose_reschedule,
)
from app.services.billing import estimate_bill
from app.services.care import get_pending_preop_tests, get_surgeon_schedule, surgery_readiness_check
from app.services.common import NoPatientContext, now, today
from app.services.insurance import check_insurance_eligibility
from app.services.labs import get_lab_results
from app.services.pharmacy import drug_interaction_check


# --- identity -------------------------------------------------------------------------------

def test_patient_tools_require_patient_identity(db, ctx_for):
    with pytest.raises(NoPatientContext):
        get_appointments(db, ctx_for("admin1"))


# --- 2.1 appointments and slots -------------------------------------------------------------

def test_get_appointments_returns_only_own(db, ctx_for):
    result = get_appointments(db, ctx_for("patient1"))
    assert result.status == ToolStatus.OK
    assert {a["department"] for a in result.data} == {"Cardiology", "Orthopedics"}
    own_ids = {a.id for a in db.scalars(select(Appointment).where(Appointment.patient_id == "P1001"))}
    assert {a["appointment_id"] for a in result.data} <= own_ids


def test_get_appointments_department_filter(db, ctx_for):
    result = get_appointments(db, ctx_for("patient1"), department="cardiology")
    assert [a["department"] for a in result.data] == ["Cardiology"]


def test_available_slots_are_free_and_future(db, ctx_for):
    result = get_available_slots(db, ctx_for("patient1"), department="Cardiology")
    assert result.status == ToolStatus.OK
    slot_ids = [s["slot_id"] for s in result.data]
    slots = db.scalars(select(Slot).where(Slot.id.in_(slot_ids))).all()
    assert all(not s.is_booked and s.start_time > now() for s in slots)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"department": "Cardiology", "date_from": today() - timedelta(days=1)},
        {"department": "Cardiology", "date_from": today(), "date_to": today() + timedelta(days=45)},
    ],
)
def test_available_slots_rejects_bad_input(db, ctx_for, kwargs):
    assert get_available_slots(db, ctx_for("patient1"), **kwargs).status == ToolStatus.ERROR


# --- 2.2 two-phase reschedule ---------------------------------------------------------------

def _cardiology(db, patient_id):
    return db.scalars(
        select(Appointment).where(Appointment.patient_id == patient_id, Appointment.department == "Cardiology")
    ).one()


def _propose_first_free(db, ctx):
    options = propose_reschedule(db, ctx, department="Cardiology")
    assert options.data["needs_choice"] is True and options.data["options"]
    slot_id = options.data["options"][0]["slot_id"]
    proposal = propose_reschedule(db, ctx, department="Cardiology", slot_id=slot_id)
    assert proposal.status == ToolStatus.OK
    return proposal.data["action_id"], slot_id


def test_propose_does_not_change_appointment(db, ctx_for):
    ctx = ctx_for("patient1")
    before = _cardiology(db, "P1001").slot_id
    _propose_first_free(db, ctx)
    assert _cardiology(db, "P1001").slot_id == before


def test_confirm_moves_appointment_and_slots(db, ctx_for):
    ctx = ctx_for("patient1")
    old_slot_id = _cardiology(db, "P1001").slot_id
    action_id, new_slot_id = _propose_first_free(db, ctx)

    result = execute_reschedule(db, ctx, action_id, "confirm")

    assert result.status == ToolStatus.OK
    appt = _cardiology(db, "P1001")
    assert appt.slot_id == new_slot_id
    assert db.get(Slot, new_slot_id).is_booked and not db.get(Slot, old_slot_id).is_booked
    assert db.get(PendingAction, action_id).status == "executed"


def test_confirm_cannot_be_replayed(db, ctx_for):
    ctx = ctx_for("patient1")
    action_id, _ = _propose_first_free(db, ctx)
    execute_reschedule(db, ctx, action_id, "confirm")
    assert execute_reschedule(db, ctx, action_id, "confirm").status == ToolStatus.ERROR


def test_other_patient_cannot_confirm(db, ctx_for):
    action_id, _ = _propose_first_free(db, ctx_for("patient1"))
    result = execute_reschedule(db, ctx_for("patient2"), action_id, "confirm")
    assert result.status == ToolStatus.NOT_FOUND
    assert db.get(PendingAction, action_id).status == "pending"


def test_expired_confirmation_is_rejected(db, ctx_for):
    ctx = ctx_for("patient1")
    before = _cardiology(db, "P1001").slot_id
    action_id, _ = _propose_first_free(db, ctx)
    db.get(PendingAction, action_id).expires_at = now() - timedelta(seconds=1)
    db.commit()

    assert execute_reschedule(db, ctx, action_id, "confirm").status == ToolStatus.ERROR
    assert db.get(PendingAction, action_id).status == "expired"
    assert _cardiology(db, "P1001").slot_id == before


def test_slot_taken_after_proposal_is_rejected(db, ctx_for):
    ctx = ctx_for("patient1")
    action_id, slot_id = _propose_first_free(db, ctx)
    db.get(Slot, slot_id).is_booked = True
    db.commit()

    assert execute_reschedule(db, ctx, action_id, "confirm").status == ToolStatus.ERROR
    assert db.get(PendingAction, action_id).status == "failed"


def test_cancel_leaves_appointment_unchanged(db, ctx_for):
    ctx = ctx_for("patient1")
    before = _cardiology(db, "P1001").slot_id
    action_id, _ = _propose_first_free(db, ctx)
    assert execute_reschedule(db, ctx, action_id, "cancel").status == ToolStatus.OK
    assert _cardiology(db, "P1001").slot_id == before


def test_cannot_propose_for_another_patients_appointment(db, ctx_for):
    other = _cardiology(db, "P1002")
    result = propose_reschedule(db, ctx_for("patient1"), appointment_id=other.id, slot_id=other.slot_id)
    assert result.status == ToolStatus.NOT_FOUND


# --- 2.3 insurance --------------------------------------------------------------------------

def test_insurance_gold_knee_replacement(db, ctx_for):
    result = check_insurance_eligibility(db, ctx_for("patient1"), "knee replacement surgery")
    assert result.status == ToolStatus.OK
    assert result.data["eligible"] and result.data["cashless_authorized"]
    assert result.data["copay_pct"] == 10 and result.data["preauth_status"] == "approved"


def test_insurance_basic_excludes_knee_replacement(db, ctx_for):
    result = check_insurance_eligibility(db, ctx_for("patient3"), "Knee Replacement")
    assert result.data["covered"] is False and result.data["eligible"] is False


def test_insurance_unknown_procedure_is_not_found(db, ctx_for):
    result = check_insurance_eligibility(db, ctx_for("patient1"), "heart transplant")
    assert result.status == ToolStatus.NOT_FOUND


def test_insurance_output_has_no_policy_number(db, ctx_for):
    result = check_insurance_eligibility(db, ctx_for("patient1"), "MRI Scan")
    assert "GLD-4471-2290" not in result.model_dump_json()


# --- 2.4 labs -------------------------------------------------------------------------------

def test_blood_sugar_maps_to_fasting_glucose(db, ctx_for):
    result = get_lab_results(db, ctx_for("patient1"), "latest blood sugar reading")
    assert result.data["test_code"] == "FBG"
    assert result.data["results"][0] == {**result.data["results"][0], "value": 132.0, "flag": "above_range"}


def test_ambiguous_test_name_asks_for_clarification(db, ctx_for):
    result = get_lab_results(db, ctx_for("patient1"), "sugar")
    assert result.data["needs_clarification"] is True and len(result.data["options"]) == 2


def test_unknown_test_is_not_found(db, ctx_for):
    assert get_lab_results(db, ctx_for("patient1"), "vitamin z").status == ToolStatus.NOT_FOUND


def test_lab_results_are_patient_scoped(db, ctx_for):
    p1 = get_lab_results(db, ctx_for("patient1"), "fasting glucose").data["results"][0]["value"]
    p2 = get_lab_results(db, ctx_for("patient2"), "fasting glucose").data["results"][0]["value"]
    assert (p1, p2) == (132.0, 94.0)


# --- 2.5 pharmacy ---------------------------------------------------------------------------

def test_current_medications_interaction_found(db, ctx_for):
    result = drug_interaction_check(db, ctx_for("patient1"))
    pairs = {(i["drug_a"], i["drug_b"], i["severity"]) for i in result.data["interactions"]}
    assert ("aspirin", "warfarin", "major") in pairs
    assert "pharmacist" in result.data["note"]


def test_proposed_medicine_checked_against_current(db, ctx_for):
    result = drug_interaction_check(db, ctx_for("patient1"), ["Ibuprofen", "zzdrug"])
    assert ("ibuprofen", "warfarin") in {(i["drug_a"], i["drug_b"]) for i in result.data["interactions"]}
    assert result.data["unknown"] == ["zzdrug"]


def test_unknown_drugs_are_never_reported_safe(db, ctx_for):
    result = drug_interaction_check(db, ctx_for("patient3"), ["zzdrug"])
    assert result.status == ToolStatus.NOT_FOUND


# --- 2.6–2.7 care coordination --------------------------------------------------------------

def test_preop_pending_for_p1001_is_hba1c_only(db, ctx_for):
    result = get_pending_preop_tests(db, ctx_for("patient1"))
    assert result.data["pending"] == ["HbA1c (glycated haemoglobin)"]
    assert result.data["protocol"]["doc_id"] == "PROT-ORTHO-KNEE"


def test_preop_complete_for_p1003(db, ctx_for):
    assert get_pending_preop_tests(db, ctx_for("patient3")).data["pending"] == []


def test_surgeon_available_on_planned_date(db, ctx_for):
    assert get_surgeon_schedule(db, ctx_for("patient1")).data["available_on_planned_date"] is True


def test_readiness_p1001_blocked_by_tests_only(db, ctx_for):
    result = surgery_readiness_check(db, ctx_for("patient1"))
    assert result.data["ready"] is False
    assert result.data["blocking_items"] == ["Pre-operative tests complete and valid"]


def test_readiness_p1003_blocked_by_insurance_only(db, ctx_for):
    result = surgery_readiness_check(db, ctx_for("patient3"))
    assert result.data["blocking_items"] == ["Insurance pre-authorisation approved"]


def test_readiness_without_planned_surgery(db, ctx_for):
    assert surgery_readiness_check(db, ctx_for("patient2")).status == ToolStatus.NOT_FOUND


# --- 2.8 billing ----------------------------------------------------------------------------

def test_bill_estimate_with_copay(db, ctx_for):
    result = estimate_bill(db, ctx_for("patient1"), "Knee Replacement")
    assert (result.data["patient_pays"], result.data["insurer_pays"]) == (35000.0, 315000.0)


def test_bill_estimate_not_covered(db, ctx_for):
    assert estimate_bill(db, ctx_for("patient3"), "Knee Replacement").data["patient_pays"] == 350000.0
