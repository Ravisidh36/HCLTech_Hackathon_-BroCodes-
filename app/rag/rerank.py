from functools import lru_cache

from app.config import get_settings


@lru_cache
def get_reranker():
    """Cross-encoder (from Argon): scores query and passage together, more precise than embeddings."""
    settings = get_settings()
    from sentence_transformers import CrossEncoder

    return CrossEncoder(settings.rerank_model)


def rerank_scores(query: str, passages: list[str]) -> list[float]:
    if not passages:
        return []
    return [float(s) for s in get_reranker().predict([(query, p) for p in passages])]
