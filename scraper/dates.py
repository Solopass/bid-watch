"""Parse the date formats that show up on town bid pages.

Stored values are either a calendar date (``YYYY-MM-DD``) or a zoned
datetime (``YYYY-MM-DDTHH:MM:SS±HH:MM``) in America/New_York. A date with
no clock time stays open through that calendar day.
"""

from __future__ import annotations

import calendar
import re
from datetime import datetime, date
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

_MONTHS = {}
for _index, _name in enumerate(calendar.month_name):
    if _name:
        _MONTHS[_name.lower()] = _index
for _index, _name in enumerate(calendar.month_abbr):
    if _name:
        _MONTHS[_name.lower()] = _index

_MONTH_PATTERN = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)"
)

_NUMERIC = re.compile(
    r"\b(\d{1,2})/(\d{1,2})/(\d{4})"
    r"(?:\s+(\d{1,2})(?::(\d{2}))?\s*([AaPp])\.?[Mm]\.?)?"
)
_MONTH_DAY = re.compile(
    rf"\b({_MONTH_PATTERN})\s+(\d{{1,2}}),\s*(\d{{4}})"
    rf"(?:\s+(?:at|by)\s+(\d{{1,2}})(?::(\d{{2}}))?\s*([AaPp])\.?[Mm]\.?)?",
    re.IGNORECASE,
)
_UNDATED = re.compile(
    r"upon\s+contract|until\s+contract|open\s+until\s+contracted|to\s+be\s+announced|tbd",
    re.IGNORECASE,
)


def _clock(hour: str | None, minute: str | None, ampm: str | None):
    if hour is None or ampm is None:
        return None
    hh = int(hour)
    mm = int(minute or 0)
    if hh > 12 or mm > 59:
        return None
    suffix = ampm.lower()
    if suffix == "p" and hh != 12:
        hh += 12
    if suffix == "a" and hh == 12:
        hh = 0
    if hh > 23:
        return None
    return hh, mm


def _iso(year: int, month: int, day: int, clock) -> str | None:
    if not (2020 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31):
        return None
    try:
        if clock is None:
            date(year, month, day)
            return f"{year:04d}-{month:02d}-{day:02d}"
        hour, minute = clock
        dt = datetime(year, month, day, hour, minute, tzinfo=EASTERN)
        return dt.isoformat(timespec="seconds")
    except ValueError:
        return None


def parse_when(text: str | None) -> str | None:
    """Return the first real date or datetime in ``text``, or None."""
    if not text:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned or _UNDATED.search(cleaned):
        # Still scan for an explicit calendar date hiding beside the phrase.
        if not (_NUMERIC.search(cleaned) or _MONTH_DAY.search(cleaned)):
            return None
    match = _NUMERIC.search(cleaned)
    if match:
        clock = _clock(match.group(4), match.group(5), match.group(6))
        found = _iso(int(match.group(3)), int(match.group(1)), int(match.group(2)), clock)
        if found:
            return found
    match = _MONTH_DAY.search(cleaned)
    if match:
        month = _MONTHS.get(match.group(1).lower())
        if month:
            clock = _clock(match.group(4), match.group(5), match.group(6))
            return _iso(int(match.group(3)), month, int(match.group(2)), clock)
    return None


def as_date(value: str | None) -> str | None:
    if not value:
        return None
    parsed = parse_when(value) if not re.match(r"\d{4}-\d{2}-\d{2}", value) else value
    if not parsed:
        return None
    return parsed[:10]


def is_past(value: str | None, now: datetime | None = None) -> bool:
    """True when a due value is already past. Date-only values last all day."""
    if not value:
        return False
    now = now or datetime.now(EASTERN)
    if now.tzinfo is None:
        now = now.replace(tzinfo=EASTERN)
    else:
        now = now.astimezone(EASTERN)
    if "T" in value:
        due = datetime.fromisoformat(value)
        if due.tzinfo is None:
            due = due.replace(tzinfo=EASTERN)
        return due < now
    return date.fromisoformat(value[:10]) < now.date()


def today_eastern(now: datetime | None = None) -> str:
    now = now or datetime.now(EASTERN)
    if now.tzinfo is None:
        now = now.replace(tzinfo=EASTERN)
    return now.astimezone(EASTERN).date().isoformat()


def format_due(value: str | None) -> str:
    """Human due string for the GitHub issue body."""
    if not value:
        return "due date not listed"
    months = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    day = date.fromisoformat(value[:10])
    label = f"{months[day.month - 1]} {day.day}, {day.year}"
    if "T" not in value:
        return label
    dt = datetime.fromisoformat(value).astimezone(EASTERN)
    hour = dt.hour % 12 or 12
    suffix = "AM" if dt.hour < 12 else "PM"
    return f"{label} {hour}:{dt.minute:02d} {suffix} ET"
