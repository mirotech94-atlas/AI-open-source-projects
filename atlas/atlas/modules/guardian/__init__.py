"""Guardian — the network watchdog.

Like the FlyCheap module before it, every endpoint the previous version called
was invented: `/api/admin/agents/summary`, `/api/admin/metrics/errors`,
`/api/admin/metrics/funnel`, `/api/admin/alerts/queue`. None exist. The real
product has a properly designed, documented, versioned API — and it is served
from `my.guarder.io`, not `guarder.io`, which is the marketing site and
answers 404 to every one of these.

**The token must be read-scope, and this module checks that rather than
trusting it.**

Guardian has a rule its own test suite enforces: `label` and `trusted` are
human vouches, not display fields. A trusted device is skipped by the
rogue-uplink check, exempt from risky-port alerts and scored lower risk. A
test fails if an automatic path writes either. `POST /v1/devices/{mac}` is the
one v1 route that sets them, and it requires `scope == "write"`.

So the control is the token's scope, not this module's good behaviour. A
read-scope token makes the warrant rule unbreakable from here no matter what
code is added later; a write-scope token would leave ATLAS one call away from
forging a human vouch, and the effect of that is not an audit-log entry — it
is a rogue device going unreported. `GET /v1/whoami` returns the scope, so
`token_scope` is a check: a write token is reported as a finding, loudly,
rather than quietly worked around.

For the same reason the real v1 write routes (`POST /v1/alerts/{key}/{op}`,
`/v1/devices/{mac}`, `/v1/scans`, `/v1/speedtest`) are not on the everyday
token: they all need the scope this module argues ATLAS should not hold on
`GUARDIAN_API_TOKEN`. Task 52 gave three of them their own actions below,
each gated on the separate `GUARDIAN_WRITE_TOKEN` instead -- see that
paragraph for which, and why `/v1/speedtest` stays unwired. One of those
three, the device trust label, was removed on 2026-09-28 (see below).

**Three write actions, and they all need a separate token.** Phase 18.6 (reel
DZteY4hJs29, see `incident.py`) started this as `guardian.contain(alert_id,
target, kind)` -- block one IP or isolate one host -- against an assumed
`POST /v1/containments`. Task 52 re-read the hub's write-scope surface
(`hub/guardian_hub/routers/api_v1.py` and `write.py`, at Guardian `bb6c439`)
end to end and found that route was never real: there is no containment
endpoint in v1 at all. `POST /v1/devices/{mac}` says in its own description
that "removing or isolating a device is not exposed", and
`POST /v1/alerts/{key}/{op}` accepts only ack / snooze / resolve / reopen.
`guardian.contain` stays registered, at Tier 2, only so an incident card can
still show what containment v1 cannot do -- `block_ip` and `isolate_host`
refuse every time, the same as `disable_account` already did for credential
revocation, and the playbook lists both as MANUAL: do it on the router
yourself.

What v1 actually exposes, write-scope, is four routes. ATLAS wires two of
them -- `guardian.alert_op` and `guardian.rescan`, alongside the
now-always-refusing `guardian.contain` -- each Tier 2, each with a real dry
run, and each gated on `GUARDIAN_WRITE_TOKEN` being deliberately set,
separate from the read-scope `GUARDIAN_API_TOKEN`, so that:

  * every check keeps reading with the read-scope token, and `token_scope`
    still reports a write-scope `GUARDIAN_API_TOKEN` as a finding;
  * the write token is used by exactly the functions in `incident.py` that
    call these routes, each with one fixed path, so no later code that
    reaches for "the Guardian token" picks up more than the hub actually
    grants -- plus one read there, `write_token_account` (`GET /v1/whoami`),
    which the read-back uses to confirm both tokens are one account;
  * leaving it unset -- the default -- means none of the three actions is in
    the catalog at all, and the reasoner cannot even name them.

**The device trust label is never written (RA-C6).** Miro, 2026-09-28:
"suggest only". `guardian.mark_untrusted` (`POST /v1/devices/{mac}`) was
removed with its tool and its write functions: ATLAS cannot change a Vault
trust label even with an approval. An incident plan instead carries a
suggestion -- which device, on which network, why, and a link to it in
Vault -- and the `open_incidents` reading names it for Miro to mark himself.

See `incident.py`'s module docstring for exactly what each of them sends,
what each rolls back to, and why `guardian.rescan`'s `network` argument is
never part of the request it makes.

`/api/v1/health` needs no token at all, so reachability is reported even when
nothing is configured. The module is therefore not gated on env: a missing
token turns six checks UNKNOWN and leaves three working, which is strictly
more useful than the whole module being disabled.
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import timedelta

import httpx

from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    Health,
    Observation,
    Severity,
    Signal,
    Tier,
)
from .._http import expect_keys, get_json

# my.guarder.io is the hub. guarder.io is the marketing site and 404s the API.
HUB = os.getenv("GUARDIAN_HUB_URL", "https://my.guarder.io")
SITE = os.getenv("GUARDIAN_SITE_URL", "https://guarder.io")
# /v1, NOT /api/v1. `my.guarder.io/api/v1/health` answers 200 with
# {"ok":true,"product":"Guardian","version":"0.1.0"} -- but that is the web
# app's own health route, not the versioned API's. It made /api/v1 look
# correct while every real endpoint under it 404'd. Verified against the live
# API: /v1/whoami returns the token principal, /api/v1/whoami is "not found".
API = "/v1"

NO_TOKEN = ("no GUARDIAN_API_TOKEN set. Create a READ-scope token in the "
            "Guardian admin console (Settings -> API tokens) and put it in "
            ".env. It must not be write-scope: a write token can set a "
            "device's `trusted` and `label` fields, which are human vouches "
            "and must never be written by anything automatic.")


def _token() -> str:
    return os.getenv("GUARDIAN_API_TOKEN", "").strip()


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_token()}"}


async def _api(path: str, **params) -> tuple[dict | None, str | None]:
    if not _token():
        return None, NO_TOKEN
    return await get_json(f"{API}{path}", base_url=HUB, headers=_headers(),
                          params=params or None)


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
                await asyncio.sleep(3)   # not under the same burst; see flycheap
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


# ------------------------------------------------------------ unauthenticated

async def check_hub_api() -> Observation:
    """`/v1/health` is public. This is the one Guardian check that works
    before anything is configured, so it is what distinguishes "the hub is
    down" from "ATLAS has no token".

    It also guards against the decoy. Both of these answer 200:

        /v1/health      {"ok":true,"product":"Guardian","api":"v1"}
        /api/v1/health  {"ok":true,"product":"Guardian","version":"0.1.0"}

    The second is the web app's own health route, and it is what made
    `/api/v1` look like the right prefix while every real endpoint under it
    404'd. The `api` field only appears on the versioned API, so pointing
    GUARDIAN_HUB_URL at something that answers without it is reported rather
    than read as health.
    """
    data, reason = await get_json(f"{API}/health", base_url=HUB)
    if reason:
        return Observation("hub_api", Health.FAILING, detail=reason)
    bad = expect_keys(data, "ok", where=f"{API}/health")
    if bad:
        return Observation("hub_api", Health.UNKNOWN, detail=bad)

    api = data.get("api")
    if not api:
        return Observation(
            "hub_api", Health.UNKNOWN, None, None,
            f"{HUB}{API}/health answered without an `api` field, so this is "
            f"probably the web app's health route rather than the versioned "
            f"API. Every authenticated check will 404 against it. Check "
            f"GUARDIAN_HUB_URL.",
            evidence={"got": sorted(data) if isinstance(data, dict) else None})

    return Observation(
        "hub_api", Health.OK if data.get("ok") else Health.FAILING,
        None, None,
        f"hub API {api} "
        + ("responding" if data.get("ok") else "reports itself unhealthy"),
        evidence={"api": api, "product": data.get("product")})


async def check_cert() -> Observation:
    from ...tools.builtin import cert_check
    result = await cert_check("guarder.io")
    if "error" in result:
        return Observation("cert", Health.UNKNOWN, detail=result["error"])
    days = result["days_left"]
    return Observation(
        "cert",
        Health.FAILING if days < 7 else Health.DEGRADED if days < 21 else Health.OK,
        float(days), "days", f"certificate expires in {days} days")


# ------------------------------------------------------------- the token itself

async def check_token_scope() -> Observation:
    """A write-scope token is a finding, not a convenience.

    `GET /v1/whoami` is the endpoint Guardian's own API guide recommends as
    "a good first call when wiring up a client", and it reports exactly what
    matters here: what this token may do.
    """
    data, reason = await _api("/whoami")
    if reason:
        return Observation("token_scope", Health.UNKNOWN, detail=reason)
    bad = expect_keys(data, "scope", "role", "account", where="/v1/whoami")
    if bad:
        return Observation("token_scope", Health.UNKNOWN, detail=bad)

    scope = str(data.get("scope", "")).lower()
    where = f"{data.get('account')} as {data.get('role')}"
    if scope == "write":
        return Observation(
            "token_scope", Health.DEGRADED, None, None,
            f"the Guardian token ATLAS holds is WRITE scope ({where}). That "
            f"permits POST /v1/devices/{{mac}}, which sets `trusted` and "
            f"`label` — human vouches that Guardian's own tests forbid any "
            f"automatic path from writing. Reissue it read-only; ATLAS needs "
            f"nothing more and should not be able to do more.",
            evidence={"scope": scope, "role": data.get("role")})
    if scope != "read":
        # Only a scope the hub names as read is read. An empty or unfamiliar
        # value used to fall through to "read-scope token", OK (RA-E1).
        return Observation(
            "token_scope", Health.UNKNOWN, None, None,
            f"/v1/whoami reported a scope ATLAS does not recognise "
            f"({scope[:20] or 'none'}), so whether the token can write a "
            f"device vouch is not known",
            evidence={"scope": scope[:20], "role": data.get("role")})
    return Observation(
        "token_scope", Health.OK, None, None,
        f"read-scope token for {where}; ATLAS cannot write a device vouch",
        evidence={"scope": scope, "role": data.get("role"),
                  "expires": data.get("expires")})


# --------------------------------------------------------------- the account

async def check_overview() -> Observation:
    """The headline picture the customer dashboard draws, read from the same
    payload so ATLAS and the dashboard cannot disagree."""
    data, reason = await _api("/overview")
    if reason:
        return Observation("overview", Health.UNKNOWN, detail=reason)
    if not isinstance(data, dict):
        return Observation("overview", Health.UNKNOWN,
                           detail=f"/v1/overview returned {type(data).__name__}")

    score = data.get("score")
    if isinstance(score, dict):
        score = score.get("value", score.get("score"))
    if not isinstance(score, (int, float)):
        # Report the shape rather than defaulting. A missing score defaulted
        # to 0 would be a confident FAILING about nothing.
        return Observation(
            "overview", Health.UNKNOWN,
            detail=f"/v1/overview carried no numeric score; got keys: "
                   f"{', '.join(sorted(data)[:8]) or 'nothing'}")

    return Observation(
        "overview",
        Health.FAILING if score < 50 else Health.DEGRADED if score < 75
        else Health.OK,
        float(score), "score", f"Guardian security score is {score:.0f}/100",
        evidence={"score": score})


async def check_sensors() -> Observation:
    """Is each network's sensor still reporting?

    A sensor that stopped is the failure that hides every other failure: the
    dashboard keeps drawing the last scan and nothing raises an alert,
    because nothing is looking. Silence reads as a quiet network.
    """
    data, reason = await _api("/networks")
    if reason:
        return Observation("sensors", Health.UNKNOWN, detail=reason)
    bad = expect_keys(data, "networks", where="/v1/networks")
    if bad:
        return Observation("sensors", Health.UNKNOWN, detail=bad)

    nets = [n for n in (data.get("networks") or []) if isinstance(n, dict)]
    if not nets:
        return Observation("sensors", Health.UNKNOWN,
                           detail="/v1/networks listed no networks at all")

    def reporting(net: dict) -> bool | None:
        for key in ("sensor_reporting", "reporting", "sensor_online", "online"):
            if key in net:
                return bool(net[key])
        return None

    states = [(str(n.get("name") or n.get("slug") or "?"), reporting(n))
              for n in nets]
    if all(state is None for _n, state in states):
        return Observation(
            "sensors", Health.UNKNOWN,
            detail=f"/v1/networks does not say whether sensors are reporting; "
                   f"keys present: {', '.join(sorted(nets[0])[:8])}")

    silent = [name for name, state in states if state is False]
    return Observation(
        "sensors",
        Health.FAILING if silent else Health.OK,
        float(len(nets) - len(silent)), "networks",
        f"{len(nets) - len(silent)} of {len(nets)} network sensor(s) reporting"
        + (f" — silent: {', '.join(silent)}. Nothing is being scanned there, "
           f"so nothing will alert." if silent else ""),
        evidence={"networks": len(nets), "silent": silent})


async def check_open_alerts() -> Observation:
    data, reason = await _api("/alerts", status="open", limit=100)
    if reason:
        return Observation("open_alerts", Health.UNKNOWN, detail=reason)
    items = (data or {}).get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return Observation(
            "open_alerts", Health.UNKNOWN,
            detail=f"/v1/alerts returned no items list; got "
                   f"{', '.join(sorted(data)[:8]) if isinstance(data, dict) else type(data).__name__}")

    # The hub names it `sev` with CRIT / WARN / INFO. Read only as
    # severity/level, a CRIT alert never counted as high and the module read
    # DEGRADED where it should read FAILING (found 2026-09-28).
    def sev(item: dict) -> str:
        return str(item.get("sev") or item.get("severity")
                   or item.get("level") or "").lower()

    high = [i for i in items if isinstance(i, dict)
            and sev(i) in ("crit", "critical", "high")]
    return Observation(
        "open_alerts",
        Health.FAILING if high else Health.DEGRADED if items else Health.OK,
        float(len(items)), "alerts",
        f"{len(items)} open alert(s)"
        + (f", {len(high)} at high or critical" if high else ""),
        evidence={"open": len(items), "high": len(high)})


# The hub clamps a page at 200; 50 is plenty for a household or an MSP's
# client, and `total` says when it was not.
INCIDENT_PAGE = 50


async def check_open_incidents() -> Observation:
    """Groups of related alerts the hub tells as one story, still open.

    When one is open, the newest one's first alert is analysed (all reads)
    and the plan rides along in the evidence -- vectors, the operator
    classification and the proposed steps, each with the time it was read --
    so the dashboard's Incident panel draws from this row rather than from a
    second, unrecorded read of the hub.
    """
    data, reason = await _api("/incidents", open="true", limit=INCIDENT_PAGE)
    if reason:
        return Observation("open_incidents", Health.UNKNOWN, detail=reason)
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        # ATLAS's own words and a count: the hub's key names stay out of a
        # detail that reaches the reasoner's context unwrapped.
        n = len(data) if isinstance(data, dict) else 0
        return Observation(
            "open_incidents", Health.UNKNOWN,
            detail=f"/v1/incidents returned no items list ({n} other field(s))")

    incidents = [i for i in items if isinstance(i, dict)]
    total = data.get("total")
    if not isinstance(total, int) or isinstance(total, bool):
        total = None
    if total is None and len(items) >= INCIDENT_PAGE:
        # A full page with no total is a floor, not a count.
        return Observation(
            "open_incidents", Health.UNKNOWN,
            detail=f"at least {len(items)} open incidents; the hub gave no "
                   f"total, so the count is not known",
            evidence={"incidents_read": len(items)})
    # The hub's own total; never fewer than the rows it actually sent.
    count = max(total, len(items)) if total is not None else len(items)
    if count == 0:
        return Observation("open_incidents", Health.OK, 0.0, "incidents",
                           "no open incidents", evidence={"incidents": []})

    summary = [{"iid": i.get("iid"), "entity": i.get("entity"),
                "sev": i.get("sev"), "opened": i.get("opened"),
                "alert_keys": [k for k in (i.get("alert_keys") or [])
                               if isinstance(k, str)]}
               for i in incidents]
    crit = [i for i in summary if str(i.get("sev") or "").upper() == "CRIT"]

    evidence: dict = {"incidents": summary, "plan": None,
                      "incidents_read": len(summary), "total": count,
                      # RA-E4. Whether the newest incident was actually
                      # investigated. "incomplete" is never a resolution:
                      # the incident stays open, and the reading stays not-OK.
                      "investigation": "incomplete"}
    chosen = next((i for i in summary if i["alert_keys"]), None)
    if chosen is not None:
        from . import incident
        # Which incident the plan belongs to: the panel keys step state on
        # it, so a proposal from an earlier incident on the same IP or host
        # never colours this one.
        evidence["plan_incident"] = chosen
        plan, why = await incident.analyse(chosen["alert_keys"][0])
        if plan is not None:
            evidence["plan"] = incident.as_dict(plan)
            evidence["narration"] = incident.narrate(plan)
            evidence["investigation"] = "complete"
        else:
            evidence["plan_unavailable"] = why

    return Observation(
        "open_incidents",
        Health.FAILING if crit else Health.DEGRADED,
        float(count), "incidents",
        f"{count} open incident(s)"
        + (f", {len(crit)} critical" if crit else "")
        + (f"; only the first {len(summary)} were read, so severity beyond "
           f"them is not known" if count > len(summary) else "")
        # ATLAS's own words only: the hub's reason can carry an alert key a
        # beacon's operator chose, so it stays in the evidence.
        + ("" if evidence["investigation"] == "complete" else
           "; investigation incomplete: the newest incident could not be "
           "analysed, so it is open and undiagnosed, not resolved")
        + _suggestions_line(evidence.get("plan")),
        evidence=evidence)


def _suggestions_line(plan: dict | None) -> str:
    """RA-C6: the trust changes the plan suggests, for Miro to make himself
    in Vault. Only validated values reach it (a mac and a network id that
    matched their shapes, and a link built from them), never hub text."""
    manual = (plan or {}).get("manual") or []
    picks = [m for m in manual if isinstance(m, dict) and m.get("link")]
    if not picks:
        return ""
    return "; suggested for you (ATLAS changes no trust label): " + "; ".join(
        f"mark {m['target']} untrusted on {m.get('network')} in Vault — "
        f"{m['link']}" for m in picks[:3])


# --------------------------------------------------------- containment (T2)
# One tenant, checked before any write (plan D07/P06). The card was built
# from what the READ token sees; the write goes out on GUARDIAN_WRITE_TOKEN.
# If those are two Guardian accounts, "ack alert k" would change another
# tenant's alert of the same key, and the prior status read for the rollback
# would be this tenant's. So a write goes out only when both tokens name the
# same account, asked of `GET /v1/whoami` with each (`readback.same_tenant`).
# Mismatched or unreadable: nothing is read or sent, and the refusal names no
# account -- the other tenant's slug is not this card's to show.

async def _tenant_refusal() -> str | None:
    from .readback import same_tenant
    _account, refused = await same_tenant()
    if refused is None:
        return None
    return f"tenant not confirmed, so nothing was sent to the hub: {refused.reason}"


# Verified absent from v1 (see the module and incident.py docstrings). Every
# kind refuses, unconditionally, so there is no "would POST" to show.

async def _contain_dry(alert_id: str, target: str, kind: str) -> ActionResult:
    from . import incident
    why = incident.refusal(alert_id, target, kind)
    return ActionResult(False, f"refused: {why}", evidence={"kind": kind})


async def _contain_run(alert_id: str, target: str, kind: str) -> ActionResult:
    from . import incident
    _data, reason = await incident.contain(alert_id, target, kind)
    return ActionResult(False, reason or "refused")


async def _release(token: str) -> ActionResult:
    from . import incident
    _data, reason = await incident.release(token)
    return ActionResult(False, reason or "nothing to release")


# --------------------------------------------------------- alert_op (real, T2)

async def _alert_op_dry(alert_key: str, op: str, hours: int | None = None) -> ActionResult:
    from . import incident
    why = incident.alert_op_refusal(alert_key, op, hours)
    call = incident.alert_op_call(alert_key, op, hours)
    detail = (f"would {call['method']} {call['url']} with "
              f"{json.dumps(call['json'])} "
              f"(Authorization: {call['headers']['Authorization']})")
    if why:
        detail += f" — not sendable: {why}"
    return ActionResult(why is None, detail, evidence={"call": call})


async def _alert_op_run(alert_key: str, op: str, hours: int | None = None) -> ActionResult:
    """Validates before reading anything (M4's reasoning, applied here too):
    an invalid alert_key or op does no read-scope GET at all. Then reads the
    alert's OWN prior status before writing -- a fixed op-to-op pairing would
    reopen an alert that was actually only snoozed, or resolve one that had
    only been acked; see `alert_status`."""
    from . import incident
    why = incident.alert_op_refusal(alert_key, op, hours)
    if why:
        return ActionResult(False, why)
    if (other := await _tenant_refusal()) is not None:
        return ActionResult(False, other)
    prior_status, read_reason = await incident.alert_status(alert_key)
    data, reason = await incident.alert_op(alert_key, op, hours)
    if reason or data is None:
        return ActionResult(False, incident.safe_hub_reason(reason))
    token = json.dumps({"alert_key": alert_key, "op": op, "prior_status": prior_status})
    detail = f"the hub applied {op} to the alert"
    if prior_status is None:
        detail += (f" (its prior status could not be read — "
                   f"{incident.safe_hub_reason(read_reason)} — so rollback "
                   f"will refuse rather than guess it)")
    return ActionResult(True, detail, evidence=incident.safe_alert_evidence(data),
                        rollback_token=token)


async def _alert_op_rollback(token: str) -> ActionResult:
    from . import incident
    try:
        held = json.loads(token or "{}")
    except ValueError:
        return ActionResult(False, "rollback token is not the JSON alert_op returned")
    alert_key = held.get("alert_key") if isinstance(held, dict) else None
    prior_status = held.get("prior_status") if isinstance(held, dict) else None
    if not alert_key:
        return ActionResult(False, "rollback token names no alert to restore")
    reverse = incident.STATUS_TO_OP.get(str(prior_status))
    if not reverse:
        return ActionResult(
            False, f"the alert's prior status ({prior_status or 'unknown'}) cannot "
                   f"be restored by an alert_op -- refusing rather than guess one")
    data, reason = await incident.alert_op(str(alert_key), reverse)
    if reason or data is None:
        return ActionResult(False, incident.safe_hub_reason(reason))
    return ActionResult(True, f"the hub restored the alert to {prior_status}",
                        evidence=incident.safe_alert_evidence(data))


# ---------------------------------------------------------- rescan (real, T2)

async def _rescan_dry(network: str, alert_key: str) -> ActionResult:
    from . import incident
    why = incident.rescan_refusal(network, alert_key)
    call = incident.rescan_call()
    detail = (f"would {call['method']} {call['url']} — network is not sent; "
              f"the real route scans the whole account, not just "
              f"{incident.safe_echo(network)}")
    if why:
        detail += f" — not sendable: {why}"
    return ActionResult(why is None, detail, evidence={"call": call})


async def _rescan_run(network: str, alert_key: str) -> ActionResult:
    from . import incident
    why = incident.rescan_refusal(network, alert_key)
    if why:
        return ActionResult(False, why)
    if (other := await _tenant_refusal()) is not None:
        return ActionResult(False, other)
    data, reason = await incident.rescan(network, alert_key)
    if reason or data is None:
        return ActionResult(False, incident.safe_hub_reason(reason))
    job = incident.safe_job_id(data)
    if not job:
        return ActionResult(False, "the hub accepted the scan without a "
                                   "well-formed job id, so it cannot be polled")
    return ActionResult(True, f"the hub queued a scan, job {job}",
                        evidence={"job": job}, rollback_token=json.dumps({"job": job}))


async def _rescan_rollback(token: str) -> ActionResult:
    return ActionResult(False, "a scan cannot be un-run", evidence={"token": token})


class GuardianModule:
    name = "guardian"
    title = "Guardian"
    # Deliberately not gated on the token. `/api/v1/health` and the cert check
    # need none, and disabling the whole module would hide the difference
    # between "the hub is down" and "ATLAS has no credential" -- which is the
    # single most useful distinction this module can draw.
    requires_env = ()
    # Not required to load, but these unlock checks that are
    # UNKNOWN without them. `atlas.cli connect guardian` says which.
    # Where: Guardian console -> Settings -> API tokens, READ scope.
    # GUARDIAN_WRITE_TOKEN is separate and deliberate: it is the only thing
    # that makes `guardian.contain` exist. See the module docstring.
    optional_env = ("GUARDIAN_API_TOKEN", "GUARDIAN_WRITE_TOKEN")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("hub_api", check_hub_api, timedelta(minutes=5),
                  Severity.CRITICAL,
                  "the Guardian hub API is responding", baseline_sigma=None),
            Check("site", lambda: _probe(SITE, "site"), timedelta(minutes=10),
                  Severity.NORMAL, "the marketing site responds"),
            Check("cert", check_cert, timedelta(hours=12), Severity.HIGH,
                  "the TLS certificate is not about to expire",
                  baseline_sigma=None),
            Check("token_scope", check_token_scope, timedelta(hours=6),
                  Severity.HIGH,
                  "ATLAS's Guardian token cannot write a device vouch",
                  baseline_sigma=None),
            Check("overview", check_overview, timedelta(minutes=30),
                  Severity.NORMAL, "the account's security score"),
            Check("sensors", check_sensors, timedelta(minutes=15),
                  Severity.HIGH,
                  "every network's sensor is still reporting",
                  baseline_sigma=None),
            Check("open_alerts", check_open_alerts, timedelta(minutes=15),
                  Severity.HIGH, "open alerts, and how severe they are"),
            # Two rounds of reads (the incident list, then three together
            # for the plan), each allowed 25 s: the 30 s default would time
            # a slow hub out into UNKNOWN before the plan came back.
            Check("open_incidents", check_open_incidents,
                  timedelta(minutes=15), Severity.HIGH,
                  "open incidents, with the response plan for the newest",
                  baseline_sigma=None, timeout=timedelta(seconds=60)),
        )

    def actions(self):
        # All three gated on the separate write token being deliberately
        # set. The fourth v1 write route, the device trust label, is not
        # wired at all (RA-C6: Miro's to set, never ATLAS's).
        if not os.getenv("GUARDIAN_WRITE_TOKEN", "").strip():
            return ()
        return (
            Action(
                name="guardian.contain",
                tier=Tier.ASK,
                summary=("Guardian's v1 API has no containment route: "
                         "block_ip and isolate_host always refuse. Kept "
                         "registered so an incident card can still show "
                         "what containment v1 cannot do."),
                run=_contain_run,
                dry_run=_contain_dry,
                rollback=_release,
                params={
                    "alert_id": "the Guardian alert key this answers",
                    "target": "the IP or host that would have been contained",
                    "kind": "block_ip | isolate_host",
                },
                # Kept from when this was believed reachable: if it ever ran,
                # a dropped connection could not be un-dropped. It never runs.
                irreversible=True,
                max_per_hour=3,
            ),
            Action(
                name="guardian.alert_op",
                tier=Tier.ASK,
                summary=("Act on one Guardian alert: ack, snooze, resolve or "
                         "reopen. Reversible: rollback reads the alert's own "
                         "prior status and restores that status, never a "
                         "guessed one."),
                run=_alert_op_run,
                dry_run=_alert_op_dry,
                rollback=_alert_op_rollback,
                params={
                    "alert_key": "the Guardian alert this answers",
                    "op": "ack | snooze | resolve | reopen",
                    "hours": "snooze length in hours, 1-168 (snooze only)",
                },
                max_per_hour=10,
            ),
            Action(
                name="guardian.rescan",
                tier=Tier.ASK,
                summary=("Ask the Guardian sensor to scan now, rather than "
                         "waiting for its next cycle. Irreversible: a scan "
                         "cannot be un-run."),
                run=_rescan_run,
                dry_run=_rescan_dry,
                rollback=_rescan_rollback,
                params={
                    "network": "which network this concerns (not sent to "
                              "the hub -- the route scans the whole account)",
                    "alert_key": "which alert this answers -- narration "
                                 "only, never sent to the hub",
                },
                irreversible=True,
                max_per_hour=3,
            ),
        )

    async def signals(self):
        obs = await check_token_scope()
        if obs.health is Health.DEGRADED:
            return (Signal(
                title="Guardian: ATLAS holds a write-scope API token",
                body=obs.detail + "\n\nNothing has been written, and no "
                     "action here uses this token, but the capability should "
                     "not exist. Reissue the token read-only in Settings -> "
                     "API tokens and replace GUARDIAN_API_TOKEN. (Containment "
                     "has its own GUARDIAN_WRITE_TOKEN; this is not it.)",
                severity=Severity.HIGH),)
        return ()

    def brief(self) -> str:
        return ("Guardian: hub API, sensor reporting, open alerts and "
                "incidents, security score, certificate")


MODULE = GuardianModule()
