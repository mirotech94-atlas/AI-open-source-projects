"""The incident CONTRACT between this sensor and the hub's ingest.

Task 41 fix round 1, finding 2: the hub read `inc["sev"]` / `inc["opened"]`
/ `inc["closed"]`, none of which `correlate_incidents` emits -- so on real
payloads every incident row came out `sev="WARN"`, `opened` rewritten to the
ingest timestamp every tick, and `closed` forever None (which made the Jira
/ ServiceNow comment-on-fix and close-on-resolve paths unreachable in
production). The hub's own fixture had been written against the reader's
keys rather than this emitter's, so its tests passed on a payload the
sensor never produces.

This test pins the emitted shape from THIS side, so the next change to
`correlate_incidents` breaks here rather than silently in the hub. The
matching hub-side test (`hub/tests/test_integrations_task41_fix1.py`,
"finding 2 / ruling (c)") ingests a payload in exactly this shape.
"""
import copy

from guardian_sensor import alerts

# Every key the hub's `ingest.store_scan` reads off an incident, and the
# type it must be. `closed` is deliberately absent: the sensor never emits
# it, which is why the hub closes an incident when its id stops appearing.
EXPECTED_KEYS = {
    "id": int, "entity": str, "title": str, "severity": str, "confidence": int,
    "alerts": list, "started": str, "story": list, "actions": list, "state": str,
}


def _res(ts="2026-09-10T12:00:00-04:00"):
    """Two CRIT alerts on one host plus two WARNs on another: enough for
    `correlate_incidents` to group (it needs 2+ alerts, or one CRIT)."""
    return {
        "ts": ts,
        "alerts": [
            {"key": "sec:miner:edge1", "sev": "CRIT", "msg": "edge1: crypto miner process",
             "first_seen": "2026-09-10T06:00:00-04:00", "confidence": 90, "state": "open"},
            {"key": "disk:edge1", "sev": "WARN", "msg": "edge1: disk 95% full",
             "first_seen": "2026-09-10T07:00:00-04:00", "confidence": 80, "state": "open"},
            {"key": "email:spf:example.test", "sev": "WARN", "msg": "example.test: no SPF record",
             "first_seen": "2026-09-10T08:00:00-04:00", "confidence": 70, "state": "open"},
            {"key": "email:dmarc:example.test", "sev": "WARN", "msg": "example.test: no DMARC record",
             "first_seen": "2026-09-10T08:00:00-04:00", "confidence": 70, "state": "open"},
            {"key": "adv:guest", "sev": "INFO", "msg": "use a guest network",
             "first_seen": "2026-09-10T08:00:00-04:00", "confidence": 50, "state": "open"},
        ],
        "recommendations": [
            {"key": "sec:miner:edge1", "title": "Kill the miner and rotate keys", "priority": "Critical"},
        ],
    }


def test_incident_shape_is_what_the_hub_reads():
    incidents = alerts.correlate_incidents(_res(), {})
    assert incidents, "two alerts on one entity must correlate into an incident"
    for inc in incidents:
        assert set(inc) == set(EXPECTED_KEYS), f"unexpected incident keys: {sorted(inc)}"
        for key, typ in EXPECTED_KEYS.items():
            assert isinstance(inc[key], typ), f"{key} is {type(inc[key])}, not {typ}"
        # The two keys the hub's reader got wrong: severity/started, never
        # sev/opened -- and no `closed` at all.
        assert "sev" not in inc and "opened" not in inc and "closed" not in inc
        assert inc["severity"] in ("CRIT", "WARN")
        # The class-prefixed entity the hub has to strip to reach `Fix.host`.
        assert ":" in inc["entity"] or inc["entity"] == "home"


def test_story_is_a_timeline_of_ts_text_sev_steps():
    inc = next(i for i in alerts.correlate_incidents(_res(), {}) if i["entity"] == "host:edge1")
    assert len(inc["story"]) == 2
    for step in inc["story"]:
        assert set(step) == {"ts", "text", "sev"}
        assert all(isinstance(v, str) for v in step.values())
    assert [s["ts"] for s in inc["story"]] == sorted(s["ts"] for s in inc["story"])


def test_actions_are_recommendation_dicts_not_strings():
    inc = next(i for i in alerts.correlate_incidents(_res(), {}) if i["entity"] == "host:edge1")
    assert inc["actions"] and all(isinstance(a, dict) and "key" in a for a in inc["actions"])


def test_started_is_the_earliest_alert_and_id_is_stable_across_ticks():
    state = {}
    first = alerts.correlate_incidents(_res(), state)
    by_entity = {i["entity"]: i for i in first}
    assert by_entity["host:edge1"]["started"] == "2026-09-10T06:00:00-04:00"

    # A later tick with the same alerts keeps the same ids -- the hub keys
    # `Incident.iid` off them, so they must not renumber.
    again = alerts.correlate_incidents(_res(ts="2026-09-10T13:00:00-04:00"), state)
    assert {i["entity"]: i["id"] for i in again} == {i["entity"]: i["id"] for i in first}


def test_a_resolved_incident_simply_leaves_the_list():
    """No `closed` flag exists: the incident is just gone. This is the
    signal the hub's close pass depends on."""
    state = {}
    res = _res()
    ids = {i["id"] for i in alerts.correlate_incidents(copy.deepcopy(res), state)}
    assert ids

    quiet = _res(ts="2026-09-10T14:00:00-04:00")
    quiet["alerts"] = [a for a in quiet["alerts"] if not a["key"].endswith("edge1")]
    still = alerts.correlate_incidents(quiet, state)
    assert "host:edge1" not in {i["entity"] for i in still}
    assert all("closed" not in i for i in still)
