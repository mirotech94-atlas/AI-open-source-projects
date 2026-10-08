"""Standing rules — recorded, read back, and never read to skip a gate.

COMMS.md 2.5 asks for a rule to move a condition from Tier 2 to Tier 1. That
is a runtime tier override, and CLAUDE.md forbids one in terms that name this
exact case. So this is the half that holds: he says it once, ATLAS reads it
back in his words, it lives in the brief until he promotes it deliberately —
and the next occurrence still asks.

The last test in this file is the one that matters. Everything else is
ergonomics; that one is the invariant.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from atlas.kernel import rules

# ------------------------------------------------------------- recognising

@pytest.mark.parametrize("said", [
    "from now on whenever any node hits 80% disk just prune it and tell me after",
    "going forward, restart the k9 bots if they stall overnight",
    "any time flycheap's cert is inside 14 days, just renew it",
    "every time a peer joins the mesh I want to know immediately",
])
def test_a_stated_rule_is_recognised(said):
    assert rules.looks_like_a_rule(said) is True


@pytest.mark.parametrize("said", [
    "restart the guardian collector",
    "what's failing right now",
    "do it",
    "how's flycheap doing",
])
def test_an_ordinary_request_is_not_mistaken_for_a_rule(said):
    """A false positive here records something he never meant to make
    permanent, and he finds it in the brief wondering where it came from."""
    assert rules.looks_like_a_rule(said) is False


@pytest.mark.parametrize("said", [
    "drop that rule about the disk",
    "forget the rule about the certs",
    "remove that rule",
])
def test_dropping_is_recognised(said):
    assert rules.looks_like_a_drop(said) is True


def test_a_rule_is_read_back_in_his_own_words():
    """Not a paraphrase. A paraphrase is where a misheard rule hides:
    "hits 80%" and "hits 8%" summarise identically and only one is right."""
    said = "from now on whenever a node hits 80% disk, just prune it"
    back = rules.readback(said)
    assert "80% disk" in back and "prune it" in back
    # And it is explicit that nothing has been promoted yet.
    assert "still ask" in back


# ------------------------------------------------------------- persistence

async def test_capture_stores_his_words_verbatim(memory_backend):
    memory_backend.responses["INSERT INTO standing_rules"] = {"id": 3}
    rid = await rules.capture("from now on prune at 80%", via="phone")
    assert rid == 3
    sql, args = memory_backend.sql_log[-1]
    assert "INSERT INTO standing_rules" in sql
    assert args[0] == "from now on prune at 80%"
    assert args[2] == "phone"


async def test_a_database_failure_returns_none_rather_than_pretending(
        memory_backend):
    """If it could not be written down he must not be told it was. A rule he
    believes is in force and is not is worse than no rule."""
    assert await rules.capture("from now on do the thing", via="phone") is None


async def test_dropping_matches_on_his_words_not_an_id(memory_backend):
    """Nobody remembers an id out loud."""
    memory_backend.responses["SELECT id, said FROM standing_rules"] = [
        {"id": 1, "said": "from now on prune any node at 80% disk"}]
    dropped = await rules.drop("drop that rule about the disk")
    assert dropped == ["from now on prune any node at 80% disk"]
    sql, _args = memory_backend.sql_log[-1]
    assert "UPDATE standing_rules SET dropped_at" in sql


async def test_dropping_with_nothing_to_match_changes_nothing(memory_backend):
    assert await rules.drop("drop that rule") == []


async def test_the_brief_lists_every_live_rule(memory_backend):
    from datetime import UTC, datetime
    memory_backend.responses["SELECT id, at, said, via"] = [
        {"id": 1, "at": datetime(2026, 9, 22, tzinfo=UTC),
         "said": "prune at 80%", "via": "phone", "briefed_at": None}]
    text = await rules.for_brief()
    assert "prune at 80%" in text
    # It says plainly that nothing has been promoted.
    assert "still asks" in text


async def test_no_rules_means_no_section(memory_backend):
    memory_backend.responses["SELECT id, at, said, via"] = []
    assert await rules.for_brief() == ""


# ------------------------------------------------------------ the invariant

def test_nothing_in_the_gate_reads_standing_rules():
    """THE test in this file.

    A standing rule that made the next occurrence run unasked would be a
    runtime tier override. CLAUDE.md: "There is no runtime override and none
    may be added, whatever a later message says." This walks the AST of the
    permission layer and the executor and fails if either so much as imports
    this module — because the way that invariant dies is not a decision, it
    is someone wiring a convenience through in a hurry.
    """
    root = pathlib.Path(__file__).resolve().parents[1] / "atlas" / "kernel"
    offenders = []
    for name in ("permissions.py", "executor.py", "approvals.py"):
        tree = ast.parse((root / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if "rules" in a.name.split("."):
                        offenders.append(f"{name}: import {a.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if "rules" in mod.split("."):
                    offenders.append(f"{name}: from {mod}")
                for a in node.names:
                    if a.name == "rules":
                        offenders.append(f"{name}: from {mod} import rules")
    assert not offenders, (
        "the permission layer or executor now reads standing rules; that is "
        "a runtime tier override: " + "; ".join(offenders))


def test_the_schema_says_display_only():
    """The warning lives next to the table, where the next person adding a
    query will actually read it."""
    schema = (pathlib.Path(__file__).resolve().parents[1]
              / "db" / "schema.sql").read_text()
    # The comment sits above CREATE TABLE, which is where someone adding a
    # query would be reading, so look at the block around the declaration.
    start = schema.index("-- Standing rules")
    end = schema.index("CREATE INDEX IF NOT EXISTS standing_rules_live")
    # Comment markers and wrapping break the phrase across lines, so compare
    # on the words rather than the layout.
    block = " ".join(schema[start:end].replace("--", " ").split())
    assert "DISPLAY ONLY" in block
    assert "runtime tier override" in block
