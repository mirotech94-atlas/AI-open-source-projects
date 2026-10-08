"""Answering, booking, invoicing and calling a lead, through the one path that acts.

`modules/agency/leads.py` takes a lead's reply in and, when it reads as
interested, a question or about price, queues it (`agency_lead_messages.
reply_channel`); when the lead's own words pick one of the offered calendar
slots or accept an allowed price -- read in code, never by a model -- it
queues a booking or an agreement too. The agency module never imports the
executor (tests/test_agency.py holds it to that), so every send is driven
from here, the way `kernel.missions` drives agency work. Every action below
is Tier 1 under Miro's standing rule (2026-09-30, restated 2026-10-06:
"make the lead replies send themselves too -- approve invoices too and
schedule meetings with them via email as well and call them as well"):
anything not urgent runs without a card, with the rules in code; a refusal
is the Tier 2 card with "Held for you: <reason>" first, and approving it
runs every check again. Nothing is ever sent from a card raised here; the
card is the executor's own path.

Each pass claims its row once (an atomic UPDATE). A **hold** -- watch-only,
a pause, an hourly ceiling, anything refused before it dispatched other
than a validation refusal (`_held`) -- releases the claim and stops the
pass, so the row is tried again on a later tick. A **refusal** (a dry run
that said no) is an answer: the claim stays, the card carries the reason,
and the same row is not retried every five minutes. A **success** posts a
Slack receipt through `kernel.comms.notify`. A queued row more than a day
old is no longer picked up (Miro has the alert and can answer it himself).

The passes, in the order `tick()` runs them:

  * **Replies** (`replies`). `agency.draft_reply` writes the draft (the
    model's words, checked in code: prices, promises, honesty, and every
    time it names against the real calendar slots it was given); then
    `agency.send_reply` sends it past `replies._guard_reply` (every rule
    again at send time, CAN-SPAM, suppression, texting consent, and the
    `AGENCY_REPLY_AUTOSEND` hold). Receipt: "Replied to <lead> by email".
  * **Bookings** (`bookings`). A lead who answered an offered time gets
    `agency.hold_meeting` (a hold on Miro's own calendar, re-checked free by
    its dry run) and then `agency.send_invite` (Google emails the
    invitation, past `meetings._invite_guard`). A hold after the calendar
    hold releases the claim; the next tick finds the hold already there
    (`_store.meeting_at`) and only sends the invite.
  * **Agreements** (`agreements`). A lead who accepted an allowed price gets
    `agency.send_agreement` by email: the agreement text written by code
    and Miro's own Stripe payment link for exactly that price (money IN).
  * **After a deploy** (`after_deploys`). A site `agency.deploy_site`
    published in the last day is claimed once (`agency_sites.
    followed_up_at`), and its lead gets, through the executor:
    `agency.find_email` (reads their own site); then, with a published
    address, `agency.draft_outreach(email)` and `agency.send_outreach` --
    the email carries the live preview, the "Have us call you" link and
    Mapfront's line; with none, `agency.call_brief` and the brief posted to
    Slack. A lead that already has outreach is left alone (a redeploy is
    not a new pitch).
  * **Asked to be called** (`calls_due`). A live call consent from the
    "Have Mapfront call me" form (`web_form`), from ringing the business
    line (`call`), or recorded by Miro after the lead said yes on HIS call
    (`principal`, `agency.record_consent`) gets `agency.call_lead` placed
    through the executor -- only while `consent.contact_guard` passes: the
    consent is the gate, and nothing cold-calls. Outside calling hours the
    consent is left unclaimed until the first tick inside them; blocked for
    any other reason, it is marked with why and nobody is called. The call
    opens with who is calling, the preview, and the offer to put them
    through to Miro. Receipt: "Calling <lead> at <phone> (they asked on
    <how>)".
  * **The one timed follow-up** (`followups`): a first outreach email still
    unanswered after `AGENCY_FOLLOWUP_DAYS` gets its single follow-up.
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

log = logging.getLogger("atlas.lead_followup")

BATCH = 5
CHANNEL_WORD = {"email": "email", "sms": "text"}


def interval() -> timedelta:
    try:
        return timedelta(minutes=max(1, int(os.getenv("AGENCY_REPLY_TICK_MIN", "5"))))
    except ValueError:
        return timedelta(minutes=5)


async def tick() -> int:
    """Run every pass; returns how many cards were raised."""
    raised = 0
    try:
        from ..modules.agency.meetings import remind
        await remind()
    except Exception:
        log.warning("meeting reminders could not be read", exc_info=True)
    for name, pass_ in (("lead replies", replies), ("bookings", bookings),
                        ("agreements", agreements),
                        ("the post-deploy follow-up", after_deploys),
                        ("calls due", calls_due), ("the timed follow-ups", followups)):
        try:
            raised += await pass_()
        except Exception:
            log.warning("%s could not run", name, exc_info=True)
    return raised


def _held(result: Any) -> bool:
    """Watch-only, a pause, an hourly ceiling: not this lead's fault; try
    again on a later tick. A validation refusal (a dry run that said no) is
    an answer, not a hold."""
    from .contracts import FailureClass
    from .executor import HOLD
    return bool((result.evidence or {}).get(HOLD)) or (
        result.dispatched is False
        and result.failure_class is not FailureClass.VALIDATION)


def _hold_reason(detail: str) -> str:
    """The reason in the executor's refusal, without its prefixes."""
    from ..modules.agency.outreach import HELD
    for prefix in ("dry run refused: ", "refused: ", f"{HELD}: "):
        detail = detail.removeprefix(prefix)
    return detail


async def _card(action: str, params: dict[str, Any], detail: str, reason: str,
                evidence: dict[str, Any], dedup_key: str, *,
                severity: Any = None) -> bool:
    """The Tier 2 card a refusal becomes: "Held for you: <reason>" first,
    then what the send was. True when it was raised."""
    from .approvals import Unrunnable, propose
    from .contracts import Severity
    hold = _hold_reason(detail)
    try:
        await propose(
            action, params,
            f"Held for you: {hold}. Approving runs every check again and does it "
            f"if it passes now.\n{reason}",
            severity=severity or Severity.NORMAL,
            evidence={**evidence, "held": hold}, dedup_key=dedup_key, automatic=True)
        return True
    except (Unrunnable, ValueError) as exc:
        log.info("%s card not raised: %s", action, exc)
        return False


async def _receipt(title: str, body: str) -> None:
    from .comms import Outbound, notify
    await notify(Outbound(title=title, body=body, topic="agency", kind="receipt"))


async def _lead_name(lead_id: int) -> str:
    from ..modules.agency import _store
    lead = await _store.get_lead(lead_id) or {}
    return str(lead.get("name") or f"lead {lead_id}")


# ---------------------------------------------------------------- replies

async def replies() -> int:
    """Draft and send what is queued. Returns cards raised."""
    from ..modules.agency import _store
    from .executor import ExecutionRequest, execute
    raised = 0
    for row in await _store.messages_awaiting_reply(BATCH):
        message_id, lead_id = int(row["id"]), int(row["lead_id"])
        channel = str(row["reply_channel"])
        if not await _store.claim_reply(message_id):
            continue
        result = await execute(ExecutionRequest(
            "agency.draft_reply", {"lead_id": lead_id, "channel": channel},
            reason=f"lead {lead_id} wrote back (message {message_id})"))
        if _held(result):
            # Watch-only, a pause, an hourly ceiling (missions spend the same
            # agency budget): not this message's fault. Released, tried later.
            await _store.release_reply(message_id)
            log.info("lead replies held: %s", result.detail)
            break
        evidence = result.evidence or {}
        draft_id = evidence.get("draft_id") if result.ok else None
        if draft_id is None:
            log.info("no reply drafted for lead %s: %s", lead_id, result.detail)
            continue
        name = await _lead_name(lead_id)
        word = CHANNEL_WORD.get(channel, channel)
        reason = f"reply to {name} ({evidence.get('to')}) by {word}, message {message_id}"
        sent = await execute(ExecutionRequest(
            "agency.send_reply", {"draft_id": int(draft_id)}, reason=reason))
        if _held(sent):
            await _store.release_reply(message_id)
            log.info("lead replies held at the send: %s", sent.detail)
            break
        if sent.ok:
            await _receipt(
                f"Replied to {name} by {word}",
                f"{sent.detail}. Sent without a card (your rule); their answer comes "
                f"to Slack.\n\n{evidence.get('text')}")
            continue
        raised += await _card(
            "agency.send_reply", {"draft_id": int(draft_id)}, sent.detail, reason,
            {"to": evidence.get("to"), "text": evidence.get("text")},
            f"agency.send_reply:{draft_id}")
    return raised


# --------------------------------------------------------------- bookings

async def bookings() -> int:
    """Hold and invite each slot a lead picked by email, once. Returns
    cards raised."""
    from ..modules.agency import _store, meetings
    from .executor import ExecutionRequest, execute
    raised = 0
    for row in await _store.bookings_awaiting(BATCH):
        message_id, lead_id = int(row["id"]), int(row["lead_id"])
        slot = row["booking_slot"]
        if not isinstance(slot, datetime) or slot.tzinfo is None:
            log.info("booking on message %s has no usable slot: %r", message_id, slot)
            continue
        if not await _store.claim_booking(message_id):
            continue
        name = await _lead_name(lead_id)
        when = meetings.spoken(slot)
        reason = f"{name} picked {when} by email (message {message_id})"
        meeting = await _store.meeting_at(lead_id, slot)
        if meeting is None:
            held = await execute(ExecutionRequest(
                "agency.hold_meeting", {"lead_id": lead_id, "start": slot.isoformat()},
                reason=reason))
            if _held(held):
                await _store.release_booking(message_id)
                log.info("bookings held: %s", held.detail)
                break
            meeting_id = (held.evidence or {}).get("meeting_id") if held.ok else None
            if meeting_id is None:
                raised += await _card(
                    "agency.hold_meeting",
                    {"lead_id": lead_id, "start": slot.isoformat()}, held.detail, reason,
                    {"start": slot.isoformat()}, f"agency.hold_meeting:{message_id}")
                continue
        elif meeting.get("status") == "invited":
            continue
        else:
            meeting_id = meeting["id"]
        invited = await execute(ExecutionRequest(
            "agency.send_invite", {"meeting_id": int(meeting_id)},
            reason=f"invite {name} to the call they picked: {when}"))
        if _held(invited):
            # The hold is on the calendar already; the next tick finds it and
            # sends only the invite.
            await _store.release_booking(message_id)
            log.info("bookings held at the invite: %s", invited.detail)
            break
        if invited.ok:
            to = (invited.evidence or {}).get("to")
            await _receipt(
                f"Booked {name}: {when}",
                f"Held on your calendar and the invite sent to {to}. They picked it "
                "by email. A reminder comes an hour before.")
            continue
        raised += await _card(
            "agency.send_invite", {"meeting_id": int(meeting_id)}, invited.detail,
            f"invite {name} to the call they picked: {when}",
            {"start": slot.isoformat()}, f"agency.send_invite:{meeting_id}")
    return raised


# ------------------------------------------------------------- agreements

async def agreements() -> int:
    """Send the agreement to each lead whose words accepted an allowed
    price, once. Returns cards raised."""
    from ..modules.agency import _store
    from .executor import ExecutionRequest, execute
    raised = 0
    for row in await _store.agreements_awaiting(BATCH):
        message_id, lead_id = int(row["id"]), int(row["lead_id"])
        price = int(row["agreement_price"])
        if not await _store.claim_agreement(message_id):
            continue
        name = await _lead_name(lead_id)
        params = {"lead_id": lead_id, "price": price, "channel": "email"}
        reason = f"{name} accepted ${price} by email (message {message_id})"
        sent = await execute(ExecutionRequest("agency.send_agreement", params,
                                              reason=reason))
        if _held(sent):
            await _store.release_agreement(message_id)
            log.info("agreements held: %s", sent.detail)
            break
        if sent.ok:
            await _receipt(
                f"Sent {name} the agreement (${price})",
                f"{sent.detail}: your payment link for ${price}. A payment posts to "
                "Slack from Stripe; nothing else goes to them unless they write back.")
            continue
        raised += await _card(
            "agency.send_agreement", params, sent.detail, reason,
            {"price": price, "to": (sent.evidence or {}).get("to")},
            f"agency.send_agreement:{message_id}")
    return raised


# ------------------------------------------------------- the one follow-up

def followup_after() -> timedelta:
    """How long a first email waits for a reply before its one follow-up
    (AGENCY_FOLLOWUP_DAYS, default 4; 0 switches the pass off)."""
    try:
        days = int(os.getenv("AGENCY_FOLLOWUP_DAYS", "4"))
    except ValueError:
        days = 4
    return timedelta(days=days)


async def followups() -> int:
    """Miro, 2026-10-05 (from a reel: an email agent that "schedules smart
    follow-ups, tracks replies, and automatically stops the sequence once
    someone responds"). The tracking and the stop already exist: a reply
    swept from Gmail marks the message `replied` (modules/agency/leads.
    sweep_inbox) and `outreach._followup_ok` refuses a second follow-up.
    This is the scheduling: a first email that is still `sent` after
    `followup_after()` gets its one follow-up, through the executor like
    everything else -- `agency.draft_outreach` chains it onto the first
    message (`followup_of`), `agency.send_outreach` runs every guard
    (suppression, CAN-SPAM, one follow-up per lead) and the autosend hold.
    A hold releases the claim for a later tick; a refusal is an answer and
    the claim stays, so the same message is not retried every five minutes.
    Returns cards raised."""
    from ..modules.agency import _store
    from .approvals import Unrunnable, propose
    from .executor import ExecutionRequest, execute
    after = followup_after()
    if after <= timedelta(0):
        return 0
    raised = 0
    due = await _store.outreach_due_followup(after, BATCH)
    for index, first in enumerate(due):
        outreach_id, lead_id = int(first["id"]), int(first["lead_id"])
        if not await _store.claim_followup(outreach_id):
            continue
        lead = await _store.get_lead(lead_id) or {}
        name = lead.get("name") or f"lead {lead_id}"
        why = (f"{name} has not answered the email of "
               f"{str(first.get('sent_at'))[:10]}; the one follow-up")
        try:
            draft = await execute(ExecutionRequest(
                "agency.draft_outreach", {"lead_id": lead_id, "kind": "email"},
                reason=why))
            if _held(draft):
                for rest in due[index:]:
                    await _store.release_followup(int(rest["id"]))
                break
            evidence = draft.evidence or {}
            if not draft.ok or evidence.get("followup_of") is None:
                log.info("no follow-up drafted for lead %s: %s", lead_id, draft.detail)
                continue
            new_id = int(evidence["outreach_id"])
            sent = await execute(ExecutionRequest(
                "agency.send_outreach", {"outreach_id": new_id},
                reason=f"follow-up to {name} ({evidence.get('to')}): {why}"))
            if _held(sent):
                for rest in due[index:]:
                    await _store.release_followup(int(rest["id"]))
                break
            if sent.ok:
                from .comms import Outbound, notify
                await notify(Outbound(
                    title=f"Followed up with {name}",
                    body=(f"{sent.detail}; their first email went unanswered for "
                          f"{after.days} days. This was the one follow-up: nothing "
                          "more goes to them unless they write back."),
                    topic="agency", kind="receipt"))
                continue
            hold = _hold_reason(sent.detail)
            try:
                await propose(
                    "agency.send_outreach", {"outreach_id": new_id},
                    f"Held for you: {hold}. Approving runs every check again and "
                    f"sends it if it passes now.\nFollow-up to {name}: {why}",
                    evidence={"to": evidence.get("to"), "text": evidence.get("text"),
                              "held": hold, "followup_of": outreach_id},
                    dedup_key=f"agency.send_outreach:{new_id}", automatic=True)
                raised += 1
            except (Unrunnable, ValueError) as exc:
                log.info("follow-up card for lead %s not raised: %s", lead_id, exc)
        except Exception:
            log.warning("follow-up for outreach %s failed", outreach_id, exc_info=True)
            await _store.release_followup(outreach_id)
    return raised


# ------------------------------------------------------------ after a deploy

FOLLOW_WITHIN = timedelta(days=1)


async def after_deploys() -> int:
    """Follow up each freshly deployed site once. Returns cards raised."""
    from ..modules.agency import _store
    raised = 0
    sites = await _store.claim_deployed_sites(FOLLOW_WITHIN)
    for index, site in enumerate(sites):
        try:
            outcome = await follow_site(site)
        except Exception:
            log.warning("follow-up for site %s failed", site.get("slug"), exc_info=True)
            await _store.release_site_followup(str(site["slug"]))
            continue
        if outcome == "held":
            for rest in sites[index:]:
                await _store.release_site_followup(str(rest["slug"]))
            break
        raised += outcome == "card"
    return raised


async def follow_site(site: dict[str, Any]) -> str:
    """sent | card | brief | skipped | held. Every step through the executor.

    The outreach email is Tier 1 (Miro, 2026-10-01) and sends here, past
    `outreach.auto_hold`; a hold or a refusal becomes the Tier 2 card with
    "Held for you: <reason>" first, and approving it runs every check again
    (the holds pass under a card, the guards do not)."""
    from ..modules.agency import _store, contacts
    from .approvals import Unrunnable, propose
    from .executor import ExecutionRequest, execute
    lead_id, slug = int(site["lead_id"]), str(site["slug"])
    if await _store.has_outreach(lead_id):
        log.info("site %s: lead %s already has outreach; no new pitch", slug, lead_id)
        return "skipped"
    lead = await _store.get_lead(lead_id) or {}
    name = lead.get("name") or f"lead {lead_id}"
    why = f"{name}'s site ({slug}) was just published"
    found = None
    if lead.get("website"):
        found = await execute(ExecutionRequest(
            "agency.find_email", {"lead_id": lead_id}, reason=why))
        if _held(found):
            return "held"
    email = (found.evidence or {}).get("email") if found and found.ok else None
    if not email:
        email = ((await contacts.published_email(lead_id)) or {}).get("address")
    if email:
        draft = await execute(ExecutionRequest(
            "agency.draft_outreach", {"lead_id": lead_id, "kind": "email"}, reason=why))
        if _held(draft):
            return "held"
        evidence = draft.evidence or {}
        outreach_id = evidence.get("outreach_id") if draft.ok else None
        if outreach_id is not None and evidence.get("to"):
            reason = (f"email {name} ({evidence.get('to')}) about the site just "
                      f"published at {evidence.get('preview_url') or slug}")
            sent = await execute(ExecutionRequest(
                "agency.send_outreach", {"outreach_id": int(outreach_id)},
                reason=reason))
            if _held(sent):
                return "held"
            if sent.ok:
                from .comms import Outbound, notify
                await notify(Outbound(
                    title=f"Emailed {name}",
                    body=(f"{sent.detail}: {evidence.get('preview_url') or slug}, "
                          f"to {evidence.get('to')} (found on their own site). "
                          "Sent without a card (your rule); a reply comes to Slack."),
                    topic="agency", kind="receipt"))
                return "sent"
            hold = _hold_reason(sent.detail)
            try:
                await propose(
                    "agency.send_outreach", {"outreach_id": int(outreach_id)},
                    f"Held for you: {hold}. Approving runs every check again and "
                    f"sends it if it passes now.\n{reason}",
                    evidence={"to": evidence.get("to"), "text": evidence.get("text"),
                              "held": hold,
                              "found_on": ((found.evidence or {}).get("found")
                                           if found else None)},
                    dedup_key=f"agency.send_outreach:{outreach_id}", automatic=True)
                return "card"
            except (Unrunnable, ValueError) as exc:
                log.info("outreach card for lead %s not raised: %s", lead_id, exc)
        else:
            log.info("no outreach drafted for lead %s: %s", lead_id, draft.detail)
    brief = await execute(ExecutionRequest(
        "agency.call_brief", {"lead_id": lead_id},
        reason=f"{why}; no published email to write to"))
    if _held(brief):
        return "held"
    if brief.ok:
        from .comms import Outbound, notify
        await notify(Outbound(
            title=f"Call brief: {name} (site live, no published email)",
            body=str((brief.evidence or {}).get("text") or brief.detail),
            topic="agency", kind="alert"))
    return "brief"


# ---------------------------------------------------------- asked to be called

# A consent from the form, from ringing the line, or recorded by Miro after
# the lead said yes on HIS call (`agency.record_consent`, 2026-10-01).
CALL_SOURCES = ("web_form", "call", "principal")
CALL_WITHIN = timedelta(days=3)
HOW_ASKED = {"web_form": "the \"Have Mapfront call me\" form on their preview",
             "call": "calling the business line",
             "principal": "your own call (you recorded their yes)"}


def _inside_hours() -> datetime:
    """Midday today, lead time: "would the guard pass during calling hours?"."""
    from ..modules.agency import consent
    return datetime.now(UTC).astimezone(consent.lead_tz()).replace(
        hour=12, minute=0, second=0, microsecond=0)


async def call_due(consent_id: int, *, now: datetime | None = None) -> str:
    """Place the agency.call_lead call a consent asks for, once, through
    the executor -- or say why not. The consent guard is the gate: nothing
    here can dial a lead who did not ask (2026-10-06; before this the call
    waited on a Tier 2 card)."""
    from ..modules.agency import _store, consent
    from .contracts import Severity
    from .executor import ExecutionRequest, execute
    row = await _store.get_consent(int(consent_id))
    if row is None or row.get("revoked_at") is not None or row.get("scope") != "call":
        return "no live consent to call on"
    if row.get("call_card_at") is not None:
        return f"already handled ({row.get('call_card_note') or 'handled'})"
    lead_id = int(row["lead_id"])
    contact, why = await consent.contact_guard(lead_id, "call", now=now)
    if contact is None:
        later, _ = await consent.contact_guard(lead_id, "call", now=_inside_hours())
        if later is not None:
            return (f"no call yet: {why}. It is placed on the first tick inside "
                    "calling hours")
        await _store.mark_call_card(int(consent_id), f"no call: {why}")
        return f"no call: {why}. Call them yourself, or answer by email"
    if not await _store.mark_call_card(int(consent_id), "calling"):
        return "already handled"
    lead = contact.lead
    evidence = _store.parse_json(row.get("evidence"), {})
    source = str(row.get("source_channel") or "")
    how = HOW_ASKED.get(source, "in writing")
    listed = evidence.get("matches_listed_phone")
    name = str(lead.get("name") or f"lead {lead_id}")
    reason = (f"{name} asked to be called at {contact.phone} ({how})"
              + ("" if listed is None else
                 "; it is their listed number" if listed else
                 "; not their listed number"))
    facts = {"consent_id": int(consent_id), "source": source, "phone": contact.phone,
             "opening": contact.opening, "page_url": evidence.get("page_url"),
             "consent_text": row.get("quoted"), "matches_listed_phone": listed}
    try:
        result = await execute(ExecutionRequest(
            "agency.call_lead", {"lead_id": lead_id}, reason=reason))
    except Exception:
        await _store.release_call_card(int(consent_id))     # a later tick retries
        raise
    if _held(result):
        await _store.release_call_card(int(consent_id))
        return f"held, tried again on a later tick: {result.detail}"
    if result.ok:
        contact_id = (result.evidence or {}).get("contact_id")
        await _store.note_call_card(int(consent_id), f"called (contact #{contact_id})")
        await _receipt(
            f"Calling {name} at {contact.phone} (they asked on {how})",
            f"{result.detail}. Placed without a card (your rule): the call opens with "
            "who is calling and the preview, and offers to put them through to you. "
            "The summary follows when it ends.")
        return f"calling {name} at {contact.phone} (contact #{contact_id})"
    try:
        raised = await _card(
            "agency.call_lead", {"lead_id": lead_id}, result.detail, reason, facts,
            f"agency.call_lead:consent:{consent_id}", severity=Severity.HIGH)
    except Exception:
        await _store.release_call_card(int(consent_id))
        raise
    hold = _hold_reason(result.detail)
    if not raised:
        await _store.note_call_card(int(consent_id), f"no card: {hold}")
        return f"no call ({hold}) and no card could be raised"
    await _store.note_call_card(int(consent_id), f"held for you: {hold}")
    return f"held for you ({hold}): the call card is waiting for Miro (Tier 2)"


async def calls_due() -> int:
    """Every consent still owed its call; returns how many cards were raised."""
    from ..modules.agency import _store
    raised = 0
    for row in await _store.consents_awaiting_call_card(CALL_SOURCES, CALL_WITHIN):
        said = await call_due(int(row["id"]))
        raised += said.startswith("held for you")
    return raised
