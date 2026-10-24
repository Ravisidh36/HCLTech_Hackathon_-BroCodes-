"""Step 3.5: flag instruction-like text in documents (R8).

Flagging is for visibility only: flagged chunks are still treated purely as data, and nothing in a
document can change assistant behaviour or document metadata.
"""

import re

_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above|earlier) (instructions|rules|prompts)",
    r"disregard (all |any )?(previous|prior|above) ",
    r"\byou are now\b",
    r"\b(administrator|admin|developer|god) mode\b",
    r"^\s*(system|assistant)\s*:",
    r"\breveal (the |all )?(system prompt|instructions|patient|records|data)",
    r"\boverrides? (every|all) (other )?(policy|policies|instructions)",
    r"<\s*/?\s*(system|instructions?)\s*>",
]
_INJECTION_RE = re.compile("|".join(f"(?:{p})" for p in _PATTERNS), re.IGNORECASE | re.MULTILINE)


def looks_like_injection(text: str) -> bool:
    return bool(_INJECTION_RE.search(text))
