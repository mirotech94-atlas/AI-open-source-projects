"""Detection: rules first, baselines second.

Rules catch the obvious (a 500, an expired cert). Baselines catch the slow
bleed that has no error attached to it — the affiliate link that still returns
200 but stopped converting, the bot that is up but stopped posting.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC

from .contracts import Check, Health, Observation, Severity
from .state import baseline

log = logging.getLogger("atlas.detect")


@dataclass(frozen=True, slots=True)
class Finding:
    check: str
    severity: Severity
    title: str
    detail: str
    observation: Observation
    kind: str                # "rule" | "baseline"
    # Set once the finding is persisted, so a proposal and a permalink can both
    # point at the same row.
    id: int | None = None


_UNCONFIGURED = re.compile(
    r"\b(not set|not configured|missing env|is unset|are unset|no [A-Z][A-Z0-9_]+ "
    r"(?:/ [A-Z][A-Z0-9_]+ )*set)\b")


def unconfigured(finding: Finding) -> bool:
    """An UNKNOWN whose own reason is a setting nobody has supplied yet (a
    token, an id). It is still a finding -- UNKNOWN is never OK -- but there
    is nothing to reason about: no action can conjure a credential, and on
    2026-09-27 reasoning over these raised eight "file an issue" cards for
    missing YouTube and messaging-bridge settings. The brief and the
    status board are where they belong."""
    return (finding.observation.health is Health.UNKNOWN
            and bool(_UNCONFIGURED.search(finding.detail or "")))


async def evaluate(key: str, check: Check, obs: Observation,
                   check_id: int) -> Finding | None:
    # An unrunnable check is never healthy. Report it as a finding rather than
    # letting a broken collector read as "everything fine".
    if obs.health is Health.UNKNOWN:
        return Finding(key, Severity.NORMAL, f"{key} could not run",
                       obs.detail or "check failed to execute", obs, "rule")

    if obs.is_actionable:
        sev = (check.severity_on_fail if obs.health is Health.FAILING
               else Severity(max(int(check.severity_on_fail) - 1, 0)))
        return Finding(key, sev, f"{key} is {obs.health}", obs.detail, obs, "rule")

    if check.baseline_sigma is None or obs.value is None:
        return None

    # Bucket in UTC, matching state.baseline. Local-time bucketing skews a
    # DST change into one double-counted hour and one empty one.
    at_utc = obs.at.astimezone(UTC)
    stats = await baseline(check_id, at_utc.hour, (at_utc.weekday() + 1) % 7)
    if stats is None:
        return None
    mu, sigma = stats
    z = (obs.value - mu) / sigma
    # Inclusive at the boundary: a value exactly `baseline_sigma` from the
    # mean has drifted that far, so it fires. The quiet band is the open
    # interval (mu - n*sigma, mu + n*sigma).
    if abs(z) < check.baseline_sigma:
        return None

    direction = "above" if z > 0 else "below"
    return Finding(
        key,
        Severity.HIGH if abs(z) > check.baseline_sigma + 2 else Severity.NORMAL,
        f"{key} is {abs(z):.1f}σ {direction} its baseline",
        f"{obs.value:,.2f}{' ' + obs.unit if obs.unit else ''} vs a typical "
        f"{mu:,.2f} (±{sigma:,.2f}) for this hour of the week",
        obs, "baseline",
    )


async def correlate(findings: list[Finding]) -> dict[str, list[Finding]]:
    """Group findings that started within a few minutes of each other across
    different properties. Three things breaking at 14:02 is one incident."""
    if len(findings) < 2:
        return {}
    buckets: dict[str, list[Finding]] = {}
    for f in findings:
        stamp = f.observation.at.replace(second=0, microsecond=0)
        key = stamp.strftime("%Y-%m-%dT%H:%M")[:-1] + "0"   # 10-minute bucket
        buckets.setdefault(key, []).append(f)
    return {k: v for k, v in buckets.items()
            if len({f.check.split(".")[0] for f in v}) > 1}
