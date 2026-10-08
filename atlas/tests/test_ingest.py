"""New mail is READ, not counted.

ingest_message() existed and nothing called it, so every arriving email was
"1 unread" and read by nobody. This sweep is what makes "if I don't see it,
ATLAS does" true -- and it is scoped to mail he has NOT opened, per account.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from atlas.kernel.contracts import Severity
from atlas.modules.mail import ingest
from atlas.modules.mail.accounts import Account


def _msg(sender, subject="hi", body="text"):
    return {"id": "m1", "subject": subject, "sender": sender, "body": body,
            "at": datetime.now(UTC)}


@pytest.fixture
def one_account(monkeypatch):
    acct = Account(name="flycheap", kind="imap", email="miro@flycheap.live",
                   host="imap.zoho.com", password="x",
                   aliases=("miro@guarder.io",))
    monkeypatch.setattr(ingest, "all_accounts", lambda: [acct])

    async def quiet(*a, **k):
        return 1
    monkeypatch.setattr("atlas.kernel.memory.remember", quiet)
    return acct


async def test_a_client_email_becomes_a_signal_in_that_business_channel(
        one_account, monkeypatch):
    async def fresh(_a):
        return [_msg("a.customer@example.com", "Sensor offline at our clinic")]

    async def classify(s, f, b):
        return {"category": "client", "urgency": 2, "summary": "customer reports outage"}

    monkeypatch.setattr(ingest, "_new_imap", fresh)
    monkeypatch.setattr("atlas.modules.mail.classify", classify)
    signal, = await ingest.sweep()
    assert "Sensor offline" in signal.title
    assert signal.params["topic"] == "flycheap"      # the mailbox's channel
    assert signal.severity is Severity.NORMAL


async def test_urgent_reads_as_high(one_account, monkeypatch):
    """HIGH is the severity that can reach the phone. Urgency 3 earns it."""
    async def fresh(_a):
        return [_msg("ops@guarder.io", "PRODUCTION DOWN")]

    async def classify(s, f, b):
        return {"category": "client", "urgency": 3, "summary": "site down"}

    monkeypatch.setattr(ingest, "_new_imap", fresh)
    monkeypatch.setattr("atlas.modules.mail.classify", classify)
    signal, = await ingest.sweep()
    assert signal.severity is Severity.HIGH


async def test_noise_is_not_announced(one_account, monkeypatch):
    async def fresh(_a):
        return [_msg("newsletter@shop.example", "20% off this weekend")]

    async def classify(s, f, b):
        return {"category": "noise", "urgency": 0, "summary": "promo"}

    monkeypatch.setattr(ingest, "_new_imap", fresh)
    monkeypatch.setattr("atlas.modules.mail.classify", classify)
    assert await ingest.sweep() == []


async def test_a_watched_sender_is_announced_even_when_classified_fyi(
        one_account, monkeypatch):
    """Fiverr, Stripe, GitLab: their notifications ARE the signal, whatever a
    small local model thinks of the wording. This is the Fiverr path now
    that the site itself is behind a bot wall."""
    async def fresh(_a):
        return [_msg("noreply@fiverr.com", "You have a new order")]

    async def classify(s, f, b):
        return {"category": "fyi", "urgency": 1, "summary": "order"}

    monkeypatch.setattr(ingest, "_new_imap", fresh)
    monkeypatch.setattr("atlas.modules.mail.classify", classify)
    signal, = await ingest.sweep()
    assert "new order" in signal.title


async def test_his_own_mail_is_never_announced(one_account, monkeypatch):
    """A message FROM him -- including from an alias -- is not news to him."""
    async def fresh(_a):
        return [_msg("miro@guarder.io", "re: invoice")]

    async def classify(s, f, b):
        raise AssertionError("must not even classify his own mail")

    monkeypatch.setattr(ingest, "_new_imap", fresh)
    monkeypatch.setattr("atlas.modules.mail.classify", classify)
    assert await ingest.sweep() == []


def test_the_mail_module_routes_by_mailbox():
    assert ingest._topic_for(Account("g", "imap", "miro@guarder.io")) == "guardian"
    assert ingest._topic_for(Account("f", "imap", "miro@flycheap.live")) == "flycheap"
    assert ingest._topic_for(Account("p", "google", "me@gmail.com")) == "mail"


def test_a_signal_may_name_its_own_channel():
    """The scheduler used the module name as the topic, so a Guardian
    customer's email from the mail module landed in the mail channel."""
    import inspect

    from atlas.kernel import scheduler
    assert '(signal.params or {}).get("topic") or name' in inspect.getsource(scheduler)


# ------------------------------------------------ the local model is down

def _msgs(*ids):
    """Newest first, as both fetchers return them."""
    return [{"id": i, "subject": f"Subject {i}", "sender": f"{i}@example.com",
             "body": "text", "at": datetime.now(UTC)} for i in ids]


async def test_unclassified_mail_is_one_alert_and_is_read_again_next_sweep(
        one_account, monkeypatch):
    fetched: list[list[str]] = []

    async def fresh(account):
        # The fetcher advances the watermark past everything it returns.
        batch = _msgs("m3", "m2", "m1")
        fetched.append([m["id"] for m in batch])
        ingest._seen[account.name] = "m3"
        return batch

    async def classify(s, f, b):
        return {"unclassified": "local model unavailable: m: unreachable"}

    monkeypatch.setattr(ingest, "_seen", {"flycheap": "m0"})
    monkeypatch.setattr(ingest, "_new_imap", fresh)
    monkeypatch.setattr("atlas.modules.mail.classify", classify)

    signal, = await ingest.sweep()
    assert signal.title == "miro@flycheap.live: 3 unclassified — local model down"
    assert "Subject m1" in signal.body and "Subject m3" in signal.body
    assert "local model unavailable" in signal.body
    assert "fyi" not in signal.body
    assert signal.severity is Severity.NORMAL
    assert signal.params["topic"] == "flycheap"
    # The read position is not moved past mail nobody read.
    assert ingest._seen["flycheap"] == "m0"


async def test_only_mail_older_than_the_oldest_unclassified_is_left_behind(
        one_account, monkeypatch):
    async def fresh(account):
        ingest._seen[account.name] = "m4"
        return _msgs("m4", "m3", "m2", "m1")

    async def classify(s, f, b):
        if s == "Subject m3":
            return {"unclassified": "local model reply was not JSON"}
        return {"category": "noise", "urgency": 0, "summary": ""}

    monkeypatch.setattr(ingest, "_seen", {})
    monkeypatch.setattr(ingest, "_new_imap", fresh)
    monkeypatch.setattr("atlas.modules.mail.classify", classify)

    signal, = await ingest.sweep()
    assert "1 unclassified" in signal.title
    # m3 is read again next sweep; m2 and m1, classified, are not.
    assert ingest._seen["flycheap"] == "m2"


async def test_a_classifier_that_raises_is_unclassified_not_fyi(
        one_account, monkeypatch):
    monkeypatch.setenv("SOME_API_TOKEN", "tok-0123456789abcdef")

    async def fresh(account):
        return _msgs("m1")

    async def classify(s, f, b):
        raise RuntimeError("upstream said tok-0123456789abcdef")

    monkeypatch.setattr(ingest, "_seen", {})
    monkeypatch.setattr(ingest, "_new_imap", fresh)
    monkeypatch.setattr("atlas.modules.mail.classify", classify)

    signal, = await ingest.sweep()
    assert "1 unclassified" in signal.title
    assert "tok-0123456789abcdef" not in signal.body
    assert "flycheap" not in ingest._seen       # nothing before it: re-read all
