"""Read-back for `messages.reply` (RA-D1). Reads only: the thread's own
message list, through the Page with the Page token -- the same read
`instagram_dm.recent` makes (verified live 2026-09-27) -- looking for the
message id the send returned, sent by the account, with the approved text.

Absence is only a contradiction when the whole thread was read (no next
page) or the page read reaches back past the run's start; otherwise it is
unknown. Never calls `_sync`, `draft_replies` or `send`. See
docs/verification.md.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    contradicted,
    reconciler,
    unknown,
    verified,
    verifier,
)
from . import PLATFORM, instagram_dm

FIELDS = "messages{id,from,message,created_time}"


def _norm(text: str) -> str:
    return " ".join(str(text or "").split()).lower()[:40]


def _when(raw: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw).replace("+0000", "+00:00"))
    except ValueError:
        return None


async def _thread(thread_id: str) -> tuple[list[dict], bool, str, str | None]:
    """(messages, whole_thread_read, our_ig_id, reason). Reads only."""
    label, _, bare = thread_id.rpartition(":")
    found = next(((p, e) for n, p, e in instagram_dm.DM_ACCOUNTS
                  if n == (label or "main")), None)
    acct = instagram_dm._account(*found) if found else None
    if acct is None:
        return [], False, "", "the thread's Instagram account is not configured"
    ig_id, user_token, page_id = acct
    token, reason = await instagram_dm._page_token(page_id, user_token)
    if token is None:
        return [], False, ig_id, reason
    data, reason = await instagram_dm.get_json(
        f"{instagram_dm.GRAPH}/{bare}", headers=instagram_dm._auth(token),
        params={"fields": FIELDS})
    if reason:
        return [], False, ig_id, reason
    box = data.get("messages") if isinstance(data, dict) else None
    rows = box.get("data") if isinstance(box, dict) else None
    if not isinstance(rows, list):
        return [], False, ig_id, "the thread answered with no message list"
    whole = not ((box.get("paging") or {}).get("next"))
    return [r for r in rows if isinstance(r, dict)], whole, ig_id, None


def _absent(what: str, rows: list[dict], whole: bool,
            started: datetime | None, **evidence: Any) -> Verification:
    oldest = min((w for w in (_when(r.get("created_time")) for r in rows) if w),
                 default=None)
    if whole or (started is not None and oldest is not None and oldest <= started):
        return contradicted(what, **evidence)
    return unknown(f"{what} in the newest page read; older pages were not read",
                   FailureClass.STALE, **evidence)


def _auth_class(reason: str) -> FailureClass:
    return (FailureClass.AUTH if "401" in reason or "403" in reason
            else FailureClass.TRANSIENT)


@verifier("messages.reply")
async def verify_reply(params: dict[str, Any], result: ActionResult) -> Verification:
    ev = result.evidence or {}
    thread_id, message_id = str(ev.get("thread_id") or ""), str(ev.get("message_id") or "")
    if ev.get("platform") != PLATFORM or not thread_id or not message_id:
        return unknown("the run recorded no Instagram thread and message id")
    rows, whole, ig_id, reason = await _thread(thread_id)
    if reason:
        return unknown(f"the thread could not be read: {reason}", _auth_class(reason))
    evidence = {"thread_id": thread_id, "message_id": message_id, "read": len(rows)}
    msg = next((r for r in rows if str(r.get("id")) == message_id), None)
    if msg is None:
        return _absent(f"message {message_id} is not in the thread", rows, whole,
                       None, **evidence)
    evidence["created_time"] = msg.get("created_time")
    if str((msg.get("from") or {}).get("id") or "") != ig_id:
        return contradicted("the message in the thread was not sent by the account",
                            **evidence)
    text = str(params.get("text") or "")
    if text.strip() and _norm(msg.get("message") or "") != _norm(text):
        return contradicted("the message in the thread carries a different text",
                            **evidence)
    return verified(**evidence)


@reconciler("messages.reply")
async def reconcile_reply(params: dict[str, Any], started: datetime) -> Verification:
    """A send with no finish: the thread, read for a message from the
    account with the approved text (or the stored draft), sent at or after
    the start. Reads `messages_inbound` with a SELECT; writes nothing."""
    from ...kernel import state
    try:
        inbound_id = int(params.get("inbound_id"))
    except (TypeError, ValueError):
        return unknown("the start row names no inbound message", FailureClass.VALIDATION)
    row = await state.fetch_one(
        "SELECT platform, thread_id, draft FROM messages_inbound WHERE id = $1",
        inbound_id)
    if row is None or row["platform"] != PLATFORM:
        return unknown(f"inbound message #{inbound_id} is not an Instagram thread")
    body = str(params.get("text") or "").strip() or str(row["draft"] or "").strip()
    if not body:
        return unknown("neither the start row nor the stored draft says what was sent")
    thread_id = str(row["thread_id"] or "")
    rows, whole, ig_id, reason = await _thread(thread_id)
    if reason:
        return unknown(f"the thread could not be read: {reason}", _auth_class(reason))
    hits = [r for r in rows
            if str((r.get("from") or {}).get("id") or "") == ig_id
            and _norm(r.get("message") or "") == _norm(body)
            and (w := _when(r.get("created_time"))) is not None and w >= started]
    evidence = {"thread_id": thread_id, "read": len(rows)}
    if len(hits) > 1:
        return unknown(f"{len(hits)} matching messages; a person should pick",
                       message_ids=[h.get("id") for h in hits], **evidence)
    if hits:
        return verified(message_id=hits[0].get("id"),
                        created_time=hits[0].get("created_time"), **evidence)
    return _absent("no reply with this text was sent after the run started", rows,
                   whole, started, **evidence)


# ------------------------------------------------------------------ messages.draft

def _sha12(text: Any) -> str:
    import hashlib
    return hashlib.sha256(str(text or "").strip().encode("utf-8")).hexdigest()[:12]


@verifier("messages.draft")
async def verify_draft(params: dict[str, Any], result: ActionResult) -> Verification:
    """The rows the run drafted, by the ids it recorded, from
    `messages_inbound` (one SELECT): each still carries a draft whose sha256
    prefix is the one the run recorded. Reads nothing else."""
    from ...kernel import state
    ev = result.evidence or {}
    try:
        ids = [int(i) for i in ev.get("ids") or []]
    except (TypeError, ValueError):
        return unknown("the run recorded unreadable row ids", FailureClass.VALIDATION)
    if not ids:
        return unknown("the run drafted nothing, so there is nothing to read back")
    hashes = {str(k): str(v) for k, v in (ev.get("draft_hashes") or {}).items()}
    rows = await state.fetch_all(
        "SELECT id, draft, replied_at FROM messages_inbound "
        "WHERE id = ANY($1::bigint[])",
        ids)
    by_id = {int(r["id"]): r for r in rows or []}
    evidence: dict[str, Any] = {"ids": ids,
                                "read": {str(i): _sha12(r["draft"]) if r["draft"]
                                         else None for i, r in by_id.items()}}
    if missing := [i for i in ids if i not in by_id]:
        return contradicted("rows not in messages_inbound: "
                            + ", ".join(map(str, missing)), **evidence)
    if empty := [i for i in ids if not str(by_id[i]["draft"] or "").strip()]:
        return contradicted("rows with no draft stored: " + ", ".join(map(str, empty)),
                            **evidence)
    if not hashes:
        return unknown("the run recorded no draft hashes to compare with", **evidence)
    if differ := [i for i in ids if hashes.get(str(i)) != evidence["read"][str(i)]]:
        return contradicted("rows whose stored draft differs from what the run "
                            "wrote: " + ", ".join(map(str, differ)), **evidence)
    return verified(**evidence)
