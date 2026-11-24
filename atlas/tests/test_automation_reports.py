"""The automation reports on itself inside the reports that already exist:
the weekly's "*Checked my work*" and one or two lines in the morning brief.

Every line has a case where it appears with the right numbers and one where
it does not. A read that fails must say so, never read as all clear."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import brief, state, weekly
from atlas.kernel.audit import audit
from atlas.modules import backups
from tests.acceptance_support import (
    needs_pg,
    pg_fixture,  # noqa: F401
    registries_fixture,  # noqa: F401
)

TITLE = "*Checked my work*"


@pytest.fixture(name="clean", autouse=True)
async def clean_fixture(pg, monkeypatch, tmp_path):
    """Both switches on (after `pg`, which clears ATLAS_VERIFY_AFTER), a
    fresh passing drill, and no schedule_runs rows left by another test file
    (the pg fixture does not truncate that table)."""
    monkeypatch.setenv("ATLAS_VERIFY_AFTER", "on")
    monkeypatch.setenv("ATLAS_RECONCILE", "on")
    drill = tmp_path / "restore_drill.json"
    monkeypatch.setattr(backups, "DRILL", str(drill))
    write_drill(drill, ok=True, age=timedelta(days=2))
    await state.execute_sql("TRUNCATE schedule_runs")
    yield drill
    await state.execute_sql("TRUNCATE schedule_runs")


def write_drill(path, *, ok: bool, age: timedelta) -> None:
    at = (datetime.now(UTC) - age).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps({"at": at, "ok": ok, "dump": "atlas-0927.dump",
                                "exit": 0 if ok else 1}))


async def section() -> str | None:
    got = [p for p in (await weekly.build()).paragraphs if p.startswith(TITLE)]
    assert len(got) <= 1
    return got[0] if got else None


async def brief_lines() -> list[str] | None:
    got = [s for s in (await brief.build()).sections
           if s.title == "Checked my work"]
    return (got[0].lines or [f"UNAVAILABLE {got[0].unavailable}"]) if got else None


async def outbox_row(action: str, status: str, n: int = 1) -> None:
    for _ in range(n):
        await state.execute_sql(
            """INSERT INTO outbox (idempotency_key, action, destination,
                      occurrence, params_hash, status, finished_at)
               VALUES (md5(random()::text), $1, 'x', 'o', 'h', $2, now())""",
            action, status)


async def schedule_row(job: str, occurrence: str, status: str,
                       late: timedelta = timedelta(0)) -> None:
    due = datetime.now(UTC) - timedelta(days=1)
    await state.execute_sql(
        """INSERT INTO schedule_runs (job, occurrence, status, claimed_at,
                  finished_at, scheduled_for)
           VALUES ($1, $2, $3, $4, $4, $5)""",
        job, occurrence, status, due + late, due)


# ------------------------------------------------------------ all clear

@needs_pg
async def test_a_clean_week_with_both_switches_on_adds_nothing(pg):
    await audit("verify.verified", "stub.act", {"state": "verified"})
    # A verified read-back is still news. Without it: nothing at all.
    assert (await section()).startswith(TITLE)
    await state.execute_sql("TRUNCATE actions_log")
    assert await section() is None
    assert await brief_lines() is None


# ------------------------------------------------------------ read-backs

@needs_pg
async def test_read_backs_are_counted_and_the_misses_named(pg):
    for _ in range(3):
        await audit("verify.verified", "stub.act", {"state": "verified"})
    await audit("verify.unknown", "stub.act", {"reason": "no verifier"})
    await audit("verify.unknown", "stub.act", {"reason": "read-back timed out"})
    await audit("verify.contradicted", "linkedin.publish_post",
                {"reason": "post not found on the feed"})
    text = await section()
    assert "• 3 actions read back and confirmed." in text
    assert ("• 2 I couldn't confirm either way — 1 has no read-back built."
            in text)
    assert ("• 1 did NOT take effect: linkedin.publish_post (post not found "
            "on the feed).") in text
    # A passing drill rides along once there is something to say.
    assert "• Restore drill passed 2d ago." in text


@needs_pg
async def test_no_contradictions_no_contradiction_line(pg):
    await audit("verify.verified", "stub.act", {"state": "verified"})
    text = await section()
    assert "did NOT take effect" not in text
    assert "couldn't confirm" not in text
    assert "1 action read back and confirmed." in text


@needs_pg
async def test_more_than_five_contradictions_are_capped(pg):
    for i in range(7):
        await audit("verify.contradicted", f"mod.act{i}", {"reason": ""})
    text = await section()
    assert ("• 7 did NOT take effect: mod.act0, mod.act1, mod.act2, mod.act3, "
            "mod.act4, and 2 more.") in text


@needs_pg
async def test_a_finish_with_no_read_back_past_grace_is_cut_short(
        pg, monkeypatch):
    monkeypatch.setattr(weekly, "READBACK_GRACE", timedelta(seconds=-10_000))
    # Before the first read-back the switch was off: not counted.
    await audit("execute.finish", "stub.early", {"ok": True})
    await audit("verify.verified", "stub.act", {"state": "verified"})
    await audit("execute.finish", "stub.act", {"ok": True})
    await audit("verify.verified", "stub.act", {"state": "verified"})
    await audit("execute.finish", "stub.lost", {"ok": True})
    await audit("execute.finish", "stub.failed", {"ok": False})
    text = await section()
    assert "• 1 I couldn't confirm either way — 1 never got one (cut short)." in text


@needs_pg
async def test_a_finish_still_inside_grace_is_not_cut_short(pg):
    await audit("verify.verified", "stub.act", {"state": "verified"})
    await audit("execute.finish", "stub.lost", {"ok": True})
    assert "cut short" not in await section()


@needs_pg
async def test_read_back_off_says_it_did_not_check(pg, monkeypatch):
    monkeypatch.setenv("ATLAS_VERIFY_AFTER", "off")
    text = await section()
    assert ("• Read-back's off, so I didn't check whether what I did took "
            "effect.") in text
    assert "confirmed" not in text


# ------------------------------------------------------------ reconcile

@needs_pg
async def test_reconciled_cut_off_runs_by_outcome(pg):
    for event, subject in (("reconcile.done", "a.x"), ("reconcile.done", "a.y"),
                           ("reconcile.failed", "reels.publish"),
                           ("reconcile.needs_human", "linkedin.publish_post")):
        await audit("execute.abandoned", subject, {})
        await audit(event, subject, {})
    await audit("execute.abandoned", "b.z", {})
    text = await section()
    assert ("• Cut-off runs I looked up: 2 took effect; 1 didn't (reels.publish); "
            "1 still unknown — check it by hand (linkedin.publish_post).") in text
    assert "• 1 cut-off run I didn't manage to look up — check it by hand." in text


@needs_pg
async def test_reconcile_off_says_cut_off_runs_were_not_looked_up(
        pg, monkeypatch):
    monkeypatch.setenv("ATLAS_RECONCILE", "off")
    await audit("execute.abandoned", "a.x", {})
    await audit("execute.abandoned", "a.y", {})
    text = await section()
    assert ("• 2 runs got cut off by a restart; reconcile's off, so I didn't "
            "look them up.") in text
    assert "Cut-off runs I looked up" not in text


@needs_pg
async def test_reconcile_off_with_nothing_cut_off_still_says_it_is_off(
        pg, monkeypatch):
    monkeypatch.setenv("ATLAS_RECONCILE", "off")
    assert "• Reconcile's off — nothing got cut off this week anyway." in (
        await section())


# ------------------------------------------------------------ pauses

@needs_pg
async def test_a_paused_module_is_named_with_its_reason(pg):
    await state.execute_sql(
        """INSERT INTO pauses (scope, paused_by, reason, paused_at)
           VALUES ('reels', 'owner', 'testing captions',
                   now() - interval '2 days 1 hour')""")
    text = await section()
    assert "• reels paused 2d ago by owner: testing captions." in text
    assert await brief_lines() == ["Paused: reels — testing captions."]


@needs_pg
async def test_a_resumed_pause_is_not_reported(pg):
    await state.execute_sql(
        """INSERT INTO pauses (scope, paused_by, reason, resumed_at, resumed_by)
           VALUES ('reels', 'owner', 'testing', now(), 'owner')""")
    assert await section() is None
    assert await brief_lines() is None


# ------------------------------------------------------------ outbox

@needs_pg
async def test_outbox_rows_that_failed_or_went_to_a_person(pg):
    await outbox_row("reels.publish", "failed", 2)
    await outbox_row("linkedin.publish_post", "needs_human")
    await outbox_row("stub.ok", "delivered", 5)
    text = await section()
    assert "• Outbox: 2 sends failed for good — reels.publish ×2." in text
    assert ("• Outbox: 1 send left for you to check by hand — "
            "linkedin.publish_post.") in text
    assert "stub.ok" not in text


@needs_pg
async def test_delivered_outbox_rows_add_nothing(pg):
    await outbox_row("stub.ok", "delivered", 3)
    assert await section() is None


# ------------------------------------------------------------ missed runs

@needs_pg
async def test_missed_runs_by_policy(pg):
    await schedule_row("brief", "2026-09-20", "skipped")
    await schedule_row("brief", "2026-09-21", "skipped")
    await schedule_row("weekly", "2026-W39", "done", late=timedelta(hours=5))
    await schedule_row("review", "2026-09-22", "done", late=timedelta(seconds=30))
    text = await section()
    assert ("• Missed while I was down: brief skipped ×2 (skip-stale), weekly "
            "caught up late (run-once-on-recovery).") in text
    assert "review" not in text


@needs_pg
async def test_on_time_runs_add_nothing(pg):
    await schedule_row("brief", "2026-09-22", "done", late=timedelta(seconds=30))
    assert await section() is None


# ------------------------------------------------------------ restore drill

@needs_pg
async def test_a_failed_drill_is_always_said(pg, clean):
    write_drill(clean, ok=False, age=timedelta(days=1))
    text = await section()
    assert text == (f"{TITLE}\n• Restore drill FAILED 1d ago (atlas-0927.dump) — "
                    f"the backups aren't proven to restore.")


@needs_pg
async def test_a_stale_drill_is_a_warning(pg, clean):
    write_drill(clean, ok=True, age=timedelta(days=12))
    assert ("• Last restore drill passed, but 12d ago — it should run weekly."
            in await section())


@needs_pg
async def test_no_drill_on_record_is_a_warning(pg, clean):
    clean.unlink()
    assert "• No restore drill on record" in await section()


@needs_pg
async def test_a_fresh_passing_drill_alone_adds_nothing(pg):
    assert await section() is None


# ------------------------------------------------------------ unreadable

@needs_pg
async def test_an_unreadable_audit_log_says_so_never_all_clear(pg, monkeypatch):
    real = state.fetch_all

    async def flaky(sql, *args):
        if "verify.%" in sql:
            raise RuntimeError("connection reset")
        return await real(sql, *args)

    monkeypatch.setattr(state, "fetch_all", flaky)
    text = await section()
    assert "• Couldn't read the read-backs, so that's unknown — not zero." in text
    assert "confirmed" not in text


@needs_pg
async def test_the_brief_names_what_it_could_not_read(pg, monkeypatch):
    async def broken(sql, *args):
        raise RuntimeError("connection reset")

    real = brief.fetch_all

    async def flaky(sql, *args):
        return await (broken if "verify.contradicted" in sql else real)(sql, *args)

    monkeypatch.setattr(brief, "fetch_all", flaky)
    assert await brief_lines() == [
        "Couldn't read the read-backs, so that part's unknown."]


# ------------------------------------------------------------ the brief

@needs_pg
async def test_the_brief_names_what_did_not_take_effect_and_what_needs_you(pg):
    await audit("verify.contradicted", "linkedin.publish_post", {"reason": "x"})
    await audit("verify.contradicted", "linkedin.publish_post", {"reason": "x"})
    await audit("reconcile.failed", "reels.publish", {})
    await audit("reconcile.needs_human", "stub.act", {})
    await outbox_row("gitlab.comment", "needs_human")
    await audit("verify.verified", "stub.ok", {"state": "verified"})
    assert await brief_lines() == [
        ("Didn't take effect: linkedin.publish_post, reels.publish; 2 need you "
         "to check by hand.")]


@needs_pg
async def test_the_brief_is_silent_about_confirmed_work(pg):
    await audit("verify.verified", "stub.ok", {"state": "verified"})
    await outbox_row("stub.ok", "delivered")
    assert await brief_lines() is None


# ------------------------------------------------------------- headline

@needs_pg
async def test_the_headline_is_not_calm_when_my_own_work_needs_a_look(pg):
    """A clean set of health checks above "1 did NOT take effect" must not
    open with "Quiet week"."""
    await audit("verify.verified", "stub.act", {"state": "verified"})
    await audit("verify.contradicted", "linkedin.publish_post",
                {"reason": "post not found on the feed"})
    report = await weekly.build()
    assert "Quiet week" not in report.headline
    assert "1 thing in my own work needs a look" in report.headline


@needs_pg
async def test_good_news_alone_does_not_raise_the_headline(pg):
    await audit("verify.verified", "stub.act", {"state": "verified"})
    report = await weekly.build()
    assert "in my own work" not in report.headline
