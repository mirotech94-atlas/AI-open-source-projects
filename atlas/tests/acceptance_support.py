"""Shared harness for tests/test_acceptance_*.py (docs/acceptance.md).

* `pg`: a throwaway Postgres (ATLAS_TEST_PG_DSN), schema from `init-db`, the
  durable core and every slice switched on, watch-only off. Tests using it
  are SKIPPED without the DSN -- a skip is not a pass, and the matrix says
  which IDs need it.
* `StubWorld` / `install_stub`: the stub publisher. A test-only catalog
  action per platform leg, a receipt notice action, a Tier 2 card action, a
  read-back verifier and an outbox reconciler -- all reading and writing
  this in-memory "provider", never a real one. Registered here only, and
  everything registered is restored afterwards.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any

import pytest

from atlas.kernel import executor, outbox, state, verify
from atlas.kernel.contracts import Action, ActionResult, Tier
from atlas.kernel.flows import content as content_flow
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY

DSN = os.getenv("ATLAS_TEST_PG_DSN", "")
needs_pg = pytest.mark.skipif(
    not DSN, reason="needs ATLAS_TEST_PG_DSN (a throwaway Postgres); see "
                    "docs/acceptance.md")

PUBLISH = "stubpub.publish"
RECEIPT = "stubpub.receipt"
CARD = "stubpub.publish_card"
LEGS = ("instagram", "tiktok")
STUB = content_flow.Publisher(PUBLISH, legs=LEGS, leg_param="platform")
CAPTION = "A short story. Made with AI"


async def _close_pool() -> None:
    pool, state._pool = state._pool, None
    if pool is not None:
        await pool.close()


class Registries:
    """Save every registry a test touches; put it back exactly."""

    def __enter__(self) -> Registries:  # noqa: PYI034
        self.saved = (dict(REGISTRY.actions), dict(REGISTRY.decisions),
                      dict(verify._VERIFIERS), dict(verify._RECONCILERS),
                      dict(outbox.RECONCILERS), dict(content_flow.RECEIPTS),
                      dict(REGISTRY.modules), dict(REGISTRY.checks))
        executor._recent.clear()
        return self

    def __exit__(self, *exc: object) -> None:
        (actions, decisions, verifiers, reconcilers, obx, receipts,
         modules, checks) = self.saved
        for live, kept in ((REGISTRY.actions, actions),
                           (REGISTRY.decisions, decisions),
                           (verify._VERIFIERS, verifiers),
                           (verify._RECONCILERS, reconcilers),
                           (outbox.RECONCILERS, obx),
                           (content_flow.RECEIPTS, receipts),
                           (REGISTRY.modules, modules),
                           (REGISTRY.checks, checks)):
            live.clear()
            live.update(kept)
        executor._recent.clear()


@pytest.fixture(name="registries")
def registries_fixture():
    with Registries() as r:
        yield r


@pytest.fixture(name="pg")
async def pg_fixture(monkeypatch, registries):
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    monkeypatch.setenv("ATLAS_DURABLE_CORE", "on")
    monkeypatch.setenv("ATLAS_DURABLE_FLOWS", "all")
    monkeypatch.delenv("ATLAS_VERIFY_AFTER", raising=False)
    await _close_pool()
    previous = state.set_backend(state.PostgresBackend())
    from atlas import cli
    await cli.init_db()
    await state.execute_sql(
        "TRUNCATE workflows, outbox, leases, proposals, pauses, incidents, "
        "actions_log, observations, checks, properties, effect_claims, lessons, "
        "restores "
        "RESTART IDENTITY CASCADE")
    try:
        yield
    finally:
        await _close_pool()
        state.set_backend(previous)


# ------------------------------------------------------------ the stub world

@dataclass
class StubWorld:
    """The provider side. `behaviour[leg]`: 'ok', 'timeout_after_accept'
    (the post lands, then the call times out), 'refuse' (nothing lands,
    run says no) or 'raise_before' (nothing lands, the call raises)."""

    behaviour: dict[str, str] = field(default_factory=dict)
    posted: dict[str, list[str]] = field(default_factory=dict)
    runs: list[tuple[str, dict]] = field(default_factory=list)
    receipts: list[str] = field(default_factory=list)
    receipt_failures: int = 0
    contradict: set[str] = field(default_factory=set)
    on_run: Any = None

    def count(self, leg: str | None = None) -> int:
        return sum(1 for name, kw in self.runs
                   if name == PUBLISH and (leg is None or kw.get("platform") == leg))


def install_stub(world: StubWorld, *, card_tier: Tier = Tier.ASK) -> None:
    async def publish(media_id: int, caption: str, platform: str) -> ActionResult:
        world.runs.append((PUBLISH, {"media_id": media_id, "platform": platform}))
        if world.on_run is not None:
            await world.on_run(platform)
        how = world.behaviour.get(platform, "ok")
        if how == "refuse":
            return ActionResult(False, f"{platform} refused the post")
        if how == "raise_before":
            raise ConnectionError(f"{platform} unreachable")
        post_id = f"{platform}-{media_id}-{len(world.posted.get(platform, [])) + 1}"
        world.posted.setdefault(platform, []).append(post_id)
        if how == "timeout_after_accept":
            raise TimeoutError(f"{platform} did not answer")
        return ActionResult(True, f"{platform}: posted {post_id}",
                            evidence={"post_id": post_id, "platform": platform})

    async def publish_dry(media_id: int, caption: str, platform: str) -> ActionResult:
        return ActionResult(True, f"would post media {media_id} to {platform}")

    async def undo(token: str) -> ActionResult:
        return ActionResult(True, "undone")

    async def receipt(text: str) -> ActionResult:
        world.runs.append((RECEIPT, {"text": text}))
        if world.receipt_failures > 0:
            world.receipt_failures -= 1
            return ActionResult(False, "slack answered 503")
        world.receipts.append(text)
        return ActionResult(True, "receipt sent", evidence={"ts": str(len(world.receipts))})

    async def receipt_dry(text: str) -> ActionResult:
        return ActionResult(True, "would send the receipt")

    async def card(media_id: int, caption: str) -> ActionResult:
        world.runs.append((CARD, {"media_id": media_id}))
        post_id = f"card-{media_id}"
        world.posted.setdefault("card", []).append(post_id)
        return ActionResult(True, f"posted {post_id}", evidence={"post_id": post_id})

    for action in (
        Action(name=PUBLISH, tier=Tier.AUTO, summary="stub publish one leg",
               run=publish, dry_run=publish_dry, rollback=undo, max_per_hour=50,
               params={"media_id": "id", "caption": "text", "platform": "leg"}),
        Action(name=RECEIPT, tier=Tier.AUTO, summary="stub receipt",
               run=receipt, dry_run=receipt_dry, rollback=undo, max_per_hour=50,
               params={"text": "body"}),
        Action(name=CARD, tier=card_tier, summary="stub card publish", run=card,
               dry_run=publish_dry if card_tier is Tier.AUTO else None,
               rollback=undo, max_per_hour=50,
               params={"media_id": "id", "caption": "text"}),
    ):
        REGISTRY.actions[action.name] = action
        REGISTRY.decisions[action.name] = classify(action)
    content_flow.RECEIPTS[PUBLISH] = RECEIPT

    async def read_back(params: dict, result: ActionResult) -> verify.Verification:
        leg = str(params.get("platform") or "card")
        post_id = str((result.evidence or {}).get("post_id") or "")
        if leg in world.contradict:
            return verify.contradicted(f"{leg} shows no post {post_id}")
        if post_id and post_id in world.posted.get(leg, []):
            return verify.verified(post_id=post_id)
        return verify.unknown(f"{leg}: no id to read back")

    verify._VERIFIERS[PUBLISH] = read_back
    verify._VERIFIERS[CARD] = read_back

    async def reconcile(row: dict) -> tuple[str, dict]:
        leg = str((row.get("params") or {}).get("platform") or "")
        found = world.posted.get(leg, [])
        return ("present", {"post_id": found[-1]}) if found else ("absent", {})

    outbox.RECONCILERS[PUBLISH] = reconcile


def media_params(media_id: int = 7) -> dict:
    return {"media_id": media_id, "caption": CAPTION}


async def workflows(kind: str | None = None) -> list[dict]:
    rows = await state.fetch_all(
        "SELECT * FROM workflows WHERE ($1::text IS NULL OR workflow_type = $1) "
        "ORDER BY id", kind)
    return [dict(r) for r in rows]


async def outbox_rows(workflow_id: int | None = None) -> list[dict]:
    rows = await state.fetch_all(
        "SELECT * FROM outbox WHERE ($1::bigint IS NULL OR workflow_id = $1) "
        "ORDER BY id", workflow_id)
    return [dict(r) for r in rows]


async def audit_events(event: str) -> list[dict]:
    rows = await state.fetch_all(
        "SELECT event, subject, payload FROM actions_log WHERE event = $1 "
        "ORDER BY id", event)
    return [dict(r) for r in rows]


async def lapse(table: str, row_id: int) -> None:
    await state.execute_sql(
        f"UPDATE {table} SET lease_expiry = now() - interval '1 second' "
        "WHERE id = $1", row_id)


async def due_now(outbox_id: int) -> None:
    await state.execute_sql(
        "UPDATE outbox SET next_attempt_at = now() WHERE id = $1", outbox_id)


async def drain_background() -> None:
    """Await every task `kernel.background.spawn` started."""
    from atlas.kernel import background
    for _ in range(5):
        pending = [t for t in list(background._TASKS) if not t.done()]
        if not pending:
            return
        await asyncio.gather(*pending, return_exceptions=True)


# ------------------------------------------------------------------- Slack

SLACK_SECRET = "acceptance-secret"
APPROVER = "UMIRO"


@pytest.fixture(name="slack")
async def slack_fixture(monkeypatch):
    """The real /webhooks/slack/events route, in THIS event loop (asyncpg's
    pool cannot cross into TestClient's), with replies captured. Yields
    (post, replies): `post(payload)` signs and delivers one event."""
    import hashlib
    import hmac
    import json
    import time

    import httpx

    from atlas.api import auth, slack_events

    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password("x" * 14))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "0.0.0.0/0")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SLACK_SECRET)
    monkeypatch.setenv("SLACK_APPROVER_IDS", APPROVER)
    monkeypatch.setattr(slack_events, "_SEEN", {})
    replies: list = []

    async def fake_notify(msg):
        replies.append(msg)
        return list(replies_to.get("used", ["slack"]))

    replies_to: dict = {}
    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)
    from atlas.api.main import app
    transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 51000))

    async def post(payload: dict, retry: str = "") -> httpx.Response:
        raw = json.dumps(payload).encode()
        ts = str(int(time.time()))
        sig = "v0=" + hmac.new(SLACK_SECRET.encode(),
                               b"v0:" + ts.encode() + b":" + raw,
                               hashlib.sha256).hexdigest()
        headers = {"Content-Type": "application/json",
                   "X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}
        if retry:
            headers["X-Slack-Retry-Num"] = retry
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://atlas.test") as client:
            return await client.post("/webhooks/slack/events", content=raw,
                                     headers=headers)

    post.replies_to = replies_to
    yield post, replies


def slack_message(text: str, *, event_id: str = "Ev0001", channel: str = "C0GENERAL",
                  ts: str = "1700000000.000100", user: str = APPROVER,
                  **extra: Any) -> dict:
    return {"type": "event_callback", "event_id": event_id,
            "event": {"type": "message", "text": text, "user": user,
                      "channel": channel, "ts": ts, **extra}}


# -------------------------------------------------------------------- checks

async def seed_check(module: str, check: str, health: str, detail: str, *,
                     hours: float, ok_before: bool = True) -> None:
    """A check that has read `health` for `hours` (after an OK reading when
    `ok_before`), as `kernel/fixes.lasting` finds it. Registered checks are
    cleared for the test (restored by `registries`), so the sweep's "no
    longer registered" filter does not drop it."""
    REGISTRY.checks.clear()
    prop = await state.fetch_one(
        """INSERT INTO properties (name, title, kind) VALUES ($1, $1, 'infra')
           ON CONFLICT (name) DO UPDATE SET title = EXCLUDED.title RETURNING id""",
        module)
    row = await state.fetch_one(
        """INSERT INTO checks (property_id, name, interval_s) VALUES ($1, $2, 300)
           ON CONFLICT (property_id, name) DO UPDATE SET interval_s = 300
           RETURNING id""", prop["id"], check)
    if ok_before:
        await state.execute_sql(
            """INSERT INTO observations (check_id, at, health, detail)
               VALUES ($1, now() - make_interval(secs => $2), 'ok', 'fine')""",
            row["id"], (hours + 1) * 3600.0)
    await state.execute_sql(
        """INSERT INTO observations (check_id, at, health, detail)
           VALUES ($1, now() - make_interval(secs => $2), $3, $4)""",
        row["id"], hours * 3600.0, health, detail)


async def observe_now(module: str, check: str, health: str, detail: str = "") -> None:
    row = await state.fetch_one(
        """SELECT c.id FROM checks c JOIN properties p ON p.id = c.property_id
            WHERE p.name = $1 AND c.name = $2""", module, check)
    await state.execute_sql(
        "INSERT INTO observations (check_id, health, detail) VALUES ($1, $2, $3)",
        row["id"], health, detail)


# ----------------------------------------------- Slack, in-suite (no database)
# The same harness as tests/test_slack_events.py (its `client`/`decisions`),
# under names this suite can import without shadowing test parameters.

@pytest.fixture(name="client")
def client_fixture(monkeypatch, memory_backend):
    from fastapi.testclient import TestClient

    from atlas.api import auth
    from tests.test_slack_events import SECRET
    monkeypatch.setenv("SESSION_SECRET", "s" * 40)
    monkeypatch.setenv("DASHBOARD_PASSWORD_HASH", auth.hash_password("x" * 14))
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "0.0.0.0/0")
    monkeypatch.setenv("SLACK_SIGNING_SECRET", SECRET)
    monkeypatch.setenv("SLACK_APPROVER_IDS", APPROVER)
    from atlas.api import slack_events
    from atlas.api.main import app
    monkeypatch.setattr(slack_events, "_SEEN", {})

    async def no_boot() -> None:
        return None

    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    with TestClient(app, client=("127.0.0.1", 51000)) as c:
        yield c


@pytest.fixture(name="decisions")
def decisions_fixture(monkeypatch):
    """Every decide() call and every reply, captured; nothing is decided."""
    decided: list = []
    replied: list = []

    class Verdict:
        def __init__(self, pid):
            self.pid = pid

        def receipt(self):
            return f"Proposal #{self.pid} decided."

    async def fake_decide(pid, verdict, via):
        decided.append((pid, verdict, via))
        return Verdict(pid)

    async def fake_notify(msg):
        replied.append(msg)
        return ["slack"]

    monkeypatch.setattr("atlas.kernel.approvals.decide", fake_decide)
    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)
    return decided, replied
