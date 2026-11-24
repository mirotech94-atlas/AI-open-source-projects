"""Gmail — ingestion, triage, commitment extraction, drafts.

Two design points that matter more than the code:

1. Classification runs on the LOCAL model (`Need.PRIVATE`). Personal mail never
   leaves the box. This is not a nicety — it is what makes it acceptable to
   point an agent at an inbox at all.

2. ATLAS never sends. It drafts into Gmail's own drafts folder and tells Miro
   it is there. A draft he can edit in the client he already uses beats an
   approval card he has to read in another app.

Ingestion uses `users.watch` + Pub/Sub push, so new mail arrives in seconds
rather than being polled. Google Voice notification emails are routed to the
gvoice parser on the way through.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta

import httpx

from ...channels import gvoice
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
from ...kernel.llm import LocalModelUnavailable, Message, Need, local_model, think
from ...kernel.memory import Kind, remember
from ...kernel.permissions import scan_for_injection, wrap_untrusted
from . import followups, imap_draft

log = logging.getLogger("atlas.mail")

API = "https://gmail.googleapis.com/gmail/v1/users/me"
_token: tuple[str, float] | None = None


# Cached per account. One global slot meant the second mailbox's request
# handed back the first mailbox's token, and Gmail answers that happily --
# with the first account's mail.
_tokens: dict[str, tuple[str, float]] = {}


async def _access_token(refresh_token: str | None = None,
                        cache_key: str = "default") -> str:
    import time

    refresh_token = refresh_token or os.environ["GOOGLE_REFRESH_TOKEN"]
    cached = _tokens.get(cache_key)
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post("https://oauth2.googleapis.com/token", data={
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
        r.raise_for_status()
        data = r.json()
    _tokens[cache_key] = (data["access_token"],
                          time.time() + int(data.get("expires_in", 3600)))
    return _tokens[cache_key][0]


async def _api(path: str, account=None, **params):
    tok = (await _access_token(account.refresh_token, account.name)
           if account is not None else await _access_token())
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{API}{path}",
                        headers={"Authorization": f"Bearer {tok}"}, params=params)
        r.raise_for_status()
        return r.json()


def _digest(text: str) -> str:
    """The first 12 hex of sha256 over `text` with line endings and outer
    whitespace normalised: what a draft's run records and its read-back
    (readback.py) compares, so a recipient or body is checked without
    either being written to the audit chain."""
    norm = str(text or "").replace("\r\n", "\n").strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]


def _address(header: str) -> str:
    """The bare, lowercased address of a To/From header: Gmail may hand a
    draft's `To` back quoted or encoded differently from the thread's
    `From`, and the address is what matters."""
    from email.utils import parseaddr
    return parseaddr(str(header or ""))[1].lower()


def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _body_text(payload: dict) -> str:
    if payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(
            payload["body"]["data"] + "==").decode("utf-8", "replace")
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/plain":
            return _body_text(part)
    for part in payload.get("parts", []):
        text = _body_text(part)
        if text:
            return text
    return ""


# The inbox heading for mail the local model could not read.
UNCLASSIFIED_HEADING = "unclassified — local model down"

CATEGORIES = ("needs_reply", "fyi", "bill", "client", "school", "vendor", "noise")

# inbox() used to classify every unread thread on every read -- one
# Need.PRIVATE call per thread per call, unbounded. Cached per thread id for
# 6h (mail doesn't get re-triaged that often) and capped to 12 fresh calls
# per inbox() call, so a large unread backlog costs a bounded number of LLM
# calls rather than one per thread every time the brief reads the inbox.
_CLASSIFY_CACHE: dict[str, tuple[dict, datetime]] = {}
_CLASSIFY_CACHE_TTL = timedelta(hours=6)
_CLASSIFY_CAP_PER_CALL = 12


async def classify(subject: str, sender: str, body: str) -> dict:
    """Local model. Cheap enough to run on every message.

    When the local model is down, or answers with something that is not
    JSON, the result is `{"unclassified": reason}` with no category at all
    -- never a made-up `fyi`. The mail is not read anywhere else.
    `bucket()` files it as "unclassified", the inbox lists it under
    "unclassified — local model down", and the ingest sweep raises it as
    one alert and reads it again next sweep."""
    try:
        reply = await think(
            Need.PRIVATE,
            "Classify this email for Miro. Reply with JSON only:\n"
            '{"category": one of ' + str(list(CATEGORIES)) + ', '
            '"urgency": 0-3, "commitment": "what was promised, by whom, by '
            'when" or null, "summary": one short sentence}',
            [Message("user", wrap_untrusted(
                f"From: {sender}\nSubject: {subject}\n\n{body[:6000]}",
                "gmail"))],
            max_tokens=400)
    except LocalModelUnavailable as exc:
        log.warning("mail.classify: %s", exc)
        return {"unclassified": str(exc)}
    import json
    try:
        start, end = reply.text.find("{"), reply.text.rfind("}")
        parsed = json.loads(reply.text[start:end + 1])
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        return {"unclassified": "local model reply was not JSON"}
    return parsed


async def _classify_capped(thread_id: str, subject: str, sender: str,
                           snippet: str, now: datetime,
                           budget: list[int]) -> dict:
    """`classify()`, but cached for `_CLASSIFY_CACHE_TTL` and capped to
    `_CLASSIFY_CAP_PER_CALL` fresh calls per `inbox()` call.

    `budget` is a one-element list used as a shared mutable counter across
    the loop in `inbox()` -- simpler than threading a class through, and
    this function has no other callers. A thread that misses the cache
    after the budget is spent gets `{}` -- no category at all -- rather than
    being skipped or handed a fabricated `fyi`. `bucket()` reads an
    unrecognised category as "could not tell" and falls back on `age_h`;
    returning a real category here would have been indistinguishable from a
    thread the local model actually read and called fyi, which is the same
    "a reading that could not be taken must not render as one that was"
    invariant `bucket()` itself already respects for its own fallback.

    A result `classify()` marks `unclassified` (the local model was down or
    answered with something that was not JSON) is passed through, so
    `bucket()` files it as "unclassified", and is not cached, so the next
    read tries again.
    """
    cached = _CLASSIFY_CACHE.get(thread_id)
    if cached is not None and now - cached[1] < _CLASSIFY_CACHE_TTL:
        return cached[0]
    if budget[0] <= 0:
        return {}
    budget[0] -= 1
    result = await classify(subject, sender, snippet)
    if "unclassified" not in result:     # a thread nobody read is retried
        _CLASSIFY_CACHE[thread_id] = (result, now)
    return result


# The unreplied check owns its budget. The scheduler's default 30s killed it
# from outside every time (mirotech: one list call plus up to 25 thread reads
# over Google's API on a slow day), and a kill from outside says only "timed
# out". The body gives up first, at DEADLINE_S, and names what it was doing;
# the Check's own timeout sits above it so the scheduler is the backstop, not
# the first line.
UNREPLIED_DEADLINE_S = 100.0
UNREPLIED_TIMEOUT = timedelta(seconds=120)
# Only the first 25 threads are read, so only 25 are listed; and only the two
# headers the check uses come back, not the whole thread.
UNREPLIED_LIST_MAX = 25
UNREPLIED_FETCH_PARALLEL = 10


async def check_unreplied_for(account) -> Observation:
    """Bounded by UNREPLIED_DEADLINE_S; a mailbox that does not answer in time
    reads UNKNOWN with the reason, never a number."""
    name = f"{account.name}_unreplied"
    try:
        return await asyncio.wait_for(_check_unreplied(account),
                                      timeout=UNREPLIED_DEADLINE_S)
    except TimeoutError:
        return Observation(
            name, Health.UNKNOWN,
            detail=f"{account.email or account.name}: the mailbox did not answer "
                   f"within {UNREPLIED_DEADLINE_S:.0f}s; nothing was measured")


async def _check_unreplied(account) -> Observation:
    """Threads in one mailbox where Miro was asked something and hasn't answered.

    The account's OWN address decides "he replied last". A single global
    address made every thread in every other mailbox look unanswered — a
    wrong reading that arrives looking exactly like a real finding.
    """
    name = f"{account.name}_unreplied"
    if not account.configured:
        return Observation(name, Health.UNKNOWN,
                           detail=f"{account.name} is not configured: set "
                                  f"{account.missing}")

    try:
        if account.kind == "imap":
            from .imap import unreplied as imap_unreplied
            stale, waiting, looked = await imap_unreplied(account)
        else:
            data = await _api("/threads", account=account,
                              q="is:unread in:inbox -category:promotions "
                                "-category:social newer_than:7d",
                              maxResults=UNREPLIED_LIST_MAX)
            threads = data.get("threads", [])
            looked = len(threads)
            stale = 0
            waiting = []
            # 25 sequential round-trips to Gmail ran past the scheduler's
            # 30s and the check reported UNKNOWN every time. Fetch them
            # together, a few at a time.
            gate = asyncio.Semaphore(UNREPLIED_FETCH_PARALLEL)

            async def _one(tid: str) -> dict:
                async with gate:
                    return await _api(
                        f"/threads/{tid}", account=account, format="metadata",
                        metadataHeaders=["From", "Subject", "List-Unsubscribe",
                                         "List-Id", "Auto-Submitted", "Precedence"],
                        fields="messages(internalDate,payload/headers)")
            fulls = await asyncio.gather(
                *(_one(t["id"]) for t in threads[:UNREPLIED_LIST_MAX]))
            for full in fulls:
                msgs = full.get("messages", [])
                if not msgs:
                    continue
                last = msgs[-1]
                frm = _header(last, "From")
                from .accounts import automated, bulk_headers
                if (account.is_me(frm) or automated(frm)
                        or bulk_headers(last.get("payload", {}).get("headers", []))):
                    continue                 # he replied last, or a machine wrote it
                ts = datetime.fromtimestamp(
                    int(last.get("internalDate", 0)) / 1000, UTC)
                if datetime.now(UTC) - ts <= timedelta(hours=48):
                    continue         # only threads over 48h count: skip the model
                from . import spam
                if (await spam.check(spam.item_from_gmail(last), account)
                        ).verdict in (spam.SPAM, spam.UNSURE):
                    continue                 # spam is not waiting on him
                stale += 1
                waiting.append(_header(last, "Subject")[:80])
    except Exception as exc:  # noqa: BLE001
        from .._http import redact
        return Observation(name, Health.UNKNOWN,
                           detail=f"{account.email or account.name}: "
                                  f"{redact(exc)}")

    return Observation(
        name,
        Health.DEGRADED if stale > 3 else Health.OK,
        float(stale), "threads",
        f"{account.email}: {stale} thread(s) waiting on you for more than "
        f"48h (of {looked} unread)",
        evidence={"subjects": waiting[:10], "account": account.email})


async def check_unreplied() -> Observation:
    """Kept for the single-account path and anything still calling it."""
    from .accounts import all_accounts

    accounts = all_accounts()
    if not accounts:
        return Observation("unreplied", Health.UNKNOWN,
                           detail="no mailboxes configured; set MAIL_ACCOUNTS")
    return await check_unreplied_for(accounts[0])


# A made-up message, so the check reads nobody's mail to prove the route.
_PROBE = ("Invoice 4471 due Friday", "billing@example.com",
          "Hi Miro, invoice 4471 for October is attached and due on Friday.")


async def check_classify_local() -> Observation:
    """Can PRIVATE classification run at all? One synthetic email through
    `classify()`, i.e. `Need.PRIVATE` on the local model. When the local
    model is absent or down, `think()` refuses rather than borrowing a cloud
    API, and this reads UNKNOWN with the reason -- never OK."""
    result = await classify(*_PROBE)
    model = local_model()
    if "unclassified" in result or not result.get("category"):
        reason = str(result.get("unclassified") or "no category in the answer")
        return Observation("classify_local", Health.UNKNOWN,
                           detail=f"PRIVATE classification did not run: "
                                  f"{reason[:200]}")
    return Observation("classify_local", Health.OK,
                       detail=f"PRIVATE classification ran on the local model "
                              f"{model}: test email filed as "
                              f"{str(result.get('category'))[:20]}")


async def check_watch_active() -> Observation:
    """Is the Gmail API answering with a real mailbox profile?

    There is no push watch to lapse: ingestion is polled (`mail.ingest`), so
    this proves only that the API answers for this mailbox. A reply with no
    mailbox fields in it is not that proof and reads UNKNOWN, never "OK, 0
    messages"."""
    try:
        data = await _api("/profile")
        if not isinstance(data, dict) or (
                "messagesTotal" not in data and "emailAddress" not in data):
            return Observation(
                "watch_active", Health.UNKNOWN,
                detail="Gmail answered without a mailbox profile "
                       "(no address, no message count); reachability is "
                       "not shown")
        total = data.get("messagesTotal")
        return Observation(
            "watch_active", Health.OK,
            float(total) if isinstance(total, (int, float)) else None,
            "messages" if isinstance(total, (int, float)) else None,
            "Gmail API answers for this mailbox (mail is polled, not pushed)")
    except Exception as exc:  # noqa: BLE001
        return Observation("watch_active", Health.FAILING,
                           detail=f"Gmail unreachable — re-auth may be needed: {exc}")


async def ingest_message(message_id: str) -> dict:
    """Called by the Pub/Sub webhook. Classify, extract, remember, route."""
    msg = await _api(f"/messages/{message_id}", format="full")
    subject = _header(msg, "Subject")
    sender = _header(msg, "From")
    body = _body_text(msg.get("payload", {}))
    at = datetime.fromtimestamp(int(msg.get("internalDate", 0)) / 1000, UTC)

    if flag := scan_for_injection(f"{subject}\n{body}", f"gmail:{sender}"):
        from ...kernel.audit import audit
        await audit("injection.suspected", "gmail", {"detail": flag, "from": sender})

    if gvoice.is_google_voice(sender):
        event = gvoice.parse(subject, body, at)
        if event:
            await remember(Kind.EPISODIC, "conversations",
                           f"Google Voice {event.kind} from {event.counterpart}: "
                           f"{event.text[:300]}", importance=0.5)
            return {"routed": "google_voice", "kind": event.kind}

    # Every ingested message gets a spam verdict before a model reads it for
    # triage. Spam and unsure mail yields no commitments and no memories.
    from . import spam
    flag = await spam.check(spam.item_from_gmail(msg, full=True))
    if flag.verdict in (spam.SPAM, spam.UNSURE):
        return {"spam_verdict": flag.verdict, "reason": flag.reason}

    result = await classify(subject, sender, body)
    if result.get("commitment"):
        await remember(Kind.SEMANTIC, "commitments",
                       f"{result['commitment']} (from: {sender}, "
                       f"subject: {subject[:80]})", importance=0.9)
    await remember(Kind.EPISODIC, "threads",
                   f"{sender} — {subject}: {result.get('summary','')}",
                   meta={"category": result.get("category")}, importance=0.4)
    return result


async def draft_reply(thread_id: str, body: str) -> ActionResult:
    """Write into Gmail drafts, where he can edit and send from his own client.

    `imap:<account>:<Message-ID>` is an IMAP-read mailbox: the draft is an
    IMAP APPEND to its Drafts folder (imap_draft.py), no OAuth token."""
    if body is not None and str(thread_id).startswith(imap_draft.PREFIX):
        return await imap_draft.draft(thread_id, body)
    tok = await _access_token()
    thread = await _api(f"/threads/{thread_id}", format="metadata")
    last = thread["messages"][-1]
    to = _header(last, "From")
    subject = _header(last, "Subject")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    raw = base64.urlsafe_b64encode(
        f"To: {to}\r\nSubject: {subject}\r\n"
        f"In-Reply-To: {_header(last, 'Message-ID')}\r\n\r\n{body}".encode()
    ).decode()

    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{API}/drafts",
                         headers={"Authorization": f"Bearer {tok}"},
                         json={"message": {"raw": raw, "threadId": thread_id}})
        r.raise_for_status()
        draft = r.json()
    return ActionResult(True, f"Draft saved in Gmail for {to} — open it to "
                              f"review and send.",
                        evidence={"draft_id": draft["id"], "thread_id": thread_id,
                                  "to_sha256": _digest(_address(to)),
                                  "subject_sha256": _digest(subject),
                                  "body_sha256": _digest(body)},
                        rollback_token=draft["id"])


async def draft_dry(thread_id: str, body: str) -> ActionResult:
    if len(body.strip()) < 10:
        return ActionResult(False, "draft body is empty")
    if str(thread_id).startswith(imap_draft.PREFIX):
        login, _name, _orig, why = imap_draft.check_params(thread_id)
        if login is None:
            return ActionResult(False, why)
        return ActionResult(True, f"would append a {len(body)}-character draft to "
                                  f"{login.email} Drafts for {thread_id[:60]}")
    return ActionResult(True, f"would save a {len(body)}-character draft on "
                              f"thread {thread_id}")


async def delete_draft(draft_id: str) -> ActionResult:
    if str(draft_id).startswith(imap_draft.PREFIX):
        return await imap_draft.delete(draft_id)
    tok = await _access_token()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.delete(f"{API}/drafts/{draft_id}",
                           headers={"Authorization": f"Bearer {tok}"})
    return ActionResult(r.status_code < 300, f"deleted draft {draft_id}")


def _split_ids(thread_ids: str) -> list[str]:
    return [t.strip() for t in thread_ids.replace(",", " ").split() if t.strip()]


# Gmail's own labels. Adding one is not filing: TRASH is deletion after
# thirty days, SPAM hides the mail and trains Google's filter, and INBOX,
# UNREAD, STARRED and IMPORTANT change what he sees. `threads.modify`
# accepts all of them, so the refusal has to be here. Only his own (user)
# labels are Tier AUTO.
SYSTEM_LABELS = frozenset({"INBOX", "SPAM", "TRASH", "UNREAD", "STARRED",
                           "IMPORTANT", "SENT", "DRAFT", "CHAT"})


def system_label(name: str) -> bool:
    """True for a Gmail system label name, however it is spelled or spaced
    ("Trash", "spam", "category social")."""
    key = "_".join(str(name or "").strip().upper().split())
    return key in SYSTEM_LABELS or key.startswith("CATEGORY_")


def _refused(name: str) -> str:
    return (f"{name!r} is a Gmail system label -- Trash, Spam, Inbox and the "
            f"like are not labels ATLAS applies")


async def _resolve_label(name: str) -> tuple[str | None, str | None, bool]:
    """(label id, refusal, created). Resolves a name to its Gmail id via
    `GET /labels`, creating it if it does not exist yet -- his own mailbox,
    so a label he asked for by name is a reason to create it, not to fail.
    Refuses any system label, by name before anything is read and by
    `type == "system"` once the list is."""
    if system_label(name):
        return None, _refused(name), False
    tok = await _access_token()
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{API}/labels", headers={"Authorization": f"Bearer {tok}"})
        r.raise_for_status()
        labels = r.json().get("labels", [])
    for lbl in labels:
        if lbl.get("name", "").lower() == name.lower():
            if lbl.get("type") == "system":
                return None, _refused(name), False
            return lbl["id"], None, False
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(f"{API}/labels",
                         headers={"Authorization": f"Bearer {tok}"},
                         json={"name": name, "labelListVisibility": "labelShow",
                               "messageListVisibility": "show"})
    if r.status_code >= 400:
        return None, f"could not create label {name!r}: HTTP {r.status_code}", False
    return r.json()["id"], None, True


async def _label_id(name: str) -> tuple[str | None, str | None]:
    """`_resolve_label` without the created flag."""
    label_id, reason, _created = await _resolve_label(name)
    return label_id, reason


async def apply_labels(thread_ids: str, label: str) -> ActionResult:
    """Add one of his own labels to one or more threads. Reversible: the
    rollback token carries the label's resolved id, exactly the thread ids
    this call actually labelled, and whether this call created the label,
    so `remove_labels` undoes only what happened, even on a partial
    success. A Gmail system label is refused."""
    ids = _split_ids(thread_ids)
    if not ids:
        return ActionResult(False, "no thread ids given")

    label_id, reason, created = await _resolve_label(label)
    if reason is not None:
        return ActionResult(False, reason)

    tok = await _access_token()
    applied = []
    async with httpx.AsyncClient(timeout=30) as c:
        for tid in ids:
            r = await c.post(f"{API}/threads/{tid}/modify",
                             headers={"Authorization": f"Bearer {tok}"},
                             json={"addLabelIds": [label_id]})
            if r.status_code < 300:
                applied.append(tid)

    if not applied:
        return ActionResult(False, f"failed to label any of {len(ids)} thread(s)")
    return ActionResult(
        True, f"labelled {len(applied)}/{len(ids)} thread(s) {label!r}",
        evidence={"threads": applied, "label_id": label_id, "created": created},
        rollback_token=f"{label_id}:{','.join(applied)}"
                       + (":new" if created else ""))


async def apply_labels_dry(thread_ids: str, label: str) -> ActionResult:
    ids = _split_ids(thread_ids)
    if not ids:
        return ActionResult(False, "no thread ids given")
    if not label.strip():
        return ActionResult(False, "label name is empty")
    if system_label(label):
        return ActionResult(False, _refused(label))
    return ActionResult(True, f"would apply label {label!r} to {len(ids)} "
                              f"thread(s)")


async def remove_labels(token: str) -> ActionResult:
    """Rollback: removes exactly the labels `apply_labels` added, on
    exactly the threads it actually applied to -- no more, no less. That is
    what decides success. A label that call created is then deleted too,
    but only once no thread carries it; if he has used it since, it is kept
    and the detail says so -- a note, not a failure, since his mail is back
    as it was."""
    label_id, _, rest = token.partition(":")
    ids_part, _, flag = rest.partition(":")
    if not label_id or not ids_part:
        return ActionResult(False, f"malformed rollback token: {token!r}")
    ids = [t for t in ids_part.split(",") if t]

    tok = await _access_token()
    removed = []
    async with httpx.AsyncClient(timeout=30) as c:
        for tid in ids:
            r = await c.post(f"{API}/threads/{tid}/modify",
                             headers={"Authorization": f"Bearer {tok}"},
                             json={"removeLabelIds": [label_id]})
            if r.status_code < 300:
                removed.append(tid)
    detail = f"removed the label from {len(removed)}/{len(ids)} thread(s)"
    ok = len(removed) == len(ids)
    if flag == "new" and ok:
        detail += "; " + await _drop_created_label(label_id, tok)
    return ActionResult(ok, detail)


async def _drop_created_label(label_id: str, tok: str) -> str:
    """Delete a label `apply_labels` created, only when it is empty. What
    happened, as a note. A label carries no mail, so this deletes nothing of
    his; an unreadable count keeps it rather than guessing.

    The count is read and the delete sent back to back on one connection.
    Gmail offers no conditional delete, so a thread labelled in between
    would lose only that label -- never mail."""
    async with httpx.AsyncClient(timeout=20) as c:
        auth = {"Authorization": f"Bearer {tok}"}
        r = await c.get(f"{API}/labels/{label_id}", headers=auth)
        total = r.json().get("threadsTotal") if r.status_code < 300 else None
        if total is None:
            return f"kept label {label_id}: could not read how many threads use it"
        if int(total) > 0:
            return f"kept label {label_id}: in use by {total} thread(s)"
        r = await c.delete(f"{API}/labels/{label_id}", headers=auth)
    if r.status_code >= 300:
        return f"kept label {label_id}: delete refused, HTTP {r.status_code}"
    return f"deleted label {label_id}, which this run had created"


def _label_map(mapping: object) -> dict[str, list[str]]:
    """`{label: [thread ids]}` from a JSON object (or a dict), ids as a list
    or a comma/space string. Empty labels and empty groups are dropped;
    anything unreadable is an empty mapping, which both runs refuse."""
    if isinstance(mapping, str):
        try:
            mapping = json.loads(mapping or "{}")
        except ValueError:
            return {}
    if not isinstance(mapping, dict):
        return {}
    out: dict[str, list[str]] = {}
    for label, ids in mapping.items():
        name = str(label).strip()
        if isinstance(ids, str):
            found = _split_ids(ids)
        elif isinstance(ids, list):
            found = [str(i).strip() for i in ids if str(i).strip()]
        else:
            found = []
        if name and found:
            out[name] = found
    return out


async def apply_label_map(mapping: str) -> ActionResult:
    """Several labels in one action: `mapping` is `{label: [thread ids]}`.
    One card for "label my inbox" rather than one per category. Each group
    goes through `apply_labels`, and the rollback token joins theirs, so
    `remove_label_map` undoes exactly what landed and nothing else."""
    groups = _label_map(mapping)
    if not groups:
        return ActionResult(False, "no labels to apply")
    if refused := [label for label in groups if system_label(label)]:
        return ActionResult(False, "; ".join(_refused(label) for label in refused))
    tokens: list[str] = []
    landed: dict[str, list[str]] = {}
    label_ids: dict[str, str] = {}
    said: list[str] = []
    for label, ids in groups.items():
        try:
            result = await apply_labels(",".join(ids), label)
        except Exception as exc:  # noqa: BLE001
            # A later group failing must not lose the earlier groups' undo:
            # what landed is returned with its token below.
            from .._http import explain
            said.append(f"{label!r} failed: {explain(exc)}")
            continue
        said.append(result.detail)
        if result.ok and result.rollback_token:
            tokens.append(result.rollback_token)
            landed[label] = list((result.evidence or {}).get("threads", ids))
            if (result.evidence or {}).get("label_id"):
                label_ids[label] = str(result.evidence["label_id"])
    if not tokens:
        return ActionResult(False, "; ".join(said))
    count = sum(len(v) for v in landed.values())
    return ActionResult(
        True, f"labelled {count} thread(s) under {len(landed)} label(s): "
              + "; ".join(said),
        evidence={"labels": landed, "label_ids": label_ids},
        rollback_token="|".join(tokens))


async def apply_label_map_dry(mapping: str) -> ActionResult:
    groups = _label_map(mapping)
    if not groups:
        return ActionResult(False, "no labels to apply")
    if refused := [label for label in groups if system_label(label)]:
        return ActionResult(False, "; ".join(_refused(label) for label in refused))
    count = sum(len(v) for v in groups.values())
    return ActionResult(True, f"would apply {len(groups)} label(s) to {count} "
                              f"thread(s)")


async def remove_label_map(token: str) -> ActionResult:
    """Rollback for `apply_label_map`: each `apply_labels` token in turn."""
    parts = [part for part in (token or "").split("|") if part]
    if not parts:
        return ActionResult(False, f"malformed rollback token: {token!r}")
    results = [await remove_labels(part) for part in parts]
    return ActionResult(all(r.ok for r in results),
                        "; ".join(r.detail for r in results))


async def suggest_labels() -> list[dict]:
    """Read-only: the classifier's own category for every unread thread, so
    Miro can decide what to label before ATLAS touches Gmail at all. Uses
    the same `classify()` mail already runs for triage -- a second,
    disagreeing classifier here would be a second answer to "what is this
    email", which this repo consistently rejects."""
    data = await _api("/threads",
                      q="is:unread -category:promotions -category:social "
                        "newer_than:7d",
                      maxResults=20)
    out: list[dict] = []
    for t in data.get("threads", []):
        full = await _api(f"/threads/{t['id']}", format="metadata")
        msgs = full.get("messages", [])
        if not msgs:
            continue
        last = msgs[-1]
        subject = _header(last, "Subject")
        sender = _header(last, "From")
        from . import spam
        if (await spam.check(spam.item_from_gmail(
                last, snippet=t.get("snippet", "")))).verdict in (
                spam.SPAM, spam.UNSURE):
            continue                 # no label suggested for suspected spam
        result = await classify(subject, sender, t.get("snippet", ""))
        if not result.get("category"):
            continue                 # unread by the model: no suggestion
        out.append({"thread_id": t["id"], "subject": subject[:80],
                   "sender": sender[:80], "category": result["category"]})
    return out


async def _nudge_text(thread_id: str) -> tuple[str | None, str | None]:
    """The nudge body `mail.draft_nudge` would save, or (None, an error) if
    nothing was ever recorded for this thread, the thread has since been
    resolved, or it was already nudged within the cooldown. Shared by the
    run and the dry run so both show exactly the same text -- a dry run that
    composed something different would not be telling him what would
    actually happen, and checking every refusal here (rather than only in
    `draft_nudge`) means the dry run refuses the same way instead of
    previewing a draft that would never be allowed to happen.

    `max_per_hour` on the action caps how often it fires at all; it does not
    stop it firing twice on the SAME thread. Two proposals both naming the
    oldest open thread -- e.g. Miro approving several days of pending cards
    at once -- would otherwise draft two nudges on one thread with nothing
    to stop it. Checking `nudged_at` here is that stop.

    The `resolved_at` check exists because a Signal's proposal can sit
    unapproved for up to 24h (the Tier 2 TTL) before Miro acts on it, and
    `check_followups` runs on its own two-hour schedule the whole time. If
    the other side replies during that window, the thread is resolved before
    the stale proposal is approved -- and without this check, approving it
    would still draft "just checking in" on something already answered.
    """
    row = await followups.followup_row(thread_id)
    if row is None:
        return None, f"no follow-up recorded for thread {thread_id}"
    if row["resolved_at"] is not None:
        # A proposal from signals() can sit unapproved for up to 24h (the
        # Tier 2 TTL). check_followups may resolve the thread in that
        # window -- someone answered -- and approving the stale proposal
        # afterwards would draft "just checking in" on a thread that no
        # longer needs one.
        return None, (
            f"they replied on {row['resolved_at']:%Y-%m-%d}; no nudge needed")
    if row["nudged_at"] is not None:
        days = followups.business_days_since(row["nudged_at"])
        if days < followups.DEFAULT_MIN_DAYS:
            return None, (
                f"already nudged {row['nudged_at']:%Y-%m-%d} ({days} "
                f"business day(s) ago) -- refusing a second nudge inside "
                f"the {followups.DEFAULT_MIN_DAYS}-business-day cooldown")
    asked = row["asked"] or row["subject"] or "what I asked before"
    try:
        reply = await think(
            Need.PRIVATE,
            "Write ONE short, friendly follow-up sentence in Miro's voice, "
            "checking in on something he already asked and has not heard "
            "back on. Do not invent new facts and do not ask a new question. "
            "Reply with the nudge text only -- no quotes, no subject line.",
            [Message("user", wrap_untrusted(
                f"He originally asked: {asked}", "mail_followups"))],
            max_tokens=200)
    except LocalModelUnavailable as exc:
        return None, str(exc)
    return reply.text.strip(), None


async def draft_nudge(thread_id: str) -> ActionResult:
    """Compose a short check-in from the original ask and save it as a Gmail
    draft. `draft_reply` is the only thing here that touches Gmail -- this
    action's own job is the compose step and recording `nudged_at` so
    `open_followups` stops offering the thread during the cooldown.

    The rollback token carries the thread id alongside the Gmail draft id:
    `draft_reply`'s own token is just the draft id, and `rollback_nudge`
    needs the thread id too, to clear `nudged_at` when it undoes this.
    """
    if str(thread_id).startswith(imap_draft.PREFIX):
        return ActionResult(False, "follow-ups are tracked from the Gmail API "
                                   "only; no nudge for an IMAP-read mailbox")
    body, error = await _nudge_text(thread_id)
    if error:
        return ActionResult(False, error)
    result = await draft_reply(thread_id, body)
    if not result.ok:
        return result
    await followups.mark_nudged(thread_id, datetime.now(UTC))
    return ActionResult(True, result.detail, evidence=dict(result.evidence or {}),
                        rollback_token=f"{thread_id}:{result.rollback_token}")


async def draft_nudge_dry(thread_id: str) -> ActionResult:
    """Shows the nudge text without saving anything -- no Gmail draft, no
    `nudged_at` write."""
    body, error = await _nudge_text(thread_id)
    if error:
        return ActionResult(False, error)
    return ActionResult(True, f"would draft on thread {thread_id}: {body}")


async def rollback_nudge(token: str) -> ActionResult:
    """Deletes the Gmail draft `draft_nudge` created and clears `nudged_at`,
    so the thread is immediately eligible for `open_followups` again rather
    than staying hidden for a nudge that no longer exists."""
    thread_id, _, draft_id = token.partition(":")
    if not thread_id or not draft_id:
        # A malformed token would otherwise call delete_draft("") -- Gmail's
        # DELETE /drafts/ with no id -- rather than failing loudly here.
        return ActionResult(False, f"malformed rollback token: {token!r}")
    result = await delete_draft(draft_id)
    if result.ok:
        await followups.clear_nudge(thread_id)
    return result


class MailModule:
    name = "mail"
    title = "Mail"
    # Not gated. Four mailboxes across two providers: one arriving should
    # light up that one, and the rest report UNKNOWN naming their own
    # variables -- the same shape as Stripe's two accounts.
    requires_env = ()
    optional_env = ("MAIL_ACCOUNTS",)

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        from .accounts import all_accounts

        def bind(account):
            async def _run() -> Observation:
                return await check_unreplied_for(account)
            return _run

        out = [Check("watch_active", check_watch_active, timedelta(hours=6),
                     Severity.HIGH, "Gmail reachable and ingesting",
                     baseline_sigma=None),
               Check("classify_local", check_classify_local, timedelta(hours=6),
                     Severity.NORMAL,
                     "personal mail is classified on the local model",
                     baseline_sigma=None),
               Check("followups", followups.check_followups, timedelta(hours=2),
                     Severity.NORMAL,
                     "follow-ups he sent and never heard back on",
                     baseline_sigma=None)]
        out += [
            Check(f"{a.name}_unreplied", bind(a), timedelta(hours=2),
                  Severity.NORMAL,
                  f"{a.email or a.name}: threads waiting on a reply",
                  baseline_sigma=None, timeout=UNREPLIED_TIMEOUT)
            for a in all_accounts()
        ]
        return tuple(out)

    def actions(self):
        return (
            Action(
                name="mail.draft_reply",
                tier=Tier.AUTO,     # a draft is reversible and nobody sees it
                summary="Save a reply into Gmail drafts for Miro to review",
                run=draft_reply, dry_run=draft_dry, rollback=delete_draft,
                params={"thread_id": "Gmail thread id",
                        "body": "the reply text, in Miro's voice"},
                max_per_hour=20,
            ),
            Action(
                name="mail.draft_nudge",
                tier=Tier.AUTO,     # a draft is reversible and nobody sees it
                summary="Draft a check-in nudge for a follow-up with no reply",
                run=draft_nudge, dry_run=draft_nudge_dry, rollback=rollback_nudge,
                params={"thread_id": "Gmail thread id of the open follow-up"},
                max_per_hour=6,
            ),
            Action(
                name="mail.apply_labels",
                tier=Tier.AUTO,     # his own mailbox; a label is reversible
                summary="Apply a Gmail label to one or more threads",
                run=apply_labels, dry_run=apply_labels_dry, rollback=remove_labels,
                params={"thread_ids": "comma- or space-separated Gmail thread ids",
                        "label": "label name; created if it does not exist"},
                max_per_hour=20,
            ),
            Action(
                name="mail.apply_label_map",
                tier=Tier.AUTO,     # his own mailbox; every label is reversible
                summary="Apply several Gmail labels at once, each to its threads",
                run=apply_label_map, dry_run=apply_label_map_dry,
                rollback=remove_label_map,
                params={"mapping": 'JSON object {"label name": ["thread id", ...]}'},
                max_per_hour=6,
            ),
        )

    async def signals(self):
        # New unread mail he has not seen, one signal per one that needs
        # him, into the channel for that mailbox. This is the part that
        # makes "if I don't see it, ATLAS does" true: counting unread was
        # never reading it.
        from .ingest import sweep

        out = list(await sweep())

        obs = await check_unreplied()
        subjects = obs.evidence.get("subjects", [])
        if len(subjects) > 3:
            out.append(Signal(
                title=f"{len(subjects)} emails waiting on you",
                body="\n".join(f"- {s}" for s in subjects[:6]) +
                     "\n\nI can draft replies for any of these.",
                severity=Severity.NORMAL))

        # `raise_signals` (scheduler.py) routes every signal that names an
        # allowed action through `propose()`, and `propose()` is tier-blind:
        # it always inserts a pending proposal and notifies with
        # Approve/Decline buttons, regardless of whether the action is Tier
        # AUTO. One Signal per open follow-up, raised every scheduler cycle,
        # would be a fresh approval card every few minutes for the same
        # threads -- and even capped to one per thread per day, that is
        # still one card per open thread per day. So this raises at most ONE
        # Signal for the whole group, once a day: `claim_daily_signal` is a
        # module-level date guard, not a dedup_key, because dedup_key keys
        # on the cause and there would be a different cause (thread) each
        # time -- exactly the shape that produces a flood of distinct keys
        # instead of one repeated one.
        open_items = await followups.open_followups()
        if open_items and followups.claim_daily_signal():
            oldest = min(open_items, key=lambda i: i["sent_at"])
            out.append(Signal(
                title=f"{len(open_items)} emails you sent have had no "
                      f"reply for 3+ business days",
                body=(f'Oldest: "{oldest["subject"]}" to '
                      f'{oldest["to_address"]}. I can draft a nudge.'),
                severity=Severity.NORMAL,
                suggests="mail.draft_nudge",
                params={"thread_id": oldest["thread_id"]}))

        # The day's "Reply today" card: counts and a list, no action. Read
        # in the background; handed over on a later cycle.
        from . import reply_today
        try:
            await reply_today.maybe_start()
        except Exception:
            log.exception("reply-today card did not start; retried next cycle")
        out.extend(reply_today.take_signals())

        # A spam verdict for every recent message that has none (read-only,
        # once a day, in the background; writes only ATLAS's own table).
        from . import spam
        try:
            await spam.maybe_backfill()
        except Exception:
            log.exception("spam backfill did not start; retried next cycle")

        # The travel organiser: a background sweep of booking emails (local
        # model, slow), then check-in reminder proposals and passport flags
        # from what earlier sweeps stored. No mailbox, nothing to read.
        from .accounts import all_accounts
        if all_accounts():
            from . import alinea, travel
            travel.maybe_sweep()
            # Alinea's emails, read-only: its own background sweep.
            alinea.maybe_sweep()
            try:
                out.extend(await travel.signals())
            except Exception:
                log.exception("travel signals failed; retried next cycle")
            # The money-leak finder: once a month, in the background; its
            # one plain Signal is handed over on a later cycle.
            from . import money
            try:
                await money.maybe_run()
            except Exception:
                log.exception("money-leak finder did not start; retried next cycle")
            out.extend(money.take_signals())
        return tuple(out)

    async def inbox(self, limit: int = 10):
        """Threads waiting on a reply, bucketed by the classifier's own
        reading, plus follow-ups he sent that nobody has answered.

        The bucket — not raw age — decides `why` and whether an item is
        urgent: `reply_today` is the only bucket that reaches Severity.HIGH.
        A thread we cannot read contributes nothing rather than a
        placeholder — check_unreplied reports UNKNOWN separately, so a
        broken mailbox is visible as a failed check, not as a quiet inbox.
        """
        data = await _api("/threads",
                          q="is:unread -category:promotions -category:social "
                            "newer_than:7d",
                          maxResults=min(50, limit * 4))
        me = os.getenv("GOOGLE_ACCOUNT_EMAIL", "")
        threads = data.get("threads", [])
        snippets = {t["id"]: t.get("snippet", "") for t in threads}
        items: list[InboxItem] = []
        now = datetime.now(UTC)
        budget = [_CLASSIFY_CAP_PER_CALL]
        for thread in threads[:limit * 3]:
            full = await _api(f"/threads/{thread['id']}", format="metadata")
            msgs = full.get("messages", [])
            if not msgs:
                continue
            last = msgs[-1]
            sender = _header(last, "From")
            if me and me in sender:
                continue                     # he replied last; nothing owed
            at = datetime.fromtimestamp(
                int(last.get("internalDate", 0)) / 1000, UTC)
            age_h = (now - at).total_seconds() / 3600
            if age_h > 168:
                continue
            subject = _header(last, "Subject") or "(no subject)"
            from . import spam
            flag = await spam.check(spam.item_from_gmail(
                last, snippet=snippets.get(thread["id"], "")))
            if flag.verdict in (spam.SPAM, spam.UNSURE):
                # Shown, flagged, with the reason; never urgent, never
                # classified or offered a draft. unsure is not "ok".
                items.append(InboxItem(
                    at=at,
                    sender=sender.split("<")[0].strip().strip('"')[:40] or sender[:40],
                    subject=subject[:80], urgency=Severity.INFO,
                    why=flag.line()[:160], reference=thread["id"]))
                continue
            classification = await _classify_capped(
                thread["id"], subject, sender, snippets.get(thread["id"], ""),
                now, budget)
            why = followups.bucket(classification, age_h)
            if why == "unclassified":
                why = UNCLASSIFIED_HEADING
            if flag.verdict == spam.UNKNOWN:
                why = f"spam check UNKNOWN; {why}"
            urgency = (Severity.HIGH if why == "reply_today"
                       else Severity.INFO if why == "noise"
                       else Severity.NORMAL)
            items.append(InboxItem(
                at=at,
                sender=sender.split("<")[0].strip().strip('"')[:40] or sender[:40],
                subject=subject[:80],
                urgency=urgency, why=why, reference=thread["id"]))

        for f in await followups.open_followups():
            # A thread that already carries a nudge must say so, not repeat
            # "no reply" as if nothing had happened -- an InboxItem must
            # never claim a draft exists when it does not, and must never
            # go quiet about one that does.
            if f["nudged_at"] is not None:
                why = f"nudge drafted {f['nudged_at']:%Y-%m-%d}"
            else:
                n = followups.business_days_since(f["sent_at"])
                why = f"no reply for {n} business days"
            items.append(InboxItem(
                at=f["sent_at"],
                sender=(f["to_address"] or "")[:40],
                subject=(f["subject"] or "(no subject)")[:80],
                urgency=Severity.NORMAL,
                why=why,
                reference=f["thread_id"]))

        items.sort(key=lambda i: (-int(i.urgency), i.at))
        return tuple(items[:limit])

    def brief(self) -> str:
        from . import alinea, money, reply_today, travel
        extra = "; ".join(filter(None, (reply_today.brief_line(),
                                        followups.brief_line(),
                                        travel.brief_line(),
                                        money.brief_line(),
                                        alinea.brief_line())))
        base = "Mail: unreplied threads, commitments"
        return f"{base}. {extra}" if extra else base


MODULE = MailModule()
