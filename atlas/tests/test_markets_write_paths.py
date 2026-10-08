"""Who may write the paper books' decisive state -- an AST read of atlas/.

The kernel owner's ask (2026-09-29, trade-0929):

* no code path but `markets.paper_idea`'s run (`ideas.idea_run`) writes
  status 'approved' to `markets_ideas`, and no write sets that table's
  status from a parameter (a literal is what this test can read);
* only `markets.paper_rebalance`'s run (`playlist.rebalance`) applies new
  playlist weights -- a `kind='rebalance'` holdings row, the proposal's
  'applied' -- and its rollback is the only other writer of weights;
* neither run function is referenced anywhere but its own `action()`, so
  the executor is the only way in.

Static on purpose: it reads every string and call in atlas/, including
code no test executes.
"""
from __future__ import annotations

import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent / "atlas"
_IS = re.IGNORECASE | re.DOTALL
IDEAS = "atlas/modules/markets/ideas.py"
PLAYLIST = "atlas/modules/markets/playlist.py"


def _files() -> list[tuple[str, ast.Module]]:
    out = []
    for path in sorted(ROOT.rglob("*.py")):
        rel = str(path.relative_to(ROOT.parent))
        out.append((rel, ast.parse(path.read_text(), rel)))
    return out


FILES = _files()


class _Walker(ast.NodeVisitor):
    """Every string (plain, or an f-string's literal parts joined) and every
    call, with the function it sits in."""

    def __init__(self, rel: str) -> None:
        self.rel, self.stack = rel, []
        self.strings: list[tuple[str, str, bool]] = []     # (where, text, fstring)
        self.calls: list[tuple[str, ast.Call]] = []
        self.names: list[tuple[str, str]] = []             # (where, name used)

    def where(self) -> str:
        return f"{self.rel}:{'.'.join(self.stack) or '<module>'}"

    def _func(self, node) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _func

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str):
            self.strings.append((self.where(), node.value, False))

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        text = "".join(v.value if isinstance(v, ast.Constant) else "{}"
                       for v in node.values)
        self.strings.append((self.where(), text, True))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append((self.where(), node))
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        self.names.append((self.where(), node.id))

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.names.append((self.where(), node.attr))
        self.generic_visit(node)


WALKS = []
for _rel, _tree in FILES:
    _w = _Walker(_rel)
    _w.visit(_tree)
    WALKS.append(_w)
STRINGS = [s for w in WALKS for s in w.strings]
CALLS = [c for w in WALKS for c in w.calls]
NAMES = [n for w in WALKS for n in w.names]


def _writes(table: str) -> list[tuple[str, str, str, bool]]:
    """(where, verb, sql, fstring) for each UPDATE/INSERT/DELETE on `table`."""
    pat = re.compile(rf"\b(UPDATE|INSERT\s+INTO|DELETE\s+FROM)\s+{table}\b", re.IGNORECASE)
    out = []
    for where, text, fstr in STRINGS:
        m = pat.search(text)
        if m:
            out.append((where, m.group(1).split()[0].upper(), text, fstr))
    return out


def _set_clause(sql: str) -> str:
    m = re.search(r"\bSET\b(.*?)(\bWHERE\b|\bRETURNING\b|\bFROM\b|$)", sql, _IS)
    return m.group(1) if m else ""


# ------------------------------------------------------------ the idea book

def test_only_the_idea_action_run_writes_approved():
    writes = _writes("markets_ideas")
    assert writes, "the scan must be found writing markets_ideas at all"
    approving = set()
    for where, verb, sql, fstr in writes:
        assert not fstr, f"{where}: markets_ideas written from an f-string"
        assert where.startswith(IDEAS), f"{where} writes markets_ideas"
        if verb == "INSERT":
            assert "'approved'" not in sql, f"{where} inserts an approved idea"
            assert "'carded'" in sql, f"{where} inserts a status other than carded"
            continue
        assert verb == "UPDATE", f"{where}: {verb} on markets_ideas"
        clause = _set_clause(sql)
        assert not re.search(r"\bstatus\s*=\s*\$\d", clause), \
            f"{where} sets markets_ideas.status from a parameter"
        if re.search(r"(=|\bTHEN|\bELSE)\s*'approved'", clause, re.IGNORECASE):
            approving.add(where)
    assert approving == {f"{IDEAS}:idea_run"}


def test_open_is_reached_only_from_approved():
    """'open' is written by the booking only, and only on a row whose own
    status is re-checked as 'approved' in the same statement."""
    for where, verb, sql, _ in _writes("markets_ideas"):
        if re.search(r"(=|\bTHEN|\bELSE)\s*'open'", _set_clause(sql), re.IGNORECASE):
            assert where == f"{IDEAS}:settle", where
            assert re.search(r"WHERE\b.*\bstatus\s*=\s*'approved'", sql, re.DOTALL), sql


def test_the_idea_run_is_reached_only_through_its_action():
    uses = [w for w, n in NAMES if n == "idea_run"]
    assert uses == [f"{IDEAS}:action"], uses


# ---------------------------------------------------------- the playlist

def test_only_the_rebalance_writes_new_weights():
    writes = _writes("markets_playlist_holdings")
    assert [(w, v) for w, v, _, _ in writes] == [(f"{PLAYLIST}:_insert", "INSERT")]
    kinds: dict[str, set[str]] = {}
    for where, call in CALLS:
        f = call.func
        name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
        if name != "_insert":
            continue
        arg = call.args[0] if call.args else None
        assert isinstance(arg, ast.Constant), f"{where}: _insert with a computed kind"
        kinds.setdefault(arg.value, set()).add(where)
    assert kinds == {"start": {f"{PLAYLIST}:start"},
                     "rebalance": {f"{PLAYLIST}:rebalance"},
                     "rollback": {f"{PLAYLIST}:rebalance_rollback"}}


def test_only_the_rebalance_marks_a_proposal_applied():
    writers = set()
    for where, verb, sql, fstr in _writes("markets_playlist_proposals"):
        assert not fstr and where.startswith(PLAYLIST), where
        clause = _set_clause(sql)
        assert not re.search(r"\bstatus\s*=\s*\$\d", clause), where
        if "'applied'" in clause:
            writers.add(where)
    assert writers == {f"{PLAYLIST}:rebalance"}


def test_the_rebalance_is_reached_only_through_its_action():
    for fn in ("rebalance", "rebalance_rollback"):
        uses = [w for w, n in NAMES if n == fn]
        assert uses == [f"{PLAYLIST}:action"], (fn, uses)


def test_positive_control_the_checks_see_what_they_forbid():
    """The shapes the rules above refuse are actually caught: the pre-fix
    booking (`status = $4`), a CASE that approves, and an INSERT of one."""
    old = ("UPDATE markets_ideas SET entry_day = $2, entry_price = $3, "
           "status = $4 WHERE id = $1")
    assert re.search(r"\bstatus\s*=\s*\$\d", _set_clause(old))
    case = ("UPDATE markets_ideas SET status = CASE WHEN x THEN 'approved' END "
            "WHERE id = $1 AND status = 'carded'")
    assert re.search(r"(=|\bTHEN|\bELSE)\s*'approved'", _set_clause(case))
    rollback = "UPDATE markets_ideas SET status = 'cancelled' WHERE status = 'approved'"
    assert not re.search(r"(=|\bTHEN|\bELSE)\s*'approved'", _set_clause(rollback))
