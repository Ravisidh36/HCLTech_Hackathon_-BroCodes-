"""Output grounding check (R3): every number, time or date in an answer must come from the
evidence, the system data or the question itself."""

import re

_NUMBER_RE = re.compile(r"\d+(?:[.:,]\d+)*")


def _normalise(token: str) -> str:
    token = token.replace(",", "")
    return token[:-2] if token.endswith(".0") else token


def _numbers(text: str) -> set[str]:
    return {_normalise(t) for t in _NUMBER_RE.findall(text)}


def unsupported_numbers(answer: str, support: str) -> list[str]:
    allowed = _numbers(support)
    return sorted(n for n in _numbers(answer) if n not in allowed)
