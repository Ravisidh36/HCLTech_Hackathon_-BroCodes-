from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.schemas.common import RequestContext

# Source-system names used in SystemCitations (§5.2).
SCHEDULING = "Hospital Scheduling System"
INSURANCE = "Insurance Verification System"
LAB = "Laboratory Information System"
EMR = "Electronic Medical Record System"
PHARMACY = "Pharmacy System (approved interaction reference)"
BILLING = "Billing System"


class NoPatientContext(Exception):
    """Raised when a patient-scoped tool is called without a patient identity."""


def require_patient(ctx: RequestContext) -> str:
    if not ctx.patient_id:
        raise NoPatientContext("This tool requires an authenticated patient.")
    return ctx.patient_id


def hospital_tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def now() -> datetime:
    return datetime.now(hospital_tz())


def today() -> date:
    return now().date()


def local_iso(dt: datetime) -> str:
    """Hospital-local 'YYYY-MM-DD HH:MM': readable for patients, still parseable by fromisoformat."""
    return dt.astimezone(hospital_tz()).strftime("%Y-%m-%d %H:%M")


def normalize(text: str) -> str:
    return " ".join(text.lower().split())
