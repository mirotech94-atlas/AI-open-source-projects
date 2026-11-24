"""The inbox reads owner@example.com only, over IMAP with an app password
(plan 2026-10-30, B1; the owner: no clips account, no OAuth token needed)."""
from __future__ import annotations

from atlas.modules.mail import accounts


def _env(monkeypatch, **values: str) -> None:
    for key in ("GOOGLE_REFRESH_TOKEN", "GOOGLE_ACCOUNT_EMAIL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_one_imap_account_for_ownertech(monkeypatch):
    _env(monkeypatch, MAIL_ACCOUNTS="exampleco", MAIL_EXAMPLECO_KIND="imap",
         MAIL_EXAMPLECO_EMAIL="owner@example.com",
         MAIL_EXAMPLECO_HOST="imap.gmail.com", MAIL_EXAMPLECO_PASSWORD="test-only")
    found = accounts.all_accounts()
    assert [a.name for a in found] == ["exampleco"]
    acct = found[0]
    assert acct.kind == "imap" and acct.port == 993
    assert acct.configured and acct.missing == ""
    assert acct.is_me("the owner <owner@example.com>")
    assert not acct.is_me("contact14@example.com")


def test_a_missing_app_password_is_named_not_guessed(monkeypatch):
    _env(monkeypatch, MAIL_ACCOUNTS="exampleco", MAIL_EXAMPLECO_KIND="imap",
         MAIL_EXAMPLECO_EMAIL="owner@example.com",
         MAIL_EXAMPLECO_HOST="imap.gmail.com")
    monkeypatch.delenv("MAIL_EXAMPLECO_PASSWORD", raising=False)
    acct = accounts.all_accounts()[0]
    assert not acct.configured
    assert acct.missing == "MAIL_EXAMPLECO_PASSWORD"


def test_the_legacy_clips_account_is_not_read_once_accounts_are_named(
        monkeypatch):
    _env(monkeypatch, MAIL_ACCOUNTS="exampleco", MAIL_EXAMPLECO_KIND="imap",
         MAIL_EXAMPLECO_EMAIL="owner@example.com",
         MAIL_EXAMPLECO_HOST="imap.gmail.com", MAIL_EXAMPLECO_PASSWORD="x",
         GOOGLE_REFRESH_TOKEN="legacy", GOOGLE_ACCOUNT_EMAIL="clips@example.com")
    assert [a.email for a in accounts.all_accounts()] == ["owner@example.com"]
