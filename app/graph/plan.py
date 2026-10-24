"""Structured output contracts for the two LLM nodes (Supervisor and Composer).

Task arguments are explicit typed fields, never a free-form dict, so the planner cannot invent
parameters. There is deliberately no patient id field anywhere: identity comes only from ctx (R7).
"""

import json
from typing import Literal

from pydantic import BaseModel, Field

SpecialistName = Literal[
    "knowledge", "appointments", "insurance", "labs", "medication", "care_coordination", "billing"
]


class Task(BaseModel):
    specialist: SpecialistName
    query: str | None = Field(None, description="knowledge: a standalone search query for hospital policy")
    procedure: str | None = Field(None, description="insurance, billing, care_coordination: procedure name")
    test: str | None = Field(None, description="labs: the lab test name as the patient said it")
    department: str | None = Field(None, description="appointments: department, e.g. Cardiology")
    action: Literal["view", "reschedule"] | None = Field(None, description="appointments: view or reschedule")
    appointment_id: int | None = Field(None, description="appointments: id from an earlier result, if known")
    slot_preference: str | None = Field(
        None,
        description="appointments: the patient's own words for the new slot they chose, e.g. 'the second one' "
        "or 'Tuesday 11:30'. Copy their words; do not invent ids.",
    )
    medications: list[str] | None = Field(None, description="medication: medicines the patient asks about")

    def key(self) -> str:
        """Canonical identity of a task, used to stop the planner repeating work."""
        return json.dumps(self.model_dump(exclude_none=True), sort_keys=True)


class SupervisorPlan(BaseModel):
    tasks: list[Task] = Field(default_factory=list, description="Specialists to run now (at most 3).")
    final_step: bool = Field(
        True,
        description="True if these tasks' results will be enough to answer. False only if you must see "
        "their results before choosing further tasks.",
    )
    needs_clarification: bool = Field(False, description="True if the request is too ambiguous to act on.")
    question_for_user: str = Field("", description="The clarifying question, when needs_clarification is true.")


class ComposerOutput(BaseModel):
    answer: str = Field(description="Answer for the patient, using only the supplied evidence and data.")
    explanation: str = Field(description="One or two sentences on where the answer came from.")
    assumptions: list[str] = Field(default_factory=list, description="Assumptions made, if any.")
    cited_evidence: list[str] = Field(default_factory=list, description="Labels of evidence used, e.g. ['E1'].")
