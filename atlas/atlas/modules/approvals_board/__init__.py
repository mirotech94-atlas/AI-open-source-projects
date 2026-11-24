"""approvals_board -- the #approvals channel and its "Waiting for you" board.

The board itself is drawn by `kernel.board` from the scheduler step
`approvals_board`; it only lists cards and can approve nothing. This module
holds the two things that are not drawing:

  * `approvals_board.create_channel` (Tier 1, dry run + rollback): makes the
    channel in ATLAS's own Slack workspace with the bot's
    `conversations.create` when SLACK_APPROVALS_CHANNEL is not a channel id
    and none was made before. Idempotent: a channel of that name that already
    exists (`name_taken`) is found and joined, not duplicated. Every approver
    in SLACK_APPROVER_IDS is invited, so Miro is in it. The id is saved in
    `approvals_board`. Rollback: the channel is archived (only one this
    action created).
  * the `board` check: whether the board was drawn recently. UNKNOWN when it
    cannot be (no token, no channel, the queue unreadable) -- never OK on a
    board that is not being kept.
"""
from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    Health,
    Observation,
    Severity,
    Tier,
)

_CHANNEL_ID = re.compile(r"^C[A-Z0-9]{6,20}$")
_USER_ID = re.compile(r"^[UW][A-Z0-9]{6,20}$")
STALE_AFTER = timedelta(minutes=20)


def _approvers() -> list[str]:
    return [u.strip() for u in os.getenv("SLACK_APPROVER_IDS", "").split(",")
            if _USER_ID.match(u.strip())]


async def _saved() -> str | None:
    from ...kernel import board
    return await board.channel()


async def create_channel_dry() -> ActionResult:
    from ...kernel import board
    if not os.getenv("SLACK_BOT_TOKEN", "").strip():
        return ActionResult(False, "SLACK_BOT_TOKEN is not set")
    if await _saved():
        return ActionResult(False, "the approvals board already has a channel")
    invite = _approvers()
    return ActionResult(True, (
        f"would create (or find) #{board.channel_name()} in ATLAS's Slack workspace "
        f"for the approvals board, and invite {len(invite)} approver(s)"))


async def _find_by_name(name: str) -> str:
    """A channel of this name that already exists (a read)."""
    from ...kernel import board
    cursor = ""
    for _ in range(10):
        data = await board._slack("conversations.list", {
            "exclude_archived": "true", "types": "public_channel", "limit": "200",
            **({"cursor": cursor} if cursor else {})}, form=True)
        if not data.get("ok"):
            return ""
        for ch in data.get("channels") or []:
            if str(ch.get("name") or "") == name:
                return str(ch.get("id") or "")
        cursor = str((data.get("response_metadata") or {}).get("next_cursor") or "")
        if not cursor:
            return ""
    return ""


async def create_channel() -> ActionResult:
    from ...kernel import board
    from ...kernel.state import execute_sql
    dry = await create_channel_dry()
    if not dry.ok:
        return ActionResult(False, f"refused: {dry.detail}")
    name = board.channel_name()
    data = await board._slack("conversations.create", {"name": name, "is_private": False})
    made = bool(data.get("ok"))
    if made:
        channel_id = str((data.get("channel") or {}).get("id") or "")
    elif data.get("error") == "name_taken":
        channel_id = await _find_by_name(name)
        if channel_id:
            joined = await board._slack("conversations.join", {"channel": channel_id})
            if not joined.get("ok"):
                return ActionResult(False, f"#{name} exists but the bot could not join "
                                           f"it: {joined.get('error')}")
    else:
        return ActionResult(False, f"Slack refused: {data.get('error')}")
    if not _CHANNEL_ID.match(channel_id):
        return ActionResult(False, f"#{name} could not be found or made")
    invited: list[str] = []
    errors: list[str] = []
    approvers = _approvers()
    if approvers:
        inv = await board._slack("conversations.invite",
                                 {"channel": channel_id, "users": ",".join(approvers)})
        if inv.get("ok") or inv.get("error") == "already_in_channel":
            invited = approvers
        else:
            errors.append(str(inv.get("error")))
    await execute_sql(
        """INSERT INTO approvals_board (key, channel, ts) VALUES ('channel', $1, NULL)
           ON CONFLICT (key) DO UPDATE SET channel = $1, updated_at = now()""",
        channel_id)
    detail = (f"{'created' if made else 'found'} #{name} ({channel_id}) for the "
              f"approvals board; invited {len(invited)} approver(s)"
              + (f"; the invite failed: {', '.join(errors)}" if errors else ""))
    return ActionResult(True, detail, evidence={"channel": channel_id, "created": made},
                        rollback_token=channel_id if made else "")


async def create_channel_rollback(token: str) -> ActionResult:
    from ...kernel import board
    from ...kernel.state import execute_sql
    if not token:
        return ActionResult(True, "no channel was created; nothing to roll back")
    data = await board._slack("conversations.archive", {"channel": token})
    await execute_sql("DELETE FROM approvals_board WHERE channel = $1", token)
    if not data.get("ok"):
        return ActionResult(False, f"Slack refused the archive: {data.get('error')}")
    return ActionResult(True, f"archived the approvals channel {token}")


async def check_board() -> Observation:
    from ...kernel import board
    seen: dict[str, Any] = board.status()
    if not seen["enabled"]:
        return Observation("board", Health.UNKNOWN, detail=(
            "the approvals board is off (no SLACK_BOT_TOKEN, or APPROVALS_BOARD=off)"))
    at = seen["at"]
    if at is None:
        return Observation("board", Health.UNKNOWN, detail=(
            "the approvals board has not been drawn by this process yet"
            + (f": {seen['error']}" if seen["error"] else "")))
    age = datetime.now(UTC) - at
    if age > STALE_AFTER:
        return Observation("board", Health.UNKNOWN, detail=(
            f"the approvals board was last drawn {int(age.total_seconds() // 60)} min "
            f"ago" + (f": {seen['error']}" if seen["error"] else "")))
    return Observation("board", Health.OK, float(age.total_seconds()), "seconds",
                       f"the approvals board was drawn at {at:%H:%M} UTC",
                       evidence={"drawn_at": at.isoformat()})


class ApprovalsBoardModule:
    name = "approvals_board"
    title = "Approvals board"
    requires_env = ()
    optional_env = ("SLACK_APPROVALS_CHANNEL", "APPROVALS_BOARD",
                    "APPROVALS_BOARD_REFRESH_MIN")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (Check("board", check_board, timedelta(minutes=10), Severity.NORMAL,
                      "the approvals board in #approvals is current",
                      baseline_sigma=None),)

    def actions(self):
        return (Action(
            name="approvals_board.create_channel", tier=Tier.AUTO,
            summary=("Create (or find) the #approvals channel in ATLAS's own Slack "
                     "workspace for the approvals board, and invite the approvers"),
            run=create_channel, dry_run=create_channel_dry,
            rollback=create_channel_rollback, max_per_hour=1),)

    async def signals(self):
        return ()

    def brief(self) -> str:
        return "Approvals board: every pending card, in one pinned message in #approvals"


MODULE = ApprovalsBoardModule()
