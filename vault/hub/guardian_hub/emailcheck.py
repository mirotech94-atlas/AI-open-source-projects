"""Is this email address safe to trust?

Miro's ask: "a quick check if emails are good or bad and trusted or not".
The moment it serves is a real one — a letter arrives asking for a payment
detail to be changed, and somebody has thirty seconds to decide whether the
sender is who it claims to be.

**Everything here is answered from DNS and from lists Vault already
downloads.** The address never leaves the hub. That is deliberate: a
security product that quietly posts its customers' correspondents to a
third-party reputation API is doing the thing it warns about. A reputation
provider can be switched on (`platform_settings.email_reputation_provider`)
and is off until somebody decides otherwise — see `reputation_note`.

What it can honestly say:

- **The domain does not exist.** Nothing can be sent from it legitimately;
  a letter claiming to be is forged, full stop.
- **The domain cannot receive mail** (no MX). Strongly suggests
  send-only — a common shape for bulk and for throwaway senders.
- **Anyone can forge it** (no SPF, or DMARC not enforcing). The strongest
  everyday signal: a domain that has not told the world who may send as it
  is one an attacker can impersonate without being stopped.
- **It is a throwaway provider.** Not malicious, but not accountable.
- **It is a lookalike of one of YOUR domains.** The one check a generic
  tool cannot do, because only Vault knows which domains are yours.

What it deliberately does NOT claim: that an address is "safe". No check
here can establish that, and a green tick on a well-configured phishing
domain is worse than no tool at all. The best verdict is "nothing wrong
found", and the copy says exactly that.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field

#: Throwaway/disposable mail providers. Not exhaustive by design — a
#: half-million-line list downloaded on every check would be its own
#: supply-chain surface, and the common ones carry most of the signal.
DISPOSABLE = frozenset({
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "temp-mail.org", "throwawaymail.com", "yopmail.com", "trashmail.com",
    "getnada.com", "sharklasers.com", "dispostable.com", "maildrop.cc",
    "fakeinbox.com", "mailnesia.com", "mintemail.com", "spam4.me",
    "emailondeck.com", "moakt.com", "tempr.email", "mohmal.com",
})

#: Free consumer providers. NOT a warning on their own — most of the world
#: uses one — but worth saying when the letter claims to be a company.
CONSUMER = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "yahoo.com", "ymail.com", "aol.com", "icloud.com", "me.com", "mac.com",
    "proton.me", "protonmail.com", "gmx.com", "mail.com", "zoho.com",
})

_ADDR = re.compile(r"^[^@\s]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})$")


@dataclass
class Finding:
    """One thing Vault noticed. `tone` drives nothing but presentation;
    the sentence is the product."""
    tone: str      # bad | warn | info | good
    text: str


@dataclass
class EmailVerdict:
    address: str = ""
    domain: str = ""
    #: forged | risky | unproven | unknown. Never "safe" — see the module
    #: docstring. `unproven` is the best available answer.
    verdict: str = "unknown"
    headline: str = ""
    findings: list[Finding] = field(default_factory=list)
    #: What Vault could NOT check, so the reader can weigh the answer.
    limits: list[str] = field(default_factory=list)
    reputation_note: str = ""


def _dig(name: str, rtype: str) -> list[str]:
    try:
        out = subprocess.run(["dig", "+short", "+time=3", "+tries=1", name, rtype],
                             capture_output=True, text=True, timeout=8).stdout
        return [l.strip().strip('"').replace('" "', "") for l in out.splitlines() if l.strip()]
    except Exception:
        return []


#: Character sequences that read as each other in a normal font, mapped to
#: one canonical form. `rn` -> `m` is the one that matters most: it is the
#: classic domain-spoofing trick and it is TWO characters, so an
#: edit-distance rule alone never catches it (acrne.example vs acme.example
#: is distance 2 and looks identical at a glance).
_CONFUSABLE = (("rn", "m"), ("vv", "w"), ("cl", "d"), ("1", "l"), ("0", "o"), ("5", "s"))


def _canon(label: str) -> str:
    """One label, with the sequences that read alike folded together."""
    out = label.lower()
    for seq, canon in _CONFUSABLE:
        out = out.replace(seq, canon)
    return out


def _lookalike(domain: str, own: list[str]) -> str:
    """The one check a generic tool cannot make: is this a near-miss of a
    domain that belongs to THIS customer?

    Deliberately narrow — an edit distance of one on the label, or the same
    label under a different suffix. A looser rule turns every supplier into
    a warning, and a warning everybody dismisses is not a warning.
    """
    label = domain.split(".")[0].lower()
    for mine in own:
        mine = (mine or "").strip().lower()
        if not mine or mine == domain:
            continue
        mylabel = mine.split(".")[0]
        if label == mylabel:
            return mine                      # same name, different suffix
        if abs(len(label) - len(mylabel)) <= 1 and _one_edit(label, mylabel):
            return mine
        # Reads the same even though it does not spell the same.
        if _canon(label) == _canon(mylabel):
            return mine
    return ""


def _one_edit(a: str, b: str) -> bool:
    """True when `a` and `b` differ by at most one insert/delete/substitute."""
    if a == b:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long = (a, b) if len(a) < len(b) else (b, a)
    if len(long) - len(short) != 1:
        return False
    for i in range(len(long)):
        if long[:i] + long[i + 1:] == short:
            return True
    return False


def check(address: str, *, own_domains: list[str] | None = None,
          reputation_note: str = "") -> EmailVerdict:
    """Everything Vault can say about one address, from DNS alone."""
    address = (address or "").strip()
    m = _ADDR.match(address)
    if not m:
        return EmailVerdict(address=address, verdict="unknown",
                            headline="That does not look like an email address.")
    domain = m.group(1).lower().rstrip(".")
    v = EmailVerdict(address=address, domain=domain, reputation_note=reputation_note)

    mx = _dig(domain, "MX")
    a = _dig(domain, "A") or _dig(domain, "AAAA")
    if not mx and not a:
        v.verdict = "forged"
        v.headline = f"{domain} does not exist."
        v.findings.append(Finding("bad", "The domain has no DNS records at all. Nothing can "
                                         "legitimately send from it, so a letter claiming to "
                                         "be from this address is forged."))
        return v

    spf = next((t for t in _dig(domain, "TXT") if t.lower().startswith("v=spf1")), "")
    dmarc_txt = next((t for t in _dig("_dmarc." + domain, "TXT") if t.lower().startswith("v=dmarc1")), "")
    policy = (re.search(r"\bp=(\w+)", dmarc_txt) or [None, ""])[1] or ""

    spoofable = not spf or policy not in ("quarantine", "reject")
    if spoofable:
        if not spf and not dmarc_txt:
            why = "publishes neither SPF nor DMARC"
        elif not spf:
            why = "publishes no SPF record"
        elif not dmarc_txt:
            why = "publishes SPF but no DMARC"
        else:
            why = f"has DMARC set to p={policy or 'none'}, which asks receivers to do nothing"
        v.findings.append(Finding(
            "warn", f"Anyone can forge this address: {domain} {why}, so a receiving server "
                    f"has no instruction to reject a letter that only claims to be from it."))
    else:
        v.findings.append(Finding(
            "good", f"{domain} publishes SPF and enforces DMARC (p={policy}), so a forged "
                    f"letter from it should be rejected before it arrives."))

    if not mx:
        v.findings.append(Finding(
            "warn", f"{domain} accepts no mail — it has no MX record. It can send but cannot "
                    f"be replied to, which is normal for bulk senders and common for "
                    f"throwaway ones."))

    if domain in DISPOSABLE:
        v.findings.append(Finding(
            "bad", "This is a throwaway mail provider. The address can be abandoned the "
                   "moment it has been used, and nobody stands behind it."))
    elif domain in CONSUMER:
        v.findings.append(Finding(
            "info", "This is a free consumer mailbox. Ordinary for a person — worth a second "
                    "look if the letter claims to be from a company."))

    near = _lookalike(domain, own_domains or [])
    if near:
        v.findings.append(Finding(
            "bad", f"This closely resembles one of your own domains ({near}). A near-miss of "
                   f"a domain you own is the usual shape of an invoice-redirect attempt."))

    worst = next((f.tone for f in v.findings if f.tone == "bad"), "") or \
            next((f.tone for f in v.findings if f.tone == "warn"), "")
    v.verdict = {"bad": "risky", "warn": "risky"}.get(worst, "unproven")
    if v.verdict == "risky":
        v.headline = f"Treat {domain} with caution."
    else:
        # Never "safe". No check here can establish that, and a green tick
        # on a well-run phishing domain is worse than no tool at all.
        v.headline = f"Nothing wrong found with {domain}."

    v.limits = [
        "Vault checked the domain, not the person. A real domain can still be "
        "operated by somebody dishonest.",
        "It cannot tell whether this particular letter really came from here — only "
        "whether the domain makes forgery easy.",
    ]
    if not reputation_note:
        v.limits.append("No reputation service is switched on, so Vault has not asked "
                        "anyone whether this address has been seen in spam or breaches.")
    return v
