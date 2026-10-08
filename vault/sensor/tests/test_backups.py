"""Backups that stopped working fourteen months ago still exit zero.

The MSP spec calls this the cheapest feature with the highest consequence,
and the framing matters: Vault is not backup software. It checks that
whatever the customer already runs is still producing plausible artifacts,
because the common failure is not an absent backup, it is a dead one
nobody noticed.

The two findings worth the whole feature:

- `test_a_backup_that_collapsed_in_size_is_caught` — a dump that suddenly
  contains only an error message still lands on schedule, still exits zero,
  and is invisible to any check that only asks "did the job run?".
- `test_an_unreachable_host_makes_no_claim` — an unreachable host has not
  told us the backup is late. Reporting it as late is the same
  unmeasured-reads-as-failed mistake, mirrored.
"""
import datetime as dt

import pytest

from guardian_sensor import backups, util

HOST = {"name": "vps1", "target": "root@10.0.0.1"}


def cfg(**over):
    job = {"name": "nightly-db", "host": "vps1", "path": "/backups/db", "every_hours": 24}
    job.update(over.pop("job", {}))
    return {"ssh_hosts": [HOST], "backups": [job], **over}


def reply(monkeypatch, line, rc=0):
    monkeypatch.setattr(util, "ssh_run", lambda h, cmd, timeout=120, on_line=None: (rc, line))


def fresh(hours_ago=1.0, size=5_000_000, pct_used=40):
    when = dt.datetime.now().astimezone().timestamp() - hours_ago * 3600
    return f"/backups/db|ok|{when} {size}|{pct_used}"


def test_a_recent_backup_of_a_sane_size_is_healthy(monkeypatch):
    reply(monkeypatch, fresh())
    row = backups.check_backups(cfg(), {})[0]
    assert row["reachable"] and row["present"]
    assert row["late"] is False and row["shrank"] is False
    assert row["age_hours"] < 2 and row["bytes"] == 5_000_000


def test_a_backup_past_its_own_schedule_is_late(monkeypatch):
    reply(monkeypatch, fresh(hours_ago=40))
    assert backups.check_backups(cfg(), {})[0]["late"] is True


def test_a_job_that_slipped_a_little_is_not_late(monkeypatch):
    """A nightly that normally lands at 02:00 and slips to 03:30 is not a
    failure, and a check that pages for it gets muted within a fortnight."""
    reply(monkeypatch, fresh(hours_ago=30))
    assert backups.check_backups(cfg(), {})[0]["late"] is False


def test_a_backup_that_collapsed_in_size_is_caught(monkeypatch):
    """The silent failure this feature exists for: the dump now contains an
    error message, lands on time, and exits zero."""
    reply(monkeypatch, fresh(size=9_000))
    state = {"backups": {"nightly-db": {"bytes": 5_000_000}}}
    row = backups.check_backups(cfg(), state)[0]
    assert row["shrank"] is True
    assert row["late"] is False, "it ran on time — that is what makes it silent"


def test_normal_week_to_week_variation_is_not_a_collapse(monkeypatch):
    reply(monkeypatch, fresh(size=4_200_000))
    state = {"backups": {"nightly-db": {"bytes": 5_000_000}}}
    assert backups.check_backups(cfg(), state)[0]["shrank"] is False


def test_a_first_run_has_nothing_to_compare_and_does_not_guess(monkeypatch):
    reply(monkeypatch, fresh())
    row = backups.check_backups(cfg(), {})[0]
    assert row["shrank"] is False and row["previous_bytes"] is None


def test_a_destination_filling_up_is_flagged_before_it_fails(monkeypatch):
    reply(monkeypatch, fresh(pct_used=95))
    assert backups.check_backups(cfg(), {})[0]["destination_full"] is True


def test_a_missing_destination_is_reported_without_a_size_verdict(monkeypatch):
    reply(monkeypatch, "/backups/db|missing||")
    row = backups.check_backups(cfg(), {})[0]
    assert row["present"] is False and "does not exist" in row["note"]
    assert "late" not in row, "no artifact means no lateness verdict to give"


def test_an_empty_destination_is_reported_as_no_artifact(monkeypatch):
    reply(monkeypatch, "/backups/db|empty||30")
    row = backups.check_backups(cfg(), {})[0]
    assert row["present"] is False and "no backup artifact" in row["note"]


def test_an_unreachable_host_makes_no_claim(monkeypatch):
    """Not late, not failed, not fine. An unreachable host has told us
    nothing, and a verdict here would be invented."""
    reply(monkeypatch, "", rc=255)
    row = backups.check_backups(cfg(), {})[0]
    assert row["reachable"] is False
    for verdict in ("late", "shrank", "present", "destination_full"):
        assert verdict not in row, f"{verdict} claimed about a host we could not reach"


def test_a_missed_sweep_does_not_become_the_next_sweeps_collapse(monkeypatch):
    """The remembered size must only update on a successful read, or an
    unreachable night would make the following night look like a collapse."""
    state = {"backups": {"nightly-db": {"bytes": 5_000_000}}}
    reply(monkeypatch, "", rc=255)
    backups.check_backups(cfg(), state)
    assert state["backups"]["nightly-db"]["bytes"] == 5_000_000


def test_a_job_below_its_configured_floor_is_flagged(monkeypatch):
    reply(monkeypatch, fresh(size=1_000))
    row = backups.check_backups(cfg(job={"min_bytes": 1_000_000}), {})[0]
    assert row["too_small"] is True


def test_no_configured_jobs_costs_nothing(monkeypatch):
    called = []
    monkeypatch.setattr(util, "ssh_run", lambda *a, **k: called.append(1) or (0, ""))
    assert backups.check_backups({"ssh_hosts": [HOST]}, {}) == []
    assert not called, "a tenant with no backup jobs must not be SSH'd for them"


def test_all_of_a_hosts_jobs_ride_one_ssh_call(monkeypatch):
    """Per-job round trips would make the sweep scale with job count."""
    calls = []

    def fake(h, cmd, timeout=120, on_line=None):
        calls.append(cmd)
        return 0, fresh() + "\n/backups/files|ok|%s 900|20" % (
            dt.datetime.now().astimezone().timestamp() - 3600)

    monkeypatch.setattr(util, "ssh_run", fake)
    c = cfg()
    c["backups"].append({"name": "files", "host": "vps1", "path": "/backups/files"})
    rows = backups.check_backups(c, {})
    assert len(rows) == 2 and len(calls) == 1


# --- the findings ---------------------------------------------------------

import copy
import json
import pathlib

from guardian_sensor import alerts

FX = pathlib.Path(__file__).parent / "fixtures"


def _fire(rows):
    with open(FX / "last.json") as f:
        last = json.load(f)
    with open(FX / "config.json") as f:
        conf = json.load(f)
    with open(FX / "state.json") as f:
        state = json.load(f)
    last["backups"] = rows
    return {a["key"]: a for a in alerts.build_alerts(copy.deepcopy(last), conf,
                                                     copy.deepcopy(state))}


BASE = {"name": "nightly-db", "host": "vps1", "reachable": True, "present": True,
        "every_hours": 24, "age_hours": 2.0, "bytes": 5_000_000, "newest": "2026-09-20",
        "late": False, "shrank": False, "too_small": False, "destination_full": False,
        "previous_bytes": 5_000_000, "free_pct": 60}


def test_a_healthy_backup_raises_nothing():
    got = _fire([dict(BASE)])
    assert not [k for k in got if k.startswith("backup:")]


def test_a_late_backup_says_how_old_and_how_often():
    got = _fire([dict(BASE, late=True, age_hours=51.0)])
    a = got["backup:nightly-db:late"]
    assert "51h old" in a["msg"] and "every 24h" in a["msg"]


def test_a_collapsed_backup_explains_why_it_matters():
    got = _fire([dict(BASE, shrank=True, bytes=9_000, previous_bytes=5_000_000)])
    a = got["backup:nightly-db:shrank"]
    assert "silent failure" in a["msg"]


def test_an_unreachable_job_raises_nothing_at_all():
    """An SSH blip must not page someone about their backups."""
    got = _fire([{"name": "nightly-db", "host": "vps1", "reachable": False,
                  "path": "/b", "every_hours": 24}])
    assert not [k for k in got if k.startswith("backup:")]


def test_a_full_destination_says_the_next_run_is_what_fails():
    got = _fire([dict(BASE, destination_full=True, free_pct=4)])
    assert "next run is what fails" in got["backup:nightly-db:full"]["msg"]


def test_the_finding_does_not_claim_guardian_takes_backups():
    got = _fire([dict(BASE, present=False, note="no backup artifact found")])
    assert "does not take one" in got["backup:nightly-db:missing"]["msg"]
