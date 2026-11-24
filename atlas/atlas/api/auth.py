"""Single-user session auth for the dashboard.

Mesh membership is the perimeter, not the identity. A laptop that joins the
NetBird mesh can reach the dashboard's port; that must not be the same thing as
being able to approve an action. This adds the second factor of "and you know
the password".

Deliberately small: one user, one password, a signed cookie. There are no
roles, no registration, no reset flow and no user table, because every one of
those is a surface that exists to serve a second user who does not exist.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from . import peers
from .peers import PerAddressLimiter

log = logging.getLogger("atlas.auth")

COOKIE = "atlas_session"
MAX_AGE = 30 * 24 * 3600            # 30 days, as specified
_hasher = PasswordHasher()

# Login attempts are throttled in-process, per source address. This is a
# single-user dashboard on a private mesh, so the threat is a mesh device
# guessing, not a botnet -- a small sliding window is proportionate and needs
# no storage. Per address, not global: one guessing device must not be able
# to lock Miro himself out of the dashboard (audit #20). The address is the
# one the mesh guard judges (peers.effective_client), not Caddy's.
MAX_ATTEMPTS = 8
WINDOW = 300.0
_attempts = PerAddressLimiter(MAX_ATTEMPTS, WINDOW)


@dataclass(frozen=True, slots=True)
class Session:
    user: str
    issued_at: float


def _secret() -> str:
    value = os.getenv("SESSION_SECRET", "")
    if len(value) < 32:
        # Failing closed is the only safe option: a weak or absent secret means
        # anyone who can reach the port can mint their own session cookie.
        raise RuntimeError(
            "SESSION_SECRET must be at least 32 characters. "
            "Generate one with: head -c 32 /dev/urandom | base64")
    return value


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(_secret(), salt="atlas-dashboard-session")


def _stored_hash() -> str:
    """The argon2 hash, from whichever form survived the deployment.

    The base64 form exists because a raw argon2 hash cannot travel through a
    Docker Compose .env file intact: `$` is interpolation syntax there, and the
    hash is mostly `$`.
    """
    encoded = os.getenv("DASHBOARD_PASSWORD_HASH_B64", "").strip()
    if encoded:
        try:
            return base64.b64decode(encoded).decode().strip()
        except Exception:  # noqa: BLE001
            log.error("DASHBOARD_PASSWORD_HASH_B64 is not valid base64")
            return ""
    return os.getenv("DASHBOARD_PASSWORD_HASH", "").strip()


def configured() -> bool:
    """Whether a USABLE password has been set.

    Reports False for a hash that arrived mangled, so the login page says "not
    configured" rather than silently rejecting the right password forever.
    """
    return hash_looks_valid(_stored_hash())


def rate_limited(client: str = "") -> bool:
    """Whether `client` (a source address) has used up its login attempts."""
    return _attempts.limited(client)


def hash_looks_valid(stored: str) -> bool:
    return stored.startswith("$argon2") and stored.count("$") >= 4


def verify_password(supplied: str, client: str = "") -> bool:
    """Check a password against the argon2 hash in the environment. Each
    check counts against `client`'s attempts."""
    stored = _stored_hash()
    if not stored:
        log.error("DASHBOARD_PASSWORD_HASH is not set — refusing every login")
        return False
    if not hash_looks_valid(stored):
        # A mangled hash is indistinguishable from a wrong password unless we
        # say so. Docker Compose interpolates `$` in a .env file, and an argon2
        # hash is full of them — `$argon2id$v=19$...` arrives as an empty
        # string, and every correct password then looks wrong. Escape each `$`
        # as `$$` in .env, or use DASHBOARD_PASSWORD_HASH_B64.
        log.error(
            "DASHBOARD_PASSWORD_HASH is not a valid argon2 hash (got %d chars, "
            "starts %r). If it came from a .env file, Docker Compose has eaten "
            "the $ signs — escape them as $$ or set "
            "DASHBOARD_PASSWORD_HASH_B64 instead.",
            len(stored), stored[:12])
        return False
    _attempts.hit(client)
    try:
        _hasher.verify(stored, supplied or "")
    except (VerifyMismatchError, InvalidHashError):
        return False
    except Exception:
        log.exception("password verification failed")
        return False
    return True


def issue(user: str = "miro") -> str:
    return _serializer().dumps({"u": user, "n": secrets.token_hex(8)})


def read(token: str | None) -> Session | None:
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=MAX_AGE, return_timestamp=True)
    except SignatureExpired:
        return None
    except BadSignature:
        log.warning("rejected a session cookie with a bad signature")
        return None
    except Exception:  # noqa: BLE001
        return None
    payload, issued = data
    if not isinstance(payload, dict) or "u" not in payload:
        return None
    return Session(user=str(payload["u"]), issued_at=issued.timestamp())


def current(request: Request) -> Session | None:
    return read(request.cookies.get(COOKIE))


def require(request: Request) -> Session:
    """FastAPI dependency. 401 rather than a redirect: the UI is a SPA and the
    server-rendered pages handle their own redirect."""
    session = current(request)
    if session is None:
        raise HTTPException(401, "not signed in")
    return session


_ON = ("1", "true", "yes", "on")
_OFF = ("0", "false", "no", "off")


def cookie_secure(request: Request) -> bool:
    """Whether the session cookie carries `Secure` (audit #16).

    The dashboard is served two ways: over HTTPS by Caddy on 443
    (atlas.storefront.test, mesh and device clients) and over plain HTTP on the
    mesh address's :8080. A `Secure` cookie over plain HTTP is dropped by the
    browser, so one fixed answer breaks one of them. DASHBOARD_HTTPS decides:

      unset / "auto"  Secure whenever this request came over HTTPS: its own
                      scheme is https, or it came from our own proxy (and
                      only from it -- peers.from_proxy) saying
                      X-Forwarded-Proto: https. The default.
      true            always Secure.
      false           never Secure: the explicit opt-out for local plain
                      HTTP development. It also wins over HTTPS, so a
                      production .env must not carry it.
    """
    mode = os.getenv("DASHBOARD_HTTPS", "").strip().lower()
    if mode in _ON:
        return True
    if mode in _OFF:
        return False
    if request.url.scheme == "https":
        return True
    if peers.from_proxy(request):
        proto = request.headers.get("x-forwarded-proto", "").split(",")[0]
        return proto.strip().lower() == "https"
    return False


def set_cookie(response, token: str, request: Request) -> None:
    response.set_cookie(
        COOKIE, token,
        max_age=MAX_AGE,
        httponly=True,          # not readable from JS
        samesite="lax",         # a cross-site POST cannot carry it
        secure=cookie_secure(request),
        path="/",
    )


def clear_cookie(response, request: Request) -> None:
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax",
                           secure=cookie_secure(request))


def hash_password(plain: str) -> str:
    """Used by `python -m atlas.cli set-password`. Never stores the plaintext."""
    return _hasher.hash(plain)


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a or "", b or "")


# --- CSRF for the GUI decision route (Miro 2026-10-29: GUI decisions) -------
# The session cookie is HttpOnly and SameSite=Lax; the decision route also
# wants proof that the page making the request read /api/session itself. The
# token is an HMAC of the cookie value, which already carries a per-login
# nonce ("n" in `issue`), so it changes with every sign-in and needs no
# storage. A page on another origin can neither read the cookie nor this.

def csrf_token(session_cookie: str) -> str:
    return hmac.new(_secret().encode(), b"atlas-csrf:" + session_cookie.encode(),
                    hashlib.sha256).hexdigest()


def csrf_ok(request: Request) -> bool:
    cookie = request.cookies.get(COOKIE) or ""
    given = request.headers.get("x-csrf-token", "").strip()
    if not cookie or not given:
        return False
    return constant_time_equals(given, csrf_token(cookie))


def request_origin(request: Request) -> str:
    """The origin this request was made to: its scheme (the proxy's
    X-Forwarded-Proto only when the peer is our own proxy) and its Host."""
    scheme = request.url.scheme
    if peers.from_proxy(request):
        proto = request.headers.get("x-forwarded-proto", "").split(",")[0]
        proto = proto.strip().lower()
        if proto in ("http", "https"):
            scheme = proto
    host = request.headers.get("host", "").strip().lower()
    return f"{scheme}://{host}"


def same_origin(request: Request, allowed_hosts: frozenset[str] | None) -> bool:
    """Fail closed: a missing, "null" or foreign Origin is not ours, nor is a
    browser that says the fetch was not same-origin."""
    origin = (request.headers.get("origin") or "").strip().rstrip("/").lower()
    host = request.headers.get("host", "").strip().lower()
    if not origin or origin == "null" or not host:
        return False
    if not hmac.compare_digest(origin, request_origin(request)):
        return False
    if allowed_hosts is not None and host not in allowed_hosts:
        return False
    site = request.headers.get("sec-fetch-site")
    return site is None or site.strip().lower() == "same-origin"
