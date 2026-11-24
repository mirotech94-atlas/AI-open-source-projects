"""Every outbound WhatsApp sender carries a category tag, an emoji and a warm tone,
and keeps its facts. The tag is assigned in code, never by a model."""
from __future__ import annotations

import json
import pathlib
import re
from datetime import UTC, datetime
from typing import ClassVar
from xml.sax.saxutils import unescape

import pytest

from atlas.api import whatsapp
from atlas.kernel import (
    agent_report,
    chat_commands,
    owner_calls,
    wa_approvals,
    wa_format,
)
from atlas.kernel.wa_format import TAGS, Category
from atlas.modules.tutorco import notify

SEP = " · "
AT = datetime(2026, 11, 12, 16, 7, tzinfo=UTC)          # 11:07 AM New York
EVENT = {"id": 3, "channel": 3, "kind": "vehicle", "at": AT}


def _has_emoji(text: str) -> bool:
    return any(ord(ch) > 0x2000 for ch in text)


def _starts_with(text: str, category: Category) -> bool:
    return text.startswith(TAGS[category] + SEP)


# ------------------------------------------------------------ sensors









# ------------------------------------------------------------ alerts, TUTORCO, cards



@pytest.mark.parametrize("kind", sorted(notify.KINDS))
def test_every_tutorco_owner_notice_is_tagged_tutorco_with_an_emoji(kind):
    text = notify.compose(kind, "ev123", card="C1", quote="hi https://x.y")
    assert _starts_with(text, Category.TUTORCO) and _has_emoji(notify.KINDS[kind])
    assert "Ref ev123." in text and "Slack card: C1." in text
    assert "Notify only: decide on the Slack card, not here." in text
    if kind in notify.QUOTED_KINDS:
        assert "https[:]//x[.]y" in text and "untrusted" in text.lower()


# ------------------------------------------------------------ the daily digest

async def test_every_digest_part_is_tagged_agency(monkeypatch):
    from atlas.kernel import whatsapp_daily

    async def read(now=None):
        return {"agents": [], "errors": {"the agents' records": "unreadable"}}
    monkeypatch.setattr(agent_report, "read", read)
    parts = await whatsapp_daily.digest(AT, limit=140)
    assert len(parts) > 1
    for part in parts:
        assert _starts_with(part, Category.AGENCY) and "Last 24 hours" in part
        assert len(part) <= 140
    assert any("couldn't" in p or "can't" in p for p in parts)       # UNKNOWN stays said


# ------------------------------------------------------------ chokepoints

class _FakeClient:
    posted: ClassVar[list[dict]] = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None, **k):
        _FakeClient.posted.append(dict(data or {}))

        class R:
            status_code = 201

            def json(self):
                return {"sid": "SM1"}
        return R()


@pytest.fixture
def twilio(monkeypatch):
    _FakeClient.posted = []
    monkeypatch.setattr(notify.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok")
    monkeypatch.setenv("TWILIO_FROM", "+15559990000")
    return _FakeClient


async def test_an_untagged_body_can_never_go_out_through_send_whatsapp(twilio):
    ok, _ = await notify.send_whatsapp("+15551230001", "plain text")
    assert ok
    assert _starts_with(twilio.posted[0]["Body"], Category.GENERAL)
    assert twilio.posted[0]["Body"].endswith("plain text")


async def test_a_tagged_body_is_sent_as_is(twilio):
    body = wa_format.tag(Category.MAIL, "\U0001F4E7 new mail")
    await notify.send_whatsapp("+15551230001", body)
    assert twilio.posted[0]["Body"] == body


async def test_the_card_template_message_is_tagged_too(twilio, monkeypatch):
    monkeypatch.setenv("WHATSAPP_CONTENT_SID_CARD", "HX" + "a" * 32)
    ok, _ = await notify.send_whatsapp_card("+15551230001", "card text", 5, "ABCDE")
    assert ok
    variables = json.loads(twilio.posted[0]["ContentVariables"])
    assert _starts_with(variables["1"], Category.APPROVALS)
    assert variables["2"] == "5" and variables["3"] == "ABCDE"


async def test_a_long_body_is_cut_to_the_whatsapp_limit(twilio):
    await notify.send_whatsapp("+15551230001", "x" * 4000)
    assert len(twilio.posted[0]["Body"]) <= 1600


def test_only_the_notify_module_posts_to_twilio_messages():
    """The single outbound WhatsApp write: no other file builds a Messages.json call
    except the SMS sender (a different channel)."""
    root = pathlib.Path(__file__).resolve().parent.parent / "atlas"
    users = {str(p.relative_to(root)) for p in root.rglob("*.py")
             if "Messages.json" in p.read_text(encoding="utf-8")}
    assert users == {"modules/tutorco/notify.py", "modules/agency/sms.py"}


# ------------------------------------------------------------ chat replies

def _bodies(response) -> list[str]:
    xml = response.body.decode()
    return [unescape(m) for m in re.findall(r"<Message>(.*?)</Message>", xml, re.DOTALL)]


def test_twiml_tags_every_message_and_defaults_to_general():
    wa_format.reset_reply_category()
    out = _bodies(whatsapp.twiml(["one", "two"]))
    assert all(_starts_with(m, Category.GENERAL) for m in out)
    out = _bodies(whatsapp.twiml(["x"], Category.MAIL))
    assert _starts_with(out[0], Category.MAIL)


def test_twiml_uses_the_category_the_answer_path_set():
    wa_format.set_reply_category(Category.TUTORCO)
    assert _starts_with(_bodies(whatsapp.twiml(["x"]))[0], Category.TUTORCO)
    wa_format.reset_reply_category()


@pytest.mark.parametrize("const", [
    "NEED_CODE", "WRONG_CODE", "USED_CODE", "RETIRED_CODE", "EXPIRED_CARD",
    "NOT_FROM_WHATSAPP", "NEVER_REPLY", "WATCH_ONLY_REPLY", "CANT_CHECK",
    "BAD_BUTTON", "NO_CARDS"])
def test_every_approval_reply_is_warm_and_keeps_its_meaning(const):
    text = getattr(wa_approvals, const)
    assert _has_emoji(text) and not text[0].isascii()
    assert "Nothing was changed" in text or "nothing was done" in text \
        or "Silence is never a yes" in text


def test_the_refusals_keep_their_meaning_with_a_friendlier_tone():
    for text in (whatsapp.CANT_DECIDE, whatsapp.CANT_ACT):
        assert _has_emoji(text) and "Nothing was changed." in text
        assert "approval card" in text
    assert "can't approve things from a chat message" in whatsapp.CANT_DECIDE
    assert "can't do that from a chat message" in whatsapp.CANT_ACT
    assert "Okay, stopped." in whatsapp.STOP_REPLY


@pytest.fixture
def quiet(monkeypatch):
    async def no_model(*_a, **_k):
        raise RuntimeError("no model")
    monkeypatch.setattr("atlas.kernel.llm.think", no_model)


async def test_a_refusal_to_approve_is_tagged_approvals():
    wa_format.reset_reply_category()
    assert await whatsapp.answer("approve everything") == whatsapp.CANT_DECIDE
    assert wa_format.reply_category() is Category.APPROVALS


async def test_a_greeting_is_general():
    wa_format.set_reply_category(Category.TUTORCO)
    await whatsapp.answer("hello")
    assert wa_format.reply_category() is Category.GENERAL


async def test_the_system_check_is_system_health(monkeypatch):
    async def build():
        return {"line": "Everything is fine as of 12:00."}
    monkeypatch.setattr("atlas.api.system_check.build", build)
    await whatsapp.answer("system check")
    assert wa_format.reply_category() is Category.SYSTEM


async def test_an_agent_report_takes_that_agents_tag(monkeypatch):
    async def report(only=None, details=False):
        return "report"
    monkeypatch.setattr(agent_report, "report", report)
    await whatsapp.answer("what did the team do")
    assert wa_format.reply_category() is Category.AGENCY
    await whatsapp.answer("what did Mark do today?")
    assert wa_format.reply_category() is Category.CONTENT


async def test_the_tutorco_agent_answers_under_the_tutorco_tag(quiet, monkeypatch):
    from atlas.kernel import missions
    monkeypatch.setattr(missions, "voice_agent", lambda q: "tutorco")
    await whatsapp._written_answer("how are the tutors doing")
    assert wa_format.reply_category() is Category.TUTORCO




@pytest.mark.parametrize("body,want", [
    ("call me", Category.SYSTEM),
    ("learn https://www.youtube.com/watch?v=abcdefghijk", Category.CONTENT),
    ("remember that I like tea", Category.GENERAL),
    ("fix the login page", Category.APPROVALS)])
async def test_each_fixed_command_sets_its_own_category(monkeypatch, body, want):
    async def reply(*_a, **_k):
        return "ok"
    for mod, fn in ((owner_calls, "handle_call_request"),
                    (chat_commands.video_learn, "queue"),
                    (chat_commands.owner_memory, "handle"),
                    (chat_commands.dev_requests, "note")):
        monkeypatch.setattr(mod, fn, reply)
    wa_format.set_reply_category(Category.MAIL)
    assert await chat_commands.run(body, "SM1") == "ok"
    assert wa_format.reply_category() is want
