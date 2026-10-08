"""FlyCheap — the fare tracker.

One endpoint, one rule: `/api/health` is public, unauthenticated, and already
carries every fact worth reporting. Everything here exists to stop that rule
being quietly forgotten under pressure to "just check the admin API".
"""
from __future__ import annotations

from ..kernel.skills import Skill, register

register(Skill(
    name="Reading FlyCheap health",
    triggers=("flycheap", "fare", "fares", "affiliate link", "provider feed",
              "delivery feed", "resend"),
    agents=("flycheap",),
    priority=85,
    body="""
`/api/health` is the only source of truth. It is public by design and it
answers 200 even when degraded, on purpose, so a monitor branches on the `ok`
field in the body — never on the status code. A 200 with `ok: false` is a
finding; a 503 means specifically that the database is unreachable and
nothing else.

There is no admin API to fall back on for a number this doesn't cover.
`/api/admin/prices/freshness`, `/api/admin/links/sample` and
`/api/admin/metrics/signups` do not exist in the product — a previous version
of this module invented them, they would 404, and a check that assumes they
exist is a check that lies about being disabled rather than reporting
UNKNOWN. If a number is not in the health payload, say it is not measured
rather than reaching for a route that was never real.

There are no actions to propose here. Regenerating affiliate links is
revenue-affecting and stays something Miro does by hand, not something an
agent runs even behind approval.
"""))
