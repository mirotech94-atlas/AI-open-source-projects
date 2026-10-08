"""VPSie — the account ATLAS itself runs in.

Rewritten against a working client: `matrix-standup-bot/app/integrations/
vpsie.py`, whose auth flow carries a "verified 2026-04-30" note. Every
endpoint the previous version used was wrong, which makes five modules found
this way — see MODULE_AUDIT.md.

    was                                  is
    /apps/v2/auth/login                  /api/v2/auth/from/api
    {"client_id", "client_secret"}       {"clientId", "clientSecret"}
    {"access_token", "expires_in"}       {"accessToken": {"token", "expires"}}
    /apps/v2/vm                          (was: a made-up /api/v2 resource)
    /apps/v2/snapshots                   /api/v2/snapshot
    /apps/v2/billing/summary             /api/v2/dashboard, /api/v2/billing/invoices

**VPSie answers HTTP 500 for a bad or disabled credential**, with its own
envelope `{"error": true, "code": 500, "message": "..."}`. So a rejected
client secret is indistinguishable from a server fault unless the body is
read — and the previous version called `raise_for_status()`, which turned
"your credentials are wrong" into "VPSie is down". That is not a cosmetic
difference: it sends Miro to look at the wrong thing.

There is no action here. The write paths are real (`/vm/restart`, `/vm/start`)
but this is the account ATLAS lives in, and restarting the wrong instance
takes out the thing doing the monitoring. A guard that refuses the host by
hostname match is weaker than not having the action at all.

Credentials are read from the environment and nowhere else. They never reach
the repo, the database, a log line or a prompt: if ATLAS reports a VPSie
problem the reasoner sees "credentials rejected", never the secret.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import time
from datetime import UTC, datetime, timedelta
from typing import Any

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
from .._http import number, redact

# TWO base paths on one API, and the official Go SDK is the only place that
# says so plainly. Authentication lives under /api/v2; every resource --
# vm, snapshot, backup, billing, monitoring, all of it -- lives under
# /apps/v2. The bot client this module was ported from had only ever
# verified auth, so /snapshot 404'd from the wrong base and looked like a
# missing endpoint. Both are kept explicit rather than hidden behind one
# BASE that is right for one call and wrong for every other.
AUTH_BASE = os.getenv("VPSIE_API_URL", "https://api.vpsie.com/api/v2").rstrip("/")
BASE = os.getenv("VPSIE_APPS_URL", "https://api.vpsie.com/apps/v2").rstrip("/")

NO_CREDS = ("no VPSIE_CLIENT_ID / VPSIE_CLIENT_SECRET set. Create them in the "
            "VPSie control panel under API -> Create App, then open the app "
            "and pick a Role — choose the most read-only role available, "
            "because ATLAS makes no changes here. Set them with "
            "bootstrap/set-secret.sh so the values never pass through a chat.")

_token: tuple[str, float] | None = None


class VpsieRejected(Exception):
    """The credentials were refused. Distinct from VPSie being unreachable."""


class VpsieRateLimited(Exception):
    """Too many logins. Not a credential problem, and it says so."""


def _message(response: httpx.Response) -> str:
    """VPSie's own error envelope, which it sends with a 500 even when the
    real problem is a bad credential."""
    try:
        body = response.json()
    except ValueError:
        return (response.text or "").strip()[:160] or f"HTTP {response.status_code}"
    if isinstance(body, dict):
        for key in ("message", "error_description", "error"):
            value = body.get(key)
            if value and not isinstance(value, bool):
                return str(value).strip()[:200]
    return f"HTTP {response.status_code}"


def _creds() -> tuple[str, str] | None:
    cid = os.getenv("VPSIE_CLIENT_ID", "").strip()
    secret = os.getenv("VPSIE_CLIENT_SECRET", "").strip()
    return (cid, secret) if cid and secret else None


# VPSie rate-limits the auth endpoint hard: "Too many request, please try
# again after an hour" after a burst of logins. The JWT lasts about an hour
# and is cached below, so a healthy process authenticates once per hour --
# but a process that is restarted repeatedly (every deploy) or a script run
# outside the process (which does not share this cache) burns a login each
# time. A tripped limit blocks EVERY VPSie check for an hour, which reads
# as "VPSie refused the credentials" -- the wrong diagnosis. So the token
# is also persisted to disk, and a limit is reported as what it is.
_TOKEN_FILE = pathlib.Path(os.getenv("VPSIE_TOKEN_CACHE",
                                     "/app/data/.vpsie_token.json"))


def _load_cached() -> tuple[str, float] | None:
    try:
        raw = json.loads(_TOKEN_FILE.read_text())
        if raw.get("token") and float(raw.get("until", 0)) > time.time() + 300:
            return str(raw["token"]), float(raw["until"])
    except Exception:                                   # noqa: BLE001
        pass
    return None


def _save_cached(token: str, until: float) -> None:
    try:
        _TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_FILE.write_text(json.dumps({"token": token, "until": until}))
        _TOKEN_FILE.chmod(0o600)
    except Exception:                                   # noqa: BLE001
        pass                    # a cache that cannot be written is just slower


async def _access_token() -> str:
    global _token
    if _token and _token[1] > time.time() + 300:
        return _token[0]
    cached = _load_cached()
    if cached:
        _token = cached
        return cached[0]

    creds = _creds()
    if creds is None:
        raise VpsieRejected(NO_CREDS)
    cid, secret = creds

    async with httpx.AsyncClient(timeout=25, base_url=AUTH_BASE) as c:
        r = await c.post("/auth/from/api",
                         json={"clientId": cid, "clientSecret": secret})

    if r.status_code >= 400:
        # Deliberately not raise_for_status(). A 500 here usually means the
        # credential was refused, not that VPSie fell over, and the body is
        # the only thing that says which.
        msg = _message(r)
        if "too many" in msg.lower() or r.status_code == 429:
            raise VpsieRateLimited(
                f"VPSie is rate-limiting logins ({msg}). The credentials are "
                f"not the problem; every VPSie check is blind until the "
                f"window passes, about an hour.")
        raise VpsieRejected(msg)

    try:
        body = r.json()
    except ValueError:
        raise VpsieRejected("auth response was not JSON") from None

    access = body.get("accessToken") if isinstance(body, dict) else None
    if isinstance(access, dict):
        token, expires = access.get("token"), access.get("expires")
    else:
        token, expires = access, None
    if not token:
        raise VpsieRejected(
            f"auth succeeded but carried no token; keys: "
            f"{', '.join(sorted(body)[:8]) if isinstance(body, dict) else '?'}")

    # The JWT lasts about an hour. Prefer the stated expiry when it parses.
    deadline = time.time() + 3300
    if isinstance(expires, str):
        try:
            when = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            deadline = when.timestamp()
        except ValueError:
            pass
    _token = (str(token), deadline)
    _save_cached(str(token), deadline)
    return str(token)


async def _get(path: str, **params) -> Any:
    token = await _access_token()
    async with httpx.AsyncClient(timeout=25, base_url=BASE) as c:
        r = await c.get(path, headers={"Authorization": f"Bearer {token}"},
                        params=params or None)
    if r.status_code in (401, 403):
        raise VpsieRejected(f"{path}: {_message(r)}")
    if r.status_code >= 400:
        raise RuntimeError(f"{path}: {_message(r)}")
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(f"{path}: response was not JSON") from None


async def _post(path: str, body: dict) -> Any:
    token = await _access_token()
    async with httpx.AsyncClient(timeout=90, base_url=BASE) as c:
        r = await c.post(path, headers={"Authorization": f"Bearer {token}"},
                         json=body)
    if r.status_code in (401, 403):
        raise VpsieRejected(f"{path}: {_message(r)}")
    if r.status_code >= 400:
        raise RuntimeError(f"{path}: {_message(r)}")
    try:
        return r.json()
    except ValueError:
        return {}


def _items(payload) -> tuple[list | None, str | None]:
    """Unwrap the VPSie list envelope, or say why it could not be unwrapped.

    Returning [] for an unrecognised shape made every list check report zero
    resources and therefore perfect health.
    """
    if isinstance(payload, list):
        return payload, None
    if not isinstance(payload, dict):
        return None, f"expected a list or object, got {type(payload).__name__}"
    data = payload.get("data", payload)
    if isinstance(data, list):
        return data, None
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return data["items"], None
    present = ", ".join(sorted(data)[:8]) if isinstance(data, dict) else "?"
    return None, (f"unrecognised response envelope — expected a list, "
                  f"data[], or data.items[]. Got: {present}")


def _guard(check: str):
    """Turn the two failure kinds into the two different readings they are."""
    def wrap(exc: BaseException) -> Observation:
        if isinstance(exc, VpsieRateLimited):
            return Observation(check, Health.UNKNOWN, detail=str(exc))
        if isinstance(exc, VpsieRejected):
            text = str(exc)
            if text is NO_CREDS or text == NO_CREDS:
                return Observation(check, Health.UNKNOWN, detail=text)
            return Observation(
                check, Health.FAILING, None, None,
                f"VPSie refused the credentials: {text}. Note VPSie answers "
                f"500 for a disabled or wrong client secret, so this is not "
                f"evidence that VPSie is down. Reissue the app credentials "
                f"in the control panel.")
        return Observation(check, Health.UNKNOWN, detail=redact(exc))
    return wrap


async def check_auth() -> Observation:
    """A token in hand is not a token VPSie accepts.

    `_access_token` serves the JWT from memory or from the disk cache without
    asking VPSie anything, so "credentials accepted" was said about a token
    nobody had shown to VPSie since it was cached (RA-E1). One cheap
    authenticated read proves it, without a fresh login: the login endpoint
    is the rate-limited one.
    """
    cached = bool((_token and _token[1] > time.time() + 300) or _load_cached())
    try:
        await _access_token()
    except Exception as exc:                            # noqa: BLE001
        return _guard("auth")(exc)
    try:
        await _get("/vm", offset=0, limit=1)
    except VpsieRejected as exc:
        if cached:
            # The cached JWT was refused. The credentials themselves were not
            # re-tested (that would burn a rate-limited login), so this says
            # what is known and no more.
            return Observation(
                "auth", Health.UNKNOWN,
                detail=f"VPSie refused the cached token ({exc}); the client "
                       f"credentials were not re-tested, so whether they are "
                       f"still valid is not known")
        return _guard("auth")(exc)
    except Exception as exc:                            # noqa: BLE001
        return Observation(
            "auth", Health.UNKNOWN,
            detail=f"a token is held but no authenticated read completed: "
                   f"{redact(exc)}")
    return Observation("auth", Health.OK, None, None,
                       "VPSie accepted the token on an authenticated read"
                       + (" (token from cache)" if cached else ""))


async def check_instances() -> Observation:
    """Which instances are actually running.

    `state` is the DATACENTER -- "NY2", "LAX", "VA1" -- not the power state.
    Reading it as a status reported all nine instances down, including the
    host ATLAS is running on, at Severity.CRITICAL. That is a page in the
    middle of the night about nothing, and it would have been the first thing
    a reporting channel ever delivered.

    The real fields are `power` (1 = on), `is_active`, `is_suspended` and
    `is_locked`. Suspended is its own reading: a suspended instance is not a
    crash, it is usually billing.
    """
    try:
        payload = await _get("/vm")
    except Exception as exc:                            # noqa: BLE001
        return _guard("instances")(exc)

    items, reason = _items(payload)
    if reason:
        return Observation("instances", Health.UNKNOWN,
                           detail=f"/vm: {reason}")
    if not items:
        return Observation(
            "instances", Health.UNKNOWN, 0.0, "instances",
            "VPSie reports no instances at all, but ATLAS runs on one — this "
            "reading cannot be right")

    def label(vm: dict) -> str:
        return str(vm.get("hostname") or vm.get("identifier") or "unnamed")

    down, suspended, locked = [], [], []
    for vm in items:
        if not isinstance(vm, dict):
            continue
        if vm.get("is_suspended"):
            suspended.append(label(vm))
        elif not (vm.get("power") or vm.get("is_active")):
            down.append(label(vm))
        if vm.get("is_locked"):
            locked.append(label(vm))

    bits = []
    if down:
        bits.append(f"powered off: {', '.join(down[:6])}")
    if suspended:
        bits.append(f"SUSPENDED (usually billing, not a crash): "
                    f"{', '.join(suspended[:6])}")
    if locked:
        bits.append(f"locked: {', '.join(locked[:6])}")

    running = len(items) - len(down) - len(suspended)
    return Observation(
        "instances",
        Health.FAILING if (down or suspended) else Health.OK,
        float(running), "running",
        f"{running} of {len(items)} instance(s) running"
        + ("; " + "; ".join(bits) if bits else ""),
        evidence={"total": len(items), "down": down, "suspended": suspended,
                  "locked": locked})


async def check_resource_ledger() -> Observation:
    """Does the billing view agree with the instance list?

    This once reported "12 servers invisible to every check": the dashboard
    said Server 21, /vm listed 9. Investigated, and the 21 is not twelve
    hidden machines -- /vm returns a `total` field of its own, and it says
    9. The dashboard's activeResources is a lifetime resource counter, and
    the module was comparing a count of one thing to a list of another.
    Two numbers from two endpoints are only comparable when they measure
    the same thing, so this now compares /vm's own total to /vm's own list
    (a pagination gap, which IS a real invisibility) and reports the
    dashboard figure as information rather than as a discrepancy.
    """
    try:
        payload = await _get("/vm")
    except Exception as exc:                            # noqa: BLE001
        return _guard("resource_ledger")(exc)

    items, reason = _items(payload)
    if reason:
        return Observation("resource_ledger", Health.UNKNOWN,
                           detail=f"/vm: {reason}")
    listed = len(items or [])
    total = payload.get("total") if isinstance(payload, dict) else None
    if not isinstance(total, int):
        # Nothing was compared, so nothing was confirmed (RA-E1).
        return Observation(
            "resource_ledger", Health.UNKNOWN, float(listed), "instances",
            f"{listed} instance(s) listed; /vm carried no total to check "
            f"against, so a pagination gap cannot be ruled out")

    gap = total - listed
    return Observation(
        "resource_ledger",
        Health.OK if gap <= 0 else Health.DEGRADED,
        float(total), "instances",
        f"{listed} of {total} instance(s) listed"
        + (f" — {gap} beyond the page ATLAS reads; raise the page size"
           if gap > 0 else ""),
        evidence={"listed": listed, "total": total})


def watching_balance() -> bool:
    """Miro, 2026-09-24: "forget about vpsie balance now". VPSIE_WATCH_BALANCE=0
    turns off every balance measurement: the check and the signal alike."""
    return os.getenv("VPSIE_WATCH_BALANCE", "1").strip() != "0"


async def check_balance() -> Observation:
    """Balance against the monthly charge — runway, not just a number.

    VPSie reports two charges and they disagree by a lot: `monthly_charge`
    (the list price of everything running) and `actual_monthly_charge` (what
    is actually billed, after whatever discount applies). Runway is computed
    from what is actually billed -- that is what drains the balance -- and
    the list price is named beside it. (Until 2026-09-25 it used the larger
    of the two, so a top-up to 791 still read "1.1 months" and paged Miro:
    791 / 694 list instead of 791 / 249 billed = 3.2 months.)

    Money is only ever read here. Topping up is Tier 3 with no override.
    """
    try:
        payload = await _get("/dashboard")
    except Exception as exc:                            # noqa: BLE001
        return _guard("balance")(exc)

    body = payload.get("data", payload) if isinstance(payload, dict) else {}
    balance_data = body.get("balanceData") if isinstance(body, dict) else None
    if not isinstance(balance_data, dict):
        return Observation(
            "balance", Health.UNKNOWN,
            detail=f"/dashboard carried no balanceData object; got: "
                   f"{', '.join(sorted(body)[:8]) if isinstance(body, dict) else '?'}")

    value, bad = number(balance_data, "current_balance",
                        where="/dashboard balanceData")
    if bad or value is None:
        return Observation("balance", Health.UNKNOWN,
                           detail=bad or "no current_balance")

    listed, _ = number(balance_data, "monthly_charge", where="balanceData")
    actual, _ = number(balance_data, "actual_monthly_charge", where="balanceData")
    charge = actual if actual is not None and actual > 0 else (listed or 0.0)

    months = value / charge if charge > 0 else None
    floor = float(os.getenv("VPSIE_RUNWAY_MONTHS", "2"))

    detail = f"balance {value:,.2f}"
    if listed is not None and actual is not None and listed != actual:
        # One basis, stated once. The list price is named only to say it is
        # not used; the arithmetic below divides by what is billed.
        detail += (f", billed {actual:,.2f}/month (basis: the amount actually "
                   f"billed; list price {listed:,.2f} not used)")
    elif charge and (actual is None or actual <= 0):
        # The billed figure is missing: the list price is the only basis.
        # Said, so a reader does not take it for the billed amount (plan D14).
        detail += (f", {charge:,.2f}/month (basis: the list price; the billed "
                   f"amount was not reported, and a discount would make the "
                   f"runway longer)")
    elif charge:
        detail += f", {charge:,.2f}/month"
    if months is None:
        detail += ("; runway not known: no monthly charge was reported, so "
                   "none is computed")
    if months is not None:
        detail += f" = {months:.1f} months of runway"
        if months < floor:
            detail += (". When this runs out the instances suspend, and the "
                       "one this message came from is one of them.")

    return Observation(
        "balance",
        Health.FAILING if value < 0 or (months is not None and months < 1)
        else Health.DEGRADED if months is not None and months < floor
        else Health.OK,
        round(months, 2) if months is not None else value,
        "months" if months is not None else "balance",
        detail,
        evidence={"balance": value, "monthly_listed": listed,
                  "monthly_actual": actual, "months": months})


async def check_protection() -> Observation:
    """Backups and snapshots, together, because the question is one question:
    if an instance is lost tonight, what is there to restore from?"""
    try:
        snaps, snap_reason = _items(await _get("/snapshot"))
        # govpsie backup.go: List is GET /backups; /backup (singular) is the
        # delete endpoint and 404s on GET.
        backups, backup_reason = _items(await _get("/backups"))
    except Exception as exc:                            # noqa: BLE001
        return _guard("protection")(exc)
    if snap_reason or backup_reason:
        return Observation("protection", Health.UNKNOWN,
                           detail=snap_reason or backup_reason)

    # The policy is the promise; the backups are the proof. Both are read,
    # because a policy that covers eight of nine instances looks, from the
    # backup list alone, like a healthy estate with one quiet member.
    policy_note, policy_bad = "", False
    # Set when the promise could not be read. The restore points are still
    # counted, but "OK" would claim a coverage nobody checked (RA-E1).
    policy_unread: str | None = None
    try:
        vms, vm_reason = _items(await _get("/vm", offset=0, limit=50))
        pol = await _get("/backups/policy/all")
        data = pol.get("data") if isinstance(pol, dict) else None
        rows = data.get("rows") if isinstance(data, dict) else None
        if vm_reason or not vms:
            # With no instance list, "covers all 0 instances" was a pass.
            raise ValueError(f"/vm: {vm_reason or 'no instances listed'}")
        if not isinstance(rows, list):
            # An unreadable policy is not "no backup policy exists".
            raise TypeError("/backups/policy/all carried no rows list")
        rows = [r for r in rows if isinstance(r, dict)]
        n_vm = len(vms)
        covered = sum(int(r.get("vmsCount") or 0) for r in rows
                      if not r.get("disabled"))
        if not rows:
            policy_note, policy_bad = "; no backup policy exists", True
        elif covered < n_vm:
            policy_note = (f"; policy covers {covered} of {n_vm} instances")
            policy_bad = True
        else:
            # A full policy with no backup after two days is a policy that
            # is not running, and "first backup pending" must not say OK
            # forever.
            oldest = min((str(r.get("created_on") or "") for r in rows), default="")
            try:
                born = datetime.fromisoformat(oldest.replace("Z", "+00:00"))
                if not (backups or []) and datetime.now(UTC) - born > timedelta(days=2):
                    policy_note, policy_bad = (
                        f"; policy has produced nothing since {born:%d %b}", True)
            except ValueError:
                pass
            names = ", ".join(str(r.get("name")) for r in rows[:3])
            if not policy_bad:
                policy_note = (
                    f"; policy {names}: every {rows[0].get('planEvery')}"
                    f" {rows[0].get('backupPlan')}, keep {rows[0].get('keep')},"
                    f" all {n_vm} instances")
    except Exception as exc:                            # noqa: BLE001
        policy_unread = redact(exc)
        policy_note = f"; policy unreadable: {policy_unread}"

    n_snap, n_backup = len(snaps or []), len(backups or [])
    failed = [str(s.get("identifier") or s.get("name") or "?")
              for s in (snaps or []) if isinstance(s, dict)
              and str(s.get("status", "")).lower() in ("failed", "error")]

    if n_snap == 0 and n_backup == 0:
        return Observation(
            "protection", Health.FAILING, 0.0, "restore points",
            "no snapshots and no backups exist for any instance. If one is "
            "lost tonight there is nothing to restore from — including the "
            "host ATLAS runs on." + policy_note,
            evidence={"snapshots": 0, "backups": 0})

    # No backups yet but a policy that covers everything is a first run
    # still pending, not an estate with no backups. Snapshots hold the line
    # until it lands; if nothing has landed by the next read it says so.
    backups_missing = not n_backup and policy_bad
    degraded = bool(failed or backups_missing or not n_snap or policy_bad)
    return Observation(
        "protection",
        Health.DEGRADED if degraded
        else Health.UNKNOWN if policy_unread is not None
        else Health.OK,
        float(n_snap + n_backup), "restore points",
        f"{n_snap} snapshot(s), {n_backup} backup(s)"
        + (f"; {len(failed)} failed: {', '.join(failed[:5])}" if failed else "")
        + ("; no backups at all" if backups_missing else "")
        + ("; first scheduled backup not landed yet"
           if not n_backup and not policy_bad and policy_unread is None else "")
        + ("; no snapshots at all" if not n_snap else "")
        + policy_note,
        evidence={"snapshots": n_snap, "backups": n_backup, "failed": failed,
                  "policy": policy_note.strip("; ")})


# ------------------------------------------------------------- the one action

async def snapshot_all(reason: str = "") -> ActionResult:
    """A snapshot of every instance. The first write this module has had.

    Verified live 2026-09-22: POST /apps/v2/snapshot/add with
    {vmIdentifier, name, note} answers 202 and the snapshot appears in
    /apps/v2/snapshot a minute later. Nine instances, nine 202s.

    Why THIS is allowed when restart is not: a snapshot is additive. The
    worst it can do is cost disk and a few dollars; it cannot take an
    instance down, and this module runs on one of them. Restart, stop and
    resize stay out, because a mistake there removes the thing doing the
    monitoring, and a hostname guard is weaker than having no action.
    """
    try:
        vms, bad = _items(await _get("/vm"))
    except Exception as exc:                            # noqa: BLE001
        return ActionResult(False, f"could not list instances: {redact(exc)}")
    if bad or not vms:
        return ActionResult(False, bad or "VPSie listed no instances")

    stamp = time.strftime("%Y%m%d-%H%M")
    queued_at = datetime.now(UTC).isoformat(timespec="seconds")
    done, failed = [], []
    # Per instance, by identifier, for the read-back (readback.py, RA-D2):
    # which were targeted, which were queued, which failed at dispatch.
    legs: dict[str, dict[str, str]] = {}
    for vm in vms:
        ident, host = vm.get("identifier"), str(vm.get("hostname") or "?")
        if not ident:
            continue
        try:
            await _post("/snapshot/add", {
                "vmIdentifier": ident, "name": f"atlas-{stamp}",
                "note": (reason or "taken by ATLAS on Miro's instruction")[:200]})
            done.append(host)
            legs[str(ident)] = {"host": host, "status": "queued"}
        except Exception as exc:                        # noqa: BLE001
            failed.append(f"{host}: {redact(exc)}")
            legs[str(ident)] = {"host": host, "status": "failed",
                                "detail": redact(exc)[:160]}
        # Human pace. Nine POSTs in the same second is the shape of a bug,
        # and VPSie queues them anyway.
        await asyncio.sleep(1.5)

    evidence = {"name": f"atlas-{stamp}", "queued_at": queued_at, "instances": legs}
    if failed and not done:
        return ActionResult(False, "no snapshot was taken: " + "; ".join(failed),
                            evidence=evidence)
    detail = f"snapshot queued for {len(done)} instance(s): {', '.join(done)}"
    if failed:
        detail += f". Failed: {'; '.join(failed)}"
    return ActionResult(True, detail, evidence=evidence, rollback_token=stamp)


async def snapshot_dry(reason: str = "") -> ActionResult:
    try:
        vms, bad = _items(await _get("/vm"))
    except Exception as exc:                            # noqa: BLE001
        return ActionResult(False, f"could not list instances: {redact(exc)}")
    if bad or not vms:
        return ActionResult(False, bad or "VPSie listed no instances")
    hosts = [str(v.get("hostname") or "?") for v in vms]
    return ActionResult(
        True, f"would queue a snapshot of {len(hosts)} instance(s): "
              f"{', '.join(hosts)}. Additive; nothing is restarted or "
              f"changed. Each snapshot costs disk until it is deleted.")



class VpsieModule:
    name = "vpsie"
    title = "VPSie"
    # Not gated: an UNKNOWN naming the control panel is more useful than the
    # module vanishing, and this is the account ATLAS itself depends on.
    requires_env = ()
    # Not required to load, but these unlock checks that are
    # UNKNOWN without them. `atlas.cli connect vpsie` says which.
    # Where: VPSie control panel -> API -> Create App
    optional_env = ("VPSIE_CLIENT_ID", "VPSIE_CLIENT_SECRET")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        checks = (
            Check("auth", check_auth, timedelta(hours=1), Severity.HIGH,
                  "the VPSie credentials are accepted", baseline_sigma=None),
            Check("instances", check_instances, timedelta(minutes=10),
                  Severity.CRITICAL, "every instance is running",
                  baseline_sigma=None),
            Check("protection", check_protection, timedelta(hours=6),
                  Severity.HIGH,
                  "something exists to restore an instance from",
                  baseline_sigma=None),
            Check("resource_ledger", check_resource_ledger, timedelta(hours=6),
                  Severity.NORMAL,
                  "every billed server is visible to ATLAS",
                  baseline_sigma=None),
            Check("balance", check_balance, timedelta(hours=6),
                  Severity.HIGH, "enough balance to keep the instances up",
                  baseline_sigma=None),
        )
        # Miro, 2026-09-24: "forget about vpsie balance now". He manages the
        # balance himself; VPSIE_WATCH_BALANCE=0 drops the check entirely --
        # not run, not recorded, not paged. An ack only mutes; disabling the
        # row in `checks` only hides it from the board (run_once runs every
        # registered check), so the off switch has to live here.
        if not watching_balance():
            checks = tuple(c for c in checks if c.name != "balance")
        return checks

    def actions(self):
        # One. Snapshots are additive and cannot take an instance down.
        # Restart, stop and resize stay out: this is the account ATLAS runs
        # in, and a mistake there removes the thing doing the monitoring.
        return (
            Action(
                name="vpsie.snapshot_all",
                tier=Tier.ASK,        # on his word; costs disk, never breaks
                summary="Take a snapshot of every VPSie instance",
                run=snapshot_all, dry_run=snapshot_dry,
                rollback=None, irreversible=False,
                params={"reason": "why, recorded in the snapshot note"},
                max_per_hour=2,
            ),
        )

    async def signals(self):
        out = []

        protection = await check_protection()
        if protection.health is Health.FAILING:
            out.append(Signal(
                title="VPSie: nothing to restore any instance from",
                body=protection.detail + "\n\nATLAS will not create these — "
                     "a snapshot is a write to the account it runs in, and "
                     "this module has no actions. Taking one is a few clicks "
                     "in the control panel per instance.",
                severity=Severity.HIGH))

        # The same off switch as the check. signals() measured the balance
        # itself, so VPSIE_WATCH_BALANCE=0 dropped the check but not this:
        # "VPSie: not much balance left" still reached #vpsie on 2026-09-25,
        # a day after Miro said to forget the balance.
        balance = await check_balance() if watching_balance() else None
        if balance is not None and balance.health in (Health.DEGRADED, Health.FAILING):
            out.append(Signal(
                title="VPSie: not much balance left",
                body=balance.detail + "\n\nATLAS cannot top this up — money "
                     "out is permanently denied and there is no override.",
                severity=Severity.HIGH))

        ledger = await check_resource_ledger()
        if ledger.health is Health.DEGRADED:
            out.append(Signal(
                title="VPSie: instances beyond the page ATLAS reads",
                body=ledger.detail + "\n\nAn instance ATLAS cannot list is "
                     "one it cannot watch, and it bills anyway.",
                severity=Severity.NORMAL))

        return tuple(out)

    def brief(self) -> str:
        return ("VPSie: instances running, restore points, billed vs "
                "visible servers, balance runway")


MODULE = VpsieModule()
