"""Read-back verifiers for ATLAS's internal modules (RA-D1): marketing,
nightshift (run, research), school, vault, lessons, research, rhythm and
linkedin.approve_draft.

Every effect here is a row or file ATLAS owns. DB reads are faked at
`state.fetch_one` / `state.fetch_all`, and the fake refuses anything but a
SELECT. Files live under tmp_path. Each verifier is covered for verified,
contradicted and unknown; a raising read is unknown; and a secret held in
the environment (and in the rows' free text) never reaches the audit shape.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, date, datetime, timedelta

import pytest

from atlas.kernel import state
from atlas.kernel import verify as v
from atlas.kernel.contracts import ActionResult

SECRET = "sk-rad1-internal-supersecret-0123456789"
T0 = datetime(2026, 9, 28, 10, 0, tzinfo=UTC)

ACTIONS = ("marketing.propose_experiment", "marketing.propose_idea",
           "marketing.record_outcome", "nightshift.run", "nightshift.research",
           "vault.log_reading",
           "vault.reindex", "lessons.clear", "research.report",
           "rhythm.note_circumstance", "linkedin.approve_draft")


def sha(text: str, n: int = 12) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:n]


class DB:
    """{table: row | None | callable(sql, args) | Exception}. SELECT only."""

    def __init__(self) -> None:
        self.rows: dict[str, object] = {}
        self.seen: list[str] = []

    def answer(self, sql: str, args: tuple) -> object:
        assert sql.lstrip().upper().startswith("SELECT"), f"a verifier wrote: {sql}"
        table = re.search(r"FROM\s+(\w+)", sql).group(1)
        self.seen.append(table)
        got = self.rows.get(table)
        if isinstance(got, Exception):
            raise got
        if callable(got):
            return got(sql, args)
        return got

    def __setitem__(self, table: str, value: object) -> None:
        self.rows[table] = value


@pytest.fixture
def db(monkeypatch):
    fake = DB()

    async def fetch_one(sql, *args):
        return fake.answer(sql, args)

    async def fetch_all(sql, *args):
        got = fake.answer(sql, args)
        return list(got or [])

    async def refuse(sql, *args):
        raise AssertionError(f"a verifier wrote: {sql}")

    monkeypatch.setattr(state, "fetch_one", fetch_one)
    monkeypatch.setattr(state, "fetch_all", fetch_all)
    monkeypatch.setattr(state, "execute_sql", refuse)
    monkeypatch.setenv("RAD1_INTERNAL_TOKEN", SECRET)
    return fake


def _no_secret(got: v.Verification) -> None:
    assert SECRET not in json.dumps(got.as_audit(), default=str)


@pytest.mark.parametrize("action", ACTIONS)
def test_each_action_has_a_verifier(action):
    assert v.has_verifier(action)


# ================================================================== marketing

PE_PARAMS = {"business": "flycheap", "platform": "instagram",
             "hypothesis": "route-deal reels beat explainers", "variant": "deal reel",
             "draft_kind": "short_script", "draft_source": "weekly"}
PE_RESULT = ActionResult(True, "experiment #5", evidence={
    "experiment_id": 5, "draft_id": 9, "variant": "deal reel", "metric": None},
    rollback_token="5:9")


def _experiment(**over):
    return {"business": "flycheap", "platform": "instagram",
            "hypothesis": PE_PARAMS["hypothesis"], "variant": "deal reel",
            "draft_id": 9, "outcome": None, "lesson": None, "measured_at": None,
            **over}


async def test_propose_experiment(db):
    db["marketing_experiments"] = _experiment()
    db["content_drafts"] = {"kind": "short_script", "status": "draft"}
    got = await v.verify("marketing.propose_experiment", PE_PARAMS, PE_RESULT)
    assert got.state is v.State.VERIFIED
    assert got.evidence["experiment"]["experiment_id"] == 5
    assert got.evidence["draft"]["status"] == "draft"

    db["marketing_experiments"] = _experiment(outcome="unmeasured",
                                              lesson="withdrawn before publishing")
    got = await v.verify("marketing.propose_experiment", PE_PARAMS, PE_RESULT)
    assert got.state is v.State.CONTRADICTED and "withdrawn" in got.reason

    db["marketing_experiments"] = _experiment()
    db["content_drafts"] = {"kind": "short_script", "status": "dropped"}
    got = await v.verify("marketing.propose_experiment", PE_PARAMS, PE_RESULT)
    assert got.state is v.State.CONTRADICTED and "dropped" in got.reason

    db["content_drafts"] = {"kind": "short_script", "status": "draft"}
    db["marketing_experiments"] = _experiment(variant="explainer")
    got = await v.verify("marketing.propose_experiment", PE_PARAMS, PE_RESULT)
    assert got.state is v.State.CONTRADICTED and "variant" in got.reason

    db["marketing_experiments"] = None
    got = await v.verify("marketing.propose_experiment", PE_PARAMS, PE_RESULT)
    assert got.state is v.State.CONTRADICTED

    got = await v.verify("marketing.propose_experiment", PE_PARAMS, ActionResult(True))
    assert got.state is v.State.UNKNOWN


PI_PARAMS = {"business": "guardian", "idea": "affiliate router links",
             "mechanism": "affiliate", "evidence": {}}
PI_RESULT = ActionResult(True, "idea", evidence={"id": 3, "mechanism": "affiliate"},
                         rollback_token="3")


async def test_propose_idea(db):
    db["monetization_ideas"] = {"business": "guardian", "idea": PI_PARAMS["idea"],
                                "mechanism": "affiliate", "status": "proposed"}
    got = await v.verify("marketing.propose_idea", PI_PARAMS, PI_RESULT)
    assert got.state is v.State.VERIFIED
    assert got.evidence["idea_sha256"] == sha(PI_PARAMS["idea"])

    db["monetization_ideas"] = {**db.rows["monetization_ideas"], "status": "dropped"}
    got = await v.verify("marketing.propose_idea", PI_PARAMS, PI_RESULT)
    assert got.state is v.State.CONTRADICTED and "dropped" in got.reason

    db["monetization_ideas"] = {"business": "guardian", "idea": "something else",
                                "mechanism": "affiliate", "status": "proposed"}
    got = await v.verify("marketing.propose_idea", PI_PARAMS, PI_RESULT)
    assert got.state is v.State.CONTRADICTED and "idea" in got.reason

    db["monetization_ideas"] = None
    got = await v.verify("marketing.propose_idea", PI_PARAMS, PI_RESULT)
    assert got.state is v.State.CONTRADICTED
    got = await v.verify("marketing.propose_idea", PI_PARAMS, ActionResult(True))
    assert got.state is v.State.UNKNOWN


RO_LESSON = f"deal reels won by 40% at 48h ({SECRET})"
RO_PARAMS = {"experiment_id": 5, "outcome": "won", "lesson": RO_LESSON}
RO_RESULT = ActionResult(True, "closed", evidence={
    "experiment_id": 5, "outcome": "won", "memory_id": 77}, rollback_token="5:77")


async def test_record_outcome(db):
    from atlas.kernel.marketing.playbook import NAMESPACE
    db["marketing_experiments"] = _experiment(outcome="won", lesson=RO_LESSON,
                                              measured_at=T0)
    db["memories"] = {"namespace": NAMESPACE, "content": RO_LESSON}
    got = await v.verify("marketing.record_outcome", RO_PARAMS, RO_RESULT)
    assert got.state is v.State.VERIFIED
    _no_secret(got)

    db["memories"] = None           # the rollback deletes the lesson
    got = await v.verify("marketing.record_outcome", RO_PARAMS, RO_RESULT)
    assert got.state is v.State.CONTRADICTED and "gone" in got.reason

    db["memories"] = {"namespace": NAMESPACE, "content": RO_LESSON}
    db["marketing_experiments"] = _experiment()        # reopened
    got = await v.verify("marketing.record_outcome", RO_PARAMS, RO_RESULT)
    assert got.state is v.State.CONTRADICTED

    got = await v.verify("marketing.record_outcome", {}, ActionResult(True))
    assert got.state is v.State.UNKNOWN


async def test_record_outcome_reconciler(db):
    db["marketing_experiments"] = _experiment(outcome="won", lesson=RO_LESSON,
                                              measured_at=T0 + timedelta(seconds=5))
    got = await v.reconcile_unknown("marketing.record_outcome", RO_PARAMS, {"at": T0})
    assert got.state is v.State.VERIFIED
    db["marketing_experiments"] = _experiment(outcome="won", lesson=RO_LESSON,
                                              measured_at=T0 - timedelta(days=1))
    got = await v.reconcile_unknown("marketing.record_outcome", RO_PARAMS, {"at": T0})
    assert got.state is v.State.UNKNOWN and got.failure_class is v.FailureClass.STALE
    db["marketing_experiments"] = _experiment()
    got = await v.reconcile_unknown("marketing.record_outcome", RO_PARAMS, {"at": T0})
    assert got.state is v.State.CONTRADICTED


# ================================================================== nightshift

NR_PARAMS = {"task": "fix the flaky test", "repo": "https://code.matrix.example.com/Miro/ai"}
NR_RESULT = ActionResult(True, "running", evidence={
    "job_id": 42, "container": "nightshift-42", "branch": "nightshift/job-42"},
    rollback_token="42")


def _job(**over):
    return {"status": "running", "kind": "code", "task": NR_PARAMS["task"],
            "branch": "nightshift/job-42", "container": "nightshift-42", **over}


@pytest.mark.parametrize("status", ["queued", "running", "done", "failed"])
async def test_nightshift_run_verified(db, status):
    db["nightshift_jobs"] = _job(status=status)
    got = await v.verify("nightshift.run", NR_PARAMS, NR_RESULT)
    assert got.state is v.State.VERIFIED
    assert got.evidence["status"] == status


async def test_nightshift_run_contradicted_unknown(db):
    db["nightshift_jobs"] = _job(status="stopped")
    got = await v.verify("nightshift.run", NR_PARAMS, NR_RESULT)
    assert got.state is v.State.CONTRADICTED and "stopped" in got.reason
    db["nightshift_jobs"] = _job(container="someone-else")
    got = await v.verify("nightshift.run", NR_PARAMS, NR_RESULT)
    assert got.state is v.State.CONTRADICTED and "container" in got.reason
    db["nightshift_jobs"] = _job(task="another task")
    got = await v.verify("nightshift.run", NR_PARAMS, NR_RESULT)
    assert got.state is v.State.CONTRADICTED
    db["nightshift_jobs"] = None
    got = await v.verify("nightshift.run", NR_PARAMS, NR_RESULT)
    assert got.state is v.State.CONTRADICTED
    got = await v.verify("nightshift.run", NR_PARAMS, ActionResult(True))
    assert got.state is v.State.UNKNOWN


QUESTION = "what do budget airlines charge for bags"
BODY = f"Bags cost $30 [1].\n\n## Sources\n1. example ({SECRET})"
NRS_RESULT = ActionResult(True, "filed", evidence={
    "job_id": 8, "report_id": 501, "sources": 2, "unverified": [],
    "research_id": 61, "body_sha256": sha(BODY)},
    rollback_token=json.dumps({"job": 8, "report": "61"}))
RR_RESULT = ActionResult(True, "filed", evidence={
    "report_id": 501, "sources": 2, "unverified": [], "research_id": 61,
    "body_sha256": sha(BODY)}, rollback_token="61")


def _research(**over):
    return {"question": QUESTION, "status": "done", "body": BODY, "report_id": 501,
            **over}


async def test_nightshift_research(db):
    db["nightshift_jobs"] = {"status": "done", "kind": "research", "task": QUESTION,
                             "branch": "", "container": None}
    db["research_reports"] = _research()
    db["reports"] = {"kind": "research"}
    got = await v.verify("nightshift.research", {"question": QUESTION}, NRS_RESULT)
    assert got.state is v.State.VERIFIED
    assert got.evidence["report"]["research_id"] == 61
    _no_secret(got)

    db["nightshift_jobs"] = {"status": "stopped", "kind": "research", "task": QUESTION}
    got = await v.verify("nightshift.research", {"question": QUESTION}, NRS_RESULT)
    assert got.state is v.State.CONTRADICTED and "job" in got.reason

    db["nightshift_jobs"] = {"status": "done", "kind": "research", "task": QUESTION}
    db["research_reports"] = _research(status="withdrawn")
    got = await v.verify("nightshift.research", {"question": QUESTION}, NRS_RESULT)
    assert got.state is v.State.CONTRADICTED and "withdrawn" in got.reason

    got = await v.verify("nightshift.research", {"question": QUESTION},
                         ActionResult(True))
    assert got.state is v.State.UNKNOWN


# ================================================================== research

async def test_research_report(db):
    db["research_reports"] = _research()
    db["reports"] = {"kind": "research"}
    got = await v.verify("research.report", {"question": QUESTION}, RR_RESULT)
    assert got.state is v.State.VERIFIED
    assert got.evidence["body_sha256"] == sha(BODY)
    _no_secret(got)

    db["research_reports"] = _research(body="a different body")
    got = await v.verify("research.report", {"question": QUESTION}, RR_RESULT)
    assert got.state is v.State.CONTRADICTED and "body" in got.reason

    db["research_reports"] = _research()
    db["reports"] = None
    got = await v.verify("research.report", {"question": QUESTION}, RR_RESULT)
    assert got.state is v.State.CONTRADICTED and "missing" in got.reason

    db["research_reports"] = None
    got = await v.verify("research.report", {"question": QUESTION}, RR_RESULT)
    assert got.state is v.State.CONTRADICTED

    got = await v.verify("research.report", {"question": QUESTION}, ActionResult(True))
    assert got.state is v.State.UNKNOWN


async def test_run_report_records_the_research_id_and_body_hash(monkeypatch):
    """The evidence the read-back needs is recorded by `run_report` itself."""
    from atlas.modules.research import report

    async def leads(_q):
        return [{"url": "https://example.com/a", "title": "A"}], None

    async def fetch_text(_url):
        return "Bags cost $30 on most carriers.", None

    class Done:
        text = "Bags cost $30 [1]."

    async def think(*_a, **_kw):
        return Done()

    ids = iter([{"id": 501}, {"id": 61}])

    async def fetch_one(sql, *args):
        return next(ids)

    async def keep(*_a, **_kw):
        return None

    from atlas.modules.vault import library
    monkeypatch.setattr(report, "perplexity_leads", leads)
    monkeypatch.setattr(report, "fetch_text", fetch_text)
    monkeypatch.setattr(report, "think", think)
    monkeypatch.setattr(report, "fetch_one", fetch_one)
    monkeypatch.setattr(library, "keep_research", keep)
    monkeypatch.setattr(report, "_FINISHED", [])
    got = await report.run_report(QUESTION)
    assert got.ok
    assert got.evidence["research_id"] == 61
    assert len(got.evidence["body_sha256"]) == 12


# ================================================================== school



DW_BODY = f"Lab 1 answers ({SECRET})"
DW_RESULT = ActionResult(True, "drafted", evidence={
    "id": 12, "work_kind": "handwritten", "dir": "/tmp/whatever",
    "files": ["README.md", "answers.md"], "body_sha256": sha(DW_BODY)},
    rollback_token=json.dumps({"draft": 12, "dir": "phy-120/7-lab-1"}))










# ================================================================== vault

LR_PARAMS = {"kind": "Book", "title": "Designing Data-Intensive Applications",
             "notes": f"replication lag matters ({SECRET})"}
LR_RESULT = ActionResult(True, "logged", evidence={"id": 4, "memory_id": 90},
                         rollback_token="4|90")


async def test_vault_log_reading(db):
    db["library_entries"] = {"kind": "book", "title": LR_PARAMS["title"],
                             "notes": LR_PARAMS["notes"], "memory_id": 90}
    db["memories"] = {"namespace": "library"}
    got = await v.verify("vault.log_reading", LR_PARAMS, LR_RESULT)
    assert got.state is v.State.VERIFIED
    _no_secret(got)

    db["memories"] = None
    got = await v.verify("vault.log_reading", LR_PARAMS, LR_RESULT)
    assert got.state is v.State.CONTRADICTED

    db["memories"] = {"namespace": "library"}
    db["library_entries"] = {"kind": "book", "title": LR_PARAMS["title"],
                             "notes": "something else", "memory_id": 90}
    got = await v.verify("vault.log_reading", LR_PARAMS, LR_RESULT)
    assert got.state is v.State.CONTRADICTED and "notes" in got.reason

    db["library_entries"] = None
    got = await v.verify("vault.log_reading", LR_PARAMS, LR_RESULT)
    assert got.state is v.State.CONTRADICTED
    got = await v.verify("vault.log_reading", LR_PARAMS, ActionResult(True))
    assert got.state is v.State.UNKNOWN


RX_RESULT = ActionResult(True, "indexed 2", evidence={
    "indexed": 2, "backfilled": 0, "problems": [],
    "docs": [{"path": "data/vault/lease.md", "sha": "a" * 12},
             {"path": "data/vault/policy.pdf", "sha": "b" * 12}]})


async def test_vault_reindex(db):
    db["vault_documents"] = [
        {"path": "data/vault/lease.md", "sha256": "a" * 64, "chunks": 3,
         "indexed_at": T0},
        {"path": "data/vault/policy.pdf", "sha256": "b" * 64, "chunks": 2,
         "indexed_at": T0}]
    got = await v.verify("vault.reindex", {}, RX_RESULT)
    assert got.state is v.State.VERIFIED and got.evidence["chunks"] == 5

    db["vault_documents"] = [
        {"path": "data/vault/lease.md", "sha256": "c" * 64, "chunks": 3,
         "indexed_at": T0},
        {"path": "data/vault/policy.pdf", "sha256": "b" * 64, "chunks": 2,
         "indexed_at": T0}]
    got = await v.verify("vault.reindex", {}, RX_RESULT)
    assert got.state is v.State.UNKNOWN and got.failure_class is v.FailureClass.STALE

    db["vault_documents"] = db.rows["vault_documents"][1:]
    got = await v.verify("vault.reindex", {}, RX_RESULT)
    assert got.state is v.State.CONTRADICTED and "lease.md" in got.reason

    got = await v.verify("vault.reindex", {}, ActionResult(True, evidence={
        "indexed": 3, "backfilled": 0, "problems": []}))
    assert got.state is v.State.UNKNOWN

    db["vault_documents"] = {"n": 14, "last": T0}
    got = await v.verify("vault.reindex", {}, ActionResult(True, evidence={
        "indexed": 0, "backfilled": 0, "problems": [], "docs": []}))
    assert got.state is v.State.VERIFIED and got.evidence["documents"] == 14


async def test_reindex_records_the_documents_it_indexed(monkeypatch, tmp_path):
    from atlas.modules.vault import index
    (tmp_path / "lease.md").write_text("The lease renews in May.")
    monkeypatch.setattr(index, "vault_dir", lambda: tmp_path)
    monkeypatch.delenv("OLLAMA_URL", raising=False)

    async def fetch_one(sql, *args):
        return None

    async def execute_sql(sql, *args):
        return "INSERT 0 1"

    async def remember(*_a, **_kw):
        return 1

    monkeypatch.setattr(index, "fetch_one", fetch_one)
    monkeypatch.setattr(index, "execute_sql", execute_sql)
    monkeypatch.setattr(index, "remember", remember)
    got = await index.reindex()
    want = hashlib.sha256(b"The lease renews in May.").hexdigest()[:12]
    assert got.evidence["docs"] == [{"path": str(tmp_path / "lease.md"), "sha": want}]
    assert await index.index_all() == (1, [])           # still a 2-tuple


# ================================================================== lessons

LC_PARAMS = {"action": "fiverr.draft_buyer_reply", "why": "the API changed",
             "params_json": json.dumps({"order": "FO1"})}
LC_RESULT = ActionResult(True, "cleared", evidence={"was": "This failed twice"},
                         rollback_token=json.dumps({
                             "action": "fiverr.draft_buyer_reply",
                             "params": {"order": "FO1"}}))


def _lessons(still: int, cleared: bool):
    def answer(sql, args):
        from atlas.kernel.lessons import params_key
        assert args[1] == params_key({"order": "FO1"})
        if "count(*)" in sql:
            return {"n": still}
        return {"id": 3, "cleared_at": T0} if cleared else None
    return answer


async def test_lessons_clear(db):
    db["lessons"] = _lessons(0, True)
    got = await v.verify("lessons.clear", LC_PARAMS, LC_RESULT)
    assert got.state is v.State.VERIFIED and got.evidence["cleared_id"] == 3

    db["lessons"] = _lessons(1, True)
    got = await v.verify("lessons.clear", LC_PARAMS, LC_RESULT)
    assert got.state is v.State.CONTRADICTED and "still blocked" in got.reason

    db["lessons"] = _lessons(0, False)
    got = await v.verify("lessons.clear", LC_PARAMS, LC_RESULT)
    assert got.state is v.State.CONTRADICTED

    # "nothing was blocking": no token, nothing refuses -> verified
    got = await v.verify("lessons.clear", LC_PARAMS, ActionResult(True, "nothing"))
    assert got.state is v.State.VERIFIED

    got = await v.verify("lessons.clear", {}, ActionResult(True))
    assert got.state is v.State.UNKNOWN

    db["lessons"] = _lessons(0, True)
    got = await v.reconcile_unknown("lessons.clear", LC_PARAMS, {"at": T0})
    assert got.state is v.State.VERIFIED
    db["lessons"] = _lessons(2, False)
    got = await v.reconcile_unknown("lessons.clear", LC_PARAMS, {"at": T0})
    assert got.state is v.State.CONTRADICTED


# ================================================================== rhythm

NC_PARAMS = {"detail": f"exam week, studying late ({SECRET})", "until": "2026-10-04"}
NC_RESULT = ActionResult(True, "noted", rollback_token="31")


async def test_rhythm_note_circumstance(db):
    db["rhythm_notes"] = {"kind": "circumstance", "detail": NC_PARAMS["detail"],
                          "until": date(2026, 10, 4), "memory_id": 5}
    got = await v.verify("rhythm.note_circumstance", NC_PARAMS, NC_RESULT)
    assert got.state is v.State.VERIFIED
    assert got.evidence["detail_sha256"] == sha(NC_PARAMS["detail"])
    assert "exam week" not in json.dumps(got.as_audit())
    _no_secret(got)

    got = await v.verify("rhythm.note_circumstance",
                         {**NC_PARAMS, "until": "20261004"}, NC_RESULT)
    assert got.state is v.State.VERIFIED                 # the same date, compact form

    db["rhythm_notes"] = {**db.rows["rhythm_notes"], "until": None}
    got = await v.verify("rhythm.note_circumstance", NC_PARAMS, NC_RESULT)
    assert got.state is v.State.CONTRADICTED

    db["rhythm_notes"] = None
    got = await v.verify("rhythm.note_circumstance", NC_PARAMS, NC_RESULT)
    assert got.state is v.State.CONTRADICTED
    got = await v.verify("rhythm.note_circumstance", NC_PARAMS, ActionResult(True))
    assert got.state is v.State.UNKNOWN


# ================================================================== linkedin

AD_TEXT = "Cheap flights to Cairo this week, from New York and Boston"


@pytest.fixture
def li_bank(monkeypatch, tmp_path):
    from atlas.modules import linkedin
    path = tmp_path / "state.json"

    def write(entry: dict | None) -> None:
        bank = [entry] if entry else []
        path.write_text(json.dumps({"improver": {"post_bank": bank, "posting": {}}}))

    monkeypatch.setattr(linkedin, "STATE", path)
    monkeypatch.setenv("RAD1_INTERNAL_TOKEN", SECRET)
    return path, write


AD_PARAMS = {"page": "flycheap", "entry_id": "B14",
             "text_sha256": hashlib.sha256(AD_TEXT.encode()).hexdigest()}
AD_RESULT = ActionResult(True, "B14 approved", rollback_token="flycheap:B14")


async def test_linkedin_approve_draft(li_bank):
    path, write = li_bank
    write({"id": "B14", "status": "approved", "approved": "2026-09-28",
           "text": AD_TEXT})
    before = path.read_bytes()
    got = await v.verify("linkedin.approve_draft", AD_PARAMS, AD_RESULT)
    assert got.state is v.State.VERIFIED and got.evidence["status"] == "approved"
    assert path.read_bytes() == before                  # nothing saved back
    assert not list(path.parent.glob("*.lock")) and not path.with_suffix(".tmp").exists()
    _no_secret(got)

    write({"id": "B14", "status": "published", "text": AD_TEXT})
    got = await v.verify("linkedin.approve_draft", AD_PARAMS, AD_RESULT)
    assert got.state is v.State.VERIFIED

    write({"id": "B14", "status": "draft", "text": AD_TEXT})
    got = await v.verify("linkedin.approve_draft", AD_PARAMS, AD_RESULT)
    assert got.state is v.State.CONTRADICTED

    write({"id": "B14", "status": "approved", "text": AD_TEXT + " (rewritten)"})
    got = await v.verify("linkedin.approve_draft", AD_PARAMS, AD_RESULT)
    assert got.state is v.State.CONTRADICTED
    assert got.failure_class is v.FailureClass.SECURITY

    write(None)
    got = await v.verify("linkedin.approve_draft", AD_PARAMS, AD_RESULT)
    assert got.state is v.State.CONTRADICTED

    got = await v.verify("linkedin.approve_draft", {}, ActionResult(True))
    assert got.state is v.State.UNKNOWN

    write({"id": "B14", "status": "approved", "kind": "image",
           "media": str(path.parent / "gone.png"), "text": AD_TEXT})
    got = await v.verify("linkedin.approve_draft", AD_PARAMS, AD_RESULT)
    assert got.state is v.State.UNKNOWN and "media" in got.reason

    path.unlink()
    got = await v.verify("linkedin.approve_draft", AD_PARAMS, AD_RESULT)
    assert got.state is v.State.UNKNOWN


async def test_linkedin_approve_draft_reconciler(li_bank):
    _path, write = li_bank
    write({"id": "B14", "status": "approved", "text": AD_TEXT})
    got = await v.reconcile_unknown("linkedin.approve_draft", AD_PARAMS, {"at": T0})
    assert got.state is v.State.VERIFIED
    write({"id": "B14", "status": "draft", "text": AD_TEXT})
    got = await v.reconcile_unknown("linkedin.approve_draft", AD_PARAMS, {"at": T0})
    assert got.state is v.State.CONTRADICTED


# ================================================================== raising reads

RAISING = [
    ("marketing.propose_experiment", PE_PARAMS, PE_RESULT),
    ("marketing.propose_idea", PI_PARAMS, PI_RESULT),
    ("marketing.record_outcome", RO_PARAMS, RO_RESULT),
    ("nightshift.run", NR_PARAMS, NR_RESULT),
    ("nightshift.research", {"question": QUESTION}, NRS_RESULT),
    ("vault.log_reading", LR_PARAMS, LR_RESULT),
    ("vault.reindex", {}, RX_RESULT),
    ("lessons.clear", LC_PARAMS, LC_RESULT),
    ("research.report", {"question": QUESTION}, RR_RESULT),
    ("rhythm.note_circumstance", NC_PARAMS, NC_RESULT),
]




async def test_linkedin_approve_draft_raising_read(li_bank, monkeypatch):
    from atlas.modules.linkedin import readback

    def boom(*_a, **_kw):
        raise OSError(f"cannot read state {SECRET}")

    monkeypatch.setattr(readback, "_entry", boom)
    got = await v.verify("linkedin.approve_draft", AD_PARAMS, AD_RESULT)
    assert got.state is v.State.UNKNOWN
    _no_secret(got)
