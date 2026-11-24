"""The comment-keyword -> DM funnel end to end on a real Postgres, with a
fake Graph API (httpx.MockTransport): the Tier 2 card and its exact text,
the set through the executor, the sweep (whole-word matching, own comments
skipped, once per person, cursor pagination), the send (the exact
private-reply payload, the public reply without the link, the effect claims),
the daily cap, quiet hours, a hash mismatch, a 429, a 401, a policy error,
the read-back, the weekly paragraph, the command hook and the dashboard API.

Skipped unless ATLAS_TEST_PG_DSN points at a throwaway database
(tests/acceptance_support.py). It TRUNCATEs the funnel tables.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from atlas.kernel import comms, executor, funnels, intent, state
from atlas.kernel.contracts import ActionResult, FailureClass, Health, Tier
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY
from atlas.modules import instagram
from atlas.modules.instagram import funnel, readback
from tests.acceptance_support import DSN, Registries, needs_pg

pytestmark = needs_pg

TOKEN = "EAAB-main-token-SECRET-0123456789"
IG = "17841400000000001"
TABLES = ("ig_funnels", "ig_funnel_cursors", "ig_funnel_sends")
DM = "Here's the free roadmap: https://academy-store.myshopify.example/roadmap"
PUBLIC = "Sent you a DM \U0001f4e9"
NOON_NY = datetime(2026, 9, 30, 16, 0, tzinfo=UTC)      # 12:00 New York (EDT)
NIGHT_NY = datetime(2026, 9, 30, 3, 0, tzinfo=UTC)      # 23:00 New York
T = "2026-09-30T15:"


def comment(cid: str, text: str, who: str = "u1", username: str = "alice",
            minute: int = 10) -> dict[str, Any]:
    return {"id": cid, "text": text, "username": username,
            "from": {"id": who, "username": username},
            "timestamp": f"{T}{minute:02d}:00+0000"}


class FakeGraph:
    """Routes by path. Records every request; behaviour switches per test."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.media = [{"id": "m1", "timestamp": f"{T}00:00+0000"}]
        self.comments: dict[str, list[list[dict[str, Any]]]] = {"m1": [[]]}
        self.replies: dict[str, list[dict[str, Any]]] = {}
        self.private_status = 200
        self.private_body: dict[str, Any] = {"recipient_id": "u1", "message_id": "mid-1"}
        self.public_status = 200
        self.comments_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path.removeprefix("/v21.0/")
        params = dict(request.url.params)
        if request.method == "GET" and path == IG:
            return httpx.Response(200, json={"id": IG, "username": "flycheaphp"})
        if request.method == "GET" and path == f"{IG}/media":
            return httpx.Response(200, json={"data": self.media})
        if request.method == "GET" and path.endswith("/comments"):
            if self.comments_status != 200:
                return httpx.Response(self.comments_status, json={
                    "error": {"message": f"no ({TOKEN})", "code": 190}})
            media_id = path.split("/")[0]
            pages = self.comments.get(media_id, [[]])
            index = int(params.get("after", "p0")[1:])
            body: dict[str, Any] = {"data": pages[index] if index < len(pages) else []}
            if index + 1 < len(pages):
                body["paging"] = {"cursors": {"after": f"p{index + 1}"},
                                  "next": f"https://graph.facebook.com/x?access_token={TOKEN}"}
            return httpx.Response(200, json=body)
        if request.method == "GET" and path.endswith("/replies"):
            return httpx.Response(200, json={"data": self.replies.get(
                path.split("/")[0], [])})
        if request.method == "POST" and path.endswith("/replies"):
            if self.public_status != 200:
                return httpx.Response(self.public_status, json={
                    "error": {"message": "nope", "code": 4}})
            cid = path.split("/")[0]
            rid = f"r-{cid}"
            self.replies.setdefault(cid, []).append({"id": rid, "text": params.get("message")})
            return httpx.Response(200, json={"id": rid})
        if request.method == "POST" and path == f"{IG}/messages":
            if self.private_status != 200:
                return httpx.Response(self.private_status, json=self.private_body)
            return httpx.Response(200, json=self.private_body)
        return httpx.Response(404, json={"error": {"message": "unknown", "code": 803}})


@pytest.fixture
def graph(monkeypatch) -> FakeGraph:
    fake = FakeGraph()
    real = httpx.AsyncClient
    fake.real = real
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: real(transport=httpx.MockTransport(fake.handler)))
    return fake


@pytest.fixture
def clock(monkeypatch):
    """Hold the funnel's clock at a moment; every `now` the tick reads is it."""
    def hold(when: datetime) -> datetime:
        monkeypatch.setattr(funnel, "_clock", lambda: when)
        return when
    hold(NOON_NY)
    return hold


@pytest.fixture
async def pg(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    monkeypatch.setenv("INSTAGRAM_ACCOUNT_ID", IG)
    monkeypatch.setenv("INSTAGRAM_TOKEN", TOKEN)
    monkeypatch.delenv("ATLAS_VERIFY_AFTER", raising=False)
    pool, state._pool = state._pool, None
    if pool is not None:
        await pool.close()
    previous = state.set_backend(state.PostgresBackend())
    from atlas import cli
    await cli.init_db()
    await state.execute_sql(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
    await state.execute_sql("TRUNCATE actions_log, pauses, proposals, effect_claims, "
                            "lessons, decision_log RESTART IDENTITY CASCADE")
    try:
        with Registries():
            REGISTRY.modules["instagram"] = instagram.MODULE
            for action in instagram.MODULE.actions():
                REGISTRY.actions[action.name] = action
                REGISTRY.decisions[action.name] = classify(action)
            yield
    finally:
        state.set_backend(previous)
        pool, state._pool = state._pool, None
        if pool is not None:
            await pool.close()


@pytest.fixture
def sent(monkeypatch):
    out: list[comms.Outbound] = []

    async def notify(msg):
        out.append(msg)
        return ["slack"]

    monkeypatch.setattr(comms, "notify", notify)
    # approvals binds `notify` at import; the card path goes through it.
    from atlas.kernel import approvals
    monkeypatch.setattr(approvals, "notify", notify)
    monkeypatch.setenv("APPROVALS_SLACK", "on")      # the Slack card is opt-in now
    return out


async def set_funnel(**over: Any) -> int:
    """A funnel as the approved card would write it (through the executor,
    as `approvals.decide` runs an approved Tier 2)."""
    params = {"account": "main", "keyword": "asset", "public_reply": PUBLIC,
              "dm_text": DM, **over}
    result = await executor.execute(executor.ExecutionRequest(
        action=funnel.SET, params=params, reason="test",
        approved_by="slack:UOWNER"))
    assert result.ok, result.detail
    fid = int(result.evidence["funnel_id"])
    # The approval is stamped by the module clock (held at NOON_NY by the
    # `clock` fixture); the fake comments are the morning before it, and a
    # comment counts only after the approval. Approve at dawn.
    await state.execute_sql("UPDATE ig_funnels SET approved_at = $2 WHERE id = $1",
                            fid, NOON_NY - timedelta(hours=10))
    return fid


async def sends() -> list[dict[str, Any]]:
    rows = await state.fetch_all("SELECT * FROM ig_funnel_sends ORDER BY id")
    return [dict(r) for r in rows]


def private_posts(graph: FakeGraph) -> list[httpx.Request]:
    return [r for r in graph.requests
            if r.method == "POST" and r.url.path.endswith(f"{IG}/messages")]


# ---------------------------------------------------------------- the card

async def test_the_tier2_card_carries_the_exact_texts_and_nothing_runs(pg, sent):
    from atlas.kernel.approvals import propose
    params = {"account": "main", "keyword": "Asset", "public_reply": PUBLIC,
              "dm_text": DM, "daily_cap": 150}
    pid = await propose(funnel.SET, params, reason="test card")
    card = sent[-1]
    assert f"#{pid}" in card.title and card.topic == "instagram"
    assert PUBLIC in card.body and DM in card.body
    assert "“asset” (whole word, any case)" in card.body
    assert "150 DMs a day" in card.body and "22:00–08:00 America/New_York" in card.body
    assert "`instagram.funnel_set` · Tier 2" in card.body
    assert await state.fetch_one("SELECT count(*) AS n FROM ig_funnels") is not None
    assert (await state.fetch_one("SELECT count(*) AS n FROM ig_funnels"))["n"] == 0


async def test_a_card_with_a_link_in_the_public_reply_is_never_raised(pg, sent):
    from atlas.kernel.approvals import Unrunnable, propose
    with pytest.raises(Unrunnable):
        await propose(funnel.SET, {"account": "main", "keyword": "asset",
                                   "public_reply": "get it at flycheap.example",
                                   "dm_text": DM}, reason="test")
    assert not sent


async def test_the_model_facing_catalog_never_offers_funnel_set(pg):
    assert funnel.SET not in intent.catalog()
    assert funnel.REPLY in intent.catalog()


# ---------------------------------------------------------------- the set

async def test_set_writes_the_row_with_its_hash_and_a_second_set_rewrites_it(pg):
    fid = await set_funnel()
    row = await funnel.get_funnel(fid)
    assert row["text_hash"] == funnel.text_hash(PUBLIC, DM)
    assert row["active"] and row["approved_at"] is not None
    assert row["daily_cap"] == 200 and row["timezone"] == "America/New_York"
    again = await set_funnel(dm_text=DM + " (v2)", daily_cap=50)
    assert again == fid
    row = await funnel.get_funnel(fid)
    assert row["dm_text"].endswith("(v2)") and row["daily_cap"] == 50
    assert row["text_hash"] == funnel.text_hash(PUBLIC, DM + " (v2)")


async def test_pause_and_its_rollback_and_the_set_rollback(pg):
    fid = await set_funnel()
    r = await executor.execute(executor.ExecutionRequest(
        action=funnel.PAUSE, params={"funnel_id": fid}, reason="test"))
    assert r.ok and not (await funnel.get_funnel(fid))["active"]
    back = await executor.rollback(funnel.PAUSE, r.rollback_token)
    assert back.ok and (await funnel.get_funnel(fid))["active"]
    undone = await executor.rollback(funnel.SET, str(fid))
    assert undone.ok and not (await funnel.get_funnel(fid))["active"]


# -------------------------------------------------------------- the sweep

async def test_sweep_queues_whole_word_matches_once_per_person_and_skips_own(pg, graph, clock):
    fid = await set_funnel()
    graph.comments["m1"] = [[
        comment("c1", "ASSET please"),                          # yes
        comment("c2", "assets?", who="u2", username="bob"),     # substring: no
        comment("c3", "asset\U0001f525", who="u3", username="cy"),  # emoji-adjacent
        comment("c4", "asset", who="u1", username="alice"),     # same person: once
        comment("c5", "asset", who=IG, username="flycheaphp"),  # own comment
        comment("c6", "reasset", who="u6", username="dan"),     # no
        comment("c7", "the Asset!", who="u7", username="eve"),  # yes
    ]]
    ready, summary = await funnel.sweep(clock(NOON_NY))
    assert [f["id"] for f in ready] == [fid]
    assert summary["queued"] == 3 and summary["errors"] == []
    rows = await sends()
    assert [(r["ig_user_id"], r["comment_id"], r["status"]) for r in rows] == [
        ("u1", "c1", "queued"), ("u3", "c3", "queued"), ("u7", "c7", "queued")]
    # Nothing was sent by the sweep: it only reads and queues.
    assert not any(r.method == "POST" for r in graph.requests)


async def test_sweep_pages_by_cursor_and_moves_the_watermark(pg, graph, clock):
    fid = await set_funnel()
    graph.comments["m1"] = [
        [comment("c1", "asset", minute=30)],
        [comment("c2", "asset", who="u2", username="bob", minute=20)],
    ]
    await funnel.sweep(clock(NOON_NY))
    asked = [r for r in graph.requests if r.url.path.endswith("/comments")]
    assert [dict(r.url.params).get("after") for r in asked] == [None, "p1"]
    # never the `next` URL, which carried the token
    assert all(TOKEN not in str(r.url) for r in graph.requests)
    assert {r["comment_id"] for r in await sends()} == {"c1", "c2"}
    cur = await state.fetch_one(
        "SELECT newest_at FROM ig_funnel_cursors WHERE funnel_id = $1", fid)
    assert cur["newest_at"] == datetime(2026, 9, 30, 15, 30, tzinfo=UTC)
    # A second sweep sees nothing new and stops on the first page.
    graph.requests.clear()
    _, summary = await funnel.sweep(clock(NOON_NY))
    assert summary["queued"] == 0
    asked = [r for r in graph.requests if r.url.path.endswith("/comments")]
    assert len(asked) == 1


async def test_sweep_records_a_refused_login_and_never_queues(pg, graph, clock):
    fid = await set_funnel()
    graph.comments_status = 401
    ready, summary = await funnel.sweep(clock(NOON_NY))
    assert ready == [] and summary["auth_refused"] == [("main", 401)]
    row = await funnel.get_funnel(fid)
    assert "HTTP 401" in row["last_error"] and TOKEN not in row["last_error"]


# --------------------------------------------------------------- the send

async def test_the_send_replies_publicly_then_privately_with_the_exact_payload(pg, graph, sent, clock):
    fid = await set_funnel()
    graph.comments["m1"] = [[comment("c1", "asset")]]
    summary = await funnels.tick(clock(NOON_NY))
    assert summary["sent"] == 1 and summary["queued"] == 1, summary
    posts = [r for r in graph.requests if r.method == "POST"]
    assert [r.url.path for r in posts] == ["/v21.0/c1/replies", f"/v21.0/{IG}/messages"]
    public, private = posts
    assert dict(public.url.params)["message"] == PUBLIC
    assert "http" not in dict(public.url.params)["message"]
    assert json.loads(private.content) == {
        "recipient": {"comment_id": "c1"}, "message": {"text": DM}}
    assert private.headers["authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(private.url)
    row = (await sends())[0]
    assert row["status"] == "sent" and row["public_reply_id"] == "r-c1"
    assert row["private_message_id"] == "mid-1" and row["sent_at"] is not None
    claims = await state.fetch_all(
        "SELECT key, status FROM effect_claims WHERE action = $1 ORDER BY key",
        funnel.REPLY)
    assert [(c["key"], c["status"]) for c in claims] == [
        (f"funnel:{fid}:comment:c1:private", "sent"),
        (f"funnel:{fid}:comment:c1:public", "sent")]
    log = await state.fetch_all(
        "SELECT event FROM actions_log WHERE subject = $1 ORDER BY id", funnel.REPLY)
    assert [r["event"] for r in log] == ["execute.dry_run", "execute.start",
                                         "execute.finish"]
    # A second tick sends nothing again: once per person, ever.
    graph.requests.clear()
    graph.comments["m1"] = [[comment("c1", "asset"), comment("c9", "asset again")]]
    summary = await funnels.tick(clock(NOON_NY))
    assert summary["sent"] == 0 and summary["queued"] == 0
    assert not private_posts(graph)


async def test_no_send_without_a_queued_match_or_an_approved_funnel(pg, graph, clock):
    fid = await set_funnel()
    r = await executor.execute(executor.ExecutionRequest(
        action=funnel.REPLY, reason="test",
        params={"account": "main", "funnel_id": fid, "comment_id": "never-commented"}))
    assert not r.ok and "not a recorded keyword match" in r.detail
    assert r.dispatched is False and not private_posts(graph)
    await state.execute_sql("UPDATE ig_funnels SET approved_at = NULL WHERE id = $1", fid)
    graph.comments["m1"] = [[comment("c1", "asset")]]
    summary = await funnels.tick(clock(NOON_NY))
    assert summary["funnels"] == 0 and summary["sent"] == 0
    assert not private_posts(graph)
    r = await executor.execute(executor.ExecutionRequest(
        action=funnel.REPLY, reason="test",
        params={"account": "main", "funnel_id": 999, "comment_id": "c1"}))
    assert not r.ok and "no funnel #999" in r.detail


async def test_a_text_edited_behind_the_card_is_refused_by_the_hash(pg, graph, clock):
    fid = await set_funnel()
    graph.comments["m1"] = [[comment("c1", "asset")]]
    await funnel.sweep(clock(NOON_NY))
    await state.execute_sql(
        "UPDATE ig_funnels SET dm_text = 'buy now: https://evil.example' WHERE id = $1",
        fid)
    summary = await funnels.tick(clock(NOON_NY))
    assert summary["sent"] == 0 and "hash mismatch" in "".join(summary["errors"])
    assert not private_posts(graph)
    r = await executor.execute(executor.ExecutionRequest(
        action=funnel.REPLY, reason="test",
        params={"account": "main", "funnel_id": fid, "comment_id": "c1"}))
    assert not r.ok and "hash mismatch" in r.detail
    assert (await funnel.check_funnels()).health is Health.DEGRADED


async def test_quiet_hours_queue_the_send_until_eight(pg, graph, clock):
    await set_funnel()
    graph.comments["m1"] = [[comment("c1", "asset")]]
    summary = await funnels.tick(clock(NIGHT_NY))
    assert summary["queued"] == 1 and summary["sent"] == 0
    assert summary["stopped"]["1"].startswith("quiet hours until 08:00")
    assert not private_posts(graph)
    assert (await sends())[0]["status"] == "queued"
    dry = await funnel.funnel_reply_dry("main", 1, "c1", now=clock(NIGHT_NY))
    assert not dry.ok and "quiet hours" in dry.detail
    summary = await funnels.tick(clock(NOON_NY))
    assert summary["sent"] == 1


async def test_the_daily_cap_holds_the_rest_for_tomorrow(pg, graph, clock):
    fid = await set_funnel(daily_cap=2)
    graph.comments["m1"] = [[comment(f"c{i}", "asset", who=f"u{i}", username=f"p{i}")
                             for i in range(4)]]
    summary = await funnels.tick(clock(NOON_NY))
    assert summary["sent"] == 2 and summary["stopped"][str(fid)] == "daily cap 2 reached"
    assert [r["status"] for r in await sends()] == ["sent", "sent", "queued", "queued"]
    obs = await funnel.check_funnels()
    assert obs.health is Health.OK and "2 sent today, 0 left of 2" in obs.detail
    assert obs.value == 2.0
    summary = await funnels.tick(clock(NOON_NY + timedelta(days=1)))
    assert summary["sent"] == 2


async def test_a_429_backs_the_send_off_and_keeps_the_public_reply(pg, graph, clock):
    fid = await set_funnel()
    graph.comments["m1"] = [[comment("c1", "asset")]]
    graph.private_status = 429
    graph.private_body = {"error": {"message": "too many", "code": 4}}
    summary = await funnels.tick(clock(NOON_NY))
    assert summary["sent"] == 0 and summary["stopped"][str(fid)] == "rate limited; backing off"
    row = (await sends())[0]
    assert row["status"] == "queued" and row["attempts"] == 1
    assert row["public_reply_id"] == "r-c1"
    assert row["next_attempt_at"] > NOON_NY + timedelta(seconds=30)
    assert "code 4" in row["error"]
    claims = await state.fetch_all(
        "SELECT key, status FROM effect_claims WHERE action = $1 ORDER BY key",
        funnel.REPLY)
    assert [(c["key"].rsplit(":", 1)[1], c["status"]) for c in claims] == [
        ("public", "sent")]                                 # private: released
    # Not due yet: nothing is retried this tick.
    graph.private_status = 200
    graph.requests.clear()
    assert (await funnels.tick(clock(NOON_NY + timedelta(seconds=10))))["sent"] == 0
    # Due: only the private leg is sent again; the public reply is not repeated.
    summary = await funnels.tick(clock(NOON_NY + timedelta(hours=1)))
    assert summary["sent"] == 1
    assert [r.url.path for r in graph.requests if r.method == "POST"] == [
        f"/v21.0/{IG}/messages"]


async def test_a_401_pauses_the_accounts_credential(pg, graph, sent, clock):
    from atlas.kernel import pause
    fid = await set_funnel()
    graph.comments["m1"] = [[comment("c1", "asset")]]
    graph.private_status = 401
    graph.private_body = {"error": {"message": f"bad token {TOKEN}", "code": 190}}
    summary = await funnels.tick(clock(NOON_NY))
    assert summary["stopped"][str(fid)] == "login refused; the account is paused"
    held = await pause.active_for("instagram", "main")
    assert held is not None and held.scope == "instagram:main"
    assert held.paused_by == pause.AUTH_REFUSED_BY
    assert any("login was refused" in m.body for m in sent)
    assert TOKEN not in json.dumps([m.body for m in sent])
    # The executor now refuses the account's sends; the row waits.
    graph.private_status = 200
    summary = await funnels.tick(clock(NOON_NY + timedelta(hours=2)))
    assert summary["sent"] == 0 and summary["stopped"][str(fid)].startswith("held: not run")


async def test_a_policy_error_stops_the_funnel_and_tells_owner(pg, graph, sent, clock):
    fid = await set_funnel()
    graph.comments["m1"] = [[comment("c1", "asset"),
                             comment("c2", "asset", who="u2", username="bob")]]
    graph.private_status = 400
    graph.private_body = {"error": {"message": "temporarily blocked for policy "
                                               "violations", "code": 368}}
    summary = await funnels.tick(clock(NOON_NY))
    assert summary["sent"] == 0
    assert summary["stopped"][str(fid)] == "policy error; the funnel is stopped"
    row = await funnel.get_funnel(fid)
    assert row["stopped_at"] is not None and "code 368" in row["stopped_why"]
    assert any(f"funnel #{fid} stopped" in m.title for m in sent)
    assert len(private_posts(graph)) == 1                # the second row never tried
    # Stopped: nothing more, whatever the next tick sees.
    graph.private_status = 200
    assert (await funnels.tick(clock(NOON_NY + timedelta(hours=1))))["funnels"] == 0
    obs = await funnel.check_funnels()
    assert obs.health is Health.DEGRADED and "stopped: #1" in obs.detail
    # Setting it again clears the stop.
    await set_funnel()
    assert (await funnel.get_funnel(fid))["stopped_at"] is None


async def test_an_unreachable_provider_keeps_the_claim_for_a_person(pg, graph, clock):
    fid = await set_funnel()
    graph.comments["m1"] = [[comment("c1", "asset")]]
    await funnel.sweep(clock(NOON_NY))

    def boom(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/messages"):
            raise httpx.ReadTimeout("slow", request=request)
        return graph.handler(request)

    import unittest.mock as um
    with um.patch.object(httpx, "AsyncClient",
                         lambda *a, **k: graph.real(transport=httpx.MockTransport(boom))):
        summary = await funnels.tick(clock(NOON_NY))
    assert summary["sent"] == 0
    row = (await sends())[0]
    assert row["status"] == "needs_human" and "unreachable" in row["error"]
    assert "hash" not in row["error"]
    claim = await state.fetch_one(
        "SELECT status FROM effect_claims WHERE key = $1",
        f"funnel:{fid}:comment:c1:private")
    assert claim["status"] == "sending"
    # And a later tick never sends it again by itself.
    assert (await funnels.tick(clock(NOON_NY + timedelta(hours=1))))["sent"] == 0


async def test_a_comment_older_than_seven_days_is_never_replied_to(pg, graph, clock):
    await set_funnel()
    graph.comments["m1"] = [[comment("c1", "asset")]]
    await funnel.sweep(clock(NOON_NY))
    summary = await funnels.tick(clock(NOON_NY + timedelta(days=8)))
    assert summary["sent"] == 0 and "older than 7 days" in summary["stopped"]["1"]
    assert not private_posts(graph)


# ------------------------------------------------------------- read-back

async def test_readback_verified_contradicted_unknown(pg, graph, clock):
    await set_funnel()
    graph.comments["m1"] = [[comment("c1", "asset")]]
    await funnels.tick(clock(NOON_NY))
    params = {"account": "main", "funnel_id": 1, "comment_id": "c1"}
    ok = ActionResult(True, "sent", evidence={"public_reply_id": "r-c1",
                                              "private_message_id": "mid-1"})
    v = await readback.verify_funnel_reply(params, ok)
    assert v.state.value == "verified", v
    assert v.evidence["public_reply_id"] == "r-c1"
    gone = ActionResult(True, "sent", evidence={"public_reply_id": "r-missing",
                                                "private_message_id": "mid-1"})
    v = await readback.verify_funnel_reply(params, gone)
    assert v.state.value == "contradicted" and "no reply r-missing" in v.reason
    half = ActionResult(True, "sent", evidence={"public_reply_id": "r-c1"})
    v = await readback.verify_funnel_reply(params, half)
    assert v.state.value == "contradicted" and "no private reply id" in v.reason
    v = await readback.verify_funnel_reply(params, ActionResult(True, "sent"))
    assert v.state.value == "unknown"
    graph.comments_status = 401

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "down", "code": 2}})

    import unittest.mock as um
    with um.patch.object(httpx, "AsyncClient",
                         lambda *a, **k: graph.real(transport=httpx.MockTransport(refuse))):
        v = await readback.verify_funnel_reply(params, ok)
    assert v.state.value == "unknown" and v.failure_class is FailureClass.TRANSIENT


# ---------------------------------------------------------------- weekly

async def test_the_weekly_paragraph_counts_sends_people_errors_and_says_unknown(pg, graph, clock):
    fid = await set_funnel()
    graph.comments["m1"] = [[comment("c1", "asset"),
                             comment("c2", "asset", who="u2", username="bob")]]
    await funnels.tick(clock(NOON_NY))
    text = await funnel.weekly_section(NOON_NY + timedelta(hours=1))
    assert text.startswith("*Instagram comment funnels*")
    assert f"#{fid} main “asset”: 2 DM(s) to 2 people, 0 error(s), 0 waiting" in text
    assert "Conversion: unknown (no join key" in text
    assert "(Read Wed 30 Sep 17:00 UTC.)" in text
    await state.execute_sql("TRUNCATE ig_funnels CASCADE")
    assert "No funnels defined." in await funnel.weekly_section(NOON_NY)


# --------------------------------------------------------------- command

async def test_the_slack_command_raises_the_card_and_pause_runs(pg, sent):
    line = ('funnel main asset "Sent you a DM \U0001f4e9" '
            '"Here’s the free roadmap: https://academy-store.myshopify.example/r" '
            'cap=100')
    out = await intent.act(line, channel="slack", who="UOWNER")
    assert out.ok and not out.ran and out.proposal_id and out.action == funnel.SET
    card = sent[-1]
    assert "Sent you a DM \U0001f4e9" in card.body
    assert "Here's the free roadmap: https://academy-store.myshopify.example/r" in card.body
    assert "100 DMs a day" in card.body
    row = await state.fetch_one("SELECT action, params, status FROM proposals WHERE id = $1",
                                out.proposal_id)
    assert row["action"] == funnel.SET and row["status"] == "pending"
    assert json.loads(row["params"])["daily_cap"] == "100"
    # The same line again returns the pending card, not a second one.
    again = await intent.act(line, channel="slack", who="UOWNER")
    assert again.proposal_id == out.proposal_id
    # A public reply with a link never becomes a card.
    bad = await intent.act('funnel main asset "see flycheap.example" "x"',
                           channel="slack", who="UOWNER")
    assert not bad.ok and "must not carry a link" in bad.spoken
    fid = await set_funnel()
    listed = await intent.act("funnel list", channel="slack", who="UOWNER")
    assert f"#{fid} main “asset”" in listed.spoken and "active" in listed.spoken
    paused = await intent.act(f"funnel pause {fid}", channel="slack", who="UOWNER")
    assert paused.ran and not (await funnel.get_funnel(fid))["active"]
    # Not from a channel that cannot prove it is him.
    refused = await intent.act("funnel list", channel="sms", who="+1555")
    assert not refused.ok and "can't act on that from here" in refused.spoken


# ----------------------------------------------------------- dashboard API

async def test_the_dashboard_api_lists_and_raises_the_same_card(pg, sent):
    from fastapi import FastAPI

    from atlas.api import auth
    from atlas.api.ig_funnels import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[auth.require] = lambda: auth.Session("owner", 0.0)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://atlas") as c:
        assert (await c.post("/api/instagram/funnels", data={"x": "y"})).status_code == 415
        bad = await c.post("/api/instagram/funnels",
                           json={"account": "main", "keyword": "asset",
                                 "public_reply": "www.flycheap.example", "dm_text": DM})
        assert bad.status_code == 422 and "must not carry a link" in bad.json()["detail"]
        r = await c.post("/api/instagram/funnels",
                         json={"account": "main", "keyword": "asset",
                               "public_reply": PUBLIC, "dm_text": DM, "bogus": 1})
        body = r.json()
        assert body["ok"] and body["proposal_id"] and PUBLIC in body["card"]
        assert (await state.fetch_one("SELECT count(*) AS n FROM ig_funnels"))["n"] == 0
        fid = await set_funnel()
        listed = (await c.get("/api/instagram/funnels")).json()
        assert listed["funnels"][0]["id"] == fid and listed["funnels"][0]["state"] == "active"
        assert "text_hash" not in listed["funnels"][0]
        # No pause route: nothing on the dashboard reaches the executor.
        assert (await c.post(f"/api/instagram/funnels/{fid}/pause")).status_code == 404


# --------------------------------------------------------------- registry

def test_the_actions_are_tiered_in_code():
    actions = {a.name: a for a in instagram.MODULE.actions()}
    assert actions[funnel.SET].tier is Tier.ASK and actions[funnel.SET].card_only
    assert actions[funnel.SET].card_detail is funnel.card_detail_set
    assert classify(actions[funnel.SET]).tier is Tier.ASK
    assert actions[funnel.PAUSE].tier is Tier.AUTO
    assert classify(actions[funnel.REPLY]).tier is Tier.AUTO
    assert actions[funnel.REPLY].rollback is funnel.funnel_reply_rollback
    assert set(actions[funnel.REPLY].params) == {"account", "funnel_id", "comment_id"}
    assert "funnels" in {c.name for c in instagram.MODULE.checks()}
