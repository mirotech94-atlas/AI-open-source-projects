"""Guardian incident response: read the alert, lay out the plan, and ask.

Phase 18.6, from reel DZteY4hJs29: a HUD narrating an assistant that traced
three C2 beacons, added firewall rules, killed processes, hunted persistence
and disabled a rogue account, "and he never had to ask me". The playbook, the
classification and the narration transfer. Acting without asking does not:
containment on a network Miro or a client depends on is externally visible,
so every step here is a Tier 2 proposal and waits for his word. Disabling
an account is further still: it is credential revocation, Tier 3 for ATLAS,
so a named account is listed for Miro to disable himself (`Plan.manual`) and
`contain` refuses the kind by name before it looks at any token.

What this file reads (all read-scope, all verified in the hub's
`hub/guardian_hub/routers/api_v1.py`, Guardian commit `bb6c439`):

  * `GET /v1/alerts/{key}/evidence` -- the one route that returns an alert's
    raw sensor `evidence` (the public Alert strips it). `reputation` and
    `mac` are read from there when the sensor put one in, and are otherwise
    None. The hub's Alert schema has no reputation or mac field -- both live
    only in the free-form `evidence` blob it never validates -- so None is
    the usual answer and neither is ever filled in.
  * `GET /v1/alerts?status=open` -- related open alerts on the same host
    become the other vectors and answer the persistence hunt. The hub pages
    at 200; its `total` says whether the page was the whole list, and when it
    was not, nothing is reported as "none open".
  * `GET /v1/alerts` -- the alert history: beacon `first_seen` times feed
    the operator rule.
  * `GET /v1/fixes` -- the fix log: `block_ip` / `block_subnet` / `ban_long`
    rows are the blocks the operator rule measures against.

  `network` is different from `mac`: the hub's `Alert` schema carries a real
  `network: NetworkRef | None` field (verified in `schemas_api.py`), but the
  evidence bundle's own hand-built `alert` dict (`api_v1.py::alert_evidence`)
  does not pass it through. So the primary vector's network is recovered by
  matching its key against the SAME alert's row in the open-alerts or history
  pages this file already reads -- a cross-reference of two verified reads
  about the identical alert, never a guess. A key absent from both pages
  gets no network, and nothing here invents one.

What it does NOT write, and why:

  * `POST /v1/containments` and `POST /v1/containments/{id}/release` were an
    ASSUMED pair of routes. Task 52 re-read the hub's write-scope surface
    (`GUARDIAN_WRITE_TOKEN`) end to end and confirmed there is no
    containment route at all: `POST /v1/devices/{mac}` says in its own
    description that "removing or isolating a device is not exposed", and
    `POST /v1/alerts/{key}/{op}` accepts only ack / snooze / resolve /
    reopen. `contain()` now never calls the hub -- `refusal()` answers
    `CONTAIN_UNAVAILABLE` (or the Tier 3 wording for `disable_account`) for
    every kind, unconditionally. The action stays registered, at Tier 2,
    only so the playbook can still show a client what containment it cannot
    do; its dry run and its `run` say the same thing and neither ever POSTs.
    `block_ip` and `isolate_host` join `disable_account` as MANUAL entries:
    "do it on the router yourself".

v1's write scope has four real routes; this file wires up three of them, each
gated on the separate `GUARDIAN_WRITE_TOKEN` (never the read-scope
`GUARDIAN_API_TOKEN`). `POST /v1/speedtest` is the fourth -- verified, same
shape as `/v1/scans`, and deliberately left unwired: nothing in the incident
playbook needs a speed test yet, and adding an action nothing proposes would
be surface for its own sake.

  * `POST /v1/alerts/{key}/{op}` -- `alert_op`. `op` is ack, snooze, resolve
    or reopen; snooze alone reads `hours` (1-168, the hub's own bound). All
    four are reversible by the hub's own description, but a fixed op-to-op
    pairing (ack/snooze/resolve → reopen, reopen → resolve) would reopen an
    alert that was actually only snoozed, or resolve one that had only been
    acked. So the rollback reads the alert's own prior status first
    (`alert_status()`, `GET /v1/alerts/{key}`, verified read-scope) and
    restores THAT status via `STATUS_TO_OP`, never a guessed one; a prior
    `snoozed` status has no restoring op (the hub does not report a
    snooze's remaining duration) and that rollback refuses honestly instead.
  * `POST /v1/devices/{mac}?network=<net>` -- the trust label. **Not
    wired, by Miro's decision (2026-09-28, RA-C6): "suggest only".** ATLAS
    never writes a Vault trust label, approved or not. Where a trust change
    would help, `analyse()` puts a SUGGESTION in the plan's `manual` list --
    the device, the network, why, and a link to the device in Vault
    (`device_link`) -- for Miro to mark himself. There is no function in
    this file, and no action or tool anywhere, that sends that request.
  * `POST /v1/scans` -- `rescan`. The real route takes no body and no
    `network` at all (verified: `trigger_scan(p=WRITE, db=...)` has no
    parameter for it) -- it queues one sensor sweep for the whole tenant.
    `network` is kept as a parameter here only to say, honestly, which
    network the incident concerns; it is never sent to the hub, and the dry
    run says so rather than implying a scope the API cannot honour. The
    202 response is `{"id": job}`, polled at `GET /v1/jobs/{id}` (not done
    here). A scan cannot be un-run, so the rollback records that as a
    failure rather than pretending to undo it. `rescan` also takes a
    narration-only `alert_key`.

For an ACTIVE beacon (`kind == "beacon"`, its alert still open) the
playbook proposes `rescan` when the network is known, and SUGGESTS marking
the device untrusted -- never proposes it -- when both a `mac` (from the
evidence blob) and a `network` (from the cross-reference above) are known and
look like a real mac / network id; missing or malformed either produces a
MANUAL entry naming what is missing rather than a guess.

**`evidence["mac"]` is VERIFIED ABSENT from the live sensor, not merely
unconfirmed.** `sensor/guardian_sensor/alerts.py`'s `build_alerts()` raises
every finding through `add(sev, key, msg)` -- no evidence argument exists at
that call site at all -- and the beacon line
(`add("WARN", f"dns:beacon:{b['ip']}:{b['domain']}", ...)`, `alerts.py:849`)
is one of its callers. The hub's own ingest (`ingest.py`) only copies
`evidence` through from whatever the sensor's alert dict already carried
(`cur.get("evidence") or {}`) -- it invents nothing -- so a beacon alert's
`evidence` is always `{}` on real data today. The trust suggestion therefore
never names a device from a live beacon: every active beacon is MANUAL
(plus `rescan`, when the network is known) until the sensor is changed to
emit a mac. That is a ruling for Miro, not this file -- `analyse()` already
degrades to MANUAL rather than guessing, and will keep doing so honestly
either way; this paragraph exists so nobody mistakes "no mac" for a bug.

`rescan` carries a narration-only `alert_key` -- never sent to the hub, the
same treatment its `network` already had -- because Mission's Incident panel
reads the last 50 `guardian.%` proposals across every incident, not just
this one. Without it, a proposal raised for an earlier, unrelated incident
could paint THIS incident's vector as done. `analyse()` fills it from the vector's own alert key; the
panel restores the same "one of this incident's own alert keys" filter the
pre-Task-52 code had for `guardian.contain`, generalised to whichever of
`alert_key`/`alert_id` a proposal's params carry, and treats an unparseable
`plan_incident.opened` as "match nothing" rather than "match everything".

There is deliberately no automatic `alert_op(resolve)` rule for "this looks
like a false positive": the hub's `Alert` schema has no field that says so,
and inferring it from `confidence` or any other number would be exactly the
guess CLAUDE.md's sixth invariant forbids. `alert_op` exists as a real,
callable action; a false-positive `resolve` is proposed by the skill's own
judgment, from the same wrapped, untrusted evidence text every other read
here already carries, never by `analyse()` computing it.

Every hub response byte that could reach a prompt or the audit log --
including a POST failure's `reason`, which can carry the request path (and
for `alert_op`, the path carries the attacker-chosen alert key) -- is passed
through `safe_hub_reason()` before it lands in an `ActionResult.detail`, and
only validated scalars (`safe_alert_evidence`, `safe_device_evidence`,
`safe_job_id`) are ever kept in `evidence`, never the raw hub payload.

Everything the hub returns is sensor-supplied and attacker-influenced -- a
beacon's domain is chosen by whoever runs the beacon. The dataclasses keep the
raw values; `atlas.tools.builtin.guardian_incident` wraps them before they
reach a prompt.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from .._http import post_json

# The package's own constants. Safe at the top: the package imports this file
# only lazily (inside its check and action functions), so by the time this
# line runs `atlas.modules.guardian` is fully initialised and there is no cycle.
from . import API, HUB

KINDS: tuple[str, ...] = ("block_ip", "isolate_host")
# Smallest first: one IP before a host. Still the shape `guardian.contain`
# validates against, even though every kind now refuses -- see the module
# docstring.
KIND_ORDER: dict[str, int] = {k: i for i, k in enumerate(KINDS)}

# Kinds ATLAS refuses by name, whatever token or endpoint exists. Disabling
# an account is credential revocation -- Tier 3, permanently denied
# (CLAUDE.md invariant 3). `permissions.denial_reason` reads only the action's
# verb, and `contain` carries no deny fragment, so this value-level refusal is
# the one place Tier 3 is enforced for this action's arguments.
DENIED_KINDS: dict[str, str] = {
    "disable_account": ("disable_account is credential revocation — Tier 3, "
                        "permanently denied for ATLAS; Miro performs it "
                        "himself"),
}
MANUAL_NOTE = "Tier 3 for ATLAS: credential revocation"

# `POST /v1/containments` never existed (verified absent, Task 52 -- see the
# module docstring). Every kind that is not Tier 3 refuses with this, always.
CONTAIN_UNAVAILABLE = "Guardian has no containment API (block/isolate not exposed)"
CONTAIN_MANUAL_NOTE = f"{CONTAIN_UNAVAILABLE} — do it on the router yourself"
NO_MAC_NOTE = ("the hub named no MAC for {host} — mark it untrusted in the "
              "Guardian console yourself")
# RA-C6, Miro 2026-09-28: "suggest only". ATLAS never writes a trust label.
SUGGEST_NOTE = ("suggested, not done — ATLAS never changes a Vault trust "
                "label; mark it untrusted in Vault yourself")
NO_NETWORK_NOTE = ("the hub named no network for {host} — act on it in the "
                   "Guardian console yourself")

NO_WRITE_TOKEN = ("the read token cannot act, by design — this needs "
                  "GUARDIAN_WRITE_TOKEN, a separate token Miro sets "
                  "deliberately; it is not set")

# The hub's own four alert operations (`ALERT_OPS` in
# hub/guardian_hub/routers/write.py).
ALERT_OPS: tuple[str, ...] = ("ack", "snooze", "resolve", "reopen")
SNOOZE_MIN_HOURS, SNOOZE_MAX_HOURS = 1, 168

# The status each op leaves an alert in (`ALERT_OPS` dict in write.py),
# inverted: the op that would RESTORE a given prior status. `alert_op`'s
# rollback reads the alert's own prior status before it ever writes and
# looks it up here -- never a fixed op-to-op pairing, which would reopen an
# alert that had actually been snoozed, or resolve one that had only been
# acked. "snoozed" has no entry: restoring a snooze would need the hub's own
# remaining duration, which nothing here reads, so that rollback refuses
# honestly rather than re-snoozing for a guessed length.
STATUS_TO_OP: dict[str, str] = {"open": "reopen", "acked": "ack", "resolved": "resolve"}

# Verified shapes for the three real write routes' path/query arguments.
# Rejecting outside these is defence in depth on top of `quote()`-ing every
# value that reaches a URL -- an alert key or a device's reported MAC is
# sensor-supplied, and a beacon's domain is chosen by the attacker running it.
_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
_NETWORK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_BAD_KEY_CHARS = re.compile(r"[\s\x00-\x1f?#]")
_ECHO_LIMIT = 64


def safe_echo(value: Any) -> str:
    """A rejected value, made safe to name in a refusal message: truncated
    and stripped of newlines. These can originate in sensor evidence -- a
    device's reported MAC, an op the model read off attacker-shaped alert
    text -- so the full string is never quoted verbatim."""
    text = str(value).replace("\n", "\\n").replace("\r", "\\r")
    if len(text) > _ECHO_LIMIT:
        text = text[:_ECHO_LIMIT] + "…"
    return repr(text)


# The fix log's words for "a firewall block went in" (sensor/guardian_sensor/
# fix.py: block_ip, block_subnet; ban_long is the fail2ban fallback).
BLOCK_ACTIONS = frozenset({"block_ip", "block_subnet", "ban_long"})
LIVE_WINDOW_S = 120

# What is safe to keep from a hub write response once it reaches an
# ActionResult's evidence -- never the raw payload. `msg`/`why` on an Alert
# are sensor-supplied free text; a device's `label`/`owner` are typed by a
# person. Only these validated scalars are ever kept.
ALERT_STATUSES = frozenset({"open", "acked", "snoozed", "resolved"})
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def safe_hub_reason(reason: str | None) -> str:
    """Every hub response byte that could reach a prompt or the audit log
    stays wrapped -- `explain()` can put the failed request's path into
    `reason`, and for `alert_op` that path carries the alert key, which is
    sensor data a beacon's own attacker chose."""
    from ...kernel.permissions import wrap_untrusted
    return wrap_untrusted(reason or "the hub returned nothing", "guardian hub error")


def safe_alert_evidence(data: dict[str, Any]) -> dict[str, Any]:
    status = data.get("status") if isinstance(data, dict) else None
    return {"status": status if status in ALERT_STATUSES else None}


def safe_device_evidence(data: dict[str, Any]) -> dict[str, Any]:
    trusted = data.get("trusted") if isinstance(data, dict) else None
    return {"trusted": trusted if isinstance(trusted, bool) else None}


def safe_job_id(data: dict[str, Any]) -> str | None:
    job = data.get("id") if isinstance(data, dict) else None
    text = str(job) if isinstance(job, (str, int)) and not isinstance(job, bool) else ""
    return text if text and _JOB_ID_RE.match(text) else None

# Sort order for the automatic steps `analyse` proposes -- mark the device
# untrusted before asking the sensor to look again.
STEP_ORDER: dict[str, int] = {"guardian.rescan": 1,
                              "guardian.alert_op": 2}

Operator = Literal["bot", "live", "unknown"]


@dataclass(frozen=True)
class Vector:
    kind: str
    target: str
    evidence: dict[str, Any]
    first_seen: datetime | None
    reputation: str | None          # the hub's own field when present, else None
    # Both None unless the hub's own records name them -- see the module
    # docstring on where each comes from. Never inferred, never resolved by
    # ATLAS from anything else.
    mac: str | None = None
    network: dict[str, Any] | None = None      # the hub's own NetworkRef dict


@dataclass(frozen=True)
class Plan:
    alert_id: str
    vectors: list[Vector]
    # "live" only on the 120 s rule; "unknown" otherwise. "bot" is part of the
    # vocabulary but has no positive rule, so nothing here ever infers it.
    operator: Operator
    steps: list[dict[str, Any]] = field(default_factory=list)
    persistence_checks: list[str] = field(default_factory=list)
    say: str = ""
    # What only Miro may do: a named account to disable, and any containment
    # this plan cannot automate (no containment API, or a beacon missing the
    # mac/network the automatic route needs). Kept OUT of `steps` -- no step
    # here is ever a Tier 3 act, and no step here is ever a guessed call.
    manual: list[dict[str, Any]] = field(default_factory=list)


# ------------------------------------------------------------------- reading

async def _read(path: str, **params: Any) -> tuple[Any, str | None]:
    """A read-scope GET through the package's one authenticated path."""
    from . import _api
    return await _api(path, **params)


def _when(raw: Any) -> datetime | None:
    """An ISO timestamp from the hub, or None. A naive stamp is the hub's
    UTC (every writer there uses UTC); an unparseable one is not guessed."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _is_ip(text: str) -> bool:
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def _parse_key(key: str) -> tuple[str, str, str | None]:
    """(kind, target, host) from a sensor alert key.

    The shapes are the sensor's own (`sensor/guardian_sensor/alerts.py`):
    `dns:beacon:{client_ip}:{domain}`, `sec:attacker:{host}:{ip}`,
    `sec:login:{host}:{ip}`, `sec:<finding>:{host}`. A key this does not
    recognise is a plain finding with no host, so nothing is proposed on it.
    """
    parts = key.split(":")
    if key.startswith("dns:beacon:") and len(parts) >= 4:
        # The IP is the CLIENT doing the beaconing (dnsstats.py), and may be
        # IPv6; the domain is last and has no colons.
        return "beacon", parts[-1], ":".join(parts[2:-1])
    if key.startswith(("sec:attacker:", "sec:login:")) and len(parts) >= 4:
        kind = "attacker" if key.startswith("sec:attacker:") else "login"
        return kind, ":".join(parts[3:]), parts[2]
    if key.startswith(("sec:users:", "sec:authkeys:")) and len(parts) >= 3:
        return "account", parts[2], parts[2]
    if key.startswith("sec:miner:") and len(parts) >= 3:
        return "process", parts[2], parts[2]
    if key.startswith(("sec:cron:", "sec:preload:", "sec:tmpexec:")) and len(parts) >= 3:
        return "persistence", parts[2], parts[2]
    if key.startswith(("home:rogue-", "lan:new:")) and len(parts) >= 3:
        return "device", parts[-1], parts[-1]
    if key.startswith("dns:dga:") and len(parts) >= 3:
        return "dga", ":".join(parts[2:]), None
    return "finding", key, None


def _named_account(raw: dict[str, Any]) -> str | None:
    for name in ("account", "user", "username"):
        value = raw.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _network_ref(value: Any) -> tuple[str | None, str | None]:
    """(id-or-slug for the hub's `?network=`, a display name) from a
    NetworkRef dict, or (None, None) when there is none. `slug` is preferred
    -- it is what a human reads in the Guardian console -- and `id` is the
    fallback when a network has none."""
    if not isinstance(value, dict):
        return None, None
    ident = value.get("slug") or value.get("id")
    ident = str(ident).strip() if isinstance(ident, (str, int)) and str(ident).strip() else None
    name = value.get("name")
    name = str(name).strip() if isinstance(name, str) and name.strip() else ident
    return ident, name


def _vector(alert: dict[str, Any], raw: dict[str, Any] | None,
            observed_at: str, network_by_key: dict[str, dict[str, Any]] | None = None) -> Vector:
    key = str(alert.get("key") or "")
    kind, target, host = _parse_key(key)
    raw = raw if isinstance(raw, dict) else {}
    reputation = raw.get("reputation")
    mac = raw.get("mac")
    mac = mac.strip() if isinstance(mac, str) and mac.strip() else None
    net = alert.get("network")
    if net is None and network_by_key:
        net = (network_by_key.get(key) or {}).get("network")
    evidence: dict[str, Any] = {
        "alert": key,
        "severity": alert.get("severity") or alert.get("sev"),
        "message": alert.get("message") or alert.get("msg") or "",
        "host": host,
        "source": "guardian hub",
        "observed_at": observed_at,
    }
    if kind == "account":
        account = _named_account(raw)
        if account:
            evidence["account"] = account
            target = account
    return Vector(
        kind=kind, target=target, evidence=evidence,
        first_seen=_when(alert.get("first_seen")),
        reputation=reputation if isinstance(reputation, str) and reputation else None,
        mac=mac, network=net if isinstance(net, dict) else None,
    )


def _contain_kind_for(vector: Vector) -> tuple[str, str] | None:
    """The containment that WOULD answer this vector, if Guardian's v1 API
    exposed one -- it does not (see the module docstring). Used only to
    decide whether a MANUAL entry is owed, never to build a step: `analyse`
    turns every non-None result here into either the beacon-specific
    automatic route below or a MANUAL line. An account vector never has
    one: disabling it is Miro's (`_manual_for`)."""
    host = vector.evidence.get("host")
    if vector.kind in ("attacker", "login"):
        return ("block_ip", vector.target) if _is_ip(vector.target) else None
    if vector.kind == "account":
        return None
    if vector.kind in ("beacon", "process", "persistence", "device") and host:
        return "isolate_host", str(host)
    return None


def _suggestion_for(vector: Vector) -> tuple[str, str] | None:
    """(mac, network id) of a device to SUGGEST Miro marks untrusted, or
    None. Only an active beacon (open, per `analyse`'s filtering of
    `_contain_kind_for`) whose mac AND network are both known by the hub's
    own records qualifies -- see the module docstring for why neither is
    ever guessed -- and both must look like a real mac / network id, since
    they end up in a link."""
    if vector.kind != "beacon":
        return None
    if _contain_kind_for(vector) is None:
        return None
    net_id, _name = _network_ref(vector.network)
    if (net_id and _NETWORK_RE.match(net_id)
            and vector.mac and _MAC_RE.match(vector.mac)):
        return vector.mac, net_id
    return None


def device_link(mac: str) -> str:
    """The device's own page in Vault, where Miro changes its trust."""
    from urllib.parse import quote
    return f"{HUB}/assets/devices/{quote(mac, safe='')}"


def _contain_manual(vector: Vector) -> dict[str, str] | None:
    """kind/target/note for a MANUAL entry this vector implies on its own --
    a trust change to SUGGEST (RA-C6: ATLAS never makes it), a containment
    Guardian's v1 API cannot do, or a beacon missing its mac or network.
    None when no containment-shaped step would ever have applied (an
    account, a finding, a DGA)."""
    candidate = _contain_kind_for(vector)
    if candidate is None:
        return None
    suggestion = _suggestion_for(vector)
    if suggestion is not None:
        mac, net_id = suggestion
        return {"kind": "mark_untrusted", "target": mac, "network": net_id,
                "note": SUGGEST_NOTE, "link": device_link(mac)}
    kind, target = candidate
    if vector.kind == "beacon":
        net_id, _name = _network_ref(vector.network)
        if net_id:
            # A network is known but no mac -- rescan still fires (see
            # `_rescan_targets`); only the device-level suggestion is missing.
            return {"kind": "mark_untrusted", "target": target,
                    "note": NO_MAC_NOTE.format(host=target)}
        return {"kind": "isolate_host", "target": target,
                "note": NO_NETWORK_NOTE.format(host=target)}
    return {"kind": kind, "target": target, "note": CONTAIN_MANUAL_NOTE}


def _rescan_targets(vectors: list[Vector]) -> list[tuple[str, str, str]]:
    """(network id, display name, alert_key), once per unique network among
    the active beacon vectors. A rescan does not need the device's mac -- it
    is useful even when `_contain_manual` had to name a missing one. The
    alert_key is the first contributing vector's own -- narration only (see
    the module docstring), but it is also what lets the Incident panel tell
    this incident's rescan from one raised for a different, unrelated one on
    the same network."""
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for v in vectors:
        if v.kind != "beacon" or _contain_kind_for(v) is None:
            continue
        net_id, net_name = _network_ref(v.network)
        if net_id and _NETWORK_RE.match(net_id) and net_id not in seen:
            seen.add(net_id)
            out.append((net_id, net_name or net_id, str(v.evidence.get("alert") or "")))
    return out


def _manual_for(vector: Vector) -> str | None:
    """The account the hub names on an account vector, for Miro to disable
    himself -- or None when the hub named none (never guessed)."""
    if vector.kind != "account":
        return None
    account = vector.evidence.get("account")
    return str(account) if account else None


def _operator(hosts: set[str], alerts: list[dict[str, Any]],
              fixes: list[dict[str, Any]]) -> Operator:
    """"live" when a new beacon appears within 120 s after a block on the same
    host, per the hub's own history. Anything else is "unknown".

    Beacon alerts name the client by IP and the fix log names hosts by name,
    so on a network where the two never agree this can only say "unknown" --
    which is the honest answer, not a gap to paper over."""
    blocks = [(str(f.get("host") or ""), _when(f.get("ts"))) for f in fixes
              if isinstance(f, dict) and f.get("action") in BLOCK_ACTIONS
              and f.get("ok") is True]
    beacons: list[tuple[str, datetime | None]] = []
    for alert in alerts:
        key = str(alert.get("key") or "") if isinstance(alert, dict) else ""
        if key.startswith("dns:beacon:"):
            _k, _t, host = _parse_key(key)
            beacons.append((host or "", _when(alert.get("first_seen"))))
    for b_host, b_at in blocks:
        if b_at is None or b_host not in hosts:
            continue
        for n_host, n_at in beacons:
            if n_at is None or n_host != b_host:
                continue
            gap = (n_at - b_at).total_seconds()
            if 0 < gap <= LIVE_WINDOW_S:
                return "live"
    return "unknown"


PERSISTENCE = (
    ("sec:cron:", "cron entries"),
    ("sec:preload:", "/etc/ld.so.preload"),
    ("sec:authkeys:", "authorized_keys"),
    ("sec:users:", "local users"),
    ("sec:tmpexec:", "executables in /tmp"),
)


def _persistence(hosts: list[str], open_keys: set[str], stamp: str,
                 partial: str | None) -> list[str]:
    """One line per host per persistence family. "none open" is said only
    when the whole open-alert list was read; from a partial page the most
    that can be said is that it was not seen in what was read."""
    out = []
    for host in hosts:
        for prefix, what in PERSISTENCE:
            live = f"{prefix}{host}" in open_keys or any(
                k.startswith(f"{prefix}{host}:") for k in open_keys)
            if live:
                tail = "open on the hub"
            elif partial:
                tail = f"not checked beyond {partial}"
            else:
                tail = f"none open as of {stamp}"
            out.append(f"{host}: {what} ({prefix.rstrip(':')}) — {tail}")
    return out


def _items(page: Any) -> tuple[list | None, int | None]:
    """A v1 page's items and the hub's own `total` (None when absent)."""
    if not isinstance(page, dict) or not isinstance(page.get("items"), list):
        return None, None
    total = page.get("total")
    ok = isinstance(total, int) and not isinstance(total, bool)
    return page["items"], total if ok else None


async def analyse(alert_id: str) -> tuple[Plan | None, str | None]:
    """Read one alert and everything around it; return the plan or why not.

    Four reads, together: the evidence bundle, the OPEN alerts (vectors and
    the persistence hunt), the alert history (beacon times for the operator
    rule -- truncation there can only cost a "live", never invent one) and
    the fix log. If any fails the answer is `(None, reason)`: a plan built on
    three quarters of the evidence would present a guess about the missing
    quarter as a finding.
    """
    (bundle, r1), (open_page, r2), (history_page, r3), (fixes_page, r4) = (
        await asyncio.gather(
            _read(f"/alerts/{alert_id}/evidence"),
            _read("/alerts", status="open", limit=200),
            _read("/alerts", limit=200),
            _read("/fixes", hours=24, limit=500),
        ))
    for reason in (r1, r2, r3, r4):
        if reason:
            return None, reason
    if not isinstance(bundle, dict) or not isinstance(bundle.get("alert"), dict):
        # ATLAS's own words and a count: the hub's key names stay out of it.
        n = len(bundle) if isinstance(bundle, dict) else 0
        return None, (f"the hub's evidence bundle carried no alert object "
                      f"({n} other field(s))")
    alerts, open_total = _items(open_page)
    history, _history_total = _items(history_page)
    fixes, _fixes_total = _items(fixes_page)
    if alerts is None or history is None or fixes is None:
        return None, "/v1/alerts or /v1/fixes returned no items list"
    read_open = len(alerts)
    # Never "none open" from a page that was not the whole list. A missing
    # total is treated as possibly truncated rather than assumed complete.
    partial = (None if open_total is not None and open_total <= read_open
               else f"the first {read_open} open alert(s)"
               + (f" of {open_total}" if open_total is not None else ""))

    now = datetime.now(UTC)
    observed_at = now.isoformat()
    stamp = now.strftime("%H:%M UTC")

    # Cross-reference for `network`: the evidence bundle's own `alert` dict
    # never carries it (verified absent -- see the module docstring), but
    # the SAME alert's row in either page already read here does. A key in
    # neither page keeps network unknown, never guessed.
    network_by_key: dict[str, dict[str, Any]] = {}
    for pool in (alerts, history):
        for a in pool:
            if isinstance(a, dict) and a.get("key"):
                network_by_key.setdefault(str(a["key"]), a)

    main = bundle["alert"]
    first = _vector({**main, "key": main.get("key") or alert_id},
                    main.get("evidence"), observed_at, network_by_key)
    vectors = [first]
    host = first.evidence.get("host")
    if host:
        related = [a for a in alerts if isinstance(a, dict)
                   and a.get("status", "open") == "open"
                   and a.get("key") != first.evidence["alert"]
                   and _parse_key(str(a.get("key") or ""))[2] == host]
        related.sort(key=lambda a: _when(a.get("first_seen")) or now)
        vectors += [_vector(a, None, observed_at, network_by_key) for a in related]

    steps: list[dict[str, Any]] = []
    manual: list[dict[str, Any]] = []
    seen_manual: set[tuple[str, str]] = set()
    for index, vector in enumerate(vectors, 1):
        account = _manual_for(vector)
        if account and all(m["target"] != account for m in manual):
            manual.append({
                "kind": "disable_account", "target": account,
                "why": f"vector {index}: {vector.kind} → {vector.target}",
                "note": MANUAL_NOTE, "observed_at": observed_at,
            })
            continue

        cm = _contain_manual(vector)
        if cm is not None and (cm["kind"], cm["target"]) not in seen_manual:
            seen_manual.add((cm["kind"], cm["target"]))
            manual.append({**cm,
                           "why": f"vector {index}: {vector.kind} → {vector.target}",
                           "observed_at": observed_at})

    for net_id, net_name, net_alert_key in _rescan_targets(vectors):
        steps.append({
            "action": "guardian.rescan",
            "params": {"network": net_id, "alert_key": net_alert_key},
            "why": f"beacon activity on {net_name}",
            "tier": 2, "observed_at": observed_at,
        })
    steps.sort(key=lambda s: STEP_ORDER.get(str(s["action"]), 9))

    hosts = sorted({str(v.evidence["host"]) for v in vectors if v.evidence.get("host")})
    open_keys = {str(a.get("key")) for a in alerts
                 if isinstance(a, dict) and a.get("status", "open") == "open"}
    operator = _operator(set(hosts), history, fixes)

    where = ", ".join(hosts) if hosts else "no named host"
    notes = sorted({m["note"] for m in manual})
    say = (f"{len(vectors)} vector(s) on {where}, read {stamp}; operator "
           f"{operator}; "
           + (f"{len(steps)} step(s) proposed, none run — each one is "
              f"awaiting Miro." if steps
              else "nothing here can be automated from what the hub shows.")
           + (f" {len(manual)} item(s) for Miro to handle himself ("
              + "; ".join(notes) + ")." if manual else "")
           + (f" Only {partial} were read, so related vectors and "
              f"persistence beyond them were not checked." if partial else ""))
    return Plan(alert_id=alert_id, vectors=vectors, operator=operator,
                steps=steps,
                persistence_checks=_persistence(hosts, open_keys, stamp, partial),
                say=say, manual=manual), None


# ------------------------------------------------------------------ telling

def _manual_phrase(entry: dict[str, str]) -> str:
    if entry["kind"] == "disable_account":
        return f"disable account {entry['target']}"
    if entry["kind"] == "block_ip":
        return f"block {entry['target']}"
    if entry["kind"] == "isolate_host":
        return f"isolate {entry['target']}"
    if entry["kind"] == "mark_untrusted" and entry.get("link"):
        return (f"mark {entry['target']} untrusted on {entry.get('network')} "
                f"in Vault: {entry['link']}")
    if entry["kind"] == "mark_untrusted":
        return f"mark {entry['target']} untrusted"
    return f"{entry['kind']} {entry['target']}"


def narrate(plan: Plan) -> str:
    """The console text, one line per vector. A proposed step is said as
    proposed and awaiting Miro -- never "severed", never "blocked": nothing
    has run, and the hub has not shown anything stop."""
    total = len(plan.vectors)
    lines = []
    for i, vector in enumerate(plan.vectors, 1):
        host = vector.evidence.get("host")
        what = f"{vector.kind} → {vector.target}"
        if host and host != vector.target:
            what += f" (on {host})"
        rep = (f"{vector.reputation} (hub)" if vector.reputation
               else "unknown")
        account = _manual_for(vector)
        manual_entry = None if account else _contain_manual(vector)
        if account:
            tail = f"MANUAL — disable account {account} — {MANUAL_NOTE}"
        elif manual_entry is not None and manual_entry.get("link"):
            tail = (f"SUGGESTED — {_manual_phrase(manual_entry)} — "
                    f"{manual_entry['note']}")
        elif manual_entry is not None:
            tail = f"MANUAL — {_manual_phrase(manual_entry)} — {manual_entry['note']}"
        else:
            tail = "no containment proposed"
        lines.append(f"VECTOR {i}/{total} — {what} — reputation: {rep} — {tail}")
    return "\n".join(lines)


def as_dict(plan: Plan) -> dict[str, Any]:
    """JSON-ready, for an Observation's evidence and for the agent's tool."""
    out = asdict(plan)
    for vector, source in zip(out["vectors"], plan.vectors, strict=True):
        first = vector.get("first_seen")
        vector["first_seen"] = first.isoformat() if isinstance(first, datetime) else None
        # The step that answers this one vector, or null. None does since
        # RA-C6: a trust change is a suggestion (`manual`), and a rescan
        # answers a network, not a vector. Kept for the panel's shape.
        vector["step"] = None
        # Manual (Tier 3 for ATLAS, or containment/mac/network v1 cannot
        # reach) is not the same claim as "no step".
        account = _manual_for(source)
        if account:
            vector["manual"] = {"kind": "disable_account", "target": account,
                                "note": MANUAL_NOTE}
        else:
            cm = _contain_manual(source)
            vector["manual"] = (
                {k: cm[k] for k in ("kind", "target", "note", "network", "link")
                 if k in cm} if cm else None)
    out["observed_at"] = (plan.vectors[0].evidence.get("observed_at")
                          if plan.vectors else None)
    return out


# ----------------------------------------------------------------- acting

def _write_token() -> str:
    return os.getenv("GUARDIAN_WRITE_TOKEN", "").strip()


def _kind_refusal(kind: str) -> str | None:
    """Refuse a Tier 3 kind BY NAME, and any other unknown kind, before
    anything else is looked at -- token, endpoint or target."""
    if kind in DENIED_KINDS:
        return DENIED_KINDS[kind]
    if kind not in KINDS:
        return f"kind must be one of {', '.join(KINDS)}, not {safe_echo(kind)}"
    return None


def _bad_params(alert_id: str, target: str, kind: str) -> str | None:
    """The executor checks parameter NAMES; the values are the model's, so
    they are checked here. A closed catalog with open arguments is not
    closed."""
    denied = _kind_refusal(kind)
    if denied:
        return denied
    if not isinstance(alert_id, str) or not alert_id.strip():
        return "alert_id is required"
    if not isinstance(target, str) or not target.strip():
        return "target is required"
    if kind == "block_ip" and not _is_ip(target.strip()):
        return (f"block_ip needs an IP address, not {safe_echo(target)} — a domain "
                f"block is a DNS change, which is permanently denied")
    return None


def refusal(alert_id: str, target: str, kind: str) -> str | None:
    """Why `contain` would not send -- always something. `disable_account`
    (or any name outside `KINDS`) gets `_kind_refusal`'s specific wording;
    every other kind gets `CONTAIN_UNAVAILABLE`, unconditionally. There is no
    path here that ever returns None: v1 has no containment route at all."""
    denied = _kind_refusal(kind)
    if denied:
        return denied
    return CONTAIN_UNAVAILABLE


async def contain(alert_id: str, target: str,
                  kind: str) -> tuple[dict[str, Any] | None, str | None]:
    """Never reaches the hub. `refusal` always answers with a reason -- see
    the module docstring for why the endpoint this used to assume does not
    exist -- so this is the whole function."""
    return None, refusal(alert_id, target, kind)


async def release(token: str) -> tuple[dict[str, Any] | None, str | None]:
    """`contain` never succeeds, so no rollback token is ever issued for it
    in practice. Kept only so the Action can declare a rollback."""
    return None, ("nothing was ever contained to release: " + CONTAIN_UNAVAILABLE)


# ------------------------------------------------------- alert_op (real, T2)

def _bad_alert_key(alert_key: str) -> str | None:
    if not isinstance(alert_key, str) or not alert_key.strip():
        return "alert_key is required"
    if _BAD_KEY_CHARS.search(alert_key):
        return "alert_key carries whitespace or a control character, so it is refused"
    return None


def _bad_narration_key(alert_key: str) -> str | None:
    """Like `_bad_alert_key`, but empty is allowed. `rescan`'s `alert_key`
    is narration only (see the module docstring) -- `analyse()` always fills
    it, and there is nothing to send that a blank value could corrupt."""
    if not isinstance(alert_key, str):
        return "alert_key must be a string"
    if alert_key and _BAD_KEY_CHARS.search(alert_key):
        return "alert_key carries whitespace or a control character, so it is refused"
    return None


def _bad_alert_op(alert_key: str, op: str, hours: int | None) -> str | None:
    bad_key = _bad_alert_key(alert_key)
    if bad_key:
        return bad_key
    if op not in ALERT_OPS:
        return f"op must be one of {', '.join(ALERT_OPS)}, not {safe_echo(op)}"
    if op == "snooze":
        if hours is None:
            return "snooze needs hours (1-168)"
        if isinstance(hours, bool) or not isinstance(hours, int):
            return "hours must be a whole number of hours"
        if not SNOOZE_MIN_HOURS <= hours <= SNOOZE_MAX_HOURS:
            return f"hours must be between {SNOOZE_MIN_HOURS} and {SNOOZE_MAX_HOURS}"
    elif hours is not None:
        return f"hours is only for snooze, not {safe_echo(op)}"
    return None


def alert_op_call(alert_key: str, op: str, hours: int | None) -> dict[str, Any]:
    """The exact request `alert_op` would make. `alert_key` is quoted before
    it ever reaches a URL -- it is sensor-supplied and can carry attacker-
    chosen text (a beacon's own domain)."""
    from urllib.parse import quote
    body: dict[str, Any] = {"hours": hours} if op == "snooze" else {}
    return {
        "method": "POST",
        "url": f"{HUB}{API}/alerts/{quote(alert_key, safe='')}/{op}",
        "headers": {"Authorization": "Bearer <GUARDIAN_WRITE_TOKEN>"},
        "json": body,
    }


def alert_op_refusal(alert_key: str, op: str, hours: int | None) -> str | None:
    if not _write_token():
        return NO_WRITE_TOKEN
    return _bad_alert_op(alert_key, op, hours)


async def alert_op(alert_key: str, op: str,
                   hours: int | None = None) -> tuple[dict[str, Any] | None, str | None]:
    """`POST /v1/alerts/{key}/{op}`. All four ops are reversible and audited
    by the hub itself; `hours` only ever reaches the body for `snooze`."""
    why = alert_op_refusal(alert_key, op, hours)
    if why:
        return None, why
    from urllib.parse import quote
    body = {"hours": hours} if op == "snooze" else {}
    data, reason = await post_json(
        f"{API}/alerts/{quote(alert_key, safe='')}/{op}", base_url=HUB,
        headers={"Authorization": f"Bearer {_write_token()}"}, json_body=body)
    if reason:
        return None, reason
    return (data if isinstance(data, dict) else {"response": data}), None


async def alert_status(alert_key: str) -> tuple[str | None, str | None]:
    """Read-scope `GET /v1/alerts/{key}` -- what `alert_op`'s rollback needs
    to know before it may ever act: the status this alert actually held,
    never the one a fixed op-to-op table would guess. `open`/`acked`/
    `resolved` map to a restoring op in `STATUS_TO_OP`; `snoozed` does not,
    by design (see that constant)."""
    from urllib.parse import quote

    from . import _api
    data, reason = await _api(f"/alerts/{quote(alert_key, safe='')}")
    if reason:
        return None, reason
    status = data.get("status") if isinstance(data, dict) else None
    if not isinstance(status, str) or status not in ALERT_STATUSES:
        return None, "the hub did not name a recognised status for this alert"
    return status, None


async def write_token_account() -> tuple[str | None, str | None]:
    """`GET /v1/whoami` with the WRITE token: which Guardian account it acts
    on. A read, but the one place outside the three write routes that this
    token is sent, so it lives here with them. `readback.same_tenant` needs
    it: a read-back with the read token only describes the tenant the write
    touched when both tokens name the same account."""
    if not _write_token():
        return None, NO_WRITE_TOKEN
    from .._http import get_json
    data, reason = await get_json(f"{API}/whoami", base_url=HUB,
                                  headers={"Authorization": f"Bearer {_write_token()}"})
    if reason or not isinstance(data, dict):
        return None, reason or "whoami answered with an unexpected shape"
    account = str(data.get("account_slug") or data.get("account") or "")
    return (account, None) if account else (None, "whoami named no account")


# ---------------------------------------------------------- rescan (real, T2)

def rescan_call() -> dict[str, Any]:
    return {"method": "POST", "url": f"{HUB}{API}/scans",
            "headers": {"Authorization": "Bearer <GUARDIAN_WRITE_TOKEN>"}, "json": {}}


def rescan_refusal(network: str, alert_key: str) -> str | None:
    """`alert_key`, like `network`, never reaches the hub -- see the module
    docstring -- but is validated: the Incident panel correlates a proposal
    to an incident by it."""
    if not _write_token():
        return NO_WRITE_TOKEN
    bad_key = _bad_narration_key(alert_key)
    if bad_key:
        return bad_key
    if not isinstance(network, str) or not _NETWORK_RE.match(network.strip()):
        return f"network must be an id or slug, not {safe_echo(network)}"
    return None


async def rescan(network: str, alert_key: str) -> tuple[dict[str, Any] | None, str | None]:
    """`POST /v1/scans` -- no body, and never `network` or `alert_key`: the
    real route scans the whole tenant (verified: `trigger_scan` takes
    neither). Both are accepted here only so the proposal can say which
    network and which alert prompted it; neither ever reaches the hub."""
    why = rescan_refusal(network, alert_key)
    if why:
        return None, why
    data, reason = await post_json(
        f"{API}/scans", base_url=HUB,
        headers={"Authorization": f"Bearer {_write_token()}"})
    if reason:
        return None, reason
    return (data if isinstance(data, dict) else {"response": data}), None
