"""A "fix X" in the verified owner chat becomes a card for a developer session.

Atlas never edits his own code, config or prompts from chat: that is Tier 3
self-modification and stays permanently denied (CLAUDE.md invariant 3). What a
message like "fix the quiet hours", "change the call limit" or "make him stop
repeating himself" may do is raise ONE pending Tier 2 card for `atlas.dev_request`.
Approving that card (in Slack, by an approver) only records the decision and stores
the request text as a task note for a developer session to pick up through git
review. It applies nothing.

  * Parsing is one strict regex at the start of the message. No model reads it, and
    no model acts on it (invariant 2).
  * The text is capped (`TEXT_MAX`, refused rather than trimmed, because a cut
    request can mean something else), refused when it looks like a secret
    (no secrets anywhere), and shown on the card wrapped as untrusted
    (invariant 4).
  * Deduped per text hash while its card is pending; at most `DEV_REQUESTS_PER_DAY`
    (5) new cards a New York day.
  * This module proposes and nothing else: it never calls `approvals.decide` or
    `executor.execute`, and `atlas.dev_request` has no WhatsApp code
    (`wa_approvals.NEVER_FROM_WHATSAPP`), so chat cannot approve it.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import UTC, datetime

log = logging.getLogger("atlas.dev_requests")

ACTION = "atlas.dev_request"
TEXT_MAX = 500
DEFAULT_PER_DAY = 5

_FIX = re.compile(
    r"^\W*(?:(?:hey|hi|please|pls|atlas)[\s,:]+)*"
    r"(?:(?:can|could|would|will) you\s+)?(?:please\s+)?"
    r"(?:fix|change|tweak|adjust|modify|rewrite|reconfigure"
    r"|make (?:him|it|atlas)"
    r"|stop (?:him|it|atlas) from"
    r"|don'?t let (?:him|it|atlas))\b", re.IGNORECASE)

# Shapes of credentials. Deliberately broad: a false positive costs one re-typed
# sentence, a false negative puts a key in the database and on a Slack card.
_SECRET = re.compile(
    r"sk-[A-Za-z0-9_-]{16,}|\bAC[0-9a-fA-F]{32}\b|xox[abprs]-[A-Za-z0-9-]{6,}"
    r"|gh[pousr]_[A-Za-z0-9]{16,}|\bAKIA[0-9A-Z]{16}\b|-----BEGIN"
    r"|(?:pass(?:word|wd)?|secret|token|api[_ -]?key|auth)\s*[:=]\s*\S{6,}"
    r"|\b[A-Fa-f0-9]{40,}\b|\b[A-Za-z0-9+/_-]{48,}={0,2}(?=\s|$)", re.IGNORECASE)

TOO_LONG = (f"That's longer than the {TEXT_MAX} characters I can note. "
            "Say it shorter and I'll note it. Nothing was changed.")
SECRET_SEEN = ("That looks like it contains a secret, so I didn't record it. "
               "Say it again without the secret. Nothing was changed.")
COULD_NOT = "I couldn't note that just now. Nothing was changed."


def per_day() -> int:
    try:
        return max(0, int(os.getenv("DEV_REQUESTS_PER_DAY", str(DEFAULT_PER_DAY))))
    except ValueError:
        return DEFAULT_PER_DAY


def is_dev_request(text: str) -> bool:
    return _FIX.match(text or "") is not None


def clean(text: str) -> str:
    return " ".join((text or "").split())


def secret_shaped(text: str) -> bool:
    return _SECRET.search(text or "") is not None


def text_hash(text: str) -> str:
    return hashlib.sha256(clean(text).casefold().encode("utf-8")).hexdigest()


def problem(text: str) -> str | None:
    """A pure reason the request cannot be queued, else None (the action's `vet`)."""
    body = clean(text)
    if not body:
        return "the request is empty"
    if len(body) > TEXT_MAX:
        return f"the request is over {TEXT_MAX} characters"
    if secret_shaped(body):
        return "the request looks like it contains a secret"
    return None


async def _pending_for(dedup_key: str) -> int | None:
    from .state import fetch_one
    row = await fetch_one(
        "SELECT id FROM proposals WHERE dedup_key = $1 AND status = 'pending' "
        "AND expires_at > now() ORDER BY created_at DESC LIMIT 1", dedup_key)
    return None if row is None else int(row["id"])


async def _today_count() -> int:
    from .state import fetch_one
    from .whatsapp_daily import NY
    midnight = datetime.now(NY).replace(hour=0, minute=0, second=0, microsecond=0)
    row = await fetch_one(
        "SELECT count(*) AS n FROM proposals WHERE action = $1 AND created_at >= $2",
        ACTION, midnight.astimezone(UTC))
    return int(row["n"]) if row else 0


def reply_for(card_id: int) -> str:
    return (f"Noted as card #{card_id}. I can't change myself; a developer session "
            "does it after you approve.")


async def note(text: str) -> str:
    """The plain reply to a fix request from the verified owner. Raises a card;
    decides and runs nothing."""
    from . import approvals
    body = clean(text)
    if len(body) > TEXT_MAX:
        return TOO_LONG
    if secret_shaped(body):
        return SECRET_SEEN
    digest = text_hash(body)
    key = f"devreq:{digest[:32]}"
    try:
        existing = await _pending_for(key)
        if existing is not None:
            return f"Already noted as card #{existing}. Nothing new was added."
        if await _today_count() >= per_day():
            return ("I've noted as many requests as I take today. "
                    "Say it again tomorrow. Nothing was changed.")
        card = await approvals.propose(
            ACTION, {"request": body, "request_sha256": digest},
            "the owner asked in chat for a change to Atlas; a developer session does "
            "it after approval",
            dedup_key=key,
            rollback_plan="nothing is applied; approval only records the decision "
                          "and a task note")
    except Exception as exc:                                # noqa: BLE001
        log.warning("dev request not queued: %s", type(exc).__name__)
        return COULD_NOT
    return reply_for(card)
