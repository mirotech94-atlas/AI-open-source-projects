"""Inbox triage (task 83, patterns A3 + A6): the one lead item, everything
else one line, and the three-way outcome as the primary axis -- built on top
of `payloads.inbox.build()`'s rows, not a second read of the alert tables.

Three moves on top of the plain inbox:

1.  **Roll up by cause.** Alerts that are not already grouped into an open
    `Incident` are grouped again here by `remedy.cause_key()` -- the same
    prefix `remedy.resolve()` already reads to say what Vault can (or
    cannot) do about a key, so a roll-up can never disagree with the row's
    own remedy. Nothing is dropped: every alert this collapses into a group
    is still in that group's `alerts` list, still counted.
2.  **Rank by danger, not repeat count.** `_score()` is deliberately small
    and its reasons are rendered, not just its number -- an unexplained score
    is worse than no score at all (the hard constraint this whole module is
    built to satisfy).
3.  **Count the three outcomes.** `handled` is Vault's own `Fix` log over
    a trailing window (`by == "auto"`, `ok == True` -- a fix a person clicked,
    or one that failed, is not "I handled it, without asking"). `needs_yes`
    and `needs_you` partition every *currently open* alert (including an
    incident's children) and every open recommendation, by
    `AlertRemedy.kind` for alerts and unconditionally for recs (a
    recommendation is always a yes/no waiting on the owner).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session as DbSession

from .. import models as m
from . import account as account_payload
from ..scans import parse_ts
from ._util import _lax_cutoff, _now
from ..schemas_dash import InboxTriagePayload, TriageItem, TriageOutcomes
from . import inbox as inbox_payload
from .. import remedy as remedy_mod

HANDLED_WINDOW_DAYS = 7

_SEV_WEIGHT = {"CRIT": 100.0, "WARN": 50.0, "INFO": 10.0}

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# Which of a row's already-role-filtered `actions` reads as the "one action
# that fits the row" (pattern A3) when more than one is present -- lower
# wins. Anything not listed here (an ad-hoc rec/trust kind) falls through to
# whichever action happened to be first, which is still a real action for
# this role, just not one this table has an opinion about ordering.
_ACTION_PRIORITY = {"fix": 0, "yes": 1, "reboot": 2, "ack": 3}


def since_phrase(first_seen: str, count: int, now: dt.datetime | None = None) -> str:
    """Turn a repeat count back into a duration (pattern A3): "seen 184x
    since ..." becomes "every scan since Tuesday". A pure function of
    `(first_seen, count, now)` so it is unit-testable against a frozen clock
    rather than only ever exercised against however old the demo fixture
    happens to be today.

    Within the last 7 days the weekday name is used ("since Tuesday");
    older than that (or a `first_seen` that fails to parse, or sits in the
    future) falls back to an absolute date, because "since Tuesday" on
    something three weeks old is a wrong answer, not a vague one.
    """
    now = now or _now()
    ts = parse_ts(first_seen)
    prefix = "since"
    if ts is None:
        return "seen repeatedly" if count and count > 1 else "just seen"
    delta = now - ts
    if 0 <= delta.days < 1:
        return f"{prefix} today"
    if 0 <= delta.days < 7:
        return f"{prefix} {_WEEKDAYS[ts.weekday()]}"
    return f"{prefix} {ts.strftime('%b %d')}"


def _score(sev: str, confidence: int | None, attach_count: int, is_incident: bool) -> tuple[float, str]:
    """`(score, reason)`. Danger (severity) first, discounted by how sure
    Vault is, then a bump for what it is attached to -- how many alerts
    this one row actually speaks for, because a row that already stands in
    for ten others is more urgent than a lookalike singleton. The reason
    string is rendered verbatim (`TriageItem.rank_reason`); a caller who
    wants to know why item #1 outranks #2 reads this, not a black box.
    """
    sev_weight = _SEV_WEIGHT.get(sev, 10.0)
    conf = 50 if confidence is None else confidence
    score = sev_weight * 1000 + conf
    reasons = [f"{sev} severity", "high evidence strength" if conf >= 80 else "moderate evidence strength" if conf >= 60 else "limited evidence"]
    if attach_count > 1:
        bump = min(attach_count, 20) * 4.0
        score += bump
        reasons.append(f"{attach_count} alerts attached")
    if is_incident:
        score += 5.0
        reasons.append("already correlated into an incident")
    return score, ", ".join(reasons)


def _primary(view: str, actions: list, remedy) -> tuple[str, str]:
    """`(primary_label, primary_min_role)`. When the row's own role-filtered
    `actions` already carries something, that action is real and enabled for
    this caller -- no role to name, so `primary_min_role` is "". When it does
    not (the caller's role is below what the action needs), the row still
    names the one action that would apply and the role it needs, so a viewer
    can render a disabled control with a real `title` instead of nothing."""
    if actions:
        best = min(actions, key=lambda a: _ACTION_PRIORITY.get(a.kind, 9))
        return best.label, ""
    if view == "recs":
        return "Yes, do it", "operator"
    if remedy is not None and remedy.kind == "approval":
        return "Fix", (remedy.min_role or "admin")
    return "", ""


def _alert_item(row, is_incident: bool = False, cause: str = "") -> TriageItem:
    alerts = row.children if is_incident else [row]
    score, reason = _score(row.sev, row.confidence, len(alerts), is_incident)
    label, min_role = _primary(row.view, row.actions, row.remedy)
    return TriageItem(
        kind="incident" if is_incident else ("group" if cause else "alert"),
        id=row.id, view=row.view, sev=row.sev, title=row.title, entity=row.entity,
        why=row.why, since_text=since_phrase(row.first_seen, len(alerts)),
        count=len(alerts), confidence=row.confidence, cause=cause,
        rank_score=score, rank_reason=reason, remedy=row.remedy, actions=row.actions,
        primary_label=label, primary_min_role=min_role, alerts=alerts,
    )


def _group_item(cause: str, group: list) -> TriageItem:
    """One roll-up row for every standalone alert sharing `cause`. Its own
    `sev`/`confidence`/`why`/`remedy`/`actions` come from the single most
    dangerous member -- the same alert that would have led alone -- so the
    row reads like the sharpest thing in the group, not an average of it.
    `alerts` still carries every member: rolling up never hides one."""
    lead = max(group, key=lambda r: (_SEV_WEIGHT.get(r.sev, 10.0), r.confidence or 50))
    first_seen = min((r.first_seen for r in group if r.first_seen), default=lead.first_seen)
    score, reason = _score(lead.sev, lead.confidence, len(group), False)
    label, min_role = _primary(lead.view, lead.actions, lead.remedy)
    return TriageItem(
        kind="group", id=f"cause:{cause}", view=lead.view, sev=lead.sev, title=lead.title,
        entity=lead.entity, why=lead.why, since_text=since_phrase(first_seen, len(group)),
        count=len(group), confidence=lead.confidence, cause=cause, rank_score=score,
        rank_reason=f"{reason} (rolled up from {len(group)} findings, one cause)",
        remedy=lead.remedy, actions=lead.actions, primary_label=label, primary_min_role=min_role,
        alerts=group,
    )


def _rec_item(row) -> TriageItem:
    score, reason = _score(row.sev, row.confidence, 1, False)
    label, min_role = _primary(row.view, row.actions, None)
    return TriageItem(
        kind="rec", id=row.id, view=row.view, sev=row.sev, title=row.title, entity=row.entity,
        why=row.why, since_text=since_phrase(row.first_seen, 1), count=1, confidence=row.confidence,
        rank_score=score, rank_reason=reason, remedy=None, actions=row.actions,
        primary_label=label, primary_min_role=min_role, alerts=[],
    )


def _handled_count(db: DbSession, tenant_id: str, now: dt.datetime) -> int:
    """Fixes Vault ran itself, without a person clicking Fix or saying
    yes, that actually worked, in the trailing window.

    Two things this deliberately does NOT do, both of which made it report 0
    on a hub that had handled plenty:

    * It does not test `by == "auto"`. Who said yes is `account.is_person`'s
      call everywhere in this app (`overview.fixed_without_you`,
      `value.build`, the console's self-healing chart) -- the sensor writes
      stamps of its own, and 61 rows stamped "test" on the live hub were
      excluded from this count entirely by a string compare.
    * It does not require `verified_at`. Verification is a strictly stronger
      claim: `verification.py` can only stamp it when the fix's host is in
      the latest scan AND that action has a known re-check, so 134 of 139
      real fixes could never qualify. Gating on it left this reading 0 while
      the Overview, counting the same rows without the gate, read 120 -- two
      panels disagreeing about one fact. `ok is True` is the standard the
      rest of the app holds a repair to, and it is the standard here.

    `Fix.ts` is a sensor-authored ISO string (its offset varies), so the SQL
    bound is deliberately lax (`_lax_cutoff`) and every row is re-checked
    exactly with `parse_ts` -- the same two-step every other payload uses
    against this column."""
    cutoff = now - dt.timedelta(days=HANDLED_WINDOW_DAYS)
    candidates = (
        db.query(m.Fix)
        .filter(m.Fix.tenant_id == tenant_id, m.Fix.ok.is_(True),
                m.Fix.ts >= _lax_cutoff(cutoff))
        .all()
    )
    count = 0
    for f in candidates:
        if account_payload.is_person(f.by):
            continue
        ts = parse_ts(f.ts)
        if ts is not None and ts >= cutoff:
            count += 1
    return count


def build(db: DbSession, tenant, role: str = "viewer") -> InboxTriagePayload:
    tenant_id = tenant.id
    now = dt.datetime.now(dt.timezone.utc)

    incidents = inbox_payload.build(db, tenant, "incidents", "open", role=role).rows
    alerts = inbox_payload.build(db, tenant, "alerts", "open", role=role).rows
    recs = inbox_payload.build(db, tenant, "recs", "open", role=role).rows

    in_incident = {child.id for inc in incidents for child in inc.children}
    standalone = [a for a in alerts if a.id not in in_incident]

    groups: dict[str, list] = {}
    for a in standalone:
        groups.setdefault(remedy_mod.cause_key(a.id), []).append(a)

    items: list[TriageItem] = []
    for cause, members in groups.items():
        items.append(_group_item(cause, members) if len(members) > 1 else _alert_item(members[0]))
    for inc in incidents:
        items.append(_alert_item(inc, is_incident=True))
    unmatched_recs = []
    for r in recs:
        parent = next((item for item in items if any(a.id == r.id for a in item.alerts)), None)
        if parent:
            parent.recommendations.append(r)
        else:
            unmatched_recs.append(r)
            items.append(_rec_item(r))

    importance = (tenant.settings or {}).get("asset_importance") or {}
    if importance:
        devices = db.query(m.Device).filter_by(tenant_id=tenant.id).all()
        for item in items:
            levels = []
            for device in devices:
                if any(device.mac in a.id and (a.network is None or a.network.id == device.network_id) for a in item.alerts):
                    levels.append(importance.get(f"{device.network_id}:{device.mac}", "normal"))
            impact = max(({"normal":0,"important":100,"critical":200}.get(level,0) for level in levels),default=0)
            if impact:
                item.rank_score += impact
                item.rank_reason += ", business-critical asset" if impact == 200 else ", important asset"

    # Deterministic order: highest danger first, then earliest-opened, then id
    # -- without the tiebreak, several INFO rows at the default confidence
    # score identically and `lead` would flap between requests.
    items.sort(key=lambda it: (-it.rank_score, min((a.first_seen for a in it.alerts), default=""), it.id))

    lead = items[0] if items else None
    rest = items[1:]

    needs_you = sum(1 for a in alerts if a.remedy is not None and a.remedy.kind == "human")
    needs_yes = sum(1 for a in alerts if a.remedy is not None and a.remedy.kind in ("auto", "approval"))
    needs_yes += len(unmatched_recs)

    outcomes = TriageOutcomes(
        handled=_handled_count(db, tenant_id, now),
        handled_period=f"the last {HANDLED_WINDOW_DAYS} days",
        needs_yes=needs_yes, needs_you=needs_you,
    )
    return InboxTriagePayload(lead=lead, rest=rest, outcomes=outcomes,
                               total_open=len(alerts) + len(unmatched_recs))
