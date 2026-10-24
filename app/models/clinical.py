"""Mock hospital systems (§5.2): EMR, scheduling, lab, insurance, pharmacy, billing."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

SCHEMA = {"schema": "clinical"}


class InsurancePlan(Base):
    __tablename__ = "insurance_plans"
    __table_args__ = SCHEMA

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    provider: Mapped[str] = mapped_column(String(120))


class CoverageRule(Base):
    __tablename__ = "coverage_rules"
    __table_args__ = (UniqueConstraint("plan_id", "procedure"), SCHEMA)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("clinical.insurance_plans.id"))
    procedure: Mapped[str] = mapped_column(String(120))
    covered: Mapped[bool] = mapped_column(Boolean)
    copay_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=0)
    preauth_required: Mapped[bool] = mapped_column(Boolean, default=False)
    waiting_period_days: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = SCHEMA

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    date_of_birth: Mapped[date] = mapped_column(Date)
    phone: Mapped[str] = mapped_column(String(32))
    insurance_plan_id: Mapped[str | None] = mapped_column(ForeignKey("clinical.insurance_plans.id"))
    policy_number: Mapped[str | None] = mapped_column(String(40))
    plan_start_date: Mapped[date | None] = mapped_column(Date)


class PreAuthorization(Base):
    __tablename__ = "preauthorizations"
    __table_args__ = SCHEMA

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("clinical.patients.id"), index=True)
    procedure: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16))  # approved | pending | denied
    valid_until: Mapped[date | None] = mapped_column(Date)


class Doctor(Base):
    __tablename__ = "doctors"
    __table_args__ = SCHEMA

    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(120))
    department: Mapped[str] = mapped_column(String(60), index=True)
    performs_surgery: Mapped[bool] = mapped_column(Boolean, default=False)


class Slot(Base):
    __tablename__ = "slots"
    __table_args__ = (UniqueConstraint("doctor_id", "start_time"), SCHEMA)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    doctor_id: Mapped[str] = mapped_column(ForeignKey("clinical.doctors.id"), index=True)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    slot_type: Mapped[str] = mapped_column(String(16))  # consultation | surgery
    is_booked: Mapped[bool] = mapped_column(Boolean, default=False)


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        # A slot can hold at most one scheduled appointment.
        Index(
            "uq_appointment_active_slot",
            "slot_id",
            unique=True,
            postgresql_where=text("status = 'scheduled'"),
        ),
        SCHEMA,
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("clinical.patients.id"), index=True)
    doctor_id: Mapped[str] = mapped_column(ForeignKey("clinical.doctors.id"))
    slot_id: Mapped[int] = mapped_column(ForeignKey("clinical.slots.id"))
    department: Mapped[str] = mapped_column(String(60))
    appointment_type: Mapped[str] = mapped_column(String(60))
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="scheduled")  # scheduled | cancelled | completed


class LabTest(Base):
    __tablename__ = "lab_tests"
    __table_args__ = SCHEMA

    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    unit: Mapped[str | None] = mapped_column(String(24))
    ref_low: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    ref_high: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)


class LabResult(Base):
    __tablename__ = "lab_results"
    __table_args__ = SCHEMA

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("clinical.patients.id"), index=True)
    test_code: Mapped[str] = mapped_column(ForeignKey("clinical.lab_tests.code"))
    value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    value_text: Mapped[str | None] = mapped_column(String(200))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = SCHEMA

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("clinical.patients.id"), index=True)
    test_code: Mapped[str] = mapped_column(ForeignKey("clinical.lab_tests.code"))
    procedure: Mapped[str | None] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(String(200))
    ordered_by: Mapped[str] = mapped_column(ForeignKey("clinical.doctors.id"))
    status: Mapped[str] = mapped_column(String(16))  # pending | completed
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class PreopRequirement(Base):
    """Structured form of the active pre-op protocol, linked to its source document for citation."""

    __tablename__ = "preop_requirements"
    __table_args__ = (UniqueConstraint("procedure", "test_code"), SCHEMA)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    procedure: Mapped[str] = mapped_column(String(120), index=True)
    test_code: Mapped[str] = mapped_column(ForeignKey("clinical.lab_tests.code"))
    valid_days: Mapped[int] = mapped_column(Integer)
    doc_id: Mapped[str] = mapped_column(String(40))
    doc_version: Mapped[str] = mapped_column(String(16))
    section: Mapped[str] = mapped_column(String(120))


class Medication(Base):
    __tablename__ = "medications"
    __table_args__ = SCHEMA

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("clinical.patients.id"), index=True)
    drug_name: Mapped[str] = mapped_column(String(80))
    dose: Mapped[str] = mapped_column(String(60))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DrugInteraction(Base):
    """Approved interaction reference. Pairs stored lower-cased with drug_a < drug_b."""

    __tablename__ = "drug_interactions"
    __table_args__ = (UniqueConstraint("drug_a", "drug_b"), SCHEMA)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    drug_a: Mapped[str] = mapped_column(String(80))
    drug_b: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(16))  # major | moderate | minor
    description: Mapped[str] = mapped_column(Text)


class KnownDrug(Base):
    """Drugs present in the approved reference; anything else is not_found, never 'safe'."""

    __tablename__ = "known_drugs"
    __table_args__ = SCHEMA

    name: Mapped[str] = mapped_column(String(80), primary_key=True)


class Surgery(Base):
    __tablename__ = "surgeries"
    __table_args__ = SCHEMA

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[str] = mapped_column(ForeignKey("clinical.patients.id"), index=True)
    procedure: Mapped[str] = mapped_column(String(120))
    surgeon_id: Mapped[str] = mapped_column(ForeignKey("clinical.doctors.id"))
    planned_date: Mapped[date] = mapped_column(Date)
    consent_signed: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="planned")


class ProcedureCost(Base):
    __tablename__ = "procedure_costs"
    __table_args__ = SCHEMA

    procedure: Mapped[str] = mapped_column(String(120), primary_key=True)
    base_cost: Mapped[Decimal] = mapped_column(Numeric(12, 2))
