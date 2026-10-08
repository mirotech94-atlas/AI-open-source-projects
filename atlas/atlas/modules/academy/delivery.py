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
from pathlib import Path

from .catalog import Catalog, File
from .stripe_read import SESSION_RE, Verdict

log = logging.getLogger("atlas.academy.delivery")

DOWNLOAD_KEY_ENV = "ACADEMY_DOWNLOAD_KEY"
FILES_ENV = "ACADEMY_FILES_DIR"
DEFAULT_FILES_DIR = "data/academy/files"
API_BASE_ENV = "ACADEMY_API_BASE"
DEFAULT_API_BASE = "https://atlas.example.com"
ACCESS_PATH = "/public/academy/access"
FILE_PREFIX = "/public/academy/file/"
_DOMAIN = b"atlas-academy-download-v1"


# ------------------------------------------------------------------ urls

def api_base() -> str:
    """The public host the access page lives on. Same default as the site
    chat (`agency.sitechat.api_base`): atlas.example.com, whose Caddy block
    proxies /public/academy/* to the api."""
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
    (and was posted to Slack). Only a payment Stripe confirmed is recorded."""
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
        body = f"Checkout {v.session_id[:14]}... status {v.status}."
    try:
        await notify(Outbound(title=title, body=body, topic="academy", kind="receipt"))
    except Exception as exc:                            # noqa: BLE001
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
