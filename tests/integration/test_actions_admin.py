"""Phase 6: confirmation endpoint and admin APIs (upload, registry, audit lookup)."""

from datetime import timedelta

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.clinical import Appointment, Slot
from app.models.knowledge import Document
from app.models.platform import PendingAction, User
from app.schemas.common import RequestContext, Role
from app.services.appointments import propose_reschedule
from app.services.common import now

pytestmark = pytest.mark.usefixtures("kb_indexed")
DEMO_DIR = get_settings().kb_dir.parent / "uploads_demo"


@pytest.fixture
def admin_headers(login):
    return login("admin1", "Admin1@demo")


@pytest.fixture
def restore_kb(test_engine, tmp_path, monkeypatch):
    """Uploads commit for real; undo them so other tests see the seeded knowledge base."""
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    with Session(test_engine) as db:
        statuses = dict(db.execute(select(Document.id, Document.status)).tuples().all())
    yield
    with Session(test_engine) as db:
        db.execute(delete(Document).where(Document.uploaded_by != "seed"))
        for doc_id, doc_status in statuses.items():
            db.execute(update(Document).where(Document.id == doc_id).values(status=doc_status))
        db.commit()


def _proposal(app_session_factory, username="patient1"):
    """Create a real pending reschedule for P1001's cardiology appointment via the service layer."""
    with app_session_factory() as db:
        user = db.scalars(select(User).where(User.username == username)).one()
        ctx = RequestContext(user_id=user.id, username=username, role=Role.PATIENT, patient_id=user.patient_id, trace_id="T")
        options = propose_reschedule(db, ctx, department="Cardiology").data["options"]
        result = propose_reschedule(db, ctx, department="Cardiology", slot_id=options[0]["slot_id"])
        return result.data["action_id"], options[0]["slot_id"]


def _cardiology_slot(app_session_factory):
    with app_session_factory() as db:
        return db.scalars(
            select(Appointment.slot_id).where(Appointment.patient_id == "P1001", Appointment.department == "Cardiology")
        ).one()


# --- confirmation ---------------------------------------------------------------------------

def test_confirm_executes_reschedule(client, patient_headers, app_session_factory):
    action_id, slot_id = _proposal(app_session_factory)
    body = client.post("/actions/confirm", json={"action_id": action_id, "decision": "confirm"}, headers=patient_headers).json()
    assert body["answer_type"] == "action_completed" and body["answer"].startswith("Done.")
    assert _cardiology_slot(app_session_factory) == slot_id
    assert body["citations"] == [{"kind": "system", "system": "Hospital Scheduling System"}]


def test_cancel_changes_nothing(client, patient_headers, app_session_factory):
    before = _cardiology_slot(app_session_factory)
    action_id, _ = _proposal(app_session_factory)
    body = client.post("/actions/confirm", json={"action_id": action_id, "decision": "cancel"}, headers=patient_headers).json()
    assert body["answer_type"] == "action_completed" and "not been changed" in body["answer"]
    assert _cardiology_slot(app_session_factory) == before


def test_other_patient_cannot_confirm(client, login, app_session_factory):
    action_id, _ = _proposal(app_session_factory)
    other = login("patient2", "Patient2@demo")
    body = client.post("/actions/confirm", json={"action_id": action_id, "decision": "confirm"}, headers=other).json()
    assert body["answer_type"] == "refused"
    with app_session_factory() as db:
        assert db.get(PendingAction, action_id).status == "pending"


def test_expired_confirmation_needs_new_choice(client, patient_headers, app_session_factory, test_engine):
    action_id, _ = _proposal(app_session_factory)
    with Session(test_engine) as db:
        db.execute(update(PendingAction).where(PendingAction.id == action_id).values(expires_at=now() - timedelta(minutes=1)))
        db.commit()
    body = client.post("/actions/confirm", json={"action_id": action_id, "decision": "confirm"}, headers=patient_headers).json()
    assert body["answer_type"] == "clarification_needed" and "expired" in body["answer"]


def test_confirmation_is_audited(client, patient_headers, admin_headers, app_session_factory):
    action_id, _ = _proposal(app_session_factory)
    body = client.post("/actions/confirm", json={"action_id": action_id, "decision": "cancel"}, headers=patient_headers).json()
    rows = client.get(f"/admin/audit/{body['trace_id']}", headers=admin_headers).json()
    assert rows[0]["endpoint"] == "/actions/confirm" and rows[0]["tools"][0]["tool"] == "execute_reschedule"


def test_confirm_requires_patient(client, admin_headers):
    assert client.post("/actions/confirm", json={"action_id": "x", "decision": "confirm"}).status_code == 401
    assert client.post("/actions/confirm", json={"action_id": "x", "decision": "confirm"}, headers=admin_headers).status_code == 403


# --- admin upload (R11) ---------------------------------------------------------------------

def _upload(client, headers, path, **form):
    defaults = {
        "doc_id": "POL-ICU-VISIT", "title": "ICU Visiting Policy", "topic": "icu_visiting", "version": "3.0",
        "effective_date": "2026-10-01", "authority": "hospital_policy",
    }
    with path.open("rb") as fh:
        return client.post("/admin/documents", data=defaults | form, files={"file": (path.name, fh)}, headers=headers)


def test_live_upload_changes_chat_answer(client, admin_headers, patient_headers, fake_llm, restore_kb):
    fake_llm()
    before = client.post("/chat", json={"message": "What are the visiting hours for ICU patients?"}, headers=patient_headers).json()
    assert before["citations"][0]["version"] == "2.0"

    response = _upload(client, admin_headers, DEMO_DIR / "icu-visiting-v3.md")
    assert response.status_code == 200, response.text
    assert response.json()["superseded"] == ["2.0"]

    after = client.post("/chat", json={"message": "What are the visiting hours for ICU patients?"}, headers=patient_headers).json()
    assert after["citations"][0]["version"] == "3.0" and "12:00 to 13:00" in after["answer"]


def test_reupload_is_duplicate(client, admin_headers, restore_kb):
    _upload(client, admin_headers, DEMO_DIR / "icu-visiting-v3.md")
    assert _upload(client, admin_headers, DEMO_DIR / "icu-visiting-v3.md").json()["status"] == "duplicate"


def test_existing_version_number_is_conflict(client, admin_headers, restore_kb):
    assert _upload(client, admin_headers, DEMO_DIR / "icu-visiting-v3.md", version="2.0").status_code == 409


def test_malicious_upload_is_flagged_and_metadata_comes_from_form(client, admin_headers, restore_kb):
    response = _upload(
        client, admin_headers, DEMO_DIR / "malicious-billing-faq.md",
        doc_id="FAQ-BILLING", title="Patient Billing FAQ", topic="billing_faq", version="1.0", authority="department_sop",
    )
    assert response.json()["injection_flags"] >= 1
    docs = {d["doc_id"]: d for d in client.get("/admin/documents", headers=admin_headers).json()}
    assert (docs["FAQ-BILLING"]["version"], docs["FAQ-BILLING"]["authority"]) == ("1.0", "department_sop")


def test_upload_validation(client, admin_headers, tmp_path, restore_kb):
    exe = tmp_path / "payload.exe"
    exe.write_bytes(b"MZ binary")
    assert _upload(client, admin_headers, exe).status_code == 400
    assert _upload(client, admin_headers, DEMO_DIR / "icu-visiting-v3.md", authority="supreme_court").status_code == 422
    assert _upload(client, admin_headers, DEMO_DIR / "icu-visiting-v3.md", doc_id="../../etc/passwd").status_code == 422


def test_upload_is_audited(client, admin_headers, restore_kb):
    response = _upload(client, admin_headers, DEMO_DIR / "icu-visiting-v3.md")
    rows = client.get(f"/admin/audit/{response.json()['trace_id']}", headers=admin_headers).json()
    assert rows[0]["endpoint"] == "/admin/documents" and rows[0]["sources"][0]["result"] == "indexed"


# --- admin access control -------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/admin/documents", "/admin/audit", "/admin/audit/HSP-X"])
def test_admin_endpoints_reject_patients(client, patient_headers, path):
    assert client.get(path, headers=patient_headers).status_code == 403


def test_patient_cannot_upload(client, patient_headers, restore_kb):
    assert _upload(client, patient_headers, DEMO_DIR / "icu-visiting-v3.md").status_code == 403


def test_document_registry_lists_versions(client, admin_headers):
    docs = client.get("/admin/documents", headers=admin_headers).json()
    icu = {d["version"]: d["status"] for d in docs if d["doc_id"] == "POL-ICU-VISIT"}
    assert icu == {"1.0": "superseded", "2.0": "active"}


def test_unknown_trace_is_404(client, admin_headers):
    assert client.get("/admin/audit/HSP-DOES-NOT-EXIST", headers=admin_headers).status_code == 404
