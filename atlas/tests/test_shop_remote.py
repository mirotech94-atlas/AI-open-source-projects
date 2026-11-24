"""T12: `academy.publish_shop_remote` pushes the `shop-build` output to the
storefront VM (ACADEMY_STORE_HOST:ACADEMY_STORE_ROOT). Tier 2: Miro approves
every push. Its argv names the VM root, scopes `--delete` to one new release,
and never carries `.env` or `data/app`; the dry run spawns nothing; watch-only
refuses the run before anything is spawned. No test here reaches a network."""
from __future__ import annotations

import asyncio
import json
import os

import pytest

from atlas.kernel import executor
from atlas.kernel.contracts import Tier
from atlas.kernel.executor import HOLD, ExecutionRequest, execute
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY
from atlas.modules import academy
from atlas.modules.academy import publish, shop_remote

HOST = "atlasweb@agency.example.com"
ROOT = "/srv/sites"
STAMP = "20261027T201500123456Z"


class Spawned:
    """Stands in for asyncio.create_subprocess_exec: records every argv and
    answers with scripted stdout; nothing is ever run."""

    def __init__(self, stdout: bytes = b"", code: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.stdout, self.code = stdout, code

    async def __call__(self, *argv, **_kw):
        self.calls.append(list(argv))
        outer = self

        class Proc:
            returncode = outer.code

            async def communicate(self):
                return outer.stdout, b""

            def kill(self):
                pass

            async def wait(self):
                return outer.code
        return Proc()


@pytest.fixture
def built(monkeypatch, tmp_path):
    build = tmp_path / "build"
    release = build / STAMP
    (release / "free").mkdir(parents=True)
    (release / "index.html").write_text("<h1>ATLAS Academy</h1>")
    (release / "free" / "n8n.json").write_text("{}")
    os.symlink(STAMP, build / "current")
    monkeypatch.setenv(publish.BUILD_ENV, str(build))
    monkeypatch.setenv("ACADEMY_STORE_HOST", HOST)
    monkeypatch.setenv("ACADEMY_STORE_ROOT", ROOT)
    spawned = Spawned()
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawned)
    return spawned


def _action():
    return {a.name: a for a in academy.MODULE.actions()}["academy.publish_shop_remote"]


# ------------------------------------------------------------------ tier

def test_the_remote_push_is_tier_2_with_a_dry_run_and_a_rollback():
    a = _action()
    assert a.tier is Tier.ASK
    decision = classify(a)
    assert decision.allowed and decision.tier is Tier.ASK
    assert a.dry_run is not None and a.rollback is not None
    assert a.params == {}


# ------------------------------------------------------------------ argv

def test_the_rsync_argv_names_the_vm_root_and_excludes_secrets():
    argv = shop_remote.push_argv("/data/build/" + STAMP, HOST, ROOT, STAMP)
    assert argv[0] == "rsync" and "--delete" in argv
    assert argv[-1] == f"{HOST}:{ROOT}/.releases/shop/{STAMP}/"
    assert argv[-2] == f"/data/build/{STAMP}/"
    assert "--exclude=.env" in argv and "--exclude=data/app" in argv
    # the excludes come before the paths, where rsync reads options
    assert argv.index("--exclude=.env") < len(argv) - 2


@pytest.mark.parametrize("host,root", [
    ("-oProxyCommand=x", ROOT), ("root@agency.example.com; rm", ROOT),
    (HOST, "srv/sites"), (HOST, "/srv/../etc"), (HOST, "/srv/si tes"),
    (HOST, "/"), ("", ROOT), (HOST, ""),
])
async def test_an_unsafe_host_or_root_is_refused_before_anything_runs(
        built, monkeypatch, host, root):
    monkeypatch.setenv("ACADEMY_STORE_HOST", host)
    monkeypatch.setenv("ACADEMY_STORE_ROOT", root)
    assert not (await shop_remote.publish_shop_remote_dry()).ok
    assert not (await shop_remote.publish_shop_remote()).ok
    assert built.calls == []


# ------------------------------------------------------------------ dry run

async def test_the_dry_run_lists_the_push_and_spawns_nothing(built):
    out = await shop_remote.publish_shop_remote_dry()
    assert out.ok, out.detail
    assert built.calls == []                       # no ssh, no rsync, no network
    assert STAMP in out.detail
    assert f"{HOST}:{ROOT}/.releases/shop/{STAMP}/" in out.detail
    assert "rsync -n" in out.detail
    assert "index.html" in out.detail and "free/n8n.json" in out.detail


async def test_the_dry_run_refuses_without_a_build(built, monkeypatch, tmp_path):
    monkeypatch.setenv(publish.BUILD_ENV, str(tmp_path / "nothing"))
    out = await shop_remote.publish_shop_remote_dry()
    assert not out.ok and "shop-build" in out.detail
    assert built.calls == []


async def test_a_build_with_a_symlink_inside_is_refused(built, tmp_path):
    os.symlink("/etc/passwd", tmp_path / "build" / STAMP / "leak")
    assert not (await shop_remote.publish_shop_remote_dry()).ok
    assert not (await shop_remote.publish_shop_remote()).ok
    assert built.calls == []


# ------------------------------------------------------------------ run

async def test_the_run_pushes_one_release_then_swaps_the_link(built):
    built.stdout = b".releases/shop/20261020T100000000000Z\n"
    out = await shop_remote.publish_shop_remote()
    assert out.ok, out.detail
    kinds = [c[0] for c in built.calls]
    assert kinds == ["ssh", "rsync", "ssh"]
    rsync = built.calls[1]
    assert rsync[-1] == f"{HOST}:{ROOT}/.releases/shop/{STAMP}/"
    assert "--exclude=.env" in rsync and "--exclude=data/app" in rsync
    swap = built.calls[2]
    assert HOST in swap and f".releases/shop/{STAMP}" in swap[-1]
    token = json.loads(out.rollback_token)
    assert token["release"] == STAMP
    assert token["previous"] == "20261020T100000000000Z"


async def test_the_rollback_re_points_the_previous_release(built):
    token = json.dumps({"host": HOST, "root": ROOT, "release": STAMP,
                        "previous": "20261020T100000000000Z"})
    out = await shop_remote.publish_shop_remote_rollback(token)
    assert out.ok, out.detail
    assert len(built.calls) == 1 and built.calls[0][0] == "ssh"
    cmd = built.calls[0][-1]
    assert ".releases/shop/20261020T100000000000Z" in cmd
    assert f".releases/shop/{STAMP}" in cmd          # refused if no longer live


async def test_the_rollback_of_a_first_push_restores_nothing(built):
    token = json.dumps({"host": HOST, "root": ROOT, "release": STAMP,
                        "previous": None})
    out = await shop_remote.publish_shop_remote_rollback(token)
    assert out.ok and built.calls == []


async def test_a_push_over_a_foreign_link_refuses_its_rollback(built):
    built.stdout = b"/srv/old-shop\n"
    out = await shop_remote.publish_shop_remote()
    assert out.ok and "/srv/old-shop" in out.detail
    built.calls.clear()
    back = await shop_remote.publish_shop_remote_rollback(out.rollback_token)
    assert not back.ok and "/srv/old-shop" in back.detail
    assert built.calls == []


async def test_a_malformed_rollback_token_runs_nothing(built):
    token = json.dumps({"host": HOST, "root": ROOT, "release": "x; rm -rf /",
                        "previous": None})
    assert not (await shop_remote.publish_shop_remote_rollback(token)).ok
    assert built.calls == []


# ------------------------------------------------------------------ watch-only

async def test_watch_only_refuses_the_push_before_anything_spawns(
        built, monkeypatch, memory_backend):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    a = _action()
    REGISTRY.actions[a.name] = a
    REGISTRY.decisions[a.name] = classify(a)
    executor._recent.clear()
    try:
        out = await execute(ExecutionRequest(a.name, {}, "push the store",
                                             approved_by="slack:U1"))
    finally:
        REGISTRY.actions.pop(a.name, None)
        REGISTRY.decisions.pop(a.name, None)
    assert not out.ok
    assert (out.evidence or {}).get(HOLD) == "watch_only"
    assert built.calls == []
