"""The evidence pack as an MSP actually uses it: a document and a spreadsheet.

The spec's deliverable is "a PDF per period: every control, the answer, the
evidence behind it, the date range it covers, and an explicit list of what
Vault cannot speak to. Plus a CSV export, because whoever is filling in
the actual form is copying and pasting."

Both come off one `controls.pack`, and the test that matters most says so:
the MSP prints the PDF and their client pastes from the CSV, and a row
saying two different things across the two files gets noticed in the
meeting rather than beforehand.
"""
import csv
import io

from tests.test_api_v1 import bearer, seeded


def _json(client, secret, **q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    r = client.get(f"/v1/compliance?{qs}" if qs else "/v1/compliance",
                   headers=bearer(secret))
    assert r.status_code == 200, r.text
    return r.json()


def test_the_pack_answers_every_control_for_a_framework(client):
    _tid, secret, _ = seeded(client)
    got = _json(client, secret, framework="cyber_essentials")
    assert got["framework"] == "cyber_essentials"
    assert got["controls"]
    assert sum(got["tally"].values()) == len(got["controls"])


def test_the_pack_lists_what_guardian_cannot_speak_to(client):
    """The gaps are the product. A pack that returned only answerable rows
    would look complete, which is over-claiming in its most dangerous
    form."""
    _tid, secret, _ = seeded(client)
    got = _json(client, secret, framework="cyber_insurance")
    outside = [c for c in got["controls"] if c["status"] == "outside_scope"]
    assert outside, "an insurance form has questions Guardian cannot answer"
    assert all(c["note"].strip() for c in outside), "each gap states its reason"
    assert any(c["key"] == "mfa_enforcement" for c in outside)


def test_the_pack_carries_the_disclaimer_and_the_date_it_speaks_for(client):
    _tid, secret, _ = seeded(client)
    got = _json(client, secret)
    assert "does not make" in got["disclaimer"]
    assert got["as_of"], "a control answer without a date is not evidence"


def test_an_unknown_framework_is_404_not_an_empty_pack(client):
    """An empty pack reads as "nothing applies", which is a different and
    untrue statement."""
    _tid, secret, _ = seeded(client)
    r = client.get("/v1/compliance?framework=iso27001", headers=bearer(secret))
    assert r.status_code == 404


def test_the_csv_is_the_same_rows_as_the_json(client):
    """The MSP prints the PDF; their client pastes from the CSV. A row that
    disagrees across the two is noticed in the meeting."""
    _tid, secret, _ = seeded(client)
    got = _json(client, secret, framework="pci_dss")

    r = client.get("/v1/compliance?framework=pci_dss&format=csv", headers=bearer(secret))
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    rows = list(csv.reader(io.StringIO(r.text)))
    header, body = rows[0], [x for x in rows[1:] if x and x[0]]

    assert header[:3] == ["control", "question", "status"]
    # Last non-empty row is the disclaimer, on its own.
    assert "does not make" in body[-1][0]
    assert len(body) - 1 == len(got["controls"])
    assert [b[0] for b in body[:-1]] == [c["title"] for c in got["controls"]]
    assert [b[2] for b in body[:-1]] == [c["status"] for c in got["controls"]]


def test_the_csv_offers_itself_as_a_download(client):
    _tid, secret, _ = seeded(client)
    r = client.get("/v1/compliance?format=csv", headers=bearer(secret))
    assert "attachment" in r.headers.get("content-disposition", "")


def test_the_pack_is_scoped_to_the_callers_account(client):
    """Answered from this tenant's own latest sweep and nobody else's."""
    tid, secret, _ = seeded(client)
    got = _json(client, secret)
    from guardian_hub import models as m
    from guardian_hub.db import SessionLocal
    db = SessionLocal()
    try:
        newest = (db.query(m.Scan).filter_by(tenant_id=tid)
                  .order_by(m.Scan.ts.desc()).first())
        assert got["as_of"] == newest.ts
    finally:
        db.close()
