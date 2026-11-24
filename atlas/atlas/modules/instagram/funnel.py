"""The comment-keyword -> DM funnel (Miro, 2026-09-30, from a reel: post a
talking-head clip, viewers comment a keyword, they get a DM with the free
roadmap, the roadmap leads to the course).

What it is, in ATLAS's terms:

* **A funnel is a row** (`ig_funnels`): account, keyword, media scope, the
  public reply, the private reply (the DM, with the link), a daily cap, a
  timezone. It exists only once Miro approved it on an
  `instagram.funnel_set` card (Tier 2, card-only) that shows the EXACT
  texts. No model writes either text; `text_hash` (sha256 of both, as
  approved) is checked again before every send, and a mismatch refuses.
* **The sender** (scheduler job `funnels`, every 5 minutes) reads
  new comments on the in-scope media since a per-media watermark, matches
  the keyword as a whole word, skips the account's own comments and anyone
  already recorded for this funnel (`ig_funnel_sends` is UNIQUE on
  funnel + commenter), and queues one send per commenter. Each send is
  `instagram.funnel_reply` (Tier 1) through `executor.execute`, params
  `{account, funnel_id, comment_id}`: the texts come from the approved
  row, never from a parameter. It replies publicly under the comment
  (`reply_comment`, the existing path -- the public reply never carries the
  link) and then sends the DM as a **private reply to the comment**:

      POST https://graph.facebook.com/v21.0/{ig-user-id}/messages
      {"recipient": {"comment_id": "<comment id>"},
       "message": {"text": "<the approved DM text>"}}

  Meta, "Private Replies" (developers.facebook.com/documentation/
  instagram-platform/private-replies, read 2026-09-30): Facebook-Login
  apps post to `graph.facebook.com/<API_VERSION>/<APP_USERS_IG_ID>/messages`
  with a bearer token carrying `instagram_basic`, `instagram_manage_comments`
  and `pages_read_engagement`; the reply is allowed "within 7 days of the
  creation time of the comment" and "only one message can be sent to the
  commenter". The older Messenger-Platform page for the same feature posts
  to `/{PAGE_ID}/messages` with a Page token; if Graph refuses the IG-id
  path, that is the shape to try, by hand, before anything else changes.
  Nothing here ever sends an ordinary DM: a person who did not comment the
  keyword is never written to (the private reply has no other recipient
  than the comment's author).
* **Idempotency**: one `effect_claims` row per leg (`funnel:<id>:comment:
  <id>:public` / `:private`), claimed before the call, settled after, and
  released only when the provider certainly refused; the send row records
  each leg's id so a retry never repeats a leg that went through.
* **Limits**: the funnel's `daily_cap` (default 200 DMs a day per account,
  counted on the account's local day) is ATLAS's own number. Meta's own
  messaging limits are published under "Rate limiting" in the Graph API
  docs and change; this module asserts none it did not read (invariant 6)
  -- Miro sets the cap under whatever that page says today. The executor's
  ceilings still apply (`max_per_hour`, `ATLAS_MODULE_MAX_PER_HOUR`,
  default 10 an hour for the whole instagram module), so the practical
  rate is the smallest of them. A 429, or Graph code 4/17/32/613, backs the
  send off (`FailureClass.TRANSIENT`, the outbox's own backoff curve); a
  401/403 or code 190 pauses that credential (`kernel.pause.
  block_capability`, scope `instagram:<account>`); a policy or block error
  (code 368, 10, 200-299) stops the funnel and tells Miro.
* **Quiet hours**: no DM between 22:00 and 08:00 in the funnel's timezone
  (America/New_York by default). Comments are still read; the sends wait.

This package only reads and queues (`sweep`) and offers the action
callables; the sending loop that calls `executor.execute` is
`kernel/funnels.py`, the scheduler job. The model-facing catalog never sees
`funnel_set` (card-only) and cannot fill in a DM text.
"""
from __future__ import annotations

import hashlib
import logging
import re
import shlex
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from ...kernel import idempotency
from ...kernel.contracts import ActionResult, FailureClass, Health, Observation, Severity
from ...kernel.state import execute_sql, fetch_all, fetch_one
from . import (
    ACCOUNTS,
    GRAPH,
    GraphError,
    NoAccount,
    _cfg,
    _get,
    _raise_for,
    _unreachable,
    reply_comment,
)

log = logging.getLogger("atlas.instagram.funnel")

SET = "instagram.funnel_set"
PAUSE = "instagram.funnel_pause"
REPLY = "instagram.funnel_reply"
TOPIC = "instagram"

DEFAULT_CAP = 200
MAX_CAP = 1000
DEFAULT_TZ = "America/New_York"
QUIET_FROM, QUIET_TO = 22, 8              # local hours: no DMs 22:00-08:00
PRIVATE_REPLY_WINDOW = timedelta(days=7)  # Meta: within 7 days of the comment
RECENT_MEDIA = 10                         # "all recent media" = the last 10
PAGE_LIMIT = 50
MAX_PAGES = 5
PUBLIC_MAX = 300
DM_MAX = 1000                             # Instagram's text message limit
MAX_ATTEMPTS = 8
BACKOFF_BASE_S = 60.0
BACKOFF_CAP_S = 3600.0

_KEYWORD = re.compile(r"^[A-Za-z0-9_\-]{2,30}$")
# A scheme, a www., or anything shaped like host.tld -- the public reply
# never carries a link, whatever the TLD. "e.g." is refused too; the vet
# says why, and a public reply has no need for it.
_LINK = re.compile(r"https?://|www\.|\b[a-z0-9-]+\.[a-z]{2,10}(?![a-z])",
                   re.IGNORECASE)
_DIGITS = re.compile(r"^\d{1,40}$")

# Graph error codes, by what they mean for a send.
RATE_CODES = frozenset({4, 17, 32, 613})
AUTH_CODES = frozenset({190})
POLICY_CODES = frozenset({10, 368}) | frozenset(range(200, 300))

PARAMS_SET = {
    "account": "'main', 'stories' or 'clips'",
    "keyword": "the word a viewer comments (letters/digits, 2-30 chars); matched "
               "as a whole word, any case",
    "public_reply": "the short public reply under the comment; never a link",
    "dm_text": "the private reply (DM) text, with the link; sent exactly as "
               "approved, once per person",
    "media_id": "one Graph media id to watch, or '' for the account's last "
                f"{RECENT_MEDIA} posts (default '')",
    "daily_cap": f"DMs a day on this account, 1-{MAX_CAP} (default {DEFAULT_CAP})",
    "timezone": f"IANA zone for the quiet hours (default {DEFAULT_TZ})",
}
PARAMS_PAUSE = {"funnel_id": "ig_funnels.id"}
PARAMS_REPLY = {
    "account": "'main', 'stories' or 'clips' -- the funnel's account",
    "funnel_id": "ig_funnels.id (its approved texts are what is sent)",
    "comment_id": "the Graph comment id that matched the keyword",
}


def _clock() -> datetime:
    """Now, UTC. A seam so a test can hold the day still (the cap and the
    quiet hours are about the funnel's local day)."""
    return datetime.now(UTC)


# --------------------------------------------------------------- pure parts

def keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Whole word, any case: 'asset' matches 'ASSET', 'asset!', 'asset🔥'
    and 'the asset please', not 'assets' or 'reasset'."""
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(keyword)}(?![A-Za-z0-9_])",
                      re.IGNORECASE)


def matches(keyword: str, text: str) -> bool:
    return bool(keyword_pattern(keyword).search(text or ""))


def text_hash(public_reply: str, dm_text: str) -> str:
    raw = f"{public_reply}\x1f{dm_text}".encode()
    return hashlib.sha256(raw).hexdigest()


def has_link(text: str) -> bool:
    return bool(_LINK.search(text or ""))


def zone(name: str) -> ZoneInfo | None:
    try:
        return ZoneInfo((name or "").strip() or DEFAULT_TZ)
    except (ZoneInfoNotFoundError, ValueError):
        return None


def in_quiet_hours(local: datetime) -> bool:
    return local.hour >= QUIET_FROM or local.hour < QUIET_TO


def next_open(local: datetime) -> datetime:
    """The next 08:00 local at or after `local`."""
    day = local.date() if local.hour < QUIET_TO else local.date() + timedelta(days=1)
    return datetime.combine(day, time(QUIET_TO), tzinfo=local.tzinfo)


def local_now(tz: str, now: datetime | None = None) -> datetime:
    return (now or _clock()).astimezone(zone(tz) or ZoneInfo(DEFAULT_TZ))


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(BACKOFF_CAP_S,
                                 BACKOFF_BASE_S * (2 ** max(0, attempts - 1))))


def scope_words(media_id: str) -> str:
    return (f"media {media_id}" if media_id
            else f"all recent media (the last {RECENT_MEDIA} posts)")


def _cap(value: Any) -> int | None:
    try:
        cap = int(value)
    except (TypeError, ValueError):
        return None
    return cap if 1 <= cap <= MAX_CAP else None


def vet_set(params: dict[str, Any]) -> str | None:
    """`instagram.funnel_set`'s `Action.vet`: refuses before a card exists.
    Pure -- no network, no database."""
    account = str(params.get("account") or "")
    if account not in ACCOUNTS:
        return f"no such Instagram account {account!r}; known: {', '.join(ACCOUNTS)}"
    keyword = str(params.get("keyword") or "").strip()
    if not _KEYWORD.match(keyword):
        return "the keyword must be 2-30 letters, digits, '_' or '-'"
    public = str(params.get("public_reply") or "").strip()
    if not public:
        return "the public reply is empty"
    if len(public) > PUBLIC_MAX:
        return f"the public reply is over {PUBLIC_MAX} characters"
    if has_link(public):
        return "the public reply must not carry a link; the link goes in the DM only"
    dm = str(params.get("dm_text") or "").strip()
    if not dm:
        return "the DM text is empty"
    if len(dm) > DM_MAX:
        return f"the DM text is over Instagram's {DM_MAX} characters"
    media = str(params.get("media_id") or "").strip()
    if media and not _DIGITS.match(media):
        return f"media_id {media[:40]!r} is not a Graph media id"
    if "daily_cap" in params and params["daily_cap"] not in (None, "") \
            and _cap(params["daily_cap"]) is None:
        return f"daily_cap must be 1-{MAX_CAP}"
    tz = str(params.get("timezone") or "").strip()
    if tz and zone(tz) is None:
        return f"{tz!r} is not an IANA timezone"
    return None


def card_detail_set(params: dict[str, Any]) -> str:
    """The exact words the `instagram.funnel_set` card carries: the texts
    as they will be sent, the keyword, the account, the scope, the caps."""
    account = str(params.get("account") or "")
    keyword = str(params.get("keyword") or "").strip().lower()
    cap = _cap(params.get("daily_cap")) or DEFAULT_CAP
    tz = str(params.get("timezone") or "").strip() or DEFAULT_TZ
    scope = scope_words(str(params.get("media_id") or "").strip())
    return "\n".join([
        (f"Funnel on the {account} account: a comment with the keyword "
         f"“{keyword}” (whole word, any case) on {scope}."),
        f"Public reply, posted under the comment:\n“{params.get('public_reply')}”",
        f"Private reply (DM), sent once per person:\n“{params.get('dm_text')}”",
        (f"Caps: {cap} DMs a day on this account; none 22:00–08:00 {tz} (queued); "
         f"one DM per person per funnel, ever; only people who comment the keyword; "
         f"the public reply never carries the link."),
    ])


# ------------------------------------------------------------------ reads

async def get_funnel(funnel_id: int) -> dict[str, Any] | None:
    row = await fetch_one("SELECT * FROM ig_funnels WHERE id = $1", int(funnel_id))
    return dict(row) if row is not None else None


async def list_funnels(account: str | None = None) -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT * FROM ig_funnels
            WHERE ($1::text IS NULL OR account = $1) ORDER BY id""", account)
    return [dict(r) for r in rows]


async def active_funnels() -> list[dict[str, Any]]:
    rows = await fetch_all(
        """SELECT * FROM ig_funnels
            WHERE active AND approved_at IS NOT NULL AND stopped_at IS NULL
            ORDER BY id""")
    return [dict(r) for r in rows]


def _day_bounds(tz: str, now: datetime | None = None) -> tuple[datetime, datetime]:
    local = local_now(tz, now)
    start = datetime.combine(local.date(), time(0), tzinfo=local.tzinfo)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


async def sent_today(account: str, tz: str, now: datetime | None = None) -> int:
    start, end = _day_bounds(tz, now)
    row = await fetch_one(
        """SELECT count(*) AS n FROM ig_funnel_sends s
             JOIN ig_funnels f ON f.id = s.funnel_id
            WHERE f.account = $1 AND s.status = 'sent'
              AND s.sent_at >= $2::timestamptz AND s.sent_at < $3::timestamptz""",
        account, start, end)
    return int(row["n"]) if row else 0


def _hash_ok(funnel: dict[str, Any]) -> bool:
    return text_hash(str(funnel["public_reply"]), str(funnel["dm_text"])) == \
        str(funnel["text_hash"])


def _funnel_refusal(funnel: dict[str, Any] | None, funnel_id: Any,
                    account: str) -> str | None:
    if funnel is None:
        return f"no funnel #{funnel_id}"
    if str(funnel["account"]) != account:
        return f"funnel #{funnel_id} is on the {funnel['account']} account, not {account}"
    if funnel["approved_at"] is None:
        return f"funnel #{funnel_id} was never approved"
    if funnel["stopped_at"] is not None:
        return f"funnel #{funnel_id} is stopped: {funnel['stopped_why']}"
    if not funnel["active"]:
        return f"funnel #{funnel_id} is paused"
    if not _hash_ok(funnel):
        return (f"funnel #{funnel_id}'s texts no longer match what was approved "
                f"(hash mismatch); nothing is sent until it is set again")
    return None


# ------------------------------------------------------------ funnel_set

async def funnel_set_dry(account: str, keyword: str, public_reply: str, dm_text: str,
                         media_id: str = "", daily_cap: Any = DEFAULT_CAP,
                         timezone: str = DEFAULT_TZ) -> ActionResult:
    why = vet_set(locals())
    if why:
        return ActionResult(False, why, failure_class=FailureClass.VALIDATION)
    try:
        _cfg(account)
    except NoAccount as exc:
        return ActionResult(False, str(exc), failure_class=FailureClass.DISABLED)
    return ActionResult(
        True, f"would set the {account} funnel for “{keyword.strip().lower()}"
              f"” on {scope_words(media_id.strip())} and start it; sends "
              f"nothing now")


async def funnel_set(account: str, keyword: str, public_reply: str, dm_text: str,
                     media_id: str = "", daily_cap: Any = DEFAULT_CAP,
                     timezone: str = DEFAULT_TZ) -> ActionResult:
    """Write (or rewrite) the funnel row exactly as the card showed it, and
    start it. The texts are stored whole with their hash; nothing is sent
    here."""
    why = vet_set(locals())
    if why:
        return ActionResult(False, f"refused: {why}",
                            failure_class=FailureClass.VALIDATION)
    keyword = keyword.strip().lower()
    public_reply, dm_text = public_reply.strip(), dm_text.strip()
    media_id = (media_id or "").strip()
    cap = _cap(daily_cap) or DEFAULT_CAP
    tz = (timezone or "").strip() or DEFAULT_TZ
    digest = text_hash(public_reply, dm_text)
    row = await fetch_one(
        """INSERT INTO ig_funnels
             (account, keyword, media_id, public_reply, dm_text, text_hash, active,
              daily_cap, timezone, created_by, approved_at)
           VALUES ($1, $2, $3, $4, $5, $6, TRUE, $7, $8, $9, $10)
           ON CONFLICT (account, keyword, media_id) DO UPDATE
              SET public_reply = EXCLUDED.public_reply, dm_text = EXCLUDED.dm_text,
                  text_hash = EXCLUDED.text_hash, active = TRUE,
                  daily_cap = EXCLUDED.daily_cap, timezone = EXCLUDED.timezone,
                  approved_at = EXCLUDED.approved_at, stopped_at = NULL,
                  stopped_why = '', last_error = '', last_error_at = NULL,
                  updated_at = now()
           RETURNING id""",
        # The module's own clock, the one the sweep reads: a comment counts
        # only after the approval, and both must be told by the same clock.
        account, keyword, media_id, public_reply, dm_text, digest, cap, tz, "card",
        _clock())
    fid = int(row["id"])
    return ActionResult(
        True, f"funnel #{fid} set on {account}: “{keyword}” on "
              f"{scope_words(media_id)}, {cap}/day, quiet 22:00–08:00 {tz}; "
              f"it starts with the next sweep",
        evidence={"funnel_id": fid, "text_hash": digest, "account": account},
        rollback_token=str(fid))


async def funnel_set_rollback(token: str) -> ActionResult:
    """Undo a set = pause the funnel. The row stays (its sends are history)."""
    return await funnel_pause(token)


# ---------------------------------------------------------- funnel_pause

async def funnel_pause_dry(funnel_id: Any) -> ActionResult:
    try:
        fid = int(funnel_id)
    except (TypeError, ValueError):
        return ActionResult(False, f"not a funnel id: {funnel_id!r}",
                            failure_class=FailureClass.VALIDATION)
    funnel = await get_funnel(fid)
    if funnel is None:
        return ActionResult(False, f"no funnel #{fid}",
                            failure_class=FailureClass.VALIDATION)
    if not funnel["active"]:
        return ActionResult(True, f"funnel #{fid} is already paused")
    return ActionResult(True, f"would pause funnel #{fid} ({funnel['account']}, "
                              f"“{funnel['keyword']}”)")


async def funnel_pause(funnel_id: Any) -> ActionResult:
    try:
        fid = int(funnel_id)
    except (TypeError, ValueError):
        return ActionResult(False, f"not a funnel id: {funnel_id!r}",
                            failure_class=FailureClass.VALIDATION)
    row = await fetch_one(
        """UPDATE ig_funnels SET active = FALSE, updated_at = now()
            WHERE id = $1 RETURNING id, account, keyword""", fid)
    if row is None:
        return ActionResult(False, f"no funnel #{fid}",
                            failure_class=FailureClass.VALIDATION)
    return ActionResult(True, f"funnel #{fid} paused ({row['account']}, "
                              f"“{row['keyword']}”); queued sends wait",
                        evidence={"funnel_id": fid}, rollback_token=str(fid))


async def funnel_resume(token: str) -> ActionResult:
    """Rollback of a pause: back to what was approved -- only a funnel that
    was approved and is not stopped by a policy error."""
    try:
        fid = int(token)
    except (TypeError, ValueError):
        return ActionResult(False, f"not a funnel id: {token!r}")
    row = await fetch_one(
        """UPDATE ig_funnels SET active = TRUE, updated_at = now()
            WHERE id = $1 AND approved_at IS NOT NULL AND stopped_at IS NULL
        RETURNING id""", fid)
    if row is None:
        return ActionResult(False, f"funnel #{fid} cannot be resumed (never "
                                   f"approved, stopped, or missing)")
    return ActionResult(True, f"funnel #{fid} resumed")


# ------------------------------------------------------------ the send

def classify_graph(exc: GraphError) -> FailureClass | None:
    """What a Graph refusal means for the send. None: unclassified -- the
    cautious path (a person looks)."""
    code = exc.code
    if exc.status_code in (401, 403) or code in AUTH_CODES:
        return FailureClass.AUTH
    if exc.status_code == 429 or code in RATE_CODES:
        return FailureClass.TRANSIENT
    if code in POLICY_CODES:
        return FailureClass.POLICY
    if exc.status_code is None or exc.status_code >= 500:
        return FailureClass.TRANSIENT
    if exc.status_code in (400, 422):
        return FailureClass.VALIDATION
    return None


def _certainly_not_sent(exc: GraphError) -> bool:
    """A 4xx is a refusal; no status (unreachable, timed out) or a 5xx may
    have gone through."""
    return exc.status_code is not None and 400 <= exc.status_code < 500


def private_reply_payload(comment_id: str, text: str) -> dict[str, Any]:
    """Meta's private-reply body, exactly (see the module docstring)."""
    return {"recipient": {"comment_id": str(comment_id)}, "message": {"text": text}}


async def _private_reply(account: str, comment_id: str, text: str) -> dict[str, Any]:
    """POST /{ig-user-id}/messages with the JSON body above. Raises
    GraphError (secret-free) on refusal."""
    ig_id, token = _cfg(account)
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{GRAPH}/{ig_id}/messages",
                             json=private_reply_payload(comment_id, text),
                             headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise _unreachable(exc, token) from None
    _raise_for(r, token)
    data = r.json()
    return data if isinstance(data, dict) else {}


async def _send_row(funnel_id: int, comment_id: str) -> dict[str, Any] | None:
    row = await fetch_one(
        "SELECT * FROM ig_funnel_sends WHERE funnel_id = $1 AND comment_id = $2",
        int(funnel_id), str(comment_id))
    return dict(row) if row is not None else None


async def _mark(send_id: int, status: str, error: str = "", **cols: Any) -> None:
    sets = ["status = $2", "error = $3"]
    args: list[Any] = [int(send_id), status, error[:500]]
    for name, value in cols.items():
        args.append(value)
        sets.append(f"{name} = ${len(args)}")
    await execute_sql(f"UPDATE ig_funnel_sends SET {', '.join(sets)} WHERE id = $1",
                      *args)


def _within_window(commented_at: datetime | None, now: datetime) -> bool:
    return commented_at is None or now - commented_at <= PRIVATE_REPLY_WINDOW


async def funnel_reply_dry(account: str, funnel_id: Any, comment_id: str,
                           *, now: datetime | None = None) -> ActionResult:
    """What run would do: the funnel is approved and unchanged, the comment
    is a queued keyword match, it is not quiet hours, the cap has room."""
    now = now or _clock()
    try:
        fid = int(funnel_id)
    except (TypeError, ValueError):
        return ActionResult(False, f"not a funnel id: {funnel_id!r}",
                            failure_class=FailureClass.VALIDATION)
    funnel = await get_funnel(fid)
    why = _funnel_refusal(funnel, fid, account)
    if why:
        return ActionResult(False, why, failure_class=FailureClass.POLICY)
    assert funnel is not None
    try:
        _cfg(account)
    except NoAccount as exc:
        return ActionResult(False, str(exc), failure_class=FailureClass.DISABLED)
    send = await _send_row(fid, comment_id)
    if send is None:
        return ActionResult(False, f"comment {comment_id} is not a recorded keyword "
                                   f"match for funnel #{fid}; nobody is written to "
                                   f"who did not comment the keyword",
                            failure_class=FailureClass.POLICY)
    if send["status"] == "sent":
        return ActionResult(True, f"already sent to @{send['username']}; would not "
                                  f"send again")
    if send["status"] != "queued":
        return ActionResult(False, f"send #{send['id']} is {send['status']}: "
                                   f"{send['error'] or 'not queued'}",
                            failure_class=FailureClass.VALIDATION)
    local = local_now(str(funnel["timezone"]), now)
    if in_quiet_hours(local):
        return ActionResult(False, f"quiet hours in {funnel['timezone']}; queued "
                                   f"until {next_open(local):%H:%M %Z}",
                            failure_class=FailureClass.VALIDATION)
    if not _within_window(send["commented_at"], now):
        return ActionResult(False, "the comment is older than 7 days; Meta no longer "
                                   "allows a private reply to it",
                            failure_class=FailureClass.VALIDATION)
    done = await sent_today(account, str(funnel["timezone"]), now)
    if done >= int(funnel["daily_cap"]):
        return ActionResult(False, f"the {account} account's daily cap of "
                                   f"{funnel['daily_cap']} DMs is reached "
                                   f"({done} today)",
                            failure_class=FailureClass.BUDGET)
    return ActionResult(True, f"would reply publicly under comment {comment_id} "
                              f"and send @{send['username']} the approved DM "
                              f"(funnel #{fid}, {done}/{funnel['daily_cap']} today)")


async def funnel_reply(account: str, funnel_id: Any, comment_id: str,
                       *, now: datetime | None = None) -> ActionResult:
    """The send: the public reply, then the private reply, each claimed
    first and recorded on the send row. The texts are the approved row's.

    Tier 1 although the DM leg cannot be recalled (the rollback hides only
    the public reply). Accepted (kernel-owner review, 2026-09-30) because
    nothing here is decided at run time: the two texts were approved once
    on the Tier 2 `instagram.funnel_set` card and are hash-checked on every
    send, and the recipient asked for it by commenting the keyword. What a
    person receives is exactly what Miro read, to exactly who asked."""
    preview = await funnel_reply_dry(account, funnel_id, comment_id, now=now)
    if not preview.ok:
        return ActionResult(False, f"refused: {preview.detail}",
                            failure_class=preview.failure_class)
    fid = int(funnel_id)
    funnel = await get_funnel(fid)
    send = await _send_row(fid, comment_id)
    assert funnel is not None and send is not None
    if send["status"] == "sent":
        return ActionResult(True, preview.detail,
                            evidence={"funnel_id": fid, "comment_id": comment_id,
                                      "deduplicated": True})
    evidence: dict[str, Any] = {"funnel_id": fid, "comment_id": str(comment_id),
                                "account": account, "username": send["username"]}
    who = f"@{send['username']}" if send["username"] else send["ig_user_id"]

    # Leg 1: the public reply (never the link).
    public_id = send["public_reply_id"]
    if not public_id:
        key = f"funnel:{fid}:comment:{comment_id}:public"
        state = await idempotency.claim(REPLY, key)
        if state == idempotency.CLAIMED:
            try:
                got = await reply_comment(account, str(comment_id),
                                          str(funnel["public_reply"]))
            except GraphError as exc:
                return await _leg_failed(send, key, exc, "public reply", evidence)
            public_id = str((got.evidence or {}).get("reply_id") or "")
            await idempotency.settle(REPLY, key, f"public reply {public_id}")
            await _mark(send["id"], "queued", public_reply_id=public_id or "sent")
        elif state != idempotency.SENT:
            await _mark(send["id"], "needs_human", idempotency.refusal(REPLY, state))
            return ActionResult(False, f"not sent: {idempotency.refusal(REPLY, state)}",
                                evidence=evidence)
    evidence["public_reply_id"] = public_id

    # Leg 2: the private reply to the comment -- the DM with the link.
    key = f"funnel:{fid}:comment:{comment_id}:private"
    state = await idempotency.claim(REPLY, key)
    if state == idempotency.SENT:
        message_id = send["private_message_id"] or "sent"
    elif state == idempotency.CLAIMED:
        try:
            got = await _private_reply(account, str(comment_id), str(funnel["dm_text"]))
        except GraphError as exc:
            return await _leg_failed(send, key, exc, "private reply", evidence)
        message_id = str(got.get("message_id") or "sent")
        await idempotency.settle(REPLY, key, f"private reply {message_id}")
    else:
        await _mark(send["id"], "needs_human", idempotency.refusal(REPLY, state))
        return ActionResult(False, f"not sent: {idempotency.refusal(REPLY, state)}",
                            evidence=evidence)
    evidence["private_message_id"] = message_id
    await _mark(send["id"], "sent", private_message_id=message_id,
                sent_at=_clock())
    return ActionResult(True, f"funnel #{fid}: replied under comment {comment_id} "
                              f"and DM'd {who} on {account}",
                        evidence=evidence,
                        rollback_token=f"{account}:{public_id}")


async def funnel_reply_rollback(token: str) -> ActionResult:
    """The one undo Graph offers: hide the public reply (`?hide=true`,
    reversible). The private reply is a DM and cannot be recalled; this
    says so rather than pretending."""
    account, _, reply_id = token.partition(":")
    if not account or not reply_id or reply_id == "sent":
        return ActionResult(False, f"no public reply id to hide in {token!r}; the "
                                   f"DM cannot be recalled")
    from . import _post
    await _post(reply_id, account=account, hide="true")
    return ActionResult(True, f"hid the public reply {reply_id} on {account}; the "
                              f"DM already sent cannot be recalled")


async def _leg_failed(send: dict[str, Any], key: str, exc: GraphError, leg: str,
                      evidence: dict[str, Any]) -> ActionResult:
    """Record one leg's refusal: release the claim when Graph certainly
    refused (a retry may send), keep it when the outcome is unknown."""
    cls = classify_graph(exc)
    detail = f"{leg} failed: {exc}"
    if _certainly_not_sent(exc):
        await idempotency.release(REPLY, key)
    else:
        log.warning("%s: the %s's outcome is unknown (%s); its claim stays", REPLY,
                    leg, exc)
        cls = None
    status = ("needs_human" if cls is None else
              "failed" if cls in (FailureClass.VALIDATION, FailureClass.POLICY) else
              "queued")
    cols: dict[str, Any] = {}
    if status == "queued":
        attempts = int(send["attempts"]) + 1
        if attempts >= MAX_ATTEMPTS:
            status = "failed"
            detail += f" (after {attempts} attempts)"
        cols = {"attempts": attempts,
                "next_attempt_at": _clock() + backoff(attempts)}
    await _mark(send["id"], status, detail, **cols)
    return ActionResult(False, detail, evidence={**evidence, "leg": leg},
                        failure_class=cls)


# --------------------------------------------------------------- the sweep

def _ts(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("+0000", "+00:00"))
    except (TypeError, ValueError):
        return None


async def _media_ids(funnel: dict[str, Any], ig_id: str) -> list[str]:
    if funnel["media_id"]:
        return [str(funnel["media_id"])]
    data = await _get(f"{ig_id}/media", account=str(funnel["account"]),
                      fields="id,timestamp", limit=RECENT_MEDIA)
    listed = data.get("data") if isinstance(data, dict) else None
    if not isinstance(listed, list):
        raise GraphError("Graph /media carried no list of posts")
    return [str(m["id"]) for m in listed if isinstance(m, dict) and m.get("id")]


async def new_comments(funnel: dict[str, Any], media_id: str
                       ) -> list[dict[str, Any]]:
    """Comments on `media_id` newer than the funnel's watermark for it,
    paged from the top by `paging.cursors.after` (never `paging.next`,
    which can carry a token in its query string), at most MAX_PAGES.
    Moves the watermark to the newest timestamp read."""
    account = str(funnel["account"])
    cur = await fetch_one(
        "SELECT newest_at FROM ig_funnel_cursors WHERE funnel_id = $1 AND media_id = $2",
        int(funnel["id"]), media_id)
    mark: datetime | None = cur["newest_at"] if cur else None
    floor = funnel["approved_at"]
    out: list[dict[str, Any]] = []
    newest = mark
    after: str | None = None
    for _ in range(MAX_PAGES):
        params: dict[str, Any] = {"fields": "id,text,username,from,timestamp",
                                  "limit": PAGE_LIMIT}
        if after:
            params["after"] = after
        page = await _get(f"{media_id}/comments", account=account, **params)
        listed = page.get("data") if isinstance(page, dict) else None
        if not isinstance(listed, list):
            raise GraphError(f"Graph /comments for {media_id} carried no list")
        stamps: list[datetime] = []
        for c in listed:
            if not isinstance(c, dict) or not c.get("id"):
                continue
            at = _ts(c.get("timestamp"))
            if at is not None:
                stamps.append(at)
            if at is not None and mark is not None and at <= mark:
                continue
            if at is not None and floor is not None and at < floor:
                continue
            out.append({"id": str(c["id"]), "text": str(c.get("text") or ""),
                        "username": str(c.get("username") or
                                        (c.get("from") or {}).get("username") or ""),
                        "user_id": str((c.get("from") or {}).get("id") or ""),
                        "at": at})
        if stamps:
            top = max(stamps)
            newest = top if newest is None or top > newest else newest
        # Newest-first pages: once a whole page is behind the mark, stop.
        if stamps and mark is not None and max(stamps) <= mark:
            break
        after = ((page.get("paging") or {}).get("cursors") or {}).get("after")
        if not after or not listed:
            break
    await execute_sql(
        """INSERT INTO ig_funnel_cursors (funnel_id, media_id, newest_at)
           VALUES ($1, $2, $3)
           ON CONFLICT (funnel_id, media_id) DO UPDATE
              SET newest_at = GREATEST(ig_funnel_cursors.newest_at, EXCLUDED.newest_at),
                  updated_at = now()""",
        int(funnel["id"]), media_id, newest)
    return out


async def _queue_matches(funnel: dict[str, Any], media_id: str, ig_id: str,
                         own_username: str, comments: list[dict[str, Any]],
                         now: datetime) -> int:
    """Record one send per commenter who wrote the keyword. Own comments,
    unknown authors, and anyone already recorded for this funnel are
    skipped -- the UNIQUE index is the once-only rule."""
    queued = 0
    keyword = str(funnel["keyword"])
    for c in comments:
        if not matches(keyword, c["text"]):
            continue
        user_id = c["user_id"] or c["username"]
        if not user_id:
            continue
        if c["user_id"] == ig_id or (own_username and
                                     c["username"].lower() == own_username.lower()):
            continue
        if not _within_window(c["at"], now):
            continue
        row = await fetch_one(
            """INSERT INTO ig_funnel_sends
                 (funnel_id, ig_user_id, username, comment_id, media_id,
                  comment_text, commented_at, queued_at, next_attempt_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $8)
               ON CONFLICT DO NOTHING RETURNING id""",
            int(funnel["id"]), user_id, c["username"][:80], c["id"], media_id,
            c["text"][:500], c["at"], now)   # due on the sweep's own clock
        if row is not None:
            queued += 1
    return queued


async def _own_username(account: str, ig_id: str) -> str:
    try:
        data = await _get(ig_id, account=account, fields="username")
        return str(data.get("username") or "") if isinstance(data, dict) else ""
    except Exception:  # noqa: BLE001
        return ""


async def _record_error(funnel_id: int, error: str) -> None:
    await execute_sql(
        """UPDATE ig_funnels SET last_error = $2, last_error_at = $3
            WHERE id = $1""", int(funnel_id), error[:500], _clock())


async def stop_funnel(funnel_id: int, why: str) -> None:
    """A policy or block error from Meta: the funnel stops (active stays
    as it was; `stopped_at` blocks every send) and Miro is told."""
    await execute_sql(
        """UPDATE ig_funnels SET stopped_at = $3, stopped_why = $2,
                                last_error = $2, last_error_at = $3
            WHERE id = $1 AND stopped_at IS NULL""", int(funnel_id), why[:500],
        _clock())
    try:
        from ...kernel import comms
        await comms.notify(comms.Outbound(
            title=f"Instagram funnel #{funnel_id} stopped",
            body=f"Meta answered with a policy/block error, so the funnel is "
                 f"stopped and sends nothing: {why[:300]}. Check the account, then "
                 f"set the funnel again to restart it.",
            severity=Severity.HIGH, topic=TOPIC))
    except Exception:  # noqa: BLE001
        log.warning("funnel #%s stopped; Miro not told", funnel_id)


async def sweep(now: datetime | None = None) -> tuple[list[dict[str, Any]],
                                                    dict[str, Any]]:
    """The read half of a tick: for every active funnel, read the new
    comments on its media and record the keyword matches as queued sends.
    Returns (the funnels whose sweep succeeded, a summary). Never raises;
    per-funnel errors land on the row (`last_error`) and in the summary.
    Sending is `kernel.funnels.tick`'s -- this module only reads and
    queues."""
    now = now or _clock()
    summary: dict[str, Any] = {"funnels": 0, "queued": 0, "errors": [],
                               "auth_refused": []}
    ready: list[dict[str, Any]] = []
    try:
        funnels = await active_funnels()
    except Exception as exc:  # noqa: BLE001
        summary["errors"].append(f"funnels unreadable: {type(exc).__name__}")
        return ready, summary
    usernames: dict[str, str] = {}
    for f in funnels:
        summary["funnels"] += 1
        account = str(f["account"])
        try:
            ig_id, _ = _cfg(account)
        except NoAccount as exc:
            await _record_error(int(f["id"]), str(exc))
            summary["errors"].append(f"#{f['id']}: {exc}")
            continue
        if not _hash_ok(f):
            why = "texts no longer match what was approved (hash mismatch)"
            await _record_error(int(f["id"]), why)
            summary["errors"].append(f"#{f['id']}: {why}")
            continue
        try:
            if account not in usernames:
                usernames[account] = await _own_username(account, ig_id)
            for media_id in await _media_ids(f, ig_id):
                comments = await new_comments(f, media_id)
                summary["queued"] += await _queue_matches(
                    f, media_id, ig_id, usernames[account], comments, now)
        except GraphError as exc:
            await _record_error(int(f["id"]), str(exc))
            summary["errors"].append(f"#{f['id']}: {exc}")
            if classify_graph(exc) is FailureClass.AUTH:
                summary["auth_refused"].append((account, exc.status_code))
            continue
        except Exception as exc:  # noqa: BLE001
            await _record_error(int(f["id"]), type(exc).__name__)
            summary["errors"].append(f"#{f['id']}: {type(exc).__name__}")
            continue
        ready.append(f)
    return ready, summary


# ------------------------------------------------------------- reporting

async def check_funnels() -> Observation:
    """Active funnels, sends today, cap remaining, last error. UNKNOWN when
    the tables cannot be read; DEGRADED when a funnel is stopped or erred
    in the last day."""
    read_at = _clock()
    try:
        funnels = await list_funnels()
        today: dict[str, int] = {}
        for f in funnels:
            acct = str(f["account"])
            if acct not in today:
                today[acct] = await sent_today(acct, str(f["timezone"]), read_at)
    except Exception as exc:  # noqa: BLE001
        return Observation("funnels", Health.UNKNOWN,
                           detail=f"funnel tables unreadable ({type(exc).__name__})")
    active = [f for f in funnels if f["active"] and f["approved_at"] and
              not f["stopped_at"]]
    stopped = [f for f in funnels if f["stopped_at"]]
    recent_err = [f for f in funnels if f["last_error_at"] and
                  f["last_error_at"] > read_at - timedelta(days=1)]
    parts = []
    for f in active:
        cap = int(f["daily_cap"]) - today.get(str(f["account"]), 0)
        parts.append(f"#{f['id']} {f['account']} “{f['keyword']}”: "
                     f"{today.get(str(f['account']), 0)} sent today, {max(cap, 0)} "
                     f"left of {f['daily_cap']}")
    detail = (f"{len(active)} active funnel(s)"
              + (": " + "; ".join(parts) if parts else "")
              + f" (read {read_at:%H:%M} UTC)")
    if stopped:
        detail += ("; stopped: " + ", ".join(
            f"#{f['id']} ({f['stopped_why'][:80]})" for f in stopped))
    if recent_err:
        detail += ("; last error: " + "; ".join(
            f"#{f['id']} {f['last_error'][:100]} at {f['last_error_at']:%H:%M} UTC"
            for f in recent_err))
    health = Health.DEGRADED if stopped or recent_err else Health.OK
    return Observation("funnels", health, float(sum(today.values())), "dms",
                       detail,
                       evidence={"active": len(active), "sent_today": today,
                                 "stopped": [int(f["id"]) for f in stopped],
                                 "read_at": read_at.isoformat()})


async def weekly_section(now: datetime | None = None, days: int = 7) -> str:
    """The weekly summary's paragraph: sends per funnel, unique people,
    errors, and what is not known. Never raises. Call site: add
    `await instagram.funnel.weekly_section(now)` beside the realestate
    section in `kernel/weekly.py` (not wired here)."""
    now = now or _clock()
    since = now - timedelta(days=days)
    head = "*Instagram comment funnels*"
    try:
        rows = await fetch_all(
            """SELECT f.id, f.account, f.keyword,
                      count(*) FILTER (WHERE s.status = 'sent'
                                         AND s.sent_at > $1::timestamptz) AS sent,
                      count(DISTINCT s.ig_user_id)
                          FILTER (WHERE s.status = 'sent'
                                    AND s.sent_at > $1::timestamptz) AS people,
                      count(*) FILTER (WHERE s.status IN ('failed', 'needs_human')
                                         AND s.queued_at > $1::timestamptz) AS errors,
                      count(*) FILTER (WHERE s.status = 'queued') AS waiting
                 FROM ig_funnels f LEFT JOIN ig_funnel_sends s ON s.funnel_id = f.id
                WHERE f.approved_at IS NOT NULL
                GROUP BY f.id, f.account, f.keyword ORDER BY f.id""", since)
    except Exception:
        log.exception("funnel: weekly section unreadable")
        return (f"{head}\nI could not read the funnel tables this week, so every "
                f"figure is UNKNOWN — not zero.")
    stamp = f"(Read {now.astimezone(UTC):%a %d %b %H:%M} UTC.)"
    if not rows:
        return f"{head}\nNo funnels defined. {stamp}"
    lines = []
    for r in rows:
        lines.append(f"#{r['id']} {r['account']} “{r['keyword']}”: "
                     f"{int(r['sent'])} DM(s) to {int(r['people'])} people, "
                     f"{int(r['errors'])} error(s), {int(r['waiting'])} waiting")
    lines.append("Conversion: unknown (no join key — the store records no "
                 "Instagram handle on a purchase).")
    return f"{head}\n" + "\n".join(lines) + f" {stamp}"


# ---------------------------------------------------------- the command

USAGE = ('`/atlas funnel <account> <keyword> "<public reply>" "<DM text>" '
         '[media=<id>] [cap=<n>] [tz=<zone>]` raises the card; '
         '`/atlas funnel list`; `/atlas funnel pause <id>`.')
_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})


_SET_FORM = re.compile(r"^[A-Za-z0-9_]+\s+[A-Za-z0-9_\-]+\s+[\"']")
_PAUSE_FORM = re.compile(r"^pause\s+(\d+)\s*$", re.IGNORECASE)


def parse_command(text: str) -> dict[str, Any] | None:
    """The exact `funnel ...` forms, parsed in code (never by a model):
    `funnel` / `funnel list`, `funnel pause <id>`, and the quoted set form
    `funnel <account> <keyword> "<public>" "<dm>" [k=v ...]`. Anything
    else that happens to start with the word -- "funnel my new leads into
    a sheet" -- is None, and falls through to the picker untouched."""
    words = (text or "").strip().translate(_QUOTES)
    if not re.match(r"^funnel\b", words, re.IGNORECASE):
        return None
    rest = words[len("funnel"):].strip()
    if not rest or rest.lower() == "list":
        return {"verb": "list"}
    if (m := _PAUSE_FORM.match(rest)) is not None:
        return {"verb": "pause", "funnel_id": int(m.group(1))}
    if not _SET_FORM.match(rest):
        return None
    try:
        parts = shlex.split(rest)
    except ValueError as exc:
        return {"verb": "error", "why": f"unbalanced quotes: {exc}"}
    if len(parts) < 4:
        return {"verb": "error", "why": f"not enough parts. {USAGE}"}
    params: dict[str, Any] = {"account": parts[0].lower(), "keyword": parts[1],
                              "public_reply": parts[2], "dm_text": parts[3]}
    for extra in parts[4:]:
        name, _, value = extra.partition("=")
        if name == "media":
            params["media_id"] = value
        elif name == "cap":
            params["daily_cap"] = value
        elif name == "tz":
            params["timezone"] = value
        else:
            return {"verb": "error", "why": f"unknown option {extra!r}. {USAGE}"}
    return {"verb": "set", "params": params}
