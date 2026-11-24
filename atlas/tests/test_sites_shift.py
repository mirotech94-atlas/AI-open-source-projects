"""The Sites shift (plan 2026-10-30, B3 + B5): the lane's daily state in one
dict, and -- only under AGENCY_AUTO_BUILD=on -- up to three demos built a
day, each followed by a publish CARD. Publishing is Tier 2: proposed, never
executed here. The owner email follows only after the owner approves the publish
(lead_followup.after_deploys)."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from atlas.modules.agency import sites_shift

AT = datetime(2026, 10, 30, 9, tzinfo=UTC)


def _fake_reads(monkeypatch, rows: dict[str, list[dict]] | None = None,
                fail: tuple[str, ...] = ()):
    rows = rows or {}

    async def fetch_all(sql, *args):
        for name, query in sites_shift.QUERIES.items():
            if sql == query:
                if name in fail:
                    raise OSError("db down")
                return rows.get(name, [])
        raise AssertionError(f"unexpected SQL {sql[:60]}")

    monkeypatch.setattr(sites_shift, "fetch_all", fetch_all)


async def test_each_bucket_comes_from_its_rows(monkeypatch):
    _fake_reads(monkeypatch, {
        "needs_demo": [{"id": 7, "name": "Joe's Deli", "has_mockup": True}],
        "built_unpublished": [{"slug": "joes-deli", "lead_id": 7}],
        "published_unpitched": [{"slug": "ana-salon", "lead_id": 8,
                                 "domain": "demo-site.vercel.example",
                                 "deployed_at": AT}],
        "cards_waiting": [{"id": 31, "action": "agency.publish_vercel",
                           "target": "joes-deli", "created_at": AT}],
        "health": [{"check": "site_ana-salon", "health": "ok", "at": AT}],
    })
    state = await sites_shift.read_state()
    assert [r["id"] for r in state["needs_demo"]] == [7]
    assert [r["slug"] for r in state["built_unpublished"]] == ["joes-deli"]
    assert [r["slug"] for r in state["published_unpitched"]] == ["ana-salon"]
    assert [c["id"] for c in state["cards_waiting"]] == [31]
    assert state["health"]["ana-salon"] == {"health": "ok", "at": AT.isoformat()}
    assert state["unreadable"] == []


async def test_a_deployed_site_with_no_observation_is_unknown(monkeypatch):
    _fake_reads(monkeypatch, {
        "published_unpitched": [{"slug": "ana-salon", "lead_id": 8,
                                 "domain": "x.vercel.app", "deployed_at": AT}],
        "deployed": [{"slug": "ana-salon"}, {"slug": "bo-bakery"}],
        "health": [{"check": "site_ana-salon", "health": "degraded", "at": AT}],
    })
    state = await sites_shift.read_state()
    assert state["health"]["ana-salon"]["health"] == "degraded"
    assert state["health"]["bo-bakery"] == {"health": "unknown", "at": None}


async def test_a_pending_card_is_listed_once(monkeypatch):
    card = {"id": 31, "action": "agency.publish_vercel", "target": "joes-deli",
            "created_at": AT}
    _fake_reads(monkeypatch, {"cards_waiting": [card, dict(card, id=32)]})
    state = await sites_shift.read_state()
    assert len(state["cards_waiting"]) == 1


async def test_an_unreadable_bucket_is_named_not_empty(monkeypatch):
    _fake_reads(monkeypatch, fail=("needs_demo",))
    state = await sites_shift.read_state()
    assert state["needs_demo"] is None
    assert state["unreadable"] == ["needs_demo: OSError"]


# --- B5: build_missing ------------------------------------------------------

def _result(ok=True, evidence=None, dispatched=True):
    return SimpleNamespace(ok=ok, evidence=evidence or {}, dispatched=dispatched,
                           failure_class=None, detail="")


def _fake_lane(monkeypatch, leads, *, hold_on=None):
    calls: list[tuple] = []

    async def needs_demo(limit):
        return leads[:limit]

    async def execute(req):
        calls.append(("execute", req.action, dict(req.params)))
        if hold_on and (req.action, req.params.get("lead_id")) == hold_on:
            return _result(ok=False, evidence={"hold": True}, dispatched=False)
        if req.action == "agency.build_site":
            return _result(evidence={"slug": f"lead-{req.params['lead_id']}"})
        return _result()

    async def propose(action, params, reason, **kw):
        calls.append(("propose", action, dict(params), kw.get("dedup_key")))
        return 99

    from atlas.kernel import approvals, executor, lead_followup
    monkeypatch.setattr(sites_shift, "_demo_candidates", needs_demo)
    monkeypatch.setattr(executor, "execute", execute)
    monkeypatch.setattr(approvals, "propose", propose)
    monkeypatch.setattr(lead_followup, "_held",
                        lambda r: bool((r.evidence or {}).get("hold")))
    return calls


async def test_flag_off_builds_nothing(monkeypatch):
    monkeypatch.delenv("AGENCY_AUTO_BUILD", raising=False)
    calls = _fake_lane(monkeypatch, [{"id": 1, "name": "A", "has_mockup": True}])
    out = await sites_shift.build_missing()
    assert calls == []
    assert out["built"] == 0 and out["skipped"] == "AGENCY_AUTO_BUILD is off"


async def test_flag_on_builds_at_most_three_and_proposes_each_publish(monkeypatch):
    monkeypatch.setenv("AGENCY_AUTO_BUILD", "on")
    leads = [{"id": i, "name": f"L{i}", "has_mockup": i != 2} for i in range(1, 6)]
    calls = _fake_lane(monkeypatch, leads)
    out = await sites_shift.build_missing()
    built = [c for c in calls if c[:2] == ("execute", "agency.build_site")]
    assert [c[2]["lead_id"] for c in built] == [1, 2, 3]
    # The lead with no mockup gets one first, through the executor.
    assert ("execute", "agency.mockup", {"lead_id": 2}) in calls
    proposed = [c for c in calls if c[0] == "propose"]
    assert [(c[1], c[2], c[3]) for c in proposed] == [
        ("agency.publish_vercel", {"slug": f"lead-{i}"}, f"publish_vercel:lead-{i}")
        for i in (1, 2, 3)]
    assert not [c for c in calls if c[:2] == ("execute", "agency.publish_vercel")]
    assert out["built"] == 3 and out["cards"] == 3 and out["held"] is None


async def test_a_hold_stops_the_run(monkeypatch):
    monkeypatch.setenv("AGENCY_AUTO_BUILD", "on")
    leads = [{"id": i, "name": f"L{i}", "has_mockup": True} for i in (1, 2, 3)]
    calls = _fake_lane(monkeypatch, leads, hold_on=("agency.build_site", 2))
    out = await sites_shift.build_missing()
    assert out["held"] == "agency.build_site"
    assert out["built"] == 1
    assert not [c for c in calls if c[0] == "execute" and c[2].get("lead_id") == 3]


async def test_a_card_the_qa_gate_holds_is_counted_not_raised(monkeypatch):
    monkeypatch.setenv("AGENCY_AUTO_BUILD", "on")
    _fake_lane(monkeypatch, [{"id": 1, "name": "A", "has_mockup": True}])
    from atlas.kernel import approvals, qa_gate

    async def refuse(*a, **k):
        raise qa_gate.QaRequired("agency.publish_vercel", "QA has not passed")

    monkeypatch.setattr(approvals, "propose", refuse)
    out = await sites_shift.build_missing()
    assert out["built"] == 1 and out["cards"] == 0
    assert out["cards_held"] == ["lead-1: QaRequired"]


@pytest.mark.parametrize("value", ["", "off", "0", "false"])
async def test_only_on_switches_it_on(monkeypatch, value):
    monkeypatch.setenv("AGENCY_AUTO_BUILD", value)
    calls = _fake_lane(monkeypatch, [{"id": 1, "name": "A", "has_mockup": True}])
    await sites_shift.build_missing()
    assert calls == []
