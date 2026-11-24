"""The "Reply today" card (`atlas/modules/mail/reply_today.py`).

What matters: machine mail is counted by its headers and never listed; a
person's mail is read by the LOCAL model, wrapped as untrusted; mail the model
did not read is its own count, never zero; an unreadable mailbox is named;
and the card is a Signal with no action.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import occurrence
from atlas.kernel.llm import Completion, LocalModelUnavailable, Need
from atlas.modules import mail
from atlas.modules.mail import reply_today
from atlas.modules.mail.accounts import Account

NOW = datetime(2026, 9, 28, 9, 0, tzinfo=UTC)

ME = Account(name="owner", kind="google", email="me@example.com",
             refresh_token="t")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    mail._CLASSIFY_CACHE.clear()
    reply_today._last.clear()
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    yield
    mail._CLASSIFY_CACHE.clear()


def _item(i: str, sender: str, subject: str, hours: float, **kw) -> dict:
    return {"id": i, "sender": sender, "subject": subject,
            "at": NOW - timedelta(hours=hours), "snippet": kw.pop("snippet", ""),
            "list_unsubscribe": kw.pop("unsub", False), "bulk": kw.pop("bulk", False)}


def _mailbox(monkeypatch, items):
    async def fake_items(account):
        return items
    monkeypatch.setattr(reply_today, "_items", fake_items)


def _model(monkeypatch, answers: dict[str, str], seen: list | None = None):
    """A stand-in for the local model keyed on a subject in the prompt."""
    async def think(need, system, messages, **kw):
        if seen is not None:
            seen.append((need, messages[0].content))
        text = messages[0].content
        for subject, answer in answers.items():
            if subject in text:
                return Completion(answer, "ollama", "local")
        return Completion('{"category": "fyi", "urgency": 0}', "ollama", "local")
    monkeypatch.setattr(mail, "think", think)


# ----------------------------------------------------------- header sorting

@pytest.mark.parametrize(("sender", "subject", "kw", "want"), [
    ("Spotify <contact65@example.com>", "Your receipt from Spotify", {}, "receipts"),
    ("Shop <contact66@example.com>", "Your order has shipped", {"list_unsubscribe": True},
     "receipts"),
    ("Substack <contact23@example.com>", "This week in AI", {"list_unsubscribe": True},
     "newsletters"),
    ("Morning Brew <contact67@example.com>", "Today", {"bulk": True}, "newsletters"),
    ("GitLab <contact68@example.com>", "Pipeline failed", {}, "notifications"),
    ("Anna <anna@client.com>", "Invoice for September", {}, None),
])
def test_machine_mail_is_sorted_by_headers(sender, subject, kw, want):
    assert reply_today.machine_kind(sender, subject, **kw) == want


@pytest.mark.parametrize(("verdict", "age", "want"), [
    ({"category": "needs_reply", "urgency": 3}, 1, "reply"),
    ({"category": "needs_reply", "urgency": 1}, 50, "reply"),
    ({"category": "needs_reply", "urgency": 1}, 5, "can_wait"),
    ({"category": "fyi", "urgency": 0}, 5, "no_reply"),
    ({"unclassified": "local model down"}, 5, "not_read"),
    ({}, 5, "not_read"),
])
def test_person_mail_sorting(verdict, age, want):
    assert reply_today.sort_person(verdict, age) == want


# ------------------------------------------------------------------- read()

async def test_card_lists_only_person_mail_that_needs_a_reply(monkeypatch):
    _mailbox(monkeypatch, [
        _item("1", "Anna <anna@client.com>", "Can you confirm Friday?", 3),
        _item("2", "Bob <bob@x.com>", "Lunch sometime", 2),
        _item("3", "Apple <contact69@example.com>", "Your receipt from Apple", 1),
        _item("4", "News <news@site.com>", "The weekly", 1, unsub=True),
        _item("5", "GitLab <contact68@example.com>", "Pipeline #9 failed", 1),
        _item("6", "Me <me@example.com>", "Re: Can you confirm Friday?", 0),
    ])
    seen: list = []
    _model(monkeypatch, {
        "Can you confirm Friday?": '{"category": "needs_reply", "urgency": 2}',
        "Lunch sometime": '{"category": "needs_reply", "urgency": 1}',
    }, seen)

    card = await reply_today.read([ME], now=NOW)

    assert [r["subject"] for r in card.reply] == ["Can you confirm Friday?"]
    assert card.counts["receipts"] == 1
    assert card.counts["newsletters"] == 1
    assert card.counts["notifications"] == 1
    assert card.counts["can_wait"] == 1
    # Only the two people's mail reached a model, and only the local one,
    # each wrapped as untrusted data.
    assert len(seen) == 2
    assert all(need is Need.PRIVATE for need, _ in seen)
    assert all("<untrusted_data" in text for _, text in seen)
    head, body = reply_today.render(card)
    assert head.startswith("1 email(s) need your reply today (read 09:00 UTC)")
    assert "Your receipt from Apple" not in body
    assert "Pipeline #9 failed" not in body
    assert "1 receipt(s), 1 newsletter(s), 1 notification(s)" in body


async def test_mail_the_model_did_not_read_is_counted_never_zero(monkeypatch):
    _mailbox(monkeypatch, [_item("1", "Anna <anna@client.com>", "Are you in?", 3)])

    async def down(*a, **k):
        raise LocalModelUnavailable("no local model configured")
    monkeypatch.setattr(mail, "think", down)

    card = await reply_today.read([ME], now=NOW)

    assert card.reply == []
    assert card.counts["not_read"] == 1
    _head, body = reply_today.render(card)
    assert "1 not read by the local model" in body
    assert "not known" in body


async def test_an_unreadable_mailbox_is_named_not_zero(monkeypatch):
    async def broken(account):
        raise RuntimeError("IMAP login failed")
    monkeypatch.setattr(reply_today, "_items", broken)

    card = await reply_today.read([ME], now=NOW)

    head, body = reply_today.render(card)
    assert "not known" in head
    assert "0 email" not in head
    assert "me@example.com: IMAP login failed" in body


async def test_an_unconfigured_mailbox_is_named(monkeypatch):
    bare = Account(name="flycheap", kind="imap", email="owner@flycheap.example")
    card = await reply_today.read([bare], now=NOW)
    assert any("MAIL_FLYCHEAP_HOST" in u for u in card.unreadable)


async def test_private_routing_holds_with_the_local_model_down(monkeypatch,
                                                                local_model_down):
    """The real router: a person's email is never sent to a cloud model."""
    _mailbox(monkeypatch, [_item("1", "Anna <anna@client.com>", "Are you in?", 3)])
    card = await reply_today.read([ME], now=NOW)
    assert card.counts["not_read"] == 1
    assert local_model_down == []


# ------------------------------------------------------------ daily signal

async def test_daily_signal_is_one_card_with_no_action(monkeypatch):
    monkeypatch.setenv("BRIEF_HOUR", "0")
    claimed: list = []

    async def claim(job, window):
        claimed.append((job, window))
        return len(claimed) == 1

    async def noop(job, window):
        return None
    monkeypatch.setattr(occurrence, "claim", claim)
    monkeypatch.setattr(occurrence, "done", noop)
    _mailbox(monkeypatch, [])

    assert await reply_today.maybe_start(now=NOW) is True
    await reply_today._task                 # read in the background
    assert await reply_today.maybe_start(now=NOW) is False

    (card,) = reply_today.take_signals()
    assert reply_today.take_signals() == []
    assert card.suggests is None and card.params == {}
    assert card.title == reply_today.title_for(NOW)
    assert claimed[0][0] == "mail.reply_today"
    assert "reply today:" in reply_today.brief_line(now=NOW)


async def test_a_failed_read_releases_the_day(monkeypatch):
    monkeypatch.setenv("BRIEF_HOUR", "0")
    released: list = []

    async def claim(job, window):
        return True

    async def release(job, window):
        released.append(window)
    monkeypatch.setattr(occurrence, "claim", claim)
    monkeypatch.setattr(occurrence, "release", release)

    async def boom(**kw):
        raise RuntimeError("database gone")
    monkeypatch.setattr(reply_today, "read", boom)

    assert await reply_today.maybe_start(now=NOW)
    await reply_today._task
    assert released and reply_today.take_signals() == []


async def test_daily_signal_waits_for_the_brief_hour(monkeypatch):
    monkeypatch.setenv("BRIEF_HOUR", "23")

    async def claim(job, window):
        raise AssertionError("must not claim before the brief hour")
    monkeypatch.setattr(occurrence, "claim", claim)
    early = datetime(2026, 9, 28, 0, 30).astimezone()
    assert await reply_today.maybe_start(now=early) is False


async def test_one_email_gets_the_inbox_s_own_reading(monkeypatch):
    """Keyed like inbox(): a Google thread the inbox already classified is
    not sent to the model again, and gets the same answer."""
    _mailbox(monkeypatch, [_item("t1", "Anna <anna@client.com>", "Are you in?", 3)])
    mail._CLASSIFY_CACHE["t1"] = ({"category": "needs_reply", "urgency": 3}, NOW)

    async def think(*a, **k):
        raise AssertionError("already read by the inbox")
    monkeypatch.setattr(mail, "think", think)

    card = await reply_today.read([ME], now=NOW)
    assert [r["subject"] for r in card.reply] == ["Are you in?"]


async def test_brief_line_says_nothing_before_a_card_was_read():
    assert reply_today.brief_line(now=NOW) == ""
