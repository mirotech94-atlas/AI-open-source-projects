"""Handing over what a buyer paid for, without ATLAS writing to anyone.

* `access_url(session_id)` is the page Stripe redirects a buyer to after a
  Payment Link checkout, on the same public host as the site chat
  (`/public/academy/access`, atlas/api/academy.py).
* `mint` / `verify` sign short-lived download links: HMAC-SHA256 over the
  file key, the expiry and the checkout session, keyed by
  ACADEMY_DOWNLOAD_KEY (else SESSION_SECRET, domain-separated). The link
  names a catalog file KEY, never a path.
* `resolve` turns a catalog file into a path inside the private files
  directory (ACADEMY_FILES_DIR, default data/academy/files -- never under
  the Mapfront publish root), refusing a symlink and anything that resolves
  outside it.
* `record` writes the purchase row and, the first time a session is seen,
  posts it to Slack with the product and the amount Stripe stated.

Nothing here sends mail or calls Stripe with a write: the buyer comes to
the page; the page asks Stripe.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from .. import store_origin
from .catalog import Catalog, File
from .stripe_read import SESSION_RE, Verdict

log = logging.getLogger("atlas.academy.delivery")

DOWNLOAD_KEY_ENV = "ACADEMY_DOWNLOAD_KEY"
FILES_ENV = "ACADEMY_FILES_DIR"
DEFAULT_FILES_DIR = "data/academy/files"
API_BASE_ENV = "ACADEMY_API_BASE"
# atlas.storefront.test is deleted (2026-10-29); the store host proxies /public/*.
DEFAULT_API_BASE = store_origin.origin()
ACCESS_PATH = "/public/academy/access"
FILE_PREFIX = "/public/academy/file/"
LIBRARY_PATH = "/public/academy/library"
_DOMAIN = b"atlas-academy-download-v1"
_LIBRARY_DOMAIN = b"atlas-academy-library-v1"
LIBRARY_TTL_S = 3600
LIBRARY_MAX_SESSIONS = 10


# ------------------------------------------------------------------ urls

def api_base() -> str:
    """The public host the access page lives on. Same default as the site
    chat (`agency.sitechat.api_base`): the store origin (ACADEMY_PUBLIC_ORIGIN),
    whose Caddy block proxies /public/* to the api."""
    raw = os.getenv(API_BASE_ENV, "").strip().rstrip("/")
    if raw.startswith("https://"):
        return raw
    from ..agency.sitechat import api_base as site_chat_base
    return site_chat_base() or DEFAULT_API_BASE


def access_url(session_id: str) -> str:
    return f"{api_base()}{ACCESS_PATH}?session_id={session_id}"


def redirect_url_for_stripe() -> str:
    """What Miro pastes into each Payment Link's after-payment redirect."""
    return f"{api_base()}{ACCESS_PATH}?session_id={{CHECKOUT_SESSION_ID}}"


# ------------------------------------------------------------------ signing

def signing_key() -> bytes | None:
    """ACADEMY_DOWNLOAD_KEY, else a key derived from SESSION_SECRET, else None
    (no link can be signed, and the page says so)."""
    own = os.getenv(DOWNLOAD_KEY_ENV, "").strip()
    if own:
        return hmac.new(own.encode(), _DOMAIN, hashlib.sha256).digest()
    shared = os.getenv("SESSION_SECRET", "").strip()
    if shared:
        return hmac.new(shared.encode(), _DOMAIN, hashlib.sha256).digest()
    return None


def _sig(key: bytes, file_key: str, expires: int, session_id: str) -> str:
    msg = f"v1|{file_key}|{expires}|{session_id}".encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def mint(session_id: str, file_key: str, *, days: int, now: float | None = None,
         key: bytes | None = None) -> str | None:
    key = key if key is not None else signing_key()
    if key is None:
        return None
    expires = int((now if now is not None else time.time()) + days * 86400)
    sig = _sig(key, file_key, expires, session_id)
    return f"v1.{file_key}.{expires}.{session_id}.{sig}"


def verify(token: str, *, now: float | None = None,
           key: bytes | None = None) -> tuple[str, str, str]:
    """(status, session_id, file_key). status is 'ok', 'expired' or 'bad'.
    'expired' is only ever said about a correctly signed link."""
    key = key if key is not None else signing_key()
    parts = (token or "").split(".")
    if key is None or len(parts) != 5 or parts[0] != "v1" or len(token) > 400:
        return "bad", "", ""
    _, file_key, expires_s, session_id, sig = parts
    if not expires_s.isdigit() or not SESSION_RE.match(session_id) or not file_key:
        return "bad", "", ""
    if not hmac.compare_digest(sig, _sig(key, file_key, int(expires_s), session_id)):
        return "bad", "", ""
    if int(expires_s) < (now if now is not None else time.time()):
        return "expired", session_id, file_key
    return "ok", session_id, file_key


def download_url(token: str) -> str:
    return f"{api_base()}{FILE_PREFIX}{token}"


# ------------------------------------------------------------------ library
#
# The purchase library link a buyer gets after proving a purchase (email +
# Stripe receipt number, atlas/api/academy.py `recover`). It is bound to the
# owner's email HASH (never the address) and to the checkout sessions that
# matched, lasts exactly one hour, and is signed with a key derived from the
# download key under its own domain and prefix, so a download token can never
# pass as a library token or the other way round.

_HEX64 = frozenset("0123456789abcdef")


def _library_key(key: bytes) -> bytes:
    return hmac.new(key, _LIBRARY_DOMAIN, hashlib.sha256).digest()


def _library_sig(key: bytes, owner: str, expires: int, sessions: str) -> str:
    msg = f"lib1|{owner}|{expires}|{sessions}".encode()
    return hmac.new(_library_key(key), msg, hashlib.sha256).hexdigest()


def mint_library(owner_hash: str, session_ids: tuple[str, ...] | list[str], *,
                 now: float | None = None, key: bytes | None = None) -> str | None:
    key = key if key is not None else signing_key()
    ids = tuple(session_ids)[:LIBRARY_MAX_SESSIONS]
    if key is None or not ids or not all(SESSION_RE.match(s) for s in ids) \
            or len(owner_hash) != 64 or not set(owner_hash) <= _HEX64:
        return None
    expires = int((now if now is not None else time.time()) + LIBRARY_TTL_S)
    sessions = "~".join(ids)
    return f"lib1.{owner_hash}.{expires}.{sessions}." \
           f"{_library_sig(key, owner_hash, expires, sessions)}"


def verify_library(token: str, *, now: float | None = None,
                   key: bytes | None = None) -> tuple[str, str, tuple[str, ...]]:
    """(status, owner_hash, session_ids). status is 'ok', 'expired' or 'bad';
    'expired' is only said about a correctly signed link."""
    key = key if key is not None else signing_key()
    parts = (token or "").split(".")
    if key is None or len(token or "") > 2400 or len(parts) != 5 or parts[0] != "lib1":
        return "bad", "", ()
    _, owner, expires_s, sessions, sig = parts
    ids = tuple(sessions.split("~"))
    if (len(owner) != 64 or not set(owner) <= _HEX64 or not expires_s.isdigit()
            or len(expires_s) > 12 or not 0 < len(ids) <= LIBRARY_MAX_SESSIONS
            or not all(SESSION_RE.match(s) for s in ids)):
        return "bad", "", ()
    if not hmac.compare_digest(sig, _library_sig(key, owner, int(expires_s), sessions)):
        return "bad", "", ()
    if int(expires_s) < (now if now is not None else time.time()):
        return "expired", "", ()
    return "ok", owner, ids


def library_url(token: str) -> str:
    return f"{api_base()}{LIBRARY_PATH}?t={token}"


# Postgres hashes the stored address the way stripe_read.email_hash does
# (lowercased, trimmed, UTF-8, sha256 hex), so the address never leaves the
# database to be compared.
_OWNER_SQL = "encode(sha256(convert_to(lower(btrim(email)), 'UTF8')), 'hex')"


async def purchases_for(owner_hash: str, limit: int = 20) -> list[dict]:
    """The recorded purchases paid from the address with this hash, newest
    first. Raises when the database can't be read: the caller says so."""
    from ...kernel.state import fetch_all
    rows = await fetch_all(
        f"""SELECT session_id, first_seen_at FROM academy_purchases
             WHERE email IS NOT NULL AND {_OWNER_SQL} = $1
             ORDER BY first_seen_at DESC LIMIT $2""",
        owner_hash, limit)
    return [dict(r) for r in rows or []]


# ------------------------------------------------------------------ files

def files_root() -> Path:
    return Path(os.getenv(FILES_ENV, DEFAULT_FILES_DIR))


def resolve(f: File, root: Path | None = None) -> Path | None:
    """The file on disk, or None: missing, a symlink, or outside the root.
    Blocking (stat calls); cheap enough for a request."""
    base = (root or files_root()).resolve()
    candidate = base / f.path
    try:
        if candidate.is_symlink():
            return None
        path = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if not path.is_relative_to(base) or path == base or not path.is_file():
        return None
    return path


# ------------------------------------------------------------------ record

async def record(v: Verdict, source: str) -> bool:
    """Write or refresh the purchase row. True when this session was new
    (and, unless it came from a buyer's recovery, was posted to Slack). Only a
    payment Stripe confirmed is recorded. A "recover" row is never posted: a
    public form submission must not be able to make ATLAS post to Slack."""
    if not v.recordable or v.amount_minor is None:
        return False
    from ...kernel.state import execute_sql, fetch_one
    status = "paid" if v.status == "expired" else v.status
    offers = [o.id for o in v.offers]
    row = await fetch_one(
        """INSERT INTO academy_purchases
               (session_id, source, status, offers, price_ids, amount_minor,
                currency, refunded_minor, email, payment_intent, livemode)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
           ON CONFLICT (session_id) DO NOTHING
           RETURNING session_id""",
        v.session_id, source, status, offers, list(v.price_ids), v.amount_minor,
        v.currency, v.refunded_minor, v.email or None, v.payment_intent or None,
        v.livemode)
    if row is None:
        await execute_sql(
            """UPDATE academy_purchases
                  SET status = $2, refunded_minor = $3, last_checked_at = now()
                WHERE session_id = $1""",
            v.session_id, status, v.refunded_minor)
        return False
    if source != "recover":
        await _notify_new(v)
    return True


def _names(v: Verdict) -> str:
    return ", ".join(o.name for o in v.offers) or "a price not in the catalog"


async def _notify_new(v: Verdict) -> None:
    from ...kernel.comms import Outbound, notify
    read = v.read_at.strftime("%H:%M UTC")
    mode = "" if v.livemode else " (TEST MODE)"
    if v.status == "unknown_price":
        title = (f"ATLAS Academy: a paid checkout matched no catalog price "
                 f"({v.amount_text()}, Stripe, read {read}){mode}")
        body = (f"Price ids: {', '.join(v.price_ids) or 'none'}. The buyer's access "
                "page says so and gives the contact address. Add the price id to "
                "atlas/modules/academy/catalog.json.")
    else:
        title = (f"ATLAS Academy: new purchase, {_names(v)}, {v.amount_text()} "
                 f"(Stripe, read {read}){mode}")
        # Invoice watch (Miro 2026-11-13): every paid checkout should carry a
        # Stripe invoice (the payment links create one). Say so, or say it is missing.
        if v.status == "paid":
            inv = (f"Invoice {v.invoice[:14]}... made; Stripe emails it to the buyer."
                   if v.invoice else
                   "NO INVOICE on this payment: check the payment link's invoice "
                   "setting in Stripe.")
        else:
            inv = ""
        body = f"Checkout {v.session_id[:14]}... status {v.status}. {inv}".strip()
    try:
        await notify(Outbound(title=title, body=body, topic="academy", kind="receipt"))
    except Exception as exc:  # noqa: BLE001
        # The row is written; a failed post must not take the buyer's page down.
        log.warning("academy: purchase not posted to Slack (%s)", type(exc).__name__)


# ------------------------------------------------------------------ readiness

def missing_files(cat: Catalog, *, live_only: bool = False,
                  root: Path | None = None) -> list[str]:
    """Catalog file keys with no file on disk. Blocking."""
    seen: dict[str, File] = {}
    for o in cat.offers():
        if live_only and not o.buy_live:
            continue
        for f in o.files:
            seen.setdefault(f.key, f)
    return sorted(k for k, f in seen.items() if resolve(f, root) is None)


@dataclass(frozen=True)
class Exceptions:
    """Paid purchases a buyer may be waiting on: a checkout whose price is
    not in the catalog (no files listed), and catalog files on sale with no
    file on disk. `status` is "ok" or "unknown" (a read failed: never shown
    as nothing waiting)."""

    status: str
    unknown_price: tuple[dict, ...] = ()
    missing: tuple[str, ...] = ()
    detail: str = ""

    @property
    def count(self) -> int:
        return len(self.unknown_price) + len(self.missing)


async def exceptions(cat: Catalog) -> Exceptions:
    """Read-only: the database and the files directory, never Stripe."""
    import asyncio

    from ...kernel.state import fetch_all
    try:
        missing = await asyncio.to_thread(missing_files, cat, live_only=True)
    except OSError as exc:
        return Exceptions("unknown", detail=f"files directory unreadable "
                          f"({type(exc).__name__})")
    try:
        rows = await fetch_all(
            """SELECT session_id, first_seen_at, amount_minor, currency, livemode
                 FROM academy_purchases WHERE status = 'unknown_price'
                ORDER BY first_seen_at DESC LIMIT 50""")
    except Exception as exc:  # noqa: BLE001
        return Exceptions("unknown", missing=tuple(missing),
                          detail=f"purchase record unreadable ({type(exc).__name__})")
    return Exceptions("ok", tuple(dict(r) for r in rows or []), tuple(missing))


def exceptions_text(ex: Exceptions, read_at: str) -> str:
    """One short digest section. No email, only a checkout prefix."""
    head = f"*Academy: awaiting access* (read {read_at})"
    if ex.status != "ok":
        tail = f" Missing files on sale: {', '.join(ex.missing)}." if ex.missing else ""
        return f"{head}\nUNKNOWN, not none: {ex.detail}.{tail}"
    if not ex.count:
        return f"{head}\nNone: every recorded paid purchase matches the catalog, and " \
               "every file on sale is on disk."
    lines = [head]
    for r in ex.unknown_price:
        amount = (f"{int(r['amount_minor']) / 100:,.2f} {str(r['currency']).upper()}"
                  if r.get("amount_minor") is not None else "amount not recorded")
        mode = "" if r.get("livemode", True) else " (test mode)"
        lines.append(f"• Paid, price not in the catalog: {str(r['session_id'])[:14]}... "
                     f"{amount}{mode}. Add its price id to catalog.json.")
    if ex.missing:
        lines.append(f"• Files on sale missing on disk: {', '.join(ex.missing)}.")
    return "\n".join(lines)
