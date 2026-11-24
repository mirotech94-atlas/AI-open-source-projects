"""OK-on-empty for the mail checks (BUILD_ORDER 21.E.1, RA-E1)."""
from __future__ import annotations

from atlas.kernel.contracts import Health
from atlas.modules import mail
from atlas.modules.mail import followups


def _async_return(value):
    async def _f(*a, **k):
        return value
    return _f


async def test_followups_reads_threads_in_full_so_the_ask_test_sees_a_body(
        monkeypatch):
    """`metadata` threads carry no body, so every thread read as 'not an ask'
    and the check said zero forever."""
    formats = []

    async def fake_api(path, **params):
        if path == "/threads":
            return {"threads": [{"id": "t1"}]}
        formats.append(params.get("format"))
        return {"id": "t1", "messages": []}

    monkeypatch.setattr(mail, "_api", fake_api)
    monkeypatch.setenv("GOOGLE_ACCOUNT_EMAIL", "me@example.com")
    monkeypatch.setattr(followups, "open_followups", _async_return([]))
    await followups.check_followups()
    assert formats == ["full"]


async def test_followups_with_a_shapeless_thread_list_is_unknown(monkeypatch):
    async def fake_api(path, **params):
        return {"error": {"message": "backend error"}}

    monkeypatch.setattr(mail, "_api", fake_api)
    monkeypatch.setenv("GOOGLE_ACCOUNT_EMAIL", "me@example.com")
    obs = await followups.check_followups()
    assert obs.health is Health.UNKNOWN


async def test_followups_with_an_honest_empty_list_is_still_ok(monkeypatch):
    """Positive control: Gmail's own empty answer is a real zero."""
    async def fake_api(path, **params):
        return {"resultSizeEstimate": 0}

    monkeypatch.setattr(mail, "_api", fake_api)
    monkeypatch.setenv("GOOGLE_ACCOUNT_EMAIL", "me@example.com")
    monkeypatch.setattr(followups, "open_followups", _async_return([]))
    obs = await followups.check_followups()
    assert obs.health is Health.OK


async def test_watch_active_with_no_mailbox_fields_is_unknown_not_zero(monkeypatch):
    monkeypatch.setattr(mail, "_api", _async_return({}))
    obs = await mail.check_watch_active()
    assert obs.health is Health.UNKNOWN
    assert obs.value is None


async def test_watch_active_with_a_real_profile_is_ok(monkeypatch):
    """Positive control, and the detail says mail is polled, not pushed."""
    monkeypatch.setattr(
        mail, "_api",
        _async_return({"emailAddress": "me@example.com", "messagesTotal": 1200}))
    obs = await mail.check_watch_active()
    assert obs.health is Health.OK
    assert obs.value == 1200.0
    assert "polled" in obs.detail
