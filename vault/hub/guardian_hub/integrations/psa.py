"""HaloPSA / Autotask / ConnectWise Manage: the MSP's ticketing system.

One shared implementation (`PsaIntegration`), registered three times, the
same way `tickets.py` registers Jira and ServiceNow off one class. The
difference from that module is the UNIT. Jira gets a ticket per incident
because an engineer triages a story; a PSA gets a ticket per escalated
FINDING because that ticket is what the MSP bills, reports and closes with
the customer, and the customer asked about one thing. The spec's own line
was that a one-way integration makes two sources of truth and is abandoned
within a month, so this runs in both directions, conservatively:

- Vault -> PSA. An open alert that meets the escalation bar (CRIT by
  default; `escalate=CRIT+WARN` widens it) gets one ticket, stamped on
  `Alert.ticket_key` -- the durable column the Inbox already reads, so a
  re-saved card or a wiped `_sync` cannot re-file it. A successful fix
  that `remedy.fix_for_key` says is the remedy for that alert is written
  back as a note on the ticket, and, only when the card opts in
  (`time_entries=on`), as a time entry of `minutes` -- a billable line
  Vault invents on the MSP's behalf is exactly the kind of thing that
  must default off. A resolved alert resolves its ticket, once.

- PSA -> Vault. A ticket a technician closed on the PSA side ACKS the
  alert here (not resolves: the sensor is the only thing that can say the
  condition is gone, and it re-asserts every scan). The ack is audited as
  `alert.ack` by `psa:<flavour>`, indistinguishable in the Edit log from a
  dashboard ack, which is the whole point of going through
  `apply_alert_op`.

`_create`/`_note`/`_time_entry`/`_resolve`/`_status` are the seams: the
three vendors' HTTP shapes live there and nowhere else, and tests replace
them wholesale. Those shapes are written from the vendors' published REST
documentation and have NOT been exercised against a live tenant of any
of the three. Two things are built so that the first live tenant is a
ten-minute job rather than an id hunt:

- **Names, not ids.** `company_ref`, `board` and `closed_status` may be
  typed as the name that appears in the PSA ("Northwind Dental",
  "Service Desk", "Closed"). `_lookup` reads the PSA's own lists
  (`_companies`/`_boards`/`_statuses`) and resolves each name to the id
  once, caching the answer in `_sync["resolved"]`; an id typed directly is
  used as is. A name that matches nothing, or more than one thing, is an
  error that lists the closest candidates by name, so the fix is to copy
  one of them -- never to open the vendor's API explorer.

- **Loud mismatches.** A vendor answering with a status code or a shape
  this module did not expect is a `PsaError` carrying the URL, the status
  and the first line of the body. The watchdog turns the first one it
  meets into `Integration.last_error`, which the card shows, and the Test
  button runs the whole discovery and reports what resolved to what. A
  create that silently returned None was the failure mode to avoid: a
  ticket that never appears, and nothing anywhere saying why.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import urllib.error
import urllib.parse
import urllib.request

from .. import models as m, networks, remedy
from . import urlpolicy
from .base import Integration, IntegrationField, Message, SendContext, SendResult
from .registry import register, sync_state

TIMEOUT_S = 15
FLAVOURS = ("halopsa", "autotask", "connectwise")

#: The vendors' stock "closed" values. A PSA with a customised workflow
#: overrides them from the card (`closed_status`).
DEFAULT_CLOSED = {"halopsa": "9", "autotask": "5", "connectwise": "Closed"}


class PsaError(Exception):
    """The PSA answered in a way this module could not use. Carries what a
    person needs to act on it -- which call, what status, what the body
    started with -- and nothing that could be a credential: the request's
    headers are never included."""

    def __init__(self, where: str, status: int, snippet: str = ""):
        self.where, self.status, self.snippet = where, status, snippet
        super().__init__(self.__str__())

    def __str__(self) -> str:
        head = f"{self.where} -> {self.status or 'no response'}"
        return f"{head}: {self.snippet}" if self.snippet else head


def _request(url: str, method: str, headers: dict, body=None, *, form: bool = False):
    """One HTTP call, JSON in and out. Raises `PsaError` on a non-2xx
    status, a transport failure, or a body that is not JSON -- never
    returns None, so a caller cannot mistake "nothing came back" for
    "nothing to do"."""
    where = f"{method} {urllib.parse.urlsplit(url).path}"
    try:
        if form:
            data = urllib.parse.urlencode(body or {}).encode()
            ctype = "application/x-www-form-urlencoded"
        else:
            data = json.dumps(body).encode() if body is not None else None
            ctype = "application/json"
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={**headers, "Content-Type": ctype, "Accept": "application/json",
                                              "User-Agent": "guardian/1.0"})
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        try:
            snippet = e.read().decode("utf-8", "replace")
        except Exception:
            snippet = ""
        raise PsaError(where, e.code, snippet.strip().splitlines()[0][:160] if snippet.strip() else "") from None
    except Exception as e:
        raise PsaError(where, 0, str(e)[:160]) from None
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        raise PsaError(where, 200, "not JSON: " + raw[:120].decode("utf-8", "replace")) from None


def _flavour(ctx: SendContext) -> str:
    return (ctx.config or {}).get("flavour") or "halopsa"


def _base(ctx: SendContext) -> str:
    return ((ctx.config or {}).get("base_url") or "").rstrip("/")


def _headers(ctx: SendContext) -> dict:
    """Per-vendor authentication. HaloPSA is OAuth2 client-credentials, so
    its bearer is fetched per tick and cached in `_sync` until it expires;
    the other two are static header sets built from the card."""
    cfg = ctx.config or {}
    f = _flavour(ctx)
    if f == "autotask":
        return {"ApiIntegrationCode": cfg.get("client_id", ""), "UserName": cfg.get("username", ""),
                "Secret": ctx.secret}
    if f == "connectwise":
        raw = f"{cfg.get('username', '')}:{ctx.secret}".encode()   # username is "company+publicKey"
        return {"Authorization": "Basic " + base64.b64encode(raw).decode(), "clientId": cfg.get("client_id", "")}
    return {"Authorization": "Bearer " + _halo_token(ctx)}


def _halo_token(ctx: SendContext) -> str:
    sync = sync_state(ctx)
    cached = sync.get("halo_token") or {}
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    if cached.get("token") and cached.get("expires", 0) > now + 60:
        return cached["token"]
    out = _request(f"{_base(ctx)}/auth/token", "POST", {}, {
        "grant_type": "client_credentials", "client_id": (ctx.config or {}).get("client_id", ""),
        "client_secret": ctx.secret, "scope": "all"}, form=True)
    token = (out or {}).get("access_token") or ""
    if not token:
        raise PsaError("POST /auth/token", 200, "no access_token in the reply; check client id and secret")
    sync["halo_token"] = {"token": token, "expires": now + float((out or {}).get("expires_in") or 3600)}
    return token


def _api(ctx: SendContext) -> str:
    f = _flavour(ctx)
    if f == "autotask":
        return f"{_base(ctx)}/ATServicesRest/V1.0"
    if f == "connectwise":
        return f"{_base(ctx)}/v4_6_release/apis/3.0"
    return f"{_base(ctx)}/api"



# --------------------------------------------------------------------------
# Discovery: the PSA's own lists, and typed names resolved against them
# --------------------------------------------------------------------------

def _companies(ctx: SendContext) -> list[tuple[str, str]]:
    """`[(id, name)]` of the customers this PSA knows."""
    f, api, h = _flavour(ctx), _api(ctx), _headers(ctx)
    if f == "autotask":
        out = _request(f"{api}/Companies/query", "POST", h,
                       {"filter": [{"op": "gt", "field": "id", "value": 0}], "MaxRecords": 500})
        return [(str(c.get("id")), str(c.get("companyName") or "")) for c in (out.get("items") or [])]
    if f == "connectwise":
        out = _request(f"{api}/company/companies?pageSize=1000&fields=id,name", "GET", h)
        return [(str(c.get("id")), str(c.get("name") or "")) for c in (out if isinstance(out, list) else [])]
    out = _request(f"{api}/Client?count=500", "GET", h)
    rows = out.get("clients") if isinstance(out, dict) else out
    return [(str(c.get("id")), str(c.get("name") or "")) for c in (rows or [])]


def _boards(ctx: SendContext) -> list[tuple[str, str]]:
    """`[(id, name)]` of the place a ticket lands: HaloPSA ticket types,
    Autotask queues, ConnectWise boards."""
    f, api, h = _flavour(ctx), _api(ctx), _headers(ctx)
    if f == "autotask":
        return _autotask_picklist(ctx, "queueID")
    if f == "connectwise":
        out = _request(f"{api}/service/boards?pageSize=1000&fields=id,name", "GET", h)
        return [(str(b.get("id")), str(b.get("name") or "")) for b in (out if isinstance(out, list) else [])]
    out = _request(f"{api}/TicketType", "GET", h)
    rows = out.get("tickettypes") if isinstance(out, dict) else out
    return [(str(b.get("id")), str(b.get("name") or "")) for b in (rows or [])]


def _statuses(ctx: SendContext) -> list[tuple[str, str, bool | None]]:
    """`[(id, name, closed)]`. `closed` is the vendor's own word for it
    where the vendor has one (ConnectWise's `closedFlag`, per board);
    None where only the name can say (HaloPSA, Autotask)."""
    f, api, h = _flavour(ctx), _api(ctx), _headers(ctx)
    if f == "autotask":
        return [(i, n, None) for i, n in _autotask_picklist(ctx, "status")]
    if f == "connectwise":
        board = _ref(ctx, "board", resolve=False)
        if not board:
            return []
        boards = _boards(ctx)
        bid = _match(board, boards, "board")[0]
        out = _request(f"{api}/service/boards/{bid}/statuses?pageSize=200&fields=id,name,closedFlag", "GET", h)
        return [(str(s.get("id")), str(s.get("name") or ""), bool(s.get("closedFlag")))
                for s in (out if isinstance(out, list) else [])]
    out = _request(f"{api}/Status", "GET", h)
    rows = out.get("statuses") if isinstance(out, dict) else out
    return [(str(s.get("id")), str(s.get("name") or ""), None) for s in (rows or [])]


def _autotask_picklist(ctx: SendContext, field: str) -> list[tuple[str, str]]:
    api, h = _api(ctx), _headers(ctx)
    out = _request(f"{api}/Tickets/entityInformation/fields", "GET", h)
    for fld in (out.get("fields") or []):
        if str(fld.get("name")) == field:
            return [(str(v.get("value")), str(v.get("label") or "")) for v in (fld.get("picklistValues") or [])
                    if v.get("isActive", True)]
    raise PsaError(f"GET /Tickets/entityInformation/fields", 200, f"no picklist named {field}")


def _match(typed: str, rows, what: str) -> tuple[str, str]:
    """Resolve a typed value against `[(id, name, ...)]`. An id that the
    list contains is taken as is; otherwise the name must match exactly
    (case-insensitively) or be the only substring match. Anything else
    lists the nearest names, because the fix is to copy one of them."""
    typed = (typed or "").strip()
    ids = {str(r[0]): r for r in rows}
    if typed in ids:
        return typed, ids[typed][1]
    fold = typed.casefold()
    exact = [r for r in rows if r[1].casefold() == fold]
    if len(exact) == 1:
        return str(exact[0][0]), exact[0][1]
    partial = [r for r in rows if fold and fold in r[1].casefold()]
    if len(partial) == 1:
        return str(partial[0][0]), partial[0][1]
    near = [r[1] for r in (exact or partial or rows)][:5]
    if not rows:
        raise PsaError(f"resolve {what}", 0, f"the PSA returned no {what}s at all; check the API user's permissions")
    hint = "; ".join(f"{n}" for n in near)
    raise PsaError(f"resolve {what}", 0,
                   f"'{typed}' matches {len(exact or partial)} {what}s, need exactly one -- nearest: {hint}")


def _ref(ctx: SendContext, name: str, *, resolve: bool = True) -> str:
    """The id to send for a card field that may hold a name. Cached in
    `_sync["resolved"]` keyed by what was typed, so a re-typed value is
    re-resolved and an unchanged one costs no call."""
    typed = str((ctx.config or {}).get(name) or "").strip()
    if not typed or not resolve:
        return typed
    cache = sync_state(ctx).setdefault("resolved", {})
    hit = cache.get(name)
    if isinstance(hit, dict) and hit.get("typed") == typed:
        return str(hit["id"])
    if name == "company_ref":
        rid, label = _match(typed, _companies(ctx), "company")
    elif name == "board":
        rid, label = _match(typed, _boards(ctx), "board")
    else:
        rid, label = _match(typed, _statuses(ctx), "status")
    cache[name] = {"typed": typed, "id": rid, "name": label}
    return rid


def _closed_ref(ctx: SendContext) -> tuple[str, str]:
    """`(id, name)` of the status a resolved ticket moves to: the card's
    `closed_status` (name or id) resolved against the PSA's list, else the
    vendor's stock closed value."""
    cfg = ctx.config or {}
    f = _flavour(ctx)
    if cfg.get("closed_status"):
        rid = _ref(ctx, "closed_status")
        return rid, (sync_state(ctx).get("resolved", {}).get("closed_status") or {}).get("name", rid)
    return DEFAULT_CLOSED[f], DEFAULT_CLOSED[f]


def _int_or_str(v: str):
    return int(v) if str(v).isdigit() else v


def _lookup(ctx: SendContext) -> dict:
    """The discovery the Test button runs: resolve every typed reference
    and say what it became. Raises `PsaError` at the first thing that does
    not resolve, with the candidates in the message."""
    out: dict = {"flavour": _flavour(ctx)}
    if _flavour(ctx) == "halopsa":
        _halo_token(ctx)
    _ref(ctx, "company_ref")
    resolved = sync_state(ctx).get("resolved", {})
    out["company"] = resolved.get("company_ref")
    if (ctx.config or {}).get("board"):
        _ref(ctx, "board")
        out["board"] = resolved.get("board")
    cid, cname = _closed_ref(ctx)
    out["closed"] = {"id": cid, "name": cname}
    return out


def _lookup_sentence(found: dict) -> str:
    names = {"halopsa": "HaloPSA", "autotask": "Autotask", "connectwise": "ConnectWise"}
    parts = [f"Connected to {names[found['flavour']]}."]
    c = found.get("company") or {}
    parts.append(f"Company '{c.get('typed')}' is id {c.get('id')}" + (f" ({c['name']})." if c.get("name") and c["name"] != c.get("typed") else "."))
    b = found.get("board")
    if b:
        parts.append(f"Board '{b.get('typed')}' is id {b.get('id')}" + (f" ({b['name']})." if b.get("name") and b["name"] != b.get("typed") else "."))
    cl = found.get("closed") or {}
    parts.append(f"Resolved tickets move to status {cl.get('name')}" + (f" ({cl['id']})." if str(cl.get('id')) != str(cl.get('name')) else "."))
    return " ".join(parts)


def _create(ctx: SendContext, summary: str, body: str) -> str:
    """File one ticket on the customer's PSA company; return its id. A
    reply without an id is a `PsaError` naming the keys that came back."""
    f, api, h = _flavour(ctx), _api(ctx), _headers(ctx)
    company = _int_or_str(_ref(ctx, "company_ref"))
    board = _ref(ctx, "board")
    if f == "autotask":
        payload = {"companyID": company, "title": summary[:255], "description": body[:8000], "status": 1, "priority": 2}
        if board:
            payload["queueID"] = _int_or_str(board)
        out = _request(f"{api}/Tickets", "POST", h, payload)
        item = out.get("itemId") if isinstance(out, dict) else None
        where = "POST /Tickets"
    elif f == "connectwise":
        payload = {"summary": summary[:100], "initialDescription": body, "company": {"id": company}}
        if board:
            payload["board"] = {"id": _int_or_str(board)}
        out = _request(f"{api}/service/tickets", "POST", h, payload)
        item = out.get("id") if isinstance(out, dict) else None
        where = "POST /service/tickets"
    else:
        payload = {"summary": summary[:255], "details": body, "client_id": company}
        if board:
            payload["tickettype_id"] = _int_or_str(board)
        out = _request(f"{api}/Tickets", "POST", h, [payload])
        rows = out if isinstance(out, list) else ((out or {}).get("tickets") or [])
        item = (rows[0] if rows else {}).get("id")
        where = "POST /api/Tickets"
    if item is None:
        keys = sorted((out[0] if isinstance(out, list) and out and isinstance(out[0], dict) else out).keys()) \
            if isinstance(out, (dict, list)) else [type(out).__name__]
        raise PsaError(where, 200, f"created nothing I can read an id from; reply keys: {', '.join(map(str, keys))[:120]}")
    return str(item)


def _note(ctx: SendContext, key: str, body: str) -> None:
    f, api, h = _flavour(ctx), _api(ctx), _headers(ctx)
    if f == "autotask":
        _request(f"{api}/Tickets/{key}/Notes", "POST", h,
                 {"ticketID": int(key), "title": "Vault", "description": body[:32000], "noteType": 1, "publish": 1})
    elif f == "connectwise":
        _request(f"{api}/service/tickets/{key}/notes", "POST", h, {"text": body, "internalAnalysisFlag": True})
    else:
        _request(f"{api}/Actions", "POST", h, [{"ticket_id": int(key), "note": body, "outcome": "Note",
                                                "hiddenfromuser": True}])


def _time_entry(ctx: SendContext, key: str, minutes: int, note: str) -> None:
    """A billable line against the ticket. Autotask requires a
    `resourceID` (the technician the time is booked to), taken from the
    card's `resource_ref`; without it Autotask rejects the entry, which is
    correct -- Vault must not book time to nobody."""
    cfg = ctx.config or {}
    f, api, h = _flavour(ctx), _api(ctx), _headers(ctx)
    hours = round(minutes / 60.0, 2)
    if f == "autotask":
        payload = {"ticketID": int(key), "dateWorked": dt.datetime.now(dt.timezone.utc).date().isoformat(),
                   "hoursWorked": hours, "summaryNotes": note[:8000]}
        if cfg.get("resource_ref"):
            payload["resourceID"] = _int_or_str(str(cfg["resource_ref"]))
        _request(f"{api}/TimeEntries", "POST", h, payload)
    elif f == "connectwise":
        _request(f"{api}/time/entries", "POST", h, {
            "chargeToId": int(key), "chargeToType": "ServiceTicket", "actualHours": hours, "notes": note,
            "company": {"id": _int_or_str(_ref(ctx, "company_ref"))}})
    else:
        _request(f"{api}/Actions", "POST", h, [{"ticket_id": int(key), "note": note, "outcome": "Note",
                                                "timetaken": hours, "hiddenfromuser": True}])


def _resolve(ctx: SendContext, key: str) -> None:
    f, api, h = _flavour(ctx), _api(ctx), _headers(ctx)
    closed, closed_name = _closed_ref(ctx)
    if f == "autotask":
        _request(f"{api}/Tickets", "PATCH", h, {"id": int(key), "status": _int_or_str(closed)})
    elif f == "connectwise":
        value = {"id": int(closed)} if str(closed).isdigit() else {"name": closed_name}
        _request(f"{api}/service/tickets/{key}", "PATCH", h, [{"op": "replace", "path": "status", "value": value}])
    else:
        _request(f"{api}/Tickets", "POST", h, [{"id": int(key), "status_id": _int_or_str(closed)}])


def _status(ctx: SendContext, key: str) -> str | None:
    """`"open"`, `"closed"`, or None when the ticket could not be read
    (deleted, merged, or the PSA is down). Only "closed" is ever acted on.
    ConnectWise says closed in its own words (`closedFlag`); the other two
    are compared against the resolved closed status."""
    f, api, h = _flavour(ctx), _api(ctx), _headers(ctx)
    closed, closed_name = _closed_ref(ctx)
    try:
        if f == "autotask":
            out = _request(f"{api}/Tickets/{key}", "GET", h)
            item = (out or {}).get("item") or {}
            if not item:
                return None
            return "closed" if str(item.get("status")) == str(closed) else "open"
        if f == "connectwise":
            out = _request(f"{api}/service/tickets/{key}", "GET", h)
            if not out:
                return None
            st = out.get("status") or {}
            if out.get("closedFlag") or str(st.get("id")) == str(closed) or st.get("name") == closed_name:
                return "closed"
            return "open"
        out = _request(f"{api}/Tickets/{key}", "GET", h)
        if not out:
            return None
        return "closed" if str(out.get("status_id")) == str(closed) else "open"
    except PsaError as e:
        if e.status == 404:
            return None
        raise


def _escalated(alert: "m.Alert", bar: str) -> bool:
    if alert.status not in ("open", "acked"):
        return False
    if alert.sev == "CRIT" or alert.escalated_at is not None:
        return True
    return bar == "CRIT+WARN" and alert.sev == "WARN"


def _summary(a: "m.Alert") -> str:
    return f"[{a.sev}] {a.msg}"[:255]


def _body(a: "m.Alert", ctx: SendContext) -> str:
    lines = [a.msg]
    if a.why:
        lines += ["", a.why]
    lines += ["", f"Vault alert: {a.key}", f"First seen: {a.first_seen}",
              f"{ctx.base_url.rstrip('/')}/inbox"]
    return "\n".join(lines)


def _fix_for(db, tenant, a: "m.Alert") -> "m.Fix | None":
    """The successful Fix that is the catalogued remedy for this alert,
    recorded since the alert first fired. Same matching rule as
    `routers/sensor._settle_fixed_alerts`, so a fix that closed the alert
    on the dashboard is the one that reaches the ticket."""
    found = remedy.fix_for_key(networks.canonical_lan_key(a.key or ""))
    if not found:
        return None
    action, host, arg = found
    q = (db.query(m.Fix).filter(m.Fix.tenant_id == tenant.id, m.Fix.host == host, m.Fix.action == action,
                                 m.Fix.ok == True, m.Fix.ts >= a.first_seen))   # noqa: E712
    if arg:
        q = q.filter(m.Fix.arg == arg)
    return q.order_by(m.Fix.ts.asc()).first()


class PsaIntegration(Integration):
    title = "PSA"
    help = ("One ticket per escalated finding on your PSA, fixes noted on it (time entries optional, off by "
            "default), resolved when Vault resolves it; a ticket closed on the PSA acks the finding here. "
            "Type the company, board and closed status by name as they appear in the PSA, then press Test: "
            "it resolves each to the PSA's own id and says what it found. "
            "Written to the vendors' documented REST APIs, not yet verified against a live tenant.")
    direction = "out+status sync"
    fields = [
        IntegrationField(name="secret", label="API secret / private key / client secret", kind="secret", required=True),
        IntegrationField(name="flavour", label="PSA", kind="select", required=True, options=list(FLAVOURS)),
        IntegrationField(name="base_url", label="Base URL", kind="url", required=True),
        IntegrationField(name="client_id", label="Client id (Halo) / integration code (Autotask) / clientId (ConnectWise)", kind="text", required=True),
        IntegrationField(name="username", label="API user (Autotask) / company+publicKey (ConnectWise)", kind="text"),
        IntegrationField(name="company_ref", label="This customer's company on the PSA (name or id)", kind="text", required=True),
        IntegrationField(name="board", label="Board / queue / ticket type (name or id)", kind="text"),
        IntegrationField(name="escalate", label="File a ticket for", kind="select", options=["CRIT", "CRIT+WARN"]),
        IntegrationField(name="time_entries", label="Book time for fixes", kind="select", options=["off", "on"]),
        IntegrationField(name="minutes", label="Minutes per fix", kind="text"),
        IntegrationField(name="resource_ref", label="Technician id for time entries (Autotask)", kind="text"),
        IntegrationField(name="closed_status", label="Closed status (name or id; blank = the vendor's stock one)", kind="text"),
    ]

    def validate(self, fields: dict) -> str:
        f = (fields or {}).get("flavour", "")
        if f and f not in FLAVOURS:
            return "flavour must be one of " + ", ".join(FLAVOURS)
        minutes = str((fields or {}).get("minutes") or "").strip()
        if minutes and (not minutes.isdigit() or int(minutes) <= 0 or int(minutes) > 480):
            return "minutes must be a whole number between 1 and 480"
        return urlpolicy.check_url((fields or {}).get("base_url") or "", "Base URL")

    def send(self, ctx: SendContext, msg: Message) -> SendResult:
        # Same ruling as Jira: this kind syncs tickets, it does not relay
        # every alert as a push, so severity routing must not file noise.
        return SendResult(True, "")

    def test(self, ctx: SendContext) -> SendResult:
        """The Test button is the first live tenant's setup check: it
        connects, resolves every typed name to the PSA's own id, and says
        what it found. It never files a "Vault test" ticket on a
        customer's board -- a stray ticket is the one thing an MSP notices."""
        try:
            found = _lookup(ctx)
        except PsaError as e:
            return SendResult(False, str(e)[:300])
        return SendResult(True, "", detail=_lookup_sentence(found))

    def watchdog(self, ctx: SendContext) -> "SendResult | None":
        """One tick. A `PsaError` on one alert is recorded and the tick
        moves on to the next, so one malformed ticket cannot stall every
        other customer's sync; the FIRST error becomes `last_error` (the
        registry writes a failed result there) so the card shows it."""
        db, tenant, cfg = ctx.db, ctx.tenant, ctx.config or {}
        sync = sync_state(ctx)
        noted: set = set(sync.get("noted_fix_ids") or [])
        resolved: set = set(sync.get("resolved_tickets") or [])
        acked: set = set(sync.get("acked_from_psa") or [])
        bar = cfg.get("escalate") or "CRIT"
        book_time = (cfg.get("time_entries") or "off") == "on"
        minutes = int(cfg.get("minutes") or 15)
        who = f"psa:{_flavour(ctx)}"
        first_error = ""

        alerts = db.query(m.Alert).filter(m.Alert.tenant_id == tenant.id).all()
        for a in alerts:
            try:
                key = a.ticket_key or ""
                if not key and _escalated(a, bar):
                    key = _create(ctx, _summary(a), _body(a, ctx))
                    a.ticket_key = key
                if not key:
                    continue

                fix = _fix_for(db, tenant, a)
                if fix is not None and fix.id not in noted:
                    line = f"Fix applied by Vault: {fix.label or fix.action} on {fix.host} ({fix.by}), {fix.ts}."
                    _note(ctx, key, line)
                    if book_time:
                        _time_entry(ctx, key, minutes, line)
                    noted.add(fix.id)

                if a.status == "resolved" and key not in resolved:
                    _resolve(ctx, key)
                    resolved.add(key)
                elif a.status == "open" and key not in resolved and key not in acked:
                    # PSA -> Vault, the conservative half: a technician
                    # closing the ticket acks the finding, never resolves it.
                    if _status(ctx, key) == "closed":
                        from ..routers.write import apply_alert_op
                        apply_alert_op(db, tenant, a.key, "ack", who, "", ip="psa-sync")
                        acked.add(key)
            except PsaError as e:
                first_error = first_error or f"{a.key}: {e}"
                if "resolve " in e.where:
                    break   # a name that does not resolve fails every alert the same way

        sync["noted_fix_ids"] = sorted(noted)
        sync["resolved_tickets"] = sorted(resolved)
        sync["acked_from_psa"] = sorted(acked)
        db.commit()
        return SendResult(False, first_error[:300]) if first_error else None


@register
class HaloPsaIntegration(PsaIntegration):
    kind = "halopsa"
    title = "HaloPSA"


@register
class AutotaskIntegration(PsaIntegration):
    kind = "autotask"
    title = "Autotask"


@register
class ConnectWiseIntegration(PsaIntegration):
    kind = "connectwise"
    title = "ConnectWise Manage"
