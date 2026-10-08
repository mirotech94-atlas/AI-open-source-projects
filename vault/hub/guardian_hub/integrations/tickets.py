"""Jira / ServiceNow: one shared implementation (`TicketsIntegration`),
registered twice — `kind="jira"` and `kind="servicenow"` — since a tenant
picks one ticket system, not both, and the Integrations tab shows them as
two distinct tiles even though the reconciliation logic is identical
either flavour. `flavour` (also stored in `config`, redundant with `kind`
but explicit) selects which API shape `_create`/`_comment`/`_close` speak.

Ticket-per-**incident**, never per-alert (spec §10.1: an incident is the
Nexus-level grouping of related alerts — see `models.Incident.alert_keys`
— and a human filing/triaging a ticket per alert inside one incident would
be noise). `watchdog` is the only entry point:

- an open `Incident` with no ticket yet -> `_create()`, then every alert in
  `incident.alert_keys` gets `Alert.ticket_key` stamped (the durable,
  already-`Alert`-column source of truth read by the Inbox — no read of
  this integration's own bookkeeping needed to know an alert has a
  ticket);
- a `Fix` recorded against the incident's entity since it opened, not yet
  commented -> `_comment()` (idempotency tracked in `Integration.config`,
  the only place available without a schema change — see `registry.
  watchdog_job`'s compare-and-reassign docstring for why a plain mutation
  wouldn't otherwise persist);
- a closed `Incident` whose ticket hasn't been transitioned yet ->
  `_close()`;
- a `Recommendation` with `status == "needs-you"` -> a separate task
  ticket (one per recommendation, tracked the same way).

Fix round 1:

- ruling (b) / finding 3: every one of those idempotency maps now lives
  under the reserved `config["_sync"]` (`registry.sync_state`), which the
  Integrations form never submits and `upsert_integration` never replaces.
  They used to sit at the top level of `config`, so re-saving the card wiped
  them — re-filing a ticket for every open incident AND for every
  `needs-you` recommendation (the latter with no `Alert.ticket_key`
  fallback at all).
- ruling (c) / finding 2: `Incident.sev` / `.opened` / `.closed` are now
  populated from the sensor's REAL emitter keys (`severity`, `started`, and
  "gone from `last.incidents`" for closed) — see `ingest.py`. Before that,
  `inc.closed` was never set in production, which made `_close()`
  unreachable, and `inc.opened` was rewritten to the ingest timestamp every
  tick, which made the `Fix.ts >= inc.opened` comment query match nothing.
- ruling (d): `direction = "out+status sync"` is a documented literal in
  `base.DIRECTIONS`.
- finding 4 / ruling e: `base_url` must be https, with loopback/link-local
  rejected — these calls carry a basic-auth `Authorization:` header.
"""

from __future__ import annotations

import json
import urllib.request

from .. import models as m
from . import urlpolicy
from .base import Integration, IntegrationField, Message, SendContext, SendResult
from .registry import register, sync_state

TIMEOUT_S = 15


def _auth_header(ctx: SendContext) -> dict:
    import base64
    cfg = ctx.config or {}
    flavour = cfg.get("flavour", "jira")
    if flavour == "servicenow":
        raw = f"{cfg.get('email', '')}:{ctx.secret}".encode()
        return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
    raw = f"{cfg.get('email', '')}:{ctx.secret}".encode()   # Jira Cloud: email + API token, basic auth
    return {"Authorization": "Basic " + base64.b64encode(raw).decode()}


def _request(url: str, method: str, headers: dict, body: dict | None = None) -> dict | None:
    try:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                      headers={**headers, "Content-Type": "application/json", "User-Agent": "guardian/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except Exception:
        return None


def _create(ctx: SendContext, summary: str, body: str) -> str | None:
    """Create one ticket, returning its key/number, or None on failure.
    Monkeypatched wholesale in tests — the real HTTP shape differs enough
    between Jira Cloud and ServiceNow's Table API that this is the natural
    seam, same role as `_post` in the webhook-shaped providers."""
    cfg = ctx.config or {}
    flavour = cfg.get("flavour", "jira")
    base = (cfg.get("base_url") or "").rstrip("/")
    headers = _auth_header(ctx)
    if flavour == "servicenow":
        table = cfg.get("project") or "incident"
        out = _request(f"{base}/api/now/table/{table}", "POST", headers,
                        {"short_description": summary, "description": body})
        return ((out or {}).get("result") or {}).get("number")
    project = cfg.get("project") or "GRD"
    out = _request(f"{base}/rest/api/2/issue", "POST", headers, {"fields": {
        "project": {"key": project}, "summary": summary,
        "description": body, "issuetype": {"name": "Task"},
    }})
    return (out or {}).get("key")


def _comment(ctx: SendContext, key: str, body: str) -> None:
    cfg = ctx.config or {}
    flavour = cfg.get("flavour", "jira")
    base = (cfg.get("base_url") or "").rstrip("/")
    headers = _auth_header(ctx)
    if flavour == "servicenow":
        table = cfg.get("project") or "incident"
        _request(f"{base}/api/now/table/{table}/{key}", "PATCH", headers, {"work_notes": body})
    else:
        _request(f"{base}/rest/api/2/issue/{key}/comment", "POST", headers, {"body": body})


def _close(ctx: SendContext, key: str) -> None:
    cfg = ctx.config or {}
    flavour = cfg.get("flavour", "jira")
    base = (cfg.get("base_url") or "").rstrip("/")
    headers = _auth_header(ctx)
    if flavour == "servicenow":
        table = cfg.get("project") or "incident"
        _request(f"{base}/api/now/table/{table}/{key}", "PATCH", headers, {"state": "7"})   # 7 = Closed
    else:
        # Jira's transition id for "Done" varies per project workflow; a
        # real deployment configures it, but every Cloud "Task"-type
        # default workflow ships transition 31 = Done.
        transition = cfg.get("done_transition_id") or "31"
        _request(f"{base}/rest/api/2/issue/{key}/transitions", "POST", headers,
                  {"transition": {"id": transition}})


def _entity_host(entity: str) -> str:
    """The bare host name inside an incident entity.

    `alerts.correlate_incidents` labels an incident by CLASS: `host:demo`,
    `site:example.com`, `domain:example.com`, `job:backup`, `home`. A `Fix` row,
    though, records `Fix.host` as the bare sensor host (`demo`) -- so the
    comment-on-fix query has to compare the two on the same footing. This
    was the second half of finding 2: even with `inc.opened` fixed, an
    `entity == "host:demo"` never equalled a `Fix.host == "demo"`, so
    `_comment()` was unreachable in production too."""
    if ":" in (entity or ""):
        return entity.split(":", 1)[1]
    return entity or ""


def _incident_summary(inc: "m.Incident") -> str:
    return f"[{inc.sev}] {inc.entity or inc.iid}"


def _action_text(a: object) -> str:
    """Render one `inc.actions` entry. Entries are usually plain strings,
    but some producers store `{'priority': ..., 'text': ...}` dicts -- render
    those as their `text` (or `key`) instead of Python's raw repr, which
    otherwise leaked `{'priority': 'Low', 'text': ...}` straight into every
    Jira/ServiceNow description."""
    if isinstance(a, dict):
        return a.get("text") or a.get("key") or str(a)
    return str(a)


def _incident_body(inc: "m.Incident") -> str:
    actions = "\n".join(f"- {_action_text(a)}" for a in (inc.actions or [])) or "(no suggested actions)"
    return f"{inc.story}\n\nSuggested actions:\n{actions}\n\nVault incident: {inc.iid}"


class TicketsIntegration(Integration):
    title = "Jira / ServiceNow"
    help = "One ticket per incident (not per alert): create, comment on fixes, close on resolution."
    direction = "out+status sync"
    fields = [
        IntegrationField(name="token", label="API token / password", kind="secret", required=True),
        IntegrationField(name="flavour", label="Flavour", kind="select", required=True, options=["jira", "servicenow"]),
        IntegrationField(name="base_url", label="Base URL", kind="url", required=True),
        IntegrationField(name="email", label="Email / username", kind="text", required=True),
        IntegrationField(name="project", label="Project key (Jira) / table (ServiceNow)", kind="text", required=True),
    ]

    def validate(self, fields: dict) -> str:
        flavour = (fields or {}).get("flavour", "")
        if flavour and flavour not in ("jira", "servicenow"):
            return "flavour must be jira or servicenow"
        return urlpolicy.check_url((fields or {}).get("base_url") or "", "Base URL")

    def send(self, ctx: SendContext, msg: Message) -> SendResult:
        # Outbound alert delivery is not this integration's job (spec
        # §10.1: it syncs ticket status, it doesn't relay every alert as a
        # push) -- routing this kind off severity in `Integration.routes`
        # would just create noise tickets, so `send` is a deliberate no-op.
        return SendResult(True, "")

    def watchdog(self, ctx: SendContext) -> None:
        db = ctx.db
        tenant = ctx.tenant
        # Ruling (b): sync bookkeeping under the reserved `_sync` sub-dict,
        # migrating any top-level copy left by a pre-fix build so an
        # already-running hub does not re-file every ticket once.
        sync = sync_state(ctx)
        legacy = ctx.config or {}
        def _carry(name):
            return sync.get(name, legacy.get(name))
        ticket_by_incident: dict[str, str] = dict(_carry("ticket_by_incident") or {})
        commented_fix_ids: set = set(_carry("commented_fix_ids") or [])
        closed_tickets: set = set(_carry("closed_tickets") or [])
        ticket_by_rec: dict[str, str] = dict(_carry("ticket_by_rec") or {})

        incidents = db.query(m.Incident).filter_by(tenant_id=tenant.id).all()
        for inc in incidents:
            key = ticket_by_incident.get(inc.iid)
            if not key and not inc.closed:
                # An alert in this incident may already carry a ticket key
                # from an earlier tick this row's own config lost track of
                # (e.g. config wiped by a re-save) -- reuse it rather than
                # filing a duplicate.
                if inc.alert_keys:
                    existing = (db.query(m.Alert.ticket_key)
                                .filter(m.Alert.tenant_id == tenant.id, m.Alert.key.in_(inc.alert_keys),
                                        m.Alert.ticket_key != "").first())
                    if existing:
                        key = existing[0]
                if not key:
                    key = _create(ctx, _incident_summary(inc), _incident_body(inc))
                if key:
                    ticket_by_incident[inc.iid] = key
                    if inc.alert_keys:
                        (db.query(m.Alert)
                         .filter(m.Alert.tenant_id == tenant.id, m.Alert.key.in_(inc.alert_keys))
                         .update({"ticket_key": key}, synchronize_session=False))

            if not key:
                continue   # ticket creation failed or incident already closed with none on record

            for f in (db.query(m.Fix)
                      .filter(m.Fix.tenant_id == tenant.id,
                              m.Fix.host.in_({inc.entity, _entity_host(inc.entity)}),
                              m.Fix.ts >= inc.opened)
                      .all()):
                if f.id in commented_fix_ids:
                    continue
                _comment(ctx, key, f"Fix applied: {f.label or f.action} on {f.host} — "
                                    f"{'succeeded' if f.ok else 'failed'}.")
                commented_fix_ids.add(f.id)

            if inc.closed and key not in closed_tickets:
                _close(ctx, key)
                closed_tickets.add(key)

        for r in db.query(m.Recommendation).filter_by(tenant_id=tenant.id, status="needs-you").all():
            if r.key in ticket_by_rec:
                continue
            key = _create(ctx, f"[Vault] {r.title}", r.why or r.title)
            if key:
                ticket_by_rec[r.key] = key

        sync["ticket_by_incident"] = ticket_by_incident
        sync["commented_fix_ids"] = sorted(commented_fix_ids)
        sync["closed_tickets"] = sorted(closed_tickets)
        sync["ticket_by_rec"] = ticket_by_rec
        # Drop the pre-fix top-level copies now that `_sync` owns them, so
        # the two can never diverge.
        for name in ("ticket_by_incident", "commented_fix_ids", "closed_tickets", "ticket_by_rec"):
            ctx.config.pop(name, None)


@register
class JiraIntegration(TicketsIntegration):
    kind = "jira"
    title = "Jira"


@register
class ServiceNowIntegration(TicketsIntegration):
    kind = "servicenow"
    title = "ServiceNow"
