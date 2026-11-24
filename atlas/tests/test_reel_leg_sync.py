"""The leg sync (content/legsync.py): a reel that posted to some platforms
and not others gets ONLY its missing leg, once, through the executor.

the owner, 2026-09-29: media 12 (Emergency Room: Code Chaos -- Part 2) auto-posted
to TikTok at 07:32 UTC while its Instagram leg was refused with Graph HTTP
401. What is load-bearing:

* only the missing leg is posted, with the caption the reel posted with;
* a second run posts nothing (reel_posts + the effect claim);
* a content / guard refusal is never retried;
* part N-1's missing leg goes before part N -- on the sync, and a new part
  N waits whole while N-1 is still missing a leg;
* campaign clips are never touched;
* the `reel_legs_sync` check: OK, DEGRADED past 2 h, UNKNOWN unreadable;
* a caught-up leg never takes a new day's automatic slot.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import executor, legs, state
from atlas.kernel import reels as day
from atlas.kernel.contracts import ActionResult, Health
from atlas.kernel.permissions import classify as tier_of
from atlas.kernel.registry import REGISTRY
from atlas.modules import content
from atlas.modules.content import crosspost, legsync
from atlas.modules.content.drafts import AI_LABEL

CAPTION = f"Kareem vs the vending machine.\n\n{AI_LABEL}"
IG_401 = ("Graph API HTTP 401 on /v21.0/17841/media: Error validating access "
          "token: Session has expired")
NOW = datetime.now(UTC)


# ------------------------------------------------------------ the fake world

def _row(rid: int, at: datetime, event: str, subject: str, data: dict) -> dict:
    return {"id": rid, "at": at, "event": event, "subject": subject,
            "payload": json.dumps({"event": event, "subject": subject,
                                   "data": data})}


def run_rows(media_id: int, legs_: dict[str, tuple[str, str]], *, at: datetime,
             caption: str = CAPTION, subject: str = "content.autopublish_reel",
             rid: int = 1) -> list[dict]:
    """The two audit rows `executor.execute` writes for one publish run."""
    lines = " · ".join(f"{crosspost.NAMES[k]}: {s}" for k, (s, _) in legs_.items())
    posted = any(s in (crosspost.POSTED, crosspost.ID_PENDING)
                 for s, _ in legs_.values())
    return [
        _row(rid, at - timedelta(seconds=40), "execute.start", subject,
             {"params": {"media_id": media_id, "caption": caption}}),
        _row(rid + 1, at, "execute.finish", subject,
             {"ok": posted, "detail": f"reel {media_id}: {lines}",
              "evidence": {k: {"status": s, "detail": d}
                           for k, (s, d) in legs_.items()}}),
    ]


class World(state.MemoryBackend):
    """actions_log, reel_posts and effect_claims, kept as state."""

    def __init__(self) -> None:
        super().__init__()
        self.runs: list[dict] = []
        self.posts: set[tuple[int, str]] = set()
        self.claims: dict[str, str] = {}

    async def fetch(self, sql, *args):
        flat = " ".join(sql.split())
        if "FROM actions_log WHERE event IN" in flat:
            self.sql_log.append((flat, args))
            own = [{"id": 10_000 + r.id, "at": r.at, "event": r.event,
                    "subject": r.subject, "payload": r.payload}
                   for r in self.audit_rows
                   if r.event in ("execute.start", "execute.finish")]
            return list(self.runs) + own
        if "FROM reel_posts WHERE media_id = ANY" in flat:
            return [{"media_id": m, "platform": p} for m, p in self.posts
                    if m in args[0]]
        if "FROM effect_claims WHERE action = $1 AND key = ANY" in flat:
            return [{"key": k, "status": v} for k, v in self.claims.items()
                    if k in args[1]]
        return await super().fetch(sql, *args)

    async def fetchrow(self, sql, *args):
        flat = " ".join(sql.split())
        if "INSERT INTO effect_claims" in flat:
            if args[1] in self.claims:
                return None
            self.claims[args[1]] = "sending"
            return {"key": args[1]}
        if "SELECT status FROM effect_claims" in flat:
            status = self.claims.get(args[1])
            return {"status": status} if status else None
        return await super().fetchrow(sql, *args)

    async def execute(self, sql, *args):
        flat = " ".join(sql.split())
        if "UPDATE effect_claims SET status = 'sent'" in flat:
            self.claims[args[1]] = "sent"
        elif "DELETE FROM effect_claims" in flat:
            if self.claims.get(args[1]) == "sending":
                del self.claims[args[1]]
        elif "INSERT INTO reel_posts" in flat:
            self.posts.add((int(args[0]), str(args[2])))
        return await super().execute(sql, *args)


@pytest.fixture
def world(monkeypatch, tmp_path):
    fake = World()
    previous = state.set_backend(fake)
    video = tmp_path / "12.mp4"
    video.write_bytes(b"mp4")
    fake.responses["FROM content_media WHERE id"] = {
        "id": 12, "status": "done", "path": str(video), "media_token": "tok",
        "media_token_expires_at": NOW + timedelta(hours=3)}
    fake.responses["SELECT d.source FROM content_media"] = {
        "source": "import:Emergency Room: Code Chaos:part 2"}
    monkeypatch.setenv("ATLAS_HOST", "atlas.example")
    monkeypatch.setenv("INSTAGRAM_STORIES_ACCOUNT_ID", "sid")
    monkeypatch.setenv("INSTAGRAM_STORIES_TOKEN", "stok")
    monkeypatch.setenv("POSTIZ_API_KEY", "pk")
    monkeypatch.delenv("FACEBOOK_STORIES_PAGE_ID", raising=False)
    monkeypatch.delenv("FACEBOOK_STORIES_PAGE_TOKEN", raising=False)
    monkeypatch.setattr(executor, "_recent", type(executor._recent)(
        executor._recent.default_factory))
    try:
        yield fake
    finally:
        state.set_backend(previous)


@pytest.fixture
def legs_posted(monkeypatch):
    """Every leg succeeds and is counted; no provider is reached."""
    calls: list[tuple[str, str]] = []

    async def ig(caption, url, *, parent, media_id):
        calls.append(("instagram", caption))
        assert parent == legsync.ACTION
        return crosspost.POSTED, "published 9001 to the stories account", "stories:9001"

    async def fb(caption, url):
        calls.append(("facebook", caption))
        return crosspost.POSTED, "video 77 on the Page", "77"

    async def tt(caption, path):
        calls.append(("tiktok", caption))
        return crosspost.POSTED, "Postiz post p-1", "p-1"

    monkeypatch.setattr(crosspost, "_instagram", ig)
    monkeypatch.setattr(crosspost, "_facebook", fb)
    monkeypatch.setattr(crosspost, "_tiktok", tt)
    return calls


@pytest.fixture
def hands(monkeypatch):
    """The executor with the content actions registered and watch-only off."""
    from atlas.modules import instagram
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    ig = [a for a in instagram.MODULE.actions() if a.name == "instagram.publish"]
    for spec in [*content.MODULE.actions(), *ig]:
        monkeypatch.setitem(REGISTRY.actions, spec.name, spec)
        monkeypatch.setitem(REGISTRY.decisions, spec.name, tier_of(spec))


MEDIA_12 = {"instagram": (crosspost.REFUSED, IG_401),
            "facebook": (crosspost.NOT_CONFIGURED,
                         "FACEBOOK_STORIES_PAGE_ID / FACEBOOK_STORIES_PAGE_TOKEN unset"),
            "tiktok": (crosspost.POSTED, "Postiz post p-12, TikTok id 7, public")}


# ------------------------------------------------------------ the action

def test_it_is_a_tier_one_action_with_a_dry_run_and_a_rollback():
    spec = {a.name: a for a in content.MODULE.actions()}[legsync.ACTION]
    assert int(tier_of(spec).tier) == 1
    assert spec.dry_run is not None and spec.rollback is crosspost.unpublish_reel
    assert set(spec.params) == {"media_id", "platform"}     # never a caption
    assert legsync.ACTION in legs.PARENTS["instagram.publish"]


async def test_media_12_gets_only_its_instagram_leg(world, legs_posted, hands):
    """The real shape: TikTok posted, Instagram refused with 401, Facebook
    not configured then (and now). The first tick after deploy posts the
    Instagram leg -- nothing else -- with the caption the reel posted with."""
    world.runs = run_rows(12, MEDIA_12, at=NOW - timedelta(hours=5))
    world.posts = {(12, "tiktok")}
    lines = await day.leg_sync()
    assert legs_posted == [("instagram", CAPTION)]
    assert (12, "instagram") in world.posts
    assert world.claims == {"12:instagram": "sent"}
    assert len(lines) == 1 and "caught up" in lines[0]
    starts = [json.loads(r.payload) for r in world.audit_rows
              if r.event == "execute.start"]
    assert [s["subject"] for s in starts] == [legsync.ACTION]
    assert starts[0]["data"]["params"] == {"media_id": 12, "platform": "instagram"}


async def test_a_second_run_posts_nothing(world, legs_posted, hands):
    world.runs = run_rows(12, MEDIA_12, at=NOW - timedelta(hours=5))
    world.posts = {(12, "tiktok")}
    await day.leg_sync()
    again = await day.leg_sync()
    assert legs_posted == [("instagram", CAPTION)] and again == []
    # Even called directly, with the leg's reel_posts row gone, the settled
    # claim stops it.
    world.posts.discard((12, "instagram"))
    result = await legsync.post_missing_leg(12, "instagram")
    assert not result.ok and legs_posted == [("instagram", CAPTION)]


async def test_a_claim_left_in_doubt_is_never_reposted(world, legs_posted, hands):
    world.runs = run_rows(12, MEDIA_12, at=NOW - timedelta(hours=5))
    world.posts = {(12, "tiktok")}
    world.claims = {"12:instagram": "sending"}
    assert await day.leg_sync() == []
    result = await legsync.post_missing_leg(12, "instagram")
    assert not result.ok and legs_posted == []
    obs = await legsync.check_reel_legs_sync()
    assert obs.health is Health.DEGRADED and "check the platform by hand" in obs.detail


async def test_a_leg_that_exists_is_never_posted(world, legs_posted, hands):
    world.runs = run_rows(12, MEDIA_12, at=NOW - timedelta(hours=5))
    world.posts = {(12, "tiktok")}
    result = await legsync.post_missing_leg(12, "tiktok")
    assert not result.ok and "no missing tiktok leg" in result.detail
    assert legs_posted == []


@pytest.mark.parametrize("said", [
    "refused: the caption names a real brand (Pepsi)",
    "refused: every leg carries the AI label",
    "Instagram could not process the reel (container ERROR)",
    "instagram.publish is paused since Tue 29 Sep 07:00 UTC by owner",
])
async def test_a_content_or_guard_refusal_is_not_retried(world, legs_posted, hands,
                                                         said):
    world.runs = run_rows(12, {**MEDIA_12, "instagram": (crosspost.REFUSED, said)},
                          at=NOW - timedelta(hours=5))
    world.posts = {(12, "tiktok")}
    assert await day.leg_sync() == []
    assert legs_posted == []
    result = await legsync.post_missing_leg(12, "instagram")
    assert not result.ok and "not retried" in result.detail
    obs = await legsync.check_reel_legs_sync()
    assert obs.health is Health.OK and "not retried" in obs.detail


@pytest.mark.parametrize("said, retry", [
    (IG_401, True),
    ("Graph API HTTP 400 on /x/media: Error validating access token", True),
    ("Graph API HTTP 403 on /x/media: not allowed", True),
    ("Postiz post: HTTP 502", True),
    ("Graph API unreachable: timed out", True),
    (("instagram.publish has already run 2 time(s) this hour (its own "
      "max_per_hour)"), True),
    ("Graph API HTTP 429 on /x/media", True),
    ("refused: the caption names a real person", False),
    ("Graph API HTTP 400 on /x/media: Invalid parameter", False),
    ("the Instagram module is paused since Tue 29 Sep by owner", False),
])
def test_what_is_retryable(said, retry):
    assert legsync.classify(crosspost.REFUSED, said) is retry


async def test_not_configured_then_is_never_backfilled(world, legs_posted, hands,
                                                       monkeypatch):
    """Facebook configured today must not post four days of old reels."""
    monkeypatch.setenv("FACEBOOK_STORIES_PAGE_ID", "page")
    monkeypatch.setenv("FACEBOOK_STORIES_PAGE_TOKEN", "ptok")
    world.runs = run_rows(12, MEDIA_12, at=NOW - timedelta(hours=5))
    world.posts = {(12, "tiktok"), (12, "instagram")}
    assert await day.leg_sync() == []
    assert legs_posted == []


async def test_facebook_refused_on_credentials_is_caught_up(world, legs_posted,
                                                           hands, monkeypatch):
    """Facebook has no reel_posts row ever: its state comes from the run's
    evidence and the claim, so a posted Page leg is never posted again."""
    monkeypatch.setenv("FACEBOOK_STORIES_PAGE_ID", "page")
    monkeypatch.setenv("FACEBOOK_STORIES_PAGE_TOKEN", "ptok")
    world.runs = (run_rows(12, {**MEDIA_12, "facebook": (
        crosspost.REFUSED, "Graph API HTTP 401 on /page/videos")},
        at=NOW - timedelta(hours=5))
        + run_rows(13, {"instagram": (crosspost.POSTED, "published 5"),
                        "facebook": (crosspost.POSTED, "video 5 on the Page"),
                        "tiktok": (crosspost.POSTED, "p-13")},
                   at=NOW - timedelta(hours=4), rid=10))
    world.posts = {(12, "tiktok"), (13, "instagram"), (13, "tiktok")}
    await day.leg_sync()
    assert sorted(legs_posted) == [("facebook", CAPTION), ("instagram", CAPTION)]
    assert world.claims["12:facebook"] == "sent"
    await day.leg_sync()
    assert len(legs_posted) == 2


async def test_an_uncertain_first_attempt_is_read_back_not_reposted(
        world, legs_posted, hands, monkeypatch):
    from atlas.kernel.verify import verified
    from atlas.modules.instagram import readback

    async def found(account, caption, started):
        assert account == "stories" and caption == CAPTION
        return verified(media_id="4242")

    monkeypatch.setattr(readback, "find_recent", found)
    world.runs = run_rows(12, {**MEDIA_12, "instagram": (
        crosspost.REFUSED, "Graph API unreachable: timed out")},
        at=NOW - timedelta(hours=5))
    world.posts = {(12, "tiktok")}
    await day.leg_sync()
    assert legs_posted == []
    assert world.claims["12:instagram"] == "sent" and (12, "instagram") in world.posts


async def test_the_instagram_hour_is_respected(world, legs_posted, hands):
    world.runs = run_rows(12, MEDIA_12, at=NOW - timedelta(hours=5))
    world.posts = {(12, "tiktok")}
    executor._record_run("instagram.publish")
    executor._record_run("instagram.publish")
    lines = await day.leg_sync()
    assert legs_posted == [] and crosspost.RATE_HELD in lines[0]
    assert "12:instagram" not in world.claims


async def test_a_still_dead_token_is_not_retried_every_tick(world, hands,
                                                            monkeypatch):
    """The token expires again: one failed catch-up, then a back-off -- the
    Instagram hour (shared with every new reel) is not spent tick after tick,
    so new reels are not held whole behind it."""
    attempts: list[int] = []

    async def ig(caption, url, *, parent, media_id):
        attempts.append(media_id)
        await legs.start("instagram.publish", parent, media_id=media_id)
        return crosspost.REFUSED, IG_401, None

    monkeypatch.setattr(crosspost, "_instagram", ig)
    world.runs = run_rows(12, MEDIA_12, at=NOW - timedelta(hours=5))
    world.posts = {(12, "tiktok")}
    first = await day.leg_sync()
    assert "not caught up" in first[0] and "12:instagram" not in world.claims
    for _ in range(3):
        assert await day.leg_sync() == []
    assert attempts == [12]
    assert legs.over_limit(crosspost.IG_LEG) is None
    direct = await legsync.post_missing_leg(12, "instagram")
    assert not direct.ok and "tried again after" in direct.detail


async def test_tiktok_and_facebook_share_an_hourly_window(world, legs_posted, hands):
    legsync.spend("tiktok")
    legsync.spend("tiktok")
    world.runs = run_rows(12, {**MEDIA_12, "instagram": (crosspost.POSTED, "ok"),
                               "tiktok": (crosspost.REFUSED, "Postiz upload: HTTP 503")},
                          at=NOW - timedelta(hours=5))
    world.posts = {(12, "instagram")}
    await day.leg_sync()
    assert legs_posted == []


# ------------------------------------------------------------ series order

@pytest.fixture
def series_12_13(world):
    """Part 2 is media 12, part 3 is media 13 (imports)."""
    world.responses["AS legsync_source FROM content_media"] = None
    orig = world.fetchrow

    async def fetchrow(sql, *args):
        flat = " ".join(sql.split())
        if "AS legsync_source FROM content_media" in flat:
            part = {12: 2, 13: 3}.get(args[0])
            return ({"legsync_source": f"import:Emergency Room: Code Chaos:part {part}"}
                    if part else None)
        if "AS legsync_prev FROM content_drafts" in flat:
            return {"import:Emergency Room: Code Chaos:part 2": {"legsync_prev": 12},
                    "import:Emergency Room: Code Chaos:part 1": {"legsync_prev": 11},
                    }.get(args[0])
        return await orig(sql, *args)

    world.fetchrow = fetchrow
    return world


async def test_part_n_minus_1_is_synced_before_part_n(series_12_13, legs_posted,
                                                     hands):
    world = series_12_13
    world.runs = (run_rows(12, MEDIA_12, at=NOW - timedelta(hours=30))
                  + run_rows(13, MEDIA_12, at=NOW - timedelta(hours=5), rid=10))
    world.posts = {(12, "tiktok"), (13, "tiktok")}
    # Part 3 alone is refused while part 2 is missing on Instagram.
    early = await legsync.post_missing_leg(13, "instagram")
    assert not early.ok and legsync.SERIES_HELD in early.detail
    lines = await day.leg_sync()           # one Instagram post per tick
    assert (12, "instagram") in world.posts and (13, "instagram") not in world.posts
    assert "media 12" in lines[0]
    executor._recent.clear()                    # the next hour
    await day.leg_sync()
    assert (13, "instagram") in world.posts
    assert len(legs_posted) == 2


async def test_a_new_part_waits_whole_while_the_part_before_is_missing(
        series_12_13, monkeypatch):
    """Part 3 arriving while part 2 lacks Instagram waits -- never posted
    to Instagram first, never remade, never a card."""
    world = series_12_13
    monkeypatch.delenv("REELS_AUTOPUBLISH", raising=False)
    world.runs = run_rows(12, MEDIA_12, at=NOW - timedelta(hours=5))
    world.posts = {(12, "tiktok")}
    why = await legsync.series_hold(13)
    assert why and legsync.SERIES_HELD in why and "media 12" in why
    world.posts.add((12, "instagram"))
    assert await legsync.series_hold(13) is None


async def test_a_series_hold_is_a_wait_in_post_or_card(monkeypatch):
    monkeypatch.delenv("REELS_AUTOPUBLISH", raising=False)
    from atlas.modules.content import quality

    async def ok(*_a, **_k):
        return None

    async def execute(req):
        return ActionResult(False, f"not posted automatically: "
                                   f"{legsync.SERIES_HELD} to be synced first")

    async def never(*a, **k):
        raise AssertionError("no card and no remake for a series hold")

    monkeypatch.setattr(quality, "check_media", ok)
    monkeypatch.setattr(day, "execute", execute)
    monkeypatch.setattr(day, "propose", never)
    monkeypatch.setattr(day, "preview", never)
    for on_fail in ("redo", "skip", "card"):
        got, _, _ = await day.post_or_card(
            {"media_id": 13, "caption": CAPTION}, "r", dedup="d", media_id=13,
            rollback_plan="x", series=True, on_fail=on_fail)
        assert got == "wait"


async def test_a_refused_part_does_not_hold_the_series(series_12_13):
    world = series_12_13
    world.runs = run_rows(12, {**MEDIA_12, "instagram": (
        crosspost.REFUSED, "refused: the caption names a real brand")},
        at=NOW - timedelta(hours=5))
    world.posts = {(12, "tiktok")}
    assert await legsync.series_hold(13) is None


# ------------------------------------------------------------ clips

async def test_clip_posts_are_never_touched(world, legs_posted, hands):
    """Campaign clips post through clips.publish_clip* / instagram.publish
    as the clips account -- one platform per clip by campaign rule. A
    failed clip leg in the log is not a reel's missing leg."""
    world.runs = [
        _row(1, NOW - timedelta(hours=3), "execute.start", "clips.publish_clip",
             {"params": {"clip_id": 12}}),
        _row(2, NOW - timedelta(hours=3), "execute.finish", "clips.publish_clip",
             {"ok": True, "detail": "reel 12: posted to instagram",
              "evidence": {"tiktok": {"status": "refused",
                                      "detail": "Postiz post: HTTP 502"}}}),
        _row(3, NOW - timedelta(hours=3), "execute.finish", "instagram.publish",
             {"ok": False, "detail": "reel 14: Graph API HTTP 401"}),
    ]
    # The query itself asks only for the reels lane's two publishers.
    assert await legsync.gaps() == []
    sql = next(s for s, _ in world.sql_log if "FROM actions_log" in s)
    assert "'content.autopublish_reel', 'content.publish_reel'" in sql
    assert "clips" not in sql
    assert await day.leg_sync() == [] and legs_posted == []


# ------------------------------------------------------------ the check

async def test_check_ok_when_every_leg_is_there(world):
    world.runs = run_rows(12, MEDIA_12, at=NOW - timedelta(hours=5))
    world.posts = {(12, "tiktok"), (12, "instagram")}
    obs = await legsync.check_reel_legs_sync()
    assert obs.health is Health.OK
    assert "every posted reel is on every configured platform" in obs.detail


async def test_check_degraded_when_a_retryable_leg_is_missing_over_two_hours(world):
    world.runs = run_rows(12, MEDIA_12, at=NOW - timedelta(hours=3))
    world.posts = {(12, "tiktok")}
    obs = await legsync.check_reel_legs_sync()
    assert obs.health is Health.DEGRADED
    assert "media 12 on Instagram (stories)" in obs.detail and "401" in obs.detail


async def test_check_ok_but_names_a_fresh_gap(world):
    world.runs = run_rows(12, MEDIA_12, at=NOW - timedelta(minutes=20))
    world.posts = {(12, "tiktok")}
    obs = await legsync.check_reel_legs_sync()
    assert obs.health is Health.OK and "next tick" in obs.detail


async def test_check_unknown_when_the_record_cannot_be_read(world):
    async def broken(sql, *args):
        raise OSError("connection refused")
    world.fetch = broken
    obs = await legsync.check_reel_legs_sync()
    assert obs.health is Health.UNKNOWN


async def test_check_degraded_when_a_leg_record_cannot_be_read(world):
    rows = run_rows(12, MEDIA_12, at=NOW - timedelta(hours=3))
    data = json.loads(rows[1]["payload"])
    data["data"]["evidence"] = {"truncated": True}
    rows[1]["payload"] = json.dumps(data)
    world.runs = rows
    world.posts = {(12, "tiktok")}
    obs = await legsync.check_reel_legs_sync()
    assert obs.health is Health.DEGRADED


def test_the_check_is_registered():
    names = {c.name for c in content.MODULE.checks()}
    assert "reel_legs_sync" in names


# ------------------------------------------------------------ the tick

async def test_the_tick_syncs_even_with_the_calendar_off(monkeypatch):
    ran: list[str] = []

    async def step():
        ran.append("sync")
        return ["Leg sync: x"]

    monkeypatch.setattr(day, "leg_sync", step)
    monkeypatch.setenv("REELS_CALENDAR", "off")
    lines = await day.calendar_tick()
    assert ran == ["sync"] and lines[0] == "Leg sync: x"


# ------------------------------------------------------------ the daily cap

async def test_a_caught_up_leg_does_not_take_a_new_days_slot(memory_backend):
    memory_backend.responses["FROM reel_posts WHERE at >="] = {"n": 0}
    assert await crosspost.posted_today() == 0
    sql, _ = memory_backend.sql_log[-1]
    assert "NOT EXISTS" in sql and "earlier.at < $1" in sql
