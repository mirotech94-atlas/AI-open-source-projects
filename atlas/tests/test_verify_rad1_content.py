"""Read-back verifiers for content's drafts, plans, research and renders, and
purchasing's prepared rows (RA-D1).

Each one: verified, contradicted, unknown, a raising read that becomes
unknown, and no secret in the audit shape. The database is faked at
`state.fetch_one` / `state.fetch_all`, and the fake refuses anything but a
SELECT. The network is faked at `httpx.AsyncClient.send` and refuses
everything (none of these verifiers may call out). ffprobe is faked at
`ffmpeg.binary` / `ffmpeg.probe`; files live in tmp_path.
"""
from __future__ import annotations

import hashlib
import json
import re

import httpx
import pytest

from atlas.kernel import state
from atlas.kernel import verify as v
from atlas.kernel.contracts import ActionResult
from atlas.modules.content import calendar, ffmpeg

SECRET = "sk-rad1-content-supersecret-0123456789"

ACTIONS = ["content.draft", "content.draft_reel", "content.draft_series_part",
           "content.plan_series", "content.reel_research",
           "content.render_calendar_job", "content.render_video",
           "purchasing.prepare_cart", "purchasing.prepare_flight"]


@pytest.fixture(autouse=True)
def secret_env(monkeypatch):
    monkeypatch.setenv("VERIFY_TEST_API_KEY", SECRET)
    monkeypatch.setenv("EBAY_CLIENT_SECRET", SECRET)


@pytest.fixture(autouse=True)
def no_net(monkeypatch):
    async def send(client, request, **_kw):
        raise AssertionError(f"a verifier called out: {request.method} {request.url}")
    monkeypatch.setattr(httpx.AsyncClient, "send", send)


@pytest.fixture
def db(monkeypatch):
    """{table: row | [rows] | exception}; SELECT only."""
    rows: dict[str, object] = {}
    seen: list[tuple[str, tuple]] = []

    def _answer(sql, args):
        assert sql.lstrip().upper().startswith("SELECT"), f"a verifier wrote: {sql}"
        seen.append((sql, args))
        got = rows.get(re.search(r"FROM\s+(\w+)", sql).group(1))
        if isinstance(got, Exception):
            raise got
        return got

    async def fetch_one(sql, *args):
        got = _answer(sql, args)
        return got[0] if isinstance(got, list) else got

    async def fetch_all(sql, *args):
        got = _answer(sql, args)
        return got if isinstance(got, list) else ([got] if got else [])

    monkeypatch.setattr(state, "fetch_one", fetch_one)
    monkeypatch.setattr(state, "fetch_all", fetch_all)
    rows["_seen"] = seen
    return rows


def _no_secret(got: v.Verification) -> None:
    assert SECRET not in json.dumps(got.as_audit(), default=str)


def _h(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


@pytest.mark.parametrize("action", ACTIONS)
def test_each_action_has_a_verifier(action):
    assert v.has_verifier(action)


def test_reel_publish_verifiers_untouched():
    from atlas.modules.content import readback
    assert v._VERIFIERS["content.publish_reel"] is readback._verify_reel
    assert v._VERIFIERS["content.autopublish_reel"] is readback._verify_reel


@pytest.mark.parametrize("action", ACTIONS)
async def test_a_raising_read_is_unknown(action, db, tmp_path, monkeypatch):
    for table in ("content_drafts", "reel_series", "reel_formats", "content_media",
                  "reel_calendar", "purchasing_prepared", "reel_series_parts"):
        db[table] = RuntimeError(f"db down {SECRET}")
    monkeypatch.setattr(calendar, "missing", lambda j: [])
    job = calendar.jobs("stories")[0]["job_id"]
    params = {"kind": "linkedin_post", "source": "weekly", "format_id": 3,
              "series_id": 4, "format_name": "f", "draft_id": 5, "job_id": job,
              "items": "ssd", "origin": "JFK", "destination": "CAI",
              "date": "2026-10-01"}
    res = ActionResult(True, "ok", evidence={"id": 1, "part": 1, "filed": [1],
                                             "media_id": 1, "left": 0},
                       rollback_token="1")
    got = await v.verify(action, params, res)
    assert got.state is v.State.UNKNOWN
    _no_secret(got)


# ================================================================ content.draft

BODY = "Cheap flights to Cairo, this week only."
DRAFT = {"id": 7, "kind": "linkedin_post", "source": "weekly", "body": BODY,
         "status": "draft"}
DRAFT_RES = ActionResult(True, "drafted", evidence={
    "id": 7, "kind": "linkedin_post", "title": "t", "body_sha256": _h(BODY)},
    rollback_token="7")
DRAFT_PARAMS = {"kind": "linkedin_post", "source": "weekly"}


async def test_draft_verified(db):
    db["content_drafts"] = dict(DRAFT)
    got = await v.verify("content.draft", DRAFT_PARAMS, DRAFT_RES)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["body_sha256"] == _h(BODY)
    assert BODY not in json.dumps(got.as_audit())
    _no_secret(got)


@pytest.mark.parametrize("row,why", [
    (None, "not in content_drafts"),
    ({**DRAFT, "status": "dropped"}, "dropped"),
    ({**DRAFT, "kind": "ebay_listing"}, "not a 'linkedin_post'"),
    ({**DRAFT, "source": "voice:3"}, "another source"),
    ({**DRAFT, "body": "something else"}, "not the one drafted"),
])
async def test_draft_contradicted(db, row, why):
    db["content_drafts"] = row
    got = await v.verify("content.draft", DRAFT_PARAMS, DRAFT_RES)
    assert got.state is v.State.CONTRADICTED and why in got.reason
    _no_secret(got)


async def test_draft_unknown_without_an_id(db):
    got = await v.verify("content.draft", DRAFT_PARAMS, ActionResult(True, "ok"))
    assert got.state is v.State.UNKNOWN and "no draft id" in got.reason
    assert db["_seen"] == []


async def test_draft_run_records_the_body_hash(db, monkeypatch):
    from atlas.modules.content import drafts

    class C:
        text = BODY

    async def gather(source):
        return "wrapped", None

    async def think(*a, **k):
        return C()

    async def fetch_one(sql, *args):
        if sql.lstrip().upper().startswith("SELECT"):
            return {"n": 0}
        assert args[3] == BODY
        return {"id": 7}

    monkeypatch.setattr(drafts, "gather", gather)
    monkeypatch.setattr(drafts, "think", think)
    monkeypatch.setattr(drafts, "fetch_one", fetch_one)
    monkeypatch.setattr(drafts, "_brand_voice", _none)
    res = await drafts.draft("short_script", "weekly")
    assert res.ok and res.evidence["body_sha256"] == _h(BODY)


async def _none(*_a, **_k):
    return None


# ================================================================ content.draft_reel

REEL_BODY = "FORMAT: talking vegetables\nBEAT: hi\nCAPTION: x Made with AI"
REEL_ROW = {"id": 9, "kind": "story_script", "source": "reel_format:3",
            "body": REEL_BODY, "status": "draft"}
REEL_RES = ActionResult(True, "drafted", evidence={
    "id": 9, "format": "talking vegetables", "body_sha256": _h(REEL_BODY)},
    rollback_token="9")


async def test_draft_reel_verified(db):
    db["content_drafts"] = dict(REEL_ROW)
    got = await v.verify("content.draft_reel", {"format_id": 3}, REEL_RES)
    assert got.state is v.State.VERIFIED, got.reason
    _no_secret(got)


async def test_draft_reel_contradicted_by_another_format(db):
    db["content_drafts"] = {**REEL_ROW, "source": "reel_format:4"}
    got = await v.verify("content.draft_reel", {"format_id": 3}, REEL_RES)
    assert got.state is v.State.CONTRADICTED and "another source" in got.reason


async def test_draft_reel_unknown_without_an_id(db):
    got = await v.verify("content.draft_reel", {"format_id": 3},
                         ActionResult(True, "ok", evidence={"format": "x"}))
    assert got.state is v.State.UNKNOWN


# ================================================================ content.draft_series_part

PART_ROW = {"id": 11, "kind": "story_script", "source": "series:4:2",
            "body": REEL_BODY, "status": "draft"}
PART_RES = ActionResult(True, "drafted", evidence={
    "id": 11, "part": 2, "total": 5, "final": False, "body_sha256": _h(REEL_BODY)},
    rollback_token="11")


async def test_draft_series_part_verified(db):
    db["content_drafts"] = dict(PART_ROW)
    db["reel_series_parts"] = {"series_id": 4, "part_no": 2, "draft_id": 11,
                               "status": "drafted"}
    got = await v.verify("content.draft_series_part", {"series_id": 4}, PART_RES)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["part"] == 2 and got.evidence["part_status"] == "drafted"
    _no_secret(got)


@pytest.mark.parametrize("parts,why", [
    (None, "no part 2 row"),
    ({"series_id": 4, "part_no": 2, "draft_id": 12, "status": "drafted"},
     "another draft"),
])
async def test_draft_series_part_contradicted(db, parts, why):
    db["content_drafts"] = dict(PART_ROW)
    db["reel_series_parts"] = parts
    got = await v.verify("content.draft_series_part", {"series_id": 4}, PART_RES)
    assert got.state is v.State.CONTRADICTED and why in got.reason


async def test_draft_series_part_unknown_without_a_part(db):
    got = await v.verify("content.draft_series_part", {"series_id": 4},
                         ActionResult(True, "ok", rollback_token="11"))
    assert got.state is v.State.UNKNOWN


# ================================================================ content.plan_series

SERIES = {"id": 4, "title": "The Night Shift", "format": "hospital er comedy series",
          "outline": json.dumps(["a", "b", "c"]), "total_parts": 3,
          "status": "queued"}
SERIES_RES = ActionResult(True, "planned", evidence={"id": 4,
                                                     "title": "The Night Shift"},
                          rollback_token="4")
SERIES_PARAMS = {"format_name": "hospital er comedy series"}


async def test_plan_series_verified(db):
    db["reel_series"] = dict(SERIES)
    got = await v.verify("content.plan_series", SERIES_PARAMS, SERIES_RES)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["outline_parts"] == 3
    _no_secret(got)


@pytest.mark.parametrize("row,why", [
    (None, "not in reel_series"),
    ({**SERIES, "status": "dropped"}, "dropped"),
    ({**SERIES, "format": "rap battle"}, "another format"),
    ({**SERIES, "title": "Other"}, "title"),
    ({**SERIES, "outline": ["a"]}, "outline"),
])
async def test_plan_series_contradicted(db, row, why):
    db["reel_series"] = row
    got = await v.verify("content.plan_series", SERIES_PARAMS, SERIES_RES)
    assert got.state is v.State.CONTRADICTED and why in got.reason


async def test_plan_series_unknown_without_an_id(db):
    got = await v.verify("content.plan_series", SERIES_PARAMS,
                         ActionResult(True, "ok"))
    assert got.state is v.State.UNKNOWN


# ================================================================ content.reel_research

RESEARCH_RES = ActionResult(True, "filed", evidence={"insights": "unknown",
                                                     "filed": [21, 22]},
                            rollback_token="[21, 22]")


async def test_reel_research_verified(db):
    db["reel_formats"] = [{"id": 21, "source": "perplexity", "retired_at": None},
                          {"id": 22, "source": "perplexity", "retired_at": None}]
    got = await v.verify("content.reel_research", {}, RESEARCH_RES)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["found"] == [21, 22]
    _no_secret(got)


@pytest.mark.parametrize("rows,why", [
    ([{"id": 21, "source": "perplexity", "retired_at": None}], "[22]"),
    ([{"id": 21, "source": "perplexity", "retired_at": None},
      {"id": 22, "source": "perplexity", "retired_at": "2026-09-28"}], "retired"),
])
async def test_reel_research_contradicted(db, rows, why):
    db["reel_formats"] = rows
    got = await v.verify("content.reel_research", {}, RESEARCH_RES)
    assert got.state is v.State.CONTRADICTED and why in got.reason


async def test_reel_research_unknown_when_nothing_was_filed(db):
    got = await v.verify("content.reel_research", {}, ActionResult(
        True, "filed 0", evidence={"insights": "unknown", "filed": []}))
    assert got.state is v.State.UNKNOWN and "nothing" in got.reason
    assert db["_seen"] == []


# ================================================================ renders

@pytest.fixture
def probe(monkeypatch):
    """ffprobe present, answering `probed` for any file."""
    probed = {"width": 1080, "height": 1920, "seconds": 24.0, "audio": True,
              "video": True, "video_seconds": 24.0, "rotation": 0,
              "subtitles": False}
    calls: list[str] = []
    monkeypatch.setattr(ffmpeg, "binary", lambda name="ffmpeg": f"/usr/bin/{name}")

    async def fake(path):
        calls.append(path)
        return dict(probed)

    monkeypatch.setattr(ffmpeg, "probe", fake)
    probed["_calls"] = calls
    return probed


def _media(tmp_path, *, size=2048, **over):
    path = tmp_path / "42.mp4"
    if size is not None:
        path.write_bytes(b"\0" * size)
    return {"id": 42, "draft_id": 5, "status": "done", "path": str(path),
            "provider": "veo", **over}


RENDER_RES = ActionResult(True, "rendered", evidence={
    "media_id": 42, "backend": "veo", "path": "/x/42.mp4", "public": True},
    rollback_token="42")


async def test_render_video_verified(db, tmp_path, probe):
    db["content_media"] = _media(tmp_path)
    got = await v.verify("content.render_video", {"draft_id": 5}, RENDER_RES)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["seconds"] == 24.0 and got.evidence["bytes"] == 2048
    assert "media_token" not in db["_seen"][0][0]
    _no_secret(got)


@pytest.mark.parametrize("over,why", [
    ({"status": "failed"}, "failed, not done"),
    ({"draft_id": 6}, "another draft"),
])
async def test_render_video_contradicted_row(db, tmp_path, probe, over, why):
    db["content_media"] = _media(tmp_path, **over)
    got = await v.verify("content.render_video", {"draft_id": 5}, RENDER_RES)
    assert got.state is v.State.CONTRADICTED and why in got.reason


async def test_render_video_contradicted_missing_or_empty_file(db, tmp_path, probe):
    db["content_media"] = _media(tmp_path, size=None)
    got = await v.verify("content.render_video", {"draft_id": 5}, RENDER_RES)
    assert got.state is v.State.CONTRADICTED and "not on disk" in got.reason
    db["content_media"] = _media(tmp_path, size=0)
    got = await v.verify("content.render_video", {"draft_id": 5}, RENDER_RES)
    assert got.state is v.State.CONTRADICTED and "empty" in got.reason
    assert got.failure_class is v.FailureClass.QUALITY


async def test_render_video_contradicted_no_duration(db, tmp_path, probe):
    probe.update(seconds=0.0)
    db["content_media"] = _media(tmp_path)
    got = await v.verify("content.render_video", {"draft_id": 5}, RENDER_RES)
    assert got.state is v.State.CONTRADICTED and "no playable video" in got.reason


async def test_render_video_unknown_without_ffprobe(db, tmp_path, monkeypatch):
    monkeypatch.setattr(ffmpeg, "binary", lambda name="ffmpeg": None)

    async def boom(path):
        raise AssertionError("probed without ffprobe")

    monkeypatch.setattr(ffmpeg, "probe", boom)
    db["content_media"] = _media(tmp_path)
    got = await v.verify("content.render_video", {"draft_id": 5}, RENDER_RES)
    assert got.state is v.State.UNKNOWN and "ffprobe is not installed" in got.reason
    assert got.evidence["bytes"] == 2048


async def test_render_video_unknown_when_probe_fails(db, tmp_path, monkeypatch):
    monkeypatch.setattr(ffmpeg, "binary", lambda name="ffmpeg": "/usr/bin/x")

    async def fails(path):
        raise ffmpeg.FfmpegError("ffprobe passed 30s and was stopped")

    monkeypatch.setattr(ffmpeg, "probe", fails)
    db["content_media"] = _media(tmp_path)
    got = await v.verify("content.render_video", {"draft_id": 5}, RENDER_RES)
    assert got.state is v.State.UNKNOWN


async def test_render_video_unknown_no_file_or_no_id(db, tmp_path, probe):
    db["content_media"] = _media(tmp_path, path=None)
    got = await v.verify("content.render_video", {"draft_id": 5}, RENDER_RES)
    assert got.state is v.State.UNKNOWN and "no file" in got.reason
    got = await v.verify("content.render_video", {"draft_id": 5},
                         ActionResult(True, "ok"))
    assert got.state is v.State.UNKNOWN and "no media id" in got.reason


# ================================================================ content.render_calendar_job

JOB = calendar.jobs("stories")[0]["job_id"]


async def test_calendar_complete_verified(db, tmp_path, probe):
    db["reel_calendar"] = {"job_id": JOB, "status": "rendered", "draft_id": 5,
                           "media_id": 42}
    db["content_media"] = _media(tmp_path)
    res = ActionResult(True, "rendered", evidence={
        "media_id": 42, "complete": True, "made": 0, "left": 0},
        rollback_token="42")
    got = await v.verify("content.render_calendar_job", {"job_id": JOB}, res)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["job_id"] == JOB and got.evidence["seconds"] == 24.0
    _no_secret(got)
    db["reel_calendar"] = {**db["reel_calendar"], "status": "carded"}
    got = await v.verify("content.render_calendar_job", {"job_id": JOB}, res)
    assert got.state is v.State.VERIFIED, got.reason


async def test_calendar_complete_contradicted(db, tmp_path, probe):
    db["reel_calendar"] = {"job_id": JOB, "status": "rendering", "draft_id": 5,
                           "media_id": None}
    db["content_media"] = _media(tmp_path)
    res = ActionResult(True, "rendered", evidence={"complete": True, "left": 0},
                       rollback_token="42")
    got = await v.verify("content.render_calendar_job", {"job_id": JOB}, res)
    assert got.state is v.State.CONTRADICTED and "not rendered" in got.reason


async def test_calendar_tick_verified(db, monkeypatch):
    j = calendar.job(JOB)
    parts = calendar.plan(j)
    monkeypatch.setattr(calendar, "missing", lambda _j: parts[:1])
    db["reel_calendar"] = {"job_id": JOB, "status": "rendering", "draft_id": None,
                           "media_id": None}
    res = ActionResult(True, "banked", evidence={"complete": False, "made": 2,
                                                 "left": 1})
    got = await v.verify("content.render_calendar_job", {"job_id": JOB}, res)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["left"] == 1 and got.evidence["clips"] == len(parts)
    _no_secret(got)


async def test_calendar_tick_contradicted(db, monkeypatch):
    parts = calendar.plan(calendar.job(JOB))
    monkeypatch.setattr(calendar, "missing", lambda _j: parts[:2])
    db["reel_calendar"] = {"job_id": JOB, "status": "rendering", "draft_id": None,
                           "media_id": None}
    res = ActionResult(True, "banked", evidence={"complete": False, "left": 1})
    got = await v.verify("content.render_calendar_job", {"job_id": JOB}, res)
    assert got.state is v.State.CONTRADICTED and "missing on disk" in got.reason
    monkeypatch.setattr(calendar, "missing", lambda _j: [])
    res = ActionResult(True, "blocked", evidence={"complete": False, "left": 1,
                                                  "blocked": True})
    got = await v.verify("content.render_calendar_job", {"job_id": JOB}, res)
    assert got.state is v.State.CONTRADICTED and "blocked" in got.reason
    db["reel_calendar"] = None
    got = await v.verify("content.render_calendar_job", {"job_id": JOB}, res)
    assert got.state is v.State.CONTRADICTED and "no reel_calendar row" in got.reason


async def test_calendar_unknown(db):
    got = await v.verify("content.render_calendar_job", {"job_id": "no-such-job"},
                         ActionResult(True, "ok", evidence={"left": 0}))
    assert got.state is v.State.UNKNOWN and db["_seen"] == []
    db["reel_calendar"] = {"job_id": JOB, "status": "rendering", "draft_id": None,
                           "media_id": None}
    got = await v.verify("content.render_calendar_job", {"job_id": JOB},
                         ActionResult(True, "ok", evidence={"complete": False}))
    assert got.state is v.State.UNKNOWN and "clips left" in got.reason


# ================================================================ purchasing

OPTIONS = [{"title": "240GB SSD", "price": 19.5, "currency": "USD",
            "url": "https://www.ebay.com/itm/1", "source": "ebay",
            "seen_at": "2026-09-28T10:00:00+00:00"},
           {"title": "Cat6 50ft", "price": 9.0, "currency": "USD",
            "url": "https://www.ebay.com/itm/2", "source": "ebay",
            "seen_at": "2026-09-28T10:00:01+00:00"}]
CART = {"id": 3, "kind": "cart", "request": "240GB SSD, Cat6 cable 50ft",
        "options": json.dumps(OPTIONS), "checkout_url": None, "withdrawn": False}
CART_PARAMS = {"items": "240GB SSD, Cat6 cable 50ft"}
CART_RES = ActionResult(True, "prepared", evidence={"options": OPTIONS,
                                                    "checkout_url": None},
                        rollback_token="3")


async def test_prepare_cart_verified(db):
    db["purchasing_prepared"] = dict(CART)
    got = await v.verify("purchasing.prepare_cart", CART_PARAMS, CART_RES)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence == {"prepared_id": 3, "kind": "cart", "withdrawn": False,
                            "options": 2, "has_checkout_url": False}
    _no_secret(got)


@pytest.mark.parametrize("row,why", [
    (None, "not in purchasing_prepared"),
    ({**CART, "withdrawn": True}, "withdrawn"),
    ({**CART, "kind": "flight"}, "not a 'cart'"),
    ({**CART, "request": "RAM"}, "another request"),
    ({**CART, "options": json.dumps(OPTIONS[:1])}, "1 option(s)"),
    ({**CART, "options": json.dumps([OPTIONS[0], {**OPTIONS[1], "url": "u"}])},
     "listing links"),
    ({**CART, "options": "[]"}, "no options"),
    ({**CART, "checkout_url": "https://www.ebay.com/itm/1"}, "checkout link"),
])
async def test_prepare_cart_contradicted(db, row, why):
    db["purchasing_prepared"] = row
    got = await v.verify("purchasing.prepare_cart", CART_PARAMS, CART_RES)
    assert got.state is v.State.CONTRADICTED and why in got.reason, got.reason


async def test_prepare_cart_unknown_without_an_id(db):
    got = await v.verify("purchasing.prepare_cart", CART_PARAMS,
                         ActionResult(False, "eBay search not available"))
    assert got.state is v.State.UNKNOWN and db["_seen"] == []


async def test_prepare_flight_is_honestly_unknown_today(db):
    """flycheap has no fare search: the run can never write a row."""
    from atlas.modules.purchasing import prepare_flight
    res = await prepare_flight("JFK", "CAI", "2026-10-01")
    assert not res.ok and res.rollback_token is None
    got = await v.verify("purchasing.prepare_flight",
                         {"origin": "JFK", "destination": "CAI",
                          "date": "2026-10-01"}, res)
    assert got.state is v.State.UNKNOWN
    assert got.failure_class is v.FailureClass.DISABLED
    assert "fare-search" in got.reason and db["_seen"] == []
    _no_secret(got)


FLIGHT_OPTS = [{"title": "JFK-CAI", "price": 540.0, "currency": "USD",
                "url": "https://flycheap.example.com/go/1"}]
FLIGHT = {"id": 8, "kind": "flight", "request": "JFK -> CAI on 2026-10-01",
          "options": FLIGHT_OPTS, "checkout_url": "https://flycheap.example.com/go/1",
          "withdrawn": False}
FLIGHT_PARAMS = {"origin": "JFK", "destination": "CAI", "date": "2026-10-01"}
FLIGHT_RES = ActionResult(True, "prepared", evidence={
    "options": FLIGHT_OPTS, "checkout_url": "https://flycheap.example.com/go/1",
    "good_time": "no history"}, rollback_token="8")


async def test_prepare_flight_row_reads_back_once_a_fare_search_exists(db):
    db["purchasing_prepared"] = dict(FLIGHT)
    got = await v.verify("purchasing.prepare_flight", FLIGHT_PARAMS, FLIGHT_RES)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["has_checkout_url"] is True
    db["purchasing_prepared"] = {**FLIGHT, "request": "JFK -> LHR on 2026-10-01"}
    got = await v.verify("purchasing.prepare_flight", FLIGHT_PARAMS, FLIGHT_RES)
    assert got.state is v.State.CONTRADICTED and "another request" in got.reason
