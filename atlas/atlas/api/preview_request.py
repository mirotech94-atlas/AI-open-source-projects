"""The "ask for a free preview" form on mapfront.io.

    POST /public/preview-request     a plain HTML form post

The landing page (atlas/modules/agency/landing/index.html) posts here: a
business name, who is asking, and a phone number or an email address. The
request is stored, posted to Miro's Slack as untrusted text and the visitor
is sent to the landing page's "thanks" page. Nothing is built, called,
texted or emailed from here: a stored request is a person asking to be
contacted, and what follows it goes through the agency's own actions.
A filled honeypot gets the same redirect and nothing else.

Speed to lead (Miro, 2026-10-06, from a reel: "a speed-to-lead agent that
calls every single new lead once they fill out a form"): when the visitor
gives a phone number AND ticks the call-me box (the exact wording
`modules/agency/callme.CONSENT_TEXT`, the same written consent the preview
sites' form takes), the request also becomes a lead row and a `web_form`
call consent, so `kernel.lead_followup.calls_due` has the assistant call
them back on its next tick inside calling hours (`agency.call_lead`, Tier 1
since 2026-10-06, past the consent guard; a refusal is the Tier 2 card). An
unticked box is a request to be contacted some other way: no consent row,
no call. Nothing here dials.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from .peers import PerAddressLimiter, effective_client

router = APIRouter()
log = logging.getLogger("atlas.preview_request")

PATH = "/public/preview-request"
SENT_URL = "https://mapfront.io/thanks/"
BACK_URL = "https://mapfront.io/#contact"
MAX_BODY = 16 * 1024
NAME_MAX, BUSINESS_MAX, PHONE_MAX, EMAIL_MAX, SITE_MAX, NOTE_MAX = 120, 160, 32, 254, 200, 2000
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_requests = PerAddressLimiter(5, 3600.0)


def _line(text: str, limit: int) -> str:
    """One line of someone else's text: no control characters, no newlines."""
    return _CONTROL.sub("", text).replace("\n", " ").replace("\r", " ").strip()[:limit]


def problems(name: str, business: str, phone: str, email: str, site: str,
             note: str) -> list[str]:
    out = []
    if not business:
        out.append("your business's name")
    elif len(business) > BUSINESS_MAX:
        out.append("a shorter business name")
    if not name:
        out.append("your name")
    elif len(name) > NAME_MAX:
        out.append("a shorter name")
    digits = sum(c.isdigit() for c in phone)
    phone_ok = 7 <= digits <= 15 and len(phone) <= PHONE_MAX
    email_ok = bool(_EMAIL.fullmatch(email)) and len(email) <= EMAIL_MAX
    if phone and not phone_ok:
        out.append("a phone number we can dial")
    if email and not email_ok:
        out.append("an email address we can reply to")
    if not phone and not email:
        out.append("a phone number or an email address")
    if len(site) > SITE_MAX:
        out.append("a shorter website address")
    if len(note) > NOTE_MAX:
        out.append("a shorter note")
    return out


def _sorry(title: str, text: str, status: int) -> PlainTextResponse:
    return PlainTextResponse(f"{title}\n\n{text}\n\nBack to the form: {BACK_URL}\n"
                             "Or email miro@mapfront.io.\n", status_code=status)


async def _store(name: str, business: str, phone: str, email: str, site: str,
                 note: str, client: str) -> int | None:
    from ..kernel.state import fetch_one
    row = await fetch_one(
        """INSERT INTO agency_preview_requests
               (name, business, phone, email, site, note, client)
           VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
        name, business, phone or None, email or None, site or None, note or None,
        client)
    return int(row["id"]) if row else None


class _Place:
    """The shape `_store.insert_lead` reads: a lead made from a form, not a
    Places listing. `place_id` is the request's own id, so a repeat post
    cannot make a second lead."""

    def __init__(self, request_id: int, business: str, phone: str, email: str,
                 site: str) -> None:
        self.place_id = f"preview-request:{request_id}"
        self.name = business
        self.category = "asked for a preview"
        self.address = None
        self.phone = phone or None
        self.website = site or None
        self.rating = None
        self.reviews = None
        self.business_status = None
        self.email = email or None


async def _consent_to_call(request_id: int, name: str, business: str, phone: str,
                           email: str, site: str, client: str, user_agent: str
                           ) -> str:
    """The ticked box becomes a lead and its call consent. Returns a one-line
    outcome for the Slack post."""
    from ..modules.agency import _store, callme, consent
    number = consent.normalize(phone)
    if number is None:
        return "asked to be called, but the number could not be read as a phone number"
    if await _store.is_suppressed(consent.tel_key(number)):
        return "asked to be called, but that number has opted out; no call"
    lead_id = await _store.insert_lead(_Place(request_id, business, number, email, site))
    if lead_id is None:
        return "asked to be called; the lead already existed"
    grants = consent.consent_from_web_form(number, callme.CONSENT_TEXT)
    if not grants:
        return "asked to be called, but the consent text did not read as consent"
    digest, keyed = callme.ip_hash(client)
    consent_id = await _store.insert_consent(
        lead_id, number, grants[0].scope, callme.SOURCE,
        f"{callme.SOURCE}:preview-request:{request_id}", grants[0].quoted,
        evidence={"page_url": BACK_URL, "ip_hash": digest, "ip_hash_keyed": keyed,
                  "user_agent": user_agent[:300],
                  "consent_version": callme.CURRENT_VERSION,
                  "consent_text": callme.CONSENT_TEXT, "name": name,
                  "preview_request_id": request_id})
    if consent_id is None:
        return "asked to be called, but the consent was not stored"
    await _store.insert_lead_message(
        lead_id, callme.SOURCE, f"{callme.SOURCE}:{consent_id}:{digest[:8]}", number, "",
        f"Asked Mapfront to call {number} (name given: {name}) on mapfront.io")
    return (f"asked to be called at {number}: lead {lead_id}, written consent "
            f"recorded; the assistant calls on the next tick inside calling hours")


async def _post(name: str, business: str, phone: str, email: str, site: str,
                note: str, call_line: str = "") -> None:
    from ..kernel.comms import Outbound, notify
    from ..kernel.permissions import wrap_untrusted
    reach = " / ".join(x for x in (phone, email) if x)
    asked = "\n".join(
        f"{label}: {value}" for label, value in (
            ("Business", business), ("Name", name), ("Phone", phone),
            ("Email", email), ("Current website", site), ("Note", note)) if value)
    body = ("Someone asked for a free preview on mapfront.io. They asked to be "
            f"contacted ({reach}); nothing has been built or sent yet."
            + (f" They {call_line}." if call_line else "")
            + " What they typed:\n" + wrap_untrusted(asked, "mapfront.io preview form"))
    await notify(Outbound(title=f"Mapfront: preview asked for by {name}",
                          body=body, topic="agency", kind="alert"))


@router.post(PATH)
async def preview_request(request: Request) -> Response:
    client = effective_client(request)
    if not _requests.allow(client):
        return _sorry("Too many requests",
                      "Please wait an hour before sending another.", 429)
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/x-www-form-urlencoded":
        return PlainTextResponse("form posts only", status_code=415)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY:
            return _sorry("That is too long", "Please shorten the note.", 413)
        chunks.append(chunk)
    form = parse_qs(b"".join(chunks).decode("utf-8", "replace"), keep_blank_values=True)

    def field(key: str, limit: int) -> str:
        return _line((form.get(key) or [""])[0], limit)

    if field("company_url", 8):                     # the honeypot: bots fill it
        return RedirectResponse(SENT_URL, status_code=303)
    name, business = field("name", NAME_MAX + 1), field("business", BUSINESS_MAX + 1)
    phone, email = field("phone", PHONE_MAX + 1), field("email", EMAIL_MAX + 1).lower()
    site = field("site", SITE_MAX + 1)
    note = _CONTROL.sub("", (form.get("note") or [""])[0]).strip()[:NOTE_MAX + 1]
    if bad := problems(name, business, phone, email, site, note):
        return _sorry("We couldn't send that",
                      "Please go back and add " + "; ".join(bad) + ".", 422)
    try:
        request_id = await _store(name, business, phone, email, site, note, client)
    except Exception as exc:                            # noqa: BLE001
        log.error("preview request not stored (%s)", type(exc).__name__)
        return _sorry("We couldn't take your request right now",
                      "Please try again in a few minutes.", 503)
    call_line = ""
    if field("callme", 8) and phone and request_id is not None:
        try:
            call_line = await _consent_to_call(
                request_id, name, business, phone, email, site, client,
                request.headers.get("user-agent", ""))
        except Exception as exc:                        # noqa: BLE001
            log.error("preview request %s: call consent not recorded (%s)",
                      request_id, type(exc).__name__)
            call_line = "asked to be called, but the consent could not be recorded"
    try:
        await _post(name, business, phone, email, site, note, call_line)
    except Exception as exc:                            # noqa: BLE001
        # Stored, so it reaches Miro on the next read; the visitor is done.
        log.warning("preview request not posted to Slack (%s)", type(exc).__name__)
    return RedirectResponse(SENT_URL, status_code=303)
