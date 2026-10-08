"""Read-back for `vpsie.snapshot_all` (RA-D1, RA-D2).

`snapshot_all` answers ok when at least one instance was queued, so a
partial run read as a success. This reads `/snapshot` back and checks, per
targeted instance, that a snapshot named `atlas-<stamp>` exists for it:

  * failed at dispatch (the run's own evidence)   -> contradicted, named;
  * listed with a failed/error status             -> contradicted, named;
  * listed                                        -> verified;
  * not listed yet, inside GRACE of the queue time -> unknown (VPSie lists a
    new snapshot about a minute later);
  * not listed past GRACE, while at least one of this run's snapshots IS
    listed                                        -> contradicted, named;
  * not listed and none of this run's are listed  -> unknown: whether
    `/snapshot` returns the whole list (pagination, ordering) has not been
    read from production, so an empty answer proves nothing.

Any instance contradicted makes the whole run contradicted, listing each
failure. A listing whose rows name no VM is unknown, never verified. Reads
only (GET). See docs/verification.md.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    combine,
    contradicted,
    reconciler,
    unknown,
    verified,
    verifier,
)
from . import VpsieRateLimited, VpsieRejected, _get, _items

GRACE = timedelta(minutes=10)
_VM_KEYS = ("vmIdentifier", "vm_identifier", "vmId", "vm_id")


def _vm_of(snap: dict[str, Any]) -> str | None:
    for key in _VM_KEYS:
        if snap.get(key):
            return str(snap[key])
    vm = snap.get("vm")
    if isinstance(vm, dict) and vm.get("identifier"):
        return str(vm["identifier"])
    return None


async def _snapshots() -> tuple[list[dict[str, Any]] | None, Verification | None]:
    try:
        rows, bad = _items(await _get("/snapshot"))
    except (VpsieRejected, VpsieRateLimited) as exc:
        cls = FailureClass.AUTH if isinstance(exc, VpsieRejected) else FailureClass.TRANSIENT
        return None, unknown(f"could not list snapshots: {exc}", cls)
    except Exception as exc:                            # noqa: BLE001
        from .._http import explain
        return None, unknown(f"could not list snapshots: {explain(exc)}",
                             FailureClass.TRANSIENT)
    if bad:
        return None, unknown(f"snapshot list unreadable: {bad}")
    return [r for r in rows or [] if isinstance(r, dict)], None


def _leg(ident: str, host: str, mine: list[dict[str, Any]], overdue: bool
         ) -> Verification:
    evidence = {"host": host, "vm": ident}
    if not mine:
        if overdue:
            return contradicted(f"{host}: no snapshot listed", **evidence)
        return unknown(f"{host}: not listed yet (or the list is incomplete)",
                       FailureClass.STALE, **evidence)
    snap = mine[0]
    status = str(snap.get("status") or "").lower()
    evidence.update(snapshot=snap.get("identifier") or snap.get("id"),
                    name=snap.get("name"), status=status or None)
    if status in ("failed", "error"):
        return contradicted(f"{host}: snapshot {status}", **evidence)
    return verified(**evidence)


@verifier("vpsie.snapshot_all")
async def verify_snapshot_all(params: dict[str, Any], result: ActionResult
                              ) -> Verification:
    ev = result.evidence or {}
    name = str(ev.get("name") or (f"atlas-{result.rollback_token}"
                                  if result.rollback_token else ""))
    targets = ev.get("instances")
    if not name or not isinstance(targets, dict) or not targets:
        return unknown("the run recorded no per-instance targets")
    try:
        queued = datetime.fromisoformat(str(ev.get("queued_at")))
        overdue = datetime.now(UTC) - queued > GRACE
    except (TypeError, ValueError):
        overdue = False
    snaps, failed = await _snapshots()
    legs: dict[str, Verification] = {}
    named = [s for s in snaps or [] if str(s.get("name") or "") == name]
    attributable = all(_vm_of(s) for s in named)
    # Absence proves something only when the list shows this run at all.
    overdue = overdue and bool(named)
    for ident, info in targets.items():
        host = str((info or {}).get("host") or ident)
        if (info or {}).get("status") == "failed":
            legs[host] = contradicted(
                f"{host}: failed at dispatch: {(info or {}).get('detail', '')}".strip(),
                host=host, vm=ident)
            continue
        if failed is not None:
            legs[host] = failed
            continue
        if not attributable:
            legs[host] = unknown(f"{host}: the snapshot list names no VM per row",
                                 host=host, vm=ident)
            continue
        legs[host] = _leg(ident, host, [s for s in named if _vm_of(s) == ident], overdue)
    return combine(legs)


def _stamp_of(name: str) -> datetime | None:
    if not name.startswith("atlas-"):
        return None
    try:
        return datetime.strptime(name[len("atlas-"):], "%Y%m%d-%H%M").replace(tzinfo=UTC)
    except ValueError:
        return None


@reconciler("vpsie.snapshot_all")
async def reconcile_snapshot_all(params: dict[str, Any], started: datetime
                                 ) -> Verification:
    """No finish row: per instance, a snapshot named `atlas-<stamp>` stamped
    at or after the start minute (the container runs in UTC)."""
    try:
        vms, bad = _items(await _get("/vm"))
    except Exception as exc:                            # noqa: BLE001
        from .._http import explain
        return unknown(f"could not list instances: {explain(exc)}",
                       FailureClass.TRANSIENT)
    if bad or not vms:
        return unknown(bad or "VPSie listed no instances")
    snaps, failed = await _snapshots()
    if failed is not None:
        return failed
    floor = started.replace(second=0, microsecond=0)
    ours = [s for s in snaps or []
            if (when := _stamp_of(str(s.get("name") or ""))) and when >= floor]
    if ours and not all(_vm_of(s) for s in ours):
        return unknown("the snapshot list names no VM per row")
    overdue = datetime.now(UTC) - started > GRACE and bool(ours)
    legs = {}
    for vm in vms:
        ident = str(vm.get("identifier") or "")
        if not ident:
            continue
        host = str(vm.get("hostname") or ident)
        legs[host] = _leg(ident, host, [s for s in ours if _vm_of(s) == ident], overdue)
    return combine(legs)
