"""Step 2.5: medication interaction check against the approved reference table.

Drugs missing from the reference are reported as unknown, never as safe, and the absence of a
listed interaction is never phrased as a safety guarantee.
"""

from itertools import combinations

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.models.clinical import DrugInteraction, KnownDrug, Medication
from app.schemas.common import RequestContext
from app.schemas.tools import ToolResult
from app.services.common import PHARMACY, normalize, require_patient

SAFETY_NOTE = (
    "Not finding an interaction in the approved reference does not mean a combination is safe. "
    "Confirm any medication change with your doctor or pharmacist."
)


def drug_interaction_check(db: Session, ctx: RequestContext, medication_list: list[str] | None = None) -> ToolResult:
    """Check proposed medicines together with the patient's current active medicines."""
    tool = "drug_interaction_check"
    patient_id = require_patient(ctx)

    current = list(
        db.scalars(
            select(Medication.drug_name).where(Medication.patient_id == patient_id, Medication.active.is_(True))
        )
    )
    proposed = [normalize(m) for m in (medication_list or []) if m.strip()]
    drugs = list(dict.fromkeys(proposed + [normalize(m) for m in current]))

    known = set(db.scalars(select(KnownDrug.name)))
    unknown = [d for d in drugs if d not in known]
    checked = [d for d in drugs if d in known]

    if len(checked) < 2:
        message = "At least two medicines from the approved reference are needed for an interaction check."
        if unknown:
            message = f"Not in the approved medication reference: {', '.join(unknown)}. " + message
        return ToolResult.not_found(tool, PHARMACY, message)

    pairs = [tuple(sorted(pair)) for pair in combinations(checked, 2)]
    interactions = db.scalars(
        select(DrugInteraction).where(tuple_(DrugInteraction.drug_a, DrugInteraction.drug_b).in_(pairs))
    ).all()

    return ToolResult.ok(
        tool,
        PHARMACY,
        {
            "checked": checked,
            "current_medications": current,
            "proposed": proposed,
            "unknown": unknown,
            "interactions": [
                {"drug_a": i.drug_a, "drug_b": i.drug_b, "severity": i.severity, "description": i.description}
                for i in interactions
            ],
            "note": SAFETY_NOTE,
        },
    )
