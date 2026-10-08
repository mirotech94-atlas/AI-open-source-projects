"""Incident narrative + incident detail (Task 83, patterns A1/A2).

A1 -- the incident carries a written narrative. Vault already writes
excellent per-alert copy (`Alert.msg`, `Alert.why`, and `remedy.py`'s "I can
fix this myself" / "I can repair this but..." sentences); this module is a
summariser over that, never a second, invented voice. Every sentence traces
back to a real `Alert` row under the incident, or says plainly that Vault
does not know more -- there is no fallback that fabricates a cause, a time
phrase or a confidence number.

A2 -- one incident, one page: `detail()` assembles the narrative, a timeline
of the alerts in the group, the two-or-three assets they touched, a graph
scoped to just those assets (built from the same `TopoNode`/`TopoLink` shapes
`payloads/topology.py` uses for the full map) and the evidence rows the
narrative was built from.

Confidence is `int(Alert.confidence)` averaged across the incident's
children, exactly the computation `InboxRowView.tsx`'s `confidenceTone` doc
comment already promises ("the sensor scores every alert 0-100 ... and an
incident averages its members'") but `payloads/inbox.py` never implemented --
`None` (not 0, not a placeholder) when no child alert carries a score.
"""

from __future__ import annotations

from sqlalchemy.orm import Session as DbSession

from .. import models as m, networks
from ..schemas_dash import (
    IncidentAsset, IncidentDetail, IncidentEvidenceRow, IncidentGraph,
    IncidentNarrative, IncidentTimelineItem, RowAction, TopoLink, TopoNode,
)

_TONE_BY_SEV = {"CRIT": "alert", "WARN": "warn", "INFO": "plain"}


def _remedy_mod():
    """Imported on use -- `remedy` reaches the chat tool registry, and a
    module-level import here would risk the same import cycle
    `payloads/inbox.py` already documents avoiding."""
    from .. import remedy
    return remedy


def _entity_from_key(key: str) -> str:
    from .inbox import _entity_from_key as impl
    return impl(key)


def children(db: DbSession, tenant_id: str, incident: "m.Incident") -> list["m.Alert"]:
    """Every `Alert` row this incident correlates, oldest first -- the same
    set `payloads/inbox.py`'s `_incident_row` expands into `children` rows,
    fetched straight from `Incident.alert_keys` rather than re-derived."""
    if not incident.alert_keys:
        return []
    rows = (
        db.query(m.Alert)
        .filter(m.Alert.tenant_id == tenant_id, m.Alert.key.in_(incident.alert_keys))
        .all()
    )
    return sorted(rows, key=lambda a: a.first_seen or "")


def confidence(alerts: list["m.Alert"]) -> tuple[int | None, str]:
    """The real computation: mean of every child alert's own `confidence`
    (the sensor scores each 0-100; see `Alert.confidence`), rounded. `None`
    with an empty basis when nothing under this incident carries a score --
    never a decorative number."""
    vals: list[int] = []
    for a in alerts:
        try:
            v = int(a.confidence)
        except (TypeError, ValueError):
            continue
        if 0 <= v <= 100:
            vals.append(v)
    if not vals:
        return None, ""
    pct = round(sum(vals) / len(vals))
    n = len(vals)
    basis = f"averaged from the {n} alert{'' if n == 1 else 's'} Vault scored"
    return pct, basis


def primary(db: DbSession, tenant, role: str, alerts: list["m.Alert"]):
    """The one alert this incident's outcome and "what happens next" inherit
    (spec A1 point 4 / A6): the alert Vault can most usefully act on --
    prefer a remedy it can run itself, then one that needs an approval, then
    one that needs the owner -- and within a tie, the one that has fired the
    most (the loudest signal in the group). Returns `(None, None)` when the
    incident has no alerts left to read.
    """
    remedy = _remedy_mod()
    if not alerts:
        return None, None
    rank = {"auto": 0, "approval": 1, "human": 2, "none": 3}
    scored = []
    for a in alerts:
        rem = remedy.resolve(db, tenant, a, role)
        scored.append((rank.get(rem.kind, 3), -(a.count or 1), a, rem))
    scored.sort(key=lambda t: (t[0], t[1]))
    _, _, lead_alert, lead_remedy = scored[0]
    return lead_alert, lead_remedy


def _loudest(alerts: list["m.Alert"]) -> "m.Alert | None":
    """The child alert with the largest repeat count -- the one whose own
    numbers carry the incident's headline fact (Task 83's own example: "made
    362 failed SSH logins")."""
    if not alerts:
        return None
    return max(alerts, key=lambda a: (a.count or 1))


def title(incident: "m.Incident", alerts: list["m.Alert"]) -> str:
    """A one-line headline built from the loudest child alert's own message
    -- never the raw `entity: SEV incident (N alerts)` key-and-count line, and
    never a raw story timestamp glued to the front."""
    lead = _loudest(alerts)
    text = (lead.msg if lead else "").strip() if lead else ""
    if not text:
        return incident.entity or "incident"
    if len(alerts) > 1:
        return f"{text} (+{len(alerts) - 1} more)"
    return text


def _what_happened(incident: "m.Incident", alerts: list["m.Alert"]) -> str:
    lead = _loudest(alerts)
    text = (lead.msg or "").strip() if lead else ""
    if not text:
        return ""
    if len(alerts) > 1:
        text = f"{text} Vault has grouped {len(alerts)} alerts into this one incident."
    return text


def _what_it_means(alerts: list["m.Alert"]) -> str:
    reasons: list[str] = []
    for a in alerts:
        why = (a.why or "").strip()
        if why and why not in reasons:
            reasons.append(why)
    if reasons:
        return " ".join(reasons)
    return "Vault has not recorded a reason beyond the alert itself."


def narrative(db: DbSession, tenant, incident: "m.Incident", alerts: list["m.Alert"],
              role: str, lead_remedy=None) -> IncidentNarrative:
    """The four fixed sections, in order, built only from `incident` and
    `alerts` -- see the module docstring for the no-invention rule.

    `lead_remedy` lets a caller that already ran `primary()` (`detail()`
    does) pass its result straight in rather than resolving every child
    alert's remedy a second time; omitted, this resolves it itself."""
    pct, basis = confidence(alerts)
    rem = lead_remedy if lead_remedy is not None else primary(db, tenant, role, alerts)[1]
    if rem is not None and rem.kind != "none":
        what_next = rem.summary
        need = rem.need
    elif alerts:
        what_next = "Vault does not have an automatic or proposed fix for this. It keeps watching for a change."
        need = ""
    else:
        what_next = ""
        need = ""
    return IncidentNarrative(
        what_happened=_what_happened(incident, alerts),
        what_it_means=_what_it_means(alerts),
        confidence=pct,
        confidence_basis=basis,
        what_next=what_next,
        need=need,
    )


def _timeline(alerts: list["m.Alert"], fixes: list["m.Fix"]) -> list[IncidentTimelineItem]:
    items: list[IncidentTimelineItem] = []
    for a in alerts:
        tone = _TONE_BY_SEV.get(a.sev, "plain")
        text = a.msg or a.key
        items.append(IncidentTimelineItem(ts=a.first_seen, tone=tone, text=text))
        if a.last_seen and a.last_seen != a.first_seen and (a.count or 1) > 1:
            items.append(IncidentTimelineItem(
                ts=a.last_seen, tone=tone,
                text=f"Still going -- seen {a.count}× in total, most recently now.",
            ))
    for f in fixes:
        label = f.label or f.action.replace("_", " ")
        text = f"Vault {'applied' if f.ok else 'tried and failed to apply'} a fix on {f.host}: {label}."
        items.append(IncidentTimelineItem(ts=f.ts, tone="good" if f.ok else "warn", text=text))
    items.sort(key=lambda it: it.ts or "")
    return items


def _fixes_for(db: DbSession, tenant_id: str, incident: "m.Incident",
                hosts: set[str]) -> list["m.Fix"]:
    """Auto-fixes Vault already ran on a host this incident touched,
    inside the incident's own window -- real rows from `Fix`, not a claim
    the narrative invents about what it did."""
    if not hosts:
        return []
    q = db.query(m.Fix).filter(m.Fix.tenant_id == tenant_id, m.Fix.host.in_(hosts))
    if incident.opened:
        q = q.filter(m.Fix.ts >= incident.opened)
    return q.order_by(m.Fix.ts.asc()).all()


def _asset_for_entity(db: DbSession, tenant_id: str, entity: str,
                       netrefs: dict) -> IncidentAsset | None:
    """Resolve one incident entity to a real `Host` or `Device` row -- an
    entity that matches neither (e.g. an attacker IP named only in an alert
    key) becomes an `external` asset with no risk score, never a guess."""
    host = db.query(m.Host).filter_by(tenant_id=tenant_id, name=entity).first()
    if host is not None:
        return IncidentAsset(
            id=f"host:{host.name}", kind="host", name=host.name,
            subtitle=host.address or "server Vault watches",
            risk=None, href=f"/assets/hosts/{host.name}",
        )
    dq = db.query(m.Device).filter(m.Device.tenant_id == tenant_id,
                                    (m.Device.mac == entity) | (m.Device.name == entity)
                                    | (m.Device.label == entity))
    device = dq.first()
    if device is not None:
        label = device.label or device.name or device.mac
        return IncidentAsset(
            id=f"device:{device.mac}", kind="device", name=label,
            subtitle=device.ip or device.vendor or "",
            risk=int(device.risk or 0), href=f"/assets/devices/{device.mac}",
        )
    return None


def _external_assets(alerts: list["m.Alert"], remedies: list) -> list[IncidentAsset]:
    """An outside address only ever enters an incident as a remedy's `arg`
    (the sensor's own attacker-IP argument for `block_ip`/`block_subnet`/
    `ban_long`) -- never inferred from free text, which would risk naming the
    wrong token as an address."""
    seen: dict[str, IncidentAsset] = {}
    for a, rem in zip(alerts, remedies):
        if rem is None or not rem.arg:
            continue
        if rem.action not in ("block_ip", "block_subnet", "ban_long", "limit_ssh"):
            continue
        if rem.arg in seen:
            continue
        seen[rem.arg] = IncidentAsset(
            id=f"external:{rem.arg}", kind="external", name=rem.arg,
            subtitle=f"outside · first seen {a.first_seen}", risk=None, href="",
        )
    return list(seen.values())


def _incident_entity_name(entity: str) -> str:
    """`Incident.entity` is `"<kind>:<name>"` (`host:booking`,
    `domain:northwind-clinics.example`) -- the name is everything after the
    first colon."""
    return entity.split(":", 1)[1] if ":" in (entity or "") else (entity or "")


def _assets(db: DbSession, tenant_id: str, incident: "m.Incident",
            alerts: list["m.Alert"], remedies: list, netrefs: dict) -> list[IncidentAsset]:
    entities: list[str] = []
    top = _incident_entity_name(incident.entity)
    for a in alerts:
        e = _entity_from_key(a.key)
        if e and e not in entities:
            entities.append(e)
    if top and top not in entities:
        entities.append(top)
    out: list[IncidentAsset] = []
    for e in entities:
        asset = _asset_for_entity(db, tenant_id, e, netrefs)
        if asset is not None:
            out.append(asset)
    out.extend(_external_assets(alerts, remedies))
    return out


def _graph(assets: list[IncidentAsset]) -> IncidentGraph:
    """A star graph: every host/device asset links to every external asset
    that targeted this incident (the only relationship Vault actually
    recorded -- see `_external_assets`). Assets with no known external actor
    (a CVE incident across three hosts, say) render as unconnected nodes
    rather than a fabricated link between them."""
    hosts = [a for a in assets if a.kind in ("host", "device")]
    externals = [a for a in assets if a.kind == "external"]
    nodes = [
        TopoNode(id=a.id, kind=a.kind, group="Involved", label=a.name,
                 ip="", mac=None,
                 icon=a.kind, state="warn" if a.kind == "external" else "online",
                 risk=a.risk or 0, ring=0 if a.kind == "external" else 1,
                 trusted=False, quarantined=False, pulse=False, detail=a.subtitle)
        for a in assets
    ]
    links = [
        TopoLink(source=ext.id, target=h.id, kind="attack", pulse=False)
        for ext in externals for h in hosts
    ]
    return IncidentGraph(nodes=nodes, links=links)


def _evidence(alerts: list["m.Alert"]) -> list[IncidentEvidenceRow]:
    rows: list[IncidentEvidenceRow] = []
    for a in alerts:
        entity = _entity_from_key(a.key)
        what = a.why or a.msg or a.key
        rows.append(IncidentEvidenceRow(ts=a.first_seen, where=entity, what=what, seen=a.count or 1))
    return rows


def find(db: DbSession, tenant_id: str, iid: str) -> "m.Incident | None":
    return db.query(m.Incident).filter_by(tenant_id=tenant_id, iid=iid).first()


def detail(db: DbSession, tenant, incident: "m.Incident", role: str) -> IncidentDetail:
    """Assemble the whole incident detail page: narrative, timeline, assets,
    a scoped graph and the evidence rows -- see the module docstring."""
    from . import inbox as inbox_payload

    tenant_id = tenant.id
    alerts = children(db, tenant_id, incident)
    remedy = _remedy_mod()
    remedies = [remedy.resolve(db, tenant, a, role) for a in alerts]
    netrefs = networks.refs_by_id(db, tenant_id)

    lead_alert, lead_remedy = primary(db, tenant, role, alerts)
    story = narrative(db, tenant, incident, alerts, role, lead_remedy=lead_remedy)

    assets = _assets(db, tenant_id, incident, alerts, remedies, netrefs)
    fix_hosts = {a.name for a in assets if a.kind in ("host", "device")}
    fixes = _fixes_for(db, tenant_id, incident, fix_hosts)

    actions: list[RowAction] = []
    if lead_alert is not None:
        entity = _entity_from_key(lead_alert.key)
        actions = inbox_payload._alert_actions(role, lead_alert, entity, lead_remedy)

    return IncidentDetail(
        id=incident.iid, title=title(incident, alerts),
        sev=incident.sev if incident.sev in ("CRIT", "WARN", "INFO") else "WARN",
        status="closed" if incident.closed else "open",
        entity=incident.entity, opened=incident.opened, closed=incident.closed,
        alert_count=len(alerts),
        narrative=story,
        timeline=_timeline(alerts, fixes),
        assets=assets,
        graph=_graph(assets),
        evidence=_evidence(alerts),
        remedy=lead_remedy,
        actions=actions,
        primary_key=lead_alert.key if lead_alert is not None else "",
    )
