"""The series engine (Miro, 2026-09-27/28): planned whole, made in order,
finished with THE END -- never left on 'Part 2' forever."""
from __future__ import annotations

import json

from atlas.kernel import reels as day
from atlas.kernel.llm import Completion
from atlas.modules.content import calendar as cal
from atlas.modules.content import guard, reel_script, series

HOSPITAL = series.SEEDS[0]


def test_the_hospital_series_is_registered_as_made_and_clean():
    """Rendered by hand Sep 28 - Oct 2 (relayed 2026-09-28): never remade."""
    assert HOSPITAL["title"] == "Emergency Room: Code Chaos"
    assert HOSPITAL["status"] == "done" and len(HOSPITAL["outline"]) == 5
    text = "\n".join([HOSPITAL["characters"], *HOSPITAL["outline"]])
    assert guard.refusal(text) is None and guard.ip_refusal(text) is None
    assert "St. Nowhere General" in HOSPITAL["characters"]


def test_the_handed_over_series_are_all_in_clean_and_in_order():
    """Miro chose ATLAS's pipeline (2026-09-28): the engine-setup session's
    14 written series, 60 episodes, are the engine's queue, in their order."""
    pk = series.package()
    assert len(pk) == 16 and sum(len(s["parts"]) for s in pk) == 67
    assert pk[0]["title"] == "The Parking Spot"
    assert pk[1]["title"] == "Halo of Coins" and pk[1]["position"] == 10, \
        "Miro's parody goes next, right after the active series"
    assert pk[2]["title"] == "The Sand Contract" and pk[2]["position"] == 10, \
        "ties with Halo of Coins, which was taken in first, so it follows it"
    assert pk[3]["title"] == "The Return"
    for item in pk:
        text = "\n".join([item["cast"]] + [b["scene"] + " " + b["caption_en"]
                                            for p in item["parts"] for b in p["beats"]])
        assert guard.refusal(text) is None and guard.ip_refusal(text) is None
        assert all(len(p["beats"]) == 8 for p in item["parts"])
        assert [p["part"] for p in item["parts"]] == list(range(1, len(item["parts"]) + 1))


def test_a_written_beat_keeps_its_scene_for_veo_and_its_english_for_captions():
    item = next(s for s in series.package() if s["title"] == "The Return")
    beats = [series.scripted_beat(b) for b in item["parts"][0]["beats"]]
    body = reel_script.compose(f"{item['title']} ({series.format_of(item)}) — "
                               f"Part 1 of 6", beats, item["parts"][0]["caption"],
                               item["cast"])
    script = reel_script.parse(body, max_scenes=20)
    assert script.scripted and script.dialogue and script.end_card == "PART 1"
    speaker, line = script.exchanges()[0][0]
    assert speaker == "Adham" and not any("\u0600" <= c <= "\u06ff" for c in line)
    from atlas.modules.content import render
    prompt = render.beat_prompt(script.character_sheet(), 1, 8, script.scenes[0],
                                arabic=True, dialogue=True)
    assert "الليلة دي لشراكتنا" in prompt and "no narrator" in prompt
    assert render.STYLE in prompt and render.MOTION in prompt


async def test_a_written_part_is_filed_word_for_word_without_a_model(
        memory_backend, monkeypatch):
    item = series.package()[0]
    row = {"id": 2, "title": item["title"], "format": series.format_of(item),
           "characters": item["cast"], "outline": json.dumps(["a", "b", "c"]),
           "total_parts": 3, "rules": "", "status": "active",
           "scripts": json.dumps(item["parts"])}
    memory_backend.responses["FROM reel_series WHERE id"] = row
    memory_backend.responses["FROM reel_series_parts"] = []
    memory_backend.responses["INSERT INTO content_drafts"] = {"id": 61}

    async def no_model(*a, **k):
        raise AssertionError("a written part never asks a model")

    monkeypatch.setattr(series, "think", no_model)
    result = await series.draft_part(2)
    assert result.ok and "from its script" in result.detail
    assert len(result.evidence["scenes"]) == 8


async def test_a_series_planned_here_first_takes_the_scripts_and_drops_its_draft(
        memory_backend):
    memory_backend.responses["UPDATE reel_series SET scripts"] = [{"id": 2}]
    memory_backend.responses["WHERE NOT EXISTS (SELECT 1 FROM reel_series"] = "OK"
    await series.ensure_seeds()
    sql = [s for s, _ in memory_backend.sql_log]
    assert any("UPDATE content_drafts SET status = 'dropped'" in s for s in sql)
    assert any("DELETE FROM reel_series_parts" in s and "'drafted'" in s for s in sql)


def test_the_end_card_says_part_n_and_the_finale_says_the_end():
    part = reel_script.parse(reel_script.compose(
        "Emergency Room: Code Chaos (Egyptian Arabic hospital ER comedy series) "
        "— Part 2 of 5", ["Kareem: Hi."], "x", "cast"))
    final = reel_script.parse(reel_script.compose(
        "Emergency Room: Code Chaos — Part 5 of 5", ["Hoda: Bye."], "x", "cast"))
    assert part.end_card == "PART 2" and final.end_card == "THE END"


def _series_row():
    return {"id": 4, "title": HOSPITAL["title"], "format": HOSPITAL["format"],
            "characters": HOSPITAL["characters"],
            "outline": json.dumps(HOSPITAL["outline"]), "total_parts": 5,
            "rules": HOSPITAL["rules"], "status": "active"}


async def test_a_part_waits_until_the_part_before_it_posted(memory_backend):
    memory_backend.responses["FROM reel_series WHERE id"] = _series_row()
    memory_backend.responses["FROM reel_series_parts"] = [
        {"part_no": 1, "draft_id": 10, "media_id": 20, "status": "carded"}]
    result = await series.draft_part(4)
    assert not result.ok and "part 1 has not posted yet" in result.detail


async def test_the_finale_is_drafted_as_a_real_ending(memory_backend, monkeypatch):
    memory_backend.responses["FROM reel_series WHERE id"] = _series_row()
    memory_backend.responses["FROM reel_series_parts"] = [
        {"part_no": i, "draft_id": i, "media_id": i, "status": "posted"}
        for i in range(1, 5)]
    memory_backend.responses["INSERT INTO content_drafts"] = {"id": 55}
    seen = []

    async def fake_think(need, system, messages, max_tokens=0, **kw):
        seen.append((system, messages[0].content))
        return Completion(text=json.dumps({
            "scenes": [f"Hoda: سطر {i}. | Kareem: رد {i}." for i in range(9)],
            "caption": "Sayed goes home"}), provider="t", model="t")

    monkeypatch.setattr(series, "think", fake_think)
    result = await series.draft_part(4)
    assert result.ok, result.detail
    assert result.evidence["final"] is True and result.evidence["part"] == 5
    assert result.evidence["caption"].startswith("Final part:")
    system, ask = seen[0]
    assert "FINALE" in system and "no cliffhanger" in system
    assert "Egyptian Arabic" in system and "no narrator" in system
    assert "THIS is part 5 of 5" in ask and HOSPITAL["characters"] in ask
    body = next(a for sql, a in memory_backend.sql_log
                if "INSERT INTO content_drafts" in sql)[2]
    assert reel_script.parse(body).end_card == "THE END"


async def test_a_drafted_part_waits_for_a_whole_days_clips_without_wasting_leftovers(
        memory_backend, monkeypatch):
    async def nothing():
        return None

    async def current():
        return _series_row()

    async def parts(sid):
        return [{"part_no": 1, "draft_id": 10, "media_id": None, "status": "drafted"}]

    async def seven(now=None):
        return 7

    async def not_recent():
        return False

    body = reel_script.compose("x — Part 1 of 5", [f"A: {i}." for i in range(9)],
                               "c", "cast")
    memory_backend.responses["FROM content_drafts WHERE id"] = {"body": body}
    monkeypatch.setattr(series, "ensure_seeds", nothing)
    monkeypatch.setattr(day, "_sync_series_cards", nothing)
    monkeypatch.setattr(series, "current", current)
    monkeypatch.setattr(series, "parts", parts)
    monkeypatch.setattr(cal, "clips_today", seven)
    monkeypatch.setattr(day, "_series_rendered_recently", not_recent)
    lines, hold = await day.series_step()
    assert hold is False and "waiting for 9 Veo clips" in lines[0], \
        "7 already spent: the 3 left go to the packages"


async def test_series_and_regular_reels_alternate_days(memory_backend, monkeypatch):
    async def nothing():
        return None

    async def current():
        return _series_row()

    async def parts(sid):
        return [{"part_no": 2, "draft_id": 11, "media_id": None, "status": "drafted"}]

    async def recent():
        return True

    async def never(req):
        raise AssertionError("no second series render within the day")

    monkeypatch.setattr(series, "ensure_seeds", nothing)
    monkeypatch.setattr(day, "_sync_series_cards", nothing)
    monkeypatch.setattr(series, "current", current)
    monkeypatch.setattr(series, "parts", parts)
    monkeypatch.setattr(day, "_series_rendered_recently", recent)
    monkeypatch.setattr(day, "execute", never)
    lines, hold = await day.series_step()
    assert hold is False and "alternating" in lines[0]


async def test_a_posted_part_is_recorded_and_the_finale_closes_the_series(
        memory_backend):
    await series.mark(4, 5, "posted", 99)
    sql = [s for s, _ in memory_backend.sql_log]
    assert any("UPDATE reel_series_parts SET status" in s for s in sql)
    assert any("UPDATE reel_series SET status = 'done'" in s for s in sql)


async def test_a_new_series_is_planned_whole_with_an_ending(memory_backend, monkeypatch):
    memory_backend.responses["INSERT INTO reel_series"] = {"id": 8}

    async def fake_think(need, system, messages, max_tokens=0, **kw):
        assert "real ending" in system
        return Completion(text=json.dumps({
            "title": "Crisper Drawer Feud", "characters": "Tomo, a tomato; Lin, a leek",
            "parts": ["a", "b", "c", "d"]}), provider="t", model="t")

    monkeypatch.setattr(series, "think", fake_think)
    result = await series.plan_series("talking fruit and vegetable brainrot saga")
    assert result.ok and "4 parts" in result.detail


async def test_a_plan_that_copies_a_film_is_refused(memory_backend, monkeypatch):
    async def fake_think(need, system, messages, max_tokens=0, **kw):
        return Completion(text=json.dumps({
            "title": "Pixar-style race", "characters": "a rusty tow truck with a hook",
            "parts": ["a", "b", "c"]}), provider="t", model="t")

    monkeypatch.setattr(series, "think", fake_think)
    result = await series.plan_series("rival cars family drama")
    assert not result.ok and "refused" in result.detail


def test_atlas_has_a_reels_producer_that_knows_the_plan_and_cannot_post():
    from atlas.kernel import agents
    from atlas.kernel.skills import skills_for
    agents.build_roster()
    producer = agents.ROSTER["reels"]
    assert "no narrator" in producer.system.lower() or "no narrator," in producer.system
    assert "10 clips a day" in producer.system and "never post" in producer.system
    assert not any("post" in t or "publish" in t for t in producer.tools)
    assert "growth" in producer.handoff and "reels" in agents.ROSTER["growth"].handoff
    import atlas.skills.reels  # noqa: F401  (registers the pack)
    names = [s.name for s in skills_for("what is the next reels series part?", "reels")]
    assert "The reels plan" in names


def test_the_workflows_tab_draws_the_reels_pipeline():
    from atlas.kernel import workflows
    wf = next(w for w in workflows.WORKFLOWS if w.id == "reels_pipeline")
    ids = {n.id.split(".", 1)[1] for n in wf.nodes}
    assert {"series", "packages", "autopost", "held", "receipt"} <= ids
