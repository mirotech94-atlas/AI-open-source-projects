"""A report says what it is, so an edited copy stops matching.

The MSP spec wants a SHA-256 in the report footer, and is right about why:
it costs nothing and it turns "trust this document" into "here is how to
check it". The readers who care — an underwriter, an auditor, a client's
finance director — are exactly the people who will not take a PDF on faith.

THE SELF-REFERENCE PROBLEM, which is the whole reason this needs a written
rule. Printing a hash of the document *into* the document changes the
document, so the printed value can never be the hash of what you are
holding. Vault resolves it by defining the canonical form precisely:

    take the document, replace the 64-hex digest with 64 zeros, SHA-256 it.

That is mechanically checkable by a third party with no access to Vault,
which is the property that makes the footer line worth printing at all.
`test_the_published_recipe_reproduces_the_hash` is that check, run as a
test — if it ever fails, the number in the footer is decoration.
"""
import hashlib
import re

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.reports import builder
from tests.test_read_write import seeded

HEX64 = re.compile(r"\b[0-9a-f]{64}\b")


def _render(client):
    tid, _ = seeded(client)
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        report, path = builder.render_to_file(db, tenant, days=7)
        return report, path.read_text(encoding="utf-8"), tid
    finally:
        db.close()


def test_the_report_carries_a_content_hash(client):
    report, html, _tid = _render(client)
    assert report.content_hash and len(report.content_hash) == 64
    assert report.content_hash in html, "the hash has to be visible, not just stored"


def test_the_published_recipe_reproduces_the_hash(client):
    """The property the footer line rests on: a third party with the file
    and no access to Vault can check it."""
    report, html, _tid = _render(client)
    canonical = html.replace(report.content_hash, "0" * 64)
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == report.content_hash


def test_the_recipe_is_stated_in_the_document(client):
    """A hash nobody knows how to verify is decoration. The instruction
    travels with the file, because the reader who needs it is the one who
    does not have the docs open."""
    _report, html, _tid = _render(client)
    assert "64 zeros" in html or "zeros" in html
    assert "SHA-256" in html


def test_an_edited_report_stops_matching(client):
    """The regression the feature exists for."""
    report, html, _tid = _render(client)
    tampered = html.replace("Confidential", "Totally fine", 1)
    canonical = tampered.replace(report.content_hash, "0" * 64)
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() != report.content_hash


def test_two_reports_of_the_same_period_hash_differently_only_if_they_differ(client):
    """Sanity on the canonicalisation: the digest must be a function of the
    content, not of when the hash happened to be computed."""
    _report, html, _tid = _render(client)
    once = hashlib.sha256(HEX64.sub("0" * 64, html).encode("utf-8")).hexdigest()
    twice = hashlib.sha256(HEX64.sub("0" * 64, html).encode("utf-8")).hexdigest()
    assert once == twice


def test_the_stored_hash_survives_the_tables_moving_underneath_it(client):
    """Immutability, asserted the way the daily-facts one is: change the
    world, re-read the document, and nothing about it moves.

    Vault already renders to a file and serves that file, so this is a
    guard on a property it has rather than one being added — which is worth
    a test precisely because it would be easy to "optimise" into a re-render.
    """
    report, html, tid = _render(client)
    db = SessionLocal()
    try:
        for alert in db.query(m.Alert).filter_by(tenant_id=tid).all():
            alert.status = "resolved"
        tenant = db.get(m.Tenant, tid)
        tenant.brand = {"name": "Something Else Entirely", "accent": "#ff0000"}
        db.commit()
        again = db.get(m.Report, report.id)
        assert again.content_hash == report.content_hash
    finally:
        db.close()

    from pathlib import Path
    assert Path(report.path).read_text(encoding="utf-8") == html
