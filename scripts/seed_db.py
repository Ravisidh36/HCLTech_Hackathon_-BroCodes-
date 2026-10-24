"""Load synthetic clinical and user data. Dates are relative to today so demos never go stale.

Usage: .venv\\Scripts\\python.exe -m scripts.seed_db [--test]
Run scripts.reset_db first; this script expects empty tables.
"""

import argparse
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import bcrypt
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import make_engine
from app.models import clinical as c
from app.models import platform as p


def _load(seed_dir: Path, name: str) -> dict:
    return json.loads((seed_dir / name).read_text(encoding="utf-8"))


def _at(day: int, hhmm: str, today: date, tz: ZoneInfo) -> datetime:
    return datetime.combine(today + timedelta(days=day), time.fromisoformat(hhmm), tzinfo=tz)


def seed(url: URL, today: date | None = None) -> None:
    settings = get_settings()
    tz = ZoneInfo(settings.timezone)
    today = today or datetime.now(tz).date()
    now = datetime.now(tz)

    people = _load(settings.seed_dir, "people.json")
    insurance = _load(settings.seed_dir, "insurance.json")
    scheduling = _load(settings.seed_dir, "scheduling.json")
    clinical = _load(settings.seed_dir, "clinical.json")

    engine = make_engine(url)
    with Session(engine) as db, db.begin():
        db.add_all(c.InsurancePlan(**plan) for plan in people["insurance_plans"])
        db.flush()
        db.add_all(
            c.Patient(
                **{k: v for k, v in pt.items() if k not in ("date_of_birth", "plan_start_date")},
                date_of_birth=date.fromisoformat(pt["date_of_birth"]),
                plan_start_date=date.fromisoformat(pt["plan_start_date"]),
            )
            for pt in people["patients"]
        )
        db.add_all(c.CoverageRule(**rule) for rule in insurance["coverage_rules"])
        db.add_all(
            c.PreAuthorization(
                patient_id=pa["patient_id"],
                procedure=pa["procedure"],
                status=pa["status"],
                valid_until=today + timedelta(days=pa["valid_days"]),
            )
            for pa in insurance["preauthorizations"]
        )
        db.add_all(c.ProcedureCost(**pc) for pc in insurance["procedure_costs"])

        # Scheduling: generate slots, then book the ones that hold seeded appointments.
        db.add_all(c.Doctor(**doc) for doc in scheduling["doctors"])
        db.flush()
        slots: dict[tuple[str, datetime], c.Slot] = {}
        for rule in scheduling["slot_rules"]:
            for day in rule["days"]:
                for hhmm in rule["times"]:
                    start = _at(day, hhmm, today, tz)
                    slot = c.Slot(
                        doctor_id=rule["doctor_id"],
                        start_time=start,
                        end_time=start + timedelta(minutes=rule["duration_min"]),
                        slot_type=rule["slot_type"],
                        is_booked=False,
                    )
                    slots[(rule["doctor_id"], start)] = slot
                    db.add(slot)
        db.flush()
        for appt in scheduling["appointments"]:
            start = _at(appt["day"], appt["time"], today, tz)
            slot = slots[(appt["doctor_id"], start)]
            slot.is_booked = True
            db.add(
                c.Appointment(
                    patient_id=appt["patient_id"],
                    doctor_id=appt["doctor_id"],
                    slot_id=slot.id,
                    department=appt["department"],
                    appointment_type=appt["appointment_type"],
                    start_time=start,
                    status="scheduled",
                )
            )
        db.add_all(
            c.Surgery(
                patient_id=s["patient_id"],
                procedure=s["procedure"],
                surgeon_id=s["surgeon_id"],
                planned_date=today + timedelta(days=s["day"]),
                consent_signed=s["consent_signed"],
            )
            for s in scheduling["surgeries"]
        )

        # Lab, orders, pre-op protocol requirements, pharmacy.
        db.add_all(c.LabTest(**t) for t in clinical["lab_tests"])
        db.flush()
        db.add_all(
            c.LabResult(
                patient_id=r["patient_id"],
                test_code=r["test_code"],
                value=r.get("value"),
                value_text=r.get("value_text"),
                collected_at=now - timedelta(days=r["days_ago"]),
            )
            for r in clinical["lab_results"]
        )
        db.add_all(
            c.Order(
                **{k: v for k, v in o.items() if k != "days_ago"},
                ordered_at=now - timedelta(days=o["days_ago"]),
            )
            for o in clinical["orders"]
        )
        db.add_all(c.PreopRequirement(**req) for req in clinical["preop_requirements"])
        db.add_all(c.KnownDrug(name=name) for name in clinical["known_drugs"])
        db.add_all(c.DrugInteraction(**i) for i in clinical["drug_interactions"])
        db.add_all(c.Medication(**m) for m in clinical["medications"])

        # Users last: they reference patients.
        db.add_all(
            p.User(
                username=u["username"],
                password_hash=bcrypt.hashpw(u["password"].encode(), bcrypt.gensalt()).decode(),
                full_name=u["full_name"],
                role=u["role"],
                patient_id=u["patient_id"],
            )
            for u in people["users"]
        )
    engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="seed the test database instead")
    args = parser.parse_args()

    settings = get_settings()
    url = settings.test_database_url if args.test else settings.owner_database_url
    seed(url)
    print(f"Seed complete: {url.database}")


if __name__ == "__main__":
    main()
