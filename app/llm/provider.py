"""
Single entry point for chat models.

The primary provider comes from LLM_PROVIDER; the other provider is attached as a
fallback when its API key is present, so a rate limit or outage on one provider
does not take the assistant down.
"""

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables import Runnable

from app.config import get_settings


def _groq(temperature: float) -> BaseChatModel | None:
    settings = get_settings()
    if not settings.groq_api_key:
        return None
    from langchain_groq import ChatGroq

    return ChatGroq(
        model=settings.groq_model,
        api_key=settings.groq_api_key,
        temperature=temperature,
        max_retries=1,
        timeout=30,
    )


def _gemini(temperature: float) -> BaseChatModel | None:
    settings = get_settings()
    if not settings.gemini_api_key:
        return None
    from langchain_google_genai import ChatGoogleGenerativeAI

    return ChatGoogleGenerativeAI(
        model=settings.gemini_model,
        google_api_key=settings.gemini_api_key,
        temperature=temperature,
        # Planner and composer work from structured evidence; extended thinking only adds latency.
        thinking_budget=0,
        max_retries=1,
        timeout=30,
    )


_BUILDERS = {"groq": _groq, "gemini": _gemini}


def model_label() -> str:
    """Identifier of the model that serves requests first, recorded in the audit log (R10)."""
    settings = get_settings()
    labels = {"groq": f"groq:{settings.groq_model}", "gemini": f"gemini:{settings.gemini_model}"}
    keys = {"groq": settings.groq_api_key, "gemini": settings.gemini_api_key}
    other = "gemini" if settings.llm_provider == "groq" else "groq"
    return labels[settings.llm_provider] if keys[settings.llm_provider] else labels[other]


def get_structured_model(schema: type, temperature: float = 0.0) -> Runnable:
    """Model that returns {"raw": AIMessage, "parsed": schema | None, "parsing_error": ...}.

    include_raw keeps token usage available for the audit log.
    """
    settings = get_settings()
    order = [settings.llm_provider, "gemini" if settings.llm_provider == "groq" else "groq"]
    models = [m for m in (_BUILDERS[name](temperature) for name in order) if m is not None]
    if not models:
        raise RuntimeError("No LLM API key configured. Set GROQ_API_KEY or GEMINI_API_KEY in .env.")
    runnables = [m.with_structured_output(schema, include_raw=True) for m in models]
    return runnables[0].with_fallbacks(runnables[1:]) if len(runnables) > 1 else runnables[0]


def get_chat_model(temperature: float = 0.0) -> Runnable:
    settings = get_settings()
    if settings.llm_provider not in _BUILDERS:
        raise ValueError(f"Unknown LLM_PROVIDER '{settings.llm_provider}'. Use groq or gemini.")

    primary = _BUILDERS[settings.llm_provider](temperature)
    fallback_name = "gemini" if settings.llm_provider == "groq" else "groq"
    fallback = _BUILDERS[fallback_name](temperature)

    if primary is None and fallback is None:
        raise RuntimeError("No LLM API key configured. Set GROQ_API_KEY or GEMINI_API_KEY in .env.")
    if primary is None:
        return fallback
    if fallback is None:
        return primary
    return primary.with_fallbacks([fallback])
