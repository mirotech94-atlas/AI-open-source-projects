"""The client portal's Coverage and Certificates-expiring cards.

Both come from the design reference's Overview, and both answer questions the
hub could previously only answer by implication.

COVERAGE is the important one, and it is not a count of what exists. The hub
upserts whatever the sensor sends and never reconciles what it stopped
sending: drop a site from the sensor's config and its row keeps its last
result forever, presenting a frozen check as a current finding. That is why
`payloads/staleness` exists, and it means "12 certificates" can be perfectly
true while nothing has looked at four of them for a month. The card is the
difference between what Vault HAS and what Vault can currently SEE.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import overview
from tests.test_auth import login, seed_user


def _tenant():
    _uid, tid = seed_user(role="admin")
    return tid


def _sweeps(tid: str, count: int = 3) -> None:
    """Enough scans for staleness to have an opinion.

    `stale_cutoff` returns None until a tenant has `STALE_AFTER_SWEEPS` scans
    on record -- with fewer sweeps than the window is wide there is no evidence
    anything was skipped, only that the hub is new. A fixture without them
    measures nothing, which is how the first version of the test below passed
    while proving the opposite of its name.
    """
    db = SessionLocal()
    now = dt.datetime.now(dt.timezone.utc)
    for i in range(count):
        db.add(m.Scan(tenant_id=tid, ts=(now - dt.timedelta(hours=i)).isoformat(), raw={}))
    db.commit(); db.close()


def _site(tid: str, name: str, *, last_seen_days_ago: int | None = 0, cert_days=None):
    db = SessionLocal()
    seen = None
    if last_seen_days_ago is not None:
        seen = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=last_seen_days_ago)).isoformat()
    last = {} if cert_days is None else {"cert_days": cert_days, "cert_issuer": "Let's Encrypt R11"}
    db.add(m.Site(tenant_id=tid, name=name, url=f"https://{name}", last=last, last_seen=seen))
    db.commit(); db.close()


def _coverage(tid: str) -> dict[str, tuple[int, int]]:
    db = SessionLocal()
    rows = overview.coverage_rows(db, tid, [])
    db.close()
    return {r.label: (r.seen, r.total) for r in rows}


def test_a_site_the_sensor_stopped_reporting_counts_as_unseen(client):
    """The whole point of the card.

    A row nothing is checking still renders its last result everywhere else in
    the product. Counting it as covered would make this card agree with the
    stale row rather than contradict it, which is the one job it has.
    """
    tid = _tenant()
    _sweeps(tid)
    _site(tid, "live.example", last_seen_days_ago=0)
    _site(tid, "frozen.example", last_seen_days_ago=45)

    seen, total = _coverage(tid)["Websites"]
    assert total == 2
    assert seen == 1, "the site nothing has reported for 45 days is not covered"


def test_a_brand_new_tenant_is_not_told_its_whole_estate_is_unseen(client):
    """Fewer sweeps on record than the window is wide means "judge nothing".

    Without this, a customer's first hour would show every asset as uncovered
    -- the product accusing itself of not working on the day it was installed.
    """
    tid = _tenant()  # no scans at all
    _site(tid, "fresh.example", last_seen_days_ago=45)
    assert _coverage(tid)["Websites"] == (1, 1)


def test_certificates_are_counted_against_the_sites_that_should_have_one(client):
    """`seen / total` is certificates READ over sites, not certificates over
    certificates -- which would always be 100% and say nothing."""
    tid = _tenant()
    _site(tid, "secure.example", cert_days=60)
    _site(tid, "nocert.example", cert_days=None)

    assert _coverage(tid)["Certificates"] == (1, 2)


def test_only_certificates_inside_the_renewal_window_are_listed(client):
    """Ninety days, soonest first.

    A certificate with a year left is not news, and listing it would bury the
    one with nine days left under things that are fine.
    """
    tid = _tenant()
    _site(tid, "soon.example", cert_days=9)
    _site(tid, "later.example", cert_days=27)
    _site(tid, "fine.example", cert_days=300)
    _site(tid, "none.example", cert_days=None)

    db = SessionLocal()
    rows = overview.certs_expiring(db, tid)
    db.close()

    assert [r.host for r in rows] == ["soon.example", "later.example"]
    assert rows[0].days_left == 9
    assert rows[0].issuer == "Let's Encrypt R11"


def test_a_class_with_nothing_in_it_is_left_out_rather_than_shown_as_zero(client):
    """A tenant with no servers should not be told they are covering 0 of 0.

    A row reading "0 / 0" is not a gap, it is an absence, and presenting it as
    a measurement invites someone to go looking for the missing servers.
    """
    tid = _tenant()
    _site(tid, "only.example")
    cov = _coverage(tid)
    assert "Websites" in cov
    assert "Servers" not in cov


def test_the_payload_carries_both_cards(client):
    """End to end, through the real endpoint, because a builder that works and
    a payload that ships it are different claims."""
    tid = _tenant()
    _site(tid, "soon.example", cert_days=5)
    login(client)
    body = client.get("/api/v1/overview").json()

    assert any(r["label"] == "Websites" for r in body["coverage"])
    assert body["certs_expiring"][0]["host"] == "soon.example"
    assert body["certs_expiring"][0]["days_left"] == 5
