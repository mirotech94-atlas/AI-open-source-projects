"""tutorco -- the owner's own WhatsApp: templated notices, approval-card prompts,
the daily report, health alerts and one voice call. Notify only.

Every action is Tier 1 with a dry run and a rollback, sends to ONE recipient taken
from the environment (`TUTORCO_OWNER_WHATSAPP`), and a free-text reply approves
nothing. A check that cannot read its source is UNKNOWN, never OK.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from ...kernel.contracts import Action, Check, Severity, Tier
from . import calls, notify

log = logging.getLogger("atlas.tutorco")


class TutorcoModule:
    name = "tutorco"
    title = "Owner notifications"
    requires_env = ()
    optional_env = ("TUTORCO_OWNER_WHATSAPP", "TUTORCO_WHATSAPP_FROM",
                    "TUTORCO_NOTIFY_PER_HOUR", "OWNER_CALLS", "OWNER_CALL_FROM",
                    "CALL_MAX_PER_DAY", "CALL_ESCALATE_MINUTES")
    setup_note = "Set TUTORCO_OWNER_WHATSAPP (E.164) to receive WhatsApp notices."

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self) -> tuple[Check, ...]:
        return (
            Check("tutorco_owner_whatsapp", notify.check_owner_whatsapp,
                  timedelta(minutes=30), Severity.INFO,
                  "owner WhatsApp configured; messages used this hour",
                  baseline_sigma=None),
        )

    def actions(self) -> tuple[Action, ...]:
        return (
            Action("tutorco.notify_owner", Tier.AUTO,
                   "Tell the owner about an event on his own WhatsApp (templated, "
                   "notify only; a reply approves nothing)",
                   run=notify.notify_owner, dry_run=notify.notify_owner_dry,
                   rollback=notify.notify_owner_rollback,
                   params={"kind": "post_decision_pending | client_urgent | portal_fix "
                                   "| prospect_reply",
                           "ref": "the event id", "card": "Slack card reference",
                           "quote": "short client text, wrapped as untrusted"},
                   max_per_hour=12),
            Action("tutorco.notify_daily", Tier.AUTO,
                   "Send the owner the daily agent report on his own WhatsApp "
                   "(built in code, today's New York date only, once per date)",
                   run=notify.notify_daily, dry_run=notify.notify_daily_dry,
                   rollback=notify.notify_daily_rollback,
                   params={"day": "today's date in New York, YYYY-MM-DD"},
                   max_per_hour=2),
            Action("tutorco.notify_alert", Tier.AUTO,
                   "Tell the owner on his own WhatsApp that a health check just went "
                   "from OK to failing (one templated line, notify only; off unless "
                   "WHATSAPP_ALERTS is on, capped per hour)",
                   run=notify.notify_alert, dry_run=notify.notify_alert_dry,
                   rollback=notify.notify_owner_rollback,
                   params={"observation_id": "the failing reading's id"},
                   max_per_hour=6),
            Action("tutorco.notify_card", Tier.AUTO,
                   "Tell the owner on his own WhatsApp that an approval card is waiting "
                   "(a one-time code only for cards on the WhatsApp allowlist; the "
                   "decision itself goes through approvals.decide)",
                   run=notify.notify_card, dry_run=notify.notify_card_dry,
                   rollback=notify.notify_card_rollback,
                   params={"proposal_id": "the pending proposal's id"},
                   max_per_hour=30),
            Action("tutorco.call_owner", Tier.AUTO,
                   "Ring the owner's own phone once with a short fixed message (Twilio "
                   "Voice; off unless OWNER_CALLS is on; one call per event, capped per "
                   "day; the number comes from the environment, never from a caller)",
                   run=calls.call_owner, dry_run=calls.call_owner_dry,
                   rollback=calls.call_owner_rollback,
                   params={"reason": "why: escalation_alert, escalation_card or "
                                     "requested (a closed list)",
                           "event": "the event id this call is for (one call per event)"},
                   # Raised only by kernel/owner_calls (the escalation job and the
                   # strict "call me" parse); never offered to a model.
                   max_per_hour=3, card_only=True),
        )

    async def signals(self):
        return ()

    def brief(self) -> str:
        return "Owner notifications: WhatsApp notices and one voice call, all Tier 1."

    async def agenda(self, within):
        return ()

    async def inbox(self, limit: int = 10):
        return ()


MODULE = TutorcoModule()
