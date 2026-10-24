"""Steps 3.8–3.10: hybrid retrieval → rerank → version/conflict resolution → relevance threshold.

Every decision about which policy applies is made here in code (R4), not by the LLM.
"""

from dataclasses import dataclass, field
from datetime import date

import numpy as np
from sqlalchemy import Text, cast, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.knowledge import Chunk, Document
from app.rag.embed import embed_query
from app.rag.rerank import rerank_scores
from app.schemas.citations import DocCitation, Evidence
from app.schemas.common import DocStatus
from app.services.common import today

RRF_K = 60  # standard Reciprocal Rank Fusion constant


@dataclass
class Candidate:
    chunk: Chunk
    document: Document
    fused: float = 0.0
    score: float = 0.0


@dataclass
class Conflict:
    topic: str
    doc_ids: list[str]


@dataclass
class Override:
    topic: str
    applied: str
    overridden: list[str]
    reason: str


@dataclass
class RetrievalResult:
    status: str  # ok | not_found | conflict
    evidence: list[Evidence] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    overrides: list[Override] = field(default_factory=list)
    top_score: float | None = None


def _effective_filter(as_of: date):
    return (Document.status == DocStatus.ACTIVE.value) & (Document.effective_date <= as_of)


def _dense(db: Session, query: str, as_of: date, limit: int) -> list[str]:
    rows = db.execute(select(Chunk.id, Chunk.embedding).join(Document).where(_effective_filter(as_of))).all()
    if not rows:
        return []
    matrix = np.asarray([r.embedding for r in rows], dtype=np.float32)
    scores = matrix @ embed_query(query)  # normalized vectors: dot product = cosine similarity
    order = np.argsort(-scores)[:limit]
    return [rows[i].id for i in order]


def _keyword(db: Session, query: str, as_of: date, limit: int) -> list[str]:
    # OR the query's lexemes so a natural-language question still matches (plainto_tsquery ANDs them).
    tsq = func.to_tsquery("english", func.replace(cast(func.plainto_tsquery("english", query), Text), "&", "|"))
    rank = func.ts_rank(Chunk.tsv, tsq)
    stmt = (
        select(Chunk.id)
        .join(Document)
        .where(_effective_filter(as_of), Chunk.tsv.op("@@")(tsq))
        .order_by(rank.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def _precedence(doc: Document) -> tuple:
    """R4 order: authority (lower rank wins) → latest effective date → highest version."""
    return (doc.authority_rank, -doc.effective_date.toordinal(), tuple(-p for p in _version_key(doc.version)))


def _passage(c: Candidate) -> str:
    # Title + section give the reranker the context a bare chunk (especially a table) lacks.
    return f"{c.document.title} | {c.chunk.section_path}\n{c.chunk.content}"


def _best_chunks(db: Session, query: str, docs: list[Document], per_doc: int = 2) -> list[Candidate]:
    """Pull in the most relevant chunks of governing documents that retrieval did not surface."""
    rows = db.execute(select(Chunk, Document).join(Document).where(Document.id.in_([d.id for d in docs]))).tuples().all()
    candidates = [Candidate(chunk, doc) for chunk, doc in rows]
    for c, s in zip(candidates, rerank_scores(query, [_passage(c) for c in candidates])):
        c.score = s
    min_score = get_settings().rag_min_score
    picked: list[Candidate] = []
    for doc in docs:
        own = sorted((c for c in candidates if c.document.id == doc.id), key=lambda c: c.score, reverse=True)
        # Always keep the governing document's best chunk; further chunks must clear the threshold.
        picked.extend(own[:1] + [c for c in own[1:per_doc] if c.score >= min_score])
    return picked


def _resolve(
    db: Session, query: str, as_of: date, relevant: list[Candidate]
) -> tuple[list[Candidate], list[Conflict], list[Override]]:
    """Apply R4 per topic, over every currently effective document on that topic, not only retrieved ones.

    Otherwise a governing document that happened to score low (e.g. a regulatory advisory) would be
    silently ignored in favour of the policy it overrides.
    """
    by_topic: dict[str, list[Candidate]] = {}
    for c in relevant:
        by_topic.setdefault(c.document.topic, []).append(c)

    kept: list[Candidate] = []
    conflicts: list[Conflict] = []
    overrides: list[Override] = []
    for topic, group in by_topic.items():
        effective = db.scalars(select(Document).where(Document.topic == topic, _effective_filter(as_of))).all()
        ranked = sorted(effective, key=_precedence)
        winner = ranked[0]
        governing = [
            d for d in ranked if d.authority_rank == winner.authority_rank and d.effective_date == winner.effective_date
        ]
        if len({d.doc_id for d in governing}) > 1:
            # Different documents with the same authority and date: cannot be resolved automatically.
            conflicts.append(Conflict(topic, sorted({d.doc_id for d in governing})))
        else:
            governing = [winner]
            losers = [d for d in ranked if d.id != winner.id]
            if losers:
                overrides.append(
                    Override(
                        topic,
                        f"{winner.doc_id} v{winner.version}",
                        [f"{d.doc_id} v{d.version}" for d in losers],
                        f"{winner.title} applies: authority '{winner.authority}', effective {winner.effective_date}.",
                    )
                )
        governing_ids = {d.id for d in governing}
        retrieved = [c for c in group if c.document.id in governing_ids]
        missing = [d for d in governing if d.id not in {c.document.id for c in retrieved}]
        kept.extend(retrieved + (_best_chunks(db, query, missing) if missing else []))

    kept.sort(key=lambda c: c.score, reverse=True)
    return kept, conflicts, overrides


def _to_evidence(c: Candidate) -> Evidence:
    return Evidence(
        chunk_id=c.chunk.id,
        doc_id=c.document.doc_id,
        topic=c.document.topic,
        authority=c.document.authority,
        content=c.chunk.content,
        score=round(c.score, 3),
        injection_flag=c.chunk.injection_flag,
        citation=DocCitation(
            document=c.document.title,
            doc_id=c.document.doc_id,
            section=c.chunk.section_path,
            version=c.document.version,
            effective_date=c.document.effective_date,
            page=c.chunk.page,
        ),
    )


def retrieve(db: Session, query: str, as_of: date | None = None) -> RetrievalResult:
    settings = get_settings()
    as_of = as_of or today()

    fused: dict[int, float] = {}
    for ranking in (
        _dense(db, query, as_of, settings.rag_candidates),
        _keyword(db, query, as_of, settings.rag_candidates),
    ):
        for rank, chunk_id in enumerate(ranking):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank + 1)
    if not fused:
        return RetrievalResult("not_found")

    rows = db.execute(select(Chunk, Document).join(Document).where(Chunk.id.in_(fused))).tuples().all()
    candidates = [Candidate(chunk, doc, fused[chunk.id]) for chunk, doc in rows]
    scores = rerank_scores(query, [_passage(c) for c in candidates])
    for c, s in zip(candidates, scores):
        c.score = s
    candidates.sort(key=lambda c: c.score, reverse=True)

    top_score = candidates[0].score
    relevant = [c for c in candidates if c.score >= settings.rag_min_score][: settings.rag_top_k]
    if not relevant:
        return RetrievalResult("not_found", top_score=top_score)

    # Keep only topics that are close in relevance to the best one; a question about ICU visiting
    # should not pull in the general-ward advisory just because both mention visiting hours.
    topic_best: dict[str, float] = {}
    for c in relevant:
        topic_best[c.document.topic] = max(topic_best.get(c.document.topic, c.score), c.score)
    relevant = [c for c in relevant if topic_best[c.document.topic] >= top_score - settings.rag_topic_gap]

    kept, conflicts, overrides = _resolve(db, query, as_of, relevant)
    return RetrievalResult(
        "conflict" if conflicts else "ok",
        evidence=[_to_evidence(c) for c in kept],
        conflicts=conflicts,
        overrides=overrides,
        top_score=top_score,
    )
