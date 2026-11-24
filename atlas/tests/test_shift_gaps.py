"""Phase C gaps (plan 2026-10-30): C1 the Sales shift finds leads per
SALES_FIND_SEGMENTS (off by default); C2 a BizOps-style day keeps what it did
on its schedule_runs row."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from atlas.kernel import scheduler
from atlas.modules.sales import shift as sales_shift


def _sales_fakes(monkeypatch, hold_on: str | None = None):
    calls: list[tuple] = []

    async def run(action, params, why):
        calls.append((action, dict(params)))
        if action == hold_on:
            raise sales_shift._Held(action)
        return SimpleNamespace(evidence={"inserted": [1, 2]})

    async def nothing(out):
        calls.append(("step", None))

    monkeypatch.setattr(sales_shift, "_run", run)
    monkeypatch.setattr(sales_shift, "_imports", nothing)
    monkeypatch.setattr(sales_shift, "_new_leads", nothing)
    monkeypatch.setattr(sales_shift, "_pitches", nothing)
    return calls


async def test_no_segments_means_no_lead_finding(monkeypatch):
    monkeypatch.delenv("SALES_FIND_SEGMENTS", raising=False)
    calls = _sales_fakes(monkeypatch)
    out = await sales_shift.run()
    assert not [c for c in calls if c[0] == "sales.find_leads"]
    assert out["found"] == 0


async def test_one_find_per_segment_before_the_new_leads(monkeypatch):
    monkeypatch.setenv("SALES_FIND_SEGMENTS", "local_nyc, coaches")
    monkeypatch.setenv("SALES_FIND_AREA", "nyc")
    calls = _sales_fakes(monkeypatch)
    out = await sales_shift.run()
    assert calls == [("step", None),
                     ("sales.find_leads", {"segment": "local_nyc", "area": "nyc"}),
                     ("sales.find_leads", {"segment": "coaches", "area": "nyc"}),
                     ("step", None), ("step", None)]
    assert out["found"] == 4


async def test_a_hold_on_finding_ends_the_shift(monkeypatch):
    monkeypatch.setenv("SALES_FIND_SEGMENTS", "local_nyc,coaches")
    monkeypatch.delenv("SALES_FIND_AREA", raising=False)
    calls = _sales_fakes(monkeypatch, hold_on="sales.find_leads")
    out = await sales_shift.run()
    assert out["held"] == "sales.find_leads"
    assert calls == [("step", None), ("sales.find_leads", {"segment": "local_nyc"})]


async def test_a_bizops_day_records_what_it_did(monkeypatch):
    from atlas.kernel import missed, occurrence
    recorded: list[tuple] = []

    async def due(policy, now):
        return SimpleNamespace(key="2026-10-30", scheduled_for=now)

    async def claim_once(job, key, at):
        return True

    async def done(job, key, detail=None):
        recorded.append((job, key, detail))

    async def run():
        return {"digest": 1, "reminders": ["x" * 2000]}

    monkeypatch.setattr(missed, "due", due)
    monkeypatch.setattr(occurrence, "claim_once", claim_once)
    monkeypatch.setattr(occurrence, "done", done)
    monkeypatch.setattr(scheduler, "_now", lambda: datetime(2026, 10, 30, 13, tzinfo=UTC))
    await scheduler._maybe_bizops("bizops_digest", run)
    [(job, key, detail)] = recorded
    assert job == "bizops_digest" and key == "2026-10-30"
    assert len(detail) == 900
    assert detail.startswith(json.dumps({"digest": 1})[:-1])


async def test_done_writes_the_detail(monkeypatch):
    from atlas.kernel import occurrence, state
    seen: list[tuple] = []

    async def execute_sql(sql, *args):
        seen.append((sql, args))

    monkeypatch.setattr(state, "execute_sql", execute_sql)
    await occurrence.done("seo_rank", "2026-10-30", detail="y" * 1200)
    await occurrence.done("seo_rank", "2026-10-31")
    assert "detail = $4" in seen[0][0] and len(seen[0][1][3]) == 900
    assert "detail" not in seen[1][0] and len(seen[1][1]) == 3
