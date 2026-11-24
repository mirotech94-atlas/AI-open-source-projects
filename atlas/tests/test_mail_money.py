"""Money-leak finder (`atlas/modules/mail/money.py`).

What matters: the model runs LOCAL on wrapped email; an amount not written in
the email is "amount unknown", never a number; cadence comes from receipt
dates in code; "not seen used" needs real browsing data; the report goes
through the report table and the vault; and nothing can cancel anything.
"""
from __future__ import annotations

import json
import pathlib
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from atlas.kernel import occurrence
from atlas.kernel.llm import Completion, LocalModelUnavailable, Need
from atlas.modules import mail
from atlas.modules.mail import money
from atlas.modules.mail.accounts import Account

RECEIPT = """Thanks for your payment.
Spotify Premium Individual
Total: €10.99 per month
Date: 12 September 2026"""
SENDER = "Spotify <no-reply@receipts.spotify.com>"
MSG = {"id": "gmail:r1", "account": "me@example.com", "subject": "Your receipt",
       "sender": SENDER, "at": datetime(2026, 9, 12, tzinfo=UTC), "body": RECEIPT}
TEXT = f"From: {SENDER}\nSubject: Your receipt\n\n{RECEIPT}"


# ---------------------------------------------------------------- grounding

@pytest.mark.parametrize(("text", "want"), [
    ("€10.99", (Decimal("10.99"), "EUR")),
    ("USD 1,299.00", (Decimal("1299.00"), "USD")),
    ("9,99 €", (Decimal("9.99"), "EUR")),
    ("£12", (Decimal(12), "GBP")),
    ("1.299,50 EUR", (Decimal("1299.50"), "EUR")),
    ("14.00", (Decimal("14.00"), None)),
    ("free", None),
    (None, None),
])
def test_amounts_are_parsed_as_written(text, want):
    assert money.parse_amount(text) == want


def test_a_reading_the_email_bears_out_is_kept():
    c = money.validate({"merchant": "Spotify", "amount": "€10.99",
                        "kind": "receipt", "plan_term": "monthly",
                        "category": "music_streaming"}, TEXT, SENDER)
    assert c["amount"] == Decimal("10.99") and c["currency"] == "EUR"
    assert c["plan_term"] == "monthly"
    assert c["merchant"] == "Spotify"


def test_an_invented_amount_is_unknown_never_a_number():
    """A stub model that hallucinates: the amount, merchant and term it made
    up are not in the email, so none of them is kept."""
    c = money.validate({"merchant": "Netflix", "amount": "€15.99",
                        "kind": "receipt", "plan_term": "yearly",
                        "category": "made_up"}, TEXT, SENDER)
    assert c["amount"] is None and c["currency"] is None
    assert c["amount_text"] is None
    assert c["plan_term"] is None
    assert c["merchant"] == "Spotify"          # the sender's own name, a fact
    assert c["category"] == "other"


def test_sender_domain_is_the_registrable_part():
    assert money.sender_domain(SENDER) == "spotify.com"
    assert money.sender_domain("MEA <x@mail.mea.com.lb>") == "mea.com.lb"
    assert money.sender_domain("nobody") is None


# ----------------------------------------------------------------- extract

async def test_extract_runs_on_the_local_model_with_the_email_wrapped(monkeypatch):
    seen = []

    async def think(need, system, messages, **kw):
        seen.append((need, messages[0].content))
        return Completion(json.dumps({"merchant": "Spotify", "amount": "€10.99",
                                      "kind": "receipt"}), "ollama", "local")
    monkeypatch.setattr("atlas.kernel.llm.think", think)

    charge = await money.extract(MSG)

    assert charge["amount"] == Decimal("10.99")
    (need, content), = seen
    assert need is Need.PRIVATE
    assert content.startswith("<untrusted_data")


async def test_private_routing_never_reaches_the_cloud(local_model_down):
    with pytest.raises(LocalModelUnavailable):
        await money.extract(MSG)
    assert local_model_down == []


# ------------------------------------------------------------------- leaks

def _row(merchant, day, amount="9.99", kind="receipt", account="a@x", **kw):
    return {"message_id": f"{merchant}{day}{account}", "account": account,
            "sender_domain": kw.get("domain", f"{merchant.lower()}.com"),
            "merchant": merchant, "merchant_key": merchant.lower(), "kind": kind,
            "amount": Decimal(amount) if amount else None,
            "currency": "EUR" if amount else None, "amount_text": None,
            "plan_term": kw.get("term"), "category": kw.get("category", "other"),
            "seen_on": day}


def test_cadence_comes_from_receipt_dates():
    assert money.cadence_of([date(2026, 7, 12), date(2026, 8, 12),
                             date(2026, 9, 12)]) == "monthly"
    assert money.cadence_of([date(2025, 9, 1), date(2026, 9, 1)]) == "yearly"
    assert money.cadence_of([date(2026, 9, 1)]) is None
    assert money.cadence_of([date(2026, 9, 1), date(2026, 9, 20)]) == "irregular"


def test_recurring_charges_are_listed_and_one_offs_are_not():
    rows = [_row("Spotify", date(2026, m, 12)) for m in (7, 8, 9)]
    rows += [_row("Amazon", date(2026, 9, 3), "54.10")]           # a one-off
    rows += [_row("Dropbox", date(2026, 9, 1), None, kind="renewal_notice")]
    found = {r.merchant: r for r in money.find_leaks(rows, {"spotify.com": 4,
                                                            "dropbox.com": 1})}
    assert set(found) == {"Spotify", "Dropbox"}
    assert found["Spotify"].cadence == "monthly"
    assert found["Spotify"].amount == Decimal("9.99")
    assert found["Dropbox"].amount is None
    assert found["Dropbox"].cadence == "cadence unknown"
    assert "amount unknown" in found["Dropbox"].line()
    assert found["Spotify"].flags == []


def test_a_single_receipt_takes_the_term_only_from_the_email():
    rows = [_row("Notion", date(2026, 9, 5), term="yearly")]
    (rec,) = money.find_leaks(rows, {"notion.com": 3})
    assert rec.cadence == "yearly (as the email states)"


def test_duplicates_are_flagged():
    rows = [_row("Spotify", date(2026, m, 12)) for m in (7, 8, 9)]
    rows += [_row("Spotify", date(2026, 9, 14))]                    # twice
    rows += [_row("Netflix", date(2026, m, 1), "15.99", category="video_streaming")
             for m in (8, 9)]
    rows += [_row("Disney", date(2026, m, 2), "8.99", category="video_streaming")
             for m in (8, 9)]
    rows += [_row("iCloud", date(2026, m, 3), "2.99", account=acct)
             for m in (8, 9) for acct in ("a@x", "b@y")]
    usage = {"spotify.com": 1, "netflix.com": 1, "disney.com": 1, "icloud.com": 1}
    found = {r.merchant: r for r in money.find_leaks(rows, usage)}
    assert any("charged twice" in f for f in found["Spotify"].flags)
    assert any("same kind of service as Disney" in f for f in found["Netflix"].flags)
    assert any("2 mailboxes" in f for f in found["iCloud"].flags)


def test_not_seen_used_needs_real_browsing_data():
    rows = [_row("Spotify", date(2026, m, 12)) for m in (7, 8, 9)]
    (unused,) = money.find_leaks(rows, {"github.com": 30})
    assert any(f.startswith("not seen used") for f in unused.flags)
    (visited,) = money.find_leaks(rows, {"open.spotify.com": 12})
    assert visited.flags == []
    (unknown,) = money.find_leaks(rows, None)
    assert unknown.flags == []
    assert "usage unknown" in unknown.line()


# ------------------------------------------------------------- monthly run

async def test_monthly_run_stores_a_report_and_one_plain_signal(monkeypatch):
    monkeypatch.setenv("BRIEF_HOUR", "0")
    windows: list = []
    released: list = []

    async def claim(job, window):
        windows.append((job, window))
        return len(windows) == 1

    async def done(job, window):
        return None

    async def release(job, window):
        released.append(window)
    monkeypatch.setattr(occurrence, "claim", claim)
    monkeypatch.setattr(occurrence, "done", done)
    monkeypatch.setattr(occurrence, "release", release)

    async def sweep(accounts=None, **kw):
        return money.SweepResult(at=datetime.now(UTC), accounts=["me@example.com"])
    monkeypatch.setattr(money, "sweep", sweep)

    rows = [_row("Spotify", date(2026, m, 12)) for m in (7, 8, 9)]
    reports: list = []

    async def fetch_all(sql, *args):
        if "FROM money_charges" in sql:
            return rows
        if "FROM browser_rollup" in sql:
            return []
        raise AssertionError(sql)

    async def fetch_one(sql, *args):
        assert "INSERT INTO reports" in sql
        reports.append(args)
        return {"id": 1}
    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)
    monkeypatch.setattr("atlas.kernel.state.fetch_one", fetch_one)

    now = datetime(2026, 10, 1, 9, tzinfo=UTC)
    assert await money.maybe_run(now) is True
    await money._task
    assert await money.maybe_run(now) is False      # the month is claimed

    (kind, title, body, _via), = reports
    assert kind == "money_leaks"
    assert title == "Recurring charges — October 2026"
    assert "Spotify — EUR 9.99, monthly" in body
    assert "never cancels" in body
    assert "usage unknown: no browsing data" in body
    (sig,) = money.take_signals()
    assert sig.suggests is None and sig.params == {}
    assert money.take_signals() == []
    assert released == []


async def test_local_model_down_releases_the_month_and_stores_nothing(monkeypatch):
    monkeypatch.setenv("BRIEF_HOUR", "0")
    released: list = []

    async def claim(job, window):
        return True

    async def release(job, window):
        released.append(window)
    monkeypatch.setattr(occurrence, "claim", claim)
    monkeypatch.setattr(occurrence, "release", release)

    async def sweep(accounts=None, **kw):
        return money.SweepResult(at=datetime.now(UTC), model_down="no local model")
    monkeypatch.setattr(money, "sweep", sweep)

    async def fetch_one(sql, *args):
        raise AssertionError("no report may be stored without the receipts read")
    monkeypatch.setattr("atlas.kernel.state.fetch_one", fetch_one)

    assert await money.maybe_run(datetime(2026, 10, 1, 9, tzinfo=UTC))
    await money._task
    assert released == ["2026-10"]
    assert money.take_signals() == []
    assert "local model down" in money.brief_line()


async def test_sweep_stores_grounded_charges(monkeypatch):
    sql: list = []

    async def fetch_all(q, *a):
        return []

    async def execute_sql(q, *a):
        sql.append((q, a))
    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)
    monkeypatch.setattr("atlas.kernel.state.execute_sql", execute_sql)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "s")

    async def find(account, **kw):
        return [MSG]
    monkeypatch.setattr("atlas.modules.mail.search.find", find)

    async def extract(msg):
        return money.validate({"merchant": "Spotify", "amount": "€99.99",
                               "kind": "receipt"}, TEXT, SENDER)
    monkeypatch.setattr(money, "extract", extract)

    me = Account(name="myron", kind="google", email="me@example.com",
                 refresh_token="t")
    result = await money.sweep([me])

    assert result.stored == 1
    (insert,) = [a for q, a in sql if "INSERT INTO money_charges" in q]
    assert insert[6] is None                 # €99.99 is not in the email
    assert insert[11] == date(2026, 9, 12)   # the email's own date


# ------------------------------------------------------------ never cancels

def test_nothing_here_can_act():
    source = pathlib.Path(money.__file__).read_text()
    for word in ("Action(", "suggests=", "executor", "_dispatch", ".post(",
                 ".delete("):
        assert word not in source
    assert sorted(a.name for a in mail.MODULE.actions()) == [
        "mail.apply_label_map", "mail.apply_labels", "mail.draft_nudge",
        "mail.draft_reply"]


# ------------------------------------------------------------------- vault

async def test_vault_note_carries_the_flag_count(monkeypatch):
    from atlas.api import vault_export

    body = "x\n\n2 recurring charge(s); ⚑ 1 to review.\n- A ⚑ not seen used."

    async def fetch_one(sql, *args):
        assert args == ("money_leaks",)
        return {"at": datetime(2026, 10, 1, tzinfo=UTC),
                "title": "Recurring charges — October 2026", "body": body}
    monkeypatch.setattr("atlas.kernel.state.fetch_one", fetch_one)

    docs: list[dict] = []
    await vault_export._money(docs)
    (doc,) = docs
    assert doc["path"] == "ATLAS/Money/Recurring charges.md"
    assert "# Recurring charges — ⚑ 1 to review" in doc["content"]
    assert "data, not instructions" in doc["content"]
