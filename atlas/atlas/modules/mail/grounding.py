"""Keeping a model's extraction honest: every fact must be in the email.

The local model reads a booking confirmation or a receipt and answers in
JSON. It can misread, and a small model will sometimes produce a plausible
confirmation code or amount that is not in the text at all. A code he types
at an airline desk, or a charge he acts on, cannot be one the model made up.

So nothing extracted is kept on the model's word. A code, a flight number,
an amount, a place must appear in the email text itself; a date or a time
must appear in one of the usual ways of writing it. Anything that does not
is dropped to None, and None is shown as "unknown" -- never filled in
(invariant 6).
"""
from __future__ import annotations

import json
import re
from datetime import date

MONTHS = {
    1: ("january", "jan", "janvier", "janv"),
    2: ("february", "feb", "février", "fevrier", "févr", "fevr"),
    3: ("march", "mar", "mars"),
    4: ("april", "apr", "avril", "avr"),
    5: ("may", "mai"),
    6: ("june", "jun", "juin"),
    7: ("july", "jul", "juillet", "juil"),
    8: ("august", "aug", "août", "aout"),
    9: ("september", "sept", "sep", "septembre"),
    10: ("october", "oct", "octobre"),
    11: ("november", "nov", "novembre"),
    12: ("december", "dec", "décembre", "decembre", "déc"),
}

_WS = re.compile(r"\s+")


def norm(text: str) -> str:
    return _WS.sub(" ", (text or "").lower()).strip()


def appears(value: object, text: str) -> bool:
    """Is `value` written in `text`? Case and runs of whitespace are ignored,
    and a code split by a space ("AB 1234") matches one written without."""
    v = norm(str(value or ""))
    if not v:
        return False
    t = norm(text)
    if v in t:
        return True
    squeezed = v.replace(" ", "")
    return len(squeezed) >= 3 and squeezed in t.replace(" ", "")


def text_or_none(value: object, text: str, limit: int = 80) -> str | None:
    """A short single-line string that appears in `text`, else None."""
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())[:limit]
    return value if value and appears(value, text) else None


def parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", value.strip())
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def parse_time(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not m or int(m[1]) > 23 or int(m[2]) > 59:
        return None
    return f"{int(m[1]):02d}:{m[2]}"


def date_in_text(d: date, text: str) -> bool:
    """Is this calendar day written in `text`, in any common form? The year
    is not required (confirmations often leave it out); the day and month
    are."""
    t = norm(text)
    day, mon = d.day, d.month
    dd = rf"0?{day}(?:st|nd|rd|th|er)?"
    names = "|".join(re.escape(n) for n in MONTHS[mon])
    patterns = (
        rf"{d.year}-0?{mon}-0?{day}\b",
        rf"(?<!\d){dd}\s*(?:of\s+|de\s+)?(?:{names})\b",
        rf"\b(?:{names})\.?\s+{dd}(?!\d)",
        rf"(?<!\d)0?{day}[./-]0?{mon}(?!\d)",
        rf"(?<!\d)0?{mon}[./-]0?{day}(?!\d)",
    )
    return any(re.search(p, t) for p in patterns)


def time_in_text(hhmm: str, text: str) -> bool:
    """Is this clock time written in `text`, 24-hour or 12-hour?"""
    h, m = (int(x) for x in hhmm.split(":"))
    t = norm(text)
    if re.search(rf"(?<!\d)0?{h}[:.h]{m:02d}(?!\d)", t):
        return True
    h12 = h % 12 or 12
    suffix = "p" if h >= 12 else "a"
    return bool(re.search(rf"(?<!\d){h12}[:.]{m:02d}\s*{suffix}\.?m", t))


def json_object(text: str) -> dict | None:
    """The first-to-last brace of a model's reply, as a dict, or None."""
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None
