"""Academy lesson videos on YouTube: source gate, audience, quota, action, embed.

No network and no database: the YouTube API and the state functions are faked.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import executor
from atlas.kernel.contracts import Tier
from atlas.kernel.executor import ExecutionRequest, execute
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY
from atlas.modules import academy
from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import course_page as cp
from atlas.modules.academy import lesson_youtube as ly
from atlas.modules.youtube import api, vet_upload

VID = "dQw4w9WgXcQ"                      # eleven characters, like a real id
VID2 = "abcdefghijk"


# ------------------------------------------------------------------ names

def test_every_catalog_lesson_has_exactly_one_file_key_from_its_staged_name():
    cat = cat_mod.load()
    seen: dict[str, str] = {}
    for p in cat.products:
        for o in p.offers:
            for f in o.files:
                if not f.path.endswith(".mp4"):
                    continue
                folder = f.path.split("/")[1]
                course = ({"videos-adults": "adults", "videos-60plus": "60plus"}
                          .get(folder) or ("operator" if p.slug == "safe-ai-operator"
                                           else "sell"))
                num = f.path.rsplit("lesson-", 1)[1][:-4]
                assert ly.file_key(course, num) == f.key
                seen[f.key] = f.path
    assert len(seen) == 132


def test_bad_course_or_number_has_no_key():
    for course, num in [("x", "1.1"), ("adults", "1"), ("adults", "../1.1"),
                        ("adults", "1.1.1"), ("", "")]:
        assert ly.file_key(course, num) is None
        assert ly.vet({"course": course, "num": num}) is not None
    assert ly.vet({"course": "adults", "num": "7.5"}) is None


def test_video_ids_are_checked_before_they_reach_html():
    assert ly.valid_video_id(VID)
    for bad in ("", "short", VID + "x", 'a"onload=1<', None, 12345678901):
        assert not ly.valid_video_id(bad)
    with pytest.raises(ValueError):
        ly.embed_url('x"><script>')
    assert ly.embed_url(VID).startswith("https://www.youtube-nocookie.com/embed/" + VID)


# ------------------------------------------------------------------ source gate

def _stage(tmp_path, name="lesson-adults-7.5.mp4", gate=True, gate_age=10,
           approved=True):
    p = tmp_path / name
    p.write_bytes(b"\0" * 2048)
    if gate is not None:
        when = datetime.fromtimestamp(p.stat().st_mtime, UTC) + timedelta(seconds=gate_age)
        (tmp_path / (name + ".gate.json")).write_text(json.dumps(
            {"pass": gate, "checks": {"length": {"actual": 186.7}},
             "read_at": when.isoformat(), "approved": approved,
             "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}))
    return p


def test_a_gated_new_render_in_the_staging_folder_is_accepted(tmp_path):
    assert ly.staged_refusal(_stage(tmp_path), tmp_path) is None


def test_refusals(tmp_path):
    ok = _stage(tmp_path)
    # old or stored videos, anywhere else
    other = tmp_path.parent / (tmp_path.name + "-files")
    other.mkdir()
    stored = other / "lesson-adults-7.5.mp4"
    stored.write_bytes(b"x")
    (other / "lesson-adults-7.5.mp4.gate.json").write_text(json.dumps(
        {"pass": True, "read_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}))
    assert "staging" in ly.staged_refusal(stored, tmp_path)
    # a link out of the folder
    link = tmp_path / "lesson-adults-7.6.mp4"
    link.symlink_to(stored)
    assert "link" in ly.staged_refusal(link, tmp_path)
    # no gate, failed gate, stale gate, bad name
    assert "gate" in ly.staged_refusal(_stage(tmp_path, "lesson-adults-1.1.mp4", gate=None),
                                       tmp_path)
    assert "did not pass" in ly.staged_refusal(
        _stage(tmp_path, "lesson-adults-1.2.mp4", gate=False), tmp_path)
    assert "changed after" in ly.staged_refusal(
        _stage(tmp_path, "lesson-adults-1.3.mp4", gate_age=-60), tmp_path)
    assert "staged lesson" in ly.staged_refusal(_stage(tmp_path, "promo.mp4"), tmp_path)
    assert "not there" in ly.staged_refusal(tmp_path / "lesson-sell-1.1.mp4", tmp_path)
    assert ly.staged_refusal(ok, tmp_path) is None


def test_the_stores_files_folder_is_never_a_source(tmp_path, monkeypatch):
    monkeypatch.setenv(ly.STAGING_ENV, str(tmp_path / "staging"))
    assert ly.staged_refusal("data/app/academy/files/ai-for-every-age/videos-adults/"
                             "lesson-7.5.mp4") is not None


# ------------------------------------------------------------------ audience, text

def test_everything_is_unlisted_until_miro_names_the_free_previews():
    assert ly.FREE_PREVIEWS == frozenset()
    assert ly.privacy_for("aiea-adults-video-7-5") == "unlisted"


def test_a_free_preview_is_public_and_gets_a_public_playlist(monkeypatch):
    monkeypatch.setattr(ly, "FREE_PREVIEWS", frozenset({"aiea-adults-video-0-1"}))
    assert ly.privacy_for("aiea-adults-video-0-1") == "public"
    assert ly.playlist_for("Everyday AI", "public") == ("Everyday AI: free previews", "public")


def test_paid_lessons_never_share_a_public_playlist():
    title, privacy = ly.playlist_for("Everyday AI", "unlisted")
    assert privacy == "unlisted" and title != ly.playlist_for("Everyday AI", "public")[0]


def test_title_is_swappable_and_within_youtubes_limit():
    assert ly.build_title("Spot a scam text", 186.7) == (
        "Learn Spot a scam text in less than 4 minutes!")
    assert ly.build_title("Spot a scam text", 30) == (
        "Learn Spot a scam text in less than 1 minute!")
    long = ly.build_title("x" * 300, 600)
    assert len(long) <= 100 and long.endswith("in less than 11 minutes!")
    assert ly.minutes_limit(120.0) == 3     # strictly less than N


def test_description_links_back_to_the_store_and_says_the_voice_is_synthetic():
    d = ly.build_description("Course", "1.1", "Title", "https://shop.example/academy/",
                             public=True)
    assert "https://shop.example/academy/" in d and "synthetic" in d
    assert ly.store_url({"domain": "agency.storefront.test"}) == "https://agency.storefront.test/academy/"
    assert ly.store_url({}) == ""


def test_srt_comes_from_the_render_cues_and_skips_bad_ones():
    srt = ly.srt_from_cues([[1.0, 2.661, "Lesson 3 point 2."], [3, 3, "empty span"],
                            ["x", 1, "bad"], [4, 5.5, "  "], [6.0, 8.25, "Next."]])
    assert srt.startswith("1\n00:00:01,000 --> 00:00:02,661\nLesson 3 point 2.")
    assert "2\n00:00:06,000 --> 00:00:08,250\nNext." in srt and "empty span" not in srt


# ------------------------------------------------------------------ quota

def test_quota_math_for_132_lessons():
    assert ly.COST_PER_LESSON == 2150
    assert ly.safe_per_day() == 3            # 10,000 less the tutorials lane's day
    assert ly.days_needed(132, 3) == 44
    assert ly.days_needed(132, 4) == 33
    assert ly.days_needed(132, 6) == 22      # uploads alone, nothing else
    with pytest.raises(ValueError):
        ly.days_needed(132, 0)


def test_per_day_is_clamped(monkeypatch):
    for raw, want in [("2", 2), ("99", ly.PER_DAY_MAX), ("0", 1), ("x", ly.PER_DAY_DEFAULT)]:
        monkeypatch.setenv(ly.PER_DAY_ENV, raw)
        assert ly.per_day() == want


# ------------------------------------------------------------------ the action

def test_the_action_is_tier_two_with_dry_run_and_rollback_and_closed_params():
    a = {x.name: x for x in academy.MODULE.actions()}[ly.ACTION]
    assert a.tier is Tier.ASK and a.dry_run is not None and a.rollback is not None
    assert set(a.params) == {"kind", "course", "num", "product"}   # no channel, privacy or path
    assert a.vet({"course": "adults", "num": "../x"}) is not None


def test_the_generic_upload_still_refuses_the_clips_channel():
    assert vet_upload({"channel": "clips"}) is not None
    assert vet_upload({"channel": "clips", "path": "/etc/passwd"}) is not None


def test_the_lessons_go_to_the_clips_key_and_nothing_loosens():
    from atlas.modules.youtube import vet_visibility
    assert ly.lesson_channel() == "clips"
    assert vet_visibility({"channel": "clips", "privacy": "public"}) is not None


async def test_api_upload_on_clips_refuses_a_path_off_the_staging_folder(
        tmp_path, monkeypatch):
    monkeypatch.setenv(ly.STAGING_ENV, str(tmp_path / "staged"))
    bad = tmp_path / "lesson-adults-7.5.mp4"
    bad.write_bytes(b"x")
    out, why = await api.upload("clips", str(bad), "t", "d", [], privacy="private")
    assert out is None and "not an Academy tutorial" in why


class Rows:
    """A tiny fake of the lesson table and effect claims."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.sql: list[str] = []
        self.today = 0
        self.marked: list[str] = []


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = Rows()
    monkeypatch.setenv(ly.STAGING_ENV, str(tmp_path))
    _stage(tmp_path)
    (tmp_path / "lesson-adults-7.5.render.json").write_text(json.dumps(
        {"cues": [[1.0, 2.0, "Hello."]]}))
    calls: list[tuple] = []

    async def fetch_one(sql, *a):
        w.sql.append(sql)
        if "count(*)" in sql:
            return {"n": w.today}
        if "SELECT" in sql and "file_key = $1" in sql:
            return w.rows.get(a[0])
        if "effect_claims" in sql:
            return {"key": "k"}
        return None

    async def execute_sql(sql, *a):
        w.sql.append(sql)
        if sql.lstrip().startswith("INSERT INTO academy_lesson_youtube"):
            w.rows[a[0]] = {"file_key": a[0], "video_id": a[3], "withdrawn": False,
                            "done": False, "thumb_set": False, "captions_set": False,
                            "item_id": None, "playlist_id": None}
        elif sql.lstrip().startswith("UPDATE academy_lesson_youtube\n") or \
                "SET privacy = $2" in sql:
            w.rows[a[0]].update(privacy=a[1], playlist_id=a[2], item_id=a[3],
                                thumb_set=a[4], captions_set=a[5], done=a[6])
        elif "withdrawn = true" in sql:
            for r in w.rows.values():
                if r["video_id"] == a[0]:
                    r["withdrawn"] = True

    async def upload(ch, path, title, desc, tags, privacy="private", **kw):
        calls.append(("upload", ch, privacy, kw))
        return {"video_id": VID}, None

    async def set_privacy(ch, vid, privacy, **kw):
        calls.append(("privacy", vid, privacy))
        return {}, None

    async def find_playlist(ch, title):
        calls.append(("find", title))
        return {}, None

    async def create_playlist(ch, title, desc, privacy="public"):
        calls.append(("create", title, privacy))
        return {"id": "PL1", "privacy": privacy}, None

    async def add_item(ch, pl, vid, *a, **k):
        calls.append(("item", pl, vid))
        return {"item_id": "IT1"}, None

    async def captions(ch, vid, srt, **kw):
        calls.append(("captions", srt.count("-->")))
        return {}, None

    async def thumb(ch, vid, path):
        calls.append(("thumb", path))
        return {}, None

    async def out(ch, item):
        calls.append(("out", item))
        return True, None

    async def mark(video_id):
        w.marked.append(video_id)

    monkeypatch.setattr("atlas.modules.youtube._mark_academy", mark)
    monkeypatch.setattr("atlas.kernel.state.fetch_one", fetch_one)
    monkeypatch.setattr("atlas.kernel.state.execute_sql", execute_sql)
    for name, fn in [("upload", upload), ("set_privacy", set_privacy),
                     ("find_playlist", find_playlist), ("create_playlist", create_playlist),
                     ("add_playlist_item", add_item), ("set_captions", captions),
                     ("set_thumbnail", thumb), ("take_out_of_playlist", out)]:
        monkeypatch.setattr(api, name, fn)
    w.calls = calls
    return w


async def test_a_paid_lesson_goes_up_private_then_unlisted_in_an_unlisted_playlist(world):
    res = await ly.upload_lesson("adults", "7.5")
    assert res.ok, res.detail
    kinds = [c[0] for c in world.calls]
    assert kinds[0] == "upload" and world.calls[0][2] == "private"
    assert world.calls[0][3]["synthetic"] is True and world.calls[0][3]["language"] == "en-GB"
    assert ("create", "Everyday AI for Adults (course members)", "unlisted") in world.calls \
        or any(c[0] == "create" and c[2] == "unlisted" for c in world.calls)
    assert world.calls[-1] == ("privacy", VID, "unlisted")      # audience last
    assert ("captions", 1) in world.calls
    assert world.rows["aiea-adults-video-7-5"]["video_id"] == VID
    assert world.rows["aiea-adults-video-7-5"]["privacy"] == "unlisted"


async def test_a_free_preview_goes_public_in_a_public_playlist(world, monkeypatch):
    monkeypatch.setattr(ly, "FREE_PREVIEWS", frozenset({"aiea-adults-video-7-5"}))
    res = await ly.upload_lesson("adults", "7.5")
    assert res.ok
    assert any(c[0] == "create" and c[2] == "public" for c in world.calls)
    assert world.calls[-1] == ("privacy", VID, "public")




async def test_an_ungated_render_is_refused_before_anything_leaves(world, tmp_path):
    (tmp_path / "lesson-adults-7.5.mp4.gate.json").unlink()
    res = await ly.upload_lesson("adults", "7.5")
    assert not res.ok and "gate" in res.detail and world.calls == []


async def test_the_daily_cap_is_counted_from_the_table(world):
    world.today = ly.per_day()
    res = await ly.upload_lesson("adults", "7.5")
    assert not res.ok and "cap" in res.detail and world.calls == []
    dry = await ly.upload_lesson_dry("adults", "7.5")
    assert not dry.ok and "cap" in dry.detail


async def test_dry_run_changes_nothing_and_names_the_audience(world):
    dry = await ly.upload_lesson_dry("adults", "7.5")
    assert dry.ok and "UNLISTED" in dry.detail and world.calls == []


async def test_a_lesson_already_done_is_not_uploaded_twice(world):
    await ly.upload_lesson("adults", "7.5")
    world.calls.clear()
    again = await ly.upload_lesson("adults", "7.5")
    assert not again.ok and "already" in again.detail and world.calls == []


async def test_rollback_sets_private_leaves_the_playlist_and_never_deletes(world):
    res = await ly.upload_lesson("adults", "7.5")
    world.calls.clear()
    back = await ly.upload_lesson_rollback(res.rollback_token)
    assert back.ok
    assert world.calls == [("privacy", VID, "private"), ("out", "IT1")]
    assert world.rows["aiea-adults-video-7-5"]["withdrawn"] is True
    bad = await ly.upload_lesson_rollback("not json")
    assert not bad.ok


async def test_watch_only_refuses_the_upload(world, monkeypatch, memory_backend):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()
    a = {x.name: x for x in academy.MODULE.actions()}[ly.ACTION]
    REGISTRY.actions[a.name] = a
    REGISTRY.decisions[a.name] = classify(a)
    try:
        result = await execute(ExecutionRequest(ly.ACTION, {"course": "adults",
                                                            "num": "7.5"}, "test"))
    finally:
        REGISTRY.actions.clear()
        REGISTRY.decisions.clear()
        executor._recent.clear()
    assert not result.ok and world.calls == []


# ------------------------------------------------------------------ the page

def _cat():
    return cat_mod.parse(copy.deepcopy(json.loads(cat_mod.CATALOG_PATH.read_text())))


def _verdict(cat, offer_id):
    from atlas.modules.academy import stripe_read
    o = next(o for p in cat.products for o in p.offers if o.id == offer_id)
    return stripe_read.Verdict(status="paid", session_id="cs_test_x", offers=(o,)), o


def _page(ids=None, missing=()):
    cat = _cat()
    v, o = _verdict(cat, "safe-ai-operator")
    links = {f.key: (cp.FileLink(present=False) if f.key in missing else
                     cp.FileLink(present=True, href=f"/f/{f.key}", view_href=f"/f/{f.key}?v=1"))
             for f in o.files}
    return cp.render(cat, v, links, {}, contact_html="help", nonce="N", youtube_ids=ids)


def test_csp_allows_only_the_privacy_enhanced_youtube_frame():
    csp = cp.csp("N")
    assert "frame-src 'self' https://www.youtube-nocookie.com;" in csp
    assert "youtube.com" not in csp.replace("youtube-nocookie.com", "")
    assert "script-src 'nonce-N'" in csp                   # still no IFrame API script


def test_a_lesson_with_an_id_carries_it_and_the_player_has_an_iframe():
    page = _page({"operator-video-0-1": VID})
    assert f'data-yt="{VID}"' in page and page.count('data-yt="') == 1
    assert '<iframe id="yt"' in page and "youtube-nocookie" in page
    assert page.count("<video") == 1


def test_a_lesson_with_no_id_falls_back_to_its_own_file():
    page = _page({})
    assert 'data-yt="' not in page and "/f/operator-video-0-1?v=1" in page


def test_a_bad_id_never_reaches_the_page():
    page = _page({"operator-video-0-1": 'x"><script>alert(1)</script>'})
    assert 'data-yt="' not in page and "alert(1)" not in page


def test_a_lesson_whose_file_is_missing_still_plays_from_youtube():
    page = _page({"operator-video-0-1": VID}, missing={"operator-video-0-1"})
    assert f'data-yt="{VID}"' in page


def test_the_paid_page_asks_for_a_referrer_so_the_embed_plays(monkeypatch):
    from atlas.api import academy as api_academy
    cat = _cat()
    v, _o = _verdict(cat, "safe-ai-operator")
    monkeypatch.setenv("ACADEMY_DOWNLOAD_KEY", "k" * 32)
    monkeypatch.setattr(api_academy.delivery, "signing_key", lambda: b"k" * 32)
    resp = api_academy._paid_page(cat, v, {}, {"operator-video-0-1": VID})
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "frame-src 'self' https://www.youtube-nocookie.com" in \
        resp.headers["content-security-policy"]


# ------------------------------------------------------------------ what is exposed

async def test_the_stores_links_read_only_public_ids(monkeypatch):
    seen: list[str] = []

    async def fetch_all(sql, *a):
        seen.append(sql)
        return [{"file_key": "k", "video_id": VID}, {"file_key": "j", "video_id": "bad"}]

    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)
    assert await ly.public_ids() == {"k": VID}
    assert "privacy = 'public'" in seen[0] and "unlisted" not in seen[0]
    assert ly.watch_link_html("bad") == ""
    assert VID in ly.watch_link_html(VID)


async def test_an_unreadable_database_gives_no_ids_so_the_page_uses_its_own_files(monkeypatch):
    async def boom(sql, *a):
        raise ConnectionRefusedError

    monkeypatch.setattr("atlas.kernel.state.fetch_all", boom)
    assert await ly.embed_ids() == {}


def test_no_store_page_builder_reads_the_embed_ids():
    from pathlib import Path

    from atlas.modules.academy import site
    src = Path(site.__file__).read_text()
    assert "embed_ids" not in src and "lesson_youtube" not in src


async def test_a_gate_record_without_a_length_cannot_make_a_title(world, tmp_path):
    g = tmp_path / "lesson-adults-7.5.mp4.gate.json"
    d = json.loads(g.read_text())
    d["checks"] = {}
    g.write_text(json.dumps(d))
    res = await ly.upload_lesson("adults", "7.5")
    assert not res.ok and "length" in res.detail and world.calls == []


async def test_the_upload_is_recorded_for_the_public_gate_and_the_shared_cap(world):
    res = await ly.upload_lesson("adults", "7.5")
    assert res.ok and world.marked == [VID]


async def test_the_generic_card_obeys_the_same_daily_cap(world, tmp_path, monkeypatch):
    monkeypatch.setenv("ACADEMY_STAGING_DIR", str(tmp_path))
    from atlas.modules.youtube import upload_video
    world.today = ly.per_day()
    res = await upload_video("clips", str(tmp_path / "lesson-adults-7.5.mp4"), "t", "d", [])
    assert not res.ok and "cap" in res.detail and world.calls == []


def test_a_render_without_approval_is_refused(tmp_path):
    p = _stage(tmp_path, approved=False)
    assert "approved" in ly.staged_refusal(p, tmp_path)
    g = tmp_path / (p.name + ".gate.json")
    d = json.loads(g.read_text())
    del d["approved"]
    g.write_text(json.dumps(d))
    assert "approved" in ly.staged_refusal(p, tmp_path)


def test_a_render_whose_bytes_changed_after_approval_is_refused(tmp_path):
    p = _stage(tmp_path)
    assert ly.staged_refusal(p, tmp_path) is None
    st = p.stat()
    p.write_bytes(b"\1" * 2048)                         # same size, other bytes
    os.utime(p, (st.st_atime, st.st_mtime))             # even with the old mtime
    assert "different file" in ly.staged_refusal(p, tmp_path)


def test_a_wrong_hash_is_refused(tmp_path):
    p = _stage(tmp_path)
    g = tmp_path / (p.name + ".gate.json")
    d = json.loads(g.read_text())
    d["sha256"] = "0" * 64
    g.write_text(json.dumps(d))
    assert "different file" in ly.staged_refusal(p, tmp_path)


def test_write_approval_only_after_a_passing_fresh_gate(tmp_path):
    p = _stage(tmp_path, approved=False)
    assert ly.write_approval(p) is None
    assert ly.staged_refusal(p, tmp_path) is None
    assert not list(tmp_path.glob("*.tmp"))
    failed = _stage(tmp_path, "lesson-adults-1.1.mp4", gate=False, approved=False)
    assert "did not pass" in ly.write_approval(failed)
    assert json.loads((tmp_path / (failed.name + ".gate.json")).read_text()).get(
        "approved") is not True
    stale = _stage(tmp_path, "lesson-adults-1.2.mp4", gate_age=-10)
    assert "older" in ly.write_approval(stale)
    assert "no gate record" in ly.write_approval(
        _stage(tmp_path, "lesson-adults-1.3.mp4", gate=None))


# ------------------------------------------------------------------ kinds (preview, short)

def _stage_kind(tmp_path, name, seconds=40.0, size="1920x1080"):
    p = tmp_path / name
    p.write_bytes(b"\0" * 2048)
    when = datetime.fromtimestamp(p.stat().st_mtime, UTC) + timedelta(seconds=10)
    (tmp_path / (name + ".gate.json")).write_text(json.dumps(
        {"pass": True, "checks": {"length": {"actual": seconds},
                                  "streams": {"size": size}},
         "read_at": when.isoformat(), "approved": True,
         "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}))
    return p


def test_vet_knows_the_three_kinds_and_a_product_preview():
    assert ly.vet({"kind": "bogus", "course": "adults", "num": "7.5"})
    assert ly.vet({"kind": "preview", "course": "adults", "num": "7.5"}) is None
    assert ly.vet({"kind": "short", "course": "adults", "num": "7.5"}) is None
    assert ly.vet({"kind": "preview", "product": "sell-with-ai"}) is None
    assert ly.vet({"kind": "full", "product": "sell-with-ai"})       # only a preview
    assert ly.vet({"kind": "preview", "product": "x/../y"})
    assert ly.vet({"kind": "preview", "product": "sell-with-ai", "course": "adults",
                   "num": "7.5"})


def test_every_kind_has_its_own_staged_name_and_key():
    assert ly.stem("adults", "7.5") == "lesson-adults-7.5"
    assert ly.stem("adults", "7.5", "preview") == "preview-lesson-adults-7.5"
    assert ly.stem("adults", "7.5", "short") == "short-lesson-adults-7.5"
    assert ly.row_key("full", "k") == "k" and ly.row_key("preview", "k") == "preview:k"
    assert ly.kind_of_name("preview-product-sell-with-ai.mp4") == "preview"
    assert ly.kind_of_name("short-lesson-sell-1.1.mp4") == "short"
    assert ly.kind_of_name("promo.mp4") is None


def test_the_same_source_gate_covers_every_kind(tmp_path):
    for name in ("preview-lesson-adults-7.5.mp4", "preview-product-sell-with-ai.mp4"):
        p = _stage_kind(tmp_path, name)
        assert ly.staged_refusal(p, tmp_path) is None
        assert ly.staged_refusal(p, tmp_path, kind="full") is not None   # wrong kind
        (tmp_path / (name + ".gate.json")).unlink()
        assert "gate" in ly.staged_refusal(p, tmp_path)
    ungated = _stage_kind(tmp_path, "short-lesson-adults-7.5.mp4", size="1080x1920")
    d = json.loads((tmp_path / "short-lesson-adults-7.5.mp4.gate.json").read_text())
    d["approved"] = False
    (tmp_path / "short-lesson-adults-7.5.mp4.gate.json").write_text(json.dumps(d))
    assert "approved" in ly.staged_refusal(ungated, tmp_path)


def test_a_short_must_be_vertical_and_under_a_minute(tmp_path):
    ok = _stage_kind(tmp_path, "short-lesson-adults-7.5.mp4", 45, "1080x1920")
    assert ly.staged_refusal(ok, tmp_path, kind="short") is None
    long = _stage_kind(tmp_path, "short-lesson-adults-7.6.mp4", 60, "1080x1920")
    assert "under 60" in ly.staged_refusal(long, tmp_path)
    wide = _stage_kind(tmp_path, "short-lesson-adults-7.7.mp4", 30, "1920x1080")
    assert "vertical" in ly.staged_refusal(wide, tmp_path)


async def test_a_preview_is_public_in_the_public_playlist_with_the_store_link(
        world, tmp_path, monkeypatch):
    _stage_kind(tmp_path, "preview-lesson-adults-7.5.mp4", 100)
    (tmp_path / "preview-lesson-adults-7.5.png").write_bytes(b"png")
    seen = {}
    orig = api.upload

    async def spy(ch, path, title, desc, tags, **kw):
        seen.update(title=title, desc=desc)
        return await orig(ch, path, title, desc, tags, **kw)

    monkeypatch.setattr(api, "upload", spy)
    res = await ly.upload_lesson("adults", "7.5", "preview")
    assert res.ok, res.detail
    assert seen["title"].startswith("Learn ") and seen["title"].endswith(
        "in less than 2 minutes!")
    assert "Free preview" in seen["desc"] and "/academy/" in seen["desc"]
    assert any(c[0] == "create" and c[2] == "public" for c in world.calls)
    assert ("thumb", str(tmp_path / "preview-lesson-adults-7.5.png")) in world.calls
    assert world.calls[-1] == ("privacy", VID, "public")
    assert world.rows["preview:aiea-adults-video-7-5"]["privacy"] == "public"


async def test_a_preview_of_a_product_uses_the_product_slug(world, tmp_path):
    _stage_kind(tmp_path, "preview-product-sell-with-ai.mp4", 70)
    res = await ly.upload_lesson(kind="preview", product="sell-with-ai")
    assert res.ok, res.detail
    assert "preview:product-sell-with-ai" in world.rows
    assert world.calls[-1] == ("privacy", VID, "public")
    dry = await ly.upload_lesson_dry(kind="preview", product="no-such-product")
    assert not dry.ok and "catalog" in dry.detail


async def test_a_short_is_public_with_no_captions_or_thumbnail(world, tmp_path):
    _stage_kind(tmp_path, "short-lesson-adults-7.5.mp4", 40, "1080x1920")
    (tmp_path / "short-lesson-adults-7.5.render.json").write_text(
        json.dumps({"cues": [[1.0, 2.0, "Hello."]]}))
    (tmp_path / "short-lesson-adults-7.5.png").write_bytes(b"png")
    res = await ly.upload_lesson("adults", "7.5", "short")
    assert res.ok, res.detail
    kinds = [c[0] for c in world.calls]
    assert "captions" not in kinds and "thumb" not in kinds
    assert any(c[0] == "create" and "Shorts" in c[1] and c[2] == "public"
               for c in world.calls)
    assert "short:aiea-adults-video-7-5" in world.rows


async def test_full_previews_and_shorts_share_one_daily_cap(world, tmp_path, monkeypatch):
    monkeypatch.setenv(ly.PER_DAY_ENV, "3")
    _stage_kind(tmp_path, "preview-lesson-adults-7.5.mp4", 40)
    _stage_kind(tmp_path, "short-lesson-adults-7.5.mp4", 40, "1080x1920")
    _stage_kind(tmp_path, "preview-lesson-adults-7.6.mp4", 40)
    plain = ly  # noqa: F841

    real = __import__("atlas.kernel.state", fromlist=["fetch_one"]).fetch_one

    async def counting(sql, *a):
        if "count(*)" in sql:
            return {"n": len(world.rows) if "academy_lesson_youtube" in sql else 0}
        return await real(sql, *a)

    monkeypatch.setattr("atlas.kernel.state.fetch_one", counting)
    first = await ly.upload_lesson("adults", "7.5")
    second = await ly.upload_lesson("adults", "7.5", "preview")
    third = await ly.upload_lesson("adults", "7.5", "short")
    assert first.ok and second.ok and third.ok
    assert len(world.rows) == 3 and await ly.uploaded_today() == 3
    calls_before = len(world.calls)
    fourth = await ly.upload_lesson("adults", "7.6", "preview")
    assert not fourth.ok and "cap" in fourth.detail and len(world.calls) == calls_before
    dry = await ly.upload_lesson_dry("adults", "7.6", "preview")
    assert not dry.ok and "cap" in dry.detail


def test_the_hard_maximum_is_four_whatever_the_setting(monkeypatch):
    monkeypatch.setenv(ly.PER_DAY_ENV, "99")
    assert ly.per_day() == 4
    monkeypatch.setenv(ly.PER_DAY_ENV, "0")
    assert ly.per_day() == 1


async def test_the_daily_count_takes_the_larger_of_the_two_tables(monkeypatch):
    async def fetch_one(sql, *a):
        return {"n": 1 if "academy_lesson_youtube" in sql else 3}

    monkeypatch.setattr("atlas.kernel.state.fetch_one", fetch_one)
    assert await ly.uploaded_today() == 3


async def test_the_stores_links_come_from_public_previews_only(monkeypatch):
    seen: list[str] = []

    async def fetch_all(sql, *a):
        seen.append(sql)
        return [{"file_key": "preview:aiea-adults-video-7-5", "video_id": VID},
                {"file_key": "preview:product-sell-with-ai", "video_id": VID2},
                {"file_key": "preview:bad", "video_id": "bad"}]

    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)
    got = await ly.public_ids()
    assert got == {"aiea-adults-video-7-5": VID, "product-sell-with-ai": VID2}
    assert "kind = 'preview'" in seen[0] and "privacy = 'public'" in seen[0]
    assert "unlisted" not in seen[0] and "NOT withdrawn" in seen[0]
    assert "kind = 'full'" in (await _embed_sql(monkeypatch))


async def _embed_sql(monkeypatch):
    seen: list[str] = []

    async def fetch_all(sql, *a):
        seen.append(sql)
        return []

    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)
    await ly.embed_ids()
    return seen[0]


def _page_with(preview_ids, ids=None):
    cat = _cat()
    v, o = _verdict(cat, "safe-ai-operator")
    links = {f.key: cp.FileLink(present=True, href=f"/f/{f.key}",
                                view_href=f"/f/{f.key}?v=1") for f in o.files}
    return cp.render(cat, v, links, {}, contact_html="help", nonce="N",
                     youtube_ids=ids, preview_ids=preview_ids)


def test_watch_on_youtube_shows_only_for_a_public_preview_id():
    page = _page_with({"operator-video-0-1": VID})
    assert page.count("Watch on YouTube") == 1
    assert f"https://www.youtube.com/watch?v={VID}" in page
    assert "script-src 'nonce-N'" in cp.csp("N")


def test_no_link_without_a_preview_id_or_with_a_bad_one():
    assert "Watch on YouTube" not in _page_with({})
    assert "Watch on YouTube" not in _page_with(None)
    bad = _page_with({"operator-video-0-1": 'x"><script>alert(1)</script>'})
    assert "Watch on YouTube" not in bad and "alert(1)" not in bad


def test_an_unlisted_embed_id_never_becomes_a_store_link():
    page = _page_with({}, ids={"operator-video-0-1": VID})
    assert "Watch on YouTube" not in page and "youtube.com/watch" not in page


def test_a_product_preview_links_from_the_course_header():
    page = _page_with({"product-safe-ai-operator": VID2})
    assert page.count("Watch on YouTube") == 1 and VID2 in page


def test_the_approval_card_words_each_kind():
    from atlas.kernel.wa_approvals import EFFECTS
    f = EFFECTS[ly.ACTION]
    assert "PREVIEW goes PUBLIC" in f({"kind": "preview"})
    assert "Short goes PUBLIC" in f({"kind": "short"})
    assert "UNLISTED" in f({}) and "daily cap" in f({})
