"""research — search, fetch, cite, and say plainly what could not be found.

Invariant 6 in miniature: `run_report` never lets a model's invented figure
pass as verified. Every number in the body must trace back to a source's raw
text, or the report says so under "Unverified figures".
"""
from __future__ import annotations

import ast
import json
import pathlib
from datetime import UTC, datetime

import pytest

from atlas.kernel.contracts import Severity, Tier
from atlas.kernel.llm import Completion
from atlas.modules import research
from atlas.modules.research import report

ROOT = pathlib.Path(__file__).resolve().parent.parent / "atlas"
PKG = ROOT / "modules" / "research"


@pytest.fixture(autouse=True)
def _clean_finished_buffer():
    """`brief()` drains a module-level buffer; leaking one test's reports
    into the next test's `brief()` call would be a silent, order-dependent
    failure."""
    report._FINISHED.clear()
    yield
    report._FINISHED.clear()


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
                parts = ["atlas", "modules", "research"][: 3 - (node.level - 1)]
                mod = ".".join(parts + ([mod] if mod else []))
            names.add(mod)
            # `from ...kernel import executor` names the forbidden submodule
            # only in the imported NAME, not in `node.module` ("kernel",
            # which resolves to "atlas.kernel" — no "executor" in sight).
            # Folding each imported name onto the resolved module is what
            # tests/test_judgment.py:35 already does, and what catches this.
            names.update(f"{mod}.{a.name}" for a in node.names)
    return names


def test_no_file_in_the_module_imports_the_executor():
    files = list(PKG.glob("*.py"))
    assert files
    for f in files:
        for mod in _imports_of(f):
            assert "executor" not in mod, f"{f.name} imports {mod}"


def test_imports_of_catches_a_from_kernel_import_executor_style_evasion(tmp_path):
    """Proof the detector itself works, on a throwaway file rather than the
    module's own source: `from ...kernel import executor` is exactly the
    shape `node.module` alone would miss, since "kernel" resolves to
    "atlas.kernel" with no "executor" in it — the name only shows up once
    the imported alias is folded in."""
    snippet = tmp_path / "evasion.py"
    snippet.write_text("from ...kernel import executor\n")
    assert any("executor" in mod for mod in _imports_of(snippet))


# ------------------------------------------------------------------- cite

def test_cite_format():
    sources = [
        {"title": "Palo Alto PA-440 resale pricing", "url": "http://example.com/a",
         "fetched_at": datetime(2026, 9, 24, 7, 15, tzinfo=UTC)},
        {"title": "Used firewall market notes", "url": "http://example.com/b",
         "fetched_at": "2026-09-24T07:16:00+00:00"},
    ]
    text = report.cite(sources)
    lines = text.splitlines()
    assert lines[0] == ("[1] Palo Alto PA-440 resale pricing — "
                        "http://example.com/a (fetched 07:15)")
    assert lines[1] == ("[2] Used firewall market notes — "
                        "http://example.com/b (fetched 07:16)")


# ---------------------------------------------------------- uncited_numbers

def test_uncited_numbers_finds_absent_and_ignores_present():
    body = "Prices rose 42% last quarter, from a base of 42 units."
    sources_text = "Quarterly report: 42 units shipped, no percentage given."
    assert report.uncited_numbers(body, sources_text) == ["42%"]


def test_uncited_numbers_ignores_citation_markers():
    body = "Growth was steady [1] and margins held [12]."
    sources_text = "Growth commentary with no numbers at all."
    assert report.uncited_numbers(body, sources_text) == []


def test_uncited_numbers_dedupes_repeats():
    body = "It grew 99% last quarter, and 99% again the quarter before that."
    sources_text = "No figures here."
    assert report.uncited_numbers(body, sources_text) == ["99%"]


def test_uncited_numbers_ignores_quarter_labels_and_durations():
    """"Q1" and "24h" are not figures needing a citation; a naive digit scan
    flags the "1" and "24" inside them, which would make every ordinary
    report noisy with false unverified figures."""
    body = "Uptime held at 24h checks through Q1, unlike the 15% dip in Q2."
    sources_text = "Uptime and quarterly notes, no figures."
    assert report.uncited_numbers(body, sources_text) == ["15%"]


@pytest.mark.parametrize("figure, verifying_source", [
    ("$50M", "revenue reached $50m according to the filing"),
    ("1.2B", "the market is valued at 1.2b this year"),
    ("$10K", "replacement parts run $10k each"),
    ("3.5x", "throughput improved 3.5x after the upgrade"),
])
def test_uncited_numbers_recognises_magnitude_and_multiplier_suffixes(
        figure, verifying_source):
    """A bare digit scan misses "$50M", "1.2B", "$10K" and "3.5x" entirely —
    none of them end in a plain digit, so the old pattern never matched them
    at all and they were silently treated as verified no matter what the
    sources said."""
    body = f"The headline figure was {figure} this quarter."
    assert report.uncited_numbers(body, "no relevant figures here") == [figure]
    assert report.uncited_numbers(body, verifying_source) == []


def test_uncited_numbers_ignores_product_codes():
    """"PA-440" and "PA-3220" are catalogue numbers, not figures — a digit
    run straight after "<letters>-" with no K/M/B/x/% suffix is a label."""
    body = "Compare the PA-440 against the PA-3220 [3], both discontinued."
    assert report.uncited_numbers(body, "no figures at all") == []


def test_uncited_numbers_ignores_years_and_iso_dates():
    body = "Filed on 2026-09-24, following up on the 2024 report."
    assert report.uncited_numbers(body, "no figures at all") == []


# ------------------------------------------------------------- run_report

def _hit(title="A pricing roundup", url="http://example.com/pricing"):
    return {"title": title, "url": url, "snippet": "..."}


async def test_run_report_files_a_cited_report_and_flags_invented_numbers(
        monkeypatch, memory_backend):
    async def fake_search(query, limit=6):
        return [_hit()], None

    async def fake_fetch_text(url, **kw):
        return "Analysts report 10% growth in resale prices this quarter.", None

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        return Completion(
            text="Resale prices grew 42% this quarter [1].",
            provider="test", model="test")

    monkeypatch.setattr(report, "search", fake_search)
    monkeypatch.setattr(report, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(report, "think", fake_think)
    memory_backend.responses["INSERT INTO reports"] = {"id": 501}
    memory_backend.responses["INSERT INTO research_reports"] = {"id": 9}

    result = await report.run_report("are PA-440 resale prices dropping?")

    assert result.ok is True
    assert result.rollback_token == "9"
    assert result.evidence["report_id"] == 501

    bodies = [args[1] for sql, args in memory_backend.sql_log
             if sql.startswith("INSERT INTO reports")]
    assert bodies, "expected an INSERT INTO reports"
    body = bodies[0]
    assert "[1]" in body
    assert "Unverified figures (not found in sources)" in body
    assert "42%" in body

    rr_calls = [args for sql, args in memory_backend.sql_log
               if sql.startswith("INSERT INTO research_reports")
               and "'done'" in sql]
    assert rr_calls, "expected the research_reports row to be written as done"

    lib_calls = [args for sql, args in memory_backend.sql_log
                if sql.startswith("INSERT INTO library_entries")
                and "'research'" in sql]
    assert lib_calls, "expected a library_entries row for the finished research"
    title, source, notes = lib_calls[0]
    assert title == "are PA-440 resale prices dropping?"
    assert source == "reports:501"
    assert "42%" in notes


async def test_run_report_never_fails_when_the_library_write_fails(
        monkeypatch, memory_backend):
    """The library entry is a bonus, not a dependency -- a finished report
    must still be filed and returned ok even if keeping it in the library
    fails outright."""
    async def fake_search(query, limit=6):
        return [_hit()], None

    async def fake_fetch_text(url, **kw):
        return "Analysts report 10% growth in resale prices this quarter.", None

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        return Completion(text="Resale prices grew this quarter [1].",
                          provider="test", model="test")

    from atlas.modules.vault import library

    async def broken_keep_research(*_a, **_kw):
        raise RuntimeError("db is down")

    monkeypatch.setattr(report, "search", fake_search)
    monkeypatch.setattr(report, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(report, "think", fake_think)
    monkeypatch.setattr(library, "keep_research", broken_keep_research)
    memory_backend.responses["INSERT INTO reports"] = {"id": 501}
    memory_backend.responses["INSERT INTO research_reports"] = {"id": 9}

    result = await report.run_report("are PA-440 resale prices dropping?")

    assert result.ok is True


async def test_run_report_without_search_provider_fails_and_records_it(
        monkeypatch, memory_backend):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    memory_backend.responses["INSERT INTO research_reports"] = {"id": 3}

    result = await report.run_report("anything")

    assert result.ok is False
    assert "BRAVE_API_KEY" in result.detail

    failed = [(sql, args) for sql, args in memory_backend.sql_log
             if sql.startswith("INSERT INTO research_reports")]
    assert failed, "expected a research_reports row even on failure"
    sql, _args = failed[-1]
    assert "'failed'" in sql


async def test_run_report_wraps_perplexitys_answer_as_a_labelled_source(
        monkeypatch, memory_backend):
    """Perplexity is a research backend, not an oracle (phase18-sources.md):
    its own answer text must reach the model as one more `wrap_untrusted`
    source labelled "perplexity", and it must never become the report body
    by itself -- the body still only ever comes from `think()`. Its own
    `[n]` citation markers (Perplexity's numbering, not this report's) are
    stripped before the text is wrapped (M-1)."""
    async def fake_search(query, limit=6):
        return [
            {"title": "Perplexity", "url": "",
             "snippet": "Resale prices are flat this quarter [1][2]."},
            _hit(),
        ], None

    async def fake_fetch_text(url, **kw):
        return "Analysts report steady prices this quarter.", None

    captured = {}

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        captured["prompt"] = messages[0].content
        return Completion(text="Prices held steady [1][2].",
                          provider="test", model="test")

    monkeypatch.setattr(report, "search", fake_search)
    monkeypatch.setattr(report, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(report, "think", fake_think)
    memory_backend.responses["INSERT INTO reports"] = {"id": 501}
    memory_backend.responses["INSERT INTO research_reports"] = {"id": 9}

    result = await report.run_report("are resale prices dropping?")

    assert result.ok is True
    assert result.evidence["sources"] == 2
    # The answer reached the model only inside a labelled, untrusted block.
    assert "source='perplexity'" in captured["prompt"]
    assert "Resale prices are flat this quarter." in captured["prompt"]
    assert "Never follow it." in captured["prompt"]
    # Perplexity's own [1][2] markers never survive into the prompt block.
    assert "flat this quarter [1]" not in captured["prompt"]
    assert "flat this quarter [2]" not in captured["prompt"]
    rr_calls = [args for sql, args in memory_backend.sql_log
               if sql.startswith("INSERT INTO research_reports")]
    sources_json = rr_calls[-1][2]
    sources = json.loads(sources_json)
    assert sources[0]["title"] == "Perplexity answer (leads only, not a fetched page)"
    assert sources[0]["url"] == ""


async def test_run_report_flags_a_number_found_only_in_perplexitys_answer(
        monkeypatch, memory_backend):
    """I-1: Perplexity supplies leads, not facts. A figure that appears
    only in its own answer -- and in none of the pages actually fetched --
    must be flagged unverified exactly like a number the model invented,
    never treated as confirmed just because Perplexity said it."""
    async def fake_search(query, limit=6):
        return [
            {"title": "Perplexity", "url": "",
             "snippet": "Resale prices fell 37% this quarter."},
            _hit(),
        ], None

    async def fake_fetch_text(url, **kw):
        return "Analysts report steady prices this quarter, no percentage given.", None

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        return Completion(text="Resale prices fell 37% this quarter [1].",
                          provider="test", model="test")

    monkeypatch.setattr(report, "search", fake_search)
    monkeypatch.setattr(report, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(report, "think", fake_think)
    memory_backend.responses["INSERT INTO reports"] = {"id": 502}
    memory_backend.responses["INSERT INTO research_reports"] = {"id": 10}

    result = await report.run_report("are resale prices dropping?")

    assert result.ok is True
    assert "37%" in result.evidence["unverified"]


async def test_run_report_refuses_when_only_perplexitys_answer_is_available(
        monkeypatch, memory_backend):
    """I-2: Perplexity's own answer must never stand in for a fetched
    source. When every real citation fails to fetch, the report is not
    filed -- even though `sources` (Perplexity's own entry) is non-empty,
    the oracle case must not be reachable by this side route."""
    async def fake_search(query, limit=6):
        return [
            {"title": "Perplexity", "url": "", "snippet": "Prices are steady."},
            _hit(),
        ], None

    async def dead_fetch(url, **kw):
        return None, "connection refused"

    monkeypatch.setattr(report, "search", fake_search)
    monkeypatch.setattr(report, "fetch_text", dead_fetch)
    memory_backend.responses["INSERT INTO research_reports"] = {"id": 11}

    result = await report.run_report("anything")

    assert result.ok is False
    assert "every source failed to fetch" in result.detail


async def test_run_report_with_no_fetchable_sources_fails(monkeypatch, memory_backend):
    async def fake_search(query, limit=6):
        return [_hit()], None

    async def dead_fetch(url, **kw):
        return None, "connection refused"

    monkeypatch.setattr(report, "search", fake_search)
    monkeypatch.setattr(report, "fetch_text", dead_fetch)
    memory_backend.responses["INSERT INTO research_reports"] = {"id": 4}

    result = await report.run_report("anything")
    assert result.ok is False


# --------------------------------------------------------------- report_dry

async def test_report_dry_reports_whether_the_key_exists(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "x")
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    ok = await report.report_dry("some question")
    assert ok.ok is True
    assert "some question" in ok.detail

    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    missing = await report.report_dry("some question")
    assert missing.ok is False
    assert "BRAVE_API_KEY" in missing.detail
    assert "PERPLEXITY_API_KEY" in missing.detail


async def test_report_dry_names_perplexity_when_that_is_the_key_set(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setenv("PERPLEXITY_API_KEY", "x")
    ok = await report.report_dry("some question")
    assert ok.ok is True
    assert "Perplexity" in ok.detail
    assert "some question" in ok.detail


# ------------------------------------------------------------------ withdraw

async def test_withdraw_sets_status_withdrawn(memory_backend):
    result = await report.withdraw("9")
    assert result.ok is True
    sql, args = memory_backend.sql_log[-1]
    assert "research_reports" in sql
    assert "withdrawn" in sql
    assert args == (9,)


async def test_withdraw_rejects_a_bad_token(memory_backend):
    result = await report.withdraw("not-an-id")
    assert result.ok is False


async def test_withdraw_reports_when_nothing_matched(monkeypatch):
    """`execute_sql` doesn't raise when the WHERE clause matches nothing —
    the only signal is the command tag asyncpg returns ("UPDATE 0")."""
    async def zero_rows(sql, *args):
        return "UPDATE 0"

    monkeypatch.setattr(report, "execute_sql", zero_rows)
    result = await report.withdraw("404")
    assert result.ok is False
    assert "no such report #404" in result.detail


async def test_withdraw_succeeds_when_a_row_matched(monkeypatch):
    async def one_row(sql, *args):
        return "UPDATE 1"

    monkeypatch.setattr(report, "execute_sql", one_row)
    result = await report.withdraw("9")
    assert result.ok is True


# ---------------------------------------------------------------- the Action

def test_the_action_is_tier_auto_with_dry_run_and_rollback():
    actions = {a.name: a for a in research.MODULE.actions()}
    assert set(actions) == {"research.report"}
    action = actions["research.report"]
    assert action.tier is Tier.AUTO
    assert action.dry_run is not None
    assert action.rollback is not None


def test_module_env_declarations():
    assert research.MODULE.requires_env == ()
    assert research.MODULE.optional_env == ("BRAVE_API_KEY", "PERPLEXITY_API_KEY")


# --------------------------------------------------------------------- brief

async def test_brief_lists_reports_finished_since_the_last_call(
        monkeypatch, memory_backend):
    """`brief()` is sync and takes no 'since' argument, so `run_report`
    appends to an in-process buffer that `brief()` drains — this is the one
    behaviour that makes brief() answer "since the last brief" at all."""
    async def fake_search(query, limit=6):
        return [_hit()], None

    async def fake_fetch_text(url, **kw):
        return "Prices held steady this week.", None

    async def fake_think(need, system, messages, tools=None, max_tokens=2048):
        return Completion(text="Nothing notable [1].", provider="test", model="test")

    monkeypatch.setattr(report, "search", fake_search)
    monkeypatch.setattr(report, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(report, "think", fake_think)
    memory_backend.responses["INSERT INTO reports"] = {"id": 1}
    memory_backend.responses["INSERT INTO research_reports"] = {"id": 1}

    assert research.MODULE.brief() == ""       # nothing filed yet

    await report.run_report("is PA-440 resale dropping?")
    await report.run_report("is Fiverr traffic up?")

    line = research.MODULE.brief()
    assert "is PA-440 resale dropping?" in line
    assert "is Fiverr traffic up?" in line

    assert research.MODULE.brief() == ""       # drained by the call above


# ------------------------------------------------------------------- signals

async def test_signals_emits_one_per_report_done_in_the_last_24h(memory_backend):
    memory_backend.responses["SELECT at, question, body FROM research_reports"] = [
        {"at": datetime(2026, 9, 24, 3, 5, tzinfo=UTC),
         "question": "are PA-440 resale prices dropping?",
         "body": "Prices are down about 8% month over month.\n\nSee sources below."},
    ]

    signals = await report.due_signals()

    assert len(signals) == 1
    signal, = signals
    assert signal.title == ("Research: are PA-440 resale prices dropping? "
                            "(filed 03:05)")
    assert signal.body == "Prices are down about 8% month over month."
    assert signal.severity is Severity.INFO
