"""Recall, and the trail it leaves.

`recall()` writes one `memory_reads` row per namespace it was asked about.
That is a write to ATLAS's own table and nothing else — no collector, no
outside world, no executor — and it exists so the brain view on `/core` can
show what ATLAS is actually using rather than what it happens to store.

The second property here is the one that matters: a recall must survive a
logging failure. A brain view that could break a recall would be a decoration
that took the system down with it.
"""
from __future__ import annotations

import pytest

from atlas.kernel import memory
from atlas.kernel.memory import Kind, recall


@pytest.fixture(autouse=True)
def no_embeddings(monkeypatch):
    """No Ollama, so recall takes the ILIKE branch and never leaves the box."""
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("ATLAS_EMBED_URL", raising=False)


@pytest.fixture
def logged(monkeypatch):
    """Capture what recall writes, and what it reads."""
    writes: list[tuple[str, tuple]] = []

    async def fake_execute(sql, *args):
        writes.append((" ".join(sql.split()), args))
        return "OK"

    monkeypatch.setattr(memory, "execute_sql", fake_execute)
    return writes


def rows(*namespaces: str) -> list[dict]:
    return [{
        "id": i, "kind": "semantic", "namespace": ns, "content": "a fact",
        "meta": "{}", "importance": 0.5, "at": None,
    } for i, ns in enumerate(namespaces, start=1)]


async def test_recall_logs_one_row_per_namespace_it_was_asked_about(
        monkeypatch, logged):
    async def fake_fetch_all(_sql, *_args):
        return rows("people", "people", "threads")

    monkeypatch.setattr(memory, "fetch_all", fake_fetch_all)

    found = await recall("who is Kajal", ["people", "threads", "vault"])
    assert len(found) == 3

    inserts = [(sql, args) for sql, args in logged if "memory_reads" in sql]
    assert len(inserts) == 3, inserts
    by_ns = {args[0]: args for _sql, args in inserts}
    assert set(by_ns) == {"people", "threads", "vault"}
    # The hit count is per namespace, from the rows that actually came back.
    assert by_ns["people"][1] == 2
    assert by_ns["threads"][1] == 1
    # A namespace that was searched and matched nothing is still a read. That
    # ATLAS looked somewhere and found nothing is a fact about what it uses.
    assert by_ns["vault"][1] == 0


async def test_the_agent_is_recorded_when_the_caller_knows_it(
        monkeypatch, logged):
    async def fake_fetch_all(_sql, *_args):
        return rows("incidents")

    monkeypatch.setattr(memory, "fetch_all", fake_fetch_all)
    await recall("what broke", ["incidents"], agent="ops")

    inserts = [args for sql, args in logged if "memory_reads" in sql]
    assert inserts[0][2] == "ops"


async def test_an_unattributed_recall_logs_a_null_agent_rather_than_a_guess(
        monkeypatch, logged):
    async def fake_fetch_all(_sql, *_args):
        return rows("infra")

    monkeypatch.setattr(memory, "fetch_all", fake_fetch_all)
    await recall("disk", ["infra"])

    inserts = [args for sql, args in logged if "memory_reads" in sql]
    assert inserts[0][2] is None


async def test_a_logging_failure_never_fails_the_recall(monkeypatch, caplog):
    """The worst thing this feature could do is take recall down with it."""
    async def fake_fetch_all(_sql, *_args):
        return rows("people")

    async def boom(_sql, *_args):
        raise RuntimeError("memory_reads is gone")

    monkeypatch.setattr(memory, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(memory, "execute_sql", boom)

    with caplog.at_level("WARNING", logger="atlas.memory"):
        found = await recall("Kajal", ["people"])
    assert len(found) == 1
    assert found[0].namespace == "people"
    # Swallowed, but not silently: the failure is on the log.
    assert "could not log a memory read" in caplog.text


async def test_no_namespaces_means_no_query_and_nothing_logged(logged):
    assert await recall("anything", []) == []
    assert logged == []


async def test_the_kind_filter_still_reaches_the_query(monkeypatch, logged):
    """Regression guard: the logging sits after the query and must not have
    rewritten what the query was asked for."""
    seen: list[tuple] = []

    async def fake_fetch_all(_sql, *args):
        seen.append(args)
        return []

    monkeypatch.setattr(memory, "fetch_all", fake_fetch_all)
    await recall("disk", ["people"], kinds=(Kind.SEMANTIC,), limit=3)

    assert seen[0][0] == ["people"]
    assert seen[0][1] == ["semantic"]
    assert seen[0][3] >= 3          # over-fetched, then re-ranked down to 3


async def test_an_agent_run_attributes_its_recall_to_that_agent(monkeypatch):
    """The harness is the one caller that knows which agent is recalling, so
    it says so — otherwise every row in the brain's log reads 'unattributed'.
    The run is stopped at the recall; nothing past memory is exercised."""
    from types import SimpleNamespace

    from atlas.kernel import harness

    seen: dict = {}

    class Stop(Exception):
        pass

    async def fake_recall(query, namespaces, *, kinds=(), limit=8, agent=None):
        seen["agent"] = agent
        seen["namespaces"] = list(namespaces)
        raise Stop

    monkeypatch.setattr(harness, "recall", fake_recall)
    who = SimpleNamespace(name="ops", memory_scope=("infra", "incidents"))

    with pytest.raises(Stop):
        await harness.run_agent(who, "is the disk full?")

    assert seen == {"agent": "ops", "namespaces": ["infra", "incidents"]}


async def test_learn_fact_dedupe_is_bookkeeping_not_a_read(monkeypatch, logged):
    """The lookup `learn_fact` does before writing is not ATLAS *using* a
    memory, so it must not light a region on the brain view."""
    async def fake_fetch_all(_sql, *_args):
        return []

    async def fake_fetch_one(_sql, *_args):
        return {"id": 1}

    monkeypatch.setattr(memory, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(memory, "fetch_one", fake_fetch_one)
    await memory.learn_fact("preferences", "the owner takes his coffee black")

    assert not [sql for sql, _ in logged if "memory_reads" in sql]


# ------------------------------------------- recall without embeddings (G11)

CORPUS = [
    {"id": 1, "kind": "semantic", "namespace": "vault", "meta": "{}",
     "importance": 0.5, "at": None,
     "content": "Home insurance policy HX-22. The policy will renew on 1 March "
                "unless cancelled in writing."},
    {"id": 2, "kind": "semantic", "namespace": "vault", "meta": "{}",
     "importance": 0.9, "at": None,
     "content": "Lease: the tenancy runs to 30 June; rent is due on the 1st."},
    {"id": 3, "kind": "semantic", "namespace": "library", "meta": "{}",
     "importance": 0.5, "at": None,
     "content": "Car insurance: third party only, paid monthly."},
]


def _ilike(pattern: str, text: str) -> bool:
    """`text ILIKE pattern` for the `%term%` patterns keyword recall builds."""
    assert pattern.startswith("%") and pattern.endswith("%"), pattern
    return pattern.strip("%").lower() in text.lower()


@pytest.fixture
def keyword_db(monkeypatch, logged):
    """A Postgres stand-in for the keyword branch: filters CORPUS with the
    patterns the query was given (`ILIKE ANY`) and ranks by how many match,
    exactly as the SQL asks. Anything else is refused."""
    seen: list[tuple[str, tuple]] = []

    async def fake_fetch_all(sql, *args):
        flat = " ".join(sql.split())
        seen.append((flat, args))
        assert "ILIKE ANY" in flat and "embedding <=>" not in flat, flat
        namespaces, kinds, patterns, limit = args
        hits = []
        for row in CORPUS:
            if row["namespace"] not in namespaces or row["kind"] not in kinds:
                continue
            n = sum(_ilike(p, row["content"]) for p in patterns)
            if n:
                hits.append((n, row["importance"], row))
        hits.sort(key=lambda h: (h[0], h[1]), reverse=True)
        return [dict(r) for _n, _i, r in hits[:limit]]

    monkeypatch.setattr(memory, "fetch_all", fake_fetch_all)
    return seen


async def test_when_does_my_insurance_renew_finds_the_chunk_without_ollama(
        keyword_db):
    found = await recall("when does my insurance renew?", ["vault", "library"])

    assert found, "keyword recall found nothing"
    assert found[0].id == 1                          # both terms beat one
    assert "renew" in found[0].content and "insurance" in found[0].content
    assert [m.id for m in found] == [1, 3]           # the lease matches nothing
    patterns = keyword_db[0][1][2]
    assert "%insurance%" in patterns and "%renew%" in patterns
    for stop in ("when", "does", "my"):
        assert f"%{stop}%" not in patterns
    assert found.degraded == "ATLAS_EMBED_URL unset"


async def test_keyword_recall_ranks_by_the_number_of_matching_terms(keyword_db):
    await recall("insurance renew", ["vault", "library"])
    sql = keyword_db[0][0]
    assert "count(*)" in sql and "unnest" in sql
    assert sql.index("ORDER BY hits DESC") > 0


async def test_a_query_of_only_stop_words_asks_nothing_and_says_why(keyword_db):
    found = await recall("what is it?", ["vault"])
    assert found == []
    assert keyword_db == []
    assert found.degraded == "ATLAS_EMBED_URL unset"


async def test_an_embedding_that_fails_is_degraded_with_its_own_reason(
        monkeypatch, keyword_db):
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")

    async def no_vector(_text):
        return None

    monkeypatch.setattr(memory, "_embed", no_vector)
    found = await recall("insurance", ["vault"])
    assert [m.id for m in found] == [1]
    assert found.degraded == "the embedding model did not answer"


async def test_a_semantic_recall_is_not_degraded(monkeypatch, logged):
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")

    async def vector(_text):
        return [0.1, 0.2]

    async def fake_fetch_all(sql, *_args):
        assert "embedding <=>" in sql
        return rows("vault")

    monkeypatch.setattr(memory, "_embed", vector)
    monkeypatch.setattr(memory, "fetch_all", fake_fetch_all)
    found = await recall("insurance", ["vault"])
    assert len(found) == 1
    assert found.degraded is None


# ------------------------------------------------------------- backfill

async def test_backfill_does_nothing_and_asks_nothing_without_ollama(monkeypatch):
    async def boom(*_a, **_k):
        raise AssertionError("no database call without OLLAMA_URL")

    monkeypatch.setattr(memory, "fetch_all", boom)
    monkeypatch.setattr(memory, "execute_sql", boom)
    assert await memory.backfill_embeddings(["vault", "library"]) == (0, None)


async def test_backfill_embeds_the_rows_that_have_none(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    asked: list[tuple] = []
    updated: list[tuple] = []

    async def fake_fetch_all(sql, *args):
        asked.append((" ".join(sql.split()), args))
        return [{"id": 4, "content": "policy renews 1 March"},
                {"id": 5, "content": "rent due on the 1st"}]

    async def fake_execute(sql, *args):
        updated.append((" ".join(sql.split()), args))

    async def vector(text):
        return [float(len(text))]

    monkeypatch.setattr(memory, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(memory, "execute_sql", fake_execute)
    monkeypatch.setattr(memory, "_embed", vector)

    done, problem = await memory.backfill_embeddings(["vault", "library"])
    assert (done, problem) == (2, None)
    sql, args = asked[0]
    assert "embedding IS NULL" in sql and args[0] == ["vault", "library"]
    assert args[1] == memory.BACKFILL_BATCH
    assert [a for _s, a in updated] == [(4, "[21.0]"), (5, "[19.0]")]
    assert all("UPDATE memories SET embedding" in s for s, _a in updated)


async def test_backfill_stops_at_the_first_embedding_that_fails(monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    updated: list = []

    async def fake_fetch_all(sql, *args):
        return [{"id": 4, "content": "a"}, {"id": 5, "content": "b"},
                {"id": 6, "content": "c"}]

    async def fake_execute(sql, *args):
        updated.append(args)

    calls = iter([[1.0], None, [3.0]])

    async def flaky(_text):
        return next(calls)

    monkeypatch.setattr(memory, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(memory, "execute_sql", fake_execute)
    monkeypatch.setattr(memory, "_embed", flaky)

    done, problem = await memory.backfill_embeddings(["vault"])
    assert done == 1
    assert updated == [(4, "[1.0]")]
    assert problem and "1 of 3" in problem


async def test_backfill_with_no_namespaces_named_covers_every_namespace(monkeypatch):
    """`threads`, `people`, `buyers`… stored before OLLAMA_URL vanish from
    semantic recall exactly as vault chunks do."""
    monkeypatch.setenv("OLLAMA_URL", "http://ollama:11434")
    asked: list = []

    async def fake_fetch_all(sql, *args):
        asked.append((" ".join(sql.split()), args))
        return []

    monkeypatch.setattr(memory, "fetch_all", fake_fetch_all)
    assert await memory.backfill_embeddings() == (0, None)
    sql, args = asked[0]
    assert "$1::text[] IS NULL OR namespace = ANY($1::text[])" in sql
    assert args[0] is None


@pytest.mark.parametrize("query", [
    "50%_off' OR 1=1 --", "renew_policy\\ %%", "it's 100% the_lease",
])
async def test_no_wildcard_or_quote_ever_reaches_a_pattern(keyword_db, query):
    import re

    await recall(query, ["vault"])
    for _sql, args in keyword_db:
        for pattern in args[2]:
            assert re.fullmatch(r"%[^\W_]+%", pattern), pattern
            assert "'" not in pattern and "_" not in pattern.strip("%")


def test_words_in_any_script_are_terms():
    assert memory._terms("Ωmega café policy") == ["ωmega", "café", "policy"]


async def test_the_hourly_maintenance_step_runs_the_backfill(monkeypatch):
    from atlas.kernel import privacy, scheduler

    ran: list = []

    async def retention():
        return {"purged_events": 0}

    async def backfill(namespaces=None, **_k):
        ran.append(namespaces)
        return 3, None

    monkeypatch.setattr(privacy, "run", retention)
    monkeypatch.setattr(memory, "backfill_embeddings", backfill)
    monkeypatch.setattr(scheduler, "_last_purge", -1e12)
    await scheduler._maybe_purge()
    assert ran == [None]


async def test_a_failed_retention_still_lets_the_backfill_run(monkeypatch):
    from atlas.kernel import privacy, scheduler

    ran: list = []

    async def retention():
        raise RuntimeError("no db")

    async def backfill(namespaces=None, **_k):
        ran.append(namespaces)
        return 0, None

    monkeypatch.setattr(privacy, "run", retention)
    monkeypatch.setattr(memory, "backfill_embeddings", backfill)
    monkeypatch.setattr(scheduler, "_last_purge", -1e12)
    await scheduler._maybe_purge()
    assert ran == [None]
