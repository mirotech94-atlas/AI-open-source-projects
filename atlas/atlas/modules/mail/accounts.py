"""Which mailboxes ATLAS watches, and how it reaches each one.

Four mailboxes across two providers, which is why this is a list rather than
a set of singular GOOGLE_* variables:

    mirotech, myron      Google      personal
    flycheap, guardian   Zoho IMAP   business

Configured as one line naming the accounts, plus a few variables per account:

    MAIL_ACCOUNTS=mirotech,myron,flycheap,guardian
    MAIL_MIROTECH_KIND=google
    MAIL_MIROTECH_EMAIL=owner@example.com
    MAIL_MIROTECH_REFRESH_TOKEN=...
    MAIL_FLYCHEAP_KIND=imap
    MAIL_FLYCHEAP_EMAIL=owner@example.com
    MAIL_FLYCHEAP_HOST=imap.zoho.com
    MAIL_FLYCHEAP_PASSWORD=...          (a Zoho app password, not the login)

The Google client id and secret are shared: one OAuth app, one consent per
account, so adding a mailbox is a refresh token rather than a new app.

EACH ACCOUNT CARRIES ITS OWN ADDRESS, and that is not incidental. The
unreplied check decides "he replied last" by matching the sender against his
address. With one global GOOGLE_ACCOUNT_EMAIL, every thread in every other
mailbox looks unanswered -- a wrong reading that arrives looking exactly like
a real finding, which is the worst shape a bug can have here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Account:
    name: str
    kind: str                    # google | imap
    email: str
    # google
    refresh_token: str = ""
    # imap
    host: str = ""
    password: str = ""
    port: int = 993
    # Other addresses that deliver into this same mailbox.
    #
    # Zoho gives one licence several addresses: owner@example.com,
    # owner@example.com and support@guarder.example.com are one INBOX, verified by
    # identical message counts and the same newest subject. So Guardian mail
    # is already read -- but a reply Miro sent FROM owner@example.com does not
    # match owner@example.com, and the thread would be counted as still
    # waiting on him. Every alias has to count as him.
    aliases: tuple[str, ...] = ()

    @property
    def addresses(self) -> tuple[str, ...]:
        return tuple(a for a in (self.email, *self.aliases) if a)

    def is_me(self, sender: str) -> bool:
        """He sent it, from the main address or any alias."""
        low = (sender or "").lower()
        return any(a.lower() in low for a in self.addresses)

    @property
    def configured(self) -> bool:
        if self.kind == "google":
            return bool(self.email and self.refresh_token
                        and os.getenv("GOOGLE_CLIENT_ID")
                        and os.getenv("GOOGLE_CLIENT_SECRET"))
        if self.kind == "imap":
            return bool(self.email and self.host and self.password)
        return False

    @property
    def missing(self) -> str:
        """What to set, named exactly, for a check to say it in one line."""
        up = self.name.upper()
        if self.kind == "google":
            lacking = [n for n, v in (
                (f"MAIL_{up}_EMAIL", self.email),
                (f"MAIL_{up}_REFRESH_TOKEN", self.refresh_token),
                ("GOOGLE_CLIENT_ID", os.getenv("GOOGLE_CLIENT_ID", "")),
                ("GOOGLE_CLIENT_SECRET", os.getenv("GOOGLE_CLIENT_SECRET", "")),
            ) if not v]
            return ", ".join(lacking)
        lacking = [n for n, v in (
            (f"MAIL_{up}_EMAIL", self.email),
            (f"MAIL_{up}_HOST", self.host),
            (f"MAIL_{up}_PASSWORD", self.password),
        ) if not v]
        return ", ".join(lacking)


def _one(name: str) -> Account:
    up = name.upper()
    return Account(
        name=name,
        kind=os.getenv(f"MAIL_{up}_KIND", "google").strip().lower(),
        email=os.getenv(f"MAIL_{up}_EMAIL", "").strip(),
        refresh_token=os.getenv(f"MAIL_{up}_REFRESH_TOKEN", "").strip(),
        host=os.getenv(f"MAIL_{up}_HOST", "").strip(),
        password=os.getenv(f"MAIL_{up}_PASSWORD", ""),
        port=int(os.getenv(f"MAIL_{up}_PORT", "993") or 993),
        aliases=tuple(a.strip() for a in
                      os.getenv(f"MAIL_{up}_ALIASES", "").split(",")
                      if a.strip()),
    )


def all_accounts() -> list[Account]:
    """Every configured mailbox, in the order they were named.

    Falls back to the original single-account GOOGLE_* variables when
    MAIL_ACCOUNTS is unset, so an existing deployment keeps working without
    being reconfigured -- and so the first account added does not silently
    replace the one already there.
    """
    named = [n.strip() for n in os.getenv("MAIL_ACCOUNTS", "").split(",")
             if n.strip()]
    if named:
        return [_one(n) for n in named]

    legacy = os.getenv("GOOGLE_REFRESH_TOKEN", "").strip()
    if legacy:
        return [Account(name="default", kind="google",
                        email=os.getenv("GOOGLE_ACCOUNT_EMAIL", "").strip(),
                        refresh_token=legacy)]
    return []


#: Local parts that are machines, not people. Zoho's own "an address was
#: deleted from your account", GitLab pipeline mail and ATLAS's own ops brief
#: were all counted as threads waiting on him -- five of five in one mailbox.
#: Nobody is waiting behind a no-reply address.
AUTOMATED = ("noreply", "no-reply", "no_reply", "donotreply", "do-not-reply",
             "notification", "notifications", "alert", "alerts", "mailer-daemon",
             "postmaster", "bounce", "newsletter", "digest", "atlas@")


def automated(sender: str) -> bool:
    """True when the From address is a machine and no reply is expected."""
    low = (sender or "").lower()
    return any(tag in low for tag in AUTOMATED)


#: Headers a machine or a mailing list sets; a person writing by hand does not.
BULK_HEADERS = ("List-Unsubscribe", "List-Id", "Auto-Submitted")


def bulk_headers(headers: list[dict]) -> bool:
    """True when the message carries a bulk/automated marker header (Miro,
    2026-11-18: automated senders never count as waiting on him)."""
    names = {str(h.get("name", "")).lower(): str(h.get("value", "")).lower()
             for h in headers or []}
    if any(h.lower() in names for h in BULK_HEADERS[:2]):
        return True
    if names.get("auto-submitted", "no") not in ("", "no"):
        return True
    return names.get("precedence", "") in ("bulk", "list", "junk")
