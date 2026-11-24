"""The Mac agent hands Whop the posted clip links; ATLAS counts what it did.

Miro, 2026-09-29, in writing. What these pin down:
  * both endpoints need the HANDOFF_TOKEN bearer and are mesh-only, like
    /handoffs;
  * the pending list offers only a clip whose post URL came from the
    platform's own response or Miro's clips.record_link, and only for a
    campaign with a permission source AND a whop_url on file;
  * recording a submission goes through kernel.unattended to the executor
    (dry run first); a URL that is not the clip's own is refused; a second
    submission is refused, and the identical repeat changes nothing;
  * the Tier 2 Whop card waits 48 h only for a clip the agent is offered;
    a clip it will never see gets the card at once, as before;
  * clips_earnings reports counts with their read time and no money field.
"""
from __future__ import annotations

import dataclasses
import json
import pathlib
import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.kernel import executor
from atlas.kernel.contracts import Health, Severity, Tier
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY
from atlas.modules import clips
from atlas.modules.clips import campaigns, posting, whop

ATLUS = "atlus-persona-metaphor"
COD = "cod-mw4-warzone-operator"
URL = "https://www.instagram.com/reel/Cxyz/"
WHOP = "https://whop.com/clipping-culture/cod"
TOKEN = "h" * 32


def _campaign(slug=COD, **changes) -> campaigns.Campaign:
    base = {c.slug: c for c in campaigns.seeds()}
    return dataclasses.replace(base[slug], **changes)


def _campaigns(monkeypatch, *found):
    by_slug = {c.slug: c for c in found}

    async def load_all():
        return list(found)

    async def load(slug):
        return by_slug.get(slug)

    monkeypatch.setattr(campaigns, "load_all", load_all)
    monkeypatch.setattr(campaigns, "load", load)


# ------------------------------------------------------------------ catalog

def test_the_record_is_tier_1_card_only_with_a_dry_run_and_rollback():
    actions = {a.name: a for a in clips.MODULE.actions()}
    record = actions["clips.record_whop_submission"]
    assert record.tier is Tier.AUTO and record.card_only
    assert record.dry_run is not None and record.rollback is not None
    assert classify(record).allowed and classify(record).tier is Tier.AUTO
    # Miro, 2026-10-06: the clips lane runs with no card, the submission too.
    assert actions["clips.submit_to_whop"].tier is Tier.AUTO


def test_the_records_and_the_auto_posts_are_the_clips_actions_on_the_unattended_door():
    """The three records, and (2026-10-06, Whop's 30-minute link window)
    the three Tier 1 auto posts an approved draft may ask for via
    /post_now -- never the YouTube API leg, which is a card."""
    from atlas.kernel import unattended
    assert {a for a in unattended.ALLOWED if a.startswith("clips.")} == {
        "clips.record_whop_submission", "clips.record_draft_submitted",
        "clips.record_draft_decision", "clips.publish_clip",
        "clips.publish_clip_tiktok", "clips.publish_clip_youtube_postiz"}
    actions = {a.name: a for a in clips.MODULE.actions()}
    for name in ("clips.publish_clip", "clips.publish_clip_tiktok",
                 "clips.publish_clip_youtube_postiz"):
        assert actions[name].tier is Tier.AUTO
    actions = {a.name: a for a in clips.MODULE.actions()}
    for name in ("clips.record_draft_submitted", "clips.record_draft_decision"):
        record = actions[name]
        assert record.tier is Tier.AUTO and record.card_only
        assert record.dry_run is not None and record.rollback is not None
        assert classify(record).allowed and classify(record).tier is Tier.AUTO


# ------------------------------------------------------------ the record

class FakeRow:
    """One clip_outputs row, and every write made to it."""

    def __init__(self, status="posted", url=URL, source="instagram",
                 submitted_by=None):
        self.row = {"id": 5, "campaign": COD, "status": status, "post_url": url,
                    "post_url_source": source,
                    "submitted_at": (datetime.now(UTC) if status == "submitted"
                                     else None),
                    "submitted_by": submitted_by}
        self.updates = 0

    async def fetch_one(self, sql, *args):
        if sql.lstrip().startswith("UPDATE"):
            assert args == (5, "mac-agent", self.row["post_url"])
            if self.row["status"] != "posted":
                return None
            self.updates += 1
            self.row.update(status="submitted", submitted_at=datetime.now(UTC),
                            submitted_by="mac-agent")
            return {"id": 5}
        return dict(self.row) if args[0] == 5 else None


@pytest.fixture
def cod_on_file(monkeypatch):
    _campaigns(monkeypatch, _campaign(COD, whop_url=WHOP))


async def test_a_submission_is_recorded_once_and_the_repeat_changes_nothing(
        monkeypatch, cod_on_file):
    db = FakeRow()
    monkeypatch.setattr(whop, "fetch_one", db.fetch_one)
    assert (await whop.record_submission_dry(5, URL)).ok
    first = await whop.record_submission(5, URL)
    assert first.ok and first.rollback_token and db.updates == 1
    assert first.evidence == {"already": False}
    again = await whop.record_submission(5, URL)
    assert again.ok and again.evidence == {"already": True} and db.updates == 1
    # The repeat carries no rollback: undoing it cannot undo the first.
    assert again.rollback_token is None
    assert db.row["submitted_by"] == "mac-agent"


async def test_a_second_submission_is_refused(monkeypatch, cod_on_file):
    # Miro's card recorded it first: the agent's claim is a second one.
    db = FakeRow(status="submitted", submitted_by="miro")
    monkeypatch.setattr(whop, "fetch_one", db.fetch_one)
    got = await whop.record_submission(5, URL)
    assert not got.ok and "second submission" in got.detail and db.updates == 0
    assert not (await whop.record_submission_dry(5, URL)).ok
    # Submitted by the agent, then a different URL: refused, not changed.
    db = FakeRow(status="submitted", submitted_by="mac-agent")
    monkeypatch.setattr(whop, "fetch_one", db.fetch_one)
    got = await whop.record_submission(5, "https://www.instagram.com/reel/Other/")
    assert not got.ok and db.updates == 0 and db.row["post_url"] == URL


@pytest.mark.parametrize(("status_", "url", "source", "why"), [
    ("posted", URL, "instagram", None),
    ("posted", URL, "record_link", None),
    ("posted", "https://www.instagram.com/reel/Other/", "instagram",
     "not clip 5's own"),
    ("posted", "", "instagram", "not clip 5's own"),
    ("made", URL, "instagram", "not posted"),
    ("withdrawn", URL, "youtube", "not posted"),
    ("posted", URL, None, "did not come from the platform or from Miro"),
    ("posted", URL, "model", "did not come from the platform or from Miro"),
])
async def test_only_the_clips_own_trusted_url_is_recorded(monkeypatch, cod_on_file,
                                                          status_, url, source, why):
    db = FakeRow(status=status_, source=source)
    monkeypatch.setattr(whop, "fetch_one", db.fetch_one)
    got = await whop.record_submission(5, url)
    if why is None:
        assert got.ok, got.detail
    else:
        assert not got.ok and why in got.detail and db.updates == 0


@pytest.mark.parametrize("campaign", [
    _campaign(COD, whop_url=None),
    _campaign(COD, whop_url="  "),
    _campaign(COD, whop_url=WHOP, permission_source=None),
    _campaign(COD, whop_url=WHOP, permission_source="trust me"),
])
async def test_a_campaign_without_permission_and_whop_url_is_not_recorded(
        monkeypatch, campaign):
    _campaigns(monkeypatch, campaign)
    db = FakeRow()
    monkeypatch.setattr(whop, "fetch_one", db.fetch_one)
    got = await whop.record_submission(5, URL)
    assert not got.ok and "not offered for Whop" in got.detail and db.updates == 0


async def test_the_rollback_touches_only_the_agents_own_record(monkeypatch,
                                                               memory_backend):
    got = await whop.record_submission_rollback(json.dumps({"id": 5}))
    assert got.ok
    sql, args = memory_backend.sql_log[-1]
    assert "submitted_by = $2" in sql and args == (5, "mac-agent")


# ------------------------------------------------------------ the pending list

def _pending_rows(monkeypatch, rows, drafts=()):
    async def fetch_all(sql, *args):
        if "status IN ('made', 'draft_pending')" in sql:     # whop.drafts
            return list(drafts)
        if "status = 'draft_approved'" in sql:                # whop.approved
            return []
        assert "status = 'posted'" in sql and "post_url IS NOT NULL" in sql
        assert "post_url_source = ANY" in sql
        assert set(args[0]) == {"instagram", "youtube", "tiktok", "record_link"}
        return rows

    monkeypatch.setattr(whop, "fetch_all", fetch_all)


def _row(cid, campaign=COD, source="instagram", url=URL):
    return {"id": cid, "campaign": campaign, "platform": "instagram",
            "post_url": url, "post_url_source": source,
            "posted_at": datetime(2026, 9, 29, 15, tzinfo=UTC)}


async def test_pending_offers_only_trusted_urls_of_campaigns_on_file(monkeypatch):
    _campaigns(monkeypatch, _campaign(COD, whop_url=WHOP),
               _campaign(ATLUS, whop_url=None))
    _pending_rows(monkeypatch, [
        _row(1), _row(2, source="record_link"),
        _row(3, source=None),                # never recorded where it came from
        _row(4, source="model"),             # not a trusted source
        _row(5, campaign=ATLUS),             # no whop_url on file
        _row(6, campaign="gone"),            # campaign not on file
    ])
    got = await whop.pending()
    assert [c["clip_id"] for c in got] == [1, 2]
    assert got[0] == {"clip_id": 1, "campaign": COD,
                      "campaign_name": _campaign(COD).name, "whop_url": WHOP,
                      "platform": "instagram", "post_url": URL,
                      "posted_at": "2026-09-29T15:00:00+00:00"}


async def test_pending_needs_a_permission_source(monkeypatch):
    _campaigns(monkeypatch, _campaign(COD, whop_url=WHOP, permission_source=None))
    _pending_rows(monkeypatch, [_row(1)])
    assert await whop.pending() == []


async def test_a_campaign_offers_nothing_until_a_whop_url_is_given(monkeypatch):
    _campaigns(monkeypatch, *(dataclasses.replace(c, whop_url=None)
                              for c in campaigns.seeds()))
    _pending_rows(monkeypatch, [_row(1), _row(2, campaign=ATLUS)])
    assert await whop.pending() == []


def test_the_live_campaigns_carry_their_own_whop_page():
    """Copied from each campaign's page on 2026-09-29; Irubyana's is the
    Whop campaign Miro joined on 2026-10-01."""
    links = {c.slug: c.whop_url for c in campaigns.seeds()}
    for slug in (COD, ATLUS):
        assert (links[slug] or "").startswith("https://contentrewards.com/discover/")
    assert links["irubyana-twitch"] == (
        "https://whop.com/rubyanaclips/exp_qOpYZgW2DMo0F9/app/")


async def test_pending_hands_the_submitter_the_whop_url_on_the_row(monkeypatch):
    """What the Mac agent files against is the campaign row's whop_url --
    the one on file (Miro set Irubyana's by hand), whatever the seed says
    -- one entry per posted leg."""
    slug = "irubyana-twitch"
    on_file = "https://whop.com/rubyanaclips/exp_qOpYZgW2DMo0F9/app/"
    _campaigns(monkeypatch, _campaign(slug, whop_url=on_file))
    rows = [_row(21, campaign=slug, source="tiktok",
                 url="https://www.tiktok.com/@atlasclips12/video/7400000000001"),
            _row(22, campaign=slug)]
    rows[0]["platform"] = "tiktok"
    _pending_rows(monkeypatch, rows)
    got = await whop.pending()
    assert [(c["clip_id"], c["platform"], c["whop_url"]) for c in got] == [
        (21, "tiktok", on_file), (22, "instagram", on_file)]
    assert got[0]["campaign_name"] == "Irubyana Twitch Clips"
    # And a row whose whop_url was changed by hand hands that one over.
    _campaigns(monkeypatch, _campaign(slug, whop_url="https://whop.com/other/app/"))
    got = await whop.pending()
    assert {c["whop_url"] for c in got} == {"https://whop.com/other/app/"}


# ------------------------------------------------ every post_url writer names its source

def test_every_post_url_writer_names_its_source():
    """A post_url is set in posting.py only beside post_url_source."""
    text = pathlib.Path(posting.__file__).read_text()
    for sql in re.findall(r'"""(UPDATE clip_outputs.*?)"""', text, re.DOTALL):
        if re.search(r"post_url\s*=", sql):
            assert "post_url_source" in sql, sql


# ------------------------------------------------------------ the endpoints

@pytest.fixture
def api(monkeypatch):
    from atlas.api import clips as clips_api
    monkeypatch.setenv("HANDOFF_TOKEN", TOKEN)
    app = FastAPI()
    app.include_router(clips_api.router)
    return TestClient(app)


def _auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def test_both_endpoints_need_the_token(api, monkeypatch):
    for headers in ({}, _auth("x" * 32), {"Authorization": TOKEN}):
        assert api.get("/clips/whop/pending", headers=headers).status_code == 401
        assert api.post("/clips/whop/5/submitted", headers=headers,
                        json={"post_url": URL}).status_code == 401
    monkeypatch.setenv("HANDOFF_TOKEN", "short")        # a weak token admits nobody
    assert api.get("/clips/whop/pending", headers=_auth("short")).status_code == 401


def test_the_pending_endpoint_says_when_it_read(api, monkeypatch):
    _campaigns(monkeypatch, _campaign(COD, whop_url=WHOP))
    _pending_rows(monkeypatch, [_row(1)])
    r = api.get("/clips/whop/pending", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert [c["clip_id"] for c in body["clips"]] == [1]
    assert datetime.fromisoformat(body["read_at"]).tzinfo is not None


def _registered(monkeypatch, *names):
    actions = {a.name: a for a in clips.MODULE.actions()}
    for name in names:
        monkeypatch.setitem(REGISTRY.actions, name, actions[name])
        monkeypatch.setitem(REGISTRY.decisions, name, classify(actions[name]))
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    executor._recent.clear()

    async def audit(*_a, **_k):
        return None

    async def not_paused(module, account=None):
        return None

    monkeypatch.setattr(executor, "audit", audit)
    monkeypatch.setattr("atlas.kernel.unattended.audit", audit)
    monkeypatch.setattr("atlas.kernel.pause.active_for", not_paused)


def test_submitted_goes_through_the_unattended_door_to_the_executor(
        api, monkeypatch, cod_on_file):
    _registered(monkeypatch, "clips.record_whop_submission")
    db = FakeRow()
    monkeypatch.setattr(whop, "fetch_one", db.fetch_one)
    seen = []
    real = executor.execute

    async def spy(req):
        seen.append(req)
        return await real(req)

    monkeypatch.setattr(executor, "execute", spy)
    r = api.post("/clips/whop/5/submitted", headers=_auth(), json={"post_url": URL})
    assert r.status_code == 200, r.text
    assert r.json()["already"] is False
    req, = seen
    assert req.action == "clips.record_whop_submission"
    assert req.params == {"clip_id": 5, "post_url": URL}
    assert req.approved_by is None and db.updates == 1
    # The identical repeat: 200, already, nothing re-recorded.
    r = api.post("/clips/whop/5/submitted", headers=_auth(), json={"post_url": URL})
    assert r.status_code == 200 and r.json()["already"] is True and db.updates == 1
    # A URL that is not the clip's own: refused by the dry run, no write.
    r = api.post("/clips/whop/5/submitted", headers=_auth(),
                 json={"post_url": "https://www.instagram.com/reel/Nope/"})
    assert r.status_code == 409 and "dry run refused" in r.json()["detail"]
    assert db.updates == 1
    assert api.post("/clips/whop/5/submitted", headers=_auth(),
                    json={}).status_code == 400


def test_a_second_submission_is_409(api, monkeypatch, cod_on_file):
    _registered(monkeypatch, "clips.record_whop_submission")
    db = FakeRow(status="submitted", submitted_by="miro")
    monkeypatch.setattr(whop, "fetch_one", db.fetch_one)
    r = api.post("/clips/whop/5/submitted", headers=_auth(), json={"post_url": URL})
    assert r.status_code == 409 and "second submission" in r.json()["detail"]
    assert db.updates == 0


def test_watch_only_refuses_the_record(api, monkeypatch, cod_on_file):
    _registered(monkeypatch, "clips.record_whop_submission")
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    db = FakeRow()
    monkeypatch.setattr(whop, "fetch_one", db.fetch_one)
    r = api.post("/clips/whop/5/submitted", headers=_auth(), json={"post_url": URL})
    assert r.status_code == 409 and "watch-only" in r.json()["detail"]
    assert db.updates == 0


def _main_app(monkeypatch, cidrs):
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", cidrs)
    monkeypatch.setenv("HANDOFF_TOKEN", TOKEN)
    from atlas.api.main import app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    return app


def test_the_whop_paths_are_mesh_only(monkeypatch, memory_backend):
    from atlas.api import main
    assert not any("/clips".startswith(p) or p.startswith("/clips")
                   for p in main.PUBLIC_PATHS)
    app = _main_app(monkeypatch, "100.64.0.0/10")
    with TestClient(app, client=("203.0.113.9", 4000)) as outsider:
        # Even with the right token: an address off the mesh is not let in.
        assert outsider.get("/clips/whop/pending",
                            headers=_auth()).status_code == 404
        assert outsider.post("/clips/whop/5/submitted", headers=_auth(),
                             json={"post_url": URL}).status_code == 404


def test_the_whop_paths_are_mounted_behind_the_token(monkeypatch, memory_backend):
    _campaigns(monkeypatch, _campaign(COD, whop_url=WHOP))
    _pending_rows(monkeypatch, [_row(1)])
    app = _main_app(monkeypatch, "100.64.0.0/10")
    with TestClient(app, client=("100.64.0.7", 4000)) as mesh:
        assert mesh.get("/clips/whop/pending").status_code == 401
        r = mesh.get("/clips/whop/pending", headers=_auth())
        assert r.status_code == 200 and [c["clip_id"] for c in r.json()["clips"]] == [1]


# ------------------------------------------------------------ the fallback card

def _posted(cid, hours, source="instagram", status="posted", campaign=ATLUS):
    return {"id": cid, "campaign": campaign, "filename": f"{cid}.mp4",
            "platform": "instagram", "caption": "c", "status": status,
            "post_ref": str(cid), "post_url_source": source,
            "post_url": f"https://www.instagram.com/reel/R{cid}/",
            "posted_at": datetime.now(UTC) - timedelta(hours=hours)}


async def _whop_cards(monkeypatch, campaign, rows) -> list[int]:
    for name in ("INSTAGRAM_CLIPS_ACCOUNT_ID", "INSTAGRAM_CLIPS_TOKEN"):
        monkeypatch.delenv(name, raising=False)

    async def load_all():
        return [campaign]

    async def fetch_all(sql, *args):
        assert "posted_at" in sql and "post_url_source" in sql
        return rows

    monkeypatch.setattr(campaigns, "load_all", load_all)
    monkeypatch.setattr(posting, "fetch_all", fetch_all)
    return [s.params["clip_id"] for s in await posting.signals()
            if s.suggests == "clips.submit_to_whop"]


async def test_a_clip_the_agent_is_offered_waits_48_hours_for_the_card(
        monkeypatch, memory_backend):
    offered = _campaign(ATLUS, whop_url="https://whop.com/clipping-culture/atlus")
    got = await _whop_cards(monkeypatch, offered, [
        _posted(3, 5), _posted(4, 49), _posted(5, 72, source="record_link"),
        _posted(6, 72, status="submitted")])
    assert got == [4, 5]


@pytest.mark.parametrize(("campaign", "source"), [
    (_campaign(ATLUS, whop_url=None), "instagram"),              # no whop_url
    (_campaign(ATLUS, whop_url="https://whop.com/x",
               permission_source=None), "instagram"),            # no permission
    (_campaign(ATLUS, whop_url="https://whop.com/x",
               permission_source="trust me"), "instagram"),      # not a valid one
    (_campaign(ATLUS, whop_url="https://whop.com/x"), None),     # URL source unknown
    (_campaign(ATLUS, whop_url="https://whop.com/x"), "model"),  # untrusted source
])
async def test_a_clip_the_agent_never_sees_gets_the_card_at_once(
        monkeypatch, memory_backend, campaign, source):
    got = await _whop_cards(monkeypatch, campaign, [
        _posted(3, 1, source=source), _posted(4, 72, status="submitted")])
    assert got == [3]


def test_card_due_follows_the_pending_rule():
    now = datetime(2026, 9, 29, 12, tzinfo=UTC)
    on_file = _campaign(COD, whop_url=WHOP)
    fresh = {"post_url": URL, "post_url_source": "youtube",
             "posted_at": now - timedelta(hours=1)}
    assert whop.offered(on_file, fresh) and not whop.card_due(on_file, fresh, now)
    assert whop.card_due(on_file, {**fresh, "posted_at": now - timedelta(hours=48)},
                         now)
    assert whop.card_due(_campaign(COD, whop_url=None), fresh, now)  # no whop_url
    assert whop.card_due(None, fresh, now)                      # no campaign
    assert whop.card_due(on_file, {**fresh, "post_url_source": None}, now)


def test_fallback_due_is_48_hours():
    now = datetime(2026, 9, 29, 12, tzinfo=UTC)
    assert not whop.fallback_due(now - timedelta(hours=47, minutes=59), now)
    assert whop.fallback_due(now - timedelta(hours=48), now)
    assert whop.fallback_due(None, now)
    naive = (now - timedelta(hours=50)).replace(tzinfo=None)
    assert whop.fallback_due(naive, now)


async def test_miros_card_records_who_submitted(monkeypatch, memory_backend):
    async def fetch_all(sql, *args):
        return [{"id": 1, "campaign": COD, "filename": "a.mp4",
                 "platform": "instagram", "caption": "c", "status": "posted",
                 "post_url": URL}]

    monkeypatch.setattr(posting, "fetch_all", fetch_all)
    got = await posting.submit_to_whop(1, URL)
    assert got.ok
    sql = next(s for s, _ in memory_backend.sql_log if "'submitted'" in s)
    assert "submitted_by = 'miro'" in sql
    await posting.submit_to_whop_rollback(got.rollback_token)
    sql, _ = memory_backend.sql_log[-1]
    # Miro's rollback never undoes the agent's record.
    assert "IS DISTINCT FROM 'mac-agent'" in sql


# ------------------------------------------------------------------ counts

async def test_the_counts_check_reports_counts_only(monkeypatch):
    async def fetch_all(sql, *args):
        return [{"campaign": COD, "status": "posted", "n": 2},
                {"campaign": COD, "status": "submitted", "n": 1},
                {"campaign": ATLUS, "status": "submitted", "n": 3}]

    monkeypatch.setattr(whop, "fetch_all", fetch_all)
    obs = await whop.check_earnings()
    assert obs.health is Health.OK
    ev = obs.evidence
    assert (ev["posted"], ev["submitted"], ev["awaiting"]) == (6, 4, 2)
    assert datetime.fromisoformat(ev["read_at"]).tzinfo is not None
    # No money field of any kind, and no dollar figure in the words.
    assert set(ev) == {"read_at", "posted", "submitted", "awaiting", "per_campaign"}
    assert not any("usd" in k or "earn" in k or "paid" in k for k in ev)
    assert "$" not in obs.detail and "read " in obs.detail
    assert "6 clip(s) posted, 4 submitted to Whop, 2 awaiting" in obs.detail


async def test_the_counts_check_is_unknown_when_it_cannot_read(monkeypatch):
    async def down(sql, *args):
        raise ConnectionError("db down")

    monkeypatch.setattr(whop, "fetch_all", down)
    obs = await whop.check_earnings()
    assert obs.health is Health.UNKNOWN and obs.evidence.get("posted") is None


async def test_clips_posted_is_its_own_number_read_fresh(monkeypatch):
    """The home page shows a check's value, never a count lifted out of a
    sentence: posted is its own check, read from clip_outputs on each run."""
    asked: list[str] = []

    async def fetch_one(sql, *args):
        asked.append(" ".join(sql.split()))
        return {"n": 6}

    monkeypatch.setattr(whop, "fetch_one", fetch_one)
    obs = await whop.check_posted()
    assert obs.check == "clips_posted"
    assert obs.health is Health.OK and obs.value == 6.0
    assert obs.unit == "clips posted, all time"
    assert "status IN ('posted', 'submitted')" in asked[0]

    async def down(sql, *args):
        raise ConnectionError("db down")

    monkeypatch.setattr(whop, "fetch_one", down)
    obs = await whop.check_posted()
    assert obs.health is Health.UNKNOWN and obs.value is None


# ------------------------------------------------------------------ drafts
#
# Irubyana's campaign on Whop (verified 2026-10-01): a draft for the
# brand's approval before posting. The Mac agent reads the drafts with a
# signed download link, uploads each in Miro's Chrome, and records the
# upload and the brand's decision through the same door.

IRUBYANA = "irubyana-twitch"
IRU_WHOP = "https://whop.com/rubyanaclips/exp_qOpYZgW2DMo0F9/app/"


@pytest.fixture
def irubyana_on_file(monkeypatch, tmp_path):
    monkeypatch.setenv("CLIPS_DATA_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("ATLAS_HOST", "atlas.example.test")
    campaign = _campaign(IRUBYANA, whop_url=IRU_WHOP)
    assert campaign.approval_before_post and campaign.max_file_mb == 256
    _campaigns(monkeypatch, campaign, _campaign(COD, whop_url=WHOP))
    return tmp_path / "clips"


def _draft(cid, status="draft_pending", campaign=IRUBYANA, platform="tiktok",
           token=None, expires=None, filename=None):
    return {"id": cid, "campaign": campaign, "platform": platform,
            "filename": filename or f"{cid}-abc-1.mp4", "status": status,
            "media_token": token, "media_token_expires_at": expires}


def _file(root, cid, campaign=IRUBYANA, size=1024):
    out = root / campaign / "out"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{cid}-abc-1.mp4").write_bytes(b"\0" * size)


def _mints(monkeypatch):
    minted: list[tuple] = []

    async def execute_sql(sql, *args):
        assert "SET media_token = $2, media_token_expires_at = $3" in sql
        minted.append(args)

    monkeypatch.setattr(whop, "execute_sql", execute_sql)
    return minted


async def test_drafts_lists_the_approval_campaigns_clips_with_a_signed_link(
        monkeypatch, irubyana_on_file):
    root = irubyana_on_file
    for cid in (21, 22, 23):
        _file(root, cid)
    live = datetime.now(UTC) + timedelta(days=3)
    _pending_rows(monkeypatch, [], drafts=[
        _draft(21),                                        # a fresh draft
        _draft(22, status="made", platform="instagram"),   # cut before the rule
        _draft(23, token="clip.live", expires=live),       # a link still live
        _draft(24),                                        # no file on disk
        _draft(25, campaign=COD, status="made"),           # no approval needed
    ])
    minted = _mints(monkeypatch)
    got = await whop.drafts()
    assert [d["clip_id"] for d in got] == [21, 22, 23, 24]
    first = got[0]
    assert first["campaign"] == IRUBYANA and first["whop_url"] == IRU_WHOP
    assert first["campaign_name"] == "Irubyana Twitch Clips"
    assert first["platform"] == "tiktok" and first["filename"] == "21-abc-1.mp4"
    assert first["bytes"] == 1024 and first["max_mb"] == 256 and first["why"] is None
    assert first["media_url"].startswith("https://atlas.example.test/media/clip.")
    lapses = datetime.fromisoformat(first["link_expires_at"])
    assert timedelta(days=6, hours=23) < lapses - datetime.now(UTC) <= timedelta(days=7)
    # Minted on ATLAS's own row for 21 and 22; 23's live token is reused.
    assert [m[0] for m in minted] == [21, 22]
    assert all(m[1].startswith("clip.") and m[1] == got[i]["media_url"].rsplit(
        "/", 1)[1] for i, m in enumerate(minted))
    assert got[2]["media_url"] == "https://atlas.example.test/media/clip.live"
    assert got[2]["link_expires_at"] == live.isoformat()
    # No file: said, never linked.
    assert got[3]["media_url"] is None and "no longer in" in got[3]["why"]


async def test_a_draft_over_the_file_cap_is_never_handed_to_whop(monkeypatch,
                                                                  irubyana_on_file):
    root = irubyana_on_file
    _campaigns(monkeypatch, _campaign(IRUBYANA, whop_url=IRU_WHOP, max_file_mb=1))
    _file(root, 21, size=2 * 1024 * 1024 + 1)
    _pending_rows(monkeypatch, [], drafts=[_draft(21)])
    minted = _mints(monkeypatch)
    got, = await whop.drafts()
    assert got["media_url"] is None and got["bytes"] == 2 * 1024 * 1024 + 1
    assert "2 MB is over" in got["why"] and "1 MB draft limit" in got["why"]
    assert minted == []


async def test_a_lapsing_link_is_minted_again(monkeypatch, irubyana_on_file):
    _file(irubyana_on_file, 21)
    soon = datetime.now(UTC) + timedelta(minutes=10)
    _pending_rows(monkeypatch, [], drafts=[_draft(21, token="clip.old", expires=soon)])
    minted = _mints(monkeypatch)
    got, = await whop.drafts()
    assert len(minted) == 1 and minted[0][1] != "clip.old"
    assert got["media_url"].endswith(minted[0][1])


async def test_drafts_need_a_whop_url_and_permission_like_the_posts(monkeypatch,
                                                                     irubyana_on_file):
    _file(irubyana_on_file, 21)
    for campaign in (_campaign(IRUBYANA, whop_url=None),
                     _campaign(IRUBYANA, whop_url=IRU_WHOP, permission_source=None)):
        _campaigns(monkeypatch, campaign)
        _pending_rows(monkeypatch, [], drafts=[_draft(21)])
        assert await whop.drafts() == []


async def test_the_pending_endpoint_carries_the_drafts(api, monkeypatch,
                                                       irubyana_on_file):
    _file(irubyana_on_file, 21)
    _pending_rows(monkeypatch, [_row(1)], drafts=[_draft(21)])
    _mints(monkeypatch)
    r = api.get("/clips/whop/pending", headers=_auth())
    assert r.status_code == 200, r.text
    body = r.json()
    assert [c["clip_id"] for c in body["clips"]] == [1]
    assert [d["clip_id"] for d in body["drafts"]] == [21]
    assert body["drafts"][0]["whop_url"] == IRU_WHOP


class FakeDraftRow:
    """One clip_outputs row of an approval campaign, and every write."""

    def __init__(self, status="draft_pending", campaign=IRUBYANA, decided=False,
                 note=None):
        self.row = {"id": 5, "campaign": campaign, "status": status,
                    "draft_submitted_at": (datetime.now(UTC)
                                           if status != "draft_pending" else None),
                    "draft_decided_at": datetime.now(UTC) if decided else None,
                    "draft_note": note}
        self.writes: list[tuple[str, tuple]] = []

    async def fetch_one(self, sql, *args):
        if sql.lstrip().startswith("UPDATE"):
            self.writes.append((" ".join(sql.split()), args))
            if "draft_submitted_at = now()" in sql:
                if self.row["status"] not in ("made", "draft_pending"):
                    return None
                self.row.update(status="draft_submitted",
                                draft_submitted_at=datetime.now(UTC))
                return {"id": 5}
            if self.row["status"] not in ("draft_pending", "draft_submitted"):
                return None
            self.row.update(status=args[1], draft_decided_at=datetime.now(UTC),
                            draft_note=args[2])
            return {"id": 5}
        return dict(self.row) if args[0] == 5 else None

    async def execute_sql(self, sql, *args):
        self.writes.append((" ".join(sql.split()), args))


def _said(monkeypatch):
    said: list = []

    async def notify(msg):
        said.append(msg)
        return ["slack"]

    monkeypatch.setattr("atlas.kernel.comms.notify", notify)
    return said


@pytest.mark.parametrize("status", ["draft_pending", "made"])
async def test_an_uploaded_draft_is_recorded_once(monkeypatch, irubyana_on_file,
                                                  status):
    db = FakeDraftRow(status=status)
    monkeypatch.setattr(whop, "fetch_one", db.fetch_one)
    dry = await whop.record_draft_submitted_dry(5)
    assert dry.ok and "would record clip 5's draft as uploaded" in dry.detail
    assert db.writes == []
    got = await whop.record_draft_submitted(5)
    assert got.ok and got.evidence == {"already": False}, got.detail
    assert db.row["status"] == "draft_submitted"
    (sql, args), = db.writes
    assert "status IN ('made', 'draft_pending')" in sql and args == (5,)
    # The identical repeat: ok, already, nothing written.
    again = await whop.record_draft_submitted(5)
    assert again.ok and again.evidence == {"already": True} and len(db.writes) == 1
    assert (await whop.record_draft_submitted_dry(5)).ok


@pytest.mark.parametrize(("row", "why"), [
    (FakeDraftRow(campaign=COD), "asks for no draft approval"),
    (FakeDraftRow(status="draft_approved", decided=True), "not a draft waiting"),
    (FakeDraftRow(status="posted"), "not a draft waiting"),
    (FakeDraftRow(status="withdrawn", decided=True), "not a draft waiting"),
])
async def test_only_a_waiting_draft_of_an_approval_campaign_is_recorded_uploaded(
        monkeypatch, irubyana_on_file, row, why):
    monkeypatch.setattr(whop, "fetch_one", row.fetch_one)
    for fn in (whop.record_draft_submitted_dry, whop.record_draft_submitted):
        got = await fn(5)
        assert not got.ok and why in got.detail
    assert row.writes == []
    assert not (await whop.record_draft_submitted(99)).ok


async def test_the_upload_record_rolls_back_to_pending(monkeypatch, irubyana_on_file):
    db = FakeDraftRow()
    monkeypatch.setattr(whop, "fetch_one", db.fetch_one)
    monkeypatch.setattr(whop, "execute_sql", db.execute_sql)
    got = await whop.record_draft_submitted(5)
    back = await whop.record_draft_submitted_rollback(got.rollback_token)
    assert back.ok and "ATLAS's record only" in back.detail
    sql, args = db.writes[-1]
    assert "SET status = 'draft_pending'" in sql and "status = 'draft_submitted'" in sql
    assert args == (5,)


@pytest.mark.parametrize("status", ["draft_submitted", "draft_pending"])
async def test_an_approved_draft_posts_and_a_rejected_one_is_withdrawn(
        monkeypatch, irubyana_on_file, status):
    db = FakeDraftRow(status=status)
    monkeypatch.setattr(whop, "fetch_one", db.fetch_one)
    said = _said(monkeypatch)
    dry = await whop.record_draft_decision_dry(5, True)
    assert dry.ok and "it then posts on its own" in dry.detail and db.writes == []
    got = await whop.record_draft_decision(5, True, "  looks great ")
    assert got.ok and db.row["status"] == "draft_approved"
    assert db.row["draft_note"] == "looks great" and got.evidence["note"] == "looks great"
    assert "posts on its own" in got.detail
    (sql, args), = db.writes
    assert "status IN ('draft_pending', 'draft_submitted')" in sql
    assert args == (5, "draft_approved", "looks great")
    receipt, = said
    assert receipt.topic == "clips" and receipt.title == "Whop approved the draft of clip 5"
    assert "looks great" in receipt.body
    # The same decision again: already; the opposite: refused.
    again = await whop.record_draft_decision(5, True)
    assert again.ok and again.evidence == {"already": True} and len(db.writes) == 1
    no = await whop.record_draft_decision(5, False, "changed my mind")
    assert not no.ok and "opposite decision is refused" in no.detail

    db = FakeDraftRow(status=status)
    monkeypatch.setattr(whop, "fetch_one", db.fetch_one)
    said.clear()
    got = await whop.record_draft_decision(5, False, "no logo visible")
    assert got.ok and db.row["status"] == "withdrawn"
    assert "withdrawn, never posted" in got.detail and "no logo visible" in got.detail
    receipt, = said
    assert receipt.title == "Whop rejected the draft of clip 5"
    assert "no logo visible" in receipt.body and "will not be posted" in receipt.body
    assert receipt.severity == Severity.NORMAL
    again = await whop.record_draft_decision(5, False)
    assert again.ok and again.evidence == {"already": True}


async def test_a_decision_needs_a_real_boolean_and_an_approval_campaign(
        monkeypatch, irubyana_on_file):
    db = FakeDraftRow(status="draft_submitted")
    monkeypatch.setattr(whop, "fetch_one", db.fetch_one)
    for bad in ("true", 1, None):
        got = await whop.record_draft_decision(5, bad)
        assert not got.ok and "true or false" in got.detail
    cod = FakeDraftRow(status="draft_submitted", campaign=COD)
    monkeypatch.setattr(whop, "fetch_one", cod.fetch_one)
    got = await whop.record_draft_decision(5, True)
    assert not got.ok and "asks for no draft approval" in got.detail
    posted = FakeDraftRow(status="posted")
    monkeypatch.setattr(whop, "fetch_one", posted.fetch_one)
    got = await whop.record_draft_decision(5, True)
    assert not got.ok and "not a draft awaiting a decision" in got.detail
    assert db.writes == cod.writes == posted.writes == []


async def test_a_decision_rolls_back_to_submitted(monkeypatch, irubyana_on_file):
    for approved, status in ((True, "draft_approved"), (False, "withdrawn")):
        db = FakeDraftRow(status="draft_submitted")
        monkeypatch.setattr(whop, "fetch_one", db.fetch_one)
        monkeypatch.setattr(whop, "execute_sql", db.execute_sql)
        _said(monkeypatch)
        got = await whop.record_draft_decision(5, approved, "n")
        assert got.ok
        back = await whop.record_draft_decision_rollback(got.rollback_token)
        assert back.ok
        sql, args = db.writes[-1]
        assert "SET status = 'draft_submitted'" in sql and args == (5, status)


def test_draft_routes_go_through_the_unattended_door(api, monkeypatch,
                                                     irubyana_on_file):
    _registered(monkeypatch, "clips.record_draft_submitted",
                "clips.record_draft_decision")
    db = FakeDraftRow()
    monkeypatch.setattr(whop, "fetch_one", db.fetch_one)
    said = _said(monkeypatch)
    seen = []
    real = executor.execute

    async def spy(req):
        seen.append(req)
        return await real(req)

    monkeypatch.setattr(executor, "execute", spy)
    # No token, a wrong token: nothing runs.
    for headers in ({}, _auth("x" * 32)):
        assert api.post("/clips/whop/5/draft_submitted", headers=headers).status_code == 401
        assert api.post("/clips/whop/5/draft_decided", headers=headers,
                        json={"approved": True}).status_code == 401
    assert seen == []
    r = api.post("/clips/whop/5/draft_submitted", headers=_auth())
    assert r.status_code == 200, r.text
    assert r.json()["already"] is False and db.row["status"] == "draft_submitted"
    req = seen[-1]
    assert req.action == "clips.record_draft_submitted"
    assert req.params == {"clip_id": 5} and req.approved_by is None
    # The decision: the body must say true or false.
    for body in ({}, {"approved": "yes"}, {"approved": 1}, [True]):
        r = api.post("/clips/whop/5/draft_decided", headers=_auth(), json=body)
        assert r.status_code == 400, body
    r = api.post("/clips/whop/5/draft_decided", headers=_auth(),
                 json={"approved": False, "note": "  no  banner "})
    assert r.status_code == 200, r.text
    req = seen[-1]
    assert req.action == "clips.record_draft_decision"
    assert req.params == {"clip_id": 5, "approved": False, "note": "no banner"}
    assert db.row["status"] == "withdrawn" and db.row["draft_note"] == "no banner"
    assert said[-1].title == "Whop rejected the draft of clip 5"
    # Decided already: the opposite is a 409 from the dry run, no write.
    r = api.post("/clips/whop/5/draft_decided", headers=_auth(), json={"approved": True})
    assert r.status_code == 409 and "dry run refused" in r.json()["detail"]
    assert len(db.writes) == 2


# ------------------------------------------------- "never paused" (Irubyana)

@pytest.mark.parametrize("status, resumed", [
    ("paused", ["irubyana-twitch"]), ("active", []), ("ended", [])])
async def test_a_pause_is_lifted_off_irubyana_and_an_end_is_left(
        monkeypatch, status, resumed):
    """Miro, 2026-10-01 and 2026-10-05: her clips are "never paused"."""
    _campaigns(monkeypatch, _campaign(IRUBYANA, status=status))
    ran: list[tuple] = []

    async def execute_sql(stmt, *args):
        ran.append((stmt, args))

    monkeypatch.setattr(campaigns, "execute_sql", execute_sql)
    assert await campaigns.keep_active() == resumed
    assert len(ran) == len(resumed)
    if ran:
        assert "status = 'active'" in ran[0][0] and "status = 'paused'" in ran[0][0]


async def test_a_paused_campaign_that_may_not_be_active_stays_paused(monkeypatch):
    _campaigns(monkeypatch, _campaign(IRUBYANA, status="paused"))
    monkeypatch.setattr(campaigns, "activation_refusal", lambda c: "rules pending")

    async def execute_sql(stmt, *args):
        raise AssertionError("nothing is written")

    monkeypatch.setattr(campaigns, "execute_sql", execute_sql)
    assert await campaigns.keep_active() == []


def test_only_irubyana_is_kept_active():
    assert campaigns.KEEP_ACTIVE == ("irubyana-twitch",)


# --------------------------------------------- post now (the 30-minute window)

async def test_approved_lists_the_drafts_the_brand_approved_with_their_length(monkeypatch):
    from datetime import UTC, datetime

    async def fetch_all(sql, *a):
        assert "draft_approved" in sql
        return [{"id": 4, "campaign": IRUBYANA, "platform": "tiktok",
                 "caption": "Best moments from @irubyana", "start_s": 10.0, "end_s": 30.4,
                 "draft_decided_at": datetime(2026, 10, 6, 20, 36, tzinfo=UTC)}]

    monkeypatch.setattr(whop, "fetch_all", fetch_all)
    got = await whop.approved()
    assert got == [{"clip_id": 4, "campaign": IRUBYANA, "platform": "tiktok",
                    "seconds": 20, "caption": "Best moments from @irubyana",
                    "approved_at": "2026-10-06T20:36:00+00:00"}]


def test_post_now_posts_an_approved_draft_through_its_own_auto_action(api, monkeypatch):
    """Whop takes a live link only within 30 minutes of the post (read
    2026-10-06), so the Mac agent asks for the post and submits the link in
    the same run. The same Tier 1 action, every gate, no approver."""
    from atlas.api import clips as clips_api
    from atlas.kernel import unattended
    from atlas.kernel.contracts import ActionResult
    from atlas.modules.clips import posting

    async def clips_rows(ids):
        return {4: {"id": 4, "status": "draft_approved", "platform": "tiktok"}}

    ran = []

    async def run_tier1(action, params, reason):
        ran.append((action, params))
        return ActionResult(True, "clip 4 posted to @atlasclips12",
                            evidence={"post_url": None})

    monkeypatch.setattr(posting, "_clips", clips_rows)
    monkeypatch.setattr(posting, "auto_action", lambda row: "clips.publish_clip_tiktok")
    monkeypatch.setattr(unattended, "run_tier1", run_tier1)
    r = api.post("/clips/whop/4/post_now", headers=_auth())
    assert r.status_code == 200, r.text
    assert ran == [("clips.publish_clip_tiktok", {"clip_id": 4})]
    assert r.json()["ok"] is True and r.json()["post_url"] is None
    assert api.post("/clips/whop/4/post_now").status_code == 401
    assert clips_api


def test_post_now_refuses_anything_but_an_approved_draft(api, monkeypatch):
    from atlas.modules.clips import posting

    async def clips_rows(ids):
        return {6: {"id": 6, "status": "posted", "platform": "tiktok"}}

    monkeypatch.setattr(posting, "_clips", clips_rows)
    r = api.post("/clips/whop/6/post_now", headers=_auth())
    assert r.status_code == 409 and "not an approved draft" in r.json()["detail"]
    assert api.post("/clips/whop/99/post_now", headers=_auth()).status_code == 404
