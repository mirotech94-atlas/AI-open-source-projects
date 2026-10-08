"""The Vault chat tool set: `ToolContext`/`ToolResult`/`Tool`, every
handler, and the `TOOLS` registry dict keyed by tool name (spec S7).

Every mutating handler reuses the exact function the matching dashboard
route calls (`routers/write.py`, `routers/settings.py`) so the resulting
`Edit`/`Queue` rows are byte-identical to a dashboard click -- the only
difference is the acting "user", which is set to `chat:<username>` before
the call so the `Edit.who` / `Audit.user` columns that function writes
already come out audited `by="chat:<user>"` without a second audit row.
Queue-only handlers that have no dashboard-route equivalent
(`device_deep_dive`, `host_command`, `rescan`, `speedtest`,
`schedule_reboot`) go through `routers/write.queue_and_audit` -- the helper
extracted from that module's own `_queue` closure -- so a chat-initiated
scan lands on the live bus exactly like a dashboard click, instead of
being a parallel implementation that silently skipped `bus.publish`.

No handler here ever builds or returns a shell command -- `host_command`
only ever passes a whitelisted `HOST_CMDS` key through the sensor queue,
never a caller-supplied string.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlparse

from fastapi import HTTPException
from sqlalchemy.orm import Session as DbSession

from .. import audit, models as m, netguard, networks
from ..config import settings as app_settings
from ..payloads.inbox import _entity_from_key
from ..reports import builder as report_builder
from ..reports import presets as report_presets
from ..scans import parse_ts
from ..routers import write as write_routes
from ..routers import security as security_routes
from ..routers import settings as settings_routes
# Aliased: `networks` is already bound above to `guardian_hub.networks`, the
# slug/attribution helper this module reads -- importing the router under that
# name would shadow it.
from ..routers import networks as network_routes
from .tiers import TIER2_FIXES, Tier
# Imported at the bottom of the import block: `surfaces` imports
# `ToolResult` back out of this module inside its handlers, so the
# cycle is resolved at call time rather than at import time.
from . import surfaces

# Port of the Nexus `HOST_CMDS` map (`server.py:159`): a read-only
# diagnostic whitelist. `apply_fix`/`set_policy`/anything mutating never
# goes through this dict -- it exists purely so `host_command` can accept a
# *name*, never an arbitrary command string.
HOST_CMDS = {
    "sockets", "logins", "docker_ps", "docker_logs", "disk", "memory",
    "uptime", "top_cpu", "ssh_log", "ufw", "fail2ban", "who",
}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


@dataclass
class ToolContext:
    db: DbSession
    tenant: "m.Tenant"
    user: "m.User"
    role: str
    request_ip: str = ""
    #: The alert key(s) this conversation was opened about -- what "this
    #: alert", "it" and "this incident" mean. `routers/chat.py` fills it
    #: from the thread's hand-off line; a tool never has to guess.
    subjects: tuple[str, ...] = ()
    #: The chatting session was opened with the public demo code. The chat
    #: route is viewer-level, so `deps.require_role`'s via_demo guard (which
    #: only bites above viewer) never sees the tools a turn runs;
    #: `registry.dispatch` reads this instead and lets only
    #: `registry.DEMO_READ_ONLY_TOOLS` through (audit 2026-09-28, pass 2).
    via_demo: bool = False


#: Every dash a model, a keyboard or a copy-paste can put where an ASCII
#: hyphen belongs. A chat model re-typing `email:dmarc-none:example.org` produced
#: `email:dmarc\u2011none:example.org` -- a NON-BREAKING hyphen -- and the exact
#: lookup that followed found nothing, so the bot told the owner the alert
#: did not exist while it sat open in front of them. The key is an
#: identifier; nothing about it is allowed to depend on which hyphen the
#: transport chose.
_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212\u00ad\ufe63\uff0d"), "-")
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)


def clean_text(value: str) -> str:
    """An identifier as the model meant it: ASCII hyphens, no zero-width
    characters, no non-breaking spaces, trimmed."""
    return value.translate(_DASHES).translate(_ZERO_WIDTH).replace("\u00a0", " ").strip()


_THIS_WORDS = {"", "this", "it", "that", "this alert", "this incident", "the alert", "the incident",
               "this one", "that one", "same", "the same"}


def resolve_alert_key(ctx: ToolContext, key: str) -> "m.Alert | None":
    """The alert a person meant, from what the model passed.

    Exact first, then with the key cleaned of Unicode dashes, then "this"
    resolved to the conversation's subject, then a case-insensitive match,
    then the ONE open alert whose key or message contains what was given
    (`dmarc-none:example.org`, `example.org`). Never a guess between two: an ambiguous
    fragment returns nothing, and the caller says so.
    """
    raw = (key or "")
    for cand in (raw, clean_text(raw)):
        if cand:
            a = networks.find_alert(ctx.db, ctx.tenant.id, cand)
            if a is not None:
                return a
    wanted = clean_text(raw).lower()
    if wanted in _THIS_WORDS:
        for subj in ctx.subjects:
            a = networks.find_alert(ctx.db, ctx.tenant.id, subj)
            if a is not None:
                return a
        return None
    rows = (ctx.db.query(m.Alert).filter(m.Alert.tenant_id == ctx.tenant.id)
            .order_by(m.Alert.last_seen.desc()).all())
    for a in rows:
        if (a.key or "").lower() == wanted:
            return a
    opens = [a for a in rows if a.status not in m.SETTLED_ALERT_STATUSES]
    hits = [a for a in opens if wanted in (a.key or "").lower() or wanted in (a.msg or "").lower()]
    if len(hits) == 1:
        return hits[0]
    return None


@dataclass
class ToolResult:
    """What a handler returns. `tier` and `status` exist so a caller can
    record a `schemas_dash.ToolStep` without re-deriving either: `status`
    is one of ok|error|queued|denied (`registry.dispatch` sets "denied" on
    its role/capability gates and stamps the effective `tier` on every
    return path), and defaults here to the obvious reading of ok/queue_id
    so a handler never has to set it by hand."""
    ok: bool
    text: str = ""
    data: dict = field(default_factory=dict)
    queue_id: str | None = None
    tier: int = 1
    status: str = ""      # ok|error|queued|denied

    def __post_init__(self) -> None:
        if not self.status:
            self.status = "error" if not self.ok else ("queued" if self.queue_id else "ok")


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    tier: Tier
    min_role: str
    handler: Callable[..., ToolResult]
    effect: str            # what happens, as a consequence sentence
    reversal: str          # how (or whether) it can be undone, in words
    reversible: bool = True
    #: The dashboard route this tool mirrors, e.g. "/dns/rules", or "" for a
    #: tool with no route behind it (a queue-only probe, or a read assembled
    #: from payload modules directly).
    #:
    #: Declared rather than inferred because `tests/test_chat_coverage.py`
    #: walks `routers/` for customer write routes and fails when one has no
    #: tool. That guard is the thing that keeps the assistant's reach equal to
    #: the dashboard's after everyone has forgotten it should be: a route
    #: added next month without a tool fails the suite by name, instead of
    #: quietly becoming something a customer can click and cannot ask for.
    #:
    #: The same field is what lets the suite assert `min_role` against the
    #: route's own `ctx=` guard. A tool one step *below* its route hands a
    #: viewer a write -- a one-character mistake, and the worst one available
    #: here. Being stricter than the route is allowed and sometimes right
    #: (`reset_baseline`); being looser never is.
    route: str = ""


class _ChatUser:
    """Stand-in for `models.User` that only ever needs `.username` -- the
    write-path functions below read nothing else off their `user` arg."""
    __slots__ = ("username",)

    def __init__(self, username: str) -> None:
        self.username = username


class _FakeClient:
    """`Request.client` is a namedtuple-ish with a `.host`; only `.host` is read."""
    __slots__ = ("host", "port")

    def __init__(self, host: str) -> None:
        self.host, self.port = host, 0


class _FakeRequest:
    """Stand-in for `fastapi.Request` -- the write-path functions only ever
    call `request.client.host` (via each router's local `_ip` helper). It
    carries the chatting user's real IP so every chat-initiated dashboard
    write is audited with an IP, like every other write."""

    def __init__(self, ip: str = "") -> None:
        self.client = _FakeClient(ip) if ip else None


def _chat_username(ctx: ToolContext) -> str:
    return f"chat:{ctx.user.username}"


def _chat_ctx(ctx: ToolContext) -> tuple[Any, "m.Tenant", str]:
    return _ChatUser(_chat_username(ctx)), ctx.tenant, ctx.role


def _schema(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


def _str_prop(desc: str) -> dict:
    return {"type": "string", "description": desc}


# --------------------------------------------------------------------------
# Tier 1 -- read-only / queue-and-report, no confirmation
# --------------------------------------------------------------------------

def _queue(ctx: ToolContext, path: str, action: str, *, body: dict | None = None,
           target: str = "") -> dict:
    """One queue row + audit + commit + live-bus publish, through the same
    helper `routers/write.py`'s own `_queue` closure uses."""
    return write_routes.queue_and_audit(ctx.db, ctx.tenant, _chat_username(ctx), ctx.role,
                                        path, action, body=body, target=target, ip=ctx.request_ip)


def h_device_deep_dive(ctx: ToolContext, target: str) -> ToolResult:
    out = _queue(ctx, "/api/deep-dive", "deep-dive", body={"target": target}, target=target)
    return ToolResult(ok=True, queue_id=out["id"],
                       text=f"Queued a live deep-dive on {target}. I will report ports, "
                            f"names and presence history once the sensor responds.")


def h_device_history(ctx: ToolContext, target: str) -> ToolResult:
    """Is this device one of ours? Answered from history Vault already
    holds -- no live probe, no queue, no waiting on the sensor. See
    payloads/familiarity.py for what counts as evidence."""
    from ..payloads import familiarity as fam_mod
    t = (target or "").strip().lower()
    dev = (ctx.db.query(m.Device)
           .filter(m.Device.tenant_id == ctx.tenant.id,
                   (m.Device.mac == t) | (m.Device.ip == t) | (m.Device.name == target) | (m.Device.label == target))
           .first())
    if dev is None:
        return ToolResult(ok=False, text=f"I have no device matching {target!r} on this network.")
    out = fam_mod.device_familiarity(ctx.db, ctx.tenant, dev.mac)
    lines = [out.summary] + [f"- {r}" for r in out.reasons]
    if out.top_dns:
        lines.append("- top DNS: " + ", ".join(out.top_dns[:4]))
    if out.name_siblings:
        lines.append("- same name under: " + ", ".join(f"{s.mac} ({'trusted' if s.trusted else 'untrusted'})" for s in out.name_siblings))
    return ToolResult(ok=True, data=out.model_dump(), text="\n".join(lines))


def h_vuln_scan(ctx: ToolContext, target: str, profile: str = "quick") -> ToolResult:
    """Queue the manual vulnerability scan (sensor vulnscan.py) on one
    target. Connect / read / HEAD only -- it never logs in or exploits."""
    t = (target or "").strip()
    if not t:
        return ToolResult(ok=False, text="Tell me what to scan: a device IP, a server name, a site, or 'lan'.")
    kind = "lan" if t.lower() in ("lan", "all", "everything", "network") else None
    if kind == "lan" and ctx.role != "admin":
        return ToolResult(ok=False, status="denied", text="A whole-network scan needs an admin; I can scan one device or server for you.")
    body = {"target": "lan" if kind else t, "profile": "full" if str(profile).lower() == "full" else "quick"}
    if kind:
        body["kind"] = kind
    out = _queue(ctx, "/api/vuln-scan", "vulnscan.start", body=body, target=body["target"])
    return ToolResult(ok=True, queue_id=out["id"],
                      text=f"Queued a {body['profile']} vulnerability scan of {body['target']}. I will report open ports, "
                           f"service versions and every finding by severity once the sensor finishes.")


def h_host_command(ctx: ToolContext, host: str, command: str, arg: str | None = None) -> ToolResult:
    if command not in HOST_CMDS:
        return ToolResult(ok=False, text=f'"{command}" is not one of my read-only host commands.')
    body = {"host": host, "command": command, "arg": arg}
    out = _queue(ctx, "/api/host-command", "host-command", body=body, target=host)
    return ToolResult(ok=True, queue_id=out["id"],
                       text=f"Queued `{command}` on {host}. I will report the output once "
                            f"the sensor responds.")


def _site_url_matches(configured: str, asked: str) -> bool:
    """A configured site matches what the caller asked for on an exact URL
    or a hostname match -- nothing looser, so "status.example.org.evil.test"
    never matches "status.example.org"."""
    if not configured or not asked:
        return False
    if configured.strip() == asked.strip():
        return True
    ch = (urlparse(configured).hostname or "").lower()
    ah = (urlparse(asked if "://" in asked else "//" + asked).hostname or asked).strip().lower()
    return bool(ch) and ch == ah


def _is_public_http(url: str) -> bool:
    """http/https only, and never a loopback, private, link-local or
    otherwise non-global address -- so `site_check` can never be turned
    into a probe of the hub's own network position (169.254.169.254, a
    LAN admin panel, the hub's own port). The rule now lives in
    `guardian_hub.netguard`, shared with the `/live` probe and the webhook
    integration, which used to have no such check at all."""
    return netguard.is_public_http(url)


def h_site_check(ctx: ToolContext, url: str) -> ToolResult:
    # Only the sites an admin already configured for this tenant, and only
    # the configured URL string -- never the caller's. The dashboard's own
    # /live route probes exactly this list; chat must be no more capable.
    sites = [s for s in ((ctx.tenant.config or {}).get("sites") or []) if isinstance(s, dict)]
    match = next((s for s in sites
                  if _site_url_matches(s.get("url") or "", url) or (s.get("name") or "") == url), None)
    if match is None:
        known = ", ".join((s.get("name") or s.get("url") or "") for s in sites[:6]) or "none yet"
        return ToolResult(ok=False, text=(
            f"I can only check the sites configured for this site, and {url} is not one of them "
            f"(I have: {known}). An admin can add it under Settings, then I can check it."))

    target = match.get("url") or ""
    if not _is_public_http(target):
        return ToolResult(ok=False, text=(
            f"{match.get('name') or target} is not a public http(s) address, so I do not probe it "
            f"from here. The dashboard's live check covers internal targets."))

    from ..routers.live import _probe   # local import: this is the one handler with a real network call
    name, result = _probe({"name": match.get("name") or target, "url": target, "expect": [200]})
    audit.log(ctx.db, ctx.tenant.id, _chat_username(ctx), ctx.role, "site-check",
              target=target, ip=ctx.request_ip, body=result)
    ctx.db.commit()
    ok = bool(result.get("ok"))
    text = f"{name}: {'up' if ok else 'down'} (HTTP {result.get('code')}, {result.get('ms')} ms)"
    return ToolResult(ok=True, text=text, data=result)


def h_lookup_ip(ctx: ToolContext, ip: str) -> ToolResult:
    row = ctx.db.get(m.IpGeo, ip)
    if not row:
        return ToolResult(ok=True, data={},
                           text=f"I don't have cached geo data for {ip} yet.")
    where = ", ".join(p for p in (row.city, row.country) if p)
    who = row.org or row.isp
    text = f"{ip}: {where or 'unknown location'}" + (f", {who}" if who else "")
    if row.hosting:
        text += " (hosting/VPN range)"
    return ToolResult(ok=True, text=text, data={
        "ip": ip, "country": row.country, "city": row.city, "org": row.org,
        "isp": row.isp, "hosting": row.hosting, "reverse": row.reverse,
    })


#: Which remediation actions can plausibly *cause* which kind of alert.
#: `explain_alert` may only answer "this was us" when a matching Fix/Audit
#: row of the right class landed inside the alert's own window -- otherwise
#: a months-old `device.update` on the same host would suppress a genuine
#: intrusion alert.
_ALERT_CAUSE_ACTIONS: dict[str, set[str]] = {
    "reboot": {"reboot", "full_upgrade", "security_updates", "reboot.plan"},
    "ssh": {"disable_ssh_password", "limit_ssh", "ban_long", "harden_fail2ban", "enable_fail2ban"},
    "login": {"disable_ssh_password", "limit_ssh", "ban_long", "harden_fail2ban", "enable_fail2ban"},
    "disk": {"cleanup_disk"},
    "service": {"restart_service", "restart_container", "reboot"},
    "container": {"restart_container", "restart_service"},
    "unreachable": {"reboot", "full_upgrade", "restart_service", "restart_container"},
    "down": {"reboot", "full_upgrade", "restart_service", "restart_container"},
    "port": {"enable_ufw", "block_ip", "block_subnet"},
    "exposed": {"enable_ufw", "block_ip", "block_subnet"},
    "firewall": {"enable_ufw", "harden_fail2ban", "enable_fail2ban"},
    "update": {"security_updates", "full_upgrade", "enable_unattended"},
    "cve": {"security_updates", "full_upgrade", "install_debsecan"},
    "miner": {"kill_miner", "block_ip"},
}

#: How stale a cause may be and still be offered as the explanation.
_CAUSE_MAX_AGE = dt.timedelta(hours=24)


def _plausible_causes(alert: "m.Alert") -> set[str]:
    text = f"{alert.key} {alert.msg}".lower()
    out: set[str] = set()
    for word, actions in _ALERT_CAUSE_ACTIONS.items():
        if word in text:
            out |= actions
    return out


def _in_alert_window(when: dt.datetime | None, alert: "m.Alert") -> bool:
    """True when `when` sits inside this occurrence's window: at or after
    the alert first appeared (the previous clear, as far as the row
    records), no later than its most recent occurrence, and no older than
    24 h."""
    if when is None:
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    first, last = parse_ts(alert.first_seen), parse_ts(alert.last_seen)
    now = _now()
    if now - when > _CAUSE_MAX_AGE:
        return False
    if first and when < first:
        return False
    if last and when > last + dt.timedelta(minutes=5):
        return False
    return True


def h_explain_alert(ctx: ToolContext, key: str) -> ToolResult:
    # Tolerates either LAN key shape (Task 51 namespaces them per network),
    # a re-typed dash, and "this" for the alert the thread is about.
    a = resolve_alert_key(ctx, key)
    if not a:
        return ToolResult(ok=False, text=f"I don't have an alert with key {key}.")
    key = a.key
    entity = _entity_from_key(key)
    plausible = _plausible_causes(a)

    fix = None
    if plausible:
        for row in (ctx.db.query(m.Fix).filter_by(tenant_id=ctx.tenant.id, host=entity, ok=True)
                    .order_by(m.Fix.ts.desc()).limit(20).all()):
            if row.action in plausible and _in_alert_window(parse_ts(row.ts), a):
                fix = row
                break
    if fix:
        return ToolResult(ok=True, data={"caused_by": "fix", "action": fix.action},
                           text=f"{a.msg}, this was us: a Vault fix ({fix.action} on {fix.host}) "
                                f"ran while this alert was firing, so it is not a new threat.")

    aud = None
    if plausible:
        for row in (ctx.db.query(m.Audit).filter_by(tenant_id=ctx.tenant.id, target=entity)
                    .order_by(m.Audit.id.desc()).limit(20).all()):
            if row.action.split(".")[-1] in plausible or row.action in plausible:
                if _in_alert_window(row.ts, a):
                    aud = row
                    break
    if aud:
        return ToolResult(ok=True, data={"caused_by": "audit", "action": aud.action},
                           text=f"{a.msg}, this was us: {aud.action} by {aud.user} while this alert "
                                f"was firing, so it is not a new threat.")

    return ToolResult(ok=True, data={"caused_by": ""},
                       text=f"{a.msg} (first seen {a.first_seen}, seen {a.count}x, no Vault action "
                            f"of mine lines up with it, treat it as real).")


def h_vuln_check(ctx: ToolContext, host: str) -> ToolResult:
    h = ctx.db.query(m.Host).filter_by(tenant_id=ctx.tenant.id, name=host).first()
    if not h:
        return ToolResult(ok=False, text=f"I don't track a host named {host}.")
    last = h.last or {}
    sec = last.get("sec") or {}
    security_updates = len(sec.get("security_updates") or [])
    cve_fixable = int(last.get("cve_fixable") or 0)
    debsecan = (ctx.db.query(m.Fix)
                .filter_by(tenant_id=ctx.tenant.id, host=host, action="install_debsecan", ok=True)
                .first() is not None)
    text = (f"{host}: {security_updates} pending security update(s), {cve_fixable} CVE(s) "
            f"auto-fixable, debsecan {'installed' if debsecan else 'not installed'}.")
    return ToolResult(ok=True, text=text,
                       data={"security_updates": security_updates, "cve_fixable": cve_fixable, "debsecan": debsecan})


def h_build_report(ctx: ToolContext, days: int = 7, preset: str = "") -> ToolResult:
    report, _path = report_builder.render_to_file(ctx.db, ctx.tenant, days=days or 7,
                                                    preset=preset or None, kind="manual")
    token = report_builder.mint_share_token(report, 168)
    audit.log(ctx.db, ctx.tenant.id, _chat_username(ctx), ctx.role, "report.build",
              target=report.id, ip=ctx.request_ip, body={"days": days, "preset": preset})
    ctx.db.commit()
    url = f"{app_settings.base_url}/r/{token}"
    return ToolResult(ok=True, text=f"Built the report. Share it at {url}",
                       data={"report_id": report.id, "share_url": url})


def h_rescan(ctx: ToolContext) -> ToolResult:
    out = _queue(ctx, "/api/run", "run")
    return ToolResult(ok=True, queue_id=out["id"], text="Queued a fresh scan. I will report back once it's done.")


def h_speedtest(ctx: ToolContext) -> ToolResult:
    out = _queue(ctx, "/api/speedtest", "speedtest")
    return ToolResult(ok=True, queue_id=out["id"], text="Queued a speed test. I will report back once it's done.")


# --------------------------------------------------------------------------
# Tier 2 -- reversible changes, execute immediately, state the undo
# --------------------------------------------------------------------------

def _device_update(ctx: ToolContext, mac: str, **fields) -> dict:
    body = write_routes.DeviceUpdate(**fields)
    return write_routes.update_device(mac, body, _FakeRequest(ctx.request_ip), _chat_ctx(ctx), ctx.db)


def h_trust_device(ctx: ToolContext, mac: str, label: str | None = None) -> ToolResult:
    fields: dict = {"trusted": True}
    if label is not None:
        fields["label"] = label
    try:
        out = _device_update(ctx, mac, **fields)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    label_suffix = f' as "{label}"' if label else ""
    return ToolResult(ok=True, data=out,
                       text=f'Trusted {mac}{label_suffix}. Say "distrust it" to undo.')


#: The most devices one `trust_devices` call will touch. "Trust everything on
#: the Wi-Fi" on a large estate is a sentence someone can type in two seconds
#: and regret for a long time; past this the answer is a narrower question,
#: not a longer list.
MAX_BULK_DEVICES = 25


def h_trust_devices(ctx: ToolContext, macs: list | None = None) -> ToolResult:
    """Trust several devices in one confirmed step.

    Exists because `trust_device` takes exactly one `mac`, so "trust all
    devices" had no honest shape to take. What the model did instead was pick
    one of six MACs and propose that -- twice, picking a different device each
    time. Silently narrowing "all" to one arbitrary device is a worse answer
    than refusing, because the user cannot see which one was chosen.

    Tier 3, unlike single-device trust at tier 2: the confirmation names every
    device, and one "yes" covering six devices deserves to be read first.
    """
    macs = [str(x).strip() for x in (macs or []) if str(x).strip()]
    # De-duplicate, preserving the order the model listed them in.
    seen: set[str] = set()
    macs = [x for x in macs if not (x.lower() in seen or seen.add(x.lower()))]
    if not macs:
        return ToolResult(ok=False, status="error",
                          text="Tell me which devices to trust and I will prepare it.")
    if len(macs) > MAX_BULK_DEVICES:
        return ToolResult(
            ok=False, status="error",
            text=(f"That is {len(macs)} devices in one go, and I cap this at "
                  f"{MAX_BULK_DEVICES}. Narrow it down -- by network, or to the ones "
                  f"you actually recognise -- and I will prepare that."))

    done, failed = [], []
    for mac in macs:
        try:
            _device_update(ctx, mac, trusted=True)
            done.append(mac)
        except HTTPException as e:
            failed.append(f"{mac} ({e.detail})")
    if not done:
        return ToolResult(ok=False, text="Trusted nothing: " + "; ".join(failed))
    text = f"Trusted {len(done)} device(s): " + ", ".join(done) + '. Say "distrust it" with a device to undo.'
    if failed:
        # Never report a clean sweep when part of it did not land.
        text += f" {len(failed)} could not be trusted: " + "; ".join(failed) + "."
    return ToolResult(ok=bool(done), data={"trusted": done, "failed": failed}, text=text)


def h_label_device(ctx: ToolContext, mac: str, label: str) -> ToolResult:
    try:
        out = _device_update(ctx, mac, label=label)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    return ToolResult(ok=True, data=out, text=f'Labeled {mac} as "{label}". Say the old label to undo.')


def h_quarantine_device(ctx: ToolContext, mac: str, on: bool) -> ToolResult:
    try:
        out = _device_update(ctx, mac, quarantined=bool(on))
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    verb, opposite = ("quarantined", "release it") if on else ("released", "quarantine it again")
    return ToolResult(ok=True, data=out, text=f'I {verb} {mac}. Say "{opposite}" to undo.')


def _alert_op(ctx: ToolContext, key: str, op: str, hours: int | None = None) -> dict:
    a = resolve_alert_key(ctx, key)
    if a is not None:
        key = a.key
    body = write_routes.AlertOp(hours=hours) if hours is not None else write_routes.AlertOp()
    return write_routes.alert_op(key, op, _FakeRequest(ctx.request_ip), body, _chat_ctx(ctx), ctx.db)


def h_ack_alert(ctx: ToolContext, key: str) -> ToolResult:
    try:
        out = _alert_op(ctx, key, "ack")
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    return ToolResult(ok=True, data=out, text=f'Acked alert {key}. Say "reopen it" to undo.')


def h_snooze_alert(ctx: ToolContext, key: str, hours: int = 8) -> ToolResult:
    try:
        out = _alert_op(ctx, key, "snooze", hours=hours)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    days = hours / 24
    span = f"{hours} hours" if hours < 48 else f"{days:g} days"
    return ToolResult(ok=True, data=out,
                       text=(f"Snoozed \"{out.get('msg') or key}\" for {span}. It stays out of the inbox until then "
                             f"and comes back only if it is still true. Say \"reopen it\" to undo early."))


def h_resolve_alert(ctx: ToolContext, key: str) -> ToolResult:
    try:
        out = _alert_op(ctx, key, "resolve")
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    return ToolResult(ok=True, data=out, text=f'Resolved alert {key}. Say "reopen it" to undo.')


#: How far back "cleared itself recently" reaches when the inbox is already
#: empty and the person asks to delete an alert -- long enough to cover a
#: blip that resolved a scan or two ago, short enough that it is genuinely
#: "just now" and not last week's history.
_RECENTLY_CLEARED_H = 6

_CLEARED_PHRASE = {
    "auto": "cleared on its own when the problem went away",
    "auto-cleared": "cleared on its own when the problem went away",
    "fix": "was cleared by a fix Vault ran",
    "human": "you cleared it earlier",
}


def _cleared_phrase(by: str) -> str:
    return _CLEARED_PHRASE.get((by or "").strip().lower(), "cleared")


def _inbox_open_alerts(ctx: ToolContext) -> list["m.Alert"]:
    """The alerts the person actually sees in the open inbox -- the same
    `m.SETTLED_ALERT_STATUSES` set the inbox 'alerts' view uses."""
    return (ctx.db.query(m.Alert)
            .filter(m.Alert.tenant_id == ctx.tenant.id,
                    m.Alert.status.notin_(m.SETTLED_ALERT_STATUSES))
            .order_by(m.Alert.last_seen.desc()).all())


def _recently_cleared_alerts(ctx: ToolContext, hours: int = _RECENTLY_CLEARED_H) -> list["m.Alert"]:
    """Alerts that resolved within the last `hours`, newest first. `last_seen`
    is the last scan an auto-cleared alert fired on -- i.e. the moment just
    before it recovered -- so it is a faithful 'when did this clear' clock for
    the 'it already went away' answer. Parsed (not string-compared) because
    `last_seen` can carry mixed offsets."""
    cutoff = _now() - dt.timedelta(hours=hours)
    dated: list[tuple[dt.datetime, "m.Alert"]] = []
    for a in (ctx.db.query(m.Alert)
              .filter(m.Alert.tenant_id == ctx.tenant.id, m.Alert.status == "resolved")
              .all()):
        ts = parse_ts(a.last_seen)
        if ts is not None and ts >= cutoff:
            dated.append((ts, a))
    dated.sort(key=lambda p: p[0], reverse=True)
    return [a for _, a in dated]


def h_dismiss_alert(ctx: ToolContext, key: str = "") -> ToolResult:
    """Delete / clear / dismiss an alert from the inbox.

    With a key, it resolves that open alert (reversibly), or -- if that alert
    already cleared -- says so rather than pretending to delete nothing. With
    no key it acts on the inbox as a whole: it lists what is open to clear, or,
    when the inbox is already empty, names any alert that cleared itself in the
    last few hours so the person learns it was handled instead of hearing a
    bare 'nothing to delete'. It never resolves an unnamed alert -- clearing a
    specific one always goes through its key."""
    key = clean_text(key or "")
    if key:
        a = resolve_alert_key(ctx, key)
        if a is None:
            return ToolResult(ok=False, text=f'No alert "{key}" exists.')
        key = a.key
        if a.status == "resolved":
            return ToolResult(
                ok=True, data={"key": key, "status": a.status, "resolved_by": a.resolved_by},
                text=f'"{a.msg}" already {_cleared_phrase(a.resolved_by)}, so it is not in the '
                     "inbox any more. Nothing to delete.")
        return h_resolve_alert(ctx, key)

    opens = _inbox_open_alerts(ctx)
    if opens:
        listed = "; ".join(f'"{a.msg}" ({a.key})' for a in opens[:6])
        more = f", and {len(opens) - 6} more" if len(opens) > 6 else ""
        return ToolResult(
            ok=True, data={"open": [a.key for a in opens]},
            text=f"You have {len(opens)} open alert(s): {listed}{more}. Tell me which one to "
                 'clear (name it), or say "resolve them all".')

    cleared = _recently_cleared_alerts(ctx)
    if cleared:
        a = cleared[0]
        when = a.last_seen[11:16] if len(a.last_seen or "") >= 16 else (a.last_seen or "")
        extra = f" {len(cleared) - 1} other(s) cleared recently too." if len(cleared) > 1 else ""
        when_txt = f" at {when}" if when else ""
        return ToolResult(
            ok=True, data={"recently_cleared": [c.key for c in cleared]},
            text=f'Your inbox is already clear. The most recent alert, "{a.msg}", '
                 f"{_cleared_phrase(a.resolved_by)}{when_txt}, so it left the inbox on its own."
                 f"{extra} Nothing to delete.")

    return ToolResult(
        ok=True,
        text="Your inbox is clear and nothing has cleared in the last few hours, so there is "
             "nothing to delete.")


def h_decide_recommendation(ctx: ToolContext, key: str, decision: str) -> ToolResult:
    if decision not in ("yes", "no"):
        return ToolResult(ok=False, text='decision must be "yes" or "no".')
    body = write_routes.RecDecision(decision=decision)
    try:
        out = write_routes.rec_decide(key, body, _FakeRequest(ctx.request_ip), _chat_ctx(ctx), ctx.db)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    queue_id = out.get("queue_id")
    text = f'Recorded "{decision}" on recommendation {key}'
    text += " and queued the fix" if queue_id else ""
    text += '. Say "clear" to undo.'
    return ToolResult(ok=True, data=out, queue_id=queue_id, text=text)


def h_check_email(ctx: ToolContext, address: str) -> ToolResult:
    """"Is billing@acme-invoices.com safe?" asked of the assistant.

    Same checker as the Security panel, so the two can never give one
    person a different answer from the other. Read-only and viewer-tier on
    purpose: it reads DNS and changes nothing, and the person who most
    needs it in the moment is often the one with the least access.
    """
    out = security_routes.email_check(address, _chat_ctx(ctx), ctx.db)
    lines = [out.headline]
    lines += [f"- {f.text}" for f in out.findings]
    if out.verdict == "unproven":
        # Never let the assistant round "nothing wrong found" up to "safe".
        lines.append("That is not the same as safe: Vault checked the domain, "
                     "not the person sending the letter.")
    return ToolResult(ok=True, data=out.model_dump(), text="\n".join(lines))


def h_leave_firewall_rule(ctx: ToolContext, rule_id: str, reason: str = "") -> ToolResult:
    """Item 21: "leave the telnet one alone" said to the assistant.

    Only the DECLINE side is a tool. Applying a rule is not, and not
    because of an oversight: Vault has no write path to a gateway, and
    the day it has one, applying a rule must stamp its automatic rollback
    deadline in the same breath. A tool that could apply one without that
    is the version of this feature that locks somebody out at 2am.
    """
    body = security_routes.FirewallDecision(reason=reason or "")
    try:
        out = security_routes.decline_rule(
            rule_id, body, _FakeRequest(ctx.request_ip), _chat_ctx(ctx), ctx.db)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    return ToolResult(ok=True, data=out.model_dump(),
                      text=f"Left it as it is: {out.title}. Vault will not propose it again.")


# --------------------------------------------------------------------------
# Tier 2/3 -- apply_fix (tier depends on the action, see tiers.effective_tier)
# --------------------------------------------------------------------------

def h_apply_fix(ctx: ToolContext, host: str, action: str, arg: str | None = None) -> ToolResult:
    # `queue_fix`, not the `/fix` route: for a tier-3 action that route demands
    # its own dashboard-minted `confirm_token`, and chat's confirmation has
    # already been burnt by `registry.dispatch` before this handler is reached.
    # Going through the route would ask a second time for the same "yes".
    try:
        out = write_routes.queue_fix(ctx.db, ctx.tenant, _chat_username(ctx), ctx.role,
                                     host, action, arg, ctx.request_ip)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    text = f"Queued {action} on {host}."
    if action in TIER2_FIXES:
        text += ' Say "undo it" and I will reverse it where that fix supports undoing.'
    return ToolResult(ok=True, queue_id=out.get("id"), text=text)


# --------------------------------------------------------------------------
# Tier 3 -- disruptive / hard to undo, admin only
# --------------------------------------------------------------------------

def h_forget_device(ctx: ToolContext, mac: str) -> ToolResult:
    try:
        out = write_routes.forget_device(mac, _FakeRequest(ctx.request_ip), _chat_ctx(ctx), ctx.db)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    return ToolResult(ok=True, data=out, text=f"Forgot device {mac} and its history. This cannot be undone.")


def h_set_policy(ctx: ToolContext, section: str, values: dict) -> ToolResult:
    fake_ctx = _chat_ctx(ctx)
    request = _FakeRequest(ctx.request_ip)
    try:
        if section == "targets":
            body = settings_routes.TargetsIn(**values)
            out = settings_routes.update_targets(body, request, fake_ctx, ctx.db)
        elif section == "autofix":
            body = settings_routes.AutoFixIn(**values)
            out = settings_routes.update_autofix(body, request, fake_ctx, ctx.db)
        elif section == "thresholds":
            out = settings_routes.update_thresholds(dict(values), request, fake_ctx, ctx.db)
        else:
            return ToolResult(ok=False, text=f'"{section}" is not a policy section I know (targets|autofix|thresholds).')
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    data = out if isinstance(out, dict) else out.model_dump()
    return ToolResult(ok=True, data=data,
                       text=f"Updated {section} policy. This can be changed back the same way.")


def h_schedule_reboot(ctx: ToolContext, host: str, window_start: str, window_end: str) -> ToolResult:
    body = {"host": host, "window_start": window_start, "window_end": window_end}
    out = _queue(ctx, "/api/reboot-plan", "reboot.plan", body=body, target=host)
    return ToolResult(ok=True, queue_id=out["id"],
                       text=f"Scheduled a reboot of {host} between {window_start} and {window_end}.")


# --------------------------------------------------------------------------
# Tier 3 -- networks (the same CRUD the dashboard's Networks screen drives)
# --------------------------------------------------------------------------
#
# Every one of these goes through `routers/networks.py` rather than touching
# `m.Network` directly, because that module does three things in ONE
# transaction that a hand-rolled write here would silently skip: it validates
# (kind, subnet shape, gateway inside the subnet, slug collisions), it audits,
# and -- decisively -- it emits a single whole-config
# `Edit(path="/api/config")` carrying the rebuilt `networks[]` block. The
# sensor replays that edit with `cfg.clear(); cfg.update(...)`, so a network
# written without it exists on the hub and does not exist on the sensor.

def _network_names(rows: list) -> str:
    return ", ".join(f'"{n.name}"' for n in rows) or "none yet"


def _resolve_network(ctx: ToolContext, ref: str) -> tuple["m.Network | None", str]:
    """`(row, "")` for the one network `ref` names, or `(None, <what to say>)`.

    People address networks the way they see them on screen -- "the Lab
    network", "guest wifi" -- never by the UUID the routes take, so the ref is
    matched case-insensitively against id, slug and name, and then (only if
    nothing matched exactly) as a substring of the name, which is what makes
    "lab" find "Lab annex".

    A miss and a tie are both *questions*, never guesses: a wrong network here
    renames the strip the owner reads at a glance, or -- for `remove_network`
    -- deletes the wrong row and moves its devices somewhere they were never
    meant to go. Neither is visible to the person who asked, which is exactly
    what makes guessing worse than asking. The miss lists the names that DO
    exist, because the usual cause is a half-remembered name rather than a
    network that was never there.
    """
    rows = ctx.db.query(m.Network).filter_by(tenant_id=ctx.tenant.id).all()
    rows.sort(key=lambda n: (not n.is_primary, (n.name or "").lower()))
    needle = (ref or "").strip().lower()
    if not needle:
        return None, f"Which network do you mean? This site has: {_network_names(rows)}."

    exact = [n for n in rows
             if needle in ((n.id or "").lower(), (n.slug or "").lower(), (n.name or "").strip().lower())]
    hits = exact or [n for n in rows if needle in (n.name or "").lower()]
    if len(hits) == 1:
        return hits[0], ""
    if len(hits) > 1:
        return None, (f'"{ref}" matches more than one network here ({_network_names(hits)}). '
                      "Which one do you mean?")
    return None, (f'I have no network called "{ref}" on this site. '
                  f"The networks here are: {_network_names(rows)}.")


def _resolve_device(ctx: ToolContext, ref: str) -> tuple["m.Device | None", str]:
    """`(row, "")` for the one device `ref` names, or `(None, <what to say>)`.

    The device counterpart of `_resolve_network`, and it asks rather than
    guesses for the same reason. People say "the reception printer", not a
    MAC; the routes take a MAC. A wrong match here writes the change to
    somebody else's device, and nothing on any screen afterwards says which
    device the assistant actually meant -- so a tie is a question.

    Label before name is deliberate. `label` is what a person typed, `name` is
    what inference produced, so when both exist and disagree the human one is
    the better match for what a human just said.
    """
    rows = ctx.db.query(m.Device).filter_by(tenant_id=ctx.tenant.id).all()
    needle = (ref or "").strip().lower()
    if not needle:
        return None, "Which device do you mean? A name, an IP or a MAC will do."

    exact = [d for d in rows
             if needle in ((d.mac or "").lower(), (d.ip or "").lower(),
                           (d.label or "").strip().lower(), (d.name or "").strip().lower())]
    hits = exact or [d for d in rows
                     if needle in (d.label or "").lower() or needle in (d.name or "").lower()]
    if len(hits) == 1:
        return hits[0], ""
    if len(hits) > 1:
        shown = ", ".join(f"{d.label or d.name or d.ip} ({d.mac})" for d in hits[:6])
        return None, f'"{ref}" matches more than one device here ({shown}). Which one do you mean?'
    return None, f'I have no device matching "{ref}" on this network.'


def h_add_network(ctx: ToolContext, name: str, subnet_prefix: str, kind: str = "wifi",
                  expected_gateway: str | None = None, ssid: str | None = None,
                  colour: str | None = None) -> ToolResult:
    # Only the fields that were actually supplied: `NetworkIn` types the string
    # fields as `str`, so handing pydantic a `None` for an optional one the
    # model left out is a ValidationError rather than the "leave it empty" the
    # caller meant.
    body = {"name": name, "subnet_prefix": subnet_prefix, "kind": kind}
    for key, value in (("expected_gateway", expected_gateway), ("ssid", ssid), ("colour", colour)):
        if value is not None:
            body[key] = value
    try:
        out = network_routes.create_network(network_routes.NetworkIn(**body),
                                            _FakeRequest(ctx.request_ip), _chat_ctx(ctx), ctx.db)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    text = f'Added the network "{out["name"]}"'
    text += f' on {out["subnet_prefix"]}.0/24.' if out["subnet_prefix"] else "."
    if out.get("status") == "no-sensor":
        # Said out loud rather than left to be discovered: a network with no
        # sensor on it reads as a broken network on the overview strip (grey,
        # no devices), and the owner should hear that it is waiting for a
        # sensor rather than wonder what went wrong.
        text += " No sensor is assigned to it yet, so nothing will report on it until one is."
    return ToolResult(ok=True, data=out, text=text)


def h_rename_network(ctx: ToolContext, target: str, name: str, colour: str | None = None) -> ToolResult:
    row, problem = _resolve_network(ctx, target)
    if row is None:
        return ToolResult(ok=False, text=problem)
    was, slug = row.name, row.slug
    # Built from the keys actually given, because `update_network` reads the
    # patch with `exclude_unset=True` and treats a PRESENT `colour` as an
    # instruction: `NetworkPatch(name=..., colour=None)` would set the stored
    # colour to "" and quietly strip the network's colour off every chip on
    # the dashboard, for a caller who only asked for a rename.
    patch = {"name": name}
    if colour is not None:
        patch["colour"] = colour
    try:
        out = network_routes.update_network(row.id, network_routes.NetworkPatch(**patch),
                                            _FakeRequest(ctx.request_ip), _chat_ctx(ctx), ctx.db)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    return ToolResult(ok=True, data=out,
                      text=(f'Renamed "{was}" to "{out["name"]}". Its links and the alerts already open '
                            f"on it keep working: the short name they are built from ({slug}) is fixed "
                            "when a network is created, and a rename does not change it."))


def h_remove_network(ctx: ToolContext, target: str, reassign: str) -> ToolResult:
    row, problem = _resolve_network(ctx, target)
    if row is None:
        return ToolResult(ok=False, text=problem)
    dest, problem = _resolve_network(ctx, reassign)
    if dest is None:
        return ToolResult(ok=False, text=f"I could not tell where to move that network's devices. {problem}")
    # The destination is resolved here and handed over as an id: the route's
    # own `networks.resolve` takes an id or a slug only, and raises a bare
    # "no such network" -- so a display name reaching it would lose the
    # name-list and the which-one-do-you-mean question above.
    try:
        out = network_routes.delete_network(row.id, _FakeRequest(ctx.request_ip), reassign=dest.id,
                                            ctx=_chat_ctx(ctx), db=ctx.db)
    except HTTPException as e:
        return ToolResult(ok=False, text=str(e.detail))
    moved = ", ".join(f"{v} {k}" for k, v in (out.get("moved") or {}).items() if v)
    text = f'Deleted the network "{row.name}" and moved what was on it to "{dest.name}"'
    text += f" ({moved})." if moved else " (it held nothing)."
    dropped = sum((out.get("dropped") or {}).values())
    if dropped:
        # Reported, not swallowed: these rows were deleted rather than moved,
        # because the destination already had the same finding open.
        text += f' {dropped} duplicate alert(s) already open on "{dest.name}" were dropped instead of moved.'
    text += " This cannot be undone."
    return ToolResult(ok=True, data=out, text=text)


# --------------------------------------------------------------------------
# The registry
# --------------------------------------------------------------------------

TOOLS: dict[str, Tool] = {
    "vuln_scan": Tool(
        name="vuln_scan",
        description=("Run a manual vulnerability scan (Nessus-style) on one device IP, a configured server, a "
                     "site, or 'lan' for every known device. Port sweep, service/version banners, TLS checks, "
                     "web headers, and on servers: exposed listeners, SSH password login, fixable CVEs. "
                     "Connect-and-look only; never logs in. Use for 'scan 192.168.1.5', 'check prod for "
                     "vulnerabilities', 'scan the whole network'."),
        schema=_schema({"target": _str_prop("device IP, server name, site name/URL, or 'lan'"),
                        "profile": _str_prop("quick (default) or full")}, ["target"]),
        tier=2, min_role="operator", handler=h_vuln_scan, route="/security/vuln-scans",
        effect="probes the target's open ports and services; changes nothing on it",
        reversal="read-only probe",
    ),
    "device_history": Tool(
        name="device_history",
        description=("Is this device one of ours? Reads the history Vault already holds for a "
                     "device -- days seen, overnight presence, joins/leaves, DNS platform hints, the "
                     "same name under other MACs, other networks -- and says which pattern it fits "
                     "(resident / regular / visitor) with the facts. Instant; no probe. Use for 'is "
                     "this device mine', 'have I seen this before', 'who is 192.168.1.x'."),
        schema=_schema({"target": _str_prop("MAC, IP, name or label of the device")}, ["target"]),
        tier=1, min_role="viewer", handler=h_device_history,
        effect="reads the device's own history; changes nothing",
        reversal="read-only",
    ),
    "device_deep_dive": Tool(
        name="device_deep_dive",
        description="Run a live port scan, name lookup and presence check on a device or IP right now.",
        schema=_schema({"target": _str_prop("IP address or MAC address")}, ["target"]),
        tier=1, min_role="viewer", handler=h_device_deep_dive,
        effect="queues a live port scan and presence check on the target; makes no persistent change",
        reversal="nothing to undo -- it only looks",
    ),
    "host_command": Tool(
        name="host_command",
        description="Run one whitelisted read-only diagnostic command on a host.",
        schema=_schema({
            "host": _str_prop("host name from the data, e.g. prod, demo, guardian-hub"),
            "command": {"type": "string", "enum": sorted(HOST_CMDS)},
            "arg": _str_prop("extra argument some commands take (e.g. a container name)"),
        }, ["host", "command"]),
        tier=1, min_role="viewer", handler=h_host_command,
        effect="runs one whitelisted read-only command on the host and reports its output; makes no change",
        reversal="nothing to undo -- it only reads",
    ),
    "site_check": Tool(
        name="site_check",
        description=("Check the live HTTP status of one of THIS site's already-configured sites. "
                     "Only a site already in the configured site list can be checked. Pass its "
                     "name or its exact url, never an arbitrary address."),
        schema=_schema({"url": _str_prop("the name or exact url of an already-configured site")}, ["url"]),
        tier=1, min_role="viewer", handler=h_site_check,
        effect="performs a live HTTP check of an already-configured site; makes no change",
        reversal="nothing to undo -- it only checks",
    ),
    "lookup_ip": Tool(
        name="lookup_ip",
        description="Look up who is behind a public IP address.",
        schema=_schema({"ip": _str_prop("the public IP address")}, ["ip"]),
        tier=1, min_role="viewer", handler=h_lookup_ip,
        effect="reads cached geolocation data for the ip; makes no change",
        reversal="nothing to undo -- it only reads",
    ),
    "explain_alert": Tool(
        name="explain_alert",
        description="Explain why an alert fired, including whether Vault itself caused it.",
        schema=_schema({"key": _str_prop("the alert key")}, ["key"]),
        tier=1, min_role="viewer", handler=h_explain_alert,
        effect="reads the alert plus its fix and audit history; makes no change",
        reversal="nothing to undo -- it only reads",
    ),
    "vuln_check": Tool(
        name="vuln_check",
        description="Check a host's pending CVEs and security updates.",
        schema=_schema({"host": _str_prop("host name")}, ["host"]),
        tier=1, min_role="viewer", handler=h_vuln_check,
        effect="reads CVE and update state for the host; makes no change",
        reversal="nothing to undo -- it only reads",
    ),
    "build_report": Tool(
        name="build_report",
        description="Render a report for a period or preset and return a share link.",
        schema=_schema({
            "days": {"type": "integer", "description": "how many days back to cover"},
            # Sourced from the preset tables rather than typed here, so a preset
            # added to `reports/presets.py` is offered to the model without a
            # second edit, and a name the model invents is refused by
            # `validate_args` instead of rendering a default report under the
            # wrong title.
            "preset": {"type": "string", "enum": sorted(report_presets.PRESETS) + sorted(report_presets.SPECIAL_PRESETS),
                       "description": "a named report preset, or omit for the default sections"},
        }, []),
        # Operator, not viewer: this stores a Report row and mints a public
        # share link that needs no login for a week. It mirrors
        # `POST /api/v1/reports/build`, which is operator-gated.
        tier=1, min_role="operator", handler=h_build_report, route="/reports/build",
        effect="renders and stores a new report and mints a share link anyone with the link can open",
        reversal="reversible -- the link can be revoked and the report deleted",
    ),
    "rescan": Tool(
        name="rescan",
        description="Start a fresh full network scan.",
        schema=_schema({}),
        tier=1, min_role="operator", handler=h_rescan,
        effect="queues a fresh scan for the sensor to run",
        reversal="nothing to undo -- it only measures",
    ),
    "speedtest": Tool(
        name="speedtest",
        description="Run an internet speed test.",
        schema=_schema({}),
        tier=1, min_role="operator", handler=h_speedtest,
        effect="queues a speed test for the sensor to run",
        reversal="nothing to undo -- it only measures",
    ),
    "ack_alert": Tool(
        name="ack_alert",
        description="Acknowledge an alert.",
        schema=_schema({"key": _str_prop("the alert key")}, ["key"]),
        tier=2, min_role="operator", handler=h_ack_alert, route="/alerts/{key:path}/{op}",
        effect="marks the alert acknowledged so it stops being delivered",
        reversal="reversible -- the alert can be reopened",
    ),
    "snooze_alert": Tool(
        name="snooze_alert",
        description=("Snooze an alert for a number of hours -- the tool for 'don't show this again', "
                     "'stop nagging me about this', 'hide this for now'. Use a long window for those "
                     "(720 hours is 30 days) and say so: the alert stays out of the inbox for that long "
                     "and comes back only if it is still true. Vault has no suppression rules, filters "
                     "or ignore lists; this is the way to quiet one alert."),
        schema=_schema({"key": _str_prop("the alert key, copied exactly, or 'this' for the alert the conversation is about"),
                         "hours": {"type": "integer",
                                   "description": "hours to stay quiet for; defaults to 8; 720 is a month"}}, ["key"]),
        tier=2, min_role="operator", handler=h_snooze_alert, route="/alerts/{key:path}/{op}",
        effect="hides the alert from delivery until the snooze window ends",
        reversal="reversible -- the alert can be reopened early",
    ),
    "resolve_alert": Tool(
        name="resolve_alert",
        description="Resolve an alert.",
        schema=_schema({"key": _str_prop("the alert key")}, ["key"]),
        tier=2, min_role="operator", handler=h_resolve_alert, route="/alerts/{key:path}/{op}",
        effect="marks the alert resolved and closes it",
        reversal="reversible -- the alert can be reopened",
    ),
    "dismiss_alert": Tool(
        name="dismiss_alert",
        description=("Delete, clear, dismiss or remove an alert from the inbox. Give the alert key to "
                     "clear that one (it is resolved, reversibly); if that alert already cleared itself, "
                     "this says so instead of pretending to delete nothing. With no key it acts on the "
                     "whole inbox: it reports what is open to clear, or -- when the inbox is already "
                     "empty -- names any alert that cleared itself in the last few hours so the person "
                     "knows it was handled. Use this for 'delete the inbox alert', 'clear my alerts', "
                     "'dismiss that', 'remove the alert'."),
        schema=_schema({"key": _str_prop("the alert key to clear; omit to act on the inbox as a whole")}, []),
        tier=2, min_role="operator", handler=h_dismiss_alert, route="/alerts/{key:path}/{op}",
        effect="clears the named alert from the inbox (resolves it), or reports what already cleared",
        reversal="reversible -- a resolved alert can be reopened",
    ),
    "trust_device": Tool(
        name="trust_device",
        description="Mark a device as trusted, optionally giving it a label.",
        schema=_schema({"mac": _str_prop("device MAC address"), "label": _str_prop("optional label")}, ["mac"]),
        tier=2, min_role="operator", handler=h_trust_device, route="/devices/{mac}",
        effect="marks the device trusted, so it stops raising new-device alerts",
        reversal="reversible -- the device can be distrusted again",
    ),
    "trust_devices": Tool(
        name="trust_devices",
        description=("Mark SEVERAL devices trusted in one confirmed step. Use this -- never "
                     "repeated trust_device calls, and never one arbitrary device -- when the "
                     "user asks for more than one ('trust all devices', 'trust everything on "
                     "the guest network'). Pass every MAC you mean."),
        schema=_schema({"macs": {"type": "array", "items": {"type": "string"},
                                 "description": "every device MAC to trust"}}, ["macs"]),
        tier=3, min_role="operator", handler=h_trust_devices, route="/devices/{mac}",
        effect="marks all of the listed devices trusted, so they stop raising new-device alerts",
        reversal="reversible -- any of them can be distrusted again",
    ),
    "label_device": Tool(
        name="label_device",
        description="Rename a device's label.",
        schema=_schema({"mac": _str_prop("device MAC address"), "label": _str_prop("new label")}, ["mac", "label"]),
        tier=2, min_role="operator", handler=h_label_device, route="/devices/{mac}",
        effect="renames the device's label everywhere it appears",
        reversal="reversible -- the old label can be set back",
    ),
    "quarantine_device": Tool(
        name="quarantine_device",
        description="Quarantine or release a device on the network.",
        schema=_schema({"mac": _str_prop("device MAC address"),
                         "on": {"type": "boolean", "description": "true to quarantine, false to release"}},
                        ["mac", "on"]),
        tier=2, min_role="operator", handler=h_quarantine_device, route="/devices/{mac}",
        effect="isolates (or releases) the device on the network, so it loses (or regains) access",
        reversal="reversible -- the device can be released again",
    ),
    "decide_recommendation": Tool(
        name="decide_recommendation",
        description="Record yes or no on a recommendation.",
        schema=_schema({"key": _str_prop("the recommendation key"),
                         "decision": {"type": "string", "enum": ["yes", "no"]}},
                        ["key", "decision"]),
        tier=2, min_role="operator", handler=h_decide_recommendation, route="/recs/{key}",
        effect="records a yes/no decision and, on yes with an attached action, queues the matching fix",
        reversal="reversible -- the decision can be cleared, though a queued fix still runs",
    ),
    "check_email": Tool(
        name="check_email",
        description=("Say whether an email address can be trusted: does its domain exist, "
                     "can it receive mail, can anyone forge it (SPF/DMARC), is it a throwaway "
                     "provider, and is it a lookalike of one of this customer's own domains. "
                     "Reads DNS only; the address never leaves the hub. Use for 'is this email "
                     "legit', 'someone emailed me asking to change bank details', 'is X safe'."),
        schema=_schema({"address": _str_prop("the email address to check")}, ["address"]),
        tier=1, min_role="viewer", handler=h_check_email,
        effect="reads public DNS for the address's domain; changes nothing",
        reversal="read-only",
    ),
    "leave_firewall_rule": Tool(
        name="leave_firewall_rule",
        description="Decline a firewall rule Vault proposed for the router, with a reason.",
        schema=_schema({"rule_id": _str_prop("the proposal's id"),
                         "reason": _str_prop("why it is being left alone")},
                        ["rule_id"]),
        tier=2, min_role="operator", handler=h_leave_firewall_rule,
        route="/security/firewall/{rule_id}/decline",
        effect="records that this proposed router rule is not wanted, and stops Vault proposing it again",
        reversal="reversible -- ask again and Vault will re-propose it",
    ),
    "apply_fix": Tool(
        name="apply_fix",
        description="Apply a named remediation action on a host.",
        schema=_schema({
            "host": _str_prop("host name"),
            "action": {"type": "string", "description": "a FIX_ACTIONS entry, e.g. block_ip, reboot"},
            "arg": _str_prop("extra argument some fixes take (e.g. an IP to block)"),
        }, ["host", "action"]),
        tier=2, min_role="admin", handler=h_apply_fix, route="/fix", reversible=False,
        effect="runs the named remediation on the host, which changes that host's running state",
        reversal="not reversible automatically -- only some fixes can be undone, and only by hand",
    ),
    "forget_device": Tool(
        name="forget_device",
        description="Permanently delete a device and its history.",
        schema=_schema({"mac": _str_prop("device MAC address")}, ["mac"]),
        tier=3, min_role="admin", handler=h_forget_device, route="/devices/{mac}/forget", reversible=False,
        effect="deletes the device row and every presence record Vault has for it",
        reversal="not reversible -- the history is gone and the device reappears as brand new",
    ),
    "set_policy": Tool(
        name="set_policy",
        description="Change targets, auto-fix policy or alert thresholds.",
        schema=_schema({
            "section": {"type": "string", "enum": ["targets", "autofix", "thresholds"]},
            "values": {"type": "object", "description": "the new values for that section"},
        }, ["section", "values"]),
        tier=3, min_role="admin", handler=h_set_policy,
        effect="replaces this site's targets/auto-fix/thresholds config and replays it to the sensor",
        reversal="reversible -- the previous values can be set back the same way",
    ),
    "schedule_reboot": Tool(
        name="schedule_reboot",
        description="Schedule a host reboot in a maintenance window.",
        schema=_schema({
            "host": _str_prop("host name"),
            "window_start": _str_prop("ISO timestamp the window opens"),
            "window_end": _str_prop("ISO timestamp the window closes"),
        }, ["host", "window_start", "window_end"]),
        tier=3, min_role="admin", handler=h_schedule_reboot, route="/reboot-plan", reversible=False,
        effect="the host restarts inside that window and is unreachable for about a minute",
        reversal="reversible only until it runs -- once it reboots, only waiting brings it back",
    ),
    "add_network": Tool(
        name="add_network",
        description=("Add a network for Vault to watch on this site -- a second Wi-Fi, a lab or guest "
                     "VLAN, a wired segment. Needs a name and the first three octets of its subnet. Use "
                     "for 'add the guest wifi', 'watch 10.0.5.x as the Lab network'."),
        schema=_schema({
            "name": _str_prop("what to call the network, e.g. Guest Wi-Fi"),
            "subnet_prefix": _str_prop("first three octets of its subnet, e.g. 192.168.5"),
            "kind": {"type": "string", "enum": [k for k in networks.KINDS if k != "home"],
                     "description": "what sort of network it is (default wifi)"},
            "expected_gateway": _str_prop("the router address on that subnet, e.g. 192.168.5.1"),
            "ssid": _str_prop("Wi-Fi network name, if it has one"),
            "colour": _str_prop("hex colour for its chip on the dashboard, e.g. #2f6fed"),
        }, ["name", "subnet_prefix"]),
        tier=3, min_role="admin", handler=h_add_network, route="/networks",
        effect=("adds the network to this site and replays the new network list to the sensor, so it "
                "starts being watched and counted on its own"),
        reversal="reversible -- it can be deleted again, which moves whatever it has collected by then",
    ),
    "rename_network": Tool(
        name="rename_network",
        description=("Rename a network, and optionally recolour its chip. Say which network by its name "
                     "or slug. Use for 'call the lab network Clinic VLAN', 'rename guest wifi'."),
        schema=_schema({
            "target": _str_prop("the network to rename, by name or slug"),
            "name": _str_prop("the new name"),
            "colour": _str_prop("new hex colour for its chip, if it should change too"),
        }, ["target", "name"]),
        tier=3, min_role="admin", handler=h_rename_network, route="/networks/{net_id}",
        effect=("renames the network everywhere it is shown and replays the new name to the sensor; its "
                "links and the alerts already open on it keep working, because the short name they are "
                "built from does not change"),
        reversal="reversible -- the old name can be set back the same way",
    ),
    "remove_network": Tool(
        name="remove_network",
        description=("Delete a network and move everything on it -- devices, alerts, history -- onto "
                     "another network. Always say which network the devices move to; there is no "
                     "default. The site's primary network cannot be deleted. Use for 'delete the lab "
                     "network and put its devices on the main one'."),
        schema=_schema({
            "target": _str_prop("the network to delete, by name or slug"),
            "reassign": _str_prop("the network its devices, alerts and history move to, by name or slug"),
        }, ["target", "reassign"]),
        tier=3, min_role="admin", handler=h_remove_network, route="/networks/{net_id}", reversible=False,
        effect=("deletes the network and moves its devices, alerts and history onto the network you "
                "name; any alert already open there for the same thing is dropped as a duplicate"),
        reversal=("not reversible -- the network is gone, and its devices and history stay on the "
                  "network they were moved to"),
    ),

    # ----------------------------------------------------------------------
    # Full-surface coverage (spec 2026-09-20-chat-full-surface-design).
    #
    # Handlers live in `surfaces.py`; see that module's docstring for why.
    # Every entry below carries a `route=` so `tests/test_chat_coverage.py`
    # can check two things the eye will not: that no customer write route is
    # missing a tool, and that no tool is one step below its route's guard.
    # ----------------------------------------------------------------------
    "dns_filtering": Tool(
        name="dns_filtering",
        description=("Block or allow a domain for every device on the network -- the DNS filtering "
                     "rules on the DNS page. Use for 'block facebook.com', 'stop the kids reaching "
                     "gambling sites', 'let the practice management site through', 'unblock "
                     "supplier.com'. For a temporary pass that expires by itself, use dns_exception "
                     "instead."),
        schema=_schema({
            "domain": _str_prop("the domain, e.g. example.com"),
            "kind": {"type": "string", "enum": ["block", "allow"],
                     "description": "block refuses it everywhere; allow lets it through even if a category would refuse it"},
            "op": {"type": "string", "enum": ["add", "remove"], "description": "add (default) or remove the rule"},
        }, ["domain"]),
        tier=2, min_role="admin", handler=surfaces.h_dns_filtering, route="/dns/rules",
        effect="adds or removes one filtering rule that applies to every device on the network",
        reversal="reversible -- ask me to remove the rule and it is gone",
    ),
    "dns_exception": Tool(
        name="dns_exception",
        description=("Open one blocked domain for a set number of hours, then close it again by "
                     "itself. Use for 'let me get to that site for the afternoon', 'unblock this "
                     "for an hour'. Unlike an allow rule, it expires on its own."),
        schema=_schema({
            "domain": _str_prop("the domain to open"),
            "hours": {"type": "integer", "description": "how long to open it for, 1-168 (default 24)"},
            "remove": {"type": "boolean", "description": "true to close an exception early"},
        }, ["domain"]),
        tier=2, min_role="admin", handler=surfaces.h_dns_exception, route="/dns/exceptions",
        effect="lets one domain through filtering for a bounded window, after which filtering resumes",
        reversal="reversible -- it closes itself, or ask me to close it early",
    ),
    "cancel_work": Tool(
        name="cancel_work",
        description=("Stop a job Vault has queued or is running -- a scan, a repair, a reboot. "
                     "Use for 'cancel that scan', 'stop the reboot'. Use work_status first if you "
                     "do not know the job id."),
        schema=_schema({"job_id": _str_prop("the job id, as work_status reports it")}, ["job_id"]),
        tier=2, min_role="operator", handler=surfaces.h_cancel_work, route="/jobs/{qid}/cancel",
        effect="stops that job; nothing from it runs",
        reversal="reversible -- ask for the same thing again and I will queue it fresh",
    ),
    "retry_work": Tool(
        name="retry_work",
        description=("Run a failed job again with the same arguments. Only for a job that failed, "
                     "and never for a disruptive repair -- those spend their confirmation once. Use "
                     "for 'try that again', 'retry the update'."),
        schema=_schema({"job_id": _str_prop("the job id, as work_status reports it")}, ["job_id"]),
        tier=2, min_role="operator", handler=surfaces.h_retry_work, route="/jobs/{qid}/retry",
        effect="queues the same job again with its original arguments",
        reversal="reversible -- it can be cancelled while it waits",
    ),
    "set_importance": Tool(
        name="set_importance",
        description=("Say how much a device matters: normal, important or critical. Changes what "
                     "Vault puts in front of you first; it does not change what Vault checks "
                     "or trusts. Use for 'the till is critical', 'the x-ray machine matters most'."),
        schema=_schema({
            "target": _str_prop("device name, label, IP or MAC"),
            "importance": {"type": "string", "enum": ["normal", "important", "critical"]},
            "network": _str_prop("network name, when the same device name exists on more than one"),
        }, ["target", "importance"]),
        tier=2, min_role="operator", handler=surfaces.h_set_importance,
        route="/devices/{mac}/importance",
        effect="changes where that device ranks in what Vault shows you first",
        reversal="reversible -- set it back to normal the same way",
    ),
    "send_sensor_invite": Tool(
        name="send_sensor_invite",
        description=("E-mail the sensor install instructions for one network, with a single-use "
                     "code. Use for 'send me the install email again', 'how do I set up the sensor "
                     "for the branch office'."),
        schema=_schema({"network": _str_prop("network name or slug")}, ["network"]),
        tier=2, min_role="admin", handler=surfaces.h_send_sensor_invite,
        route="/onboarding/networks/{network_id}/invite",
        effect="mints a single-use install code valid for a week and e-mails it to you",
        reversal="reversible -- the code expires by itself, and a new one replaces it",
    ),
    "reset_baseline": Tool(
        name="reset_baseline",
        description=("Treat everything on the network right now as known, so only devices that "
                     "appear from now on are flagged as new. Use for 'stop telling me about these "
                     "devices, they are all ours'. This cannot be undone."),
        schema=_schema({}),
        # Tier 3 although its route is a plain admin POST -- see
        # `surfaces.h_reset_baseline`. Silent, network-wide and not undoable.
        tier=3, min_role="admin", handler=surfaces.h_reset_baseline, reversible=False,
        route="/reset-lan-baseline",
        effect=("marks every device currently on the network as known-from-today, so none of them "
                "will ever be flagged as new again"),
        reversal="not reversible -- the old baseline is gone and cannot be restored",
    ),

    # Read -- "advise about anything". All tier 1 / viewer, because the
    # dashboard shows every one of these to a viewer: an assistant that
    # refused would be less capable than the page beside it.
    "threat_detail": Tool(
        name="threat_detail",
        description=("What Vault found for one class of attack and how each finding was settled "
                     "-- the drawer behind a card in Threats and protection. Use for 'what happened "
                     "with that break-in attempt', 'has there been any phishing', 'how did you deal "
                     "with the malware'."),
        schema=_schema({
            "kind": {"type": "string",
                     "enum": ["phishing", "malware", "mitm", "breakin", "dos", "ransomware"]},
            "days": {"type": "integer", "description": "how far back to look, 1-90 (default 7)"},
        }, ["kind"]),
        tier=1, min_role="viewer", handler=surfaces.h_threat_detail,
        effect="reads the findings Vault already holds for that attack class; changes nothing",
        reversal="read-only",
    ),
    "work_status": Tool(
        name="work_status",
        description=("What Vault is doing right now: what is queued, running, failed or done. "
                     "Use for 'what are you doing', 'is that scan finished', 'did the fix work', "
                     "and to get a job id for cancel_work or retry_work."),
        schema=_schema({}),
        tier=1, min_role="viewer", handler=surfaces.h_work_status,
        effect="reads the job queue; changes nothing",
        reversal="read-only",
    ),
    "setup_status": Tool(
        name="setup_status",
        description=("How far through setup each network is and the one thing to do next. Use for "
                     "'am I set up', 'what is left to do', 'why am I not seeing anything yet'."),
        schema=_schema({}),
        tier=1, min_role="viewer", handler=surfaces.h_setup_status,
        effect="reads how far setup has got; changes nothing",
        reversal="read-only",
    ),
    "dns_activity": Tool(
        name="dns_activity",
        description=("What DNS filtering is doing: whether it is on, how much it refused, which "
                     "domains, and anything that looks like malware calling home. Use for 'is "
                     "filtering working', 'what have you blocked', 'what is this device talking to'."),
        schema=_schema({"days": {"type": "integer", "description": "how far back to look (default 7)"}}),
        tier=1, min_role="viewer", handler=surfaces.h_dns_activity,
        effect="reads the DNS figures from the latest scan; changes nothing",
        reversal="read-only",
    ),
    "trend": Tool(
        name="trend",
        description=("Whether things are getting better or worse over time -- the History tab, in "
                     "words. Use for 'is it improving', 'how did last month go', 'are there more "
                     "alerts than before'."),
        schema=_schema({"days": {"type": "integer", "description": "how many days to cover (default 30)"}}),
        tier=1, min_role="viewer", handler=surfaces.h_trend,
        effect="reads the trend rows the History tab draws; changes nothing",
        reversal="read-only",
    ),
    "network_map": Tool(
        name="network_map",
        description=("What is plugged into what: the uplink, the gateway, the infrastructure and "
                     "what hangs off it -- the Topology tab, in words. Use for 'what is on my "
                     "network', 'draw me the layout', 'what is connected to the router'."),
        schema=_schema({}),
        tier=1, min_role="viewer", handler=surfaces.h_network_map,
        effect="reads the layout the Topology tab draws; changes nothing",
        reversal="read-only",
    ),
    "update_status": Tool(
        name="update_status",
        description=("What needs patching and what is waiting on a reboot, with the known CVEs "
                     "behind it. Use for 'is everything up to date', 'what needs updating', 'am I "
                     "vulnerable to anything known'."),
        schema=_schema({}),
        tier=1, min_role="viewer", handler=surfaces.h_update_status,
        effect="reads pending updates and CVEs Vault already holds; changes nothing",
        reversal="read-only",
    ),
    "bulk_alerts": Tool(
        name="bulk_alerts",
        description=("Acknowledge, snooze, resolve or reopen SEVERAL alerts in one step. Use "
                     "whenever the ask covers more than one alert -- 'ack everything low', 'snooze "
                     "these until Monday', 'clear the lot'. Never narrow a request covering many "
                     "alerts down to one; use this instead."),
        schema=_schema({
            "keys": {"type": "array", "items": {"type": "string"},
                     "description": "the alert keys to act on"},
            "op": {"type": "string", "enum": ["ack", "snooze", "resolve", "reopen"]},
            "hours": {"type": "integer", "description": "for snooze: how long, default 8"},
        }, ["keys", "op"]),
        tier=2, min_role="operator", handler=surfaces.h_bulk_alerts, route="/alerts/bulk",
        effect="moves every alert you name to that state at once",
        reversal="reversible -- reopen puts them back",
    ),
    "decide_check": Tool(
        name="decide_check",
        description=("Record that the owner is deliberately leaving a failing security check as it "
                     "is, with their reason -- or undo that and let it be raised again. This is an "
                     "accepted risk, not a repair: the check still fails. Always needs a reason in "
                     "the owner's own words; never invent one."),
        schema=_schema({
            "key": _str_prop("the check's key, as the Security page shows it"),
            "op": {"type": "string", "enum": ["leave", "undo"]},
            "reason": _str_prop("why it is being left, in the owner's words -- required for 'leave'"),
            "network": _str_prop("network name, when the check is per-network"),
            "target": _str_prop("what the check is about, when it has one"),
            "expires_at": _str_prop("ISO date the exception should lapse, if one is required"),
        }, ["key", "op"]),
        tier=2, min_role="operator", handler=surfaces.h_decide_check,
        route="/security/checks/{op}",
        effect="stops that check being raised and records who accepted it and why",
        reversal="reversible -- 'undo' puts the check back",
    ),
    "block_attacker": Tool(
        name="block_attacker",
        description=("Set what Vault does to a host that attacks this network: auto, block_ip, "
                     "block_subnet, ban_long, limit_ssh, or off to stop it acting on its own. A "
                     "standing policy, not a one-off. Use for 'block anyone brute-forcing us', "
                     "'stop firewalling things automatically'."),
        schema=_schema({"method": {"type": "string",
                                   "enum": ["auto", "block_ip", "block_subnet", "ban_long",
                                            "limit_ssh", "off"]}}, ["method"]),
        tier=2, min_role="admin", handler=surfaces.h_block_attacker, route="/attacker",
        effect="changes what Vault does, unattended, the next time a host attacks this network",
        reversal="reversible -- set it to a different method, or off",
    ),
    "raise_ticket": Tool(
        name="raise_ticket",
        description=("Open a support ticket with the people who run Vault. Use whenever the "
                     "problem is one Vault cannot fix by itself -- a sensor that has stopped "
                     "reporting, hardware that needs someone on site, a billing question, or "
                     "anything the customer wants a human to look at. Always offer this rather than "
                     "ending on 'I cannot do anything about that'."),
        schema=_schema({
            "subject": _str_prop("one line saying what it is about"),
            "body": _str_prop("what is happening, in the customer's own words plus what you know"),
            "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
        }, ["subject", "body"]),
        tier=2, min_role="viewer", handler=surfaces.h_raise_ticket, route="/support/tickets",
        effect="opens a support ticket and notifies the people who run Vault",
        reversal="reversible -- say so on the ticket and it gets closed",
    ),
    "reply_ticket": Tool(
        name="reply_ticket",
        description="Add a message to a support ticket that is already open.",
        schema=_schema({
            "ticket_id": _str_prop("the ticket id"),
            "body": _str_prop("the message to add"),
        }, ["ticket_id", "body"]),
        tier=2, min_role="viewer", handler=surfaces.h_reply_ticket,
        route="/support/tickets/{ticket_id}/reply",
        effect="adds your message to that ticket and notifies support",
        reversal="not reversible -- a message once sent cannot be unsent",
        reversible=False,
    ),
    "camera_status": Tool(
        name="camera_status",
        description=("How many cameras Vault can see, how many a recorder says should be there, "
                     "which recorder is quiet, and whether any camera is reachable from the internet. "
                     "Use for 'are all the cameras up', 'is camera 3 down', 'can anyone see my cameras "
                     "from outside'."),
        schema=_schema({}),
        tier=1, min_role="viewer", handler=surfaces.h_camera_status,
        effect="reads the camera census from the latest scan; changes nothing",
        reversal="read-only",
    ),
    "check_detail": Tool(
        name="check_detail",
        description=("What one failing security check actually saw and why it matters, and whether "
                     "Vault can fix it -- the drill-down behind a row on the Security page. Read "
                     "this before decide_check. Use for 'what is this evil twin thing', 'why is the "
                     "firewall check red'."),
        schema=_schema({
            "key": _str_prop("the check's key, as the Security page shows it"),
            "network": _str_prop("network name, when the check is per-network"),
            "target": _str_prop("what the check is about, when it has one"),
        }, ["key"]),
        tier=1, min_role="viewer", handler=surfaces.h_check_detail,
        effect="reads the evidence behind one check; changes nothing",
        reversal="read-only",
    ),
    "top_talkers": Tool(
        name="top_talkers",
        description=("Which device is eating the bandwidth. Busiest devices on a network over a recent "
                     "window -- bytes in/out, and on a mirrored network, their top destinations and any "
                     "peer nobody here had talked to before. Says plainly when the network has no mirror "
                     "port and no router feed, rather than a confident answer the rows cannot support. "
                     "Use for 'who is using all the bandwidth', 'what is eating my internet'."),
        schema=_schema({
            "hours": {"type": "integer", "description": "how many hours back to look, default 1, max 168"},
            "network": _str_prop("network name, to scope to one network"),
        }, []),
        tier=1, min_role="viewer", handler=surfaces.h_top_talkers,
        effect="reads traffic rows already stored; changes nothing",
        reversal="read-only",
    ),
    "device_traffic": Tool(
        name="device_traffic",
        description=("What one device is talking to -- e.g. what is the x-ray PC talking to. Bytes "
                     "in/out for a device over a recent window, and on a mirrored network its actual "
                     "peers by host, port and protocol. Asks which device is meant rather than guessing "
                     "when the name is ambiguous or unknown. Use for 'what is 192.168.1.5 talking to', "
                     "'is the x-ray machine phoning home'."),
        schema=_schema({
            "target": _str_prop("MAC, IP, name or label of the device"),
            "hours": {"type": "integer", "description": "how many hours back to look, default 24, max 168"},
        }, ["target"]),
        tier=1, min_role="viewer", handler=surfaces.h_device_traffic,
        effect="reads traffic rows already stored; changes nothing",
        reversal="read-only",
    ),
    "server_peers": Tool(
        name="server_peers",
        description=("Is prod talking to anything it shouldn't. Traffic for one configured server, "
                     "resolved by its ssh_hosts name to the device the sensor sees on the wire -- same "
                     "answer shape as device_traffic. Use for 'is prod talking to anything odd', "
                     "'what has the database server been sending out'."),
        schema=_schema({
            "host": _str_prop("host name, as configured in ssh_hosts, e.g. prod, demo"),
            "hours": {"type": "integer", "description": "how many hours back to look, default 24, max 168"},
        }, ["host"]),
        tier=1, min_role="viewer", handler=surfaces.h_server_peers,
        effect="reads traffic rows already stored; changes nothing",
        reversal="read-only",
    ),
}
