"""Domain and interaction acceptance tests, D01-D16 (plan p. 15), plus the
end-to-end runs of the three first-slice workflows (plan p. 14).

Each test is named by its plan ID (or SLICE<n> for a slice's own path);
docs/acceptance.md is the matrix. `needs_pg` tests are skipped without
ATLAS_TEST_PG_DSN.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import durable, outbox
from atlas.kernel.contracts import Action, ActionResult, Tier
from atlas.kernel.flows import content as content_flow
from atlas.kernel.flows import ops as ops_flow
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY

# Fixtures. pytest registers them by their own names; the aliases keep
# the module names apart from the test parameters that request them.
from tests.acceptance_support import (
    STUB,
    StubWorld,
    client_fixture,  # noqa: F401
    decisions_fixture,  # noqa: F401
    drain_background,
    install_stub,
    media_params,
    needs_pg,
    observe_now,
    outbox_rows,
    pg_fixture,  # noqa: F401
    registries_fixture,  # noqa: F401
    seed_check,
    slack_fixture,  # noqa: F401
    slack_message,
    workflows,
)
from tests.test_slack_decisions import _rendered_card
from tests.test_slack_events import (
    _pending,
    message,
    post,
    settle,
)

S = durable.State
NOW = timedelta(0)


def _knowledge(monkeypatch, *, fails: bool = False) -> list[str]:
    from atlas.kernel import improve, knowledge
    asked: list[str] = []

    async def not_improve(*_a, **_kw):
        return None

    async def answer(question, **_kw):
        asked.append(question)
        if fails:
            raise ConnectionError("perplexity unreachable")
        return "About 100 °C at sea level. (Perplexity, read 12:00 UTC)"

    monkeypatch.setattr(improve, "handle", not_improve)
    monkeypatch.setattr(knowledge, "answer", answer)
    return asked


# -------------------------------------------------------------------- D01

@needs_pg
@pytest.mark.parametrize("where", [
    {"channel": "C0GENERAL"},                                  # a channel
    {"channel": "C0FLYCHEAP", "thread_ts": "1699999999.000001"},  # a thread
    {"channel": "D0MIRO", "channel_type": "im"},               # a DM
])
async def test_D01_a_question_in_any_channel_is_answered_where_it_was_asked(
        pg, slack, monkeypatch, where):
    post_event, replies = slack
    _knowledge(monkeypatch)
    extra = {k: v for k, v in where.items() if k != "channel"}
    event = slack_message("atlas, what is the boiling point of water at sea level?",
                          event_id="EvD01", channel=where["channel"], **extra)
    await post_event(event)
    await drain_background()
    assert len(replies) == 1
    reply = replies[0]
    assert reply.channel == where["channel"]
    assert reply.thread == where.get("thread_ts", event["event"]["ts"])
    assert "100 °C" in reply.body
    [row] = await workflows("slack.event")
    assert row["originating_channel"] == f"slack:{where['channel']}"
    assert row["state"] == "SUCCEEDED"
    assert "answer:knowledge:fresh" in row["evidence_refs"]


@needs_pg
async def test_SLICE1_a_failed_lookup_is_an_honest_unknown_not_a_guess(
        pg, slack, monkeypatch):
    post_event, replies = slack
    _knowledge(monkeypatch, fails=True)
    await post_event(slack_message("atlas, what is the boiling point of water?",
                                   event_id="EvS1"))
    await drain_background()
    [reply] = replies
    assert reply.body.startswith("I don't know right now")
    assert "perplexity unreachable" in reply.body and "Nothing was executed" in reply.body
    [row] = await workflows("slack.event")
    assert row["state"] == "SUCCEEDED"
    assert "answer:knowledge:unknown" in row["evidence_refs"]


# -------------------------------------------------------------------- D02

def test_D02_yes_under_one_card_approves_exactly_that_card(
        client, decisions, memory_backend, monkeypatch):
    decided, _ = decisions
    _pending(memory_backend, 82, 83)
    from atlas.api import slack_events

    async def parent(channel, thread_ts):
        return _rendered_card(83, "File an issue")

    monkeypatch.setattr(slack_events, "_parent", parent)
    post(client, message("yes", ts="1.5", thread_ts="1.0"))
    settle()
    assert decided == [(83, "approve", "slack:UMIRO")]


@pytest.mark.parametrize("text", ["yes", "do it", "approve"])
def test_D02_yes_with_several_pending_is_one_clarification_and_no_approval(
        client, decisions, memory_backend, text):
    decided, replied = decisions
    _pending(memory_backend, 82, 83)
    post(client, message(text))
    settle()
    assert decided == []
    assert len(replied) == 1 and "#82" in replied[0].body and "#83" in replied[0].body
    assert "Nothing was executed" in replied[0].body


# -------------------------------------------------------------------- D03

@pytest.mark.parametrize("text", ["ack", "seen", "thanks", "acknowledged"])
def test_D03_ack_seen_or_thanks_is_noted_and_never_a_decision(
        client, decisions, memory_backend, monkeypatch, text):
    decided, replied = decisions
    _pending(memory_backend, 82)
    from atlas.api import slack_events

    async def never(_text):
        raise AssertionError("an acknowledgement reached the model")

    monkeypatch.setattr(slack_events, "_converse", never)
    post(client, message(text))
    settle()
    assert decided == [] and replied[0].body == "Noted."
    assert any(r.event == "slack.acknowledged" for r in memory_backend.audit_rows)


def test_D03_decline_declines_and_never_executes(client, decisions, memory_backend):
    decided, _ = decisions
    _pending(memory_backend, 82)
    post(client, message("decline"))
    settle()
    assert decided == [(82, "decline", "slack:UMIRO")]


# -------------------------------------------------------------------- D05

@needs_pg
async def test_D05_a_diagnostic_that_hits_its_tool_limit_checkpoints_unknowns_and_a_next_step(
        pg, monkeypatch):
    from atlas.kernel import fixes
    await seed_check("cloudhost", "balance", "failing", "balance read 3.10 USD", hours=4)
    posted: list = []

    async def route(_prompt):
        return "I reached the tool-call limit before finding the cause."

    async def notify(msg):
        posted.append(msg)
        return ["slack"]

    monkeypatch.setattr("atlas.kernel.agents.route", route)
    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    await fixes.sweep()
    [msg] = posted
    assert "investigation incomplete" in msg.title
    assert "*Impact:* unknown" in msg.body and "*Next step:*" in msg.body
    [row] = await workflows(ops_flow.WORKFLOW)
    assert row["state"] == "NEEDS_HUMAN"
    assert row["remaining_unknowns"] in (["cause", "impact"], '["cause", "impact"]')
    assert "Next step" in row["result_summary"]
    incident = await pg_incident()
    assert incident["correlation_key"] == f"workflow:{row['id']}"


async def pg_incident() -> dict:
    from atlas.kernel import state
    return dict(await state.fetch_one("SELECT * FROM incidents ORDER BY id LIMIT 1"))


# ------------------------------------------------------------ slice 2 (ops)

def _install_fix(runs: list) -> None:
    async def restart(target: str) -> ActionResult:
        runs.append(target)
        return ActionResult(True, f"restarted {target}", evidence={"job": "r1"})

    async def dry(target: str) -> ActionResult:
        return ActionResult(True, f"would restart {target}")

    async def undo(_token: str) -> ActionResult:
        return ActionResult(True, "undone")

    action = Action(name="stubops.restart", tier=Tier.AUTO, summary="restart",
                    run=restart, dry_run=dry, rollback=undo,
                    params={"target": "what"}, max_per_hour=50)
    REGISTRY.actions[action.name] = action
    REGISTRY.decisions[action.name] = classify(action)


@needs_pg
async def test_SLICE2_one_synthetic_issue_is_deduplicated_fixed_once_and_verified(pg):
    from atlas.kernel import fixes
    runs: list = []
    _install_fix(runs)
    await seed_check("stubops", "api_health", "failing", "GET /health 503", hours=3)
    [item] = await fixes.lasting()
    taken = await ops_flow.begin(item)
    assert taken is not None and taken.incident_id is not None
    assert await ops_flow.begin(item) is None               # the same incident
    said, complete = await ops_flow.diagnose(
        _async("The API worker is wedged; a restart clears it."))
    assert complete
    left = await ops_flow.prepare(
        taken, ops_flow.Fix("stubops.restart", {"target": "api"}, said))
    assert left is S.RUNNING
    [done] = await outbox.dispatch(limit=5)
    assert done.status == "delivered" and runs == ["api"]
    assert (await durable.get(taken.workflow_id))["state"] == "VERIFYING"
    # Not verified on the fix's say-so: only an OK reading after it counts.
    assert await ops_flow.verify_recovery() == []
    await observe_now("stubops", "api_health", "ok", "GET /health 200")
    assert await ops_flow.verify_recovery() == [(taken.workflow_id, "SUCCEEDED")]
    assert runs == ["api"]


@needs_pg
async def test_SLICE2_a_fix_that_does_not_clear_it_is_escalated(pg):
    from atlas.kernel import fixes
    runs: list = []
    _install_fix(runs)
    await seed_check("stubops", "api_health", "failing", "GET /health 503", hours=3)
    [item] = await fixes.lasting()
    taken = await ops_flow.begin(item)
    await ops_flow.prepare(taken, ops_flow.Fix("stubops.restart", {"target": "api"}))
    await outbox.dispatch(limit=5)
    await observe_now("stubops", "api_health", "failing", "GET /health 503")
    posted: list = []

    async def post_it(title, body):
        posted.append((title, body))

    later = datetime.now(UTC) + ops_flow.VERIFY_WINDOW + timedelta(minutes=1)
    moved = await ops_flow.verify_recovery(now=later, post=post_it)
    assert moved == [(taken.workflow_id, "NEEDS_HUMAN")]
    assert "still reads failing" in posted[0][1]
    assert runs == ["api"]                                  # never re-run


@needs_pg
async def test_SLICE2_no_action_means_one_next_step_with_an_owner(pg):
    from atlas.kernel import fixes
    await seed_check("stubops", "cert_expiry", "degraded", "expires in 5 days",
                     hours=3)
    [item] = await fixes.lasting()
    taken = await ops_flow.begin(item)
    posted: list = []

    async def post_it(title, body):
        posted.append(body)

    left = await ops_flow.prepare(
        taken, ops_flow.NextStep("Renew the certificate in Caddy."), post=post_it)
    assert left is S.NEEDS_HUMAN and posted == ["Renew the certificate in Caddy."]
    await observe_now("stubops", "cert_expiry", "ok")
    assert await ops_flow.verify_recovery() == [(taken.workflow_id, "SUCCEEDED")]


def _async(text: str):
    async def produce() -> str:
        return text
    return produce


# -------------------------------------------------------------------- D09

async def test_D09_a_render_that_would_fall_back_to_stills_is_refused(monkeypatch):
    from atlas.modules import content
    from atlas.modules.content import render

    async def draft(sql, *args):
        return {"id": 5, "kind": "story_script",
                "body": "Title\nA scene.\nAnother scene.\nMade with AI"}

    async def stills(_script):
        return render.Choice("local", "no Veo quota left today")

    monkeypatch.setattr(content, "fetch_one", draft)
    monkeypatch.setattr(render, "choose_backend", stills)
    got = await content.render_video(5)
    assert not got.ok and content.NO_SLIDES in got.detail


@needs_pg
async def test_D09_a_quality_exception_is_recorded_and_nothing_is_posted(pg):
    world = StubWorld()
    install_stub(world)
    await content_flow.gate_failed(media_params(), dedup="day:D09", media_id=7,
                                   flaw="it plays 12.0 s; this reel must run 30-60 s")
    [row] = await workflows("content.reel")
    assert row["state"] == "NEEDS_HUMAN"
    assert row["result_summary"].startswith("quality exception: it plays 12.0 s")
    again = await content_flow.autopost(media_params(), "reel", dedup="day:D09",
                                        media_id=7, publisher=STUB)
    assert not again.ok and await outbox_rows() == [] and world.runs == []


# The motion check runs the real ffmpeg on real files. The fixtures are
# small (108x192, 12 fps) so the suite stays fast; only ffprobe's answer is
# stood in for, with the fixture's true length, because a native 1080x1920
# frame is not what these tests are about.
_FFMPEG = pytest.mark.skipif(
    __import__("shutil").which("ffmpeg") is None, reason="ffmpeg not installed")
_W, _H, _FPS = 108, 192, 12


def _ff(*args: str) -> None:
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
                   check=True, timeout=120)


def _moving_clip(tmp_path) -> str:
    out = str(tmp_path / "moving.mp4")
    _ff("-f", "lavfi", "-i", f"testsrc2=size={_W}x{_H}:rate={_FPS}:duration=15",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=15",
        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", out)
    return out


def _slideshow(tmp_path, stills: int = 3, each: float = 5.0) -> str:
    """`stills` still frames, `each` seconds apiece, with a voice-level tone."""
    args: list[str] = []
    for i in range(stills):
        png = str(tmp_path / f"still{i}.png")
        _ff("-f", "lavfi", "-i", f"testsrc2=size={_W}x{_H}:rate=1",
            "-frames:v", "1", "-vf", f"hue=h={(i + 1) * 90}", png)
        args += ["-loop", "1", "-t", f"{each:g}", "-i", png]
    total = stills * each
    out = str(tmp_path / f"slides{stills}.mp4")
    chain = "".join(f"[{i}:v]" for i in range(stills))
    _ff(*args, "-f", "lavfi", "-i", f"sine=frequency=330:duration={total:g}",
        "-filter_complex",
        f"{chain}concat=n={stills}:v=1:a=0,fps={_FPS},format=yuv420p[v]",
        "-map", "[v]", "-map", f"{stills}:a", "-c:v", "libx264", "-crf", "18",
        "-c:a", "aac", "-shortest", out)
    return out


def _probe_says_native(monkeypatch):
    """ffprobe's answer with the size swapped for the reel's: the real
    duration, audio and streams of the fixture otherwise."""
    from atlas.modules.content import ffmpeg
    real = ffmpeg.probe

    async def probe(path):
        info = await real(path)
        return {**info, "width": ffmpeg.WIDTH, "height": ffmpeg.HEIGHT}

    monkeypatch.setattr(ffmpeg, "probe", probe)


async def _gate(path: str) -> str | None:
    from atlas.modules.content import quality
    _, why = await quality.check_file(path, "A story. Made with AI", series=False,
                                      seconds=(10.0, 20.0))
    return why


@_FFMPEG
async def test_D09_a_stills_slideshow_file_fails_the_quality_gate(monkeypatch, tmp_path):
    """Three stills x 5 s with sound: right size, length, sound and label --
    everything but motion. A quality exception, with the reason."""
    _probe_says_native(monkeypatch)
    why = await _gate(_slideshow(tmp_path))
    assert why is not None and why.startswith("it is not motion video: ")
    assert "stills or slides never post" in why


@_FFMPEG
async def test_D09_one_still_held_to_the_end_fails_the_quality_gate(
        monkeypatch, tmp_path):
    """A freeze that lasts until the file ends prints only its start;
    it still counts, to the end of the video."""
    _probe_says_native(monkeypatch)
    why = await _gate(_slideshow(tmp_path, stills=1, each=15.0))
    assert why is not None and why.startswith("it is not motion video: 15.0 s")


@_FFMPEG
async def test_D09_moving_video_passes_the_same_gate(monkeypatch, tmp_path):
    """The positive control: the same gate, the same checks, a clip that
    moves in every frame."""
    _probe_says_native(monkeypatch)
    assert await _gate(_moving_clip(tmp_path)) is None


async def test_D09_motion_that_cannot_be_read_is_not_a_pass(monkeypatch):
    from atlas.modules.content import ffmpeg, quality

    async def probe(_path):
        return {"video": True, "width": ffmpeg.WIDTH, "height": ffmpeg.HEIGHT,
                "rotation": 0, "video_seconds": 40.0, "audio": True}

    async def loud(_path):
        return -18.0

    monkeypatch.setattr(ffmpeg, "probe", probe)
    monkeypatch.setattr(ffmpeg, "mean_volume", loud)
    monkeypatch.setattr(ffmpeg, "binary", lambda name="ffmpeg": None)
    _, why = await quality.check_file("/tmp/stills.mp4", "A story. Made with AI",
                                      series=False, seconds=quality.OTHER_SECONDS)
    assert why == "the motion could not be checked: ffmpeg not installed"


def test_D09_freezedetect_log_is_read_including_an_open_freeze():
    from atlas.modules.content import ffmpeg
    log = ("[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 0\n"
           "[freezedetect @ 0x1] lavfi.freezedetect.freeze_duration: 5\n"
           "[freezedetect @ 0x1] lavfi.freezedetect.freeze_end: 5\n"
           "[freezedetect @ 0x1] lavfi.freezedetect.freeze_start: 10\n")
    assert ffmpeg.parse_frozen(log, 15.0) == 10.0
    assert ffmpeg.parse_frozen("", 15.0) == 0.0


# -------------------------------------------------------------------- D10

@needs_pg
async def test_D10_one_platform_posts_and_one_times_out_success_kept_uncertain_reconciled(
        pg):
    world = StubWorld(behaviour={"tiktok": "timeout_after_accept"})
    install_stub(world)
    res = await content_flow.autopost(media_params(), "reel", dedup="day:D10",
                                      media_id=7, publisher=STUB)
    assert res.ok and "instagram: delivered" in res.detail
    assert "tiktok: reconciling" in res.detail
    wid = (await workflows("content.reel"))[0]["id"]
    # A later pass never re-sends either leg.
    await outbox.dispatch(limit=5)
    assert world.count("instagram") == 1 and world.count("tiktok") == 1
    assert (await outbox.reconcile_once()).status == "delivered"
    assert await content_flow.settle(wid, grace=NOW) is S.SUCCEEDED
    [receipt] = [r for r in await outbox_rows(wid) if r["kind"] == "notice"]
    await outbox.dispatch_once(only_id=receipt["id"])
    assert "instagram: delivered, read back verified" in world.receipts[0]
    assert "tiktok: delivered, read back verified" in world.receipts[0]


@needs_pg
async def test_D10_a_leg_nobody_can_confirm_goes_to_a_person_with_the_success_kept(pg):
    world = StubWorld(behaviour={"tiktok": "timeout_after_accept"})
    install_stub(world)
    from atlas.kernel import outbox as ob
    ob.RECONCILERS.pop("stubpub.publish")                   # no read-back
    await content_flow.autopost(media_params(), "reel", dedup="day:D10b",
                                media_id=7, publisher=STUB)
    wid = (await workflows("content.reel"))[0]["id"]
    assert (await outbox.reconcile_once()).status == "needs_human"
    assert await content_flow.settle(wid, grace=NOW) is S.NEEDS_HUMAN
    row = await durable.get(wid)
    assert "instagram: delivered, read back verified" in row["result_summary"]
    assert "tiktok: needs_human" in row["result_summary"]
    assert world.count("tiktok") == 1                       # never resent


# ------------------------------------------------------ slice 3, flag guard

async def test_SLICE3_the_stub_publisher_exists_only_inside_tests():
    assert "stubpub.publish" not in REGISTRY.actions
    assert content_flow.PRODUCTION.action == "content.autopublish_reel"
    assert content_flow.RECEIPTS == {}
