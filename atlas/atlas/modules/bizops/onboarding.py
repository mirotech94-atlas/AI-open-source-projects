"""Onboarding after a won or paid deal (six agents phase 9).

`start_onboarding` (Tier 1) keys the onboarding on one payment reference:
the lead's earliest won/paid deal (`deal:<id>`), else a paid test invoice
(`invoice:<id>`). The row is inserted with ON CONFLICT DO NOTHING, so a
replayed trigger writes nothing. A new onboarding opens one project with
five tasks (intake, kickoff, build, QA, launch) and drafts the welcome
email with the intake questions and real kickoff slots read from the
calendar; the email is a Tier 2 card. The kickoff invitation itself is the
agency's own `agency.send_invite` card, raised when a slot is held."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import ActionResult, FailureClass
from . import store

SEND_ACTION = "bizops.send_onboarding_email"
INTAKE = (
    "Your business name as it should appear on the site, and your logo if you have one.",
    "The services or products to list, with any prices you want shown.",
    "Your opening hours, address and the phone number customers should call.",
    "Three websites you like the look of, and what you like about each.",
    "Photos you want used (or say if we should pick stock photos).",
)
# (title, kind, owner, due in days)
TASKS = (
    ("Intake: collect the client's answers", "task", "bizops", 3),
    ("Kickoff call with the client", "task", "you", 5),
    ("Build the site", "build", "you", 14),
    ("QA pass on the built site", "task", "qa", 16),
    ("Launch the site", "task", "you", 18),
)


def _refused(why: str) -> ActionResult:
    return ActionResult(False, why, failure_class=FailureClass.VALIDATION)


async def _payment_ref(lead_id: int) -> str | None:
    deal = await store.first_closed_deal(lead_id)
    if deal is not None:
        return f"deal:{int(deal['id'])}"
    paid = await store.paid_invoice_for(lead_id)
    return f"invoice:{paid}" if paid is not None else None


async def _slots() -> list[str]:
    from ..agency import meetings
    free, _why = await meetings.free_times()
    zone = meetings.tz()
    return [f"{t.astimezone(zone):%a %d %b, %H:%M %Z}" for t in (free or [])[:3]]


def compose(name: str, slots: list[str]) -> tuple[str, str]:
    from ..agency.consent import agency_name
    from ..agency.replies import _email_finish
    agency = agency_name() or "Mapfront"
    questions = "\n".join(f"{i}. {q}" for i, q in enumerate(INTAKE, 1))
    times = ("\n".join(f"- {s}" for s in slots) if slots else
             "- reply with two or three times that suit you")
    body = (f"Hi {name},\n\nThank you for choosing {agency}. To start your website, "
            f"please reply with:\n\n{questions}\n\nFor the kickoff call, any of these "
            f"times works:\n{times}\n\nReply with the one that suits you and an "
            "invitation follows.")
    return f"Welcome to {agency}: next steps for your website", _email_finish(body)


async def start_onboarding_dry(lead_id: int) -> ActionResult:
    ref = await _payment_ref(int(lead_id))
    if ref is None:
        return _refused("no won or paid deal for this lead")
    if await store.onboarding_for_ref(ref) is not None:
        return ActionResult(True, f"already onboarding for {ref}; nothing to do")
    return ActionResult(True, f"would open a project with {len(TASKS)} tasks and draft "
                              f"the welcome email ({ref})")


async def start_onboarding(lead_id: int) -> ActionResult:
    from ..agency import replies
    from ..projects import store as board
    lead_id = int(lead_id)
    ref = await _payment_ref(lead_id)
    if ref is None:
        return _refused("no won or paid deal for this lead")
    existing = await store.onboarding_for_ref(ref)
    if existing is not None:
        return ActionResult(True, f"already onboarding for {ref} "
                                  f"(onboarding {existing['id']}); nothing written")
    name = await store.lead_name(lead_id) or f"lead {lead_id}"
    to, why_to = await replies._recipient(lead_id, "email")
    slots = await _slots()
    subject, body = compose(name, slots)
    oid = await store.insert_onboarding(lead_id=lead_id, payment_ref=ref,
                                        to_address=to or "", subject=subject,
                                        body=body, kickoff_slots=slots)
    if oid is None:
        return ActionResult(True, f"already onboarding for {ref}; nothing written")
    project_id, ptoken = await board.create_project(lead_id, name,
                                                    f"Website for {name}")
    tokens = [ptoken]
    now = datetime.now(UTC)
    for title, kind, owner, days in TASKS:
        _tid, token = await board.add_task(
            project_id=project_id, title=title, kind=kind, owner=owner,
            due_at=now + timedelta(days=days), evidence={"onboarding_id": oid},
            suggested_fix="", source_action="bizops.start_onboarding",
            source_ref=f"bizops_onboardings:{oid}")
        tokens.append(token)
    await store.attach_project(oid, project_id, tokens)
    carded = False
    if to:
        carded = await store.raise_card(
            SEND_ACTION, {"onboarding_id": oid},
            f"Welcome email to {name} ({to}): intake questions and kickoff times",
            name, body, f"{SEND_ACTION}:{oid}")
    note = "; card raised" if carded else f"; no card: {why_to}" if not to else ""
    return ActionResult(True, f"onboarding {oid} for {name}: project {project_id}, "
                              f"{len(TASKS)} tasks{note}",
                        evidence={"onboarding_id": oid, "project_id": project_id,
                                  "payment_ref": ref, "slots": slots},
                        rollback_token=f"onb:{oid}")


async def drop_onboarding(token: str) -> ActionResult:
    from ..projects import store as board
    head, _, rest = str(token).partition(":")
    if head != "onb" or not rest.isdigit():
        return _refused("not an onboarding token")
    row = await store.onboarding(int(rest))
    if row is None:
        return _refused("no such onboarding")
    await store.set_onboarding(int(rest), "dropped")
    done = [await board.restore(t) for t in row.get("history_tokens") or []]
    return ActionResult(True, f"onboarding {rest} dropped; " + "; ".join(map(str, done)))


# ------------------------------------------------------------------ the email

async def _email_refusal(oid: int) -> tuple[dict[str, Any] | None, str | None]:
    from .mail import guard
    row = await store.onboarding(int(oid))
    if row is None:
        return None, "no such onboarding"
    if row.get("status") != "drafted":
        return None, f"onboarding {oid} is {row.get('status')}"
    if not row.get("to_address"):
        return None, "no email address on record for this lead"
    why = await guard(int(row["lead_id"]), str(row["to_address"]),
                      str(row["subject"]), str(row["body"]))
    return (None, why) if why is not None else (row, None)


async def send_onboarding_email_dry(onboarding_id: int) -> ActionResult:
    row, why = await _email_refusal(int(onboarding_id))
    if row is None:
        return _refused(str(why))
    return ActionResult(True, f"would email the welcome to {row['to_address']}",
                        evidence={"to": row["to_address"], "subject": row["subject"],
                                  "text": row["body"]})


async def send_onboarding_email(onboarding_id: int) -> ActionResult:
    from .mail import send
    row, why = await _email_refusal(int(onboarding_id))
    if row is None:
        return _refused(str(why))
    got = await send(SEND_ACTION, f"onb-{int(onboarding_id)}", str(row["to_address"]),
                     str(row["subject"]), str(row["body"]))
    if got.ok:
        await store.set_onboarding(int(onboarding_id), "sent")
    return got
