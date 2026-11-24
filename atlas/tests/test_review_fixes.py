"""Review fixes on feat/atlas-improve: a database the new schema has not reached
still alerts, and the PG lane never touches a live container."""
from __future__ import annotations

import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]








def _lane(name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(ROOT / "ops" / "pg_lane.sh")],
                          check=False,
                          env={"PG_LANE_NAME": name, "PATH": "/usr/bin:/bin"},
                          capture_output=True, text=True, timeout=30)


def test_pg_lane_refuses_live_and_unknown_containers():
    for name in ("atlas-postgres-1", "atlas-agency-postgres-1", "postgres"):
        out = _lane(name)
        assert out.returncode == 2, (name, out.stderr)
        assert "refusing" in out.stderr
