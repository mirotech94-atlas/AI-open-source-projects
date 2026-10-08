"""Phase 16 schema guard: the six new tables exist in db/schema.sql.

No database, no network. This just parses the schema text so later tasks
(22-28) can rely on the tables being there before they write module code
against them.
"""
from __future__ import annotations

import pathlib
import re

import pytest

SCHEMA_PATH = pathlib.Path(__file__).resolve().parent.parent / "db" / "schema.sql"

PHASE_16_TABLES = [
    "memory_reads",
    "browser_sessions",
    "messages_inbound",
    "rhythm_hours",
    "rhythm_notes",
    "school_items",
]


@pytest.fixture(scope="module")
def schema_text() -> str:
    return SCHEMA_PATH.read_text()


@pytest.mark.parametrize("table", PHASE_16_TABLES)
def test_table_created_if_not_exists(schema_text: str, table: str) -> None:
    pattern = rf"CREATE TABLE IF NOT EXISTS {re.escape(table)}\s*\("
    assert re.search(pattern, schema_text), (
        f"expected 'CREATE TABLE IF NOT EXISTS {table} (' in db/schema.sql"
    )
