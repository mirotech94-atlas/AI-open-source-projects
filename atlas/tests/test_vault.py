"""vault — documents indexed for recall, and a reading log that becomes memory.

Two properties are load-bearing:

* Indexing is idempotent by content, not by name. A file that has not
  changed since the last pass must not be re-chunked and re-remembered —
  otherwise every scheduled `vault.reindex` duplicates the same facts in
  semantic memory and recall degrades as noise grows.
* `check_index` is UNKNOWN, never OK or a bare zero, when the vault
  directory itself is missing. "No documents" and "nowhere to look" are
  different facts, and reading the second as the first hides a
  misconfigured `VAULT_DIR` forever.
"""
from __future__ import annotations

import ast
import itertools
import pathlib

import pytest

from atlas.kernel.agents import ROSTER, build_roster
from atlas.kernel.contracts import Health
from atlas.modules.vault import index, library

ROOT = pathlib.Path(__file__).resolve().parent.parent / "atlas"
PKG = ROOT / "modules" / "vault"


# ------------------------------------------------------- no-executor path

def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                parts = ["atlas", "modules", "vault"][: 3 - (node.level - 1)]
                mod = ".".join(parts + ([mod] if mod else []))
            names.add(mod)
    return names


def test_no_file_in_the_module_imports_the_executor():
    files = list(PKG.glob("*.py"))
    assert files
    for f in files:
        for mod in _imports_of(f):
            assert "executor" not in mod, f"{f.name} imports {mod}"


# ------------------------------------------------------------------ chunk

def test_chunk_overlaps_and_covers():
    text = "x" * 3000
    pieces = index.chunk(text, size=1200, overlap=150)
    assert len(pieces) > 1

    step = 1200 - 150
    rebuilt = "".join(p[:step] for p in pieces[:-1]) + pieces[-1]
    assert rebuilt == text

    for a, b in itertools.pairwise(pieces):
        assert a[-150:] == b[:150]


def test_chunk_of_short_text_is_one_piece():
    assert index.chunk("hello world") == ["hello world"]


def test_chunk_of_empty_text_is_empty():
    assert index.chunk("") == []


# -------------------------------------------------------------- guess_kind

@pytest.mark.parametrize("name, text, expected", [
    ("Renters Insurance Policy.pdf", "", "insurance"),
    ("lease_2026.pdf", "This tenancy agreement between landlord and tenant", "lease"),
    ("receipt_costco.txt", "TOTAL DUE $42.10 thank you for your purchase", "receipt"),
    ("Master Services Agreement.md", "the parties hereby agree to the terms", "contract"),
    ("photo.txt", "just some notes about nothing in particular", "other"),
])
def test_guess_kind_from_name_and_text(name, text, expected):
    assert index.guess_kind(name, text) == expected


# ------------------------------------------------------------- check_index

async def test_check_index_is_unknown_when_vault_dir_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "vault_dir", lambda: tmp_path / "does-not-exist")
    obs = await index.check_index()
    assert obs.health is Health.UNKNOWN
    assert "does-not-exist" in obs.detail


def _counts(documents: int, unembedded: int | Exception):
    """fetch_one for check_index: the document count, and the count of
    vault/library chunks with no embedding (or that query failing)."""
    async def fake_fetch_one(sql, *args):
        flat = " ".join(sql.split())
        if "FROM vault_documents" in flat:
            return {"n": documents}
        if "FROM memories" in flat and "embedding IS NULL" in flat:
            assert "namespace" not in flat          # every namespace counts
            if isinstance(unembedded, Exception):
                raise unembedded
            return {"n": unembedded}
        raise AssertionError(f"unexpected fetch_one: {flat}")
    return fake_fetch_one


async def test_check_index_reports_the_document_count(tmp_path, monkeypatch):
    """OK only when semantic search can run over every chunk. Before G11 this
    was OK with OLLAMA_URL unset — the one unconfigured door that did not
    say so."""
    monkeypatch.setattr(index, "vault_dir", lambda: tmp_path)
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setattr(index, "fetch_one", _counts(3, 0))
    obs = await index.check_index()
    assert obs.health is Health.OK
    assert obs.value == 3.0


async def test_check_index_is_degraded_without_ollama_and_names_the_count(
        tmp_path, monkeypatch):
    monkeypatch.setattr(index, "vault_dir", lambda: tmp_path)
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.setattr(index, "fetch_one", _counts(3, 41))
    obs = await index.check_index()
    assert obs.health is Health.DEGRADED
    assert obs.value == 3.0
    assert "OLLAMA_URL unset" in obs.detail
    assert "keyword match only" in obs.detail
    assert "41" in obs.detail


async def test_check_index_is_degraded_while_chunks_wait_for_an_embedding(
        tmp_path, monkeypatch):
    monkeypatch.setattr(index, "vault_dir", lambda: tmp_path)
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setattr(index, "fetch_one", _counts(3, 12))
    obs = await index.check_index()
    assert obs.health is Health.DEGRADED
    assert "12 memories have no embedding" in obs.detail
    assert "vault.reindex" in obs.detail


async def test_check_index_is_unknown_when_the_embedding_count_cannot_be_read(
        tmp_path, monkeypatch):
    monkeypatch.setattr(index, "vault_dir", lambda: tmp_path)
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setattr(index, "fetch_one", _counts(3, RuntimeError("gone")))
    obs = await index.check_index()
    assert obs.health is Health.UNKNOWN
    assert "embedding" in obs.detail


# ---------------------------------------- kind='skill' under skills/ dir

async def test_index_all_kinds_a_file_under_skills_as_skill_not_by_content(
        tmp_path, monkeypatch):
    """Task 60 drops one `.md` per agent playbook under `<vault>/skills/`.
    Several of the real playbooks' own text matches `guess_kind`'s
    "insurance"/"contract"/"lease" keyword hints (e.g. "policy", "agreement")
    -- verified against the 13 real files. Anything under `skills/` must be
    classified by where it is, not by what it says, so the Vault table
    never shows an agent's playbook as somebody's lease."""
    monkeypatch.setattr(index, "vault_dir", lambda: tmp_path)
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    (skills_dir / "vpsie.md").write_text(
        "the parties hereby agree to the lease and insurance policy",
        encoding="utf-8")

    stored: dict[str, str] = {}
    remembered: list[tuple[str, str, dict]] = []

    async def fake_remember(kind, namespace, content, *, meta=None, importance=0.5):
        remembered.append((namespace, content, meta))
        return len(remembered)

    async def fake_fetch_one(sql, *args):
        return None

    async def fake_execute_sql(sql, *args):
        stored[args[0]] = args

    monkeypatch.setattr(index, "remember", fake_remember)
    monkeypatch.setattr(index, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(index, "execute_sql", fake_execute_sql)

    indexed, problems = await index.index_all()
    assert indexed == 1
    assert problems == []
    assert remembered[0][2]["kind"] == "skill"

    doc_args = stored[str(skills_dir / "vpsie.md")]
    assert doc_args[3] == "skill"          # (path, sha256, title, kind, chunks)


# -------------------------------------------------------------- index_all

async def test_index_all_chunks_a_new_file_and_skips_it_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "vault_dir", lambda: tmp_path)
    (tmp_path / "note.md").write_text("word " * 400, encoding="utf-8")

    stored: dict[str, str] = {}
    remembered: list[tuple[str, str, dict]] = []

    async def fake_remember(kind, namespace, content, *, meta=None, importance=0.5):
        remembered.append((namespace, content, meta))
        return len(remembered)

    async def fake_fetch_one(sql, *args):
        if "vault_documents" in sql and "sha256" in sql:
            sha = stored.get(args[0])
            return {"sha256": sha} if sha else None
        raise AssertionError(f"unexpected fetch_one: {sql}")

    async def fake_execute_sql(sql, *args):
        stored[args[0]] = args[1]

    monkeypatch.setattr(index, "remember", fake_remember)
    monkeypatch.setattr(index, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(index, "execute_sql", fake_execute_sql)

    indexed, problems = await index.index_all()
    assert indexed == 1
    assert problems == []
    assert remembered, "expected at least one chunk remembered"
    assert all(ns == "vault" for ns, _content, _meta in remembered)
    for _ns, _content, meta in remembered:
        assert meta["path"] == str(tmp_path / "note.md")
        assert meta["title"] == "note"
        assert meta["kind"] == "other"
        assert isinstance(meta["i"], int)

    # Second pass over the same, unchanged file: nothing new is remembered.
    remembered.clear()
    indexed2, problems2 = await index.index_all()
    assert indexed2 == 0
    assert problems2 == []
    assert remembered == []


async def test_index_all_reports_a_problem_for_an_unreadable_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "vault_dir", lambda: tmp_path)
    (tmp_path / "scan.pdf").write_bytes(b"not really a pdf")

    async def fake_fetch_one(sql, *args):
        return None

    remembered = []

    async def fake_remember(*a, **k):
        remembered.append((a, k))
        return 1

    monkeypatch.setattr(index, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(index, "remember", fake_remember)

    indexed, problems = await index.index_all()
    assert indexed == 0
    assert len(problems) == 1
    assert "scan.pdf" in problems[0]
    assert remembered == []


async def test_index_all_backfills_missing_embeddings_once_ollama_works(
        tmp_path, monkeypatch):
    monkeypatch.setattr(index, "vault_dir", lambda: tmp_path)
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    asked: list = []

    async def backfill(namespaces):
        asked.append(namespaces)
        return 7, None

    monkeypatch.setattr(index, "backfill_embeddings", backfill)
    indexed, problems = await index.index_all()
    assert (indexed, problems) == (0, [])
    assert asked == [None]                           # every namespace


async def test_reindex_puts_the_backfill_count_in_the_audited_detail(
        tmp_path, monkeypatch):
    monkeypatch.setattr(index, "vault_dir", lambda: tmp_path)
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")

    async def backfill(namespaces):
        return 500, None

    monkeypatch.setattr(index, "backfill_embeddings", backfill)
    result = await index.reindex()
    assert "embedded 500 memories that had none" in result.detail
    assert result.evidence["backfilled"] == 500


async def test_reindex_dry_says_what_the_backfill_would_do(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "vault_dir", lambda: tmp_path)
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    monkeypatch.setattr(index, "fetch_one", _counts(0, 1234))

    async def boom(*_a, **_k):
        raise AssertionError("a dry run embeds nothing")

    monkeypatch.setattr(index, "backfill_embeddings", boom)
    result = await index.reindex_dry()
    assert "would embed up to 500 of 1234 memories" in result.detail


async def test_reindex_dry_without_ollama_mentions_no_backfill(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "vault_dir", lambda: tmp_path)
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    result = await index.reindex_dry()
    assert result.detail == "no new or changed files to index"


async def test_index_all_reports_a_backfill_that_stopped(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "vault_dir", lambda: tmp_path)
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")

    async def backfill(namespaces):
        return 1, "the embedding model stopped answering after 1 of 3"

    monkeypatch.setattr(index, "backfill_embeddings", backfill)
    _indexed, problems = await index.index_all()
    assert problems == [("embedding backfill: the embedding model stopped "
                         "answering after 1 of 3")]


async def test_index_all_does_not_backfill_without_ollama(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "vault_dir", lambda: tmp_path)
    monkeypatch.delenv("OLLAMA_URL", raising=False)

    async def backfill(namespaces):
        raise AssertionError("nothing to embed with")

    monkeypatch.setattr(index, "backfill_embeddings", backfill)
    assert await index.index_all() == (0, [])


# ----------------------------------------------------------- vault_recall

async def test_vault_recall_says_when_it_could_only_match_keywords(monkeypatch):
    from atlas.kernel.memory import Kind, Memory, Recalled
    from atlas.tools import builtin

    async def fake_recall(query, namespaces, *, kinds=(), limit=8):
        return Recalled([Memory(1, Kind.SEMANTIC, "vault",
                                "The policy will renew on 1 March.")],
                        degraded="OLLAMA_URL unset")

    monkeypatch.setattr("atlas.kernel.memory.recall", fake_recall)
    out = await builtin.vault_recall("when does my insurance renew?")
    assert out["count"] == 1
    assert out["search"] == ("semantic search unavailable (OLLAMA_URL unset) "
                             "— keyword match only")
    assert "<untrusted_data" in out["memories"]


async def test_vault_recall_finding_nothing_by_keyword_does_not_claim_absence(
        monkeypatch):
    from atlas.kernel.memory import Recalled
    from atlas.tools import builtin

    async def fake_recall(query, namespaces, *, kinds=(), limit=8):
        return Recalled([], degraded="OLLAMA_URL unset")

    monkeypatch.setattr("atlas.kernel.memory.recall", fake_recall)
    out = await builtin.vault_recall("when is the gas bill due?")
    assert out["count"] == 0
    assert "semantic search unavailable (OLLAMA_URL unset)" in out["memories"]
    assert "keyword match only" in out["memories"]


async def test_vault_recall_by_meaning_has_no_degraded_note(monkeypatch):
    from atlas.kernel.memory import Recalled
    from atlas.tools import builtin

    async def fake_recall(query, namespaces, *, kinds=(), limit=8):
        return Recalled([], degraded=None)

    monkeypatch.setattr("atlas.kernel.memory.recall", fake_recall)
    out = await builtin.vault_recall("anything")
    assert out["memories"] == "nothing in vault or library matches"
    assert "search" not in out


# ------------------------------------------------------------ extract_text

async def test_extract_text_reads_markdown_directly(tmp_path):
    path = tmp_path / "a.md"
    path.write_text("# hello", encoding="utf-8")
    text, problem = await index.extract_text(path)
    assert problem is None
    assert text == "# hello"


async def test_extract_text_reports_missing_pypdf(tmp_path, monkeypatch):
    path = tmp_path / "a.pdf"
    path.write_bytes(b"whatever")
    monkeypatch.setitem(__import__("sys").modules, "pypdf", None)
    text, problem = await index.extract_text(path)
    assert text is None
    assert problem == "pypdf not installed"


async def test_extract_text_refuses_unsupported_types(tmp_path):
    path = tmp_path / "a.docx"
    path.write_bytes(b"whatever")
    text, problem = await index.extract_text(path)
    assert text is None
    assert "unsupported" in problem.lower()


# ------------------------------------------------------------- log_reading

async def test_log_reading_stores_and_remembers_with_library_namespace(monkeypatch):
    calls: dict = {}
    remembered = []

    async def fake_fetch_one(sql, *args):
        calls["insert_args"] = args
        return {"id": 42}

    async def fake_execute_sql(sql, *args):
        calls["update_args"] = args

    async def fake_remember(kind, namespace, content, *, meta=None, importance=0.5):
        remembered.append((namespace, content, meta))
        return 7

    monkeypatch.setattr(library, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(library, "execute_sql", fake_execute_sql)
    monkeypatch.setattr(library, "remember", fake_remember)

    result = await library.log_reading(
        "book", "Antifragile", "risk asymmetry notes", "amazon.com/x")

    assert result.ok
    assert remembered and remembered[0][0] == "library"
    assert "Antifragile" in remembered[0][1]
    assert result.rollback_token == "42|7"
    assert calls["update_args"] == (42, 7)


async def test_log_reading_refuses_without_notes(monkeypatch):
    result = await library.log_reading("book", "Antifragile", "   ")
    assert not result.ok


async def test_unlog_removes_the_entry_and_its_memory(monkeypatch):
    deleted = []

    async def fake_execute_sql(sql, *args):
        deleted.append((sql, args))

    monkeypatch.setattr(library, "execute_sql", fake_execute_sql)
    result = await library.unlog("42|7")
    assert result.ok
    assert deleted[0][1] == (42,)
    assert deleted[1][1] == (7,)


# --------------------------------------------------------------- reindex

async def test_reindex_dry_run_lists_changed_files_without_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(index, "vault_dir", lambda: tmp_path)
    (tmp_path / "note.md").write_text("hello", encoding="utf-8")

    async def fake_fetch_one(sql, *args):
        return None

    writes = []

    async def fake_execute_sql(sql, *args):
        writes.append((sql, args))

    monkeypatch.setattr(index, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(index, "execute_sql", fake_execute_sql)

    result = await index.reindex_dry()
    assert result.ok
    assert "note.md" in result.detail
    assert writes == []


async def test_reindex_rollback_is_a_no_op():
    result = await index.reindex_rollback("anything")
    assert result.ok
    assert not result.rollback_token


# -------------------------------------------------------- memory scoping

def test_concierge_and_inbox_scopes_include_vault_and_library():
    build_roster()
    for name in ("concierge", "inbox"):
        scope = ROSTER[name].memory_scope
        assert "vault" in scope, f"{name} cannot recall documents"
        assert "library" in scope, f"{name} cannot recall readings"


# ------------------------------------------------------------- keep_answer

async def test_keep_answer_inserts_when_nothing_matches_today(monkeypatch):
    calls: dict = {}

    async def fake_fetch_one(sql, *args):
        calls["select"] = (sql, args)

    async def fake_execute_sql(sql, *args):
        calls["write"] = (sql, args)

    monkeypatch.setattr(library, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(library, "execute_sql", fake_execute_sql)

    await library.keep_answer("what is the anatomy of the knee",
                              "The knee is a hinge joint.", "model")

    assert "INSERT INTO library_entries" in calls["write"][0]
    assert "'answer'" in calls["write"][0]
    assert calls["write"][1] == ("what is the anatomy of the knee",
                                 "model", "The knee is a hinge joint.")


async def test_keep_answer_updates_the_row_asked_again_within_a_day(monkeypatch):
    calls: dict = {}

    async def fake_fetch_one(sql, *args):
        return {"id": 5}

    async def fake_execute_sql(sql, *args):
        calls["write"] = (sql, args)

    monkeypatch.setattr(library, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(library, "execute_sql", fake_execute_sql)

    await library.keep_answer("what is the anatomy of the knee",
                              "Updated answer.", "model")

    assert "UPDATE library_entries" in calls["write"][0]
    assert calls["write"][1] == (5, "model", "Updated answer.")


async def test_keep_answer_title_is_trimmed_to_200_chars(monkeypatch):
    calls: dict = {}
    long_question = "why " * 100

    async def fake_fetch_one(sql, *args):
        calls["select_args"] = args

    async def fake_execute_sql(sql, *args):
        calls["write_args"] = args

    monkeypatch.setattr(library, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(library, "execute_sql", fake_execute_sql)

    await library.keep_answer(long_question, "an answer", "model")

    assert len(calls["select_args"][0]) == 200
    assert len(calls["write_args"][0]) == 200


async def test_keep_answer_never_raises_when_the_write_fails(monkeypatch):
    async def fake_fetch_one(sql, *args):
        raise RuntimeError("db is down")

    monkeypatch.setattr(library, "fetch_one", fake_fetch_one)

    await library.keep_answer("what is the anatomy of the knee", "an answer", "model")


# ------------------------------------------------------------- seed_skill

async def test_seed_skill_inserts_when_the_source_is_new(monkeypatch):
    calls: dict = {}

    async def fake_fetch_one(sql, *args):
        return None

    async def fake_execute_sql(sql, *args):
        calls["write"] = (sql, args)

    monkeypatch.setattr(library, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(library, "execute_sql", fake_execute_sql)

    changed = await library.seed_skill("Mirotech", "atlas/skills/mirotech.py",
                                       "pricing playbook")

    assert changed is True
    assert "INSERT INTO library_entries" in calls["write"][0]
    assert "'skill'" in calls["write"][0]
    assert calls["write"][1] == ("Mirotech", "atlas/skills/mirotech.py",
                                 "pricing playbook")


async def test_seed_skill_updates_in_place_when_the_source_matches_and_changed(
        monkeypatch):
    calls: dict = {}

    async def fake_fetch_one(sql, *args):
        return {"id": 3, "title": "Mirotech", "notes": "old playbook"}

    async def fake_execute_sql(sql, *args):
        calls["write"] = (sql, args)

    monkeypatch.setattr(library, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(library, "execute_sql", fake_execute_sql)

    changed = await library.seed_skill("Mirotech", "atlas/skills/mirotech.py",
                                       "new playbook")

    assert changed is True
    assert "UPDATE library_entries" in calls["write"][0]
    assert calls["write"][1] == (3, "Mirotech", "new playbook")


async def test_seed_skill_is_a_no_op_when_nothing_changed(monkeypatch):
    async def fake_fetch_one(sql, *args):
        return {"id": 3, "title": "Mirotech", "notes": "same playbook"}

    async def fake_execute_sql(sql, *args):
        raise AssertionError("no write should happen when nothing changed")

    monkeypatch.setattr(library, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(library, "execute_sql", fake_execute_sql)

    changed = await library.seed_skill("Mirotech", "atlas/skills/mirotech.py",
                                       "same playbook")

    assert changed is False


# ----------------------------------------------------------- keep_research

async def test_keep_research_inserts_a_library_row(monkeypatch):
    calls: dict = {}

    async def fake_execute_sql(sql, *args):
        calls["write"] = (sql, args)

    monkeypatch.setattr(library, "execute_sql", fake_execute_sql)

    await library.keep_research("are PA-440 resale prices dropping?",
                                "reports:501", "the finished report body")

    assert "INSERT INTO library_entries" in calls["write"][0]
    assert "'research'" in calls["write"][0]
    assert calls["write"][1] == ("are PA-440 resale prices dropping?",
                                 "reports:501", "the finished report body")


async def test_keep_research_never_raises_when_the_write_fails(monkeypatch):
    async def fake_execute_sql(sql, *args):
        raise RuntimeError("db is down")

    monkeypatch.setattr(library, "execute_sql", fake_execute_sql)

    await library.keep_research("are PA-440 resale prices dropping?",
                                "reports:501", "body")
