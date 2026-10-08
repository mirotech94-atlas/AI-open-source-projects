"""Retention. The promise that justifies collecting browsing history at all is
that the raw record is short-lived, so something must enforce it unprompted."""
from __future__ import annotations

import hashlib
import pathlib
from datetime import UTC, datetime, timedelta

import pytest

import atlas
from atlas.kernel import privacy
from atlas.modules.clips import sources as clip_sources


@pytest.fixture
def db(memory_backend):
    memory_backend.responses["FROM browser_rollup"] = {"n": 7}
    return memory_backend


async def test_retention_defaults_to_90_days(monkeypatch):
    monkeypatch.delenv("BROWSER_RETENTION_DAYS", raising=False)
    assert privacy.retention_days() == 90


@pytest.mark.parametrize("value", ["0", "-5", "notanumber", ""])
async def test_a_nonsense_retention_falls_back_rather_than_deleting_everything(
        monkeypatch, value):
    """'0 days' is far more likely to be a typo than an instruction."""
    monkeypatch.setenv("BROWSER_RETENTION_DAYS", value)
    assert privacy.retention_days() == 90


async def test_a_shorter_retention_is_honoured(monkeypatch):
    monkeypatch.setenv("BROWSER_RETENTION_DAYS", "30")
    assert privacy.retention_days() == 30


async def test_purge_rolls_up_before_deleting(db):
    await privacy.purge_browser_history()
    statements = [sql for sql, _ in db.sql_log]
    rollup = next(i for i, s in enumerate(statements) if "browser_rollup" in s)
    delete = next(i for i, s in enumerate(statements) if s.startswith("DELETE"))
    assert rollup < delete, "raw rows were deleted before the roll-up was written"


async def test_a_failed_rollup_aborts_the_delete(memory_backend):
    """Losing the aggregate is recoverable. Losing it and the detail is not."""
    async def boom(sql, *args):
        if "browser_rollup" in sql:
            raise RuntimeError("disk full")
        raise AssertionError("DELETE must not be reached")

    memory_backend.execute = boom
    with pytest.raises(RuntimeError):
        await privacy.purge_browser_history()


async def test_purge_uses_the_configured_window(db, monkeypatch):
    monkeypatch.setenv("BROWSER_RETENTION_DAYS", "30")
    await privacy.purge_browser_history()
    deletes = [sql for sql, _ in db.sql_log if sql.startswith("DELETE")]
    assert "interval '30 days'" in deletes[0]


async def test_rollup_is_idempotent_on_conflict(db):
    await privacy.roll_up_browser_history()
    inserts = [sql for sql, _ in db.sql_log if "INSERT INTO browser_rollup" in sql]
    assert "ON CONFLICT (day, domain) DO UPDATE" in inserts[0]


async def test_rollup_buckets_in_utc(db):
    await privacy.roll_up_browser_history()
    inserts = [sql for sql, _ in db.sql_log if "INSERT INTO browser_rollup" in sql]
    assert "AT TIME ZONE 'UTC'" in inserts[0]


async def test_memory_reads_age_out_after_thirty_days(memory_backend):
    """The recall log holds no content, only which namespace was read and
    when; the brain view needs its last hour and last thirty rows. Thirty
    days bounds the table the view aggregates on every load."""
    await privacy.purge_memory_reads()
    deletes = [sql for sql, _ in memory_backend.sql_log
               if sql.startswith("DELETE FROM memory_reads")]
    assert deletes and "interval '30 days'" in deletes[0]


async def test_the_hourly_run_purges_the_recall_log(db):
    result = await privacy.run()
    assert "purged_memory_reads" in result
    assert any(sql.startswith("DELETE FROM memory_reads") for sql, _ in db.sql_log)


# ------------------------------------------------------ clip source footage

SLUG = "irubyana-twitch"
VOD = b"a streamer's whole evening, in bytes"


@pytest.fixture
def clips(tmp_path, monkeypatch):
    root = tmp_path / "clips"
    (root / SLUG / "source").mkdir(parents=True)
    (root / SLUG / "out").mkdir()
    monkeypatch.setenv("CLIPS_DATA_DIR", str(root))
    privacy._CLIP_HASHES.clear()
    return root


SOURCES_SQL = "c.status AS campaign_status FROM clip_sources s JOIN clip_campaigns"
CLIPS_SQL = "FROM clip_outputs o JOIN clip_sources s ON s.id = o.source_id"


def _row(sid=1, filename="vod.mp4", data=VOD, clips=2, waiting=0, days_old=1,
         campaign=SLUG, cut_attempts=0, campaign_status="active", cards=()):
    """A source row, and the clip rows it stands for: `waiting` unposted
    clips with no card answered, the rest posted; plus (status, card) pairs
    in `cards`."""
    clip_rows = ([{"source_id": sid, "status": "made", "card": None}] * waiting
                 + [{"source_id": sid, "status": "posted", "card": None}]
                 * (clips - waiting)
                 + [{"source_id": sid, "status": st, "card": card}
                    for st, card in cards])
    return {"id": sid, "campaign": campaign, "filename": filename,
            "sha256": hashlib.sha256(data).hexdigest(),
            "registered_at": datetime.now(UTC) - timedelta(days=days_old),
            "cut_attempts": cut_attempts, "campaign_status": campaign_status,
            "_clips": clip_rows}


def _serve(memory_backend, rows):
    memory_backend.responses[SOURCES_SQL] = [
        {k: v for k, v in r.items() if k != "_clips"} for r in rows]
    memory_backend.responses[CLIPS_SQL] = [c for r in rows for c in r["_clips"]]


def _marked(memory_backend):
    return [a[0] for sql, a in memory_backend.sql_log
            if sql.startswith("UPDATE clip_sources SET deleted_at")]


def test_the_kernel_restates_the_clips_folder_and_slug_exactly(clips):
    assert privacy.clips_dir() == clip_sources.root()
    assert privacy._CLIP_SLUG.pattern == clip_sources.SLUG.pattern
    from atlas.modules.clips import intake
    assert privacy.CLIP_MAX_ATTEMPTS == intake.MAX_ATTEMPTS


async def test_a_source_whose_clips_are_all_done_is_removed(clips, memory_backend):
    vod = clips / SLUG / "source" / "vod.mp4"
    vod.write_bytes(VOD)
    clip = clips / SLUG / "out" / "1-abc-1.mp4"
    clip.write_bytes(b"a clip")
    _serve(memory_backend, [_row()])
    files, freed = await privacy.purge_clip_sources()
    assert (files, freed) == (1, len(VOD))
    assert not vod.exists()
    assert clip.exists()                        # outputs are never touched
    assert _marked(memory_backend) == [1]
    queries = [sql for sql, _ in memory_backend.sql_log if "clip_sources s" in sql]
    assert all("deleted_at IS NULL" in q for q in queries) and len(queries) == 2
    for action in privacy.CLIP_POST_CARDS:
        assert f"'{action}'" in queries[1]


async def test_a_pending_clip_blocks_the_purge(clips, memory_backend):
    vod = clips / SLUG / "source" / "vod.mp4"
    vod.write_bytes(VOD)
    # Even if the query let it through: one clip still waiting to post.
    _serve(memory_backend, [_row(waiting=1, days_old=400)])
    assert await privacy.purge_clip_sources() == (0, 0)
    assert vod.exists() and _marked(memory_backend) == []


async def test_a_fresh_source_never_cut_is_kept(clips, memory_backend):
    """"Every clip is done" is vacuously true of a source with no clips."""
    vod = clips / SLUG / "source" / "vod.mp4"
    vod.write_bytes(VOD)
    _serve(memory_backend, [_row(clips=0, days_old=2)])
    assert await privacy.purge_clip_sources() == (0, 0)
    assert vod.exists()


@pytest.mark.parametrize("card", ["declined", "expired"])
async def test_a_clip_whose_card_got_a_no_no_longer_blocks_the_purge(
        clips, memory_backend, card):
    """Declined, or expired unanswered: Miro's "no" is final for the purge,
    like a withdrawn clip. The clip file itself stays in out/."""
    vod = clips / SLUG / "source" / "vod.mp4"
    vod.write_bytes(VOD)
    _serve(memory_backend, [_row(clips=1, cards=[("made", card),
                                                 ("handed_off", card)])])
    assert await privacy.purge_clip_sources() == (1, len(VOD))
    assert not vod.exists()


@pytest.mark.parametrize("card", [None, "pending", "approved"])
async def test_a_clip_whose_card_is_open_or_yes_still_blocks(clips, memory_backend,
                                                             card):
    vod = clips / SLUG / "source" / "vod.mp4"
    vod.write_bytes(VOD)
    _serve(memory_backend, [_row(clips=1, cards=[("made", card)])])
    assert await privacy.purge_clip_sources() == (0, 0)
    assert vod.exists()


def test_a_clip_waits_until_it_is_done_or_refused():
    assert privacy.clip_waiting("made", None)
    assert privacy.clip_waiting("handed_off", "pending")
    assert not privacy.clip_waiting("made", "declined")
    assert not privacy.clip_waiting("made", "expired")
    for state in privacy.CLIP_DONE:
        assert not privacy.clip_waiting(state, None)


async def test_a_never_cut_source_with_attempts_left_is_kept_at_day_25(
        clips, memory_backend, monkeypatch):
    """No clip waiting is not a reason: nothing was ever cut from it (and
    under watch-only nothing could be). It stays until the intake gives up
    or its campaign ends."""
    monkeypatch.setenv("CLIPS_SOURCE_RETENTION_DAYS", "21")
    vod = clips / SLUG / "source" / "vod.mp4"
    vod.write_bytes(VOD)
    _serve(memory_backend, [_row(clips=0, days_old=25, cut_attempts=2)])
    assert await privacy.purge_clip_sources() == (0, 0)
    assert vod.exists() and _marked(memory_backend) == []


async def test_a_never_cut_source_the_intake_gave_up_on_is_removed(
        clips, memory_backend, monkeypatch):
    monkeypatch.setenv("CLIPS_SOURCE_RETENTION_DAYS", "21")
    vod = clips / SLUG / "source" / "vod.mp4"
    vod.write_bytes(VOD)
    _serve(memory_backend, [_row(clips=0, days_old=22,
                                 cut_attempts=privacy.CLIP_MAX_ATTEMPTS)])
    assert await privacy.purge_clip_sources() == (1, len(VOD))
    assert not vod.exists()


async def test_a_never_cut_source_of_an_ended_campaign_is_removed(
        clips, memory_backend, monkeypatch):
    monkeypatch.setenv("CLIPS_SOURCE_RETENTION_DAYS", "21")
    vod = clips / SLUG / "source" / "vod.mp4"
    vod.write_bytes(VOD)
    _serve(memory_backend, [_row(clips=0, days_old=22, campaign_status="ended")])
    assert await privacy.purge_clip_sources() == (1, len(VOD))
    assert not vod.exists()


@pytest.mark.parametrize("changes", [
    {"campaign_status": "ended", "days_old": 3},          # ended, too young
    {"campaign_status": "paused", "days_old": 90},        # paused is not ended
])
async def test_a_never_cut_source_is_otherwise_kept(clips, memory_backend, changes):
    vod = clips / SLUG / "source" / "vod.mp4"
    vod.write_bytes(VOD)
    _serve(memory_backend, [_row(clips=0, **changes)])
    assert await privacy.purge_clip_sources() == (0, 0)
    assert vod.exists()


@pytest.mark.parametrize("value", ["0", "-3", "soon", ""])
def test_a_nonsense_clip_retention_falls_back_to_21_days(monkeypatch, value):
    monkeypatch.setenv("CLIPS_SOURCE_RETENTION_DAYS", value)
    assert privacy.clip_source_days() == 21


@pytest.mark.parametrize("filename", [
    "../vod.mp4", "../../vod.mp4", "/etc/passwd", "sub/vod.mp4",
    "..", ".", ".hidden.mp4", "a\\b.mp4", "",
])
async def test_a_hostile_filename_is_refused(clips, memory_backend, filename):
    # Bait at every place a traversal could reach.
    (clips / SLUG / "vod.mp4").write_bytes(VOD)
    (clips / "vod.mp4").write_bytes(VOD)
    (clips / SLUG / "source" / "sub").mkdir()
    (clips / SLUG / "source" / "sub" / "vod.mp4").write_bytes(VOD)
    _serve(memory_backend, [_row(filename=filename)])
    assert await privacy.purge_clip_sources() == (0, 0)
    assert (clips / SLUG / "vod.mp4").exists() and (clips / "vod.mp4").exists()
    assert (clips / SLUG / "source" / "sub" / "vod.mp4").exists()
    assert _marked(memory_backend) == []


async def test_a_hostile_slug_is_refused(clips, memory_backend):
    (clips / "vod.mp4").write_bytes(VOD)
    for slug in ("..", "../x", "/etc", "A-B"):
        _serve(memory_backend, [_row(campaign=slug)])
        assert await privacy.purge_clip_sources() == (0, 0)
    assert (clips / "vod.mp4").exists()


async def test_a_symlink_is_never_followed(clips, memory_backend, tmp_path):
    target = tmp_path / "elsewhere.mp4"
    target.write_bytes(VOD)
    (clips / SLUG / "source" / "vod.mp4").symlink_to(target)
    _serve(memory_backend, [_row()])
    assert await privacy.purge_clip_sources() == (0, 0)
    assert target.exists() and _marked(memory_backend) == []


async def test_a_symlinked_source_folder_is_never_followed(clips, memory_backend,
                                                           tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "vod.mp4").write_bytes(VOD)
    folder = clips / SLUG / "source"
    folder.rmdir()
    folder.symlink_to(other)
    _serve(memory_backend, [_row()])
    assert await privacy.purge_clip_sources() == (0, 0)
    assert (other / "vod.mp4").exists()


async def test_a_file_that_is_not_the_registered_bytes_is_kept(clips,
                                                               memory_backend):
    vod = clips / SLUG / "source" / "vod.mp4"
    vod.write_bytes(b"a different file under the same name")
    _serve(memory_backend, [_row()])
    assert await privacy.purge_clip_sources() == (0, 0)
    assert vod.exists() and _marked(memory_backend) == []


async def test_a_source_already_gone_is_not_claimed_removed(clips, memory_backend):
    _serve(memory_backend, [_row()])
    assert await privacy.purge_clip_sources() == (0, 0)
    assert _marked(memory_backend) == []


async def test_the_hourly_run_purges_clip_sources(db, clips):
    db.responses[SOURCES_SQL] = []
    db.responses[CLIPS_SQL] = []
    result = await privacy.run()
    assert result["purged_clip_sources"] == 0
    assert result["freed_clip_source_bytes"] == 0


def test_the_clip_purge_is_not_an_action_anything_can_ask_for():
    """Deletion is Tier 3: the purge is retention, run by the scheduler, and
    no executor action, tool or module refers to it in code."""
    import ast
    pkg = pathlib.Path(atlas.__file__).resolve().parent
    for path in pkg.rglob("*.py"):
        if path.name == "privacy.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            name = (node.id if isinstance(node, ast.Name) else
                    node.attr if isinstance(node, ast.Attribute) else
                    " ".join(a.name for a in node.names)
                    if isinstance(node, ast.ImportFrom) else "")
            assert "purge_clip_sources" not in name, path
