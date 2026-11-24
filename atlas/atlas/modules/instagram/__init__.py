"""Instagram, via the official Graph API. Three accounts, one Graph API.

What is actually possible, so nobody builds on a wrong assumption:

  * Requires an Instagram Business or Creator account linked to a Facebook
    Page, and a Meta app that has passed review for the permissions below.
  * Reading insights, media, comments and mentions: supported.
  * Publishing feed posts, carousels and Reels: supported via the two-step
    container/publish flow, with a daily publishing quota.
  * Stories: publishing is supported for Business accounts only.
  * DMs: only through the Messenger Platform, and only for conversations the
    user initiated within the messaging window. No DM check exists here yet
    — `brief()` says so rather than reporting zero.
  * Hiding a comment (`?hide=true`) and unhiding it (`?hide=false`) are both
    supported and both reversible. Hiding is Tier ASK, not Tier AUTO: REELS
    (`Da3bCbINuQf`) rules that hiding someone else's comment is a decision
    about another person, even though it is reversible and invisible to
    everyone but the commenter — the reversibility is why it keeps its
    `unhide_comment` rollback rather than losing one, not why it runs
    unattended. Deleting a comment is not offered here at all — that is
    permanently denied elsewhere (invariant 3) and there is no reason for
    this module to reach for it.

Anything outside that list needs browser automation, which for Instagram is a
reliable way to lose the account. This module does not do it.

**Accounts.** `ACCOUNTS` maps a short name to its env prefix: `main` is
Miro's own account, `stories` is the AI-stories account added in Phase 18 —
labelled AI-made short stories, under a monthly render-credit cap enforced
in `content.render_video`, nothing else. It is not a content farm of fake
people: every caption it posts says "Made with AI" (Meta's own disclosure
rule), and `publish`/`publish_dry` refuse to post to `stories` unless the
caption already ends with `content.drafts.AI_LABEL` — enforced in code, not
left to whoever writes the caption. It goes through `instagram.publish`
exactly like the main account — the same Tier ASK gate, never auto-posted.
Adding a third account is a new prefix in `ACCOUNTS`, not new code; every
check and action below takes `account` and resolves its own env rather than
assuming which one.

**The clips account** (2026-09-28): `clips` is @atlas.clipsdrop, where the
clips lane posts brand campaign clips cut from the brand's own footage
(`INSTAGRAM_CLIPS_ACCOUNT_ID`, `INSTAGRAM_CLIPS_TOKEN`). It takes one kind,
`campaign_clip`, and its caption rule is the FTC one the campaigns set, not
the AI label: an `#Ad` (or `#Advertisement` / `#Sponsored`) line, alone,
right after the post text and a blank line, and the first hashtag in the
caption. A campaign's own rules (its handle, its hashtags) are checked by
`clips.captions` before a card is raised; this is the account-level floor.
And it posts only a Reel from a `clip.` link on ATLAS's own host, which only
`clips.publish_clip` mints -- so nothing reaches the account without the
lane's registration, sha256 and content checks. Every clip post is Tier
ASK, one card per clip.

**What each account may post** (Miro, 2026-09-24, Task 55): `main`
(@flycheaphp) posts only FlyCheap / travel / cheap-flight content and never
an AI story reel; AI story reels go only to `stories` (@atlasstories3).
`ACCOUNT_KINDS` is that ruling as data, and `instagram.publish` carries a
required `content_kind` that must be one the account takes. It is refused
three times over, all in code: by the action's pure `vet`, which
`approvals.propose()` asks before a card exists (so a mismatched card is
never raised); by the dry run; and by the run itself, before any Graph call.
`content_kind` is its own parameter because `kind` already means the media
type (image or reel) and changing that would change every existing caller.
A missing `content_kind` is a missing parameter, never a default.

**The overnight line.** `brief()` is synchronous (the Module protocol gives
it no await), so it cannot query Postgres itself. `check_overnight` is an
ordinary async Check — it runs on the scheduler's clock, reads `actions_log`
since the last stored brief (or 24h ago, on a fresh install),
`messages.check_unanswered`'s cached observation and the pending
`instagram.hide_comment` proposals, and caches what it found in
`_overnight_cache`. `brief()` only ever reads that cache: a figure it has
never read says so, and nothing is invented for the account a run has not
yet reached. A DM count is never reported as a silent zero when the bridge
that would have measured it was actually unreachable. "Visits" and "reach"
are left out of the line: the engagement check reads them (`reach`,
`profile_views`, verified live 2026-09-25 with `metric_type=total_value`).
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

import httpx

from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    Health,
    Observation,
    Severity,
    Signal,
    Tier,
)
from .._http import number

GRAPH = "https://graph.facebook.com/v21.0"

ACCOUNTS: dict[str, str] = {"main": "INSTAGRAM_", "stories": "INSTAGRAM_STORIES_",
                            "clips": "INSTAGRAM_CLIPS_"}

# What each account may post, by content kind (Miro's ruling, Task 55). An
# account missing here takes nothing -- a new account is refused until its
# kinds are written down, never waved through.
ACCOUNT_KINDS: dict[str, set[str]] = {
    "main": {"flycheap", "travel"},
    "stories": {"ai_story"},
    # Miro, 2026-10-05 ("post on clips too"): the clips account also takes
    # the Academy store's own promo reels, through clips.publish_promo only.
    "clips": {"campaign_clip", "store_promo"},
}
STORE_PROMO = "store_promo"

# The FTC disclosures the clipping campaigns accept (their rules docs, 2026-09-28).
DISCLOSURES = ("#Ad", "#Advertisement", "#Sponsored")


class NoAccount(Exception):
    """This account's env vars are not set. Not the same as Graph refusing it."""


class GraphError(Exception):
    """The Graph API refused, or could not be reached. Its text is safe.

    The token now rides in an `Authorization` header, never the query
    string (ed4a247: a 400 on /insights printed the live token through
    httpx's HTTPStatusError). Even so, httpx's own exceptions print the full
    URL -- so an httpx error from here used to
    carry the token into `observations.detail`, the reasoner's prompt, and
    `actions_log`, which is a hash chain and can never be scrubbed. This is
    raised in its place, `from None`, with a message built from the status,
    the path and Graph's own error message, redacted and with the token
    itself masked. `status_code` is None when nothing answered.
    """

    def __init__(self, message: str, status_code: int | None = None,
                 code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        # Graph's own error code (`error.code`: 4/17/32 rate limits, 190 a
        # dead token, 368 a policy block ...), when the body carried one.
        self.code = code


def _scrub(text: str, token: str) -> str:
    """`redact()`, and the token masked by value whatever its length or name."""
    from .._http import redact
    cleaned = redact(text)
    return cleaned.replace(token, "<redacted>") if token else cleaned


def _raise_for(r: httpx.Response, token: str) -> None:
    """`raise_for_status`, without the URL: a GraphError that names the
    status, the path and what Graph said, never the token."""
    if r.status_code < 400:
        return
    said = ""
    code: int | None = None
    try:
        body = r.json()
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            said = str(error.get("message") or "")
            if isinstance(error.get("code"), int):
                code = int(error["code"])
    except ValueError:
        said = r.text[:200]
    path = r.request.url.path if r.request is not None else ""
    message = f"Graph API HTTP {r.status_code} on {path}"
    if code is not None:
        message += f" (code {code})"
    if said:
        message += f": {' '.join(said.split())[:200]}"
    raise GraphError(_scrub(message, token), r.status_code, code)


def _unreachable(exc: BaseException, token: str) -> GraphError:
    from .._http import explain
    return GraphError(_scrub(f"Graph API unreachable: {explain(exc)}", token))


def _why(exc: BaseException) -> str:
    """A detail for an Observation or ActionResult: never the raw exception."""
    if isinstance(exc, (GraphError, NoAccount)):
        return str(exc)
    from .._http import explain
    return explain(exc)


def switched_off() -> bool:
    """Miro, 2026-10-11: "delete everything containing Instagram, only keep
    YouTube and TikTok". Instagram is off when the module is named in
    ATLAS_DISABLED_MODULES. Every reader and poster in ATLAS gets its token
    through `_cfg` (and the DM reader through `messages.instagram_dm`, which
    asks this too), so off here is off everywhere: each caller already
    treats NoAccount as "not configured" and skips the leg, never fails the
    whole reel."""
    return "instagram" in {m.strip().lower() for m in
                           os.getenv("ATLAS_DISABLED_MODULES", "").split(",")}


def _cfg(account: str = "main") -> tuple[str, str]:
    if switched_off():
        raise NoAccount("Instagram is switched off (ATLAS_DISABLED_MODULES)")
    prefix = ACCOUNTS.get(account)
    if prefix is None:
        raise NoAccount(
            f"no such Instagram account {account!r}; known accounts: "
            f"{', '.join(ACCOUNTS)}")
    ig_id = os.getenv(f"{prefix}ACCOUNT_ID", "").strip()
    token = os.getenv(f"{prefix}TOKEN", "").strip()
    if not ig_id or not token:
        raise NoAccount(
            f"{account} account is not configured: set {prefix}ACCOUNT_ID "
            f"and {prefix}TOKEN")
    return ig_id, token


async def _get(path: str, account: str = "main", **params) -> dict:
    _, token = _cfg(account)
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{GRAPH}/{path}", params=params,
                            headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise _unreachable(exc, token) from None
    _raise_for(r, token)
    return r.json()


async def _post(path: str, account: str = "main", **params) -> dict:
    _, token = _cfg(account)
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{GRAPH}/{path}", params=params,
                             headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise _unreachable(exc, token) from None
    _raise_for(r, token)
    return r.json()


# ---------------------------------------------------------------- overnight

# The fallback for `_since_last_brief` when no brief has ever been stored --
# `kernel.brief.build`'s own default window, so a fresh install reads the
# same 24h a brief itself would rather than inventing a second convention.
OVERNIGHT_WINDOW = timedelta(hours=24)

# `brief()` is synchronous (the Module protocol gives it no await), so it
# cannot query Postgres itself. `check_overnight` is an ordinary async Check
# that does the querying and leaves its result here, keyed by account — the
# only thing `_overnight_line` ever reads. An account this has never run for
# reads as "not read yet", never as zero: a zero nothing has measured is
# exactly the invariant-6/7 failure this repo rejects.
_overnight_cache: dict[str, Observation] = {}


def _account_marker(action: str, account: str) -> str:
    """The exact substring `hide_comment`/`reply_comment` write into their
    own `ActionResult.detail` for this account — read back out of
    `actions_log`, not re-derived, so a wording change in one place cannot
    silently stop matching the other."""
    if action == "instagram.hide_comment":
        return f"on the {account} account"
    return f"on {account}"


async def _latest_messages_observation() -> dict | None:
    """The newest `messages`/`unanswered` observation, of ANY health,
    together with the time it was read.

    `kernel.state.last_evidence` deliberately skips readings that carry no
    evidence, which is right for a check diffing against "what did I see
    last time" but wrong here: an UNKNOWN reading (both DM bridges
    unreachable — the likely state whenever the Mac itself is offline) IS
    the thing this line has to report, not something to quietly paper over
    with whichever earlier reading still had evidence. `properties.name` /
    `checks.name` split the same way `kernel.scheduler` splits the registry
    key `messages.unanswered` (`property_id`, `check_id`), so this matches
    what the scheduler actually wrote. Returns `None` on any error or when
    there is no observation at all yet."""
    from ...kernel.state import fetch_one as _fetch_one

    try:
        return await _fetch_one(
            """SELECT o.health, o.evidence, o.at
                 FROM observations o
                 JOIN checks c ON c.id = o.check_id
                 JOIN properties p ON p.id = c.property_id
                WHERE p.name = 'messages' AND c.name = 'unanswered'
                ORDER BY o.at DESC
                LIMIT 1""")
    except Exception:  # noqa: BLE001
        return None


def _dm_waiting_text(row: dict | None) -> tuple[int | None, str]:
    """`messages.check_unanswered`'s newest observation, read honestly
    rather than handed straight to a template. Two ways this must not
    become a silent zero or a silent stale number:

      * an UNKNOWN row with no `unreachable` evidence (the check could not
        run at all) — the observation's own health says so,
        which is why this reads health straight off the row rather than
        trusting `last_evidence` (which would keep returning the last
        reading that still had evidence, hours or days old, presented as
        current);
      * even with a healthy reading, it omits a platform from
        `by_platform` entirely both when it measured zero AND when its
        bridge was unreachable or its window was full, so
        `.get("instagram", 0)` alone would still turn "could not check"
        into a healthy zero.

    Returns `(count_or_None, text)`; the text always carries the read time
    once there is a reading to carry it from."""
    if row is None:
        return None, "not read"
    at = row["at"]
    evidence = row["evidence"]
    if isinstance(evidence, str):
        evidence = json.loads(evidence)
    evidence = evidence or {}
    if row["health"] == Health.UNKNOWN and not evidence.get("unreachable"):
        return None, f"not read (check could not run, {at:%H:%M} UTC)"
    unreachable = evidence.get("unreachable") or []
    if any("instagram" in str(u).lower() for u in unreachable):
        return None, f"not read (Instagram bridge unreachable, {at:%H:%M} UTC)"
    count = int(evidence.get("by_platform", {}).get("instagram", 0))
    truncated = evidence.get("truncated") or []
    if any(str(t).lower() == "instagram" for t in truncated):
        return count, f"at least {count} as of {at:%H:%M} UTC"
    return count, f"{count} as of {at:%H:%M} UTC"


async def _since_last_brief() -> datetime:
    """The last brief actually sent (`reports.kind = 'brief'`), or
    `OVERNIGHT_WINDOW` ago when none has ever been stored — `kernel.brief.
    build`'s own default, so a fresh install reads the same 24h window the
    brief itself would. Never raises: an unreadable `reports` table falls
    back to the same default rather than failing the whole check over a
    detail this line does not strictly need."""
    from ...kernel.state import fetch_one as _fetch_one

    fallback = datetime.now(UTC) - OVERNIGHT_WINDOW
    try:
        row = await _fetch_one(
            "SELECT max(at) AS at FROM reports WHERE kind = 'brief'")
        at = row["at"] if row else None
    except Exception:  # noqa: BLE001
        return fallback
    return at or fallback


async def check_overnight(account: str) -> Observation:
    """Rebuilt from Postgres every run — never a process counter, which
    reads as zero after every restart and was never true for more than one
    process. Three sources, none of them guessed:

      * `actions_log` `execute.finish` rows for `instagram.hide_comment` and
        `instagram.reply_comment` since `_since_last_brief()` (the last
        stored `reports.kind = 'brief'`, or `OVERNIGHT_WINDOW` ago when none
        exists), filtered to this account by the account marker the action
        itself wrote into `detail` (the audit entry carries no structured
        params — only `execute.start` does — so the human-readable detail
        is the record).
      * DMs waiting: `messages.check_unanswered`'s own latest observation,
        filtered to instagram — this module runs no DM check of its own.
      * Hidden-comment proposals still waiting on a decision: pending,
        unexpired rows in `proposals` for this account.

    Caches whatever it returns for `_overnight_line` to read, since
    `brief()` cannot await this itself — including the UNKNOWN case, so a
    run that could not read `actions_log` replaces a stale cached count
    with "not read" rather than leaving the brief repeating old figures
    forever. The observation's own health is UNKNOWN whenever ANY part of
    the line was not actually measured (invariant 7: an unmeasured part
    must never share OK's treatment), not only when `actions_log` itself is
    unreadable.
    """
    from ...kernel.state import fetch_all, fetch_one

    name = f"{account}_overnight"
    since = await _since_last_brief()
    try:
        rows = await fetch_all(
            """SELECT subject, payload, at FROM actions_log
               WHERE event = 'execute.finish'
                 AND subject IN ('instagram.hide_comment', 'instagram.reply_comment')
                 AND at >= $1
               ORDER BY at""", since)
    except Exception as exc:  # noqa: BLE001
        obs = Observation(name, Health.UNKNOWN, detail=f"not read — {exc}")
        _overnight_cache[account] = obs
        return obs

    hidden_n, hidden_at = 0, None
    answered_n, answered_at = 0, None
    for row in rows:
        try:
            data = json.loads(row["payload"])["data"]
        except (ValueError, KeyError, TypeError):
            continue
        if not data.get("ok"):
            continue
        detail = data.get("detail") or ""
        if _account_marker(row["subject"], account) not in detail:
            continue
        if row["subject"] == "instagram.hide_comment":
            hidden_n += 1
            hidden_at = row["at"]
        else:
            answered_n += 1
            answered_at = row["at"]

    dm_row = await _latest_messages_observation()
    dm_waiting, dm_text = _dm_waiting_text(dm_row)

    moderation_pending: int | None
    try:
        pending_row = await fetch_one(
            """SELECT count(*) AS n FROM proposals
               WHERE status = 'pending' AND expires_at > now()
                 AND action = 'instagram.hide_comment'
                 AND params->>'account' = $1""", account)
        moderation_pending = int(pending_row["n"]) if pending_row else 0
    except Exception:  # noqa: BLE001
        moderation_pending = None

    parts = [
        f"{hidden_n} hidden" + (f" (last {hidden_at:%H:%M} UTC)" if hidden_at else ""),
        f"{answered_n} comment(s) answered" +
        (f" (last {answered_at:%H:%M} UTC)" if answered_at else ""),
        f"DMs waiting: {dm_text}",
        f"moderation awaiting approval: {moderation_pending}"
        if moderation_pending is not None else
        "moderation awaiting approval: not read",
        ("visits/reach: not in this line — read by the engagement check "
         "(reach, profile_views; verified 2026-09-25)"),
    ]
    # Any part not actually measured means the observation itself is not
    # fully measured either — an unmeasured DM count or moderation count
    # must not share OK's treatment (invariant 7), even though the text
    # already says "not read" plainly.
    health = Health.UNKNOWN if (dm_waiting is None or moderation_pending is None) \
        else Health.OK
    obs = Observation(
        name, health, detail=", ".join(parts),
        evidence={"hidden": hidden_n, "answered": answered_n,
                 "dm_waiting": dm_waiting, "moderation_pending": moderation_pending})
    _overnight_cache[account] = obs
    return obs


def _overnight_line() -> str:
    parts = []
    for account in ACCOUNTS:
        obs = _overnight_cache.get(account)
        if obs is None:
            parts.append(f"{account}: not read yet")
        else:
            parts.append(f"{account} (read {obs.at:%H:%M} UTC): {obs.detail}")
    return "Overnight — " + "; ".join(parts)


# -------------------------------------------------------------------- checks

async def check_token(account: str = "main") -> Observation:
    """A silently expired long-lived token is the most common Instagram
    failure: everything looks fine and nothing is collected."""
    name = f"{account}_token"
    try:
        ig_id, _ = _cfg(account)
    except NoAccount as exc:
        return Observation(name, Health.UNKNOWN, detail=str(exc))
    try:
        data = await _get(f"{ig_id}", account=account,
                          fields="id,username,followers_count")
        # The token works: that is what this check measures. A missing
        # follower count is no count, never a zero.
        followers, missing = number(data, "followers_count", where="Graph")
        return Observation(
            check=name, health=Health.OK,
            value=followers, unit="followers",
            detail=f"@{data.get('username')}"
                   + (f" (follower count not read: {missing})" if missing else ""),
        )
    except GraphError as exc:
        if exc.status_code is None:
            return Observation(check=name, health=Health.UNKNOWN, detail=str(exc))
        return Observation(
            check=name, health=Health.FAILING,
            detail=f"Graph API rejected the token: {exc.status_code}. "
                   "Long-lived tokens last 60 days and must be refreshed.",
            evidence={"status": exc.status_code},
        )
    except Exception as exc:  # noqa: BLE001
        return Observation(check=name, health=Health.UNKNOWN, detail=_why(exc))


# Account-level insights (verified live 2026-09-25 on both accounts, v21.0):
# `metric=reach,profile_views,accounts_engaged&period=day&metric_type=
# total_value` answers `data[].total_value.value`. Without `metric_type`
# Graph refuses with "(#100) The following metrics (profile_views) should be
# specified with parameter metric_type=total_value".
ACCOUNT_INSIGHT_PARAMS = {"period": "day", "metric_type": "total_value"}


def insight_value(metric: dict) -> float | None:
    """One metric's number from a Graph insights entry: `total_value.value`
    (the shape `metric_type=total_value` returns) or, for a media insight,
    `values[0].value`. None when neither is there -- never 0."""
    if not isinstance(metric, dict):
        return None
    total = metric.get("total_value")
    if isinstance(total, dict) and "value" in total:
        try:
            return float(total["value"])
        except (TypeError, ValueError):
            return None
    try:
        return float(metric["values"][0]["value"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


async def check_engagement(account: str = "main") -> Observation:
    name = f"{account}_engagement"
    try:
        ig_id, _ = _cfg(account)
    except NoAccount as exc:
        return Observation(name, Health.UNKNOWN, detail=str(exc))
    try:
        data = await _get(f"{ig_id}/insights", account=account,
                          metric="reach,profile_views", **ACCOUNT_INSIGHT_PARAMS)
    except Exception as exc:  # noqa: BLE001
        return Observation(name, Health.UNKNOWN, detail=_why(exc))
    # Graph answered, but a reach it did not report is not a reach of zero.
    reach: float | None = None
    visits: float | None = None
    try:
        for metric in data.get("data") or []:
            if metric.get("name") == "reach":
                reach = insight_value(metric)
            elif metric.get("name") == "profile_views":
                visits = insight_value(metric)
    except (AttributeError, TypeError):
        reach = None
    if reach is None:
        return Observation(name, Health.UNKNOWN,
                           detail="Graph insights carried no readable reach "
                                  "value; the metric name or shape may have "
                                  "changed")
    at = datetime.now(UTC)
    said = (f"reach {reach:.0f}/day, profile views "
            f"{'not reported' if visits is None else f'{visits:.0f}'} "
            f"(read {at:%H:%M} UTC)")
    return Observation(check=name, health=Health.OK, value=reach,
                       unit="reach/day", detail=said,
                       evidence={"reach": reach, "profile_views": visits,
                                 "read_at": at.isoformat()})


async def check_comments_pending(account: str = "main") -> Observation:
    """Comments on this account's last ten posts with no reply yet. One
    answer to "how many are waiting", per account — the previous single-
    account version of this check is this function with `account` bound."""
    name = f"{account}_comments_pending"
    try:
        ig_id, _ = _cfg(account)
    except NoAccount as exc:
        return Observation(name, Health.UNKNOWN, detail=str(exc))
    try:
        media = await _get(f"{ig_id}/media", account=account,
                           fields="id,caption", limit=10)
        posts = media.get("data")
        if not isinstance(posts, list):
            return Observation(name, Health.UNKNOWN,
                               detail="Graph /media carried no list of posts")
        pending = 0
        for item in posts:
            comments = await _get(f"{item['id']}/comments", account=account,
                                  fields="id,text,replies", limit=25)
            listed = comments.get("data")
            if not isinstance(listed, list):
                return Observation(
                    name, Health.UNKNOWN,
                    detail=f"Graph /comments for post {item['id']} carried no "
                           f"list; the count would be a guess")
            pending += sum(1 for c in listed if not c.get("replies"))
    except Exception as exc:  # noqa: BLE001
        return Observation(name, Health.UNKNOWN, detail=_why(exc))
    return Observation(
        check=name,
        health=Health.DEGRADED if pending > 5 else Health.OK,
        value=float(pending), unit="comments",
        detail=f"{account}: {pending} comments without a reply on the last "
               f"10 posts",
    )


def _make_check(account: str, fn):
    async def _wrapped() -> Observation:
        return await fn(account)
    return _wrapped


# ------------------------------------------------------------------- publish

def disclosure_refusal(caption: str) -> str | None:
    """Why this caption fails the campaigns' FTC rule, or None: a disclosure
    alone on its own line, right after the post text and one blank line,
    and the first hashtag anywhere in the caption. Pure."""
    import re
    lines = caption.split("\n")
    at = next((i for i, line in enumerate(lines) if line.strip() in DISCLOSURES), None)
    if at is None:
        return (f"a campaign clip's caption must carry {DISCLOSURES[0]} alone on "
                f"its own line")
    if at < 2 or lines[at - 1].strip() or not any(x.strip() for x in lines[:at - 1]):
        return (f"{lines[at].strip()} must come right after the post text and a "
                f"blank line")
    first = re.search(r"#\w+", caption)
    if first is None or first.group(0) != lines[at].strip():
        return f"{lines[at].strip()} must be the first hashtag in the caption"
    return None


def _source_refusal(account: str, kind: str, image_url: str,
                    content_kind: str = "") -> str | None:
    """The clips account posts only what the clips lane cut and checked:
    a Reel from a `clip.` link on ATLAS's own host, which only
    `clips.publish_clip` mints (after the source's sha256, the content rule
    and the campaign's caption rules). Any other video claiming to be a
    campaign clip is refused. A store promo (`STORE_PROMO`, through
    `clips.publish_promo`) is the one other thing it posts: a finished
    render served from ATLAS's own `/media/` link, never a clip link and
    never a file from anywhere else. Pure."""
    if account != "clips":
        return None
    if kind != "reel":
        return "the clips account posts Reels only"
    host = os.getenv("ATLAS_HOST", "").strip()
    url = str(image_url or "")
    if content_kind == STORE_PROMO:
        if (not host or not url.startswith(f"https://{host}/media/")
                or url.startswith(f"https://{host}/media/clip.")):
            return ("a store promo on the clips account posts only a render "
                    "served from ATLAS's own /media/ link, through "
                    "clips.publish_promo")
        return None
    if not host or not url.startswith(f"https://{host}/media/clip."):
        return ("the clips account posts only a clip the clips lane cut and "
                "checked, through clips.publish_clip")
    return None


def _label_refusal(account: str, caption: str,
                   content_kind: str = "") -> ActionResult | None:
    """Meta requires AI-made content to say so. `stories` posts nothing
    without that label already on the caption — enforced here, in code,
    rather than trusted to whoever wrote it, so the card Miro approves
    visibly carries the label rather than a promise that it will.

    `clips` posts a brand's own footage, so its rule is the campaigns' FTC
    disclosure instead (`disclosure_refusal`), never the AI label -- except
    a store promo (`STORE_PROMO`), which is ATLAS's own AI-made reel and
    carries the AI label like the stories account's."""
    if account == "clips" and content_kind != STORE_PROMO:
        why = disclosure_refusal(caption)
        return ActionResult(False, f"refused: {why}") if why else None
    if account != "stories" and content_kind != STORE_PROMO:
        return None
    from ..content.drafts import AI_LABEL
    if caption.rstrip().endswith(AI_LABEL):
        return None
    who = "a store promo" if content_kind == STORE_PROMO else "the stories account"
    return ActionResult(
        False, f"refused: {who} only posts AI-labelled "
               f"content — the caption must end with {AI_LABEL!r} "
               f"(content.drafts.AI_LABEL)")


def _kind_refusal(account: str, content_kind: str) -> str | None:
    """Miro's ruling: `main` posts only FlyCheap / travel content, `stories`
    only AI story reels. A reason when this account does not take this
    kind, None when it does."""
    allowed = ACCOUNT_KINDS.get(account, set())
    if content_kind in allowed:
        return None
    takes = ", ".join(sorted(allowed)) or "nothing yet"
    said = repr(content_kind) if content_kind else "no content kind"
    return (f"the {account} account only posts {takes} content, and this "
            f"card is {said}")


def vet_publish(params: dict) -> str | None:
    """`instagram.publish`'s `Action.vet`: the checks that need no network,
    asked by `approvals.propose()` before the card exists. `account`
    defaults to main exactly as the run signature does."""
    account = str(params.get("account") or "main")
    why = _kind_refusal(account, str(params.get("content_kind") or ""))
    if why:
        return why
    content_kind = str(params.get("content_kind") or "")
    why = _source_refusal(account, str(params.get("kind") or "image"),
                          str(params.get("image_url") or ""), content_kind)
    if why:
        return why
    refusal = _label_refusal(account, str(params.get("caption") or ""), content_kind)
    return refusal.detail if refusal is not None else None


# A Reel's container is processed by Instagram before it can be published;
# `media_publish` on an unfinished one fails. Polled until FINISHED, never
# published before, and given up on with a reason after five minutes.
REEL_POLL_S = 5.0
REEL_DEADLINE_S = 300.0


async def _await_container(c: httpx.AsyncClient, container: str,
                           auth: dict[str, str], token: str) -> str | None:
    """None once the container is FINISHED; otherwise the reason it will
    not be published (ERROR, EXPIRED, or still processing at the deadline)."""
    import asyncio
    import time
    started = time.monotonic()
    status = ""
    while True:
        r = await c.get(f"{GRAPH}/{container}", params={"fields": "status_code"},
                        headers=auth)
        _raise_for(r, token)
        status = str((r.json() or {}).get("status_code") or "")
        if status == "FINISHED":
            return None
        if status in ("ERROR", "EXPIRED"):
            return (f"Instagram could not process the reel (container "
                    f"{container} is {status}); nothing was published")
        if time.monotonic() - started >= REEL_DEADLINE_S:
            return (f"Instagram was still processing the reel after "
                    f"{REEL_DEADLINE_S:.0f}s (container {container} is "
                    f"{status or 'unreported'}); not published")
        await asyncio.sleep(REEL_POLL_S)


def topic_for_publish(params: dict) -> str:
    """Where an `instagram.publish` card lands: the stories account's in
    `instagram_stories` (#instagram-reels, via SLACK_INSTAGRAM_REELS_CHANNEL),
    the main account's on the namespace, exactly as before."""
    if params.get("account") == "clips":
        return "clips"
    return "instagram_stories" if params.get("account") == "stories" else ""


async def publish(caption: str, image_url: str, kind: str = "image",
                  account: str = "main", *, content_kind: str) -> ActionResult:
    why = _kind_refusal(account, content_kind) or _source_refusal(
        account, kind, image_url, content_kind)
    if why:
        return ActionResult(False, f"refused: {why}")
    refusal = _label_refusal(account, caption, content_kind)
    if refusal is not None:
        return refusal
    ig_id, token = _cfg(account)
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            auth = {"Authorization": f"Bearer {token}"}
            payload = {"caption": caption}
            payload["video_url" if kind == "reel" else "image_url"] = image_url
            if kind == "reel":
                payload["media_type"] = "REELS"
            made = await c.post(f"{GRAPH}/{ig_id}/media", data=payload,
                                headers=auth)
            _raise_for(made, token)
            container = made.json()["id"]
            if kind == "reel":
                unfinished = await _await_container(c, container, auth, token)
                if unfinished is not None:
                    return ActionResult(False, unfinished)
            done = await c.post(f"{GRAPH}/{ig_id}/media_publish",
                                data={"creation_id": container}, headers=auth)
            _raise_for(done, token)
            media_id = done.json()["id"]
    except httpx.HTTPError as exc:
        raise _unreachable(exc, token) from None
    return ActionResult(True, f"published {media_id} to the {account} account",
                        rollback_token=f"{account}:{media_id}")


async def publish_dry(caption: str, image_url: str, kind: str = "image",
                      account: str = "main", *, content_kind: str) -> ActionResult:
    why = _kind_refusal(account, content_kind) or _source_refusal(
        account, kind, image_url, content_kind)
    if why:
        return ActionResult(False, f"refused: {why}")
    try:
        _cfg(account)
    except NoAccount as exc:
        return ActionResult(False, str(exc))
    refusal = _label_refusal(account, caption, content_kind)
    if refusal is not None:
        return refusal
    if len(caption) > 2200:
        return ActionResult(False, "caption exceeds Instagram's 2200 characters")
    return ActionResult(
        True, f"would publish a {content_kind} {kind} to {account} with a "
              f"{len(caption)}-char caption")


async def unpublish(token: str) -> ActionResult:
    account, _, media_id = token.partition(":")
    if not account or not media_id:
        return ActionResult(False, f"malformed rollback token: {token!r}")
    _, tok = _cfg(account)
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.delete(f"{GRAPH}/{media_id}",
                               headers={"Authorization": f"Bearer {tok}"})
    except httpx.HTTPError as exc:
        return ActionResult(False, str(_unreachable(exc, tok)))
    if r.status_code != 200:
        return ActionResult(False, f"could not delete {media_id} on {account}: "
                                   f"Graph API HTTP {r.status_code}")
    return ActionResult(True, f"deleted {media_id} on {account}")


# --------------------------------------------------------------- moderation

async def hide_comment_dry(account: str, comment_id: str) -> ActionResult:
    try:
        _cfg(account)
    except NoAccount as exc:
        return ActionResult(False, str(exc))
    try:
        data = await _get(comment_id, account=account, fields="id,hidden")
    except GraphError as exc:
        return ActionResult(False, f"cannot read comment {comment_id}: {exc}")
    if data.get("hidden"):
        return ActionResult(False, f"comment {comment_id} is already hidden")
    return ActionResult(True, f"would hide comment {comment_id} on {account}")


async def hide_comment(account: str, comment_id: str) -> ActionResult:
    """Hide a comment on one of his own posts. Reversible (`?hide=false`
    unhides it), and invisible to anyone but the commenter — but REELS
    (`Da3bCbINuQf`) rules that hiding someone else's comment is still a
    decision about another person, so this is Tier ASK, not Tier AUTO. The
    rollback stays: reversibility is why it keeps one, not why it runs
    unattended."""
    await _post(comment_id, account=account, hide="true")
    return ActionResult(True, f"hid comment {comment_id} on the {account} account",
                        rollback_token=f"{account}:{comment_id}")


async def unhide_comment(token: str) -> ActionResult:
    account, _, comment_id = token.partition(":")
    if not account or not comment_id:
        return ActionResult(False, f"malformed rollback token: {token!r}")
    await _post(comment_id, account=account, hide="false")
    return ActionResult(True, f"unhid comment {comment_id} on {account}")


async def reply_comment_dry(account: str, comment_id: str, text: str) -> ActionResult:
    try:
        _cfg(account)
    except NoAccount as exc:
        return ActionResult(False, str(exc))
    if not text.strip():
        return ActionResult(False, "reply text is empty")
    return ActionResult(
        True, f"would reply to comment {comment_id} on {account}: "
              f"{text[:80]!r}")


async def reply_comment(account: str, comment_id: str, text: str) -> ActionResult:
    """A public reply under someone else's comment. Irreversible — Instagram
    has no unsend for a comment reply — so this is Tier ASK, never AUTO,
    whichever account it is on."""
    data = await _post(f"{comment_id}/replies", account=account, message=text)
    return ActionResult(True, f"replied to comment {comment_id} on {account}",
                        evidence={"reply_id": data.get("id")})


# After everything it needs is defined: funnel.py imports this package.
from . import funnel


class InstagramModule:
    name = "instagram"
    title = "Instagram"
    # Not gated: the accounts are independent, and one arriving before
    # the other should light up that one rather than keep both dark — same
    # reasoning as Stripe's two businesses and mail's several mailboxes.
    requires_env = ()
    optional_env = tuple(
        f"{prefix}{suffix}"
        for prefix in ACCOUNTS.values()
        for suffix in ("ACCOUNT_ID", "TOKEN")
    )

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        out = []
        for account in ACCOUNTS:
            out += [
                Check(f"{account}_token", _make_check(account, check_token),
                      timedelta(hours=6), Severity.HIGH,
                      f"{account}: long-lived token still valid",
                      baseline_sigma=None),
                Check(f"{account}_engagement", _make_check(account, check_engagement),
                      timedelta(hours=6), Severity.INFO,
                      f"{account}: daily reach vs baseline"),
                Check(f"{account}_comments_pending",
                      _make_check(account, check_comments_pending),
                      timedelta(hours=4), Severity.NORMAL,
                      f"{account}: comments awaiting a reply",
                      baseline_sigma=None),
                Check(f"{account}_overnight",
                      _make_check(account, check_overnight),
                      timedelta(hours=6), Severity.INFO,
                      f"{account}: overnight moderation summary for the brief",
                      baseline_sigma=None),
            ]
        out.append(Check("funnels", funnel.check_funnels, timedelta(minutes=30),
                         Severity.NORMAL,
                         "comment-keyword funnels: active, sends today, cap left, "
                         "last error", baseline_sigma=None))
        return tuple(out)

    async def command(self, text: str, *, channel: str, who: str):
        """`kernel.intent.act`'s code-parsed hook: `/atlas funnel ...`. The
        kernel does the acting (it is the one that may reach the executor);
        this module only reads and queues."""
        from ...kernel import funnels
        return await funnels.command(text, channel=channel, who=who)

    def actions(self):
        return (
            Action(
                name="instagram.publish",
                tier=Tier.ASK,              # never autonomous on a public feed
                summary="Publish a feed post or Reel to Instagram",
                run=publish, dry_run=publish_dry, rollback=unpublish,
                params={"caption": "post caption, max 2200 chars",
                        "image_url": "public URL of the image or video",
                        "kind": "'image' or 'reel'",
                        "account": "'main', 'stories' or 'clips' "
                                   "(default 'main')",
                        "content_kind": "what the post is: main takes "
                                        "'flycheap' or 'travel', stories "
                                        "takes 'ai_story', clips takes "
                                        "'campaign_clip'"},
                max_per_hour=2,
                vet=vet_publish, topic_for=topic_for_publish,
            ),
            Action(
                name="instagram.hide_comment",
                tier=Tier.ASK,      # reversible, but still a decision about
                                    # another person (REELS Da3bCbINuQf)
                summary="Hide a comment on one of his own posts",
                run=hide_comment, dry_run=hide_comment_dry, rollback=unhide_comment,
                params={"account": "'main', 'stories' or 'clips'",
                        "comment_id": "Graph API comment id"},
                max_per_hour=20,
            ),
            Action(
                name="instagram.reply_comment",
                tier=Tier.ASK,      # public and irreversible
                summary="Publicly reply to a comment",
                run=reply_comment, dry_run=reply_comment_dry, rollback=None,
                irreversible=True,
                params={"account": "'main', 'stories' or 'clips'",
                        "comment_id": "Graph API comment id",
                        "text": "the reply text"},
                max_per_hour=10,
            ),
            Action(
                name=funnel.SET,
                tier=Tier.ASK,      # the texts a stranger will read; once
                summary="Set a comment-keyword → DM funnel (the exact public "
                        "reply and DM text on the card); Miro approves once",
                run=funnel.funnel_set, dry_run=funnel.funnel_set_dry,
                rollback=funnel.funnel_set_rollback,
                params=dict(funnel.PARAMS_SET), max_per_hour=6,
                vet=funnel.vet_set, card_detail=funnel.card_detail_set,
                verbose_card=True, card_only=True,
            ),
            Action(
                name=funnel.PAUSE,
                tier=Tier.AUTO,     # ATLAS's own row; nothing leaves
                summary="Pause a comment-keyword funnel (its queued sends wait)",
                run=funnel.funnel_pause, dry_run=funnel.funnel_pause_dry,
                rollback=funnel.funnel_resume,
                params=dict(funnel.PARAMS_PAUSE), max_per_hour=20,
            ),
            Action(
                name=funnel.REPLY,
                tier=Tier.AUTO,     # deterministic: the approved texts, to a
                                    # person who commented the keyword, once
                summary="Reply publicly under a keyword comment and send its "
                        "author the approved DM as a private reply (the DM "
                        "cannot be recalled; the rollback hides the public "
                        "reply only)",
                run=funnel.funnel_reply, dry_run=funnel.funnel_reply_dry,
                # The one honest undo Graph offers: hide the public reply.
                # The DM cannot be recalled, and the rollback says so. Tier 1
                # all the same: the texts were approved once on the Tier 2
                # funnel_set card, are hash-checked on every send, and the
                # recipient asked by commenting the keyword (MODULE_AUDIT.md).
                rollback=funnel.funnel_reply_rollback,
                params=dict(funnel.PARAMS_REPLY), max_per_hour=60,
            ),
        )

    async def signals(self):
        out = []
        for account in ACCOUNTS:
            obs = await check_comments_pending(account)
            if obs.value and obs.value > 5:
                out.append(Signal(
                    title=f"Instagram ({account}) comments piling up",
                    body=f"{int(obs.value)} comments on recent posts have no "
                         f"reply. Engagement within the first hours "
                         f"materially affects reach.",
                    severity=Severity.NORMAL,
                ))
        return tuple(out)

    def brief(self) -> str:
        return f"Instagram: see engagement panel. {_overnight_line()}"


MODULE = InstagramModule()
