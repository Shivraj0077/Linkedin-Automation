"""
Best-effort normalization of LinkedIn's free-text date renderings into
ISO-ish strings. LinkedIn renders dates as plain text inside RSC leaf
nodes (e.g. "Jan 2024", "2024", "Present") rather than structured
{month, year} objects in the captures we have, so this is text parsing,
not a guaranteed-exact conversion. Never raises on unparseable input —
returns None and lets the caller decide what to do.
"""
import re
from typing import Optional

_MONTHS = {
    "jan": "01", "january": "01",
    "feb": "02", "february": "02",
    "mar": "03", "march": "03",
    "apr": "04", "april": "04",
    "may": "05",
    "jun": "06", "june": "06",
    "jul": "07", "july": "07",
    "aug": "08", "august": "08",
    "sep": "09", "sept": "09", "september": "09",
    "oct": "10", "october": "10",
    "nov": "11", "november": "11",
    "dec": "12", "december": "12",
}

_MONTH_YEAR_RE = re.compile(r"^([A-Za-z]+)\.?\s+(\d{4})$")
_YEAR_RE = re.compile(r"^(\d{4})$")


def normalize_date(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    raw = raw.strip()
    if not raw or raw.lower() == "present":
        return None

    m = _MONTH_YEAR_RE.match(raw)
    if m:
        month_name, year = m.groups()
        month = _MONTHS.get(month_name.lower())
        if month:
            return f"{year}-{month}"

    m = _YEAR_RE.match(raw)
    if m:
        return m.group(1)

    # Unrecognized format — return as-is rather than dropping the signal.
    return raw


def is_present(raw: Optional[str]) -> bool:
    return bool(raw) and raw.strip().lower() == "present"


# LinkedIn renders ranges with an en-dash or hyphen, e.g.
# "Jul 2024 – Jul 2028" (confirmed in the real education-component
# capture) or "Jan 2024 - Present".
_RANGE_RE = re.compile(r"^(.+?)\s*[\u2013\u2014-]\s*(.+)$")


def split_date_range(raw: Optional[str]):
    """Returns (start_date, end_date) as normalized strings, or
    (None, None) if `raw` doesn't look like a date range at all
    (callers should treat that as 'not a date line', not an error)."""
    if not raw:
        return None, None
    m = _RANGE_RE.match(raw.strip())
    if not m:
        return None, None
    start_raw, end_raw = m.groups()
    start = normalize_date(start_raw)
    end = None if is_present(end_raw) else normalize_date(end_raw)
    # Guard against false positives (e.g. "Full-Stack Development" would
    # not match _MONTH_YEAR_RE/_YEAR_RE and normalize_date would return
    # it unchanged) — only accept if at least one side looks date-shaped.
    if not (_MONTH_YEAR_RE.match(start_raw.strip()) or _YEAR_RE.match(start_raw.strip())):
        return None, None
    return start, end
