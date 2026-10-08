"""Read-back verifiers for the agency module's local writes (RA-D1):
prospect, audit, rank, mockup, build_site, draft_outreach, suppress.

Every one reads this box's own database and disk. The DB is faked at
`state.fetch_one` / `state.fetch_all`, and the fake refuses anything but a
SELECT. The network is faked at `httpx.AsyncClient.send` and refuses every
request: none of these read-backs needs one. Files live under tmp_path.
"""
from __future__ import annotations

import hashlib
import json
import re
import struct

import httpx
import pytest

from atlas.kernel import state
from atlas.kernel import verify as v
from atlas.kernel.contracts import ActionResult
from atlas.modules.agency.score import scores_sha

SECRET = "sk-rad1-agency-supersecret-0123456789"
STRANGER = "owner@stranger-business.example"

ACTIONS = ("agency.prospect", "agency.audit", "agency.rank", "agency.mockup",
           "agency.build_site", "agency.draft_outreach", "agency.suppress")


@pytest.fixture(autouse=True)
def no_net(monkeypatch):
    async def send(client, request, **_kw):
        raise AssertionError(f"an agency read-back went to the network: "
                             f"{request.method} {request.url}")

    monkeypatch.setattr(httpx.AsyncClient, "send", send)


@pytest.fixture(autouse=True)
def secret_env(monkeypatch):
    monkeypatch.setenv("AGENCY_SMTP_PASSWORD", SECRET)
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", SECRET)


class Db:
    """{table: {key: row}}; a SELECT's first argument is the key, or, for
    `= ANY($1)`, a list of keys."""

    def __init__(self) -> None:
        self.tables: dict[str, dict] = {}
        self.sql: list[str] = []

    def put(self, table: str, key, row: dict) -> None:
        self.tables.setdefault(table, {})[key] = row

    def _check(self, sql: str) -> str:
        assert sql.lstrip().upper().startswith("SELECT"), f"a verifier wrote: {sql}"
        self.sql.append(sql)
        return re.search(r"FROM\s+(\w+)", sql).group(1)

    async def fetch_one(self, sql, *args):
        return self.tables.get(self._check(sql), {}).get(args[0])

    async def fetch_all(self, sql, *args):
        table = self.tables.get(self._check(sql), {})
        return [table[k] for k in args[0] if k in table]


@pytest.fixture
def db(monkeypatch):
    fake = Db()
    monkeypatch.setattr(state, "fetch_one", fake.fetch_one)
    monkeypatch.setattr(state, "fetch_all", fake.fetch_all)
    return fake


def _clean(got: v.Verification) -> None:
    text = json.dumps(got.as_audit(), default=str)
    assert SECRET not in text
    assert STRANGER not in text


def _sha(text: str | bytes) -> str:
    raw = text.encode() if isinstance(text, str) else text
    return hashlib.sha256(raw).hexdigest()[:12]


@pytest.mark.parametrize("action", ACTIONS)
def test_each_action_has_a_verifier(action):
    assert v.has_verifier(action)


def test_only_suppress_has_a_reconciler():
    assert v.has_reconciler("agency.suppress")
    for action in ("agency.prospect", "agency.audit", "agency.rank",
                   "agency.mockup", "agency.build_site", "agency.draft_outreach"):
        assert not v.has_reconciler(action)


# ================================================================ prospect

PROSPECTED = ActionResult(True, "2 new lead(s)", evidence={"lead_ids": [11, 12],
                                                           "query": "plumber"},
                          rollback_token="11,12")


async def test_prospect_verified(db):
    db.put("agency_leads", 11, {"id": 11, "status": "new"})
    db.put("agency_leads", 12, {"id": 12, "status": "new"})
    got = await v.verify("agency.prospect", {"query": "plumber"}, PROSPECTED)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["lead_ids"] == [11, 12] and got.evidence["found"] == 2
    _clean(got)


async def test_prospect_contradicted_when_a_lead_is_missing(db):
    db.put("agency_leads", 11, {"id": 11, "status": "new"})
    got = await v.verify("agency.prospect", {}, PROSPECTED)
    assert got.state is v.State.CONTRADICTED and "12" in got.reason
    _clean(got)


async def test_prospect_contradicted_when_rolled_back(db):
    db.put("agency_leads", 11, {"id": 11, "status": "dropped"})
    db.put("agency_leads", 12, {"id": 12, "status": "new"})
    got = await v.verify("agency.prospect", {}, PROSPECTED)
    assert got.state is v.State.CONTRADICTED and "dropped" in got.reason


async def test_prospect_unknown_when_nothing_new(db):
    got = await v.verify("agency.prospect", {}, ActionResult(
        True, "0 new", evidence={"lead_ids": [], "query": "q"}, rollback_token=""))
    assert got.state is v.State.UNKNOWN and not db.sql


# ================================================================== audit

AUDITED = ActionResult(True, "audit", evidence={"findings": [], "severity": 5,
                                                "url": "https://x.example",
                                                "audit_id": 70},
                       rollback_token="70")


async def test_audit_verified(db):
    db.put("agency_audits", 70, {"id": 70, "lead_id": 3, "severity": 5})
    got = await v.verify("agency.audit", {"lead_id": 3}, AUDITED)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence == {"audit_id": 70, "lead_id": 3, "severity": 5,
                            "served_from_cache": False}
    _clean(got)


async def test_audit_cache_hit_reads_the_cached_row(db):
    db.put("agency_audits", 44, {"id": 44, "lead_id": 3, "severity": 5})
    cached = ActionResult(True, "served from cache", evidence={
        "severity": 5, "audit_id": 44}, rollback_token="")
    got = await v.verify("agency.audit", {"lead_id": 3}, cached)
    assert got.state is v.State.VERIFIED and got.evidence["audit_id"] == 44
    assert got.evidence["served_from_cache"] is True


@pytest.mark.parametrize("row, words", [
    (None, "not in agency_audits"),
    ({"id": 70, "lead_id": 3, "severity": -1}, "withdrawn"),
    ({"id": 70, "lead_id": 9, "severity": 5}, "lead 9"),
    ({"id": 70, "lead_id": 3, "severity": 2}, "severity 2"),
])
async def test_audit_contradicted(db, row, words):
    if row is not None:
        db.put("agency_audits", 70, row)
    got = await v.verify("agency.audit", {"lead_id": 3}, AUDITED)
    assert got.state is v.State.CONTRADICTED and words in got.reason
    _clean(got)


async def test_audit_unknown_without_an_id(db):
    got = await v.verify("agency.audit", {"lead_id": 3},
                         ActionResult(True, "old run", evidence={"severity": 1},
                                      rollback_token=""))
    assert got.state is v.State.UNKNOWN and not db.sql


# =================================================================== rank

def _f4(x: float) -> float:
    return struct.unpack("f", struct.pack("f", x))[0]


WRITTEN = {5: 0.123456789012, 6: 0.987654321098}
RANKED = ActionResult(True, "recomputed", evidence={
    "updated": 2, "scores_sha": scores_sha(WRITTEN)},
    rollback_token=json.dumps({"5": {"score": 0.1, "features": {}},
                               "6": {"score": None, "features": {}}}))


async def test_rank_verified_through_float4_rounding(db):
    # A REAL column hands back the float4 value, not the double written.
    for i, s in WRITTEN.items():
        db.put("agency_leads", i, {"id": i, "score": _f4(s)})
    assert _f4(WRITTEN[5]) != WRITTEN[5]
    got = await v.verify("agency.rank", {}, RANKED)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["ranked"] == 2
    _clean(got)


@pytest.mark.parametrize("rows, words", [
    ({5: 0.5, 6: 0.987654321098}, "differ"),
    ({5: None, 6: 0.9}, "no score"),
    ({5: 0.123456789012}, "gone"),
])
async def test_rank_contradicted(db, rows, words):
    for i, s in rows.items():
        db.put("agency_leads", i, {"id": i, "score": s})
    got = await v.verify("agency.rank", {}, RANKED)
    assert got.state is v.State.CONTRADICTED and words in got.reason


async def test_rank_unknown(db):
    got = await v.verify("agency.rank", {}, ActionResult(
        True, "recomputed 0", evidence={"updated": 0, "scores_sha": scores_sha({})},
        rollback_token="{}"))
    assert got.state is v.State.UNKNOWN and not db.sql
    for i, s in WRITTEN.items():
        db.put("agency_leads", i, {"id": i, "score": s})
    no_hash = ActionResult(True, "old run", evidence={"updated": 2},
                           rollback_token=RANKED.rollback_token)
    got = await v.verify("agency.rank", {}, no_hash)
    assert got.state is v.State.UNKNOWN and "no score hash" in got.reason


# ================================================================= mockup

def _mocked(path) -> ActionResult:
    return ActionResult(True, "mockup", evidence={"path": str(path), "screenshot": None},
                        rollback_token=json.dumps({
                            "lead_id": 3, "mockup_id": 90, "previous_status": "audited",
                            "html_path": str(path), "screenshot_path": None}))


async def test_mockup_verified(db, tmp_path):
    page = tmp_path / "3.html"
    page.write_text("<!doctype html><title>Joe</title>")
    db.put("agency_mockups", 90, {"id": 90, "lead_id": 3, "path": str(page)})
    got = await v.verify("agency.mockup", {"lead_id": 3}, _mocked(page))
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["file"]["size"] > 0 and got.evidence["row"]["mockup_id"] == 90
    _clean(got)


@pytest.mark.parametrize("case", ["no_file", "empty", "no_row", "other_lead"])
async def test_mockup_contradicted(db, tmp_path, case):
    page = tmp_path / "3.html"
    if case != "no_file":
        page.write_text("" if case == "empty" else "<html></html>")
    if case != "no_row":
        db.put("agency_mockups", 90, {"id": 90, "path": str(page),
                                      "lead_id": 4 if case == "other_lead" else 3})
    got = await v.verify("agency.mockup", {"lead_id": 3}, _mocked(page))
    assert got.state is v.State.CONTRADICTED, case


async def test_mockup_unknown(db, tmp_path):
    got = await v.verify("agency.mockup", {"lead_id": 3},
                         ActionResult(True, "?", evidence={}, rollback_token=""))
    assert got.state is v.State.UNKNOWN
    page = tmp_path / "3.html"
    page.write_text("<html></html>")
    got = await v.verify("agency.mockup", {"lead_id": 3}, ActionResult(
        True, "?", evidence={"path": str(page)},
        rollback_token=json.dumps({"mockup_id": None})))
    assert got.state is v.State.UNKNOWN and "no agency_mockups id" in got.reason


# ============================================================= build_site

def _site(tmp_path, html="<!doctype html><title>Joe's</title>"):
    site = tmp_path / "joes-3"
    site.mkdir()
    (site / "index.html").write_text(html)
    return site


def _built(site, sha) -> ActionResult:
    evidence = {"slug": "joes-3", "path": str(site), "kind": "brochure"}
    if sha is not None:
        evidence["index_sha"] = sha
    return ActionResult(True, "built", evidence=evidence, rollback_token="{}")


async def test_build_site_verified(db, tmp_path):
    site = _site(tmp_path)
    db.put("agency_sites", "joes-3", {"id": 8, "lead_id": 3, "slug": "joes-3",
                                      "path": str(site)})
    sha = _sha((site / "index.html").read_bytes())
    got = await v.verify("agency.build_site", {"lead_id": 3}, _built(site, sha))
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["hash"]["index_sha"] == sha
    _clean(got)


@pytest.mark.parametrize("case", ["no_row", "other_path", "no_index", "changed"])
async def test_build_site_contradicted(db, tmp_path, case):
    site = _site(tmp_path)
    sha = _sha((site / "index.html").read_bytes())
    if case != "no_row":
        db.put("agency_sites", "joes-3", {
            "id": 8, "lead_id": 3, "slug": "joes-3",
            "path": "/elsewhere" if case == "other_path" else str(site)})
    if case == "no_index":
        (site / "index.html").unlink()
    if case == "changed":
        (site / "index.html").write_text("<html>edited later</html>")
    got = await v.verify("agency.build_site", {"lead_id": 3}, _built(site, sha))
    assert got.state is v.State.CONTRADICTED, case


async def test_build_site_unknown(db, tmp_path):
    got = await v.verify("agency.build_site", {"lead_id": 3},
                         ActionResult(True, "?", evidence={}))
    assert got.state is v.State.UNKNOWN and not db.sql
    site = _site(tmp_path)
    db.put("agency_sites", "joes-3", {"id": 8, "lead_id": 3, "slug": "joes-3",
                                      "path": str(site)})
    got = await v.verify("agency.build_site", {"lead_id": 3}, _built(site, None))
    assert got.state is v.State.UNKNOWN and "no index.html hash" in got.reason


# ========================================================= draft_outreach

BODY = f"Hi Joe, your site is slow.\n\nReply STOP to opt out. {STRANGER}"
DRAFTED = ActionResult(True, "drafted", evidence={
    "outreach_id": 42, "kind": "email", "followup_of": None, "lead_features": {},
    "audit_severity": 2, "body_sha": _sha(BODY)}, rollback_token="42")


def _draft_row(**over):
    row = {"id": 42, "lead_id": 3, "kind": "email", "status": "draft", "body": BODY}
    row.update(over)
    return row


async def test_draft_verified(db):
    db.put("agency_outreach", 42, _draft_row())
    got = await v.verify("agency.draft_outreach", {"lead_id": 3, "kind": "email"},
                         DRAFTED)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence["body_sha"] == _sha(BODY) and got.evidence["status"] == "draft"
    _clean(got)


@pytest.mark.parametrize("row, words", [
    (None, "not in agency_outreach"),
    (_draft_row(status="sent"), "'sent'"),
    (_draft_row(body="something else"), "body differs"),
    (_draft_row(lead_id=9), "lead 9"),
    (_draft_row(kind="call_script"), "call_script"),
])
async def test_draft_contradicted(db, row, words):
    if row is not None:
        db.put("agency_outreach", 42, row)
    got = await v.verify("agency.draft_outreach", {"lead_id": 3, "kind": "email"},
                         DRAFTED)
    assert got.state is v.State.CONTRADICTED and words in got.reason
    _clean(got)


async def test_draft_unknown(db):
    got = await v.verify("agency.draft_outreach", {"lead_id": 3, "kind": "email"},
                         ActionResult(True, "?", evidence={}, rollback_token=""))
    assert got.state is v.State.UNKNOWN and not db.sql
    db.put("agency_outreach", 42, _draft_row())
    no_hash = ActionResult(True, "old run", evidence={"outreach_id": 42})
    got = await v.verify("agency.draft_outreach", {"lead_id": 3, "kind": "email"},
                         no_hash)
    assert got.state is v.State.UNKNOWN and "no body hash" in got.reason


# =============================================================== suppress

SUPPRESSED = ActionResult(True, "suppressed", rollback_token=STRANGER)


async def test_suppress_verified_and_hides_the_address(db):
    db.put("agency_suppression", STRANGER, {"address": STRANGER, "reason": "asked"})
    got = await v.verify("agency.suppress",
                         {"address": "  Owner@Stranger-Business.example ",
                          "reason": "asked"}, SUPPRESSED)
    assert got.state is v.State.VERIFIED, got.reason
    assert got.evidence == {"suppressed": True, "reason_matches": True,
                            "address_sha": _sha(STRANGER)}
    _clean(got)


async def test_suppress_normalises_the_param_without_a_token(db):
    db.put("agency_suppression", STRANGER, {"address": STRANGER, "reason": "asked"})
    got = await v.verify("agency.suppress", {"address": " OWNER@stranger-business"
                                             ".example", "reason": "asked"},
                         ActionResult(True, "suppressed"))
    assert got.state is v.State.VERIFIED


async def test_suppress_contradicted(db):
    got = await v.verify("agency.suppress", {"address": STRANGER, "reason": "asked"},
                         SUPPRESSED)
    assert got.state is v.State.CONTRADICTED
    _clean(got)


async def test_suppress_unknown(db):
    got = await v.verify("agency.suppress", {"address": " "},
                         ActionResult(True, "?", rollback_token=""))
    assert got.state is v.State.UNKNOWN and not db.sql


async def test_suppress_reconciler(db):
    params = {"address": STRANGER.upper(), "reason": "asked"}
    START = {"at": "2026-09-28T10:00:00+00:00", "payload": {"params": params}}
    got = await v.reconcile_unknown("agency.suppress", params,
                                    START)
    assert got.state is v.State.CONTRADICTED
    db.put("agency_suppression", STRANGER, {"address": STRANGER, "reason": "other"})
    got = await v.reconcile_unknown("agency.suppress", params,
                                    START)
    assert got.state is v.State.UNKNOWN and "another reason" in got.reason
    db.put("agency_suppression", STRANGER, {"address": STRANGER, "reason": "asked"})
    got = await v.reconcile_unknown("agency.suppress", params,
                                    START)
    assert got.state is v.State.VERIFIED
    _clean(got)


# ======================================================== raising reads

RAISING = [
    ("agency.prospect", {}, PROSPECTED),
    ("agency.audit", {"lead_id": 3}, AUDITED),
    ("agency.rank", {}, RANKED),
    ("agency.build_site", {"lead_id": 3}, _built("/nowhere/joes-3", "abc")),
    ("agency.draft_outreach", {"lead_id": 3, "kind": "email"}, DRAFTED),
    ("agency.suppress", {"address": STRANGER}, SUPPRESSED),
    ("agency.mockup", {"lead_id": 3}, _mocked("/nowhere/3.html")),
]


@pytest.mark.parametrize("action, params, result", RAISING,
                         ids=[r[0] for r in RAISING])
async def test_a_raising_read_is_unknown(monkeypatch, action, params, result):
    async def boom(sql, *args):
        raise ConnectionError(f"db down {SECRET}")

    monkeypatch.setattr(state, "fetch_one", boom)
    monkeypatch.setattr(state, "fetch_all", boom)
    got = await v.verify(action, params, result)
    assert got.state is v.State.UNKNOWN and "raised" in got.reason
    _clean(got)
