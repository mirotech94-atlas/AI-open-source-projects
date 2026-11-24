"""messaging — Slack read-only.

What has to hold: the module only reads; unanswered Slack mentions are
counted, and a Slack that cannot be read is never a confident zero
(invariant 7). WhatsApp, the last platform `messaging.reply` could send on,
was removed at the owner's request on 2026-09-28, and the action went with it:
old WhatsApp and Telegram rows stay in `messaging_inbound`, but are never
read back, and nothing can reply on them.
"""
from __future__ import annotations

import ast
import importlib.util
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import intent as _intent
from atlas.kernel.contracts import Health
from atlas.modules import messaging
from atlas.modules.messaging import slack_read

ROOT = pathlib.Path(__file__).resolve().parent.parent / "atlas"
PKG = ROOT / "modules" / "messaging"

# Stands in for rows a live platform would return. There is none today; the
# counting below is still what a future one would go through.
LIVE = "chat"

REMOVED = ("whatsapp", "telegram")

# ------------------------------------------------------------ no executor

def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                parts = ["atlas", "modules", "messaging"][: 3 - (node.level - 1)]
                mod = ".".join(parts + ([mod] if mod else []))
            names.add(mod)
    return names


def test_no_file_in_the_module_imports_the_executor():
    files = list(PKG.glob("*.py"))
    assert files
    for f in files:
        for mod in _imports_of(f):
            assert "executor" not in mod, f"{f.name} imports {mod}"


# ------------------------------------------------------------- slack_read

async def test_unanswered_is_empty_without_configuration(monkeypatch):
    monkeypatch.delenv("MESSAGING_SLACK_CHANNELS", raising=False)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APPROVER_IDS", raising=False)
    assert await slack_read.unanswered() == []


async def test_unanswered_finds_a_mention_with_no_reply(monkeypatch):
    monkeypatch.setenv("MESSAGING_SLACK_CHANNELS", "C1")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "tok")
    monkeypatch.setenv("SLACK_APPROVER_IDS", "UOWNER")

    async def fake_get_json(url, **kw):
        if url.endswith("conversations.history"):
            return {"ok": True, "messages": [
                {"user": "UBUYER", "text": "hey <@UOWNER> any update?",
                 "ts": "100.1"}]}, None
        return {"ok": True, "messages": []}, None

    monkeypatch.setattr(slack_read, "get_json", fake_get_json)
    out = await slack_read.unanswered()
    assert len(out) == 1 and out[0]["sender"] == "UBUYER"


async def test_unanswered_skips_a_thread_owner_already_answered(monkeypatch):
    monkeypatch.setenv("MESSAGING_SLACK_CHANNELS", "C1")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "tok")
    monkeypatch.setenv("SLACK_APPROVER_IDS", "UOWNER")

    async def fake_get_json(url, **kw):
        if url.endswith("conversations.history"):
            return {"ok": True, "messages": [
                {"user": "UBUYER", "text": "hey <@UOWNER>",
                 "ts": "100.1", "thread_ts": "100.1", "reply_count": 1}]}, None
        if url.endswith("conversations.replies"):
            return {"ok": True, "messages": [
                {"user": "UBUYER", "ts": "100.1"},
                {"user": "UOWNER", "ts": "100.2"}]}, None
        return {"ok": False}, None

    monkeypatch.setattr(slack_read, "get_json", fake_get_json)
    assert await slack_read.unanswered() == []


async def test_unanswered_ignores_atlas_own_messages(monkeypatch):
    monkeypatch.setenv("MESSAGING_SLACK_CHANNELS", "C1")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "tok")
    monkeypatch.setenv("SLACK_APPROVER_IDS", "UOWNER")

    async def fake_get_json(url, **kw):
        return {"ok": True, "messages": [
            {"bot_id": "B1", "text": "<@UOWNER> approve this",
             "ts": "100.1", "user": "UATLAS"}]}, None

    monkeypatch.setattr(slack_read, "get_json", fake_get_json)
    assert await slack_read.unanswered() == []


# ------------------------------------------------- no reply action any more

def test_there_is_no_action():
    """messaging.reply went with WhatsApp: a Tier 2 name that could only
    ever refuse has no place in the catalog."""
    assert messaging.MODULE.actions() == ()
    assert not hasattr(messaging, "reply")
    assert not hasattr(messaging, "reply_dry")


def test_messaging_reply_is_not_offered_to_the_agents():
    from atlas.kernel.tools import TOOLS
    from atlas.tools.builtin import register_all
    register_all()
    assert "messaging_reply" not in TOOLS
    assert "messaging.reply" not in {t.action for t in TOOLS.values()}


async def test_a_reply_to_an_old_whatsapp_row_is_refused_at_the_executor(
        monkeypatch):
    """The one door refuses the name before anything is looked up or sent,
    approved or not."""
    from atlas.kernel import executor
    from atlas.kernel.registry import Registry

    reg = Registry()
    reg.load("atlas.modules.messaging", "messaging")
    monkeypatch.setattr(executor, "REGISTRY", reg)
    refused = []

    async def fake_audit(event, subject, detail=None, *a, **kw):
        refused.append((event, subject))

    monkeypatch.setattr(executor, "audit", fake_audit)
    result = await executor.execute(executor.ExecutionRequest(
        "messaging.reply", {"inbound_id": 1, "text": "hi"},
        "old whatsapp row", approved_by="slack:UOWNER"))
    assert result.ok is False
    assert result.detail == "no such action in the catalog"
    assert refused == [("execute.refused", "messaging.reply")]


async def test_unanswered_and_inbox_read_only_live_platforms(monkeypatch):
    """No platform writing to `messaging_inbound` is live: both reads filter
    to an empty list, so an old whatsapp or telegram row never surfaces."""
    seen = []

    async def fake_fetch_all(sql, *args):
        seen.append((sql, args))
        return []

    async def none(hours=24):
        return []

    monkeypatch.setattr(messaging, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(slack_read, "unanswered", none)
    await messaging.check_unanswered()
    await messaging.inbox()
    assert len(seen) == 2
    for sql, args in seen:
        assert "platform = ANY" in sql
        assert [] in args
    assert messaging._SENDABLE == ()


# ---------------------------------------------------------- check_unanswered

async def test_check_unanswered_is_ok_at_five_or_fewer(monkeypatch):
    async def fake_fetch_all(sql, *args):
        return [{"platform": LIVE}] * 5

    async def fake_unanswered(hours=24):
        return []

    monkeypatch.setattr(messaging, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(slack_read, "unanswered", fake_unanswered)
    obs = await messaging.check_unanswered()
    assert obs.health is Health.OK and obs.value == 5


async def test_check_unanswered_is_degraded_past_five(monkeypatch):
    async def fake_fetch_all(sql, *args):
        return [{"platform": LIVE}] * 6

    async def fake_unanswered(hours=24):
        return []

    monkeypatch.setattr(messaging, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(slack_read, "unanswered", fake_unanswered)
    obs = await messaging.check_unanswered()
    assert obs.health is Health.DEGRADED


async def test_check_unanswered_counts_slack_too(monkeypatch):
    async def fake_fetch_all(sql, *args):
        return []

    async def fake_unanswered(hours=24):
        return [{"chat_id": "C1"}] * 6

    monkeypatch.setattr(messaging, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(slack_read, "unanswered", fake_unanswered)
    obs = await messaging.check_unanswered()
    assert obs.value == 6 and obs.health is Health.DEGRADED


@pytest.mark.parametrize("reply", [
    ({"ok": False, "error": "channel_not_found"}, None),
    (None, "HTTP 500 from /api/conversations.history"),
])
async def test_a_slack_channel_that_cannot_be_read_raises(monkeypatch, reply):
    """It used to be skipped -- and a skipped channel is a zero."""
    monkeypatch.setenv("MESSAGING_SLACK_CHANNELS", "C1")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "tok")
    monkeypatch.setenv("SLACK_APPROVER_IDS", "UOWNER")

    async def fake_get_json(url, **kw):
        return reply

    monkeypatch.setattr(slack_read, "get_json", fake_get_json)
    with pytest.raises(slack_read.SlackUnreadable):
        await slack_read.unanswered()


async def test_a_failed_reply_lookup_is_not_counted_as_unreplied(monkeypatch):
    monkeypatch.setenv("MESSAGING_SLACK_CHANNELS", "C1")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "tok")
    monkeypatch.setenv("SLACK_APPROVER_IDS", "UOWNER")

    async def fake_get_json(url, **kw):
        if url.endswith("conversations.history"):
            return {"ok": True, "messages": [
                {"user": "UBUYER", "text": "hey <@UOWNER>", "ts": "100.1"}]}, None
        return None, "timed out"

    monkeypatch.setattr(slack_read, "get_json", fake_get_json)
    with pytest.raises(slack_read.SlackUnreadable):
        await slack_read.unanswered()


@pytest.mark.parametrize("rows", [0, 3])
async def test_check_unanswered_is_unknown_when_slack_cannot_be_read(
        monkeypatch, rows):
    async def fake_fetch_all(sql, *args):
        return [{"platform": LIVE}] * rows

    async def broken(hours=24):
        raise slack_read.SlackUnreadable("history of C1: channel_not_found")

    monkeypatch.setattr(messaging, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(slack_read, "unanswered", broken)
    obs = await messaging.check_unanswered()
    assert obs.health is Health.UNKNOWN and obs.value is None
    assert "Slack could not be read" in obs.detail
    assert f"at least {rows}" in obs.detail


async def test_a_high_floor_is_still_degraded_when_slack_is_down(monkeypatch):
    async def fake_fetch_all(sql, *args):
        return [{"platform": LIVE}] * 6

    async def broken(hours=24):
        raise slack_read.SlackUnreadable("history of C1: ratelimited")

    monkeypatch.setattr(messaging, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(slack_read, "unanswered", broken)
    obs = await messaging.check_unanswered()
    assert obs.health is Health.DEGRADED and "at least 6" in obs.detail


async def test_check_unanswered_is_unknown_when_the_query_fails(monkeypatch):
    async def fake_fetch_all(sql, *args):
        raise RuntimeError("db down")

    monkeypatch.setattr(messaging, "fetch_all", fake_fetch_all)
    obs = await messaging.check_unanswered()
    assert obs.health is Health.UNKNOWN


# -------------------------------------------------------------------- inbox

async def test_inbox_reports_sender_snippet_and_age(monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=3)

    async def fake_fetch_all(sql, *args):
        return [{"id": 5, "at": at, "platform": LIVE, "sender": "Ada",
                 "text": "x" * 100}]

    monkeypatch.setattr(messaging, "fetch_all", fake_fetch_all)
    items = await messaging.inbox(10)
    assert len(items) == 1
    item = items[0]
    assert item.sender == "Ada"
    assert item.subject == "x" * 80
    assert "unanswered 3h" in item.why
    assert item.reference == "5"


async def test_inbox_raises_when_the_query_fails(monkeypatch):
    """Unreadable is not empty: the brief names this inbox instead of saying
    nothing is waiting."""
    async def fake_fetch_all(sql, *args):
        raise RuntimeError("db down")

    monkeypatch.setattr(messaging, "fetch_all", fake_fetch_all)
    with pytest.raises(RuntimeError):
        await messaging.inbox(10)


async def test_module_inbox_method_delegates(monkeypatch):
    async def fake_fetch_all(sql, *args):
        return []

    monkeypatch.setattr(messaging, "fetch_all", fake_fetch_all)
    assert await messaging.MODULE.inbox(5) == ()


# -------------------------------------------------------------------- module

def test_requires_no_env_to_load():
    assert messaging.MODULE.requires_env == ()


def test_optional_env_lists_only_the_slack_variable():
    assert messaging.MODULE.optional_env == ("MESSAGING_SLACK_CHANNELS",)


def test_the_only_check_is_unanswered():
    assert [c.name for c in messaging.MODULE.checks()] == ["unanswered"]


def test_telegram_is_gone():
    """Removed at the owner's request, 2026-09-28: no check, no env var, no
    webhook route, and no module file left to import."""
    names = [c.name for c in messaging.MODULE.checks()]
    assert "telegram_bot" not in names
    assert not any("TELEGRAM" in v for v in messaging.MODULE.optional_env)
    assert not (PKG / "telegram.py").exists()
    from atlas.api.main import app
    assert not any("telegram" in getattr(r, "path", "") for r in app.routes)


def test_whatsapp_is_gone():
    """Removed at the owner's request, 2026-09-28: no token check, no env var,
    no webhook route or router, no module file, and no reply action."""
    names = [c.name for c in messaging.MODULE.checks()]
    assert "whatsapp_token" not in names
    assert not any("WHATSAPP" in v for v in messaging.MODULE.optional_env)
    assert not (PKG / "whatsapp.py").exists()
    assert importlib.util.find_spec("atlas.api.messaging_webhooks") is None
    assert not hasattr(messaging, "answer_principal")
    assert not hasattr(messaging, "is_owner")
    assert "messaging.reply" not in {a.name for a in messaging.MODULE.actions()}
    from atlas.api.main import app
    assert not any("whatsapp" in getattr(r, "path", "") for r in app.routes)


@pytest.mark.parametrize("platform", REMOVED)
def test_a_removed_platform_is_never_owner(monkeypatch, platform):
    """A leftover setting names nobody."""
    monkeypatch.setenv("OWNER_TELEGRAM_CHAT_ID", "555")
    monkeypatch.setenv("OWNER_WHATSAPP_NUMBER", "15550001111")
    chat = "555" if platform == "telegram" else "15550001111"
    assert _intent.principal_channel(platform, chat) is None


# ---------------------------------------------------------- voice labels

def test_voice_label():
    assert messaging.voice_label({}) is None
    assert messaging.voice_label(None) is None
    assert messaging.voice_label('{"voice": true, "transcribed": true}') == \
        "voice note (transcribed)"
    assert messaging.voice_label({"voice": True, "transcribed": False}) == \
        "voice note (not transcribed)"


async def test_inbox_labels_a_transcript(monkeypatch):
    at = datetime.now(UTC) - timedelta(hours=2)

    async def fake_fetch_all(sql, *args):
        return [{"id": 1, "at": at, "platform": LIVE, "sender": "sam",
                 "text": "call me back", "meta": {"voice": True,
                                                  "transcribed": True}},
                {"id": 2, "at": at, "platform": LIVE, "sender": "ali",
                 "text": "typed", "meta": {}}]

    monkeypatch.setattr(messaging, "fetch_all", fake_fetch_all)
    items = await messaging.inbox()
    assert items[0].why.startswith("voice note (transcribed)")
    assert "voice note" not in items[1].why


