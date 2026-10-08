"""Every SQL string under atlas/ names tables and columns db/schema.sql has.

The module and API tests stub `fetch_all` / `execute_sql`, so no query in
them ever reaches Postgres. That is right for speed, and it means a query
naming a column that does not exist passes every test and 500s the first time
it runs for real: Task 22's `/api/tasks` joined `f.id = p.finding_id`, and
`proposals` has no `finding_id`. This lint is the cheap half of the fix (the
other half is one pass against a live database).

What it checks, in every string literal that reads as SQL:
  * `FROM`/`JOIN`/`INTO`/`UPDATE`/`USING` <table> is a table in the schema;
  * `alias.column` resolves, through that string's own aliases, to a column;
  * `INSERT INTO t (a, b)`, `ON CONFLICT (a)`, `UPDATE t SET a = ...` and
    `DO UPDATE SET a = ...` name columns of `t`; `EXCLUDED.a` is a column of
    the INSERT's target.

What it does not try to do: unqualified columns (which table they belong to
needs a real parser), and anything behind an alias it cannot pin to one
table -- a CTE, a derived table or LATERAL subquery `(...) alias`, an
f-string placeholder. Those are skipped, never guessed.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "db" / "schema.sql"
ATLAS = ROOT / "atlas"

# Stands in for an f-string's `{...}`: never a table, never an alias.
PLACEHOLDER = "__ph__"

# Columns every Postgres table has without declaring them.
SYSTEM_COLUMNS = frozenset({"ctid", "xmin", "xmax", "tableoid"})

# Words that can follow a table name where an alias would otherwise go.
KEYWORDS = frozenset({
    "all", "and", "any", "as", "asc", "by", "cross", "default", "delete", "desc",
    "distinct", "do", "except", "exists", "fetch", "for", "from", "full", "group",
    "having", "in", "inner", "insert", "intersect", "into", "is", "join", "lateral",
    "left", "limit", "natural", "not", "null", "offset", "on", "only", "or", "order",
    "outer", "returning", "right", "select", "set", "table", "tablesample", "then",
    "union", "update", "using", "values", "when", "where", "window", "with",
})

# First words of a CREATE TABLE entry that are not a column.
CONSTRAINT_WORDS = frozenset(
    {"constraint", "primary", "unique", "check", "foreign", "exclude", "like"})

# (file relative to the repo, "table.column") -> why it stands. Empty is the
# goal; an entry is a real defect in a file this task may not edit, recorded
# rather than hidden by loosening a rule.
KNOWN: dict[tuple[str, str], str] = {}

# Files whose SQL goes to another database, never to db/schema.sql's: the
# PostHog module's HogQL reads PostHog's own `events` table.
OTHER_DATABASES = frozenset({"atlas/modules/posthog/__init__.py"})


# ------------------------------------------------------------------ schema

def _strip_sql(text: str) -> str:
    """Comments and quoted literals out: 'mail.draft_reply' is an action
    name, not alias.column."""
    text = re.sub(r"--[^\n]*", " ", text)
    return re.sub(r"'(?:[^']|'')*'", "''", text)


def _top_level_split(body: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(body):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(body[start:i])
            start = i + 1
    parts.append(body[start:])
    return parts


def _balanced(text: str, open_at: int) -> str:
    """The text inside the parenthesis opening at `open_at`."""
    depth = 0
    for i in range(open_at, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_at + 1:i]
    return text[open_at + 1:]


def parse_schema(text: str) -> dict[str, set[str]]:
    text = _strip_sql(text)
    tables: dict[str, set[str]] = {}
    for m in re.finditer(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(",
                         text, re.IGNORECASE):
        cols = tables.setdefault(m.group(1).lower(), set())
        for part in _top_level_split(_balanced(text, m.end() - 1)):
            word = re.match(r"\s*\"?(\w+)", part)
            if word and word.group(1).lower() not in CONSTRAINT_WORDS:
                cols.add(word.group(1).lower())
    for m in re.finditer(r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(\w+)"
                         r"([^;]*);", text, re.IGNORECASE):
        cols = tables.setdefault(m.group(1).lower(), set())
        for c in re.finditer(r"ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
                             m.group(2), re.IGNORECASE):
            cols.add(c.group(1).lower())
        for c in re.finditer(r"RENAME\s+COLUMN\s+\w+\s+TO\s+(\w+)",
                             m.group(2), re.IGNORECASE):
            cols.add(c.group(1).lower())
    return tables


# --------------------------------------------------------------------- lint

_SQL_SHAPE = re.compile(
    r"\b(?:SELECT\b[\s\S]*\bFROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM)\b")

_SOURCE = re.compile(
    r"\b(FROM|JOIN|INTO|UPDATE|USING)\s+(?:ONLY\s+)?(\w+(?:\.\w+)?)(\s*\()?"
    r"(?:\s+(?:AS\s+)?(\w+))?", re.IGNORECASE)
_MORE = re.compile(r"\s*,\s*(\w+)(\s*\()?(?:\s+(?:AS\s+)?(\w+))?", re.IGNORECASE)


def looks_like_sql(text: str) -> bool:
    return bool(_SQL_SHAPE.search(text))


def lint_sql(sql: str, tables: dict[str, set[str]]) -> list[str]:
    """Problems in one SQL string, each naming the table and column."""
    sql = _strip_sql(sql)
    # `extract(hour FROM at)`, `trim(both FROM x)`: a FROM inside a call's
    # own grammar, not a table source.
    sql = re.sub(r"\(\s*(\w+)\s+FROM\b", r"(\1, ", sql, flags=re.IGNORECASE)
    problems: list[str] = []

    ctes = {m.group(1).lower() for m in
            re.finditer(r"(\w+)\s+AS\s+(?:NOT\s+)?(?:MATERIALIZED\s+)?\(",
                        sql, re.IGNORECASE)}
    derived = {m.group(1).lower() for m in
               re.finditer(r"\)\s*(?:AS\s+)?(\w+)", sql, re.IGNORECASE)
               if m.group(1).lower() not in KEYWORDS}
    unpinned = ctes | derived

    aliases: dict[str, set[str]] = {}

    def source(name: str, call: str | None, alias: str | None) -> None:
        name = name.lower()
        if call or "." in name or name in KEYWORDS or name.startswith(PLACEHOLDER):
            return
        alias_l = alias.lower() if alias else None
        if alias_l in KEYWORDS:
            alias_l = None
        if name in ctes or name.startswith("pg_"):
            if alias_l:
                unpinned.add(alias_l)
            return
        if name not in tables:
            problems.append(f"unknown table {name!r}")
            return
        aliases.setdefault(name, set()).add(name)
        if alias_l:
            aliases.setdefault(alias_l, set()).add(name)

    for m in _SOURCE.finditer(sql):
        source(m.group(2), m.group(3), m.group(4))
        if m.group(1).upper() != "FROM":
            continue
        pos = m.end()
        while more := _MORE.match(sql, pos):
            source(more.group(1), more.group(2), more.group(3))
            pos = more.end()

    def need(table: str, column: str, where: str) -> None:
        column = column.lower()
        if column.startswith(PLACEHOLDER) or column in SYSTEM_COLUMNS:
            return
        if column not in tables.get(table, set()):
            problems.append(f"{where}: {table}.{column} is not a column")

    targets = [m.group(1).lower() for m in re.finditer(r"INSERT\s+INTO\s+(\w+)",
                                                        sql, re.IGNORECASE)]
    for m in re.finditer(r"INSERT\s+INTO\s+(\w+)\s*\(", sql, re.IGNORECASE):
        table = m.group(1).lower()
        if table in tables:
            for col in _top_level_split(_balanced(sql, m.end() - 1)):
                if word := re.fullmatch(r"\s*(\w+)\s*", col):
                    need(table, word.group(1), "INSERT")
    for m in re.finditer(r"ON\s+CONFLICT\s*\(", sql, re.IGNORECASE):
        for table in targets[:1]:
            if table in tables:
                for col in _top_level_split(_balanced(sql, m.end() - 1)):
                    if word := re.fullmatch(r"\s*(\w+)\s*", col):
                        need(table, word.group(1), "ON CONFLICT")

    def set_list(table: str, body: str, where: str) -> None:
        body = re.split(r"\b(?:WHERE|RETURNING|FROM)\b", body, maxsplit=1,
                        flags=re.IGNORECASE)[0]
        for part in _top_level_split(body):
            if col := re.match(r"\s*(\w+)\s*=", part):
                need(table, col.group(1), where)

    for m in re.finditer(r"\bUPDATE\s+(\w+)(?:\s+(?:AS\s+)?\w+)?\s+SET\b(.*)",
                         sql, re.IGNORECASE | re.DOTALL):
        table = m.group(1).lower()
        if table in tables:
            set_list(table, m.group(2), "UPDATE")
    for m in re.finditer(r"DO\s+UPDATE\s+SET\b(.*)", sql, re.IGNORECASE | re.DOTALL):
        for table in targets[:1]:
            if table in tables:
                set_list(table, m.group(1), "DO UPDATE")

    for m in re.finditer(r"(?<![\w.])(\w+)\.(\w+)\b", sql):
        alias, column = m.group(1).lower(), m.group(2)
        if alias == "excluded":
            for table in targets[:1]:
                if table in tables:
                    need(table, column, "EXCLUDED")
            continue
        if alias in unpinned or alias not in aliases:
            continue
        owners = aliases[alias]
        if not any(column.lower() in tables[t] or column.lower() in SYSTEM_COLUMNS
                   for t in owners):
            problems.append(f"{alias}.{column}: not a column of "
                            f"{' or '.join(sorted(owners))}")
    return problems


# ----------------------------------------------------------------- sources

def _docstring_ids(tree: ast.AST) -> set[int]:
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                out.add(id(body[0].value))
    return out


def sql_strings(path: pathlib.Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(), filename=str(path))
    skip = _docstring_ids(tree)
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for part in node.values:
                skip.add(id(part))
            text = "".join(
                p.value if isinstance(p, ast.Constant) and isinstance(p.value, str)
                else f" {PLACEHOLDER}{i} " for i, p in enumerate(node.values))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in skip:
                continue
            text = node.value
        else:
            continue
        if looks_like_sql(text):
            out.append((node.lineno, text))
    return out


# -------------------------------------------------------------------- tests

@pytest.fixture(scope="module")
def tables() -> dict[str, set[str]]:
    return parse_schema(SCHEMA.read_text())


def test_the_schema_parser_reads_create_and_alter(tables):
    assert {"id", "created_at", "action", "status"} <= tables["proposals"]
    # Added by ALTER TABLE ... ADD COLUMN, not in the CREATE.
    assert "media_token" in tables["content_media"]
    assert "dedup_key" in tables["proposals"]
    assert "primary" not in tables["proposals"]


def test_it_flags_the_task_22_join(tables):
    bad = """SELECT p.id, f.title FROM proposals p
             LEFT JOIN findings f ON f.id = p.finding_id"""
    assert any("p.finding_id" in p for p in lint_sql(bad, tables))
    good = """SELECT p.id, f.title FROM proposals p
              LEFT JOIN findings f ON f.proposal_id = p.id"""
    assert lint_sql(good, tables) == []


def test_it_flags_unknown_tables_and_insert_columns(tables):
    assert lint_sql("SELECT x.a FROM no_such_table x", tables) == [
        "unknown table 'no_such_table'"]
    problems = lint_sql(
        "INSERT INTO proposals (action, no_such_col) VALUES ($1, $2) "
        "ON CONFLICT (dedup_key) DO UPDATE SET nope = EXCLUDED.nada", tables)
    assert any("proposals.no_such_col" in p for p in problems)
    assert any("proposals.nope" in p for p in problems)
    assert any("proposals.nada" in p for p in problems)
    assert any("proposals.bogus" in p for p in lint_sql(
        "UPDATE proposals SET status = $1, bogus = now() WHERE id = $2", tables))


def test_it_skips_what_it_cannot_pin_rather_than_guess(tables):
    lateral = """SELECT c.name, o.whatever FROM checks c
                 LEFT JOIN LATERAL (SELECT at AS whatever FROM observations
                                    WHERE check_id = c.id) o ON TRUE"""
    assert lint_sql(lateral, tables) == []
    cte = """WITH latest AS (SELECT check_id, max(at) AS at FROM observations
                             GROUP BY check_id)
             SELECT l.anything FROM latest l"""
    assert lint_sql(cte, tables) == []
    quoted = "SELECT count(*) FROM actions_log WHERE subject = 'mail.nonsense'"
    assert lint_sql(quoted, tables) == []
    extract = ("SELECT avg(value) FROM observations "
               "WHERE extract(hour from at at time zone 'UTC') = $1")
    assert lint_sql(extract, tables) == []


def _all_sql() -> list[tuple[str, int, str]]:
    return [(str(path.relative_to(ROOT)), line, text)
            for path in sorted(ATLAS.rglob("*.py"))
            for line, text in sql_strings(path)]


def test_the_lint_finds_the_sql_it_is_meant_to_read():
    found = _all_sql()
    assert len(found) > 100
    files = {f for f, _, _ in found}
    assert "atlas/tools/builtin.py" in files
    assert "atlas/api/dashboard.py" in files


def test_every_sql_string_names_real_tables_and_columns(tables):
    problems = []
    for file, line, text in _all_sql():
        if file in OTHER_DATABASES:
            continue
        for problem in lint_sql(text, tables):
            column = re.search(r"(\w+\.\w+)", problem)
            if column and (file, column.group(1)) in KNOWN:
                continue
            problems.append(f"{file}:{line}: {problem}")
    assert not problems, "\n".join(problems)
