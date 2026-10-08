"""The rsync of a deploy and every docker call of the nightshift sandbox are
bounded (audit #28). A child that never answers is killed and reaped, and
the caller gets a reason instead of a hang."""
from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

from atlas.modules.agency import deploy
from atlas.modules.nightshift import sandbox


class Hung:
    """A child process whose communicate() never returns until it is killed."""

    instances: ClassVar[list[Hung]] = []

    def __init__(self, *argv, **_kw) -> None:
        self.argv = argv
        self.killed = False
        self.reaped = False
        self.returncode: int | None = None
        self._dead = asyncio.Event()
        Hung.instances.append(self)

    async def communicate(self):
        await self._dead.wait()
        return b"", b""

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._dead.set()

    async def wait(self) -> int:
        await self._dead.wait()
        self.reaped = True
        return -9


@pytest.fixture
def hung(monkeypatch):
    Hung.instances = []

    async def fake_exec(*argv, **kw):
        return Hung(*argv, **kw)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return Hung.instances


async def test_a_stalled_rsync_is_killed_with_a_reason(monkeypatch, hung):
    monkeypatch.setattr(deploy, "RSYNC_TIMEOUT", 0.05)
    ok, reason = await asyncio.wait_for(deploy._run(["rsync", "-az", "a/", "h:b/"]), 5)
    assert ok is False
    assert "did not finish" in reason and "partially updated" in reason
    assert hung[0].killed and hung[0].reaped


async def test_a_hung_docker_run_is_killed_and_the_container_stopped(
        monkeypatch, hung):
    monkeypatch.setattr(sandbox, "RUN_TIMEOUT", 0.05)
    monkeypatch.setattr(sandbox, "STOP_TIMEOUT", 0.05)
    name, reason = await asyncio.wait_for(sandbox.start(7, "task", "https://x/r"), 5)
    assert name is None
    assert "did not return" in reason and "nightshift-7" in reason
    run, stop = hung[0], hung[1]
    assert run.argv[:2] == ("docker", "run") and run.killed and run.reaped
    # The container docker may have created is stopped, not left running.
    assert stop.argv == ("docker", "stop", "nightshift-7")


async def test_a_hung_inspect_is_docker_unavailable_not_missing(monkeypatch, hung):
    """UNKNOWN upstream, never a guess that the container is gone."""
    monkeypatch.setattr(sandbox, "QUERY_TIMEOUT", 0.05)
    with pytest.raises(sandbox.DockerUnavailable, match="did not answer"):
        await asyncio.wait_for(sandbox.status("nightshift-7"), 5)
    assert hung[0].killed and hung[0].reaped


async def test_hung_logs_return_a_placeholder(monkeypatch, hung):
    monkeypatch.setattr(sandbox, "QUERY_TIMEOUT", 0.05)
    out = await asyncio.wait_for(sandbox.logs("nightshift-7"), 5)
    assert "could not fetch logs" in out
    assert hung[0].killed


async def test_a_hung_stop_gives_up_quietly(monkeypatch, hung):
    monkeypatch.setattr(sandbox, "STOP_TIMEOUT", 0.05)
    await asyncio.wait_for(sandbox.stop("nightshift-7"), 5)
    assert hung[0].killed and hung[0].reaped


async def test_a_hung_branch_delete_is_killed(monkeypatch, hung):
    monkeypatch.setenv("NIGHTSHIFT_GIT_TOKEN", "t" * 20)
    monkeypatch.setattr(sandbox, "DELETE_TIMEOUT", 0.05)
    ok, reason = await asyncio.wait_for(
        sandbox.delete_branch("https://x/r", "nightshift/job-7"), 5)
    assert ok is False and "killed" in reason
    assert hung[0].killed and hung[0].reaped
