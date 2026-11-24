"""Read-back verifiers for the modules that are off in prod (RA-D1):
youtube (upload, set_visibility, draft_metadata), tiktok.upload_draft,
screening.book_slot and exampleco.price_check.

Providers are faked at `httpx.AsyncClient.send`; the fake records any method
but GET and the fixture fails the test at teardown if one was sent (an
assertion inside the fake would be swallowed by the kernel's "a verifier
that raises is unknown"). DB reads are faked at `state.fetch_one`, SELECT
only. Every "off" case also asserts that no request left at all.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

import httpx
import pytest

from atlas.kernel import state
from atlas.kernel import verify as v
from atlas.kernel.contracts import ActionResult

SECRET = "sk-verify-rad1-supersecret-0123456789"


class Net:
    """Routes by a regex on the request path. Non-GET requests are recorded
    in `writes` and answered 405."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, tuple]] = []
        self.seen: list[str] = []
        self.writes: list[str] = []

    def on(self, pattern: str, status: int = 200, body=None, *, raises=None) -> None:
        self.routes.insert(0, (pattern, (status, body, raises)))

    async def send(self, client, request: httpx.Request, **_kw) -> httpx.Response:
        path = request.url.path
        if request.method != "GET":
            self.writes.append(f"{request.method} {path}")
            return httpx.Response(405, request=request)
        self.seen.append(path)
        for pattern, (status, body, raises) in self.routes:
            if re.search(pattern, path):
                if raises is not None:
                    raise raises
                return httpx.Response(status, json=body, request=request)
        return httpx.Response(404, json={"error": "no route"}, request=request)


@pytest.fixture
def net(monkeypatch):
    fake = Net()

    async def send(client, request, **kw):
        return await fake.send(client, request, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "send", send)
    yield fake
    assert fake.writes == [], f"a verifier wrote: {fake.writes}"


@pytest.fixture
def db(monkeypatch):
    rows: dict[str, dict | None] = {}

    async def fetch_one(sql, *args):
        assert sql.lstrip().upper().startswith("SELECT"), f"a verifier wrote: {sql}"
        return rows.get(re.search(r"FROM\s+(\w+)", sql).group(1))

    async def fetch_all(sql, *args):
        raise AssertionError("no verifier here reads more than one row")

    async def execute_sql(*_a, **_kw):
        raise AssertionError("a verifier wrote")

    monkeypatch.setattr(state, "fetch_one", fetch_one)
    monkeypatch.setattr(state, "fetch_all", fetch_all)
    monkeypatch.setattr(state, "execute_sql", execute_sql)
    return rows


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("ATLAS_DISABLED_MODULES", raising=False)
    monkeypatch.setenv("VERIFY_RAD1_API_KEY", SECRET)


def _no_secret(got: v.Verification) -> None:
    assert SECRET not in json.dumps(got.as_audit(), default=str)


ACTIONS = ["youtube.upload", "youtube.set_visibility", "youtube.draft_metadata",
           "tiktok.upload_draft", "screening.book_slot", "exampleco.price_check"]


@pytest.mark.parametrize("action", ACTIONS)
def test_each_action_has_a_verifier(action):
    assert v.has_verifier(action)


def test_only_set_visibility_has_a_reconciler():
    assert v.has_reconciler("youtube.set_visibility")
    for action in ACTIONS:
        if action != "youtube.set_visibility":
            assert not v.has_reconciler(action)


# ================================================================== youtube

@pytest.fixture
def yt(monkeypatch):
    """Switch a stand-in "flycheap" channel on, with a warm token cache."""
    from atlas.modules.youtube import api
    for suffix in ("CLIENT_ID", "CLIENT_SECRET", "CHANNEL_ID"):
        monkeypatch.setenv(f"YOUTUBE_FLYCHEAP_{suffix}", "x")
    monkeypatch.setenv("YOUTUBE_FLYCHEAP_REFRESH_TOKEN", SECRET)
    monkeypatch.setitem(api.CHANNELS, "flycheap", "YOUTUBE_FLYCHEAP_")
    monkeypatch.setitem(api._token_cache, "flycheap", (SECRET, time.time() + 600))
    return api


def _video(privacy="private", title="Cheap flights", upload="processed"):
    return {"items": [{"id": "vid1", "snippet": {"title": title},
                       "status": {"privacyStatus": privacy,
                                  "uploadStatus": upload}}]}


UP_PARAMS = {"channel": "flycheap", "path": "/tmp/x.mp4", "title": "Cheap flights",
             "description": "d", "tags": []}
UP_RESULT = ActionResult(True, "uploaded", evidence={"channel": "flycheap",
                                                     "video_id": "vid1"},
                         rollback_token="flycheap:vid1")


async def test_upload_verified(net, yt):
    net.on(r"/youtube/v3/videos$", body=_video())
    got = await v.verify("youtube.upload", UP_PARAMS, UP_RESULT)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["video_id"] == "vid1" and got.evidence["privacy"] == "private"
    _no_secret(got)


async def test_upload_contradicted(net, yt):
    net.on(r"/youtube/v3/videos$", body={"items": []})
    got = await v.verify("youtube.upload", UP_PARAMS, UP_RESULT)
    assert got.state is v.State.CONTRADICTED and "not on" in got.reason

    net.on(r"/youtube/v3/videos$", body=_video(privacy="public"))
    got = await v.verify("youtube.upload", UP_PARAMS, UP_RESULT)
    assert got.state is v.State.CONTRADICTED
    assert got.failure_class is v.FailureClass.POLICY

    net.on(r"/youtube/v3/videos$", body=_video(title="Something else"))
    got = await v.verify("youtube.upload", UP_PARAMS, UP_RESULT)
    assert got.state is v.State.CONTRADICTED and "title" in got.reason

    net.on(r"/youtube/v3/videos$", body=_video(upload="rejected"))
    got = await v.verify("youtube.upload", UP_PARAMS, UP_RESULT)
    assert got.state is v.State.CONTRADICTED
    _no_secret(got)


async def test_upload_unknown(net, yt, monkeypatch):
    got = await v.verify("youtube.upload", UP_PARAMS, ActionResult(True))
    assert got.state is v.State.UNKNOWN and "no video id" in got.reason

    net.on(r"/youtube/v3/videos$", 401, {"error": "bad token"})
    got = await v.verify("youtube.upload", UP_PARAMS, UP_RESULT)
    assert got.state is v.State.UNKNOWN and got.failure_class is v.FailureClass.AUTH
    _no_secret(got)

    # A cold token cache is unknown, never a refresh POST.
    monkeypatch.delitem(yt._token_cache, "flycheap")
    before = list(net.seen)
    got = await v.verify("youtube.upload", UP_PARAMS, UP_RESULT)
    assert got.state is v.State.UNKNOWN and "POST" in got.reason
    assert net.seen == before


@pytest.mark.parametrize("off", ["module", "creds"])
async def test_youtube_off_is_disabled_before_any_request(net, yt, monkeypatch, off,
                                                          db):
    if off == "module":
        monkeypatch.setenv("ATLAS_DISABLED_MODULES", "stripe, YouTube")
    else:
        monkeypatch.delenv("YOUTUBE_FLYCHEAP_REFRESH_TOKEN")
    got = await v.verify("youtube.upload", UP_PARAMS, UP_RESULT)
    assert got.state is v.State.UNKNOWN
    assert got.failure_class is v.FailureClass.DISABLED
    got = await v.verify("youtube.set_visibility", SV_PARAMS, SV_RESULT)
    assert got.failure_class is v.FailureClass.DISABLED
    if off == "module":
        got = await v.verify("youtube.draft_metadata", DM_PARAMS, DM_RESULT)
        assert got.failure_class is v.FailureClass.DISABLED
    assert net.seen == []
    _no_secret(got)


SV_PARAMS = {"channel": "flycheap", "video_id": "vid1", "privacy": "public"}
SV_RESULT = ActionResult(True, "now public",
                         evidence={"previous": "private", "channel": "flycheap",
                                   "video_id": "vid1"},
                         rollback_token="flycheap:vid1:private")


async def test_set_visibility_verified(net, yt):
    net.on(r"/youtube/v3/videos$", body=_video(privacy="public"))
    got = await v.verify("youtube.set_visibility", SV_PARAMS, SV_RESULT)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["privacy"] == "public" and got.evidence["approved"] == "public"
    _no_secret(got)


async def test_set_visibility_contradicted(net, yt):
    net.on(r"/youtube/v3/videos$", body=_video(privacy="private"))
    got = await v.verify("youtube.set_visibility", SV_PARAMS, SV_RESULT)
    assert got.state is v.State.CONTRADICTED and "approved public" in got.reason

    net.on(r"/youtube/v3/videos$", body={"items": []})
    got = await v.verify("youtube.set_visibility", SV_PARAMS, SV_RESULT)
    assert got.state is v.State.CONTRADICTED

    other = {**SV_PARAMS, "video_id": "vid2"}
    got = await v.verify("youtube.set_visibility", other, SV_RESULT)
    assert got.state is v.State.CONTRADICTED and "different video" in got.reason


async def test_set_visibility_unknown(net, yt):
    net.on(r"/youtube/v3/videos$", 503, {"error": "down"})
    got = await v.verify("youtube.set_visibility", SV_PARAMS, SV_RESULT)
    assert got.state is v.State.UNKNOWN
    got = await v.verify("youtube.set_visibility", {**SV_PARAMS, "privacy": "loud"},
                         SV_RESULT)
    assert got.state is v.State.UNKNOWN
    assert got.failure_class is v.FailureClass.VALIDATION
    _no_secret(got)


async def test_set_visibility_reconciler(net, yt):
    net.on(r"/youtube/v3/videos$", body=_video(privacy="public"))
    got = await v.reconcile_unknown("youtube.set_visibility", SV_PARAMS,
                                    {"at": "2026-09-28T10:00:00+00:00"})
    assert got.state is v.State.VERIFIED
    net.on(r"/youtube/v3/videos$", body=_video(privacy="unlisted"))
    got = await v.reconcile_unknown("youtube.set_visibility", SV_PARAMS,
                                    {"at": "2026-09-28T10:00:00+00:00"})
    assert got.state is v.State.CONTRADICTED


# ------------------------------------------------------------ draft_metadata

DM_BODY = json.dumps({"title": "A great video", "description": "D"})
DM_SHA = hashlib.sha256(DM_BODY.encode()).hexdigest()[:12]
DM_PARAMS = {"business": "flycheap", "source": "weekly"}
DM_RESULT = ActionResult(True, "drafted",
                         evidence={"id": 7, "title": "A great video", "sha256": DM_SHA},
                         rollback_token="7")


def _draft(**over):
    return {"kind": "youtube_video", "source": "weekly", "title": "A great video",
            "body": DM_BODY, "status": "draft", **over}


async def test_draft_metadata_verified(net, db):
    db["content_drafts"] = _draft()
    got = await v.verify("youtube.draft_metadata", DM_PARAMS, DM_RESULT)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence == {"draft_id": 7, "kind": "youtube_video", "status": "draft",
                            "title": "A great video", "sha256": DM_SHA}
    # A later lifecycle state is still the draft that was made.
    db["content_drafts"] = _draft(status="approved")
    got = await v.verify("youtube.draft_metadata", DM_PARAMS, DM_RESULT)
    assert got.state is v.State.VERIFIED
    assert net.seen == []
    _no_secret(got)


@pytest.mark.parametrize("row, words", [
    (None, "no content_drafts row"),
    (_draft(status="dropped"), "dropped"),
    (_draft(kind="story_script"), "not youtube_video"),
    (_draft(source="note:x"), "different source"),
    (_draft(title="Other"), "different title"),
    (_draft(body="{}"), "body differs"),
])
async def test_draft_metadata_contradicted(net, db, row, words):
    db["content_drafts"] = row
    got = await v.verify("youtube.draft_metadata", DM_PARAMS, DM_RESULT)
    assert got.state is v.State.CONTRADICTED and words in got.reason


async def test_draft_metadata_unknown(net, db, monkeypatch):
    got = await v.verify("youtube.draft_metadata", DM_PARAMS, ActionResult(True))
    assert got.state is v.State.UNKNOWN and "no draft id" in got.reason

    async def boom(*_a, **_kw):
        raise RuntimeError(f"db down {SECRET}")

    monkeypatch.setattr(state, "fetch_one", boom)
    got = await v.verify("youtube.draft_metadata", DM_PARAMS, DM_RESULT)
    assert got.state is v.State.UNKNOWN and "raised" in got.reason
    _no_secret(got)


async def test_draft_metadata_run_records_the_body_hash(monkeypatch):
    from atlas.modules import youtube

    async def fake_draft(business, source):
        return {"title": "A great video", "description": "D"}

    stored = {}

    async def fake_fetch_one(sql, *args):
        stored["body"] = args[2]
        return {"id": 7}

    monkeypatch.setattr(youtube, "_draft_metadata", fake_draft)
    monkeypatch.setattr(youtube, "fetch_one", fake_fetch_one)
    result = await youtube.draft_metadata("flycheap", "weekly")
    assert result.evidence["sha256"] == hashlib.sha256(
        stored["body"].encode()).hexdigest()[:12]


# ================================================================== tiktok

TT_RESULT = ActionResult(True, "in drafts", rollback_token="pub_123")


@pytest.fixture
def tt_on(monkeypatch):
    for name in ("TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET"):
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("TIKTOK_REFRESH_TOKEN", SECRET)


async def test_tiktok_unknown_by_design_when_on(net, tt_on):
    got = await v.verify("tiktok.upload_draft", {"video_url": "https://x/v.mp4"},
                         TT_RESULT)
    assert got.state is v.State.UNKNOWN and got.failure_class is None
    assert "POST" in got.reason and got.evidence == {"publish_id": "pub_123"}
    assert net.seen == []
    _no_secret(got)

    got = await v.verify("tiktok.upload_draft", {}, ActionResult(True))
    assert got.state is v.State.UNKNOWN and "no publish id" in got.reason


@pytest.mark.parametrize("off", ["module", "env"])
async def test_tiktok_off_is_disabled(net, tt_on, monkeypatch, off):
    if off == "module":
        monkeypatch.setenv("ATLAS_DISABLED_MODULES", "tiktok")
    else:
        monkeypatch.delenv("TIKTOK_REFRESH_TOKEN")
    got = await v.verify("tiktok.upload_draft", {}, TT_RESULT)
    assert got.state is v.State.UNKNOWN
    assert got.failure_class is v.FailureClass.DISABLED
    assert net.seen == []
    _no_secret(got)


# ================================================================== screening

EVENT_ID = "evt42"
BS_PARAMS = {"call_sid": "CA1", "start": "2026-09-29T14:00:00-04:00"}
BS_RESULT = ActionResult(True, "held", rollback_token=EVENT_ID)
CALLER = "+15550001111"


@pytest.fixture
def cal_on(monkeypatch):
    from atlas.modules import calendar
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "x")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", SECRET)

    async def tok():
        return SECRET

    monkeypatch.setattr(calendar, "_token", tok)


def _event(**over):
    return {"id": EVENT_ID, "status": "confirmed", "summary": f"Call: {CALLER}",
            "htmlLink": "https://calendar.google.com/event?eid=x",
            "start": {"dateTime": "2026-09-29T14:00:00-04:00",
                      "timeZone": "America/New_York"},
            "end": {"dateTime": "2026-09-29T14:30:00-04:00",
                    "timeZone": "America/New_York"}, **over}


async def test_book_slot_verified(net, db, cal_on):
    net.on(rf"/events/{EVENT_ID}$", body=_event())
    db["screening_calls"] = {"outcome": "booked", "event_id": EVENT_ID}
    got = await v.verify("screening.book_slot", BS_PARAMS, BS_RESULT)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["event_id"] == EVENT_ID
    assert got.evidence["outcome"] == "booked"
    audit = json.dumps(got.as_audit(), default=str)
    assert CALLER not in audit and "Call:" not in audit
    _no_secret(got)


async def test_book_slot_naive_start_resolved_in_calendar_zone(net, db, cal_on):
    net.on(rf"/events/{EVENT_ID}$", body=_event())
    db["screening_calls"] = {"outcome": "booked", "event_id": EVENT_ID}
    got = await v.verify("screening.book_slot",
                         {**BS_PARAMS, "start": "2026-09-29T14:00:00"}, BS_RESULT)
    assert got.state is v.State.VERIFIED, got.reason


@pytest.mark.parametrize("event, row, words", [
    (None, None, "not on the calendar"),
    (_event(status="cancelled"), None, "cancelled"),
    (_event(start={"dateTime": "2026-09-29T15:00:00-04:00"},
            end={"dateTime": "2026-09-29T15:30:00-04:00"}), None, "starts"),
    (_event(end={"dateTime": "2026-09-29T15:00:00-04:00"}), None, "runs 60 min"),
    (_event(attendees=[{"email": "x@example.com"}]), None, "attendee"),
    (_event(), None, "no screening_calls row"),
    (_event(), {"outcome": "message", "event_id": None}, "'message'"),
    (_event(), {"outcome": "booked", "event_id": "other"}, "event id"),
])
async def test_book_slot_contradicted(net, db, cal_on, event, row, words):
    if event is None:
        net.on(rf"/events/{EVENT_ID}$", 404, {"error": "gone"})
    else:
        net.on(rf"/events/{EVENT_ID}$", body=event)
    db["screening_calls"] = row
    got = await v.verify("screening.book_slot", BS_PARAMS, BS_RESULT)
    assert got.state is v.State.CONTRADICTED and words in got.reason, got.reason
    assert CALLER not in json.dumps(got.as_audit(), default=str)


async def test_book_slot_unknown(net, db, cal_on):
    got = await v.verify("screening.book_slot", BS_PARAMS, ActionResult(True))
    assert got.state is v.State.UNKNOWN and "no event id" in got.reason

    net.on(rf"/events/{EVENT_ID}$", 503, {"error": "down"})
    got = await v.verify("screening.book_slot", BS_PARAMS, BS_RESULT)
    assert got.state is v.State.UNKNOWN
    _no_secret(got)


@pytest.mark.parametrize("off", ["module", "env"])
async def test_book_slot_off_is_disabled(net, db, cal_on, monkeypatch, off):
    if off == "module":
        monkeypatch.setenv("ATLAS_DISABLED_MODULES", "screening")
    else:
        monkeypatch.delenv("GOOGLE_REFRESH_TOKEN")
    got = await v.verify("screening.book_slot", BS_PARAMS, BS_RESULT)
    assert got.state is v.State.UNKNOWN
    assert got.failure_class is v.FailureClass.DISABLED
    assert net.seen == []
    _no_secret(got)


# ================================================================== exampleco

PC_RESULT = ActionResult(True, "3 comparables", evidence={"prices": [300.0, 100.0,
                                                                     200.0]})


@pytest.fixture
def ebay_on(monkeypatch):
    monkeypatch.setenv("EBAY_CLIENT_ID", "x")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "x")
    monkeypatch.setenv("EBAY_REFRESH_TOKEN", SECRET)


async def test_price_check_unknown_by_design_when_on(net, ebay_on):
    got = await v.verify("exampleco.price_check", {"query": "PA-3220"}, PC_RESULT)
    assert got.state is v.State.UNKNOWN and got.failure_class is None
    assert "writes nothing" in got.reason
    assert got.evidence["run_recorded"] == {"comparables": 3, "low": 100.0,
                                            "median": 200.0, "high": 300.0}
    assert net.seen == []
    _no_secret(got)


@pytest.mark.parametrize("off", ["module", "env"])
async def test_price_check_off_is_disabled(net, ebay_on, monkeypatch, off):
    if off == "module":
        monkeypatch.setenv("ATLAS_DISABLED_MODULES", "exampleco")
    else:
        monkeypatch.delenv("EBAY_CLIENT_ID")
    got = await v.verify("exampleco.price_check", {"query": "x"}, PC_RESULT)
    assert got.state is v.State.UNKNOWN
    assert got.failure_class is v.FailureClass.DISABLED
    assert net.seen == []
    _no_secret(got)
