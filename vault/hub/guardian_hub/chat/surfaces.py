"""The handlers that give Ask Vault the same reach as the dashboard.

Spec: docs/superpowers/specs/2026-09-20-chat-full-surface-design.md

WHY THIS IS A SEPARATE MODULE

`tools.py` was already 1144 lines before this work and these handlers roughly
double the handler count. Splitting on "the ones added to close the coverage
gap" would be an arbitrary seam that stops meaning anything in a month. The
seam that does mean something is the one in the spec: **a handler here turns a
customer's sentence into a dashboard route's arguments, and the route's
`HTTPException` back into a sentence.** It owns no logic of its own. That is a
single, checkable property, and it is why `TOOLS` still lives in `tools.py` --
there is one registry, not two.

THE RULE EVERY HANDLER HERE FOLLOWS

Call the route function; never reimplement it. The route owns the audit row,
the `Edit` row the sensor syncs from, the live-bus publish, the validation and
the role check. A handler that re-derives any of those is a second copy, and
the drift is silent in the worst way: chat keeps appearing to work while
quietly not writing the audit trail the dashboard writes, so the one record
that would show a customer what the assistant did to their network is the
record that is missing.

THE ONE PLACE THAT IS NOT A MIRROR

`h_reset_baseline` is tier 3 although `POST /reset-lan-baseline` is a plain
admin POST. See its docstring -- the reason is the warrant rule, not the route.
"""

from __future__ import annotations

from fastapi import HTTPException

from .. import models as m, networks, onboarding
from ..payloads import security as security_payload
from ..routers import write as write_routes
from ..scans import latest_scan

# Imported lazily inside handlers where the module is heavy or only needed on
# one path; see each call site.


def _plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one}" if n == 1 else f"{n} {many or one + 's'}"


# --------------------------------------------------------------------------
# Write -- "fix anything"
# --------------------------------------------------------------------------

def h_dns_filtering(ctx, domain: str, kind: str = "block", op: str = "add"):
    """Add or remove one allow/block rule, through `POST /dns/rules`.

    The gap this closes is the sharpest one in the product. Vault shows a
    standing line on the dashboard saying DNS filtering is off and the customer
    should switch it on -- and until now the assistant sitting beside that line
    could not switch it on. A product that nags about a feature its own
    assistant cannot reach is worse than one that never nagged, because the
    nag has taught the customer that Vault knows and is not helping.
    """
    from .tools import ToolResult, _chat_ctx, _FakeRequest

    kind = (kind or "block").strip().lower()
    op = (op or "add").strip().lower()
    try:
        out = write_routes.dns_rules(
            write_routes.DnsRuleIn(kind=kind, domain=domain, op=op),
            _FakeRequest(ctx.request_ip), ctx=_chat_ctx(ctx), db=ctx.db)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))

    verb = "Blocked" if kind == "block" else "Allowed"
    if op == "remove":
        text = (f"Removed the {kind} rule for {domain}. It is back to whatever the "
                f"filtering categories say about it.")
    else:
        text = (f"{verb} {domain} on every device on this network. "
                + ("Nothing here will be able to load it."
                   if kind == "block"
                   else "It will load even if a filtering category would have refused it."))
    text += (f" The list now holds {_plural(len(out['block']), 'blocked domain')} and "
             f"{_plural(len(out['allow']), 'allowed domain')}.")
    return ToolResult(ok=True, data=out, text=text)


def h_dns_exception(ctx, domain: str, hours: int = 24, remove: bool = False):
    """Open one domain for a bounded window, through `POST /dns/exceptions`.

    Separate from `dns_filtering` rather than a flag on it, because they are
    different promises: an allow rule stands until somebody removes it, and an
    exception expires on its own. A customer who says "let me get to this one
    supplier site this afternoon" is asking for the second, and giving them the
    first would quietly leave a hole open for years.
    """
    from .tools import ToolResult, _chat_ctx, _FakeRequest

    try:
        hours = int(hours)
    except (TypeError, ValueError):
        return ToolResult(ok=False, text="Tell me how many hours to open it for, between 1 and 168.")
    try:
        write_routes.dns_exception(
            write_routes.DnsExceptionIn(domain=domain, hours=hours, remove=bool(remove)),
            _FakeRequest(ctx.request_ip), ctx=_chat_ctx(ctx), db=ctx.db)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))

    if remove:
        return ToolResult(ok=True, text=f"Closed the exception for {domain}. Filtering applies to it again.")
    return ToolResult(ok=True, text=(f"Opened {domain} for {_plural(hours, 'hour')}. It closes itself after "
                                     f"that -- you do not have to remember to put it back."))


def h_cancel_work(ctx, job_id: str):
    """Stop a queued or running job, through `POST /jobs/{qid}/cancel`."""
    from .tools import ToolResult, _chat_ctx, _FakeRequest

    try:
        write_routes.cancel_job(job_id, _FakeRequest(ctx.request_ip), ctx=_chat_ctx(ctx), db=ctx.db)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    return ToolResult(ok=True, text="Cancelled. Nothing from that job will run.")


def h_retry_work(ctx, job_id: str):
    """Re-queue a failed job with its original arguments, through
    `POST /jobs/{qid}/retry`.

    The route refuses a job that did not fail and any disruptive `TIER3_FIXES`
    action -- that class has already spent its one confirmation, so a retry
    would be a second unconfirmed run of something a person said yes to once.
    The refusal is the route's, deliberately: putting the same rule here would
    be the second copy this module exists to avoid.
    """
    from .tools import ToolResult, _chat_ctx, _FakeRequest

    try:
        out = write_routes.job_retry(job_id, _FakeRequest(ctx.request_ip),
                                     ctx=_chat_ctx(ctx), db=ctx.db)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    new_id = (out or {}).get("id") if isinstance(out, dict) else None
    return ToolResult(ok=True, queue_id=new_id, data=out if isinstance(out, dict) else {},
                      text="Queued it again with the same arguments. I will tell you how it goes.")


def h_set_importance(ctx, target: str, importance: str, network: str = ""):
    """Mark how much a device matters, through `POST /devices/{mac}/importance`.

    Importance is machine-readable ranking, not a human warrant: it changes
    what Vault shows first, never what Vault checks. That is why it is
    tier 2 and `trust_device` is what it is -- see the warrant rule in
    CLAUDE.md.
    """
    from .tools import ToolResult, _chat_ctx, _FakeRequest, _resolve_device

    dev, problem = _resolve_device(ctx, target)
    if dev is None:
        return ToolResult(ok=False, text=problem)
    try:
        write_routes.set_device_importance(
            dev.mac, write_routes.AssetImportanceIn(importance=(importance or "").strip().lower()),
            _FakeRequest(ctx.request_ip), network=network, ctx=_chat_ctx(ctx), db=ctx.db)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    shown = dev.label or dev.name or dev.ip or dev.mac
    return ToolResult(ok=True, text=(f"Marked {shown} as {importance}. It changes what I put in front of you "
                                     f"first; it does not change what I check on it."))


def h_reset_baseline(ctx):
    """Re-anchor what "a new device" means, through `POST /reset-lan-baseline`.

    **Tier 3, although the route is a plain admin POST.** This is the one
    handler in this module that is deliberately stricter than the route it
    mirrors, and the reason is the warrant rule rather than the route's guard.

    `home.py` decides a device is new by comparing it against the baseline.
    Re-anchoring marks every device currently on the network as
    known-from-the-start -- which is the same suppression `trusted` performs,
    applied to the whole estate at once, by one sentence. It cannot be undone
    by re-running anything (the old anchor is gone), and afterwards there is
    nothing on any screen that says it happened. Silent, wide and
    irreversible is exactly the shape the confirmation tier exists for, and
    an LLM choosing the argument is exactly the caller it exists to sit in
    front of.
    """
    from .tools import ToolResult, _chat_ctx, _FakeRequest

    try:
        out = write_routes.reset_lan_baseline(_FakeRequest(ctx.request_ip),
                                              ctx=_chat_ctx(ctx), db=ctx.db)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    return ToolResult(ok=True, data=out if isinstance(out, dict) else {},
                      text=("Re-anchored the baseline. Everything on the network right now counts as "
                            "known from today, so I will only flag devices that appear after this."))


def h_send_sensor_invite(ctx, network: str):
    """Email the sensor install instructions for one network, through
    `POST /onboarding/networks/{id}/invite`."""
    from .tools import ToolResult, _chat_ctx, _FakeRequest, _resolve_network

    row, problem = _resolve_network(ctx, network)
    if row is None:
        return ToolResult(ok=False, text=problem)
    try:
        out = write_routes.invite_sensor(row.id, write_routes.SensorInviteIn(),
                                         _FakeRequest(ctx.request_ip),
                                         ctx=_chat_ctx(ctx), db=ctx.db)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    out = out if isinstance(out, dict) else {}
    if out.get("emailed"):
        text = (f'Sent the install instructions for "{row.name}" to your address. '
                f"The code inside is single-use and expires in a week.")
    else:
        # Mail is optional in this product and a claim that an email was sent
        # when none was is exactly the kind of false promise
        # `test_chat_no_false_claims.py` exists to catch.
        text = (f'Made an install code for "{row.name}", but I could not send the email -- '
                f"mail is not configured on this hub. You can copy it from Settings instead.")
    return ToolResult(ok=True, data=out, text=text)


# --------------------------------------------------------------------------
# Read -- "advise about anything"
# --------------------------------------------------------------------------

def h_threat_detail(ctx, kind: str, days: int = 7):
    """What Vault found in one attack class, and how each finding was
    settled -- the Security page's drawer, in words.

    Until now this was reachable only by clicking the card. An assistant that
    can *run* a repair but cannot read the finding it repaired cannot advise
    about it, which is the half of the request that "advise about anything"
    names.
    """
    from .tools import ToolResult

    key = (kind or "").strip().lower()
    known = [t["key"] for t in security_payload.THREAT_SPEC]
    if key not in known:
        return ToolResult(ok=False, text=("I track these attack classes: " + ", ".join(known) +
                                          ". Which one do you mean?"))
    try:
        days = max(1, min(int(days), 90))
    except (TypeError, ValueError):
        days = 7
    d = security_payload.threat_detail(ctx.db, ctx.tenant, key, days=days)

    lines = [f"{d.label} over the last {_plural(d.days, 'day')}: "
             f"{d.open_count} open, {d.resolved_count} settled, {d.blocked} blocked by DNS filtering.",
             d.what]
    # `limit` is carried on purpose. For the classes Vault can only observe
    # rather than stop, the honest answer to "how did you solve it" is that it
    # did not -- dropping that here would turn an honest card into a
    # misleading answer, which is the same argument ThreatMatrix makes.
    if d.limit:
        lines.append(f"What it cannot do: {d.limit}")
    for f in d.findings[:12]:
        if f.resolved:
            how = (f"Vault ran {f.fix}" if f.fix
                   else (f"settled by {f.resolved_by}" if f.resolved_by else "settled without a repair"))
            lines.append(f"- [settled] {f.title} -- {how}")
        else:
            lines.append(f"- [{f.severity or 'open'}] {f.title}" + (f" -- {f.why}" if f.why else ""))
    if len(d.findings) > 12:
        lines.append(f"...and {len(d.findings) - 12} more on the Security page.")
    return ToolResult(ok=True, data=d.model_dump(), text="\n".join(lines))


def h_work_status(ctx):
    """What Vault is doing right now: queued, running, failed."""
    from .tools import ToolResult
    from ..payloads import jobs as jobs_payload

    rows = (ctx.db.query(m.Queue).filter_by(tenant_id=ctx.tenant.id)
            .order_by(m.Queue.ts.desc()).limit(25).all())
    if not rows:
        return ToolResult(ok=True, text="Nothing queued or running. Vault is idle.")
    buckets: dict[str, list] = {}
    for q in rows:
        buckets.setdefault(q.status or "queued", []).append(q)
    lines = []
    for status in ("running", "queued", "failed", "done", "cancelled"):
        got = buckets.get(status) or []
        if not got:
            continue
        lines.append(f"{status}: {len(got)}")
        for q in got[:4]:
            label = jobs_payload.human_label(q.path, q.body or {}, q.result)
            lines.append(f"  - {label} ({q.id})")
    return ToolResult(ok=True, data={"jobs": [{"id": q.id, "status": q.status, "path": q.path} for q in rows]},
                      text="\n".join(lines))


def h_setup_status(ctx):
    """How far through setup each network is, and the one thing to do next.

    Reads `onboarding.next_action` rather than deciding here, because that
    function already encodes the order in which a network becomes useful --
    exists, then watched, then reporting, then filtered -- across every
    network. A second ordering in chat would tell a customer to do one thing
    while the wizard on the same screen told them another.
    """
    from .tools import ToolResult

    nets = onboarding.progress(ctx.db, ctx.tenant)
    if not nets:
        return ToolResult(ok=True, text=("No networks declared yet. Tell me what to call the first one "
                                         "and I will add it."))
    sensor_words = {
        "none": "no sensor yet",
        "pending": "an install code is out, not yet used",
        "active": "sensor active",
        "quiet": "sensor bound but silent -- it has stopped reporting",
    }
    lines = []
    for n in nets:
        bits = [sensor_words.get(n.sensor, n.sensor),
                "DNS filtering on" if n.dns == "working" else "DNS filtering off"]
        if n.devices:
            bits.append(_plural(n.devices, "device") + " seen")
        lines.append(f"{n.name or n.slug}: " + ", ".join(bits))

    nxt = onboarding.next_action(nets)
    if nxt is None:
        lines.append("Nothing left to set up.")
    else:
        where = next((n.name or n.slug for n in nets if n.id == nxt.network_id), "")
        on = f" on {where}" if where else ""
        lines.append({
            "declare_network": "Next: tell me the name of your first network and I will add it.",
            "enrol_sensor": f"Next: get a sensor running{on}. Ask me to send the install instructions.",
            "await_first_scan": f"Next: waiting for the first scan{on}. Nothing for you to do.",
            "offer_dns": f"Next: switch on DNS filtering{on}. Ask me and I will set the rules up.",
        }.get(nxt.action, f"Next: {nxt.action}{on}."))
    return ToolResult(ok=True, data={"networks": [n.model_dump() for n in nets]}, text="\n".join(lines))


def h_dns_activity(ctx, days: int = 7):
    """What DNS filtering is actually doing: how much it refused, what it
    refused, and anything looking like beaconing."""
    from .tools import ToolResult
    from ..payloads import dnsview

    scan = latest_scan(ctx.db, ctx.tenant.id)
    raw = (scan.raw or {}) if scan else {}
    st = dnsview.status(raw)

    if not st.enabled:
        head = "DNS filtering is not switched on for this network."
    elif not st.running:
        head = "DNS filtering is configured but the resolver is not running, so nothing is being filtered."
    else:
        head = (f"DNS filtering is on: {st.queries_24h} lookups in the last 24 hours, "
                f"{st.blocked_24h} refused ({st.block_pct:.1f}%), "
                f"{st.total_blocked_domains} domains on the block lists.")
    lines = [head]

    blocked = dnsview.blocked_top(raw)
    if blocked:
        lines.append("Most-refused: " + ", ".join(f"{b.name} ({b.count})" for b in blocked[:8]))
    top = dnsview.top(raw)
    if top:
        lines.append("Busiest: " + ", ".join(f"{b.name} ({b.count})" for b in top[:8]))
    beacon = dnsview.beaconing(raw)
    if beacon:
        # Named rather than counted: a regular, machine-like lookup pattern is
        # only interesting if the customer can recognise the domain.
        lines.append("Regular machine-like lookups (possible beaconing): "
                     + ", ".join(b.name for b in beacon[:5]))
    sus = dnsview.suspicious(raw)
    if sus:
        lines.append("Suspicious: " + ", ".join(b.name for b in sus[:5]))
    return ToolResult(ok=True, data=st.model_dump(), text="\n".join(lines))


def h_trend(ctx, days: int = 30):
    """Is it getting better? Reads the same rows the History tab draws.

    Goes through `payloads/history` rather than querying, because that module
    is deliberately the ONLY place a trend is computed -- three surfaces
    already draw these rows at different widths, and a fourth definition of
    "new alerts over 30 days" would be discovered by the customer rather than
    by us. `window` is called for the same reason: it clamps the span, so a
    chat answer labelled "30 days" covers the span the customer's own History
    tab labels "30 days".
    """
    from .tools import ToolResult
    from ..payloads import history

    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 30
    days, _hours, cutoff, bucket = history.window(days=days)
    charts = history.build_charts(ctx.db, ctx.tenant.id, cutoff, bucket=bucket)

    lines = []
    for c in charts:
        for s in c.series:
            pts = s.points
            if len(pts) < 2:
                continue
            first, last = pts[0].value, pts[-1].value
            name = f"{c.label} ({s.name})" if s.name and s.name != c.label else c.label
            if first == last:
                move = f"steady at {last}{c.unit}"
            elif last < first:
                move = f"down from {first}{c.unit} to {last}{c.unit}"
            else:
                move = f"up from {first}{c.unit} to {last}{c.unit}"
            lines.append(f"- {name}: {move}")
    if not lines:
        return ToolResult(ok=True, text=(f"I do not hold enough history yet to show a trend over "
                                         f"{_plural(days, 'day')}. Ask me again in a few days."))
    return ToolResult(ok=True, text=f"Over the last {_plural(days, 'day')}:\n" + "\n".join(lines[:14]))


def h_network_map(ctx):
    """What is plugged into what.

    Read off `payloads/topology`'s own node list rather than re-walking
    devices, so the shape chat describes is the shape the Topology tab draws.
    Grouped by `ring` because that is the field carrying the layering the
    diagram shows -- internet, gateway, infrastructure, then everything
    hanging off it -- and a flat list of forty device names answers nothing.
    """
    from .tools import ToolResult
    from ..payloads import topology

    tree = topology.build(ctx.db, ctx.tenant)
    if not tree.nodes:
        return ToolResult(ok=True, text=("I have not mapped this network yet -- no scan has reported "
                                         "a layout."))
    rings = {0: "Internet", 1: "Gateway", 2: "Infrastructure", 3: "Devices"}
    by_ring: dict[int, list] = {}
    for n in tree.nodes:
        by_ring.setdefault(n.ring, []).append(n)

    lines = []
    for ring in sorted(by_ring):
        got = by_ring[ring]
        lines.append(f"{rings.get(ring, f'Ring {ring}')} ({len(got)}):")
        for n in got[:10]:
            bits = [n.label or n.ip or n.id]
            if n.detail:
                bits.append(n.detail)
            # Only the states worth interrupting for. "ok" on forty rows is
            # noise, and the quiet-when-healthy rule applies to sentences too.
            if n.state in ("warn", "crit", "offline"):
                bits.append(n.state)
            if n.quarantined:
                bits.append("quarantined")
            lines.append("  - " + " -- ".join(bits))
        if len(got) > 10:
            lines.append(f"  ...and {len(got) - 10} more")
    return ToolResult(ok=True, text="\n".join(lines))


def h_update_status(ctx):
    """What needs patching, and what is waiting on a reboot."""
    from .tools import ToolResult
    from ..payloads import updates

    hosts = updates.hosts(ctx.db, ctx.tenant)
    lines = []
    for h in hosts:
        if not (h.pending or h.security_pending or h.reboot_required):
            continue
        bits = []
        if h.pending:
            bits.append(_plural(h.pending, "update") + " waiting")
        if h.security_pending:
            bits.append(f"{h.security_pending} of them security")
        if h.cve_fixable:
            bits.append(f"{h.cve_fixable} fixable CVEs")
        if h.reboot_required:
            bits.append("needs a reboot")
        lines.append(f"- {h.name}: " + ", ".join(bits))

    cves = updates.cves(ctx.db, ctx.tenant)
    if cves:
        lines.append(f"{_plural(len(cves), 'known CVE')} across these hosts, worst first: "
                     + ", ".join(f"{c.cve} ({c.package} on {c.host})" for c in cves[:5]))
    if not lines:
        return ToolResult(ok=True, text=("Nothing is waiting to be patched on the hosts Vault can "
                                         "reach, and none of them need a reboot."))
    return ToolResult(ok=True, text="Updates waiting:\n" + "\n".join(lines))


def h_bulk_alerts(ctx, keys: list | None = None, op: str = "ack", hours: int | None = None):
    """Acknowledge, snooze, resolve or reopen several alerts at once, through
    `POST /alerts/bulk`.

    Exists for the same reason `trust_devices` does: the single-alert tools
    take exactly one `key`, so "ack all the low ones" had no honest shape and
    the model's only way to comply was to pick one alert and pretend that was
    the answer. Silently narrowing "all" to one arbitrary row is worse than
    refusing, because the person who asked cannot see which row was chosen.
    """
    from .tools import ToolResult, _chat_ctx, _FakeRequest

    keys = [str(k) for k in (keys or []) if str(k).strip()]
    if not keys:
        return ToolResult(ok=False, text="Tell me which alerts -- I need their keys.")
    try:
        out = write_routes.alert_bulk(
            write_routes.BulkAlertOp(keys=keys, op=(op or "ack").strip().lower(), hours=hours),
            _FakeRequest(ctx.request_ip), ctx=_chat_ctx(ctx), db=ctx.db)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))

    out = out if isinstance(out, dict) else {}
    n = int(out.get("updated") or 0)
    # Reported, not rounded up to what was asked for. A key that matched
    # nothing is the usual cause, and a customer told "done" for eight when
    # six moved has been told something false.
    text = f"{_plural(n, 'alert')} moved to {op}."
    if n < len(keys):
        text += f" {len(keys) - n} of the {len(keys)} keys you gave me matched nothing here."
    return ToolResult(ok=True, data=out, text=text)


def h_decide_check(ctx, key: str, op: str = "leave", reason: str = "",
                   network: str = "", target: str = "", expires_at: str = ""):
    """Record the owner's own call on a failing security check, or undo it,
    through `POST /security/checks/{op}`.

    `leave` is an exception, not a fix: the check still fails and Vault
    still knows it fails -- what changes is that a person has said, in
    writing, that they accept it. That is why the route insists on a reason
    and the tool does not invent one. A blank reason reaching the route is
    refused there, and the refusal is the right answer rather than something
    to paper over here: an accepted risk with no recorded reason is
    indistinguishable, six months later, from one nobody noticed.
    """
    from .tools import ToolResult, _chat_ctx, _FakeRequest
    from ..routers import security as security_routes

    op = (op or "leave").strip().lower()
    try:
        security_routes.check_decision_op(
            op, security_routes.CheckDecisionIn(key=key, network=network, target=target,
                                                reason=reason, expires_at=expires_at),
            _FakeRequest(ctx.request_ip), ctx=_chat_ctx(ctx), db=ctx.db)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))

    if op == "undo":
        return ToolResult(ok=True, text=(f"Reopened {key}. It counts against the score again and I will "
                                         f"keep raising it."))
    return ToolResult(ok=True, text=(f'Recorded that you are leaving {key} as it is, because: "{reason}". '
                                     f"It stops being raised, and the reason stays on the record."))


def h_block_attacker(ctx, method: str = "auto"):
    """Choose what Vault does to a host attacking this network, through
    `POST /attacker`.

    A standing policy, not a one-off block: it decides what happens the next
    time, unattended. `off` is offered plainly because a customer who wants
    Vault to stop firewalling things needs a way to say so that is not
    uninstalling the product.
    """
    from .tools import ToolResult, _chat_ctx, _FakeRequest

    method = (method or "auto").strip().lower()
    try:
        write_routes.attacker_method(write_routes.AttackerMethodIn(method=method),
                                     _FakeRequest(ctx.request_ip), ctx=_chat_ctx(ctx), db=ctx.db)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    if method == "off":
        return ToolResult(ok=True, text=("Vault will no longer block hosts that attack this network. "
                                         "It still tells you about them."))
    return ToolResult(ok=True, text=(f"Vault will answer an attacking host with {method} from now on. "
                                     f"Ask me for 'off' to stop it acting on its own."))


def h_raise_ticket(ctx, subject: str, body: str, priority: str = "normal"):
    """Open a support ticket with the people who run Vault, through
    `POST /support/tickets`.

    This is the "report anything" half of the ask, and it is the one tool here
    whose whole point is that Vault cannot solve the problem itself. It
    matters most exactly where the assistant is least able to help -- a dead
    sensor is `remedy`'s `human` tier, because every fix Vault runs travels
    through the sensor -- and "I cannot do anything about that" is a much
    worse answer than the same sentence followed by a ticket.

    Viewer, mirroring the route. Anyone who can see that something is wrong
    can say so; requiring an operator to report a problem would mean the
    person most likely to notice is the one who cannot tell anybody.
    """
    from .tools import ToolResult, _chat_ctx, _FakeRequest
    from ..routers import tickets as ticket_routes

    try:
        out = ticket_routes.open_ticket(
            ticket_routes.TicketIn(subject=subject, body=body,
                                   priority=(priority or "normal").strip().lower()),
            _FakeRequest(ctx.request_ip), ctx=_chat_ctx(ctx), db=ctx.db)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    tid = getattr(getattr(out, "ticket", None), "id", "")
    return ToolResult(ok=True, data={"id": tid},
                      text=(f'Raised support ticket "{subject}". You can follow it under Support, and '
                            f"you will get a reply there."))


def h_reply_ticket(ctx, ticket_id: str, body: str):
    """Add a message to a support ticket already open, through
    `POST /support/tickets/{ticket_id}/reply`."""
    from .tools import ToolResult, _chat_ctx, _FakeRequest
    from ..routers import tickets as ticket_routes

    try:
        ticket_routes.my_reply(ticket_id, ticket_routes.ReplyIn(body=body),
                               _FakeRequest(ctx.request_ip), ctx=_chat_ctx(ctx), db=ctx.db)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    return ToolResult(ok=True, text="Added that to the ticket.")


def h_camera_status(ctx):
    """How many cameras Vault can see, how many it expected, and which
    recorder is quiet -- Overview's camera census, in words.

    `unobserved` is carried separately from `offline` on purpose, and the
    wording keeps them apart: a recorder that has gone quiet reads as "N
    cameras unaccounted for", never as "there are now fewer cameras". The
    second sentence is the one that gets a practice to stop worrying at the
    exact moment it should start.
    """
    from .tools import ToolResult
    from ..payloads import cameras

    scan = latest_scan(ctx.db, ctx.tenant.id)
    raw = (scan.raw or {}) if scan else {}
    c = cameras.census(ctx.db, ctx.tenant, raw)
    if not (c.known or c.expected):
        return ToolResult(ok=True, data=c.model_dump(),
                          text="Vault has not seen any cameras or recorders on this network.")
    lines = [f"{_plural(c.known, 'camera')} seen, {c.online} online" +
             (f", {c.offline} offline" if c.offline else "") + "."]
    if c.unobserved:
        lines.append(f"{_plural(c.unobserved, 'camera')} unaccounted for -- a recorder says they "
                     f"should be there and has not listed them.")
    for g in c.gaps[:4]:
        lines.append(f"- {getattr(g, 'recorder', '') or 'recorder'}: {getattr(g, 'reason', '')}")
    if c.exposure_known:
        lines.append("Cameras reachable from the internet on ports "
                     + ", ".join(str(p) for p in c.exposed_ports) + " -- that needs fixing."
                     if c.exposed else "None of them are reachable from the internet.")
    return ToolResult(ok=True, data=c.model_dump(), text="\n".join(lines))


def h_check_detail(ctx, key: str, network: str = "", target: str = ""):
    """What one failing security check saw and why it matters -- the
    Security page's drill-down, in words. The natural pair of `decide_check`:
    read this first, then decide.

    Goes through `payloads/security.check_detail`, the builder the route
    itself uses, and passes the customer's role in: the payload's `remedy`
    already says whether Vault can fix it or a person must, so the answer
    never promises a repair the tier system would refuse.
    """
    from .tools import ToolResult

    net = None
    if network:
        try:
            net = networks.resolve(ctx.db, ctx.tenant.id, network)
        except Exception:
            return ToolResult(ok=False, text=f'I have no network called "{network}" on this site.')
    d = security_payload.check_detail(ctx.db, ctx.tenant, ctx.role, key, net=net, target=target)
    if d is None:
        return ToolResult(ok=False, text=(f"There is no failing check called {key} right now. It may have "
                                          f"passed since, or the key is slightly different."))
    lines = [f"{d.title}" + (f" on {d.target}" if d.target else "") + ":"]
    if d.what_we_saw:
        lines.append(d.what_we_saw)
    if d.why_it_matters:
        lines.append(f"Why it matters: {d.why_it_matters}")
    if d.confidence == "uncertain":
        lines.append("The same evidence has more than one honest explanation, so treat this as a "
                     "question rather than a verdict.")
    r = d.remedy
    kind = getattr(r, "kind", "")
    if kind == "human":
        lines.append("This is not something Vault can fix by itself; it needs a person.")
    elif kind:
        lines.append(f"Vault can fix this ({getattr(r, 'action', '') or kind}) -- ask me to.")
    dec = d.decision
    if getattr(dec, "status", "") == "left":
        lines.append(f"You chose to leave this as it is" +
                     (f': "{dec.reason}"' if getattr(dec, "reason", "") else "") + ".")
    return ToolResult(ok=True, data=d.model_dump(), text="\n".join(lines))


# --------------------------------------------------------------------------
# Traffic -- "who is talking to whom" (Task 11, spec §4)
# --------------------------------------------------------------------------

def _mode_preamble(mode: str) -> str:
    """The sentence every non-mirror answer opens with. The worst answer in
    this feature is a confident one built on rows that cannot support it."""
    if mode == "router":
        return ("I can only see bytes per device on this network -- the router reports them; there is no "
                "mirror port -- so I can say who is busiest but not what they are talking to. ")
    if mode == "host":
        return ("I can only see the sensor machine's own connections -- it is not on a mirror port -- so this "
                "is one machine's traffic, not the rest of the network's. ")
    if mode == "none":
        return ("I cannot see traffic on this network: no mirror port is configured and the router does not "
                "report per-device counts. Ask me to help set up a mirror port if you want this. ")
    return ""


def _mb(n: int) -> str: return f"{n / 1e6:.1f} MB" if n >= 1e6 else f"{n / 1e3:.0f} KB"


def h_top_talkers(ctx, hours: int = 1, network: str = ""):
    """Which device is eating the bandwidth right now -- `payloads/traffic.overview`,
    read the same way the Traffic tab reads it. Opens with `_mode_preamble` on
    anything short of a mirror, because "busiest" reads very differently
    depending on whether the counts come from a mirrored switch port or a
    router that cannot see per-device bytes at all."""
    from .tools import ToolResult, _resolve_network
    from ..payloads import traffic
    net = None
    if network:
        net, problem = _resolve_network(ctx, network)
        if net is None: return ToolResult(ok=False, text=problem)
    try: hours = max(1, min(int(hours), 168))
    except (TypeError, ValueError): hours = 1
    o = traffic.overview(ctx.db, ctx.tenant, hours=hours, net=net)
    lines = [_mode_preamble(o.mode).strip()] if o.mode != "mirror" else []
    if o.mode == "none" or not o.top_talkers:
        lines.append(f"No traffic rows in the last {_plural(hours, 'hour')}.")
        return ToolResult(ok=True, data=o.model_dump(), text="\n".join(lines))
    lines.append(f"Busiest in the last {_plural(hours, 'hour')}:")
    for t in o.top_talkers[:6]:
        lines.append(f"- {t.name}: {_mb(t.bytes_in)} in, {_mb(t.bytes_out)} out" + (f", mostly {t.top_peer}" if t.top_peer and o.mode in ("mirror", "host") else ""))
    if o.mode in ("mirror", "host"):
        if o.top_peers: lines.append("Top destinations: " + ", ".join(f"{p.peer} ({_mb(p.bytes_in + p.bytes_out)})" for p in o.top_peers[:5]))
        if o.new_peers: lines.append(("New this window (the sensor had not contacted them before): "
                                      if o.mode == "host" else
                                      "New this window (nobody here had contacted them before): ") + ", ".join(p.peer for p in o.new_peers[:5]))
        lines.append(f"{round(o.external_share * 100)}% of bytes left the LAN.")
    if o.truncated: lines.append("The sensor saw more flows than it could send, so these are the biggest, not all of them.")
    return ToolResult(ok=True, data=o.model_dump(), text="\n".join(lines))


def h_device_traffic(ctx, target: str, hours: int = 24):
    """What one device is talking to -- `payloads/traffic.device`, the same
    builder the device drawer's Traffic panel uses. Asks rather than guesses
    on an ambiguous or unknown device (`_resolve_device`'s rule, not this
    handler's own), because a wrong match here would describe the wrong
    device's traffic as though it were the one asked about."""
    from .tools import ToolResult, _resolve_device
    from ..payloads import traffic
    dev, problem = _resolve_device(ctx, target)
    if dev is None: return ToolResult(ok=False, text=problem)
    try: hours = max(1, min(int(hours), 168))
    except (TypeError, ValueError): hours = 24
    d = traffic.device(ctx.db, ctx.tenant, dev.mac, hours=hours)
    shown = dev.label or dev.name or dev.ip or dev.mac
    pre = _mode_preamble(d.mode)
    if d.mode not in ("mirror", "host"):
        return ToolResult(ok=True, data=d.model_dump(), text=pre + f"{shown} moved {_mb(d.bytes_in)} in and {_mb(d.bytes_out)} out in the last {_plural(hours, 'hour')}.")
    lines = ([pre.strip()] if d.mode == "host" else []) + [
        f"{shown} in the last {_plural(hours, 'hour')}: {_mb(d.bytes_in)} in, {_mb(d.bytes_out)} out, {_plural(len(d.peers), 'peer')}."]
    for p in d.peers[:8]:
        lines.append(f"- {p.peer}:{p.peer_port}/{p.proto} -- {_mb(p.bytes_in + p.bytes_out)}")
    if len(d.peers) > 8: lines.append(f"...and {len(d.peers) - 8} more on the device's page.")
    return ToolResult(ok=True, data=d.model_dump(), text="\n".join(lines))


def h_server_peers(ctx, host: str, hours: int = 24):
    """Is this server talking to anything it shouldn't -- `payloads/traffic.server`
    resolves the configured host to its device row, then this defers to
    `h_device_traffic` so the two never describe the same rows two different
    ways. Names the host, not a MAC, when nothing matches: a server host with
    no device row is usually "the sensor hasn't seen it yet", and the
    customer typed the host name, not a MAC, so the answer should too."""
    from .tools import ToolResult
    from ..payloads import traffic
    d = traffic.server(ctx.db, ctx.tenant, host)
    if d is None:
        return ToolResult(ok=False, text=f"I have no device row for the server called {host!r}; is it on a network the sensor watches?")
    return h_device_traffic(ctx, d.mac, hours=hours)
