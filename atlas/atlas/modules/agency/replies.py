"""agency.replies -- answering a lead: drafted with the playbook, sent by itself.

Miro, 2026-10-06: "make the lead replies send themselves too -- approve
invoices too and schedule meetings with them via email as well". Under his
standing rule (2026-09-30) that anything not urgent runs without a card,
the reply and the agreement are Tier AUTO with every rule in code; a hold
or a refusal is the Tier 2 card with "Held for you: <reason>"
(`kernel.lead_followup`), never a send.

  * `agency.draft_reply` (Tier AUTO): writes a draft row, sends nothing. The
    model gets the sales playbook (`playbook.full()`) and the price terms in
    its SYSTEM prompt, and the lead's words, audit findings and what we
    built in the user turn inside `wrap_untrusted()`. For an interested lead
    it is also given two or three REAL free slots read from Miro's calendar
    just now (`meetings.free_times`, `meetings.choose`) and told to offer
    exactly those; they are stored on the draft (`offered_slots`) so the
    lead's answer can be matched to one in code (`leads.take_in`). What
    comes back is checked in code before it is kept: every figure against
    the price rules (`pricing.price_refusal`), every promise against what
    `build_site` delivers (`pricing.promise_refusal`), the honesty rules
    (`playbook.honesty_refusal`), and every time it names against the slots
    it was given (`meetings.slot_refusal`: a time that is not one of them,
    or any time when none were offered, is an invented appointment). A
    refused draft is not written at all.
  * `agency.send_reply` (Tier AUTO): sends a draft by email or, only with the
    lead's consent, by text. Every check above runs again at send time with
    fresh terms -- a deal recorded since makes the lead a returning client --
    plus the channel's own rules (CAN-SPAM and the sending domain for email;
    `consent.contact_guard` for a text), suppression, and last the hold:
    `AGENCY_REPLY_AUTOSEND` off (on by default) holds every reply for a
    card, with the `outreach.HELD` prefix the kernel reads. The rollback is
    honest: a delivered message is not unsent; it suppresses the address or
    number and records the fact on the lead.
  * `agency.send_agreement` (Tier ASK, a card): the agreement and the payment link,
    composed here from the configured scope and price, never by a model.
    Proposed when a lead agrees -- at their price on a call, or in words
    read in code from their email (`pricing.accepted_price`); Miro approves. The link is
    Miro's own Stripe Payment Link for that exact price
    (`AGENCY_PAYMENT_LINKS`, "500=https://buy.stripe.com/..,300=.."):
    ATLAS creates nothing at Stripe, and no link for the price means no
    send. This is money IN to Miro's account, never money out, so it is not
    Tier 3. Its rollback is as honest as the reply's.
  * `agency.record_deal` (Tier AUTO, principal_only): Miro records a deal as
    won, paid or lost. Only his own word reaches it -- a won or paid row
    makes that lead a returning client at $300, so it is not a thing an email
    or a model should be able to write.
"""
from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from functools import partial
from typing import Any

from ...kernel.contracts import ActionResult
from ...kernel.llm import Message, Need, think
from ...kernel.permissions import wrap_untrusted
from ...kernel.voice_persona import EMAIL_STYLE, reask, voice_problems, voice_refusal
from . import _store, consent, meetings, outreach, playbook, pricing, sms
from .audit import audit_lead

CHANNELS = ("email", "sms")
DEAL_STATUSES = ("won", "paid", "lost")

REPLY_SYSTEM = (
    "You write the reply from Miro's AI assistant to a small business owner "
    "who answered Miro's message about a website he built for them. Follow "
    "the playbook below. Answer what they actually said, in their words, "
    "tie value to their own audit findings, and end with one clear next "
    "step. {length} Plain text only: no markdown, no subject line, no "
    "signature (one is added). Never claim to be Miro or a person.\n\n"
    + EMAIL_STYLE + "\n\n"
    "{playbook}\n\n{prices}\n\n{deliverables}"
)
LENGTH = {"email": "Three to six short sentences.",
          "sms": "Two or three short sentences, under 300 characters. Do not "
                 "introduce yourself; the introduction is added."}


# ------------------------------------------------------------------ shared

def autosend_enabled() -> bool:
    """`AGENCY_REPLY_AUTOSEND`: on by default; off holds every reply for a
    card, the way AGENCY_OUTREACH_AUTOSEND holds every outreach email."""
    return os.getenv("AGENCY_REPLY_AUTOSEND", "on").strip().lower() not in (
        "0", "off", "false", "no")


async def _context(lead_id: int) -> dict[str, Any]:
    lead = await _store.get_lead(lead_id)
    if lead is None:
        raise LookupError(f"no such lead: {lead_id}")
    message = await _store.latest_lead_message(lead_id)
    bodies = await _store.lead_message_bodies(lead_id)
    return {"lead": lead, "message": message, "objection": pricing.objected(*bodies)}


def refusal(text: str, terms: pricing.Terms, *,
            offered: list[datetime] | None = None) -> str | None:
    """Every content rule a lead-facing text must pass, first failure first.
    `offered`: the calendar slots the text was allowed to name (a list, empty
    when none were) -- None skips that check, for a spoken line whose times
    are read out by code."""
    return (pricing.price_refusal(text, terms)
            or pricing.promise_refusal(text)
            or playbook.honesty_refusal(text)
            or (meetings.slot_refusal(text, offered) if offered is not None else None))


def _email_finish(body: str) -> str:
    """Where to call for more (ATLAS Agency's line, answered by the AI) and for
    Miro himself (Miro, 2026-09-30: a reply always carries both; a first
    cold email never carries his number), then the same CAN-SPAM lines and
    assistant trailer outreach carries. Written by code, never the model."""
    extra = [line for line in (outreach.more_info_line(), outreach.escalation_line())
             if line and line not in body]
    if extra:
        body = body.rstrip() + "\n\n" + "\n".join(extra)
    postal = os.getenv("AGENCY_POSTAL_ADDRESS", "").strip()
    if postal and postal not in body:
        body = body.rstrip() + f"\n\n{postal}"
    if "reply stop" not in body.lower() and "unsubscribe" not in body.lower():
        body = body.rstrip() + "\n\nReply STOP to opt out."
    if outreach.TRAILER not in body:
        body = body.rstrip() + f"\n\n{outreach.TRAILER}"
    return body


def _email_problem(to: str, subject: str | None, body: str) -> str | None:
    """The sender and CAN-SPAM rules `outreach._guarded` applies."""
    sender = outreach.sender_problem()
    if sender:
        return sender
    if "mapfront" in body.lower():
        return "the draft names mapfront: draft it again as ATLAS Agency"
    missing = outreach.can_spam_ok(body, os.getenv("AGENCY_POSTAL_ADDRESS", ""))
    if not (subject or "").strip():
        missing.append("subject")
    if missing:
        return f"CAN-SPAM: missing {', '.join(missing)}"
    if not body.rstrip().endswith(outreach.TRAILER):
        return "the body does not end with the assistant trailer"
    return None


async def _recipient(lead_id: int, channel: str) -> tuple[str | None, str | None]:
    from .verify import contact_refusal
    unverified = await contact_refusal(lead_id)
    if unverified is not None:
        return None, unverified
    if channel == "email":
        address = await _store.lead_email(lead_id)
        if not address:
            return None, "no email address on record for this lead"
        if await _store.is_suppressed(address.strip().lower()):
            return None, f"{address} is on the suppression list"
        return address, None
    row = await _store.consent_for_lead(lead_id, "sms")
    if row is None:
        return None, ("no consent on record to text this lead; draft an email, or "
                      "use agency.call_brief for Miro to call")
    return str(row["phone"]), None


# ------------------------------------------------------------- draft_reply

NO_TIMES = ("Offer NO day or time for a call: say Miro will send a couple of times "
            "himself.")


async def _times_block(intent: str | None) -> tuple[list[datetime], str]:
    """The slots an interested lead is offered, read from the calendar now,
    and the instruction the model gets. Code decides the set; the model only
    words it. Any other intent is offered nothing (and may name no time)."""
    if intent != "interested":
        return [], ""
    slots, why = await meetings.free_times()
    if slots is None:
        return [], f"Miro's calendar could not be read ({why}). {NO_TIMES}"
    offered = meetings.choose(slots)
    if not offered:
        return [], f"Miro has no free business-hours slot this week. {NO_TIMES}"
    listed = "\n".join(f"{i}) {meetings.offered_line(s)}" for i, s in enumerate(offered, 1))
    return offered, (
        f"Times Miro can do a {meetings.minutes()}-minute call, read from his "
        f"calendar just now ({meetings.tz().key}). Offer EXACTLY these, written with the "
        f"weekday, date and time as listed, and no other day or time:\n{listed}")


async def _material(ctx: dict[str, Any], times: str = "") -> str:
    lead = ctx["lead"]
    audit = await audit_lead(int(lead["id"]), lead.get("website"))
    findings = "\n".join(f"- {f.detail}" for f in audit.findings) or "- none recorded"
    links = await _store.lead_links(int(lead["id"]))
    from .preview_card import shared
    if await shared(int(lead["id"])):
        built = sms.site_url(links) or ("a mockup" if links.get("mockup") else "nothing yet")
    else:
        # Miro, 2026-10-28: the preview link reaches the owner only on the
        # email card he approves, never in an automatic reply.
        built = ("a free preview; Miro sends the link himself, so do not give one"
                 if sms.site_url(links) else
                 "a mockup" if links.get("mockup") else "nothing yet")
    message = ctx["message"] or {}
    return (f"Business: {lead.get('name')} ({lead.get('category')})\n"
            f"Their current site's audit findings:\n{findings}\n"
            f"What Miro built for them: {built}\n"
            + (f"{times}\n" if times else "")
            + f"Their latest message ({message.get('channel', 'unknown')}):\n"
            f"{message.get('body') or '(none on record)'}")


async def draft_reply(lead_id: int, channel: str) -> ActionResult:
    lead_id = int(lead_id)
    if channel not in CHANNELS:
        return ActionResult(False, f"unknown channel {channel!r}; one of {CHANNELS}")
    try:
        ctx = await _context(lead_id)
    except LookupError as exc:
        return ActionResult(False, str(exc))
    to, why = await _recipient(lead_id, channel)
    if to is None:
        return ActionResult(False, f"refused: {why}")
    terms = await pricing.terms(lead_id)
    offered, times = await _times_block((ctx["message"] or {}).get("intent"))
    system = REPLY_SYSTEM.format(
        length=LENGTH[channel], playbook=playbook.guidance("full"),
        prices=terms.describe(), deliverables=pricing.deliverables_block())
    material = wrap_untrusted(await _material(ctx, times), "agency-lead-reply")
    turns = [Message("user", material)]
    completion = await think(Need.DRAFT, system, turns, max_tokens=600, answer=True)
    text = " ".join(completion.text.split()) if channel == "sms" else completion.text.strip()
    if not text:
        return ActionResult(False, "the model returned nothing")
    if bad := voice_problems(text):
        again = await reask(partial(think, answer=True), Need.DRAFT, system, turns, text,
                            bad, max_tokens=600)
        if again:
            text = " ".join(again.split()) if channel == "sms" else again
    why = (refusal(text, terms, offered=offered)
           or voice_refusal(text))
    if why is not None:
        return ActionResult(False, f"refused the draft: {why}",
                            evidence={"refused": why})

    subject = None
    if channel == "sms":
        try:
            opening = consent.disclosure(str(ctx["lead"].get("name") or ""),
                                         calling=False)
        except ValueError as exc:
            return ActionResult(False, f"refused: {exc}")
        body = f"{opening}\n{text}\n{sms.STOP_LINE}"
    else:
        body = _email_finish(text)
        previous = (ctx["message"] or {}).get("subject") or ""
        subject = previous if previous.lower().startswith("re:") else (
            f"Re: {previous}" if previous else f"About {ctx['lead'].get('name')}'s website")
    message_id = (ctx["message"] or {}).get("id")
    slots = [s.isoformat() for s in offered] or None
    draft_id = await _store.insert_reply_draft(
        lead_id, message_id, "reply", channel, to, subject, body,
        pricing.find_prices(body), offered_slots=slots)
    if draft_id is None:
        return ActionResult(False, "failed to write the reply draft")
    return ActionResult(
        True, f"drafted {channel} reply #{draft_id} for lead {lead_id}",
        evidence={"draft_id": draft_id, "to": to, "text": body,
                  "prices": pricing.find_prices(body), "offered_slots": slots,
                  "returning": terms.returning, "terms_read_at": terms.read_at.isoformat(),
                  "body_sha": hashlib.sha256(body.encode()).hexdigest()[:12]},
        rollback_token=str(draft_id))


async def draft_reply_dry(lead_id: int, channel: str) -> ActionResult:
    if channel not in CHANNELS:
        return ActionResult(False, f"unknown channel {channel!r}; one of {CHANNELS}")
    if await _store.get_lead(int(lead_id)) is None:
        return ActionResult(False, f"no such lead: {lead_id}")
    to, why = await _recipient(int(lead_id), channel)
    if to is None:
        return ActionResult(False, str(why))
    return ActionResult(True, f"would draft a {channel} reply to {to}")


async def draft_reply_rollback(token: str) -> ActionResult:
    if not token:
        return ActionResult(True, "nothing was written; nothing to roll back")
    await _store.discard_reply_draft(int(token))
    return ActionResult(True, f"withdrew reply draft {token}")


# -------------------------------------------------------------- send_reply

async def _guard_reply(draft_id: int) -> tuple[dict[str, Any] | None, str | None]:
    draft = await _store.get_reply_draft(int(draft_id))
    if draft is None:
        return None, f"no such reply draft: {draft_id}"
    if draft.get("status") != "draft" or draft.get("kind") != "reply":
        return None, f"draft {draft_id} is not an unsent reply (status={draft.get('status')!r})"
    lead_id = int(draft["lead_id"])
    from .verify import contact_refusal
    unverified = await contact_refusal(lead_id)
    if unverified is not None:
        return None, unverified
    body = str(draft.get("body") or "")
    terms = await pricing.terms(lead_id)
    why = (refusal(body, terms,
                   offered=meetings.parse_slots(draft.get("offered_slots")))
           or voice_refusal(body))
    if why is not None:
        return None, f"the draft no longer passes: {why}"
    if draft["channel"] == "email":
        to = str(draft["to_address"]).strip().lower()
        if await _store.is_suppressed(to):
            return None, f"{to} is on the suppression list"
        problem = _email_problem(to, draft.get("subject"), body)
        if problem:
            return None, problem
    else:
        contact, why = await consent.contact_guard(lead_id, "sms")
        if contact is None:
            return None, why
        if consent.normalize(str(draft["to_address"])) != contact.phone:
            return None, "the draft's number is not the number the lead consented on"
        if not consent.discloses(body):
            return None, "the text does not open with the AI disclosure"
    # Last, the hold -- after every rule of the draft itself, and never of
    # an approved card: Miro's approval is the answer to a hold.
    if not outreach.answered_by_card() and not autosend_enabled():
        return None, f"{outreach.HELD}: automatic replies are off (AGENCY_REPLY_AUTOSEND)"
    return draft, None


async def send_reply(draft_id: int) -> ActionResult:
    """Tier AUTO (2026-10-06), only past `_guard_reply`. A sent email or text
    cannot be taken back: the rollback says so and suppresses the address."""
    draft, why = await _guard_reply(int(draft_id))
    if draft is None:
        return ActionResult(False, f"refused: {why}")
    body = str(draft["body"])
    if draft["channel"] == "email":
        from email.utils import make_msgid
        sender = outreach.agency_sender()
        ok, reason = await outreach._gmail_send(
            str(draft["to_address"]), str(draft.get("subject") or ""), body,
            message_id=make_msgid(domain=sender.rpartition("@")[2] or None))
        if not ok:
            return ActionResult(False, f"send failed: {reason}")
        result = ActionResult(True, f"emailed reply #{draft_id} to {draft['to_address']}")
    else:
        opening = body.splitlines()[0]
        result = await sms.deliver(int(draft["lead_id"]), str(draft["to_address"]),
                                   opening, body)
        if not result.ok:
            return result
    await _store.mark_reply_sent(int(draft_id))
    return ActionResult(True, result.detail,
                        evidence={**result.evidence, "draft_id": int(draft_id),
                                  "to": draft["to_address"], "text": body,
                                  "channel": draft["channel"]},
                        rollback_token=str(int(draft_id)))


async def _rolled_back(kind: str, draft: dict[str, Any], token: str) -> ActionResult:
    """What an honest rollback of a delivered message can do: suppress where
    it went and record the fact on the lead, so nothing follows it."""
    to = str(draft.get("to_address") or "").strip().lower()
    key = to if draft.get("channel") == "email" else (
        consent.tel_key(consent.normalize(to) or to) if to else "")
    why = (f"{kind} #{token} was rolled back after delivery: a sent message cannot "
           "be unsent, so nothing further goes to this address")
    if key:
        await _store.add_suppression(key, why)
    await _store.set_lead_meta(int(draft["lead_id"]), f"{kind}_rolled_back", {
        "draft_id": int(token), "to": to, "at": datetime.now(UTC).isoformat()})
    return ActionResult(
        True, f"{kind} #{token} to {to or 'the lead'} was already delivered and cannot "
              "be unsent; the address is suppressed so nothing follows it up, and the "
              "lead carries the record",
        evidence={"suppressed": key, "unsent": False})


async def send_reply_rollback(token: str) -> ActionResult:
    """A delivered reply is not unsent, and this does not pretend to."""
    if not token:
        return ActionResult(True, "nothing was sent for this run; nothing to roll back")
    draft = await _store.get_reply_draft(int(token))
    if draft is None:
        return ActionResult(False, f"no such reply draft: {token}")
    return await _rolled_back("reply", draft, token)


async def send_reply_dry(draft_id: int) -> ActionResult:
    draft, why = await _guard_reply(int(draft_id))
    if draft is None:
        return ActionResult(False, str(why))
    return ActionResult(
        True, f"would send by {draft['channel']} to {draft['to_address']}:\n{draft['body']}",
        evidence={"to": draft["to_address"], "text": draft["body"]})


# ---------------------------------------------------------- send_agreement

def payment_url(lead_id: int, price: int) -> str | None:
    """The link sent to this lead: Miro's Payment Link for the price, with
    `client_reference_id=lead_<id>` so the paid Checkout Session names the
    lead back to ATLAS (`payments._match`)."""
    link = payment_link(price)
    if link is None:
        return None
    joiner = "&" if "?" in link else "?"
    return f"{link}{joiner}client_reference_id=lead_{int(lead_id)}"


def payment_link(price: int) -> str | None:
    """Miro's own Payment Link for exactly this price, or None."""
    for item in os.getenv("AGENCY_PAYMENT_LINKS", "").split(","):
        amount, _, url = item.partition("=")
        try:
            if int(amount.strip().lstrip("$")) == price and url.strip().startswith("https://"):
                return url.strip()
        except ValueError:
            continue
    return None


def agreement_text(business: str, price: int, link: str) -> str:
    scope = "\n".join(f"- {d}" for d in pricing.deliverables())
    return (f"Here is the agreement for {business}'s website, as discussed.\n\n"
            f"What's included:\n{scope}\n\n"
            f"Price: ${price}, paid through this secure link: {link}\n\n"
            "Miro starts work once payment is in and will confirm the timeline "
            "with you in writing. Reply to this message with any question before "
            "you pay.")


async def _guard_agreement(lead_id: int, price: int, channel: str
                           ) -> tuple[dict[str, Any] | None, str | None]:
    if channel not in CHANNELS:
        return None, f"unknown channel {channel!r}; one of {CHANNELS}"
    lead = await _store.get_lead(lead_id)
    if lead is None:
        return None, f"no such lead: {lead_id}"
    from .verify import contact_refusal
    unverified = await contact_refusal(lead_id)
    if unverified is not None:
        return None, unverified
    terms = await pricing.terms(lead_id)
    if price not in terms.allowed():
        if terms.returning is None:
            return None, (f"${price} cannot be agreed: whether the lead is new or "
                          "returning could not be read")
        who = "returning" if terms.returning else "new"
        return None, (f"${price} is not the price for a {who} client "
                      f"(${terms.price})")
    link = payment_url(lead_id, price)
    if link is None:
        return None, f"no payment link for ${price} in AGENCY_PAYMENT_LINKS"
    text = agreement_text(str(lead.get("name") or "your business"), price, link)
    if channel == "email":
        to, why = await _recipient(lead_id, "email")
        if to is None:
            return None, why
        body = _email_finish(text)
        subject = f"Agreement: {lead.get('name')}'s website"
        problem = _email_problem(to, subject, body)
        if problem:
            return None, problem
        return {"lead": lead, "to": to, "subject": subject, "body": body}, None
    contact, why = await consent.contact_guard(lead_id, "sms")
    if contact is None:
        return None, why
    body = f"{contact.opening}\n{text}\n{sms.STOP_LINE}"
    return {"lead": lead, "to": contact.phone, "subject": None, "body": body,
            "opening": contact.opening}, None


async def send_agreement(lead_id: int, price: int, channel: str) -> ActionResult:
    """Tier AUTO (2026-10-06), only past `_guard_agreement`: the agreement
    and Miro's payment link for exactly this price, to a verified lead."""
    lead_id, price = int(lead_id), int(price)
    ready, why = await _guard_agreement(lead_id, price, channel)
    if ready is None:
        return ActionResult(False, f"refused: {why}")
    if channel == "email":
        ok, reason = await outreach._gmail_send(
            ready["to"], ready["subject"], ready["body"])
        if not ok:
            return ActionResult(False, f"send failed: {reason}")
        result = ActionResult(True, f"emailed the agreement (${price}) to {ready['to']}")
    else:
        result = await sms.deliver(lead_id, ready["to"], ready["opening"], ready["body"])
        if not result.ok:
            return result
    row_id = await _store.insert_reply_draft(
        lead_id, None, "agreement", channel, ready["to"], ready["subject"],
        ready["body"], [price], price=price, status="sent")
    return ActionResult(True, result.detail,
                        evidence={**result.evidence, "price": price, "to": ready["to"],
                                  "text": ready["body"], "agreement_id": row_id},
                        rollback_token=str(row_id) if row_id is not None else "")


async def send_agreement_rollback(token: str) -> ActionResult:
    """A delivered agreement is not unsent, and the payment link is Miro's
    own at Stripe (ATLAS touches nothing there). It suppresses the address
    and records the fact on the lead."""
    if not token:
        return ActionResult(True, "nothing was sent for this run; nothing to roll back")
    draft = await _store.get_reply_draft(int(token))
    if draft is None:
        return ActionResult(False, f"no such agreement: {token}")
    return await _rolled_back("agreement", draft, token)


async def send_agreement_dry(lead_id: int, price: int, channel: str) -> ActionResult:
    try:
        lead_id, price = int(lead_id), int(price)
    except (TypeError, ValueError):
        return ActionResult(False, "lead_id and price must be whole numbers")
    ready, why = await _guard_agreement(lead_id, price, channel)
    if ready is None:
        return ActionResult(False, str(why))
    return ActionResult(True, f"would send to {ready['to']}:\n{ready['body']}",
                        evidence={"to": ready["to"], "text": ready["body"]})


# ------------------------------------------------------------- record_deal

def _deal_params(lead_id: Any, status: str, amount: Any) -> tuple[int, int | None] | str:
    if status not in DEAL_STATUSES:
        return f"status must be one of {DEAL_STATUSES}"
    try:
        lid = int(lead_id)
        value = None if amount in (None, "") else int(str(amount).strip().lstrip("$"))
    except (TypeError, ValueError):
        return "lead_id and amount must be whole numbers"
    return lid, value


async def record_deal(lead_id: int, status: str, amount: int | None = None) -> ActionResult:
    parsed = _deal_params(lead_id, status, amount)
    if isinstance(parsed, str):
        return ActionResult(False, parsed)
    lid, value = parsed
    if await _store.get_lead(lid) is None:
        return ActionResult(False, f"no such lead: {lid}")
    deal_id = await _store.insert_deal(lid, status, value, "recorded by Miro")
    if deal_id is None:
        return ActionResult(False, "failed to record the deal")
    return ActionResult(True, f"recorded lead {lid} as {status}"
                              + (f" (${value})" if value is not None else ""),
                        evidence={"deal_id": deal_id}, rollback_token=str(deal_id))


async def record_deal_dry(lead_id: int, status: str, amount: int | None = None
                          ) -> ActionResult:
    parsed = _deal_params(lead_id, status, amount)
    if isinstance(parsed, str):
        return ActionResult(False, parsed)
    if await _store.get_lead(parsed[0]) is None:
        return ActionResult(False, f"no such lead: {parsed[0]}")
    return ActionResult(True, f"would record lead {parsed[0]} as {status}")


async def record_deal_rollback(token: str) -> ActionResult:
    if not token:
        return ActionResult(True, "nothing to roll back")
    await _store.undo_deal(int(token))
    return ActionResult(True, f"removed deal record {token}")
