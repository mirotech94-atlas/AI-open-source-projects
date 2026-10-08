"""agency's Task 3 surface: mockups, outreach guards, build and deploy
(AGENCY.md §2.4-2.6, §3).

Nothing here touches a socket or a real LLM: SMTP, GetLayers and `think`
are all monkeypatched, and Postgres is either `memory_backend` or a direct
monkeypatch on `_store`, the same style `tests/test_agency.py` uses.
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from typing import ClassVar

import httpx
import pytest

from atlas.kernel import review
from atlas.kernel.contracts import Health, Severity, Tier
from atlas.kernel.llm import Completion
from atlas.kernel.permissions import classify
from atlas.modules import agency
from atlas.modules.agency import _store, build, deploy, mockup, outreach
from atlas.modules.agency.audit import Audit, Finding


@pytest.fixture(autouse=True)
def _verified(monkeypatch):
    """Every lead here is a verified NYC business unless a test says not:
    the verification gate has its own tests (tests/test_agency_verify.py)."""
    from atlas.modules.agency import verify

    async def ok(_lead_id):
        return None
    monkeypatch.setattr(verify, "contact_refusal", ok)


def _async_returns(value):
    async def fake(*args, **kwargs):
        return value
    return fake


@pytest.fixture(autouse=True)
def _nothing_built_or_found(monkeypatch):
    """No live site and no published email unless a test says so."""
    from atlas.modules.agency import contacts
    monkeypatch.setattr(_store, "lead_links", _async_returns(
        {"domain": None, "slug": None, "deployed": False, "webild_url": None,
         "mockup": None}))
    monkeypatch.setattr(contacts, "published_email", _async_returns(None))


REAL_AUTO_HOLD = outreach.auto_hold


@pytest.fixture(autouse=True)
def _no_hold(monkeypatch):
    """The guard tests below test the guards. The holds of `auto_hold`
    (the unattended send's own gates) have their own section, which puts
    the real one back."""
    monkeypatch.setattr(outreach, "auto_hold", _async_returns(None))


def _draft(**overrides):
    row = {
        "id": 1,
        "lead_id": 1,
        "kind": "email",
        "to_address": "owner@example.com",
        "subject": "About your website",
        "body": ("Hi, your site could use some work.\n\n123 Main St, Springfield\n\n"
                "Reply STOP to unsubscribe.\n\n— sent by Miro's assistant"),
        "status": "draft",
        "followup_of": None,
    }
    row.update(overrides)
    return row


def _env_ok(monkeypatch):
    monkeypatch.setenv("AGENCY_SEND_DOMAIN", "sendfrom.example")
    monkeypatch.setenv("AGENCY_SEND_FROM", "outreach@sendfrom.example")
    monkeypatch.setenv("AGENCY_POSTAL_ADDRESS", "123 Main St, Springfield")


def _no_lead_features(monkeypatch, lead_id=1):
    """Guard-passing tests now trigger one extra `_store.get_lead` lookup
    (for the proposal's `lead_features` evidence) once every guard has
    passed; this is the default stand-in for it."""
    monkeypatch.setattr(_store, "get_lead", _async_returns({"id": lead_id, "features": {}}))


# ------------------------------------------------------------- can_spam_ok

def test_can_spam_ok_is_empty_when_everything_is_present():
    body = "Hi.\n\n123 Main St, Springfield\n\nReply STOP to unsubscribe."
    assert outreach.can_spam_ok(body, "123 Main St, Springfield") == []


def test_can_spam_ok_flags_a_missing_postal_address():
    missing = outreach.can_spam_ok("reply STOP to opt out, no address though",
                                   "123 Main St, Springfield")
    assert any("postal" in m for m in missing)


def test_can_spam_ok_flags_an_unset_postal_env_distinctly():
    missing = outreach.can_spam_ok("reply STOP to unsubscribe", "")
    assert any("AGENCY_POSTAL_ADDRESS" in m for m in missing)


def test_can_spam_ok_flags_a_missing_optout_line():
    missing = outreach.can_spam_ok("123 Main St, Springfield, nothing else",
                                   "123 Main St, Springfield")
    assert any("opt-out" in m for m in missing)


# -------------------------------------------------------------- sends_today

async def test_sends_today_uses_explicit_local_midnight_bounds_never_a_date_cast(
    memory_backend,
):
    memory_backend.responses["FROM agency_outreach"] = {"n": 3}
    n = await outreach.sends_today()
    assert n == 3
    sql, args = memory_backend.sql_log[-1]
    assert "sent_at >= $1" in sql
    assert "sent_at < $2" in sql
    assert "::date" not in sql
    assert len(args) == 2
    start, end = args
    assert end - start == timedelta(days=1)


def test_daily_cap_defaults_to_10(monkeypatch):
    monkeypatch.delenv("AGENCY_DAILY_CAP", raising=False)
    assert outreach.daily_cap() == 10


def test_daily_cap_reads_the_env_var(monkeypatch):
    monkeypatch.setenv("AGENCY_DAILY_CAP", "3")
    assert outreach.daily_cap() == 3


async def test_has_sent_followup_excludes_drafts_and_the_row_itself(memory_backend):
    memory_backend.responses["FROM agency_outreach"] = None
    result = await _store.has_sent_followup(5, 2)
    assert result is False
    sql, args = memory_backend.sql_log[-1]
    assert "followup_of IS NOT NULL" in sql
    assert "status <> 'draft'" in sql
    assert "id <> $2" in sql
    assert args == (5, 2)


# --------------------------------------------------------- send_dry guards

async def test_send_dry_refuses_a_suppressed_address(monkeypatch):
    monkeypatch.setattr(_store, "get_outreach", _async_returns(_draft()))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(True))
    _env_ok(monkeypatch)
    result = await outreach.send_dry(1)
    assert not result.ok
    assert "suppression" in result.detail


async def test_send_dry_refuses_at_the_daily_cap(monkeypatch):
    monkeypatch.setattr(_store, "get_outreach", _async_returns(_draft()))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
    monkeypatch.setattr(_store, "count_sent_between", _async_returns(10))
    monkeypatch.setenv("AGENCY_DAILY_CAP", "10")
    _env_ok(monkeypatch)
    result = await outreach.send_dry(1)
    assert not result.ok
    assert "cap" in result.detail


async def test_send_dry_refuses_18dn_com_as_the_sending_domain(monkeypatch):
    monkeypatch.setattr(_store, "get_outreach", _async_returns(_draft()))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
    monkeypatch.setattr(_store, "count_sent_between", _async_returns(0))
    monkeypatch.setenv("AGENCY_SEND_DOMAIN", "example.com")
    monkeypatch.setenv("AGENCY_POSTAL_ADDRESS", "123 Main St, Springfield")
    result = await outreach.send_dry(1)
    assert not result.ok
    assert "example.com" in result.detail


async def test_send_dry_refuses_a_subdomain_of_18dn_com(monkeypatch):
    monkeypatch.setattr(_store, "get_outreach", _async_returns(_draft()))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
    monkeypatch.setattr(_store, "count_sent_between", _async_returns(0))
    monkeypatch.setenv("AGENCY_SEND_DOMAIN", "mail.example.com")
    monkeypatch.setenv("AGENCY_POSTAL_ADDRESS", "123 Main St, Springfield")
    result = await outreach.send_dry(1)
    assert not result.ok
    assert "example.com" in result.detail


async def test_send_dry_refuses_a_from_address_on_a_different_domain(monkeypatch):
    """Guard 4 (the sending domain isn't example.com) is pointless if the
    message actually goes out From an address on some OTHER domain
    entirely -- this binds the two together."""
    monkeypatch.setattr(_store, "get_outreach", _async_returns(_draft()))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
    monkeypatch.setattr(_store, "count_sent_between", _async_returns(0))
    monkeypatch.setenv("AGENCY_SEND_DOMAIN", "sendfrom.example")
    monkeypatch.setenv("AGENCY_SEND_FROM", "someone@totally-different.example")
    monkeypatch.setenv("AGENCY_POSTAL_ADDRESS", "123 Main St, Springfield")
    result = await outreach.send_dry(1)
    assert not result.ok
    assert "totally-different.example" in result.detail
    assert "sendfrom.example" in result.detail


async def test_send_dry_with_no_from_address_uses_mapfronts_and_checks_it(monkeypatch):
    """No AGENCY_SEND_FROM: Mapfront's own miro@mapfront.io, still held to
    AGENCY_SEND_DOMAIN -- a different sending domain is refused."""
    monkeypatch.setattr(_store, "get_outreach", _async_returns(_draft()))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
    monkeypatch.setattr(_store, "count_sent_between", _async_returns(0))
    monkeypatch.setenv("AGENCY_SEND_DOMAIN", "sendfrom.example")
    monkeypatch.delenv("AGENCY_SEND_FROM", raising=False)
    monkeypatch.delenv("AGENCY_SMTP_USER", raising=False)
    result = await outreach.send_dry(1)
    assert not result.ok
    assert "miro@mapfront.io" in result.detail and "AGENCY_SEND_DOMAIN" in result.detail


async def test_send_dry_falls_back_to_agency_smtp_user_as_the_from_address(monkeypatch):
    monkeypatch.setattr(_store, "get_outreach", _async_returns(_draft()))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
    monkeypatch.setattr(_store, "count_sent_between", _async_returns(0))
    monkeypatch.setenv("AGENCY_SEND_DOMAIN", "sendfrom.example")
    monkeypatch.delenv("AGENCY_SEND_FROM", raising=False)
    monkeypatch.setenv("AGENCY_SMTP_USER", "relay@sendfrom.example")
    monkeypatch.setenv("AGENCY_POSTAL_ADDRESS", "123 Main St, Springfield")
    _no_lead_features(monkeypatch)
    result = await outreach.send_dry(1)
    assert result.ok


async def test_send_dry_refuses_a_draft_missing_the_postal_address(monkeypatch):
    row = _draft(body="Hi there. Reply STOP to unsubscribe.")
    monkeypatch.setattr(_store, "get_outreach", _async_returns(row))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
    monkeypatch.setattr(_store, "count_sent_between", _async_returns(0))
    _env_ok(monkeypatch)
    result = await outreach.send_dry(1)
    assert not result.ok
    assert "postal" in result.detail


async def test_send_dry_refuses_a_draft_missing_the_optout_line(monkeypatch):
    row = _draft(body="Hi there. 123 Main St, Springfield. That's all.")
    monkeypatch.setattr(_store, "get_outreach", _async_returns(row))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
    monkeypatch.setattr(_store, "count_sent_between", _async_returns(0))
    _env_ok(monkeypatch)
    result = await outreach.send_dry(1)
    assert not result.ok
    assert "opt-out" in result.detail


async def test_send_dry_refuses_a_second_follow_up(monkeypatch):
    """Enforced per LEAD, not per chain: this outreach is a follow-up, and
    the lead already has some OTHER already-sent follow-up (which may or
    may not be this row's own parent -- `has_sent_followup` doesn't care)."""
    row = _draft(id=2, followup_of=1)
    monkeypatch.setattr(_store, "get_outreach", _async_returns(row))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
    monkeypatch.setattr(_store, "count_sent_between", _async_returns(0))
    monkeypatch.setattr(_store, "has_sent_followup", _async_returns(True))
    _env_ok(monkeypatch)
    result = await outreach.send_dry(2)
    assert not result.ok
    assert "follow-up" in result.detail


async def test_send_dry_allows_the_first_follow_up(monkeypatch):
    row = _draft(id=2, followup_of=1)
    monkeypatch.setattr(_store, "get_outreach", _async_returns(row))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
    monkeypatch.setattr(_store, "count_sent_between", _async_returns(0))
    monkeypatch.setattr(_store, "has_sent_followup", _async_returns(False))
    _no_lead_features(monkeypatch)
    _env_ok(monkeypatch)
    result = await outreach.send_dry(2)
    assert result.ok


async def test_send_dry_succeeds_when_every_guard_passes(monkeypatch):
    monkeypatch.setattr(_store, "get_outreach", _async_returns(_draft()))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
    monkeypatch.setattr(_store, "count_sent_between", _async_returns(0))
    _no_lead_features(monkeypatch)
    _env_ok(monkeypatch)
    result = await outreach.send_dry(1)
    assert result.ok


async def test_send_dry_evidence_carries_the_leads_own_stored_features(monkeypatch):
    monkeypatch.setattr(_store, "get_outreach", _async_returns(_draft()))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
    monkeypatch.setattr(_store, "count_sent_between", _async_returns(0))
    monkeypatch.setattr(_store, "get_lead",
                        _async_returns({"id": 1, "features": {"audit_severity": 7.0}}))
    _env_ok(monkeypatch)
    result = await outreach.send_dry(1)
    assert result.ok
    assert result.evidence["lead_features"] == {"audit_severity": 7.0}


async def test_send_dry_refuses_an_already_sent_outreach(monkeypatch):
    monkeypatch.setattr(_store, "get_outreach", _async_returns(_draft(status="sent")))
    result = await outreach.send_dry(1)
    assert not result.ok
    assert "draft" in result.detail


async def test_send_dry_refuses_a_missing_outreach(monkeypatch):
    monkeypatch.setattr(_store, "get_outreach", _async_returns(None))
    result = await outreach.send_dry(999)
    assert not result.ok


# ------------------------------------------------------------ send_outreach

async def test_send_outreach_sends_and_marks_the_row_sent(monkeypatch):
    monkeypatch.setattr(_store, "get_outreach", _async_returns(_draft()))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
    monkeypatch.setattr(_store, "count_sent_between", _async_returns(0))
    _no_lead_features(monkeypatch)
    _env_ok(monkeypatch)

    sent = {}

    async def fake_smtp_send(from_address, to_address, subject, body, **_kw):
        sent["from"] = from_address
        sent["to"] = to_address
        sent["subject"] = subject
        return True, None
    monkeypatch.setattr(outreach, "_smtp_send", fake_smtp_send)

    marked = []

    async def fake_mark_sent(outreach_id, message_id=None):
        marked.append(outreach_id)
    monkeypatch.setattr(_store, "mark_outreach_sent", fake_mark_sent)

    result = await outreach.send_outreach(1)
    assert result.ok
    assert sent["from"] == "outreach@sendfrom.example"
    assert sent["to"] == "owner@example.com"
    assert marked == [1]


async def test_send_outreach_evidence_carries_the_leads_own_stored_features(monkeypatch):
    monkeypatch.setattr(_store, "get_outreach", _async_returns(_draft()))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
    monkeypatch.setattr(_store, "count_sent_between", _async_returns(0))
    monkeypatch.setattr(_store, "get_lead",
                        _async_returns({"id": 1, "features": {"audit_severity": 9.0}}))
    monkeypatch.setattr(_store, "mark_outreach_sent", _async_returns(None))
    _env_ok(monkeypatch)

    async def fake_smtp_send(from_address, to_address, subject, body, **_kw):
        return True, None
    monkeypatch.setattr(outreach, "_smtp_send", fake_smtp_send)

    result = await outreach.send_outreach(1)
    assert result.ok
    assert result.evidence["lead_features"] == {"audit_severity": 9.0}


async def test_send_outreach_reports_the_smtp_failure_and_does_not_mark_sent(monkeypatch):
    monkeypatch.setattr(_store, "get_outreach", _async_returns(_draft()))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
    monkeypatch.setattr(_store, "count_sent_between", _async_returns(0))
    _env_ok(monkeypatch)

    async def fail_smtp(from_address, to_address, subject, body, **_kw):
        return False, "connection refused"
    monkeypatch.setattr(outreach, "_smtp_send", fail_smtp)

    async def fail_if_called(outreach_id):
        raise AssertionError("must not mark sent on a failed send")
    monkeypatch.setattr(_store, "mark_outreach_sent", fail_if_called)

    result = await outreach.send_outreach(1)
    assert not result.ok
    assert "connection refused" in result.detail


async def test_send_outreach_refuses_a_non_email_kind(monkeypatch):
    monkeypatch.setattr(_store, "get_outreach",
                        _async_returns(_draft(kind="call_script")))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
    monkeypatch.setattr(_store, "count_sent_between", _async_returns(0))
    _env_ok(monkeypatch)

    async def fail_if_called(*a, **kw):
        raise AssertionError("must not attempt to send a non-email kind")
    monkeypatch.setattr(outreach, "_smtp_send", fail_if_called)

    result = await outreach.send_outreach(1)
    assert not result.ok
    assert "email" in result.detail


async def test_send_outreach_refuses_a_body_missing_the_trailer(monkeypatch):
    monkeypatch.setattr(_store, "get_outreach",
                        _async_returns(_draft(body=(
                            "Hi, 123 Main St, Springfield. Reply STOP to unsubscribe."))))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
    monkeypatch.setattr(_store, "count_sent_between", _async_returns(0))
    _env_ok(monkeypatch)

    async def fail_if_called(*a, **kw):
        raise AssertionError("must not send a body without the assistant trailer")
    monkeypatch.setattr(outreach, "_smtp_send", fail_if_called)

    result = await outreach.send_outreach(1)
    assert not result.ok
    assert "trailer" in result.detail


async def test_send_outreach_refuses_the_same_guards_as_send_dry(monkeypatch):
    monkeypatch.setattr(_store, "get_outreach", _async_returns(_draft()))
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(True))
    _env_ok(monkeypatch)

    async def fail_if_called(*a, **kw):
        raise AssertionError("must not attempt to send once a guard has failed")
    monkeypatch.setattr(outreach, "_smtp_send", fail_if_called)

    result = await outreach.send_outreach(1)
    assert not result.ok
    assert "suppression" in result.detail


# ------------------------------------------------ the unattended send's holds
#
# Miro, 2026-10-01: the outreach email sends without a card. These are the
# gates decided in code before it does; each failing one is a hold, which
# kernel.lead_followup turns into the Tier 2 card with the reason.

class _Held:
    """A draft the way the row sits after draft_outreach, and the world
    around it with every hold passing; each test breaks one."""

    def __init__(self, monkeypatch, **over):
        from atlas.modules.agency import contacts
        body = _draft()["body"]
        self.row = _draft(**{"body_sha": outreach.body_sha(body), **over})
        self.sent: list[tuple[str, str]] = []
        self.marked: list[int] = []
        monkeypatch.setenv("AGENCY_OUTREACH_AUTOSEND", "on")
        monkeypatch.setattr(outreach, "auto_hold", REAL_AUTO_HOLD)
        monkeypatch.setattr(_store, "get_outreach", self._get)
        monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
        monkeypatch.setattr(_store, "count_sent_between", _async_returns(0))
        monkeypatch.setattr(_store, "lead_links", _async_returns(LIVE_LINKS))
        monkeypatch.setattr(_store, "has_outreach", _async_returns(False))
        monkeypatch.setattr(_store, "latest_lead_message", _async_returns(None))
        monkeypatch.setattr(contacts, "emails_for", _async_returns(
            [{"address": "Owner@example.com", "found_url": "https://x/contact"}]))
        _no_lead_features(monkeypatch)
        _env_ok(monkeypatch)

        async def smtp(from_address, to_address, subject, body, **_kw):
            self.sent.append((to_address, subject))
            return True, None
        monkeypatch.setattr(outreach, "_smtp_send", smtp)

        async def mark(outreach_id, message_id=None):
            self.marked.append(outreach_id)
            self.row["status"] = "sent"            # the row, as Postgres would have it
        monkeypatch.setattr(_store, "mark_outreach_sent", mark)

    async def _get(self, outreach_id):
        return dict(self.row) if outreach_id == self.row["id"] else None


async def test_every_hold_passing_sends_once_without_a_card(monkeypatch):
    world = _Held(monkeypatch)
    assert (await outreach.send_dry(1)).ok
    result = await outreach.send_outreach(1)
    assert result.ok and result.rollback_token == "1"
    assert world.sent == [("owner@example.com", "About your website")]
    assert world.marked == [1]
    # No double send: the row is 'sent' now, and the first guard says so.
    again = await outreach.send_outreach(1)
    assert not again.ok and "not a draft" in again.detail
    assert world.sent == [("owner@example.com", "About your website")]


async def test_the_switch_off_holds_every_email(monkeypatch):
    world = _Held(monkeypatch)
    monkeypatch.setenv("AGENCY_OUTREACH_AUTOSEND", "off")
    result = await outreach.send_outreach(1)
    assert not result.ok and result.detail.startswith(outreach.HELD)
    assert "AGENCY_OUTREACH_AUTOSEND" in result.detail and world.sent == []


async def test_a_body_that_is_not_the_draft_is_held(monkeypatch):
    world = _Held(monkeypatch, body_sha=outreach.body_sha("something else entirely"))
    result = await outreach.send_dry(1)
    assert not result.ok and "SHA-256" in result.detail
    assert result.detail.startswith(outreach.HELD) and world.sent == []


async def test_a_row_with_no_sha_is_held(monkeypatch):
    world = _Held(monkeypatch, body_sha=None)
    result = await outreach.send_outreach(1)
    assert not result.ok and "SHA-256" in result.detail and world.sent == []


async def test_an_address_not_found_on_their_site_is_held(monkeypatch):
    from atlas.modules.agency import contacts
    world = _Held(monkeypatch)
    monkeypatch.setattr(contacts, "emails_for", _async_returns(
        [{"address": "someone-else@example.com"}]))
    result = await outreach.send_outreach(1)
    assert not result.ok and "not an address found on the business's own site" in (
        result.detail)
    assert world.sent == []


async def test_a_preview_not_deployed_is_held(monkeypatch):
    world = _Held(monkeypatch)
    monkeypatch.setattr(_store, "lead_links", _async_returns(
        {**LIVE_LINKS, "deployed": False}))
    result = await outreach.send_outreach(1)
    assert not result.ok and "not deployed" in result.detail and world.sent == []


async def test_a_second_email_to_a_silent_lead_is_held(monkeypatch):
    world = _Held(monkeypatch)
    monkeypatch.setattr(_store, "has_outreach", _async_returns(True))
    result = await outreach.send_outreach(1)
    assert not result.ok and "already emailed once" in result.detail
    assert world.sent == []


async def test_a_second_email_to_a_lead_who_wrote_back_may_go(monkeypatch):
    world = _Held(monkeypatch)
    monkeypatch.setattr(_store, "has_outreach", _async_returns(True))
    monkeypatch.setattr(_store, "latest_lead_message", _async_returns(
        {"id": 3, "channel": "email", "body": "tell me more"}))
    assert (await outreach.send_outreach(1)).ok and len(world.sent) == 1


async def test_an_approved_card_passes_the_holds_but_not_the_guards(monkeypatch):
    """His approval answers a hold (the run is named proposal:<id> around
    the executor); it does not answer CAN-SPAM or the suppression list."""
    from atlas.kernel import idempotency
    world = _Held(monkeypatch)
    monkeypatch.setenv("AGENCY_OUTREACH_AUTOSEND", "off")
    with idempotency.running("proposal:9"):
        assert (await outreach.send_outreach(1)).ok
    assert len(world.sent) == 1
    world.row["status"] = "draft"
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(True))
    with idempotency.running("proposal:10"):
        result = await outreach.send_outreach(1)
    assert not result.ok and "suppression" in result.detail and len(world.sent) == 1


async def test_the_rollback_is_honest_it_suppresses_and_records_not_unsends(monkeypatch):
    monkeypatch.setattr(_store, "get_outreach", _async_returns(
        _draft(status="sent", to_address="Owner@Example.com")))
    suppressed: list[tuple[str, str]] = []
    meta: list[tuple] = []

    async def add(address, reason):
        suppressed.append((address, reason))
    monkeypatch.setattr(_store, "add_suppression", add)

    async def set_meta(lead_id, key, value):
        meta.append((lead_id, key, value))
    monkeypatch.setattr(_store, "set_lead_meta", set_meta)
    result = await outreach.send_outreach_rollback("1")
    assert result.ok and "cannot be unsent" in result.detail
    assert result.evidence == {"suppressed": "owner@example.com", "unsent": False}
    [(address, reason)] = suppressed
    assert address == "owner@example.com" and "cannot be unsent" in reason
    [(lead_id, key, value)] = meta
    assert (lead_id, key) == (1, "outreach_rolled_back")
    assert value["outreach_id"] == 1 and value["to"] == "owner@example.com"


async def test_the_rollback_with_no_token_touches_nothing(monkeypatch):
    async def never(*_a, **_kw):
        raise AssertionError("nothing to roll back")
    monkeypatch.setattr(_store, "add_suppression", never)
    assert (await outreach.send_outreach_rollback("")).ok


# --------------------------------------------------------------- _smtp_send

class _FakeSmtplibServer:
    """Stands in for `smtplib.SMTP` used as a context manager."""

    instances: ClassVar[list[_FakeSmtplibServer]] = []

    def __init__(self, host, port, timeout=25):
        self.host = host
        self.port = port
        self.starttls_called = False
        self.login_args = None
        self.sent_message = None
        _FakeSmtplibServer.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.starttls_called = True

    def login(self, user, password):
        self.login_args = (user, password)

    def send_message(self, message):
        self.sent_message = message


async def test_smtp_send_uses_smtplib_off_the_event_loop_when_aiosmtplib_is_absent(
    monkeypatch,
):
    monkeypatch.setenv("AGENCY_SMTP_HOST", "smtp.sendfrom.example")
    monkeypatch.setenv("AGENCY_SMTP_PORT", "587")
    monkeypatch.setenv("AGENCY_SMTP_USER", "relay@sendfrom.example")
    monkeypatch.setenv("AGENCY_SMTP_PASSWORD", "secret")
    monkeypatch.setattr(outreach.importlib.util, "find_spec", lambda name: None)

    _FakeSmtplibServer.instances.clear()
    fake_smtplib = type("smtplib", (), {"SMTP": _FakeSmtplibServer})
    monkeypatch.setitem(sys.modules, "smtplib", fake_smtplib)

    ok, reason = await outreach._smtp_send(
        "outreach@sendfrom.example", "owner@example.com", "About your site", "Hi there.")

    assert ok
    assert reason is None
    server = _FakeSmtplibServer.instances[-1]
    assert server.starttls_called
    assert server.login_args == ("relay@sendfrom.example", "secret")
    assert server.sent_message["From"] == "outreach@sendfrom.example"
    assert server.sent_message["To"] == "owner@example.com"
    assert server.sent_message["Subject"] == "About your site"


async def test_smtp_send_uses_aiosmtplib_when_importable(monkeypatch):
    monkeypatch.setenv("AGENCY_SMTP_HOST", "smtp.sendfrom.example")
    monkeypatch.setenv("AGENCY_SMTP_PORT", "587")
    monkeypatch.setenv("AGENCY_SMTP_USER", "relay@sendfrom.example")
    monkeypatch.setenv("AGENCY_SMTP_PASSWORD", "secret")

    real_find_spec = outreach.importlib.util.find_spec

    def fake_find_spec(name):
        return object() if name == "aiosmtplib" else real_find_spec(name)
    monkeypatch.setattr(outreach.importlib.util, "find_spec", fake_find_spec)

    sent = {}

    async def fake_send(message, *, hostname, port, username, password, start_tls):
        sent["message"] = message
        sent["hostname"] = hostname
        sent["port"] = port
        sent["username"] = username
        sent["password"] = password
        sent["start_tls"] = start_tls

    fake_aiosmtplib = type("aiosmtplib", (), {"send": staticmethod(fake_send)})
    monkeypatch.setitem(sys.modules, "aiosmtplib", fake_aiosmtplib)

    ok, reason = await outreach._smtp_send(
        "outreach@sendfrom.example", "owner@example.com", "About your site", "Hi there.")

    assert ok
    assert reason is None
    assert sent["hostname"] == "smtp.sendfrom.example"
    assert sent["port"] == 587
    assert sent["start_tls"] is True
    assert sent["message"]["From"] == "outreach@sendfrom.example"
    assert sent["message"]["Subject"] == "About your site"


async def test_smtp_send_refuses_when_smtp_env_is_incomplete(monkeypatch):
    monkeypatch.delenv("AGENCY_SMTP_HOST", raising=False)
    ok, reason = await outreach._smtp_send("a@b.example", "c@d.example", "s", "body")
    assert not ok
    assert "AGENCY_SMTP" in reason


# ----------------------------------------------------- action registration

def _action(name: str):
    return next(a for a in agency.MODULE.actions() if a.name == name)


def test_send_outreach_action_is_tier_auto_with_an_honest_rollback():
    # Miro, 2026-10-01: email the clients about their previews; anything not
    # urgent runs without a card. The holds live in outreach.auto_hold.
    action = _action("agency.send_outreach")
    assert action.tier is Tier.AUTO
    assert not action.irreversible
    assert action.dry_run is outreach.send_dry
    assert action.rollback is outreach.send_outreach_rollback


def test_classify_agency_send_outreach_is_tier_auto():
    decision = classify(_action("agency.send_outreach"))
    assert decision.tier is Tier.AUTO
    assert decision.allowed


def test_deploy_site_action_is_tier_auto_with_a_rollback():
    # Miro, 2026-09-30: "anything that is not urgent, you approve it". A
    # preview on a mapfront.io subdomain; the previous release is kept.
    action = _action("agency.deploy_site")
    assert action.tier is Tier.AUTO
    assert action.dry_run is not None and action.rollback is not None
    decision = classify(action)
    assert decision.tier is Tier.AUTO
    assert decision.allowed


# -------------------------------------------------------------- suppression

async def test_suppress_lowercases_and_can_be_rolled_back(monkeypatch):
    added = []

    async def fake_add(address, reason):
        added.append((address, reason))
    monkeypatch.setattr(_store, "add_suppression", fake_add)

    result = await outreach.suppress("Owner@Example.com", "bounced")
    assert result.ok
    assert added == [("owner@example.com", "bounced")]
    assert result.rollback_token == "owner@example.com"


async def test_suppress_refuses_an_empty_address():
    result = await outreach.suppress("   ", "why")
    assert not result.ok


async def test_suppress_rollback_removes_the_address(monkeypatch):
    removed = []

    async def fake_remove(address):
        removed.append(address)
    monkeypatch.setattr(_store, "remove_suppression", fake_remove)

    result = await outreach.suppress_rollback("owner@example.com")
    assert result.ok
    assert removed == ["owner@example.com"]


# ------------------------------------------------------------ draft_outreach

LIVE_LINKS = {"domain": None, "slug": "joes-plumbing-1", "deployed": True,
              "webild_url": None, "mockup": None}


async def test_draft_outreach_wraps_audit_findings_before_the_think_call(monkeypatch):
    """The model drafts the call script and the one-pager (nothing sends
    those); the audit reaches it quoted as untrusted data."""
    lead = {"id": 1, "name": "Joe's Plumbing", "category": "plumber",
           "website": "https://joes.example", "features": {"audit_severity": 2.0}}
    monkeypatch.setattr(_store, "get_lead", _async_returns(lead))
    monkeypatch.setattr(_store, "latest_mockup_path", _async_returns(None))
    monkeypatch.setattr(_store, "latest_sent_outreach_id", _async_returns(None))
    monkeypatch.setattr(outreach, "audit_lead", _async_returns(
        Audit("https://joes.example", [Finding("no_https", 2, "plain http")], {}, 2)))

    captured = {}

    async def fake_think(need, system, messages, max_tokens=800):
        captured["message"] = messages[0].content
        return Completion(text="Hi Joe, your site is served over plain http.",
                          provider="test", model="test")
    monkeypatch.setattr(outreach, "think", fake_think)

    inserted = {}

    async def fake_insert(lead_id, kind, to_address, subject, body, followup_of,
                          body_sha=None):
        inserted.update(lead_id=lead_id, kind=kind, to_address=to_address,
                        subject=subject, body=body, followup_of=followup_of,
                        body_sha=body_sha)
        return 42
    monkeypatch.setattr(_store, "insert_outreach", fake_insert)

    result = await outreach.draft_outreach(1, "call_script")
    assert result.ok
    assert "<untrusted_data source='agency-audit'>" in captured["message"]
    assert inserted["to_address"] is None      # nobody guesses an email address
    assert inserted["followup_of"] is None
    assert inserted["body_sha"] is None        # only an email carries the sha
    # evidence carries the lead's own features -- what the judgment layer
    # reads back on the proposal card (score.py's docstring).
    assert result.evidence["lead_features"] == {"audit_severity": 2.0}
    assert result.evidence["audit_severity"] == 2
    assert result.evidence["outreach_id"] == 42
    assert result.evidence["kind"] == "call_script"


def _email_world(monkeypatch, lead, *, parent=None):
    monkeypatch.setattr(_store, "get_lead", _async_returns(lead))
    monkeypatch.setattr(_store, "latest_mockup_path", _async_returns(None))
    monkeypatch.setattr(_store, "latest_sent_outreach_id", _async_returns(parent))
    monkeypatch.setattr(_store, "lead_links", _async_returns(LIVE_LINKS))
    monkeypatch.setattr(outreach, "audit_lead", _async_returns(Audit(None, [], {}, 0)))
    monkeypatch.setenv("AGENCY_POSTAL_ADDRESS", "123 Main St, Springfield")
    monkeypatch.setenv("RECEPTION_ENABLED", "1")
    monkeypatch.setenv("TWILIO_FROM", "+17185550000")

    async def no_model(*_a, **_kw):
        raise AssertionError("the email is written by code, never a model")
    monkeypatch.setattr(outreach, "think", no_model)
    inserted = {}

    async def fake_insert(lead_id, kind, to_address, subject, body, followup_of,
                          body_sha=None):
        inserted.update(to=to_address, subject=subject, body=body,
                        followup_of=followup_of, body_sha=body_sha)
        return 44
    monkeypatch.setattr(_store, "insert_outreach", fake_insert)
    return inserted


async def test_the_email_is_miros_framing_written_by_code(monkeypatch):
    """Miro, 2026-10-01: "this is ATLAS Academy, we saw your website needed
    help ... we made you a free preview to look at". No model, no claim, a
    CAN-SPAM footer, and the sha of what was written stored with the row."""
    from atlas.modules.agency import contacts
    lead = {"id": 1, "name": "Joe's Plumbing", "category": "plumber",
            "website": "https://joes.example"}
    inserted = _email_world(monkeypatch, lead)
    monkeypatch.setattr(contacts, "published_email",
                        _async_returns({"address": "office@joes.example"}))
    result = await outreach.draft_outreach(1, "email")
    assert result.ok, result.detail
    body = inserted["body"]
    assert body.startswith("This is ATLAS Academy (Mapfront).")
    assert ("We looked at Joe's Plumbing's website and thought it needed help, so "
            "we built a free preview: https://joes-plumbing-1.mapfront.io/") in body
    assert ("Reply to this email or call Mapfront at (718) 555-0000 and we'll walk "
            "you through it; if you'd like, we can put you through to the owner.") in body
    assert "https://joes-plumbing-1.mapfront.io/#call-me" in body
    assert outreach.can_spam_ok(body, "123 Main St, Springfield") == []
    assert body.endswith(outreach.TRAILER)
    assert inserted["to"] == "office@joes.example"
    assert inserted["subject"] == "A free website preview for Joe's Plumbing"
    assert inserted["body_sha"] == outreach.body_sha(body)
    assert result.evidence["body_sha"] == inserted["body_sha"][:12]
    # No figure, no promise: nothing in it for the honesty rules to refuse.
    from atlas.modules.agency import playbook, pricing
    assert pricing.find_prices(body) == []
    assert playbook.honesty_refusal(body) is None


async def test_a_lead_with_no_website_is_not_told_we_looked_at_it(monkeypatch):
    lead = {"id": 1, "name": "Joe's Plumbing", "category": "plumber", "website": None}
    inserted = _email_world(monkeypatch, lead)
    monkeypatch.delenv("RECEPTION_ENABLED", raising=False)
    assert (await outreach.draft_outreach(1, "email")).ok
    body = inserted["body"]
    assert "looked at" not in body
    assert ("Joe's Plumbing has no website that we could find, so we built a free "
            "preview: https://joes-plumbing-1.mapfront.io/") in body
    # The line is off: no number is given out, and the reply path stays.
    assert "call Mapfront at" not in body
    assert "Reply to this email and we'll walk you through it" in body


async def test_a_follow_up_email_says_so(monkeypatch):
    lead = {"id": 1, "name": "Joe's Plumbing", "category": "plumber", "website": None}
    inserted = _email_world(monkeypatch, lead, parent=7)
    assert (await outreach.draft_outreach(1, "email")).ok
    assert inserted["followup_of"] == 7
    assert "A short follow-up to our last email" in inserted["body"]
    assert inserted["subject"].startswith("Following up:")


async def test_an_email_draft_needs_a_live_preview(monkeypatch):
    """"We built you a free preview: <url>" needs a URL that opens."""
    lead = {"id": 1, "name": "Joe's Plumbing", "category": "plumber", "website": None}
    inserted = _email_world(monkeypatch, lead)
    monkeypatch.setattr(_store, "lead_links", _async_returns({**LIVE_LINKS, "deployed": False}))
    result = await outreach.draft_outreach(1, "email")
    assert not result.ok and "deploy" in result.detail
    assert inserted == {}


async def test_draft_outreach_chains_onto_the_last_sent_message(monkeypatch):
    lead = {"id": 1, "name": "Joe's Plumbing", "category": "plumber", "website": None}
    monkeypatch.setattr(_store, "get_lead", _async_returns(lead))
    monkeypatch.setattr(_store, "latest_mockup_path", _async_returns(None))
    monkeypatch.setattr(_store, "latest_sent_outreach_id", _async_returns(7))
    monkeypatch.setattr(outreach, "audit_lead", _async_returns(Audit(None, [], {}, 0)))
    monkeypatch.setattr(outreach, "think", _async_returns(
        Completion(text="following up", provider="t", model="t")))

    inserted = {}

    async def fake_insert(lead_id, kind, to_address, subject, body, followup_of,
                          body_sha=None):
        inserted["followup_of"] = followup_of
        return 43
    monkeypatch.setattr(_store, "insert_outreach", fake_insert)

    result = await outreach.draft_outreach(1, "call_script")
    assert result.ok
    assert inserted["followup_of"] == 7


async def test_draft_outreach_rollback_deletes_the_row(monkeypatch):
    deleted = []

    async def fake_delete(outreach_id):
        deleted.append(outreach_id)
    monkeypatch.setattr(_store, "delete_outreach", fake_delete)

    result = await outreach.draft_outreach_rollback("42")
    assert result.ok
    assert deleted == [42]


# ------------------------------------------------------------------ mockup

async def test_make_mockup_refuses_without_the_licence_flag(monkeypatch):
    monkeypatch.delenv("AGENCY_GETLAYERS_LICENCE_OK", raising=False)
    result = await mockup.make_mockup(1)
    assert not result.ok
    assert "AGENCY_GETLAYERS_LICENCE_OK" in result.detail


async def test_make_mockup_dry_also_refuses_without_the_licence_flag(monkeypatch):
    monkeypatch.delenv("AGENCY_GETLAYERS_LICENCE_OK", raising=False)
    result = await mockup.make_mockup_dry(1)
    assert not result.ok


class _FakeMcpClient:
    """Stands in for `_mcp.McpClient` in mockup tests: three tools present,
    each `call` handing back text distinguishable by tool name."""

    def __init__(self, *a, **kw) -> None:
        pass

    async def initialize(self):
        return {}, None

    async def list_tools(self):
        return [{"name": "getlayers_search"}, {"name": "getlayers_compositions"},
                {"name": "getlayers_materialize"}], None

    async def call(self, name, arguments):
        return f"<result from {name}>", None


def _mockup_env(monkeypatch, tmp_path, lead: dict, think_text: str | Exception):
    """make_mockup with a lead, no client.json, and a scripted `think`."""
    monkeypatch.setenv("AGENCY_GETLAYERS_LICENCE_OK", "true")
    monkeypatch.delenv("AGENCY_FORM_ACTION", raising=False)
    monkeypatch.setattr(mockup, "MOCKUP_DIR", tmp_path / "mockups")
    monkeypatch.setattr(build, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(_store, "get_lead", _async_returns(lead))
    monkeypatch.setattr(_store, "insert_mockup", _async_returns(9))
    monkeypatch.setattr(_store, "set_lead_status", _async_returns(None))
    captured: dict = {}

    async def fake_think(need, system, messages, max_tokens=4000):
        captured["system"] = system
        captured["message"] = messages[0].content
        if isinstance(think_text, Exception):
            raise think_text
        return Completion(text=think_text, provider="t", model="t")
    monkeypatch.setattr(mockup, "think", fake_think)

    class _NoGetLayers(_FakeMcpClient):
        def __init__(self, *a, **kw) -> None:
            raise AssertionError("a mockup must not call GetLayers at run time")
    monkeypatch.setattr(mockup, "McpClient", _NoGetLayers)
    return captured


# What the 2026-09-30 preview at eco-electrical-services-llc-38.mapfront.io
# actually said, as its body copy.
ECO_PARAGRAPH = (
    "The data contains a JSON object with information about a business called Eco "
    "Electrical Services, LLC ... The majority of the data consists of GetLayers search "
    "results ... the final section of the data appears to be an error message from a "
    "tool called GetLayers, indicating an input validation issue.")


async def test_make_mockup_renders_the_template_and_never_calls_getlayers(
        monkeypatch, tmp_path):
    lead = {"id": 1, "name": "Joe's Plumbing", "category": "plumber",
            "phone": "(332) 222-0135", "address": "885 6th Ave, New York, NY 10001",
            "website": None, "status": "audited"}
    copy = json.dumps({
        "headline": "Plumbing help across Manhattan",
        "subhead": "Leaks, drains and water heaters sorted.",
        "about": "We are a plumbing company on Sixth Avenue in New York.",
        "services": [{"name": "Leak repairs", "line": "Pipes that drip or run."},
                     {"name": "Drains", "line": "Blocked sinks cleared."},
                     {"name": "Water heaters", "line": "When the hot water stops."}],
        "faq": [{"q": "Do you work in my building?", "a": "Call and ask us."}]})
    captured = _mockup_env(monkeypatch, tmp_path, lead, copy)

    result = await mockup.make_mockup(1)
    assert result.ok, result.detail
    # The lead's details reach the prompt only as quoted, untrusted data.
    assert "<untrusted_data source='places'>" in captured["message"]
    html = (tmp_path / "mockups" / "1.html").read_text()
    assert html.startswith("<!doctype html>")
    assert 'data-family="trades"' in html and "Plumbing help across Manhattan" in html
    assert "Preview made by" in html and 'content="noindex"' in html
    assert (tmp_path / "mockups" / "fonts" / "archivo-var.woff2").exists()
    record = json.loads((tmp_path / "mockups" / "1.copy.json").read_text())
    assert record["family"] == "trades" and record["style_id"] == "ridgeline-style"
    assert result.evidence["copy_source"] == "model"


async def test_make_mockup_never_publishes_the_eco_electrical_paragraph(
        monkeypatch, tmp_path):
    """The model answering with a summary of its inputs is no longer a page:
    the answer is not JSON, so every field is the code-written default."""
    lead = {"id": 38, "name": "Eco Electrical Services, LLC", "category": "electrician",
            "phone": "(212) 915-0610", "website": None, "status": "new"}
    _mockup_env(monkeypatch, tmp_path, lead, ECO_PARAGRAPH)
    result = await mockup.make_mockup(38)
    assert result.ok, result.detail
    html = (tmp_path / "mockups" / "38.html").read_text()
    for word in ("data contains", "JSON object", "GetLayers", "validation", "error message"):
        assert word not in html
    assert "Eco Electrical Services" in html and "tel:2129150610" in html
    assert result.evidence["copy_source"] == "defaults"


async def test_make_mockup_with_no_model_uses_the_defaults(monkeypatch, tmp_path):
    lead = {"id": 2, "name": "Petal & Stem", "category": "florist",
            "phone": "(646) 555-0101", "website": None, "status": "new"}
    _mockup_env(monkeypatch, tmp_path, lead, RuntimeError("no provider"))
    result = await mockup.make_mockup(2)
    assert result.ok, result.detail
    assert 'data-family="food"' in (tmp_path / "mockups" / "2.html").read_text()
    assert "code defaults used" in result.detail


async def test_make_mockup_rollback_withdraws_the_row_and_restores_status(monkeypatch, tmp_path):
    html_path = tmp_path / "1.html"
    html_path.write_text("<html></html>")
    token = json.dumps({"lead_id": 1, "mockup_id": 9, "previous_status": "audited",
                        "html_path": str(html_path), "screenshot_path": None})

    deleted = []

    async def fake_delete(mockup_id):
        deleted.append(mockup_id)
    monkeypatch.setattr(_store, "delete_mockup", fake_delete)

    restored = []

    async def fake_status(lead_id, status):
        restored.append((lead_id, status))
    monkeypatch.setattr(_store, "set_lead_status", fake_status)

    result = await mockup.make_mockup_rollback(token)
    assert result.ok
    assert deleted == [9]
    assert restored == [(1, "audited")]
    assert not html_path.exists()


async def test_make_mockup_rollback_with_no_token_is_a_noop(monkeypatch):
    async def fail_if_called(*a, **kw):
        raise AssertionError("nothing was written; nothing should be touched")
    monkeypatch.setattr(_store, "delete_mockup", fail_if_called)
    result = await mockup.make_mockup_rollback("")
    assert result.ok


# ------------------------------------------------------------------- build

async def test_build_site_refuses_without_a_mockup_on_disk(monkeypatch):
    monkeypatch.setattr(_store, "get_lead", _async_returns({"id": 1, "name": "Joe's"}))
    monkeypatch.setattr(_store, "latest_mockup_path", _async_returns(None))
    result = await build.build_site(1)
    assert not result.ok
    assert "mockup" in result.detail


async def test_build_site_renders_the_designed_template_into_a_slug_directory(
        monkeypatch, tmp_path):
    mockup_html = tmp_path / "mockup.html"
    mockup_html.write_text("<html>mockup</html>")
    mockup_html.with_suffix(".copy.json").write_text(
        json.dumps({"family": "trades", "copy": {"headline": "Plumbing in Queens"}}))
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "sites")
    monkeypatch.setattr(_store, "get_lead",
                        _async_returns({"id": 1, "name": "Joe's Plumbing",
                                        "category": "plumber"}))
    monkeypatch.setattr(_store, "latest_mockup_path", _async_returns(str(mockup_html)))
    monkeypatch.setattr(_store, "get_site", _async_returns(None))
    monkeypatch.setattr(_store, "insert_site", _async_returns(5))
    monkeypatch.setattr(_store, "set_lead_status", _async_returns(None))

    result = await build.build_site(1)
    assert result.ok, result.detail
    slug = build.slugify("Joe's Plumbing", 1)
    html = (tmp_path / "sites" / slug / "index.html").read_text()
    # The page is rendered from the checked-in template, never copied from
    # model output (2026-09-30); the launch items are added around it.
    assert html.startswith("<!doctype html>") and 'data-family="trades"' in html
    assert "Plumbing in Queens" in html and "mockup</html>" not in html
    assert (tmp_path / "sites" / slug / "fonts" / "archivo-var.woff2").exists()


async def test_build_site_refuses_a_mockup_from_before_the_templates(monkeypatch, tmp_path):
    """The raw-model mockups the templates replaced are never copied into a
    site again: no validated copy beside the page, no build."""
    mockup_html = tmp_path / "mockup.html"
    mockup_html.write_text("<!doctype html><html><body><p>" + ECO_PARAGRAPH
                           + "</p></body></html>")
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "sites")
    monkeypatch.setattr(_store, "get_lead",
                        _async_returns({"id": 38, "name": "Eco Electrical Services, LLC"}))
    monkeypatch.setattr(_store, "latest_mockup_path", _async_returns(str(mockup_html)))
    result = await build.build_site(38)
    dry = await build.build_site_dry(38)
    assert not result.ok and "run agency.mockup again" in result.detail
    assert not dry.ok and "run agency.mockup again" in dry.detail
    assert not (tmp_path / "sites").exists()


async def test_build_site_runs_the_filesystem_write_through_a_thread(monkeypatch, tmp_path):
    """The copy/mkdir work is blocking; it must go through
    `asyncio.to_thread`, not run straight on the event loop."""
    mockup_html = tmp_path / "mockup.html"
    mockup_html.write_text("<html>mockup</html>")
    mockup_html.with_suffix(".copy.json").write_text(
        json.dumps({"family": "trades", "copy": {}}))
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "sites")
    monkeypatch.setattr(_store, "get_lead",
                        _async_returns({"id": 1, "name": "Joe's Plumbing"}))
    monkeypatch.setattr(_store, "latest_mockup_path", _async_returns(str(mockup_html)))
    monkeypatch.setattr(_store, "get_site", _async_returns(None))
    monkeypatch.setattr(_store, "insert_site", _async_returns(5))
    monkeypatch.setattr(_store, "set_lead_status", _async_returns(None))

    calls = []
    real_to_thread = build.asyncio.to_thread

    async def recording_to_thread(func, *args, **kwargs):
        calls.append(func)
        return await real_to_thread(func, *args, **kwargs)
    monkeypatch.setattr(build.asyncio, "to_thread", recording_to_thread)

    result = await build.build_site(1)
    assert result.ok
    assert build._write_build in calls


async def test_build_site_rollback_removes_a_freshly_created_site(monkeypatch, tmp_path):
    site_dir = tmp_path / "sites" / "joes-1"
    site_dir.mkdir(parents=True)
    (site_dir / "index.html").write_text("x")
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "sites")

    deleted = []

    async def fake_delete(slug):
        deleted.append(slug)
    monkeypatch.setattr(_store, "delete_site", fake_delete)

    token = json.dumps({"slug": "joes-1", "created": True, "backup_dir": None})
    result = await build.build_site_rollback(token)
    assert result.ok
    assert deleted == ["joes-1"]
    assert not site_dir.exists()


# ------------------------------------------------------------- build: store

def _upload(monkeypatch, tmp_path, lead_id, name, content=b"fake-image-bytes"):
    """Points `build.UPLOADS_DIR` at a tmp folder and writes `name` under
    this lead's own uploads subfolder, returning its absolute path -- the
    one place I-3 allows `product_photo`/`reviews_path` to point at."""
    uploads = tmp_path / "uploads"
    monkeypatch.setattr(build, "UPLOADS_DIR", uploads)
    lead_dir = uploads / str(lead_id)
    lead_dir.mkdir(parents=True, exist_ok=True)
    path = lead_dir / name
    path.write_bytes(content)
    return str(path)


async def test_build_site_refuses_an_unknown_kind():
    result = await build.build_site(1, kind="storefront")
    assert not result.ok
    assert "storefront" in result.detail


async def test_build_site_store_refuses_without_the_getlayers_licence(monkeypatch):
    monkeypatch.delenv("AGENCY_GETLAYERS_LICENCE_OK", raising=False)
    monkeypatch.setattr(_store, "get_lead", _async_returns({"id": 1, "name": "Joe's"}))
    result = await build.build_site(
        1, kind="store", product_photo="/photos/mug.jpg", product_description="a mug")
    assert not result.ok
    assert "AGENCY_GETLAYERS_LICENCE_OK" in result.detail


async def test_build_site_store_refuses_without_a_product_photo(monkeypatch):
    monkeypatch.setenv("AGENCY_GETLAYERS_LICENCE_OK", "true")
    monkeypatch.setattr(_store, "get_lead", _async_returns({"id": 1, "name": "Joe's"}))
    result = await build.build_site(1, kind="store", product_description="a mug")
    assert not result.ok
    assert "product_photo" in result.detail


async def test_build_site_store_refuses_without_a_product_description(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENCY_GETLAYERS_LICENCE_OK", "true")
    monkeypatch.setattr(_store, "get_lead", _async_returns({"id": 1, "name": "Joe's"}))
    photo = _upload(monkeypatch, tmp_path, 1, "mug.jpg")
    result = await build.build_site(1, kind="store", product_photo=photo)
    assert not result.ok
    assert "product_description" in result.detail


async def test_build_site_store_refuses_with_no_phone_and_no_email(monkeypatch, tmp_path):
    """M-5: a page whose only job is "call or email to order" must refuse
    to exist at all if there is no phone and no email to put on it."""
    monkeypatch.setenv("AGENCY_GETLAYERS_LICENCE_OK", "true")
    lead = {"id": 1, "name": "Joe's", "phone": None}
    monkeypatch.setattr(_store, "get_lead", _async_returns(lead))
    photo = _upload(monkeypatch, tmp_path, 1, "mug.jpg")
    result = await build.build_site(
        1, kind="store", product_photo=photo, product_description="a mug")
    assert not result.ok
    assert result.detail == "a store needs a way to order"


async def test_build_site_store_refuses_with_no_phone_and_no_email_dry(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENCY_GETLAYERS_LICENCE_OK", "true")
    lead = {"id": 1, "name": "Joe's", "phone": None}
    monkeypatch.setattr(_store, "get_lead", _async_returns(lead))
    photo = _upload(monkeypatch, tmp_path, 1, "mug.jpg")
    result = await build.build_site_dry(
        1, kind="store", product_photo=photo, product_description="a mug")
    assert not result.ok
    assert result.detail == "a store needs a way to order"


async def test_build_site_store_refuses_a_product_photo_that_does_not_exist(monkeypatch):
    monkeypatch.setenv("AGENCY_GETLAYERS_LICENCE_OK", "true")
    lead = {"id": 1, "name": "Joe's", "phone": "555-1234"}
    monkeypatch.setattr(_store, "get_lead", _async_returns(lead))
    result = await build.build_site(
        1, kind="store", product_photo="/no/such/photo.jpg", product_description="a mug")
    assert not result.ok
    assert "product_photo not found" in result.detail


async def test_build_site_store_refuses_a_photo_outside_the_uploads_folder(
        monkeypatch, tmp_path):
    """I-3: a path outside this lead's uploads folder is refused, even
    when it points at a real, correctly-suffixed image -- an absolute
    path handed to a Tier AUTO action by a model is not a place ATLAS
    reads from just because the file happens to exist."""
    monkeypatch.setenv("AGENCY_GETLAYERS_LICENCE_OK", "true")
    lead = {"id": 1, "name": "Joe's", "phone": "555-1234"}
    monkeypatch.setattr(_store, "get_lead", _async_returns(lead))
    monkeypatch.setattr(build, "UPLOADS_DIR", tmp_path / "uploads")
    outside = tmp_path / "elsewhere" / "mug.jpg"
    outside.parent.mkdir(parents=True)
    outside.write_bytes(b"fake-image-bytes")

    result = await build.build_site(
        1, kind="store", product_photo=str(outside), product_description="a mug")
    assert not result.ok
    assert "uploads folder" in result.detail


async def test_build_site_store_refuses_a_non_image_suffix(monkeypatch, tmp_path):
    """I-3: only .png/.jpg/.jpeg/.webp are accepted for the photo -- a
    `.env` (or any other file) sitting in the uploads folder is refused
    by suffix, not silently copied and served."""
    monkeypatch.setenv("AGENCY_GETLAYERS_LICENCE_OK", "true")
    lead = {"id": 1, "name": "Joe's", "phone": "555-1234"}
    monkeypatch.setattr(_store, "get_lead", _async_returns(lead))
    env_file = _upload(monkeypatch, tmp_path, 1, ".env", content=b"SECRET=1")

    result = await build.build_site(
        1, kind="store", product_photo=env_file, product_description="a mug")
    assert not result.ok
    assert "must end in one of" in result.detail


async def test_build_site_store_refuses_a_symlink_escaping_the_uploads_folder(
        monkeypatch, tmp_path):
    """I-3: a symlink planted inside the uploads folder that points
    outside it must not be followed -- `Path.resolve()` follows the link,
    and the confinement check catches where it actually lands."""
    monkeypatch.setenv("AGENCY_GETLAYERS_LICENCE_OK", "true")
    lead = {"id": 1, "name": "Joe's", "phone": "555-1234"}
    monkeypatch.setattr(_store, "get_lead", _async_returns(lead))
    uploads = tmp_path / "uploads"
    monkeypatch.setattr(build, "UPLOADS_DIR", uploads)
    lead_dir = uploads / "1"
    lead_dir.mkdir(parents=True)
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"not actually yours")
    symlink = lead_dir / "mug.jpg"
    symlink.symlink_to(outside)

    result = await build.build_site(
        1, kind="store", product_photo=str(symlink), product_description="a mug")
    assert not result.ok
    assert "uploads folder" in result.detail


async def test_build_site_store_refuses_a_reviews_file_outside_the_uploads_folder(
        monkeypatch, tmp_path):
    monkeypatch.setenv("AGENCY_GETLAYERS_LICENCE_OK", "true")
    lead = {"id": 1, "name": "Joe's", "phone": "555-1234"}
    monkeypatch.setattr(_store, "get_lead", _async_returns(lead))
    photo = _upload(monkeypatch, tmp_path, 1, "mug.jpg")
    outside_reviews = tmp_path / "elsewhere" / "reviews.json"
    outside_reviews.parent.mkdir(parents=True)
    outside_reviews.write_text(json.dumps([{"text": "great"}]))

    result = await build.build_site(
        1, kind="store", product_photo=photo, product_description="a mug",
        reviews_path=str(outside_reviews))
    assert not result.ok
    assert "uploads folder" in result.detail


async def test_build_site_store_reports_the_mcp_reason_on_initialize_failure(
        monkeypatch, tmp_path):
    monkeypatch.setenv("AGENCY_GETLAYERS_LICENCE_OK", "true")
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "sites")
    lead = {"id": 1, "name": "Joe's Mugs", "phone": "555-1234"}
    monkeypatch.setattr(_store, "get_lead", _async_returns(lead))
    monkeypatch.setattr(_store, "get_site", _async_returns(None))

    photo = _upload(monkeypatch, tmp_path, 1, "mug.jpg")

    class _Refused(_FakeMcpClient):
        async def initialize(self):
            return None, "GetLayers refused the token — check GETLAYERS_TOKEN"
    monkeypatch.setattr(build, "McpClient", _Refused)

    result = await build.build_site(
        1, kind="store", product_photo=photo, product_description="a mug")
    assert not result.ok
    assert "GETLAYERS_TOKEN" in result.detail


async def test_build_site_store_produces_the_five_sections_and_no_payment_form(
        monkeypatch, tmp_path):
    monkeypatch.setenv("AGENCY_GETLAYERS_LICENCE_OK", "true")
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "sites")
    lead = {"id": 1, "name": "Joe's Mugs", "category": "gift shop", "phone": "555-1234",
           "status": "audited"}
    monkeypatch.setattr(_store, "get_lead", _async_returns(lead))
    monkeypatch.setattr(_store, "get_site", _async_returns(None))
    monkeypatch.setattr(_store, "insert_site", _async_returns(5))
    monkeypatch.setattr(_store, "set_lead_status", _async_returns(None))
    monkeypatch.setattr(build, "McpClient", _FakeMcpClient)

    photo = _upload(monkeypatch, tmp_path, 1, "mug.jpg")

    async def fake_think(need, system, messages, max_tokens=2000):
        return Completion(text=json.dumps({
            "headline": "The Perfect Mug",
            "subheadline": "Hand-thrown, dishwasher safe",
            "bundles": ["Buy 2, save 10%", "Gift box add-on"],
            "upsells": ["Add a matching coaster"],
            "faq": [{"q": "When does it ship?", "a": "Within 3 business days"}],
        }), provider="t", model="t")
    monkeypatch.setattr(build, "think", fake_think)

    result = await build.build_site(
        1, kind="store", product_photo=photo,
        product_description="a hand-thrown ceramic mug",
        contact_email="orders@joesmugs.example")

    assert result.ok
    assert result.evidence["kind"] == "store"
    slug = build.slugify("Joe's Mugs", 1)
    site_dir = tmp_path / "sites" / slug
    html = (site_dir / "index.html").read_text()

    for section in ("hero", "bundles", "upsells", "faq"):
        assert f'id="{section}"' in html, f"missing section {section!r}"
    assert "how-to-order" in html
    assert "555-1234" in html
    assert "orders@joesmugs.example" in html
    assert "The Perfect Mug" in html
    # No reviews file, no reviews section -- not even a placeholder
    # (2026-09-29); the launch check lists reviews as NEEDS_CLIENT instead.
    assert 'id="reviews"' not in html and "on their way" not in html
    assert (site_dir / "product.jpg").exists()   # the photo travels with the page
    assert 'src="product.jpg"' in html

    lowered = html.lower()
    for forbidden in ("<form", "card number", "checkout", "credit card",
                     "cvv", 'type="submit"', "stripe", "paypal"):
        assert forbidden not in lowered, f"found {forbidden!r} in a store page"


async def test_build_site_store_drops_fabricated_ratings_and_discounts(
        monkeypatch, tmp_path):
    """I-4(b): a bundle/faq line carrying an invented rating, customer
    count, or percentage not in the product description must not survive
    into the page, even though the prompt (I-4a) already asks the model
    not to write one -- the prompt is not the enforcement. An ordinary
    small quantity ("Buy 2, get 1") is not a fabricated claim and must
    survive."""
    monkeypatch.setenv("AGENCY_GETLAYERS_LICENCE_OK", "true")
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "sites")
    lead = {"id": 4, "name": "Fig Co", "category": "shop", "phone": "555-7777"}
    monkeypatch.setattr(_store, "get_lead", _async_returns(lead))
    monkeypatch.setattr(_store, "get_site", _async_returns(None))
    monkeypatch.setattr(_store, "insert_site", _async_returns(8))
    monkeypatch.setattr(_store, "set_lead_status", _async_returns(None))
    monkeypatch.setattr(build, "McpClient", _FakeMcpClient)

    photo = _upload(monkeypatch, tmp_path, 4, "widget.jpg")

    async def fake_think(need, system, messages, max_tokens=2000):
        return Completion(text=json.dumps({
            "headline": "Rated 4.9 stars by 10,000 customers",
            "subheadline": "Save 20% today only",
            "bundles": ["Buy 2, get 1 free", "Rated 4.8/5 by our fans"],
            "upsells": ["Add a case"],
            "faq": [{"q": "Any discount?", "a": "Yes, save 30% right now"},
                    {"q": "How big is it?", "a": "Fits in one hand"}],
        }), provider="t", model="t")
    monkeypatch.setattr(build, "think", fake_think)

    result = await build.build_site(
        4, kind="store", product_photo=photo, product_description="a small widget")

    assert result.ok
    slug = build.slugify("Fig Co", 4)
    html = (tmp_path / "sites" / slug / "index.html").read_text()

    # Fabricated ratings/discounts/counts never appear.
    assert "4.9 stars" not in html
    assert "10,000 customers" not in html
    assert "Save 20%" not in html
    assert "4.8/5" not in html
    assert "save 30%" not in html
    assert "Any discount?" not in html   # the whole Q&A pair is dropped

    # An ordinary small quantity is not a fabricated claim and survives.
    assert "Buy 2, get 1 free" in html
    assert "Fits in one hand" in html


async def test_build_site_store_escapes_model_copy_that_looks_like_markup(
        monkeypatch, tmp_path):
    """The no-payment-form guarantee has to hold even when the model returns
    something that looks like a form or a script -- otherwise it is only a
    prompt instruction, not something this code enforces."""
    monkeypatch.setenv("AGENCY_GETLAYERS_LICENCE_OK", "true")
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "sites")
    lead = {"id": 3, "name": "Test Co", "category": "shop", "phone": "555-0000"}
    monkeypatch.setattr(_store, "get_lead", _async_returns(lead))
    monkeypatch.setattr(_store, "get_site", _async_returns(None))
    monkeypatch.setattr(_store, "insert_site", _async_returns(7))
    monkeypatch.setattr(_store, "set_lead_status", _async_returns(None))
    monkeypatch.setattr(build, "McpClient", _FakeMcpClient)

    photo = _upload(monkeypatch, tmp_path, 3, "widget.jpg")

    async def fake_think(need, system, messages, max_tokens=2000):
        return Completion(text=json.dumps({
            "headline": '<form action="/pay"><input name="card"></form>',
            "faq": [{"q": "<script>alert(1)</script>", "a": "yes"}],
        }), provider="t", model="t")
    monkeypatch.setattr(build, "think", fake_think)

    result = await build.build_site(
        3, kind="store", product_photo=photo, product_description="a widget")

    assert result.ok
    slug = build.slugify("Test Co", 3)
    html = (tmp_path / "sites" / slug / "index.html").read_text()
    assert "<form" not in html
    # The only scripts are ATLAS's own JSON-LD blocks, where the model's
    # "<script>" is serialised as <script -- data, never a tag.
    assert "<script" not in html.replace('<script type="application/ld+json">', "")
    assert "<script>alert" not in html
    assert "&lt;form" in html
    assert "&lt;script" in html


async def test_build_site_store_uses_real_reviews_from_a_client_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENCY_GETLAYERS_LICENCE_OK", "true")
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "sites")
    lead = {"id": 2, "name": "Ada's Candles", "category": "candles", "phone": None}
    monkeypatch.setattr(_store, "get_lead", _async_returns(lead))
    monkeypatch.setattr(_store, "get_site", _async_returns(None))
    monkeypatch.setattr(_store, "insert_site", _async_returns(6))
    monkeypatch.setattr(_store, "set_lead_status", _async_returns(None))
    monkeypatch.setattr(build, "McpClient", _FakeMcpClient)

    photo = _upload(monkeypatch, tmp_path, 2, "candle.jpg")
    reviews_file = build.UPLOADS_DIR / "2" / "reviews.json"
    reviews_file.write_text(json.dumps([
        {"author": "Priya", "text": "Smells amazing and lasts for weeks."},
    ]))

    async def fake_think(need, system, messages, max_tokens=2000):
        return Completion(text="{}", provider="t", model="t")
    monkeypatch.setattr(build, "think", fake_think)

    result = await build.build_site(
        2, kind="store", product_photo=photo,
        product_description="a soy candle", contact_email="hi@adascandles.example",
        reviews_path=str(reviews_file))

    assert result.ok
    slug = build.slugify("Ada's Candles", 2)
    html = (tmp_path / "sites" / slug / "index.html").read_text()
    assert "Smells amazing and lasts for weeks." in html
    assert "Priya" in html
    assert "on their way" not in html   # the placeholder is gone


async def test_build_site_rollback_restores_the_leads_previous_status(monkeypatch, tmp_path):
    """M-7: a build against a lead already further along than "audited"
    (say, "contacted") must not leave it stuck at "mocked" once the build
    is rolled back."""
    site_dir = tmp_path / "sites" / "joes-1"
    site_dir.mkdir(parents=True)
    (site_dir / "index.html").write_text("x")
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "sites")

    restored = []

    async def fake_status(lead_id, status):
        restored.append((lead_id, status))
    monkeypatch.setattr(_store, "set_lead_status", fake_status)

    deleted = []

    async def fake_delete(slug):
        deleted.append(slug)
    monkeypatch.setattr(_store, "delete_site", fake_delete)

    token = json.dumps({"slug": "joes-1", "created": True, "backup_dir": None,
                        "lead_id": 1, "previous_status": "contacted"})
    result = await build.build_site_rollback(token)
    assert result.ok
    assert deleted == ["joes-1"]
    assert restored == [(1, "contacted")]


async def test_build_site_sets_mocked_but_rollback_restores_the_prior_status(
        monkeypatch, tmp_path):
    """The same M-7 guarantee end-to-end: a lead that was "contacted"
    before a store build goes to "mocked" during the build, and back to
    "contacted" once that build is rolled back."""
    monkeypatch.setenv("AGENCY_GETLAYERS_LICENCE_OK", "true")
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "sites")
    lead = {"id": 9, "name": "Old Lead", "phone": "555-9999", "status": "contacted"}
    monkeypatch.setattr(_store, "get_lead", _async_returns(lead))
    monkeypatch.setattr(_store, "get_site", _async_returns(None))
    monkeypatch.setattr(_store, "insert_site", _async_returns(20))
    monkeypatch.setattr(build, "McpClient", _FakeMcpClient)

    statuses = []

    async def fake_set_status(lead_id, status):
        statuses.append((lead_id, status))
    monkeypatch.setattr(_store, "set_lead_status", fake_set_status)

    photo = _upload(monkeypatch, tmp_path, 9, "mug.jpg")

    async def fake_think(need, system, messages, max_tokens=2000):
        return Completion(text="{}", provider="t", model="t")
    monkeypatch.setattr(build, "think", fake_think)

    build_result = await build.build_site(
        9, kind="store", product_photo=photo, product_description="a mug")
    assert build_result.ok
    assert statuses == [(9, "mocked")]

    deleted = []

    async def fake_delete(slug):
        deleted.append(slug)
    monkeypatch.setattr(_store, "delete_site", fake_delete)

    rollback_result = await build.build_site_rollback(build_result.rollback_token)
    assert rollback_result.ok
    assert statuses == [(9, "mocked"), (9, "contacted")]


# ------------------------------------------------------------------ deploy

def test_rsync_argv_scopes_delete_to_the_release_dir_only():
    argv = deploy.rsync_argv("/local/sites/joes", "deployhost", "/var/www/clients", "joes")
    assert argv[0] == "rsync"
    assert "--delete" in argv
    source, destination = argv[-2], argv[-1]
    assert source == "/local/sites/joes/"
    assert destination == "deployhost:/var/www/clients/joes/"
    assert destination not in ("deployhost:/var/www/clients/", "deployhost:/var/www/clients")


def test_rsync_argv_normalises_a_trailing_slash_on_the_remote_root():
    argv = deploy.rsync_argv("/local/sites/joes/", "host", "/var/www/clients/", "joes")
    assert argv[-1] == "host:/var/www/clients/joes/"


def _launch_ready_site(monkeypatch, site_dir):
    """A built site with no launch-check ISSUE (2026-09-29: deploy_dry and
    deploy_site refuse anything else), made the way build_site makes one."""
    from atlas.modules.agency.client import ClientData
    from atlas.modules.agency.launch import finish_site
    monkeypatch.delenv("POSTHOG_PUBLIC_KEY", raising=False)
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "index.html").write_text(
        "<!doctype html><html><head><title>Joe's Plumbing</title></head><body>"
        "<section id=\"hero\"><h1>Pipes, fixed</h1></section></body></html>")
    finish_site(site_dir, ClientData(name="Joe's Plumbing", phone="555-0100",
                                     address="1 Main St, Astoria"),
                analytics=None, form_action=None)
    return site_dir


async def test_deploy_dry_previews_the_rsync_command(monkeypatch, tmp_path):
    site_dir = _launch_ready_site(monkeypatch, tmp_path / "sites" / "joes")
    site = {"slug": "joes", "path": str(site_dir)}
    monkeypatch.setattr(_store, "get_site", _async_returns(site))
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", "deployhost")
    monkeypatch.setenv("AGENCY_DEPLOY_ROOT", "/var/www/clients")

    result = await deploy.deploy_dry("joes")
    assert result.ok
    assert "rsync" in result.detail
    assert "joes/" in result.detail


async def test_deploy_dry_lists_the_local_files_with_sizes(monkeypatch, tmp_path):
    """I-3: the deploy preview is the one place a human sees what is about
    to be published -- it must show what is actually in the directory,
    not just the rsync command line."""
    site_dir = _launch_ready_site(monkeypatch, tmp_path / "sites" / "joes")
    (site_dir / "sub").mkdir(parents=True)
    (site_dir / "sub" / "product.jpg").write_bytes(b"12345")
    index_size = (site_dir / "index.html").stat().st_size

    site = {"slug": "joes", "path": str(site_dir)}
    monkeypatch.setattr(_store, "get_site", _async_returns(site))
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", "deployhost")
    monkeypatch.setenv("AGENCY_DEPLOY_ROOT", "/var/www/clients")

    result = await deploy.deploy_dry("joes")
    assert result.ok
    assert f"index.html ({index_size} bytes)" in result.detail
    assert "sub/product.jpg (5 bytes)" in result.detail


async def test_deploy_dry_refuses_an_unknown_slug(monkeypatch):
    monkeypatch.setattr(_store, "get_site", _async_returns(None))
    result = await deploy.deploy_dry("nope")
    assert not result.ok


async def test_deploy_site_runs_the_release_snapshot_through_a_thread(monkeypatch, tmp_path):
    """The pre/post-deploy `shutil.copytree`/`rmtree` bookkeeping is
    blocking; it must go through `asyncio.to_thread`, not the event loop."""
    local_dir = _launch_ready_site(monkeypatch, tmp_path / "site")
    monkeypatch.setattr(_store, "get_site",
                        _async_returns({"slug": "joes", "path": str(local_dir)}))
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", "deployhost")
    monkeypatch.setenv("AGENCY_DEPLOY_ROOT", "/var/www/clients")
    monkeypatch.setattr(deploy, "RELEASES_DIR", tmp_path / "releases")
    monkeypatch.setattr(deploy, "_run", _async_returns((True, "")))
    monkeypatch.setattr(_store, "set_site_deployed", _async_returns(None))

    calls = []
    real_to_thread = deploy.asyncio.to_thread

    async def recording_to_thread(func, *args, **kwargs):
        calls.append(func)
        return await real_to_thread(func, *args, **kwargs)
    monkeypatch.setattr(deploy.asyncio, "to_thread", recording_to_thread)

    result = await deploy.deploy_site("joes")
    assert result.ok
    assert deploy._record_current_release in calls


async def test_deploy_site_refuses_a_remote_host_without_a_root(monkeypatch):
    """An unset AGENCY_DEPLOY_HOST now means the local Mapfront target
    (tests/test_mapfront_hosting.py); a remote host still needs its root."""
    monkeypatch.setattr(_store, "get_site",
                        _async_returns({"slug": "joes", "path": "/local/sites/joes"}))
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", "deployhost")
    monkeypatch.delenv("AGENCY_DEPLOY_ROOT", raising=False)
    result = await deploy.deploy_site("joes")
    assert not result.ok and "AGENCY_DEPLOY_ROOT" in result.detail


async def test_deploy_rollback_with_no_previous_release_is_a_noop(monkeypatch):
    result = await deploy.deploy_rollback("")
    assert result.ok
    assert "nothing" in result.detail.lower()


# ---------------------------------------------------------- draft validation

async def test_draft_outreach_refuses_an_unknown_kind():
    result = await outreach.draft_outreach(1, "carrier_pigeon")
    assert not result.ok
    assert "carrier_pigeon" in result.detail


async def test_draft_outreach_dry_refuses_an_unknown_kind():
    result = await outreach.draft_outreach_dry(1, "carrier_pigeon")
    assert not result.ok


# -------------------------------------------------------------------- inbox

async def test_inbox_surfaces_replied_and_bounced_outreach(monkeypatch):
    rows = [
        {"id": 10, "lead_id": 1, "lead_name": "Joe's Plumbing", "status": "replied",
         "subject": "About your website", "at": None},
        {"id": 11, "lead_id": 2, "lead_name": "Ace Auto", "status": "bounced",
         "subject": "About your website", "at": None},
    ]
    monkeypatch.setattr(_store, "list_outreach_needing_attention", _async_returns(rows))

    items = await agency.MODULE.inbox(limit=10)
    assert len(items) == 2
    replied, bounced = items
    assert replied.urgency is Severity.HIGH
    assert replied.reference == "10"
    assert "replied" in replied.why
    assert bounced.urgency is Severity.NORMAL
    assert bounced.reference == "11"
    assert "bounced" in bounced.why


async def test_inbox_raises_on_a_store_failure(monkeypatch):
    """Unreadable is not empty: the brief names this inbox instead of saying
    nothing is waiting."""
    async def boom(limit):
        raise RuntimeError("db unreachable")
    monkeypatch.setattr(_store, "list_outreach_needing_attention", boom)

    with pytest.raises(RuntimeError):
        await agency.MODULE.inbox(limit=10)


# ----------------------------------------------------------- site checks

async def test_site_checks_do_not_leak_state_between_slugs(monkeypatch):
    """A bare closure over a loop variable would make every `_make_site_check`
    probe read back whatever `slug`/`domain` the LAST iteration held. Two
    distinct sites, both probed, each asserting on its OWN name and domain,
    is what catches that regression — a single-site test would pass either
    way."""
    monkeypatch.setenv("AGENCY_SITES", "joes-1:joes.example,acme-2:acme.example")
    mod = agency.AgencyModule()          # not agency.MODULE: never mutate the
    await mod.setup()                   # process-wide singleton from a test

    checks = {c.name: c for c in mod.checks()}
    assert set(checks) >= {"site_joes-1", "site_acme-2"}
    for name in ("site_joes-1", "site_acme-2"):
        assert checks[name].interval == timedelta(minutes=30)
        assert checks[name].baseline_sigma is None

    def fake_tls(domain):
        return 42, None
    monkeypatch.setattr(agency, "_tls_days_left", fake_tls)

    class _FakeHeadResponse:
        status_code = 200

    async def fake_head(self, url):
        return _FakeHeadResponse()
    monkeypatch.setattr(httpx.AsyncClient, "head", fake_head)

    joes_obs = await checks["site_joes-1"].run()
    acme_obs = await checks["site_acme-2"].run()

    assert joes_obs.check == "site_joes-1"
    assert "joes.example" in joes_obs.detail
    assert acme_obs.check == "site_acme-2"
    assert "acme.example" in acme_obs.detail
    assert joes_obs.detail != acme_obs.detail


async def test_site_check_is_unknown_with_no_domain_on_record():
    mod = agency.AgencyModule()
    mod._sites = [("no-domain-1", None)]
    check = mod.checks()[-1]
    obs = await check.run()
    assert obs.health is Health.UNKNOWN
    assert "no-domain-1" in obs.detail


async def test_site_check_is_degraded_close_to_cert_expiry(monkeypatch):
    mod = agency.AgencyModule()
    mod._sites = [("joes-1", "joes.example")]
    check = mod.checks()[-1]

    monkeypatch.setattr(agency, "_tls_days_left", lambda domain: (10, None))

    class _FakeHeadResponse:
        status_code = 200

    async def fake_head(self, url):
        return _FakeHeadResponse()
    monkeypatch.setattr(httpx.AsyncClient, "head", fake_head)

    obs = await check.run()
    assert obs.health is Health.DEGRADED
    assert obs.value == 10.0


# ------------------------------------------------- build: store variants (Phase 20)
# Variants are proposals, not a traffic-splitting engine: up to five
# deliberately different store pages, one Style between them, each scored by
# `kernel/review.py`'s page rubric; the best becomes the page that deploys and
# the others are attached beside it, never shipped.


class _StyledMcpClient(_FakeMcpClient):
    """A GetLayers stand-in whose search names a Style, and which records
    every call so a test can see what each variant asked for."""

    calls: ClassVar[list] = []

    async def call(self, name, arguments):
        type(self).calls.append((name, dict(arguments)))
        if name == "getlayers_search":
            return json.dumps({"results": [{"name": "warm ceramic"}],
                               "styleId": "sty_warm_01"}), None
        if name == "getlayers_compositions":
            return f"<composition for {arguments['query']}>", None
        return f"<materialized {arguments.get('styleId')}>", None


class _UnstyledMcpClient(_StyledMcpClient):
    """GetLayers answering `"styleId": null` -- no Style named at all."""

    async def call(self, name, arguments):
        if name == "getlayers_search":
            type(self).calls.append((name, dict(arguments)))
            return json.dumps({"results": [], "styleId": None}), None
        return await super().call(name, arguments)


def _store_lead(monkeypatch, tmp_path, lead_id=11, name="Kiln & Co"):
    monkeypatch.setenv("AGENCY_GETLAYERS_LICENCE_OK", "true")
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "sites")
    lead = {"id": lead_id, "name": name, "category": "ceramics", "phone": "555-2020",
            "status": "audited"}
    monkeypatch.setattr(_store, "get_lead", _async_returns(lead))
    monkeypatch.setattr(_store, "get_site", _async_returns(None))
    inserted = []

    async def fake_insert(*args, **kwargs):
        inserted.append((args, kwargs))
        return 31
    monkeypatch.setattr(_store, "insert_site", fake_insert)
    monkeypatch.setattr(_store, "set_lead_status", _async_returns(None))
    _StyledMcpClient.calls = []
    monkeypatch.setattr(build, "McpClient", _StyledMcpClient)
    return _upload(monkeypatch, tmp_path, lead_id, "vase.jpg"), inserted


def _angle_think(recorded=None, headline_for=None):
    """A `think` stand-in that writes a different headline per angle -- the
    angle is named in the system prompt each variant sends."""
    async def fake_think(need, system, messages, max_tokens=2000):
        angle = next(a for a in build.ANGLES if f"Angle: {a}" in system)
        if recorded is not None:
            recorded.append(angle)
        headline = (headline_for or {}).get(angle, f"A {angle} vase")
        return Completion(text=json.dumps({
            "headline": headline,
            "subheadline": "Thrown by hand in a small studio",
            "bundles": ["Two-vase set"],
            "upsells": ["Add a matching bowl"],
            "faq": [{"q": "Is it watertight?", "a": "Yes, glazed inside"}],
        }), provider="t", model="t")
    return fake_think


def test_variants_clamp_to_the_store_maximum():
    assert build.STORE_VARIANTS_MAX == 5
    assert build.clamp_variants(7) == (5, None)
    assert build.clamp_variants(0) == (1, None)
    assert build.clamp_variants("3") == (3, None)
    n, reason = build.clamp_variants("many")
    assert n is None and "variants" in reason
    n, reason = build.clamp_variants(True)
    assert n is None


async def test_build_site_store_variants_7_builds_five_ranked_versions(
        monkeypatch, tmp_path):
    photo, inserted = _store_lead(monkeypatch, tmp_path)
    angles_seen = []
    monkeypatch.setattr(build, "think", _angle_think(angles_seen))

    result = await build.build_site(
        11, kind="store", product_photo=photo, product_description="a stoneware vase",
        variants=7)

    assert result.ok, result.detail
    record = result.evidence["variants"]
    assert len(record["variants"]) == 5
    assert sorted(angles_seen) == sorted(build.ANGLES)       # every angle, once
    assert [v["rank"] for v in record["variants"]] == [1, 2, 3, 4, 5]
    assert record["primary"] == record["variants"][0]["angle"]
    # The record travels to agency_sites.variants on the insert.
    assert inserted and inserted[0][1]["variants"] == record


async def test_build_site_store_variants_share_one_style_and_differ_in_composition(
        monkeypatch, tmp_path):
    photo, _ = _store_lead(monkeypatch, tmp_path)
    monkeypatch.setattr(build, "think", _angle_think())

    result = await build.build_site(
        11, kind="store", product_photo=photo, product_description="a stoneware vase",
        variants=3)

    assert result.ok, result.detail
    calls = _StyledMcpClient.calls
    materialize = [args for name, args in calls if name == "getlayers_materialize"]
    compositions = [args for name, args in calls if name == "getlayers_compositions"]
    assert len(materialize) == 3
    assert {m.get("styleId") for m in materialize} == {"sty_warm_01"}
    assert len({c["query"] for c in compositions}) == 3      # a composition per variant
    assert sum(1 for name, _ in calls if name == "getlayers_search") == 1
    record = result.evidence["variants"]
    assert record["style_id"] == "sty_warm_01"
    assert {v["style_id"] for v in record["variants"]} == {"sty_warm_01"}


async def test_rubric_rejects_a_payment_form_on_a_store_page():
    page = ('<!doctype html><html><head><title>Vase</title></head><body>'
            '<section id="hero"><img src="product.jpg" alt="Vase"><h1>A vase</h1>'
            '<p>Thrown by hand</p></section>'
            '<form action="/pay"><label>Card number</label><input name="card">'
            '<button>Pay</button></form></body></html>')
    scored = review.score_page(page, store=True)
    assert scored.rejected
    assert "no payment form" in scored.failed

    # The same words escaped into text are not a form.
    escaped = page.replace("<form", "&lt;form").replace("</form>", "&lt;/form&gt;")
    escaped = escaped.replace('<input name="card">', "")
    assert "no payment form" not in review.score_page(escaped, store=True).failed


async def test_rubric_rejects_a_reviews_block_without_a_client_file():
    fabricated = ('<html><head><title>Vase</title></head><body>'
                  '<section id="reviews"><h2>Reviews</h2>'
                  '<blockquote>Best vase I ever bought!<footer>— Sam</footer>'
                  '</blockquote></section></body></html>')
    scored = review.score_page(fabricated, store=True, client_reviews=None)
    assert scored.rejected
    assert "no fabricated reviews" in scored.failed

    stars = ('<html><body><section id="reviews"><p>★★★★★ 4.9/5</p></section>'
             '</body></html>')
    assert "no fabricated reviews" in review.score_page(stars, store=True).failed

    # The same block from a file the client supplied is theirs, not invented.
    supplied = review.score_page(fabricated, store=True,
                                 client_reviews=["Best vase I ever bought!"])
    assert "no fabricated reviews" not in supplied.failed

    # And the neutral placeholder a page carries until then is not a review.
    placeholder = ('<html><body><section id="reviews"><h2>Reviews</h2>'
                   '<p>Reviews are on their way — check back soon.</p></section>'
                   '</body></html>')
    assert "no fabricated reviews" not in review.score_page(placeholder, store=True).failed


async def test_a_variant_the_rubric_rejects_is_never_the_primary(monkeypatch, tmp_path):
    photo, _ = _store_lead(monkeypatch, tmp_path)
    monkeypatch.setattr(build, "think", _angle_think(headline_for={
        "benefit": "BENEFIT-VERSION"}))
    real_render = build._render_store_html

    def render_with_a_testimonial(lead, copy, reviews, photo_name, how_to_order):
        html = real_render(lead, copy, reviews, photo_name, how_to_order)
        if copy["headline"] == "BENEFIT-VERSION":
            html = html.replace(
                '<section id="faq">',
                "<blockquote>Changed my life<footer>— Ann</footer></blockquote>"
                '<section id="faq">')
        return html
    monkeypatch.setattr(build, "_render_store_html", render_with_a_testimonial)

    result = await build.build_site(
        11, kind="store", product_photo=photo, product_description="a stoneware vase",
        variants=2)

    assert result.ok, result.detail
    record = result.evidence["variants"]
    assert record["primary"] == "proof"
    benefit = next(v for v in record["variants"] if v["angle"] == "benefit")
    assert benefit["rejected"] is True
    assert "no fabricated reviews" in benefit["failed"]
    slug = build.slugify("Kiln & Co", 11)
    html = (tmp_path / "sites" / slug / "index.html").read_text()
    assert "Changed my life" not in html


async def test_build_site_store_refuses_when_every_variant_is_rejected(
        monkeypatch, tmp_path):
    photo, inserted = _store_lead(monkeypatch, tmp_path)
    monkeypatch.setattr(build, "think", _angle_think())
    real_render = build._render_store_html

    def render_with_a_card_form(*args):
        return real_render(*args).replace(
            "</body>", '<form action="/pay"><input name="card"></form></body>')
    monkeypatch.setattr(build, "_render_store_html", render_with_a_card_form)

    result = await build.build_site(
        11, kind="store", product_photo=photo, product_description="a stoneware vase",
        variants=2)

    assert not result.ok
    assert "rubric" in result.detail
    assert inserted == []
    assert not (tmp_path / "sites" / build.slugify("Kiln & Co", 11)).exists()


async def test_build_site_store_product_photo_lands_in_the_hero(monkeypatch, tmp_path):
    photo, _ = _store_lead(monkeypatch, tmp_path)
    monkeypatch.setattr(build, "think", _angle_think())

    result = await build.build_site(
        11, kind="store", product_photo=photo, product_description="a stoneware vase")

    assert result.ok, result.detail
    site_dir = tmp_path / "sites" / build.slugify("Kiln & Co", 11)
    html = (site_dir / "index.html").read_text()
    hero = html[html.index('<section id="hero">'):]
    hero = hero[:hero.index("</section>")]
    assert '<img src="product.jpg"' in hero
    assert (site_dir / "product.jpg").read_bytes() == b"fake-image-bytes"


async def test_store_alternates_live_beside_the_site_never_inside_it(
        monkeypatch, tmp_path):
    """`deploy.py` rsyncs the whole site directory with --delete: an
    alternate stored inside it would go public on the client's domain."""
    photo, _ = _store_lead(monkeypatch, tmp_path)
    monkeypatch.setattr(build, "think", _angle_think())

    result = await build.build_site(
        11, kind="store", product_photo=photo, product_description="a stoneware vase",
        variants=3)
    assert result.ok, result.detail

    slug = build.slugify("Kiln & Co", 11)
    site_dir = tmp_path / "sites" / slug
    # The launch pages (robots.txt, privacy, terms, contact, custom 404, the
    # share image) ship inside the site; the alternates never do, and
    # neither do the launch report and its provenance record.
    launch = {"robots.txt", "privacy.html", "terms.html", "contact.html", "404.html",
              "sitemap.xml", "og-image.png"}
    assert sorted(p.name for p in site_dir.iterdir() if p.name not in launch) == [
        "index.html", "product.jpg"]
    record = result.evidence["variants"]
    alternates = [v for v in record["variants"] if v["rank"] != 1]
    assert len(alternates) == 2
    for v in alternates:
        assert v["path"].startswith(str(tmp_path / "sites" / f".{slug}.variants"))
        assert (tmp_path / "sites" / f".{slug}.variants" / f"{v['angle']}.html").exists()

    # The deploy preview names the attached alternates and says they stay home.
    site = {"slug": slug, "path": str(site_dir), "variants": json.dumps(record)}
    monkeypatch.setattr(_store, "get_site", _async_returns(site))
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", "deployhost")
    monkeypatch.setenv("AGENCY_DEPLOY_ROOT", "/var/www/clients")
    preview = await deploy.deploy_dry(slug)
    assert preview.ok
    assert f"primary: {record['primary']}" in preview.detail
    assert "not deployed" in preview.detail
    for v in alternates:
        assert v["angle"] in preview.detail
    assert ".variants" not in preview.detail.split("files:")[1]


async def test_build_site_store_variants_rollback_removes_the_alternates(
        monkeypatch, tmp_path):
    photo, _ = _store_lead(monkeypatch, tmp_path)
    monkeypatch.setattr(build, "think", _angle_think())
    monkeypatch.setattr(_store, "delete_site", _async_returns(None))

    result = await build.build_site(
        11, kind="store", product_photo=photo, product_description="a stoneware vase",
        variants=3)
    assert result.ok, result.detail
    slug = build.slugify("Kiln & Co", 11)
    variants_dir = tmp_path / "sites" / f".{slug}.variants"
    assert variants_dir.exists()

    undone = await build.build_site_rollback(result.rollback_token)
    assert undone.ok
    assert not variants_dir.exists()
    assert not (tmp_path / "sites" / slug).exists()


async def test_build_site_store_rebuild_rollback_restores_the_previous_variants(
        monkeypatch, tmp_path):
    photo, _ = _store_lead(monkeypatch, tmp_path)
    monkeypatch.setattr(build, "think", _angle_think())
    slug = build.slugify("Kiln & Co", 11)
    old_record = {"primary": "story", "variants": []}
    old_alt = tmp_path / "sites" / f".{slug}.variants" / "story.html"
    old_alt.parent.mkdir(parents=True)
    old_alt.write_text("old alternate")
    (tmp_path / "sites" / slug).mkdir(parents=True)
    (tmp_path / "sites" / slug / "index.html").write_text("old primary")
    monkeypatch.setattr(_store, "get_site",
                        _async_returns({"slug": slug, "variants": old_record}))
    written = []

    async def fake_set_variants(s, record):
        written.append((s, record))
    monkeypatch.setattr(_store, "set_site_variants", fake_set_variants)

    result = await build.build_site(
        11, kind="store", product_photo=photo, product_description="a stoneware vase",
        variants=2)
    assert result.ok, result.detail
    assert written[-1][1] == result.evidence["variants"]
    assert not old_alt.exists()

    undone = await build.build_site_rollback(result.rollback_token)
    assert undone.ok
    assert written[-1] == (slug, old_record)
    assert old_alt.read_text() == "old alternate"
    assert (tmp_path / "sites" / slug / "index.html").read_text() == "old primary"


async def test_build_site_refuses_variants_for_a_brochure(monkeypatch):
    monkeypatch.setattr(_store, "get_lead", _async_returns({"id": 1, "name": "Joe's"}))
    result = await build.build_site(1, variants=3)
    assert not result.ok
    assert "store" in result.detail
    dry = await build.build_site_dry(1, variants=3)
    assert not dry.ok


async def test_build_site_dry_and_run_refuse_a_non_numeric_variants_alike(
        monkeypatch, tmp_path):
    photo, _ = _store_lead(monkeypatch, tmp_path)
    kwargs = {"kind": "store", "product_photo": photo,
              "product_description": "a stoneware vase", "variants": "lots"}
    dry = await build.build_site_dry(11, **kwargs)
    run = await build.build_site(11, **kwargs)
    assert not dry.ok and not run.ok
    assert dry.detail == run.detail


async def test_build_site_dry_names_the_clamped_variant_count(monkeypatch, tmp_path):
    photo, _ = _store_lead(monkeypatch, tmp_path)
    dry = await build.build_site_dry(
        11, kind="store", product_photo=photo, product_description="a stoneware vase",
        variants=9)
    assert dry.ok
    assert "5 variants" in dry.detail


def test_build_site_action_declares_the_variants_param():
    action = _action("agency.build_site")
    assert "variants" in action.params


async def test_build_site_store_sends_no_style_when_getlayers_names_none(
        monkeypatch, tmp_path):
    """`"styleId": null` is no Style, not a Style called "null": nothing is
    invented and sent on to GetLayers."""
    photo, _ = _store_lead(monkeypatch, tmp_path)
    monkeypatch.setattr(build, "McpClient", _UnstyledMcpClient)
    monkeypatch.setattr(build, "think", _angle_think())

    result = await build.build_site(
        11, kind="store", product_photo=photo, product_description="a stoneware vase",
        variants=2)

    assert result.ok, result.detail
    materialize = [args for name, args in _StyledMcpClient.calls
                   if name == "getlayers_materialize"]
    assert len(materialize) == 2
    assert all("styleId" not in m for m in materialize)
    assert result.evidence["variants"]["style_id"] is None
