"""content importer: a reel made outside ATLAS is filed and handed to the
reels path. An unreadable file, a caption without the disclosure, a path
outside data/import and a second import of the same part are refused before
any row exists; a file that fails a quality check (size, length, sound,
captions) is filed and carded with the quality exception (RA-E5), which
tests/test_reels_quality.py covers through the real gate."""
from __future__ import annotations

import pytest

from atlas.kernel import reels
from atlas.modules.content import ffmpeg, importer
from atlas.modules.content.drafts import AI_LABEL

CAPTION = f"Part 2. The parking spot was never free.\n\n{AI_LABEL}"
GOOD = {"width": 1080, "height": 1920, "seconds": 72.0, "audio": True,
        "video": True, "video_seconds": 72.0, "rotation": 0, "subtitles": False}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "import").mkdir(parents=True)
    video = tmp_path / "data" / "import" / "park2.mp4"
    video.write_bytes(b"mp4-bytes")
    state = {"sql": [], "drafts": [], "proposed": [], "existing": None,
             "probe": dict(GOOD), "level": -18.0,
             "how": "posted", "kw": []}

    async def fetch_one(sql, *args):
        state["sql"].append(sql)
        if "SELECT id FROM content_drafts" in sql:
            return state["existing"]
        if "INSERT INTO content_drafts" in sql:
            state["drafts"].append(args)
            return {"id": 7}
        if "INSERT INTO content_media" in sql:
            return {"id": 99}
        return None

    async def execute_sql(sql, *args):
        state["sql"].append(sql)

    async def probe(path):
        return state["probe"]

    async def mean_volume(path):
        return state["level"]

    async def post_or_card(params, reason, *, dedup, media_id, rollback_plan,
                           series, on_fail="card", seconds=None):
        state["proposed"].append((params, reason, dedup, media_id))
        state["kw"].append({"series": series, "on_fail": on_fail})
        return state["how"], (311 if state["how"] == "carded" else None), "done"

    monkeypatch.setattr(importer, "fetch_one", fetch_one)
    monkeypatch.setattr(importer, "execute_sql", execute_sql)
    monkeypatch.setattr(ffmpeg, "probe", probe)
    monkeypatch.setattr(ffmpeg, "mean_volume", mean_volume)

    async def moving(path, seconds):
        return 0.0

    monkeypatch.setattr(ffmpeg, "frozen_seconds", moving)
    monkeypatch.setattr(reels, "post_or_card", post_or_card)
    state["video"] = "data/import/park2.mp4"
    state["root"] = tmp_path
    return state


async def test_a_good_file_posts_itself_through_the_reels_path(world):
    """the owner, 2026-09-28: Veo makes the episode, ATLAS uploads it everywhere."""
    done = await importer.import_reel(world["video"], CAPTION,
                                      series="The Parking Spot", part=2,
                                      total_parts=3)
    assert done.ok and done.media_id == 99 and "posted" in done.detail
    [(params, reason, dedup, media_id)] = world["proposed"]
    assert params == {"media_id": 99, "caption": CAPTION} and media_id == 99
    assert "part 2 of 3" in reason and reels.PLATFORMS in reason
    assert dedup == "import-reel:import:The Parking Spot:part 2"
    assert (world["root"] / "data" / "content" / "99.mp4").read_bytes() == b"mp4-bytes"
    assert world["drafts"][0][0] == "import:The Parking Spot:part 2"


async def test_a_file_that_fails_a_post_check_becomes_a_card(world):
    world["how"] = "carded"
    done = await importer.import_reel(world["video"], CAPTION, series="S", part=1)
    assert done.ok and done.proposal_id == 311 and "card #311" in done.detail


async def test_the_same_part_twice_is_refused(world):
    world["existing"] = {"id": 7}
    done = await importer.import_reel(world["video"], CAPTION,
                                      series="The Parking Spot", part=2)
    assert not done.ok and "already imported" in done.detail
    assert world["proposed"] == []


@pytest.mark.parametrize("path", ["/etc/passwd", "data/import/../../x.mp4",
                                  "data/import", "data/import/missing.mp4"])
async def test_only_mp4_files_under_the_import_dir(world, path):
    done = await importer.import_reel(path, CAPTION)
    assert not done.ok and "data/import" in done.detail
    assert world["sql"] == []


@pytest.mark.parametrize("change,why", [
    ({"probe": {**GOOD, "video": False}}, "no video stream"),
    ({"probe": None}, "could not be read"),
])
async def test_an_unreadable_file_is_refused_before_any_row(world, change, why,
                                                            monkeypatch):
    world.update(change)
    if world["probe"] is None:
        async def broken(path):
            raise ffmpeg.FfmpegError("moov atom not found")
        monkeypatch.setattr(ffmpeg, "probe", broken)
    done = await importer.import_reel(world["video"], CAPTION)
    assert not done.ok and why in done.detail
    assert not any("INSERT" in s for s in world["sql"])


@pytest.mark.parametrize("change", [
    {"probe": {**GOOD, "width": 720, "height": 1280}},
    {"probe": {**GOOD, "audio": False}},
    {"level": -75.0},
    {"probe": {**GOOD, "video_seconds": 2.0}},
])
async def test_a_file_failing_a_quality_check_is_filed_for_a_card_not_dropped(
        world, change):
    """An exit code to a host job nobody reads was a silent failure; now the
    gate inside post_or_card makes it a quality exception on a card."""
    world.update(change)
    world["how"] = "carded"
    done = await importer.import_reel(world["video"], CAPTION, series="S", part=2)
    assert done.ok and done.proposal_id == 311
    assert world["kw"] == [{"series": True, "on_fail": "card"}]


async def test_a_one_off_import_is_not_a_series_episode(world):
    await importer.import_reel(world["video"], CAPTION)
    assert world["kw"] == [{"series": False, "on_fail": "card"}]


def test_captions_come_from_a_subtitle_stream_or_the_jobs_own_file(tmp_path):
    video = tmp_path / "ep.mp4"
    video.write_bytes(b"x")
    assert importer.captions_of(str(video), {"subtitles": True}) is True
    assert importer.captions_of(str(video), {"subtitles": False}) is None
    (tmp_path / "ep.srt").write_text("")
    assert importer.captions_of(str(video), {}) is None, "an empty file is no record"
    (tmp_path / "ep.srt").write_text("1\n00:00:00,000 --> 00:00:02,000\nHi\n")
    assert importer.captions_of(str(video), {}) is True


async def test_a_caption_without_the_ai_label_is_refused(world):
    done = await importer.import_reel(world["video"], "no label here")
    assert not done.ok and AI_LABEL in done.detail
    assert world["proposed"] == []


def test_the_cli_names_the_command():
    from atlas import cli
    assert cli.COMMANDS["import-reel"] is cli.import_reel


async def test_a_cap_held_import_waits_and_posts_on_a_later_tick(world, monkeypatch):
    """No retry existed for a one-off import: a used daily cap would have
    left the episode filed and never posted."""
    world["how"] = "wait"
    done = await importer.import_reel(world["video"], CAPTION, series="S", part=3)
    assert done.ok and "next free day" in done.detail
    assert any("status = 'waiting'" in s for s in world["sql"])

    async def waiting_row(sql, *args):
        if "d.status = 'waiting'" in sql:
            return {"draft_id": 7, "body": CAPTION, "media_id": 99,
                    "source": "import:S:part 3"}
        return None

    monkeypatch.setattr(importer, "fetch_one", waiting_row)
    world["how"] = "posted"
    assert await importer.post_waiting() == [
        "Imported episode (media 99): posted -- done"]
    assert world["kw"][-1] == {"series": True, "on_fail": "card"}
