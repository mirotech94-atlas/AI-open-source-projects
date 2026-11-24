"""The unreplied check owns its time budget and says why it gave up."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from atlas.kernel.contracts import Health
from atlas.modules import mail
from atlas.modules.mail.accounts import Account


@pytest.fixture(autouse=True)
def _google_env(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "x")


def _account() -> Account:
    return Account(name="mirotech", kind="google", email="m@example.com",
                   refresh_token="r")


async def test_a_slow_mailbox_is_unknown_and_names_the_deadline(monkeypatch):
    monkeypatch.setattr(mail, "UNREPLIED_DEADLINE_S", 0.05)

    async def slow(path, account=None, **params):
        await asyncio.sleep(1)

    monkeypatch.setattr(mail, "_api", slow)
    obs = await mail.check_unreplied_for(_account())
    assert obs.health is Health.UNKNOWN and obs.value is None
    assert "did not answer within" in obs.detail


async def test_a_mailbox_that_answers_reads_a_real_number(monkeypatch):
    seen: list[dict] = []

    async def fake(path, account=None, **params):
        seen.append({"path": path, **params})
        if path == "/threads":
            return {"threads": [{"id": f"t{i}"} for i in range(25)]}
        return {"messages": [{"internalDate": "1000",
                              "payload": {"headers": [
                                  {"name": "From", "value": "Buyer <b@x.org>"},
                                  {"name": "Subject", "value": "hello"}]}}]}

    monkeypatch.setattr(mail, "_api", fake)
    obs = await mail.check_unreplied_for(_account())
    assert obs.health is Health.DEGRADED and obs.value == 25.0
    listing = seen[0]
    assert listing["maxResults"] == 25 and "in:inbox" in listing["q"]
    thread_calls = [c for c in seen if c["path"].startswith("/threads/")]
    assert thread_calls and all(c["fields"] and c["metadataHeaders"]
                                for c in thread_calls)


def test_the_check_budget_sits_above_the_body_deadline(monkeypatch):
    assert mail.UNREPLIED_TIMEOUT > timedelta(seconds=mail.UNREPLIED_DEADLINE_S)
    monkeypatch.setenv("MAIL_ACCOUNTS", "mirotech")
    monkeypatch.setenv("MAIL_MIROTECH_EMAIL", "m@example.com")
    unreplied = [c for c in mail.MODULE.checks() if c.name.endswith("_unreplied")]
    assert unreplied
    assert all(c.timeout == mail.UNREPLIED_TIMEOUT for c in unreplied)
