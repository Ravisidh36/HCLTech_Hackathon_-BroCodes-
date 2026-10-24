"""Schema, seed data and database-level controls."""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, ProgrammingError

from app.config import get_settings
from app.db import make_engine
from app.models import clinical as c
from app.models import platform as p


def test_seeded_patients_and_users(db):
    assert db.scalar(select(func.count()).select_from(c.Patient)) == 3
    roles = dict(db.execute(select(p.User.username, p.User.role)).all())
    assert roles == {"patient1": "patient", "patient2": "patient", "patient3": "patient", "admin1": "admin"}


def test_passwords_are_hashed(db):
    hashes = db.scalars(select(p.User.password_hash)).all()
    assert all(h.startswith("$2") for h in hashes)


def test_demo_scenario_data(db):
    # P1001: HbA1c order pending for the knee replacement.
    pending = db.scalars(
        select(c.Order.test_code).where(c.Order.patient_id == "P1001", c.Order.status == "pending")
    ).all()
    assert pending == ["HBA1C"]
    # Appointment slots are booked.
    booked = db.scalar(select(func.count()).select_from(c.Slot).where(c.Slot.is_booked))
    assert booked == db.scalar(select(func.count()).select_from(c.Appointment))


def test_slot_cannot_hold_two_scheduled_appointments(db):
    appt = db.scalars(select(c.Appointment).where(c.Appointment.patient_id == "P1001")).first()
    db.add(
        c.Appointment(
            patient_id="P1002",
            doctor_id=appt.doctor_id,
            slot_id=appt.slot_id,
            department=appt.department,
            appointment_type=appt.appointment_type,
            start_time=appt.start_time,
            status="scheduled",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_app_role_cannot_modify_audit_log(test_engine):
    settings = get_settings()
    app_engine = make_engine(settings.test_app_database_url)
    try:
        with app_engine.begin() as conn:
            conn.execute(text("INSERT INTO platform.audit_log (trace_id, endpoint) VALUES ('T-1', 'test')"))
        with pytest.raises(ProgrammingError), app_engine.begin() as conn:
            conn.execute(text("UPDATE platform.audit_log SET endpoint = 'tampered'"))
        with pytest.raises(ProgrammingError), app_engine.begin() as conn:
            conn.execute(text("DELETE FROM platform.audit_log"))
    finally:
        app_engine.dispose()
