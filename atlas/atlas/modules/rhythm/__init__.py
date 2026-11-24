"""rhythm — notices early, late, and what he told you about it.

Watches: `atlas.kernel.rhythm.usual_window()`, the rolling picture of which
hours, on each day of the week, the owner is usually active (built from voice
turns, browser events and his own agent runs — see that module's docstring
for exactly what counts and why calendar events are deliberately not part of
it). The `pattern` check reports whether *this* hour matches. Below four
weeks of history it reports `Health.OK` with no judgement made rather than
`Health.UNKNOWN`: the check ran and measured exactly what there is to
measure so far, it just doesn't have enough yet to say usual/early/late.
`Health.UNKNOWN` is reserved for the check actually failing to run — the
tables themselves being unreachable.

Also watches: `atlas.kernel.rhythm.presence()`'s notion of "home" (Task 51 —
a listed peer's `connection_ip` inside `HOME_IPS`, not mere mesh membership).
A residential ISP can renumber a WAN address without telling anyone, and
when that happens every listed peer looks "away" until `HOME_IPS` is
updated by hand — this module never rewrites it itself (no runtime override,
in the spirit of invariant 3). The `home_ip_change` check tracks, per listed
peer, how long it has sat continuously on ONE address outside `HOME_IPS`
(state kept across restarts in the check's own evidence, the same way
`netbird.check_new_peers` remembers what it saw last time — no schema
change). Once that reaches 12 hours, `signals()` raises a single INFO
notice naming the address and suggesting he update `HOME_IPS`; the same
address is not raised again for 7 days, and an address already inside
`HOME_IPS` is never raised at all. This is a notice, not a decision: nothing
here concludes his home moved, and presence keeps reading "not home" the
whole time regardless.

May do: `rhythm.note_circumstance` — record something the owner said about his
schedule (exam week, travel, feeling sick). Tier AUTO: it only ever writes
ATLAS's own bookkeeping (a `rhythm_notes` row and a memory), never anything
an outside party would see, and it is fully reversible.

Assumes: nothing external. Everything this module reads is already in
Postgres by the time it looks — the actual measuring (what counts as
"active", the 28-day recompute) lives in `atlas.kernel.rhythm` because it is
infrastructure other kernel code (the voice endpoint, the brief) also needs
directly, not something scoped to one business. This file is the thin
`Module` wrapper the registry discovers.
"""
from __future__ import annotations

import os
import re
from datetime import UTC, datetime, timedelta

from ...kernel import rhythm
from ...kernel.contracts import (
    Action,
    Check,
    Health,
    Observation,
    Severity,
    Signal,
    Tier,
)
from ...kernel.state import fetch_one, last_evidence

# The `property` this module registers its checks and signals under --
# shared by `last_evidence`'s lookups and `_home_ip_change_signals`'s own
# dedup query, rather than each repeating the literal "rhythm" and risking
# one silently drifting from `RhythmModule.name` on a rename.
PROPERTY = "rhythm"


async def check_pattern() -> Observation:
    """Hourly. `value` is 1 when this hour is not the owner's usual one for this
    day of the week, 0 when it is, `None` while there is nothing to judge
    against yet.

    `Health.UNKNOWN` means a check that could not run — this one runs every
    time, it just may not have enough history to have an opinion. Reporting
    UNKNOWN for "not enough data" would, through `kernel.detect.evaluate`
    (which stamps every UNKNOWN finding at a hardcoded `Severity.NORMAL`
    regardless of this check's own INFO severity), call the reasoner's
    `diagnose()` every hour for the entire learning period — an LLM call to
    "fix" a check that isn't broken. `Health.OK` with a detail that plainly
    says no judgement was made keeps this check both honest (it never claims
    usual/early/late without grounds) and quiet (nothing to page or reason
    about) while it learns. `Health.UNKNOWN` is kept for the one thing that
    actually means "could not run": the source tables being unreachable.
    """
    now = datetime.now().astimezone()
    try:
        window = await rhythm.usual_window(now.weekday())
    except Exception as exc:                            # noqa: BLE001
        return Observation("pattern", Health.UNKNOWN,
                           detail=f"could not read rhythm_hours: {exc}")

    if window is None:
        try:
            days = await rhythm.days_observed()
        except Exception as exc:                        # noqa: BLE001
            return Observation("pattern", Health.UNKNOWN,
                               detail=f"could not read rhythm history: {exc}")
        return Observation(
            "pattern", Health.OK, None, "days",
            f"learning his hours: {days} of {rhythm.WINDOW_DAYS} days; "
            f"no judgement yet")

    state = rhythm.classify_hour(now.hour, window)
    value = 0.0 if state == "usual" else 1.0
    start, end = window
    return Observation(
        "pattern",
        Health.OK if state == "usual" else Health.DEGRADED,
        value, "deviation",
        f"{state} for {now:%A %H:%M} (usual {start:02d}:00–{end:02d}:00)")


# How often `check_home_ip_change` actually runs, shared with its own
# `Check` registration below so the staleness bound beneath it is never out
# of step with the real interval.
HOME_IP_CHECK_INTERVAL = timedelta(minutes=30)
# A prior reading counts as "still watching continuously" only inside this
# bound. Wider than the interval on purpose (a slow cycle or one missed run
# must not look like a gap), but still a bound: an outage of unknown length
# must not be read as an unbroken sighting on the other side of it.
HOME_IP_STALE_AFTER = HOME_IP_CHECK_INTERVAL * 2


async def check_home_ip_change() -> Observation:
    """Passive. Tracks, per listed peer (`PRESENCE_PEERS`), how long it has
    been connected continuously from ONE address outside `HOME_IPS` —
    never a peer that is home, offline, or unknown.

    State lives in this check's own evidence, read back through
    `kernel.state.last_evidence`, not in memory: the same shape
    `netbird.check_new_peers` uses so a restart does not forget how long a
    peer has already been sitting on an address. Resets the moment a peer's
    address changes again, goes home, or disconnects.

    Every run's evidence carries `observed_at` alongside `away`, and a prior
    `since` is trusted only when that timestamp is within
    `HOME_IP_STALE_AFTER` — otherwise a gap (the mesh unreadable for a
    while, a missed cycle) would let an old sighting masquerade as
    continuous watching straight through it.

    Two distinct "nothing to report" cases, on invariant 7's insistence that
    a check which could not run is never reported the same as one that ran
    and found nothing:
      * `PRESENCE_PEERS` genuinely unset — nothing is configured to watch,
        which is not this check failing to do its job. `Health.OK`, with a
        fresh "nothing away" reading (there is nothing to have gone stale
        about, so any previously tracked peer is cleared rather than
        carried forward).
      * `PRESENCE_PEERS` names at least one peer, but `rhythm.presence()`
        could not determine any of their statuses this run — the mesh was
        unreachable, or every named peer is one the mesh has never heard
        of. This check did not run to completion: `Health.UNKNOWN`, naming
        the read failure, the same distinction `check_pattern` above and
        `netbird.check_peers`/`check_new_peers` already make for the
        identical shape of dependency. No evidence is written for this
        case — `last_evidence` already skips `UNKNOWN` and evidence-less
        rows, so the next run still finds the last real reading and its
        true age on its own, rather than this one needing to echo it
        forward by hand.

    Always `Health.OK` once it has something to say either way: sitting on
    a foreign address for a long time is not this check failing, it is
    exactly the case `_home_ip_change_signals()` below watches for. A
    `Check` is a read-only probe, so nothing here ever notifies the owner
    directly — it only leaves the evidence for that function (and the next
    run of this one) to read.
    """
    if not os.getenv("PRESENCE_PEERS", "").strip():
        return Observation(
            "home_ip_change", Health.OK,
            detail="no listed peers to track (PRESENCE_PEERS is not set)",
            evidence={"away": {}, "observed_at": datetime.now(UTC).isoformat()})

    seen = await rhythm.presence(timeout=rhythm.CHECK_PRESENCE_TIMEOUT_S)
    peers = seen.get("peers") or {}
    if not peers:
        return Observation(
            "home_ip_change", Health.UNKNOWN,
            detail=seen.get("reason") or
                  "could not read any listed peer's status")

    now = datetime.now(UTC)
    prior = await last_evidence(PROPERTY, "home_ip_change")
    prior_away = prior.get("away", {}) if isinstance(prior, dict) else {}
    prior_observed_raw = (prior.get("observed_at")
                          if isinstance(prior, dict) else None)

    trust_prior = False
    if prior_observed_raw:
        try:
            trust_prior = (now - datetime.fromisoformat(prior_observed_raw)
                          <= HOME_IP_STALE_AFTER)
        except ValueError:
            trust_prior = False

    away_state: dict[str, dict] = {}
    for label, info in peers.items():
        # Only a peer confirmed connected from an address outside HOME_IPS
        # counts -- home (True), unknown (None) and no-address peers are
        # never guessed into "away since forever".
        ip = info.get("connection_ip")
        if info.get("home") is not False or not ip:
            continue
        prior_entry = prior_away.get(label) if trust_prior else None
        since = now
        if isinstance(prior_entry, dict) and prior_entry.get("ip") == ip:
            try:
                since = datetime.fromisoformat(prior_entry["since"])
            except ValueError:
                since = now
        away_state[label] = {"ip": ip, "since": since.isoformat()}

    evidence = {"away": away_state, "observed_at": now.isoformat()}
    if not away_state:
        return Observation(
            "home_ip_change", Health.OK, 0.0, "peers",
            "no listed peer connected from a persistent non-home address",
            evidence=evidence)

    longest_hours = max(
        (now - datetime.fromisoformat(v["since"])).total_seconds() / 3600
        for v in away_state.values())
    return Observation(
        "home_ip_change", Health.OK, round(longest_hours, 1), "hours",
        f"{len(away_state)} listed peer(s) connected from a persistent "
        f"non-home address, longest {longest_hours:.1f}h",
        evidence=evidence)


# 12h: long enough that a normal commute, a coffee-shop tether or a mobile
# hotspot for an afternoon never trips it -- this is for "my ISP handed me a
# new address", which is sticky, not for "I left the house".
HOME_IP_CHANGE_AFTER = timedelta(hours=12)
# Re-raised, per address, at most this often -- checked against the signals
# table itself (what was actually said and when), not a second piece of
# state duplicating it.
HOME_IP_RENOTIFY_DAYS = 7


async def _home_ip_change_signals() -> tuple[Signal, ...]:
    """One INFO notice per address a listed peer has sat on, continuously,
    for at least `HOME_IP_CHANGE_AFTER` outside `HOME_IPS`.

    Requires the tracker's own last reading to be within
    `HOME_IP_STALE_AFTER` of now: a `since` computed from a check that has
    not actually run in a while is not something to act on, whatever it
    says. `check_home_ip_change` applies the same bound on the way in, but
    this is asked independently, since `signals()` and the scheduler's own
    check loop run on separate schedules.

    A notice only: this never rewrites `HOME_IPS`, and `rhythm.presence()`
    keeps reading "not home" for as long as the address stays unlisted,
    whatever this says. Re-raised at most once every
    `HOME_IP_RENOTIFY_DAYS` for the same address; an address already inside
    `HOME_IPS` never reaches here at all, because `check_home_ip_change`
    only ever tracks a peer whose per-peer `home` came back False.
    """
    prior = await last_evidence(PROPERTY, "home_ip_change")
    away = prior.get("away", {}) if isinstance(prior, dict) else {}
    observed_raw = prior.get("observed_at") if isinstance(prior, dict) else None
    if not away or not observed_raw:
        return ()
    try:
        observed_at = datetime.fromisoformat(observed_raw)
    except ValueError:
        return ()

    now = datetime.now(UTC)
    if now - observed_at > HOME_IP_STALE_AFTER:
        return ()

    out: list[Signal] = []
    for label, info in away.items():
        ip = info.get("ip")
        since_raw = info.get("since")
        if not ip or not since_raw:
            continue
        try:
            since = datetime.fromisoformat(since_raw)
        except ValueError:
            continue
        if now - since < HOME_IP_CHANGE_AFTER:
            continue
        title = f"Home IP may have changed to {ip}"
        already = await fetch_one(
            f"""SELECT id FROM signals WHERE property = $1
                AND title = $2
                AND at > now() - interval '{HOME_IP_RENOTIFY_DAYS} days'
                LIMIT 1""", PROPERTY, title)
        if already:
            continue
        local_since = since.astimezone()
        out.append(Signal(
            title=title,
            body=(f"Your home IP may have changed to {ip} ({label} has "
                 f"been on it since {local_since:%H:%M}); update "
                 f"HOME_IPS?"),
            severity=Severity.INFO,
        ))
    return tuple(out)


# G18 of the reel audit: a circumstance he already gave ("I'll be working
# late this week", "travelling until Friday") explains an off-hours check-in
# on its own, so a fresh "anything I should know?" nudge on top of it is
# noise, not news. Narrower than `api.voice.CIRCUMSTANCE`, which also catches
# exams and deadlines: those don't say anything about *when* he's working, so
# they must not silence this check. Matches free text the owner said aloud, not a
# structured field -- `rhythm_notes.kind` is "circumstance" for every note
# `note_circumstance` writes, whatever it's actually about.
_COVERS_HOURS = re.compile(
    r"\b(working (?:late|early)|up (?:late|early)|late|early|away|"
    r"travel(?:l)?ing|out of town|on holiday|off (?:this|next) week)\b",
    re.IGNORECASE,
)


async def signals() -> tuple[Signal, ...]:
    """One INFO signal while this hour is off the owner's usual pattern for this
    day. Title is fixed so the scheduler's own 24h title-dedup (see
    `kernel.scheduler.collect_signals`) keeps this to one a day even though
    the clock in the body changes every time it is asked. `suggests` is left
    unset on purpose: setting it would route this through `propose()` as a
    parameterless approval card instead of `notify()`, and `notify()` is what
    makes INFO respect quiet hours (invariant 5 in spirit — a 2am nudge
    should wait for the morning, not ring anything).

    Suppressed entirely while an active circumstance note already covers
    working late, early or away (G18): he told ATLAS why, so the notice would
    only be asking him something he already answered.

    Also carries `_home_ip_change_signals()` — an unrelated notice (his home
    IP may have changed) that happens to live in the same module because it
    is `rhythm.presence()`'s own bookkeeping; it is independent of the
    off-hours check above and fires or not on its own terms.
    """
    out: list[Signal] = []

    now = datetime.now().astimezone()
    window = await rhythm.usual_window(now.weekday())
    if window is not None:
        state = rhythm.classify_hour(now.hour, window)
        if state != "usual":
            notes = await rhythm.active_notes()
            if not any(_COVERS_HOURS.search(n.get("detail", "") or "")
                      for n in notes):
                start, end = window
                verb = "already" if state == "early" else "still"
                out.append(Signal(
                    title="Working outside usual hours",
                    body=(f"It's {now:%H:%M} and you're {verb} on — outside "
                         f"your usual {start:02d}:00–{end:02d}:00. Anything "
                         f"I should know?"),
                    severity=Severity.INFO,
                ))

    out.extend(await _home_ip_change_signals())
    return tuple(out)


class RhythmModule:
    name = PROPERTY
    title = "Rhythm"
    requires_env: tuple[str, ...] = ()
    optional_env: tuple[str, ...] = ()
    setup_note = ""

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("pattern", check_pattern, timedelta(hours=1),
                 Severity.INFO, "deviation from the owner's usual hours for today",
                 baseline_sigma=None),
            Check("home_ip_change", check_home_ip_change, HOME_IP_CHECK_INTERVAL,
                 Severity.INFO, "how long a listed peer has sat on one "
                 "address outside HOME_IPS", baseline_sigma=None),
        )

    def actions(self):
        return (
            Action(
                name="rhythm.note_circumstance",
                tier=Tier.AUTO,
                summary="Record something the owner said about his schedule "
                        "(exam week, travel, feeling unwell, ...)",
                run=rhythm.note_circumstance,
                dry_run=rhythm.note_circumstance_dry,
                rollback=rhythm.note_circumstance_rollback,
                params={"detail": "what he said, close to verbatim",
                        "until": "ISO date it applies through, or omit"},
            ),
        )

    async def signals(self):
        return await signals()

    def brief(self) -> str:
        return ""

    async def agenda(self, within):
        return ()

    async def inbox(self, limit: int = 10):
        return ()


MODULE = RhythmModule()
