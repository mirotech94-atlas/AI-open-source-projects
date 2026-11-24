"""agency.outreach — drafts, and the one email that sends itself.

`draft_outreach` writes a row; nothing about writing that row can reach a
third party. `send_outreach` is the only function in this module that can,
and every guard `send_dry` and `send_outreach` check lives in one place
(`_guarded`) so the two can never drift into checking different things —
`send_dry` IS what `send_outreach` would have done, minus the SMTP call.

**It is a card** (Tier ASK since 2026-10-27, six agents: every send to a
client waits for the owner). Even approved, it goes only past every guard below
AND the holds in `auto_hold` (an approved card answers the autosend holds),
each decided in code, never by a model:

  * the row is the draft `draft_outreach` wrote for the email channel: its
    stored SHA-256 (`agency_outreach.body_sha`) matches the body now, so a
    body edited by hand or written by anything else does not go;
  * the address is one the business published on its own site, read by
    `agency.find_email` (`agency_lead_emails`) -- never guessed, never typed;
  * the preview is live: `agency.publish_vercel` succeeded for this lead, so
    the email's link opens;
  * one outreach email per lead, ever, unless the lead wrote back: a second
    message to someone who never answered is the owner's call, not ATLAS's;
  * `AGENCY_OUTREACH_AUTOSEND` is not off (on by default).

A hold is answered by a card: `kernel.lead_followup.follow_site` raises
the ordinary Tier 2 `agency.send_outreach` card with "Held for you:
<reason>" first, and approving it runs every guard again under the card's
run (`answered_by_card`), where the holds -- and only the holds -- are
passed; the guards below still refuse. The rollback is honest about what
it can do: a delivered email cannot be unsent, so `send_outreach_rollback`
records that on the lead and suppresses the address, and nothing follows
it up.

Guards run in this order, matching AGENCY.md §2.5 and CAN-SPAM:

1. the outreach row exists and is still a `draft` (not already sent)
2. `to_address` is set and is not on the permanent suppression list
3. `sends_today() < AGENCY_DAILY_CAP` (default 10)
4. the sender is ATLAS Agency's fixed Gmail address (`AGENCY_SENDER`, the owner
   2026-10-30), never on example.com, and `GMAIL_APP_PASSWORD` is set: without
   it nothing is sent ("GMAIL_APP_PASSWORD missing")
5. the body names no "mapfront": a draft written before the switch to ATLAS
   Agency (2026-10-30) is refused, never sent with a dead link
6. CAN-SPAM: a postal address line, an opt-out line ("reply STOP" or
   "unsubscribe"), and a non-empty subject
7. at most one follow-up per lead: a `followup_of` row whose OWN
   `followup_of` is set would make this the second follow-up in the chain,
   and AGENCY.md is explicit that two is where it stops
8. the holds of `auto_hold`, listed above, unless an approved card is
   running this send

The email itself is written by code, not a model (`email_body`), in the owner's
own framing (2026-10-01, brand ATLAS Agency since 2026-10-30): "This is
ATLAS Agency. We looked at <business>'s website and thought it needed help
/ <business> has no website that we could find, so we built a free
preview: <url>. Reply or call <our number> and we'll walk you through it;
if you'd like, we can put you through to the owner." No claim about results, no figure, the
CAN-SPAM footer, and the `— sent by the owner's assistant` trailer -- the
assistant wrote every word, and the trailer says so honestly. The model
still drafts the `call_script` and `one_pager` kinds, which nothing sends.
An email draft needs a live preview to point at: without one it is refused,
because "we built you a free preview" would not be true yet.

Agency mail goes out FROM owner@example.com through Gmail SMTP
(smtp.gmail.com:587, STARTTLS) with the app password in
`GMAIL_APP_PASSWORD`, sender name "ATLAS Agency", Reply-To the same address
(`_gmail_send`). `_smtp_send` (AGENCY_SMTP_*) stays only for ATLAS Academy's
mail, which is not the agency's to change.

Sending itself uses `aiosmtplib` when it is importable, and falls back to
the stdlib `smtplib` off the event loop (`asyncio.to_thread`) otherwise — a
network call that blocks the loop is exactly what "async throughout" rules
out.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import os
from datetime import UTC, date, datetime, time, timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from typing import Any

from ...kernel.contracts import ActionResult
from ...kernel.llm import Message, Need, think
from ...kernel.permissions import wrap_untrusted
from .._http import explain
from . import _store
from .audit import audit_lead

TRAILER = "— sent by the owner's assistant"
# The head of every hold's detail, so `kernel.lead_followup` tells a hold
# (a card with the reason) from a refusal, the way `clips.posting.HELD` does.
HELD = "held for you"
# the owner, 2026-10-30: the agency is "ATLAS Agency". Agency mail goes out from
# this Gmail address (fixed: no env var can point it elsewhere) and its public
# link is the storefront. 2026-11-14: ATLAS Academy's mail (receipts, sign-up,
# support) uses the same address, so this is the ONE place the sender lives.
BRAND = "ATLAS Agency"
AGENCY_SENDER = "owner@example.com"
AGENCY_URL = "https://agency.example.com"
GMAIL_SMTP = ("smtp.gmail.com", 587)
GMAIL_MISSING = "GMAIL_APP_PASSWORD missing: nothing was sent"
# ATLAS Academy's sender (`_send_from_address`, `_smtp_send`) defaults to it too.
DEFAULT_SEND_FROM = AGENCY_SENDER
DEFAULT_SEND_DOMAIN = AGENCY_SENDER.partition("@")[2]

# Plain writing, adapted from the stop-slop skill (Hardik Pandya, MIT): an
# owner who spots an AI tell in the first line stops reading.
PLAIN_WRITING = (
    "Write like a person who looked at their site, not like an AI: no "
    "throat-clearing openers (\"I hope this finds you well\", \"here's the "
    "thing\"), no adverbs, no em dashes, no \"not X, it's Y\" contrasts, no "
    "punchy one-line closers, active voice, and the specific finding instead "
    "of a vague claim."
)

DRAFT_SYSTEM = (
    "You write cold outreach for a local business whose own website was "
    "just audited and found wanting. Lead with the concrete audit finding "
    "-- \"your site takes 11 seconds on a phone and is flagged insecure\" "
    "-- never \"I noticed your website\". Mention the mockup if one is "
    "given, as proof rather than a promise. Keep an email to three or four "
    "short sentences; a call_script reads like something spoken aloud; a "
    "one_pager is a few lines for a walk-in leave-behind. " + PLAIN_WRITING + " "
    "Output only the body text, nothing else."
)


def agency_sender() -> str:
    """The address every agency email goes out from (the owner, 2026-10-30)."""
    return AGENCY_SENDER


def sender_problem() -> str | None:
    """Why the agency sender cannot send right now: never from example.com
    (that is the storefront, not a mailbox), and never without the app
    password. Shared by outreach, replies and agreements."""
    domain = agency_sender().lower().partition("@")[2]
    if domain == "example.com" or domain.endswith(".example.com"):
        return "the sending address must not be on example.com"
    return gmail_problem()


def gmail_problem() -> str | None:
    """Why agency mail cannot go out now, or None."""
    if not os.getenv("GMAIL_APP_PASSWORD", "").strip():
        return GMAIL_MISSING
    return None


def _send_from_address() -> str:
    """ATLAS Academy's sender (unchanged; not the agency's). The address this module will actually put in `From:`. A separate
    `AGENCY_SEND_FROM` lets the mailbox ATLAS authenticates as
    (`AGENCY_SMTP_USER`, which may be a relay account) differ from the
    address a reply lands in, while still tying both to the one guard that
    matters: whichever address goes in `From:` must be on
    `AGENCY_SEND_DOMAIN`."""
    explicit = (os.getenv("AGENCY_SEND_FROM") or "").strip()
    if explicit:
        return explicit
    user = (os.getenv("AGENCY_SMTP_USER") or "").strip()
    return user if "@" in user else DEFAULT_SEND_FROM


def send_domain() -> str:
    """AGENCY_SEND_DOMAIN, default the domain of the agency sender (gmail.com)."""
    raw = os.getenv("AGENCY_SEND_DOMAIN")
    return (DEFAULT_SEND_DOMAIN if raw is None else raw).strip().lower()


def daily_cap() -> int:
    """Read fresh every call, like `prospect.cap()` — flipping the env var
    is a restart, not a code change."""
    try:
        return int(os.getenv("AGENCY_DAILY_CAP", "10"))
    except ValueError:
        return 10


def _local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """Local midnight to local midnight the next day, as aware datetimes —
    the same shape `kernel/review.py::_local_day_bounds` uses, and for the
    same reason: a bare `at::date = $1` filter truncates in whatever
    timezone the database SESSION happens to be in, not the caller's."""
    start = datetime.combine(day, time.min).astimezone()
    return start, start + timedelta(days=1)


async def sends_today() -> int:
    start, end = _local_day_bounds(datetime.now().astimezone().date())
    return await _store.count_sent_between(start, end)


def can_spam_ok(body: str, postal: str) -> list[str]:
    """CAN-SPAM must-haves this draft is checked against, returning what is
    missing (empty when compliant). The subject line is a separate,
    non-empty check the caller makes alongside this one — CAN-SPAM ties it
    to the message as a whole, not to the body text this function's fixed
    signature is given."""
    missing: list[str] = []
    postal = (postal or "").strip()
    if not postal:
        missing.append("postal address (AGENCY_POSTAL_ADDRESS is not set)")
    elif postal not in (body or ""):
        missing.append("postal address line")

    low = (body or "").lower()
    if "reply stop" not in low and "unsubscribe" not in low:
        missing.append("opt-out line (needs 'reply STOP' or 'unsubscribe')")
    return missing


async def _followup_ok(outreach: dict[str, Any]) -> bool:
    """AGENCY.md §2.5: one follow-up, then stop — enforced per LEAD, not
    per chain. Checking only `outreach`'s own parent would miss a second,
    independent follow-up chained onto the ORIGINAL message rather than
    onto the first one (two siblings, not a three-deep chain) — that is
    still a second follow-up to the same lead."""
    if outreach.get("followup_of") is None:
        return True                      # not a follow-up at all
    lead_id = outreach.get("lead_id")
    if lead_id is None:
        return True
    return not await _store.has_sent_followup(lead_id, outreach.get("id"))


# ------------------------------------------------------------------ holds

def autosend_enabled() -> bool:
    """`AGENCY_OUTREACH_AUTOSEND`: on by default; off holds every outreach
    email for a card, the way CLIPS_AUTOPUBLISH holds every clip."""
    return os.getenv("AGENCY_OUTREACH_AUTOSEND", "on").strip().lower() not in (
        "0", "off", "false", "no")


def answered_by_card() -> bool:
    """Whether this run is an approved card's: `approvals.decide` names the
    run `proposal:<id>` around the executor, so the hold it answers is
    passed. An unattended Tier 1 run names none."""
    from ...kernel import idempotency
    return (idempotency.current_run() or "").startswith("proposal:")


def body_sha(body: str) -> str:
    """The SHA-256 `draft_outreach` stores with the row and `auto_hold`
    checks before an unattended send."""
    return hashlib.sha256((body or "").encode()).hexdigest()


async def auto_hold(outreach: dict[str, Any]) -> str | None:
    """Why this email is held for the owner rather than sent on its own, or None.
    Asked last, after every guard of the draft itself, and never of an
    approved card: his approval is the answer to a hold."""
    if answered_by_card():
        return None
    if not autosend_enabled():
        return f"{HELD}: automatic sending is off (AGENCY_OUTREACH_AUTOSEND)"
    stored = str(outreach.get("body_sha") or "")
    if not stored or stored != body_sha(str(outreach.get("body") or "")):
        return (f"{HELD}: the body is not the draft draft_outreach wrote "
                "(its stored SHA-256 does not match)")
    lead_id = int(outreach.get("lead_id") or 0)
    to_address = (outreach.get("to_address") or "").strip().lower()
    from .contacts import emails_for
    published = {str(row.get("address") or "").strip().lower()
                 for row in await emails_for(lead_id)}
    if to_address not in published:
        return (f"{HELD}: {to_address} is not an address found on the "
                "business's own site (agency.find_email)")
    links = await _store.lead_links(lead_id)
    if preview_url(links) is None:
        return (f"{HELD}: the preview is not published on Vercel yet "
                "(agency.publish_vercel)")
    if await _store.has_outreach(lead_id) and (
            await _store.latest_lead_message(lead_id)) is None:
        return (f"{HELD}: lead {lead_id} was already emailed once and has not "
                "written back; a second message is yours to decide")
    return None


async def _guarded(outreach_id: int) -> tuple[dict[str, Any] | None, str | None]:
    """Every guard `send_dry` and `send_outreach` share, checked in the
    order the module docstring lists. Returns the outreach row once every
    guard passes, or `(None, reason)` naming the first one that failed."""
    outreach = await _store.get_outreach(outreach_id)
    if outreach is None:
        return None, f"no such outreach: {outreach_id}"
    if outreach.get("status") != "draft":
        return None, (f"outreach {outreach_id} is not a draft "
                      f"(status={outreach.get('status')!r})")

    from .verify import contact_refusal, us_refusal
    unverified = await contact_refusal(int(outreach.get("lead_id") or 0))
    if unverified is not None:
        return None, unverified
    not_us = await us_refusal(int(outreach.get("lead_id") or 0))
    if not_us is not None:
        return None, not_us

    to_address = (outreach.get("to_address") or "").strip().lower()
    if not to_address:
        return None, "outreach has no to_address"
    if await _store.is_suppressed(to_address):
        return None, f"{to_address} is on the suppression list"

    cap = daily_cap()
    sent_today = await sends_today()
    if sent_today >= cap:
        return None, f"daily send cap reached ({sent_today}/{cap})"

    problem = sender_problem()
    if problem:
        return None, problem

    body = outreach.get("body") or ""
    if "mapfront" in body.lower():
        return None, ("the draft names mapfront, written before the switch to "
                      "ATLAS Agency: draft it again")
    postal = os.getenv("AGENCY_POSTAL_ADDRESS", "")
    missing = can_spam_ok(body, postal)
    if not (outreach.get("subject") or "").strip():
        missing = [*missing, "subject"]
    if missing:
        return None, f"CAN-SPAM: missing {', '.join(missing)}"

    if not await _followup_ok(outreach):
        return None, "second follow-up: one follow-up per lead, then stop (AGENCY.md §2.5)"

    hold = await auto_hold(outreach)
    if hold is not None:
        return None, hold

    return outreach, None


# --------------------------------------------------------------- drafting

KINDS = ("email", "call_script", "one_pager")


def pretty_phone(number: str) -> str:
    """+17185550101 -> (718) 555-0101; anything that is not a US/Canada
    number is returned as given."""
    raw = (number or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    elif raw.startswith("+") or len(digits) != 10:
        return raw
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def mapfront_number() -> str:
    """The business line (`api.reception`), where the agency's AI assistant
    answers; "" while it is switched off -- nothing ever gives out a number
    nobody answers."""
    on = os.getenv("RECEPTION_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")
    number = os.getenv("TWILIO_FROM", "").strip()
    return pretty_phone(number) if on and number else ""


def callback_line() -> str:
    """The number a business rings back on. The AI answers it, so it is
    the agency's number, never "the owner's"."""
    number = mapfront_number()
    return f"Questions? Call or text {BRAND} at {number}." if number else ""


def more_info_line() -> str:
    """Every drafted reply to a lead carries this (the owner, 2026-09-30)."""
    number = mapfront_number()
    return (f"For more information, call or text {BRAND} at {number}; our AI "
            "assistant answers any time.") if number else ""


def escalation_phone() -> str:
    """the owner's own phone, for a lead who asks for him: AGENCY_ESCALATION_PHONE,
    else TWILIO_TO (the number ATLAS already rings him on). Never in a first
    cold email: only in replies, and on the line when someone asks for a
    person or is upset."""
    raw = (os.getenv("AGENCY_ESCALATION_PHONE", "").strip()
           or os.getenv("TWILIO_TO", "").strip())
    return pretty_phone(raw) if raw else ""


def escalation_line() -> str:
    number = escalation_phone()
    return f"To speak to the owner directly, call {number}." if number else ""


def preview_url(links: dict[str, Any]) -> str | None:
    """The public address of what was built for the lead, once it is live:
    the domain `agency.publish_vercel` recorded (or the client's own). None
    before that -- there is no box-hosted fallback any more (the owner,
    2026-10-30: demos live on Vercel only)."""
    if not links.get("deployed") or not links.get("domain"):
        return None
    return f"https://{links['domain']}/"


def call_me_url(links: dict[str, Any]) -> str | None:
    """The call-me form was served by this box, which no longer serves demos:
    no preview carries it, so there is no link."""
    return None


def site_lines(links: dict[str, Any]) -> list[str]:
    """The preview link and the call-me link, written by code so the model
    can neither drop nor garble them."""
    lines = []
    url = preview_url(links)
    if url:
        lines.append(f"See the site we built for you: {url}")
    call_me = call_me_url(links)
    if call_me:
        lines.append(f"Rather talk it through? Have us call you: {call_me}")
    return lines


_NOT_A_SITE = ("instagram.com", "facebook.com", "booksy.com", "yelp.com",
               "linktr.ee", "tiktok.com", "google.com", "square.site")


def has_own_site(lead: dict[str, Any]) -> bool:
    """A business whose only web presence is a social or booking page has no
    website of its own: the email must not say "your website" to it."""
    site = str(lead.get("website") or "").strip().lower()
    return bool(site) and not any(h in site for h in _NOT_A_SITE)


def email_subject(lead: dict[str, Any], *, followup: bool = False) -> str:
    name = " ".join(str(lead.get("name") or "your business").split())
    head = ("A free website for " if not has_own_site(lead)
            else "A free redesign demo for ")
    return ("Following up: " if followup else "") + head + name


def email_body(lead: dict[str, Any], live: str, links: dict[str, Any],
               *, followup: bool = False) -> str:
    """the owner's copy (2026-10-30), written by code so a model can neither
    inflate it nor drop a line. Two variants: no website of its own, or a
    website that can be improved (only the latter is told "we looked at your
    website"). Then the CAN-SPAM footer the send guard requires."""
    name = " ".join(str(lead.get("name") or "your business").split())
    asks = ("Just reply with what you'd want on it (booking, payments, a "
            "gallery, maps, reviews, whatever makes sense) and I'll take it "
            "from there.")
    if has_own_site(lead):
        intro = (f"I'm the owner, I run an agency called {BRAND}. I came across "
                 f"the website for {name} and thought it could be doing more "
                 f"for you, so I put together a demo of a redesigned one. No "
                 f"charge, have a look: {live}")
        offer = ("If you like it, we can get a proper version live in under a "
                 "week for $300, everything included. The real one has a lot "
                 "more to it than the demo (integrations, search, the works). "
                 + asks)
    else:
        intro = (f"I'm the owner, I run an agency called {BRAND}. I noticed "
                 f"{name} doesn't have a website yet, so I built a demo of one "
                 f"for you. No charge, have a look: {live}")
        offer = ("If you like it, we can have your live site up in under a "
                 "week for $300, everything included. The real one has a lot "
                 "more to it than the demo (integrations, search, the works). "
                 + asks)
    parts = ["Hi,"]
    if followup:
        parts.append("Following up on my last email in case it got buried.")
    parts.extend([intro, offer, "the owner"])
    postal = os.getenv("AGENCY_POSTAL_ADDRESS", "").strip()
    footer = f"{BRAND} · {AGENCY_URL}"
    parts.append(f"{footer} · {postal}" if postal else footer)
    parts.append("If you'd rather not hear from me, just reply stop.")
    return "\n\n".join(parts)


async def _model_draft(lead: dict[str, Any], audit: Any, mockup_path: str | None,
                       parent_id: int | None, live: str | None) -> str:
    """A call script or a one-pager, drafted by the model from the audit
    (quoted as untrusted data). Nothing sends these."""
    findings_text = "\n".join(f"- {f.detail}" for f in audit.findings) or "no findings recorded"
    built = (f"a new site, live at {live}"
             if live else ("a mockup, not public yet" if mockup_path else "not built yet"))
    material = (
        f"Business name: {lead.get('name')}\n"
        f"Category: {lead.get('category')}\n"
        f"Audit findings:\n{findings_text}\n"
        f"What the owner built: {built}\n"
        f"{'This is a follow-up to an earlier message that got no reply.' if parent_id else ''}"
    )
    wrapped = wrap_untrusted(material, "agency-audit")
    completion = await think(Need.DRAFT, DRAFT_SYSTEM, [Message("user", wrapped)],
                             max_tokens=800)
    return completion.text.strip()


async def draft_outreach(lead_id: int, kind: str) -> ActionResult:
    if kind not in KINDS:
        return ActionResult(False, f"unknown kind {kind!r}; must be one of {KINDS}")

    lead = await _store.get_lead(lead_id)
    if lead is None:
        return ActionResult(False, f"no such lead: {lead_id}")
    from .verify import contact_refusal
    unverified = await contact_refusal(lead_id)
    if unverified is not None:
        return ActionResult(False, f"refused: {unverified}")

    audit = await audit_lead(lead_id, lead.get("website"))
    mockup_path = await _store.latest_mockup_path(lead_id)
    parent_id = await _store.latest_sent_outreach_id(lead_id)
    links = await _store.lead_links(lead_id)
    live = preview_url(links)

    subject = None
    to_address = None
    sha = None
    if kind == "email":
        if live is None:
            return ActionResult(False, "refused: no live preview to point the email "
                                       "at; publish it on Vercel first (agency.publish_vercel)")
        # Only an address the business published on its own site
        # (`agency.find_email`), never a guess.
        from .contacts import published_email
        found = await published_email(lead_id)
        to_address = (found or {}).get("address")
        body = email_body(lead, live, links, followup=parent_id is not None)
        sha = body_sha(body)
        subject = email_subject(lead, followup=parent_id is not None)
    else:
        body = await _model_draft(lead, audit, mockup_path, parent_id, live)

    outreach_id = await _store.insert_outreach(
        lead_id, kind, to_address, subject, body, parent_id, body_sha=sha)
    if outreach_id is None:
        return ActionResult(False, "failed to write the outreach draft")

    lead_features = _store.parse_json(lead.get("features"), {})
    return ActionResult(
        True, f"drafted {kind} outreach #{outreach_id} for lead {lead_id}",
        evidence={"outreach_id": outreach_id, "kind": kind, "followup_of": parent_id,
                 "to": to_address, "subject": subject, "text": body,
                 "preview_url": live,
                 "lead_features": lead_features, "audit_severity": audit.severity,
                 "body_sha": body_sha(body)[:12]},
        rollback_token=str(outreach_id))


async def draft_outreach_dry(lead_id: int, kind: str) -> ActionResult:
    if kind not in KINDS:
        return ActionResult(False, f"unknown kind {kind!r}; must be one of {KINDS}")
    lead = await _store.get_lead(lead_id)
    if lead is None:
        return ActionResult(False, f"no such lead: {lead_id}")
    return ActionResult(True, f"would draft {kind} outreach for lead {lead_id}")


async def draft_outreach_rollback(token: str) -> ActionResult:
    if not token:
        return ActionResult(True, "nothing was written for this run; nothing to roll back")
    await _store.delete_outreach(int(token))
    return ActionResult(True, f"withdrew outreach draft {token}")


# ------------------------------------------------------------------ send

async def _lead_features_for(lead_id: int) -> dict[str, Any]:
    """What `agency.rank` last computed for this lead — copied into the
    outreach proposal's evidence so the judgment layer sees the same
    features it would score the lead on (score.py's docstring: `features()`
    is what Task 3 copies into the outreach proposal's evidence)."""
    lead = await _store.get_lead(lead_id)
    if lead is None:
        return {}
    return _store.parse_json(lead.get("features"), {})


async def send_dry(outreach_id: int) -> ActionResult:
    outreach, reason = await _guarded(outreach_id)
    if reason is not None:
        return ActionResult(False, reason)
    lead_features = await _lead_features_for(outreach["lead_id"])
    # Exactly what approving sends: the address, the subject, the words and
    # the preview they point at (the card shows them; nothing is sent here).
    links = await _store.lead_links(int(outreach["lead_id"]))
    return ActionResult(
        True, f"would send outreach {outreach_id} to {outreach['to_address']}",
        evidence={"lead_features": lead_features, "to": outreach.get("to_address"),
                  "subject": outreach.get("subject"), "text": outreach.get("body"),
                  "preview_url": preview_url(links)})


def _message(from_header: str, to_address: str, subject: str, body: str,
             message_id: str | None, reply_to: str | None = None,
             headers: dict[str, str] | None = None,
             html: str | None = None) -> EmailMessage:
    message = EmailMessage()
    message["From"] = from_header
    message["To"] = to_address
    message["Subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    if message_id:
        # Our own id, so the read-back can find this message (RA-D1).
        message["Message-ID"] = message_id
    for name, value in (headers or {}).items():
        message[name] = value
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")
    return message


async def _deliver(message: EmailMessage, host: str, port: int, user: str,
                   password: str) -> tuple[bool, str | None]:
    """STARTTLS, then login. Errors through `explain`: no password shows."""
    if importlib.util.find_spec("aiosmtplib") is not None:
        import aiosmtplib
        try:
            await aiosmtplib.send(message, hostname=host, port=port, username=user,
                                  password=password, start_tls=True)
        except Exception as exc:                            # noqa: BLE001
            return False, _scrub(explain(exc), password)
        return True, None

    def _send_sync() -> None:
        import smtplib
        with smtplib.SMTP(host, port, timeout=25) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(message)

    try:
        await asyncio.to_thread(_send_sync)
    except Exception as exc:                            # noqa: BLE001
        return False, _scrub(explain(exc), password)
    return True, None


def _scrub(reason: str, password: str) -> str:
    """`explain` redacts known token shapes; an app password has none, so
    take it out by value too."""
    return reason.replace(password, "[redacted]") if password else reason


async def _gmail_send(to_address: str, subject: str, body: str,
                      message_id: str | None = None) -> tuple[bool, str | None]:
    """Agency mail: From "ATLAS Agency" <owner@example.com> through
    smtp.gmail.com:587 STARTTLS with GMAIL_APP_PASSWORD. Fails closed, with
    no connection attempted, when the password is empty."""
    problem = gmail_problem()
    if problem:
        return False, problem
    password = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    sender = agency_sender()
    message = _message(formataddr((BRAND, sender)), to_address, subject, body,
                       message_id, reply_to=sender)
    host, port = GMAIL_SMTP
    ok, reason = await _deliver(message, host, port, sender, password)
    if not ok:
        return False, f"Gmail refused the message, nothing was sent: {reason}"
    return True, None


RESEND_SMTP = ("smtp.resend.com", 587, "resend")


def _smtp_config(from_address: str = "") -> tuple[str, int, str, str] | None:
    """ATLAS Academy's relay: the four AGENCY_SMTP_* when all are set; else
    Gmail SMTP when `from_address` is the agency's own Gmail sender and
    GMAIL_APP_PASSWORD is set (Resend cannot send From a gmail.com address);
    else Resend's SMTP relay from RESEND_API_KEY alone; else None. Raises
    ValueError when AGENCY_SMTP_PORT is set but not a number."""
    host = os.getenv("AGENCY_SMTP_HOST")
    port_raw = os.getenv("AGENCY_SMTP_PORT")
    user = os.getenv("AGENCY_SMTP_USER")
    password = os.getenv("AGENCY_SMTP_PASSWORD")
    if host and port_raw and user and password:
        return host, int(port_raw), user, password
    gmail_pw = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    if gmail_pw and from_address.strip().lower() == agency_sender().lower():
        return (*GMAIL_SMTP, agency_sender(), gmail_pw)
    key = os.getenv("RESEND_API_KEY", "").strip()
    if key:
        relay, port, login = RESEND_SMTP
        return relay, port, login, key
    return None


async def _smtp_send(from_address: str, to_address: str, subject: str,
                     body: str, message_id: str | None = None,
                     headers: dict[str, str] | None = None,
                     html: bool = False) -> tuple[bool, str | None]:
    """ATLAS Academy's mail (AGENCY_SMTP_*, else Gmail when it is sent from
    the agency's Gmail address, else Resend); agency mail uses `_gmail_send`."""
    try:
        config = _smtp_config(from_address)
    except ValueError:
        return False, "AGENCY_SMTP_PORT is not a number"
    if config is None:
        return False, ("mail is not configured: set AGENCY_SMTP_HOST/PORT/USER/"
                       "PASSWORD, or RESEND_API_KEY for Resend's SMTP relay")
    host, port, user, password = config
    page = None
    if html:                       # the Academy look; cold agency mail stays plain
        from ..academy import mail_template
        page = mail_template.render(subject, body)
    message = _message(from_address, to_address, subject, body, message_id,
                       headers=headers, html=page)
    ok, reason = await _deliver(message, host, port, user, password)
    if not ok and host == RESEND_SMTP[0]:
        reason = ("Resend refused the message, nothing was sent (is the sending "
                  f"domain verified in Resend?): {reason}")
    return ok, reason


def _owner_signed(body: str) -> bool:
    """the owner's own cold email (2026-10-31): signed "the owner" and ending on the
    CAN-SPAM opt-out line instead of the assistant trailer."""
    lines = [ln.strip() for ln in body.rstrip().splitlines() if ln.strip()]
    return ("the owner" in lines and bool(lines)
            and ("reply stop" in lines[-1].lower() or "unsubscribe" in lines[-1].lower()))


async def send_outreach(outreach_id: int) -> ActionResult:
    """Tier AUTO (2026-10-01), past `_guarded` and the holds of `auto_hold`.
    What leaves the SMTP server cannot be recalled: the rollback below says
    so and makes sure nothing follows it."""
    outreach, reason = await _guarded(outreach_id)
    if reason is not None:
        return ActionResult(False, reason)

    # Belt and braces on top of `_guarded`: only ever send what looks like
    # the draft this module itself wrote for the email channel.
    if outreach.get("kind") != "email":
        return ActionResult(False, f"refused: kind is {outreach.get('kind')!r}, not 'email'")
    body = outreach["body"]
    if not (body.endswith(TRAILER) or _owner_signed(body)):
        return ActionResult(False, "refused: body does not end with the assistant trailer "
                                   "or the owner's signature and the opt-out line")

    from_address = agency_sender()
    # A Message-ID of ATLAS's own making: SMTP returns no id, and this is the
    # one handle a read-back of the sent copy can search for (RA-D1).
    message_id = make_msgid(domain=from_address.rpartition("@")[2] or None)
    ok, send_reason = await _gmail_send(
        outreach["to_address"], outreach.get("subject") or "", body,
        message_id=message_id)
    if not ok:
        return ActionResult(False, f"send failed: {send_reason}")

    await _store.mark_outreach_sent(outreach_id, message_id)
    lead_features = await _lead_features_for(outreach["lead_id"])
    return ActionResult(
        True, f"sent outreach {outreach_id} to {outreach['to_address']}",
        evidence={"to": outreach["to_address"], "subject": outreach.get("subject"),
                 "message_id": message_id, "lead_features": lead_features},
        rollback_token=str(outreach_id))


async def send_outreach_rollback(token: str) -> ActionResult:
    """An email that was delivered cannot be unsent, and this does not
    pretend to. It records the fact on the lead and suppresses the address,
    so no follow-up, reply draft or second outreach ever goes to it."""
    if not token:
        return ActionResult(True, "nothing was sent for this run; nothing to roll back")
    outreach = await _store.get_outreach(int(token))
    if outreach is None:
        return ActionResult(False, f"no such outreach: {token}")
    to_address = (outreach.get("to_address") or "").strip().lower()
    why = (f"outreach #{token} was rolled back after delivery: the email cannot "
           "be unsent, so nothing further goes to this address")
    if to_address:
        await _store.add_suppression(to_address, why)
    lead_id = outreach.get("lead_id")
    if lead_id is not None:
        await _store.set_lead_meta(int(lead_id), "outreach_rolled_back", {
            "outreach_id": int(token), "to": to_address,
            "at": datetime.now(UTC).isoformat()})
    return ActionResult(
        True, f"outreach #{token} to {to_address or 'its address'} was already "
              "delivered and cannot be unsent; the address is suppressed so "
              "nothing follows it up, and the lead carries the record",
        evidence={"suppressed": to_address, "unsent": False})


# ------------------------------------------------------------ suppression

async def suppress(address: str, reason: str) -> ActionResult:
    address = (address or "").strip().lower()
    if not address:
        return ActionResult(False, "refused: empty address")
    await _store.add_suppression(address, reason)
    return ActionResult(True, f"suppressed {address}: {reason}", rollback_token=address)


async def suppress_dry(address: str, reason: str) -> ActionResult:
    address = (address or "").strip().lower()
    if not address:
        return ActionResult(False, "refused: empty address")
    return ActionResult(True, f"would suppress {address}: {reason}")


async def suppress_rollback(token: str) -> ActionResult:
    if not token:
        return ActionResult(True, "nothing to roll back")
    await _store.remove_suppression(token)
    return ActionResult(True, f"removed {token} from the suppression list")
