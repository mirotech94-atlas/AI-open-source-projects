"""The combined video-system bump (2026-10-18): every unposted draft renders
again, once, and nothing public is uploaded again.

The keys before the bump were recorded at d6613f3a (the branch tip the bump
started from) in fixtures/academy_render_keys_d6613f3a.json. Which drafts
are unposted lives in Postgres, so every episode scripted at the bump is
checked: the unposted ones are among them. An episode scripted later has no
recorded key (none is made up for it); any render of it from before the bump
carries the pre-bump versions in its key, so it is never reused either."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from atlas.kernel.contracts import ActionResult
from atlas.modules.academy import tutorial_design as design
from atlas.modules.academy import tutorial_music, tutorial_thumbs
from atlas.modules.academy import tutorial_series as ts
from atlas.modules.academy import tutorials as tut

BEFORE = json.loads((Path(__file__).parent / "fixtures"
                     / "academy_render_keys_d6613f3a.json").read_text())["keys"]
SCRIPTED = [(s, e) for s in ts.SERIES for e in s.episodes if e.scripted]
# Scripted at the bump (their keys recorded), and scripted after it.
RECORDED = [(s, e) for s, e in SCRIPTED if f"{s.key}/{e.slug}" in BEFORE]
LATER = [(s, e) for s, e in SCRIPTED if f"{s.key}/{e.slug}" not in BEFORE]
# The bump's raised versions that script_key reads, at their pre-bump values
# (f1fcaa4c; tutorial_short.LOOK_VERSION is the Shorts' and not in the key).
PRE_BUMP = ((design, "DESIGN_VERSION", "7"), (tutorial_music, "MUSIC_VERSION", "1"),
            (tutorial_thumbs, "THUMB_VERSION", "3"))


def _fresh_key(ep) -> str:
    """script_key with look_stamp (cached once per process) read again."""
    design.look_stamp.cache_clear()
    return tut.script_key(ep)


@pytest.fixture
def default_env(monkeypatch):
    """The keys were recorded with the defaults: no speed or model override."""
    for name in ("ACADEMY_TUTORIALS_VOICE_SPEED", "ACADEMY_TUTORIALS_FISH_MODEL"):
        monkeypatch.delenv(name, raising=False)


def test_every_recorded_episode_is_still_scripted():
    """The record is the bump's set: no entry lost its script or its slug."""
    assert {f"{s.key}/{e.slug}" for s, e in RECORDED} == set(BEFORE)


@pytest.mark.parametrize("s,ep", RECORDED, ids=lambda x: getattr(x, "slug", ""))
def test_every_recorded_episode_has_a_new_render_key(default_env, s, ep):
    assert tut.script_key(ep) != BEFORE[f"{s.key}/{ep.slug}"]


@pytest.mark.parametrize("s,ep", LATER, ids=lambda x: getattr(x, "slug", ""))
def test_a_later_episode_has_no_render_from_before_the_bump(default_env, monkeypatch,
                                                            s, ep):
    """An episode scripted after the bump has no recorded key. A render of it
    made before the bump (a draft from an earlier deploy) was keyed with the
    pre-bump versions: each one alone moves the key, so reusable() refuses
    that render and it is drawn again under the new gates."""
    now = _fresh_key(ep)
    for mod, name, old in PRE_BUMP:
        assert getattr(mod, name) != old, name
        with monkeypatch.context() as m:
            m.setattr(mod, name, old)
            assert _fresh_key(ep) != now, name
    with monkeypatch.context() as m:
        for mod, name, old in PRE_BUMP:
            m.setattr(mod, name, old)
        pre = _fresh_key(ep)
    assert _fresh_key(ep) == now
    stale = tut.Manifest(series=s.key, episode=ep.number, audio="fish", seconds=300.0,
                         chapters=[(0.0, "a")],
                         parts=["intro-result.mp4", "outro-card.mp4"], script=pre)
    monkeypatch.setattr(tut, "publishable_render", lambda _p: None)
    monkeypatch.setattr(tut, "read_manifest", lambda _d: stale)
    monkeypatch.setattr(tut, "bookends_refusal", lambda _m: None)
    assert tut.reusable(s, ep) is False


@pytest.mark.parametrize("s,ep", RECORDED[:3] + RECORDED[-3:],
                         ids=lambda x: getattr(x, "slug", ""))
def test_an_old_render_is_never_reused(default_env, monkeypatch, s, ep):
    """reusable() (what _post and redraft_stale ask before rendering) is
    False for a finished render made under the old key, however complete,
    so it is rendered again and passes through the new gates."""
    old = tut.Manifest(series=s.key, episode=ep.number, audio="fish", seconds=300.0,
                       chapters=[(0.0, "a")], parts=["intro-result.mp4", "outro-card.mp4"],
                       script=BEFORE[f"{s.key}/{ep.slug}"])
    monkeypatch.setattr(tut, "publishable_render", lambda _p: None)
    monkeypatch.setattr(tut, "read_manifest", lambda _d: old)
    monkeypatch.setattr(tut, "bookends_refusal", lambda _m: None)
    assert tut.reusable(s, ep) is False
    monkeypatch.setattr(tut, "read_manifest",
                        lambda _d: tut.Manifest(**{**old.__dict__,
                                                   "script": tut.script_key(ep)}))
    assert tut.reusable(s, ep) is True                # the check itself works


# ---------------------------------------------- nothing public goes up again

def test_the_daily_lineup_never_picks_an_episode_already_posted(monkeypatch):
    """_done() counts every academy_tutorial_posts row (public or not) and
    every drafted one; the lineup skips them however stale their key is."""
    monkeypatch.setenv("ACADEMY_TUTORIALS_PER_SERIES_DAILY", "50")
    posted = {(s.key, e.number) for s, e in SCRIPTED}
    todo, _notes = tut.lineup(posted, {}, {})
    assert not set(todo) & posted


async def test_a_public_episode_is_never_uploaded_again(monkeypatch, tmp_path):
    """The bump re-keys public episodes too; if one reached _publish (the
    automatic post, or Miro's card), it is refused before any upload."""
    from atlas.kernel import idempotency
    from atlas.modules.youtube import api
    s, ep = tut.resolve("n8n", 1)
    m = tut.Manifest(series="n8n", episode=1, audio="fish", seconds=300.0,
                     chapters=[(0.0, "a")])

    async def ready(_series, _n):
        return (s, ep, tmp_path, m, "description"), None

    async def posted(_key, _n):
        return {"id": 7, "video_id": "Vaaaaaaaaaa", "public": True, "withdrawn": False}

    calls: list[str] = []

    async def forbidden(*_a, **_k):
        calls.append("youtube")
        return None, "must not be called"

    async def claim(*_a, **_k):
        calls.append("claim")
        return idempotency.CLAIMED

    monkeypatch.setattr(tut, "ready", ready)
    monkeypatch.setattr(tut, "posted", posted)
    monkeypatch.setattr(api, "upload", forbidden)
    monkeypatch.setattr(idempotency, "claim", claim)
    for strict in (True, False):
        got = await tut._publish("n8n", 1, strict=strict)
        assert isinstance(got, ActionResult) and not got.ok
        assert "already public" in got.detail
    assert calls == []


async def test_a_stale_draft_of_a_posted_episode_is_never_redrafted(monkeypatch):
    """redraft_stale reads only drafts with no academy_tutorial_posts row."""
    from atlas.kernel import state
    seen: list[str] = []

    async def fetch_all(sql, *args):
        seen.append(sql)
        return []

    monkeypatch.setattr(state, "fetch_all", fetch_all)
    assert await tut._stale_drafts() == []
    assert "NOT EXISTS" in seen[0] and "academy_tutorial_posts" in seen[0]


def test_the_relaunch_stays_an_ask():
    """Re-uploading a public lesson is academy.relaunch_tutorial, Tier 2:
    no schedule runs it, so a new key alone never relaunches anything."""
    from atlas.kernel.contracts import Tier
    from atlas.modules.academy import AcademyModule
    act = next(a for a in AcademyModule().actions() if a.name == "academy.relaunch_tutorial")
    assert act.tier == Tier.ASK
    sched = (Path(tut.__file__).parents[2] / "kernel" / "scheduler.py").read_text()
    assert "relaunch_tutorial" not in sched


async def test_a_teaching_short_already_posted_is_never_cut_again(monkeypatch):
    """LOOK_VERSION 3 makes rendered_teaching() miss every older Short, so a
    posted cut would be rendered again if anything chose it. The guard is on
    the slug: untaught() lists every academy_promo_posts slug as taught,
    whatever look it went up in, and _next_cut never tries a taught kind."""
    from atlas.kernel import state
    from atlas.modules.academy import tutorial_short
    tried: list[str] = []

    async def cut(_key, _n, kind):
        tried.append(kind)
        return "no cut"

    async def fetch_all(sql, *args):
        if "academy_tutorial_posts" in sql:
            return [{"series": "n8n", "episode": 1, "at": None}]
        if "academy_promo_posts" in sql:
            return [{"slug": f"tutorial:n8n:1:{k}"} for k in tutorial_short.KINDS]
        return []

    monkeypatch.setattr(tut, "_teaching_cut", cut)
    monkeypatch.setattr(state, "fetch_all", fetch_all)
    (row,) = await tut.untaught()
    assert set(row["taught"]) == set(tutorial_short.KINDS)
    assert isinstance(await tut._next_cut(row), str) and tried == []
