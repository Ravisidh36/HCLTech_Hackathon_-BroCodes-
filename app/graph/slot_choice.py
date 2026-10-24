"""Match a patient's free-text slot choice ("the second one", "Tuesday 11:30") to one offered slot.

Deterministic on purpose: the planner never sees or invents slot ids. Ambiguous or unmatched
choices return None so the assistant asks again instead of guessing.
"""

import re
from datetime import datetime

_ORDINALS = {
    "first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2,
    "fourth": 3, "4th": 3, "fifth": 4, "5th": 4, "last": -1,
}
_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")  # 2026-10-29, as shown in the slot list
_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-]\d{2,4})?\b")  # 29/10 or 29-10-2026
_MONTH_DATE_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)")


def _time_in(text: str) -> str | None:
    """'11:30', '11.30', '3 pm', '3:00pm' → 'HH:MM' (24h). Bare numbers (e.g. date parts) are not times."""
    for m in re.finditer(r"\b(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?\b", text):
        if m.group(2) is None and m.group(3) is None:
            continue
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        if m.group(3) == "am" and hour == 12:
            hour = 0
        if hour < 24 and minute < 60:
            return f"{hour:02d}:{minute:02d}"
    return None


def _day_in(text: str) -> tuple[int, int | None] | None:
    """(day, month or None) from '2026-10-29', '29/10' or '29 oct'."""
    if m := _ISO_DATE_RE.search(text):
        return int(m.group(3)), int(m.group(2))
    if m := _NUMERIC_DATE_RE.search(text):
        return int(m.group(1)), int(m.group(2))
    if m := _MONTH_DATE_RE.search(text):
        return int(m.group(1)), None
    return None


def match_slot(preference: str, options: list[dict]) -> int | None:
    """Return the slot_id the preference uniquely identifies, else None."""
    if not options:
        return None
    text = preference.lower()

    for word, index in _ORDINALS.items():
        if re.search(rf"\b{word}\b", text):
            return options[index]["slot_id"] if -len(options) <= index < len(options) else None
    number = re.fullmatch(r"\s*(?:option|slot|number|no\.?)?\s*#?(\d)\s*", text)
    if number and 1 <= int(number.group(1)) <= len(options):
        return options[int(number.group(1)) - 1]["slot_id"]

    # Remove the date before looking for a time, so "2026-10-29 13:00" isn't read as 10 o'clock.
    date_part = _day_in(text)
    wanted_time = _time_in(_NUMERIC_DATE_RE.sub(" ", _ISO_DATE_RE.sub(" ", text)))
    wanted_day = next((d for d in _WEEKDAYS if d in text), None)
    if not (wanted_time or wanted_day or date_part):
        return None

    matches = []
    for option in options:
        start = datetime.fromisoformat(option["start_time"])
        if wanted_time and start.strftime("%H:%M") != wanted_time:
            continue
        if wanted_day and _WEEKDAYS[start.weekday()] != wanted_day:
            continue
        if date_part and (start.day != date_part[0] or (date_part[1] and start.month != date_part[1])):
            continue
        matches.append(option["slot_id"])
    return matches[0] if len(matches) == 1 else None
