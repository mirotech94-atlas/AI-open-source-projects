"""Alinea watcher: the owner's Alinea Invest emails, read -- never acted on.

Alinea is a retail AI auto-investing app. It has no public API and it
trades inside the app, on its own, with the owner's money. ATLAS has no hands
there and gets none: placing a trade or moving money is money out, Tier 3,
permanently denied (CLAUDE.md, invariant 3). This file adds no action, and
nothing here gives buy/sell advice. It only reads what Alinea emails him:

* **Which emails** (`trust`, decided before any model sees the email; the
  Gmail query and IMAP subject words are only a pre-filter):
  - the From address is on ALINEA_DOMAINS or CUSTODIAN_DOMAINS, or a
    subdomain of one. A look-alike (`alinea-invest.com.evil.io`,
    `alinea.xyz`) is skipped unread;
  - custodian mail (DriveWealth serves many apps) counts only if it
    mentions Alinea, and gives a statement's balance only: Alinea sends
    its own deposit and trade emails, and the custodian's confirmation of
    the same deposit would count it twice;
  - alinea-invest.com publishes DMARC p=none, so a forged From is not
    rejected by anyone (Alinea handbook, read 2026-09-28). Where the
    mailbox exposes its own Authentication-Results (Gmail), the message is
    kept only with dkim=pass for that same domain or a subdomain of it;
  - over IMAP there is no such header to trust: the domain check alone
    decides, and the rows are stored `verified` NULL and shown unverified.
* **Reading.** The LOCAL model (`Need.PRIVATE`: his money stays on the box)
  reads each email once, wrapped by `wrap_untrusted`; its id is kept in
  `mail_extractions` under agent 'alinea'.
* **Grounding.** The model names each event and copies the line that
  states it. A figure is kept only if it is written in the email with its
  digits whole (`$1` is not in `$10.99`); a trade's ticker, side, quantity
  and price only if they are in that line. Anything else is NULL, shown as
  unknown -- never 0, never estimated (invariant 6).
* **Confirmations only.** A deposit, withdrawal or trade counts once the
  money moved or the order filled; the "initiated / pending / placed"
  notice before it is dropped, or one deposit would count twice.

Stored in `alinea_events`, one row per event. The markets module's
`alinea` check reads them (`markets/alinea.py`).
"""
from __future__ import annotations

import asyncio
import email.utils
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from . import grounding as g

log = logging.getLogger("atlas.mail.alinea")

# The sender domains that are Alinea. Exact registrable domains only: a
# wildcard TLD ("alinea.*") would accept alinea.xyz, which anyone can buy.
# Add one here only once a real Alinea email shows it.
ALINEA_DOMAINS: tuple[str, ...] = ("alinea-invest.com",)
# Alinea's custodian, which is required to send statements to clients
# directly (Alinea handbook, read 2026-09-28; its sender is unverified). It
# serves many apps, so its mail counts only if it mentions Alinea.
CUSTODIAN_DOMAINS: tuple[str, ...] = ("drivewealth.com",)
SENDER_DOMAINS = ALINEA_DOMAINS + CUSTODIAN_DOMAINS

KINDS = ("deposit", "trade", "statement", "dividend", "withdrawal", "fee")
SIDES = ("buy", "sell")
GMAIL_QUERY = ("{" + " ".join(f"from:{d}" for d in ALINEA_DOMAINS) + " "
               + " ".join(f"(from:{d} alinea)" for d in CUSTODIAN_DOMAINS) + "}")
IMAP_SUBJECTS = ("alinea", "deposit", "order", "trade", "statement", "dividend",
                 "withdrawal", "transfer")
# The benchmark needs every deposit. Asked for on their own first, so a burst
# of order-filled emails can't push one past SEARCH_LIMIT.
GMAIL_MOVES = (GMAIL_QUERY + " {subject:deposit subject:withdrawal "
               "subject:transfer subject:statement}")
IMAP_MOVES = ("deposit", "withdrawal", "transfer", "statement")
SEARCH_DAYS = 400
SEARCH_LIMIT = 40
CAP_PER_RUN = 10
MAX_EVENTS = 20
SWEEP_EVERY = timedelta(hours=6)

SYSTEM = (
    "This is an email from Alinea Invest, the owner's investing app. List what it "
    "reports. Reply with JSON only:\n"
    '{"events": [{"kind": "deposit" | "trade" | "statement" | "dividend" | '
    '"withdrawal" | "fee" | "notice", '
    '"line": the one line or sentence of the email that states this event, '
    "copied exactly, or null, "
    '"ticker": the stock symbol exactly as written, or null, '
    '"side": "buy" | "sell" for a trade, else null, '
    '"quantity": the number of shares exactly as written, or null, '
    '"price": the price per share exactly as written, or null, '
    '"amount": the money amount of this event exactly as written with its '
    'currency symbol (e.g. "$50.00"), or null, '
    '"balance": the account balance or portfolio value exactly as written, '
    "or null, "
    '"realized": a realised gain or loss exactly as written, or null, '
    '"fee": a fee charged (a subscription, a trade fee) exactly as written, '
    "or null}]}\n"
    "Report a deposit or withdrawal only once the money has moved, and a "
    "trade only once the order has filled: a requested, initiated, pending, "
    'scheduled or placed notice is kind "notice". '
    "One entry per trade; a statement is one entry, with its balance and "
    "any realised figure. Copy values exactly as written; never infer or "
    "compute a number that is not in the email. A marketing email, a "
    'newsletter or anything that reports no event: {"events": []}.')

_state: dict[str, Any] = {}
_task: asyncio.Task | None = None


# ----------------------------------------------------------------- sender

def sender_domain(sender: str) -> str | None:
    """The full domain of the one address in a From header, or None when
    there is not exactly one address."""
    found = [a for _n, a in email.utils.getaddresses([sender or ""]) if a]
    if len(found) != 1 or found[0].count("@") != 1:
        return None
    domain = found[0].rsplit("@", 1)[1].lower().strip().strip(".")
    return domain or None


def _under(domain: str, base: str) -> bool:
    return domain == base or domain.endswith("." + base)


def matched_domain(sender: str) -> str | None:
    """The SENDER_DOMAINS entry the From address is on (or under), else None."""
    domain = sender_domain(sender)
    if domain is None:
        return None
    return next((d for d in SENDER_DOMAINS if _under(domain, d)), None)


def is_alinea(sender: str) -> bool:
    """True only for an address on an Alinea or custodian domain, or a
    subdomain of one. The domain half of `trust`."""
    return matched_domain(sender) is not None


_DKIM_DOMAIN = re.compile(r"header\.(?:d=|i=[^@\s;]*@)([a-z0-9.-]+)", re.IGNORECASE)


def dkim_passes_for(auth: str, base: str) -> bool:
    """Does this Authentication-Results show dkim=pass signed by `base` or a
    subdomain of it? A pass for anyone else (sendgrid.net) does not count."""
    for part in auth.split(";"):
        part = part.strip()
        if not re.match(r"dkim\s*=\s*pass\b", part, re.IGNORECASE):
            continue
        m = _DKIM_DOMAIN.search(part)
        if m and _under(m[1].lower().strip("."), base):
            return True
    return False


def trust(msg: dict) -> str | None:
    """"verified", "unverified" (the mailbox shows no authentication result
    to read), "forged" (from a trusted domain, but its DKIM did not pass --
    not read, and counted so the check can say so), or None: not ours."""
    base = matched_domain(msg.get("sender") or "")
    if base is None:
        return None
    if base in CUSTODIAN_DOMAINS and "alinea" not in (
            f"{msg.get('subject') or ''}\n{msg.get('body') or ''}").lower():
        return None
    auth = msg.get("auth")
    if auth is None:
        return "unverified"
    return "verified" if dkim_passes_for(auth, base) else "forged"


def custodian_only(events: list[dict]) -> list[dict]:
    """What a custodian email may contribute: a statement's balance. Its
    deposits, trades, dividends, fees and realised figures would repeat
    the ones Alinea emails itself."""
    out = []
    for e in events:
        if e["kind"] == "statement" and e["balance"] is not None:
            out.append({**e, "realized": None, "fee": None, "amount": None,
                        "amount_text": None})
    return out


# -------------------------------------------------------------- grounding

_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}
_CODES = ("USD", "EUR", "GBP", "CAD")
_MONEY = re.compile(
    r"^(?P<sign>[-+−])?\s*(?P<pre>[$€£]|(?:" + "|".join(_CODES) + r")\b)?\s*"
    r"(?P<sign2>[-+−])?\s*"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<post>[$€£]|(?:" + "|".join(_CODES) + r"))?$", re.IGNORECASE)
_TICKER = re.compile(r"^[A-Z]{1,5}(?:\.[A-Z])?$")
# A notice that money or an order is on its way, not that it arrived: the
# confirmation follows in its own email, and counting both counts it twice.
_NOT_YET = re.compile(r"\b(?:request(?:ed)?|initiated|pending|scheduled|"
                      r"on its way|will arrive|placed|processing)\b", re.IGNORECASE)
_MOVES = ("deposit", "withdrawal", "trade")
_BUY = re.compile(r"\b(?:bought|buy|purchased?)\b", re.IGNORECASE)
_SELL = re.compile(r"\b(?:sold|sell|sale)\b", re.IGNORECASE)


def parse_money(text: object) -> tuple[Decimal, str | None, bool] | None:
    """(value, currency code or None, sign written) for an amount written
    as ONE figure -- "$1,250.00", "-$12.30", "40 USD" -- else None. Never
    truncates: "$1,234.5678" is 1234.5678, not 1234.56."""
    if not isinstance(text, str):
        return None
    m = _MONEY.match(" ".join(text.split()))
    if not m:
        return None
    try:
        value = Decimal(m["num"].replace(",", ""))
    except InvalidOperation:
        return None
    sign = m["sign"] or m["sign2"]
    if sign in ("-", "−"):
        value = -value
    cur = m["pre"] or m["post"]
    code = _SYMBOLS.get(cur, cur.upper()) if cur else None
    return value, code, bool(sign)


def figure_in(value: str, where: str) -> bool:
    """Is this figure written in `where` with its digits whole? "$1" is not
    in "$10.99", and "1" is not in "2026-09-01"."""
    m = re.search(r"\d[\d,]*(?:\.\d+)?", value)
    if not m or not g.appears(value, where):
        return False
    core = re.escape(m[0])
    return bool(re.search(rf"(?<![\d.,]){core}(?![\d]|[.,]\d)", where))


def _grounded_money(raw: object, where: str) -> tuple[Decimal, str | None,
                                                      bool, str] | None:
    if not isinstance(raw, str):
        return None
    written = " ".join(raw.split())[:40]
    parsed = parse_money(written)
    if parsed is None or not figure_in(written, where):
        return None
    return (*parsed, written)


def _quantity(raw: object, line: str | None) -> Decimal | None:
    if line is None or not isinstance(raw, (str, int, float)) \
            or isinstance(raw, bool):
        return None
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(?:shares?)?\s*", str(raw), re.IGNORECASE)
    if not m or not figure_in(m[1], line):
        return None
    try:
        q = Decimal(m[1])
    except InvalidOperation:
        return None
    return q if q > 0 else None


def _ticker(raw: object, line: str | None) -> str | None:
    if line is None or not isinstance(raw, str):
        return None
    t = raw.strip().lstrip("$")
    if not _TICKER.match(t):
        return None
    # Case-sensitive, whole word: "V" is Visa's ticker and in every word.
    hit = re.search(rf"(?<![A-Za-z0-9.]){re.escape(t)}(?![A-Za-z0-9])", line)
    return t if hit else None


def _side(raw: object, line: str | None) -> str | None:
    if line is None or raw not in SIDES:
        return None
    word = _BUY if raw == "buy" else _SELL
    other = _SELL if raw == "buy" else _BUY
    return raw if word.search(line) and not other.search(line) else None


def _realized(raw: object, where: str) -> Decimal | None:
    """A realised figure keeps its sign only when the email makes it plain:
    a written sign, or a line that says "gain" or "loss" but not both."""
    got = _grounded_money(raw, where)
    if got is None:
        return None
    value, _cur, signed, _written = got
    if signed:
        return value
    low = g.norm(where)
    if re.search(r"unreali[sz]ed", low):
        return None                    # a paper figure, not a realised one
    gain = bool(re.search(r"\b(?:gain|profit)s?\b", low))
    loss = bool(re.search(r"\bloss(?:es)?\b", low))
    if gain == loss:
        return None                    # "gain/loss: $12.30" -- which?
    return value if gain else -value


def _subject(text: str) -> str:
    m = re.search(r"^Subject: (.*)$", text, re.MULTILINE)
    return m[1] if m else ""


def validate(raw: object, text: str) -> dict | None:
    """One model-read event, kept only as far as the email bears it out.
    None when it is not one of KINDS."""
    if not isinstance(raw, dict) or raw.get("kind") not in KINDS:
        return None
    kind = raw["kind"]
    line = g.text_or_none(raw.get("line"), text, limit=400)
    where = line or text
    if kind in _MOVES and _NOT_YET.search(line or _subject(text)):
        return None
    trade = kind == "trade"
    amount = _grounded_money(raw.get("amount"), where)
    balance = _grounded_money(raw.get("balance"), where)
    price = _grounded_money(raw.get("price"), line) if trade and line else None
    fee = _grounded_money(raw.get("fee"), where)
    if kind == "fee" and fee is None:
        fee = amount                   # a fee email's amount is the fee
    currency = next((m[1] for m in (amount, balance, price, fee) if m and m[1]),
                    None)
    return {
        "kind": kind,
        "ticker": _ticker(raw.get("ticker"), line) if trade else None,
        "side": _side(raw.get("side"), line) if trade else None,
        "quantity": _quantity(raw.get("quantity"), line) if trade else None,
        "price": price[0] if price else None,
        "amount": amount[0] if amount else None,
        "amount_text": amount[3] if amount else None,
        "currency": currency,
        "balance": balance[0] if balance else None,
        "realized": _realized(raw.get("realized"), where),
        "fee": fee[0] if fee else None,
    }


def _source(msg: dict) -> str:
    return f"From: {msg['sender']}\nSubject: {msg['subject']}\n\n{msg['body']}"


async def extract(msg: dict) -> list[dict] | None:
    """The events in one Alinea email, grounded. None when the model's reply
    was not JSON. Raises LocalModelUnavailable when there is no local model:
    the email is then not marked read, and is read on a later sweep."""
    from ...kernel.llm import Message, Need, think
    from ...kernel.permissions import scan_for_injection, wrap_untrusted

    text = _source(msg)
    # Instruction-shaped text is logged, never obeyed: it is only ever data.
    if scan_for_injection(text, f"mail:{msg['sender']}"):
        log.warning("instruction-shaped Alinea email %s", msg["id"])
    reply = await think(Need.PRIVATE, SYSTEM,
                        [Message("user", wrap_untrusted(text, "mail:alinea"))],
                        max_tokens=1200)
    parsed = g.json_object(reply.text)
    if parsed is None or not isinstance(parsed.get("events"), list):
        return None
    events = (validate(e, text) for e in parsed["events"][:MAX_EVENTS])
    return [e for e in events if e]


# ---------------------------------------------------------------- the sweep

@dataclass
class SweepResult:
    at: datetime
    read: int = 0
    found: int = 0
    unparsed: int = 0
    skipped: int = 0                  # not a trusted Alinea sender: never read
    unverified: int = 0               # read on the domain alone (no auth header)
    forged: int = 0                   # a trusted From, but DKIM did not pass
    waiting: int = 0                  # left for the next sweep (the cap)
    model_down: str | None = None
    unreadable: list[str] = field(default_factory=list)


def row_id(message_id: str, index: int) -> str:
    return message_id if index == 0 else f"{message_id}#{index}"


async def sweep(accounts=None, *, cap: int = CAP_PER_RUN) -> SweepResult:
    """Read new Alinea emails in every mailbox, at most `cap` model calls."""
    from ...kernel.llm import LocalModelUnavailable
    from ...kernel.state import execute_sql, fetch_all
    from .._http import redact
    from .accounts import all_accounts
    from .search import find

    result = SweepResult(at=datetime.now(UTC))
    seen = {r["message_id"] for r in await fetch_all(
        "SELECT message_id FROM mail_extractions WHERE agent = 'alinea'")}
    budget = cap
    for account in (all_accounts() if accounts is None else accounts):
        who = account.email or account.name
        if not account.configured:
            result.unreadable.append(f"{who}: not configured")
            continue
        try:
            messages: list[dict] = []
            for query, subjects in ((GMAIL_MOVES, IMAP_MOVES),
                                    (GMAIL_QUERY, IMAP_SUBJECTS)):
                got = await find(account, gmail_query=query,
                                 imap_subjects=subjects, days=SEARCH_DAYS,
                                 limit=SEARCH_LIMIT)
                have = {m["id"] for m in messages}
                messages += [m for m in got if m["id"] not in have]
        except Exception as exc:                        # noqa: BLE001
            result.unreadable.append(f"{who}: {redact(exc)}"[:160])
            continue
        for msg in messages:
            if msg["id"] in seen:
                continue
            trusted = trust(msg)
            if trusted is None:
                result.skipped += 1
                continue
            if trusted == "forged":
                result.forged += 1
                continue
            if budget <= 0:
                result.waiting += 1
                continue
            budget -= 1
            try:
                events = await extract(msg)
            except LocalModelUnavailable as exc:
                result.model_down = exc.reason[:200]
                return result
            result.read += 1
            result.unverified += trusted == "unverified"
            seen.add(msg["id"])
            if events is None:
                result.unparsed += 1
                await _mark(msg, "unparsed")
                continue
            if matched_domain(msg["sender"]) in CUSTODIAN_DOMAINS:
                events = custodian_only(events)
            at = msg.get("at")
            for index, e in enumerate(events):
                await execute_sql(
                    """INSERT INTO alinea_events
                         (message_id, account, kind, ticker, side, quantity,
                          price, amount, currency, amount_text, balance,
                          realized, seen_on, subject, fee, verified)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,
                               $15,$16)
                       ON CONFLICT (message_id) DO NOTHING""",
                    row_id(msg["id"], index), msg["account"], e["kind"],
                    e["ticker"], e["side"], e["quantity"], e["price"],
                    e["amount"], e["currency"], e["amount_text"], e["balance"],
                    e["realized"], at.date() if at else None,
                    (msg["subject"] or "")[:200], e["fee"],
                    True if trusted == "verified" else None)
            result.found += len(events)
            await _mark(msg, "found" if events else "none")
    return result


async def _mark(msg: dict, outcome: str) -> None:
    from ...kernel.state import execute_sql
    await execute_sql(
        """INSERT INTO mail_extractions (agent, message_id, account, outcome)
           VALUES ('alinea', $1, $2, $3)
           ON CONFLICT (agent, message_id) DO NOTHING""",
        msg["id"], msg["account"], outcome)


async def _run_sweep() -> None:
    try:
        result = await sweep()
    except Exception as exc:                            # noqa: BLE001
        from .._http import redact
        log.warning("alinea sweep failed: %s", redact(exc))
        _state.update(at=datetime.now(UTC), error=redact(exc)[:200])
        return
    _state.update(at=result.at, error=None, result=result)


def maybe_sweep(now: datetime | None = None) -> bool:
    """Start a sweep in the background if none is running and the last one
    started more than SWEEP_EVERY ago. Returns whether one was started."""
    global _task
    from ...kernel import background

    now = now or datetime.now(UTC)
    if _task is not None and not _task.done():
        return False
    started = _state.get("started")
    if started is not None and now - started < SWEEP_EVERY:
        return False
    _state["started"] = now
    _task = background.spawn(_run_sweep(), "mail alinea sweep")
    return True


def brief_line() -> str:
    """What the last sweep could not do. Nothing when it went normally."""
    at = _state.get("at")
    if at is None:
        return ""
    if _state.get("error"):
        return f"alinea: emails not read ({_state['error'][:80]}, {at:%H:%M} UTC)"
    result: SweepResult = _state["result"]
    if result.model_down:
        return f"alinea: emails not read, local model down ({at:%H:%M} UTC)"
    if result.unreadable:
        return (f"alinea: {len(result.unreadable)} mailbox(es) not read "
                f"({at:%H:%M} UTC)")
    if result.forged:
        return (f"alinea: {result.forged} email(s) from an Alinea domain not read: "
                f"no DKIM pass ({at:%H:%M} UTC)")
    return ""
