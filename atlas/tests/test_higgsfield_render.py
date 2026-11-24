"""The daily reel path falls through to Higgsfield (the owner, 2026-09-29).

Veo stays first. When Higgsfield is configured and Veo's month
(`VEO_MONTHLY_SECONDS`) or day (`VEO_DAILY_CLIPS`, the reserve included) cannot
make every beat of a story -- or Veo answers a daily-quota 429 mid-story --
`render.render_veo` makes the rest on Higgsfield, every overflow clip's price
reserved under `HIGGSFIELD_MONTHLY_USD` before the next clip, or the story
fails whole. Without Higgsfield keys nothing changes. Ported from the peer
branch's test_higgsfield_fallback, onto this module and its dollar ledger."""
from __future__ import annotations

import pathlib
from decimal import Decimal

import pytest

from atlas.kernel.contracts import ActionResult
from atlas.modules import content
from atlas.modules.content import calendar as cal
from atlas.modules.content import captions, ffmpeg, reel_script, render, veo
from atlas.modules.content import higgsfield as hf

CLIFF = reel_script.compose(
    'cliffhanger "Part 1" story',
    ["The lighthouse keeper had not seen a ship in forty years.",
     "Tonight a light answered his.",
     "It was coming from under the water.",
     "And it was spelling his name."],
    "Nobody told him the sea was gone.",
    characters="an old lighthouse keeper, grey beard, yellow oilskin coat")


@pytest.fixture
def fake_ffmpeg(monkeypatch, tmp_path):
    ran: list[list[str]] = []
    monkeypatch.setattr(ffmpeg, "binary", lambda name="ffmpeg": f"/usr/bin/{name}")

    async def fake_run(argv, *, timeout=ffmpeg.TIMEOUT_S):
        ran.append(list(argv))
        if argv[-1].endswith((".mp4", ".mp3")):
            pathlib.Path(argv[-1]).write_bytes(b"media")
        return b""

    async def fake_duration(path):
        return 32.0

    async def fake_words(audio, filename="reel.mp3"):
        return [captions.Word("The", 0.0, 0.3)], None

    monkeypatch.setattr(ffmpeg, "run", fake_run)
    monkeypatch.setattr(ffmpeg, "duration", fake_duration)
    monkeypatch.setattr(captions, "word_timings", fake_words)
    monkeypatch.setattr(content, "DATA_DIR", str(tmp_path / "content"))
    monkeypatch.setenv("ATLAS_HOST", "atlas.example")
    monkeypatch.setenv("REELS_EXPORT_4K", "0")
    return ran


@pytest.fixture
def veo_on(monkeypatch, memory_backend):
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("VEO_MONTHLY_SECONDS", "600")
    monkeypatch.setenv("VEO_DAILY_CLIPS", "10")
    monkeypatch.delenv("OPENART_TOKEN", raising=False)
    memory_backend.responses["FROM veo_usage"] = {"seconds": 0}
    memory_backend.responses["INSERT INTO content_media"] = {"id": 42}
    return memory_backend


@pytest.fixture
def hf_off(monkeypatch):
    monkeypatch.delenv(hf.KEY_ID_ENV, raising=False)
    monkeypatch.delenv(hf.SECRET_ENV, raising=False)

    async def unread(now=None):
        raise AssertionError("Veo's day is not read without Higgsfield keys")

    monkeypatch.setattr(cal, "clips_today", unread)


@pytest.fixture
def hf_on(monkeypatch):
    """Higgsfield keyed, its ledger at $2 of $20, every door faked."""
    monkeypatch.setenv(hf.KEY_ID_ENV, "fake-id")
    monkeypatch.setenv(hf.SECRET_ENV, "fake-secret")
    monkeypatch.delenv("HIGGSFIELD_MONTHLY_USD", raising=False)
    log: dict[str, list] = {"reserve": [], "hf": [], "refund": [], "veo": [],
                            "today": 0, "price": Decimal("0.80"), "read": True}

    async def today(now=None):
        return log["today"]

    async def spent():
        return Decimal(2)

    async def reserve_all(prompts, *, job_id, parts):
        log["reserve"].append((list(parts), job_id))
        return [hf.Price(log["price"], "estimate" if log["read"] else "worst case",
                         log["read"], 8, "1080p") for _ in prompts], None

    async def clip(prompt):
        log["hf"].append(prompt)
        return b"HF"

    async def refund(price, *, job_id, part):
        log["refund"].append((price.usd, part))

    async def veo_clip(prompt):
        log["veo"].append(prompt)
        return b"VEO"

    monkeypatch.setattr(cal, "clips_today", today)
    monkeypatch.setattr(hf, "spent_this_month", spent)
    monkeypatch.setattr(hf, "reserve_all", reserve_all)
    monkeypatch.setattr(hf, "clip", clip)
    monkeypatch.setattr(hf, "refund", refund)
    monkeypatch.setattr(veo, "clip", veo_clip)
    return log


def _veo_usage(memory_backend):
    return [args[0] for sql, args in memory_backend.sql_log
            if "INSERT INTO veo_usage" in sql]


# ================================================================ choosing

async def test_without_higgsfield_keys_nothing_changes(veo_on, fake_ffmpeg, hf_off):
    choice = await render.choose_backend(reel_script.parse(CLIFF))
    assert choice.backend == "veo"


async def test_veos_day_short_chooses_veo_then_higgsfield(veo_on, fake_ffmpeg,
                                                          hf_on):
    hf_on["today"] = 8                                  # 2 left, story needs 4
    choice = await render.choose_backend(reel_script.parse(CLIFF))
    assert choice.backend == render.VEO_AND_HIGGSFIELD
    assert "Veo for 2 of 4" in choice.why and "8/10 used today" in choice.why
    assert "$2.00 of $20.00" in choice.why and "UTC" in choice.why


async def test_veos_month_short_chooses_veo_then_higgsfield(veo_on, fake_ffmpeg,
                                                            hf_on):
    veo_on.responses["FROM veo_usage"] = {"seconds": 584}   # 16 s left: 2 clips
    choice = await render.choose_backend(reel_script.parse(CLIFF))
    assert choice.backend == render.VEO_AND_HIGGSFIELD
    assert "Veo for 2 of 4" in choice.why and "584/600" in choice.why


async def test_higgsfield_at_its_cap_leaves_veo_as_it_was(veo_on, fake_ffmpeg,
                                                          hf_on, monkeypatch):
    async def full():
        return Decimal(20)

    monkeypatch.setattr(hf, "spent_this_month", full)
    hf_on["today"] = 10
    choice = await render.choose_backend(reel_script.parse(CLIFF))
    assert choice.backend == "veo" and "Higgsfield cap reached" in choice.why


# ================================================================ rendering

async def test_veo_makes_what_its_day_allows_and_higgsfield_the_rest(
        veo_on, fake_ffmpeg, hf_on):
    hf_on["today"] = 8
    result = await render.render_veo(7, reel_script.parse(CLIFF))
    assert result.ok is True, result.detail
    assert len(hf_on["veo"]) == 2 and len(hf_on["hf"]) == 2
    assert hf_on["reserve"] == [(["beat-3", "beat-4"], "media:42")]
    assert _veo_usage(veo_on) == [16], "only Veo's own clips are Veo seconds"
    normalised = [a for a in fake_ffmpeg if a[-1].endswith(("clip_3.mp4",
                                                            "clip_4.mp4"))]
    assert len(normalised) == 2 and all("48000" in a for a in normalised)
    assert "2 Veo clip(s)" in result.detail
    assert "2 Higgsfield overflow clip(s)" in result.detail
    assert "$1.60 by Higgsfield's estimate" in result.detail
    assert result.evidence["backend"] == render.VEO_AND_HIGGSFIELD
    assert hf_on["refund"] == []


async def test_veos_monthly_cap_falls_through_under_the_dollar_cap(
        veo_on, fake_ffmpeg, hf_on):
    veo_on.responses["FROM veo_usage"] = {"seconds": 584}
    result = await render.render_veo(7, reel_script.parse(CLIFF))
    assert result.ok is True, result.detail
    assert len(hf_on["veo"]) == 2 and len(hf_on["hf"]) == 2
    assert _veo_usage(veo_on) == [16]


async def test_veo_with_no_room_at_all_is_a_whole_higgsfield_story(
        veo_on, fake_ffmpeg, hf_on):
    hf_on["today"] = 10
    result = await render.render_veo(7, reel_script.parse(CLIFF))
    assert result.ok is True, result.detail
    assert hf_on["veo"] == [] and len(hf_on["hf"]) == 4
    assert _veo_usage(veo_on) == [], "no Veo seconds booked for no Veo clips"


async def test_a_daily_quota_429_mid_story_moves_the_rest_to_higgsfield(
        veo_on, fake_ffmpeg, hf_on, monkeypatch):
    calls = {"n": 0}

    async def veo_clip(prompt):
        calls["n"] += 1
        if calls["n"] == 2:
            raise veo.VeoError("per day", status_code=429, daily_quota=True)
        return b"VEO"

    async def no_wait(seconds):
        raise AssertionError("a daily quota is not waited out when Higgsfield "
                             "can take the rest")

    monkeypatch.setattr(veo, "clip", veo_clip)
    monkeypatch.setattr(render.asyncio, "sleep", no_wait)
    result = await render.render_veo(7, reel_script.parse(CLIFF))
    assert result.ok is True, result.detail
    assert calls["n"] == 2 and len(hf_on["hf"]) == 3
    assert hf_on["reserve"] == [(["beat-2", "beat-3", "beat-4"], "media:42")]
    assert _veo_usage(veo_on) == [32, -24], "Veo's unused seconds go back"


async def test_a_402_credits_depleted_mid_story_moves_the_rest_to_higgsfield(
        veo_on, fake_ffmpeg, hf_on, monkeypatch):
    """Seen live 2026-09-30: Veo answered HTTP 402 "prepayment credits are
    depleted" on beat 1 and the story failed whole, though Higgsfield had
    room. A depleted balance is an exhausted day that no backoff brings
    back: the rest goes to Higgsfield, and nothing waits."""
    calls = {"n": 0}

    async def veo_clip(prompt):
        calls["n"] += 1
        if calls["n"] == 2:
            raise veo.VeoError("Veo submit failed: HTTP 402: Your prepayment "
                               "credits are depleted", status_code=402)
        return b"VEO"

    async def no_wait(seconds):
        raise AssertionError("a depleted balance is not waited out")

    monkeypatch.setattr(veo, "clip", veo_clip)
    monkeypatch.setattr(render.asyncio, "sleep", no_wait)
    result = await render.render_veo(7, reel_script.parse(CLIFF))
    assert result.ok is True, result.detail
    assert calls["n"] == 2 and len(hf_on["hf"]) == 3
    assert hf_on["reserve"] == [(["beat-2", "beat-3", "beat-4"], "media:42")]
    assert _veo_usage(veo_on) == [32, -24], "Veo's unused seconds go back"
    assert veo.depleted_since() is not None, "the check can now say so"


# ---------------------------------------------------- REELS_RENDERER=higgsfield

async def test_higgsfield_only_makes_every_beat_there_and_never_asks_veo(
        veo_on, fake_ffmpeg, hf_on, monkeypatch):
    """the owner, 2026-09-30 (relayed): "don't use veo, use higgsfield instead
    only". With the switch set, Veo is off: not first, not a fallback."""
    monkeypatch.setenv("REELS_RENDERER", "higgsfield")
    choice = await render.choose_backend(reel_script.parse(CLIFF))
    assert choice.backend == render.HIGGSFIELD_ONLY
    assert "Veo off" in choice.why
    result = await render.render_veo(7, reel_script.parse(CLIFF))
    assert result.ok is True, result.detail
    assert hf_on["veo"] == [] and len(hf_on["hf"]) == 4
    assert hf_on["reserve"] == [(["beat-1", "beat-2", "beat-3", "beat-4"],
                                 "media:42")]
    assert _veo_usage(veo_on) == [], "no Veo seconds booked, none refunded"
    assert result.evidence["backend"] == render.HIGGSFIELD_ONLY
    assert "Veo off" in result.detail


async def test_higgsfield_only_refused_fails_whole_and_never_falls_to_veo(
        veo_on, fake_ffmpeg, hf_on, monkeypatch):
    """No credits (403), or the cap: the story fails whole, and Veo is not
    asked -- a silent Veo fallback would be exactly what the owner turned off."""
    monkeypatch.setenv("REELS_RENDERER", "higgsfield")

    async def refused(prompts, *, job_id, parts):
        return None, "Higgsfield: insufficient credits (HTTP 403)"

    monkeypatch.setattr(hf, "reserve_all", refused)
    result = await render.render_veo(7, reel_script.parse(CLIFF))
    assert result.ok is False
    assert "Veo is off" in result.detail and "insufficient credits" in result.detail
    assert hf_on["veo"] == [] and hf_on["hf"] == []
    assert _veo_usage(veo_on) == []


async def test_higgsfield_only_without_keys_refuses_instead_of_veo(
        veo_on, fake_ffmpeg, hf_on, monkeypatch):
    monkeypatch.setenv("REELS_RENDERER", "higgsfield")
    monkeypatch.delenv(hf.KEY_ID_ENV, raising=False)
    result = await render.render_veo(7, reel_script.parse(CLIFF))
    assert result.ok is False
    assert "REELS_RENDERER=higgsfield" in result.detail and "not configured" in result.detail
    assert hf_on["veo"] == [] and hf_on["hf"] == []


async def test_the_overflow_refused_at_the_cap_fails_the_story_whole(
        veo_on, fake_ffmpeg, hf_on, monkeypatch):
    async def refused(prompts, *, job_id, parts):
        return None, "Higgsfield cap: 2 clip(s) need $6.00 ... refused as a whole"

    monkeypatch.setattr(hf, "reserve_all", refused)
    veo_on.responses["FROM veo_usage"] = {"seconds": 590}   # Veo's month: 1 clip
    result = await render.render_veo(7, reel_script.parse(CLIFF))
    assert result.ok is False
    assert "Higgsfield overflow refused" in result.detail
    assert "590/600" in result.detail
    assert hf_on["veo"] == [] and hf_on["hf"] == [], "no clip of a refused story"
    assert _veo_usage(veo_on) == [8, -8]


async def test_only_the_day_short_and_no_overflow_room_is_veo_as_before(
        veo_on, fake_ffmpeg, hf_on, monkeypatch):
    """Higgsfield at its cap late in the month: when Veo's month still holds
    the whole story, Veo is asked for every beat, as it was before
    Higgsfield -- the render agrees with what choose_backend said."""
    async def refused(prompts, *, job_id, parts):
        return None, "Higgsfield cap: no room"

    monkeypatch.setattr(hf, "reserve_all", refused)
    hf_on["today"] = 8
    result = await render.render_veo(7, reel_script.parse(CLIFF))
    assert result.ok is True, result.detail
    assert len(hf_on["veo"]) == 4 and hf_on["hf"] == []
    assert _veo_usage(veo_on) == [16, 16], "the rest of the story booked first"
    assert "so Veo for every beat, as before" in result.detail
    assert result.evidence["backend"] == "veo"


async def test_a_daily_429_with_no_overflow_room_fails_as_before(
        veo_on, fake_ffmpeg, hf_on, monkeypatch):
    async def daily(prompt):
        raise veo.VeoError("per day", status_code=429, daily_quota=True)

    async def refused(prompts, *, job_id, parts):
        return None, "Higgsfield cap: no room"

    monkeypatch.setattr(veo, "clip", daily)
    monkeypatch.setattr(hf, "reserve_all", refused)
    result = await render.render_veo(7, reel_script.parse(CLIFF))
    assert result.ok is False and result.evidence == {"veo_quota": "daily quota"}
    assert "Veo daily quota exhausted" in result.detail
    assert "Higgsfield cap: no room" in result.detail


async def test_an_uncharged_higgsfield_failure_refunds_it_and_the_rest(
        veo_on, fake_ffmpeg, hf_on, monkeypatch):
    async def failed(prompt):
        raise hf.HiggsfieldError("Higgsfield ended failed", charged=False)

    monkeypatch.setattr(hf, "clip", failed)
    hf_on["today"] = 8
    result = await render.render_veo(7, reel_script.parse(CLIFF))
    assert result.ok is False and "Higgsfield beat 3/4" in result.detail
    assert sorted(p for _, p in hf_on["refund"]) == ["beat-3", "beat-4"]


async def test_a_charged_higgsfield_failure_keeps_its_reservation(
        veo_on, fake_ffmpeg, hf_on, monkeypatch):
    async def slow(prompt):
        raise hf.HiggsfieldError("did not finish", charged=True)

    monkeypatch.setattr(hf, "clip", slow)
    hf_on["today"] = 8
    result = await render.render_veo(7, reel_script.parse(CLIFF))
    assert result.ok is False
    assert hf_on["refund"] == [(Decimal("0.80"), "beat-4")], (
        "beat 3 may be billed; only beat 4, never asked for, goes back")


async def test_an_unread_overflow_price_is_said_as_not_read(veo_on, fake_ffmpeg,
                                                            hf_on):
    hf_on["today"] = 8
    hf_on["read"], hf_on["price"] = False, Decimal("3.00")
    result = await render.render_veo(7, reel_script.parse(CLIFF))
    assert result.ok is True, result.detail
    assert "price not read for 2 clip(s), $6.00 booked at worst case" in result.detail
    assert "by Higgsfield's estimate" not in result.detail


async def test_without_keys_veos_cap_still_refuses_the_story_whole(
        veo_on, fake_ffmpeg, hf_off, monkeypatch):
    veo_on.responses["FROM veo_usage"] = {"seconds": 584}

    async def boom(prompt):
        raise AssertionError("no clip past the cap")

    monkeypatch.setattr(veo, "clip", boom)
    result = await render.render_veo(7, reel_script.parse(CLIFF))
    assert result.ok is False and "as a whole" in result.detail


async def test_render_video_runs_the_mixed_backend(veo_on, fake_ffmpeg, hf_on,
                                                   monkeypatch):
    seen = []

    async def choice(script, backend):
        return render.Choice(render.VEO_AND_HIGGSFIELD, "why")

    async def rendered(draft_id, script):
        seen.append(draft_id)
        return ActionResult(True, "ok")

    async def draft(draft_id):
        return {"body": CLIFF}, None

    monkeypatch.setattr(content, "_choice", choice)
    monkeypatch.setattr(render, "render_veo", rendered)
    monkeypatch.setattr(content, "_draft_for_render", draft)
    result = await content.render_video(5)
    assert result.ok and seen == [5]
