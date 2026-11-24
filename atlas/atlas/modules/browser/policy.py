"""Where the browser may go.

The seed mirrors the Chrome extension's deny-list (`extension/config.js`)
exactly: the categories nobody wants an assistant reading — banks, health,
adult sites, personal mail and chat. On top of that go the auth and payment
hosts, because the server browser never signs in and never pays.
`BROWSER_DENY` (comma-separated) extends the list; nothing narrows it.

Matching is on the host, like the extension:

  * an entry with a dot (`chase.com`) matches that host and every subdomain;
  * a bare category word (`bank`, `health`) matches when any host label
    contains it — the extension's rule, false positives included
    (`example-health.org` is denied);
  * the auth/payment words (`login`, `signin`, `checkout`, `cart`) match a
    whole host label only (`login.example.com`), so Carter Plumbing is not a
    shopping cart;
  * the one entry with a path (`instagram.com/direct`) matches that host plus
    that path prefix.

Paths are otherwise not matched: `/cartoons` and `/health-library` are pages,
not categories. A sign-in page is still never signed into — `session` notes a
password field, refuses a form that holds one, and refuses a URL that carries
a credential in its query (a magic link, a reset token, an OAuth code), since
opening one IS signing in.

Fixed rules, not the owner's to edit: Google's own results page (it blocks
headless browsers, so search goes through Brave or DuckDuckGo), and anything
that is not the public web — non-http(s) schemes, localhost, and literal
private, loopback, link-local or mesh addresses. Names are resolved and
checked by `modules._netguard.assert_public`, asynchronously, in `session`.
"""
from __future__ import annotations

import os
import re
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .._netguard import blocked, literal

# The extension's list, exactly, plus auth and payment hosts. web.whatsapp.com
# stays although ATLAS no longer integrates WhatsApp: this list keeps the
# browser out of his personal chats, and dropping it would only loosen that.
DENY_DEFAULT = ("bank", "chase.com", "paypal.com", "health", "nhs.uk",
                "pornhub.com", "xvideos.com", "mail.google.com", "web.whatsapp.com",
                "instagram.com/direct", "accounts.google.com", "login", "signin",
                "checkout", "cart")
# Matched as a whole host label, never a substring of one.
AUTH_WORDS = frozenset({"login", "signin", "checkout", "cart"})
# A query parameter with one of these names (matched whole, after any number
# of `prefix_`/`prefix-` parts: access_token, auth_code, reset_key,
# X-Amz-Signature, X-Amz-Credential, X-Goog-Signature) and a value that looks
# like a secret makes the URL a credential: opening it signs in, resets,
# confirms, or spends a pre-signed capability. Whole names, so `?keyword=`
# and `?postcode=` are just queries.
_CREDENTIAL_NAME = re.compile(
    r"(?:[a-z0-9]+[_-])*(?:token|code|auth|session|session_?id|reset|sig|signature"
    r"|credential|otp|key|apikey|api_key|access_key)", re.IGNORECASE)
# The same thing carried in the path: a token-alphabet segment next to a
# segment that says what it is (/accounts/reset/<token>, /auth/magic/<jwt>).
# 32+ characters counts; 16+ counts when it holds a digit. A lower-case,
# hyphenated slug of words (/verify/how-to-verify-your-business-2026) does not;
# a UUID, all hex, is not a slug and does.
_PATH_TOKEN = re.compile(r"[A-Za-z0-9_.-]{16,}")
_WORDISH = re.compile(r"^[a-z]*[g-z][a-z]*$")
_WORD = re.compile(r"^[a-z]*[aeiouy][a-z]*$")


def _is_slug(segment: str) -> bool:
    """Words joined by hyphens: 3+ parts, lower case, 2+ parts made of
    letters (one outside a-f, so hex groups do not count), and at least one
    of 3+ letters with a vowel — a code like xkqz-mvtr-8h2k-p3nd has none."""
    parts = segment.split("-")
    return (len(parts) >= 3 and segment == segment.lower()
            and sum(1 for part in parts if _WORDISH.match(part)) >= 2
            and any(len(part) >= 3 and _WORD.match(part) for part in parts))
_PATH_WORD = re.compile(r"token|reset|verify|confirm|auth|magic", re.IGNORECASE)
CREDENTIAL = "URL carries a credential"

_GOOGLE = re.compile(r"^(www\.)?google(\.[a-z]{2,3}){1,2}$")
_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
_NOT_PUBLIC = "is not on the public web; the browser reads the public web only"
_GOOGLE_RESULTS = ("Google's results page blocks headless browsers and is denied; "
                   "search goes through Brave or DuckDuckGo")


def entries() -> tuple[str, ...]:
    """The defaults plus `BROWSER_DENY`, read fresh on every call."""
    extra = [e.strip().lower() for e in os.getenv("BROWSER_DENY", "").split(",")]
    return DENY_DEFAULT + tuple(e for e in extra if e)


def _host_matches(entry: str, host: str) -> bool:
    if "." in entry:
        return host == entry or host.endswith("." + entry)
    labels = host.split(".")
    if entry in AUTH_WORDS:
        return entry in labels
    return any(entry in label for label in labels)


def _matches(entry: str, host: str, path: str) -> bool:
    if "/" in entry:
        domain, _, rest = entry.partition("/")
        return _host_matches(domain, host) and path.startswith("/" + rest)
    return _host_matches(entry, host)


def _not_public(host: str) -> bool:
    if host == "localhost" or host.endswith(_LOCAL_SUFFIXES):
        return True
    ip = literal(host)
    return ip is not None and blocked(ip) is not None


def carries_credential(url: str) -> bool:
    """A token-like query parameter (a credential name, a value of 16+
    characters with no whitespace), or a token-like path segment (see
    `_PATH_TOKEN`) within two segments after, or one before, a segment naming
    token/reset/verify/confirm/auth/magic."""
    try:
        query = urlsplit(url).query
    except ValueError:
        return False
    for name, value in parse_qsl(query, keep_blank_values=True):
        if (_CREDENTIAL_NAME.fullmatch(name) and len(value) >= 16
                and not any(ch.isspace() for ch in value)):
            return True
    try:
        segments = [seg for seg in urlsplit(url).path.split("/") if seg]
    except ValueError:
        return False
    for i, seg in enumerate(segments):
        if not _PATH_TOKEN.fullmatch(seg):
            continue
        if _is_slug(seg) or (len(seg) < 32 and not any(ch.isdigit() for ch in seg)):
            continue
        near = segments[max(0, i - 2):i] + segments[i + 1:i + 2]
        if any(_PATH_WORD.search(other) for other in near):
            return True
    return False


def without_query(url: str) -> str:
    """The URL as it may be stored or reported: no query, no fragment."""
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc.rpartition("@")[2],
                           parts.path, "", ""))
    except ValueError:
        return "(unparseable URL)"


def _split(url: str) -> tuple[str, str, str] | None:
    """(scheme, host, path), lower-cased, or None when it will not parse."""
    try:
        parts = urlsplit((url or "").strip())
        host = (parts.hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if host.startswith("www.") and not _GOOGLE.match(host):
        host = host[4:]
    return parts.scheme.lower(), host, parts.path.lower()


def allowed(url: str) -> tuple[bool, str]:
    """(True, "") when the browser may open `url`, else (False, why)."""
    split = _split(url)
    if split is None:
        return False, f"{url!r} is not a URL the browser can open"
    scheme, host, path = split
    if scheme not in ("http", "https"):
        return False, f"only http(s) pages are opened, not {scheme or 'no scheme'!r}"
    if not host:
        return False, f"{url!r} has no host"
    if _not_public(host):
        return False, f"{host} {_NOT_PUBLIC}"
    if _GOOGLE.match(host) and path.startswith("/search"):
        return False, _GOOGLE_RESULTS
    if carries_credential(url):
        return False, CREDENTIAL
    for entry in entries():
        if _matches(entry, host, path):
            return False, f"denied by policy ({entry})"
    return True, ""


def host_allowed(url: str) -> bool:
    """The host half of `allowed`, for the sub-resources a page pulls in: a
    stylesheet at /login.css is not a login page, a pixel on chase.com is
    still chase.com."""
    split = _split(url)
    if split is None:
        return False
    _scheme, host, _path = split
    if not host or _not_public(host):
        return False
    return not any(_host_matches(e, host) for e in entries() if "/" not in e)
