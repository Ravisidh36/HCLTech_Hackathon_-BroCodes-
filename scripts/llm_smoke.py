"""Phase 0 check: confirm each configured LLM provider answers.

Usage: .venv\\Scripts\\python.exe -m scripts.llm_smoke
"""

from app.config import get_settings
from app.llm.provider import _gemini, _groq

PROMPT = "Reply with exactly: OK"


def main() -> None:
    settings = get_settings()
    providers = {"groq": (_groq, settings.groq_model), "gemini": (_gemini, settings.gemini_model)}
    for name, (build, model) in providers.items():
        llm = build(0.0)
        if llm is None:
            print(f"[skip] {name}: no API key in .env")
            continue
        try:
            reply = llm.invoke(PROMPT).content
            print(f"[ok]   {name} ({model}): {str(reply).strip()[:60]}")
        except Exception as exc:
            print(f"[FAIL] {name} ({model}): {exc}")


if __name__ == "__main__":
    main()
