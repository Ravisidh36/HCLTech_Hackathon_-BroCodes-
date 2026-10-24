"""The search_policies tool: the only way the assistant reaches hospital documents (R1)."""

from sqlalchemy.orm import Session

from app.rag.retrieve import retrieve
from app.schemas.common import NOT_FOUND_MESSAGE, RequestContext
from app.schemas.tools import ToolResult

KNOWLEDGE_BASE = "Hospital Knowledge Base (approved documents)"


def search_policies(db: Session, ctx: RequestContext, query: str) -> ToolResult:
    tool = "search_policies"
    result = retrieve(db, query)
    if result.status == "not_found":
        return ToolResult.not_found(tool, KNOWLEDGE_BASE, NOT_FOUND_MESSAGE)
    return ToolResult.ok(
        tool,
        KNOWLEDGE_BASE,
        {
            "evidence": [e.model_dump(mode="json") for e in result.evidence],
            "conflict": result.status == "conflict",
            "conflicts": [{"topic": c.topic, "doc_ids": c.doc_ids} for c in result.conflicts],
            "overrides": [
                {"topic": o.topic, "applied": o.applied, "overridden": o.overridden, "reason": o.reason}
                for o in result.overrides
            ],
            "top_score": result.top_score,
        },
    )
