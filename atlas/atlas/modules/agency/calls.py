"""agency.calls -- ATLAS rings a lead who asked for a call; Miro gets a brief
for everyone else.

`agency.call_lead` (Tier AUTO since 2026-10-06; Miro: "call them as well",
under his rule that anything not urgent runs without a card). The business
line (`api/reception.py`, 2026-09-27) was inbound only because an AI voice
on an outbound call is an "artificial voice" under the TCPA. That still
holds for every lead who has not asked: this action exists for the ones who
did, and the consent is the gate. `consent.contact_guard` must pass -- a
live consent for calls from the form, from ringing the line, or recorded by
Miro from his own call; not suppressed; AGENCY_CALLS_ENABLED; Twilio
configured; 08:00-21:00 lead time; under the contact cap -- in the dry run
and again in the run. `kernel.lead_followup.call_due` places the call
through the executor on the first tick inside calling hours and posts a
receipt; a refusal is the Tier 2 card with the reason. Nothing cold-calls.
The rollback is honest: a ringing phone is not un-rung; it records the fact
on the lead and suppresses the number so nothing dials it again.

The call is Twilio's, placed from TWILIO_FROM with its own routes
(`/webhooks/twilio/agency/voice|turn|status`, `api/lead_call.py`), never the
ones that ring Miro: those can approve cards by keypad, and their status
callback counts an unanswered call as Miro declining. Each call carries a
fresh secret in its URL; only its SHA-256 is stored (`agency_contacts.
token_sha`), and a webhook is admitted only when the secret, the contact row,
the lead's number and Twilio's CallSid all agree. The opening line is the
disclosure, built in code, spoken first, and stored with the row.

`agency.call_brief` (Tier AUTO) is the path for a lead with no consent: a
one-page brief Miro dials from himself -- who, what their site gets wrong,
what we built, talking points, an opening line in his own words (Miro,
2026-10-01: "this is ATLAS Academy, we saw your website needed help / we
made you a free preview"), the one line to ask -- "Can my assistant call
you back with the full proposal?" -- whose yes he records with
`agency.record_consent` (principal_only; that is the consent
`agency.call_lead` needs), likely objections with answers from the
playbook, and the price anchors, each figure from config and the deal
count read now. It writes one file under `AGENCY_BRIEFS_DIR` (default
data/agency/briefs) and nothing else. Nothing dials a lead with no consent.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx

from ...kernel.contracts import ActionResult
from .._http import explain
from . import _store, consent, hosting, pricing
from .audit import audit_lead

API = "https://api.twilio.com/2010-04-01/Accounts"


def token_sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def call_lead_dry(lead_id: int) -> ActionResult:
    contact, why = await consent.contact_guard(int(lead_id), "call")
    if contact is None:
        return ActionResult(False, str(why))
    return ActionResult(True, f"would call {contact.phone}, opening: {contact.opening}",
                        evidence={"to": contact.phone, "opening": contact.opening})


async def call_lead(lead_id: int) -> ActionResult:
    """Tier AUTO (2026-10-06), only past `consent.contact_guard`. A ringing
    phone cannot be un-rung: the rollback says so and suppresses the number."""
    lead_id = int(lead_id)
    contact, why = await consent.contact_guard(lead_id, "call")
    if contact is None:
        return ActionResult(False, f"refused: {why}")
    token = secrets.token_urlsafe(24)
    contact_id = await _store.insert_contact(lead_id, "call", contact.phone,
                                             contact.opening, "", token_sha(token))
    if contact_id is None:
        return ActionResult(False, "failed to record the call before placing it")
    host = os.getenv("ATLAS_HOST", "").strip()
    query = urlencode({"c": contact_id, "t": token}, quote_via=quote)
    sid_env = os.getenv("TWILIO_ACCOUNT_SID", "")
    try:
        async with httpx.AsyncClient(timeout=20,
                                     auth=(sid_env, os.getenv("TWILIO_AUTH_TOKEN", ""))) as c:
            r = await c.post(f"{API}/{sid_env}/Calls.json", data={
                "From": os.getenv("TWILIO_FROM", ""), "To": contact.phone,
                "Url": f"https://{host}/webhooks/twilio/agency/voice?{query}",
                "Method": "POST",
                "MachineDetection": "DetectMessageEnd",
                "StatusCallback": f"https://{host}/webhooks/twilio/agency/status?{query}",
                "StatusCallbackMethod": "POST",
                "StatusCallbackEvent": "completed"})
            r.raise_for_status()
            call_sid = str(r.json().get("sid") or "")
    except Exception as exc:                            # noqa: BLE001
        await _store.set_contact(contact_id, status="failed", ended=True)
        return ActionResult(False, f"the call was not placed: {explain(exc)}")
    await _store.set_contact(contact_id, sid=call_sid)
    return ActionResult(True, f"calling lead {lead_id} at {contact.phone}",
                        evidence={"contact_id": contact_id, "sid": call_sid,
                                  "to": contact.phone, "opening": contact.opening},
                        rollback_token=str(contact_id))


async def call_lead_rollback(token: str) -> ActionResult:
    """A call that was placed cannot be un-placed, and this does not pretend
    to. It records the fact on the lead and suppresses the number, so no
    call or text ever goes to it again (the same honesty as
    `outreach.send_outreach_rollback`)."""
    if not token:
        return ActionResult(True, "no call was placed for this run; nothing to roll back")
    contact = await _store.get_contact(int(token))
    if contact is None:
        return ActionResult(False, f"no such contact: {token}")
    phone = str(contact.get("phone") or "")
    if phone:
        await _store.add_suppression(consent.tel_key(phone), (
            f"call #{token} was rolled back after it was placed: a call cannot be "
            "un-rung, so nothing further dials or texts this number"))
    await _store.set_lead_meta(int(contact["lead_id"]), "call_rolled_back", {
        "contact_id": int(token), "phone": phone,
        "at": datetime.now(UTC).isoformat()})
    return ActionResult(
        True, f"call #{token} to {phone or 'the lead'} was already placed and cannot "
              "be un-rung; the number is suppressed so nothing dials it again, and "
              "the lead carries the record",
        evidence={"suppressed": phone, "unrung": False})


# ------------------------------------------------------------- call brief

def _contact() -> str:
    from .outreach import _send_from_address
    return _send_from_address()


def briefs_dir() -> Path:
    return Path(os.getenv("AGENCY_BRIEFS_DIR", "data/agency/briefs"))


OBJECTIONS = (
    ("It's too expensive.",
     ("Acknowledge it, restate what their own audit found, then offer the next "
      "step on the ladder (a smaller first phase, or a payment plan) before any "
      "lower figure.")),
    ("We already have someone.",
     ("Respect it. Ask what they'd change about the current site; if nothing, "
      "leave the link and stop.")),
    ("Not right now.",
     "Ask when would be better, note the date, and don't follow up before it."),
    ("I need to think about it.",
     ("Ask what they'd want to be sure of, and offer to send the link and a "
      "two-line summary.")),
    ("Is this a scam?",
     ("Say who you are, how you found them (their public listing), what you "
      "built, and that one word from them stops any further contact.")),
)


# Miro, 2026-10-01: the line that turns a lead with no consent into one
# ATLAS may call. Their yes is prior express consent for agency.call_lead,
# recorded by him through agency.record_consent (principal_only).
ASK_ASSISTANT = "Can my assistant call you back with the full proposal?"


def opening_line(lead: dict, url: str | None = None) -> str:
    """What Miro says first, in his own words (2026-10-01). Only a lead with
    a website on record hears that we looked at it: saying so to a business
    with no site is the first thing on the call that is not true."""
    who = consent.agency_name() or "my studio"
    preview = f": {url}" if url else ""
    if lead.get("website"):
        pitch = ("We looked at your website and thought it needed help, so we made "
                 f"you a free preview to look at{preview}.")
    else:
        pitch = ("We couldn't find a website for your business, so we made you a "
                 f"free preview{preview}.")
    return (f"Hi, is this {lead.get('name')}? This is Miro from {who}. {pitch} "
            "Have you got five minutes?")


def built_line(links: dict) -> str:
    """What Miro built, as the address the lead can open: the client's own
    domain, the public preview at https://<slug>.mapfront.io, or a Webild
    page. A path on the ATLAS box is never something to read out."""
    if links.get("domain"):
        return f"https://{links['domain']}"
    slug = links.get("slug")
    if slug and hosting.slug_problem(slug) is None:
        url = hosting.public_url(str(slug))
        if links.get("deployed"):
            return f"the preview site, live at {url}"
        return (f"a preview site for {url}, built but not deployed yet: deploy it "
                "(agency.deploy_site) before giving out the link")
    if links.get("webild_url"):
        return str(links["webild_url"])
    if links.get("mockup"):
        return ("a draft preview, not public yet: run agency.build_site and deploy it "
                "before mentioning it")
    return "nothing built yet"


async def brief_text(lead_id: int) -> str | None:
    lead = await _store.get_lead(lead_id)
    if lead is None:
        return None
    audit = await audit_lead(lead_id, lead.get("website"))
    links = await _store.lead_links(lead_id)
    terms = await pricing.terms()
    read_at = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    findings = [f.detail for f in audit.findings]
    built = built_line(links)
    points = [f"Their current site: {d}" for d in findings[:4]] or [
        "No audit finding on record: ask how customers find them today."]
    points.append(f"What Miro built for them: {built}.")
    points.append("Included: " + "; ".join(pricing.deliverables()) + ".")
    if not pricing.ai_chat_built():
        points.append("Not included yet, do not promise it: an AI chat on the site.")
    if terms.configured:
        prices = [f"List ${terms.list_price} (anchor with this)",
                  f"floor ${terms.floor}, never lower"]
        if terms.first_sale_open:
            prices.append(f"first-sale ${terms.first_sale}, only if they say it's "
                          "too much, while no deal is won or paid")
        ladder = "; then ".join(terms.ladder) or "none configured"
        price_block = (", ".join(prices) + f".\nConcessions, in order: {ladder}.\n"
                       f"Won/paid deals on record: "
                       f"{'unreadable' if terms.deals is None else terms.deals}")
    else:
        price_block = "No price configured: don't name a figure; say you'll confirm it."
    from .outreach import preview_url
    opening = opening_line(lead, preview_url(links))
    asked = await _store.consent_for_lead(lead_id, "call")
    if asked:
        how = {"web_form": "on the \"Have Mapfront call me\" form",
               "call": "by ringing the business line",
               "sms": "in a text", "email": "in an email",
               consent.PRINCIPAL: "to you, on your own call"}.get(
                   str(asked.get("source_channel")), "in writing")
        when = asked.get("granted_at")
        stamp = when.strftime("%Y-%m-%d %H:%M UTC") if hasattr(when, "strftime") else ""
        status = (f"**Priority: they asked to be called** {how}"
                  + (f" ({stamp})" if stamp else "")
                  + f", on {asked.get('phone')}. ATLAS makes this call itself on "
                  "the next tick inside calling hours (agency.call_lead, opening with "
                  "who is calling and the preview; a refusal becomes a card), unless "
                  "you dial it first.")
    else:
        status = ("Miro dials this one himself: there is no consent on record for "
                  "ATLAS to call or text, so ATLAS does not.")
    ask_lines = [
        "## The ask",
        ASK_ASSISTANT,
        ("- If they say yes, that is their consent for ATLAS to call: record it "
         f"with agency.record_consent(lead_id={lead_id}, how=\"said yes on Miro's "
         "call\"). ATLAS places the agency.call_lead call on the next tick inside "
         "calling hours (your rule: no card; a refusal becomes one), and the call "
         "opens with the preview and the offer to put them through to you."),
        "- If they say no, thank them; nothing dials them.",
        "",
    ]
    from .outreach import mapfront_number
    line = mapfront_number()
    lines = [
        f"# Call brief: {lead.get('name')}",
        f"_Prepared {read_at}._ {status}",
        "",
        "## Who",
        f"- {lead.get('name')} ({lead.get('category')})",
        f"- Phone: {lead.get('phone') or 'not listed'}",
        f"- Address: {lead.get('address') or 'not listed'}",
        f"- Current site: {lead.get('website') or 'none'}",
        "",
        "## Opening line",
        opening,
        "",
        *ask_lines,
        "## Contact to give",
        f"- Email: {_contact()}",
        *([f"- Mapfront's line (the AI assistant answers, any time): {line}"]
          if line else []),
        "",
        "## Talking points",
        *[f"- {p}" for p in points],
        "",
        "## Likely objections",
        *[f"- **{q}** {a}" for q, a in OBJECTIONS],
        "",
        "## Price anchors",
        price_block,
        "",
        "## Before you dial",
        ("- If the number is a mobile, check it against the National Do Not Call "
         "Registry first; business landlines are generally exempt."),
        "- If they say not to call again, add them to the suppression list.",
    ]
    return "\n".join(lines) + "\n"


def _write(path: Path, text: str) -> str | None:
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return previous


async def call_brief(lead_id: int) -> ActionResult:
    lead_id = int(lead_id)
    text = await brief_text(lead_id)
    if text is None:
        return ActionResult(False, f"no such lead: {lead_id}")
    path = briefs_dir() / f"lead-{lead_id}.md"
    backup = path.with_suffix(".md.prev")
    previous = await asyncio.to_thread(_write, path, text)
    if previous is not None:
        await asyncio.to_thread(backup.write_text, previous, "utf-8")
    elif backup.exists():
        await asyncio.to_thread(backup.unlink)
    return ActionResult(True, f"call brief for lead {lead_id} at {path}",
                        evidence={"path": str(path), "text": text,
                                  "sha": hashlib.sha256(text.encode()).hexdigest()[:12]},
                        rollback_token=str(lead_id))


async def call_brief_dry(lead_id: int) -> ActionResult:
    if await _store.get_lead(int(lead_id)) is None:
        return ActionResult(False, f"no such lead: {lead_id}")
    return ActionResult(True, f"would write {briefs_dir() / f'lead-{int(lead_id)}.md'}")


async def call_brief_rollback(token: str) -> ActionResult:
    if not token:
        return ActionResult(True, "nothing to roll back")
    path = briefs_dir() / f"lead-{int(token)}.md"
    backup = path.with_suffix(".md.prev")

    def undo() -> str:
        if backup.exists():
            backup.replace(path)
            return "restored the previous brief"
        if path.exists():
            path.unlink()
            return "removed the brief"
        return "nothing was there"
    return ActionResult(True, await asyncio.to_thread(undo))
