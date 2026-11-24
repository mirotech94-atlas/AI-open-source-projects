"""bizops -- the BizOps agent's module (six agents phase 9).

After a deal is won: onboarding (a project, five tasks, the welcome email
card), a Stripe invoice in TEST MODE ONLY (a live key is refused in code),
payment reminders at most one per invoice per 7 days, automation ideas from
work that repeats, and an 08:00 New York digest to #agency-alerts in which
every figure carries the time it was read.

Tier 1: start_onboarding, draft_invoice, draft_payment_reminder,
find_repeats, daily_digest -- our own rows and cards, each with a rollback.
Tier 2 (cards): send_onboarding_email, send_invoice, send_payment_reminder
(QA gated) and propose_automation. Nothing here moves real money; a refund
or a deletion is Tier 3 and does not exist."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    FailureClass,
    Health,
    Observation,
    Severity,
    Tier,
)
from . import onboarding, repeats, store, stripe_invoice

log = logging.getLogger("atlas.bizops")

TOPIC = "agency_decisions"
STALLED_DAYS = 3


def _refused(why: str) -> ActionResult:
    return ActionResult(False, why, failure_class=FailureClass.VALIDATION)


# ------------------------------------------------------------------ digest

async def digest_lines(now: datetime | None = None) -> list[str]:
    """Every figure with its read time, or UNKNOWN with the reason."""
    now = now or datetime.now(UTC)
    stamp = f"(read {now:%Y-%m-%d %H:%M} UTC)"
    try:
        r = await store.digest_reads()
    except Exception as exc:                            # noqa: BLE001
        return [(f"UNKNOWN: the BizOps figures could not be read "
                 f"({type(exc).__name__}, tried {now:%Y-%m-%d %H:%M} UTC)")]
    _secret, why = stripe_invoice.key()
    return [
        f"Open test invoices: {r['open']}, ${r['open_total']} in all {stamp}",
        f"Overdue invoices: {r['overdue']} {stamp}",
        f"Invoices paid in the last 24 hours: {r['paid_day']} {stamp}",
        f"Welcome emails waiting for your approval: {r['onboard_waiting']} {stamp}",
        f"Automation ideas waiting: {r['ideas_new']} {stamp}",
        "Stripe: test mode key set" if why is None else f"Stripe: UNKNOWN ({why})",
    ]


async def daily_digest_dry() -> ActionResult:
    return ActionResult(True, "would write the BizOps digest to reports and "
                              "#agency-alerts")


async def daily_digest() -> ActionResult:
    from ...kernel.comms import Outbound, notify
    now = datetime.now(UTC)
    lines = await digest_lines(now)
    body = "\n".join(f"- {line}" for line in lines)
    title = f"BizOps digest {now:%Y-%m-%d}"
    rid = await store.insert_report(title, body, [{"title": title, "lines": lines}])
    await notify(Outbound(title=title, body=body, topic="agency_alerts"))
    return ActionResult(True, f"{title} written (report {rid})",
                        evidence={"report_id": rid}, rollback_token=str(rid))


async def withdraw_digest(token: str) -> ActionResult:
    if not str(token).isdigit():
        return _refused("not a report id")
    await store.withdraw_report(int(token))
    return ActionResult(True, f"report {token} withdrawn")


# -------------------------------------------------------------------- jobs

async def _run(action: str, params: dict[str, Any], why: str) -> str:
    from ...kernel import executor
    got = await executor.execute(executor.ExecutionRequest(action, params, reason=why))
    return str(got.detail)


async def run_daily() -> dict[str, str]:
    """The bizops_digest job (08:00 New York): onboard new wins, draft
    reminders for overdue invoices, then the digest -- every step an
    executor call, so watch-only holds all of it."""
    out: dict[str, str] = {}
    try:
        leads = await store.leads_to_onboard()
    except Exception as exc:                            # noqa: BLE001
        leads, out["leads"] = [], f"unreadable: {type(exc).__name__}"
    for lead in leads:
        out[f"onboard:{lead}"] = await _run("bizops.start_onboarding",
                                            {"lead_id": lead}, "a won or paid deal")
    try:
        overdue = await store.overdue_invoices()
    except Exception as exc:                            # noqa: BLE001
        overdue, out["overdue"] = [], f"unreadable: {type(exc).__name__}"
    for inv in overdue:
        out[f"remind:{inv['id']}"] = await _run(
            "bizops.draft_payment_reminder", {"invoice_id": int(inv["id"])},
            "the invoice is past due")
    out["digest"] = await _run("bizops.daily_digest", {}, "the 08:00 digest")
    return out


async def run_find_repeats() -> dict[str, str]:
    return {"find_repeats": await _run("bizops.find_repeats", {},
                                       "the daily repeat scan")}


# ------------------------------------------------------------------ checks

async def check_stripe_key() -> Observation:
    """No HTTP: whether the key is a usable TEST key."""
    secret, why = stripe_invoice.key()
    if secret is not None:
        return Observation("stripe_invoice_key", Health.OK,
                           detail="a Stripe test-mode key is set")
    if why == stripe_invoice.LIVE_REFUSAL:
        return Observation("stripe_invoice_key", Health.FAILING,
                           detail=f"{stripe_invoice.KEY_ENV} holds a live key: "
                                  f"{why}; set a test key")
    return Observation("stripe_invoice_key", Health.UNKNOWN, detail=str(why))


async def check_overdue() -> Observation:
    now = datetime.now(UTC)
    try:
        rows = await store.overdue_invoices()
    except Exception as exc:                            # noqa: BLE001
        return Observation("invoices_overdue", Health.UNKNOWN,
                           detail=f"bizops_invoices unreadable: {type(exc).__name__}")
    return Observation("invoices_overdue", Health.DEGRADED if rows else Health.OK,
                       float(len(rows)), "invoices",
                       f"{len(rows)} overdue test invoice(s) "
                       f"(read {now:%Y-%m-%d %H:%M} UTC)",
                       evidence={"ids": [int(r["id"]) for r in rows][:20]})


async def check_stalled() -> Observation:
    now = datetime.now(UTC)
    try:
        n = await store.stalled_onboardings(STALLED_DAYS)
    except Exception as exc:                            # noqa: BLE001
        return Observation("onboarding_stalled", Health.UNKNOWN,
                           detail=f"bizops_onboardings unreadable: "
                                  f"{type(exc).__name__}")
    return Observation("onboarding_stalled", Health.DEGRADED if n else Health.OK,
                       float(n), "onboardings",
                       f"{n} welcome email(s) unsent after {STALLED_DAYS} days "
                       f"(read {now:%Y-%m-%d %H:%M} UTC)")


# ------------------------------------------------------------------ module

def _detail(params: dict[str, Any]) -> str:
    key, value = next(iter(params.items()), ("", ""))
    return f"BizOps {key.replace('_', ' ')} {value} (text in the card's evidence)"


class BizOpsModule:
    name = "bizops"
    title = "Business automation"
    requires_env: tuple[str, ...] = ()
    optional_env = (stripe_invoice.KEY_ENV,)
    setup_note = ("Stripe invoices need a TEST-mode restricted key in "
                  f"{stripe_invoice.KEY_ENV} (CONFIGURE.md, six agents step 4); "
                  "until then the invoice checks read UNKNOWN.")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self) -> tuple[Check, ...]:
        return (
            Check("invoice_status", stripe_invoice.check_invoice_status,
                  timedelta(minutes=30), Severity.INFO,
                  "open test invoices read from Stripe; paid ones marked paid",
                  baseline_sigma=None),
            Check("invoices_overdue", check_overdue, timedelta(hours=1), Severity.INFO,
                  "test invoices past their due date", baseline_sigma=None),
            Check("onboarding_stalled", check_stalled, timedelta(hours=6),
                  Severity.INFO, "welcome emails not sent within 3 days",
                  baseline_sigma=None),
            Check("stripe_invoice_key", check_stripe_key, timedelta(hours=1),
                  Severity.NORMAL, "the invoice key is a Stripe TEST key",
                  baseline_sigma=None),
        )

    def actions(self) -> tuple[Action, ...]:
        card: dict[str, Any] = {"card_only": True, "verbose_card": True,
                                "topic_for": lambda _p: TOPIC, "card_detail": _detail,
                                "max_per_hour": 6}
        return (
            Action("bizops.start_onboarding", Tier.AUTO,
                   "Open the project, five tasks and the welcome email card for a "
                   "won or paid deal (once per payment)",
                   run=onboarding.start_onboarding,
                   dry_run=onboarding.start_onboarding_dry,
                   rollback=onboarding.drop_onboarding,
                   params={"lead_id": "the agency lead id"}, max_per_hour=10),
            Action("bizops.send_onboarding_email", Tier.ASK,
                   "Email the client the welcome, intake questions and kickoff times",
                   run=onboarding.send_onboarding_email,
                   dry_run=onboarding.send_onboarding_email_dry, irreversible=True,
                   params={"onboarding_id": "the bizops_onboardings id"}, **card),
            Action("bizops.draft_invoice", Tier.AUTO,
                   "Draft a TEST MODE Stripe invoice ($500 or $300) for a won deal",
                   run=stripe_invoice.draft_invoice,
                   dry_run=stripe_invoice.draft_invoice_dry,
                   rollback=stripe_invoice.withdraw_invoice,
                   params={"lead_id": "the agency lead id"}, max_per_hour=6),
            Action("bizops.send_invoice", Tier.ASK,
                   "Send a TEST MODE Stripe invoice (live keys are refused)",
                   run=stripe_invoice.send_invoice,
                   dry_run=stripe_invoice.send_invoice_dry,
                   rollback=stripe_invoice.void_invoice,
                   params={"invoice_id": "the bizops_invoices id"}, **card),
            Action("bizops.draft_payment_reminder", Tier.AUTO,
                   "Draft a payment reminder for an overdue test invoice "
                   "(one per 7 days)",
                   run=stripe_invoice.draft_payment_reminder,
                   dry_run=stripe_invoice.draft_payment_reminder_dry,
                   rollback=stripe_invoice.withdraw_reminder,
                   params={"invoice_id": "the bizops_invoices id"}, max_per_hour=10),
            Action("bizops.send_payment_reminder", Tier.ASK,
                   "Email the client a payment reminder with the invoice link",
                   run=stripe_invoice.send_payment_reminder,
                   dry_run=stripe_invoice.send_payment_reminder_dry, irreversible=True,
                   params={"reminder_id": "the bizops_reminders id"}, **card),
            Action("bizops.find_repeats", Tier.AUTO,
                   "Find work seen 3+ times in 14 days and card it as an idea",
                   run=repeats.find_repeats, dry_run=repeats.find_repeats_dry,
                   rollback=repeats.dismiss_ideas, max_per_hour=2),
            Action("bizops.propose_automation", Tier.ASK,
                   "Add a build task for you to automate a repeated piece of work",
                   run=repeats.propose_automation,
                   dry_run=repeats.propose_automation_dry,
                   rollback=repeats.undo_proposal,
                   params={"idea_id": "the bizops_automation_ideas id"}, **card),
            Action("bizops.daily_digest", Tier.AUTO,
                   "Write the BizOps digest (every figure with its read time) to "
                   "reports and #agency-alerts",
                   run=daily_digest, dry_run=daily_digest_dry,
                   rollback=withdraw_digest, max_per_hour=2),
        )

    async def signals(self) -> tuple:
        return ()


MODULE = BizOpsModule()
