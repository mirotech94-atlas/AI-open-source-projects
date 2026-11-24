"""ATLAS Academy labs and the reference-video rules (docs/academy-youtube-plan.md
rules 9-13, docs/academy-reference-learnings.md, Miro 2026-10-11: "learn from
them, add improvements to your own"): a hands-on lab ending every n8n lesson,
its file a free download from the store, a "what you need" line, and the first
hands-on step within 90 s of a build episode."""
from __future__ import annotations

import ast
import json
import os
import re
from datetime import UTC, datetime
from itertools import pairwise

import pytest

from atlas.modules.academy import site
from atlas.modules.academy import tutorial_design as design
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorials as tut
from atlas.modules.academy.catalog import load
from atlas.modules.agency import hosting

SCRIPTED = [(s, e) for s in ts.SERIES for e in s.episodes if e.scripted]
LESSONS = [(s, e) for s, e in SCRIPTED if s.playlist]
LABS = [(s, e, sc) for s, e in SCRIPTED for sc in e.scenes if sc.kind == "lab"]
SLOW_WPS = tut.FISH_WORDS_PER_SECOND * 0.9
# Every node a lab may use: credential-free, so an import runs as it is.
LAB_NODE_TYPES = {"n8n-nodes-base.manualTrigger", "n8n-nodes-base.webhook",
                  "n8n-nodes-base.scheduleTrigger", "n8n-nodes-base.httpRequest",
                  "n8n-nodes-base.set"}
# 2026-10-25 (Miro): the silent-failure watch is a watcher, not an exercise
# that runs on import: it needs the n8n API and a Slack credential picked
# after import (neither is embedded -- the checks below still hold), so it
# may also use these. Named per file: every other lab stays credential-free.
LAB_EXTRA_NODE_TYPES = {
    "silent-failure-watch.json": {"n8n-nodes-base.code", "n8n-nodes-base.if",
                                  "n8n-nodes-base.noOp", "n8n-nodes-base.slack",
                                  "n8n-nodes-base.stickyNote"},
}


# ------------------------------------------------------------- the lab scene

def test_the_lab_is_a_list_scene_with_four_beats():
    assert "lab" in ts.SCENE_KINDS and "lab" in ts.LIST_KINDS
    assert "lab" not in ts.BEAT_KINDS
    assert ts.LAB_LABELS == ("Do", "Expect", "Stuck?", "Challenge")
    assert LABS
    for _s, ep, sc in LABS:
        assert sc.chapter, ep.slug                    # a chapter of its own
        assert len(sc.items) == len(sc.said) == len(ts.LAB_LABELS), ep.slug
        assert sc.resource in ts.LAB_FILES, sc.resource
        assert sc.resource in sc.items[0]             # "Import <file>" on screen
        assert "description" in sc.said[0]           # said where to get it


def test_every_n8n_lesson_ends_with_a_lab_before_the_recap():
    n8n = [e for s, e in LESSONS if s.key == "n8n"]
    assert [e.number for e in n8n] == [0, 1, 2, 3, 4, 5, 6, 7]
    for ep in n8n:
        chapters = [sc for sc in ep.scenes if sc.chapter]
        assert chapters[-1].kind == "checklist" and chapters[-1].chapter == "Recap"
        assert chapters[-2].kind == "lab", ep.slug
        hook = " ".join(ep.hook.said).lower()
        assert "lab" in hook, ep.slug                 # the hook promises it


def test_a_lab_slide_reveals_one_beat_per_step_and_names_the_download():
    sc = next(sc for _s, e, sc in LABS if e.slug == "n8n-first-workflow")
    first = design.slide(sc, 0, mark="m", chapter=0, chapters=1)
    assert first.count('class="hidden"') == len(sc.items) - 1
    assert "agency.storefront.test/academy/free/n8n-city-info.json" in first
    assert "Stuck?" in first and design.STORE_SHOWN in first
    assert design.TAG not in first        # Miro 2026-10-17: off screen; disclosed via containsSyntheticMedia + description
    last = design.slide(sc, len(sc.items) - 1, mark="m", chapter=0, chapters=1)
    assert 'class="hidden"' not in last and "Challenge" in last


def test_a_lab_stands_on_the_code_loop_and_joins_no_beat():
    sc = LABS[0][2]
    assert design.scene_topic(sc, "n8n") == "code"
    assert design.scene_topic(sc, "n8n") in design.TOPICS


# ------------------------------------------------------------- the lab files

@pytest.mark.parametrize("name", [n for n in ts.LAB_FILES if n.endswith(".json")])
def test_every_lab_workflow_imports_with_no_credential(name):
    wf = json.loads(ts.LAB_FILES[name])
    names = [n["name"] for n in wf["nodes"]]
    assert len(names) == len(set(names)) >= 2
    allowed = LAB_NODE_TYPES | LAB_EXTRA_NODE_TYPES.get(name, set())
    assert {n["type"] for n in wf["nodes"]} <= allowed, name
    assert "credentials" not in ts.LAB_FILES[name]
    assert not re.search(r"(?i)(api[_-]?key|token|secret|password|bearer)", ts.LAB_FILES[name])
    for src, out in wf["connections"].items():
        assert src in names
        assert all(link["node"] in names for branch in out["main"] for link in branch)
    for node in wf["nodes"]:
        if node["type"] == "n8n-nodes-base.set":      # v3.3+: "Fields to Set"
            assert node["typeVersion"] >= 3.3
            assert node["parameters"]["assignments"]["assignments"]
    assert ts.LAB_FILES[name].endswith("}\n")


def test_the_city_lab_is_the_demo_without_slack():
    lab = json.loads(ts.LAB_FILES["n8n-city-info.json"])
    shown = json.loads(ts.N8N_WORKFLOW_JSON)
    assert lab["nodes"][:2] == shown["nodes"][:2]       # Webhook, HTTP Request as taught
    assert "n8n-nodes-base.slack" not in {n["type"] for n in lab["nodes"]}
    text = lab["nodes"][2]["parameters"]["assignments"]["assignments"][0]["value"]
    assert "$json.results[0].name" in text and "$json.results[0].country" in text


def test_the_schedule_lab_runs_every_day_at_nine():
    wf = json.loads(ts.LAB_FILES["n8n-every-day-at-nine.json"])
    rule = wf["nodes"][0]["parameters"]["rule"]["interval"][0]
    assert rule == {"field": "days", "triggerAtHour": 9}


def test_the_silent_failure_watch_alerts_once_and_says_when_it_is_back():
    from atlas.modules.academy.tutorial_scripts import n8n_04
    name = "silent-failure-watch.json"
    text = ts.LAB_FILES[name]
    assert text == n8n_04.WATCH_JSON + "\n"
    wf = json.loads(text)
    by = {n["name"]: n for n in wf["nodes"]}
    work = [n for n in wf["nodes"] if n["type"] != "n8n-nodes-base.stickyNote"]
    assert work[0]["type"] == "n8n-nodes-base.scheduleTrigger"
    # The one field to fill in, clearly marked, in the Settings node.
    sets = {a["name"]: a["value"]
            for a in by["Settings"]["parameters"]["assignments"]["assignments"]}
    assert sets["workflowId"] == n8n_04.WATCH_ID_PLACEHOLDER
    assert sets["maxAgeMinutes"] > 0
    assert "example.com" in sets["baseUrl"]
    # The n8n API: the newest execution of that one workflow.
    http = by["Last run"]["parameters"]
    assert http["url"].endswith("/api/v1/executions")
    assert http["authentication"] == "predefinedCredentialType"
    assert http["nodeCredentialType"] == "n8nApi"
    query = {p["name"]: p["value"] for p in http["queryParameters"]["parameters"]}
    assert query == {"workflowId": "={{ $json.workflowId }}", "limit": "1"}
    # One alert, then quiet; one "back": the state in workflow static data.
    code = by["Overdue?"]["parameters"]["jsCode"]
    assert "$getWorkflowStaticData('global')" in code
    assert "state.alerted = true" in code and "state.alerted = false" in code
    assert "Date.parse(last.startedAt)" in code      # a failed run still started
    assert "ageMinutes === null" in code             # no run at all is overdue
    # IF: something to say goes to Slack, nothing to say stays quiet.
    out = wf["connections"]["Anything to say?"]["main"]
    assert [b[0]["node"] for b in out] == ["Tell me in Slack", "Stay quiet"]
    assert by["Tell me in Slack"]["parameters"]["text"] == "={{ $json.message }}"
    chain = ["Every 15 minutes", "Settings", "Last run", "Overdue?",
             "Anything to say?"]
    for a, b in pairwise(chain):
        assert wf["connections"][a]["main"][0][0]["node"] == b
    notes = [n for n in wf["nodes"] if n["type"] == "n8n-nodes-base.stickyNote"]
    assert len(notes) >= 4 and all(n["parameters"]["content"] for n in notes)
    # Placeholders only: no real secret shapes, hosts or addresses.
    assert not re.search(r"eyJ[A-Za-z0-9_-]{10,}|xox[abpr]-|[A-Fa-f0-9]{32,}"
                         r"|[A-Za-z0-9+/]{40,}|@[a-z0-9-]+\.[a-z]", text)
    hosts = set(re.findall(r"https?://([^/\s\"]+)", text))
    assert hosts == {"your-n8n.example.com"}


def test_n8n_4_links_the_watch_as_a_second_download():
    ep = ts.series("n8n").episode(4)
    assert ts.downloads_of(ep) == ("n8n-when-a-step-fails.json",
                                   "silent-failure-watch.json")
    watch = next(sc for sc in ep.scenes if sc.chapter == "The failure nothing catches")
    assert watch.kind == "steps" and len(watch.items) == len(watch.said)
    assert "description" in watch.said[-1]
    chapters = [sc for sc in ep.scenes if sc.chapter]
    assert chapters.index(watch) == len(chapters) - 3      # then the lab, the recap


def test_the_mcp_lab_file_is_the_server_on_screen():
    code = ts.LAB_FILES["mcp-hello-server.py"]
    assert code == ts.MCP_SERVER_PY + "\n"
    ast.parse(code)
    assert "print(" not in code


# ------------------------------------------------- served by the store itself

def test_the_store_serves_every_lab_file_byte_for_byte():
    files = site.render_root(load(), datetime(2026, 10, 11, tzinfo=UTC))
    for name, text in ts.LAB_FILES.items():
        assert files[f"{ts.LAB_DIR}/{name}"] == text.encode()
    assert ts.LAB_DIR not in {p.slug for p in load().products}
    assert ts.lab_url("x.json") == f"{ts.STORE_URL}/free/x.json"


def test_every_download_a_description_links_is_a_file_the_store_builds():
    files = site.render_root(load(), datetime(2026, 10, 11, tzinfo=UTC))
    for s, ep in SCRIPTED:
        text = tut.description(s, ep, [(0.0, "Intro"), (30.0, "A"), (90.0, "B")], 5)
        for url in re.findall(rf"{re.escape(ts.STORE_URL)}/{ts.LAB_DIR}/\S+", text):
            assert url.removeprefix(f"{ts.STORE_URL}/") in files, url
        for name in ts.downloads_of(ep):
            assert ts.lab_url(name) in text
    assert ts.downloads_of(ts.series("claude").episode(1)) == ("mcp-hello-server.py",)


# ------------------------------------------------ rule 10: what you need

def test_every_lesson_says_what_you_need_out_loud_and_in_writing():
    for s, ep in LESSONS:
        if s.key in ("project", "business"):
            continue                  # not in this round's episodes (2026-10-11)
        assert ep.needs, ep.slug
        assert re.search(r"\b(need|nothing to install|no install)\b",
                         " ".join(ep.hook.said).lower()), ep.slug
        text = tut.description(s, ep, [(0.0, "Intro"), (30.0, "A"), (90.0, "B")], 5)
        # 2026-10-18: the result, then what you need, are the first two lines
        assert text.startswith(f"What you'll have: {ts.lead_lower(ts.result_of(ep))}.\n"
                               f"What you need: {'; '.join(ep.needs)}.\n\n"
                               f"{ep.level_label} · 5 minutes\n{ep.summary}")
        assert tut.description_refusal(text) is None


# ------------------------------------------- rule 9: hands-on within 90 s

def test_a_build_episode_reaches_its_first_hands_on_step_within_90_seconds():
    builds = [(s, e) for s, e in LESSONS if e.formula_level == 1]
    assert {(s.key, e.number) for s, e in builds} >= {("claude", 1), ("claude", 2),
                                                       ("n8n", 1), ("n8n", 2)}
    for s, ep in builds:
        at = tut.hands_on_at(ep, wps=SLOW_WPS)
        assert at is not None and at <= tut.HANDS_ON_SECONDS, (s.key, ep.number, at)


def test_the_hands_on_clock_counts_the_result_sting_and_scope():
    """2026-10-17: the opening is the result (6 s), the sting (1 s) and the
    scope line; the hands-on clock starts counting at 0:00 under all of it."""
    ep = ts.series("n8n").episode(1)
    at = tut.hands_on_at(ep, wps=SLOW_WPS)
    scope = ts.opening_lines(ep)[1]
    assert at > (design.RESULT_SECONDS + design.STING_SECONDS
                 + len(scope.split()) / (SLOW_WPS * tut.voice_speed()))
    assert tut.hands_on_at(ts.series("agents").episode(1)) is None   # an explainer


# --------------------------------------- the publish gate: the store has them

def _live_store(root, files: dict[str, str]) -> None:
    stamp = "20261011T000000000000Z"
    rel = root / hosting.RELEASES / "shop" / stamp
    for name, text in files.items():
        (rel / ts.LAB_DIR).mkdir(parents=True, exist_ok=True)
        (rel / ts.LAB_DIR / name).write_text(text)
    os.symlink(f"{hosting.RELEASES}/shop/{stamp}", root / "shop")


def test_a_video_with_a_lab_waits_until_the_live_store_serves_it(tmp_path, monkeypatch):
    monkeypatch.setattr(hosting, "PUBLISH_ROOT", tmp_path)
    ep = ts.series("n8n").episode(1)
    assert "does not serve" in tut.downloads_refusal(ep)            # nothing live
    _live_store(tmp_path, {"n8n-city-info.json": "{}\n"})
    assert "does not serve" in tut.downloads_refusal(ep)            # stale bytes


def test_a_video_with_a_lab_publishes_once_the_store_has_the_same_bytes(tmp_path,
                                                                         monkeypatch):
    monkeypatch.setattr(hosting, "PUBLISH_ROOT", tmp_path)
    _live_store(tmp_path, {"n8n-city-info.json": ts.LAB_FILES["n8n-city-info.json"]})
    assert tut.downloads_refusal(ts.series("n8n").episode(1)) is None
    assert tut.downloads_refusal(ts.series("agents").episode(1)) is None  # no lab


async def test_a_render_of_an_older_script_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(tut, "episode_dir", lambda *_: tmp_path)
    monkeypatch.setattr(tut, "publishable_render", lambda _p: None)
    monkeypatch.setattr(tut, "read_manifest", lambda _d: tut.Manifest(
        series="n8n", episode=1, audio="fish", seconds=300.0,
        chapters=[(0.0, "Intro"), (60.0, "A"), (120.0, "B")], script="0" * 16))
    got, why = await tut.ready("n8n", 1)
    assert got is None and "older script" in why
