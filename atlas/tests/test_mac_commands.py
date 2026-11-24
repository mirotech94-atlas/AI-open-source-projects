"""Words said to the Mac from the menu bar (the owner, 2026-10-06, from a reel):
read in code, resolved whole, run in order through the executor.

The parser is pure (atlas/modules/mac/commands.py); the runner is
kernel.spoken.run_spoken_commands; the route is POST /api/voice/command.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import executor, spoken
from atlas.kernel.contracts import ActionResult
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY
from atlas.modules import mac
from atlas.modules.mac import commands
from atlas.modules.mac.commands import Step, Unread, clauses, plan


def _steps(said: str) -> list[tuple[str, dict]]:
    p = plan(said)
    assert p.ask is None, p.ask
    return [(s.action, s.params) for s in p.items if isinstance(s, Step)]


# ------------------------------------------------------------------ the reel

def test_open_notes_and_create_a_note_titled_hello():
    assert _steps("open Notes and create a note titled hello") == [
        ("mac.open_app", {"app": "Notes"}),
        ("mac.new_note", {"title": "hello", "body": ""})]


@pytest.mark.parametrize("said", [
    "Open Notes, once you're there, create a new note and make the title say hello",
    "open notes once you're there create a new note make the title say hello",
    "Hey Atlas, open Notes. Once it's open, create a new note. Make the title say hello.",
    "open the notes app and then create a new note, title it hello",
])
def test_once_youre_there_a_new_note_takes_the_title_said_after_it(said):
    assert _steps(said) == [("mac.open_app", {"app": "Notes"}),
                            ("mac.new_note", {"title": "hello", "body": ""})]


@pytest.mark.parametrize("said,query", [
    ("open the browser and google best pizza in town", "best pizza in town"),
    ("Open the browser and Google-search the weather in Rome", "the weather in Rome"),
    ("open my browser, then search for flights to Lisbon", "flights to Lisbon"),
])
def test_open_the_browser_and_search_is_one_search_in_his_browser(said, query):
    assert _steps(said) == [("mac.web_search", {"query": query, "engine": "google"})]


@pytest.mark.parametrize("said", ["open x.com", "go to x.com", "Open X.com.",
                                  "go to x dot com", "visit https://x.com",
                                  "open twitter", "take me to x"])
def test_open_x_dot_com(said):
    assert _steps(said) == [("mac.open_url", {"url": "https://x.com"})]


def test_the_browser_alone_opens_google():
    assert _steps("open the browser") == [
        ("mac.open_url", {"url": "https://www.google.com"})]


# ------------------------------------------------------------------ phrases

@pytest.mark.parametrize("said,want", [
    ("open Safari", [("mac.open_app", {"app": "Safari"})]),
    ("launch spotify", [("mac.open_app", {"app": "Spotify"})]),
    ("open up the notes app", [("mac.open_app", {"app": "Notes"})]),
    ("open chrome", [("mac.open_app", {"app": "Google Chrome"})]),
    ("close Safari", [("mac.quit_app", {"app": "Safari"})]),
    ("quit Slack please", [("mac.quit_app", {"app": "Slack"})]),
    ("quit Safari then open Notes", [("mac.quit_app", {"app": "Safari"}),
                                     ("mac.open_app", {"app": "Notes"})]),
    ("go to youtube", [("mac.open_url", {"url": "https://www.youtube.com"})]),
    ("go to atlas", [("mac.open_url", {"url": "http://127.0.0.1:8080"})]),
    ("go to flycheap.example/deals", [("mac.open_url",
                                    {"url": "https://flycheap.example/deals"})]),
    ("search for salt and pepper", [("mac.web_search",
                                     {"query": "salt and pepper", "engine": "google"})]),
    ("google how to open a jar", [("mac.web_search",
                                   {"query": "how to open a jar", "engine": "google"})]),
    ("search youtube for lofi beats", [("mac.web_search",
                                        {"query": "lofi beats", "engine": "youtube"})]),
    ("search for cats on youtube", [("mac.web_search",
                                     {"query": "cats", "engine": "youtube"})]),
    ("look up the capital of Peru", [("mac.web_search",
                                      {"query": "the capital of Peru",
                                       "engine": "google"})]),
    ("new note groceries", [("mac.new_note", {"title": "groceries", "body": ""})]),
    ("create a note called bread and butter",
     [("mac.new_note", {"title": "bread and butter", "body": ""})]),
    ("take a note that says buy milk", [("mac.new_note",
                                         {"title": "", "body": "buy milk"})]),
    ("Open Notes. Create a note called groceries. Then search for pasta recipes.",
     [("mac.open_app", {"app": "Notes"}),
      ("mac.new_note", {"title": "groceries", "body": ""}),
      ("mac.web_search", {"query": "pasta recipes", "engine": "google"})]),
])
def test_the_phrases_it_reads(said, want):
    assert _steps(said) == want


@pytest.mark.parametrize("said,parts", [
    ("search for salt and pepper", ["search for salt and pepper"]),
    ("create a note called bread and butter", ["create a note called bread and butter"]),
    ("search for open source projects", ["search for open source projects"]),
    ("open Notes and create a note", ["open Notes", "create a note"]),
    ("open Notes, then open Safari", ["open Notes", "open Safari"]),
    ("new note called shopping, milk and eggs", ["new note called shopping, milk and eggs"]),
])
def test_a_clause_is_cut_only_where_a_command_starts(said, parts):
    assert clauses(said)[0] == parts


# ------------------------------------------------------------------ cancel

@pytest.mark.parametrize("said,want", [
    ("open Safari... no wait, open Chrome", [("mac.open_app", {"app": "Google Chrome"})]),
    ("open Safari, no wait, Chrome", [("mac.open_app", {"app": "Google Chrome"})]),
    ("open Notes, search for cats, never mind", [("mac.open_app", {"app": "Notes"})]),
    ("open Notes, scratch that, open Mail", [("mac.open_app", {"app": "Mail"})]),
])
def test_no_wait_and_never_mind_withdraw_the_clause_before(said, want):
    assert _steps(said) == want


@pytest.mark.parametrize("said", ["never mind", "open Safari no wait",
                                  "Open X… no wait", "nevermind."])
def test_a_withdrawn_request_runs_nothing_and_is_still_heard(said):
    p = plan(said)
    assert p.handled and p.steps == () and p.ask is None and p.cancelled


# ------------------------------------------------------------------ ask, once

@pytest.mark.parametrize("said,question", [
    ("close it", "Which app"),
    ("open that", "Open what"),
    ("search", "Search for what"),
    ("open", "Open what"),
    ("make the title say hello", "Which note"),
    ("create a new note", "What should the note be called"),
    ("open Notes or Safari", "which one: Notes or Safari"),
    ("open Notes and Safari", "Two apps"),
    ("go to bobsite", "Which address is bobsite"),
    ("open Notes, then close it", "Which app"),
])
def test_an_ambiguous_clause_is_one_question_and_nothing_runs(said, question):
    p = plan(said)
    assert p.ask is not None and question in p.ask and p.items == ()


def test_the_title_never_edits_a_note_already_on_screen():
    """'Make the title say' with no new note in the same breath is a
    question, not an edit of whatever note is in front."""
    p = plan("open Notes and make the title say hello")
    assert p.ask is not None and p.items == ()


def test_a_question_is_not_a_mac_command():
    p = plan("what's the weather today")
    assert not p.handled and p.items == (Unread("what's the weather today"),)


def test_a_clause_it_cannot_read_is_left_for_the_model_in_its_place():
    p = plan("open Notes and play some lofi")
    assert p.items == (Step("mac.open_app", {"app": "Notes"}, "open Notes"),
                       Unread("play some lofi"))


@pytest.mark.parametrize("action,params", [
    ("mac.open_app", {"app": "Terminal"}),
    ("mac.open_app", {"app": "Photo Booth"}),
    ("mac.quit_app", {"app": "Finder"}),
    ("mac.open_url", {"url": "file:///etc/passwd"}),
    ("mac.web_search", {"query": "", "engine": "google"}),
    ("mac.new_note", {"title": "", "body": ""}),
])
def test_the_pure_checks_refuse_before_anything_runs(action, params):
    assert commands.problem(action, params) is not None


# ------------------------------------------------------------------ the runner

@pytest.fixture
def registered(monkeypatch, memory_backend):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    for a in mac.MODULE.actions():
        monkeypatch.setitem(REGISTRY.actions, a.name, a)
        monkeypatch.setitem(REGISTRY.decisions, a.name, classify(a))
    executor._recent.clear()
    return memory_backend


class FakeExecutor:
    def __init__(self, refuse: str | None = None):
        self.calls: list = []
        self.refuse = refuse

    async def __call__(self, req):
        self.calls.append(req)
        if req.action == self.refuse:
            return ActionResult(False, f"no app called {req.params.get('app')!r}")
        return ActionResult(True, f"did {req.action}")


async def _no_model(*_a, **_k):
    raise AssertionError("the model was asked")


async def test_a_chain_runs_in_order_as_owner_from_the_menu_bar(registered):
    fake = FakeExecutor()
    heard = await spoken.run_spoken_commands(
        "open Notes, once you're there create a new note, make the title say hello",
        execute=fake, choose=_no_model)
    assert [(r.action, r.params) for r in fake.calls] == [
        ("mac.open_app", {"app": "Notes"}),
        ("mac.new_note", {"title": "hello", "body": ""})]
    assert all(r.approved_by == "voice:menubar" for r in fake.calls)
    assert heard.handled and heard.ask is None and [r.ok for r in heard.ran] == [1, 1]
    assert heard.line == "Did mac.open_app; did mac.new_note"


async def test_the_chain_stops_at_the_first_refusal_and_says_so(registered):
    fake = FakeExecutor(refuse="mac.open_app")
    heard = await spoken.run_spoken_commands(
        "quit Safari, open Nopes, search for cats", execute=fake, choose=_no_model)
    assert [r.action for r in fake.calls] == ["mac.quit_app", "mac.open_app"]
    assert "stopped at “open Nopes”" in heard.line
    assert "not run: “search for cats”" in heard.line


async def test_at_most_six_steps_run(registered):
    fake = FakeExecutor()
    said = ", ".join(f"search for thing {n}" for n in range(8))
    heard = await spoken.run_spoken_commands(said, execute=fake, choose=_no_model)
    assert len(fake.calls) == spoken.MAX_CHAIN == 6
    assert "at most 6" in heard.line and "thing 7" in heard.line


async def test_a_refused_parameter_runs_nothing_at_all(registered):
    fake = FakeExecutor()
    heard = await spoken.run_spoken_commands("open Notes and open Terminal",
                                             execute=fake, choose=_no_model)
    assert fake.calls == [] and heard.line.startswith("Nothing ran")


async def test_a_question_runs_nothing(registered):
    fake = FakeExecutor()
    heard = await spoken.run_spoken_commands("open Notes and close it",
                                             execute=fake, choose=_no_model)
    assert fake.calls == [] and heard.ask and "Which app" in heard.ask


async def test_never_mind_runs_nothing(registered):
    fake = FakeExecutor()
    heard = await spoken.run_spoken_commands("never mind", execute=fake,
                                             choose=_no_model)
    assert fake.calls == [] and heard.handled and "nothing ran" in heard.line


async def test_not_a_mac_command_is_left_for_ask(registered):
    fake = FakeExecutor()
    heard = await spoken.run_spoken_commands("what's the weather today",
                                             execute=fake, choose=_no_model)
    assert not heard.handled and fake.calls == []


async def test_the_model_only_fills_an_unread_clause_from_the_mac_actions(registered):
    asked = []

    async def choose(text, *, principal, only):
        asked.append((text, principal, only))
        return "mac.play_music", {"query": "lofi"}

    fake = FakeExecutor()
    heard = await spoken.run_spoken_commands("open Notes and play some lofi",
                                             execute=fake, choose=choose)
    assert [(r.action, r.params) for r in fake.calls] == [
        ("mac.open_app", {"app": "Notes"}), ("mac.play_music", {"query": "lofi"})]
    (text, principal, only), = asked
    assert text == "play some lofi" and principal
    assert only == spoken.speakable_names()
    assert "mac.send_imessage" not in only and "mac.run_shortcut" not in only
    assert all(n.startswith("mac.") for n in only)
    assert heard.handled


@pytest.mark.parametrize("pick", [None, ("mac.send_imessage", {"to": "Mum", "text": "x"}),
                                  ("gitlab.open_issue", {}), ("mac.notify", "x")])
async def test_a_clause_the_model_cannot_place_runs_nothing(registered, pick):
    async def choose(*_a, **_k):
        return pick

    fake = FakeExecutor()
    heard = await spoken.run_spoken_commands("open Notes and play some lofi",
                                             execute=fake, choose=choose)
    assert fake.calls == [] and heard.ask and "play some lofi" in heard.ask


async def test_a_tier_2_mac_action_never_runs_from_the_chain(registered):
    fake = FakeExecutor()
    ran = await spoken.run_chain(
        [Step("mac.send_imessage", {"to": "Mum", "text": "hi"}, "text Mum hi")],
        "text Mum hi", execute=fake)
    assert fake.calls == [] and not ran[0].ok


async def test_through_the_real_executor_a_new_note_reaches_the_mac(registered):
    registered.responses["SELECT max(last_seen) AS at FROM mac_agent"] = {
        "at": datetime.now(UTC) - timedelta(seconds=3)}
    registered.responses["INSERT INTO mac_jobs"] = {"id": 4}
    registered.responses["SELECT status, ok, detail, token FROM mac_jobs"] = {
        "status": "done", "ok": True, "detail": "created 'hello' in Notes",
        "token": '{"id": "n1", "title": "hello"}'}
    heard = await spoken.run_spoken_commands("new note hello", choose=_no_model)
    assert heard.ran[0].ok and heard.line == "Created 'hello' in Notes"


# ------------------------------------------------------------------ the route

@pytest.fixture
def client(monkeypatch, memory_backend):
    from fastapi.testclient import TestClient

    from atlas.api import auth
    monkeypatch.setenv("SESSION_SECRET", "v" * 40)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password("pw-long-enough"))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "127.0.0.0/8")
    auth._attempts.clear()
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("127.0.0.1", 52000)) as c:
        yield c


def test_the_route_needs_a_signed_in_session(client):
    assert client.post("/api/voice/command",
                       json={"transcript": "open Notes"}).status_code == 401


def test_the_route_runs_the_fast_path(client, monkeypatch):
    client.post("/api/login", json={"password": "pw-long-enough"})
    seen = []

    async def run(transcript):
        seen.append(transcript)
        return spoken.Heard(True, "Notes is open",
                            ran=(spoken.Ran("mac.open_app", "open Notes", True,
                                            "Notes is open"),))
    monkeypatch.setattr(spoken, "run_spoken_commands", run)
    body = client.post("/api/voice/command",
                       json={"transcript": "  open   Notes "}).json()
    assert seen == ["open Notes"]
    assert body["handled"] and body["answer"] == "Notes is open"
    assert body["ran"] == [{"action": "mac.open_app", "said": "open Notes",
                            "ok": True, "detail": "Notes is open"}]


def test_the_route_says_when_it_is_not_a_mac_command(client, monkeypatch):
    client.post("/api/login", json={"password": "pw-long-enough"})

    async def run(_t):
        return spoken.Heard(False, "")
    monkeypatch.setattr(spoken, "run_spoken_commands", run)
    body = client.post("/api/voice/command",
                       json={"transcript": "how are you"}).json()
    assert body["handled"] is False


@pytest.mark.parametrize("said", ["open Air Glass", "Atlas, open airglass please",
                                  "show me air glass"])
def test_open_air_glass_tells_the_menu_bar_to_open_its_window(client, monkeypatch, said):
    """2026-10-06: after the ⌘-tap voice, "open Air Glass" opens ATLAS Menu's
    own Air Glass window. Nothing runs: the Mac command parser (which would
    read "Air Glass" as an app) and the executor are never reached."""
    client.post("/api/login", json={"password": "pw-long-enough"})

    async def run(_t):
        raise AssertionError("a window is not a Mac command")
    monkeypatch.setattr(spoken, "run_spoken_commands", run)
    body = client.post("/api/voice/command", json={"transcript": said}).json()
    assert body["handled"] is True
    assert body["window"] == "airglass"
    assert body["ran"] == []
    assert body["answer"] == "Opening Air Glass."


def test_open_air_glass_is_a_dashboard_view():
    from atlas.api import voice
    assert voice.view_target("open Air Glass") == {"screen": "/airglass"}
    assert voice.view_target("open air glass and delete the logs") is None


@pytest.mark.parametrize("payload", [{"transcript": "   "}, {}, ["open Notes"]])
def test_the_route_refuses_nothing_said(client, payload):
    client.post("/api/login", json={"password": "pw-long-enough"})
    assert client.post("/api/voice/command", json=payload).status_code == 400
