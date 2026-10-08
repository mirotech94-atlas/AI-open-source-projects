"""Phase 18 schema guard: the three new tables exist in db/schema.sql.

No database, no network. This just parses the schema text, the same way
`test_schema_phase16.py` does, so the tasks that write module code against
`lessons`, `content_media` and `render_spend` can rely on the tables being
there first.
"""
from __future__ import annotations

import pathlib
import re

import pytest

SCHEMA_PATH = pathlib.Path(__file__).resolve().parent.parent / "db" / "schema.sql"

PHASE_18_TABLES = [
    "lessons",
    "content_media",
    "render_spend",
]


@pytest.fixture(scope="module")
def schema_text() -> str:
    return SCHEMA_PATH.read_text()


@pytest.mark.parametrize("table", PHASE_18_TABLES)
def test_table_created_if_not_exists(schema_text: str, table: str) -> None:
    pattern = rf"CREATE TABLE IF NOT EXISTS {re.escape(table)}\s*\("
    assert re.search(pattern, schema_text), (
        f"expected 'CREATE TABLE IF NOT EXISTS {table} (' in db/schema.sql"
    )


def test_the_never_repeat_lookup_is_indexed(schema_text: str) -> None:
    """`lessons.blocked()` reads by (action, params_key) on every proposal.
    Without the index that is a sequential scan on the approval path."""
    assert re.search(
        r"CREATE INDEX IF NOT EXISTS lessons_action\s+ON lessons \(action, params_key\)",
        schema_text), "expected the lessons_action index in db/schema.sql"


def test_a_lesson_can_be_cleared_and_counted(schema_text: str) -> None:
    """The two columns never-repeat depends on: `times` (a mistake is allowed
    once) and `cleared_at` (he said it is different now). A table without
    them would satisfy the CREATE TABLE test and still not work."""
    block = schema_text.split("CREATE TABLE IF NOT EXISTS lessons (", 1)[-1]
    block = block.split(");", 1)[0]
    for column in ("times", "cleared_at", "params_key", "kind", "lesson"):
        assert re.search(rf"^\s*{column}\b", block, re.MULTILINE), (
            f"the lessons table needs a {column} column")
