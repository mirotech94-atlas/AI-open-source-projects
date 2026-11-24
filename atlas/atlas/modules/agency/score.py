"""agency.rank — which leads are worth the time.

AGENCY.md 2.3 sends this to the judgment layer eventually: features in,
labels (did the owner pursue it, did it close) out, and within a few dozen
leads it learns what a real pitch looks like. That needs history this
module does not have yet, so the score here is a heuristic — deliberately
simple, deliberately named `heuristic_score` rather than `predict`, so
nobody mistakes it for a trained model.

`features()` is stored on the lead row (`agency_leads.features`) and is
what Task 3 copies into the outreach proposal's evidence — the decision log
reads that, not this file. Nothing here imports `atlas.kernel.judgment`:
swapping the heuristic for a real head later is a `score.py` change, not a
rewrite of prospect.py or audit.py.
"""
from __future__ import annotations

import hashlib
import json
import struct
from typing import Any

from ...kernel.contracts import ActionResult
from . import _store
from .audit import Audit, Finding

# Weights are a starting point, not a fit: a worse website matters most (it
# is the whole pitch), reviews and rating are secondary evidence that the
# business is real and reachable.
_SEVERITY_WEIGHT = 0.6
_REVIEWS_WEIGHT = 0.25
_RATING_WEIGHT = 0.15
# A severity above this is treated the same as "as bad as it gets" — no
# real audit exceeds it by much, and an unbounded term would let one
# adversarial number swamp the other two.
_SEVERITY_CEILING = 13.0
_REVIEWS_CEILING = 100.0


def features(lead: dict[str, Any], audit: Audit) -> dict[str, float]:
    """Never invents a number: a rating or review count the lead does not
    have is recorded as absent (`has_rating`/`has_reviews`), not defaulted
    into a 0 that reads as "one star" or "no reviews yet" to whatever
    reads this dict next."""
    rating = lead.get("rating")
    reviews = lead.get("reviews")
    distance_km = lead.get("distance_km")
    return {
        "audit_severity": float(audit.severity),
        "finding_count": float(len(audit.findings)),
        "no_website": 1.0 if audit.url is None else 0.0,
        "rating": float(rating) if rating is not None else 0.0,
        "has_rating": 1.0 if rating is not None else 0.0,
        "reviews": float(reviews) if reviews is not None else 0.0,
        "has_reviews": 1.0 if reviews is not None else 0.0,
        "distance_km": float(distance_km) if distance_km is not None else 0.0,
    }


def heuristic_score(f: dict[str, float]) -> float:
    """0..1. Monotonic in `audit_severity`: a worse website is always a
    better lead, all else equal — that is the entire thesis in 2.2."""
    severity_score = min(f.get("audit_severity", 0.0) / _SEVERITY_CEILING, 1.0)

    if f.get("has_reviews"):
        reviews_score = min(f.get("reviews", 0.0) / _REVIEWS_CEILING, 1.0)
    else:
        reviews_score = 0.3          # no signal either way; not a penalty

    if f.get("has_rating"):
        rating_score = max(0.0, min(f.get("rating", 0.0) / 5.0, 1.0))
    else:
        rating_score = 0.5

    score = (_SEVERITY_WEIGHT * severity_score
             + _REVIEWS_WEIGHT * reviews_score
             + _RATING_WEIGHT * rating_score)
    return max(0.0, min(1.0, score))


def _as_real(score: float | None) -> float | None:
    """`agency_leads.score` is a REAL (float4): round a double through
    float4 so the value written and the value read back hash the same."""
    if score is None:
        return None
    return struct.unpack("f", struct.pack("f", float(score)))[0]


def scores_sha(scores: dict[int, float | None]) -> str:
    """The first 12 hex chars of a sha256 over `id:score` pairs, sorted by
    id, each score as stored in a REAL column. `agency.rank` records it; its
    read-back recomputes it from the rows."""
    text = ";".join(f"{int(i)}:{_as_real(s)!r}"
                    for i, s in sorted(scores.items(), key=lambda kv: int(kv[0])))
    return hashlib.sha256(text.encode()).hexdigest()[:12]


# --------------------------------------------------------------- the action

async def rank_action() -> ActionResult:
    leads = await _store.list_non_closed_leads()
    # Score and features are computed together and restored together — a
    # rollback that put the old score back next to the new run's features
    # would leave the row internally inconsistent with what `agency.rank`
    # actually looked at.
    previous: dict[str, dict[str, Any]] = {}
    written: dict[int, float] = {}

    for lead in leads:
        lead_id = lead["id"]
        previous[str(lead_id)] = {
            "score": lead.get("score"),
            "features": _store.parse_json(lead.get("features"), {}),
        }

        cached = await _store.recent_audit(lead_id)
        if cached is not None:
            findings = [Finding(**item)
                       for item in _store.parse_json(cached.get("findings"), [])]
            audit = Audit(cached.get("url"), findings,
                          _store.parse_json(cached.get("measured"), {}),
                          int(cached.get("severity") or 0))
        else:
            # No audit yet: neutral rather than assumed-bad or assumed-good.
            audit = Audit(lead.get("website"), [], {}, 0)

        feats = features(lead, audit)
        written[lead_id] = heuristic_score(feats)
        await _store.update_lead_score(lead_id, written[lead_id], feats)

    return ActionResult(
        True, f"recomputed score for {len(leads)} lead(s)",
        evidence={"updated": len(leads), "scores_sha": scores_sha(written)},
        rollback_token=json.dumps(previous),
    )


async def rank_dry() -> ActionResult:
    leads = await _store.list_non_closed_leads()
    return ActionResult(
        True, f"would recompute score and features for {len(leads)} lead(s)")


async def rank_rollback(token: str) -> ActionResult:
    try:
        previous = json.loads(token) if token else {}
    except ValueError:
        return ActionResult(False, "rollback token is not valid JSON")
    for lead_id_str, prior in previous.items():
        await _store.restore_lead_score(
            int(lead_id_str), prior.get("score"), prior.get("features", {}))
    return ActionResult(True, f"restored score and features for {len(previous)} lead(s)")
