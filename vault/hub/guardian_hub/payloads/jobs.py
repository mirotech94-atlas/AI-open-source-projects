"""Jobs / Activity payload: every row of the sensor work queue (`Queue`),
rendered so the owner can see what Vault is doing or has just done
without reading a raw `path` + JSON `body` (spec: the owner's own words were
"a place where i could find all the queues and jobs that are happening").

One queue row is one job. The sensor only ever drains four `path`s
(`sensor/guardian_sensor/runner.run_queue`): `/api/fix`, `/api/speedtest`,
`/api/run`, `/api/reboot-plan`. Everything below is keyed off that closed
set, plus `remedy.FIX_LABELS` for the fix catalogue's own human names --
ported rather than duplicated, same as `remedy.py` itself does for the
sensor's fix table.
"""

import datetime as dt

from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..chat.tiers import TIER3_FIXES
from ..remedy import FIX_LABELS
from ..scans import parse_ts
from ..schemas_dash import JobRow, JobsPayload
from ._util import _lax_cutoff

#: How long a job of this kind normally takes to run. A `running` row past
#: this age is flagged `stuck` rather than shown as ordinary progress -- this
#: is the whole point of the page (spec: a `/api/speedtest` sitting in
#: `running` for 40+ minutes with nothing anywhere saying so).
#:
#: `/api/fix` looks up its own action below (`_FIX_EXPECTED_SECONDS`); these
#: three cover the other paths, and also its fallback.
EXPECTED_SECONDS: dict[str, int] = {
    "/api/speedtest": 180,       # a real speed test finishes in well under a minute
    "/api/run": 300,             # a full LAN+host scan
    "/api/reboot-plan": 300,
}

#: `/api/fix` action -> expected seconds. An apt upgrade genuinely takes
#: minutes; most other repairs are a handful of seconds over one SSH hop.
#: Unlisted actions fall back to `_FIX_DEFAULT_SECONDS`.
_FIX_EXPECTED_SECONDS: dict[str, int] = {
    "full_upgrade": 1800,
    "security_updates": 900,
    "install_debsecan": 300,
    "reboot": 300,
}
_FIX_DEFAULT_SECONDS = 180

#: `path -> kind`, the value the filter dropdown and `JobRow.kind` use.
#: "scan" and "reboot-plan" read better than the raw `run`/`reboot-plan`
#: path segments in a filter control.
KIND_BY_PATH: dict[str, str] = {
    "/api/fix": "fix",
    "/api/speedtest": "speedtest",
    "/api/run": "scan",
    "/api/vuln-scan": "vulnerability-scan",
    "/api/deep-dive": "deep-dive",
    "/api/reboot-plan": "reboot-plan",
}

#: Every kind of job that is safe to run again with the exact same
#: arguments, no fresh confirmation needed: nothing here is disruptive, and
#: a retry queues the identical body a second time. `/api/fix` is judged
#: action-by-action instead (see `_fix_retryable`) because some of its
#: actions ARE disruptive.
_ALWAYS_RETRYABLE_KINDS = {"speedtest", "scan", "vulnerability-scan", "deep-dive"}


def job_kind(path: str) -> str:
    return KIND_BY_PATH.get(path, (path.rsplit("/", 1)[-1] or "job"))


def _fix_action_label(body: dict, result: dict | None) -> str:
    """Prefer the sensor's own `label` off the fix result (`fix.apply_fix`'s
    `label, cmd, tmo = FIX_ACTIONS[action]`) -- it is the exact word the fix
    log and `remedy.FIX_LABELS` already use, so a fix queued before this
    action existed in `FIX_LABELS` (or with a name that has since changed)
    still reads correctly once it has a result. Falls back to the ported
    table, then to the raw action name, for a row that has no result yet."""
    action = body.get("action") or ""
    label = (result or {}).get("label") or FIX_LABELS.get(action) or action.replace("_", " ")
    return label or "repair"


def human_label(path: str, body: dict, result: dict | None) -> str:
    """What this job is, in plain English -- never the raw `path`."""
    body = body or {}
    if path == "/api/fix":
        return _fix_action_label(body, result)
    if path == "/api/speedtest":
        return "speed test"
    if path == "/api/run":
        return "network scan"
    if path == "/api/reboot-plan":
        return "planned reboot"
    if path == "/api/vuln-scan":
        return "vulnerability scan"
    if path == "/api/deep-dive":
        return "device deep dive"
    return path.lstrip("/").replace("-", " ").replace("/", " ") or "job"


def job_target(path: str, body: dict) -> str:
    """The host/site this job runs against, or "" for one that is not
    scoped to a single host (a whole-network scan or speed test)."""
    return (body or {}).get("host") or (body or {}).get("target") or (body or {}).get("mac") or ""


def expected_seconds(path: str, body: dict) -> int:
    if path == "/api/fix":
        return _FIX_EXPECTED_SECONDS.get((body or {}).get("action") or "", _FIX_DEFAULT_SECONDS)
    return EXPECTED_SECONDS.get(path, _FIX_DEFAULT_SECONDS)


#: The role each kind of job's own route demands. Retry re-queues a job's
#: exact body, so it has to demand the same role the route that created it
#: does -- otherwise Retry is a way around the gate. `/api/fix` and
#: `/api/reboot-plan` are `ADMIN` in `routers/write.py`; `/api/run` and
#: `/api/speedtest` are `OPERATOR`.
ROLE_BY_PATH: dict[str, str] = {
    "/api/fix": "admin",
    "/api/reboot-plan": "admin",
    "/api/run": "operator",
    "/api/vuln-scan": "admin",
    "/api/deep-dive": "operator",
    "/api/speedtest": "operator",
}


def role_required(path: str) -> str:
    """The role needed to queue this path, defaulting to the strictest.

    An unknown path defaults to `admin` on purpose: a job kind added later
    without a row here should refuse a retry rather than quietly allow one.
    """
    return ROLE_BY_PATH.get(path, "admin")


def is_retryable(path: str, body: dict) -> bool:
    """Whether re-queuing this exact job is something Vault may offer on
    its own -- never for a disruptive `TIER3_FIXES` action, which is only
    ever run after a fresh, explicit confirmation (see `routers/write.fix`);
    a plain retry has no confirmation to spend."""
    kind = job_kind(path)
    if kind in _ALWAYS_RETRYABLE_KINDS:
        return True
    if kind == "fix":
        return ((body or {}).get("action") or "") not in TIER3_FIXES
    return False


def _why_failed(label: str, target: str, out: str) -> str:
    """A failed job's reason, in Vault's own first-person voice (the same
    voice `remedy.py` writes every alert explanation in) -- never a bare
    stack trace or raw `out` blob with no framing."""
    where = f" on {target}" if target else ""
    reason = (out or "").strip() or "it did not say why."
    return f"I tried to run {label}{where}, but it failed: {reason}"


#: How long a job may sit in `queued` before nobody is coming for it.
#:
#: The sensor polls the queue every ~10 seconds on its own thread, so a job
#: still waiting minutes later does not mean a slow sensor, it means no sensor
#: is listening to this tenant at all. Five minutes is many times the poll
#: interval and still well inside the old once-per-scan behaviour, so an
#: out-of-date sensor is not flagged for being merely old.
QUEUED_STUCK_SECONDS = 300


def _duration_s(queued_at: dt.datetime, finished_at: dt.datetime | None, status: str,
                now: dt.datetime) -> int | None:
    """How long this job has taken, or waited.

    A `queued` row used to return None here, so the page showed no age for it
    at all. Combined with a stuck check that only looked at `running`, a job no
    sensor ever collected was completely invisible as a problem -- which is the
    exact failure this page exists to surface. A queued row now reports how
    long it has been waiting.
    """
    end = finished_at if finished_at is not None else now
    return max(0, int((end - queued_at).total_seconds()))


def job_row(q: "m.Queue", now: dt.datetime) -> JobRow:
    body = q.body or {}
    result = q.result or {}
    label = human_label(q.path, body, result)
    target = job_target(q.path, body)
    duration = _duration_s(q.started_at or q.ts, q.finished_at, q.status, now)
    waiting = max(0, int(((q.started_at or q.finished_at or now) - q.ts).total_seconds()))
    running = _duration_s(q.started_at, q.finished_at, q.status, now) if q.started_at else None
    # Stuck in either direction: running far longer than this kind of job
    # takes, or queued so long that no sensor can be listening for it.
    stuck = duration is not None and (
        (q.status == "running" and duration > expected_seconds(q.path, body))
        or (q.status == "queued" and duration > QUEUED_STUCK_SECONDS)
    )
    out = str(result.get("out") or "")
    return JobRow(
        id=q.id, kind=job_kind(q.path), label=label, target=target, who=q.who,
        status=q.status, queued_at=q.ts.isoformat(timespec="seconds"),
        finished_at=q.finished_at.isoformat(timespec="seconds") if q.finished_at else None,
        sensor_id=q.sensor_id, started_at=q.started_at.isoformat() if q.started_at else None,
        not_before=q.not_before.isoformat() if q.not_before else None,
        expires_at=q.expires_at.isoformat() if q.expires_at else None,
        wait_reason=result.get("verification_note") or q.wait_reason or "", waiting_s=waiting, running_s=running,
        duration_s=duration, ok=(q.status == "done") if q.status in ("done", "failed") else None,
        out=out[:2000],
        why_failed=_why_failed(label, target, out) if q.status == "failed" else "",
        stuck=stuck, retryable=is_retryable(q.path, body) if q.status == "failed" else False,
    )


#: How many rows the page ever needs at once -- generous for a household/small
#: business queue (`Assets`'s heaviest list caps lower), and bounded so a
#: tenant that has queued thousands of jobs over months cannot turn this into
#: an unbounded read every 3-5 s.
MAX_ROWS = 500


def _who_from_by(by: str) -> str:
    """`Fix.by` as the Activity table's "Who" column -- Vault's own first
    -person name for its own unattended work, a plain username for
    everything a person asked for, "hub:chat:" included."""
    if by == "auto":
        return "Vault"
    if by.startswith("hub:chat:"):
        return by[len("hub:chat:"):]
    if by.startswith("hub:"):
        return by[len("hub:"):]
    return by


def _autofix_row(f: "m.Fix") -> JobRow:
    """One unattended repair (`fix.remediate()`, `Fix.by == "auto"`) as an
    Activity row -- it has no `Queue` row of its own (see `build()`'s
    docstring for why), so without this it is entirely invisible on the one
    page whose purpose is "every job Vault is running or has just run",
    while Overview's own Fix Log shows the same repair in its history.

    Always `done` by construction: `remediation.jsonl`, and the `Fix` row
    ingested from its tail, are written only after the command has already
    finished running (`fix.apply_fix`). There is no "auto job in progress"
    state to show -- `stuck`/`retryable` do not apply to something that is
    not queued and cannot be re-queued from here."""
    label = f.label or FIX_LABELS.get(f.action, f.action.replace("_", " "))
    return JobRow(
        id=f"auto:{f.id}", kind="fix", label=label, target=f.host, who=_who_from_by(f.by),
        status="done" if f.ok else "failed", queued_at=f.ts, finished_at=f.ts, duration_s=None,
        ok=f.ok, out=(f.out or "")[:2000],
        wait_reason="Recovery verified by a later target check" if f.verified_at else "Command completed; recovery not yet verified" if f.ok else "",
        why_failed=_why_failed(label, f.host, f.out or "") if not f.ok else "",
        stuck=False, retryable=False,
    )


def build(db: DbSession, tenant_id: str, limit: int = MAX_ROWS) -> JobsPayload:
    now = dt.datetime.now(dt.timezone.utc)
    queued = (
        db.query(m.Queue)
        .filter_by(tenant_id=tenant_id)
        .order_by(m.Queue.ts.desc())
        .limit(limit)
        .all()
    )
    # Only `by == "auto"`: a fix dispatched through the dashboard (any other
    # `by`) already has a real `Queue` row -- `apply_fix` appends to
    # `remediation.jsonl` regardless of how it was invoked, so counting every
    # `Fix` row here would show that same fix a second time.
    autofixed = (
        db.query(m.Fix)
        .filter_by(tenant_id=tenant_id, by="auto")
        .order_by(m.Fix.ts.desc())
        .limit(limit)
        .all()
    )
    reports = (
        db.query(m.Report)
        .filter_by(tenant_id=tenant_id)
        .order_by(m.Report.created.desc())
        .limit(limit)
        .all()
    )
    rows = ([job_row(q, now) for q in queued] + [_autofix_row(f) for f in autofixed]
            + [_report_row(r) for r in reports])
    _link_scans(db, tenant_id, [(row, q) for row, q in zip(rows, queued) if row.kind == "scan"])
    rows.sort(key=lambda r: r.queued_at, reverse=True)
    return JobsPayload(jobs=rows[:limit])


def _report_row(r: "m.Report") -> JobRow:
    """A report someone built or a schedule produced, as an Activity row.

    Reports are not queue jobs -- they render in the request -- so without
    this the one thing an owner most often makes on purpose never appeared in
    their own activity history. The row opens the report it names."""
    created = r.created.isoformat() if r.created else ""
    return JobRow(
        id=f"report:{r.id}", kind="report", label=r.title or "report",
        target=f"last {r.period_days} days" if r.period_days else "",
        # Manual builds (dashboard or chat) are `kind == "manual"`; the
        # scheduler stamps its period instead (`daily`, `weekly`, ...).
        who="" if (r.kind or "manual") == "manual" else "schedule",
        status="done", queued_at=created, finished_at=created, ok=True,
        report_id=r.id,
    )


def _link_scans(db: DbSession, tenant_id: str, pairs) -> None:
    """Point each finished scan request at the scan that answered it.

    The request's queue row records only "scan requested": the sensor runs
    the scan and pushes it the ordinary way, and nothing ties the two. So the
    scan a row opens is the first one received AFTER the request finished,
    from the same sensor when the row names one -- which is what the request
    produced, unless the sensor was about to send a scheduled one anyway, and
    then that scan is just as current. The drawer says "the scan that
    followed this request", not "this request's scan", for that reason.

    One query for every row: `(id, ts, sensor_id)` of the scans since the
    oldest request, never the scans' `raw` payloads.
    """
    done = [(row, q) for row, q in pairs if q.status == "done" and q.finished_at]
    if not done:
        return
    oldest = min(q.finished_at for _, q in done)
    if oldest.tzinfo is None:
        oldest = oldest.replace(tzinfo=dt.timezone.utc)
    scans = []
    for sid, ts, sensor in (db.query(m.Scan.id, m.Scan.ts, m.Scan.sensor_id)
                            .filter(m.Scan.tenant_id == tenant_id,
                                    m.Scan.ts >= _lax_cutoff(oldest)).all()):
        when = parse_ts(ts)
        if when is not None:
            scans.append((when, sid, sensor))
    scans.sort()
    for row, q in done:
        finished = q.finished_at if q.finished_at.tzinfo else q.finished_at.replace(tzinfo=dt.timezone.utc)
        for when, sid, sensor in scans:
            if when >= finished and (not q.sensor_id or sensor == q.sensor_id):
                row.scan_id = sid
                break
