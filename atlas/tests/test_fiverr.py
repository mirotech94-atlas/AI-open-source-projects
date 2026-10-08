"""Fiverr — the one module that reads a page rather than an API.

Fiverr has no public API, so ATLAS drives a browser session a human signed
in. That makes one failure mode dominant and expensive: the session expires,
Fiverr answers 200 with a login page, the selectors match nothing, and every
count comes back zero.

Zero unread messages is the answer that costs money. Response time drives
gig ranking, so "inbox clear" while five buyers wait is worse than an error,
and it stays wrong for as long as the session stays dead.
"""
from __future__ import annotations

import pytest

from atlas.kernel.contracts import Health
from atlas.modules import fiverr


class FakePage:
    """A page that is where it says it is and is not the bot wall."""

    def __init__(self, url: str):
        self.url = url

    async def title(self):
        return "Fiverr"

    async def content(self):
        return "<html><body>a real page</body></html>"


@pytest.mark.parametrize("url, expected", [
    ("https://www.fiverr.com/login", "/inbox"),
    ("https://www.fiverr.com/sign_in?next=/inbox", "/inbox"),
    ("https://www.fiverr.com/", "/inbox"),
    ("https://www.fiverr.com/login", "/manage_orders"),
])
async def test_a_login_page_is_not_data(url, expected):
    """Landing anywhere other than the page we asked for means the session
    is gone and nothing on the page can be read as a measurement."""
    with pytest.raises(fiverr.SessionExpired) as caught:
        await fiverr._landed(FakePage(url), expected)
    assert "session has expired" in str(caught.value)
    assert "fiverr-login.sh" in str(caught.value)


async def test_the_right_page_passes():
    await fiverr._landed(FakePage("https://www.fiverr.com/inbox"), "/inbox")


async def test_every_page_check_confirms_where_it_landed():
    """The guard is only worth having if every check uses it. check_session
    had its own version; the other three read zeros off a login page."""
    import ast
    import inspect

    source = inspect.getsource(fiverr)
    tree = ast.parse(source)
    reading = {"check_unanswered_messages", "check_active_orders",
               "check_gig_stats"}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name in reading:
            calls = {getattr(n.func, "id", "") for n in ast.walk(node)
                     if isinstance(n, ast.Call)}
            assert "_landed" in calls, (
                f"{node.name} reads the page without confirming it is the "
                f"right page")


async def test_an_expired_session_is_failing_not_unknown(monkeypatch):
    """UNKNOWN says "I could not measure". This is measurable and measured:
    the session is dead and he has to log in again. That is a finding."""
    async def fake_browser():
        raise AssertionError("should not get this far")

    import inspect
    source = inspect.getsource(fiverr)
    # Each reading check maps SessionExpired to FAILING, and everything else
    # to UNKNOWN with a redacted reason.
    assert source.count("except SessionExpired as exc:") == 3
    assert "Health.FAILING, detail=str(exc)" in source


async def test_a_missing_session_file_names_the_helper(monkeypatch, tmp_path):
    """The first run, before he has ever logged in."""
    monkeypatch.setattr(fiverr, "STATE", tmp_path / "absent.json")
    obs = await fiverr.check_session()
    assert obs.health is Health.UNKNOWN
    assert "no saved Fiverr session" in obs.detail


def test_the_module_never_sends_a_message():
    """A bot reply to a buyer is a policy risk and a reputation risk on the
    thing his ranking depends on. Drafts only."""
    actions = {a.name for a in fiverr.MODULE.actions()}
    assert actions == {"fiverr.draft_buyer_reply"}


async def test_an_unreadable_analytics_page_is_not_zero_impressions(monkeypatch):
    """`impressions = 0.0` as a starting value meant a page whose layout had
    changed reported "0 impressions, OK" — a gig nobody can see, and a page
    ATLAS could not read, rendered identically. Impressions drive ranking,
    so that is the wrong way round."""
    import inspect

    source = inspect.getsource(fiverr.check_gig_stats)
    assert "impressions: float | None = None" in source
    assert "Health.UNKNOWN" in source
    assert "unmeasured rather than zero" in source


async def test_zero_impressions_is_a_finding_not_health():
    import inspect

    source = inspect.getsource(fiverr.check_gig_stats)
    assert "Health.DEGRADED if impressions == 0" in source


async def test_a_bot_wall_is_unknown_never_zero(monkeypatch):
    """PerimeterX serves its challenge AT the requested URL with a 200. The
    URL check passes, the selectors match nothing, and every count is zero.
    Read as health that is "inbox clear" on a page that was never Fiverr."""
    class WallPage:
        url = "https://www.fiverr.com/inbox"
        async def title(self): return "It needs a human touch"
        async def content(self): return "<div id='px-captcha'></div>"

    with pytest.raises(fiverr.BotWall) as caught:
        await fiverr._landed(WallPage(), "/inbox")
    assert "PerimeterX" in str(caught.value)


async def test_every_reading_maps_the_wall_to_unknown():
    import inspect

    source = inspect.getsource(fiverr)
    # session + three readers
    assert source.count("except BotWall as exc:") == 4
    assert 'Health.OK, detail="session valid"' in source


# ---------------------------------------------------------- wall backoff

async def test_after_the_wall_no_request_is_made_for_a_day(monkeypatch, tmp_path):
    """102 walled requests in 24 h from a datacentre IP is the pattern that
    flags a seller account. One hit, then quiet — across restarts."""
    import json
    from datetime import UTC, datetime, timedelta

    from atlas.kernel.contracts import Health
    from atlas.modules import fiverr

    wall = tmp_path / "wall.json"
    monkeypatch.setattr(fiverr, "WALL", wall)
    assert fiverr._backing_off() is None                  # no wall yet

    fiverr._record_wall()
    assert "not trying again" in fiverr._backing_off()

    async def boom():
        raise AssertionError("a browser was launched during the backoff")
    monkeypatch.setattr(fiverr, "_browser", boom)
    for check in (fiverr.check_session, fiverr.check_unanswered_messages,
                  fiverr.check_active_orders, fiverr.check_gig_stats):
        o = await check()
        assert o.health is Health.UNKNOWN and "bot wall" in o.detail

    old = (datetime.now(UTC) - timedelta(hours=25)).isoformat()
    wall.write_text(json.dumps({"at": old}))
    assert fiverr._backing_off() is None                  # a day later: try once
