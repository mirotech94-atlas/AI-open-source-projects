"""`mail.draft_reply` for a mailbox read over IMAP (owner).

The draft is an IMAP APPEND to that account's Drafts folder with the \\Draft
flag. It is only ever a draft: no SMTP, no send. Rollback deletes that draft
by its Message-ID, in Drafts only. The app password is never logged, returned
or recorded.
"""
from __future__ import annotations

import email
import inspect
import logging
from typing import ClassVar

import pytest

from atlas.kernel.contracts import Tier
from atlas.modules import mail
from atlas.modules.mail import imap_draft, readback

PASSWORD = "app-pass-for-test"
ORIG_ID = "<orig-123@mail.example.com>"
HEADERS = (b"From: Ann Lee <ann@client.example>\r\n"
           b"Subject: Quote for the site\r\n"
           b"Message-ID: <orig-123@mail.example.com>\r\n"
           b"References: <older-1@mail.example.com>\r\n\r\n")
DRAFTS = '"[Gmail]/Drafts"'


class FakeIMAP:
    """Records every command; holds one INBOX message and a Drafts list."""
    instances: ClassVar[list] = []
    inbox_has = True
    headers = HEADERS

    def __init__(self, host, port=993, **kw):
        self.host, self.port = host, port
        self.log: list[tuple] = []
        self.selected = None
        self.drafts: list[bytes] = []
        FakeIMAP.instances.append(self)

    def login(self, user, password):
        self.log.append(("login", user, password))
        return "OK", [b"ok"]

    def select(self, mailbox, readonly=False):
        self.selected = mailbox
        self.log.append(("select", mailbox, readonly))
        return "OK", [b"1"]

    def uid(self, command, *args):
        self.log.append(("uid", command, *args))
        if command == "SEARCH":
            if self.selected == "INBOX":
                return "OK", [b"7" if FakeIMAP.inbox_has else b""]
            return "OK", [b"3" if _drafts() else b""]
        if command == "FETCH":
            if self.selected == "INBOX":
                return "OK", [(b"7 (BODY[...] {1}", FakeIMAP.headers), b")"]
            return "OK", [(b"3 (BODY[] {1}", _drafts()[-1]), b")"]
        if command == "STORE":
            return "OK", [b"ok"]
        raise AssertionError(command)

    def append(self, mailbox, flags, date_time, message):
        self.log.append(("append", mailbox, flags, message))
        _drafts().append(message)
        return "OK", [b"appended"]

    def expunge(self):
        self.log.append(("expunge", self.selected))
        return "OK", [b"3"]

    def logout(self):
        self.log.append(("logout",))
        return "BYE", [b"bye"]


_STORE: list[bytes] = []


def _drafts() -> list[bytes]:
    return _STORE


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    FakeIMAP.instances = []
    FakeIMAP.inbox_has = True
    FakeIMAP.headers = HEADERS
    _STORE.clear()
    monkeypatch.setattr(imap_draft.imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setenv("MAIL_ACCOUNTS", "exampleco")
    monkeypatch.setenv("MAIL_EXAMPLECO_KIND", "imap")
    monkeypatch.setenv("MAIL_EXAMPLECO_EMAIL", "owner@example.com")
    monkeypatch.setenv("MAIL_EXAMPLECO_HOST", "imap.gmail.com")
    monkeypatch.delenv("MAIL_EXAMPLECO_PASSWORD", raising=False)
    monkeypatch.setenv("GMAIL_APP_PASSWORD", PASSWORD)


TID = f"imap:exampleco:{ORIG_ID}"
BODY = "Hi Ann, thanks, the quote is attached.\nBest, the owner"


def test_thread_id_parsing():
    assert imap_draft.parse_thread_id(TID) == ("exampleco", ORIG_ID)
    assert imap_draft.parse_thread_id("18c0ffee") is None
    assert imap_draft.parse_thread_id("imap:exampleco:") is None


async def test_appends_a_draft_to_gmail_drafts_with_reply_headers():
    result = await mail.draft_reply(TID, BODY)
    assert result.ok, result.detail
    conn, = FakeIMAP.instances
    appends = [c for c in conn.log if c[0] == "append"]
    assert len(appends) == 1
    _, folder, flags, raw = appends[0]
    assert folder == DRAFTS
    assert "\\Draft" in flags
    msg = email.message_from_bytes(raw)
    assert msg["In-Reply-To"] == ORIG_ID
    assert msg["References"].split() == ["<older-1@mail.example.com>", ORIG_ID]
    assert msg["Subject"] == "Re: Quote for the site"
    assert "ann@client.example" in msg["To"]
    assert "owner@example.com" in msg["From"]
    assert msg["Message-ID"].startswith("<") and msg["Message-ID"] != ORIG_ID
    assert BODY in msg.get_payload(decode=True).decode().replace("\r\n", "\n")
    # INBOX is only ever opened read-only
    assert all(c[2] is True for c in conn.log
               if c[0] == "select" and c[1] == "INBOX")
    assert result.rollback_token == f"imap:exampleco:{msg['Message-ID']}"
    assert result.evidence["draft_id"] == result.rollback_token
    assert result.evidence["thread_id"] == TID


async def test_subject_is_not_doubled_re():
    FakeIMAP.headers = HEADERS.replace(b"Quote for", b"Re: Quote for")
    result = await mail.draft_reply(TID, BODY)
    assert result.ok
    assert email.message_from_bytes(_STORE[0])["Subject"] == "Re: Quote for the site"


async def test_original_not_found_appends_nothing():
    FakeIMAP.inbox_has = False
    result = await mail.draft_reply(TID, BODY)
    assert not result.ok
    assert not any(c[0] == "append" for c in FakeIMAP.instances[0].log)


@pytest.mark.parametrize("bad", ['imap:exampleco:<a"b@x>', "imap:exampleco:<a\r\nb@x>",
                                 "imap:exampleco:<a\\b@x>"])
async def test_injection_in_message_id_is_refused(bad):
    result = await mail.draft_reply(bad, BODY)
    assert not result.ok
    assert FakeIMAP.instances == []


async def test_unknown_or_non_imap_account_is_refused(monkeypatch):
    assert not (await mail.draft_reply(f"imap:nobody:{ORIG_ID}", BODY)).ok
    monkeypatch.setenv("MAIL_EXAMPLECO_KIND", "google")
    assert not (await mail.draft_reply(TID, BODY)).ok
    assert FakeIMAP.instances == []


async def test_no_password_anywhere_refuses(monkeypatch):
    monkeypatch.delenv("GMAIL_APP_PASSWORD")
    result = await mail.draft_reply(TID, BODY)
    assert not result.ok
    assert FakeIMAP.instances == []


async def test_own_password_beats_the_shared_one(monkeypatch):
    monkeypatch.setenv("MAIL_EXAMPLECO_PASSWORD", "own-pass")
    await mail.draft_reply(TID, BODY)
    assert ("login", "owner@example.com", "own-pass") in FakeIMAP.instances[0].log


async def test_the_password_is_used_but_never_leaks(caplog):
    caplog.set_level(logging.DEBUG)
    result = await mail.draft_reply(TID, BODY)
    assert ("login", "owner@example.com", PASSWORD) in FakeIMAP.instances[0].log
    assert PASSWORD not in repr(result) and PASSWORD not in caplog.text
    FakeIMAP.inbox_has = False
    failed = await mail.draft_reply(TID, BODY)
    assert PASSWORD not in repr(failed)


async def test_dry_run_touches_no_network():
    ok = await mail.draft_dry(TID, BODY)
    assert ok.ok and FakeIMAP.instances == []
    assert not (await mail.draft_dry(TID, "short")).ok
    assert not (await mail.draft_dry("imap:nobody:<x@y>", BODY)).ok
    assert FakeIMAP.instances == []


async def test_rollback_deletes_only_that_draft_in_drafts():
    result = await mail.draft_reply(TID, BODY)
    FakeIMAP.instances.clear()
    undone = await mail.delete_draft(result.rollback_token)
    assert undone.ok, undone.detail
    conn, = FakeIMAP.instances
    assert ("select", DRAFTS, False) in conn.log
    assert not any(c[0] == "select" and c[1] == "INBOX" for c in conn.log)
    new_id = result.rollback_token.split(":", 2)[2]
    search = next(c for c in conn.log if c[:2] == ("uid", "SEARCH"))
    assert f'"{new_id}"' in search
    assert any(c[:2] == ("uid", "STORE") and "\\Deleted" in " ".join(map(str, c))
               for c in conn.log)
    assert ("expunge", DRAFTS) in conn.log


async def test_rollback_with_a_bad_token_or_missing_draft_fails_closed():
    assert not (await mail.delete_draft("imap:exampleco:")).ok
    assert not (await mail.delete_draft('imap:exampleco:<a"b@x>')).ok
    assert FakeIMAP.instances == []
    undone = await mail.delete_draft("imap:exampleco:<gone@x>")   # Drafts empty
    assert not undone.ok
    assert not any(c[0] == "expunge" for c in FakeIMAP.instances[0].log)


async def test_gmail_api_path_is_unchanged_for_plain_ids(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("gmail api reached")
    monkeypatch.setattr(mail, "_access_token", boom)
    with pytest.raises(RuntimeError, match="gmail api reached"):
        await mail.draft_reply("18c0ffee", BODY)
    assert FakeIMAP.instances == []


async def test_nudge_refuses_an_imap_thread():
    result = await mail.draft_nudge(TID)
    assert not result.ok and FakeIMAP.instances == []


def test_the_action_keeps_its_tier_dry_run_and_rollback():
    action = next(a for a in mail.MailModule().actions()
                  if a.name == "mail.draft_reply")
    assert action.tier is Tier.AUTO          # unchanged by this work
    assert action.dry_run is mail.draft_dry and action.rollback is mail.delete_draft


def test_the_module_cannot_send():
    source = inspect.getsource(imap_draft)
    for forbidden in ("smtplib", "sendmail", "send_message", "SMTP"):
        assert forbidden not in source


async def test_readback_reads_the_imap_draft_by_message_id(monkeypatch):
    result = await mail.draft_reply(TID, BODY)
    params = {"thread_id": TID, "body": BODY}
    monkeypatch.delenv("GOOGLE_REFRESH_TOKEN", raising=False)
    got = await readback.verify_draft_reply(params, result)
    assert got.state.name == "VERIFIED", got
    other = await readback.verify_draft_reply({**params, "body": "different text!!"},
                                              result)
    assert other.state.name == "CONTRADICTED"
    _STORE.clear()
    gone = await readback.verify_draft_reply(params, result)
    assert gone.state.name == "CONTRADICTED"
