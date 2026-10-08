"""`python -m atlas.cli selftest` on a machine with no credentials and no
database: the module, check and action listing is the point of running it
bare, and the one step that needs Postgres -- the audit chain -- must say it
could not be read rather than end the run in a traceback. Unverifiable is
not OK (invariant 7), so the exit code still says the run did not pass.
"""
from __future__ import annotations

import pytest

from atlas import cli


async def _no_boot() -> None:
    return None


async def test_an_unreadable_audit_chain_is_unknown_not_a_traceback(monkeypatch, capsys):
    async def no_database():
        raise KeyError("DATABASE_URL")

    monkeypatch.setattr(cli, "boot", _no_boot)
    monkeypatch.setattr(cli, "verify_chain", no_database)
    with pytest.raises(SystemExit) as stopped:
        await cli.selftest()
    assert stopped.value.code == 1
    out = capsys.readouterr().out
    assert "audit    : UNKNOWN" in out
    assert "DATABASE_URL" in out
    assert "OK" not in out.split("audit    :")[1]


async def test_an_intact_chain_still_says_ok(monkeypatch, capsys):
    async def intact():
        return True, "12 rows, chain intact"

    monkeypatch.setattr(cli, "boot", _no_boot)
    monkeypatch.setattr(cli, "verify_chain", intact)
    await cli.selftest()
    assert "audit    : OK — 12 rows, chain intact" in capsys.readouterr().out
