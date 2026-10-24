"""Phase 3: ingestion and retrieval against the indexed test database (loads BGE-M3 + reranker)."""

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.knowledge import Document
from app.rag import ingest as ingest_module
from app.rag.embed import check_embedding_model
from app.rag.ingest import DocumentMeta, IngestError, ingest_file
from app.rag.retrieve import Candidate, _keyword, _resolve, retrieve
from app.rag.search import search_policies
from app.schemas.common import NOT_FOUND_MESSAGE
from app.schemas.tools import ToolStatus
from app.services.common import today

pytestmark = pytest.mark.usefixtures("kb_indexed")

DEMO_DIR = get_settings().kb_dir.parent / "uploads_demo"
ICU_V3 = DocumentMeta(
    doc_id="POL-ICU-VISIT",
    title="ICU Visiting Policy",
    topic="icu_visiting",
    version="3.0",
    effective_date=date(2026, 10, 1),
    authority="hospital_policy",
)


def _docs(result):
    return {(e.doc_id, e.citation.version) for e in result.evidence}


# --- retrieval and policy resolution (R1–R4) ------------------------------------------------

def test_icu_hours_use_active_version_only(db):
    result = retrieve(db, "What are the visiting hours for ICU patients?")
    assert result.status == "ok"
    assert _docs(result) == {("POL-ICU-VISIT", "2.0")}
    assert "10:00 to 11:00" in result.evidence[0].content


def test_citation_has_document_section_version_date(db):
    citation = retrieve(db, "How do I schedule an MRI scan?").evidence[0].citation
    assert citation.document == "MRI Scan Scheduling Procedure"
    assert citation.section.startswith("2. Steps to Schedule an MRI")
    assert (citation.version, citation.effective_date) == ("2.1", date(2025, 11, 15))


def test_regulatory_advisory_overrides_hospital_policy(db):
    result = retrieve(db, "What are the visiting hours in general wards?")
    assert {doc for doc, _ in _docs(result)} == {"ADV-RESP-2026"}
    assert result.overrides and result.overrides[0].overridden == ["POL-VISIT-GEN v1.3"]


def test_governing_document_is_pulled_in_when_not_retrieved(db):
    """Even if only the overridden policy is retrieved, the advisory must govern the answer."""
    rows = db.execute(
        select(ingest_module.Chunk, Document).join(Document).where(Document.doc_id == "POL-VISIT-GEN")
    ).tuples().all()
    only_overridden = [Candidate(chunk, doc, score=5.0) for chunk, doc in rows]
    kept, conflicts, overrides = _resolve(db, "general ward visiting hours", today(), only_overridden)
    assert {c.document.doc_id for c in kept} == {"ADV-RESP-2026"} and not conflicts


def test_same_rank_documents_are_flagged_as_conflict(db):
    result = retrieve(db, "What are the pharmacy opening hours?")
    assert result.status == "conflict"
    assert result.conflicts[0].doc_ids == ["POL-PHARM-OPD", "POL-PHARM-SVC"]
    assert {doc for doc, _ in _docs(result)} == {"POL-PHARM-OPD", "POL-PHARM-SVC"}


def test_unsupported_question_is_not_found(db):
    assert retrieve(db, "What is the treatment for dragon pox?").status == "not_found"


def test_search_tool_returns_exact_not_found_message(db, ctx_for):
    result = search_policies(db, ctx_for("patient1"), "What is the cafeteria menu today?")
    assert result.status == ToolStatus.NOT_FOUND and result.message == NOT_FOUND_MESSAGE


def test_table_answer_is_retrieved(db):
    result = retrieve(db, "Is HbA1c required before knee replacement surgery?")
    assert any(e.doc_id == "PROT-ORTHO-KNEE" and "HBA1C" in e.content for e in result.evidence)


def test_keyword_channel_matches_exact_terms(db):
    chunk_ids = _keyword(db, "HbA1c", today(), 10)
    assert any(cid.startswith("PROT-ORTHO-KNEE:3.0") for cid in chunk_ids)


# --- ingestion (R8, R11) --------------------------------------------------------------------

def test_reingesting_same_file_is_skipped(db):
    path = get_settings().kb_dir / "mri-scheduling-procedure.md"
    meta = DocumentMeta(
        doc_id="SOP-RAD-MRI", title="x" * 5, topic="mri_scheduling", version="9.9",
        effective_date=date(2025, 1, 1), authority="department_sop",
    )
    assert ingest_file(db, path, meta, "admin1").status == "duplicate"


def test_live_upload_supersedes_and_changes_answer(db):
    result = ingest_file(db, DEMO_DIR / "icu-visiting-v3.md", ICU_V3, "admin1")
    assert result.status == "indexed" and result.superseded == ["2.0"]

    answer = retrieve(db, "What are the visiting hours for ICU patients?")
    assert _docs(answer) == {("POL-ICU-VISIT", "3.0")}
    assert "12:00 to 13:00" in answer.evidence[0].content


def test_duplicate_version_number_is_rejected(db):
    meta = ICU_V3.model_copy(update={"version": "2.0"})
    with pytest.raises(IngestError):
        ingest_file(db, DEMO_DIR / "icu-visiting-v3.md", meta, "admin1")


def test_malicious_document_metadata_comes_from_form_not_text(db):
    meta = DocumentMeta(
        doc_id="FAQ-BILLING", title="Patient Billing FAQ", topic="billing_faq", version="1.0",
        effective_date=date(2026, 10, 1), authority="department_sop",
    )
    result = ingest_file(db, DEMO_DIR / "malicious-billing-faq.md", meta, "admin1")
    assert result.injection_flags >= 1

    stored = db.scalars(select(Document).where(Document.doc_id == "FAQ-BILLING")).one()
    assert (stored.version, stored.authority) == ("1.0", "department_sop")  # not "99" / "regulatory"

    # Flagged chunks are quarantined: never retrieved. The rest of the document stays answerable.
    evidence = retrieve(db, "Are all surgeries fully covered by insurance?").evidence
    assert not any("IGNORE ALL PREVIOUS" in e.content or e.injection_flag for e in evidence)
    refunds = retrieve(db, "How long do refunds for cancelled procedures take?").evidence
    assert any(e.doc_id == "FAQ-BILLING" and "7 working days" in e.content for e in refunds)


def test_failed_ingest_leaves_no_rows(test_engine, monkeypatch):
    """Uses a real session (not the savepoint fixture) so the production rollback path is what's tested."""

    def explode(*args, **kwargs):
        raise RuntimeError("simulated failure while storing chunks")

    monkeypatch.setattr(ingest_module, "Chunk", explode)
    with Session(test_engine) as session:
        with pytest.raises(RuntimeError):
            ingest_file(session, DEMO_DIR / "icu-visiting-v3.md", ICU_V3, "admin1")
    with Session(test_engine) as session:
        versions = dict(session.execute(
            select(Document.version, Document.status).where(Document.doc_id == "POL-ICU-VISIT")
        ).tuples().all())
    assert versions == {"1.0": "superseded", "2.0": "active"}


def test_embedding_model_guard(db):
    db.scalars(select(Document)).first().embedding_model = "some/other-model"
    db.flush()
    with pytest.raises(RuntimeError, match="re-run|Re-run"):
        check_embedding_model(db)
