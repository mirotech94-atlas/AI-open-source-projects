"""The deterministic words check every outgoing draft passes before QA lets
it go: no network, no model, the same answer every time.

Rules (the owner, 2026-10-27, the six-agents plan):
- never "$100" (the retired cheap tier), "guaranteed" or "#1";
- a percentage, an "Nx" multiplier, or a dollar amount other than the two
  prices ($500 / $300) only on a line that names its source;
- emails are signed by the assistant (outreach.TRAILER, or "ATLAS, assistant
  to the owner at Mapfront"), never as the owner, and carry the CAN-SPAM lines;
- texts carry STOP; a Reddit draft carries a line starting "Disclosure:".
"""
from __future__ import annotations

import os
import re

TRAILER = "— sent by the owner's assistant"          # agency.outreach.TRAILER
SIGNATURE = "ATLAS, assistant to the owner at Mapfront"
SIGNATURES = (TRAILER, SIGNATURE)
PRICES = frozenset({500, 300})

# kind -> what that channel must carry
# "outreach": the agency's cold email to a lead. the owner signs it himself
# (the owner 2026-10-31); every other rule, CAN-SPAM included, still applies.
KINDS = ("email", "sms", "invite", "post", "reddit", "report", "outreach")
SIGNED = frozenset({"email", "report"})

_BANNED: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bguarantee(?:d|s)?\b", re.IGNORECASE), 'says "guaranteed"'),
    (re.compile(r"(?<![\w&])#\s?1\b(?!\d)"), 'says "#1"'),
)
_PERCENT = re.compile(r"\d+(?:[.,]\d+)?\s?(?:%|percent\b)", re.IGNORECASE)
_MULTIPLIER = re.compile(r"\b\d+(?:\.\d+)?\s?[x×](?![a-z0-9])", re.IGNORECASE)
_DOLLARS = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s?([kKmM])?\b")
_SOURCED = re.compile(r"\bsource[sd]?\s*:|\baccording\s+to\b|https?://", re.IGNORECASE)
_SIGNED_AS_OWNER = re.compile(
    r"^[\s\-—–]*(?:(?:best|thanks|thank\s+you|regards|cheers|sincerely|warmly)"
    r"[\s,]*)?owner[\s.!]*$", re.IGNORECASE)
_STOP = re.compile(r"\bSTOP\b")
_DISCLOSURE = re.compile(r"^\s*Disclosure:", re.IGNORECASE | re.MULTILINE)


def _amount(raw: str, suffix: str | None) -> float | None:
    try:
        value = float(raw.replace(",", ""))
    except ValueError:
        return None
    if suffix:
        value *= 1_000 if suffix.lower() == "k" else 1_000_000
    return value


def _unsourced_numbers(text: str) -> list[str]:
    out: list[str] = []
    for line in text.splitlines():
        if _SOURCED.search(line):
            continue
        for m in _PERCENT.finditer(line):
            out.append(f"the figure {m.group(0).strip()!r} has no source")
        for m in _MULTIPLIER.finditer(line):
            out.append(f"the multiplier {m.group(0).strip()!r} has no source")
        for m in _DOLLARS.finditer(line):
            value = _amount(m.group(1), m.group(2))
            if value is None or value == 100 or value in PRICES:
                continue
            out.append(f"the amount {m.group(0).strip()!r} is not a Mapfront price "
                       "and has no source")
    return out


def _can_spam(text: str) -> list[str]:
    missing: list[str] = []
    postal = os.getenv("AGENCY_POSTAL_ADDRESS", "").strip()
    if not postal:
        missing.append("CAN-SPAM: no postal address (AGENCY_POSTAL_ADDRESS is not set)")
    elif postal not in text:
        missing.append("CAN-SPAM: the postal address line is missing")
    low = text.lower()
    if "reply stop" not in low and "unsubscribe" not in low:
        missing.append("CAN-SPAM: no opt-out line (reply STOP or unsubscribe)")
    return missing


def refusals(text: str, kind: str) -> list[str]:
    """Every rule `text` breaks as a `kind` draft; [] when it may go."""
    if kind not in KINDS:
        return [f"unknown draft kind {kind!r}"]
    text = text or ""
    if not text.strip():
        return ["the draft is empty"]
    out = []
    # The retired cheap tier, however written ($100, $100.00), on any
    # line: a source does not make it a price Mapfront charges.
    if any(_amount(m.group(1), m.group(2)) == 100 for m in _DOLLARS.finditer(text)):
        out.append('says "$100"')
    out += [why for pattern, why in _BANNED if pattern.search(text)]
    out += _unsourced_numbers(text)
    if kind != "outreach" and any(_SIGNED_AS_OWNER.match(line)
                                  for line in text.splitlines()):
        out.append("it is signed as the owner; the assistant signs, never the owner")
    if kind in SIGNED and not any(sig in text for sig in SIGNATURES):
        out.append("no assistant signature (the trailer or "
                   f"{SIGNATURE!r})")
    if kind in ("email", "outreach"):
        out += _can_spam(text)
    if kind == "sms" and not _STOP.search(text):
        out.append("the text has no STOP line")
    if kind == "reddit" and not _DISCLOSURE.search(text):
        out.append('the Reddit draft has no line starting "Disclosure:"')
    # One line per rule broken, in order, without repeats.
    return list(dict.fromkeys(out))
