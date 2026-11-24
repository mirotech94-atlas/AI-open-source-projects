"""Everyday Mac control (Miro, 2026-09-28): iMessage, URLs, Shortcuts, quit,
volume, notifications. Closed, validated, tiered in code.

The Mac side of the same rules (quoting, the Shortcut allowlist) is in
tests/test_mac_agent_script.py.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import executor
from atlas.kernel.approvals import Unrunnable, propose
from atlas.kernel.contracts import Health, Tier
from atlas.kernel.executor import ExecutionRequest
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY, payload_hash
from atlas.modules import mac

ACTIONS = {a.name: a for a in mac.MODULE.actions()}


@pytest.fixture
def registered(monkeypatch, memory_backend):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    for a in ACTIONS.values():
        monkeypatch.setitem(REGISTRY.actions, a.name, a)
        monkeypatch.setitem(REGISTRY.decisions, a.name, classify(a))
    executor._recent.clear()
    return memory_backend


def _awake(mem, seconds_ago: float = 5) -> None:
    mem.responses["SELECT max(last_seen) AS at FROM mac_agent"] = {
        "at": datetime.now(UTC) - timedelta(seconds=seconds_ago)}


def _mac_answers(mem, *, ok=True, detail="done", token=None, jid=9) -> None:
    mem.responses["INSERT INTO mac_jobs"] = {"id": jid}
    mem.responses["SELECT status, ok, detail, token FROM mac_jobs"] = {
        "status": "done", "ok": ok, "detail": detail, "token": token}


def _jobs(mem) -> list[tuple[str, dict]]:
    return [(args[0], json.loads(args[1])) for q, args in mem.sql_log
            if "INSERT INTO mac_jobs" in q]


# ------------------------------------------------------------------ tiers

TIER = {
    "mac.open_app": Tier.AUTO, "mac.play_music": Tier.AUTO,
    "mac.music_control": Tier.AUTO, "mac.add_reminder": Tier.AUTO,
    "mac.set_volume": Tier.AUTO, "mac.notify": Tier.AUTO,
    # Tier 1 since 2026-10-06 (Miro's 2026-09-30 ruling: not urgent, no card).
    "mac.open_url": Tier.AUTO, "mac.quit_app": Tier.AUTO,
    "mac.web_search": Tier.AUTO, "mac.new_note": Tier.AUTO,
    "mac.send_imessage": Tier.ASK, "mac.run_shortcut": Tier.ASK,
}


def test_each_action_has_the_tier_set_for_it_in_code():
    assert {n: a.tier for n, a in ACTIONS.items()} == TIER
    for name, action in ACTIONS.items():
        decision = classify(action)
        assert decision.allowed and decision.tier is TIER[name], name


def test_every_mac_action_is_principal_only():
    assert all(a.principal_only for a in ACTIONS.values())


def test_tier_1_mac_actions_have_a_dry_run_and_a_rollback():
    for name in ("mac.set_volume", "mac.notify", "mac.open_url", "mac.quit_app",
                 "mac.web_search", "mac.new_note"):
        a = ACTIONS[name]
        assert a.dry_run is not None and a.rollback is not None and not a.irreversible


def test_tier_2_mac_actions_are_vetted_and_carry_their_params_on_the_card():
    for name in ("mac.send_imessage", "mac.run_shortcut"):
        a = ACTIONS[name]
        assert a.vet is not None and a.card_detail is not None, name
        assert a.irreversible and a.rollback is None, name
    assert ACTIONS["mac.send_imessage"].card_always


def test_open_url_is_one_tier_whatever_the_host():
    """Invariant 3: a tier is never chosen from a parameter. An own-site
    URL is the same tier as any other (Tier 1 since 2026-10-06)."""
    assert ACTIONS["mac.open_url"].tier is Tier.AUTO
    assert not any(n.startswith("mac.open_site") for n in ACTIONS)


def test_quit_and_url_keep_their_parameter_checks_as_tier_1():
    """Out of the cards, the pure checks still refuse before anything runs."""
    assert ACTIONS["mac.open_url"].vet is mac.vet_url
    assert ACTIONS["mac.quit_app"].vet is mac.vet_quit


# ------------------------------------------------------------------ iMessage

@pytest.mark.parametrize("to,kind", [
    ("+15551234567", "phone"), ("+447911123456", "phone"),
    ("miro@example.com", "email"), ("a.b+c@sub.example.co.uk", "email"),
    ("Mum", "contact"), ("Jean-Luc O'Brien", "contact"), ("Zoë", "contact"),
])
def test_a_recipient_is_a_phone_an_email_or_a_contact_name(to, kind):
    assert mac.recipient_kind(to) == kind


@pytest.mark.parametrize("to", [
    "", " Mum", "Mum ", "5551234567", "+0551234567", "+1 555 123 4567",
    'Mum" & (do shell script "id") & "', "Mum; rm -rf ~", "-e", "a\nb",
    "x" * 61, "bob@", "@example.com", "bob@example", "tel:+15551234567",
    "Mum\u202e", 42, None,
    # `$` would match before a trailing newline; the patterns end in \Z.
    "+15551234567\n", "bob@example.com\n", "Mum\n",
])
def test_anything_else_is_refused_as_a_recipient(to):
    assert mac.recipient_kind(to) is None
    assert mac.imessage_problem(to, "hi") is not None


def test_the_text_is_1000_characters_at_most():
    assert mac.imessage_problem("Mum", "x" * 1000) is None
    assert "1001" in mac.imessage_problem("Mum", "x" * 1001)


@pytest.mark.parametrize("text", ["", "   ", "hi\x00", "hi\x1b[2J", "pay \u202eevil"])
def test_an_empty_or_hidden_character_text_is_refused(text):
    assert mac.imessage_problem("Mum", text) is not None


def test_quotes_and_newlines_in_a_message_are_just_text():
    text = 'he said "end tell" & do shell script "id"\nsecond line'
    assert mac.imessage_problem("+15551234567", text) is None


def test_the_imessage_card_shows_the_exact_recipient_and_whole_text():
    text = "Running 10 min late\n" + "y" * 900
    card = mac.card_imessage({"to": "+15551234567", "text": text})
    assert "*To:* +15551234567 (phone number)" in card
    assert "\n".join("> " + ln for ln in text.split("\n")) in card
    named = mac.card_imessage({"to": "Mum", "text": "hi"})
    assert "*To:* Mum" in named and "exactly one person" in named


@pytest.mark.parametrize("text", ["<https://phish.example|Your parcel>",
                                  "<!here> urgent", "<@U123> & <#C1>"])
def test_slack_markup_in_a_message_is_shown_as_text_not_rendered(text):
    """A caller can get a card raised (an inbound call is classified into
    the same catalog): what the card shows must be what would be sent."""
    card = mac.card_imessage({"to": "+15551234567", "text": text})
    # Every Slack link, mention and broadcast starts with "<".
    assert "<" not in card and mac.shown(text) in card
    # And Slack shows the escaped form as exactly the characters sent.
    unescaped = (mac.shown(text).replace("&lt;", "<").replace("&gt;", ">")
                 .replace("&amp;", "&"))
    assert unescaped == text


def test_the_other_cards_escape_what_they_show():
    assert "<" not in mac.card_shortcut({"name": "<!here>"})
    assert mac.url_problem("https://example.com/<!here>") is not None


async def test_a_bad_imessage_is_never_a_card(registered):
    with pytest.raises(Unrunnable):
        await propose("mac.send_imessage", {"to": "Mum; rm", "text": "hi"},
                      reason="asked")
    with pytest.raises(Unrunnable):
        await propose("mac.send_imessage", {"to": "Mum", "text": "x" * 1001},
                      reason="asked")


async def test_the_dry_run_only_resolves_and_the_run_sends(registered):
    _awake(registered)
    _mac_answers(registered, detail="Mum (+1 555 123 4567)")
    dry = await mac.send_imessage_dry("Mum", "on my way")
    assert dry.ok and "Mum (+1 555 123 4567)" in dry.detail
    assert _jobs(registered) == [("resolve_recipient", {"to": "Mum"})]


async def test_approved_on_slack_the_message_goes_through_the_executor(
        registered, monkeypatch):
    """End to end: the card, approved with a Slack button, passes the
    principal gate, resolves (dry run) and then sends."""
    from atlas.kernel import approvals
    from atlas.kernel.approvals import decide
    _awake(registered)
    _mac_answers(registered, detail="sent to Mum (+1 555 123 4567)")
    params = {"to": "Mum", "text": "on my way"}
    registered.responses["UPDATE proposals SET status='approved'"] = {
        "action": "mac.send_imessage", "params": json.dumps(params),
        "reason": "Asked in Slack", "incident_id": None}
    # What was recorded with the card, read beside the claim (P04, S08).
    registered.responses["SELECT catalog_version, approved_payload_hash"] = {
        "catalog_version": REGISTRY.action_version("mac.send_imessage"),
        "approved_payload_hash": payload_hash(params), "dedup_key": None,
        "evidence": "{}", "rollback_plan": ""}
    receipts = []

    async def fake_notify(msg):
        receipts.append(msg)
        return ["slack"]

    monkeypatch.setattr(approvals, "notify", fake_notify)
    verdict = await decide(12, "approve", via="slack:UMIRO")
    # Answered at once (Slack's 3 s window); the Mac work runs after it.
    assert verdict.ok and verdict.outcome == "started", verdict.render()
    await approvals.RUNNING[12]
    assert _jobs(registered) == [("resolve_recipient", {"to": "Mum"}),
                                 ("send_imessage", params)]
    assert any("sent to Mum" in r.body for r in receipts)


@pytest.mark.parametrize("who", [None, "voice:dashboard", "phone", "agent:ops"])
async def test_nothing_else_can_send_one(registered, who):
    _awake(registered)
    _mac_answers(registered)
    out = await executor.execute(ExecutionRequest(
        "mac.send_imessage", {"to": "+15551234567", "text": "hi"}, "x",
        approved_by=who))
    assert not out.ok and _jobs(registered) == []


# ------------------------------------------------------------------ URLs

@pytest.mark.parametrize("url,host", [
    ("https://atlas.storefront.test/dashboard", "atlas.storefront.test"),
    ("http://example.com", "example.com"),
    ("https://www.youtube.com/watch?v=abc&t=1", "www.youtube.com"),
    ("https://bücher.example/x", "xn--bcher-kva.example"),
    ("https://example.com:8443/a#b", "example.com"),
])
def test_a_plain_http_url_opens(url, host):
    assert mac.url_problem(url) is None and mac.url_host(url) == host


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "javascript:alert(1)", "data:text/html,hi",
    "ftp://example.com", "x-apple.systempreferences:com.apple.preference",
    "vnc://host", "smb://host/share", "ssh://host", "shortcuts://run-shortcut?name=x",
    "https://user:pw@example.com/", "https://atlas.storefront.test@evil.io/",
    "https://exa mple.com", "https://example.com/\nx", " https://example.com",
    "-a https://example.com", "https:example.com", "https:///path",
    "https://example.com\\@evil.io", "https://e\u202example.com",
    "https://example.com:99999/", "http://" + "a" * 2050 + ".com", "", None,
])
def test_anything_but_a_plain_http_url_is_refused(url):
    assert mac.url_problem(url) is not None


@pytest.mark.parametrize("url,own", [
    ("https://atlas.storefront.test/x", True), ("https://m.youtube.com/", True),
    ("https://admin.shopify.com/store/x", True), ("https://flycheap.example.com", True),
    ("https://youtube.com.evil.io/", False), ("https://notyoutube.com/", False),
    ("https://evil-atlas.storefront.test.io/", False),
])
async def test_the_url_dry_run_says_whether_it_is_one_of_his_sites(
        registered, url, own):
    _awake(registered)
    out = await mac.open_url_dry(url)
    assert out.ok and mac.url_host(url) in out.detail
    assert ("not one of your sites" in out.detail) is (not own)


async def test_open_url_runs_straight_away_for_miro_and_its_undo_is_honest(
        registered):
    _awake(registered)
    _mac_answers(registered, detail="opened x.com in your browser")
    out = await executor.execute(ExecutionRequest(
        "mac.open_url", {"url": "https://x.com"}, "asked",
        approved_by="voice:menubar"))
    assert out.ok and _jobs(registered) == [("open_url", {"url": "https://x.com"})]
    undo = await mac.undo_open_url("")
    assert undo.ok and "can't be un-opened" in undo.detail
    assert len(_jobs(registered)) == 1


# ------------------------------------------------------------------ Shortcuts

@pytest.mark.parametrize("name", ["Morning", "Log water 💧", "Home / Away"])
def test_a_shortcut_name_is_passed_on(name):
    assert mac.shortcut_problem(name) is None


@pytest.mark.parametrize("name", ["", "-i", "--input-path=/etc/passwd", " x",
                                  "x ", "a\nb", "a\x00", "x" * 101, None])
def test_a_shortcut_name_that_could_be_an_option_or_hides_text_is_refused(name):
    assert mac.shortcut_problem(name) is not None


async def test_the_shortcut_dry_run_asks_the_mac_and_learns_only_yes_or_no(registered):
    _awake(registered)
    _mac_answers(registered, ok=False,
                 detail="'Morning' is not in ~/.config/atlas/shortcuts-allow.txt")
    out = await mac.run_shortcut_dry("Morning")
    assert not out.ok and "shortcuts-allow" in out.detail
    assert _jobs(registered) == [("shortcut_allowed", {"name": "Morning"})]


# ------------------------------------------------------------------ quit

@pytest.mark.parametrize("app", ["Finder", "finder", "loginwindow", "Dock",
                                 "SystemUIServer", "System Events", "Terminal",
                                 "Keychain Access", "/System/Library/x", "../Notes"])
def test_the_desktop_and_system_processes_are_never_quit(app):
    assert mac.vet_quit({"app": app}) is not None


def test_an_ordinary_app_can_be_quit():
    assert mac.vet_quit({"app": "Notes"}) is None


async def test_quit_runs_straight_away_and_the_undo_reopens_only_what_it_quit(
        registered):
    _awake(registered)
    token = json.dumps({"app": "Notes", "quit_by_us": True})
    _mac_answers(registered, detail="Notes quit", token=token)
    out = await executor.execute(ExecutionRequest(
        "mac.quit_app", {"app": "Notes"}, "asked", approved_by="slack:UMIRO"))
    assert out.ok and out.rollback_token == token
    await mac.undo_quit_app(token)
    assert _jobs(registered)[-1] == ("open_app", {"app": "Notes"})


@pytest.mark.parametrize("token", [
    "", "null", "not json", json.dumps({"app": "Notes"}),
    json.dumps({"app": "Notes", "quit_by_us": False}),
])
async def test_a_quit_that_closed_nothing_reopens_nothing(registered, token):
    """An older agent sends no token; an app that was not open was not quit."""
    _awake(registered)
    out = await mac.undo_quit_app(token)
    assert out.ok and "nothing to reopen" in out.detail and _jobs(registered) == []


async def test_a_bad_app_in_a_quit_token_is_never_opened(registered):
    out = await mac.undo_quit_app(json.dumps({"app": "Terminal", "quit_by_us": True}))
    assert not out.ok and _jobs(registered) == []


# ------------------------------------------------------------------ web search

@pytest.mark.parametrize("query,engine,url", [
    ("best pizza near me", "google", "https://www.google.com/search?q=best+pizza+near+me"),
    ("a&b=c #1 /x?", "google",
     "https://www.google.com/search?q=a%26b%3Dc+%231+%2Fx%3F"),
    ("lofi", "youtube", "https://www.youtube.com/results?search_query=lofi"),
    ("  two   spaces ", "duckduckgo", "https://duckduckgo.com/?q=two+spaces"),
    ("café", "bing", "https://www.bing.com/search?q=caf%C3%A9"),
])
def test_a_search_is_only_ever_the_value_of_q(query, engine, url):
    assert mac.search_problem(query, engine) is None
    assert mac.search_url(query, engine) == url
    assert mac.url_problem(url) is None


@pytest.mark.parametrize("query,engine", [
    ("", "google"), ("   ", "google"), ("x" * 301, "google"), (None, "google"),
    ("ok", "evil.com"), ("ok", ""), ("ok", None), ("a\x00b", "google"),
    ("pay \u202eevil", "google"),
])
def test_a_bad_search_is_refused(query, engine):
    assert mac.search_problem(query, engine) is not None


async def test_web_search_opens_through_the_existing_open_url_operation(registered):
    """So it works on an agent installed before mac.web_search existed."""
    _awake(registered)
    _mac_answers(registered, detail="opened www.google.com in your browser")
    out = await executor.execute(ExecutionRequest(
        "mac.web_search", {"query": "cheap flights to rome"}, "asked",
        approved_by="voice:menubar"))
    assert out.ok and "cheap flights to rome" in out.detail
    assert _jobs(registered) == [
        ("open_url", {"url": "https://www.google.com/search?q=cheap+flights+to+rome"})]


async def test_web_search_refuses_an_engine_it_does_not_know(registered):
    _awake(registered)
    out = await executor.execute(ExecutionRequest(
        "mac.web_search", {"query": "x", "engine": "evil"}, "asked",
        approved_by="voice:menubar"))
    assert not out.ok and _jobs(registered) == []


# ------------------------------------------------------------------ notes

@pytest.mark.parametrize("title,body,ok", [
    ("hello", "", True), ("", "buy milk", True), ("Shopping", "eggs\nmilk", True),
    ("", "", False), ("  ", " ", False), ("x" * 101, "", False),
    ("ok", "x" * 2001, False), ("a\nb", "", False), ("t\x07", "", False),
    ("ok", "pay \u202eevil", False), (None, "", False), ("ok", None, False),
])
def test_a_note_is_a_title_and_some_plain_text(title, body, ok):
    assert (mac.note_problem(title, body) is None) is ok


async def test_a_new_note_goes_to_the_mac_as_its_new_note_operation(registered):
    _awake(registered)
    token = json.dumps({"id": "x-coredata://n/1", "title": "hello"})
    _mac_answers(registered, detail="created 'hello' in Notes", token=token)
    out = await executor.execute(ExecutionRequest(
        "mac.new_note", {"title": "hello"}, "asked", approved_by="voice:menubar"))
    assert out.ok and out.rollback_token == token
    assert _jobs(registered) == [("new_note", {"title": "hello", "body": ""})]


async def test_the_new_note_undo_never_deletes(registered):
    out = await mac.undo_new_note(json.dumps({"id": "x", "title": "hello"}))
    assert out.ok and "never deletes" in out.detail and "'hello'" in out.detail
    assert _jobs(registered) == []


async def test_an_old_agent_refusing_new_note_is_said_honestly(registered):
    """An agent installed before this change answers 'unknown operation';
    the reply says what to do, not a bare refusal."""
    _awake(registered)
    _mac_answers(registered, ok=False, detail="unknown operation 'new_note'")
    out = await mac.new_note("hello", "")
    assert not out.ok and "older than this" in out.detail
    assert "install-agent.sh" in out.detail


# ------------------------------------------------------------------ volume

@pytest.mark.parametrize("given,want", [
    (0, 0), (100, 100), (35, 35), ("50", 50), (" 7 ", 7),
    (True, None), (False, None), (101, None), (-1, None), ("5.5", None),
    (5.0, None), ("fifty", None), (None, None),
])
def test_the_volume_is_a_whole_number_from_0_to_100(given, want):
    assert mac.volume_level(given) == want


async def test_the_volume_dry_run_reads_the_level_and_the_undo_restores_it(registered):
    _awake(registered)
    _mac_answers(registered, detail="the volume is 19")
    dry = await mac.set_volume_dry("60")
    assert dry.ok and "19" in dry.detail and "60" in dry.detail
    assert _jobs(registered) == [("get_volume", {})]
    registered.sql_log.clear()
    _mac_answers(registered, detail="volume set to 19 (it was 60)")
    back = await mac.undo_volume(json.dumps({"previous": 19}))
    assert back.ok and _jobs(registered) == [("set_volume", {"level": 19})]


async def test_set_volume_runs_straight_away_for_miro(registered):
    _awake(registered)
    _mac_answers(registered, detail="volume set to 60 (it was 19)",
                 token=json.dumps({"previous": 19}))
    out = await executor.execute(ExecutionRequest(
        "mac.set_volume", {"level": 60}, "asked", approved_by="slack:UMIRO"))
    assert out.ok and out.rollback_token == json.dumps({"previous": 19})
    assert ("set_volume", {"level": 60}) in _jobs(registered)


@pytest.mark.parametrize("token", ["", "nope", "[]", '{"previous": 250}',
                                   '{"previous": true}'])
async def test_an_unreadable_volume_token_restores_nothing(registered, token):
    out = await mac.undo_volume(token)
    assert not out.ok and _jobs(registered) == []


# ------------------------------------------------------------------ notify

def test_a_notification_is_short_and_plain():
    assert mac.notify_problem("Build done", "all green") is None
    assert mac.notify_problem("Build done", "") is None
    assert mac.notify_problem("", "x") is not None
    assert mac.notify_problem("x" * 101, "") is not None
    assert mac.notify_problem("t", "x" * 501) is not None
    assert mac.notify_problem("t\x07", "") is not None


async def test_the_notification_undo_is_recorded_not_performed(registered):
    out = await mac.undo_notify("")
    assert out.ok and "nothing to undo" in out.detail
    assert _jobs(registered) == []


# ------------------------------------------------------------------ mac.agent

@pytest.mark.parametrize("seconds_ago", [6 * 60, 3600, 86400])
async def test_a_stale_agent_is_unknown_never_ok(memory_backend, seconds_ago):
    _awake(memory_backend, seconds_ago)
    obs = await mac.check_agent()
    assert obs.health is Health.UNKNOWN


async def test_an_agent_that_never_connected_is_unknown(memory_backend):
    memory_backend.responses["SELECT max(last_seen) AS at FROM mac_agent"] = {"at": None}
    assert (await mac.check_agent()).health is Health.UNKNOWN


async def test_an_unreadable_agent_state_is_unknown(monkeypatch):
    async def broken(*_a, **_kw):
        raise ConnectionError("db down")

    monkeypatch.setattr("atlas.kernel.state.fetch_one", broken)
    assert (await mac.check_agent()).health is Health.UNKNOWN


async def test_a_live_agent_is_ok(memory_backend):
    _awake(memory_backend, 10)
    assert (await mac.check_agent()).health is Health.OK


# ------------------------------------------------------------------ said out loud

@pytest.mark.parametrize("said", [
    "text Mum I'm running late", "Jarvis, message Sam that I'm outside",
    "iMessage +15551234567 on my way", "set the volume to 30",
    "turn the volume down", "mute", "notify me when the build is done",
    "open flycheap.example.com", "go to https://atlas.storefront.test/dashboard",
    "run my Morning shortcut", "quit Safari",
])
def test_everyday_mac_requests_are_heard_as_orders(said):
    from atlas.api.voice import ACTION_INTENT
    assert ACTION_INTENT.search(said), said


@pytest.mark.parametrize("said", [
    "what did Sam's text say", "is there a message from the bank",
    "how loud is the volume", "open the guardian build report",
])
def test_questions_about_messages_are_not_orders(said):
    from atlas.api.voice import ACTION_INTENT
    assert ACTION_INTENT.search(said) is None, said
