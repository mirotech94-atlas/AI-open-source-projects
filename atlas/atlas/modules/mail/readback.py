"""Read-back for the Gmail writes (RA-D1). Reads only -- GETs on the Gmail
API with the module's own token helper (`_access_token`, which tests fake):

  * `mail.apply_labels`: the label by the id the run recorded (its name is
    the approved one), and every approved thread by id, carrying that label;
  * `mail.apply_label_map`: the same, once per approved label, combined;
  * `mail.draft_reply`: the draft by id -- on the approved thread, with the
    recipient, subject and body the run recorded (sha256 prefixes, so no
    address or text reaches the audit chain);
  * `mail.draft_nudge`: the same draft check (the body was composed by the
    model, so its hash comes from the run), plus `mail_followups.nudged_at`
    by a SELECT.

Mail is "off" when no Google refresh token is configured: unknown, class
disabled, before any network call. No reconcilers: none of these params
carry the id the effect was stored under. See docs/verification.md.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    classify,
    combine,
    contradicted,
    unknown,
    verified,
    verifier,
)

OFF = "mail is not configured (no GOOGLE_REFRESH_TOKEN), so nothing was read"


def _off() -> Verification | None:
    if not os.getenv("GOOGLE_REFRESH_TOKEN"):
        return unknown(OFF, FailureClass.DISABLED)
    return None


async def _get(path: str, **params: Any) -> Any:
    """One Gmail GET. The token comes from the module's helper, looked up at
    call time so a test's fake is the one used."""
    from . import API, _access_token
    tok = await _access_token()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{API}{path}", headers={"Authorization": f"Bearer {tok}"},
                        params=params)
    r.raise_for_status()
    return r.json()


def _status(exc: httpx.HTTPStatusError) -> int:
    return exc.response.status_code


# ------------------------------------------------------------------ labels

async def _check_label(label: str, label_id: str, thread_ids: list[str]
                       ) -> Verification:
    """The label by id carries the approved name, and each approved thread
    carries the label on at least one message."""
    evidence: dict[str, Any] = {"label_id": label_id, "threads": thread_ids}
    try:
        found = await _get(f"/labels/{label_id}")
    except httpx.HTTPStatusError as exc:
        if _status(exc) == 404:
            return contradicted(f"label {label_id} is not in the mailbox", **evidence)
        return unknown(f"could not read label {label_id}", classify(exc), **evidence)
    name = str((found or {}).get("name") or "")
    evidence["label"] = name
    if name.lower() != label.strip().lower():
        return contradicted(f"label {label_id} is named {name!r}, approved {label!r}",
                            **evidence)
    missing, gone = [], []
    for tid in thread_ids:
        try:
            thread = await _get(f"/threads/{tid}", format="minimal")
        except httpx.HTTPStatusError as exc:
            if _status(exc) == 404:
                gone.append(tid)
                continue
            return unknown(f"could not read thread {tid}", classify(exc), **evidence)
        carried = {lid for m in (thread or {}).get("messages") or []
                   for lid in m.get("labelIds") or []}
        if label_id not in carried:
            missing.append(tid)
    if gone:
        return contradicted("threads not in the mailbox: " + ", ".join(gone),
                            **evidence)
    if missing:
        return contradicted(f"threads without label {name!r}: " + ", ".join(missing),
                            missing=missing, **evidence)
    return verified(**evidence)


def _token_legs(token: str | None) -> list[tuple[str, list[str]]]:
    """(label id, thread ids) per `apply_labels` rollback token."""
    out = []
    for part in (token or "").split("|"):
        label_id, _, rest = part.partition(":")
        ids = [t for t in rest.partition(":")[0].split(",") if t]
        if label_id and ids:
            out.append((label_id, ids))
    return out


@verifier("mail.apply_labels")
async def verify_apply_labels(params: dict[str, Any], result: ActionResult
                              ) -> Verification:
    from . import _split_ids
    legs = _token_legs(result.rollback_token)
    label_id = str((result.evidence or {}).get("label_id")
                   or (legs[0][0] if legs else ""))
    if not label_id:
        return unknown("the run recorded no label id")
    approved = _split_ids(str(params.get("thread_ids") or ""))
    if not approved:
        return unknown("no thread ids were approved", FailureClass.VALIDATION)
    if (off := _off()) is not None:
        return off
    return await _check_label(str(params.get("label") or ""), label_id, approved)


@verifier("mail.apply_label_map")
async def verify_apply_label_map(params: dict[str, Any], result: ActionResult
                                 ) -> Verification:
    from . import _label_map
    groups = _label_map(params.get("mapping"))
    if not groups:
        return unknown("no labels were approved", FailureClass.VALIDATION)
    ev = result.evidence or {}
    label_ids = dict(ev.get("label_ids") or {})
    if not label_ids:
        # An older run recorded no ids: its tokens are in the landed order.
        landed = list((ev.get("labels") or {}).keys())
        label_ids = {name: lid for name, (lid, _ids)
                     in zip(landed, _token_legs(result.rollback_token), strict=False)}
    if not label_ids:
        return unknown("the run recorded no label ids")
    if (off := _off()) is not None:
        return off
    legs: dict[str, Verification] = {}
    for label, ids in groups.items():
        if not (lid := label_ids.get(label)):
            legs[label] = contradicted("this label was not applied by the run",
                                       threads=ids)
            continue
        legs[label] = await _check_label(label, str(lid), ids)
    return combine(legs)


# ------------------------------------------------------------------ drafts

async def _check_draft(draft_id: str, thread_id: str, ev: dict[str, Any],
                       approved_body: str | None) -> Verification:
    """The draft by id: on the approved thread, and its To, Subject and body
    hash to what the run recorded (and the body to the approved text when
    the params carry it)."""
    from . import _address, _body_text, _digest, _header
    evidence: dict[str, Any] = {"draft_id": draft_id, "thread_id": thread_id}
    try:
        draft = await _get(f"/drafts/{draft_id}", format="full")
    except httpx.HTTPStatusError as exc:
        if _status(exc) == 404:
            return contradicted(f"draft {draft_id} is not in Gmail drafts", **evidence)
        return unknown(f"could not read draft {draft_id}", classify(exc), **evidence)
    msg = (draft or {}).get("message") or {}
    got = {"to_sha256": _digest(_address(_header(msg, "To"))),
           "subject_sha256": _digest(_header(msg, "Subject")),
           "body_sha256": _digest(_body_text(msg.get("payload") or {}))}
    evidence.update(got, message_id=msg.get("id"), read_thread_id=msg.get("threadId"))
    if str(msg.get("threadId") or "") != thread_id:
        return contradicted("the draft sits on a different thread", **evidence)
    want_body = _digest(approved_body) if approved_body is not None else ev.get(
        "body_sha256")
    if not want_body:
        return unknown("the run recorded no body hash to compare the draft with",
                       **evidence)
    if got["body_sha256"] != want_body:
        return contradicted("the draft carries a different body", **evidence)
    for key, what in (("to_sha256", "recipient"), ("subject_sha256", "subject")):
        if ev.get(key) and ev[key] != got[key]:
            return contradicted(f"the draft carries a different {what}", **evidence)
    return verified(**evidence)


@verifier("mail.draft_reply")
async def verify_draft_reply(params: dict[str, Any], result: ActionResult
                             ) -> Verification:
    ev = result.evidence or {}
    draft_id = str(ev.get("draft_id") or result.rollback_token or "")
    if not draft_id:
        return unknown("the run recorded no draft id")
    thread_id = str(params.get("thread_id") or "")
    if not thread_id:
        return unknown("no thread id was approved", FailureClass.VALIDATION)
    if (off := _off()) is not None:
        return off
    body = str(params.get("body") or "")
    return await _check_draft(draft_id, thread_id, ev, body if body.strip() else None)


async def _nudged(thread_id: str) -> Verification:
    from ...kernel import state
    row = await state.fetch_one(
        "SELECT nudged_at FROM mail_followups WHERE thread_id = $1", thread_id)
    if row is None:
        return contradicted("no follow-up is recorded for the thread",
                            thread_id=thread_id)
    at = row["nudged_at"]
    if at is None:
        return contradicted("the follow-up does not record the nudge",
                            thread_id=thread_id)
    return verified(thread_id=thread_id, nudged_at=str(at))


@verifier("mail.draft_nudge")
async def verify_draft_nudge(params: dict[str, Any], result: ActionResult
                             ) -> Verification:
    thread_id, _, draft_id = str(result.rollback_token or "").partition(":")
    ev = result.evidence or {}
    draft_id = draft_id or str(ev.get("draft_id") or "")
    thread_id = thread_id or str(params.get("thread_id") or "")
    if not draft_id or not thread_id:
        return unknown("the run recorded no thread and draft id")
    if params.get("thread_id") and str(params["thread_id"]) != thread_id:
        return contradicted("the run drafted on a different thread than approved",
                            FailureClass.POLICY, thread_id=thread_id)
    if (off := _off()) is not None:
        return off
    return combine({"draft": await _check_draft(draft_id, thread_id, ev, None),
                    "followup": await _nudged(thread_id)})
