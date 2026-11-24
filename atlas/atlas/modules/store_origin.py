"""Where the ATLAS Academy store lives: `ACADEMY_PUBLIC_ORIGIN`.

The store lives at https://agency.storefront.test (one host with the agency landing
and the client previews). Every link, CORS origin, allow-list and spoken
address derives from here, read at call time. An unset or non-https value
falls back to the default.

Standard library only: presenter.py and tools load modules by path."""
from __future__ import annotations

import os
from urllib.parse import quote, urlsplit

ENV = "ACADEMY_PUBLIC_ORIGIN"
# 2026-10-29: ATLAS is local-only (127.0.0.1 on Miro's Mac), so the static
# store and landing page cannot post to it. Every contact, support, recover
# and preview request is an email to this address instead: a mailto: link, or
# a GET form whose `subject` and `body` fields the visitor's mail app fills.
CONTACT_MAIL = "owner@example.com"
# Miro 2026-11-13: a phone number anyone can reach him on, next to the address.
CONTACT_PHONE = "+15550100142"
CONTACT_PHONE_DISPLAY = "+1 555 010 0142"
DEFAULT = "https://agency.storefront.test"
STORE_PATH = "academy"          # the store site is {origin}/academy/

_DIGITS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
           "nine")


def origin() -> str:
    """`https://<host>`, lower case, no trailing slash."""
    raw = os.getenv(ENV, "").strip().rstrip("/").lower()
    parts = urlsplit(raw)
    if (parts.scheme != "https" or not parts.hostname or parts.path or parts.query
            or parts.username):
        return DEFAULT
    return f"https://{parts.netloc}"


def host() -> str:
    """The store's host name, e.g. agency.storefront.test."""
    return origin().removeprefix("https://")


def url(path: str = "") -> str:
    """An absolute store URL, under /academy/ (2026-11-16):
    url("help/sent/") -> https://<host>/academy/help/sent/; url() is the store
    home. The old bare paths (https://<host>/help/sent/) still work: Caddy
    308-redirects them. API paths (/public/academy/...) are not store pages:
    build those from origin()."""
    return f"{origin()}/{STORE_PATH}/{path.lstrip('/')}"


def _label(label: str) -> str:
    # A word reads as a word; a label with digits is spelled out, letter by
    # letter ("storefront" -> "one eight d n"), the way it is said aloud.
    if label.isalpha():
        return label
    return " ".join(_DIGITS[int(c)] if c.isdigit() else c for c in label if c != "-")


def spoken(h: str | None = None) -> str:
    """The host as a narrator says it: agency dot storefront dot test."""
    return " dot ".join(_label(part) for part in (h or host()).split("."))


def mailto(subject: str = "", body: str = "") -> str:
    """mailto:CONTACT_MAIL with a prefilled subject and body, URL-encoded."""
    query = "&".join(f"{k}={quote(v, safe='')}" for k, v in
                     (("subject", subject), ("body", body)) if v)
    return f"mailto:{CONTACT_MAIL}" + (f"?{query}" if query else "")
