"""Read-back for three Guardian write actions (RA-D1). Reads only, with
the READ-scope token, the same `GET /v1/...` routes the module already reads:

  guardian.alert_op        GET /v1/alerts/{key}      status matches the op
  guardian.rescan          GET /v1/jobs/{id}         the job exists, not failed
  guardian.contain         no read: v1 has no containment route, so a
                           containment can never exist (contradicted)

**That tenant only.** The write went out on `GUARDIAN_WRITE_TOKEN` and the
read-back uses `GUARDIAN_API_TOKEN`. If those two tokens belong to different
Guardian accounts, the read-back would describe another tenant, so before
anything is read, `GET /v1/whoami` is asked with each token (the write
token's is `incident.write_token_account`, since only incident.py holds that
token) and both must name the same account. A mismatch
or an unreadable whoami is unknown, class security, never verified.

Evidence is enums and ids only, through `incident.safe_*`: an alert key is
sensor data a beacon's attacker may have chosen, and the audit chain keeps
what it is given.

`guardian.mark_untrusted` has no verifier on purpose: Miro ruled (2026-09-28)
that ATLAS only SUGGESTS trust changes, and that write action is being
removed. See docs/verification.md.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    contradicted,
    reconciler,
    unknown,
    verified,
    verifier,
)

OP_STATUS = {"ack": "acked", "snooze": "snoozed", "resolve": "resolved",
             "reopen": "open"}
# The hub's own Queue statuses (hub/guardian_hub/models.py and
# schemas_api.Job): queued|scheduled|running|done|failed|missed.
JOB_LIVE = frozenset({"queued", "scheduled", "running", "done"})
JOB_DEAD = frozenset({"failed", "missed"})


async def _read_account() -> str | None:
    from . import _api
    data, reason = await _api("/whoami")
    if reason or not isinstance(data, dict):
        return None
    return str(data.get("account_slug") or data.get("account") or "") or None


async def same_tenant() -> tuple[str | None, Verification | None]:
    """(account, None) when the read and write tokens are one account;
    (None, unknown) otherwise. The write token's whoami is asked in
    `incident.write_token_account`, the one file that holds that token."""
    from . import _token
    from .incident import _write_token, write_token_account
    if not _token() or not _write_token():
        return None, unknown("the Guardian read or write token is not set",
                             FailureClass.DISABLED)
    mine = await _read_account()
    theirs, _why = await write_token_account()
    if mine is None or theirs is None:
        return None, unknown("could not confirm which Guardian account each token "
                             "belongs to", FailureClass.SECURITY)
    if mine != theirs:
        return None, unknown("the read and write tokens belong to different Guardian "
                             "accounts, so a read-back would describe another tenant",
                             FailureClass.SECURITY)
    return mine, None


def _fail(reason: str, what: str, **evidence: Any) -> Verification:
    if "HTTP 404" in reason:
        return contradicted(f"{what} is not on this account", **evidence)
    cls = FailureClass.AUTH if ("401" in reason or "403" in reason) else \
        FailureClass.TRANSIENT
    return unknown(f"could not read {what}", cls, **evidence)


async def _alert(params: dict[str, Any]) -> Verification:
    from . import _api
    from .incident import ALERT_STATUSES
    op = str(params.get("op") or "")
    key = str(params.get("alert_key") or "")
    want = OP_STATUS.get(op)
    if want is None or not key:
        return unknown("the run names no alert and op", FailureClass.VALIDATION)
    account, refused = await same_tenant()
    if refused:
        return refused
    data, reason = await _api(f"/alerts/{quote(key, safe='')}")
    evidence = {"account": account, "op": op, "expected": want}
    if reason:
        return _fail(reason, "the alert", **evidence)
    status = data.get("status") if isinstance(data, dict) else None
    evidence["status"] = status if status in ALERT_STATUSES else None
    if evidence["status"] is None:
        return unknown("the hub named no recognised status for the alert", **evidence)
    if status != want:
        return contradicted(f"the alert is {status}, not {want}", **evidence)
    return verified(**evidence)


@verifier("guardian.alert_op")
async def verify_alert_op(params: dict[str, Any], result: ActionResult) -> Verification:
    return await _alert(params)


@reconciler("guardian.alert_op")
async def reconcile_alert_op(params: dict[str, Any], started: datetime) -> Verification:
    return await _alert(params)


@verifier("guardian.rescan")
async def verify_rescan(params: dict[str, Any], result: ActionResult) -> Verification:
    from . import _api
    from .incident import safe_job_id
    job = safe_job_id({"id": (result.evidence or {}).get("job")})
    if not job:
        return unknown("the run recorded no job id")
    account, refused = await same_tenant()
    if refused:
        return refused
    data, reason = await _api(f"/jobs/{quote(job, safe='')}")
    evidence = {"account": account, "job": job}
    if reason:
        return _fail(reason, f"job {job}", **evidence)
    status = str(data.get("status") or "") if isinstance(data, dict) else ""
    ok = data.get("ok") if isinstance(data, dict) else None
    evidence.update(status=status if status in JOB_LIVE | JOB_DEAD else None,
                    ok=ok if isinstance(ok, bool) else None)
    if str((data or {}).get("id") or "") != job:
        return contradicted("the hub answered for a different job", **evidence)
    if status in JOB_DEAD or ok is False:
        return contradicted(f"scan job {job} is {status or 'not ok'}", **evidence)
    if status in JOB_LIVE:
        return verified(**evidence)
    return unknown("the hub named a job status ATLAS does not know", **evidence)


def _never_contained(params: dict[str, Any]) -> Verification:
    from .incident import CONTAIN_UNAVAILABLE, DENIED_KINDS, KINDS
    kind = params.get("kind")
    return contradicted(CONTAIN_UNAVAILABLE, hub_route="none",
                        kind=kind if kind in KINDS or kind in DENIED_KINDS else None)


@verifier("guardian.contain")
async def verify_contain(params: dict[str, Any], result: ActionResult) -> Verification:
    return _never_contained(params)


@reconciler("guardian.contain")
async def reconcile_contain(params: dict[str, Any], started: datetime) -> Verification:
    """`contain` never reaches the hub, so a run cut short changed nothing."""
    return _never_contained(params)
