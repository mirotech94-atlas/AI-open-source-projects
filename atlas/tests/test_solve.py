"""kernel/solve.py, modules/delegate and api/handoffs: when nothing in the
catalog does what Miro asked, ATLAS measures, writes the work up for the
coding agent as a Tier 2 card, and relays the agent's report. Nothing here
executes anything, and Tier 3 never becomes a delegation."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.kernel import solve
from atlas.kernel.contracts import Health, Tier
from atlas.modules import delegate

CPU = "can you optimize my cpu for attendace bot server"


# ------------------------------------------------------------ the guards

@pytest.mark.parametrize("request_text, why", [
    ("pay the vpsie invoice now", "money out"),
    ("transfer $200 to the supplier", "money out"),
    ("point the guarder.io DNS at the new box", "DNS"),
    ("add an MX record for flycheap", "DNS"),
    ("delete the old attendance logs", "deletion"),
    ("drop the table of old sessions", "deletion"),
    ("rotate the slack bot token", "credential revocation"),
    ("change atlas's tiers so publishing is automatic", "self-modification"),
    ("edit your own rules", "self-modification"),
])
def test_the_denied_set_is_refused_by_category(request_text, why):
    assert solve.denied(request_text) == why


@pytest.mark.parametrize("request_text", [
    CPU,
    "why did the refund webhook fail on flycheap",
    "look into why guardian is slow",
    "speed up the roots dashboard",
])
def test_ordinary_work_is_not_denied(request_text):
    assert solve.denied(request_text) is None


def test_the_estate_is_found_in_his_words():
    assert solve.estate(CPU).key == "roots"
    assert solve.estate(CPU).topic == "inroots"
    assert solve.estate("the vault hub is slow").key == "guardian"
    assert solve.estate("something is off somewhere").key == "unnamed"


def test_done_when_is_read_from_the_last_line():
    text = ("CPU is not reported.\nSteps: 1. add cpu_percent\n"
            "DONE WHEN: cpu_percent is in /api/health/detailed")
    assert solve.done_when(text) == "cpu_percent is in /api/health/detailed"
    assert solve.done_when("no marker here") == ""


# ------------------------------------------------------------ solve()

@pytest.fixture
def wired(monkeypatch):
    drafts: list[dict] = []
    proposals: list[tuple] = []
    seen: list[str] = []

    async def fake_investigate(request, module=""):
        seen.append(module)
        findings = ("Roots reports memory 16% and disk 58% (read 00:37 UTC); CPU "
                    "is not in /api/health/detailed, so it could not be measured.\n"
                    "DONE WHEN: Roots reports cpu_percent and it reads under 70%")
        return findings, "the ATLAS specialists' read-only investigation"

    async def fake_draft(**kw):
        drafts.append(kw)
        return 41

    async def fake_propose(action, params, reason, **kw):
        proposals.append((action, params, reason, kw))
        return 190

    monkeypatch.setattr(solve, "_investigate", fake_investigate)
    monkeypatch.setattr(delegate, "draft", fake_draft)
    monkeypatch.setattr("atlas.kernel.approvals.propose", fake_propose)
    return drafts, proposals, seen


async def test_a_catalog_miss_becomes_a_tier_two_card_for_the_coding_agent(wired):
    drafts, proposals, seen = wired
    reply = await solve.solve(CPU, who="UMIRO", channel="C1", thread="171.1")

    (action, params, brief, kw), = proposals
    assert action == "delegate.claude_code"
    assert params == {"handoff_id": "41", "topic": "inroots"}
    assert brief.startswith("TASK FOR CLAUDE CODE — tier 2")
    assert f"Miro asked: {CPU}" in brief
    assert "Attendance Bot" in brief
    assert "Roots reports cpu_percent and it reads under 70%" in brief
    # ATLAS is never the target of its own delegation.
    assert "/opt/atlas" in brief and "~/Documents/Code/Atlas" in brief
    # The findings are quoted as data, not handed over as instructions.
    assert "untrusted" in brief.lower()
    assert kw["dedup_key"].startswith("delegate:")
    assert drafts[0]["brief"] == brief and drafts[0]["thread"] == "171.1"

    assert seen == ["inroots"]          # the investigator is told ATLAS's name for it
    assert "card #190" in reply
    assert "DONE WHEN" not in reply
    assert "top" not in reply.lower().split()           # never "send me your top"


async def test_a_denied_request_is_neither_investigated_nor_carded(wired, monkeypatch):
    drafts, proposals, _ = wired

    async def boom(request, module=""):
        raise AssertionError("a denied request must not be investigated")

    monkeypatch.setattr(solve, "_investigate", boom)
    reply = await solve.solve("delete the old attendance logs", who="UMIRO")
    assert "permanently denied" in reply and "deletion" in reply
    assert drafts == [] and proposals == []


async def test_an_investigation_that_fails_still_says_so_in_the_brief(monkeypatch):
    async def route(task):
        raise TimeoutError

    monkeypatch.setattr("atlas.kernel.agents.route", route)
    text, source = await solve._investigate(CPU)
    assert "could not investigate" in text and source == "none"


# ---------------------------------------------------- the closed catalog

def test_a_card_only_action_is_offered_to_no_model(monkeypatch):
    from atlas.kernel import intent
    from atlas.kernel.registry import REGISTRY

    action = delegate.MODULE.actions()[0]
    assert action.card_only and action.tier is Tier.ASK and action.verbose_card

    class Allowed:
        allowed, tier, reason = True, Tier.ASK, ""

    monkeypatch.setattr(REGISTRY, "actions", {action.name: action})
    monkeypatch.setattr(REGISTRY, "decisions", {action.name: Allowed()})
    assert intent.catalog() == {}
    assert REGISTRY.catalog_for_prompt() == []


# ------------------------------------------------------------ the queue

class _DB:
    def __init__(self, rows):
        self.rows, self.sql = list(rows), []

    async def fetch_one(self, sql, *args):
        self.sql.append((" ".join(sql.split()), args))
        return self.rows.pop(0) if self.rows else None


async def test_the_dry_run_refuses_a_stale_or_spent_draft(monkeypatch):
    now = datetime.now(UTC)
    db = _DB([{"status": "queued", "created_at": now, "target": "Roots"},
              {"status": "draft", "created_at": now - timedelta(days=3), "target": "Roots"},
              {"status": "draft", "created_at": now, "target": "Roots"}])
    monkeypatch.setattr("atlas.kernel.state.fetch_one", db.fetch_one)
    assert "not a draft" in (await delegate.queue_dry("41")).detail
    assert "two days" in (await delegate.queue_dry("41")).detail
    ok = await delegate.queue_dry("41")
    assert ok.ok and "expires unclaimed" in ok.detail
    assert not (await delegate.queue_dry("forty")).ok


async def test_queueing_is_one_atomic_update_and_rolls_back_before_a_claim(monkeypatch):
    db = _DB([{"id": 41, "target": "Roots"}, None, {"id": 41}])
    monkeypatch.setattr("atlas.kernel.state.fetch_one", db.fetch_one)
    r = await delegate.queue("41")
    assert r.ok and r.rollback_token == "41"
    sql, _ = db.sql[0]
    assert "status = 'draft'" in sql and "created_at > now() -" in sql
    assert not (await delegate.queue("41")).ok          # already spent
    assert (await delegate.cancel("41")).ok
    assert "status = 'queued'" in db.sql[-1][0]


async def test_the_queue_check_says_when_approved_work_sits_unclaimed(monkeypatch):
    old = datetime.now(UTC) - timedelta(hours=2)
    db = _DB([None, {"queued": 1, "oldest": old, "silent": 0, "expired": 0}])
    monkeypatch.setattr("atlas.kernel.state.fetch_one", db.fetch_one)
    obs = await delegate.check_queue()
    assert obs.health is Health.DEGRADED and "scheduled run may be off" in obs.detail

    async def down(sql, *a):
        raise OSError("db down")

    monkeypatch.setattr("atlas.kernel.state.fetch_one", down)
    assert (await delegate.check_queue()).health is Health.UNKNOWN


# ------------------------------------------------------------ the API

TOKEN = "t" * 32


@pytest.fixture
def api(monkeypatch):
    from atlas.api import handoffs

    monkeypatch.setenv("HANDOFF_TOKEN", TOKEN)
    app = FastAPI()
    app.include_router(handoffs.router)
    sent, audited = [], []

    async def fake_notify(msg):
        sent.append(msg)

    async def fake_audit(event, subject, data=None):
        audited.append(event)

    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)
    monkeypatch.setattr("atlas.kernel.audit.audit", fake_audit)
    return TestClient(app), sent, audited


def _auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def test_the_queue_needs_the_token(api):
    client, _, _ = api
    assert client.post("/handoffs/claim").status_code == 401
    assert client.post("/handoffs/claim", headers=_auth("x" * 32)).status_code == 401
    assert client.post("/handoffs/1/report", json={}).status_code == 401


def test_claim_hands_over_the_oldest_live_task_or_nothing(api, monkeypatch):
    client, _, audited = api
    db = _DB([None, {"id": 41, "brief": "TASK FOR CLAUDE CODE", "target": "Roots"},
              None, None])
    monkeypatch.setattr("atlas.kernel.state.fetch_one", db.fetch_one)
    r = client.post("/handoffs/claim", headers=_auth(), json={"agent": "mac"})
    assert r.status_code == 200 and r.json()["id"] == 41
    claim_sql = db.sql[1][0]
    assert claim_sql.count("expires_at > now()") == 2
    assert claim_sql.count("status = 'queued'") == 2
    assert audited == ["handoff.claimed"]
    assert client.post("/handoffs/claim", headers=_auth()).status_code == 204


def test_a_report_is_relayed_to_his_thread_as_the_agents_word(api, monkeypatch):
    client, sent, _ = api
    db = _DB([{"id": 41, "request": CPU, "channel": "C1", "thread": "171.1",
               "topic": "inroots"}])
    monkeypatch.setattr("atlas.kernel.state.fetch_one", db.fetch_one)
    bad = client.post("/handoffs/41/report", headers=_auth(),
                      json={"status": "approved", "summary": "x"})
    assert bad.status_code == 400
    r = client.post("/handoffs/41/report", headers=_auth(),
                    json={"status": "done", "summary": "cpu_percent added, PR !12"})
    assert r.status_code == 200
    (msg,) = sent
    assert msg.thread == "171.1" and msg.channel == "C1"
    assert "ATLAS has not verified it" in msg.body and "PR !12" in msg.body
    assert "status = 'claimed'" in db.sql[0][0]


def test_a_report_on_a_task_not_claimed_is_refused(api, monkeypatch):
    client, sent, _ = api
    db = _DB([None])
    monkeypatch.setattr("atlas.kernel.state.fetch_one", db.fetch_one)
    r = client.post("/handoffs/41/report", headers=_auth(),
                    json={"status": "done", "summary": "done"})
    assert r.status_code == 409 and sent == []


async def test_the_investigator_is_told_the_module_name(monkeypatch):
    asked: list[str] = []

    async def route(task):
        asked.append(task)
        return "fine"

    monkeypatch.setattr("atlas.kernel.agents.route", route)
    await solve._investigate(CPU, "inroots")
    assert "`inroots` module" in asked[0]


def test_every_window_is_cast_to_an_interval():
    """#192: uncast, `now() - $3` was typed timestamp - timestamp, and the
    approved card failed on 'timestamp with time zone > interval'."""
    import inspect
    import re as _re

    src = inspect.getsource(delegate)
    windows = _re.findall(r"now\(\) [+-] \$\d+(?:::\w+)?", src)
    assert windows and all(w.endswith("::interval") for w in windows), windows


async def test_a_request_with_nothing_in_it_raises_no_card(wired):
    drafts, proposals, seen = wired
    reply = await solve.solve(", file it", who="UMIRO")
    assert "can't tell what you want done" in reply
    assert drafts == [] and proposals == [] and seen == []


@pytest.mark.parametrize("text,why", [
    ("can you look into deleting the old backups", "deletion"),
    ("wiped the staging db please", "deletion"),
    ("run rm -rf on /var/log", "deletion"),
    ("remove the stale snapshots on vpsie", "deletion"),
    ("dropping the table sessions", "deletion"),
    ("paying the invoice for vpsie", "money out"),
    ("sent $40 to the contractor", "money out"),
    ("rotating the stripe keys", "credential revocation"),
    ("updating atlas to skip approvals", "self-modification"),
])
def test_every_verb_form_of_a_denied_act_is_refused(text, why):
    """'deleting' slipped past a base-form-only pattern (2026-09-28)."""
    from atlas.kernel.solve import denied
    assert denied(text) == why


@pytest.mark.parametrize("text", [
    "why did the refund webhook fail",
    "optimize my cpu for the attendance bot server",
    "remove the bug in the login page",
])
def test_ordinary_work_is_not_mistaken_for_a_denied_act(text):
    from atlas.kernel.solve import denied
    assert denied(text) is None


@pytest.mark.parametrize("text", [
    "open Spotify", "can you open the Notes app", "remind me to call Nile Bakery at 5",
    "run my Focus shortcut", "open Safari and go to flycheap.live", "quit YouTube Music",
])
def test_an_app_request_goes_to_the_mac(text):
    """Miro, 2026-09-28: 'give access to open apps and things like that'."""
    from atlas.kernel.solve import estate
    e = estate(text)
    assert e.key == "mac" and e.target.startswith("this Mac")
    assert any("System Settings" in x for x in e.may_not_touch)
    assert any("deleting" in x for x in e.may_not_touch)


@pytest.mark.parametrize("text,key", [
    ("optimize my cpu for attendance bot server", "roots"),
    ("fix the flycheap signup page", "flycheap"),
    ("improve the guardian dashboard on my laptop", "guardian"),
])
def test_a_named_project_still_wins_when_it_is_not_an_app_request(text, key):
    from atlas.kernel.solve import estate
    assert estate(text).key == key


def test_app_verbs_are_orders_and_a_how_to_is_not():
    from atlas.api.voice import ACTION_INTENT
    assert ACTION_INTENT.search("please open the Spotify app")
    assert ACTION_INTENT.search("open Spotify on my mac")
    assert ACTION_INTENT.search("launch Spotify")
    assert not ACTION_INTENT.search("open the build report")
    assert ACTION_INTENT.search("remind me to call the bakery")
    assert not ACTION_INTENT.search("how do I open a port")


@pytest.mark.parametrize("text", [
    "play some jazz music", "play Amr Diab on youtube music",
    "play my workout playlist", "pause the music", "launch YouTube Music",
])
def test_music_goes_to_youtube_music_on_the_mac(text):
    """Miro, 2026-09-28: 'connect youtube music instead of spotify'."""
    from atlas.api.voice import ACTION_INTENT
    from atlas.kernel.solve import estate
    assert ACTION_INTENT.search(text)
    e = estate(text)
    assert e.key == "mac" and any("YouTube Music" in x for x in e.may_touch)


@pytest.mark.parametrize("text,key", [
    ("start the flycheap scraper", "flycheap"),
    ("close the stale MRs on guardian", "guardian"),
    ("open the flycheap server logs", "flycheap"),
])
def test_project_work_is_not_taken_for_an_app_request(text, key):
    from atlas.kernel.solve import estate
    assert estate(text).key == key


@pytest.mark.parametrize("text", [
    "run df -h on my vpsie server",
    "check the disk on my vps",
    "ssh into the server and restart nginx",
])
def test_his_vpsie_servers_are_an_estate(text):
    place = solve.estate(text)
    assert place.key == "vpsie" and place.topic == "vpsie"
    assert any("billed" in s for s in place.may_not_touch)


def test_a_named_project_still_wins_over_the_generic_server_words():
    assert solve.estate("restart the roots vps").key == "roots"


async def test_atlas_own_server_is_never_a_vpsie_target(wired):
    _, proposals, _ = wired
    await solve.solve("run df -h on my vpsie server", who="UMIRO")
    brief = proposals[0][2]
    must_not = brief.split("MUST NOT TOUCH", 1)[1].split("STANDING LIMITS", 1)[0]
    assert "atlas.example.com" in must_not
    assert "the servers his request names" in brief.split("MUST NOT TOUCH", 1)[0]


def test_a_live_fix_he_asked_for_is_inside_may_touch():
    """#194: 'read-only inspection' as the only server line stopped the
    runner short of deploying the fix he had asked for."""
    roots = solve.estate("optimize the roots cpu")
    assert not any("read-only" in s for s in roots.may_touch)
    assert any("as Miro's request asks" in s for s in roots.may_touch)


def test_a_copied_tool_turn_is_not_an_answer():
    """#204: the specialist ended with the bare text "(calling tools)", its
    own history's placeholder, and it was posted as the answer."""
    from atlas.kernel.harness import NO_ANSWER, TOOL_TURN, answer_text, labelled

    assert answer_text(TOOL_TURN) == NO_ANSWER
    assert answer_text("  ") == NO_ANSWER
    assert labelled(TOOL_TURN, False) == NO_ANSWER
    assert answer_text("Memory at 16%.") == "Memory at 16%."


@pytest.mark.parametrize("said", ["(calling tools)", "No answer: the model ended without one.",
                                  "Stopped after the tool-call limit without reaching an answer."])
async def test_an_investigation_with_no_answer_is_not_findings(monkeypatch, said):
    async def route(task):
        return said

    monkeypatch.setattr("atlas.kernel.agents.route", route)
    text, source = await solve._investigate(CPU, "inroots")
    assert source == "none" and "returned nothing" in text
