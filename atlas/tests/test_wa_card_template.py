"""`tools/wa_card_template.py`: the one-time Content API template script. Mocked
transport only; nothing reaches Twilio."""
from __future__ import annotations

import importlib.util
import json
import pathlib

import httpx
import pytest

PATH = pathlib.Path(__file__).resolve().parent.parent / "tools" / "wa_card_template.py"
SID = "HX" + "c" * 32


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("wa_card_template", PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_the_template_has_two_quick_reply_buttons_with_card_payloads(tool):
    body = tool.template_body()["types"]["twilio/quick-reply"]
    assert body["body"] == "{{1}}"
    ids = [a["id"] for a in body["actions"]]
    assert ids == ["approve:{{2}}:{{3}}", "decline:{{2}}:{{3}}"]
    assert [a["title"] for a in body["actions"]] == ["✅ Approve", "❌ Decline"]


def test_it_creates_the_template_once_when_missing(tool):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append((req.method, str(req.url)))
        if req.method == "GET":
            return httpx.Response(200, json={"contents": [], "meta": {}})
        assert json.loads(req.content)["friendly_name"] == tool.FRIENDLY_NAME
        return httpx.Response(201, json={"sid": SID})
    sid, created = tool.ensure_template(client(handler))
    assert (sid, created) == (SID, True)
    assert [m for m, _ in calls] == ["GET", "POST"]


def test_it_reuses_an_existing_template_and_never_posts(tool):
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.method == "GET"
        return httpx.Response(200, json={"contents": [
            {"sid": "HX" + "1" * 32, "friendly_name": "other"},
            {"sid": SID, "friendly_name": tool.FRIENDLY_NAME}], "meta": {}})
    assert tool.ensure_template(client(handler)) == (SID, False)


def test_it_follows_pages_when_looking(tool):
    def handler(req: httpx.Request) -> httpx.Response:
        if "Page=1" in str(req.url):
            return httpx.Response(200, json={"contents": [
                {"sid": SID, "friendly_name": tool.FRIENDLY_NAME}], "meta": {}})
        return httpx.Response(200, json={"contents": [], "meta": {
            "next_page_url": "https://content.twilio.com/v1/Content?Page=1"}})
    assert tool.ensure_template(client(handler)) == (SID, False)


def test_a_refusal_is_not_hidden(tool):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})
    with pytest.raises(httpx.HTTPStatusError):
        tool.ensure_template(client(handler))


def test_main_needs_credentials_and_prints_the_env_line(tool, monkeypatch, capsys):
    monkeypatch.delenv("TWILIO_ACCOUNT_SID", raising=False)
    monkeypatch.delenv("TWILIO_AUTH_TOKEN", raising=False)
    assert tool.main() == 2
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC" + "0" * 32)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "t" * 32)
    monkeypatch.setattr(tool, "ensure_template", lambda c: (SID, False))
    assert tool.main() == 0
    out = capsys.readouterr().out
    assert f"WHATSAPP_CONTENT_SID_CARD={SID}" in out
