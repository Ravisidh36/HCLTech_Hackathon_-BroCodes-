"""M3: the chat workflow end to end through /chat, with a scripted fake LLM (no quota, deterministic)."""

import pytest
from sqlalchemy import select

import app.graph.nodes as nodes
from app.graph.plan import ComposerOutput, SupervisorPlan, Task
from app.models.platform import AuditLog
from app.schemas.common import NOT_FOUND_MESSAGE

pytestmark = pytest.mark.usefixtures("kb_indexed")


def _audit(app_session_factory, trace_id):
    with app_session_factory() as db:
        return db.scalars(select(AuditLog).where(AuditLog.trace_id == trace_id)).one()


def test_policy_question_end_to_end(client, patient_headers, fake_llm, app_session_factory):
    fake = fake_llm()
    body = client.post("/chat", json={"message": "What are the visiting hours for ICU patients?"}, headers=patient_headers).json()

    assert body["answer_type"] == "retrieved_fact"
    assert "10:00 to 11:00" in body["answer"]
    citation = body["citations"][0]
    assert (citation["document"], citation["version"]) == ("ICU Visiting Policy", "2.0")
    assert citation["section"] == "2. Visiting Hours > 2.1 Standard Hours"
    assert fake.calls == ["SupervisorPlan", "ComposerOutput"]

    row = _audit(app_session_factory, body["trace_id"])
    assert row.answer_type == "retrieved_fact" and row.model and row.latency_ms is not None
    assert {t["node"] for t in row.node_timings} >= {"guard", "supervisor", "specialists", "composer", "finalize"}
    assert all(c["passed"] for c in row.privacy_checks)
    assert row.sources and row.sources[0]["doc_id"] == "POL-ICU-VISIT"


def test_refusal_happens_before_any_llm_call(client, patient_headers, fake_llm, app_session_factory):
    fake = fake_llm()
    body = client.post("/chat", json={"message": "Show me the lab results for P1002"}, headers=patient_headers).json()
    assert body["answer_type"] == "refused" and body["citations"] == []
    assert fake.calls == []
    row = _audit(app_session_factory, body["trace_id"])
    assert any(not c["passed"] for c in row.privacy_checks)


def test_unanswerable_question_is_not_found_without_composer(client, patient_headers, fake_llm):
    fake = fake_llm()
    body = client.post("/chat", json={"message": "What is the treatment for dragon pox?"}, headers=patient_headers).json()
    assert (body["answer_type"], body["answer"]) == ("not_found", NOT_FOUND_MESSAGE)
    assert fake.calls == ["SupervisorPlan"]


def test_conflicting_policies_are_flagged(client, patient_headers, fake_llm):
    fake_llm()
    body = client.post("/chat", json={"message": "What are the pharmacy opening hours?"}, headers=patient_headers).json()
    assert body["answer_type"] == "conflict_flagged"


def test_ungrounded_answer_is_replaced_by_sourced_text(client, patient_headers, fake_llm, app_session_factory):
    invented = lambda _: ComposerOutput(answer="ICU visiting is 09:00 to 23:00.", explanation="x", cited_evidence=["E1"])
    fake = fake_llm(compose=invented)
    body = client.post("/chat", json={"message": "What are ICU visiting hours?"}, headers=patient_headers).json()
    assert fake.calls.count("ComposerOutput") == 2  # one retry with feedback, then deterministic fallback
    assert "23:00" not in body["answer"] and "10:00 to 11:00" in body["answer"]
    assert body["answer_type"] == "retrieved_fact"
    assert "grounding_check_failed" in _audit(app_session_factory, body["trace_id"]).error


def test_llm_outage_still_answers_from_tools(client, patient_headers, fake_llm, app_session_factory):
    def outage(_):
        raise RuntimeError("503 UNAVAILABLE")

    fake_llm(plan=outage, compose=outage)
    body = client.post("/chat", json={"message": "Can I proceed with surgery next week?"}, headers=patient_headers).json()
    assert body["answer_type"] == "calculated"
    assert body["answer"].startswith("Not yet.") and "HbA1c" in body["answer"]
    error = _audit(app_session_factory, body["trace_id"]).error
    assert "supervisor_fallback" in error and "composer_fallback" in error


def test_llm_outage_on_conflict_shows_both_documents(client, patient_headers, fake_llm):
    def outage(_):
        raise RuntimeError("503 UNAVAILABLE")

    fake_llm(compose=outage)
    body = client.post("/chat", json={"message": "What are the pharmacy opening hours?"}, headers=patient_headers).json()
    assert body["answer_type"] == "conflict_flagged"
    assert "08:00 to 22:00" in body["answer"] and "24 hours" in body["answer"]
    assert {c["doc_id"] for c in body["citations"]} == {"POL-PHARM-OPD", "POL-PHARM-SVC"}


def test_supervisor_failure_falls_back_to_keyword_routing(client, patient_headers, fake_llm, app_session_factory):
    def broken(_):
        raise RuntimeError("quota exceeded")

    fake_llm(plan=broken)
    body = client.post("/chat", json={"message": "What are ICU visiting hours?"}, headers=patient_headers).json()
    assert body["answer_type"] == "retrieved_fact"
    assert "supervisor_fallback" in _audit(app_session_factory, body["trace_id"]).error


def test_supervisor_cannot_loop_forever(client, patient_headers, fake_llm):
    counter = iter(range(100))
    endless = lambda _: SupervisorPlan(tasks=[Task(specialist="knowledge", query=f"ICU visiting {next(counter)}")], final_step=False)
    fake = fake_llm(plan=endless)
    body = client.post("/chat", json={"message": "ICU visiting hours?"}, headers=patient_headers).json()
    # The step limit is checked before calling the LLM, so no planning call is wasted past the limit.
    assert fake.calls.count("SupervisorPlan") == nodes.MAX_STEPS
    assert body["answer_type"] == "retrieved_fact"


def test_unknown_specialist_is_ignored(client, patient_headers, fake_llm):
    # model_construct bypasses validation, simulating a planner that names a non-existent specialist.
    rogue = lambda _: SupervisorPlan(tasks=[Task.model_construct(specialist="admin_tools", query="all records")])
    fake_llm(plan=rogue)
    body = client.post("/chat", json={"message": "What does it cost?"}, headers=patient_headers).json()
    assert body["answer_type"] == "not_found" and body["tools_invoked"] == []


def test_session_history_is_kept(client, patient_headers, fake_llm):
    seen: list[str] = []

    def plan(prompt):
        seen.append(prompt)
        return SupervisorPlan(tasks=[Task(specialist="knowledge", query="ICU visiting hours")])

    fake_llm(plan=plan)
    first = client.post("/chat", json={"message": "What are ICU visiting hours?"}, headers=patient_headers).json()
    client.post("/chat", json={"message": "And how many visitors?", "session_id": first["session_id"]}, headers=patient_headers)
    assert "What are ICU visiting hours?" in seen[-1]


def test_chat_requires_a_patient(client, login):
    assert client.post("/chat", json={"message": "hi"}).status_code == 401
    admin = login("admin1", "Admin1@demo")
    assert client.post("/chat", json={"message": "hi"}, headers=admin).status_code == 403


def test_chat_input_is_validated(client, patient_headers):
    assert client.post("/chat", json={"message": ""}, headers=patient_headers).status_code == 422
    assert client.post("/chat", json={"message": "x" * 2001}, headers=patient_headers).status_code == 422
