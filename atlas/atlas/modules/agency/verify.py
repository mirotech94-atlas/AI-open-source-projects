"""agency.verify -- only real, active New York businesses that need help.

the owner, 2026-09-30. Before a lead is audited, built for or contacted, ATLAS
confirms three things and keeps the evidence:

  1. **It is operating.** Google Places' own `businessStatus` first:
     CLOSED_PERMANENTLY or CLOSED_TEMPORARILY disqualifies without asking
     anyone. Then Perplexity (the MCP client in `_perplexity.py`, the one
     door): open now, not a chain or franchise head office, not a
     government office.
  2. **It is in New York City.** Decided in code from the address Places
     gave -- a five-borough ZIP (`nyc_by_address`) -- and, only when the
     address carries no ZIP, from Perplexity's answer.
  3. **Its web presence problem is real.** No website on Places is real by
     itself; otherwise Perplexity must name one (none, broken, outdated,
     not mobile-friendly, no online booking). "Fine" disqualifies.

Perplexity is asked for a fixed set of labelled lines and its answer is READ
by a regex in code -- no model interprets it, and its words never reach a
prompt from here. What is stored is `wrap_untrusted()` text (the answer as
`_perplexity.call` returns it), its source URLs, and the time it was read.

Verdicts, one row per run in `agency_verifications`:

  * ``verified``      every check passed on evidence
  * ``disqualified``  a check failed; the lead's status becomes
                      'disqualified' with the reason, and it is never
                      contacted
  * ``unknown``       the question could not be answered -- Perplexity
                      unreachable, not configured, over its cap, or silent
                      on a check. Never read as OK: an unknown lead is not
                      audited by a mission and never contacted.

Cost is capped per day (`AGENCY_VERIFY_DAILY_CAP`, default 40, New York
day), on top of Perplexity's own monthly cap. A verified or disqualified
verdict is reused for `AGENCY_VERIFY_DAYS` (default 30); an unknown one is
not, so a later run asks again.

`contact_refusal` is the one gate every outreach, reply, text and call runs
through: nothing reaches a lead without a live verified row.
"""
from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ...kernel.contracts import ActionResult
from .. import _perplexity
from . import _store

VERDICTS = ("verified", "disqualified", "unknown")
CALLER = "agency-verify"
NY = ZoneInfo("America/New_York")

# The five boroughs by ZIP: Manhattan 100-102, Staten Island 103, the Bronx
# 104, Brooklyn 112, Queens 111/113/114/116 plus 11004-11005 (Glen Oaks,
# Floral Park's Queens side). 110xx is otherwise Nassau County.
_ZIP = re.compile(r"\b(?:NY|New York)\s*,?\s*(\d{5})(?:-\d{4})?\b", re.IGNORECASE)
_NYC_PREFIXES = ("100", "101", "102", "103", "104", "111", "112", "113", "114", "116")
_NYC_EXTRA = ("11004", "11005")

LABELS = ("OPERATING", "CHAIN", "GOVERNMENT", "IN_NYC", "WEBSITE")
_LABEL = "|".join(LABELS)
# A label, its value, and the value's end: the next label, a line break or
# the end. Perplexity may answer one label per line or all on one line.
_FIELD = re.compile(
    rf"\b({_LABEL})\s*\**\s*:\s*\**\s*(.*?)\s*(?=\**\s*\b(?:{_LABEL})\s*\**\s*:|\n|$)",
    re.IGNORECASE)
_YES = re.compile(r"^(?:yes|true|y)\b", re.IGNORECASE)
_NO = re.compile(r"^(?:no|false|n)\b", re.IGNORECASE)
WEB_PROBLEMS = ("none", "broken", "outdated", "no_mobile", "no_booking")


def daily_cap() -> int:
    try:
        return max(0, int(os.getenv("AGENCY_VERIFY_DAILY_CAP", "40")))
    except ValueError:
        return 40


def reuse_days() -> int:
    try:
        return max(0, int(os.getenv("AGENCY_VERIFY_DAYS", "30")))
    except ValueError:
        return 30


def nyc_by_address(address: str | None) -> bool | None:
    """True/False from a ZIP in the address; None when it carries none."""
    match = _ZIP.search(address or "")
    if match is None:
        return None
    zipcode = match.group(1)
    return zipcode.startswith(_NYC_PREFIXES) or zipcode in _NYC_EXTRA


def question(lead: dict[str, Any]) -> str:
    """Short enough for `_perplexity.MAX_QUERY` (500) whatever the lead's
    name, address and site: each is cut, and the instructions are fixed."""
    name = " ".join(str(lead.get("name") or "").split())[:60]
    where = " ".join(str(lead.get("address") or "").split())[:80]
    site = " ".join(str(lead.get("website") or "").split())[:60] or "none listed"
    return (f'Business "{name}", {where or "address unknown"}, site: {site}. '
            "Reply only: OPERATING: yes/no/unknown (open now). CHAIN: "
            "yes/no/unknown (chain or franchise HQ). GOVERNMENT: yes/no/unknown. "
            "IN_NYC: yes/no/unknown. WEBSITE: none/broken/outdated/no_mobile/"
            "no_booking/fine/unknown. Cite sources.")


def parse(text: str) -> dict[str, Any]:
    """The labelled lines of Perplexity's answer, read by code. A field it
    did not answer is None (unknown), never a default."""
    fields: dict[str, str] = {}
    for key, value in _FIELD.findall(text or ""):
        fields.setdefault(key.upper(), value.strip().strip("*.").strip())

    def yes_no(key: str) -> bool | None:
        value = fields.get(key, "")
        if _YES.match(value):
            return True
        if _NO.match(value):
            return False
        return None

    web_raw = fields.get("WEBSITE", "").lower().replace("-", "_").replace(" ", "_")
    web = sorted({w for w in (*WEB_PROBLEMS, "fine") if w in web_raw})
    return {"operating": yes_no("OPERATING"), "chain": yes_no("CHAIN"),
            "government": yes_no("GOVERNMENT"), "in_nyc": yes_no("IN_NYC"),
            "website": web or None}


def judge(lead: dict[str, Any], found: dict[str, Any] | None
          ) -> tuple[str, list[str], dict[str, Any]]:
    """(verdict, reasons, checks) from Places, the address and Perplexity's
    parsed answer (None when it could not be read). Pure."""
    status = (lead.get("business_status") or "").upper() or None
    checks: dict[str, Any] = {"places_status": status,
                              "nyc_by_address": nyc_by_address(lead.get("address")),
                              "listed_website": bool(lead.get("website"))}
    reasons: list[str] = []
    if status in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
        reasons.append(f"Google Places says {status.lower().replace('_', ' ')}")
    if checks["nyc_by_address"] is False:
        reasons.append(f"not in New York City ({lead.get('address')})")
    if found is not None:
        checks.update(found)
        if found["operating"] is False:
            reasons.append("not operating, per the research")
        if found["chain"] is True:
            reasons.append("a chain or franchise head office")
        if found["government"] is True:
            reasons.append("a government or public office")
        if checks["nyc_by_address"] is None and found["in_nyc"] is False:
            reasons.append("not in New York City, per the research")
        if checks["listed_website"] and found["website"] == ["fine"]:
            reasons.append("its website looks fine: no problem to fix")
    if reasons:
        return "disqualified", reasons, checks
    if found is None:
        return "unknown", ["the research could not be read"], checks
    unknown: list[str] = []
    # Places' own OPERATIONAL is enough when the research is silent on it.
    if status != "OPERATIONAL" and found["operating"] is not True:
        unknown.append("whether it is operating")
    if found["chain"] is None:
        unknown.append("whether it is a chain")
    if found["government"] is None:
        unknown.append("whether it is a government office")
    nyc = checks["nyc_by_address"]
    if nyc is None and found["in_nyc"] is not True:
        unknown.append("whether it is in New York City")
    web_real = (not checks["listed_website"]) or bool(
        set(found["website"] or ()) & set(WEB_PROBLEMS))
    if not web_real:
        unknown.append("whether its web presence has a real problem")
    if unknown:
        return "unknown", [f"could not confirm {u}" for u in unknown], checks
    return "verified", [], checks


async def _budget_left() -> tuple[bool, str]:
    start = datetime.now(NY).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        used = await _store.verify_calls_since(start)
    except Exception as exc:                            # noqa: BLE001
        return False, f"the verification budget could not be read ({type(exc).__name__})"
    cap = daily_cap()
    if used >= cap:
        return False, f"daily verification cap reached ({used}/{cap}, AGENCY_VERIFY_DAILY_CAP)"
    return True, ""


def _reusable(row: dict[str, Any] | None) -> bool:
    if not row or row.get("verdict") not in ("verified", "disqualified"):
        return False
    at = row.get("at")
    if isinstance(at, datetime):
        at = at if at.tzinfo else at.replace(tzinfo=UTC)
        return datetime.now(UTC) - at < timedelta(days=reuse_days())
    return False


PROFILES = ("local_nyc", "remote_us")
PAID_ENV = "SALES_PAID_VERIFY"


def paid_for_sales() -> bool:
    return (os.getenv(PAID_ENV) or "").strip().lower() in ("1", "on", "true", "yes")


async def _remote_us(lead: dict[str, Any]
                     ) -> tuple[str, list[str], dict[str, Any], list[str], Any]:
    """A remote US business, verified from its own site: live, and a US
    street address on it. No paid read. (verdict, reasons, checks, sources,
    read_at)."""
    from .. import _scrape
    from ..sales import rules
    checks: dict[str, Any] = {"profile": "remote_us",
                              "listed_website": bool(lead.get("website"))}
    if rules.excluded(lead.get("name"), lead.get("category")):
        return "disqualified", ["a place of worship"], checks, [], None
    site = (lead.get("website") or "").strip()
    if not site:
        return "unknown", ["no website to read"], checks, [], None
    page = await _scrape.fetch(site, agent="sales")
    checks["site_status"] = page.status
    if not page.ok:
        why = page.blocked or f"HTTP {page.status}"
        return "unknown", [f"its site could not be read: {why}"], checks, [], None
    checks["us_address"] = await rules.has_us_address_async(page.text)
    sources = [page.final_url or page.url]
    if not checks["us_address"]:
        return ("unknown", ["no US street address on its own site"], checks, sources,
                page.fetched_at)
    return "verified", [], checks, sources, page.fetched_at


async def verify_lead(lead_id: int, profile: str = "") -> ActionResult:
    """Tier AUTO: one read (Perplexity for local_nyc, the lead's own site for
    remote_us) and rows on ATLAS's own tables."""
    lead_id = int(lead_id)
    if profile and profile not in PROFILES:
        return ActionResult(False, f"unknown profile {profile!r}: "
                                   f"{' or '.join(PROFILES)}")
    lead = await _store.get_lead(lead_id)
    if lead is None:
        return ActionResult(False, f"no such lead: {lead_id}")
    profile = profile or str(lead.get("segment") or "local_nyc")
    if profile not in PROFILES:
        profile = "local_nyc"
    cached = await _store.latest_verification(lead_id)
    if _reusable(cached):
        return ActionResult(True, f"lead {lead_id}: {cached['verdict']} (verified "
                                  f"{cached['at']:%Y-%m-%d}; reused, nothing written)",
                            evidence={"verdict": cached["verdict"], "cached": True,
                                      "verification_id": cached["id"]},
                            rollback_token="")

    if profile == "remote_us":
        return await _record(lead, *await _remote_us(lead), "", None)

    found: dict[str, Any] | None = None
    answer_text, sources, read_at, unread = "", [], None, None
    status = (lead.get("business_status") or "").upper()
    closed = status in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY")
    outside = nyc_by_address(lead.get("address")) is False
    sales_lead = bool(lead.get("source"))
    if not (closed or outside) and sales_lead and not paid_for_sales():
        unread = (f"{PAID_ENV} is off: a Sales lead gets no paid research read "
                  "(set it on to allow one)")
    elif not (closed or outside):
        ok, why = await _budget_left()
        if not ok:
            unread = why
        else:
            answer, why = await _perplexity.call(
                "perplexity_ask", question(lead), caller=CALLER, guard_names=False)
            if answer is None:
                unread = why or "Perplexity returned nothing"
            else:
                found = parse(answer.unwrapped)
                answer_text, sources, read_at = (answer.text, list(answer.citations),
                                                 answer.read_at)
    verdict, reasons, checks = judge(lead, found)
    checks["profile"] = "local_nyc"
    if unread and verdict == "unknown":
        reasons = [f"Perplexity unread: {unread}"]
    return await _record(lead, verdict, reasons, checks, sources, read_at,
                         answer_text, unread)


async def _record(lead: dict[str, Any], verdict: str, reasons: list[str],
                  checks: dict[str, Any], sources: list[str], read_at: Any,
                  answer_text: str, unread: str | None) -> ActionResult:
    lead_id = int(lead["id"])
    previous = str(lead.get("status") or "")
    vid = await _store.insert_verification(lead_id, verdict, reasons, checks,
                                           answer_text, sources, read_at, previous)
    if vid is None:
        return ActionResult(False, "failed to record the verification")
    if verdict == "disqualified":
        await _store.set_lead_status(lead_id, "disqualified")
        await _store.set_lead_meta(lead_id, "disqualified", {
            "reasons": reasons, "at": datetime.now(UTC).isoformat()})
    return ActionResult(
        True, f"lead {lead_id}: {verdict}" + (f" ({'; '.join(reasons)})" if reasons else ""),
        evidence={"verdict": verdict, "reasons": reasons, "checks": checks,
                  "sources": sources, "verification_id": vid,
                  "read_at": read_at.isoformat() if read_at else None,
                  "unread": unread},
        rollback_token=str(vid))


async def verify_dry(lead_id: int, profile: str = "") -> ActionResult:
    lead = await _store.get_lead(int(lead_id))
    if lead is None:
        return ActionResult(False, f"no such lead: {lead_id}")
    if (lead.get("segment") or "") == "remote_us":
        return ActionResult(True, f"would verify {lead.get('name')} (lead {lead_id}) "
                                  "from its own site: live, with a US address",
                            evidence={"would_fetch": [lead.get("website")]})
    return ActionResult(True, f"would verify {lead.get('name')} (lead {lead_id}) "
                              "with Places status, its address and one Perplexity read")


async def verify_rollback(token: str) -> ActionResult:
    if not token:
        return ActionResult(True, "nothing was written; nothing to roll back")
    row = await _store.get_verification(int(token))
    if row is None:
        return ActionResult(True, "nothing to roll back")
    await _store.withdraw_verification(int(token))
    if row.get("verdict") == "disqualified" and row.get("previous_status"):
        await _store.set_lead_status(int(row["lead_id"]), str(row["previous_status"]))
    return ActionResult(True, f"withdrew verification {token}")


# ------------------------------------------------------------- the gate

async def contact_refusal(lead_id: int) -> str | None:
    """Why ATLAS may not reach out to this lead, or None. Only a live
    'verified' verdict passes; unknown and disqualified never do, and a
    verification that cannot be read is a refusal."""
    try:
        row = await _store.latest_verification(int(lead_id))
    except Exception as exc:                            # noqa: BLE001
        return f"its verification could not be read ({type(exc).__name__})"
    if row is None:
        return (f"lead {lead_id} has not been verified as a real, active NYC "
                "business; run agency.verify first")
    if row.get("verdict") == "disqualified":
        reasons = _store.parse_json(row.get("reasons"), [])
        return f"lead {lead_id} is disqualified: {'; '.join(reasons) or 'no reason'}"
    if row.get("verdict") != "verified":
        return (f"lead {lead_id}'s verification is {row.get('verdict')}; an "
                "unverified lead is never contacted")
    return None


# the owner, 2026-10-28: outreach goes to US leads only. A two-letter state and a
# ZIP at the end of the address ("Brooklyn, NY 11201"), or a country part
# that says the US.
_US_STATE_ZIP = re.compile(
    r"\b(A[KLRZ]|C[AOT]|D[CE]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]"
    r"|O[HKR]|PA|PR|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])\s+\d{5}(?:-\d{4})?\b")
_US_NAMES = frozenset({"usa", "us", "u.s.", "u.s.a.", "united states",
                       "united states of america"})


def is_us_lead(lead: dict[str, Any]) -> bool:
    """A Sales CSV row (its import refuses every non-US row), the remote-US
    segment, or an address that shows the US. Anything else is not US."""
    if lead.get("segment") == "remote_us" or lead.get("source") == "csv":
        return True
    address = str(lead.get("address") or "").strip()
    if not address:
        return False
    if address.rsplit(",", 1)[-1].strip().lower() in _US_NAMES:
        return True
    return bool(_US_STATE_ZIP.search(address))


async def us_refusal(lead_id: int) -> str | None:
    """Why this lead is not a US lead, or None. A lead that cannot be read is
    a refusal (fails closed)."""
    try:
        lead = await _store.get_lead(int(lead_id))
    except Exception as exc:                            # noqa: BLE001
        return f"lead {lead_id} could not be read ({type(exc).__name__})"
    if lead is None:
        return f"no such lead: {lead_id}"
    if is_us_lead(lead):
        return None
    return (f"lead {lead_id} is not shown to be in the US; outreach goes to "
            "US businesses only")
