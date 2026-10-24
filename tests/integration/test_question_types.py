"""Phase 5: every question type from §2 of the use case through /chat (scripted fake LLM)."""

import pytest
from sqlalchemy import select

from app.graph.plan import SupervisorPlan, Task
from app.models.clinical import Appointment
from app.models.platform import AuditLog, PendingAction

pytestmark = pytest.mark.usefixtures("kb_indexed")


def ask(client, headers, message, session_id=None):
    payload = {"message": message} | ({"session_id": session_id} if session_id else {})
    response = client.post("/chat", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def tools(body):
    return [t["tool"] for t in body["tools_invoked"]]


def systems(body):
    return {c["system"] for c in body["citations"] if c["kind"] == "system"}


def documents(body):
    return {(c["doc_id"], c["section"]) for c in body["citations"] if c["kind"] == "document"}


# §2 "Policy fact" and "Not found" are covered in test_chat.py.

def test_procedure_question(client, patient_headers, fake_llm):
    fake_llm()
    body = ask(client, patient_headers, "How do I schedule an MRI scan?")
    assert body["answer_type"] == "retrieved_fact"
    assert any(doc == "SOP-RAD-MRI" for doc, _ in documents(body))


def test_personal_data_question(client, patient_headers, fake_llm):
    fake_llm()
    body = ask(client, patient_headers, "What are my upcoming appointments?")
    assert body["answer_type"] == "calculated"
    assert tools(body) == ["get_appointments"] and systems(body) == {"Hospital Scheduling System"}


def test_eligibility_check(client, patient_headers, fake_llm):
    fake = fake_llm()
    body = ask(client, patient_headers, "Am I covered for knee replacement under my insurance plan?")
    assert body["answer_type"] == "calculated" and tools(body) == ["check_insurance_eligibility"]
    assert '"cashless_authorized": true' in fake.prompts[-1]  # decision came from the tool, not the LLM


def test_eligibility_for_recommended_surgery_resolves_procedure_in_code(client, patient_headers, fake_llm):
    fake = fake_llm(plan=lambda _: SupervisorPlan(tasks=[Task(specialist="insurance")]))
    body = ask(client, patient_headers, "Am I covered for the surgery my doctor recommended?")
    assert body["answer_type"] == "calculated"
    assert '"procedure": "Knee Replacement"' in fake.prompts[-1]


def test_lab_result_question(client, patient_headers, fake_llm):
    fake = fake_llm()
    body = ask(client, patient_headers, "What is my latest blood sugar reading?")
    assert body["answer_type"] == "calculated" and systems(body) == {"Laboratory Information System"}
    assert '"test_code": "FBG"' in fake.prompts[-1] and '"flag": "above_range"' in fake.prompts[-1]


def test_ambiguous_lab_name_asks_for_clarification(client, patient_headers, fake_llm):
    fake_llm(plan=lambda _: SupervisorPlan(tasks=[Task(specialist="labs", test="sugar")]))
    body = ask(client, patient_headers, "What's my sugar level?")
    assert body["answer_type"] == "clarification_needed"


def test_multi_step_surgery_readiness(client, patient_headers, fake_llm, app_session_factory):
    fake = fake_llm()
    body = ask(client, patient_headers, "Can I proceed with surgery next week?")

    assert body["answer_type"] == "calculated"
    assert set(tools(body)) == {
        "get_pending_preop_tests", "check_insurance_eligibility", "get_surgeon_schedule", "surgery_readiness_check"
    }
    composer_input = fake.prompts[-1]
    assert '"ready": false' in composer_input and "HbA1c" in composer_input
    # Protocol sections behind the readiness rules are cited (R2), alongside the systems used.
    assert ("PROT-ORTHO-KNEE", "3. Readiness Checklist > 3.1 Clearance Conditions") in documents(body)
    assert ("PROT-ORTHO-KNEE", "2. Required Pre-operative Tests") in documents(body)
    assert {"Electronic Medical Record System", "Insurance Verification System"} <= systems(body)

    with app_session_factory() as db:
        timings = db.scalars(select(AuditLog.node_timings).where(AuditLog.trace_id == body["trace_id"])).one()
    assert {"care_preop", "care_insurance", "care_surgeon", "care_readiness"} <= {t["node"] for t in timings}


def test_readiness_blocked_by_insurance_for_basic_plan(client, login, fake_llm):
    fake = fake_llm()
    ask(client, login("patient3", "Patient3@demo"), "Am I ready for my knee surgery? Can I proceed?")
    assert '"blocking_items": [\n' in fake.prompts[-1] or "Insurance pre-authorisation approved" in fake.prompts[-1]
    assert '"ready": false' in fake.prompts[-1]


def test_pending_preop_tests_question(client, patient_headers, fake_llm):
    fake = fake_llm()
    body = ask(client, patient_headers, "My doctor recommended surgery. What pre-operative tests are still pending?")
    assert "get_pending_preop_tests" in tools(body)
    assert '"status": "ordered_pending"' in fake.prompts[-1]


def test_reschedule_offers_slots_then_proposes_without_changing_data(client, patient_headers, fake_llm, app_session_factory):
    fake_llm()
    first = ask(client, patient_headers, "Reschedule my cardiology appointment")
    assert first["answer_type"] == "clarification_needed" and first["pending_action"] is None

    choose = lambda _: SupervisorPlan(
        tasks=[Task(specialist="appointments", action="reschedule", department="Cardiology", slot_preference="the first one")]
    )
    fake = fake_llm(plan=choose)
    with app_session_factory() as db:
        before = db.scalars(select(Appointment.start_time).where(Appointment.patient_id == "P1001", Appointment.department == "Cardiology")).one()

    second = ask(client, patient_headers, "The first one please", session_id=first["session_id"])
    assert second["answer_type"] == "clarification_needed"
    assert second["pending_action"] and "Move your Cardiology Consultation" in second["pending_action"]["summary"]
    assert "NOT made" in fake.prompts[-1]  # composer told the change is only proposed

    with app_session_factory() as db:
        action = db.get(PendingAction, second["pending_action"]["action_id"])
        after = db.scalars(select(Appointment.start_time).where(Appointment.patient_id == "P1001", Appointment.department == "Cardiology")).one()
    assert action.status == "pending" and before == after


def test_unmatched_slot_choice_asks_again(client, patient_headers, fake_llm):
    vague = lambda _: SupervisorPlan(
        tasks=[Task(specialist="appointments", action="reschedule", department="Cardiology", slot_preference="sometime soon")]
    )
    fake_llm(plan=vague)
    body = ask(client, patient_headers, "sometime soon")
    assert body["answer_type"] == "clarification_needed" and body["pending_action"] is None


def test_medication_interaction(client, patient_headers, fake_llm):
    fake = fake_llm(plan=lambda _: SupervisorPlan(tasks=[Task(specialist="medication", medications=["ibuprofen"])]))
    body = ask(client, patient_headers, "Can I take ibuprofen with my current medicines?")
    assert body["answer_type"] == "calculated"
    assert '"drug_a": "ibuprofen"' in fake.prompts[-1] and "pharmacist" in fake.prompts[-1]


def test_bill_estimate(client, patient_headers, fake_llm):
    fake = fake_llm()
    body = ask(client, patient_headers, "How much will my knee replacement cost?")
    assert body["answer_type"] == "calculated" and '"patient_pays": 35000.0' in fake.prompts[-1]


def test_multi_intent_request_runs_both_specialists(client, patient_headers, fake_llm):
    both = lambda _: SupervisorPlan(
        tasks=[Task(specialist="appointments", action="view"), Task(specialist="knowledge", query="ICU visiting hours")]
    )
    fake_llm(plan=both)
    body = ask(client, patient_headers, "What are my appointments and the ICU visiting hours?")
    assert {"get_appointments", "search_policies"} <= set(tools(body))
    assert body["answer_type"] == "calculated"
