#!/usr/bin/env python3
"""Reply-time baseline: p50 / p95 per channel and the target that follows.

    python ops/latency_report.py [--days 7] [--write-doc]

Reads `reply_timings` (WhatsApp and portal, kept apart) and, as a SEPARATE
historical series, `voice_turns.ms` for the portal before timing existed. The
two are never mixed.

target = round(0.28 * baseline p50), the typical reply (the owner, 2026-11-12).
With fewer than MIN_SAMPLES rows on a channel it says UNKNOWN and exits 2: an
estimate is never used (invariants 6 and 7). Read-only: it only runs SELECTs.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import pathlib
import sys
from datetime import UTC, datetime
from typing import Any

MIN_SAMPLES = 100
WINDOW_DAYS = 7
KEEP_FRACTION = 0.28                    # a 72% cut
CHANNELS = ("whatsapp", "portal")
DOC = pathlib.Path(__file__).resolve().parent.parent / "docs" / "latency-baseline.md"

QUERY = ("SELECT total_ms FROM reply_timings WHERE channel = $1 AND ok "
         "AND at > now() - make_interval(days => $2) ORDER BY at")
HISTORY_QUERY = ("SELECT ms FROM voice_turns WHERE ms IS NOT NULL "
                 "AND at > now() - make_interval(days => $1) ORDER BY at")


def percentile(values: list[int], pct: float) -> int:
    """Nearest-rank percentile of a non-empty list."""
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return int(ordered[rank - 1])


def _channel(values: list[int]) -> dict[str, Any]:
    n = len(values)
    if n < MIN_SAMPLES:
        return {"n": n, "p50": None, "p95": None, "target_p50": None,
                "target_p95": None, "enough": False}
    p50, p95 = percentile(values, 50), percentile(values, 95)
    return {"n": n, "p50": p50, "p95": p95,
            "target_p50": round(KEEP_FRACTION * p50),
            "target_p95": round(KEEP_FRACTION * p95), "enough": True}


def build(series: dict[str, list[int]], voice_history: list[int],
          window_days: int = WINDOW_DAYS) -> dict[str, Any]:
    channels = {c: _channel(list(series.get(c, []))) for c in CHANNELS}
    history = ({"n": len(voice_history),
                "p50": percentile(voice_history, 50) if voice_history else None,
                "p95": percentile(voice_history, 95) if voice_history else None})
    ok = all(ch["enough"] for ch in channels.values())
    return {"status": "ok" if ok else "unknown", "window_days": window_days,
            "min_samples": MIN_SAMPLES, "channels": channels,
            "voice_history": history,
            "read_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")}


def exit_code(report: dict[str, Any]) -> int:
    return 0 if report["status"] == "ok" else 2


def render(report: dict[str, Any]) -> str:
    lines = [(f"Reply-time baseline, last {report['window_days']} days "
              f"(read {report['read_at']})")]
    for name in CHANNELS:
        ch = report["channels"][name]
        if ch["enough"]:
            lines.append(f"  {name:9} n={ch['n']:<6} p50={ch['p50']} ms  "
                         f"p95={ch['p95']} ms  target p50 <= {ch['target_p50']} ms "
                         f"(p95 <= {ch['target_p95']} ms)")
        else:
            lines.append(f"  {name:9} n={ch['n']:<6} UNKNOWN: needs "
                         f"{report['min_samples']} replies, has {ch['n']}")
    hist = report["voice_history"]
    if hist["n"]:
        lines.append(f"  portal, historical voice_turns.ms (separate series): "
                     f"n={hist['n']} p50={hist['p50']} ms p95={hist['p95']} ms")
    if report["status"] != "ok":
        lines.append("Target: UNKNOWN until every channel has enough replies.")
    return "\n".join(lines)


def doc_text(report: dict[str, Any]) -> str:
    return ("# Reply-time baseline\n\n"
            "Numbers come only from a real run of `ops/latency_report.py` on the live "
            "ATLAS. Target = round(0.28 x baseline p50) per channel (the typical "
            "reply, the owner 2026-11-12).\n\n```\n" + render(report) + "\n```\n\n"
            f"Query: `{QUERY}` and, for history, `{HISTORY_QUERY}`.\n")


async def _read(days: int) -> tuple[dict[str, list[int]], list[int]]:
    from atlas.kernel.state import fetch_all
    series = {c: [int(r["total_ms"]) for r in await fetch_all(QUERY, c, days)]
              for c in CHANNELS}
    history = [int(r["ms"]) for r in await fetch_all(HISTORY_QUERY, days)]
    return series, history


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=WINDOW_DAYS)
    ap.add_argument("--write-doc", action="store_true",
                    help="write docs/latency-baseline.md from this run")
    args = ap.parse_args(argv)
    try:
        series, history = asyncio.run(_read(args.days))
    except Exception as exc:                                # noqa: BLE001
        print(f"Reply-time baseline: UNKNOWN (could not read the database: "
              f"{type(exc).__name__})")
        return 2
    report = build(series, history, args.days)
    print(render(report))
    if args.write_doc:
        DOC.write_text(doc_text(report))
    return exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
