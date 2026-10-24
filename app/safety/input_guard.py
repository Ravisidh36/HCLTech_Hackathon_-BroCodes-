"""Step 4.3: deterministic input guard, run before any LLM sees the request.

Fail-closed: any error inside the guard refuses the request. Identity enforcement does not rely on
this guard (tools only ever read ctx.patient_id); the guard exists to refuse early and explainably.
"""

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.clinical import Patient
from app.rag.screen import looks_like_injection
from app.schemas.common import RequestContext

_PATIENT_ID_RE = re.compile(r"\bP\d{4}\b", re.IGNORECASE)
_THIRD_PARTY_RE = re.compile(
    r"\b(another|other|different|all|every)\s+(patient|patients|person|people|user|users)('s)?\b"
    r"|\b(my|his|her|their)\s+(wife|husband|son|daughter|mother|father|friend|neighbou?r|relative)('s)?\s+"
    r"(records?|results?|reports?|appointments?|labs?|tests?|medications?|insurance|bills?)\b",
    re.IGNORECASE,
)
_MEDICAL_ADVICE_RE = re.compile(
    r"\bshould i (stop|start|skip|double|increase|decrease|reduce|change|quit)\b"
    r"|\bcan i (stop|skip|double|quit) (taking|my)\b"
    r"|\b(what|which) (dose|dosage)\b|\bhow (much|many) .{0,30}should i take\b"
    r"|\b(diagnose|prescribe)\b"
    r"|\bdo i have (cancer|diabetes|covid|an? \w+ (disease|infection|disorder))\b"
    r"|\bwhat (disease|illness|condition) do i have\b",
    re.IGNORECASE,
)
_EXTRA_INJECTION_RE = re.compile(
    r"\b(system prompt|jailbreak|developer mode|pretend (you are|to be)|bypass (the )?(rules|guard|filters?))\b",
    re.IGNORECASE,
)

REFUSE_OTHER_PATIENT = (
    "I can only help with your own records. I can't access information about other patients."
)
REFUSE_MEDICAL_ADVICE = (
    "I can't give medical advice, diagnoses or dosing decisions. Please speak to your doctor or "
    "pharmacist. I can show your records, appointments and the hospital's published guidance."
)
REFUSE_INJECTION = "I can't follow instructions that try to change how this assistant works."
REFUSE_ERROR = "I couldn't safely process this request. Please rephrase it."


@dataclass
class GuardVerdict:
    allowed: bool
    category: str = "allowed"
    reason: str = ""
    checks: list[dict] = field(default_factory=list)


def _other_patient_names(db: Session, own_patient_id: str | None) -> list[str]:
    names = []
    for full_name in db.scalars(select(Patient.full_name).where(Patient.id != (own_patient_id or ""))):
        names.append(full_name)
        names.append(full_name.split()[0])
    return names


def _check(db: Session, ctx: RequestContext, question: str) -> GuardVerdict:
    checks: list[dict] = []

    foreign_ids = {pid.upper() for pid in _PATIENT_ID_RE.findall(question)} - {ctx.patient_id or ""}
    names = _other_patient_names(db, ctx.patient_id)
    named = [n for n in names if re.search(rf"\b{re.escape(n)}\b", question, re.IGNORECASE)]
    third_party = bool(_THIRD_PARTY_RE.search(question))
    other_patient = bool(foreign_ids or named or third_party)
    checks.append({"check": "other_patient_reference", "passed": not other_patient})
    if other_patient:
        return GuardVerdict(False, "other_patient", REFUSE_OTHER_PATIENT, checks)

    injection = looks_like_injection(question) or bool(_EXTRA_INJECTION_RE.search(question))
    checks.append({"check": "prompt_injection", "passed": not injection})
    if injection:
        return GuardVerdict(False, "prompt_injection", REFUSE_INJECTION, checks)

    advice = bool(_MEDICAL_ADVICE_RE.search(question))
    checks.append({"check": "medical_advice_request", "passed": not advice})
    if advice:
        return GuardVerdict(False, "medical_advice", REFUSE_MEDICAL_ADVICE, checks)

    return GuardVerdict(True, checks=checks)


def check_input(db: Session, ctx: RequestContext, question: str) -> GuardVerdict:
    try:
        return _check(db, ctx, question)
    except Exception:
        return GuardVerdict(False, "guard_error", REFUSE_ERROR, [{"check": "guard_error", "passed": False}])
