"""agency.callme -- "Have Mapfront call me", the written consent an AI call needs.

the owner, 2026-09-30: "once I approve [a site], you should call them and do a
proposal instead of me". `agency.call_lead` may ring only a lead with prior
express consent on record (TCPA; FCC, February 2024: an AI voice is an
artificial voice, and marketing needs the consent in writing). That gate is
not touched here. This is how the consent arrives, from the lead, in
writing:

  * **The form.** Every PREVIEW site (no package bought; `sitetemplate.
    is_preview`) carries `mapfront-callme.js` (`callme_widget.js`, this
    site's endpoint and the consent text baked in by `launch.finish_site`)
    and a mount on the home page (`slot`). It asks for a name, a phone number
    and an unchecked, required box whose label is `CONSENT_TEXT`, word for
    word, shows the number back ("We'll call (718) 555-0101"), and says the
    request goes to Mapfront, not to the business. It is built in the
    browser, not written as a `<form>`: the launch check holds every static
    form to the client's own form service, and this one is Mapfront's.
  * **The endpoint** (`api/call_me.py`, `POST /public/call-me/<slug>`)
    checks the site is deployed and the Origin is its own, rate-limits per
    address and per site, drops a filled honeypot silently, accepts only a
    real US number (`us_phone`), and records nothing unless `consent` is
    literally true. `submit` then writes one `agency_lead_consent` row,
    source `web_form`, scope `call`, with its evidence: the page URL, the
    time, an HMAC of the IP address (`ip_hash`), the user agent, the exact
    consent text shown (looked up by the version the page carries, never
    taken from the request), the name given, and whether the number is the
    lead's listed one. The name and page are the visitor's words: untrusted,
    stored as received, never in a prompt.
  * **Suppression wins.** A number on the suppression list (an earlier STOP
    or "don't call") is not re-consented by a form; nothing is recorded and
    the answer is the same as for everyone else, so the form cannot be used
    to learn who opted out.
  * **The form IS the consent, and the consent is the gate.**
    `kernel.lead_followup.call_due` places the `agency.call_lead` call
    through the executor (Tier 1 since 2026-10-06, the owner: "call them as
    well"), only while `consent.contact_guard` passes (verified lead, calls
    on, 08:00-21:00 lead time, under the cap, not suppressed); outside
    calling hours it waits for the next tick inside them, and a refusal is
    the Tier 2 card with the reason. The call opens with the AI disclosure.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import _store, consent, sitechat
from . import client as _client
from .client import ClientData

log = logging.getLogger("atlas.agency.callme")

WIDGET = "mapfront-callme.js"
ANCHOR = "call-me"
SOURCE = "web_form"
KEY_ENV = "AGENCY_CALLME_IP_KEY"

CONSENT_TEXT = (
    "I agree that Mapfront may call me at this number about this website, "
    "including with an AI assistant. Consent isn't required to buy anything. "
    "Reply STOP or say 'don't call' anytime.")


def version_of(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


# Every consent text a page may carry, by the version it posts. A new wording
# is added here, never edited in place: an old page still names its own text.
CONSENT_VERSIONS: dict[str, str] = {version_of(CONSENT_TEXT): CONSENT_TEXT}
CURRENT_VERSION = version_of(CONSENT_TEXT)

NAME_MAX = 80
PAGE_MAX = 300
UA_MAX = 300
BODY_MAX = 4 * 1024
PER_IP = 3               # submissions per address ...
PER_IP_WINDOW_S = 600.0  # ... per ten minutes
PER_SITE = 20            # submissions per site per day
DONE_MESSAGE = ("Thanks. Mapfront has your request, and the owner reviews every one "
                "before anyone calls.")


# ------------------------------------------------------------------- build

def endpoint(slug: str) -> str:
    return f"{sitechat.api_base()}/public/call-me/{slug}"


def wanted(client: ClientData) -> bool:
    """Only a preview carries the form: once a client has bought, their own
    customers must never see "Have Mapfront call me". And only a designed
    template page (`client.theme`), the one with the `$callme` slot. And
    only while the endpoint has a public door (`client.PUBLIC_DOOR`)."""
    return _client.PUBLIC_DOOR and client.package is None and bool(client.theme)


def slot(client: ClientData) -> str:
    """The home page's mount and script, or nothing on a bought site."""
    if not wanted(client):
        return ""
    return (f'<div id="{ANCHOR}" data-atlas="callme"></div>'
            f'<script src="{WIDGET}" defer data-atlas="callme"></script>')


_TEMPLATE = Path(__file__).with_name("callme_widget.js")
_CONFIG_MARK = "/*MAPFRONT_CALLME_CONFIG*/null"


def widget_js(slug: str, client: ClientData) -> str:
    """The widget with this site's endpoint, the consent text and its
    version, and Mapfront's line baked in. Blocking (reads the template)."""
    from .outreach import mapfront_number
    from .sitekit import _js
    number = mapfront_number()
    config = {"endpoint": endpoint(slug), "business": client.name,
              "consent": CONSENT_TEXT, "version": CURRENT_VERSION,
              "line": number or None,
              "lineTel": ("tel:+1" + "".join(c for c in number if c.isdigit()))
              if number else None}
    template = _TEMPLATE.read_text()
    assert _CONFIG_MARK in template
    return template.replace(_CONFIG_MARK, _js(config), 1)


# ---------------------------------------------------------------- the form

def us_phone(raw: object) -> str | None:
    """E.164 for a real-looking US number (+1, area code and exchange not
    starting 0 or 1), or None. Nothing else is called."""
    if not isinstance(raw, str) or len(raw) > 40:
        return None
    number = consent.normalize(raw)
    if number is None or not number.startswith("+1") or len(number) != 12:
        return None
    area, exchange = number[2:5], number[5:8]
    if area[0] in "01" or exchange[0] in "01" or area[1:] == "11":
        return None
    return number


def ip_hash(ip: str) -> tuple[str, bool]:
    """(HMAC-SHA256 of the address, keyed?). Keyed by AGENCY_CALLME_IP_KEY,
    else SESSION_SECRET; an unkeyed IPv4 hash can be reversed by trying them
    all, so the evidence says when it is not keyed."""
    key = (os.getenv(KEY_ENV, "") or os.getenv("SESSION_SECRET", "")).encode()
    if key:
        return hmac.new(key, ip.encode(), hashlib.sha256).hexdigest(), True
    return hashlib.sha256(f"mapfront-callme:{ip}".encode()).hexdigest(), False


def clean_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if not 0 < len(text) <= limit:
        return None
    return text


@dataclass(frozen=True, slots=True)
class Submitted:
    lead_id: int | None
    consent_id: int | None
    phone: str | None
    recorded: bool
    why: str = ""            # why nothing was recorded (never shown to the visitor)
    listed: bool = False     # the number is the lead's listed (Places) one


class NotHere(LookupError):
    """No deployed site by that label, or no lead behind it."""


async def site_lead(slug: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(site, lead) for a deployed site label, or NotHere."""
    if not sitechat.valid_slug(slug):
        raise NotHere(slug)
    site = await _store.get_site(slug)
    if site is None or site.get("deployed_at") is None or site.get("lead_id") is None:
        raise NotHere(slug)
    lead = await _store.get_lead(int(site["lead_id"]))
    if lead is None:
        raise NotHere(slug)
    return site, lead


def origins(site: dict[str, Any]) -> tuple[str, ...]:
    out = [sitechat.site_origin(str(site["slug"]))]
    if site.get("domain"):
        out.append(f"https://{str(site['domain']).lower().rstrip('/')}")
    return tuple(out)


async def submit(slug: str, *, name: str, phone: str, page_url: str, ip: str,
                 user_agent: str, version: str) -> Submitted:
    """Record the consent a visitor just gave on `slug`'s form. The caller
    has checked the shape; `phone` is already E.164 and `version` known."""
    _site, lead = await site_lead(slug)
    lead_id = int(lead["id"])
    if await _store.is_suppressed(consent.tel_key(phone)):
        return Submitted(lead_id, None, phone, False, "suppressed")
    digest, keyed = ip_hash(ip)
    listed = consent.normalize(lead.get("phone"))
    evidence = {"page_url": page_url, "ip_hash": digest, "ip_hash_keyed": keyed,
                "user_agent": user_agent[:UA_MAX], "consent_version": version,
                "consent_text": CONSENT_VERSIONS[version], "name": name,
                "matches_listed_phone": listed == phone, "site": slug}
    grants = consent.consent_from_web_form(phone, CONSENT_VERSIONS[version])
    if not grants:
        return Submitted(lead_id, None, phone, False, "not a consent")
    existing = await _store.active_consent(phone, "call")
    if existing is not None and int(existing["lead_id"]) == lead_id:
        consent_id = int(existing["id"])
    else:
        tag = hashlib.sha256(phone.encode()).hexdigest()[:8]
        consent_id = await _store.insert_consent(
            lead_id, phone, grants[0].scope, SOURCE, f"{SOURCE}:{slug}:{tag}",
            grants[0].quoted, evidence=evidence)
        if consent_id is None:
            return Submitted(lead_id, None, phone, False, "the consent was not stored")
    # Their word, for the contact cap (a lead who just asked is not "unanswered")
    # and the lead's history. Stored as received; untrusted.
    await _store.insert_lead_message(
        lead_id, SOURCE, f"{SOURCE}:{consent_id}:{digest[:8]}", phone, "",
        f"Asked Mapfront to call {phone} (name given: {name}) on {page_url}")
    return Submitted(lead_id, consent_id, phone, True, listed=listed == phone)


async def alert(lead: dict[str, Any], sub: Submitted, card: str, *,
                name: str, page_url: str) -> None:
    """Slack, HIGH: a lead asked to be called. What they gave is quoted."""
    from ...kernel.comms import Outbound, notify
    from ...kernel.contracts import Severity
    match = ("yes, the number Google lists for them" if sub.listed else
             "no: not the number Google lists, so it may be the owner's mobile, "
             "or someone else's")
    body = (f"*Business:* {lead.get('name')} ({lead.get('category')})\n"
            f"*They asked to be called at:* {sub.phone}\n"
            f"*Name given (quoted, untrusted):* {json.dumps(name)}\n"
            f"*On the page:* {page_url}\n"
            f"*Their listed number?* {match}\n"
            f"*Consent:* written, on the form, box ticked ({CONSENT_TEXT!r})\n"
            f"*Next:* {card}")
    try:
        await notify(Outbound(
            title=f"{lead.get('name')} asked Mapfront to call them",
            body=body, severity=Severity.HIGH, topic="agency", kind="alert"))
    except Exception as exc:                            # noqa: BLE001
        log.warning("callme: alert not posted: %s", type(exc).__name__)
    from . import owner_wa
    await owner_wa.notify_owner(
        "portal", f"{lead.get('name')} asked Mapfront to call them at {sub.phone}.")
