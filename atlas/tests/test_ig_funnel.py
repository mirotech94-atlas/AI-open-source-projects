"""The funnel's pure parts: whole-word keyword matching, the link rule, the
text hash, quiet hours, the private-reply payload, the Graph error classes,
the card text, the command grammar, the scheduler and department wiring.
No database, no network."""
from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from atlas.kernel import departments, scheduler
from atlas.kernel.contracts import FailureClass
from atlas.modules.instagram import GraphError, funnel

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- matching

@pytest.mark.parametrize("text", [
    "asset", "ASSET", "Asset please", "asset!", "asset\U0001f525", "\U0001f525asset",
    "the asset", "asset.", "(asset)", "asset\nplease", "@you asset",
])
def test_the_keyword_matches_as_a_whole_word_in_any_case(text):
    assert funnel.matches("asset", text)


@pytest.mark.parametrize("text", [
    "assets", "reasset", "asset_1", "my_asset", "ass et", "", "link", "asse t",
])
def test_the_keyword_does_not_match_a_substring(text):
    assert not funnel.matches("asset", text)


def test_a_keyword_with_a_hyphen_is_escaped():
    assert funnel.matches("free-guide", "FREE-GUIDE pls") and not funnel.matches(
        "free-guide", "freexguide")


# ------------------------------------------------------------------ rules

@pytest.mark.parametrize("text", [
    "https://flycheap.example", "http://x.co", "www.flycheap.example", "flycheap.example",
    "see academy-store.myshopify.example", "link.ly/x", "Flycheap.Live now",
])
def test_the_public_reply_may_never_carry_a_link(text):
    assert funnel.has_link(text)
    assert "link" in (funnel.vet_set({"account": "main", "keyword": "asset",
                                      "public_reply": text, "dm_text": "x"}) or "")


@pytest.mark.parametrize("text", ["Sent you a DM \U0001f4e9", "check your inbox.",
                                  "Done. Look at your requests folder!"])
def test_ordinary_public_replies_pass(text):
    assert not funnel.has_link(text)


def test_vet_refuses_what_a_card_could_only_fail_on():
    good = {"account": "main", "keyword": "asset", "public_reply": "Sent!",
            "dm_text": "here: https://x.y/z"}
    assert funnel.vet_set(good) is None
    assert "no such Instagram account" in funnel.vet_set({**good, "account": "x"})
    assert "keyword" in funnel.vet_set({**good, "keyword": "a b"})
    assert "keyword" in funnel.vet_set({**good, "keyword": "a"})
    assert "public reply is empty" in funnel.vet_set({**good, "public_reply": " "})
    assert "DM text is empty" in funnel.vet_set({**good, "dm_text": ""})
    assert "1000" in funnel.vet_set({**good, "dm_text": "x" * 1001})
    assert "media_id" in funnel.vet_set({**good, "media_id": "abc"})
    assert funnel.vet_set({**good, "media_id": "17900000000000001"}) is None
    assert "daily_cap" in funnel.vet_set({**good, "daily_cap": 0})
    assert "daily_cap" in funnel.vet_set({**good, "daily_cap": "lots"})
    assert funnel.vet_set({**good, "daily_cap": "150"}) is None
    assert "timezone" in funnel.vet_set({**good, "timezone": "Mars/Olympus"})
    assert funnel.vet_set({**good, "timezone": "Africa/Cairo"}) is None


def test_the_text_hash_covers_both_texts_exactly():
    h = funnel.text_hash("a", "b")
    assert len(h) == 64 and h == funnel.text_hash("a", "b")
    assert h != funnel.text_hash("a ", "b") and h != funnel.text_hash("a", "b.")
    assert h != funnel.text_hash("b", "a")


def test_quiet_hours_are_22_to_8_local_and_queue_until_8():
    ny = ZoneInfo("America/New_York")
    assert funnel.in_quiet_hours(datetime(2026, 9, 30, 22, 0, tzinfo=ny))
    assert funnel.in_quiet_hours(datetime(2026, 9, 30, 3, 0, tzinfo=ny))
    assert funnel.in_quiet_hours(datetime(2026, 9, 30, 7, 59, tzinfo=ny))
    assert not funnel.in_quiet_hours(datetime(2026, 9, 30, 8, 0, tzinfo=ny))
    assert not funnel.in_quiet_hours(datetime(2026, 9, 30, 21, 59, tzinfo=ny))
    late = datetime(2026, 9, 30, 23, 30, tzinfo=ny)
    assert funnel.next_open(late) == datetime(2026, 10, 1, 8, 0, tzinfo=ny)
    early = datetime(2026, 9, 30, 2, 0, tzinfo=ny)
    assert funnel.next_open(early) == datetime(2026, 9, 30, 8, 0, tzinfo=ny)
    # The funnel's zone, not the process's: 03:00 UTC is 23:00 in New York.
    local = funnel.local_now("America/New_York", datetime(2026, 9, 30, 3, 0, tzinfo=UTC))
    assert local.hour == 23 and funnel.in_quiet_hours(local)
    assert funnel.zone("Nowhere/Here") is None and funnel.zone("") is not None


def test_backoff_doubles_and_caps():
    assert funnel.backoff(1).total_seconds() == 60
    assert funnel.backoff(2).total_seconds() == 120
    assert funnel.backoff(20).total_seconds() == 3600


# ---------------------------------------------------------- the payload

def test_the_private_reply_payload_is_metas_shape_exactly():
    assert funnel.private_reply_payload("1780", "hi there") == {
        "recipient": {"comment_id": "1780"}, "message": {"text": "hi there"}}


def test_the_private_reply_docstring_cites_the_endpoint_and_the_doc():
    doc = funnel.__doc__
    assert "/{ig-user-id}/messages" in doc
    assert '{"recipient": {"comment_id"' in doc
    assert "instagram-platform/private-replies" in doc
    assert "within 7 days" in doc and "only one message" in doc


# ----------------------------------------------------------- the classes

def _err(status: int | None, code: int | None = None) -> GraphError:
    return GraphError(f"Graph API HTTP {status} (code {code})", status, code)


@pytest.mark.parametrize("status, code, cls", [
    (401, None, FailureClass.AUTH), (403, None, FailureClass.AUTH),
    (400, 190, FailureClass.AUTH),
    (429, None, FailureClass.TRANSIENT), (400, 4, FailureClass.TRANSIENT),
    (400, 17, FailureClass.TRANSIENT), (400, 32, FailureClass.TRANSIENT),
    (400, 613, FailureClass.TRANSIENT),
    (400, 368, FailureClass.POLICY), (400, 10, FailureClass.POLICY),
    (400, 230, FailureClass.POLICY),
    (500, None, FailureClass.TRANSIENT), (None, None, FailureClass.TRANSIENT),
    (400, 100, FailureClass.VALIDATION), (404, 803, None),
])
def test_graph_errors_map_to_the_failure_classes(status, code, cls):
    assert funnel.classify_graph(_err(status, code)) is cls


def test_graph_error_carries_the_code_from_the_body():
    import httpx

    from atlas.modules.instagram import _raise_for
    r = httpx.Response(400, json={"error": {"message": "blocked", "code": 368}},
                       request=httpx.Request("POST", "https://graph.facebook.com/v21.0/x"))
    with pytest.raises(GraphError) as info:
        _raise_for(r, "tok")
    assert info.value.code == 368 and info.value.status_code == 400
    assert "(code 368)" in str(info.value)


# ------------------------------------------------------------------ card

def test_the_card_text_shows_the_exact_texts_keyword_scope_and_caps():
    text = funnel.card_detail_set({
        "account": "main", "keyword": "Asset", "public_reply": "Sent you a DM \U0001f4e9",
        "dm_text": "Roadmap: https://x.y/r", "daily_cap": "120",
        "timezone": "Africa/Cairo"})
    assert "Funnel on the main account" in text
    assert "“asset” (whole word, any case) on all recent media (the last 10 posts)" in text
    assert "“Sent you a DM \U0001f4e9”" in text
    assert "“Roadmap: https://x.y/r”" in text
    assert "120 DMs a day" in text and "22:00–08:00 Africa/Cairo" in text
    assert "one DM per person per funnel, ever" in text
    scoped = funnel.card_detail_set({"account": "stories", "keyword": "link",
                                     "public_reply": "p", "dm_text": "d",
                                     "media_id": "1790"})
    assert "on media 1790" in scoped and "200 DMs a day" in scoped


# --------------------------------------------------------------- command

def test_the_command_grammar():
    assert funnel.parse_command("pause instagram") is None
    assert funnel.parse_command("what is a funnel?") is None
    assert funnel.parse_command("funnel") == {"verb": "list"}
    assert funnel.parse_command("funnel list") == {"verb": "list"}
    assert funnel.parse_command("funnel pause 3") == {"verb": "pause", "funnel_id": 3}
    assert funnel.parse_command("funnel pause x") is None
    # Plain English that happens to start with the word is not a command:
    # it falls through to the picker untouched.
    for said in ("funnel my new leads into a sheet", "funnel the leads",
                 "funnel main asset now", "Funnel everything through Slack",
                 "funnels are great", "funnel pause everything for now"):
        assert funnel.parse_command(said) is None, said
    got = funnel.parse_command(
        'funnel main asset "Sent you a DM \U0001f4e9" "Here’s it: https://x.y/r" '
        "media=1790 cap=50 tz=Africa/Cairo")
    assert got == {"verb": "set", "params": {
        "account": "main", "keyword": "asset", "public_reply": "Sent you a DM \U0001f4e9",
        "dm_text": "Here's it: https://x.y/r", "media_id": "1790", "daily_cap": "50",
        "timezone": "Africa/Cairo"}}
    smart = funnel.parse_command("funnel Main asset “Sent!” “DM text”")
    assert smart["params"]["account"] == "main"
    assert smart["params"]["public_reply"] == "Sent!"
    assert funnel.parse_command('funnel main asset "only one"')["verb"] == "error"
    assert funnel.parse_command('funnel main asset "a" "b" bogus=1')["verb"] == "error"
    assert "unbalanced" in funnel.parse_command('funnel main asset "a "b"')["why"]


# ---------------------------------------------------------------- wiring

def test_the_scheduler_job_and_department_are_declared():
    assert "funnels" in scheduler.POLICIES
    assert scheduler.POLICIES["funnels"].missed.value == "skip-stale"
    assert departments.department_of_job("funnels") == "content"
    assert departments.JOBS["funnels"].actions == ("instagram.funnel_reply",)
    assert scheduler.FUNNELS_TICK.total_seconds() == 300


def test_the_instagram_package_never_calls_the_executor_itself():
    """The sending loop lives in kernel/funnels.py; the module only reads
    and queues (the same rule tests/test_instagram_accounts.py pins)."""
    pkg = ROOT / "atlas" / "modules" / "instagram"
    for f in pkg.glob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert "executor" not in (node.module or ""), f.name
    kernel = (ROOT / "atlas" / "kernel" / "funnels.py").read_text()
    assert "execute(ExecutionRequest(" in kernel


async def test_the_scheduler_step_is_gated_and_detached(monkeypatch):
    calls: list[str] = []

    async def fake_tick():
        calls.append("tick")
        return {}

    from atlas.kernel import funnels
    monkeypatch.setattr(funnels, "tick", fake_tick)
    monkeypatch.setattr(scheduler, "_last_funnels_at", None)
    monkeypatch.setattr(scheduler, "_funnels_task", None)
    monkeypatch.setenv("FUNNELS", "off")
    await scheduler._maybe_funnels()
    assert calls == []
    monkeypatch.setenv("FUNNELS", "on")
    await scheduler._maybe_funnels()
    import asyncio
    await asyncio.sleep(0)
    assert calls == ["tick"]
    await scheduler._maybe_funnels()          # inside FUNNELS_TICK: not again
    await asyncio.sleep(0)
    assert calls == ["tick"]
