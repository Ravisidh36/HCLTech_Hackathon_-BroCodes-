from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class AnswerType(StrEnum):
    """R9 / §6 answer classification. Assigned by code, never by the LLM."""

    RETRIEVED_FACT = "retrieved_fact"
    CALCULATED = "calculated"
    ACTION_COMPLETED = "action_completed"
    CLARIFICATION_NEEDED = "clarification_needed"
    NOT_FOUND = "not_found"
    REFUSED = "refused"
    CONFLICT_FLAGGED = "conflict_flagged"


# Exact wording from §3 of the use case.
NOT_FOUND_MESSAGE = "I could not find this information in the authorised hospital sources."


class Role(StrEnum):
    PATIENT = "patient"
    ADMIN = "admin"


class Authority(StrEnum):
    REGULATORY = "regulatory"
    CLINICAL_GUIDELINE = "clinical_guideline"
    HOSPITAL_POLICY = "hospital_policy"
    DEPARTMENT_SOP = "department_sop"


# R4 hierarchy: lower rank wins. Stated as a design assumption in the README.
AUTHORITY_RANK: dict[Authority, int] = {
    Authority.REGULATORY: 1,
    Authority.CLINICAL_GUIDELINE: 2,
    Authority.HOSPITAL_POLICY: 3,
    Authority.DEPARTMENT_SOP: 4,
}


class DocStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DRAFT = "draft"


class RequestContext(BaseModel):
    """Trusted identity for one request, built from the JWT. Never from the LLM (R7)."""

    model_config = ConfigDict(frozen=True)

    user_id: int
    username: str
    role: Role
    patient_id: str | None
    trace_id: str
