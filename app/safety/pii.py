"""Step 4.4: mask sensitive identifiers in responses and audit records (R7)."""

import re

_RULES: list[tuple[re.Pattern[str], str]] = [
    # Insurance policy numbers, e.g. GLD-4471-2290 → GLD-****-2290
    (re.compile(r"\b([A-Z]{3})-\d{4}-(\d{4})\b"), r"\1-****-\2"),
    # Aadhaar-style 12-digit ids → keep last 4
    (re.compile(r"\b\d{4}[ -]?\d{4}[ -]?(\d{4})\b"), r"XXXX XXXX \1"),
    # Indian mobile numbers with optional +91 → keep last 4
    (re.compile(r"(?:\+?91[-\s]?)?\b\d{5}[-\s]?\d(\d{4})\b"), r"*****-*\1"),
    # Email addresses → keep domain
    (re.compile(r"\b[\w.+-]+@([\w-]+\.[\w.-]+)\b"), r"***@\1"),
    # Dates of birth when labelled as such
    (re.compile(r"(?i)\b(date of birth|dob|born on)(\W{0,3})\d{4}-\d{2}-\d{2}"), r"\1\2****-**-**"),
]


def mask_pii(text: str) -> str:
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


def redact_for_audit(text: str, limit: int = 500) -> str:
    masked = mask_pii(text)
    return masked if len(masked) <= limit else masked[:limit] + "…"
