"""NetBird mesh — and ATLAS's own perimeter, which is why it is watched.

Mesh membership *is* the authentication for the dashboard: `atlas.example.com`
answers only to a source inside the mesh. So a peer appearing here is not a
networking event, it is an access-control event, and it is the one thing in
this module worth waking someone for.

Three corrections over the previous version:

* **It pointed at `api.netbird.io`**, the hosted service. Miro runs his own
  NetBird at `mironetbird.example.com`. A token minted on the self-hosted server
  would have authenticated against a SaaS account that has none of his peers
  — the same class of error as reading Guardian from its marketing domain.

* **Exceptions were rendered with `str(exc)`.** An httpx error carries the
  request URL, and this module's requests go to the server that fronts the
  mesh; the repo already has `redact()`/`explain()` for exactly this and the
  module bypassed them. The standing rule is no secrets in logs or in
  Postgres, and an observation's `detail` lands in both.

* **"New peer" was decided from `connected_at`**, which is when a peer last
  came online, not when it first appeared. Every laptop that reconnects each
  morning would have been reported as a new peer every morning, at HIGH — and
  a security alert that fires daily is one nobody reads. It now diffs against
  the peer ids recorded in the previous observation, so "new" means "not seen
  before" and nothing else. With no history it says so rather than declaring
  every peer new on the first run.

Setup keys are read for name and expiry only. The key value is a credential
that adds peers to the mesh, so it is never read, never logged and never
stored — the check only ever answers "which named key expires soon".
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import Check, Health, Observation, Severity, Signal
from .._http import explain, get_json

BASE = os.getenv("NETBIRD_API_URL", "https://mironetbird.example.com").rstrip("/")

NO_TOKEN = ("no NETBIRD_TOKEN set. Create a personal access token in the "
            "NetBird console at " + BASE + " (Settings -> Users -> your "
            "user -> Access tokens). A read of peers and setup keys is all "
            "ATLAS needs; it makes no mesh changes.")

# Fields that might carry a peer's identity, most stable first. `id` is what
# the API guarantees; the others are for readability in the message.
ID_FIELDS = ("id", "key", "ip")
NAME_FIELDS = ("name", "hostname", "dns_label")


def _token() -> str:
    return os.getenv("NETBIRD_TOKEN", "").strip()


async def _get(path: str) -> tuple[list | None, str | None]:
    if not _token():
        return None, NO_TOKEN
    data, reason = await get_json(
        f"/api{path}", base_url=BASE,
        headers={"Authorization": f"Token {_token()}"})
    if reason:
        return None, reason
    if not isinstance(data, list):
        return None, (f"/api{path} returned {type(data).__name__}; "
                      f"expected a list")
    return [p for p in data if isinstance(p, dict)], None


def _identity(peer: dict) -> str:
    for field in ID_FIELDS:
        if peer.get(field):
            return str(peer[field])
    return ""


def _label(peer: dict) -> str:
    for field in NAME_FIELDS:
        if peer.get(field):
            return str(peer[field])
    return _identity(peer) or "unnamed peer"


async def check_peers() -> Observation:
    peers, reason = await _get("/peers")
    if reason:
        return Observation("peers", Health.UNKNOWN, detail=reason)
    if not peers:
        # An empty mesh is not a healthy mesh: ATLAS itself is a peer, so
        # zero peers means this reading is wrong, not that all is well.
        return Observation("peers", Health.UNKNOWN, 0.0, "online",
                           "the mesh reports no peers at all, but ATLAS is "
                           "itself a peer — this reading cannot be right")

    # Peers that are allowed to be off the mesh (NETBIRD_OPTIONAL_PEERS, a
    # comma list of names): a phone whose app sleeps is not an outage, and
    # since 2026-09-27 presence is read from the home Wi-Fi, not the mesh.
    optional = {n.strip().lower() for n in
                os.getenv("NETBIRD_OPTIONAL_PEERS", "").split(",") if n.strip()}
    offline = [_label(p) for p in peers if not p.get("connected")]
    counted = [n for n in offline if n.lower() not in optional]
    resting = [n for n in offline if n.lower() in optional]
    note = f"; optional and off: {', '.join(resting)}" if resting else ""
    return Observation(
        "peers",
        Health.DEGRADED if counted else Health.OK,
        float(len(peers) - len(offline)), "online",
        (f"{len(counted)} of {len(peers)} peers offline: "
         + ", ".join(counted[:8]) + note) if counted
        else f"{len(peers) - len(offline)} of {len(peers)} peers online{note}",
        evidence={"total": len(peers), "offline": offline[:20],
                  "optional_offline": resting[:20]})


async def check_new_peers() -> Observation:
    """A peer joining the mesh is an access-control event.

    Decided by diffing against the ids seen last time, not by reading a
    timestamp off the peer. NetBird's per-peer times describe the last
    connection, so a timestamp test reports every returning laptop as an
    arrival — which is how this alert would have become background noise.
    """
    from ...kernel.state import last_evidence

    peers, reason = await _get("/peers")
    if reason:
        return Observation("new_peers", Health.UNKNOWN, detail=reason)

    current = {_identity(p): _label(p) for p in peers if _identity(p)}
    if not current:
        return Observation(
            "new_peers", Health.UNKNOWN,
            detail="no peer carried an id, key or ip; cannot tell peers apart")

    prior = await last_evidence("netbird", "new_peers")
    known: list[Any] = prior.get("known") if isinstance(prior, dict) else None

    if not isinstance(known, list) or not known:
        # First run, or no history. Declaring every existing peer "new" would
        # be a false alarm on the very first reading, which is the worst
        # possible introduction to this alert.
        return Observation(
            "new_peers", Health.OK, 0.0, "peers",
            f"baseline recorded: {len(current)} peer(s) known. Arrivals are "
            f"reported from the next run onwards.",
            evidence={"known": sorted(current), "baseline": True})

    arrived = [label for ident, label in current.items()
               if ident not in set(known)]
    return Observation(
        "new_peers",
        Health.DEGRADED if arrived else Health.OK,
        float(len(arrived)), "peers",
        f"{len(arrived)} peer(s) joined the mesh: {', '.join(arrived[:8])}"
        if arrived else f"no new peers ({len(current)} known)",
        evidence={"known": sorted(current), "arrived": arrived})


async def check_setup_keys() -> Observation:
    """Names and expiry only. A setup key's value adds peers to the mesh, so
    it is never read into an observation, a log line or Postgres."""
    keys, reason = await _get("/setup-keys")
    if reason:
        return Observation("setup_keys", Health.UNKNOWN, detail=reason)

    cutoff = datetime.now(UTC) + timedelta(days=14)
    expiring, spent, unparsed = [], [], 0
    for key in keys or ():
        if key.get("revoked") or not key.get("expires"):
            continue
        try:
            when = datetime.fromisoformat(str(key["expires"]))
        except ValueError:
            unparsed += 1
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        if when < cutoff:
            # A setup key enrols NEW peers; it does nothing for peers already
            # on the mesh. The "Atlas" key expiring in a week flagged as
            # DEGRADED on the first live run, and it had done its one job
            # (used_times 1) -- letting it lapse changes nothing. Only an
            # unused key expiring is a finding, because that is the one
            # somebody still intends to use.
            if int(key.get("used_times") or 0) == 0:
                expiring.append(str(key.get("name") or "unnamed key"))
            else:
                spent.append(str(key.get("name") or "unnamed key"))

    if unparsed and not expiring:
        return Observation(
            "setup_keys", Health.UNKNOWN, float(unparsed), "keys",
            f"{unparsed} setup key(s) had an expiry this could not parse, so "
            f"an expiring key may be hiding behind that")
    return Observation(
        "setup_keys",
        Health.DEGRADED if expiring else Health.OK,
        float(len(expiring)), "keys",
        f"{len(expiring)} unused setup key(s) expiring within 14 days: "
        + ", ".join(expiring[:8]) if expiring else
        ("no unused keys expiring soon"
         + (f" ({len(spent)} already-used key(s) lapsing, which is fine)"
            if spent else "")),
        evidence={"expiring": expiring[:20], "spent": spent[:20]})


class NetBirdModule:
    name = "netbird"
    title = "NetBird mesh"
    # Not gated on the token: it is better to report UNKNOWN naming the
    # console to mint one than to vanish from the board entirely, because
    # this mesh is the dashboard's whole perimeter.
    requires_env = ()
    # Not required to load, but these unlock checks that are
    # UNKNOWN without them. `atlas.cli connect netbird` says which.
    # Where: NetBird console -> Settings -> Users -> Access tokens
    optional_env = ("NETBIRD_TOKEN",)

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("peers", check_peers, timedelta(minutes=5), Severity.HIGH,
                  "peers online", baseline_sigma=None),
            Check("new_peers", check_new_peers, timedelta(hours=1),
                  Severity.HIGH, "peers that were not here last time",
                  baseline_sigma=None),
            Check("setup_keys", check_setup_keys, timedelta(hours=12),
                  Severity.NORMAL, "setup keys expiring", baseline_sigma=None),
        )

    def actions(self):
        return ()          # mesh changes are Miro's, not ATLAS's

    async def signals(self):
        obs = await check_new_peers()
        arrived = obs.evidence.get("arrived") or []
        if not arrived:
            return ()
        return (Signal(
            title=f"NetBird: {len(arrived)} peer(s) joined the mesh",
            body=", ".join(arrived)
                 + "\n\nMesh membership is what authenticates the ATLAS "
                   "dashboard — a peer here can reach atlas.example.com. If you "
                   "did not add these, treat it as urgent.",
            severity=Severity.HIGH),)

    def brief(self) -> str:
        return "NetBird: peers online, arrivals, setup key expiry"


MODULE = NetBirdModule()
