"""The hard wipe is by hand only and touches only Miro's own notes."""
from __future__ import annotations

import ast
import pathlib

import pytest

from atlas.kernel import memory_wipe


@pytest.fixture
def db(monkeypatch):
    seen: list[tuple] = []

    async def fetch_all(sql, *args):
        seen.append((" ".join(sql.split()), args))
        return [{"id": 1}, {"id": 2}]

    async def audit(*a, **k):
        seen.append(("audit", a))
    monkeypatch.setattr(memory_wipe, "fetch_all", fetch_all)
    monkeypatch.setattr(memory_wipe, "audit", audit)
    return seen


async def test_hidden_only_deletes_hidden_own_rows(db):
    assert await memory_wipe.wipe(hidden=True) == 2
    sql = db[0][0]
    assert "deleted_at IS NOT NULL" in sql and "namespace = 'owner'" in sql


async def test_by_id_stays_inside_own_namespaces(db):
    await memory_wipe.wipe(ids=[3, 4])
    assert "namespace = 'owner'" in db[0][0] and db[0][1] == ([3, 4],)


async def test_everything_needs_yes_and_one_choice(db):
    with pytest.raises(ValueError):
        await memory_wipe.wipe(everything=True)
    with pytest.raises(ValueError):
        await memory_wipe.wipe(everything=True, hidden=True, confirmed=True)
    with pytest.raises(ValueError):
        await memory_wipe.wipe()
    assert await memory_wipe.wipe(everything=True, confirmed=True) == 2


def test_nothing_but_the_cli_can_reach_the_wipe():
    root = pathlib.Path(__file__).resolve().parent.parent / "atlas"
    for path in root.rglob("*.py"):
        if path.name in {"memory_wipe.py", "cli.py"}:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            elif isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            assert "memory_wipe" not in names, path


def test_the_hard_wipe_is_reachable_only_from_the_cli():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1] / "atlas"
    users = {p.relative_to(root).as_posix() for p in root.rglob("*.py")
             if "import memory_wipe" in p.read_text()
             or "memory_wipe import" in p.read_text()}
    assert users == {"cli.py"}
