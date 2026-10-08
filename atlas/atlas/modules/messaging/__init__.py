"""messaging — Slack reading, one inbox (CAPABILITIES.md §2.3).

Watches: Slack mentions waiting on a reply, read-only through
`conversations.history` on the channels named in `MESSAGING_SLACK_CHANNELS`.

May do: nothing. There is no action here. WhatsApp was the last platform
`messaging.reply` could send on, and it was removed at Miro's request on
2026-09-28 -- so the action went with it rather than stay in the catalog as
a Tier 2 name that could only ever refuse.

Slack replies are not wired here. Slack already has a channel
(`atlas.channels.slack`) and a typed-command surface
(`atlas.api.slack_events`) of its own; this module only adds reading, per
CAPABILITIES.md §2.3 ("this adds reading and drafting ... not just posting
alerts").

Endpoint assumed: Slack Web API `slack.com/api`, documented in
`slack_read.py` for Miro to correct if a shape changes.

Only `_SENDABLE` platforms are read back from `messaging_inbound`, and there
are none left: rows from WhatsApp and Telegram, both since removed, stay in
the table untouched, but never surface as something waiting on a reply.
"""
from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import (
    Check,
    Health,
    InboxItem,
    Observation,
    Severity,
)
from ...kernel.state import fetch_all
from . import slack_read

log = logging.getLogger("atlas.messaging")

# Platforms whose `messaging_inbound` rows are still live. Empty: every
# platform that wrote there (WhatsApp, Telegram) has been removed. Their rows
# are kept, filtered out of every read, and nothing can reply on them.
_SENDABLE: tuple[str, ...] = ()


VOICE_TRANSCRIBED = "voice note (transcribed)"
VOICE_UNHEARD = "voice note (not transcribed)"


def row_meta(raw: Any) -> dict:
    """`messaging_inbound.meta` as a dict, whether the driver handed back a
    dict, a JSON string (asyncpg's default for JSONB), or nothing at all."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def voice_label(meta: Any) -> str | None:
    """How to label a row whose text is a machine transcript rather than
    something the sender typed, or None for a typed message. Anyone
    approving a reply against it should know it may be mis-heard."""
    meta = row_meta(meta)
    if not meta.get("voice"):
        return None
    return VOICE_TRANSCRIBED if meta.get("transcribed") else VOICE_UNHEARD


async def check_unanswered() -> Observation:
    """Open rows across every platform. DEGRADED past 5 — a queue nobody is
    reading is exactly the failure this check exists to catch. UNKNOWN, not
    a confident zero, if the query itself fails (invariant 7)."""
    try:
        rows = await fetch_all(
            """SELECT platform FROM messaging_inbound
               WHERE replied_at IS NULL AND platform = ANY($1::text[])""",
            list(_SENDABLE))
    except Exception as exc:                            # noqa: BLE001
        return Observation("unanswered", Health.UNKNOWN, detail=str(exc))

    by_platform: dict[str, int] = {}
    for r in rows:
        by_platform[r["platform"]] = by_platform.get(r["platform"], 0) + 1

    slack_pending: list[dict] = []
    slack_failed: str | None = None
    try:
        slack_pending = await slack_read.unanswered()
    except Exception as exc:                             # noqa: BLE001
        # Any stored rows still stand, but the total does not: a
        # Slack that could not be read is not a Slack with nothing in it.
        from .._http import explain
        slack_failed = (str(exc) if isinstance(exc, slack_read.SlackUnreadable)
                        else explain(exc))
        log.warning("slack_read.unanswered failed: %s", slack_failed)
    if slack_pending:
        by_platform["slack"] = len(slack_pending)

    total = len(rows) + len(slack_pending)
    channels = ", ".join(sorted(by_platform)) or "no channel configured"
    if slack_failed:
        # At least `total`: enough to say DEGRADED past five, never OK.
        return Observation(
            "unanswered", Health.DEGRADED if total > 5 else Health.UNKNOWN,
            float(total) if total > 5 else None, "messages",
            f"at least {total} unanswered message(s) across {channels}; "
            f"Slack could not be read ({slack_failed})",
            evidence={"by_platform": by_platform, "unreachable": ["slack"]})
    return Observation(
        "unanswered", Health.DEGRADED if total > 5 else Health.OK,
        float(total), "messages",
        f"{total} unanswered message(s) across {channels}",
        evidence={"by_platform": by_platform})


async def inbox(limit: int = 10) -> tuple[InboxItem, ...]:
    """Rows waiting on a reply, most recent first."""
    # A failed read raises: the brief names this inbox as unreadable rather
    # than folding it into "Nothing waiting on you" (invariant 7).
    rows = await fetch_all(
        """SELECT id, at, platform, sender, text, meta FROM messaging_inbound
           WHERE replied_at IS NULL AND platform = ANY($2::text[])
           ORDER BY at DESC LIMIT $1""", limit, list(_SENDABLE))
    now = datetime.now(UTC)
    items = []
    for r in rows:
        age_h = (now - r["at"]).total_seconds() / 3600
        label = voice_label(r.get("meta"))
        why = f"unanswered {age_h:.0f}h"
        items.append(InboxItem(
            at=r["at"], sender=r["sender"], subject=r["text"][:80],
            urgency=Severity.NORMAL,
            why=f"{label}, {why}" if label else why,
            reference=str(r["id"]), source="messaging"))
    return tuple(items)


class MessagingModule:
    name = "messaging"
    title = "Messaging (Slack)"
    # Not gated: the module loads without a channel list, and its check says
    # "no channel configured" rather than staying dark.
    requires_env = ()
    optional_env = ("MESSAGING_SLACK_CHANNELS",)
    setup_note = ("Slack reading: MESSAGING_SLACK_CHANNELS (comma-separated "
                 "channel ids); reuses SLACK_BOT_TOKEN/SLACK_APPROVER_IDS.")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("unanswered", check_unanswered, timedelta(minutes=30),
                  Severity.NORMAL, "messages waiting on a reply",
                  baseline_sigma=None),
        )

    def actions(self):
        return ()

    def brief(self) -> str:
        return "Messaging: Slack inbox"

    async def inbox(self, limit: int = 10):
        return await inbox(limit)


MODULE = MessagingModule()
