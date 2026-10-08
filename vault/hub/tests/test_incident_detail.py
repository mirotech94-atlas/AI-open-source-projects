"""Task 83, pattern A1/A2: the incident carries a written narrative, and one
incident is reachable as one detail payload -- story, timeline, the assets
involved, a scoped graph and the underlying evidence.

Both tests run against the seeded demo tenant's REAL fixture data (see
`tests.test_seed_helpers.seeded_demo` / `hub/tests/fixtures/last.json`, rebased
and host-name-scrubbed to `booking`/`records`/`clinic-nas` by
`tests.scanfixture`), never a hand-rolled incident row -- the whole point of
A1 is that the narrative is computed from data Vault actually recorded, so
a test that fabricates its own incident would not catch a narrative module
that fabricates its own text.
"""

from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m


def _bruteforce_incident_iid(client) -> str:
    rows = client.get("/api/v1/inbox?view=incidents").json()["rows"]
    row = next(r for r in rows if r["entity"] == "host:booking")
    return row["id"]


def test_incident_card_carries_a_written_narrative_not_a_database_key(client):
    """A1: the incidents-view row for a real correlated incident reads as a
    sentence, not `host:booking: WARN incident (1 alerts)`, and its confidence
    is the real average of the alerts Vault scored -- not null, not a
    made-up number."""
    seeded_demo(client)
    rows = client.get("/api/v1/inbox?view=incidents").json()["rows"]
    row = next(r for r in rows if r["entity"] == "host:booking")

    assert "WARN incident (" not in row["title"], "still the raw key+count title"
    assert not row["title"].startswith("20"), "still a raw ISO timestamp glued to the front"
    # The one alert under this incident carries msg "booking: 192.0.2.107 made
    # 362 failed SSH logins in 24h" and confidence 95 -- the card's numbers
    # must be traceable to that, not invented.
    assert "362" in row["title"] or "362" in row["why"] or "362" in row["what_changed"]
    assert row["confidence"] == 95


def test_incident_detail_endpoint_is_role_readable_and_self_consistent(client):
    """A2: one incident, one URL, one payload -- narrative, timeline, the
    handful of assets it touched, a scoped graph over just those assets, and
    the evidence rows the narrative was built from. Viewer-readable (spec:
    employees/viewers read everything; only write controls are role-gated)."""
    seeded_demo(client)
    iid = _bruteforce_incident_iid(client)

    r = client.get(f"/api/v1/incidents/{iid}")
    assert r.status_code == 200, r.text
    d = r.json()

    assert d["id"] == iid
    assert d["entity"] == "host:booking"
    assert d["alert_count"] == 1

    n = d["narrative"]
    assert n["what_happened"] != "" and "362" in n["what_happened"]
    assert n["confidence"] == 95
    assert "1 alert" in n["confidence_basis"]
    # sec:attacker:{host}:{ip} resolves to a real remedy (block_ip, in
    # remedy.py's ported fix catalogue) -- "what happens next" must not be
    # empty for an incident Vault actually knows how to act on, and must
    # match the alert's own remedy copy rather than inventing new wording.
    assert n["what_next"] != ""
    assert "block" in n["what_next"].lower() and "booking" in n["what_next"]

    assert len(d["timeline"]) >= 1
    assert d["timeline"][0]["text"] != ""

    names = {a["name"] for a in d["assets"]}
    assert "booking" in names
    booking_asset = next(a for a in d["assets"] if a["name"] == "booking")
    assert booking_asset["kind"] == "host"

    assert len(d["evidence"]) == 1
    assert d["evidence"][0]["seen"] >= 1

    node_ids = {node["id"] for node in d["graph"]["nodes"]}
    assert node_ids, "a scoped graph with zero nodes tells the owner nothing"
    # scoped, not the whole topology: nowhere near the device count of a
    # full-tenant map
    assert len(node_ids) <= 6


def test_incident_detail_404s_for_an_unknown_incident_id(client):
    seeded_demo(client)
    r = client.get("/api/v1/incidents/does-not-exist")
    assert r.status_code == 404


def test_incident_detail_never_names_a_repair_the_role_may_not_run(client):
    """A viewer may read the whole incident, including what a repair WOULD do.

    The contract changed with F6: `actions` used to be emptied for a role that
    could not run them, which left a viewer looking at a row with none of the
    verbs everyone talks about and no explanation. The hub now sends every
    action tagged with the `tier` it needs, and the UI renders them disabled
    with a title naming the role -- explain, do not remove. The refusal that
    matters is server-side and unchanged: every one of these routes is behind
    `require_role("operator")` or `require_role("admin")` in `routers/write.py`,
    so a viewer POSTing one verbatim is refused 403 whatever the payload says.

    This test therefore asserts the new contract: the actions are present, and
    every one of them is tiered above a viewer."""
    seeded_demo(client)
    db = SessionLocal()
    tenant = db.query(m.Tenant).filter_by(slug="demo").first() or db.query(m.Tenant).first()
    for mem in db.query(m.Membership).filter_by(tenant_id=tenant.id).all():
        mem.role = "viewer"
    db.commit()
    db.close()

    iid = _bruteforce_incident_iid(client)
    r = client.get(f"/api/v1/incidents/{iid}")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["actions"], "a viewer is shown the verbs, disabled, not an empty row"
    assert all(a["tier"] >= 2 for a in d["actions"]), (
        "every action offered here needs a role above viewer, so the UI can "
        "disable each one and say which role it needs"
    )
    # and the server refuses it regardless of what the payload offered
    refused = client.post(d["actions"][0]["path"], json=d["actions"][0]["body"])
    assert refused.status_code == 403, refused.text
    # the remedy is still described in plain language for a viewer to read
    assert d["remedy"] is not None and d["remedy"]["kind"] != "none"
