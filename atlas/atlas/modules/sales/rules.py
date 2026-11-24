"""Code rules for Sales: who is never a lead, what a US address looks like,
and the segments. Pure; no network, no model."""
from __future__ import annotations

import asyncio
import hashlib
import re
from urllib.parse import urlsplit

# Miro, 2026-10-27: places of worship are never sales leads (the community work
# is AMSM's own channel, not a prospect list).
EXCLUDED_TYPES = frozenset({
    "community", "place_of_worship", "synagogue", "mosque", "temple", "hindu_temple",
    "buddhist_temple", "religious_organization", "cathedral", "chapel",
})
_WORSHIP_NAME = re.compile(
    r"\b(?:community|chapel|cathedral|parish|synagogue|shul|temple|mosque|masjid"
    r"|ministr(?:y|ies)|congregation|tabernacle|diocese|gurdwara|mandir)\b",
    re.IGNORECASE)

# Where a lead is and therefore how it is verified (agency/verify.py).
SEGMENTS = {
    "local_nyc": "local_nyc",      # Places / OSM, five boroughs
    "coaches": "remote_us",        # web search, anywhere in the US
    "shops": "remote_us",
    "remote_us": "remote_us",
}
SEARCHES = {
    "coaches": "independent business coach US contact",
    "shops": "independent online shop small business US contact",
}

STATES = ("AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
          "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
          "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
          "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
          "WV", "WI", "WY")
# Page text is attacker-controlled: every pattern here is linear (no nested
# or adjacent variable-length quantifiers) and runs on a capped slice.
MAX_TEXT = 50_000
# 1. A state and a ZIP ("TX 78701"): one alternation, fixed-width digits.
STATE_ZIP = re.compile(r"\b(?:" + "|".join(STATES) + r")[ \t]+\d{5}(?:-\d{4})?\b")
# 2. Then, in a fixed window before it on the same line, a street number
#    followed by a word ("120 Main").
STREET_NUMBER = re.compile(r"(?:^|[^\w-])\d{1,6}[ \t]+[A-Za-z]")
LOOKBACK = 120
MAX_CANDIDATES = 200
US_COUNTRY = frozenset({"", "us", "usa", "u.s.", "u.s.a.", "united states",
                        "united states of america"})


def excluded(name: str | None, category: str | None) -> bool:
    """A place of worship, by type or by name."""
    if (category or "").strip().lower() in EXCLUDED_TYPES:
        return True
    return bool(_WORSHIP_NAME.search((name or "")[:300]))


def has_us_address(text: str | None) -> bool:
    """A street number then, within LOOKBACK characters on the same line, a
    state and a ZIP. Linear in the capped text."""
    body = (text or "")[:MAX_TEXT]
    for n, m in enumerate(STATE_ZIP.finditer(body)):
        if n >= MAX_CANDIDATES:
            return False
        window = body[max(0, m.start() - LOOKBACK):m.start()]
        window = window[window.rfind("\n") + 1:]
        if STREET_NUMBER.search(window):
            return True
    return False


async def has_us_address_async(text: str | None) -> bool:
    """Off the event loop for anything but a short text."""
    if len(text or "") <= 2_000:
        return has_us_address(text)
    return await asyncio.to_thread(has_us_address, text)


def domain(url: str | None) -> str:
    host = (urlsplit(url if "//" in (url or "") else f"//{url or ''}").hostname
            or "").lower()
    return host.removeprefix("www.")


def key(prefix: str, seed: str) -> str:
    return f"{prefix}:" + hashlib.sha256(seed.encode()).hexdigest()[:16]
