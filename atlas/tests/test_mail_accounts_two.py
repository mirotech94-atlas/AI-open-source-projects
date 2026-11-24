"""The inbox reads owner@example.com only, over IMAP with an app password
(plan 2026-10-30, B1; Miro: no clips account, no OAuth token needed)."""
from __future__ import annotations

from atlas.modules.mail import accounts


def _env(monkeypatch, **values: str) -> None:
    for key in ("GOOGLE_REFRESH_TOKEN", "GOOGLE_ACCOUNT_EMAIL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_one_imap_account_for_mirotech(monkeypatch):
    _env(monkeypatch, MAIL_ACCOUNTS="mirotech", MAIL_MIROTECH_KIND="imap",
         MAIL_MIROTECH_EMAIL="owner@example.com",
         MAIL_MIROTECH_HOST="imap.gmail.com", MAIL_MIROTECH_PASSWORD="test-only")
    found = accounts.all_accounts()
    assert [a.name for a in found] == ["mirotech"]
    acct = found[0]
    assert acct.kind == "imap" and acct.port == 993
    assert acct.configured and acct.missing == ""
    assert acct.is_me("Miro <owner@example.com>")
    assert not acct.is_me("dropclips@example.com")


def test_a_missing_app_password_is_named_not_guessed(monkeypatch):
    _env(monkeypatch, MAIL_ACCOUNTS="mirotech", MAIL_MIROTECH_KIND="imap",
         MAIL_MIROTECH_EMAIL="owner@example.com",
         MAIL_MIROTECH_HOST="imap.gmail.com")
    monkeypatch.delenv("MAIL_MIROTECH_PASSWORD", raising=False)
    acct = accounts.all_accounts()[0]
    assert not acct.configured
    assert acct.missing == "MAIL_MIROTECH_PASSWORD"


def test_the_legacy_clips_account_is_not_read_once_accounts_are_named(
        monkeypatch):
    _env(monkeypatch, MAIL_ACCOUNTS="mirotech", MAIL_MIROTECH_KIND="imap",
         MAIL_MIROTECH_EMAIL="owner@example.com",
         MAIL_MIROTECH_HOST="imap.gmail.com", MAIL_MIROTECH_PASSWORD="x",
         GOOGLE_REFRESH_TOKEN="legacy", GOOGLE_ACCOUNT_EMAIL="clips@example.com")
    assert [a.email for a in accounts.all_accounts()] == ["owner@example.com"]
