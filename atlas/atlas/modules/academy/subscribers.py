"""Free updates for storefront visitors: an email address and nothing else.

A visitor types an address on the store (POST /public/academy/subscribe,
atlas/api/academy.py). No purchase, no password, no account.

* **Double opt-in.** A signup is `pending` and never counts as a subscriber
  until its owner clicks the link in the confirmation email (silence is never
  consent). A pending row not confirmed within 7 days of the first signup is deleted,
  and it gets at most MAX_ATTEMPTS confirmation mails in that time.
* **Nothing but the address.** One row: the lowercased email, a status, when
  it was created / confirmed / unsubscribed, and the page it came from. No
  name, no client address, no browser details.
* **Tokens are never stored raw.** The confirm token is random; only its
  SHA-256 is kept, with an expiry. The unsubscribe link is an HMAC of the row
  id (domain-separated key, like `delivery`): it is stored nowhere and does not
  expire, so an unsubscribe is always honoured.
* **Sending is the executor's.** The signup only queues a confirmation
  (`mail_due`); `academy.send_subscribe_confirmation` (below) is the one
  action that mails, through the same SMTP path as `academy.resend_access`,
  driven by `kernel/academy_subscribe.py`. Nothing here sends a newsletter:
  `confirmed_subscribers` only lists who may be written to, and a later
  newsletter must be its own Tier 2 card.
* **The address is untrusted** and is never put in a prompt, a log line or a
  card (`mask` shows two letters and the domain).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import Action, ActionResult, Tier
from .stripe_read import normalise_email

log = logging.getLogger("atlas.academy.subscribers")

SUBSCRIBE_PATH = "/public/academy/subscribe"
CONFIRM_PATH = "/public/academy/subscribe/confirm"
UNSUBSCRIBE_PATH = "/public/academy/unsubscribe"
# Named without the word "subscribe": permissions.py denies that word outright
# (a recurring charge), and a free signup is not one.
ACTION = "academy.send_signup_confirmation"

PENDING_TTL = timedelta(days=7)          # a signup nobody confirms is deleted
RESEND_AFTER = timedelta(hours=24)       # one confirmation mail per address a day
MAX_ATTEMPTS = 3                         # SMTP failures before the mail is dropped
SOURCES = frozenset({"hero", "footer", "page"})
DEFAULT_SOURCE = "site"
SUBJECT = "Confirm your ATLAS Academy updates"
_DOMAIN = b"atlas-academy-subscribe-v1"
MAIL_ENV = "ACADEMY_SUBSCRIBE_MAIL"
CAP_ENV = "ACADEMY_SUBSCRIBE_DAILY_CAP"


# ------------------------------------------------------------------ input

def clean_email(raw: object) -> str:
    """The address lowercased, or "" when it is not one. Strict: no spaces,
    no control characters, no display name, at most 254 characters."""
    if isinstance(raw, str) and len(raw) > 320:
        return ""
    return normalise_email(raw)


def clean_source(raw: object) -> str:
    return raw if isinstance(raw, str) and raw in SOURCES else DEFAULT_SOURCE


def mask(email: str) -> str:
    name, _, domain = email.partition("@")
    return f"{name[:2]}***@{domain}" if domain else "***"


# ------------------------------------------------------------------ tokens

def signing_key() -> bytes | None:
    """The unsubscribe key: ACADEMY_DOWNLOAD_KEY, else SESSION_SECRET (each
    domain-separated), else None -- and then no mail is sent, because a mail
    without a working unsubscribe link is not allowed out."""
    secret = (os.getenv("ACADEMY_DOWNLOAD_KEY", "").strip()
              or os.getenv("SESSION_SECRET", "").strip())
    if not secret:
        return None
    return hmac.new(secret.encode(), _DOMAIN, hashlib.sha256).digest()


def new_confirm_token() -> tuple[str, str]:
    """(raw token for the email, sha256 hex to store)."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _unsub_sig(key: bytes, sub_id: int) -> str:
    return hmac.new(key, f"unsub|{int(sub_id)}".encode(), hashlib.sha256).hexdigest()


def unsubscribe_token(sub_id: int, *, key: bytes | None = None) -> str | None:
    key = key if key is not None else signing_key()
    if key is None:
        return None
    return f"u1.{int(sub_id)}.{_unsub_sig(key, sub_id)}"


def verify_unsubscribe(token: str, *, key: bytes | None = None) -> int | None:
    """The subscriber id a genuine unsubscribe token names, else None."""
    key = key if key is not None else signing_key()
    parts = (token or "").split(".")
    if key is None or len(token or "") > 120 or len(parts) != 3 or parts[0] != "u1":
        return None
    if not parts[1].isdigit() or len(parts[1]) > 18:
        return None
    if not hmac.compare_digest(parts[2], _unsub_sig(key, int(parts[1]))):
        return None
    return int(parts[1])


def _base() -> str:
    from . import delivery
    return delivery.api_base()


def confirm_url(raw: str) -> str:
    return f"{_base()}{CONFIRM_PATH}?t={raw}"


def unsubscribe_url(sub_id: int) -> str | None:
    token = unsubscribe_token(sub_id)
    return f"{_base()}{UNSUBSCRIBE_PATH}?t={token}" if token else None


# ------------------------------------------------------------------ the store

class Store:
    """The SQL, one atomic statement per step. Tests substitute a fake with
    the same methods (`use_store`); tests/test_schema_lint.py checks the SQL
    against db/schema.sql."""

    async def purge(self, now: datetime) -> None:
        from ...kernel.state import execute_sql
        await execute_sql(
            "DELETE FROM academy_subscribers WHERE status = 'pending' "
            "AND created_at < $1", now - PENDING_TTL)

    async def signup(self, email: str, source: str, now: datetime) -> None:
        """Insert, or -- for a pending address whose last mail is over a day
        old and that has had fewer than MAX_ATTEMPTS mails -- queue one more
        confirmation. `created_at` is never moved and the attempts are never
        reset, so a stranger re-typing someone's address cannot keep the row
        alive past its 7 days or mail it more than MAX_ATTEMPTS times. A
        confirmed or unsubscribed address is left exactly as it is."""
        from ...kernel.state import execute_sql
        await execute_sql(
            """INSERT INTO academy_subscribers (email, source, created_at)
                VALUES ($1, $2, $4)
                ON CONFLICT (email) DO UPDATE SET
                    mail_due = CASE WHEN academy_subscribers.status = 'pending'
                                     AND academy_subscribers.mail_attempts < $5
                                     AND (academy_subscribers.confirm_sent_at IS NULL
                                          OR academy_subscribers.confirm_sent_at < $3)
                               THEN TRUE ELSE academy_subscribers.mail_due END""",
            email, source, now - RESEND_AFTER, now, MAX_ATTEMPTS)

    async def due(self, limit: int) -> list[int]:
        from ...kernel.state import fetch_all
        rows = await fetch_all(
            "SELECT id FROM academy_subscribers WHERE status = 'pending' "
            "AND mail_due ORDER BY created_at LIMIT $1", limit)
        return [int(r["id"]) for r in rows or []]

    async def peek(self, sub_id: int) -> dict[str, Any] | None:
        from ...kernel.state import fetch_one
        row = await fetch_one(
            "SELECT id, email, status, mail_due FROM academy_subscribers "
            "WHERE id = $1", int(sub_id))
        return dict(row) if row else None

    async def claim(self, sub_id: int, token_hash: str, expires: datetime
                    ) -> tuple[str, datetime] | None:
        """Take the one send of this signup: (email, link expiry), or None when
        it is not pending or nothing is queued (so a second claim cannot send
        twice). The link never outlives the row: it expires at the earlier of
        `expires` and created_at + PENDING_TTL, when purge deletes the row."""
        from ...kernel.state import fetch_one
        row = await fetch_one(
            """UPDATE academy_subscribers
                  SET mail_due = FALSE, mail_attempts = mail_attempts + 1,
                      confirm_token_hash = $2,
                      confirm_expires_at = LEAST($3, created_at + $4::interval)
                WHERE id = $1 AND status = 'pending' AND mail_due
            RETURNING email, confirm_expires_at""",
            int(sub_id), token_hash, expires, PENDING_TTL)
        return (str(row["email"]), row["confirm_expires_at"]) if row else None

    async def sent(self, sub_id: int, now: datetime) -> None:
        from ...kernel.state import execute_sql
        await execute_sql("UPDATE academy_subscribers SET confirm_sent_at = $2 "
                          "WHERE id = $1", int(sub_id), now)

    async def failed(self, sub_id: int) -> None:
        from ...kernel.state import execute_sql
        await execute_sql(
            "UPDATE academy_subscribers SET mail_due = (mail_attempts < $2) "
            "WHERE id = $1 AND status = 'pending'", int(sub_id), MAX_ATTEMPTS)

    async def sent_since(self, since: datetime) -> int:
        from ...kernel.state import fetch_one
        row = await fetch_one("SELECT count(*) AS n FROM academy_subscribers "
                              "WHERE confirm_sent_at > $1", since)
        return int(row["n"]) if row else 0

    async def confirm(self, token_hash: str, now: datetime) -> bool:
        from ...kernel.state import fetch_one
        row = await fetch_one(
            """UPDATE academy_subscribers
                  SET status = 'confirmed', confirmed_at = $2, mail_due = FALSE,
                      confirm_token_hash = NULL, confirm_expires_at = NULL
                WHERE confirm_token_hash = $1 AND status = 'pending'
                  AND confirm_expires_at > $2
            RETURNING id""", token_hash, now)
        return row is not None

    async def unsubscribe(self, sub_id: int, now: datetime) -> bool:
        """Any status to unsubscribed, token dropped, nothing queued. The row
        stays so the address is never mailed again."""
        from ...kernel.state import fetch_one
        row = await fetch_one(
            """UPDATE academy_subscribers
                  SET status = 'unsubscribed', unsubscribed_at = $2,
                      mail_due = FALSE, confirm_token_hash = NULL,
                      confirm_expires_at = NULL
                WHERE id = $1 AND status <> 'unsubscribed'
            RETURNING id""", int(sub_id), now)
        return row is not None

    async def exists(self, sub_id: int) -> bool:
        return await self.peek(sub_id) is not None

    async def confirmed(self, limit: int, after_id: int) -> list[dict[str, Any]]:
        from ...kernel.state import fetch_all
        rows = await fetch_all(
            "SELECT id, email FROM academy_subscribers WHERE status = 'confirmed' "
            "AND id > $1 ORDER BY id LIMIT $2", int(after_id), limit)
        return [dict(r) for r in rows or []]


_store: Any = Store()


def use_store(new: Any) -> Any:
    """Swap the store (tests). Returns the previous one."""
    global _store
    old, _store = _store, new
    return old


def _now() -> datetime:
    return datetime.now(UTC)


# ------------------------------------------------------------------ the flows

async def signup(email: str, source: str = DEFAULT_SOURCE, *,
                 now: datetime | None = None) -> None:
    """Record a signup for an already-cleaned address. Outwardly it does the
    same thing whoever the address is: callers answer identically."""
    now = now or _now()
    await _store.purge(now)
    await _store.signup(email, clean_source(source), now)


async def confirm(raw_token: str, *, now: datetime | None = None) -> bool:
    """True when the token is genuine, unexpired and unused: the address is
    now confirmed. Expired, used, tampered and unknown tokens are all False."""
    if not isinstance(raw_token, str) or not (20 <= len(raw_token) <= 100):
        return False
    return await _store.confirm(hash_token(raw_token), now or _now())


async def unsubscribe(token: str) -> bool:
    """True when the token is genuine (even if the address had already
    unsubscribed or was deleted): an unsubscribe is always honoured."""
    sub_id = verify_unsubscribe(token)
    if sub_id is None:
        return False
    await _store.unsubscribe(sub_id, _now())
    return True


async def confirmed_subscribers(limit: int = 500, after_id: int = 0
                                ) -> list[dict[str, Any]]:
    """Who may be written to: confirmed, not unsubscribed, in id order, each
    {"id", "email", "unsubscribe_url"}. A pending address never appears. A
    helper only: nothing here sends, and a newsletter is a separate Tier 2
    card that must put each unsubscribe_url in its message."""
    rows = await _store.confirmed(max(1, min(int(limit), 5000)), int(after_id))
    return [{"id": r["id"], "email": r["email"],
             "unsubscribe_url": unsubscribe_url(int(r["id"]))} for r in rows]


# ------------------------------------------------------------------ the action

def mail_enabled() -> bool:
    return os.getenv(MAIL_ENV, "on").strip().lower() not in ("0", "off", "false", "no")


def daily_cap() -> int:
    try:
        return max(0, int(os.getenv(CAP_ENV, "100")))
    except ValueError:
        return 100


def vet(params: dict[str, Any]) -> str | None:
    sid = params.get("subscriber_id")
    if not isinstance(sid, int) or isinstance(sid, bool) or sid < 1:
        return "subscriber_id must be a positive integer"
    return None


def _day(when: datetime) -> str:
    return f"{when.day} {when:%B %Y}"


def body_for(confirm_link: str, unsub_link: str, expires: datetime) -> str:
    postal = os.getenv("AGENCY_POSTAL_ADDRESS", "").strip()
    tail = f"\n{postal}\n" if postal else ""
    return (
        "Hello,\n\n"
        "Someone asked for free ATLAS Academy updates (new free lessons and store "
        "news) to be sent to this address. To say yes, open this link by "
        f"{_day(expires)} (UTC):\n\n"
        f"{confirm_link}\n\n"
        "If it wasn't you, do nothing: you will not be signed up and we will not "
        "write again. Buying a course never signs you up for these emails.\n\n"
        f"To stop at any time: {unsub_link}\n\n"
        f"ATLAS Academy{tail}")


async def _guard(sub_id: int) -> tuple[str | None, str | None]:
    """(email, None) when a confirmation may go out now, else (None, why)."""
    if not mail_enabled():
        return None, f"held: {MAIL_ENV} is off"
    if signing_key() is None:
        return None, "held: no signing key is set, so the unsubscribe link cannot be made"
    row = await _store.peek(sub_id)
    if row is None:
        return None, f"no such signup: {sub_id}"
    if row["status"] != "pending":
        return None, f"signup {sub_id} is {row['status']}; nothing is sent"
    if not row.get("mail_due"):
        return None, f"signup {sub_id} has no confirmation queued"
    if await _store.sent_since(_now() - timedelta(days=1)) >= daily_cap():
        return None, f"held: {daily_cap()} confirmations already went out today"
    return str(row["email"]), None


async def send_confirmation_dry(subscriber_id: int = 0) -> ActionResult:
    email, why = await _guard(int(subscriber_id))
    if email is None:
        return ActionResult(False, why or "not sending")
    return ActionResult(True, f"would email {mask(email)} one confirmation link "
                              "(it dies with the signup, 7 days after it) and an unsubscribe link",
                        evidence={"to": mask(email)})


async def send_confirmation(subscriber_id: int = 0) -> ActionResult:
    sub_id = int(subscriber_id)
    email, why = await _guard(sub_id)
    if email is None:
        return ActionResult(False, f"refused: {why}")
    raw, hashed = new_confirm_token()
    taken = await _store.claim(sub_id, hashed, _now() + PENDING_TTL)
    if taken is None:                                   # somebody else sent it
        return ActionResult(False, "refused: this confirmation is already taken")
    claimed, expires = taken
    unsub = unsubscribe_url(sub_id)
    if unsub is None:
        await _store.failed(sub_id)
        return ActionResult(False, "refused: no signing key")
    from ..agency.outreach import _send_from_address, _smtp_send
    # RFC 8058: a mail app's own Unsubscribe button POSTs to this https link.
    ok, reason = await _smtp_send(
        _send_from_address(), claimed, SUBJECT, body_for(confirm_url(raw), unsub, expires),
        headers={"List-Unsubscribe": f"<{unsub}>",
                 "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}, html=True)
    if not ok:
        await _store.failed(sub_id)
        return ActionResult(False, f"send failed: {reason}")
    await _store.sent(sub_id, _now())
    from ...kernel.audit import audit
    try:
        await audit("academy.signup_confirmation", str(sub_id), {"to": mask(claimed)})
    except Exception:  # noqa: BLE001
        log.warning("academy signup: the audit row could not be written")
    return ActionResult(True, f"emailed {mask(claimed)} a confirmation link",
                        evidence={"to": mask(claimed)}, rollback_token=str(sub_id))


async def send_confirmation_rollback(token: str) -> ActionResult:
    """A delivered email cannot be unsent, and this does not pretend to: the
    signup is marked unsubscribed, its token dropped, so the link in the
    delivered mail is dead and nothing follows it."""
    if not token or not token.isdigit():
        return ActionResult(True, "nothing was sent for this run; nothing to roll back")
    await _store.unsubscribe(int(token), _now())
    return ActionResult(True, f"signup {token} was already emailed and cannot be "
                              "unsent; it is marked unsubscribed and its link is dead",
                        evidence={"unsent": False})


def actions() -> tuple[Action, ...]:
    return (
        Action(name=ACTION, tier=Tier.AUTO,
               summary="Email one free-updates signup its confirmation link (to the "
                       "address typed on the store, fixed text, never a newsletter)",
               run=send_confirmation, dry_run=send_confirmation_dry,
               rollback=send_confirmation_rollback, vet=vet,
               params={"subscriber_id": "the academy_subscribers row id"},
               max_per_hour=30),
    )
