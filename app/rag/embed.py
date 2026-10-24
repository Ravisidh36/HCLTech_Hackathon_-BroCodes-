"""Step 3.1: BGE-M3 embeddings, loaded once per process, plus the embedding-model guard."""

from functools import lru_cache

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.knowledge import Document


@lru_cache
def get_embedder():
    settings = get_settings()  # sets HF_HOME / offline flags before the import below
    from langchain_huggingface import HuggingFaceEmbeddings

    # Normalized vectors: a dot product equals cosine similarity, which BGE-M3 is trained for.
    return HuggingFaceEmbeddings(
        model_name=settings.embedding_model,
        encode_kwargs={"normalize_embeddings": True},
    )


def embed_texts(texts: list[str]) -> list[list[float]]:
    return get_embedder().embed_documents(texts)


def embed_query(text: str) -> np.ndarray:
    return np.asarray(get_embedder().embed_query(text), dtype=np.float32)


def check_embedding_model(db: Session) -> None:
    """Refuse to run against vectors produced by a different model (they live in another space)."""
    expected = get_settings().embedding_model
    stored = set(db.scalars(select(Document.embedding_model).distinct()))
    mismatched = stored - {expected}
    if mismatched:
        raise RuntimeError(
            f"Knowledge base was embedded with {sorted(mismatched)} but the configured model is "
            f"'{expected}'. Re-run scripts.build_index after resetting the knowledge tables."
        )
