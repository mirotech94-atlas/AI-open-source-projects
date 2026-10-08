"""FlyCheap — the fare tracker.

**Every endpoint the previous version of this module called was invented.**
`/api/admin/prices/freshness`, `/api/admin/links/sample` and
`/api/admin/metrics/signups` do not exist in the repo; the routes are
`app/api/**/route.ts` and enumerating them from the filesystem shows nothing
of that shape. The module also declared `requires_env = ("FLYCHEAP_API_TOKEN",)`,
naming a variable the product has no concept of — so it was disabled by
construction, guarding endpoints that would have 404'd anyway.

What FlyCheap actually has is better than any of that: **`/api/health` is a
public, unauthenticated endpoint built for exactly this job.** Its own comment
says "used by the /status page and any external uptime monitor", and it
already carries every fact the invented checks were reaching for:

    database       row count, and the only condition that returns 503
    last_poll      minutes since the last successful fare poll
    providers      how many fare sources are configured
    last_alert     when an alert last went out
    metrics        snapshots, alerts, users, active routes
    delivery_feed  Resend webhook events against sends

Two properties of that endpoint shape this module:

* **It returns 200 even when degraded**, on purpose, so a monitor branches on
  the `ok` field rather than the status code. Treating 200 as healthy — which
  is what a plain probe does — reads a broken scraper as a working site.
* **503 means the database is unreachable**, and nothing else does.

So there are no credentials here and nothing to configure. The checks work
the moment ATLAS can reach the internet.

There are also no actions. The two that existed called endpoints that do not
exist, and one of them regenerated affiliate markers — revenue, which goes to
Miro rather than to an agent.
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from datetime import timedelta

import httpx

from ...kernel.contracts import (
    Check,
    Health,
    Observation,
    Severity,
    Signal,
)
from .._http import expect_keys, get_json

PUBLIC = os.getenv("FLYCHEAP_PUBLIC_URL", "https://flycheap.live")
CLIENT = os.getenv("FLYCHEAP_CLIENT_URL", "https://my.flycheap.live")
ADMIN = os.getenv("FLYCHEAP_ADMIN_URL", "https://admin.flycheap.live")

HEALTH = "/api/health"

# The subsystems inside `checks`, and how much each one matters. A fare
# tracker whose poller stopped is broken even though every page still renders,
# which is the whole reason this module reads the payload instead of the
# status code.
SUBSYSTEM: dict[str, tuple[Severity, str]] = {
    "database": (Severity.CRITICAL, "the database behind every surface"),
    "last_poll": (Severity.HIGH,
                  "fare polling — a scraper that stopped writing looks "
                  "exactly like a quiet market"),
    "providers": (Severity.HIGH, "configured fare sources"),
    "delivery_feed": (Severity.NORMAL,
                      "the Resend webhook feed. Sends succeeding while the "
                      "feed records nothing is a dead feed wearing a "
                      "0%-failure rate"),
    "last_alert": (Severity.INFO, "when an alert last went out"),
    "metrics": (Severity.INFO, "snapshot and route counts"),
}


async def _probe(url: str, name: str, slow_ms: int = 3000) -> Observation:
    """Reachability and latency.

    Two samples, and the better one is reported. A single GET timed at
    3.7s flagged guarder.io and status.example.org as DEGRADED; measured from the
    same host a minute later both answered in under 0.3s. One slow sample
    is a slow moment -- a TLS handshake, a cold worker -- not a slow site,
    and a check that cannot tell those apart trains him to ignore it.
    """
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get(url)
            first = r.elapsed.total_seconds() * 1000
            if first > slow_ms and r.status_code < 400:
                # Not straight away: right after a restart every check fires
                # at once and a second sample taken in the same second
                # measures that contention, not the site. flycheap.live
                # read 4.2s twice in a row that way and 0.29s alone.
                await asyncio.sleep(3)
                r = await c.get(url)
                ms = min(first, r.elapsed.total_seconds() * 1000)
            else:
                ms = first
        if r.status_code >= 500:
            return Observation(name, Health.FAILING, ms, "ms", f"HTTP {r.status_code}")
        if r.status_code >= 400:
            return Observation(name, Health.DEGRADED, ms, "ms", f"HTTP {r.status_code}")
        if ms > slow_ms:
            return Observation(name, Health.DEGRADED, ms, "ms",
                               f"{url} was slow: {ms:.0f}ms")
        return Observation(name, Health.OK, ms, "ms", f"HTTP {r.status_code}")
    except Exception as exc:                            # noqa: BLE001
        return Observation(name, Health.FAILING, None, None, f"unreachable: {exc}")


async def _health_payload() -> tuple[dict | None, str | None]:
    """Fetch /api/health once, for whichever check asked.

    On a 503 `get_json` reports a transport failure, which is correct: 503 is
    the one status FlyCheap uses to mean the database is unreachable, and at
    that point nothing else in the payload can be believed either.
    """
    data, reason = await get_json(HEALTH, base_url=PUBLIC)
    if reason:
        return None, reason
    bad = expect_keys(data, "ok", "checks", where=HEALTH)
    if bad:
        return None, bad
    return data, None


async def check_api() -> Observation:
    """The `ok` flag, which is the endpoint's own verdict on itself.

    Not a status-code probe. FlyCheap answers 200 while degraded by design so
    that monitors read the body; a probe that stops at the status code reports
    a stalled poller and a dead delivery feed as a healthy site.
    """
    data, reason = await _health_payload()
    if reason:
        return Observation("api", Health.UNKNOWN, detail=reason)

    checks = data.get("checks")
    if not isinstance(checks, dict):
        return Observation("api", Health.UNKNOWN,
                           detail=f"{HEALTH} returned no checks object")

    # Only an entry that carries its own `ok` is a reading. An empty object,
    # or one whose entries are all malformed, is a payload ATLAS could not
    # read -- and it used to say "all 0 subsystems healthy" (RA-E1).
    readable = {name: c for name, c in checks.items()
                if isinstance(c, dict) and "ok" in c}
    if not readable:
        return Observation(
            "api", Health.UNKNOWN,
            detail=f"{HEALTH} returned {len(checks)} subsystem entr"
                   f"{'y' if len(checks) == 1 else 'ies'} and none carried an "
                   f"ok flag, so nothing was measured")
    failed = sorted(name for name, c in readable.items() if not c.get("ok"))
    version = data.get("version", "?")
    if not failed:
        skipped = sorted(set(checks) - set(readable))
        return Observation(
            "api", Health.OK, float(len(readable)), "subsystems",
            f"all {len(readable)} subsystems healthy (v{version})"
            + (f"; {len(skipped)} unreadable entr"
               f"{'y' if len(skipped) == 1 else 'ies'} not counted"
               if skipped else ""),
            evidence={"version": version, "checks": sorted(readable),
                      "unreadable": skipped})

    worst = max((SUBSYSTEM.get(n, (Severity.NORMAL, ""))[0] for n in failed),
                default=Severity.NORMAL)
    return Observation(
        "api",
        Health.FAILING if worst >= Severity.HIGH else Health.DEGRADED,
        float(len(failed)), "subsystems",
        f"{len(failed)} of {len(checks)} subsystems unhealthy: "
        + "; ".join(f"{n} ({checks[n].get('detail', 'no detail')})"
                    for n in failed),
        evidence={"failed": failed, "version": version})


def _subsystem_check(key: str):
    """One Check per subsystem, so each gets its own history and severity.

    Folding them into a single reading would mean a stalled poller and a
    slightly stale delivery feed produce the same observation, and the
    baseline would track the count rather than the thing.
    """
    async def _run() -> Observation:
        data, reason = await _health_payload()
        if reason:
            return Observation(key, Health.UNKNOWN, detail=reason)
        entry = (data.get("checks") or {}).get(key)
        if not isinstance(entry, dict) or "ok" not in entry:
            # The endpoint omits a subsystem when its own read threw. Absent
            # is not healthy, and it is not failing either.
            return Observation(
                key, Health.UNKNOWN,
                detail=f"{HEALTH} did not report {key}; it is omitted when "
                       f"the check itself raises")
        detail = str(entry.get("detail", "")).strip()
        _severity, what = SUBSYSTEM.get(key, (Severity.NORMAL, key))
        return Observation(
            key, Health.OK if entry.get("ok") else Health.FAILING,
            None, None,
            f"{what}: {detail or ('ok' if entry.get('ok') else 'not ok')}",
            evidence={"detail": detail})
    return _run


# ------------------------------------------------------------ source coverage
#
# RA-E3. "Is FlyCheap seeing fares?" is four questions per fare source, and
# they have different answers: is it configured (a key is present), enabled
# (FlyCheap is told to use it), reachable (its API answers) and fresh (it has
# delivered a usable observation recently). A disabled source is a coverage
# CHOICE -- not a success, and not an outage. One source succeeding must not
# stand in for the others.
#
# What the public /api/health actually exposes, read from FlyCheap's own
# route (`src/app/api/health/route.ts`): `checks.providers.detail` is
# "N/M configured (id, id, ...)" and `checks.last_poll.detail` is "K min ago"
# -- the newest successful call across ALL providers. Enabled, reachable and
# per-source freshness are not in it. So each of those reads None ("not
# known"), with the reason, and is never filled in by inference -- except the
# one inference that is exact: if the newest success across every source is
# older than the threshold, every source's own last success is older still.

STALE_AFTER_MIN = float(os.getenv("FLYCHEAP_SOURCE_STALE_MIN", "120"))
NOT_EXPOSED = "not exposed by FlyCheap's /api/health"
COVERAGE_SIGNAL = "FlyCheap: fare source coverage needs a look"

_PROVIDERS_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s+configured\s*(?:\((.*)\))?")
_MINUTES_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*min\b")


@dataclass(frozen=True)
class SourceStatus:
    """One fare source, one field per dimension. None means not known, and
    `why` says why for every None."""

    source: str
    configured: bool | None
    enabled: bool | None
    reachable: bool | None
    fresh: bool | None
    age_min: float | None = None
    why: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Coverage:
    health: Health
    detail: str
    sources: tuple[SourceStatus, ...]
    stale: tuple[str, ...]           # configured, enabled-or-unknown, not fresh
    unreachable: tuple[str, ...]
    off_by_choice: tuple[str, ...]   # configured and deliberately disabled
    not_configured: int
    next_step: str | None

    def evidence(self) -> dict:
        return {
            "sources": [{"source": x.source, "configured": x.configured,
                         "enabled": x.enabled, "reachable": x.reachable,
                         "fresh": x.fresh, "age_min": x.age_min, "why": x.why}
                        for x in self.sources],
            "stale": list(self.stale), "unreachable": list(self.unreachable),
            "off_by_choice": list(self.off_by_choice),
            "not_configured": self.not_configured,
            "next_step": self.next_step,
        }


def parse_providers(detail: str) -> tuple[list[str], int] | None:
    """`"4/6 configured (kiwi, duffel, ...)"` -> (configured ids, total)."""
    m = _PROVIDERS_RE.match(detail or "")
    if not m:
        return None
    got, total = int(m.group(1)), int(m.group(2))
    ids = [x.strip() for x in (m.group(3) or "").split(",") if x.strip()]
    if len(ids) != got or got > total:
        return None
    return ids, total


def poll_age_min(entry: object) -> float | None:
    """Minutes since the newest successful poll, from `last_poll.detail`."""
    if not isinstance(entry, dict):
        return None
    m = _MINUTES_RE.match(str(entry.get("detail", "")))
    return float(m.group(1)) if m else None


def sources_from_health(data: dict) -> tuple[list[SourceStatus], int] | str:
    """Per-source records from the real /api/health shape, or why not."""
    checks = data.get("checks") if isinstance(data, dict) else None
    entry = checks.get("providers") if isinstance(checks, dict) else None
    if not isinstance(entry, dict):
        return f"{HEALTH} did not report providers"
    parsed = parse_providers(str(entry.get("detail", "")))
    if parsed is None:
        return f"{HEALTH} providers detail was not 'N/M configured (ids)'"
    ids, total = parsed
    age = poll_age_min(checks.get("last_poll"))
    out = []
    for sid in ids:
        why = {"enabled": NOT_EXPOSED, "reachable": NOT_EXPOSED}
        fresh: bool | None = None
        if age is None:
            why["fresh"] = f"{HEALTH} gave no readable last_poll age"
        elif age > STALE_AFTER_MIN:
            # Exact, not a guess: nothing has succeeded for `age` minutes,
            # so neither has this source.
            fresh = False
        else:
            why["fresh"] = (f"{NOT_EXPOSED} per source; the newest success "
                            f"across all sources was {age:.0f} min ago")
        out.append(SourceStatus(sid, True, None, None, fresh,
                                age if fresh is False else None, why))
    return out, total - len(ids)


def assess_coverage(sources: list[SourceStatus], not_configured: int = 0,
                    ) -> Coverage:
    """The coverage verdict. Pure: every fact it uses is in `sources`.

    * configured and disabled: a choice, listed, never counted either way;
    * configured, enabled (or not known to be off), and stale or
      unreachable: narrowed coverage -- DEGRADED, FAILING when none of the
      usable sources is known fresh and reachable;
    * any usable source whose freshness or reachability is not known keeps
      the whole reading UNKNOWN, because one healthy source says nothing
      about the rest;
    * not configured: counted and named in the detail, not a failure.
    """
    configured = [x for x in sources if x.configured]
    off = tuple(x.source for x in configured if x.enabled is False)
    usable = [x for x in configured if x.enabled is not False]
    stale = tuple(x.source for x in usable if x.fresh is False)
    unreachable = tuple(x.source for x in usable if x.reachable is False)
    bad = set(stale) | set(unreachable)
    good = [x for x in usable if x.fresh is True and x.reachable is True]
    unknown = [x for x in usable if x.source not in bad
               and (x.fresh is None or x.reachable is None)]

    parts = [f"{len(configured)} fare source(s) configured"]
    if not_configured:
        parts.append(f"{not_configured} not configured")
    if off:
        parts.append(f"off by choice: {', '.join(off)}")
    if stale:
        parts.append(f"stale: {', '.join(stale)}")
    if unreachable:
        parts.append(f"unreachable: {', '.join(unreachable)}")
    if good:
        parts.append(f"fresh: {', '.join(x.source for x in good)}")
    if unknown:
        reasons = sorted({r for x in unknown for k, r in x.why.items()
                          if k in ("fresh", "reachable")})
        parts.append(f"not known for {', '.join(x.source for x in unknown)} "
                     f"({'; '.join(reasons) or 'no reason recorded'})")

    next_step = None
    if bad:
        next_step = (f"check {', '.join(sorted(bad))} in FlyCheap's admin "
                     f"console (key, quota, and whether the provider still "
                     f"answers); ATLAS has no action for this and will not "
                     f"change FlyCheap's source settings")

    if not usable:
        health = Health.UNKNOWN if not configured else Health.DEGRADED
        if not configured:
            parts.append("nothing to measure")
        else:
            parts.append("every configured source is off by choice")
    elif bad:
        health = Health.FAILING if not good and not unknown else Health.DEGRADED
    elif unknown:
        health = Health.UNKNOWN
    else:
        health = Health.OK
    return Coverage(health, "; ".join(parts), tuple(sources), stale,
                    unreachable, off, not_configured, next_step)


async def _coverage() -> Coverage | str:
    data, reason = await _health_payload()
    if reason:
        return reason
    got = sources_from_health(data)
    if isinstance(got, str):
        return got
    sources, not_configured = got
    return assess_coverage(sources, not_configured)


async def check_coverage() -> Observation:
    """Per-source coverage, each dimension separately (RA-E3)."""
    cov = await _coverage()
    if isinstance(cov, str):
        return Observation("coverage", Health.UNKNOWN, detail=cov)
    usable = [x for x in cov.sources if x.configured and x.enabled is not False]
    return Observation("coverage", cov.health, float(len(usable)), "sources",
                       cov.detail, evidence=cov.evidence())


async def check_cert() -> Observation:
    from ...tools.builtin import cert_check
    result = await cert_check("flycheap.live")
    if "error" in result:
        return Observation("cert", Health.UNKNOWN, detail=result["error"])
    days = result["days_left"]
    return Observation(
        "cert",
        Health.FAILING if days < 7 else Health.DEGRADED if days < 21 else Health.OK,
        float(days), "days", f"certificate expires in {days} days")


class FlycheapModule:
    name = "flycheap"
    title = "FlyCheap"
    # Nothing. /api/health is public by design, and the product has no
    # service-token concept: /api/v1/* authenticates a mobile *user* via
    # apiAuthBearer, and /api/admin/* is a session cookie plus ADMIN_EMAILS.
    # Neither is a credential ATLAS should hold.
    requires_env = ()
    # Not required to load, but these unlock checks that are
    # UNKNOWN without them. `atlas.cli connect flycheap` says which.
    # Where: nothing: /api/health is public
    optional_env = ()

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        out = [
            Check("api", check_api, timedelta(minutes=5), Severity.HIGH,
                  "FlyCheap's own health verdict, read from the body",
                  baseline_sigma=None),
            Check("public", lambda: _probe(PUBLIC, "public"),
                  timedelta(minutes=5), Severity.HIGH,
                  "the marketing site responds"),
            Check("client_portal", lambda: _probe(CLIENT, "client_portal"),
                  timedelta(minutes=5), Severity.HIGH,
                  "my.flycheap.live responds"),
            Check("admin_portal", lambda: _probe(ADMIN, "admin_portal"),
                  timedelta(minutes=15), Severity.NORMAL,
                  "admin.flycheap.live responds"),
            Check("cert", check_cert, timedelta(hours=12), Severity.HIGH,
                  "the TLS certificate is not about to expire",
                  baseline_sigma=None),
            Check("coverage", check_coverage, timedelta(minutes=30),
                  Severity.NORMAL,
                  "each fare source: configured, enabled, reachable, fresh",
                  baseline_sigma=None),
        ]
        out += [
            Check(key, _subsystem_check(key), timedelta(minutes=10), severity,
                  what, baseline_sigma=None)
            for key, (severity, what) in SUBSYSTEM.items()
        ]
        return tuple(out)

    def actions(self):
        # None. The two that used to be here called endpoints that do not
        # exist (`/api/admin/links/snapshot`, a scraper rerun), and one of
        # them regenerated affiliate markers -- revenue, which is Miro's.
        return ()

    async def signals(self):
        """At most one tracked next step, through the note path.

        A stale or unreachable source gets ONE signal with a fixed title
        (the scheduler dedups on title for 24 h, so a count or an age in the
        title would re-notify every cycle) and no `suggests`: there is no
        FlyCheap action, and nothing here reaches the executor.
        """
        data, reason = await _health_payload()
        if reason:
            return ()
        got = sources_from_health(data)
        cov = assess_coverage(*got) if not isinstance(got, str) else None
        if cov is not None and cov.next_step:
            return (Signal(
                title=COVERAGE_SIGNAL,
                body=f"{cov.detail}.\nNext step: {cov.next_step}.",
                severity=Severity.NORMAL),)
        # The count alone is enough for this one, even when the id list does
        # not match it and no per-source record could be built.
        entry = (data.get("checks") or {}).get("providers")
        m = _PROVIDERS_RE.match(str(entry.get("detail", ""))
                                if isinstance(entry, dict) else "")
        if m and int(m.group(1)) < int(m.group(2)):
            head = f"{m.group(1)}/{m.group(2)}"
            missing = int(m.group(2)) - int(m.group(1))
            # Said as what it is. "Not configured" is not "switched off":
            # FlyCheap tracks configured and enabled separately, and
            # /api/health only reports the first.
            return (Signal(
                title=f"FlyCheap: {head} fare sources configured",
                body=f"{entry.get('detail')}. The other {missing} are not "
                     f"configured: a coverage choice, not an outage, so "
                     f"nothing will alert about it, and coverage is narrower "
                     f"than the source list suggests.",
                severity=Severity.INFO),)
        return ()

    def brief(self) -> str:
        return ("FlyCheap: site and portal reachability, fare polling, "
                "per-source coverage, delivery feed, certificate")


MODULE = FlycheapModule()
