"""Deciding an ATLAS card from WhatsApp: one-time codes, a closed allowlist.

This module RECORDS a decision through the existing door, `approvals.decide`
(the same single conditional UPDATE Slack, the phone and the portal use). It
never calls the executor itself and never raises a card: invariant 1 holds, the
only code that acts is still `kernel/executor.py::execute`, reached from
`decide` exactly as for a Slack tap.

How a card is decided from here (CLAUDE.md invariants 2, 3, 4, 5, 6):

  * When a Tier 2 card is raised and WhatsApp is configured, one message goes
    to the owner (`mmt.notify_card`, a Tier 1 action: the only WhatsApp write).
    Every eligible Tier 2 card carries a one-time code and two
    tappable buttons (Approve / Decline, a Twilio quick-reply template whose
    button payload is `approve:<card>:<code>`); if the template is unavailable
    the plain text "Reply YES <code> or NO <code>" goes instead. A card in
    an ineligible one is told read-only: "decide it on the portal".
  * The code is random, 5 characters of an alphabet without look-alikes, stored
    only as a SHA-256, tied to the proposal id, single use, and it expires with
    the card. It is claimed by ONE atomic UPDATE that also requires the proposal
    to be still pending and unexpired, so a decision made in Slack first, an
    expired card or a second use all match nothing.
  * The inbound text is untrusted and is parsed by one strict regex, never by a
    model. A bare "yes", "ok" or "approve" without a code never decides.
  * Tier 3 / PERMANENT_DENY, money out, DNS, deletion, credential revocation and
    self-modification are refused here whatever a card says. the owner, 2026-11-11:
    "all actions are permissible", so every action whose tier is Tier 2 in the
    code registry (read live, never from a list kept here) can be decided from
    WhatsApp, new Tier 2 actions included. What stays refused: Tier 1, Tier 3
    and unregistered actions (deny by default), `NEVER_FROM_WHATSAPP`
    (`atlas.dev_request` and the self-modifying kinds), the deny fragments, a
    card whose text is cut off in the message, and a card whose parameters are
    missing, undeclared or not shown. There is no runtime override.
  * A tap on a button decides exactly the card whose id and code it carries. A
    bare "yes"/"no" NEVER decides anything: it is answered with the pending cards
    and how to answer (tap the button, or send YES <code>).
  * Five wrong codes in ten minutes lock WhatsApp approvals for thirty minutes
    (kept in the database, so a restart does not unlock it) and say so in the
    reply. Slack notices exist only with APPROVALS_SLACK=on (default off).
  * Silence is never consent: nothing here runs on a timer or a timeout.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from .audit import audit
from .contracts import Tier
from .executor import watch_only
from .state import execute_sql, fetch_all, fetch_one

log = logging.getLogger("atlas.wa_approvals")

# No 0/O, 1/I/L: a code read off a phone screen is typed back correctly.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LEN = 5
MAX_ATTEMPTS = 5                       # wrong codes ...
ATTEMPT_WINDOW = timedelta(minutes=10)  # ... inside this window ...
LOCK_FOR = timedelta(minutes=30)       # ... lock WhatsApp approvals this long

CHANNEL = "whatsapp"

# Never decided from WhatsApp, whatever the tier says (CLAUDE.md invariants 2, 3):
# `atlas.dev_request` is self-modification-adjacent and stays portal-only, and so
# do the kinds that change ATLAS itself, its safety ledger or an agent with a
# shell. A test pins the list.
NEVER_FROM_WHATSAPP: frozenset[str] = frozenset({
    "atlas.dev_request", "nightshift.merge", "agency.add_skill", "lessons.clear",
    "delegate.claude_code",
    # Hand a brief to a coding / browser agent: the brief IS the capability and the
    # WhatsApp card shows only its id, so the owner would approve text he never read.
    "agency.request_template", "agency.publish_doc", "agency.build_with_webild",
})
# Defence in depth: Tier 2 is approvable from WhatsApp by rule, so an action whose
# NAME says money, credentials, deletion, DNS or self-change stays portal-only
# until someone looks. Substring match, deliberately broad: a false hit costs a
# trip to the portal, a miss costs money or a secret.
DENY_FRAGMENTS: tuple[str, ...] = (
    "dns", "delete", "remove", "credential", "secret", "password", "token",
    "api_key", "revoke", "payment", "invoice", "charge", "checkout", "refund",
    "payout", "transfer", "withdraw", "purchase", "billing", "wire_", "dev_request",
    "self_modif", "purge", "grant_access", "disable_audit", "set_tier",
)
# Money-out verb + object anywhere in the name (no end anchor).
_MONEY_OUT = re.compile(
    r"(send|make|pay|issue|wire|submit|create|capture|collect|release|initiate)"
    r"[_.]?(payment|payout|invoice|charge|funds|money|transfer|refund)")


def portal_only(action: str, spec: object | None = None) -> bool:
    """True for an action that is never decided from WhatsApp: listed by name,
    named like money / credentials / deletion, or carrying the explicit
    `moves_money_or_credentials` flag on its spec, or classed as money by
    `board.MONEY` / `board.MONEY_NAMESPACES` (moves money or asks someone for it)."""
    from .board import MONEY, MONEY_NAMESPACES
    low = (action or "").casefold()
    return (low in NEVER_FROM_WHATSAPP or low in MONEY
            or low.split(".", 1)[0] in MONEY_NAMESPACES
            or _MONEY_OUT.search(low) is not None
            or any(f in low for f in DENY_FRAGMENTS)
            or getattr(spec, "moves_money_or_credentials", False) is True)


# The MMT page only, and a named post: without `entry_id` the run publishes "the
# next approved entry", which may not be the post the card showed.
def _mmt_named_post(p: dict) -> bool:
    return (p.get("page") == "mmt"
            and str(p.get("entry_id") or "").strip().lower() not in ("", "none", "null"))


PARAM_CHECKS: dict[str, Callable[[dict], bool]] = {
    "linkedin.publish_post": _mmt_named_post,
}


# Parameters whose VALUE is a stored-row id that the card's own renderer shows by
# resolving the row (the wording names the row, not the number). Explicit and per
# action: any other parameter must appear in the card text itself.
ID_SHOWN: dict[str, frozenset[str]] = {}


def _norm(v: Any) -> str:
    return " ".join(str(v).split()).casefold()


def params_shown(action: str, params: dict | None, spec: object | None,
                 shown: str) -> bool:
    """The generic parameter rule: what a card would run must be complete and
    visible. Every required parameter is present, none is undeclared, and EVERY
    given parameter's full value appears in the card text `shown` as a whole token
    (a longer value than the card shows is refused, never cut). Empty values are
    nothing to show; an id is exempt only through `ID_SHOWN`."""
    from .approvals import undeclared_params
    from .executor import missing_params
    given = dict(params or {})
    if missing_params(spec, given) or undeclared_params(spec, given):
        return False
    text = _norm(shown)
    exempt = ID_SHOWN.get(action, frozenset())
    for name, value in given.items():
        if value is None or value == "" or name in exempt:
            continue
        v = _norm(value)
        if not v:
            continue
        if re.search(r"(?<!\w)" + re.escape(v) + r"(?!\w)", text) is None:
            return False
    return True


# What approving does, in words built in code from the action: for anything that
# makes something public, changes who can see it, or relaunches it. Never from
# the reasoner's text. Others get "Approving runs: <summary>".
def _s(v: Any, n: int = 40) -> str:
    return " ".join(str(v if v not in (None, "") else "?").split())[:n]


EFFECTS: dict[str, Callable[[dict], str]] = {
    "academy.relaunch_tutorial": lambda p: (
        "Approving: the old public video is replaced by a new upload and goes "
        "PUBLIC again."),
    "academy.publish_tutorial": lambda p: (
        "Approving: the tutorial is posted to YouTube and goes PUBLIC."),
    "youtube.set_visibility": lambda p: (
        f"Approving: the video becomes {_s(p.get('privacy'))}"
        + (" and goes LIVE for everyone." if str(p.get("privacy")).lower() == "public"
           else ".")),
    "academy.upload_lesson": lambda p: (
        "Approving: a video is uploaded to YouTube. "
        + {"preview": "A short free PREVIEW goes PUBLIC, with the store link.",
           "short": "A vertical Short goes PUBLIC, with the store link."}.get(
            str(p.get("kind") or "full"),
            "A full lesson goes UNLISTED and is embedded only on the buyer's page.")
        + " Uploads of every kind share one daily cap."),
    "youtube.upload": lambda p: (
        "Approving: the video is uploaded to YouTube as PRIVATE. Nothing goes "
        "public."),
    "linkedin.publish_post": lambda p: (
        f"Approving: the post goes LIVE on the {_s(p.get('page'))} LinkedIn page "
        f"and is reshared to your profile."),
    "linkedin.reshare_post": lambda p: (
        "Approving: the post is reshared to your own LinkedIn profile (public)."),
    "instagram.publish": lambda p: "Approving: it goes LIVE on Instagram.",
    "content.publish_reel": lambda p: "Approving: the reel goes LIVE on Instagram.",
    "course.publish_post": lambda p: "Approving: the post goes LIVE on its channel.",
    "reddit.submit_post": lambda p: "Approving: the post goes LIVE on Reddit.",
    "reddit.submit_comment": lambda p: "Approving: the reply goes LIVE on Reddit.",
    "clips.publish_clip_youtube": lambda p: (
        "Approving: the clip is posted to the clips YouTube channel as a Short."),
    "clips.publish_promo": lambda p: (
        "Approving: the promo goes LIVE on the clips accounts."),
    "church.schedule_youtube_shorts": lambda p: (
        "Approving: the Shorts upload as PRIVATE, each made public on its date."),
    "church.schedule_facebook_reels": lambda p: (
        "Approving: the reels are scheduled to go LIVE on the church Facebook page."),
    "seo.publish_blog_post": lambda p: (
        "Approving: the post is written into the site build (not live yet)."),
    "seo.gsc_add_site": lambda p: (
        "Approving: the site is added to Google Search Console (removable)."),
    "seo.gsc_verify_site": lambda p: (
        "Approving: Google is asked to verify the site by the tag already on it."),
    "seo.gsc_submit_sitemap": lambda p: (
        "Approving: the sitemap is sent to Google Search Console (removable)."),
    "agency.deploy_demo": lambda p: "Approving: the client demo goes LIVE.",
    "agency.publish_vercel": lambda p: "Approving: the preview goes LIVE on Vercel.",
    "browser.submit_form": lambda p: (
        "Approving: the form is submitted on the live page. A sent form cannot be "
        "unsent."),
    "agency.publish_doc": lambda p: (
        "Approving: the coding agent publishes the document as a Docs artifact "
        "and reports its link."),
    "academy.publish_shop_remote": lambda p: (
        "Approving: the Academy store build goes LIVE on the store server."),
}


def effect_line(action: str, params: dict | None, spec: object | None = None) -> str:
    """One plain sentence on what approving does. Explicit for publishing,
    visibility and relaunch kinds; for the rest the action's own summary."""
    render = EFFECTS.get(action)
    if render is not None:
        try:
            line = " ".join(render(dict(params or {})).split())
            if line:
                return line
        except Exception:                                   # noqa: BLE001, S110
            pass
    summary = " ".join(str(getattr(spec, "summary", "") or "").split())[:160]
    return f"Approving runs: {summary or action}."


_REPLY = re.compile(rf"^\s*(yes|no)\s+([{ALPHABET}]{{4,6}})\s*$", re.IGNORECASE)
# What a button tap carries: `approve:<card id>:<code>` / `decline:<card id>:<code>`.
_PAYLOAD = re.compile(rf"^(approve|decline):([0-9]{{1,9}}):([{ALPHABET}]{{4,6}})$")
_LOOKS_LIKE_A_VERDICT = re.compile(
    r"^\W*(yes|no|y|n|ok|okay|yep|yeah|yup|nope|sure|approve[ds]?|decline[ds]?|"
    r"confirm|do it|go ahead)\b", re.IGNORECASE)
LIST_MAX = 5

NEED_CODE = ("\U0001F511 To decide a card, tap its Approve or Decline button, or reply with "
             "its code, like YES 4F2K or NO 4F2K. Nothing was changed.")
WRONG_CODE = "\U0001F914 That code doesn't match any card. Nothing was changed."
USED_CODE = "\u267B\uFE0F That code was already used. Nothing was changed."
RETIRED_CODE = ("\U0001F4CB That card was already decided somewhere else, or replaced. "
                "Nothing was changed.")
EXPIRED_CARD = "\u23F0 That card expired, nothing was done. Silence is never a yes."
NOT_FROM_WHATSAPP = ("\U0001F5A5\uFE0F That one has to be decided on the portal, not from here. "
                     "Nothing was changed.")
NEVER_REPLY = ("\U0001F6D1 Money, DNS, deletion, credential and self-changes are never "
               "approved from WhatsApp. Do it yourself in the portal. "
               "Nothing was changed.")
WATCH_ONLY_REPLY = ("\U0001F440 ATLAS is in watch-only mode, so nothing can run yet. The "
                    "card is still waiting and the code still works later. "
                    "Nothing was changed.")
CANT_CHECK = ("\U0001F937 I can't check that right now, so I won't guess. Use the portal. "
              "Nothing was changed.")
BAD_BUTTON = "\U0001F914 I didn't recognise that button. Nothing was changed."
NO_CARDS = "\U0001F4ED No card is waiting for you on WhatsApp right now. Nothing was changed."


def slack_on() -> bool:
    """Slack is NOT part of approvals unless APPROVALS_SLACK=on (default off)."""
    return os.getenv("APPROVALS_SLACK", "off").strip().lower() in (
        "1", "on", "true", "yes")


# ------------------------------------------------------------------ codes

def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


def code_hash(code: str) -> str:
    """SHA-256 of the upper-cased code. Only this is stored. The space is small
    by design (typed on a phone); the lockout, the owner-only gate and the code
    expiring with the card are what bound guessing."""
    return hashlib.sha256(f"atlas-wa-card:{code.strip().upper()}".encode()).hexdigest()


_CODE_SHAPED = re.compile(rf"\b(yes|no)\s+[{ALPHABET}]{{4,6}}\b", re.IGNORECASE)


def strip_code_shapes(text: str) -> str:
    """Outside text in a card must not carry a `YES <code>` of its own: a planted
    one costs nothing and five typed back lock WhatsApp approvals."""
    return _CODE_SHAPED.sub("[reply removed]", text or "")


def parse(text: str) -> tuple[str, str] | None:
    """`(verdict, code)` for a message that is exactly `YES <code>` or
    `NO <code>`, else None. Strict, whole message, no model."""
    match = _REPLY.match(text or "")
    if match is None:
        return None
    return ("approve" if match.group(1).lower() == "yes" else "decline",
            match.group(2).upper())


def parse_button(payload: str | None) -> tuple[str, int, str] | None:
    """`(verdict, card id, code)` for a quick-reply tap's `ButtonPayload`, else
    None. One strict regex over the whole payload, no model."""
    match = _PAYLOAD.match((payload or "").strip())
    if match is None:
        return None
    return match.group(1), int(match.group(2)), match.group(3)


def looks_like_a_verdict(text: str) -> bool:
    """A short message that reads like an answer to a card but is not the strict
    form: it gets "I need the code", never a guess."""
    words = (text or "").split()
    return 0 < len(words) <= 3 and _LOOKS_LIKE_A_VERDICT.match(text) is not None


def enabled() -> bool:
    """WhatsApp cards are on only when the owner number and the Twilio sender
    are all set. Off, every function here that touches a card does nothing."""
    from ..modules.mmt import notify
    number, _ = notify.owner_number()
    return number is not None and notify.sender_problem() is None


# ------------------------------------------------------------- eligibility

def _params_of(raw: Any) -> dict:
    import json
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            got = json.loads(raw or "{}")
        except ValueError:
            return {}
        return got if isinstance(got, dict) else {}
    return {}


def eligibility(action: str, params: dict | None, *, dry_ok: bool | None = None,
                ) -> tuple[bool, str]:
    """May a card for `action` be decided from WhatsApp? `(True, "")` or
    `(False, reply)`. Read from the live registry at the time of asking (send
    time and again at decision time), never from what the code row remembers:
    a tier raised in code since the card was sent still wins."""
    from .permissions import denial_reason
    from .registry import REGISTRY
    if denial_reason(action) is not None:
        return False, NEVER_REPLY
    decision = REGISTRY.decisions.get(action)
    if decision is None or not decision.allowed or decision.tier is Tier.NEVER:
        return False, NEVER_REPLY
    if decision.tier is not Tier.ASK:
        return False, NOT_FROM_WHATSAPP
    spec = REGISTRY.actions.get(action)
    if portal_only(action, spec):
        return False, NOT_FROM_WHATSAPP
    if getattr(spec, "principal_only", False):
        # The executor would refuse a `whatsapp:` origin AFTER the claim and
        # burn the card to failed: refuse before.
        return False, NOT_FROM_WHATSAPP
    if dry_ok is False:
        return False, NOT_FROM_WHATSAPP
    try:
        check = PARAM_CHECKS.get(action)
        if check is not None and not check(params or {}):
            return False, NOT_FROM_WHATSAPP
        # The message shows `board.what_of(...)` cut at CARD_WHAT_MAX. A decision is
        # not made on words the owner cannot read in full: longer is portal-only.
        from ..modules.mmt.notify import CARD_WHAT_MAX
        from .board import what_of
        what = " ".join(what_of(action, dict(params or {}), spec).split())
        if len(what) > CARD_WHAT_MAX:
            return False, NOT_FROM_WHATSAPP
        # Matched against what the owner reads: the card text after the code-shape
        # strip, and only text RENDERED from the params (a WHAT / EFFECTS line).
        # The spec summary fallback is fixed words: a short value that happens to
        # be one of them would look "shown" while the card never showed it.
        from .board import WHAT
        shown = ""
        render = WHAT.get(action)
        if render is not None:
            shown = strip_code_shapes(" ".join(str(render(dict(params or {}))).split()))
        if action in EFFECTS:
            shown += " " + strip_code_shapes(effect_line(action, params, spec))
        if not params_shown(action, params, spec, shown):
            return False, NOT_FROM_WHATSAPP
    except Exception:                                       # noqa: BLE001
        return False, NOT_FROM_WHATSAPP
    return True, ""


# ----------------------------------------------------------------- storage

async def create_code(pid: int, action: str, expires_at: datetime) -> str | None:
    """A fresh single-use code for card `pid`, or None if it could not be
    stored (a hash collision is retried with a new code)."""
    for _ in range(6):
        code = new_code()
        row = await fetch_one(
            """INSERT INTO whatsapp_codes
                    (proposal_id, code_hash, action, expires_at, status)
                    VALUES ($1, $2, $3, $4, 'open')
               ON CONFLICT DO NOTHING RETURNING id""",
            pid, code_hash(code), action, expires_at)
        if row is not None:
            return code
    return None


async def retire(pid: int) -> None:
    """The card was decided (or replaced): its open code stops working. A no-op
    when it has none. Best effort: the claim in `handle_reply` also needs the
    proposal to be pending, so a missed retire cannot approve anything."""
    try:
        await execute_sql(
            "UPDATE whatsapp_codes SET status='retired' "
            "WHERE proposal_id=$1 AND status='open'", pid)
    except Exception as exc:                                # noqa: BLE001
        log.warning("card #%s: code not retired (%s)", pid, type(exc).__name__)


# ----------------------------------------------------------------- lockout

async def locked_until(now: datetime) -> datetime | None:
    row = await fetch_one(
        "SELECT until_at FROM whatsapp_attempts WHERE kind='lock' "
        "AND until_at > $1 ORDER BY until_at DESC LIMIT 1", now)
    return None if row is None else row["until_at"]


async def _wrong_code(now: datetime) -> bool:
    """Record one wrong code. True when that was the one that locks."""
    await execute_sql(
        "INSERT INTO whatsapp_attempts (at, kind) VALUES ($1, 'wrong')", now)
    row = await fetch_one(
        "SELECT count(*) AS n FROM whatsapp_attempts WHERE kind='wrong' "
        "AND at > $1", now - ATTEMPT_WINDOW)
    if int((row or {}).get("n", 0)) < MAX_ATTEMPTS:
        return False
    await execute_sql(
        "INSERT INTO whatsapp_attempts (at, kind, until_at) "
        "VALUES ($1, 'lock', $2)", now, now + LOCK_FOR)
    await audit("whatsapp.locked", "whatsapp", {
        "until": (now + LOCK_FOR).isoformat(), "wrong_codes": MAX_ATTEMPTS})
    await tell_slack(
        "WhatsApp approvals locked",
        f"{MAX_ATTEMPTS} wrong approval codes arrived from the owner number in "
        f"{int(ATTEMPT_WINDOW.total_seconds() // 60)} minutes. WhatsApp approvals "
        f"are off for {int(LOCK_FOR.total_seconds() // 60)} minutes. Decide cards "
        f"in the portal. If this was not you, someone has the phone or the number.")
    return True


async def tell_slack(title: str, body: str) -> None:
    """An optional Slack copy, only with APPROVALS_SLACK=on (default off): WhatsApp
    and the portal are where approvals live. Best effort, never raises."""
    if not slack_on():
        return
    try:
        from .comms import Outbound, notify
        from .contracts import Severity
        await notify(Outbound(title=title, body=body, severity=Severity.NORMAL,
                              topic="decisions", kind="alert"))
    except Exception as exc:                                # noqa: BLE001
        log.warning("slack notice not sent: %s", type(exc).__name__)


# ----------------------------------------------------------------- inbound

async def handle_reply(verdict: str, code: str, sid: str,
                       now: datetime | None = None, *,
                       pid: int | None = None) -> str:
    """Answer `YES|NO <code>` (or a button tap, which also names the card `pid`)
    from the verified owner. Returns the one reply line. Every refusal changes
    nothing. Called only from the signed, owner-only, replay-checked webhook
    (`api/whatsapp.py`)."""
    now = now or datetime.now(UTC)
    try:
        until = await locked_until(now)
    except Exception as exc:                                # noqa: BLE001
        log.warning("lockout unreadable: %s", type(exc).__name__)
        return CANT_CHECK
    if until is not None:
        return _locked_reply(until)
    try:
        row = await fetch_one(
            "SELECT id, proposal_id, action, status, expires_at "
            "FROM whatsapp_codes WHERE code_hash=$1", code_hash(code))
        # A tap names its card as well as its code: a code that belongs to another
        # card is as wrong as one that matches nothing.
        if row is not None and pid is not None and int(row["proposal_id"]) != pid:
            row = None
        if row is None:
            locked = await _wrong_code(now)
            await audit("whatsapp.wrong_code", "whatsapp", {"sid": sid})
            return (f"{WRONG_CODE} Too many wrong codes: WhatsApp approvals "
                    f"are off for {int(LOCK_FOR.total_seconds() // 60)} minutes."
                    if locked else WRONG_CODE)
    except Exception as exc:                                # noqa: BLE001
        log.warning("whatsapp decision not read: %s", type(exc).__name__)
        return CANT_CHECK
    return await _decide_row(row, verdict, sid, now)


def _locked_reply(until: datetime) -> str:
    return (f"WhatsApp approvals are locked until {until:%H:%M} UTC after "
            f"too many wrong codes. Use the portal. Nothing was changed.")


async def _decide_row(row: Any, verdict: str, sid: str, now: datetime) -> str:
    """The code row is known: run every gate, make the one claim, record the
    decision through `approvals.decide`."""
    pid = int(row["proposal_id"])
    try:
        if row["status"] == "used":
            return USED_CODE
        if row["status"] != "open":
            return await _closed_reason(pid)
        if row["expires_at"] <= now:
            return EXPIRED_CARD
        card = await fetch_one(
            "SELECT action, params, tier, status, dry_run_ok FROM proposals "
            "WHERE id=$1", pid)
        if card is None or card["status"] != "pending":
            await retire(pid)
            return await _closed_reason(pid)
        ok, why = eligibility(str(card["action"]), _params_of(card["params"]),
                              dry_ok=card.get("dry_run_ok"))
        if not ok:
            await audit("whatsapp.refused", str(pid), {
                "action": card["action"], "channel": CHANNEL, "sid": sid})
            return why
        if watch_only():
            return WATCH_ONLY_REPLY
        # The one claim: this code, still open and unexpired, for a proposal
        # still pending and unexpired. A decision made elsewhere first, expiry, or
        # a second use all match nothing.
        claimed = await fetch_one(
            """UPDATE whatsapp_codes SET status='used', used_at=$2, message_sid=$3
                WHERE id=$1 AND status='open' AND expires_at > $2
                  AND EXISTS (SELECT 1 FROM proposals p
                               WHERE p.id = whatsapp_codes.proposal_id
                                 AND p.status='pending' AND p.expires_at > $2)
               RETURNING proposal_id""", int(row["id"]), now, sid)
    except Exception as exc:                                # noqa: BLE001
        log.warning("whatsapp decision not read: %s", type(exc).__name__)
        return CANT_CHECK
    if claimed is None:
        return await _why_not(int(row["id"]), now)

    from .approvals import decide
    try:
        result = await decide(pid, verdict, via=f"{CHANNEL}:{sid}")
    except Exception as exc:                                # noqa: BLE001
        log.warning("whatsapp decision failed: %s", type(exc).__name__)
        await audit("whatsapp.decision_failed", str(pid), {
            "verdict": verdict, "channel": CHANNEL, "sid": sid})
        return ("Something went wrong deciding that card. Check it on the portal. "
                "Nothing more was done.")
    await audit("whatsapp.decision", str(pid), {
        "verdict": verdict, "outcome": result.outcome, "ok": result.ok,
        "channel": CHANNEL, "sid": sid})
    await tell_slack(
        f"#{pid} decided from WhatsApp",
        f"Card #{pid} was {'approved' if verdict == 'approve' else 'declined'} "
        f"by the owner on WhatsApp. {result.receipt()}")
    return result.receipt()


async def _closed_reason(pid: int) -> str:
    """A code that is not open: an expired card says so (a late tap), anything
    else was decided elsewhere or replaced."""
    try:
        row = await fetch_one(
            "SELECT status, expires_at FROM proposals WHERE id=$1", pid)
    except Exception:                                       # noqa: BLE001
        return RETIRED_CODE
    if row is not None and row["status"] == "expired":
        return EXPIRED_CARD
    return RETIRED_CODE


def _ago(now: datetime, then: datetime) -> str:
    minutes = max(0, int((now - then).total_seconds() // 60))
    return "just now" if minutes < 1 else f"{minutes} min ago"


async def waiting_cards(now: datetime) -> list[dict[str, Any]]:
    """The cards that can be decided from WhatsApp right now: an open code, the
    card still pending and unexpired. Newest first."""
    rows = await fetch_all(
        """SELECT c.id, c.proposal_id, c.action, c.status, c.expires_at,
                  c.created_at
             FROM whatsapp_codes c JOIN proposals p ON p.id = c.proposal_id
            WHERE c.status = 'open' AND c.expires_at > $1
              AND p.status = 'pending' AND p.expires_at > $1
            ORDER BY c.id DESC LIMIT 20""", now)
    return [dict(r) for r in rows or []]


async def handle_bare(now: datetime | None = None) -> str:
    """A message like "yes", "no", "ok", "approve": NEVER a decision, however many
    cards wait. It is answered with the pending cards and how to answer. The
    one-time code (typed, or carried by a button) is the only proof."""
    now = now or datetime.now(UTC)
    try:
        until = await locked_until(now)
        if until is not None:
            return _locked_reply(until)
        cards = await waiting_cards(now)
    except Exception as exc:                                # noqa: BLE001
        log.warning("waiting cards unreadable: %s", type(exc).__name__)
        return CANT_CHECK
    if not cards:
        return NO_CARDS
    lines = [("A plain yes or no decides nothing. Tap the Approve or Decline button "
              "on the card, or send YES or NO followed by its code.")]
    lines.append(f"{len(cards)} card{'s' if len(cards) != 1 else ''} waiting:")
    for card in cards[:LIST_MAX]:
        lines.append(f"#{card['proposal_id']} sent {_ago(now, card['created_at'])}")
    if len(cards) > LIST_MAX:
        lines.append(f"... and {len(cards) - LIST_MAX} more")
    lines.append("Or decide on the portal. Nothing was changed.")
    return "\n".join(lines)


async def _why_not(code_id: int, now: datetime) -> str:
    """The claim matched nothing: say which of the reasons it was."""
    try:
        row = await fetch_one(
            "SELECT status, expires_at FROM whatsapp_codes WHERE id=$1", code_id)
    except Exception:                                       # noqa: BLE001
        return CANT_CHECK
    if row is not None and row["status"] == "used":
        return USED_CODE
    if row is not None and row["status"] == "open" and row["expires_at"] <= now:
        return EXPIRED_CARD
    return RETIRED_CODE
