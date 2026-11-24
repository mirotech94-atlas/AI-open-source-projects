"""Answering, booking, invoicing and calling a lead, through the one path that acts.

`modules/agency/leads.py` takes a lead's reply in and, when it reads as
interested, a question or about price, queues it (`agency_lead_messages.
reply_channel`); when the lead's own words pick one of the offered calendar
slots or accept an allowed price -- read in code, never by a model -- it
queues a booking or an agreement too. The agency module never imports the
executor (tests/test_agency.py holds it to that), so every send is driven
from here, the way `kernel.missions` drives agency work. Drafting, holding
a calendar slot, replying and calling are Tier 1 under the owner's standing rule
(2026-09-30, restated 2026-10-06); a refusal is the Tier 2 card with "Held
for you: <reason>" first. The three sends to a client -- the outreach email,
the calendar invite and the agreement -- are Tier 2 cards since 2026-10-27
(six agents): drafted through the executor, dry-run here
(`executor.preview`, which can never reach `run`), then ONE card each
(`_send_card`, a dedup key, `automatic=True`). Nothing here executes them;
approving the card runs every check again through the executor.

Each pass claims its row once (an atomic UPDATE). A **hold** -- watch-only,
a pause, an hourly ceiling, anything refused before it dispatched other
than a validation refusal (`_held`) -- releases the claim and stops the
pass, so the row is tried again on a later tick. A **refusal** (a dry run
that said no) is an answer: the claim stays, the card carries the reason,
and the same row is not retried every five minutes. A **success** posts a
Slack receipt through `kernel.comms.notify`. A queued row more than a day
old is no longer picked up (the owner has the alert and can answer it himself).

The passes, in the order `tick()` runs them:

  * **Replies** (`replies`). `agency.draft_reply` writes the draft (the
    model's words, checked in code: prices, promises, honesty, and every
    time it names against the real calendar slots it was given); then
    `agency.send_reply` sends it past `replies._guard_reply` (every rule
    again at send time, CAN-SPAM, suppression, texting consent, and the
    `AGENCY_REPLY_AUTOSEND` hold). Receipt: "Replied to <lead> by email".
  * **Bookings** (`bookings`). A lead who answered an offered time gets
    `agency.hold_meeting` (a hold on the owner's own calendar, re-checked free by
    its dry run) and then the `agency.send_invite` card (Google emails the
    invitation once the owner approves, past `meetings._invite_guard`).
  * **Agreements** (`agreements`). A lead who accepted an allowed price gets
    the `agency.send_agreement` card: the agreement text written by code
    and the owner's own Stripe payment link for exactly that price (money IN).
  * **After a deploy** (`after_deploys`). A site `agency.deploy_site`
    published in the last day is claimed once (`agency_sites.
    followed_up_at`), and its lead gets, through the executor:
    `agency.find_email` (reads their own site); then, with a published
    address, `agency.draft_outreach(email)` and the `agency.send_outreach`
    card -- the email carries the live preview, the "Have us call you" link and
    Mapfront's line; with none, `agency.call_brief` and the brief posted to
    Slack. A lead that already has outreach is left alone (a redeploy is
    not a new pitch).
  * **Asked to be called** (`calls_due`). A live call consent from the
    "Have Mapfront call me" form (`web_form`), from ringing the business
    line (`call`), or recorded by the owner after the lead said yes on HIS call
    (`principal`, `agency.record_consent`) gets `agency.call_lead` placed
    through the executor -- only while `consent.contact_guard` passes: the
    consent is the gate, and nothing cold-calls. Outside calling hours the
    consent is left unclaimed until the first tick inside them; blocked for
    any other reason, it is marked with why and nobody is called. The call
    opens with who is calling, the preview, and the offer to put them
    through to the owner. Receipt: "Calling <lead> at <phone> (they asked on
    <how>)".
  * **The one timed follow-up** (`followups`): a first outreach email still
    unanswered after `AGENCY_FOLLOWUP_DAYS` gets its single follow-up, as a
    card.
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
    again on a later tick. A validation refusal (a dry run that said no) or
    a QA refusal (kernel/qa_gate.py) is an answer, not a hold."""
    from .contracts import FailureClass
    from .executor import HOLD
    return bool((result.evidence or {}).get(HOLD)) or (
        result.dispatched is False
        and result.failure_class not in (FailureClass.VALIDATION, FailureClass.QUALITY))


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


async def _send_card(action: str, params: dict[str, Any], reason: str,
                     evidence: dict[str, Any], dedup_key: str) -> bool:
    """One Tier 2 card for a send to a client; the send never runs here.

    The dry run is read first (`executor.preview` cannot reach `run`): what
    it would send goes on the card, and a guard that refused puts "Held for
    you: <reason>" first. The autosend holds (`HELD`) are what the card
    itself answers, so they are not a refusal. True when a card exists."""
    from ..modules.agency.outreach import HELD
    from .approvals import Unrunnable, propose
    from .executor import preview
    try:
        dry = await preview(action, params)
        detail = str(dry.detail)
        for prefix in ("dry run refused: ", "refused: "):
            detail = detail.removeprefix(prefix)
        held = (None if dry.ok or detail.lower().startswith(HELD)
                else _hold_reason(detail))
        would = (dry.evidence or {}) if dry.ok else {}
    except Exception as exc:                            # noqa: BLE001
        held, would = f"the dry run could not run ({type(exc).__name__})", {}
    text = (f"Held for you: {held}. Approving runs every check again and sends "
            f"it only if it passes then.\n{reason}" if held else
            f"{reason}\nApproving sends it, after every check runs again.")
    try:
        await propose(action, params, text,
                      evidence={**evidence, "held": held,
                                "dry_run": {k: would[k] for k in
                                            ("to", "subject", "text", "body", "price")
                                            if k in would}},
                      dedup_key=dedup_key, automatic=True)
        return True
    except (Unrunnable, ValueError) as exc:
        log.info("%s card not raised: %s", action, exc)
        return False


async def _receipt(title: str, body: str) -> None:
    from .comms import Outbound, notify
    await notify(Outbound(title=title, body=body, topic="agency", kind="receipt"))
    from ..modules.agency import owner_wa
    await owner_wa.notify_owner("portal_digest", title)   # low value: folded into a digest


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
        raised += await _send_card(
            "agency.send_invite", {"meeting_id": int(meeting_id)},
            f"invite {name} to the call they picked by email: {when} (held on "
            "your calendar)",
            {"start": slot.isoformat()}, f"agency.send_invite:{meeting_id}")
    return raised


# ------------------------------------------------------------- agreements

async def _proposal_for(lead_id: int, name: str) -> dict | None:
    """The written proposal at the lead's own price, drafted through the
    executor (sales.draft_proposal, Tier 1) for the agreement card. A hold or
    a failure leaves the card without one; it never blocks the card."""
    from .executor import ExecutionRequest, execute
    try:
        got = await execute(ExecutionRequest(
            "sales.draft_proposal", {"lead_id": lead_id},
            reason=f"the proposal for {name}'s agreement card"))
    except Exception:
        log.warning("proposal for lead %s failed", lead_id, exc_info=True)
        return None
    if _held(got) or not got.ok:
        return None
    ev = got.evidence or {}
    return {k: ev.get(k) for k in ("proposal_id", "price", "html_path", "pdf_path")}


async def agreements() -> int:
    """One agreement card for each lead whose words accepted an allowed
    price. Returns cards raised."""
    from ..modules.agency import _store
    raised = 0
    for row in await _store.agreements_awaiting(BATCH):
        message_id, lead_id = int(row["id"]), int(row["lead_id"])
        price = int(row["agreement_price"])
        if not await _store.claim_agreement(message_id):
            continue
        name = await _lead_name(lead_id)
        params = {"lead_id": lead_id, "price": price, "channel": "email"}
        reason = f"{name} accepted ${price} by email (message {message_id})"
        evidence: dict = {"price": price}
        attached = await _proposal_for(lead_id, name)
        if attached:
            evidence["proposal"] = attached
            reason += f"; proposal #{attached.get('proposal_id')} attached"
        raised += await _send_card(
            "agency.send_agreement", params, reason, evidence,
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
    """the owner, 2026-10-05 (from a reel: an email agent that "schedules smart
    follow-ups, tracks replies, and automatically stops the sequence once
    someone responds"). The tracking and the stop already exist: a reply
    swept from Gmail marks the message `replied` (modules/agency/leads.
    sweep_inbox) and `outreach._followup_ok` refuses a second follow-up.
    This is the scheduling: a first email that is still `sent` after
    `followup_after()` gets its one follow-up, through the executor like
    everything else -- `agency.draft_outreach` chains it onto the first
    message (`followup_of`), and the `agency.send_outreach` card carries it
    (approving runs every guard: suppression, CAN-SPAM, one follow-up per
    lead). A hold on the draft releases the claim for a later tick.
    Returns cards raised."""
    from ..modules.agency import _store
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
            raised += await raise_preview_card(
                outreach_id=new_id, name=str(name), why=why, draft=evidence,
                extra={"followup_of": outreach_id})
        except Exception:
            log.warning("follow-up for outreach %s failed", outreach_id, exc_info=True)
            await _store.release_followup(outreach_id)
    return raised


# ------------------------------------------- the Websites lane's preview card

async def raise_preview_card(*, outreach_id: int, name: str, why: str,
                             draft: dict[str, Any], found_on: Any = None,
                             extra: dict[str, Any] | None = None) -> bool:
    """The Websites lane's one card for one drafted email, in the words of
    `modules/agency/preview_card.py` (dedup on the outreach id)."""
    from ..modules.agency.preview_card import ACTION, card_text
    reason = card_text(name=name, to=str(draft.get("to") or ""),
                       subject=draft.get("subject"), body=str(draft.get("text") or ""),
                       preview_url=draft.get("preview_url"), why=why,
                       followup=bool((extra or {}).get("followup_of")))
    evidence = {"to": draft.get("to"), "subject": draft.get("subject"),
                "text": draft.get("text"), "preview_url": draft.get("preview_url"),
                "owner": name, "client": name, "found_on": found_on, **(extra or {})}
    return await _send_card(ACTION, {"outreach_id": int(outreach_id)}, reason, evidence,
                            f"{ACTION}:{outreach_id}")


async def pitch_preview(lead: dict[str, Any], why: str, *, find: bool) -> str:
    """Find the owner's published address, draft the email, have QA check
    it, raise the one card. Every step through the executor; a hold
    (watch-only, a pause) returns HELD and leaves the lead for a later tick.
    `find`: look on the lead's own site first (`agency.find_email`)."""
    from ..modules.agency import contacts
    from ..modules.agency import preview_card as pc
    from .executor import ExecutionRequest, execute
    lead_id = int(lead["id"])
    name = str(lead.get("name") or f"lead {lead_id}")
    found = None
    if find:
        found = await execute(ExecutionRequest(
            "agency.find_email", {"lead_id": lead_id}, reason=f"{name}'s published email"))
        if _held(found):
            return pc.HELD
    email = (found.evidence or {}).get("email") if found and found.ok else None
    if not email:
        email = ((await contacts.published_email(lead_id)) or {}).get("address")
    if not email:
        return pc.NO_EMAIL
    draft = await execute(ExecutionRequest(
        "agency.draft_outreach", {"lead_id": lead_id, "kind": "email"}, reason=why))
    if _held(draft):
        return pc.HELD
    ev = draft.evidence or {}
    if not draft.ok or ev.get("outreach_id") is None or not ev.get("to"):
        detail = str(draft.detail).lower()
        log.info("no outreach drafted for lead %s: %s", lead_id, draft.detail)
        demo = "deploy" in detail or "preview" in detail
        return pc.NEEDS_DEMO if demo else pc.NOT_RAISED
    oid = int(ev["outreach_id"])
    qa = await execute(ExecutionRequest(
        "qa.check_draft", {"kind": "email", "ref": f"agency_outreach:{oid}"},
        reason=f"QA the email to {name}"))
    if _held(qa):
        return pc.HELD
    if not qa.ok or not (qa.evidence or {}).get("passed"):
        return pc.QA_FAILED                 # QA raised the remediation itself
    ok = await raise_preview_card(
        outreach_id=oid, name=name, why=why, draft=ev,
        found_on=(found.evidence or {}).get("found") if found else None)
    return pc.CARD if ok else pc.NOT_RAISED


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
    """card | brief | skipped | held. Every step through the executor.

    The outreach email is the Websites lane's Tier 2 card (`pitch_preview`,
    2026-10-28): drafted and QA'd here, one card raised, never sent from
    this pass; approving it runs every check again. Returns "card" whenever
    the card exists, else falls back to the call brief, which says why."""
    from ..modules.agency import _store, preview_card
    from .executor import ExecutionRequest, execute
    lead_id, slug = int(site["lead_id"]), str(site["slug"])
    if await _store.has_outreach(lead_id):
        log.info("site %s: lead %s already has outreach; no new pitch", slug, lead_id)
        return "skipped"
    lead = await _store.get_lead(lead_id) or {}
    name = lead.get("name") or f"lead {lead_id}"
    why = f"{name}'s site ({slug}) was just published"
    # The Websites lane's one card (modules/agency/preview_card.py, 2026-10-28).
    got = await pitch_preview({**lead, "id": lead_id, "name": name}, why,
                              find=bool(lead.get("website")))
    if got in (preview_card.CARD, preview_card.HELD):
        return got
    without = {preview_card.NO_EMAIL: "no published email",
               preview_card.QA_FAILED: "the email failed QA",
               preview_card.NEEDS_DEMO: "no live preview for the email"}.get(
                   got, "the email card was not raised")
    if got != preview_card.NO_EMAIL:
        log.info("site %s: no card for lead %s (%s)", slug, lead_id, got)
    brief = await execute(ExecutionRequest(
        "agency.call_brief", {"lead_id": lead_id}, reason=f"{why}; {without}"))
    if _held(brief):
        return "held"
    if brief.ok:
        from .comms import Outbound, notify
        await notify(Outbound(
            title=f"Call brief: {name} (site live, {without})",
            body=str((brief.evidence or {}).get("text") or brief.detail),
            topic="agency", kind="alert"))
    return "brief"


# ---------------------------------------------------------- asked to be called

# A consent from the form, from ringing the line, or recorded by the owner after
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
    return f"held for you ({hold}): the call card is waiting for the owner (Tier 2)"


async def calls_due() -> int:
    """Every consent still owed its call; returns how many cards were raised."""
    from ..modules.agency import _store
    raised = 0
    for row in await _store.consents_awaiting_call_card(CALL_SOURCES, CALL_WITHIN):
        said = await call_due(int(row["id"]))
        raised += said.startswith("held for you")
    return raised
