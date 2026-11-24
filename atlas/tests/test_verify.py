"""kernel.verify -- read-back verification (RA-D1, RA-D2).

Every provider is faked at `httpx.AsyncClient.send`, and that fake refuses
any method but GET: a verifier that tried to post, send or change anything
fails the test that exercised it. No network, no database.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from atlas.kernel import executor
from atlas.kernel import verify as v
from atlas.kernel.contracts import Action, ActionResult, Tier
from atlas.kernel.executor import ExecutionRequest, execute
from atlas.kernel.permissions import classify as classify_tier
from atlas.kernel.registry import REGISTRY
from atlas.kernel.state import AuditRow

SECRET = "sk-verify-supersecret-0123456789"


class Net:
    """Routes by a regex on the request path; GET only."""

    def __init__(self) -> None:
        self.routes: list[tuple[str, object]] = []
        self.seen: list[str] = []

    def on(self, pattern: str, status: int = 200, body=None, *, raises=None) -> None:
        self.routes.append((pattern, (status, body, raises)))

    async def send(self, client, request: httpx.Request, **_kw) -> httpx.Response:
        assert request.method == "GET", (
            f"a verifier tried to write: {request.method} {request.url.path}")
        path = request.url.path
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
    return fake


@pytest.fixture(autouse=True)
def secret_env(monkeypatch):
    monkeypatch.setenv("VERIFY_TEST_API_KEY", SECRET)


def _no_secret(result: v.Verification) -> None:
    assert SECRET not in json.dumps(result.as_audit(), default=str)


# ================================================================== kernel

@pytest.fixture
def temp_verifiers():
    before_v, before_r = dict(v._VERIFIERS), dict(v._RECONCILERS)
    yield
    v._VERIFIERS.clear()
    v._VERIFIERS.update(before_v)
    v._RECONCILERS.clear()
    v._RECONCILERS.update(before_r)


async def test_no_verifier_is_unknown_never_verified():
    assert not v.has_verifier("nosuchmodule.act")
    got = await v.verify("nosuchmodule.act", {}, ActionResult(True, "done"))
    assert got.state is v.State.UNKNOWN
    assert got.reason == "no verifier"


async def test_an_action_of_a_module_without_readback_has_no_verifier():
    # Every action in the catalogue now has a verifier (RA-D1), so this uses
    # a real module that has no readback.py and no actions.
    assert not v.has_verifier("markets.anything")


async def test_verified_carries_evidence_and_a_time(temp_verifiers):
    @v.verifier("ztest.ok")
    async def _ok(params, result):
        return v.verified(id=7, permalink="https://example.test/7")

    got = await v.verify("ztest.ok", {}, ActionResult(True))
    assert got.state is v.State.VERIFIED
    assert got.evidence == {"id": 7, "permalink": "https://example.test/7"}
    assert got.checked_at.tzinfo is not None


async def test_verified_without_evidence_is_downgraded(temp_verifiers):
    @v.verifier("ztest.empty")
    async def _empty(params, result):
        return v.Verification(v.State.VERIFIED)

    got = await v.verify("ztest.empty", {}, ActionResult(True))
    assert got.state is v.State.UNKNOWN


async def test_a_verifier_that_raises_is_unknown_and_redacted(temp_verifiers):
    @v.verifier("ztest.boom")
    async def _boom(params, result):
        raise RuntimeError(f"cannot read with {SECRET}")

    got = await v.verify("ztest.boom", {}, ActionResult(True))
    assert got.state is v.State.UNKNOWN
    assert got.reason.startswith("verifier raised")
    _no_secret(got)


async def test_a_verifier_that_returns_junk_is_unknown(temp_verifiers):
    @v.verifier("ztest.junk")
    async def _junk(params, result):
        return True

    got = await v.verify("ztest.junk", {}, ActionResult(True))
    assert got.state is v.State.UNKNOWN


async def test_a_hung_read_is_unknown_transient(temp_verifiers, monkeypatch):
    monkeypatch.setattr(v, "TIMEOUT_S", 0.01)

    @v.verifier("ztest.slow")
    async def _slow(params, result):
        await asyncio.sleep(1)
        return v.verified(id=1)

    got = await v.verify("ztest.slow", {}, ActionResult(True))
    assert got.state is v.State.UNKNOWN
    assert got.failure_class is v.FailureClass.TRANSIENT


async def test_cancellation_is_not_swallowed(temp_verifiers):
    @v.verifier("ztest.cancel")
    async def _cancel(params, result):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await v.verify("ztest.cancel", {}, ActionResult(True))


async def test_evidence_never_carries_a_secret(temp_verifiers):
    @v.verifier("ztest.leaky")
    async def _leaky(params, result):
        return v.verified(id=1, access_token="abc", note=f"read with {SECRET}",
                          nested={"page_token": "x", "ok": "fine"})

    got = await v.verify("ztest.leaky", {}, ActionResult(True))
    assert "access_token" not in got.evidence
    assert got.evidence["nested"] == {"ok": "fine"}
    _no_secret(got)


def test_combine_contradicted_wins_then_unknown_then_verified():
    ok, bad, huh = (v.verified(id=1), v.contradicted("gone"), v.unknown("later"))
    assert v.combine({"a": ok, "b": huh, "c": bad}).state is v.State.CONTRADICTED
    assert "c: gone" in v.combine({"a": ok, "c": bad}).reason
    assert v.combine({"a": ok, "b": huh}).state is v.State.UNKNOWN
    assert v.combine({"a": ok}).state is v.State.VERIFIED
    assert v.combine({}).state is v.State.UNKNOWN


async def test_reconcile_without_a_reconciler_is_unknown():
    row = {"id": 3, "at": datetime(2026, 9, 28, 4, 0, tzinfo=UTC),
           "subject": "nosuchmodule.act"}
    got = await v.reconcile_unknown("nosuchmodule.act", {}, row)
    assert got.state is v.State.UNKNOWN and got.reason == "no reconciler"


async def test_reconcile_reads_params_off_an_audit_row(temp_verifiers):
    seen = {}

    @v.reconciler("ztest.rec")
    async def _rec(params, started):
        seen.update(params=params, started=started)
        return v.contradicted("not there", looked="yes")

    payload = json.dumps({"event": "execute.start", "subject": "ztest.rec",
                          "data": {"params": {"title": "T", "project": "p"}}})
    at = datetime(2026, 9, 28, 4, 0, tzinfo=UTC)
    row = AuditRow(1, at, "execute.start", "ztest.rec", payload, "g", "d")
    got = await v.reconcile_unknown("ztest.rec", {"project": "q"}, row)
    assert got.state is v.State.CONTRADICTED
    assert seen["params"] == {"title": "T", "project": "q"}
    assert seen["started"] == at


async def test_reconcile_never_touches_the_action(temp_verifiers):
    """Looking an effect up must not re-run, dry-run or roll back."""
    async def forbidden(*_a, **_kw):
        raise AssertionError("reconcile reached the action")

    REGISTRY.actions["ztest.rec2"] = Action(
        name="ztest.rec2", tier=Tier.ASK, summary="s", run=forbidden,
        dry_run=forbidden, rollback=forbidden)
    try:
        @v.reconciler("ztest.rec2")
        async def _rec(params, started):
            return v.unknown("cannot tell")

        got = await v.reconcile_unknown(
            "ztest.rec2", {}, {"at": "2026-09-28T04:00:00+00:00"})
        assert got.state is v.State.UNKNOWN
    finally:
        REGISTRY.actions.pop("ztest.rec2", None)


# ================================================================== executor hook

@pytest.fixture
def hands_on(monkeypatch, memory_backend):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()

    async def _free(_module, _account=None):
        return None

    from atlas.kernel import pause
    monkeypatch.setattr(pause, "active_for", _free)
    yield memory_backend
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()


def _install(name="ztest.act", ok=True) -> ActionResult:
    result = ActionResult(ok, "done", evidence={"id": 9}, rollback_token="tok")

    async def run(**_kw):
        return result

    async def dry(**_kw):
        return ActionResult(True, "preview")

    async def undo(_t):
        return ActionResult(True)

    action = Action(name=name, tier=Tier.ASK, summary="s", run=run, dry_run=dry,
                    rollback=undo)
    REGISTRY.actions[name] = action
    REGISTRY.decisions[name] = classify_tier(action)
    return result


def _events(backend) -> list[str]:
    return [r.event for r in backend.audit_rows]


async def _settle() -> None:
    """The hook is detached: wait for the read-backs it started."""
    from atlas.kernel import background
    pending = [t for t in list(background._TASKS) if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def test_hook_off_by_default_never_calls_verify(hands_on, monkeypatch):
    monkeypatch.delenv("ATLAS_VERIFY_AFTER", raising=False)
    called = []

    async def spy(*a):
        called.append(a)
        return v.verified(id=1)

    monkeypatch.setattr(v, "verify", spy)
    _install()
    got = await execute(ExecutionRequest("ztest.act", {}, "r", approved_by="owner"))
    await _settle()
    assert got.ok and called == []
    assert not any(e.startswith("verify.") for e in _events(hands_on))


async def test_hook_on_records_a_verify_event(hands_on, monkeypatch, temp_verifiers):
    monkeypatch.setenv("ATLAS_VERIFY_AFTER", "on")

    @v.verifier("ztest.act")
    async def _ok(params, result):
        return v.verified(id=result.evidence["id"])

    original = _install()
    got = await execute(ExecutionRequest("ztest.act", {}, "r", approved_by="owner"))
    # The action's own result, untouched bar the executor's RA-B3 stamp.
    assert got == dataclasses.replace(original, dispatched=True)
    await _settle()
    assert _events(hands_on)[-2:] == ["execute.finish", "verify.verified"]
    payload = json.loads(hands_on.audit_rows[-1].payload)
    assert payload["data"]["evidence"] == {"id": 9}
    assert payload["data"]["state"] == "verified"


async def test_hook_with_no_verifier_records_unknown(hands_on, monkeypatch):
    monkeypatch.setenv("ATLAS_VERIFY_AFTER", "on")
    _install("ztestnone.act")
    got = await execute(ExecutionRequest("ztestnone.act", {}, "r", approved_by="owner"))
    assert got.ok
    await _settle()
    assert _events(hands_on)[-1] == "verify.unknown"


async def test_a_raising_verifier_does_not_break_execute(hands_on, monkeypatch,
                                                         temp_verifiers):
    monkeypatch.setenv("ATLAS_VERIFY_AFTER", "on")

    @v.verifier("ztest.act")
    async def _boom(params, result):
        raise RuntimeError("provider exploded")

    original = _install()
    got = await execute(ExecutionRequest("ztest.act", {}, "r", approved_by="owner"))
    # The action's own result, untouched bar the executor's RA-B3 stamp.
    assert got == dataclasses.replace(original, dispatched=True)
    await _settle()
    assert _events(hands_on)[-1] == "verify.unknown"


async def test_a_raising_verify_call_does_not_break_execute(hands_on, monkeypatch):
    monkeypatch.setenv("ATLAS_VERIFY_AFTER", "on")

    async def broken(*_a):
        raise RuntimeError("the kernel's own bug")

    monkeypatch.setattr(v, "verify", broken)
    original = _install()
    got = await execute(ExecutionRequest("ztest.act", {}, "r", approved_by="owner"))
    # The action's own result, untouched bar the executor's RA-B3 stamp.
    assert got == dataclasses.replace(original, dispatched=True)
    await _settle()
    assert _events(hands_on)[-1] == "execute.finish"


async def test_a_failed_action_is_not_read_back(hands_on, monkeypatch):
    monkeypatch.setenv("ATLAS_VERIFY_AFTER", "on")
    called = []

    async def spy(*a):
        called.append(a)
        return v.verified(id=1)

    monkeypatch.setattr(v, "verify", spy)
    _install(ok=False)
    await execute(ExecutionRequest("ztest.act", {}, "r", approved_by="owner"))
    await _settle()
    assert called == []


async def test_a_cancelled_read_back_never_reaches_the_caller(hands_on, monkeypatch,
                                                              temp_verifiers):
    """A shutdown during the read-back must not turn a finished run into a
    failed one: execute has already returned when the read-back is cut."""
    monkeypatch.setenv("ATLAS_VERIFY_AFTER", "on")
    gate = asyncio.Event()

    @v.verifier("ztest.act")
    async def _slow(params, result):
        await gate.wait()
        return v.verified(id=1)

    original = _install()
    got = await execute(ExecutionRequest("ztest.act", {}, "r", approved_by="owner"))
    # The action's own result, untouched bar the executor's RA-B3 stamp.
    assert got == dataclasses.replace(original, dispatched=True)
    from atlas.kernel import background
    cut = await background.cancel_all(timeout=1)
    assert any(name.startswith("verify ztest.act") for name in cut)
    assert _events(hands_on)[-1] == "execute.finish"


# ================================================================== gitlab

GL_ISSUE = r"/api/v4/projects/root(%2F|/)atlas/issues/7$"


@pytest.fixture
def gitlab_env(monkeypatch):
    monkeypatch.setenv("GITLAB_TOKEN", SECRET)


GL_PARAMS = {"instance": "business", "project": "root/atlas", "title": "Disk low"}
GL_RESULT = ActionResult(True, "opened", rollback_token="business|root/atlas|7")


def _issue(**over):
    return {"iid": 7, "title": "Disk low", "state": "opened",
            "web_url": "https://aicode.example.com/root/atlas/-/issues/7", **over}


async def test_gitlab_verified(net, gitlab_env):
    net.on(GL_ISSUE, body=_issue())
    got = await v.verify("gitlab.open_issue", GL_PARAMS, GL_RESULT)
    assert got.state is v.State.VERIFIED
    assert got.evidence["iid"] == 7 and got.evidence["web_url"].endswith("/7")
    _no_secret(got)


async def test_gitlab_19_work_item_links_are_verified(net, gitlab_env):
    """GitLab 19 links an issue as /-/work_items/<iid>. Accepting only
    /-/issues/ contradicted six issues opened exactly as approved."""
    net.on(GL_ISSUE, body=_issue(
        web_url="https://aicode.example.com/root/atlas/-/work_items/7"))
    got = await v.verify("gitlab.open_issue", GL_PARAMS, GL_RESULT)
    assert got.state is v.State.VERIFIED


async def test_a_work_item_in_another_project_is_still_contradicted(net, gitlab_env):
    net.on(GL_ISSUE, body=_issue(
        web_url="https://aicode.example.com/root/other/-/work_items/7"))
    got = await v.verify("gitlab.open_issue", GL_PARAMS, GL_RESULT)
    assert got.state is v.State.CONTRADICTED and "not in root/atlas" in got.reason


async def test_gitlab_contradicted_when_missing_or_wrong(net, gitlab_env):
    net.on(GL_ISSUE, 404, {"message": "404 Not found"})
    got = await v.verify("gitlab.open_issue", GL_PARAMS, GL_RESULT)
    assert got.state is v.State.CONTRADICTED

    net.routes.clear()
    net.on(GL_ISSUE, body=_issue(title="Something else"))
    got = await v.verify("gitlab.open_issue", GL_PARAMS, GL_RESULT)
    assert got.state is v.State.CONTRADICTED and "title" in got.reason


async def test_gitlab_unknown(net, gitlab_env):
    got = await v.verify("gitlab.open_issue", GL_PARAMS, ActionResult(True, "opened"))
    assert got.state is v.State.UNKNOWN
    net.on(GL_ISSUE, 503, {"message": "down"})
    got = await v.verify("gitlab.open_issue", GL_PARAMS, GL_RESULT)
    assert got.state is v.State.UNKNOWN
    assert got.failure_class is v.FailureClass.TRANSIENT


async def test_gitlab_raises_is_unknown(net, gitlab_env):
    net.on(GL_ISSUE, raises=httpx.ConnectError("refused"))
    got = await v.verify("gitlab.open_issue", GL_PARAMS, GL_RESULT)
    assert got.state is v.State.UNKNOWN and "raised" in got.reason
    _no_secret(got)


async def test_gitlab_reconcile_finds_or_denies(net, gitlab_env):
    started = datetime(2026, 9, 28, 4, 0, tzinfo=UTC)
    net.on(r"/issues$", body=[_issue()])
    got = await v.reconcile_unknown("gitlab.open_issue", GL_PARAMS, {"at": started})
    assert got.state is v.State.VERIFIED
    net.routes.clear()
    net.on(r"/issues$", body=[])
    got = await v.reconcile_unknown("gitlab.open_issue", GL_PARAMS, {"at": started})
    assert got.state is v.State.CONTRADICTED


# ================================================================== instagram

@pytest.fixture
def ig_env(monkeypatch):
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", "1789")
    monkeypatch.setenv("INSTAGRAM_TOKEN", SECRET)
    monkeypatch.setenv("INSTAGRAM_STORIES_ACCOUNT_ID", "1790")
    monkeypatch.setenv("INSTAGRAM_STORIES_TOKEN", SECRET + "-stories")


IG_PARAMS = {"caption": "Cairo for 200 dollars", "image_url": "https://x/y.jpg",
             "kind": "image", "account": "main", "content_kind": "flycheap"}
IG_RESULT = ActionResult(True, "published 55", rollback_token="main:55")


def _media(mid="55", **over):
    return {"id": mid, "permalink": f"https://www.instagram.com/p/{mid}/",
            "media_type": "IMAGE", "caption": "Cairo for 200 dollars",
            "timestamp": "2026-09-28T10:00:00+0000", **over}


async def test_instagram_verified(net, ig_env):
    net.on(r"/55$", body=_media())
    got = await v.verify("instagram.publish", IG_PARAMS, IG_RESULT)
    assert got.state is v.State.VERIFIED
    assert got.evidence["permalink"].endswith("/55/")
    _no_secret(got)


async def test_instagram_contradicted(net, ig_env):
    net.on(r"/55$", 400, {"error": {"message": "Object with ID '55' does not exist"}})
    got = await v.verify("instagram.publish", IG_PARAMS, IG_RESULT)
    assert got.state is v.State.CONTRADICTED

    net.routes.clear()
    net.on(r"/55$", body=_media(caption="another post"))
    got = await v.verify("instagram.publish", IG_PARAMS, IG_RESULT)
    assert got.state is v.State.CONTRADICTED and "caption" in got.reason


async def test_instagram_unknown(net, ig_env, monkeypatch):
    net.on(r"/55$", 401, {"error": {"message": "token expired"}})
    got = await v.verify("instagram.publish", IG_PARAMS, IG_RESULT)
    assert got.state is v.State.UNKNOWN
    assert got.failure_class is v.FailureClass.AUTH
    _no_secret(got)
    got = await v.verify("instagram.publish", IG_PARAMS, ActionResult(True))
    assert got.state is v.State.UNKNOWN


async def test_instagram_raises_is_unknown(ig_env, monkeypatch):
    from atlas.modules.instagram import readback

    async def boom(*_a, **_kw):
        raise RuntimeError("parse error")

    monkeypatch.setattr(readback, "_get", boom)
    got = await v.verify("instagram.publish", IG_PARAMS, IG_RESULT)
    assert got.state is v.State.UNKNOWN and "raised" in got.reason


async def test_instagram_reconcile(net, ig_env):
    started = datetime(2026, 9, 28, 9, 59, tzinfo=UTC)
    net.on(r"/1789/media$", body={"data": [_media()]})
    got = await v.reconcile_unknown("instagram.publish", IG_PARAMS, {"at": started})
    assert got.state is v.State.VERIFIED and got.evidence["media_id"] == "55"
    got = await v.reconcile_unknown("instagram.publish", IG_PARAMS,
                                    {"at": started + timedelta(hours=1)})
    assert got.state is v.State.CONTRADICTED


# ================================================================== reels

@pytest.fixture
def reel_env(monkeypatch, ig_env):
    monkeypatch.setenv("FACEBOOK_STORIES_PAGE_ID", "4242")
    monkeypatch.setenv("FACEBOOK_STORIES_PAGE_TOKEN", SECRET + "-page")
    monkeypatch.setenv("POSTIZ_API_KEY", SECRET + "-postiz")


CAPTION = "A fox finds a lantern\n\nMade with AI"
REEL_PARAMS = {"media_id": 10, "caption": CAPTION}


def _reel_result(statuses: dict, posted: dict) -> ActionResult:
    return ActionResult(True, "reel 10", rollback_token=json.dumps(posted),
                        evidence={k: {"status": s, "detail": ""}
                                  for k, s in statuses.items()})


ALL_POSTED = _reel_result({"instagram": "posted", "facebook": "posted",
                           "tiktok": "posted"},
                          {"instagram": "stories:11", "facebook": "22",
                           "tiktok": "p33"})


def _reel_routes(net, *, fb=None, tiktok_state="PUBLISHED"):
    net.on(r"/11$", body=_media("11", media_type="VIDEO", media_product_type="REELS",
                                caption=CAPTION))
    net.on(r"/22$", body=fb if fb is not None else {
        "id": "22", "permalink_url": "https://facebook.com/v/22",
        "status": {"video_status": "ready"}})
    net.on(r"/posts$", body={"posts": [{"id": "p33", "state": tiktok_state}]})


@pytest.mark.parametrize("action", ["content.publish_reel", "content.autopublish_reel"])
async def test_reel_verified_on_every_leg(net, reel_env, action):
    _reel_routes(net)
    got = await v.verify(action, REEL_PARAMS, ALL_POSTED)
    assert got.state is v.State.VERIFIED
    assert set(got.evidence) == {"instagram", "facebook", "tiktok"}
    assert got.evidence["facebook"]["permalink"] == "https://facebook.com/v/22"
    _no_secret(got)


async def test_reel_missing_facebook_leg_is_contradicted_and_named(net, reel_env):
    """Media 10: instagram and tiktok posted, facebook did not (RA-D2)."""
    _reel_routes(net)
    result = _reel_result({"instagram": "posted", "facebook": "refused",
                           "tiktok": "posted"},
                          {"instagram": "stories:11", "tiktok": "p33"})
    got = await v.verify("content.publish_reel", REEL_PARAMS, result)
    assert got.state is v.State.CONTRADICTED
    assert got.reason.startswith("facebook")
    assert got.evidence["instagram"]["state"] == "verified"

    no_row = ActionResult(True, "reel 10",
                          rollback_token=json.dumps({"instagram": "stories:11",
                                                     "tiktok": "p33"}))
    got = await v.verify("content.publish_reel", REEL_PARAMS, no_row)
    assert got.state is v.State.CONTRADICTED and "facebook" in got.reason


async def test_reel_leg_gone_after_posting_is_contradicted(net, reel_env):
    _reel_routes(net, fb={"id": "22", "status": {"video_status": "error"}})
    got = await v.verify("content.publish_reel", REEL_PARAMS, ALL_POSTED)
    assert got.state is v.State.CONTRADICTED and "facebook" in got.reason


async def test_reel_unconfigured_leg_is_skipped_not_an_outage(net, reel_env):
    _reel_routes(net)
    result = _reel_result({"instagram": "posted", "facebook": "not configured",
                           "tiktok": "posted"},
                          {"instagram": "stories:11", "tiktok": "p33"})
    got = await v.verify("content.publish_reel", REEL_PARAMS, result)
    assert got.state is v.State.VERIFIED
    assert "facebook" not in got.evidence


async def test_reel_queued_leg_is_unknown(net, reel_env):
    _reel_routes(net, tiktok_state="QUEUE")
    got = await v.verify("content.publish_reel", REEL_PARAMS, ALL_POSTED)
    assert got.state is v.State.UNKNOWN and "tiktok" in got.reason


async def test_reel_raising_read_is_unknown(net, reel_env, monkeypatch):
    _reel_routes(net)
    from atlas.modules.content import crosspost

    def boom():
        raise RuntimeError("config blew up")

    monkeypatch.setattr(crosspost, "_fb_cfg", boom)
    got = await v.verify("content.publish_reel", REEL_PARAMS, ALL_POSTED)
    assert got.state is v.State.UNKNOWN and "facebook" in got.reason
    got = await v.verify("content.publish_reel", REEL_PARAMS,
                         ActionResult(True, "x", rollback_token="not json"))
    assert got.state is v.State.UNKNOWN


async def test_reel_reconcile_names_the_missing_facebook_leg(net, reel_env, monkeypatch):
    from atlas.modules.content import readback

    async def recorded(_media_id):
        return {"instagram": "11", "tiktok": "p33"}

    monkeypatch.setattr(readback, "_recorded", recorded)
    _reel_routes(net)
    net.on(r"/4242/videos$", body={"data": []})
    started = datetime(2026, 9, 28, 7, 0, tzinfo=UTC)
    got = await v.reconcile_unknown("content.publish_reel", REEL_PARAMS, {"at": started})
    assert got.state is v.State.CONTRADICTED and got.reason.startswith("facebook")

    net.routes.clear()
    _reel_routes(net)
    net.on(r"/4242/videos$", body={"data": [{
        "id": "22", "description": CAPTION, "created_time": "2026-09-28T07:01:00+0000"}]})
    got = await v.reconcile_unknown("content.publish_reel", REEL_PARAMS, {"at": started})
    assert got.state is v.State.VERIFIED


# ================================================================== linkedin

@pytest.fixture
def li_state(monkeypatch, tmp_path):
    from atlas.modules import linkedin
    path = tmp_path / "state.json"

    def write(entry: dict) -> None:
        path.write_text(json.dumps({"improver": {"post_bank": [entry], "posting": {}}}))

    monkeypatch.setattr(linkedin, "STATE", path)
    return write


LI_TEXT = "Cheap flights to Cairo this week, from New York and Boston"
LI_ENTRY = {"id": "B13", "status": "published", "text": LI_TEXT,
            "activity_url": "https://www.linkedin.com/feed/update/urn:li:activity:1",
            "published_at": "2026-09-28T10:00-04:00"}
LI_RESULT = ActionResult(True, "published B13",
                         evidence={"page": "flycheap", "entry": "B13"})


def _page_shows(monkeypatch, text=None, *, raises=None):
    from atlas.modules.linkedin import readback

    async def published_text(_page):
        if raises is not None:
            raise raises
        return text

    monkeypatch.setattr(readback, "published_text", published_text)


async def test_linkedin_verified(li_state, monkeypatch):
    li_state(LI_ENTRY)
    _page_shows(monkeypatch, "Posts\n" + LI_TEXT + "\n12 impressions")
    got = await v.verify("linkedin.publish_post", {"page": "flycheap"}, LI_RESULT)
    assert got.state is v.State.VERIFIED
    assert got.evidence["activity_url"].endswith("activity:1")


async def test_linkedin_contradicted(li_state, monkeypatch):
    li_state(LI_ENTRY)
    _page_shows(monkeypatch, "Posts\nsomething older")
    got = await v.verify("linkedin.publish_post", {"page": "flycheap"}, LI_RESULT)
    assert got.state is v.State.CONTRADICTED


async def test_linkedin_unknown_when_signed_out(li_state, monkeypatch):
    from atlas.modules.linkedin.readback import Unreadable
    li_state(LI_ENTRY)
    _page_shows(monkeypatch, raises=Unreadable("signed out", v.FailureClass.AUTH))
    got = await v.verify("linkedin.publish_post", {"page": "flycheap"}, LI_RESULT)
    assert got.state is v.State.UNKNOWN
    assert got.failure_class is v.FailureClass.AUTH


async def test_linkedin_raises_is_unknown(li_state, monkeypatch):
    li_state(LI_ENTRY)
    _page_shows(monkeypatch, raises=RuntimeError("playwright crashed"))
    got = await v.verify("linkedin.publish_post", {"page": "flycheap"}, LI_RESULT)
    assert got.state is v.State.UNKNOWN and "raised" in got.reason


async def test_linkedin_reconcile_the_crash_window(li_state, monkeypatch):
    """Post clicked, killed before the entry was marked: still `approved`.
    Only the page's list can say whether it is live; nothing is re-posted."""
    li_state({**LI_ENTRY, "status": "approved", "published_at": None})
    started = datetime(2026, 9, 28, 4, 14, tzinfo=UTC)
    params = {"page": "flycheap", "entry_id": "B13"}
    _page_shows(monkeypatch, LI_TEXT)
    got = await v.reconcile_unknown("linkedin.publish_post", params, {"at": started})
    assert got.state is v.State.VERIFIED
    _page_shows(monkeypatch, "nothing new")
    got = await v.reconcile_unknown("linkedin.publish_post", params, {"at": started})
    assert got.state is v.State.CONTRADICTED
    got = await v.reconcile_unknown("linkedin.publish_post", {"page": "flycheap"},
                                    {"at": started})
    assert got.state is v.State.UNKNOWN


# ================================================================== calendar

@pytest.fixture
def cal(monkeypatch):
    from atlas.modules import calendar

    async def tok():
        return SECRET

    monkeypatch.setattr(calendar, "_token", tok)


def _event(**over):
    return {"id": "e1", "status": "confirmed", "summary": "Focus",
            "htmlLink": "https://calendar.google.com/e1",
            "start": {"dateTime": "2026-10-01T09:00:00-04:00",
                      "timeZone": "America/New_York"},
            "end": {"dateTime": "2026-10-01T10:00:00-04:00",
                    "timeZone": "America/New_York"}, **over}


BLOCK = {"summary": "Focus", "start": "2026-10-01T09:00:00", "minutes": "60"}
BLOCK_RESULT = ActionResult(True, "blocked", rollback_token="e1")


async def test_calendar_block_verified_in_the_calendar_timezone(net, cal):
    net.on(r"/events/e1$", body=_event())
    got = await v.verify("calendar.create_block", BLOCK, BLOCK_RESULT)
    assert got.state is v.State.VERIFIED
    assert got.evidence["timezone"] == "America/New_York"
    assert got.evidence["approved_naive"] is True
    _no_secret(got)


async def test_calendar_block_contradicted(net, cal):
    net.on(r"/events/e1$", body=_event(start={"dateTime": "2026-10-01T09:00:00Z"},
                                       end={"dateTime": "2026-10-01T10:00:00Z"}))
    net.on(r"/calendars/primary$", body={"timeZone": "America/New_York"})
    got = await v.verify("calendar.create_block", BLOCK, BLOCK_RESULT)
    assert got.state is v.State.CONTRADICTED and "starts" in got.reason

    net.routes.clear()
    net.on(r"/events/e1$", body=_event(attendees=[{"email": "x@y.com"}]))
    got = await v.verify("calendar.create_block", BLOCK, BLOCK_RESULT)
    assert got.state is v.State.CONTRADICTED
    assert got.failure_class is v.FailureClass.POLICY

    net.routes.clear()
    net.on(r"/events/e1$", 404, {"error": "gone"})
    got = await v.verify("calendar.create_block", BLOCK, BLOCK_RESULT)
    assert got.state is v.State.CONTRADICTED


async def test_calendar_unknown(net, cal):
    got = await v.verify("calendar.create_block", BLOCK, ActionResult(True))
    assert got.state is v.State.UNKNOWN
    net.on(r"/events/e1$", 503, {"error": "busy"})
    got = await v.verify("calendar.create_block", BLOCK, BLOCK_RESULT)
    assert got.state is v.State.UNKNOWN


async def test_calendar_raises_is_unknown(net, cal):
    net.on(r"/events/e1$", raises=httpx.ConnectError("refused"))
    got = await v.verify("calendar.create_block", BLOCK, BLOCK_RESULT)
    assert got.state is v.State.UNKNOWN and "raised" in got.reason


async def test_calendar_book_with_attendee_scope(net, cal):
    params = {"to": "Ana@Example.com", "summary": "Focus",
              "start": "2026-10-01T09:00:00-04:00", "minutes": 60}
    me = {"email": "owner@example.com", "self": True, "organizer": True}
    net.on(r"/events/e1$", body=_event(attendees=[me, {"email": "ana@example.com"}]))
    got = await v.verify("calendar.book_with", params, BLOCK_RESULT)
    assert got.state is v.State.VERIFIED
    net.routes.clear()
    net.on(r"/events/e1$", body=_event(attendees=[
        me, {"email": "ana@example.com"}, {"email": "bob@example.com"}]))
    got = await v.verify("calendar.book_with", params, BLOCK_RESULT)
    assert got.state is v.State.CONTRADICTED


async def test_calendar_decline(net, cal):
    params = {"event_id": "e1", "alternatives": "Thu 3pm"}
    net.on(r"/events/e1$", body=_event(attendees=[
        {"email": "m@x.com", "self": True, "responseStatus": "declined"}]))
    got = await v.verify("calendar.decline_invite", params, ActionResult(True))
    assert got.state is v.State.VERIFIED
    net.routes.clear()
    net.on(r"/events/e1$", body=_event(attendees=[
        {"email": "m@x.com", "self": True, "responseStatus": "needsAction"}]))
    got = await v.verify("calendar.decline_invite", params, ActionResult(True))
    assert got.state is v.State.CONTRADICTED


async def test_calendar_offer_slots_reads_the_sent_message(net, cal):
    params = {"to": "ana@example.com", "subject": "Times", "slots": "Thu"}
    result = ActionResult(True, "sent", evidence={"message_id": "m1"})
    net.on(r"/messages/m1$", body={"id": "m1", "labelIds": ["SENT"], "payload": {
        "headers": [{"name": "To", "value": "ana@example.com"},
                    {"name": "Subject", "value": "Times"}]}})
    got = await v.verify("calendar.offer_slots", params, result)
    assert got.state is v.State.VERIFIED
    net.routes.clear()
    net.on(r"/messages/m1$", 404, {"error": "gone"})
    got = await v.verify("calendar.offer_slots", params, result)
    assert got.state is v.State.CONTRADICTED


async def test_calendar_reconcile(net, cal):
    net.on(r"/calendars/primary$", body={"timeZone": "America/New_York"})
    net.on(r"/events/e1$", body=_event())
    net.on(r"/events$", body={"items": [_event()]})
    started = datetime(2026, 9, 28, 4, 0, tzinfo=UTC)
    got = await v.reconcile_unknown("calendar.create_block", BLOCK, {"at": started})
    assert got.state is v.State.VERIFIED
    net.routes.clear()
    net.on(r"/calendars/primary$", body={"timeZone": "America/New_York"})
    net.on(r"/events$", body={"items": []})
    got = await v.reconcile_unknown("calendar.create_block", BLOCK, {"at": started})
    assert got.state is v.State.CONTRADICTED


# ================================================================== cloudco

@pytest.fixture
def vps(monkeypatch):
    from atlas.modules import cloudco

    async def tok():
        return SECRET

    monkeypatch.setattr(cloudco, "_access_token", tok)


def _snap_result(queued_minutes_ago=20, **statuses) -> ActionResult:
    queued = (datetime.now(UTC) - timedelta(minutes=queued_minutes_ago)).isoformat()
    instances = {ident: {"host": f"host-{ident}", "status": s}
                 for ident, s in (statuses or {"vm1": "queued", "vm2": "queued"}).items()}
    return ActionResult(True, "snapshot queued", rollback_token="20260928-1000",
                        evidence={"name": "atlas-20260928-1000", "queued_at": queued,
                                  "instances": instances})


def _snap(vm, status="active", name="atlas-20260928-1000"):
    return {"identifier": f"s-{vm}", "name": name, "vmIdentifier": vm, "status": status}


async def test_cloudco_verified_per_instance(net, vps):
    net.on(r"/snapshot$", body={"data": [_snap("vm1"), _snap("vm2")]})
    got = await v.verify("cloudco.snapshot_all", {"reason": "r"}, _snap_result())
    assert got.state is v.State.VERIFIED
    assert set(got.evidence) == {"host-vm1", "host-vm2"}
    _no_secret(got)


async def test_cloudco_partial_success_is_contradicted_listing_failures(net, vps):
    """RA-D2: ok on partial failure used to read as success."""
    net.on(r"/snapshot$", body={"data": [_snap("vm1")]})
    result = _snap_result(vm1="queued", vm2="failed")
    got = await v.verify("cloudco.snapshot_all", {"reason": "r"}, result)
    assert got.state is v.State.CONTRADICTED
    assert "host-vm2" in got.reason and "host-vm1" not in got.reason

    got = await v.verify("cloudco.snapshot_all", {"reason": "r"}, _snap_result())
    assert got.state is v.State.CONTRADICTED and "host-vm2: no snapshot" in got.reason

    net.routes.clear()
    net.on(r"/snapshot$", body={"data": [_snap("vm1"), _snap("vm2", "error")]})
    got = await v.verify("cloudco.snapshot_all", {"reason": "r"}, _snap_result())
    assert got.state is v.State.CONTRADICTED


async def test_cloudco_unknown(net, vps):
    net.on(r"/snapshot$", body={"data": [_snap("vm1")]})
    got = await v.verify("cloudco.snapshot_all", {}, _snap_result(queued_minutes_ago=0))
    assert got.state is v.State.UNKNOWN and "not listed yet" in got.reason

    net.routes.clear()
    net.on(r"/snapshot$", body={"data": [{"name": "atlas-20260928-1000"}]})
    got = await v.verify("cloudco.snapshot_all", {}, _snap_result())
    assert got.state is v.State.UNKNOWN

    net.routes.clear()
    net.on(r"/snapshot$", 401, {"error": True, "message": "bad credentials"})
    got = await v.verify("cloudco.snapshot_all", {}, _snap_result())
    assert got.state is v.State.UNKNOWN
    assert got.failure_class is v.FailureClass.AUTH


async def test_cloudco_an_empty_list_proves_nothing(net, vps):
    """Pagination of /snapshot is unverified: when none of this run's
    snapshots are visible, absence is unknown, not contradicted."""
    net.on(r"/snapshot$", body={"data": [_snap("vm9", name="atlas-20260101-0000")]})
    got = await v.verify("cloudco.snapshot_all", {}, _snap_result(queued_minutes_ago=60))
    assert got.state is v.State.UNKNOWN


async def test_cloudco_raises_is_unknown(net, vps):
    net.on(r"/snapshot$", raises=httpx.ConnectError("refused"))
    got = await v.verify("cloudco.snapshot_all", {}, _snap_result())
    assert got.state is v.State.UNKNOWN
    _no_secret(got)


async def test_cloudco_reconcile(net, vps):
    started = datetime(2026, 9, 28, 10, 0, 30, tzinfo=UTC)
    net.on(r"/vm$", body={"data": [{"identifier": "vm1", "hostname": "a"},
                                   {"identifier": "vm2", "hostname": "b"}]})
    net.on(r"/snapshot$", body={"data": [
        _snap("vm1"), _snap("vm2", name="atlas-20260927-0900")]})
    got = await v.reconcile_unknown("cloudco.snapshot_all", {}, {"at": started})
    assert got.state is v.State.CONTRADICTED and "b: no snapshot" in got.reason


async def test_snapshot_all_records_each_instance(monkeypatch):
    """The action's own evidence names every target, so the read-back can."""
    from atlas.modules import cloudco

    async def get(path, **_p):
        return {"data": [{"identifier": "vm1", "hostname": "a"},
                         {"identifier": "vm2", "hostname": "b"}]}

    async def post(path, body):
        if body["vmIdentifier"] == "vm2":
            raise RuntimeError("quota")
        return {}

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(cloudco, "_get", get)
    monkeypatch.setattr(cloudco, "_post", post)
    monkeypatch.setattr(cloudco.asyncio, "sleep", no_sleep)
    result = await cloudco.snapshot_all("r")
    assert result.ok                                    # unchanged semantics
    legs = result.evidence["instances"]
    assert legs["vm1"]["status"] == "queued" and legs["vm2"]["status"] == "failed"
    assert result.evidence["name"] == f"atlas-{result.rollback_token}"
