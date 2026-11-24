"""qa -- the QA agent's checks, and the evidence the hard gate reads.

Every gated action (kernel/qa_gate.py) needs a standing pass in
`qa_results` for its exact content. This module writes those rows. All its
actions are Tier 1: each writes only ATLAS's own tables, has a dry run that
lists the checks it would run and reads nothing outside, and a rollback that
supersedes its row (it stops counting as a pass) or cancels the task it
raised. Nothing is deleted.

Actions:
- `qa.check_draft(kind, ref)`: the words check over a stored draft
  (`agency_outreach:<id>`, `community_posts:<id,...>`, `reddit_drafts:<id>`,
  `academy_comment_drafts:<id>`);
- `qa.check_site(slug)`: the launch check over the files a deploy would put
  live (the gate's pass for `agency.deploy_site` / `deploy_demo`), plus,
  for a site already live, the live launch check, PageSpeed when keyed and
  desktop + iPhone screenshots through the qa-browser service -- each of
  those UNKNOWN when it cannot run, recorded and never counted as a pass;
- `qa.security_scan(slug)`: passive only (security.py);
- `qa.verify_runs()`: hourly; every scheduled window due in the last day
  against `schedule_runs`; a miss is a remediation task, never a retry;
- `qa.autopilot_cycle()`: diagnose, fix by a registered Tier 1 action or ask once
  (autopilot.py);
- `qa.create_remediation(...)`: one task on the local board, deduped.

Watches: `gate_health` (QA results in the last day) and `open_remediation`
(open remediation tasks: DEGRADED is the alert). UNKNOWN when unreadable.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    FailureClass,
    Health,
    Observation,
    Severity,
    Tier,
)
from .._http import explain
from . import autopilot, browser, security, store, words

DRAFT_CHECKS = ("words: $100 / guaranteed / #1", "words: unsourced numbers",
                "signature (assistant, never Miro)", "CAN-SPAM (email)", "STOP (sms)",
                "Disclosure line (reddit)")
SITE_CHECKS = ("launch check over the files a deploy would put live",
               "live launch check (when live)", "PageSpeed (when keyed)",
               "desktop + iPhone screenshots (qa-browser)")
SECURITY_CHECKS = ("allowlist + public address before any socket", "TLS version + expiry",
                   "security headers", "exposed files: " + " ".join(security.EXPOSED_PATHS),
                   "<script src> versions", f"at most {security.MAX_REQUESTS} GETs")
RUNS_LATE = timedelta(hours=2)
RUNS_CHECK = ("every recorded window due in the last day has a done (or running) "
              "schedule_runs row")
REF_PREFIXES = ("agency_outreach:", "community_posts:", "reddit_drafts:",
                "academy_comment_drafts:", "bizops_onboardings:", "bizops_invoices:",
                "bizops_reminders:", "content_drafts:", "seo_changes:",
                "research_client_reports:")
_BIZOPS_REFS = {"bizops_onboardings": ("bizops.send_onboarding_email", "onboarding_id"),
                "bizops_invoices": ("bizops.send_invoice", "invoice_id"),
                "bizops_reminders": ("bizops.send_payment_reminder", "reminder_id"),
                "research_client_reports": ("research.send_client_report",
                                            "report_id")}


def _refused(why: str) -> ActionResult:
    return ActionResult(False, why, failure_class=FailureClass.VALIDATION)


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()
                          ).hexdigest()


async def rollback(token: str) -> ActionResult:
    """`qa:<id>` supersedes a result; `task:<token>` cancels a task raised."""
    kind, _, rest = str(token).partition(":")
    if kind == "qa" and rest.isdigit():
        done = await store.supersede(int(rest))
        return ActionResult(True, f"QA result {rest} superseded" if done
                            else f"QA result {rest} was already superseded")
    if kind == "task" and rest:
        from ..projects import store as board
        try:
            return ActionResult(True, await board.restore(rest))
        except board.Invalid as exc:
            return _refused(str(exc))
    return _refused("not a QA rollback token")


# ---------------------------------------------------------------- drafts

async def _draft_subject(kind: str, ref: str):  # type: ignore[no-untyped-def]
    from ...kernel import qa_gate
    if kind not in words.KINDS:
        raise ValueError(f"kind must be one of {', '.join(words.KINDS)}")
    head, _, rest = (ref or "").partition(":")
    if head == "agency_outreach" and rest.isdigit():
        subject = await qa_gate.RESOLVERS["agency.send_outreach"]({"outreach_id": rest})
    elif head == "community_posts" and rest:
        try:
            ids = [int(i) for i in rest.split(",")]
        except ValueError:
            raise ValueError("community_posts ids must be numbers") from None
        subject = await qa_gate.RESOLVERS["community.schedule_youtube_shorts"](
            {"post_ids": ids})
    elif head == "reddit_drafts" and rest.isdigit():
        subject = await qa_gate.RESOLVERS["reddit.submit_post"]({"draft_id": rest})
    elif head == "academy_comment_drafts" and rest.isdigit():
        subject = await qa_gate.RESOLVERS["academy.send_comment_reply"](
            {"draft_id": rest})
    elif head == "content_drafts" and rest.isdigit():
        subject = await qa_gate.RESOLVERS["seo.publish_blog_post"]({"draft_id": rest})
    elif head == "seo_changes" and rest.isdigit():
        subject = await qa_gate.RESOLVERS["seo.apply_change"]({"change_id": rest})
    elif head in _BIZOPS_REFS and rest.isdigit():
        action, key = _BIZOPS_REFS[head]
        subject = await qa_gate.RESOLVERS[action]({key: rest})
    else:
        raise ValueError(f"ref must start with one of {', '.join(REF_PREFIXES)}")
    if subject is None:
        raise ValueError(f"nothing stored under {ref}")
    if subject.words_kind != kind:
        raise ValueError(f"{ref} is a {subject.words_kind} draft, not {kind}")
    return subject


async def check_draft_dry(kind: str, ref: str) -> ActionResult:
    try:
        subject = await _draft_subject(kind, ref)
    except ValueError as exc:
        return _refused(str(exc))
    return ActionResult(True, f"would run the words check on {subject.ref}",
                        evidence={"checks": list(DRAFT_CHECKS), "sha": subject.sha})


async def check_draft(kind: str, ref: str) -> ActionResult:
    from ...kernel import qa_gate
    try:
        subject = await _draft_subject(kind, ref)
    except ValueError as exc:
        return _refused(str(exc))
    failures = words.refusals(subject.text or "", kind)
    rid = await store.record("draft", subject.ref, subject.sha, not failures, failures,
                             {"words_kind": kind, "checks": ["words"]})
    if failures:
        await qa_gate.remediate("qa.check_draft", subject.ref, subject.sha, failures)
    return ActionResult(True, (f"QA passed {subject.ref}" if not failures else
                               f"QA failed {subject.ref}: " + "; ".join(failures[:5])),
                        evidence={"result_id": rid, "passed": not failures,
                                  "failures": failures},
                        rollback_token=f"qa:{rid}")


# ------------------------------------------------------------------ sites

async def check_site_dry(slug: str) -> ActionResult:
    from ...kernel import qa_gate
    try:
        got = await qa_gate.site_subject(str(slug))
    except (OSError, ValueError) as exc:
        return _refused(f"the files for {slug} cannot be read: {explain(exc)}")
    if got is None:
        return _refused(f"no site {slug!r}")
    return ActionResult(True, f"would check {got[0].ref} at sha {got[0].sha[:12]}",
                        evidence={"checks": list(SITE_CHECKS)})


async def _disk_failures(ref: str, site: dict[str, Any], root: Any) -> tuple[list[str],
                                                                            list[str]]:
    from ..agency.demo_host import demo_audit, hostname_of
    from ..agency.launchcheck import launch_audit
    if ref.startswith("demo:"):
        return list(await asyncio.to_thread(demo_audit, root, hostname_of(site))), []
    report = await asyncio.to_thread(launch_audit, root)
    issues = [f"launch check {i.number} {i.name}: {i.evidence}"[:300] for i in report.issues]
    client = [f"{i.number} {i.name}" for i in report.needs_client]
    return issues, client


async def _live(site: dict[str, Any], url: str, host: str) -> tuple[dict[str, Any],
                                                                    list[str]]:
    """What a live site shows now: information for the row, never a pass."""
    from ..agency.audit import _pagespeed
    from ..agency.livecheck import live_launch_audit
    evidence: dict[str, Any] = {"url": url}
    unknown: list[str] = []
    started = datetime.now(UTC)
    report = await live_launch_audit(url, None, allowed_host=lambda h: h == host)
    evidence["live_elapsed_s"] = round((datetime.now(UTC) - started).total_seconds(), 2)
    evidence["live_issues"] = [f"{i.number} {i.name}" for i in report.issues][:20]
    key = os.getenv("GOOGLE_PAGESPEED_API_KEY")
    if key:
        score, why = await _pagespeed(url, key)
        if why is None:
            evidence["pagespeed_mobile"] = score
        else:
            unknown.append(f"PageSpeed: {why}")
    else:
        unknown.append("PageSpeed: GOOGLE_PAGESPEED_API_KEY is not set")
    for label, width in (("desktop", browser.DESKTOP), ("iphone", browser.IPHONE)):
        shot = await browser.shot(url, width)
        if shot.ok:
            evidence[f"shot_{label}"] = {"bytes": len(shot.data),
                                         "sha256": hashlib.sha256(shot.data).hexdigest()}
        else:
            unknown.append(f"{label} screenshot: {shot.detail}")
    return evidence, unknown


async def check_site(slug: str) -> ActionResult:
    from ...kernel import qa_gate
    from ..agency.demo_host import hostname_of
    try:
        got = await qa_gate.site_subject(str(slug))
    except (OSError, ValueError) as exc:
        return _refused(f"the files for {slug} cannot be read: {explain(exc)}")
    if got is None:
        return _refused(f"no site {slug!r}")
    subject, site, root = got
    failures, needs_client = await _disk_failures(subject.ref, site, root)
    evidence: dict[str, Any] = {"checks": list(SITE_CHECKS), "needs_client": needs_client}
    unknown: list[str] = []
    if site.get("deployed_at"):
        host = hostname_of(site)
        live, unknown = await _live(site, f"https://{host}/", host)
        evidence.update(live)
    else:
        unknown.append("live checks and screenshots: not deployed yet")
    evidence["unknown"] = unknown
    rid = await store.record("site", subject.ref, subject.sha, not failures, failures,
                             evidence)
    if failures:
        await qa_gate.remediate("qa.check_site", subject.ref, subject.sha, failures)
    said = f"QA passed {subject.ref}" if not failures else (
        f"QA failed {subject.ref}: " + "; ".join(failures[:5]))
    if unknown:
        said += f" (UNKNOWN, not counted: {'; '.join(unknown[:3])})"
    return ActionResult(True, said, evidence={"result_id": rid, "passed": not failures,
                                              "failures": failures, "unknown": unknown},
                        rollback_token=f"qa:{rid}")


# --------------------------------------------------------------- security

async def security_scan_dry(slug: str) -> ActionResult:
    try:
        host = await security.allowed_host(str(slug))
    except security.Refused as exc:
        return _refused(f"not scanned: {exc}")
    return ActionResult(True, f"would scan {host} passively",
                        evidence={"checks": list(SECURITY_CHECKS)})


async def security_scan(slug: str) -> ActionResult:
    from ...kernel import qa_gate
    try:
        scan = await security.scan(str(slug))
    except security.Refused as exc:
        return _refused(f"not scanned: {exc}")
    failures = list(scan.findings) + [f"UNKNOWN: {u}" for u in scan.unknown]
    ref, sha = f"security:{scan.host}", _sha(scan.probes)
    rid = await store.record("security", ref, sha, not failures, failures,
                             scan.evidence())
    if scan.findings:
        await qa_gate.remediate("qa.security_scan", ref, sha, scan.findings)
    said = (f"{scan.host}: no findings" if not failures
            else f"{scan.host}: " + "; ".join(failures[:5]))
    return ActionResult(True, said, evidence={"result_id": rid, "findings": scan.findings,
                                              "warnings": scan.warnings,
                                              "unknown": scan.unknown},
                        rollback_token=f"qa:{rid}")


# ------------------------------------------------------------------- runs

def expected_windows(now: datetime) -> list[tuple[str, str, datetime]]:
    """(job, occurrence, due) for every recorded window due in the last day
    and at least RUNS_LATE ago."""
    from ...kernel.scheduler import POLICIES
    out = []
    for policy in POLICIES.values():
        if not policy.recorded or policy.windows is None:
            continue
        if policy.enabled is not None and not policy.enabled():
            continue                    # switched off: no run was ever owed
        for w in policy.windows(now, timedelta(days=1)):
            if now - timedelta(days=1) <= w.scheduled_for <= now - RUNS_LATE:
                out.append((policy.job, w.key, w.scheduled_for))
    return out


async def _run_rows(jobs: list[str]) -> dict[tuple[str, str], str]:
    from ...kernel.state import fetch_all
    if not jobs:
        return {}
    rows = await fetch_all(
        """SELECT job, occurrence, status FROM schedule_runs
            WHERE job = ANY($1::text[]) AND claimed_at > now() - interval '3 days'""",
        sorted(set(jobs)))
    return {(str(r["job"]), str(r["occurrence"])): str(r["status"]) for r in rows}


async def verify_runs_dry() -> ActionResult:
    expected = expected_windows(datetime.now().astimezone())
    return ActionResult(True, f"would compare {len(expected)} scheduled window(s) of the "
                              "last day with schedule_runs",
                        evidence={"checks": [RUNS_CHECK]})


async def verify_runs() -> ActionResult:
    from ...kernel import qa_gate
    now = datetime.now().astimezone()
    expected = expected_windows(now)
    try:
        seen = await _run_rows([job for job, _k, _d in expected])
    except Exception as exc:  # noqa: BLE001
        return ActionResult(False, f"schedule_runs unreadable: {explain(exc)}",
                            failure_class=FailureClass.TRANSIENT)
    missed = [(job, key, due) for job, key, due in expected
              if seen.get((job, key)) not in ("done", "running")]
    failures = [f"{job} {key}: {seen.get((job, key)) or 'never ran'} "
                f"(due {due:%Y-%m-%d %H:%M})" for job, key, due in missed]
    rid = await store.record("runs", "schedule_runs",
                             _sha([(j, k) for j, k, _d in expected]), not missed,
                             failures, {"expected": len(expected), "missed": len(missed)})
    tasks = []
    for (job, key, _due), why in zip(missed, failures, strict=True):
        # No retry: a missed window is somebody's task, never a second run.
        tasks.append(await qa_gate.remediate("qa.verify_runs", f"{job}:{key}",
                                             _sha([job, key]), [why]))
    return ActionResult(True, (f"all {len(expected)} scheduled window(s) ran"
                               if not missed else f"{len(missed)} missed: "
                               + "; ".join(failures[:5])),
                        evidence={"result_id": rid, "missed": failures, "tasks": tasks},
                        rollback_token=f"qa:{rid}")


# ------------------------------------------------------------ remediation

CHECK_LEVEL_SOURCES = frozenset({"qa.gate_health"})


async def create_remediation_dry(owner: str, title: str, evidence: Any = None,
                                 suggested_fix: str = "", source_action: str = "",
                                 source_ref: str = "") -> ActionResult:
    if evidence is not None and not isinstance(evidence, dict):
        return _refused("evidence must be an object")
    if not (title or "").strip() or not (owner or "").strip():
        return _refused("owner and title are required")
    return ActionResult(True, f"would add a remediation task for {owner}: {title!r} "
                              f"(deduped on {source_action}/{source_ref} while open)",
                        evidence={"checks": ["dedupe on (source_action, source_ref)"]})


async def create_remediation(owner: str, title: str, evidence: Any = None,
                             suggested_fix: str = "", source_action: str = "",
                             source_ref: str = "") -> ActionResult:
    from ..projects import store as board
    if source_action in CHECK_LEVEL_SOURCES:
        # One standing problem is one task, however the caller words the ref
        # (18 duplicates piled up for one unset QA_BROWSER_URL, 2026-11-20).
        source_ref = source_action
    refused = await create_remediation_dry(owner, title, evidence, suggested_fix,
                                           source_action, source_ref)
    if not refused.ok:
        return refused
    if source_action and source_ref:
        existing = await board.open_remediation_for(source_action, source_ref)
        if existing is not None:
            return ActionResult(True, f"task #{existing} is already open for "
                                      f"{source_action}/{source_ref}",
                                evidence={"task_id": existing, "deduped": True})
    try:
        tid, token = await board.add_task(
            project_id=None, title=title, kind="remediation", owner=owner, due_at=None,
            evidence=evidence or {}, suggested_fix=suggested_fix,
            source_action=source_action, source_ref=source_ref)
    except board.Invalid as exc:
        return _refused(str(exc))
    return ActionResult(True, f"remediation task #{tid} for {owner}",
                        evidence={"task_id": tid, "deduped": False},
                        rollback_token=f"task:{token}")


# ----------------------------------------------------------------- checks

async def check_gate_health() -> Observation:
    now = datetime.now(UTC)
    try:
        counts = await store.counts_since(24)
    except Exception as exc:  # noqa: BLE001
        return Observation("gate_health", Health.UNKNOWN, at=now,
                           detail=f"qa_results unreadable: {explain(exc)}")
    shots_on = bool(browser.socket_path())
    shots = "on" if shots_on else "off (QA_BROWSER_URL unset: screenshots UNKNOWN)"
    # Real failures stay DEGRADED. With none, the board may say OK only if the
    # screenshot service can run; a gate that cannot take its screenshots reads
    # UNKNOWN, never OK (invariant 7).
    health = (Health.DEGRADED if counts["failed"]
              else Health.OK if shots_on else Health.UNKNOWN)
    return Observation(
        "gate_health", health,
        value=float(counts["failed"]), unit="failed",
        detail=(f"{counts['passed']} passed, {counts['failed']} failed in the last day; "
                f"qa-browser {shots} (read {now:%H:%M} UTC)"),
        evidence={**counts, "read_at": now.isoformat()}, at=now)


async def _open_remediation() -> int:
    from ...kernel.state import fetch_one
    row = await fetch_one(
        """SELECT count(*) AS n FROM project_tasks WHERE kind = 'remediation'
              AND status IN ('todo','doing','blocked')""")
    return int(row["n"]) if row is not None else 0


async def check_open_remediation() -> Observation:
    now = datetime.now(UTC)
    try:
        n = await _open_remediation()
    except Exception as exc:  # noqa: BLE001
        return Observation("open_remediation", Health.UNKNOWN, at=now,
                           detail=f"project board unreadable: {explain(exc)}")
    return Observation("open_remediation", Health.DEGRADED if n else Health.OK,
                       value=float(n), unit="tasks",
                       detail=f"{n} open remediation task(s) (read {now:%H:%M} UTC)",
                       evidence={"open": n, "read_at": now.isoformat()}, at=now)


class QaModule:
    name = "qa"
    title = "QA"
    requires_env = ()
    optional_env = ("QA_BROWSER_URL", "GOOGLE_PAGESPEED_API_KEY")
    setup_note = ("Screenshots need the qa-browser service (compose profile "
                  "qa-browser) and QA_BROWSER_URL; without them they read UNKNOWN.")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self) -> tuple[Check, ...]:
        return (
            Check("gate_health", check_gate_health, timedelta(minutes=30), Severity.INFO,
                  "QA results of the last day", baseline_sigma=None),
            Check("open_remediation", check_open_remediation, timedelta(minutes=30),
                  Severity.NORMAL, "open remediation tasks QA raised",
                  baseline_sigma=None),
        )

    def actions(self) -> tuple[Action, ...]:
        return (
            Action("qa.check_draft", Tier.AUTO,
                   "Run the words check on a stored draft and record the result",
                   run=check_draft, dry_run=check_draft_dry, rollback=rollback,
                   params={"kind": "email | sms | invite | post | reddit | report",
                           "ref": "agency_outreach:<id>, community_posts:<id,...>, "
                                  "reddit_drafts:<id> or academy_comment_drafts:<id>"},
                   max_per_hour=60),
            Action("qa.check_site", Tier.AUTO,
                   "QA the files a deploy would put live (and the live site, if any)",
                   run=check_site, dry_run=check_site_dry, rollback=rollback,
                   params={"slug": "the agency_sites.slug"}, max_per_hour=20),
            Action("qa.security_scan", Tier.AUTO,
                   "Passive security look at an allowed site (GETs only)",
                   run=security_scan, dry_run=security_scan_dry, rollback=rollback,
                   params={"slug": "an agency_sites.slug, or shop / mapfront / agency"},
                   max_per_hour=6),
            Action("qa.verify_runs", Tier.AUTO,
                   "Check every scheduled window of the last day ran; raise tasks",
                   run=verify_runs, dry_run=verify_runs_dry, rollback=rollback,
                   params={}, max_per_hour=4),
            Action("qa.autopilot_cycle", Tier.AUTO,
                   "Diagnose every check and agent that is not OK; run a registered "
                   "Tier 1 fix and prove it by a re-read, else one task or card",
                   run=autopilot.cycle, dry_run=autopilot.cycle_dry,
                   rollback=autopilot.undo, params={}, max_per_hour=12),
            Action("qa.create_remediation", Tier.AUTO,
                   "Add one remediation task to the local board (deduped while open)",
                   run=create_remediation, dry_run=create_remediation_dry,
                   rollback=rollback,
                   params={"owner": "the agent or person who fixes it",
                           "title": "what to fix, one line",
                           "evidence": "object: what was found, where (optional)",
                           "suggested_fix": "how to fix it (optional)",
                           "source_action": "the action or check that found it",
                           "source_ref": "what it is about"},
                   max_per_hour=30),
        )

    async def signals(self):
        return ()

    def brief(self) -> str:
        return ""

    async def agenda(self, within):
        return ()

    async def inbox(self, limit: int = 10):
        return ()


MODULE = QaModule()
