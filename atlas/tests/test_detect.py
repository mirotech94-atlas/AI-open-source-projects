"""detect.evaluate — UNKNOWN must never read as healthy, and the baseline
maths must be right at the sigma boundary."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from atlas.kernel import detect
from atlas.kernel.contracts import Check, Health, Observation, Severity

AT = datetime(2026, 9, 21, 14, 0, tzinfo=UTC)


def check(sigma: float | None = 3.0, severity=Severity.HIGH) -> Check:
    async def run() -> Observation:                     # pragma: no cover
        raise AssertionError("evaluate must not run the check")
    return Check(name="probe", run=run, interval=timedelta(minutes=5),
                 severity_on_fail=severity, baseline_sigma=sigma)


def obs(health=Health.OK, value=None, detail="") -> Observation:
    return Observation(check="mod.probe", health=health, value=value,
                       detail=detail, at=AT)


@pytest.fixture
def no_baseline(monkeypatch):
    async def none(*_a, **_k):
        return None
    monkeypatch.setattr(detect, "baseline", none)


def with_baseline(monkeypatch, mu: float, sigma: float):
    async def stats(*_a, **_k):
        return (mu, sigma)
    monkeypatch.setattr(detect, "baseline", stats)


# ------------------------------------------------------- UNKNOWN is not OK

async def test_unknown_produces_a_finding(no_baseline):
    finding = await detect.evaluate("mod.probe", check(), obs(Health.UNKNOWN), 1)
    assert finding is not None
    assert "could not run" in finding.title


async def test_unknown_is_reported_even_when_a_value_is_present(no_baseline):
    """A collector can return a stale number alongside UNKNOWN. The number must
    not let it pass as healthy."""
    finding = await detect.evaluate(
        "mod.probe", check(), obs(Health.UNKNOWN, value=100.0), 1)
    assert finding is not None
    assert finding.kind == "rule"


async def test_ok_with_no_baseline_produces_nothing(no_baseline):
    assert await detect.evaluate("mod.probe", check(), obs(Health.OK, 5.0), 1) is None


async def test_failing_uses_the_declared_severity(no_baseline):
    finding = await detect.evaluate(
        "mod.probe", check(severity=Severity.CRITICAL), obs(Health.FAILING), 1)
    assert finding.severity is Severity.CRITICAL


async def test_degraded_is_one_step_below_failing(no_baseline):
    finding = await detect.evaluate(
        "mod.probe", check(severity=Severity.CRITICAL), obs(Health.DEGRADED), 1)
    assert finding.severity is Severity.HIGH


# --------------------------------------------------------- baseline maths

async def test_the_sigma_boundary_is_inclusive(monkeypatch):
    """A value exactly `baseline_sigma` from the mean HAS drifted that far, so
    it fires. The band is (mu - n*sigma, mu + n*sigma), exclusive."""
    with_baseline(monkeypatch, mu=100.0, sigma=10.0)
    # exactly 3.0 sigma above 100 with sigma 10 -> 130
    finding = await detect.evaluate(
        "mod.probe", check(sigma=3.0), obs(Health.OK, 130.0), 1)
    assert finding is not None, "at exactly 3 sigma the check must fire"
    assert finding.kind == "baseline"
    assert "above" in finding.title


async def test_just_inside_the_boundary_does_not_fire(monkeypatch):
    with_baseline(monkeypatch, mu=100.0, sigma=10.0)
    assert await detect.evaluate(
        "mod.probe", check(sigma=3.0), obs(Health.OK, 129.99), 1) is None


async def test_the_mean_itself_never_fires(monkeypatch):
    with_baseline(monkeypatch, mu=100.0, sigma=10.0)
    assert await detect.evaluate(
        "mod.probe", check(sigma=3.0), obs(Health.OK, 100.0), 1) is None


async def test_a_drop_is_detected_as_well_as_a_spike(monkeypatch):
    with_baseline(monkeypatch, mu=100.0, sigma=10.0)
    finding = await detect.evaluate(
        "mod.probe", check(sigma=3.0), obs(Health.OK, 60.0), 1)
    assert finding is not None and "below" in finding.title


async def test_a_large_excursion_is_high_severity(monkeypatch):
    with_baseline(monkeypatch, mu=100.0, sigma=10.0)
    finding = await detect.evaluate(
        "mod.probe", check(sigma=3.0), obs(Health.OK, 160.0), 1)   # 6 sigma
    assert finding.severity is Severity.HIGH


async def test_baseline_disabled_means_no_statistical_finding(monkeypatch):
    with_baseline(monkeypatch, mu=100.0, sigma=10.0)
    assert await detect.evaluate(
        "mod.probe", check(sigma=None), obs(Health.OK, 9999.0), 1) is None


async def test_no_value_means_no_statistical_finding(monkeypatch):
    with_baseline(monkeypatch, mu=100.0, sigma=10.0)
    assert await detect.evaluate(
        "mod.probe", check(sigma=3.0), obs(Health.OK, None), 1) is None


# ----------------------------------------------- thin baselines return none

async def test_thin_baseline_returns_no_finding(monkeypatch):
    """state.baseline returns None below 5 points; evaluate must not guess."""
    async def thin(*_a, **_k):
        return None
    monkeypatch.setattr(detect, "baseline", thin)
    assert await detect.evaluate(
        "mod.probe", check(sigma=3.0), obs(Health.OK, 9999.0), 1) is None


async def test_state_baseline_refuses_thin_and_flat_history(memory_backend):
    from atlas.kernel.state import baseline as real_baseline

    class Row(dict):
        pass

    memory_backend.responses["SELECT avg(value)"] = Row(
        {"mu": 10.0, "sigma": 2.0, "n": 4})            # too few points
    assert await real_baseline(1, 14, 1) is None

    memory_backend.responses["SELECT avg(value)"] = Row(
        {"mu": 10.0, "sigma": 0, "n": 50})             # flat: no spread
    assert await real_baseline(1, 14, 1) is None

    memory_backend.responses["SELECT avg(value)"] = Row(
        {"mu": 10.0, "sigma": 2.0, "n": 50})
    assert await real_baseline(1, 14, 1) == (10.0, 2.0)


# --------------------------------------------------------------- bucketing

async def test_baseline_is_bucketed_in_utc(monkeypatch):
    """C6: a local-time observation must bucket by its UTC hour-of-week."""
    seen: dict = {}

    async def spy(check_id, hour, dow):
        seen.update(check_id=check_id, hour=hour, dow=dow)

    monkeypatch.setattr(detect, "baseline", spy)
    # 23:30 on Sunday in UTC-4 is 03:30 Monday UTC.
    local = datetime(2026, 9, 20, 23, 30, tzinfo=timezone(timedelta(hours=-4)))
    o = Observation(check="mod.probe", health=Health.OK, value=1.0, at=local)
    await detect.evaluate("mod.probe", check(), o, 7)
    assert seen["hour"] == 3
    assert seen["dow"] == 1          # Postgres DOW: Monday == 1


# ------------------------------------------- a crashing check is not silence

async def test_a_raising_check_becomes_an_unknown_finding(monkeypatch, memory_backend):
    """A collector that throws must surface, not vanish from the record."""
    from atlas.kernel import scheduler

    async def explode() -> Observation:
        raise RuntimeError("admin API changed shape")

    broken = Check(name="probe", run=explode, interval=timedelta(minutes=1))
    monkeypatch.setattr(scheduler.REGISTRY, "checks", {"mod.probe": broken})
    monkeypatch.setattr(scheduler, "_next_run", {})
    monkeypatch.setattr(scheduler, "_check_id", lambda key: _one())
    recorded: list = []

    async def capture(cid, obs):
        recorded.append(obs)

    monkeypatch.setattr(scheduler, "record_observation", capture)
    _stub_persistence(monkeypatch, scheduler)

    async def none(*_a, **_k):
        return None
    monkeypatch.setattr(detect, "baseline", none)

    findings = await scheduler.run_once()
    assert len(recorded) == 1
    assert recorded[0].health is Health.UNKNOWN
    assert len(findings) == 1
    assert "could not run" in findings[0].title
    assert "RuntimeError" in findings[0].detail


async def test_a_timing_out_check_becomes_an_unknown_finding(
        monkeypatch, memory_backend):
    from atlas.kernel import scheduler

    async def hang() -> Observation:
        import asyncio
        await asyncio.sleep(5)
        raise AssertionError("should have timed out")

    slow = Check(name="probe", run=hang, interval=timedelta(minutes=1),
                 timeout=timedelta(milliseconds=30))
    monkeypatch.setattr(scheduler.REGISTRY, "checks", {"mod.probe": slow})
    monkeypatch.setattr(scheduler, "_next_run", {})
    monkeypatch.setattr(scheduler, "_check_id", lambda key: _one())

    async def capture(cid, obs):
        pass
    monkeypatch.setattr(scheduler, "record_observation", capture)
    _stub_persistence(monkeypatch, scheduler)

    async def none(*_a, **_k):
        return None
    monkeypatch.setattr(detect, "baseline", none)

    findings = await scheduler.run_once()
    assert len(findings) == 1
    assert "timed out" in findings[0].detail


async def _one() -> int:
    return 1


def _stub_persistence(monkeypatch, scheduler):
    """These tests are about detection, not storage."""
    async def record_finding(_finding, _key):
        return 1

    async def record_schedule(*_a):
        return None

    monkeypatch.setattr(scheduler, "record_finding", record_finding)
    monkeypatch.setattr(scheduler, "record_schedule", record_schedule)
