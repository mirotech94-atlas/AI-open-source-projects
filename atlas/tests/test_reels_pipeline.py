"""Task 55 (c): research which story formats are working, and draft from the
best of them every day -- proposed, never posted.

What is load-bearing here:

* `content.reel_research` (Tier AUTO) asks Perplexity the fixed question
  under the marketing pass's 90 s deadline, files what it found in
  `reel_formats` with its read time and sources, keeps Miro's seeds there,
  and reads the stories account's own insights only when the token allows --
  UNKNOWN, said in words, when it does not (no instagram_manage_insights).
* Research text is untrusted: it reaches the model wrapped, as data.
* `content.draft_reel` (Tier AUTO) writes one `story_script` draft in the
  reel shape, narration marked for Fish's British voice, ending with the AI
  label, and refuses a script that names a real brand or person.
* The daily step (`kernel.reels.daily`) drafts, renders and proposes at most
  REELS_PER_DAY `content.publish_reel` cards, all through the executor and
  `propose()`; nothing publishes; every figure in the digest has a read time.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest


@pytest.fixture(autouse=True)
def _daily_digest(monkeypatch):
    """These tests read the day's digest message. Miro, 2026-10-06: the
    digest is stored daily and said weekly; REELS_DIGEST=daily keeps the
    daily message for the tests that inspect it."""
    monkeypatch.setenv("REELS_DIGEST", "daily")

from atlas.kernel import reels as day
from atlas.kernel.contracts import ActionResult, Tier
from atlas.kernel.llm import Completion
from atlas.modules import _perplexity, content, instagram
from atlas.modules.content import reel_script
from atlas.modules.content import reels as playbook


def drafting_system_prompt(pb) -> str:
    return pb.DRAFT_SYSTEM

_real_no_platform_line = day.no_platform_line


@pytest.fixture(autouse=True)
def _quality_gate_passes(monkeypatch):
    """These tests are about routing after the file checks; the RA-E5 gate
    itself is tested in tests/test_reels_quality.py."""
    from atlas.modules.content import quality

    async def clean(media_id, caption, *, series, seconds):
        return None

    monkeypatch.setattr(quality, "check_media", clean)


@pytest.fixture(autouse=True)
def _no_calendar(monkeypatch):
    """These tests are the daily rotation's; the 30-day calendar, which
    pauses it while it runs, has its own (tests/test_reels_calendar.py)."""
    monkeypatch.setenv("REELS_CALENDAR", "off")
    # These tests are about the card path; the automatic path has its own.
    monkeypatch.setenv("REELS_AUTOPUBLISH", "off")


    async def some_platform():
        return None

    # At least one platform can take a post, unless a test says otherwise.
    monkeypatch.setattr(day, "no_platform_line", some_platform)
from atlas.modules.content.drafts import AI_LABEL

READ_AT = datetime(2026, 9, 24, 7, 30, tzinfo=UTC)


def _answer(text: str = "Cliffhanger Part 1 stories and talking vegetables lead."):
    from atlas.kernel.permissions import wrap_untrusted
    return _perplexity.Answer("perplexity_research", wrap_untrusted(text, "perplexity"),
                              ("https://example.com/reels-report",), READ_AT, "mcp")


def _completion(payload) -> Completion:
    return Completion(json.dumps(payload), "fake", "fake")


# ============================================================ the playbook

def test_the_seeds_are_miros_four_formats_and_five_genres():
    names = [name for name, _ in playbook.SEED_FORMATS]
    for wanted in ("before/after transformation timelapse",
                   'cliffhanger "Part 1" story',
                   "talking-object family drama", "mystery hook",
                   "funny animals (dogs, cats)", "vegetable/object drama",
                   "murder-mystery story", "drama story", "love story"):
        assert wanted in names
    assert all(1 <= scenes <= 4 for _, scenes in playbook.SEED_FORMATS)


async def test_seeding_is_idempotent_and_marked_by_source(memory_backend):
    await playbook.ensure_seeds()
    inserts = [(sql, args) for sql, args in memory_backend.sql_log
               if "INSERT INTO reel_formats" in sql]
    assert len(inserts) == (len(playbook.SEED_FORMATS)
                            + len(playbook.MIRO_IDEAS) + 6)
    assert all("WHERE NOT EXISTS" in sql for sql, _ in inserts)
    miro = inserts[:len(playbook.SEED_FORMATS)]
    measured = inserts[len(playbook.SEED_FORMATS)
                       + len(playbook.MIRO_IDEAS):]
    assert all(args[2] == "miro" for _, args in miro)
    from datetime import date
    assert all(args[0] == date(2026, 9, 25) and args[3] == "vidiq_outliers"
               for _, args in measured)
    # the bound value is a real date, never a string (asyncpg refuses a str
    # for $1::date; the in-memory backend now does too)
    assert all(type(args[0]) is date for _, args in measured)


async def test_a_string_bound_to_a_date_cast_is_refused_like_asyncpg(memory_backend):
    from atlas.kernel.state import execute_sql
    with pytest.raises(TypeError, match="needs a date/datetime"):
        await execute_sql("SELECT $1::date", "2026-09-25")
    with pytest.raises(TypeError):
        await execute_sql("SELECT $2::timestamptz", 1, "now")
    await execute_sql("SELECT $1::date", datetime(2026, 9, 25, tzinfo=UTC))


def test_the_six_measured_formats_carry_their_views_and_read_date():
    names = [name for name, _, _ in playbook.MEASURED_FORMATS]
    assert names == ["anthropomorphic animal soap opera",
                     "fruit/vegetable-person drama", "clever pet outsmarts human",
                     "dogs in human scenarios compilation",
                     "animal selfie/travel series",
                     "melodrama micro-short with an on-screen dialogue hook"]
    for _, scenes, evidence in playbook.MEASURED_FORMATS:
        assert "read 2026-09-25" in evidence and "views" in evidence
        assert 1 <= scenes <= 4
    pet = {n: s for n, s, _ in playbook.MEASURED_FORMATS}
    assert pet["clever pet outsmarts human"] == 2          # an 8-20 s loop


def test_the_script_rules_are_in_the_prompt():
    prompt = playbook.DRAFT_SYSTEM
    for rule in ("first 3 seconds", "captioned", "30-70 seconds",
                 "8-20", "same character sheet", "follow for the next part"):
        assert rule in prompt


def test_research_never_scrapes_instagram_through_his_account():
    assert "never does:** read Instagram through Miro's logged-in account" \
        in playbook.__doc__


async def test_a_measured_cliffhanger_format_ends_on_part_1(drafting, monkeypatch):
    drafting.responses["FROM reel_formats WHERE id"] = {
        "id": 12, "format": "anthropomorphic animal soap opera",
        "evidence": playbook.MEASURED_FORMATS[0][2], "source": "vidiq_outliers",
        "max_scenes": 4}
    seen = {}

    async def fake_think(need, system, messages, max_tokens=2048, **kw):
        seen["system"], seen["ask"] = system, messages[0].content
        return _completion({"characters": "a pig-headed husband in a grey suit",
                            "scenes": ["He came home early.", "She was not alone."],
                            "caption": "Follow for part 2."})

    monkeypatch.setattr(playbook, "think", fake_think)
    result = await playbook.draft_reel(12)
    assert result.ok is True
    body = next(args for sql, args in drafting.sql_log
                if "INSERT INTO content_drafts" in sql)[3]
    assert reel_script.parse(body).part_one is True
    assert "follow for part 2" in seen["system"]
    assert "untrusted_data" not in seen["ask"] and "7.3M" in seen["ask"]


def test_reel_research_and_draft_reel_are_tier_auto_with_rollbacks():
    actions = {a.name: a for a in content.MODULE.actions()}
    for name in ("content.reel_research", "content.draft_reel"):
        assert actions[name].tier is Tier.AUTO
        assert actions[name].dry_run is not None and actions[name].rollback is not None


# ============================================================ research

@pytest.fixture
def research_world(memory_backend, monkeypatch):
    monkeypatch.setenv("PERPLEXITY_API_KEY", "pplx-secret")
    asked = {}

    async def fake_call(tool, text, **kw):
        asked.update(tool=tool, text=text, **kw)
        return _answer(), None

    thought = {}

    async def fake_think(need, system, messages, max_tokens=2048, **kw):
        thought["messages"] = messages
        return _completion({"formats": [
            {"format": 'cliffhanger "Part 1" story',
             "evidence": "Part 1 endings drive follows."},
            {"format": "talking vegetables", "evidence": "Object dramas trend."},
            {"format": "Nike unboxing story", "evidence": "brand stuff"}]})

    monkeypatch.setattr(_perplexity, "call", fake_call)
    monkeypatch.setattr(playbook, "think", fake_think)
    memory_backend.responses["INSERT INTO reel_formats (format, evidence, source, "
                             "score, max_scenes) VALUES ($1"] = {"id": 101}
    return memory_backend, asked, thought


async def test_research_asks_the_fixed_question_under_the_90s_deadline(
        research_world, monkeypatch):
    _, asked, _ = research_world
    monkeypatch.delenv("INSTAGRAM_STORIES_TOKEN", raising=False)
    await playbook.research()
    assert asked["tool"] == "perplexity_research"
    assert asked["text"] == playbook.RESEARCH_QUESTION
    assert "short-form story formats" in asked["text"] and "this month" in asked["text"]
    assert asked["deadline"] == 90.0 and asked["caller"] == "content"


async def test_research_files_rows_with_read_time_and_sources(research_world,
                                                             monkeypatch):
    backend, _, thought = research_world
    monkeypatch.delenv("INSTAGRAM_STORIES_TOKEN", raising=False)
    result = await playbook.research()
    assert result.ok is True, result.detail
    rows = [args for sql, args in backend.sql_log
            if "INSERT INTO reel_formats" in sql and "RETURNING id" in sql]
    assert [r[0] for r in rows] == ['cliffhanger "Part 1" story', "talking vegetables"]
    assert all(r[2] == "perplexity" for r in rows)
    assert all("read 07:30 UTC" in r[1] and "example.com" in r[1] for r in rows)
    assert "Nike" in result.detail           # the guard's catch is said, not hidden
    # the research reached the model wrapped, as data
    assert any("<untrusted_data source='perplexity'>" in m.content
               for m in thought["messages"])
    assert json.loads(result.rollback_token) == [101, 101]


async def test_research_logs_the_raw_extract_when_nothing_is_filed(
        research_world, monkeypatch, caplog):
    async def fake_think(need, system, messages, max_tokens=2048, **kw):
        return _completion({"formats": []})

    monkeypatch.setattr(playbook, "think", fake_think)
    with caplog.at_level("WARNING", logger="atlas.content"):
        result = await playbook.research()
    assert result.ok is True
    assert "filed 0 format(s)" in result.detail
    assert any("filed 0 formats" in r.message and '"formats": []' in r.message
               for r in caplog.records)


# ------------------------------------------------------- _json_object (Task 56)

def test_json_object_parses_a_fenced_answer_with_trailing_prose():
    text = ('Sure, here is the JSON:\n\n```json\n'
            '{"formats": [{"format": "cliffhanger", "evidence": "e"}]}\n'
            '```\n\nLet me know if you want another one, e.g. {"other": "idea"}.')
    assert playbook._json_object(text) == {
        "formats": [{"format": "cliffhanger", "evidence": "e"}]}


def test_json_object_parses_a_prose_wrapped_answer():
    text = ('Based on the research, here are the formats you asked for: '
            '{"formats": [{"format": "mystery hook", "evidence": "e"}]} '
            'Hope this helps -- reply in JSON {like this} next time.')
    assert playbook._json_object(text) == {
        "formats": [{"format": "mystery hook", "evidence": "e"}]}


def test_json_object_finds_no_object_in_plain_prose():
    assert playbook._json_object("Sorry, I have no formats to report today.") is None


async def test_the_insights_part_is_unknown_without_the_permission(
        research_world, monkeypatch):
    monkeypatch.setenv("INSTAGRAM_STORIES_ACCOUNT_ID", "sid")
    monkeypatch.setenv("INSTAGRAM_STORIES_TOKEN", "stok")

    async def refused(path, account="main", **params):
        raise instagram.GraphError(
            "Graph API HTTP 403 on /v21.0/sid/insights: (#10) Application does "
            "not have permission for this action", 403)

    monkeypatch.setattr(instagram, "_get", refused)
    result = await playbook.research()
    assert result.ok is True
    assert "own insights: UNKNOWN" in result.detail
    assert "instagram_manage_insights" in result.detail
    assert result.evidence["insights"] == "unknown"


async def test_the_insights_part_is_unknown_when_the_account_is_not_set(
        research_world, monkeypatch):
    monkeypatch.delenv("INSTAGRAM_STORIES_ACCOUNT_ID", raising=False)
    result = await playbook.research()
    assert "own insights: UNKNOWN" in result.detail
    assert result.evidence["insights"] == "unknown"


async def test_readable_insights_carry_their_read_time(research_world, monkeypatch):
    monkeypatch.setenv("INSTAGRAM_STORIES_ACCOUNT_ID", "sid")
    monkeypatch.setenv("INSTAGRAM_STORIES_TOKEN", "stok")

    async def ok(path, account="main", **params):
        # The live shape (2026-09-25): metric_type=total_value is required
        # and the number comes back under total_value, not values[].
        assert params["metric_type"] == "total_value" and params["period"] == "day"
        return {"data": [{"name": "reach", "period": "day",
                          "total_value": {"value": 812}}]}

    monkeypatch.setattr(instagram, "_get", ok)
    result = await playbook.research()
    assert "reach 812/day (read " in result.detail
    assert result.evidence["insights"] == "read"


async def test_research_without_perplexity_says_so(memory_backend, monkeypatch):
    async def unavailable(tool, text, **kw):
        return None, "PERPLEXITY_API_KEY not set"

    monkeypatch.setattr(_perplexity, "call", unavailable)
    monkeypatch.delenv("INSTAGRAM_STORIES_ACCOUNT_ID", raising=False)
    result = await playbook.research()
    assert result.ok is False
    assert "PERPLEXITY_API_KEY not set" in result.detail
    assert "own insights: UNKNOWN" in result.detail


async def test_the_research_rollback_retires_rows(memory_backend):
    result = await playbook.research_rollback(json.dumps([4, 5]))
    assert result.ok is True
    retired = [args for sql, args in memory_backend.sql_log
               if "SET retired_at = now()" in sql]
    assert retired == [([4, 5],)]


# ============================================================ ranking

async def test_top_formats_rank_by_weight_then_rotate_by_last_drafted(
        memory_backend):
    memory_backend.responses["GROUP BY lower(f.format)"] = [
        {"id": 1, "format": "mystery hook", "weight": 2.0, "max_scenes": 3,
         "source": "miro"},
        {"id": 2, "format": "love story", "weight": 2.0, "max_scenes": 4,
         "source": "miro"},
        {"id": 3, "format": 'cliffhanger "Part 1" story', "weight": 3.0,
         "max_scenes": 4, "source": "miro"},
        {"id": 9, "format": "talking vegetables", "weight": 1.0, "max_scenes": 4,
         "source": "perplexity"},
    ]
    memory_backend.responses["GROUP BY source"] = [
        {"source": "reel_format:1", "at": datetime(2026, 9, 23, tzinfo=UTC)}]
    top = await playbook.top_formats(3)
    assert [f["id"] for f in top] == [3, 2, 1]


# ============================================================ drafting

@pytest.fixture
def drafting(memory_backend, monkeypatch):
    # The drafting tests are about the script's shape; the length rule has
    # its own tests (test_reel_lengths_follow_miros_rule).
    monkeypatch.setattr(playbook, "beat_range", lambda name: (1, 4))
    memory_backend.responses["FROM reel_formats WHERE id"] = {
        "id": 3, "format": 'cliffhanger "Part 1" story', "evidence": "Miro chose it",
        "source": "miro", "max_scenes": 4}
    memory_backend.responses["INSERT INTO content_drafts"] = {"id": 77}
    return memory_backend


async def test_draft_reel_writes_a_labelled_story_script(drafting, monkeypatch):
    async def fake_think(need, system, messages, max_tokens=2048, **kw):
        return _completion({
            "characters": "an old lighthouse keeper, grey beard, yellow coat",
            "scenes": ["He had not seen a ship in forty years.",
                       "Tonight a light answered his.",
                       "It came from under the water.",
                       "It spelled his name.", "A fifth beat, too many."],
            "caption": "Nobody told him the sea was gone."})

    monkeypatch.setattr(playbook, "think", fake_think)
    result = await playbook.draft_reel(3)
    assert result.ok is True, result.detail
    inserted = next(args for sql, args in drafting.sql_log
                    if "INSERT INTO content_drafts" in sql)
    kind, source, _title, body = inserted
    assert kind == "story_script" and source == "reel_format:3"
    assert body.rstrip().endswith(AI_LABEL)
    assert reel_script.DIALOGUE_MARK in body    # characters speak; no narrator
    assert "no narrator" in drafting_system_prompt(playbook).lower()
    script = reel_script.parse(body)
    assert len(script.scenes) == 4               # the format's max_scenes
    assert script.characters.startswith("an old lighthouse keeper")
    assert result.evidence["id"] == 77
    assert result.evidence["caption"].endswith(AI_LABEL)
    assert result.rollback_token == "77"


async def test_a_research_format_reaches_the_drafter_wrapped(drafting, monkeypatch):
    drafting.responses["FROM reel_formats WHERE id"] = {
        "id": 9, "format": "talking vegetables", "evidence": "Object dramas trend.",
        "source": "perplexity", "max_scenes": 4}
    seen = {}

    async def fake_think(need, system, messages, max_tokens=2048, **kw):
        seen["messages"] = messages
        return _completion({"characters": "a carrot", "scenes": ["One."],
                            "caption": "Veg."})

    monkeypatch.setattr(playbook, "think", fake_think)
    await playbook.draft_reel(9)
    assert "<untrusted_data source='perplexity'>" in seen["messages"][0].content


async def test_draft_reel_refuses_a_script_naming_a_real_brand(drafting, monkeypatch):
    async def fake_think(need, system, messages, max_tokens=2048, **kw):
        return _completion({"characters": "a cat", "scenes": [
            "The cat opened a can of Red Bull."], "caption": "Wings."})

    monkeypatch.setattr(playbook, "think", fake_think)
    result = await playbook.draft_reel(3)
    assert result.ok is False and "Red Bull" in result.detail
    assert not any("INSERT INTO content_drafts" in sql for sql, _ in drafting.sql_log)


async def test_draft_reel_refuses_unusable_model_output(drafting, monkeypatch):
    async def fake_think(need, system, messages, max_tokens=2048, **kw):
        return Completion("not json at all", "fake", "fake")

    monkeypatch.setattr(playbook, "think", fake_think)
    result = await playbook.draft_reel(3)
    assert result.ok is False and "usable" in result.detail


# ---------------------------------- narration is speech, not production notes

def test_the_prompt_bans_production_language_in_narration():
    prompt = playbook.DRAFT_SYSTEM
    for phrase in ("in-world speech", "watch as", "in seconds", "looping"):
        assert phrase in prompt


async def test_narration_with_watch_as_in_seconds_is_rejected_and_reasked(
        drafting, monkeypatch):
    calls = []

    async def fake_think(need, system, messages, max_tokens=2048, **kw):
        calls.append(messages[0].content)
        if len(calls) == 1:
            return _completion({
                "characters": "an old lighthouse keeper",
                "scenes": ["Watch as the tide rises in seconds.",
                           "It came from under the water."],
                "caption": "Follow along."})
        return _completion({
            "characters": "an old lighthouse keeper",
            "scenes": ["He had not seen a ship in forty years.",
                       "Tonight a light answered his."],
            "caption": "Follow along."})

    monkeypatch.setattr(playbook, "think", fake_think)
    result = await playbook.draft_reel(3)
    assert result.ok is True, result.detail
    assert len(calls) == 2, "a bad narration is re-asked exactly once"
    assert calls[1] != calls[0]                 # the re-ask says what was wrong
    script = reel_script.parse(
        next(args for sql, args in drafting.sql_log
             if "INSERT INTO content_drafts" in sql)[3])
    assert "watch as" not in " ".join(script.scenes).lower()


async def test_narration_still_bad_after_the_reask_fails_the_draft(
        drafting, monkeypatch):
    calls = []

    async def fake_think(need, system, messages, max_tokens=2048, **kw):
        calls.append(messages[0].content)
        return _completion({
            "characters": "a lighthouse keeper",
            "scenes": ["Watch as the tide rises in seconds.",
                       "It came from under the water."],
            "caption": "Follow along."})

    monkeypatch.setattr(playbook, "think", fake_think)
    result = await playbook.draft_reel(3)
    assert result.ok is False
    assert len(calls) == 2, "never a third try"
    assert "production" in result.detail.lower()
    assert not any("INSERT INTO content_drafts" in sql
                  for sql, _ in drafting.sql_log)


async def test_a_seeded_formats_template_reaches_the_prompt(drafting, monkeypatch):
    drafting.responses["FROM reel_formats WHERE id"] = {
        "id": 5, "format": "clever pet outsmarts human", "evidence": "vidIQ says so",
        "source": "miro", "max_scenes": 2}
    seen = {}

    async def fake_think(need, system, messages, max_tokens=2048, **kw):
        seen["system"] = system
        return _completion({"characters": "a dog",
                            "scenes": ["It ate the cheese and left the pill.",
                                      "It looked pleased with itself."],
                            "caption": "Good boy."})

    monkeypatch.setattr(playbook, "think", fake_think)
    result = await playbook.draft_reel(5)
    assert result.ok is True, result.detail
    assert "setup" in seen["system"].lower()
    assert "mischief" in seen["system"].lower()
    assert "reaction shot" in seen["system"].lower()


# ============================================================ the daily step

FORMATS = [
    {"id": 3, "format": 'cliffhanger "Part 1" story', "weight": 3.0, "max_scenes": 4,
     "source": "miro"},
    {"id": 2, "format": "love story", "weight": 2.0, "max_scenes": 4,
     "source": "miro"},
    {"id": 1, "format": "mystery hook", "weight": 2.0, "max_scenes": 3,
     "source": "miro"},
]


@pytest.fixture
def the_day(memory_backend, monkeypatch):
    # `kernel.reels.daily` refuses an empty catalog (a first-run guard), so
    # the day is run against the content module's real actions whatever
    # order the files were collected in.
    from atlas.kernel.registry import REGISTRY
    if "content.draft_reel" not in REGISTRY.actions:
        REGISTRY.load("atlas.modules.content", "content")
    memory_backend.responses["FROM reports WHERE kind"] = {"at": None}
    monkeypatch.setenv("REELS_PER_DAY", "2")
    executed: list[tuple[str, dict]] = []
    proposed: list[tuple[str, dict, str, dict]] = []
    sent: list = []
    drafts = iter([70, 71, 72])
    media = iter([500, 501, 502])

    async def fake_execute(req):
        executed.append((req.action, req.params))
        if req.action == "content.reel_research":
            return ActionResult(True, "filed 2 format(s) (read 07:30 UTC); own "
                                      "insights: UNKNOWN — needs "
                                      "instagram_manage_insights")
        if req.action == "content.draft_reel":
            n = next(drafts)
            return ActionResult(True, f"drafted {n}", evidence={
                "id": n, "caption": f"Caption {n}\n\n{AI_LABEL}",
                "format": "f", "scenes": ["a", "b"]})
        if req.action == "content.render_video":
            return ActionResult(True, "rendered with the local backend",
                                evidence={"media_id": next(media), "public": True})
        raise AssertionError(req.action)

    async def fake_preview(action, params):
        assert action == "content.publish_reel"
        return ActionResult(True, "would post to 1 of 3: Instagram (stories): ready")

    async def fake_propose(action, params, reason, **kw):
        proposed.append((action, params, reason, kw))
        return 900 + len(proposed)

    async def fake_notify(msg):
        sent.append(msg)
        return ["slack"]

    async def fake_link(media_id):
        return f"https://atlas.example/media/tok{media_id}", READ_AT

    async def fake_overnight():
        return ["Overnight on the stories account (read 06:00 UTC): 0 hidden"]

    async def fake_top(n):
        return FORMATS[:n]

    async def fake_today():
        return 0

    monkeypatch.setattr(day, "execute", fake_execute)
    monkeypatch.setattr(day, "preview", fake_preview)
    monkeypatch.setattr(day, "propose", fake_propose)
    monkeypatch.setattr(day, "notify", fake_notify)
    monkeypatch.setattr(day, "overnight_lines", fake_overnight)
    monkeypatch.setattr(content, "public_link", fake_link)
    monkeypatch.setattr(playbook, "top_formats", fake_top)

    async def fake_pick(n, today=None):
        return FORMATS[:n]

    monkeypatch.setattr(playbook, "pick_today", fake_pick)
    monkeypatch.setattr(playbook, "drafted_today", fake_today)
    return executed, proposed, sent


async def test_the_day_proposes_at_most_reels_per_day_labelled_cards(the_day):
    executed, proposed, sent = the_day
    await day.daily()
    assert [a for a, _ in executed] == [
        "content.reel_research",
        "content.draft_reel", "content.render_video",
        "content.draft_reel", "content.render_video"]
    assert [p[0] for p in proposed] == ["content.publish_reel"] * 2
    for _, params, reason, kw in proposed:
        assert set(params) == {"media_id", "caption"}
        assert params["caption"].endswith(AI_LABEL)
        assert "tok" not in json.dumps(params)            # no token on the card
        assert "Instagram (stories): ready" in reason
        assert "Nothing posts until you approve" in reason
        assert kw["dedup_key"].startswith("reel:")
    links = [m for m in sent if "watch" in m.title.lower()]
    assert len(links) == 2 and all(m.topic == "instagram_stories" for m in links)
    assert "https://atlas.example/media/tok500" in links[0].body
    digest = sent[-1]
    assert digest.topic == "instagram_stories" and digest.kind == "digest"
    assert "instagram_manage_insights" in digest.body
    assert "2 posted or carded" in digest.body


async def test_drafts_already_made_today_count_against_the_limit(the_day,
                                                                 monkeypatch):
    executed, proposed, _ = the_day

    async def one_already():
        return 1

    monkeypatch.setattr(playbook, "drafted_today", one_already)
    await day.daily()
    assert [a for a, _ in executed].count("content.draft_reel") == 1
    assert len(proposed) == 1


async def test_a_refused_render_raises_no_card_and_says_why(the_day, monkeypatch):
    executed, proposed, sent = the_day
    real = day.execute

    async def render_refused(req):
        if req.action == "content.render_video":
            executed.append((req.action, req.params))
            return ActionResult(False, "refused: ffmpeg not installed")
        return await real(req)

    monkeypatch.setattr(day, "execute", render_refused)
    await day.daily()
    assert proposed == []
    assert "ffmpeg not installed" in sent[-1].body
    assert "no card" in sent[-1].body


async def test_watch_only_drafts_nothing_and_the_digest_says_so(the_day, monkeypatch):
    executed, proposed, sent = the_day

    async def watch_only(req):
        executed.append((req.action, req.params))
        return ActionResult(False, f"ATLAS is in watch-only mode and did not run "
                                   f"{req.action}.")

    monkeypatch.setattr(day, "execute", watch_only)
    await day.daily()
    assert proposed == []
    assert "watch-only" in sent[-1].body



# ============================================================ the Slack preview

async def test_notify_remembers_where_a_card_landed(monkeypatch):
    from atlas.kernel import comms

    class FakeSlack:
        name, rich, interactive = "slack", True, True

        async def available(self):
            return True

        async def send(self, msg):
            return "1727.000100"

    monkeypatch.setattr(comms, "CHANNELS", {"slack": FakeSlack()})
    monkeypatch.setattr(comms, "ROUTING", {s: ("slack",) for s in comms.ROUTING})
    monkeypatch.setattr(comms, "_REFS", {})
    monkeypatch.delenv("QUIET_HOURS", raising=False)

    async def home():
        return {"home": True}

    monkeypatch.setattr(comms, "presence", home)
    await comms.notify(comms.Outbound(title="#901 — Post a reel", body="b"))
    await comms.notify(comms.Outbound(title="#9010 — something else", body="b"))
    assert comms.sent_ref("#901 — ", "slack") == "1727.000100"
    assert comms.sent_ref("#902 — ", "slack") is None
    assert comms.sent_ref("#901 — ", "matrix") is None


def test_the_refs_are_bounded(monkeypatch):
    from atlas.kernel import comms
    monkeypatch.setattr(comms, "_REFS", {})
    for i in range(comms.REFS_KEPT + 10):
        comms._remember(f"#{i} — x", "slack", str(i))
    assert len(comms._REFS) == comms.REFS_KEPT
    assert comms.sent_ref("#0 — ", "slack") is None


def _slack(monkeypatch, *, missing_scope=False):
    import httpx

    from atlas.channels import slack as slack_mod
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-secret")
    calls: list[tuple[str, bytes]] = []

    def handler(request):
        calls.append((str(request.url), request.content))
        if request.url.path == "/api/files.getUploadURLExternal":
            if missing_scope:
                return httpx.Response(200, json={"ok": False, "error": "missing_scope",
                                                 "needed": "files:write"})
            return httpx.Response(200, json={"ok": True, "file_id": "F123",
                                             "upload_url": "https://files.slack.com/up/abc"})
        if request.url.path == "/up/abc":
            return httpx.Response(200, text="OK - 5")
        if request.url.path == "/api/files.completeUploadExternal":
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(500)

    real = httpx.AsyncClient
    monkeypatch.setattr(slack_mod.httpx, "AsyncClient", lambda *a, **k: real(
        transport=httpx.MockTransport(handler)))
    return slack_mod.SlackChannel(), calls


async def test_slack_upload_puts_the_file_in_the_thread(monkeypatch):
    channel, calls = _slack(monkeypatch)
    why = await channel.upload(channel="C0REELS", thread="1727.000100",
                               filename="reel-500.mp4", data=b"MP4BYTES",
                               title="#901 preview", comment="watch")
    assert why is None
    assert [url.split("?")[0] for url, _ in calls] == [
        "https://slack.com/api/files.getUploadURLExternal",
        "https://files.slack.com/up/abc",
        "https://slack.com/api/files.completeUploadExternal"]
    assert b"length=8" in calls[0][1] and calls[1][1] == b"MP4BYTES"
    done = json.loads(calls[2][1])
    assert done["channel_id"] == "C0REELS" and done["thread_ts"] == "1727.000100"
    assert done["files"] == [{"id": "F123", "title": "#901 preview"}]


async def test_slack_upload_names_the_missing_scope(monkeypatch):
    channel, _ = _slack(monkeypatch, missing_scope=True)
    why = await channel.upload(channel="C0REELS", thread="1", filename="r.mp4",
                               data=b"x", title="t", comment="c")
    assert why == "the Slack app lacks the files:write scope"
    assert "xoxb-secret" not in why


@pytest.fixture
def under_card(monkeypatch, tmp_path):
    from atlas.kernel import comms
    video = tmp_path / "500.mp4"
    video.write_bytes(b"MP4BYTES")
    uploads: list[dict] = []
    sent: list = []

    class FakeSlack:
        why: str | None = None

        def channel_for(self, topic):
            assert topic == "instagram_stories"
            return "C0REELS"

        async def upload(self, **kw):
            uploads.append(kw)
            return self.why

    slack = FakeSlack()
    monkeypatch.setattr(comms, "CHANNELS", {"slack": slack})
    monkeypatch.setattr(comms, "_REFS", {"#901 — Post a reel": {"slack": "1727.1"}})

    async def fake_link(media_id):
        return f"https://atlas.example/media/tok{media_id}", READ_AT

    async def fake_path(media_id):
        return str(video)

    async def fake_notify(msg):
        sent.append(msg)
        return ["slack"]

    monkeypatch.setattr(content, "public_link", fake_link)
    monkeypatch.setattr(content, "rendered_path", fake_path)
    monkeypatch.setattr(day, "notify", fake_notify)
    return slack, uploads, sent


async def test_the_preview_lands_in_the_channel(under_card):
    _, uploads, sent = under_card
    said = await day.preview_under_card(901, 500)
    assert said == "preview uploaded to the channel"
    assert uploads[0]["thread"] is None and uploads[0]["channel"] == "C0REELS"
    assert uploads[0]["data"] == b"MP4BYTES"
    assert "https://atlas.example/media/tok500" in uploads[0]["comment"]
    assert sent == []                               # no link message needed


async def test_missing_scope_falls_back_to_the_link_with_the_note(under_card):
    slack, uploads, sent = under_card
    slack.why = "the Slack app lacks the files:write scope"
    said = await day.preview_under_card(901, 500)
    assert uploads and len(sent) == 1
    assert sent[0].body.startswith(day.SCOPE_NOTE)
    assert "https://atlas.example/media/tok500" in sent[0].body
    assert sent[0].thread == "1727.1" and sent[0].topic == "instagram_stories"
    assert "files:write" in said


async def test_a_raising_upload_never_fails_the_card(under_card, monkeypatch):
    slack, _, sent = under_card

    async def boom(**kw):
        raise RuntimeError("socket closed")

    monkeypatch.setattr(slack, "upload", boom)
    said = await day.preview_under_card(901, 500)
    assert "link in its place" in said and "https://atlas.example" in sent[0].body



# ============================================================ the 17:00 nudge

@pytest.fixture
def nudging(memory_backend, monkeypatch):
    monkeypatch.setattr(day, "watch_only", lambda: False)
    sent: list = []

    async def fake_notify(msg):
        sent.append(msg)
        return ["slack"]

    monkeypatch.setattr(day, "notify", fake_notify)
    memory_backend.responses["FROM reports WHERE kind"] = {"at": None}
    memory_backend.responses["decided_at >= $1"] = {"n": 0}
    memory_backend.responses["status = 'pending'"] = {"n": 2}
    return memory_backend, sent


async def test_the_nudge_fires_when_nothing_was_approved(nudging):
    backend, sent = nudging
    assert await day.nudge() is True
    assert sent[0].title == "2 reels waiting — approve one to keep today's post"
    assert sent[0].topic == "instagram_stories" and not sent[0].buttons
    assert "never publishes itself" in sent[0].body and "UTC" in sent[0].body
    stored = [a for sql, a in backend.sql_log if "INSERT INTO reports" in sql]
    assert stored[0][0] == day.NUDGE_KIND


async def test_no_nudge_once_a_reel_was_approved(nudging):
    backend, sent = nudging
    backend.responses["decided_at >= $1"] = {"n": 1}
    assert await day.nudge() is False and sent == []


async def test_the_nudge_fires_once_a_day(nudging):
    backend, sent = nudging
    backend.responses["FROM reports WHERE kind"] = {
        "at": datetime.now().astimezone()}
    assert await day.nudge() is False and sent == []


async def test_unreadable_proposals_mean_no_nudge(memory_backend, monkeypatch):
    memory_backend.responses["FROM reports WHERE kind"] = {"at": None}
    sent = []

    async def fake_notify(msg):
        sent.append(msg)

    monkeypatch.setattr(day, "notify", fake_notify)
    assert await day.nudge() is False and sent == []


async def test_the_scheduler_nudges_at_17_once(monkeypatch):
    from atlas.kernel import scheduler
    monkeypatch.setattr(scheduler, "_last_reels_date",
                        datetime.now().astimezone().date())
    monkeypatch.setattr(scheduler, "_last_nudge_date", None)
    monkeypatch.setattr(scheduler, "_reels_task", None)
    spawned = []
    monkeypatch.setattr(scheduler.background, "spawn",
                        lambda coro, name: spawned.append(name) or coro.close())
    for hour in (16, 17, 18):
        monkeypatch.setattr(scheduler, "_now", lambda h=hour: datetime.now()
                            .astimezone().replace(hour=h, minute=0))
        await scheduler._maybe_reels()
    assert spawned == ["the reels nudge"]



async def test_no_nudge_in_watch_only(nudging, monkeypatch):
    _, sent = nudging
    monkeypatch.setattr(day, "watch_only", lambda: True)
    assert await day.nudge() is False and sent == []


def test_a_production_word_is_matched_whole_never_inside_another_word():
    from atlas.modules.content.reels import _production_language
    assert _production_language(["She found the loophole and ran."]) is None
    assert _production_language(["Watch as it loops back."]) == "watch as"
    assert _production_language(["It loops forever."]) is None


def test_the_extract_budget_leaves_room_for_a_reasoning_model():
    from atlas.modules.content.reels import EXTRACT_MAX_TOKENS
    assert EXTRACT_MAX_TOKENS >= 2500



def test_miros_second_round_is_seeded_with_its_template_and_no_film_named():
    """2026-09-27: his revenge story, the two film archetypes and the TikTok
    formats. A film is an archetype only: its title never reaches a prompt."""
    names = [n for n, _, _ in playbook.MIRO_IDEAS]
    assert "wronged-man revenge saga" in names
    for name, scenes, evidence in playbook.MIRO_IDEAS:
        assert 1 <= scenes <= 4
        for film in ("أمير الانتقام", "الجلسة سرية", "إرهابي", "Monte Cristo is"):
            assert film not in evidence
    tpl = playbook.FORMAT_TEMPLATES["wronged-man revenge saga"]
    assert "daughter" in tpl and "Never name a film" in tpl



# -------------------------------------------------- the day's mix (09-27)

def _fmt(i, name, weight=2.0):
    return {"id": i, "format": name, "weight": weight, "max_scenes": 4,
            "source": "miro", "results": 0.0}


MIX = [_fmt(1, "wronged-man revenge saga", 2.5),
       _fmt(2, "Egyptian Arabic revenge serial"),
       _fmt(3, "original insane plot"),
       _fmt(4, "funny animals (dogs, cats)", 2.2),
       _fmt(5, "talking fruit and vegetable brainrot saga", 2.1)]


def test_every_format_lands_in_one_of_the_four_buckets():
    got = {f["format"]: playbook.bucket_of(f["format"]) for f in MIX}
    assert got == {"wronged-man revenge saga": "serial",
                   "Egyptian Arabic revenge serial": "serial",
                   "original insane plot": "original",
                   "funny animals (dogs, cats)": "animals",
                   "talking fruit and vegetable brainrot saga": "produce"}


async def test_an_original_plot_comes_first_when_none_in_two_days(monkeypatch):
    from datetime import date

    async def ranked(n):
        return list(MIX)

    async def last():
        return {}

    monkeypatch.setattr(playbook, "top_formats", ranked)
    monkeypatch.setattr(playbook, "_bucket_last_drafted", last)
    even = date(2026, 9, 28) if date(2026, 9, 28).toordinal() % 2 == 0 else date(2026, 9, 27)
    picked = await playbook.pick_today(2, even)
    assert [p["format"] for p in picked] == ["original insane plot",
                                             "Egyptian Arabic revenge serial"]


async def test_serial_days_alternate_arabic_and_english(monkeypatch):
    from datetime import UTC, date, datetime

    async def ranked(n):
        return list(MIX)

    async def last():   # an original was drafted today: not due
        return {"original": datetime.now(UTC)}

    monkeypatch.setattr(playbook, "top_formats", ranked)
    monkeypatch.setattr(playbook, "_bucket_last_drafted", last)
    a = datetime.now(UTC).date()
    b = date.fromordinal(a.toordinal() + 1)
    first = (await playbook.pick_today(1, a))[0]["format"]
    second = (await playbook.pick_today(1, b))[0]["format"]
    assert {first, second} == {"wronged-man revenge saga",
                               "Egyptian Arabic revenge serial"}


async def test_the_fill_takes_the_bucket_drafted_longest_ago(monkeypatch):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)

    async def ranked(n):
        return list(MIX)

    async def last():
        return {"original": now, "animals": now - timedelta(hours=5),
                "produce": now - timedelta(days=3), "serial": now}

    monkeypatch.setattr(playbook, "top_formats", ranked)
    monkeypatch.setattr(playbook, "_bucket_last_drafted", last)
    picked = await playbook.pick_today(2, now.date())
    assert playbook.bucket_of(picked[0]["format"]) == "serial"
    assert picked[1]["format"] == "talking fruit and vegetable brainrot saga"


async def test_two_days_without_a_post_is_said_first_and_still_posts_nothing(nudging):
    """Miro, 2026-09-27: at least every other day. The reminder gets louder;
    nothing is ever posted on silence (invariant 5)."""
    backend, sent = nudging
    backend.responses["interval '48 hours'"] = {"n": 0}
    assert await day.nudge() is True
    assert sent[0].title.startswith("Nothing has posted for 2 days. 2 reels waiting")
    assert not sent[0].buttons


async def test_a_post_yesterday_keeps_the_ordinary_reminder(nudging):
    backend, sent = nudging
    backend.responses["interval '48 hours'"] = {"n": 1}
    assert await day.nudge() is True
    assert sent[0].title == "2 reels waiting — approve one to keep today's post"


async def test_no_reel_is_made_when_no_platform_can_take_one(monkeypatch):
    """Miro, 2026-09-27: every day -- unless no platform can take a post.
    Then nothing is drafted, and the line says which accounts to fix."""
    from atlas.modules.content import crosspost

    async def none_ready(caption=""):
        return 0, ["Instagram: refused — token expired", "Facebook: not configured",
                   "TikTok: not configured"]

    monkeypatch.setattr(crosspost, "legs_ready", none_ready)
    monkeypatch.setattr(day, "no_platform_line", _real_no_platform_line)
    ran = []

    async def execute(req):
        ran.append(req.action)
        raise AssertionError("nothing may be drafted")

    monkeypatch.setattr(day, "execute", execute)
    lines = await day.reel_lines()
    assert ran == [] and lines[0].startswith("No reel made today: no platform")
    assert "token expired" in lines[0]


async def test_the_nudge_says_the_accounts_need_fixing(nudging, monkeypatch):
    from atlas.modules.content import crosspost

    async def none_ready(caption=""):
        return 0, ["Instagram: refused — token expired"]

    monkeypatch.setattr(crosspost, "legs_ready", none_ready)
    monkeypatch.setattr(day, "no_platform_line", _real_no_platform_line)
    _backend, sent = nudging
    assert await day.nudge() is True
    assert sent[0].title == "No platform can take a post — the accounts need fixing"
    assert "token expired" in sent[0].body


def test_reel_lengths_follow_miros_rule(monkeypatch):
    """Relayed 2026-09-27: a series episode 60-90 s (8-11 beats), everything
    else 30-60 s (4-8), held to what Veo can make in one day."""
    monkeypatch.setenv("VEO_DAILY_CLIPS", "50")
    assert playbook.beat_range("wronged-man revenge saga") == (8, 11)
    assert playbook.beat_range("hospital ER comedy series") == (8, 11)
    assert playbook.beat_range("clever pet outsmarts human") == (4, 5)
    monkeypatch.setenv("VEO_DAILY_CLIPS", "10")
    assert playbook.beat_range("wronged-man revenge saga") == (8, 10)


def test_miros_priority_order():
    order = ["Egyptian Arabic comedy series", "rival cars family drama",
             "hospital ER comedy series", "wronged-man revenge saga",
             "clever pet outsmarts human"]
    ranks = [playbook.priority(n) for n in order]
    assert ranks == sorted(ranks) and ranks[0] == 0 and ranks[-1] > ranks[-2]


async def test_a_short_script_is_reasked_then_refused(drafting, monkeypatch):
    monkeypatch.setattr(playbook, "beat_range", lambda name: (8, 10))
    asks = []

    async def fake_think(need, system, messages, max_tokens=2048, **kw):
        asks.append(messages[0].content)
        return _completion({"characters": "Mia; Rex", "scenes": ["Mia: Hi."],
                            "caption": "Hi."})

    monkeypatch.setattr(playbook, "think", fake_think)
    result = await playbook.draft_reel(3)
    assert not result.ok and "needs 8-10" in result.detail
    assert len(asks) == 2 and "between 8 and 10 beats" in asks[1]


# ===================================================== posting on its own

@pytest.fixture
def a_good_render(memory_backend, monkeypatch, tmp_path):
    from atlas.modules.content import crosspost, ffmpeg
    path = tmp_path / "7.mp4"
    path.write_bytes(b"mp4")
    memory_backend.responses["FROM content_media WHERE id"] = {
        "id": 7, "status": "done", "path": str(path), "media_token": None,
        "media_token_expires_at": None}
    memory_backend.responses["FROM reel_posts WHERE at >="] = {"n": 0}
    info = {"width": 1080, "height": 1920, "seconds": 42.0, "audio": True}

    async def probe(p):
        return dict(info)

    async def loud(p):
        return info.get("volume", -20.0)

    monkeypatch.setattr(ffmpeg, "probe", probe)
    monkeypatch.setattr(ffmpeg, "mean_volume", loud)
    monkeypatch.delenv("REELS_AUTOPUBLISH", raising=False)
    return memory_backend, info, crosspost


async def test_a_good_reel_may_post_itself(a_good_render):
    _backend, _info, crosspost = a_good_render
    assert await crosspost.autopublish_check(7, "A story.\n\nMade with AI") is None


@pytest.mark.parametrize(("change", "why"), [
    ({"width": 720}, "not 1080x1920"),
    ({"audio": False}, "no sound"),
    ({"seconds": 3.0}, "under 5 s"),
    ({"volume": float("-inf")}, "silent"),
])
async def test_a_bad_render_is_held_for_miro(a_good_render, change, why):
    _backend, info, crosspost = a_good_render
    info.update(change)
    assert why in await crosspost.autopublish_check(7, "A story.\n\nMade with AI")


async def test_the_switch_the_label_the_guard_and_the_daily_cap_hold_a_reel(
        a_good_render, monkeypatch):
    backend, _info, crosspost = a_good_render
    assert "AI label" in await crosspost.autopublish_check(7, "No label here")
    assert "real brand" in await crosspost.autopublish_check(
        7, "Drinking Red Bull.\n\nMade with AI")
    backend.responses["FROM reel_posts WHERE at >="] = {"n": 3}
    assert "automatic post(s) are done" in await crosspost.autopublish_check(
        7, "A story.\n\nMade with AI")
    monkeypatch.setenv("REELS_AUTOPUBLISH", "off")
    assert "off" in await crosspost.autopublish_check(7, "A story.\n\nMade with AI")


async def test_a_reel_that_fails_a_check_becomes_a_card_with_the_reason_first(
        memory_backend, monkeypatch):
    monkeypatch.delenv("REELS_AUTOPUBLISH", raising=False)

    async def execute(req):
        assert req.action == "content.autopublish_reel"
        return ActionResult(False, "not posted automatically: the render has no sound")

    async def preview(action, params):
        return ActionResult(True, "would post to 3 of 3")

    raised = []

    async def propose(action, params, reason, **kw):
        raised.append((action, reason))
        return 9

    async def shown(pid, media_id):
        return "preview in thread"

    monkeypatch.setattr(day, "execute", execute)
    monkeypatch.setattr(day, "preview", preview)
    monkeypatch.setattr(day, "propose", propose)
    monkeypatch.setattr(day, "preview_under_card", shown)
    outcome, pid, _ = await day.post_or_card(
        {"media_id": 7, "caption": "x\n\nMade with AI"}, "Reel 7.",
        dedup="reel:7", media_id=7, rollback_plan="deletes the posts", series=False)
    assert (outcome, pid) == ("carded", 9)
    assert raised[0][0] == "content.publish_reel"
    assert raised[0][1].startswith("Held for you: not posted automatically: the "
                                   "render has no sound")


async def test_a_reel_that_passes_posts_and_miro_gets_a_receipt(monkeypatch):
    monkeypatch.delenv("REELS_AUTOPUBLISH", raising=False)
    sent = []

    async def execute(req):
        return ActionResult(True, "reel 7: Instagram: posted · TikTok: posted")

    async def notify(msg):
        sent.append(msg)
        return ["slack"]

    async def never(*a, **k):
        raise AssertionError("no card for a reel that posted")

    monkeypatch.setattr(day, "execute", execute)
    monkeypatch.setattr(day, "notify", notify)
    monkeypatch.setattr(day, "propose", never)
    outcome, pid, detail = await day.post_or_card(
        {"media_id": 7, "caption": "x\n\nMade with AI"}, "Reel 7.",
        dedup="reel:7", media_id=7, rollback_plan="deletes the posts", series=False)
    assert outcome == "posted" and pid is None and "Instagram: posted" in detail
    assert sent[0].title == "Posted a reel on its own"
    assert "REELS_AUTOPUBLISH=off" in sent[0].body


@pytest.mark.parametrize(("detail", "on_fail", "outcome"), [
    ("not posted automatically: today's 2 automatic post(s) are done", "card", "wait"),
    ("not posted automatically: the render's sound is silent (-91 dB)", "redo", "redo"),
    ("not posted automatically: the render has no sound", "skip", "skip"),
])
async def test_miro_is_not_asked_when_the_pipeline_can_handle_it(
        monkeypatch, detail, on_fail, outcome):
    """Miro, 2026-09-28: "no need for me to meddle"."""
    monkeypatch.delenv("REELS_AUTOPUBLISH", raising=False)

    async def execute(req):
        return ActionResult(False, detail)

    async def never(*a, **k):
        raise AssertionError("no card for Miro")

    monkeypatch.setattr(day, "execute", execute)
    monkeypatch.setattr(day, "propose", never)
    monkeypatch.setattr(day, "preview", never)
    got, pid, said = await day.post_or_card(
        {"media_id": 7, "caption": "x\n\nMade with AI"}, "Reel 7.",
        dedup="reel:7", media_id=7, rollback_plan="deletes", on_fail=on_fail,
        series=False)
    assert (got, pid) == (outcome, None) and said == detail


async def test_a_redo_drops_the_banked_clips_and_requeues_once(memory_backend,
                                                               monkeypatch, tmp_path):
    from atlas.modules.content import calendar as cal
    from atlas.modules.content import series
    monkeypatch.setattr(cal, "clip_dir", lambda job_id: str(tmp_path / job_id))
    (tmp_path / "d01").mkdir()
    (tmp_path / "d01" / "b-1.mp4").write_bytes(b"x")
    await cal.redo("d01", "silent")
    assert not (tmp_path / "d01").exists()
    await series.redo(2, 1)
    sql = " ".join(s for s, _ in memory_backend.sql_log)
    assert "redos = redos + 1" in sql and "status = 'draft'" in sql
    assert "status = 'drafted'" in sql
