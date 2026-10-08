"""Read-back for `instagram.publish`, `instagram.hide_comment` and
`instagram.reply_comment` (RA-D1). Reads only, through the module's own
`_get` and the account's own token: the media by the id the run recorded;
the comment's own `hidden` flag; the comment's replies, for the reply id the
run recorded. See docs/verification.md."""
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
from . import GraphError, NoAccount, _cfg, _get

FIELDS = "id,permalink,media_type,media_product_type,timestamp,caption"


def _norm(text: str) -> str:
    return " ".join(str(text or "").split()).lower()


def _prefix(caption: str) -> str:
    return _norm(caption)[:40]


def missing(exc: GraphError) -> bool:
    """Graph says an object does not exist with a 400 (code 100), not a 404."""
    text = str(exc).lower()
    return exc.status_code == 404 or (
        exc.status_code == 400 and ("does not exist" in text
                                    or "unsupported get request" in text))


def graph_failure(exc: Exception) -> Verification:
    if isinstance(exc, NoAccount):
        return unknown(str(exc), FailureClass.DISABLED)
    status = getattr(exc, "status_code", None)
    if status in (401, 403, 190):
        return unknown(str(exc), FailureClass.AUTH)
    return unknown(str(exc), FailureClass.TRANSIENT)


async def read_media(account: str, media_id: str, *, caption: str = "",
                     kind: str = "") -> Verification:
    """The shared read: exists, right kind, right caption. Used by the reel
    verifier for its Instagram leg too."""
    try:
        media = await _get(media_id, account, fields=FIELDS)
    except GraphError as exc:
        if missing(exc):
            return contradicted(f"media {media_id} does not exist on {account}",
                                media_id=media_id, account=account)
        return graph_failure(exc)
    except NoAccount as exc:
        return graph_failure(exc)
    evidence = {"media_id": media.get("id"), "account": account,
                "permalink": media.get("permalink"),
                "media_type": media.get("media_type"),
                "timestamp": media.get("timestamp")}
    if str(media.get("id") or "") != str(media_id):
        return contradicted("Graph answered for a different media id", **evidence)
    if kind == "reel" and str(media.get("media_type") or "").upper() != "VIDEO" \
            and str(media.get("media_product_type") or "").upper() != "REELS":
        return contradicted(f"media {media_id} is not a reel", **evidence)
    if caption and _prefix(media.get("caption") or "") != _prefix(caption):
        return contradicted(f"media {media_id} carries a different caption",
                            **evidence)
    if not media.get("permalink"):
        return unknown(f"media {media_id} exists but has no permalink yet",
                       FailureClass.STALE, **evidence)
    return verified(**evidence)


@verifier("instagram.publish")
async def verify_publish(params: dict[str, Any], result: ActionResult
                         ) -> Verification:
    account, _, media_id = (result.rollback_token or "").partition(":")
    if not account or not media_id:
        return unknown("the run recorded no media id")
    if account != str(params.get("account") or "main"):
        return contradicted("published to a different account than approved",
                            account=account, approved=params.get("account"))
    return await read_media(account, media_id, caption=str(params.get("caption") or ""),
                            kind=str(params.get("kind") or ""))


async def find_recent(account: str, caption: str, started: datetime
                      ) -> Verification:
    """A publish with no recorded id: the account's recent media, matched by
    caption, published at or after the run started. Reads only."""
    try:
        ig_id, _ = _cfg(account)
        listed = await _get(f"{ig_id}/media", account, fields=FIELDS, limit=25)
    except (GraphError, NoAccount) as exc:
        return graph_failure(exc)
    want = _prefix(caption)
    hits = []
    for item in (listed.get("data") or []) if isinstance(listed, dict) else []:
        try:
            when = datetime.fromisoformat(
                str(item.get("timestamp") or "").replace("+0000", "+00:00"))
        except ValueError:
            continue
        if when >= started and want and _prefix(item.get("caption") or "") == want:
            hits.append(item)
    if not hits:
        return contradicted(f"nothing with this caption was published on {account} "
                            "after the run started", account=account)
    if len(hits) > 1:
        return unknown(f"{len(hits)} matching posts; a person should pick",
                       media_ids=[h.get("id") for h in hits])
    item = hits[0]
    return verified(media_id=item.get("id"), account=account,
                    permalink=item.get("permalink"), timestamp=item.get("timestamp"))


@reconciler("instagram.publish")
async def reconcile_publish(params: dict[str, Any], started: datetime) -> Verification:
    return await find_recent(str(params.get("account") or "main"),
                             str(params.get("caption") or ""), started)


# ------------------------------------------------------------ comments (RA-D1)

async def _comment_hidden(account: str, comment_id: str) -> Verification:
    """The comment's own `hidden` flag. Hide and its reconciler read the
    same thing: the state is the effect, whoever set it."""
    try:
        got = await _get(comment_id, account, fields="id,hidden,timestamp")
    except GraphError as exc:
        if missing(exc):
            return contradicted(f"comment {comment_id} no longer exists on {account}",
                                comment_id=comment_id, account=account)
        return graph_failure(exc)
    except NoAccount as exc:
        return graph_failure(exc)
    evidence = {"comment_id": got.get("id"), "account": account,
                "hidden": got.get("hidden")}
    if str(got.get("id") or "") != str(comment_id):
        return contradicted("Graph answered for a different comment", **evidence)
    if got.get("hidden") is True:
        return verified(**evidence)
    if got.get("hidden") is False:
        return contradicted(f"comment {comment_id} is not hidden", **evidence)
    return unknown("Graph did not say whether the comment is hidden", **evidence)


def _hide_target(params: dict[str, Any], token: str | None) -> tuple[str, str]:
    account, _, comment_id = (token or "").partition(":")
    return (account or str(params.get("account") or ""),
            comment_id or str(params.get("comment_id") or ""))


@verifier("instagram.hide_comment")
async def verify_hide(params: dict[str, Any], result: ActionResult) -> Verification:
    account, comment_id = _hide_target(params, result.rollback_token)
    if not account or not comment_id:
        return unknown("the run recorded no account and comment id")
    if params.get("comment_id") and str(params["comment_id"]) != comment_id:
        return contradicted("the run hid a different comment than approved",
                            comment_id=comment_id)
    return await _comment_hidden(account, comment_id)


@reconciler("instagram.hide_comment")
async def reconcile_hide(params: dict[str, Any], started: datetime) -> Verification:
    account, comment_id = _hide_target(params, None)
    if not account or not comment_id:
        return unknown("the start row names no account and comment id",
                       FailureClass.VALIDATION)
    return await _comment_hidden(account, comment_id)


REPLY_FIELDS = "id,text,timestamp"


def _stamp(raw: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(raw or "").replace("+0000", "+00:00"))
    except ValueError:
        return None


async def _replies(account: str, comment_id: str
                   ) -> tuple[list[dict[str, Any]], bool] | Verification:
    """The comment's replies, and whether that was the whole list."""
    try:
        got = await _get(f"{comment_id}/replies", account, fields=REPLY_FIELDS,
                         limit=50)
    except GraphError as exc:
        if missing(exc):
            return contradicted(f"comment {comment_id} no longer exists on {account}",
                                comment_id=comment_id, account=account)
        return graph_failure(exc)
    except NoAccount as exc:
        return graph_failure(exc)
    rows = got.get("data") if isinstance(got, dict) else None
    if not isinstance(rows, list):
        return unknown("Graph answered the replies with no data list",
                       comment_id=comment_id)
    whole = not ((got.get("paging") or {}).get("next"))
    return [r for r in rows if isinstance(r, dict)], whole


@verifier("instagram.reply_comment")
async def verify_reply(params: dict[str, Any], result: ActionResult) -> Verification:
    reply_id = str((result.evidence or {}).get("reply_id") or "")
    account = str(params.get("account") or "")
    comment_id = str(params.get("comment_id") or "")
    if not reply_id or not account or not comment_id:
        return unknown("the run recorded no reply id")
    listed = await _replies(account, comment_id)
    if isinstance(listed, Verification):
        return listed
    rows, whole = listed
    evidence = {"reply_id": reply_id, "comment_id": comment_id, "account": account}
    hit = next((r for r in rows if str(r.get("id")) == reply_id), None)
    if hit is None:
        if whole:
            return contradicted(f"reply {reply_id} is not under comment {comment_id}",
                                **evidence)
        return unknown(f"reply {reply_id} is not on the first page of replies",
                       FailureClass.STALE, **evidence)
    evidence["timestamp"] = hit.get("timestamp")
    if _prefix(hit.get("text") or "") != _prefix(str(params.get("text") or "")):
        return contradicted(f"reply {reply_id} carries a different text", **evidence)
    return verified(**evidence)


@reconciler("instagram.reply_comment")
async def reconcile_reply(params: dict[str, Any], started: datetime) -> Verification:
    account = str(params.get("account") or "")
    comment_id = str(params.get("comment_id") or "")
    want = _prefix(str(params.get("text") or ""))
    if not account or not comment_id or not want:
        return unknown("the start row names no account, comment and text",
                       FailureClass.VALIDATION)
    listed = await _replies(account, comment_id)
    if isinstance(listed, Verification):
        return listed
    rows, whole = listed
    hits = [r for r in rows if _prefix(r.get("text") or "") == want
            and (w := _stamp(r.get("timestamp"))) is not None and w >= started]
    if len(hits) > 1:
        return unknown(f"{len(hits)} matching replies; a person should pick",
                       reply_ids=[h.get("id") for h in hits])
    if hits:
        return verified(reply_id=hits[0].get("id"), comment_id=comment_id,
                        account=account, timestamp=hits[0].get("timestamp"))
    oldest = min((w for w in (_stamp(r.get("timestamp")) for r in rows) if w),
                 default=None)
    if whole or (oldest is not None and oldest <= started):
        return contradicted(f"no reply with this text was posted under comment "
                            f"{comment_id} after the run started",
                            comment_id=comment_id, account=account)
    return unknown("the first page of replies does not reach back to the run's start",
                   FailureClass.STALE, comment_id=comment_id)


@verifier("instagram.funnel_reply")
async def verify_funnel_reply(params: dict[str, Any], result: ActionResult
                              ) -> Verification:
    """The public reply the run recorded is under the comment (read
    `/{comment}/replies`), and the private reply's message id was recorded.
    A private message cannot be read back through Graph, so its id from the
    send response is the evidence for that leg; the public reply is the
    leg that is read back from the provider."""
    evidence = result.evidence or {}
    account = str(params.get("account") or "main")
    comment_id = str(params.get("comment_id") or "")
    reply_id = str(evidence.get("public_reply_id") or "")
    message_id = str(evidence.get("private_message_id") or "")
    if evidence.get("deduplicated"):
        return unknown("already sent on an earlier run; nothing new to read back")
    if not comment_id or not reply_id:
        return unknown("the run recorded no public reply id")
    try:
        got = await _get(f"{comment_id}/replies", account, fields="id,text")
    except GraphError as exc:
        if missing(exc):
            return contradicted(f"comment {comment_id} no longer exists on {account}",
                                comment_id=comment_id)
        return graph_failure(exc)
    except NoAccount as exc:
        return graph_failure(exc)
    listed = got.get("data") if isinstance(got, dict) else None
    if not isinstance(listed, list):
        return unknown("Graph /replies carried no list")
    ids = {str(r.get("id")) for r in listed if isinstance(r, dict)}
    seen = {"comment_id": comment_id, "public_reply_id": reply_id,
            "private_message_id": message_id or None, "account": account}
    if reply_id == "sent" or reply_id not in ids:
        if reply_id == "sent" and listed:
            return unknown("the public reply id was not recorded; replies exist "
                           "under the comment", **seen)
        return contradicted(f"no reply {reply_id} under comment {comment_id}", **seen)
    if not message_id:
        return contradicted("the public reply is there but no private reply id was "
                            "recorded", **seen)
    return verified(**seen)
