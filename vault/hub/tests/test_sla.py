"""ISP service credits: the money SMBs leave on the table every month.

They do not claim because assembling the evidence costs more than the
credit. Vault measures from inside the network — the vantage point the
provider cannot dispute — so the claim becomes a two-minute job.

The feature turns entirely on cause attribution, and so does this file.
An outage is claimable only if it was the provider's:

- gateway answers, nothing beyond it does -> the line is down, `isp`
- gateway unreachable too -> the fault is in the building, `local`
- anything else -> `unknown`

`test_only_isp_minutes_are_claimed` is the one that matters. Claiming a
local outage once gets the customer told by their own provider that their
monitoring is wrong, and no MSP recovers from that — so the conservative
reading is worth more than the extra minutes every time.
"""
import datetime as dt

from guardian_hub import models as m, sla
from guardian_hub.db import SessionLocal
from tests.test_tenants import make_super

UP = {"loss_pct": 0.0}
DOWN = {"loss_pct": 100.0}


def paths(gateway, external):
    return {"gateway": gateway, "cloudflare": external, "google": external}


# --------------------------------------------------------------------------
# Attribution
# --------------------------------------------------------------------------

def test_the_line_being_down_behind_a_live_gateway_is_the_isps():
    assert sla.classify(paths(UP, DOWN), None, True) == "isp"


def test_a_dead_gateway_means_the_fault_is_in_the_building():
    assert sla.classify(paths(DOWN, DOWN), None, True) == "local"


def test_a_working_line_is_not_an_outage_at_all():
    assert sla.classify(paths(UP, UP), "203.0.113.9", True) == ""


def test_off_its_expected_network_the_sensor_judges_nothing():
    """Every verdict it could give would be about a different line."""
    assert sla.classify(paths(UP, DOWN), None, False) == "unknown"


def test_without_a_gateway_probe_the_cause_is_unknown_not_assumed():
    assert sla.classify({"cloudflare": DOWN, "google": DOWN}, None, True) == "unknown"
    assert sla.classify(None, None, True) == "unknown"


# --------------------------------------------------------------------------
# Recording
# --------------------------------------------------------------------------

def _scan(ts, gateway, external, on_lan=True):
    return {"ts": ts, "on_home_lan": on_lan, "wan_path": paths(gateway, external)}


def _tenant(tid):
    db = SessionLocal()
    return db.get(m.Tenant, tid), db


def test_an_outage_opens_stays_open_and_closes(client):
    tid = make_super(client)
    t, db = _tenant(tid)
    try:
        sla.record(db, t, _scan("2026-09-01T10:00:00+00:00", UP, DOWN))
        sla.record(db, t, _scan("2026-09-01T10:05:00+00:00", UP, DOWN))
        rows = db.query(m.WanOutage).filter_by(tenant_id=tid).all()
        assert len(rows) == 1 and rows[0].ended_at is None and rows[0].cause == "isp"

        sla.record(db, t, _scan("2026-09-01T10:10:00+00:00", UP, UP))
        db.refresh(rows[0])
        assert rows[0].ended_at == "2026-09-01T10:10:00+00:00"
    finally:
        db.close()


def test_a_replayed_sweep_does_not_open_a_second_outage(client):
    """The sensor buffers and replays; duplicates are guaranteed."""
    tid = make_super(client)
    t, db = _tenant(tid)
    try:
        for _ in range(3):
            sla.record(db, t, _scan("2026-09-01T10:00:00+00:00", UP, DOWN))
        assert db.query(m.WanOutage).filter_by(tenant_id=tid).count() == 1
    finally:
        db.close()


def test_an_ambiguous_sweep_alone_opens_nothing(client):
    """Silence beats a row nobody can defend to a billing department."""
    tid = make_super(client)
    t, db = _tenant(tid)
    try:
        sla.record(db, t, _scan("2026-09-01T10:00:00+00:00", UP, DOWN, on_lan=False))
        assert db.query(m.WanOutage).filter_by(tenant_id=tid).count() == 0
    finally:
        db.close()


def test_an_outage_that_becomes_attributable_takes_the_stronger_reading(client):
    tid = make_super(client)
    t, db = _tenant(tid)
    try:
        sla.record(db, t, {"ts": "2026-09-01T10:00:00+00:00", "on_home_lan": True,
                           "wan_path": {"cloudflare": DOWN, "google": DOWN}})
        # Nothing opened — ambiguous alone. Now a clear ISP sweep.
        sla.record(db, t, _scan("2026-09-01T10:05:00+00:00", UP, DOWN))
        rows = db.query(m.WanOutage).filter_by(tenant_id=tid).all()
        assert len(rows) == 1 and rows[0].cause == "isp"
    finally:
        db.close()


# --------------------------------------------------------------------------
# The claim
# --------------------------------------------------------------------------

def _outage(db, tid, start, end, cause):
    db.add(m.WanOutage(tenant_id=tid, started_at=start, ended_at=end, cause=cause,
                       evidence={}))
    db.commit()


def test_only_isp_minutes_are_claimed(client):
    """The test the whole feature rests on. A claim that quietly includes
    the customer's own outages is one the provider rejects and remembers."""
    tid = make_super(client)
    t, db = _tenant(tid)
    try:
        t.settings = {**(t.settings or {}), "sla_committed_pct": 99.9,
                      "circuit_monthly_cost": 300}
        db.commit()
        _outage(db, tid, "2026-09-02T01:00:00+00:00", "2026-09-02T03:00:00+00:00", "isp")
        _outage(db, tid, "2026-09-05T01:00:00+00:00", "2026-09-05T09:00:00+00:00", "local")
        _outage(db, tid, "2026-09-07T01:00:00+00:00", "2026-09-07T02:00:00+00:00", "unknown")

        got = sla.credit(db, t, "2026-09-01", "2026-09-30")
        assert got["claimable_minutes"] == 120.0
        assert got["other_minutes"]["local"] == 480.0
        assert got["other_minutes"]["unknown"] == 60.0
    finally:
        db.close()


def test_the_claim_shows_every_term_of_its_arithmetic(client):
    """The reader is a billing department looking for a reason to say no.
    A number with no working shown is the easiest thing to reject."""
    tid = make_super(client)
    t, db = _tenant(tid)
    try:
        t.settings = {**(t.settings or {}), "sla_committed_pct": 99.9,
                      "circuit_monthly_cost": 300}
        db.commit()
        _outage(db, tid, "2026-09-02T01:00:00+00:00", "2026-09-02T09:00:00+00:00", "isp")
        got = sla.credit(db, t, "2026-09-01", "2026-09-30")
        for term in ("committed_pct", "measured_pct", "shortfall_pct",
                     "claimable_minutes", "monthly_cost", "credit_owed", "outages"):
            assert term in got, term
        assert got["measured_pct"] < got["committed_pct"]
        assert got["credit_owed"] > 0
    finally:
        db.close()


def test_a_month_that_met_its_commitment_owes_nothing(client):
    tid = make_super(client)
    t, db = _tenant(tid)
    try:
        t.settings = {**(t.settings or {}), "sla_committed_pct": 99.0,
                      "circuit_monthly_cost": 300}
        db.commit()
        _outage(db, tid, "2026-09-02T01:00:00+00:00", "2026-09-02T01:10:00+00:00", "isp")
        got = sla.credit(db, t, "2026-09-01", "2026-09-30")
        assert got["shortfall_pct"] == 0.0 and got["credit_owed"] == 0.0
    finally:
        db.close()


def test_without_a_recorded_commitment_the_pack_still_measures(client):
    """A tenant who never entered their SLA still gets the minutes and the
    outage table; it just cannot compute a shortfall."""
    tid = make_super(client)
    t, db = _tenant(tid)
    try:
        _outage(db, tid, "2026-09-02T01:00:00+00:00", "2026-09-02T03:00:00+00:00", "isp")
        got = sla.credit(db, t, "2026-09-01", "2026-09-30")
        assert got["committed_pct"] is None and got["credit_owed"] == 0.0
        assert got["claimable_minutes"] == 120.0
    finally:
        db.close()


def test_the_letter_states_what_it_excludes(client):
    """A claim that hides the customer's own outages is rejected and
    remembered. Naming them is what makes the rest believable."""
    tid = make_super(client)
    t, db = _tenant(tid)
    try:
        t.settings = {**(t.settings or {}), "sla_committed_pct": 99.9,
                      "circuit_monthly_cost": 300}
        db.commit()
        _outage(db, tid, "2026-09-02T01:00:00+00:00", "2026-09-02T09:00:00+00:00", "isp")
        _outage(db, tid, "2026-09-05T01:00:00+00:00", "2026-09-05T09:00:00+00:00", "local")
        letter = sla.claim_letter(t, sla.credit(db, t, "2026-09-01", "2026-09-30"))
        assert "NOT attributed" in letter
        assert "local faults: 480.0 minutes" in letter
        assert "2026-09-05" not in letter.split("NOT attributed")[0], (
            "the local outage must not appear in the claimed list"
        )
        assert "demarcation" in letter
    finally:
        db.close()


# --------------------------------------------------------------------------
# The endpoint, and the ingest path that feeds it
# --------------------------------------------------------------------------

def test_the_pack_is_reachable_over_the_api(client):
    from tests.test_api_v1 import bearer, seeded
    tid, secret, _ = seeded(client)
    db = SessionLocal()
    try:
        _outage(db, tid, "2026-09-02T01:00:00+00:00", "2026-09-02T03:00:00+00:00", "isp")
    finally:
        db.close()
    r = client.get("/v1/sla?from_day=2026-09-01&to_day=2026-09-30", headers=bearer(secret))
    assert r.status_code == 200, r.text
    assert r.json()["claimable_minutes"] == 120.0


def test_the_letter_comes_back_as_text_to_send(client):
    from tests.test_api_v1 import bearer, seeded
    tid, secret, _ = seeded(client)
    db = SessionLocal()
    try:
        _outage(db, tid, "2026-09-02T01:00:00+00:00", "2026-09-02T03:00:00+00:00", "isp")
    finally:
        db.close()
    r = client.get("/v1/sla?from_day=2026-09-01&to_day=2026-09-30&format=letter",
                   headers=bearer(secret))
    assert r.status_code == 200 and "text/plain" in r.headers["content-type"]
    assert "service credit" in r.text.lower()


def test_a_malformed_date_is_refused_rather_than_guessed(client):
    from tests.test_api_v1 import bearer, seeded
    _tid, secret, _ = seeded(client)
    r = client.get("/v1/sla?from_day=last-tuesday", headers=bearer(secret))
    assert r.status_code == 422


def test_ingesting_a_scan_records_the_outage_it_implies(client):
    """The sweep's own timestamp, never now(), so a backfill lands outages
    where they happened and re-seeding stays idempotent."""
    from guardian_hub import ingest
    from tests.test_tenants import make_super
    tid = make_super(client)
    db = SessionLocal()
    try:
        t = db.get(m.Tenant, tid)
        ingest.store_scan(db, t, None, {"last": _scan("2026-08-11T04:00:00+00:00", UP, DOWN),
                                        "state": {}}, deliver=False)
        rows = db.query(m.WanOutage).filter_by(tenant_id=tid).all()
        assert len(rows) == 1
        assert rows[0].started_at.startswith("2026-08-11"), "stamped by the scan, not today"
        assert rows[0].cause == "isp"
    finally:
        db.close()
