"""agency.sms -- texting a lead who asked to be texted.

`agency.text_lead` (Tier ASK, irreversible) sends one of a closed set of
texts whose every word is composed here, never by a model: the words are the
decision, so the card carries them whole (`card_detail` is not enough -- it
must be pure, and the lead's name is a read -- so the proposer puts the text
in the card's evidence, and `text_lead_dry` shows it again on approval).

    site_link    the disclosure, the link to the site we built, STOP line
    missed_call  the disclosure, "I tried calling as you asked", STOP line

A free-form reply goes out through `agency.send_reply` instead, drafted by
`agency.draft_reply` and checked there. Both reach Twilio only through
`send_sms` below, and only after `consent.contact_guard` passes: a live
consent for texts, not suppressed, AGENCY_SMS_ENABLED (an approved A2P
10DLC campaign), inside 08:00-21:00 lead time, under the contact cap.

Twilio error 21610 means the recipient texted STOP to our number: Twilio
already refuses to deliver, and ATLAS records it as an opt-out on the spot.
"""
from __future__ import annotations

import logging
import os

import httpx

from ...kernel.contracts import ActionResult
from .._http import explain
from . import _store, consent

log = logging.getLogger("atlas.agency.sms")

API = "https://api.twilio.com/2010-04-01/Accounts"
STOP_LINE = "Reply STOP to opt out."
KINDS = ("site_link", "missed_call")
TWILIO_UNSUBSCRIBED = 21610
MAX_SMS = 640


def site_url(links: dict) -> str | None:
    """The public address of what we built, or None. A local mockup path is
    not a link a lead can open, so it is never texted."""
    if links.get("domain"):
        return f"https://{links['domain']}"
    if links.get("webild_url"):
        return str(links["webild_url"])
    return None


async def compose(lead_id: int, kind: str, opening: str) -> tuple[str | None, str | None]:
    if kind not in KINDS:
        return None, f"unknown kind {kind!r}; must be one of {KINDS}"
    if kind == "site_link":
        url = site_url(await _store.lead_links(lead_id))
        if url is None:
            return None, "no public site or Webild page is on record for this lead yet"
        return f"{opening}\nHere it is: {url}\n{STOP_LINE}", None
    return (f"{opening}\nI tried calling as you asked. When's a good time for a "
            f"quick call?\n{STOP_LINE}"), None


async def send_sms(to: str, body: str) -> tuple[bool, str]:
    """(True, MessageSid) or (False, reason). The only Twilio SMS write in
    ATLAS; reached only from an action's run, through the executor."""
    sid = os.getenv("TWILIO_ACCOUNT_SID", "")
    token = os.getenv("TWILIO_AUTH_TOKEN", "")
    frm = os.getenv("AGENCY_SMS_FROM", "").strip() or os.getenv("TWILIO_FROM", "")
    if not (sid and token and frm):
        return False, "TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM not set"
    try:
        async with httpx.AsyncClient(timeout=20, auth=(sid, token)) as client:
            r = await client.post(f"{API}/{sid}/Messages.json",
                                  data={"From": frm, "To": to, "Body": body[:MAX_SMS]})
    except Exception as exc:  # noqa: BLE001
        return False, explain(exc)
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        code = data.get("code")
        if code == TWILIO_UNSUBSCRIBED:
            return False, "unsubscribed"
        return False, f"HTTP {r.status_code} from api.twilio.com (code {code})"
    return True, str(data.get("sid") or "")


async def deliver(lead_id: int, phone: str, opening: str, body: str) -> ActionResult:
    """Send `body` (which already opens with `opening`) and record the
    contact. A STOP Twilio already holds is recorded as the opt-out it is."""
    if not consent.discloses(body):
        return ActionResult(False, "refused: the text does not open with the AI disclosure")
    if STOP_LINE.lower() not in body.lower() and "stop" not in body.lower():
        return ActionResult(False, "refused: the text has no opt-out line")
    ok, detail = await send_sms(phone, body)
    if not ok and detail == "unsubscribed":
        await consent.opt_out(lead_id, phone=phone,
                              why="Twilio 21610: the number texted STOP")
        return ActionResult(False, f"not sent: {phone} has opted out at Twilio; "
                                   "consent revoked and the number suppressed")
    if not ok:
        return ActionResult(False, f"send failed: {detail}")
    contact_id = await _store.insert_contact(lead_id, "sms", phone, opening, body)
    if contact_id is not None:
        await _store.set_contact(contact_id, status="sent", sid=detail)
    return ActionResult(True, f"texted lead {lead_id}",
                        evidence={"sid": detail, "contact_id": contact_id,
                                  "opening": opening})


async def _guarded(lead_id: int, kind: str) -> tuple[tuple | None, str | None]:
    contact, why = await consent.contact_guard(lead_id, "sms")
    if contact is None:
        return None, why
    body, why = await compose(lead_id, kind, contact.opening)
    if body is None:
        return None, why
    return (contact, body), None


async def text_lead(lead_id: int, kind: str) -> ActionResult:
    """Tier ASK, irreversible: a text cannot be taken back."""
    ready, why = await _guarded(int(lead_id), kind)
    if ready is None:
        return ActionResult(False, f"refused: {why}")
    contact, body = ready
    return await deliver(int(lead_id), contact.phone, contact.opening, body)


async def text_lead_dry(lead_id: int, kind: str) -> ActionResult:
    ready, why = await _guarded(int(lead_id), kind)
    if ready is None:
        return ActionResult(False, str(why))
    contact, body = ready
    return ActionResult(True, f"would text {contact.phone}:\n{body}",
                        evidence={"to": contact.phone, "text": body})
