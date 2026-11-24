"""messages — Instagram DMs, in one inbox.

Watches: messages from someone other than Miro, with no reply from him
within 4 hours (`check_unanswered`, computed live from the Graph API —
DEGRADED past 5, UNKNOWN whenever Instagram does not answer).

May do exactly two things:

  * `messages.draft` (Tier AUTO) — pulls anything new from Instagram into
    `messages_inbound` and fills a draft for every open row missing one.
    Tier AUTO because both the sync and the draft are writes to ATLAS's own
    table; nothing here reaches Instagram. Drafting uses
    `Need.PRIVATE`: these are personal messages, and the model that reads
    them never leaves the box.
  * `messages.reply` (Tier ASK, irreversible) — the only place in ATLAS that
    can put words in Miro's mouth to anyone on Instagram.

This is the mechanism behind "if I'm not next to my phone, he can send it
for me": a reply leaves only through a proposal Miro approves — from Slack,
the dashboard, or a call ATLAS placed to him. The approval IS the tap that
would otherwise have happened on his phone. Nothing here, and nothing this
module can reach, sends unattended.

This module is the only path to anyone on Instagram DM at all.

Endpoint assumed: the Graph API Messenger platform (`instagram_dm.py`) — see
its docstring for the exact shape assumed, so Miro can correct it if it
drifts.

Only `platform = 'instagram'` rows are read back from `messages_inbound`:
older rows from a bridge that has since been removed stay in the table
untouched, but never surface as something waiting on a reply.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from ...kernel import idempotency
from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    Health,
    InboxItem,
    Observation,
    Severity,
    Signal,
    Tier,
)
from ...kernel.llm import LocalModelUnavailable, Message, Need, think
from ...kernel.permissions import scan_for_injection, wrap_untrusted
from ...kernel.state import execute_sql, fetch_all, fetch_one
from ...kernel.voice_persona import EMAIL_STYLE, reask, strip_banned, voice_problems
from . import instagram_dm

UNANSWERED_WINDOW = timedelta(hours=4)
DEGRADED_ABOVE = 5

# `check_unanswered` needs enough history that a busy account's stale thread
# doesn't fall outside the window it fetched and silently drop out of the
# count. `recent()`'s own default (25) is sized for drafting a handful of
# open messages, not for this — pass this explicitly.
CHECK_INSTAGRAM_LIMIT = 100

# The only platform this module still sends on. Rows written by a removed
# bridge are left in the table and filtered out of every read below.
PLATFORM = "instagram"

DRAFT_SYSTEM = (
    "You draft a short reply to a personal message, for Miro to review and "
    "send himself if he approves it. Match the tone and length of the "
    "message below -- this is a text or a DM, not an email. Use only what "
    "is in the message; do not invent facts or commitments. Output the "
    "reply text only, nothing else. It is written as Miro, in his own words; "
    "add no signature.\n\n" + EMAIL_STYLE
)


def _parse_at(value: object) -> datetime:
    """Best-effort parse of whatever timestamp shape a source returned.
    Never raises: an unparseable stamp becomes "now" rather than blocking
    ingestion on a guess about ordering. Epoch seconds or milliseconds and
    ISO 8601 (what the Graph API reports) are all handled."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(ts, UTC)
        except (OverflowError, OSError, ValueError):
            return datetime.now(UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value).astimezone(UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


async def _sync() -> None:
    """Store anything new from Instagram into `messages_inbound`, keyed
    on `external_id` so a re-fetch is harmless. Never called from a Check —
    a Check may only read (invariant 1) — which is why this is only ever
    reached from `messages.draft` (a Tier AUTO action; writing ATLAS's own
    table is exactly what that tier allows) and from `signals()`, which the
    scheduler already permits to write (the same shape as `mail.signals()`
    calling `ingest.sweep()`)."""
    platform = PLATFORM
    rows, _reason = await instagram_dm.recent()
    for row in rows:
        if row.get("is_from_me") or not row.get("external_id"):
            continue
        # A stranger's DM is the highest-value injection surface this
        # module has: it becomes a draft Miro taps approve on. Logged
        # here, at ingestion, same as `mail.ingest_message` — the text
        # itself is still only ever quoted through `wrap_untrusted` at
        # prompt time (invariant 4), never treated as an instruction.
        sender = row.get("sender", "")
        text = row.get("text", "")
        if flag := scan_for_injection(text, f"{platform}:{sender}"):
            from ...kernel.audit import audit
            await audit("injection.suspected", "messages",
                       {"detail": flag, "platform": platform, "from": sender})
        await execute_sql(
            """INSERT INTO messages_inbound
                     (at, platform, thread_id, sender, text, external_id)
               VALUES ($1,$2,$3,$4,$5,$6)
               ON CONFLICT (external_id) DO NOTHING""",
            _parse_at(row.get("at")), platform, row.get("thread_id", ""),
            sender, text, row["external_id"])


async def check_unanswered() -> Observation:
    """Messages from someone else with no reply from Miro within 4 hours,
    computed live from Instagram — not from `messages_inbound`, which only
    `messages.draft` may write (invariant 1: a Check only reads). UNKNOWN
    whenever Instagram does not answer: with no source read, there is no
    count to report (invariant 7).

    Judged per thread by its LATEST message only: if the newest thing in a
    thread is from Miro (an approved `messages.reply`, or him answering from
    his own phone — both show up as `is_from_me`), the thread reads as
    answered even if an earlier message from the other side once sat
    unanswered for days. That earlier gap is real but stale news by the time
    someone has replied since; a per-message count would re-flag history
    every run instead of what is actually still open.

    Fetches `CHECK_INSTAGRAM_LIMIT` messages rather than `recent()`'s small
    drafting default. If Instagram still returns exactly that many, the
    window may not reach every thread's newest message on a busy account —
    a stale thread just outside it would silently read as answered, which is
    the invariant-7 failure this exists to avoid. A full window is reported
    as "at least N" and never as Health.OK, even when N itself is small."""
    ig_rows, ig_reason = await instagram_dm.recent(limit=CHECK_INSTAGRAM_LIMIT)
    if ig_reason:
        return Observation(
            "unanswered", Health.UNKNOWN,
            detail=f"could not check Instagram: {ig_reason}",
            evidence={"by_platform": {}, "unreachable": [f"Instagram ({ig_reason})"],
                      "truncated": []})

    now = datetime.now(UTC)
    by_platform: dict[str, int] = {}
    latest: dict[str, dict] = {}
    for row in ig_rows:
        tid = row.get("thread_id", "")
        at = _parse_at(row.get("at"))
        seen = latest.get(tid)
        if seen is None or at > seen["at"]:
            latest[tid] = {"at": at, "is_from_me": bool(row.get("is_from_me"))}
    total = sum(
        1 for t in latest.values()
        if not t["is_from_me"] and (now - t["at"]) > UNANSWERED_WINDOW)
    if total:
        by_platform[PLATFORM] = total

    # A window that came back with exactly as many rows as we asked for may
    # be hiding older threads outside it — treat that as "at least N", not a
    # confident count, and never let it read as a healthy OK.
    truncated: list[tuple[str, int]] = []
    if len(ig_rows) == CHECK_INSTAGRAM_LIMIT:
        truncated.append(("Instagram", CHECK_INSTAGRAM_LIMIT))

    if truncated:
        windows = "; ".join(
            f"{name}'s window of {limit} messages was full"
            for name, limit in truncated)
        detail = (f"at least {total} unanswered ({windows} — older threads "
                  f"may be hidden)")
    else:
        detail = f"{total} unanswered message(s)"
    if by_platform:
        detail += " — " + ", ".join(f"{k}: {v}" for k, v in by_platform.items())

    health = (Health.DEGRADED if truncated or total > DEGRADED_ABOVE
             else Health.OK)
    return Observation(
        "unanswered", health, float(total), "messages", detail,
        evidence={"by_platform": by_platform, "unreachable": [],
                 "truncated": [name for name, _ in truncated]})


async def draft_replies() -> ActionResult:
    """`messages.draft`. Syncs anything new into `messages_inbound`, then
    fills a draft for every open row that still needs one."""
    await _sync()
    try:
        rows = await fetch_all(
            """SELECT id, platform, sender, text FROM messages_inbound
               WHERE replied_at IS NULL AND draft IS NULL
                 AND platform = $1""", PLATFORM)
    except Exception as exc:  # noqa: BLE001
        return ActionResult(False, f"could not read open messages: {exc}")
    if not rows:
        return ActionResult(True, "no open message needs a draft")

    drafted_ids: list[int] = []
    hashes: dict[str, str] = {}      # id -> sha256[:12] of the stored draft
    for row in rows:
        wrapped = wrap_untrusted(row["text"], f"{row['platform']}:{row['sender']}")
        try:
            completion = await think(
                Need.PRIVATE, DRAFT_SYSTEM, [Message("user", wrapped)],
                max_tokens=400)
        except LocalModelUnavailable as exc:
            # Personal messages are never drafted anywhere else. What was
            # already drafted stays undoable through the rollback token.
            return ActionResult(
                False, f"{exc}; drafted {len(drafted_ids)} of {len(rows)} "
                       f"open message(s) before it stopped",
                evidence={"ids": drafted_ids},
                rollback_token=",".join(str(i) for i in drafted_ids) or None)
        body = completion.text.strip()
        if body and (bad := voice_problems(body)):
            body = await reask(think, Need.PRIVATE, DRAFT_SYSTEM,
                               [Message("user", wrapped)], body, bad,
                               max_tokens=400) or body
            # No rule holds a draft Miro reads before he sends it: what is
            # still off-voice is cut, not kept.
            body = strip_banned(body) if voice_problems(body) else body
        if not body:
            continue
        await execute_sql(
            "UPDATE messages_inbound SET draft = $2 WHERE id = $1",
            row["id"], body)
        drafted_ids.append(row["id"])
        hashes[str(row["id"])] = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]

    token = ",".join(str(i) for i in drafted_ids)
    return ActionResult(
        True, f"drafted {len(drafted_ids)} of {len(rows)} open message(s)",
        evidence={"ids": drafted_ids, "draft_hashes": hashes}, rollback_token=token)


async def draft_dry() -> ActionResult:
    """Preview only: counts what is open and undrafted right now. Makes no
    LLM call and writes nothing — `_sync()` runs only in the real action."""
    try:
        rows = await fetch_all(
            """SELECT id FROM messages_inbound
               WHERE replied_at IS NULL AND draft IS NULL
                 AND platform = $1""", PLATFORM)
    except Exception as exc:  # noqa: BLE001
        return ActionResult(False, f"could not read open messages: {exc}")
    return ActionResult(
        True, f"would draft up to {len(rows)} open message(s), plus "
              f"anything new since the last sync")


async def draft_rollback(token: str) -> ActionResult:
    """Clears the draft text `draft_replies` just wrote. The ingested rows
    themselves stay — they are Miro's messages, not something to undo."""
    if not token:
        return ActionResult(True, "nothing to roll back")
    ids = [int(t) for t in token.split(",") if t]
    for i in ids:
        await execute_sql(
            "UPDATE messages_inbound SET draft = NULL WHERE id = $1", i)
    return ActionResult(True, f"cleared {len(ids)} draft(s)")


async def _recipient(row: dict, *, cache: bool) -> tuple[str | None, str | None]:
    """The IGSID a reply to `row` goes to: the one cached with the stored
    conversation, else the conversation's other participant read from
    Instagram (and, when `cache`, stored on every row of that thread).

    `thread_id` is the CONVERSATION id `recent()` returns. Sending to it as
    though it were the person's id was the bug this exists for: the Graph
    send endpoint addresses a person. There is no fallback to `sender` --
    a conversation with no resolvable participant is refused, not guessed."""
    cached = (row.get("recipient_id") or "").strip()
    if cached:
        return cached, None
    recipient, reason = await instagram_dm.resolve_recipient(row["thread_id"])
    if recipient is None:
        return None, reason
    if cache:
        await execute_sql(
            """UPDATE messages_inbound SET recipient_id = $2
               WHERE thread_id = $1 AND platform = $3""",
            row["thread_id"], recipient, PLATFORM)
    return recipient, None


async def reply(inbound_id: int, text: str = "") -> ActionResult:
    """`messages.reply` — Tier ASK, irreversible. The only place in ATLAS
    that can put words in Miro's mouth to a third party on Instagram.
    Reached only through an approved proposal — from Slack, the
    dashboard, or a call ATLAS placed to him; the approval IS the tap that
    would otherwise have happened on his phone. Empty `text` sends the
    stored draft; refuses when there is neither, and when the
    conversation's other participant cannot be resolved."""
    row = await fetch_one(
        """SELECT platform, thread_id, recipient_id, draft, replied_at
           FROM messages_inbound WHERE id = $1""", inbound_id)
    if row is None:
        return ActionResult(False, f"no inbound message #{inbound_id}")

    body = text.strip() or (row["draft"] or "").strip()
    if not body:
        return ActionResult(False, "refused: no text given and no draft stored")

    platform = row["platform"]
    thread_id = row["thread_id"]
    if platform != PLATFORM:
        return ActionResult(False, f"messages.reply does not send on {platform!r}")
    # Resolved BEFORE the claim: a refusal here must leave no claim behind.
    recipient, why = await _recipient(row, cache=True)
    if recipient is None:
        return ActionResult(
            False, f"refused: no recipient for conversation {thread_id}: {why}",
            evidence={"platform": platform, "thread_id": thread_id})
    # One send per run (RA-B7): Instagram takes no idempotency key, so the
    # send is claimed first and a retry of the same card finds the claim.
    claim_key = f"inbound:{inbound_id}|" + idempotency.run_key(
        "messages.reply", {"inbound_id": inbound_id, "text": text})
    claimed = await idempotency.claim("messages.reply", claim_key)
    if claimed == idempotency.SENT:
        return ActionResult(True, idempotency.refusal("messages.reply", claimed),
                            evidence={"platform": platform, "thread_id": thread_id,
                                      "recipient_id": recipient,
                                      "deduplicated": True})
    if claimed != idempotency.CLAIMED:
        return ActionResult(
            False, f"not sent: {idempotency.refusal('messages.reply', claimed)}")
    # A raise here leaves the claim in place: the outcome is unknown.
    sent, reason = await instagram_dm.send(thread_id, recipient, body)
    await idempotency.finish("messages.reply", claim_key, reason,
                             f"replied on {platform}")
    if reason:
        return ActionResult(False, f"send failed: {reason}")

    await execute_sql(
        "UPDATE messages_inbound SET replied_at = now() WHERE id = $1",
        inbound_id)
    evidence = {"platform": platform, "thread_id": thread_id,
                "recipient_id": recipient}
    if isinstance(sent, dict) and sent.get("message_id"):
        evidence["message_id"] = str(sent["message_id"])
    return ActionResult(True, f"replied on {platform}", evidence=evidence)


async def reply_dry(inbound_id: int, text: str = "") -> ActionResult:
    """Report what `reply` would do, without sending or writing anything.
    Resolving an uncached recipient is a read, so the preview refuses the
    same conversations the real run would."""
    row = await fetch_one(
        """SELECT platform, thread_id, recipient_id, draft, replied_at
           FROM messages_inbound WHERE id = $1""", inbound_id)
    if row is None:
        return ActionResult(False, f"no inbound message #{inbound_id}")
    body = text.strip() or (row["draft"] or "").strip()
    if not body:
        return ActionResult(False, "refused: no text given and no draft stored")
    if row["platform"] != PLATFORM:
        return ActionResult(
            False, f"messages.reply does not send on {row['platform']!r}")
    recipient, why = await _recipient(row, cache=False)
    if recipient is None:
        return ActionResult(
            False, f"refused: no recipient for conversation "
                   f"{row['thread_id']}: {why}")
    already = (" (already replied — would send again)"
              if row["replied_at"] else "")
    return ActionResult(
        True, f"would send on {row['platform']} to {recipient}{already}")


async def inbox(limit: int = 10) -> tuple[InboxItem, ...]:
    """Open items, most recent first: sender, first 80 characters, and
    whether a draft is ready."""
    # A failed read raises: the brief names this inbox as unreadable rather
    # than folding it into "Nothing waiting on you" (invariant 7).
    rows = await fetch_all(
        """SELECT id, at, platform, sender, text, draft
           FROM messages_inbound
           WHERE replied_at IS NULL AND platform = $2
           ORDER BY at DESC LIMIT $1""", limit, PLATFORM)
    items = []
    for r in rows:
        status = "draft ready" if r["draft"] else "no draft yet"
        items.append(InboxItem(
            at=r["at"], sender=r["sender"], subject=r["text"][:80],
            urgency=Severity.NORMAL, why=status,
            reference=str(r["id"]), source="messages"))
    return tuple(items)


class MessagesModule:
    name = "messages"
    title = "Messages (Instagram DM)"
    # Optional rather than required, like `messaging`: an unset token lights
    # up its checks as UNKNOWN with the reason, instead of keeping them dark.
    requires_env = ()
    optional_env = ("INSTAGRAM_ACCOUNT_ID", "INSTAGRAM_TOKEN")
    setup_note = ("Instagram DMs: "
                 "INSTAGRAM_ACCOUNT_ID, INSTAGRAM_TOKEN — the token needs "
                 "the instagram_manage_messages permission.")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("dm_scope", instagram_dm.check_scope, timedelta(hours=6),
                  Severity.NORMAL,
                  "Instagram token still carries instagram_manage_messages",
                  baseline_sigma=None),
            Check("unanswered", check_unanswered, timedelta(minutes=30),
                  Severity.NORMAL, "messages waiting on a reply",
                  baseline_sigma=None),
        )

    def actions(self):
        return (
            Action(
                name="messages.draft",
                tier=Tier.AUTO,      # writes only ATLAS's own table
                summary="Draft replies for open Instagram messages",
                run=draft_replies, dry_run=draft_dry, rollback=draft_rollback,
                params={},
                max_per_hour=12,
            ),
            Action(
                name="messages.reply",
                tier=Tier.ASK,
                summary="Send a reply on Instagram DM",
                run=reply, dry_run=reply_dry, rollback=None, irreversible=True,
                params={"inbound_id": "id of the row in messages_inbound to "
                                     "reply to",
                        "text": "exact text to send; empty sends the stored "
                               "draft"},
                max_per_hour=20,
            ),
        )

    async def signals(self):
        await _sync()
        try:
            row = await fetch_one(
                """SELECT count(*) AS n FROM messages_inbound
                   WHERE replied_at IS NULL AND draft IS NULL
                     AND platform = $1""", PLATFORM)
        except Exception:  # noqa: BLE001
            return ()
        n = int(row["n"]) if row else 0
        if not n:
            return ()
        return (Signal(
            title=f"{n} message(s) waiting on a reply",
            body="Instagram — I can draft replies for your "
                 "review; nothing sends without your yes.",
            severity=Severity.NORMAL,
            suggests="messages.draft"),)

    def brief(self) -> str:
        return "Messages: Instagram DMs waiting on a reply"

    async def inbox(self, limit: int = 10):
        return await inbox(limit)


MODULE = MessagesModule()
