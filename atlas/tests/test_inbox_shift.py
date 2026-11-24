"""The inbox shift (plan 2026-10-30, B2): reads example-owner over IMAP and
reports. It drafts nothing: `mail.draft_reply` writes through the clips
mailbox's Gmail token and Gmail thread ids, so an IMAP thread cannot be
drafted -- the run says so instead of reporting zero drafts as done."""
from __future__ import annotations

from datetime import UTC, datetime

from atlas.modules.mail import reply_today
from atlas.modules.mail import shift as inbox_shift
from atlas.modules.mail.accounts import Account

NOW = datetime(2026, 10, 30, 11, tzinfo=UTC)
MIROTECH = Account(name="mirotech", kind="imap", email="owner@example.com",
                   host="imap.gmail.com", password="test-only")


def _card(**kw) -> reply_today.ReplyToday:
    card = reply_today.ReplyToday(at=NOW)
    for k, v in kw.items():
        setattr(card, k, v)
    return card


def _patch(monkeypatch, card, commitments=2, accounts=(MIROTECH,)):
    calls: list = []

    async def read(accts=None, now=None):
        calls.append(("read", [a.email for a in accts]))
        return card

    async def commitments_since(days: int) -> int:
        return commitments

    async def execute(req):  # pragma: no cover - must never run
        calls.append(("execute", req.action))
        raise AssertionError("the inbox shift executes nothing")

    from atlas.kernel import executor
    monkeypatch.setattr(reply_today, "read", read)
    monkeypatch.setattr(inbox_shift, "_commitments", commitments_since)
    monkeypatch.setattr(inbox_shift, "all_accounts", lambda: list(accounts))
    monkeypatch.setattr(executor, "execute", execute)
    return calls


async def test_reads_mirotech_and_reports_without_drafting(monkeypatch):
    card = _card(looked=7, reply=[{"account": "owner@example.com",
                                   "sender": "Ana", "subject": "Quote?",
                                   "age_h": 3.0, "urgency": 2}])
    card.counts["can_wait"] = 2
    calls = _patch(monkeypatch, card)
    out = await inbox_shift.run(now=NOW)
    assert calls == [("read", ["owner@example.com"])]
    assert out["read"] == 7
    assert out["reply_today"] == 1
    assert out["drafted"] == 0
    assert "imap" in out["drafts_blocked"].lower()
    assert out["unread_unclassified"] == 0
    assert out["commitments"] == 2
    assert out["health"] == "ok"
    assert out["held"] is None
    assert out["at"] == NOW.isoformat()


async def test_unclassified_mail_is_counted_never_zeroed(monkeypatch):
    card = _card(looked=4)
    card.counts["not_read"] = 4
    _patch(monkeypatch, card)
    out = await inbox_shift.run(now=NOW)
    assert out["unread_unclassified"] == 4
    assert out["health"] == "degraded"


async def test_an_unreadable_mailbox_is_unknown_not_zero(monkeypatch):
    card = _card(unreadable=["owner@example.com: LoginError"])
    _patch(monkeypatch, card)
    out = await inbox_shift.run(now=NOW)
    assert out["health"] == "unknown"
    assert out["read"] is None
    assert out["unreadable"] == ["owner@example.com: LoginError"]


async def test_no_mailbox_configured_is_unknown(monkeypatch):
    card = _card(unreadable=["no mailboxes configured (MAIL_ACCOUNTS)"])
    _patch(monkeypatch, card, accounts=())
    out = await inbox_shift.run(now=NOW)
    assert out["health"] == "unknown"
    assert out["read"] is None


async def test_an_unreadable_commitment_count_is_none(monkeypatch):
    _patch(monkeypatch, _card(looked=1), commitments=None)
    out = await inbox_shift.run(now=NOW)
    assert out["commitments"] is None
