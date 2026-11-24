"""Run the dashboard against seeded in-memory data, for looking at it.

Not a test fixture and not shipped in the image: this exists so the design can
be reviewed with realistic content — in particular a module whose checks could
not run sitting next to one that is fine, which is the case the UI must never
blur.

    python tools/preview.py     then open http://127.0.0.1:8099
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SESSION_SECRET", "preview-only-secret-at-least-32-characters")
os.environ.setdefault("DATABASE_URL", "postgresql://preview/preview")
os.environ.setdefault("ATLAS_WATCH_ONLY", "true")
os.environ.setdefault("MESH_ALLOWED_CIDRS", "127.0.0.0/8")

from argon2 import PasswordHasher

os.environ.setdefault("DASHBOARD_PASSWORD_HASH", PasswordHasher().hash("preview"))

from atlas.kernel import state

NOW = datetime.now(UTC)


def ago(minutes: float) -> datetime:
    return NOW - timedelta(minutes=minutes)


CHECK_ROWS = [
    # property, check, last, next, interval, health, detail
    ("flycheap", "affiliate_links", ago(4), NOW + timedelta(minutes=11), 900,
     "ok", "6/6 affiliate links intact"),
    ("flycheap", "conversion_ratio", ago(6), NOW + timedelta(minutes=54), 3600,
     "failing", ("0 bookings from 412 clicks (0.00%) — traffic is normal "
                 "but nothing is converting")),
    ("flycheap", "price_freshness", ago(9), NOW + timedelta(minutes=6), 900,
     "ok", "newest price row is 3 min old (expected under 15)"),
    # The case the whole design turns on: a module ATLAS cannot measure.
    ("guardian", "alert_pipeline", ago(2), NOW + timedelta(minutes=13), 900,
     "unknown", ("canary could not be verified: HTTP 401 from "
                 "/api/admin/alerts/canary/verify — credentials rejected")),
    ("guardian", "agent_checkins", ago(2), NOW + timedelta(minutes=28), 1800,
     "unknown", "/api/admin/agents/summary: timed out"),
    ("inroots", "attendance_submitted", ago(31), NOW + timedelta(minutes=29), 3600,
     "degraded", "18/21 recorded, 3 missing"),
    ("inroots", "standup_bot", ago(31), NOW + timedelta(minutes=29), 3600,
     "ok", "last bot message 12 min ago"),
    ("cloudco", "instances", ago(14), NOW + timedelta(minutes=46), 3600,
     "ok", "7 running, 0 stopped"),
]

FINDINGS = [
    (1, "flycheap.conversion_ratio", "flycheap", 3, "rule", "failing",
     "flycheap.conversion_ratio is failing",
     ("0 bookings from 412 clicks (0.00%) — traffic is normal but nothing "
      "is converting"), 0.0, "%", ago(6), 1),
    (2, "guardian.alert_pipeline", "guardian", 1, "rule", "unknown",
     "guardian.alert_pipeline could not run",
     ("canary could not be verified: HTTP 401 from "
      "/api/admin/alerts/canary/verify — credentials rejected"),
     None, None, ago(2), None),
    (3, "inroots.attendance_submitted", "inroots", 1, "rule", "degraded",
     "inroots.attendance_submitted is degraded", "18/21 recorded, 3 missing",
     18.0, "people", ago(31), None),
    (4, "flycheap.price_freshness", "flycheap", 1, "baseline", "ok",
     "flycheap.price_freshness is 3.4σ above its baseline",
     "41.00 min vs a typical 12.00 (±8.50) for this hour of the week",
     41.0, "min", ago(64), None),
]

PROPOSAL = {
    "id": 1, "action": "flycheap.regenerate_links", "params": "{}",
    "tier": 2, "status": "pending",
    "reason": "Every sampled affiliate link still returns 200 but the "
              "Travelpayouts marker is missing from the redirect chain, so "
              "clicks are not being attributed.",
    "evidence": json.dumps({
        "clicks": 412, "bookings": 0,
        "broken": ["https://flycheap.example/go/abc (marker missing from redirect)"],
    }),
    "rollback_plan": "Restore the link table from snapshot "
                     "snap-2026-09-22T03-40Z, which is taken before any write.",
    "confidence": 0.82,
    "created_at": ago(6), "expires_at": NOW + timedelta(hours=3, minutes=54),
    "dry_run": "would regenerate the link table; 6 sampled link(s) are "
               "currently broken\n\n"
               "  flycheap.example/go/abc   marker missing   -> would rewrite\n"
               "  flycheap.example/go/def   marker missing   -> would rewrite\n"
               "  flycheap.example/go/ghi   HTTP 404         -> would rewrite\n"
               "  flycheap.example/go/jkl   marker missing   -> would rewrite\n"
               "  flycheap.example/go/mno   marker missing   -> would rewrite\n"
               "  flycheap.example/go/pqr   marker missing   -> would rewrite\n\n"
               "snapshot snap-2026-09-22T03-40Z would be taken first\n"
               "no other table is touched",
    "dry_run_ok": True, "dry_run_at": ago(6),
    "finding_id": 1, "finding_title": "flycheap.conversion_ratio is failing",
    "finding_detail": "0 bookings from 412 clicks (0.00%)",
    "check_key": "flycheap.conversion_ratio", "health": "failing",
    "incident_id": None,
}

AUDIT = [
    (3, ago(6), "proposal.created", "flycheap.regenerate_links",
     '{"data": {"id": 1}, "event": "proposal.created"}', "b" * 64, "c" * 64),
    (2, ago(6), "preview", "flycheap.regenerate_links",
     '{"data": {"ok": true}, "event": "preview"}', "a" * 64, "b" * 64),
    (1, ago(7), "execute.watch_only", "flycheap.regenerate_links",
     '{"data": {"tier": 2}, "event": "execute.watch_only"}', "0" * 64, "a" * 64),
]


class PreviewBackend(state.MemoryBackend):
    async def fetch(self, sql: str, *args):
        flat = " ".join(sql.split())
        if "FROM checks c" in flat and "JOIN properties" in flat:
            return [{"property": p, "check_name": c, "last_run_at": la,
                     "next_run_at": nx, "interval_s": iv, "health": h,
                     "detail": d, "observed_at": la}
                    for p, c, la, nx, iv, h, d in CHECK_ROWS]
        if "DISTINCT ON (property)" in flat:
            seen, out = set(), []
            for f in FINDINGS:
                if f[2] in seen:
                    continue
                seen.add(f[2])
                out.append({"property": f[2], "id": f[0], "title": f[6],
                            "severity": f[3], "at": f[10]})
            return out
        if "FROM proposals p" in flat and "pending" in flat:
            return [dict(PROPOSAL)]
        if "FROM findings f" in flat:
            return [self._finding(f) for f in FINDINGS]
        if "FROM actions_log" in flat and "ORDER BY id DESC" in flat:
            return [{"id": i, "at": at, "event": e, "subject": s,
                     "payload": p, "prev_digest": pd, "digest": dg}
                    for i, at, e, s, p, pd, dg in AUDIT]
        if "FROM findings WHERE" in flat or (
                "FROM findings" in flat and "::date" in flat):
            return [self._finding(f) for f in FINDINGS]
        if "GROUP BY 1 ORDER BY 1 DESC" in flat:
            return [{"day": NOW.date(), "findings": 4, "serious": 1}]
        if "FROM reports" in flat:
            return [{"id": 1, "at": ago(280), "kind": "brief",
                     "title": "Morning brief — Tue 22 Sep",
                     "delivered_via": "slack",
                     "excerpt": "Overnight: flycheap.conversion_ratio is failing"}]
        if "FROM observations o JOIN checks c" in flat:
            return [{"at": ago(i * 3), "module": CHECK_ROWS[i % len(CHECK_ROWS)][0],
                     "check_name": CHECK_ROWS[i % len(CHECK_ROWS)][1],
                     "health": CHECK_ROWS[i % len(CHECK_ROWS)][5],
                     "detail": CHECK_ROWS[i % len(CHECK_ROWS)][6],
                     "value": None, "unit": None}
                    for i in range(14)]
        if "FROM actions_log WHERE event LIKE" in flat:
            return [{"id": i, "at": at, "event": e, "subject": s, "payload": p}
                    for i, at, e, s, p, _pd, _dg in AUDIT]
        if "FROM observations o" in flat:
            return [{"at": ago(i * 15), "value": 100 + (i % 7) * 4 - (i % 3) * 9,
                     "health": "unknown" if i in (5, 6) else "ok"}
                    for i in range(24)]
        return []

    @staticmethod
    def _finding(f):
        (fid, key, prop, sev, kind, health, title, detail, value, unit, at,
         proposal_id) = f
        return {"id": fid, "at": at, "check_key": key, "property": prop,
                "severity": sev, "kind": kind, "health": health, "title": title,
                "detail": detail, "value": value, "unit": unit,
                "evidence": json.dumps({"sampled": 6}), "observed_at": at,
                "proposal_id": proposal_id,
                "proposal_status": "pending" if proposal_id else None,
                "proposal_action": "flycheap.regenerate_links" if proposal_id else None}

    async def fetchrow(self, sql: str, *args):
        flat = " ".join(sql.split())
        if "count(*) AS n FROM observations" in flat:
            return {"n": 412}
        if "FROM findings WHERE at >= $1 AND health = 'unknown'" in flat:
            return {"n": 2}
        if "FROM findings WHERE at >= $1 AND health <> 'ok'" in flat:
            return {"n": 3}
        if "FROM actions_log WHERE at >= $1 AND event = 'execute.finish'" in flat:
            return {"n": 0}
        if "count(*) AS n FROM proposals" in flat:
            return {"n": 1}
        if "FROM reports WHERE id" in flat:
            return {"id": 1, "at": ago(280), "kind": "brief",
                    "title": "Morning brief — Tue 22 Sep",
                    "body": "Morning brief — Tuesday 22 September, 07:00Z\n\n"
                            "Overnight\n  04:12 flycheap.conversion_ratio is "
                            "failing — still open\n\nWhere things stand\n"
                            "  flycheap: 412 clicks, 0 bookings\n"
                            "  guardian: not watched — missing env",
                    "sections": "[]", "delivered_via": "slack"}
        if "FROM findings f" in flat:
            wanted = args[0] if args else 1
            match = next((f for f in FINDINGS if f[0] == wanted), FINDINGS[0])
            row = self._finding(match)
            # The permalink template also shows the proposal and its dry run.
            row.update({
                "proposal_reason": PROPOSAL["reason"] if row["proposal_id"] else None,
                "dry_run": PROPOSAL["dry_run"] if row["proposal_id"] else None,
                "dry_run_ok": True if row["proposal_id"] else None,
            })
            return row
        return None

    async def execute(self, sql: str, *args):
        return "OK"


class FakeModule:
    """Enough of the Module protocol for the registry to treat it as loaded."""

    def __init__(self, name: str, title: str, line: str) -> None:
        self.name = name
        self.title = title
        self._line = line
        self.requires_env = ()

    async def setup(self) -> None:
        """Required by the Module protocol. Without it start_modules disables
        this module — correctly — and every tile reads 'not watched'."""

    async def teardown(self) -> None:
        return None

    def checks(self):
        return ()

    def actions(self):
        return ()

    def brief(self) -> str:
        return self._line


def _register_modules() -> None:
    """Mark the modules that have seeded data as loaded.

    Without this every tile reads 'not watched' and no gauge is drawn, so the
    one case worth looking at — a measured module beside an unmeasured one —
    never appears.
    """
    from atlas.kernel.registry import REGISTRY

    for name, title, line in (
        ("flycheap", "flycheap", "412 clicks, 0 bookings"),
        ("guardian", "Guardian", "canary unverified"),
        ("inroots", "InRoots", "18/21 recorded"),
        ("cloudco", "CloudCo", "7 running"),
    ):
        REGISTRY.modules[name] = FakeModule(name, title, line)
    for name in ("calendar", "mail", "gitlab", "instagram", "tiktok",
                 "linkedin", "netbird", "stripe_billing", "exampleco", "fiverr"):
        REGISTRY.disabled[name] = f"missing env: {name.upper()}_TOKEN"


state.set_backend(PreviewBackend())

if __name__ == "__main__":
    import uvicorn

    _register_modules()
    print("preview password: preview")
    uvicorn.run("atlas.api.main:app", host="127.0.0.1", port=8099, log_level="warning")
