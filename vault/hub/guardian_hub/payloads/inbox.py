"""Inbox payload: one `InboxRow` shape shared by the alerts, incidents and
recommendations views (spec S6/S7/S8).

- `view="alerts"`: one row per `Alert`.
- `view="incidents"`: one row per open `Incident`, with `children` = the
  `InboxRow`s (alerts-view shape) of its `alert_keys`.
- `view="recs"`: one row per `Recommendation`.

Every row's `actions` list is the exact set of real routes the UI may POST
verbatim for the *current* role -- an action the caller's role may not
perform is omitted, never shown disabled. This mirrors the tier table Task
43's chat agent uses: 1 = read/safe, 2 = changes the network (operator),
3 = admin-only (reboot and friends -- `MANUAL_ONLY` below, a port of
Nexus's own `MANUAL_ONLY` set in checks.py).
"""

import datetime as dt
import re
from urllib.parse import quote

from sqlalchemy.orm import Session as DbSession

from .. import drift as drift_mod, models as m, networks
from ..chat.tiers import fix_args
from ..schemas_dash import AlertRemedy, DriftDetail, InboxCounts, InboxPayload, InboxRow, RowAction

ROLE_ORDER = {"viewer": 0, "operator": 1, "admin": 2}

ALERT_OPS = ("ack", "snooze", "resolve", "reopen")

# Port of Nexus checks.FIX_ACTIONS' key set (the command bodies live on the
# sensor/collector side; the hub only needs to know which names are valid
# fix actions and which of them are manual/admin-only).
FIX_ACTIONS = {
    "enable_fail2ban", "security_updates", "enable_unattended", "kill_miner",
    "harden_fail2ban", "block_ip", "block_subnet", "ban_long", "limit_ssh",
    "restart_container", "restart_service", "cleanup_disk",
    "disable_ssh_password", "enable_ufw", "reboot", "install_debsecan",
    "full_upgrade", "security_headers",
}
MANUAL_ONLY = {"reboot"}

_PRIORITY_SEV = {"critical": "CRIT", "high": "CRIT", "medium": "WARN", "low": "INFO"}

_MAC_RE = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$", re.I)


def _remedy_mod():
    """`guardian_hub.remedy`, imported on use rather than at module import.

    `remedy` reaches the chat tool registry (for the tier rules and the
    capability gate) and `chat/tools.py` imports `_entity_from_key` from this
    module, so a module-level import here would be a cycle. One attribute
    lookup per inbox build is not worth restructuring either module for.
    """
    from .. import remedy
    return remedy


def _can(role: str, min_role: str) -> bool:
    return ROLE_ORDER.get(role, 0) >= ROLE_ORDER.get(min_role, 99)


def _entity_from_key(key: str) -> str:
    """Best-effort entity extraction from an alert/rec key. Nexus keys are
    `prefix:entity[:sub]` (e.g. `reboot:safehome`, `sec:attacker:demo:1.2.3.4`,
    `lan:unusual-hour:aa:bb:...`) -- the entity is the 3rd colon-segment when
    there are >=3, else the 2nd.

    LAN keys are namespaced per network as of Task 51
    (`lan:<slug>:new:<mac>`), so the network segment is stripped first --
    positional parsing would otherwise read the slug as the entity."""
    parts = networks.canonical_lan_key(key or "").split(":")
    if len(parts) >= 3:
        return parts[2]
    if len(parts) >= 2:
        return parts[1]
    return key or ""


def _alert_actions(role: str, a: "m.Alert", entity: str,
                   remedy: "AlertRemedy | None" = None) -> list[RowAction]:
    """The routes the UI may offer for this row, tagged with the role each
    one actually needs (`tier`, on the hub's own viewer(1)/operator(2)/
    admin(3) ladder) -- NOT filtered down to only the ones this caller's
    `role` may use.

    That used to happen here: a role below the action's tier made the whole
    entry vanish, on the theory that an action a role may not perform should
    never render at all. In practice that left a viewer's alert rows with
    nothing on them but "Ask Vault" and no reason the everyday verbs
    (Ack/Snooze/Resolve/Reopen) were missing -- the one place in the product
    that did not follow the header's own rule for this (`TopBar.tsx`'s
    `gate()`: render disabled, say which role it needs). The write routes
    underneath (`routers/write.py`'s `OPERATOR`/`ADMIN` deps) enforce the
    real gate independently of what this payload says, so sending the row's
    full action set to every role costs nothing but a label the UI can grey
    out and explain -- `InboxRowView` does exactly that now.

    **Fix appears only when the remedy is one Vault may run right now**
    (`remedy.kind == "auto"`: an admin, a tier-2 action, a host it can reach
    and has fixed before). A remedy that needs a confirmation, or a role the
    caller does not have, is deliberately NOT a one-click POST here -- it
    goes through the chat hand-off, which mints the ordinary tier-3 proposal
    and takes a real "yes". Before this, `reboot` (tier 3) was offered as a
    plain button that rebooted a host on one click. `remedy.resolve` already
    downgrades `kind` away from "auto" for a role that cannot run it, with
    its own explanatory `summary` ("...but not with your access") rendered
    on the row regardless of role -- so a Fix a viewer cannot one-click is
    already explained in prose, and does not need a second, redundant
    disabled button here.

    The old `a.fix in FIX_ACTIONS` gate is gone with it: the sensor writes
    prose into `Alert.fix` ("auto-fixed: restart service", `runner.py`), not
    an action name, so that branch practically never fired and the remedy is
    resolved from the alert key instead (`guardian_hub/remedy.py`).
    """
    out: list[RowAction] = []
    for op in ALERT_OPS:
        out.append(RowAction(kind=op, label=op.capitalize(), method="POST",
                              path=f"/api/v1/alerts/{quote(a.key, safe='')}/{op}",
                              body={"hours": 8} if op == "snooze" else {}, tier=2))
    if _MAC_RE.match(entity or ""):
        out.append(RowAction(kind="trust", label="Trust device", method="POST",
                              path=f"/api/v1/devices/{entity}", body={"trusted": True}, tier=2))
    if remedy is not None and remedy.kind == "auto" and _can(role, remedy.min_role or "admin"):
        out.append(RowAction(kind="fix", label="Fix", method="POST", path="/api/v1/fix",
                              body=fix_args(action=remedy.action, host=remedy.host, arg=remedy.arg),
                              tier=remedy.tier or 2))
    return out


def _alert_row(a: "m.Alert", netrefs: dict | None = None, remedy: "AlertRemedy | None" = None,
                hosts_by_name: dict | None = None) -> tuple[InboxRow, str]:
    """Build the alerts-view row for `a`; returns (row-without-actions-role, entity)
    -- callers fill `actions` once they know the requester's role."""
    entity = _entity_from_key(a.key)
    why = a.why or (", ".join(f"{k}: {v}" for k, v in (a.evidence or {}).items()) if a.evidence else "")
    what_changed = f"seen {a.count}× since {a.first_seen}" if a.count and a.count > 1 else f"first seen {a.first_seen}"
    confidence = None
    try:
        confidence = int(a.confidence) if a.confidence else None
    except (TypeError, ValueError):
        confidence = None
    drift = drift_mod.detail(hosts_by_name or {}, a.key, a.first_seen)
    row = InboxRow(
        id=a.key, view="alerts", sev=a.sev if a.sev in ("CRIT", "WARN", "INFO") else "INFO",
        title=a.msg, entity=entity, why=why, what_changed=what_changed, status=a.status,
        first_seen=a.first_seen, last_seen=a.last_seen, count=a.count or 1,
        ticket_key=a.ticket_key or "", confidence=confidence,
        network=(netrefs or {}).get(a.network_id),
        remedy=remedy,
        drift=DriftDetail(**drift) if drift else None,
    )
    return row, entity


def _rec_actions(role: str, r: "m.Recommendation") -> list[RowAction]:
    # Always offered, tagged tier 2 (operator) -- see `_alert_actions`'s
    # docstring for why this no longer filters by `role` itself. `role` stays
    # a parameter for callers' sake even though this function no longer reads
    # it, so every call site keeps naming the requester's role for the row it
    # is building, not just the ones that still gate on it.
    del role
    # A declined recommendation ("No thanks") is hidden, not gone: it must
    # stay findable (under the inbox's "All" filter) and reversible without
    # waiting for the condition to clear and recur. "Bring this back" posts
    # the same `decision: "clear"` the route already supports -- it drops
    # the old decline outright, same as a recurrence would, so the row comes
    # back as a fresh, undecided recommendation rather than one stuck
    # showing a stale "no".
    if r.decision == "no":
        return [
            RowAction(kind="restore", label="Bring this back", method="POST", path=f"/api/v1/recs/{r.key}",
                      body={"decision": "clear"}, tier=2),
        ]
    # A recommendation the owner has already accepted must not go on offering
    # the same "Yes, do it" it already answered. It used to: after a yes on
    # something Vault cannot run alone, `status` became `needs-you` and the
    # row looked untouched, so the only evidence the click had done anything
    # was a toast that had already gone. What is waiting now is the owner, in
    # chat -- so the row says so, and offers the way back rather than the same
    # question again.
    if r.decision == "yes" and r.status == "needs-you":
        return [
            RowAction(kind="ask", label="Open the conversation", method="POST",
                      path="/api/v1/chat/handoff", body={"key": r.key}, tier=2),
            RowAction(kind="restore", label="Change my mind", method="POST",
                      path=f"/api/v1/recs/{r.key}", body={"decision": "clear"}, tier=2),
        ]
    # Accepted, and the work is already queued or finished. `_rec_open_filter`
    # keeps these out of the open list, but they are still reachable under
    # "All" -- and there the row must not ask a question it has an answer to.
    # Offering "Yes, do it" again is what made an accepted recommendation look
    # like a click that did nothing.
    # No buttons at all: every `RowAction` is a route the UI POSTs verbatim,
    # and there is nothing left to post here. What happened to this one is on
    # the Activity tab, and the row says so in `what_changed`.
    if r.decision == "yes" and r.status in _REC_ACTIONED:
        return []
    return [
        RowAction(kind="yes", label="Yes, do it", method="POST", path=f"/api/v1/recs/{r.key}",
                  body={"decision": "yes"}, tier=2),
        RowAction(kind="no", label="No thanks", method="POST", path=f"/api/v1/recs/{r.key}",
                  body={"decision": "no"}, tier=2),
    ]


def _rec_row(r: "m.Recommendation", role: str) -> InboxRow:
    sev = _PRIORITY_SEV.get((r.priority or "").lower(), "INFO")
    status = r.status or (r.decision or "open")
    return InboxRow(
        id=r.key, view="recs", sev=sev, title=r.title, entity=_entity_from_key(r.key),
        why=r.why,
        what_changed=("you said yes. This one needs you, so I am waiting in the chat."
                      if r.decision == "yes" and r.status == "needs-you"
                      else "you said yes. Vault queued the work -- follow it on Activity."
                      if r.decision == "yes" and r.status == "queued"
                      else "you said yes, and Vault has finished it."
                      if r.decision == "yes" and r.status == "done"
                      else f"last seen {r.last_seen}"),
        status=status,
        first_seen=r.last_seen, last_seen=r.last_seen, count=1,
        actions=_rec_actions(role, r), ticket_key="", confidence=None,
    )


def _incident_row(db: DbSession, tenant, i: "m.Incident", role: str,
                   hosts_by_name: dict | None = None) -> InboxRow:
    from . import incidents as incidents_payload

    tenant_id = tenant.id
    child_alerts = (
        db.query(m.Alert).filter(m.Alert.tenant_id == tenant_id, m.Alert.key.in_(i.alert_keys or [])).all()
        if i.alert_keys else []
    )
    _, lead_remedy = incidents_payload.primary(db, tenant, role, child_alerts)
    story = incidents_payload.narrative(db, tenant, i, child_alerts, role, lead_remedy=lead_remedy)

    children: list[InboxRow] = []
    netrefs = networks.refs_by_id(db, tenant_id)
    for a in child_alerts:
        rem = _remedy_mod().resolve(db, tenant, a, role)
        row, entity = _alert_row(a, netrefs, rem, hosts_by_name)
        row.actions = _alert_actions(role, a, entity, rem)
        children.append(row)

    last_seen = i.closed or (max((c.last_seen for c in children), default=i.opened) or i.opened)
    return InboxRow(
        id=i.iid, view="incidents", sev=i.sev if i.sev in ("CRIT", "WARN", "INFO") else "WARN",
        title=incidents_payload.title(i, child_alerts), entity=i.entity,
        why=story.what_happened, what_changed=story.what_it_means,
        status="closed" if i.closed else "open", first_seen=i.opened, last_seen=last_seen,
        count=len(i.alert_keys or []) or 1, actions=[], children=children,
        ticket_key="", confidence=story.confidence, remedy=lead_remedy,
    )


def wake_expired(db: DbSession, tenant_id: str, *, announce: bool) -> list:
    """Wake every alert whose snooze has run out. THE one place that does it.

    Two callers, one rule -- `build()` below on every inbox read, so a
    snoozed alert never stays hidden once its time is up, and
    `scheduler.snooze_wake_job` on a clock, which is what actually tells the
    customer. Splitting this in two was the bug it is written to prevent: a
    read path that cleared `snoozed_until` left the job nothing to find, so
    anyone who opened the tab before the sweep got no reminder at all --
    exactly the people most likely to be waiting for one.

    `snoozed_until` therefore means "a snooze whose end has not been
    announced yet". Flipping the status is safe to do on a read; CLEARING
    the stamp is the announcement's own receipt, so only the announcing
    caller does it. `status` is not in the filter for the same reason: by
    the time the job runs, a read may already have flipped it to `open`.

    Every other write path (ack, resolve, reopen) clears `snoozed_until`
    itself, so an alert somebody has since dealt with is never announced.
    """
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    expired = (
        db.query(m.Alert)
        .filter(m.Alert.tenant_id == tenant_id,
                m.Alert.snoozed_until.isnot(None), m.Alert.snoozed_until <= now_iso)
        .all()
    )
    if not expired:
        return []
    for a in expired:
        a.status = "open"
        if announce:
            a.snoozed_until = None
    db.commit()
    return expired


def _expire_snoozed(db: DbSession, tenant_id: str) -> None:
    """The read path's half: unhide, but leave the reminder owing."""
    wake_expired(db, tenant_id, announce=False)


#: A recommendation's work is under way or finished: the owner answered it and
#: Vault dispatched a job, so it is no longer something to decide.
_REC_ACTIONED = ("queued", "done")


def _rec_open_filter(q):
    """Narrow a recommendation query to the ones still waiting on a decision.

    A declined one is hidden (findable under "All", restorable). So is one the
    owner accepted and Vault has already queued or run -- that work is on
    the Activity tab now, not a question here.

    Leaving those in is what made "Yes, do it" look broken: the click queued a
    real job, the row stayed in the open list still offering the same "Yes, do
    it", and the only sign anything had happened was a toast that had already
    faded. `needs-you` and `failed` deliberately stay: those genuinely still
    want the owner.
    """
    return q.filter(m.Recommendation.decision != "no").filter(
        ~((m.Recommendation.decision == "yes") & (m.Recommendation.status.in_(_REC_ACTIONED)))
    )


def _counts(db: DbSession, tenant_id: str, prev_seen, tenant=None, role: str = "viewer") -> InboxCounts:
    # "open" here excludes snoozed the same way the alerts-view filter below
    # does -- a snoozed alert isn't counted as an open item until it expires
    # (or is manually reopened) and `_expire_snoozed` flips it back.
    alerts_open = db.query(m.Alert).filter(
        m.Alert.tenant_id == tenant_id, m.Alert.status.notin_(m.SETTLED_ALERT_STATUSES)
    ).count()
    incidents_open = db.query(m.Incident).filter(m.Incident.tenant_id == tenant_id, m.Incident.closed.is_(None)).count()
    recs_open = _rec_open_filter(db.query(m.Recommendation).filter(m.Recommendation.tenant_id == tenant_id)).count()

    unread = 0
    if prev_seen is not None:
        prev_iso = prev_seen.isoformat(timespec="seconds") if hasattr(prev_seen, "isoformat") else str(prev_seen)
        unread += db.query(m.Alert).filter(m.Alert.tenant_id == tenant_id, m.Alert.last_seen > prev_iso).count()
        unread += db.query(m.Incident).filter(m.Incident.tenant_id == tenant_id,
                                               (m.Incident.closed.is_(None)),
                                               m.Incident.opened > prev_iso).count()
    # Exact rather than capped: this is a badge a person reads as a promise,
    # and "3" when it is really 5 is worse than a slower page. It resolves a
    # remedy per OPEN alert only, which is the small set by construction --
    # a tenant with hundreds of open alerts has a louder problem than this.
    needs = 0
    if tenant is not None:
        for a in db.query(m.Alert).filter(
            m.Alert.tenant_id == tenant_id,
            m.Alert.status.notin_(m.SETTLED_ALERT_STATUSES),
        ).all():
            if _remedy_mod().resolve(db, tenant, a, role).kind == "approval":
                needs += 1

    return InboxCounts(incidents=incidents_open, alerts=alerts_open, recs=recs_open,
                       unread=unread, needs=needs)


def build(db: DbSession, tenant, view: str, status: str = "open", role: str = "viewer",
          prev_seen=None, net=None) -> InboxPayload:
    """`net` narrows the alerts view to one network. Incidents and
    recommendations are not network-scoped rows (an incident correlates alerts
    across a whole tenant, a recommendation is advice about the tenant), so a
    `?network=` filter leaves those two views alone rather than silently
    emptying them."""
    tenant_id = tenant.id
    _expire_snoozed(db, tenant_id)
    counts = _counts(db, tenant_id, prev_seen, tenant, role)
    netrefs = networks.refs_by_id(db, tenant_id)
    # Built once per call, not per row: `_alert_row` needs it only for a
    # `sec:drift:` key's host lookup (`drift.detail`), which is otherwise a
    # query-per-row.
    hosts_by_name = {h.name: h for h in db.query(m.Host).filter_by(tenant_id=tenant_id).all()}

    rows: list[InboxRow] = []
    if view in ("alerts", "needs"):
        q = db.query(m.Alert).filter_by(tenant_id=tenant_id)
        if net is not None:
            q = q.filter(m.Alert.network_id == net.id)
        # "Needs you" is always the open set: an acked or resolved alert is
        # not waiting on a decision, whatever its remedy would have been.
        if status == "open" or view == "needs":
            # Snoozed alerts stay hidden from the default "open" view until
            # `_expire_snoozed` flips them back -- `status="snoozed"` (or
            # "all") still shows them.
            q = q.filter(m.Alert.status.notin_(m.SETTLED_ALERT_STATUSES))
        elif status != "all":
            q = q.filter(m.Alert.status == status)
        for a in q.order_by(m.Alert.last_seen.desc()).all():
            rem = _remedy_mod().resolve(db, tenant, a, role)
            # The filter is on the resolved remedy, not on the key: only a
            # repair a "yes" actually runs belongs in a queue whose buttons
            # are Approve and Not now.
            if view == "needs" and rem.kind != "approval":
                continue
            row, entity = _alert_row(a, netrefs, rem, hosts_by_name)
            row.actions = _alert_actions(role, a, entity, rem)
            rows.append(row)
    elif view == "incidents":
        q = db.query(m.Incident).filter_by(tenant_id=tenant_id)
        if status == "open":
            q = q.filter(m.Incident.closed.is_(None))
        for i in q.order_by(m.Incident.opened.desc()).all():
            rows.append(_incident_row(db, tenant, i, role, hosts_by_name))
    elif view == "recs":
        q = db.query(m.Recommendation).filter_by(tenant_id=tenant_id)
        if status == "open":
            q = _rec_open_filter(q)
        for r in q.order_by(m.Recommendation.last_seen.desc()).all():
            rows.append(_rec_row(r, role))
    else:
        from fastapi import HTTPException
        raise HTTPException(422, "view must be alerts|incidents|recs")

    return InboxPayload(counts=counts, rows=rows)
