"""Money-leak finder: the recurring charges in his mail, once a month.

It reads receipts and renewal notices (read-only, `search.find`), has the
LOCAL model pick out the merchant, the amount and what kind of email it is
(`Need.PRIVATE`: personal finance never leaves the box; the email is
wrapped by `wrap_untrusted`), and then decides everything else in code:

* **Grounded.** An amount is kept only if it is written in the email, and
  is shown as "amount unknown" otherwise -- never estimated. The merchant is
  the name in the email, or failing that the sender's own name; a plan term
  ("monthly") only when the email says it.
* **Cadence** comes from the dates of two or more receipts, never from the
  model. One receipt is "cadence unknown" unless the email states the term.
* **Flags**, each with the reason written out: a charge that landed twice
  inside one billing period; the same merchant billed in two mailboxes;
  two services of the same kind; and "not seen used" -- only from real
  browsing data (`browser_rollup`), and "usage unknown" when there is none.

It NEVER cancels anything and has no action: the report is for Miro to
decide. It lands as a monthly report (`reports`, kind `money_leaks`), which
the vault export turns into a note, and as one plain Signal.
"""
from __future__ import annotations

import asyncio
import email.utils
import logging
import os
import re
import statistics
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from typing import Any

from ...kernel.contracts import Severity, Signal
from . import grounding as g

log = logging.getLogger("atlas.mail.money")

KIND = "money_leaks"
KINDS = ("receipt", "renewal_notice", "trial_ending", "other")
TERMS = ("weekly", "monthly", "quarterly", "yearly")
TERM_WORDS = {"weekly": ("weekly", "per week", "/week", "a week"),
              "monthly": ("monthly", "per month", "/month", "a month", "/mo"),
              "quarterly": ("quarterly", "per quarter", "every 3 months"),
              "yearly": ("yearly", "annual", "per year", "/year", "a year")}
TERM_DAYS = {"weekly": 7, "monthly": 30, "quarterly": 91, "yearly": 365}
CATEGORIES = ("video_streaming", "music_streaming", "cloud_storage", "software",
              "ai_tools", "hosting", "news", "fitness", "vpn", "phone_internet",
              "other")
GMAIL_QUERY = (
    "{subject:receipt subject:invoice subject:renewal subject:renewed "
    "subject:subscription subject:billing subject:charged subject:payment "
    'subject:"your order" subject:"will renew" subject:"trial ends"}')
IMAP_SUBJECTS = ("receipt", "invoice", "renewal", "subscription", "payment",
                 "billing")
SEARCH_DAYS = 400
SEARCH_LIMIT = 80
CAP_PER_PASS = 40
PASSES = 3
USAGE_DAYS = 45

SYSTEM = (
    "This is a receipt, invoice or renewal email of Miro's. Reply with JSON only:\n"
    '{"merchant": the company charging, exactly as written, or null, '
    '"amount": the amount charged exactly as written, with its currency symbol '
    'or code (e.g. "€9.99"), or null, '
    '"kind": "receipt" (money was taken) | "renewal_notice" (it will renew) | '
    '"trial_ending" | "other" (not a charge: a shipping update, an offer), '
    '"plan_term": "weekly" | "monthly" | "quarterly" | "yearly" ONLY if the '
    'email states the billing period, else null, '
    '"category": one of ' + str(list(CATEGORIES)) + "}\n"
    "Copy values exactly as written. Never infer an amount that is not in the "
    "email.")

CHARGES_SQL = (
    "SELECT message_id, account, sender_domain, merchant, merchant_key, kind, "
    "amount, currency, amount_text, plan_term, category, seen_on "
    "FROM money_charges WHERE seen_on >= $1")

_state: dict[str, Any] = {}
_task: asyncio.Task | None = None
_pending: list[Signal] = []


# ---------------------------------------------------------------- grounding

_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
_CODES = ("USD", "EUR", "GBP", "LBP", "AED", "CHF", "CAD", "AUD", "SAR", "JPY")
_AMOUNT = re.compile(
    r"(?P<pre>[$€£¥]|\b(?:" + "|".join(_CODES) + r"))?\s*"
    r"(?P<num>\d{1,3}(?:[,.  ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
    r"\s*(?P<post>[$€£¥]|(?:" + "|".join(_CODES) + r")\b)?", re.IGNORECASE)


def parse_amount(text: str | None) -> tuple[Decimal, str | None] | None:
    """(amount, currency code or None) from an amount as written, or None."""
    if not text:
        return None
    m = _AMOUNT.search(text)
    if not m:
        return None
    num = m["num"].replace(" ", "").replace(" ", "")
    if "," in num and "." in num:
        decimal_mark = "," if num.rfind(",") > num.rfind(".") else "."
    elif "," in num:
        decimal_mark = "," if len(num) - num.rfind(",") - 1 in (1, 2) else ""
    elif "." in num:
        decimal_mark = "." if len(num) - num.rfind(".") - 1 in (1, 2) else ""
    else:
        decimal_mark = ""
    thousands = {",", "."} - {decimal_mark}
    for sep in thousands:
        num = num.replace(sep, "")
    if decimal_mark:
        num = num.replace(decimal_mark, ".")
    try:
        value = Decimal(num)
    except InvalidOperation:
        return None
    cur = m["pre"] or m["post"]
    code = _SYMBOLS.get(cur, cur.upper()) if cur else None
    return value, code


def sender_domain(sender: str) -> str | None:
    """The registrable part of the sender's domain: receipts.spotify.com ->
    spotify.com, mail.mea.com.lb -> mea.com.lb."""
    _name, addr = email.utils.parseaddr(sender or "")
    if "@" not in addr:
        return None
    labels = addr.rsplit("@", 1)[1].lower().strip(".").split(".")
    if len(labels) < 2:
        return None
    if (len(labels) >= 3 and len(labels[-1]) == 2
            and labels[-2] in ("co", "com", "org", "net", "gov", "ac", "edu")):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def merchant_key(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    words = [w for w in words if w not in ("inc", "ltd", "llc", "limited", "gmbh",
                                           "sa", "sarl", "plc", "the", "com")]
    return " ".join(words)[:60]


def validate(raw: object, text: str, sender: str) -> dict | None:
    """One model reading, kept only as far as the email bears it out."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind") if raw.get("kind") in KINDS else "other"
    merchant = g.text_or_none(raw.get("merchant"), text, limit=60)
    if merchant is None:
        # The sender's own display name is a fact from the header, not a
        # guess; the domain if there is no name.
        name, _addr = email.utils.parseaddr(sender or "")
        merchant = (name.strip() or sender_domain(sender) or "unknown merchant")[:60]
    amount_text = g.text_or_none(raw.get("amount"), text, limit=30)
    parsed = parse_amount(amount_text)
    term = raw.get("plan_term")
    low = g.norm(text)
    term = term if term in TERMS and any(w in low for w in TERM_WORDS[term]) else None
    category = raw.get("category") if raw.get("category") in CATEGORIES else "other"
    return {"kind": kind, "merchant": merchant,
            "merchant_key": merchant_key(merchant) or (sender_domain(sender) or ""),
            "amount": parsed[0] if parsed else None,
            "currency": parsed[1] if parsed else None,
            "amount_text": amount_text if parsed else None,
            "plan_term": term, "category": category}


async def extract(msg: dict) -> dict | None:
    """One email's charge, grounded, or None when the model's reply was not
    JSON. Raises LocalModelUnavailable when there is no local model."""
    from ...kernel.llm import Message, Need, think
    from ...kernel.permissions import wrap_untrusted

    text = f"From: {msg['sender']}\nSubject: {msg['subject']}\n\n{msg['body']}"
    reply = await think(Need.PRIVATE, SYSTEM,
                        [Message("user", wrap_untrusted(text, "mail:money"))],
                        max_tokens=400)
    parsed = g.json_object(reply.text)
    if parsed is None:
        return None
    return validate(parsed, text, msg["sender"])


# ---------------------------------------------------------------- the sweep

@dataclass
class SweepResult:
    at: datetime
    read: int = 0
    stored: int = 0
    unparsed: int = 0
    waiting: int = 0
    model_down: str | None = None
    unreadable: list[str] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)


async def sweep(accounts=None, *, cap: int = CAP_PER_PASS) -> SweepResult:
    """Read new receipts and renewal emails, at most `cap` model calls."""
    from ...kernel.llm import LocalModelUnavailable
    from ...kernel.state import execute_sql, fetch_all
    from .._http import redact
    from .accounts import all_accounts
    from .search import find

    result = SweepResult(at=datetime.now(UTC))
    seen = {r["message_id"] for r in await fetch_all(
        "SELECT message_id FROM mail_extractions WHERE agent = 'money'")}
    budget = cap
    for account in (all_accounts() if accounts is None else accounts):
        who = account.email or account.name
        if not account.configured:
            result.unreadable.append(f"{who}: not configured")
            continue
        try:
            messages = await find(account, gmail_query=GMAIL_QUERY,
                                  imap_subjects=IMAP_SUBJECTS, days=SEARCH_DAYS,
                                  limit=SEARCH_LIMIT)
        except Exception as exc:                        # noqa: BLE001
            result.unreadable.append(f"{who}: {redact(exc)}"[:160])
            continue
        result.accounts.append(who)
        for msg in messages:
            if msg["id"] in seen:
                continue
            if budget <= 0:
                result.waiting += 1
                continue
            budget -= 1
            try:
                charge = await extract(msg)
            except LocalModelUnavailable as exc:
                result.model_down = exc.reason[:200]
                return result
            result.read += 1
            seen.add(msg["id"])
            outcome = "unparsed" if charge is None else (
                "none" if charge["kind"] == "other" else "found")
            if outcome == "unparsed":
                result.unparsed += 1
            if outcome == "found":
                at = msg.get("at")
                await execute_sql(
                    """INSERT INTO money_charges
                         (message_id, account, sender_domain, merchant,
                          merchant_key, kind, amount, currency, amount_text,
                          plan_term, category, seen_on, subject)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
                       ON CONFLICT (message_id) DO NOTHING""",
                    msg["id"], msg["account"], sender_domain(msg["sender"]),
                    charge["merchant"], charge["merchant_key"], charge["kind"],
                    charge["amount"], charge["currency"], charge["amount_text"],
                    charge["plan_term"], charge["category"],
                    at.date() if at else None, (msg["subject"] or "")[:200])
                result.stored += 1
            await execute_sql(
                """INSERT INTO mail_extractions (agent, message_id, account, outcome)
                   VALUES ('money', $1, $2, $3)
                   ON CONFLICT (agent, message_id) DO NOTHING""",
                msg["id"], msg["account"], outcome)
    return result


# ------------------------------------------------------------ the findings

@dataclass
class Recurring:
    merchant: str
    key: str
    domain: str | None
    amount: Decimal | None
    currency: str | None
    cadence: str
    last_seen: date
    receipts: int
    category: str
    accounts: set[str] = field(default_factory=set)
    flags: list[str] = field(default_factory=list)
    usage: str = ""

    def line(self) -> str:
        money = (f"{self.currency + ' ' if self.currency else ''}{self.amount:.2f}"
                 if self.amount is not None else "amount unknown")
        flags = "".join(f" ⚑ {f}." for f in self.flags)
        usage = f" {self.usage}." if self.usage and not any(
            f.startswith("not seen used") for f in self.flags) else ""
        return (f"- {self.merchant} — {money}, {self.cadence}, last seen "
                f"{self.last_seen:%Y-%m-%d} ({self.receipts} receipt(s)){flags}{usage}")


def cadence_of(days: list[date]) -> str | None:
    """weekly | monthly | quarterly | yearly | irregular from two or more
    dates, else None."""
    days = sorted(set(days))
    if len(days) < 2:
        return None
    gap = statistics.median((b - a).days for a, b in pairwise(days))
    for term, (lo, hi) in (("weekly", (5, 9)), ("monthly", (25, 35)),
                           ("quarterly", (80, 100)), ("yearly", (330, 400))):
        if lo <= gap <= hi:
            return term
    return "irregular"


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _amount(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _used(domain: str | None, usage: dict[str, int]) -> int:
    if not domain:
        return 0
    return sum(v for d, v in usage.items() if d == domain or d.endswith("." + domain))


def find_leaks(rows: list[dict], usage: dict[str, int] | None) -> list[Recurring]:
    """The recurring charges among stored rows, flagged. Pure: no I/O."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("kind") == "other" or _as_date(row.get("seen_on")) is None:
            continue
        key = row.get("merchant_key") or row.get("sender_domain") or row["merchant"]
        groups.setdefault(key, []).append(row)

    out: list[Recurring] = []
    for key, items in groups.items():
        items.sort(key=lambda r: _as_date(r["seen_on"]))
        receipts = [r for r in items if r["kind"] == "receipt"]
        cadence = cadence_of([_as_date(r["seen_on"]) for r in receipts])
        stated = next((r["plan_term"] for r in reversed(items) if r.get("plan_term")),
                      None)
        notice = any(r["kind"] in ("renewal_notice", "trial_ending") for r in items)
        if cadence in TERMS:
            said = cadence
        elif stated:
            said = f"{stated} (as the email states)"
        else:
            said = "cadence unknown"
        if cadence not in TERMS and not stated and not notice:
            continue                         # one-off or irregular: not recurring
        priced = [r for r in receipts if _amount(r.get("amount")) is not None]
        latest = priced[-1] if priced else None
        last = items[-1]
        rec = Recurring(
            merchant=str(last["merchant"]), key=key, domain=last.get("sender_domain"),
            amount=_amount(latest["amount"]) if latest else None,
            currency=latest.get("currency") if latest else None,
            cadence=said, last_seen=_as_date(last["seen_on"]),
            receipts=len(receipts),
            category=str(last.get("category") or "other"),
            accounts={str(r["account"]) for r in receipts})
        _twice(rec, receipts, cadence or stated)
        if len(rec.accounts) > 1:
            rec.flags.append(f"billed in {len(rec.accounts)} mailboxes "
                             f"({', '.join(sorted(rec.accounts))}): possibly two "
                             f"subscriptions")
        if usage is None:
            rec.usage = "usage unknown (no browsing data)"
        elif not rec.domain:
            rec.usage = "usage unknown (no sender domain)"
        elif _used(rec.domain, usage) == 0:
            rec.flags.append(f"not seen used: no visits to {rec.domain} in the last "
                             f"{USAGE_DAYS} days of browser history")
        else:
            rec.usage = (f"{_used(rec.domain, usage)} visit(s) to {rec.domain} in "
                         f"{USAGE_DAYS} days")
        out.append(rec)

    by_category: dict[str, list[Recurring]] = {}
    for rec in out:
        if rec.category != "other":
            by_category.setdefault(rec.category, []).append(rec)
    for same in by_category.values():
        if len(same) > 1:
            for rec in same:
                others = ", ".join(r.merchant for r in same if r is not rec)
                rec.flags.append(f"same kind of service as {others} "
                                 f"({rec.category.replace('_', ' ')})")
    out.sort(key=lambda r: (-len(r.flags), r.merchant.lower()))
    return out


def _twice(rec: Recurring, receipts: list[dict], term: str | None) -> None:
    """Two receipts for the same amount closer together than half a billing
    period (three days when the period is not known)."""
    limit = TERM_DAYS[term] / 2 if term in TERM_DAYS else 3
    for a, b in pairwise(receipts):
        amount_a, amount_b = _amount(a.get("amount")), _amount(b.get("amount"))
        if amount_a is None or amount_a != amount_b:
            continue
        gap = (_as_date(b["seen_on"]) - _as_date(a["seen_on"])).days
        if gap < limit:
            rec.flags.append(f"charged twice: {_as_date(a['seen_on']):%Y-%m-%d} and "
                             f"{_as_date(b['seen_on']):%Y-%m-%d}, "
                             f"{amount_a:.2f} each")
            return


async def usage_by_domain(now: datetime) -> tuple[dict[str, int] | None, str]:
    """(visits per domain over USAGE_DAYS, what that is). None when there is
    no browsing data at all -- then nothing is ever called unused."""
    from ...kernel.state import fetch_all
    try:
        rows = await fetch_all(
            """SELECT domain, sum(visits) AS visits FROM browser_rollup
                WHERE day >= $1 GROUP BY domain""",
            (now - timedelta(days=USAGE_DAYS)).date())
    except Exception as exc:                            # noqa: BLE001
        return None, f"usage unknown: browser history unreadable ({type(exc).__name__})"
    if not rows:
        return None, "usage unknown: no browsing data in the last 45 days"
    return ({str(r["domain"]).lower(): int(r["visits"] or 0) for r in rows},
            f"usage from browser history, last {USAGE_DAYS} days")


def render(found: list[Recurring], *, at: datetime, sweep: SweepResult,
           usage_note: str) -> str:
    flagged = sum(1 for r in found if r.flags)
    where = ", ".join(sweep.accounts) or "no mailbox"
    lines = [
        (f"Read from mail at {at:%Y-%m-%d %H:%M} UTC: receipts and renewal emails "
         f"from the last {SEARCH_DAYS} days in {where}."),
        "ATLAS never cancels anything. Each line is for you to decide.",
        "",
        f"{len(found)} recurring charge(s); ⚑ {flagged} to review." if found
        else "No recurring charges found.",
        *(r.line() for r in found),
        "",
        f"Usage: {usage_note}.",
    ]
    if sweep.waiting:
        lines.append(f"{sweep.waiting} email(s) not read yet (the per-run limit); "
                     f"they are read next time.")
    if sweep.unparsed:
        lines.append(f"{sweep.unparsed} email(s) the local model answered "
                     f"unreadably; not counted.")
    for u in sweep.unreadable:
        lines.append(f"Not read: {u}")
    return "\n".join(lines)


# ------------------------------------------------------------ monthly run

def _brief_hour() -> int:
    try:
        return max(0, min(23, int(os.getenv("BRIEF_HOUR", "7"))))
    except ValueError:
        return 7


def _title(month: str) -> str:
    year, mon = (int(x) for x in month.split("-"))
    return f"Recurring charges — {date(year, mon, 1):%B %Y}"


async def run(month: str) -> tuple[str, str] | None:
    """Read, find, store the report. (title, body), or None when the local
    model was down -- a report that silently skipped his receipts would read
    as "no leaks"."""
    from ...kernel.state import fetch_all, fetch_one

    total = SweepResult(at=datetime.now(UTC))
    for _ in range(PASSES):
        result = await sweep()
        if result.model_down:
            _state.update(at=result.at, error=f"local model down: {result.model_down}")
            return None
        total.read += result.read
        total.stored += result.stored
        total.unparsed += result.unparsed
        total.waiting = result.waiting
        total.unreadable, total.accounts = result.unreadable, result.accounts
        if not result.waiting:
            break
    now = datetime.now(UTC)
    rows = await fetch_all(CHARGES_SQL, (now - timedelta(days=SEARCH_DAYS)).date())
    usage, usage_note = await usage_by_domain(now)
    found = find_leaks([dict(r) for r in rows], usage)
    title = _title(month)
    body = render(found, at=now, sweep=total, usage_note=usage_note)
    await fetch_one(
        """INSERT INTO reports (kind, title, body, delivered_via)
           VALUES ($1, $2, $3, $4) RETURNING id""", KIND, title, body, "signal")
    _state.update(at=now, error=None, flagged=sum(1 for r in found if r.flags),
                  count=len(found))
    return title, body


async def _run_claimed(window: str) -> None:
    from ...kernel import occurrence
    try:
        got = await run(window)
    except Exception as exc:                            # noqa: BLE001
        from .._http import redact
        log.warning("money-leak run failed: %s", redact(exc))
        _state.update(at=datetime.now(UTC), error=redact(exc)[:200])
        await occurrence.release("mail.money_leaks", window)
        return
    if got is None:
        await occurrence.release("mail.money_leaks", window)   # tried again later
        return
    await occurrence.done("mail.money_leaks", window)
    title, body = got
    head = body.split("\n\n", 1)[1].split("\n", 1)[0] if "\n\n" in body else title
    _pending.append(Signal(
        title=title,
        body=f"{head} The full list is in the vault (ATLAS/Money). "
             f"Nothing is cancelled; you decide.",
        severity=Severity.INFO))


async def maybe_run(now: datetime | None = None) -> bool:
    """Once a month, after BRIEF_HOUR: claim the month durably and read in
    the background. Returns whether a run was started."""
    global _task
    from ...kernel import background, occurrence

    now = now or datetime.now(UTC)
    local = now.astimezone()
    if local.hour < _brief_hour():
        return False
    if _task is not None and not _task.done():
        return False
    window = f"{local:%Y-%m}"
    if not await occurrence.claim("mail.money_leaks", window):
        return False
    _task = background.spawn(_run_claimed(window), "mail money-leak finder")
    return True


def take_signals() -> list[Signal]:
    out = list(_pending)
    _pending.clear()
    return out


def brief_line() -> str:
    at = _state.get("at")
    if at is None:
        return ""
    if _state.get("error"):
        return f"recurring charges not read ({_state['error'][:80]}, {at:%H:%M} UTC)"
    return (f"recurring charges: {_state['count']}, ⚑ {_state['flagged']} to review "
            f"(read {at:%Y-%m-%d})")


def vault_note(report: dict) -> tuple[str, str]:
    """(title, body) of the Money note from the latest report row: the flag
    count goes in the title, so it shows in the note list at a glance."""
    body = str(report.get("body") or "")
    m = re.search(r"⚑ (\d+) to review", body)
    flagged = int(m[1]) if m else 0
    title = (f"Recurring charges — ⚑ {flagged} to review" if flagged > 0
             else "Recurring charges")
    stamp = report.get("at")
    when = f"Report of {stamp:%Y-%m-%d}. " if isinstance(stamp, datetime) else ""
    return title, f"{when}{report.get('title') or ''}\n\n{body}"
