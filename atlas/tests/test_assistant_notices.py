"""Proactive updates: the assistant may tell the owner one short, code-built note.

the owner, 2026-11-19. Tier 1 `assistant.update_owner`: a Slack DM to his own id
through `comms.notify`, at most 3 a day and 1 an hour, never in QUIET_HOURS,
never above NORMAL (so it never pages), text built by code from fixed fields.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from atlas.kernel import comms, executor
from atlas.kernel.contracts import Severity, Tier
from atlas.modules.assistant import notices

NOW = datetime(2026, 11, 19, 15, 0, tzinfo=UTC)


class FakeStore:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}
        self.state: dict[str, str] = {}
        self.n = 0

    async def insert(self, kind, ref, text, key, now, window):
        for r in self.rows.values():
            if r["dedupe_key"] == key and r["created_at"] > now - window:
                return None
        self.n += 1
        self.rows[self.n] = {"id": self.n, "kind": kind, "ref": ref, "text": text,
                             "dedupe_key": key, "status": "pending", "attempts": 0,
                             "created_at": now, "updated_at": now, "sent_at": None}
        return self.n

    async def peek(self, nid):
        return dict(self.rows[nid]) if nid in self.rows else None

    async def waiting(self, limit, now):
        return [i for i, r in self.rows.items() if r["status"] == "pending"
                and (not r["attempts"] or r["updated_at"] < now - notices.RETRY_WAIT)][:limit]

    async def claim(self, nid, now):
        r = self.rows.get(nid)
        if not r or r["status"] != "pending":
            return None
        r["status"] = "sending"
        r["attempts"] += 1
        return dict(r)

    async def sent(self, nid, now):
        self.rows[nid].update(status="sent", sent_at=now)

    async def failed(self, nid, reason, now):
        r = self.rows[nid]
        r["updated_at"] = now
        r["status"] = "failed" if r["attempts"] >= notices.MAX_ATTEMPTS else "pending"

    async def sent_since(self, since):
        return sum(1 for r in self.rows.values()
                   if r["sent_at"] and r["sent_at"] > since)

    async def state_get(self, key):
        return self.state.get(key)

    async def state_set(self, key, value, now):
        self.state[key] = value


@pytest.fixture
def store():
    s = FakeStore()
    old = notices.use_store(s)
    yield s
    notices.use_store(old)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SLACK_APPROVER_IDS", "U0OWNER")
    monkeypatch.delenv("SLACK_OWNER_ID", raising=False)
    monkeypatch.delenv("QUIET_HOURS", raising=False)
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    monkeypatch.setattr(notices, "_now", lambda: NOW)


@pytest.fixture
def sent(monkeypatch):
    out: list[comms.Outbound] = []

    async def fake_notify(msg):
        out.append(msg)
        return ["slack"]
    monkeypatch.setattr(comms, "notify", fake_notify)
    return out


async def test_action_is_tier1_with_dry_run_and_rollback():
    (a,) = notices.actions()
    assert a.name == "assistant.update_owner" == notices.ACTION
    assert a.tier is Tier.AUTO and a.dry_run and a.rollback and a.vet
    assert a.max_per_hour >= 3          # retries are bounded; the sent caps are in `_budget_problem`


def test_text_is_built_from_fixed_fields():
    assert notices.compose("check_failing", name="cloudco.api", at=NOW) == (
        "Heads up: the check cloudco.api went from OK to failing "
        "(seen at 15:00 UTC on Nov 19).")
    assert "3" in notices.compose("proposal_waiting", count=3, hours=2, at=NOW)
    assert "from 5 (read 14:00 UTC on Nov 19) to 3 of 8 (read 15:00 UTC on Nov 19)" in (
        notices.compose("camera_drop", before=5, after=3, total=8,
                        before_at=NOW - timedelta(hours=1), at=NOW))
    with pytest.raises(ValueError):
        notices.compose("free_text", text="hello")


def test_outside_words_are_defanged_and_capped():
    text = notices.compose("check_failing",
                           name="x https://evil.test/a <!channel> " + "y" * 200, at=NOW)
    assert "https://" not in text and "<!channel>" not in text
    assert len(text) < 200


def test_vet_rejects_free_text_and_bad_ids():
    assert notices.vet({"notice_id": 1}) is None
    assert notices.vet({"notice_id": 0})
    assert notices.vet({"notice_id": True})
    assert notices.vet({"notice_id": 1, "text": "do this"})
    assert notices.vet({"notice_id": 1, "channel": "C1"})


async def test_dry_run_sends_nothing(store, sent):
    nid = await notices.queue("check_failing", ref="a", name="a", at=NOW)
    res = await notices.dry(nid)
    assert res.ok and not sent
    assert store.rows[nid]["status"] == "pending"


async def test_run_sends_one_dm_to_the_owner_at_normal(store, sent):
    nid = await notices.queue("check_failing", ref="a", name="a", at=NOW)
    res = await notices.run(nid)
    assert res.ok
    (msg,) = sent
    assert msg.channel == "U0OWNER"
    assert msg.severity is Severity.NORMAL and msg.severity < Severity.CRITICAL
    assert not msg.buttons
    assert store.rows[nid]["status"] == "sent"


async def test_never_critical_even_if_asked(store, sent):
    nid = await notices.queue("check_failing", ref="a", name="a", at=NOW)
    await notices.run(nid)
    assert sent[0].severity is not Severity.CRITICAL


async def test_dedupe_same_thing_inside_window(store, sent):
    a = await notices.queue("check_failing", ref="a", name="a", at=NOW)
    b = await notices.queue("check_failing", ref="a", name="a", at=NOW)
    c = await notices.queue("check_failing", ref="b", name="b", at=NOW)
    assert a and b is None and c


async def test_failed_attempts_do_not_use_up_the_hour(store, sent, monkeypatch):
    from atlas.kernel import comms
    ok = [False]

    async def flaky(msg):
        return ["slack"] if ok[0] else []
    monkeypatch.setattr(comms, "notify", flaky)
    t = [NOW]
    monkeypatch.setattr(notices, "_now", lambda: t[0])
    nid = await notices.queue("check_failing", ref="a", name="a", at=NOW)
    assert not (await notices.run(nid)).ok          # Slack refused: nothing sent
    assert await notices._budget_problem(t[0]) == ""    # the hour is still free
    ok[0] = True
    t[0] = NOW + notices.RETRY_WAIT + timedelta(seconds=1)
    assert (await notices.run(nid)).ok
    assert store.rows[nid]["status"] == "sent"


async def test_hour_cap_one(store, sent):
    a = await notices.queue("check_failing", ref="a", name="a", at=NOW)
    b = await notices.queue("check_failing", ref="b", name="b", at=NOW)
    assert (await notices.run(a)).ok
    res = await notices.run(b)
    assert not res.ok and "hour" in res.detail
    assert len(sent) == 1 and store.rows[b]["status"] == "pending"


async def test_day_cap_three(store, sent, monkeypatch):
    ids = [await notices.queue("check_failing", ref=str(i), name=str(i), at=NOW)
           for i in range(3)]
    t = [NOW]
    monkeypatch.setattr(notices, "_now", lambda: t[0])
    for i in range(3):
        assert (await notices.run(ids[i])).ok
        t[0] += timedelta(minutes=70)
    fourth = await notices.queue("check_failing", ref="late", name="late", at=NOW)
    res = await notices.run(fourth)
    assert not res.ok and "day" in res.detail
    assert len(sent) == 3
    t[0] = NOW + timedelta(hours=24, minutes=1)      # the first send left the day
    assert (await notices.run(fourth)).ok


async def test_quiet_hours_hold_it(store, sent, monkeypatch):
    monkeypatch.setenv("QUIET_HOURS", "00:00-23:59")
    nid = await notices.queue("check_failing", ref="a", name="a", at=NOW)
    res = await notices.run(nid)
    assert not res.ok and "quiet" in res.detail
    assert not sent and store.rows[nid]["status"] == "pending"


async def test_no_owner_id_no_send(store, sent, monkeypatch):
    monkeypatch.setenv("SLACK_APPROVER_IDS", "U1,U2")
    nid = await notices.queue("check_failing", ref="a", name="a", at=NOW)
    res = await notices.run(nid)
    assert not res.ok and not sent


async def test_watch_only_blocks_through_the_executor(store, sent, monkeypatch,
                                                      memory_backend):
    from atlas.kernel.permissions import classify
    from atlas.kernel.registry import REGISTRY
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()
    for a in notices.actions():
        REGISTRY.actions[a.name] = a
        REGISTRY.decisions[a.name] = classify(a)
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    nid = await notices.queue("check_failing", ref="a", name="a", at=NOW)
    from atlas.kernel.assistant_notice import send_notice
    assert not await send_notice(nid)
    assert not sent
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")
    executor._recent.clear()
    assert await send_notice(nid)
    assert len(sent) == 1
    REGISTRY.actions.clear()
    REGISTRY.decisions.clear()
    executor._recent.clear()


async def test_scan_checks_failing(store, sent, monkeypatch):
    async def flips(since):
        return [{"name": "cloudco.api", "at": NOW}, {"name": "mail.x", "at": NOW}]
    monkeypatch.setattr(notices, "_went_failing", flips)
    n = await notices.scan_checks()
    assert n == 2
    assert {r["ref"] for r in store.rows.values()} == {"cloudco.api", "mail.x"}


async def test_scan_proposals_waiting_over_two_hours(store, monkeypatch):
    async def waiting(cut):
        assert cut == NOW - timedelta(hours=2)
        return 3
    monkeypatch.setattr(notices, "_proposals_waiting", waiting)
    assert await notices.scan_proposals() == 1
    (row,) = store.rows.values()
    assert "3" in row["text"]


async def test_scan_proposals_none_waiting(store, monkeypatch):
    async def waiting(cut):
        return 0
    monkeypatch.setattr(notices, "_proposals_waiting", waiting)
    assert await notices.scan_proposals() == 0


async def test_scan_cameras_drop_only(store, monkeypatch):
    reads = iter([(8, 8), (8, 6), (8, None), (8, 6), (8, 7)])

    async def read():
        return next(reads)
    monkeypatch.setattr(notices, "_camera_online", read)
    assert await notices.scan_cameras() == 0        # first read: baseline
    assert await notices.scan_cameras() == 1        # 8 -> 6: told
    assert await notices.scan_cameras() == 0        # unreadable: never a number
    assert await notices.scan_cameras() == 0        # same 6: already told
    assert await notices.scan_cameras() == 0        # rising: not a drop
    (row,) = store.rows.values()
    assert "read 15:00 UTC" in row["text"]          # both figures carry a time


async def test_scanner_reads_without_the_private_gate(monkeypatch):
    """The scheduler is not a private agent; its own reader must still work, while
    the model-facing tool refuses outside a private run."""
    from atlas.modules.assistant import tools as T
    from atlas.modules.cameras import channels

    async def chans():
        return [{"channel": c, "online": 1} for c in range(channels.COUNT)], None, "t"
    monkeypatch.setattr(T, "_camera_channels", chans)
    total, online = await notices._camera_online()
    assert online is not None and online <= total
    assert "refused" in await T.camera_status()


async def test_every_figure_text_carries_a_time():
    for kind, f in (("check_failing", {"name": "x", "at": NOW}),
                    ("proposal_waiting", {"count": 1, "hours": 2, "at": NOW}),
                    ("camera_drop", {"before": 2, "after": 1, "total": 2,
                                     "before_at": NOW, "at": NOW})):
        assert "UTC on Nov 19" in notices.compose(kind, **f)
    with pytest.raises(TypeError):
        notices.compose("check_failing", name="x")            # no time, no text


async def test_slack_delivery_counts_and_a_matrix_only_one_does_not(store, monkeypatch):
    """A Matrix room is not the owner DM: it is never counted as telling him."""
    async def via_matrix(msg):
        return ["matrix"]
    monkeypatch.setattr(comms, "notify", via_matrix)
    a = await notices.queue("check_failing", ref="a", name="a", at=NOW)
    assert not (await notices.run(a)).ok and store.rows[a]["status"] != "sent"

    async def via_slack(msg):
        return ["slack"]
    monkeypatch.setattr(comms, "notify", via_slack)
    b = await notices.queue("check_failing", ref="b", name="b", at=NOW)
    c = await notices.queue("check_failing", ref="c", name="c", at=NOW)
    assert (await notices.run(b)).ok and store.rows[b]["status"] == "sent"
    assert not (await notices.run(c)).ok          # the hour cap holds the next


async def test_drain_rechecks_the_caps_before_each_notice(store, monkeypatch):
    calls = []

    async def send(nid):
        calls.append(nid)
        store.rows[nid]["status"] = "sent"
        store.rows[nid]["sent_at"] = NOW
        return True
    monkeypatch.setattr(notices, "_through_executor", send)
    for r in "abc":
        await notices.queue("check_failing", ref=r, name=r, at=NOW)
    await notices.drain()
    assert len(calls) == 1                         # the hour cap stops the second




async def test_nothing_delivered_waits_before_a_retry(store, monkeypatch):
    async def none(msg):
        return []
    monkeypatch.setattr(comms, "notify", none)
    nid = await notices.queue("check_failing", ref="a", name="a", at=NOW)
    assert not (await notices.run(nid)).ok
    again = await notices.run(nid)
    assert not again.ok and "retry wait" in again.detail


async def test_drain_checks_quiet_and_caps_before_the_executor(store, monkeypatch):
    calls = []

    async def spy(nid):
        calls.append(nid)
        return True
    monkeypatch.setattr(notices, "_through_executor", spy)
    await notices.queue("check_failing", ref="a", name="a", at=NOW)
    monkeypatch.setenv("QUIET_HOURS", "00:00-23:59")
    assert await notices.drain() == 0 and calls == []
    monkeypatch.delenv("QUIET_HOURS")
    monkeypatch.setenv("SLACK_APPROVER_IDS", "U1,U2")
    assert await notices.drain() == 0 and calls == []
    monkeypatch.setenv("SLACK_APPROVER_IDS", "U0OWNER")
    assert await notices.drain() == 1 and calls == [1]


async def test_test_notice_is_marked(store, sent):
    nid = await notices.queue("test", ref="test")
    await notices.run(nid)
    assert sent[0].body.startswith("TEST")
