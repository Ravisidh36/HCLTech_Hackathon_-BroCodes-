from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class DocCitation(BaseModel):
    """R2: document, section, version and effective date (page where available)."""

    kind: Literal["document"] = "document"
    document: str
    doc_id: str
    section: str
    version: str
    effective_date: date
    page: int | None = None


class SystemCitation(BaseModel):
    """Citation for data that came from a hospital system through a tool (§8 example)."""

    kind: Literal["system"] = "system"
    system: str


Citation = Annotated[DocCitation | SystemCitation, Field(discriminator="kind")]


class Evidence(BaseModel):
    """One retrieved, version-checked chunk. Content is untrusted data (R8)."""

    chunk_id: str
    doc_id: str
    topic: str
    authority: str
    content: str
    score: float
    injection_flag: bool = False
    citation: DocCitation
