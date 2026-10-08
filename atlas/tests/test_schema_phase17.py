"""Phase 17 schema guard: the three new tables exist in db/schema.sql.

No database, no network. This just parses the schema text so Task 30 (the
marketing agent) can rely on the tables being there before it writes module
code against them.
"""
from __future__ import annotations

import pathlib
import re

import pytest

SCHEMA_PATH = pathlib.Path(__file__).resolve().parent.parent / "db" / "schema.sql"

PHASE_17_TABLES = [
    "youtube_videos",
    "marketing_experiments",
    "monetization_ideas",
]


@pytest.fixture(scope="module")
def schema_text() -> str:
    return SCHEMA_PATH.read_text()


@pytest.mark.parametrize("table", PHASE_17_TABLES)
def test_table_created_if_not_exists(schema_text: str, table: str) -> None:
    pattern = rf"CREATE TABLE IF NOT EXISTS {re.escape(table)}\s*\("
    assert re.search(pattern, schema_text), (
        f"expected 'CREATE TABLE IF NOT EXISTS {table} (' in db/schema.sql"
    )
