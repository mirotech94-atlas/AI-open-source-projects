"""`dispatch()` -- the single entry point Task 44's `POST /chat` calls for
every tool invocation: unknown-tool guard, role gate, argument validation,
capability gate, tier escalation + confirmation, execution, and (for
anything that needs asking first) minting/consuming a 10-minute
confirmation token bound to one exact proposal.

Gate order matters and is fixed: unknown tool -> role -> argument
validation (including an unknown `apply_fix` action) -> capability ->
confirmation/tier -> handler. Validation sits before tier computation so
Vault never asks the user to confirm something that could not run
anyway.

Confirmation is keyed on *the token being present*, not on the computed
tier: whenever a caller passes `confirm_token`, it is consumed through
`acttokens.consume_chat` -- the same rowcount-guarded atomic primitive the
public `/act` route uses -- and a token that does not match the exact
tool + canonicalised args + user + tenant it was minted for is refused.
That is what makes a "yes" single-use under concurrency and unusable
against a different target, a different user's proposal, or a second time.

Re-exports `ToolContext`, `ToolResult`, `Tool`, `TOOLS` (from `.tools`) and
`ChatProposal`/`ProposedAction` (from `..schemas_dash`) so every caller and
test imports everything it needs from this one module.
"""

from __future__ import annotations

import datetime as dt

from .. import acttokens, models as m
from .tools import clean_text
from ..deps import ROLE_ORDER
from ..schemas_dash import ChatProposal, ProposedAction
from .tiers import KNOWN_FIXES, TIER2_FIXES, effective_tier, touches_own_port
from .tools import TOOLS, Tool, ToolContext, ToolResult

__all__ = [
    "ToolContext", "ToolResult", "Tool", "TOOLS",
    "ChatProposal", "ProposedAction",
    "dispatch", "capability_gap", "DEMO_READ_ONLY_TOOLS", "DEMO_REFUSAL",
]

#: The only tools a session opened with the public demo code may run.
#:
#: An allowlist, not a denylist, and not "tier 1": tier says how much a write
#: needs confirming, not whether something is a write. `rescan`, `speedtest`,
#: `device_deep_dive` and `host_command` are tier 1 and queue sensor work;
#: `build_report` is tier 1 and writes a Report row with a week-long public
#: share link; `raise_ticket` is viewer-level and e-mails the support
#: mailbox. A demo visitor typing "reboot the NAS" into the public demo used
#: to reach every one of them, because `POST /chat` is viewer-level and the
#: via_demo guard in `deps.require_role` only bites above viewer.
#:
#: A tool added next month is therefore refused to the demo until someone
#: decides it only reads -- the safe direction to be wrong in.
#: `tests/test_security_fixes_2_2026_09_28.py` holds that nothing on this list
#: is above tier 1 or mirrors a write route.
DEMO_READ_ONLY_TOOLS = frozenset({
    "device_history", "site_check", "lookup_ip", "explain_alert", "vuln_check",
    "check_email", "threat_detail", "work_status", "setup_status", "dns_activity",
    "trend", "network_map", "update_status", "camera_status", "check_detail",
    "top_talkers", "device_traffic", "server_peers",
})

DEMO_REFUSAL = ("I cannot do that here: the demo is read-only. Everything you can see is real "
                "Vault behaviour, but nothing in the demo can be changed, queued or sent.")

_CONFIRM_MINUTES = 10

#: The verb a proposal leads with when the args carry no action/command of
#: their own, so the summary always reads "<verb> <target> -- ...".
_VERBS = {
    "forget_device": "forget", "set_policy": "change the", "schedule_reboot": "schedule a reboot of",
    "trust_device": "trust", "label_device": "relabel", "quarantine_device": "quarantine",
    # Without this the fallback turns the tool name into the verb and the
    # sentence reads "trust devices 30 devices (...)".
    "trust_devices": "trust",
    "ack_alert": "acknowledge", "snooze_alert": "snooze", "resolve_alert": "resolve",
    "decide_recommendation": "decide", "build_report": "build a report for",
    "device_deep_dive": "deep-dive", "rescan": "rescan", "speedtest": "speed-test",
    # The full-surface tools (spec 2026-09-20-chat-full-surface-design). A
    # `{}` marks where the target goes; without one the head falls back to
    # the house "<verb> on <target>", which reads wrongly for most of these
    # ("e-mail the install instructions on Office Wi-Fi"). A verb with no
    # `{}` and no target is used bare, so `reset_baseline` reads as a
    # sentence rather than as its own tool name.
    "dns_exception": "open a temporary exception for {}",
    "cancel_work": "cancel job {}", "retry_work": "retry job {}",
    "set_importance": "change the importance of {}",
    "send_sensor_invite": "e-mail the sensor install instructions for {}",
    "reset_baseline": "re-anchor the device baseline, so everything on the network now counts as known",
    "block_attacker": "set the response to attacking hosts to {}",
    "raise_ticket": "open a support ticket: {}", "reply_ticket": "reply on ticket {}",
}

#: The word a customer reads for an alert/check operation. `bulk_alerts` and
#: `decide_check` carry their verb in `op`, the way `apply_fix` carries it in
#: `action`; left raw, the head would read "ack on ..." or "leave on ...".
_OP_VERBS = {"ack": "acknowledge", "snooze": "snooze", "resolve": "resolve", "reopen": "reopen",
             "leave": "leave as it is", "undo": "reopen"}

#: Sections of `set_policy` whose payload is a whole-list replacement: a
#: call that omits one of these keys empties that list, and the proposal
#: has to say so out loud.
_POLICY_LIST_FIELDS = {"targets": ("sites", "hosts")}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _describe(tool: Tool) -> str:
    d = tool.description.strip().rstrip(".")
    return d[0].lower() + d[1:] if d else "do that"


def capability_gap(ctx: ToolContext, name: str, args: dict) -> str | None:
    """A precise sentence naming what capability is missing, or None when
    the tool can run. Two capability axes, neither one about role (a role
    grant does not create an SSH key or enrol a sensor, so this copy never
    offers one as the remedy -- the role gate has its own message):

    - a host-targeting tool (`apply_fix`, `host_command`, `schedule_reboot`)
      whose `host` is not in `Tenant.config["ssh_hosts"]` -- the sensor has
      no SSH access configured for it;
    - a LAN-probing tool (`device_deep_dive`) with no active `Sensor`
      enrolled on this tenant at all.
    """
    tool = TOOLS.get(name)
    if tool is None:
        return None

    if name in ("apply_fix", "host_command", "schedule_reboot"):
        host = args.get("host")
        if host:
            hosts_cfg = (ctx.tenant.config or {}).get("ssh_hosts") or []
            if not any(h.get("name") == host for h in hosts_cfg):
                what = args.get("action") or args.get("command") or "that"
                return (f"to run {what} on {host} I need the sensor to have SSH access to it, and "
                        f"{host} is not in this site's host list: an admin can add it under "
                        f"Settings with a key on the sensor, then I can run it")

    if name == "device_deep_dive":
        active = ctx.db.query(m.Sensor).filter_by(tenant_id=ctx.tenant.id, status="active").first()
        if not active:
            return ("I need an active sensor enrolled on this site to run a live scan, and there "
                    "is none right now: an admin can enrol one from Settings, then I can scan")

    return None


def validate_args(tool: Tool, args: dict) -> str | None:
    """A sentence naming what is wrong with `args`, or None. The model
    supplies these keys, so an extra, missing or wrongly-typed one must
    become a refusal the user can read -- never a `TypeError` out of
    `tool.handler(ctx, **args)`."""
    if not isinstance(args, dict):
        return "I could not read the details for that. Ask me again in plain words."
    props = (tool.schema or {}).get("properties") or {}
    required = (tool.schema or {}).get("required") or []

    missing = [k for k in required if args.get(k) is None]
    if missing:
        return f"I need {', '.join(missing)} before I can {_describe(tool)}."
    unknown = sorted(k for k in args if k not in props)
    if unknown:
        return f"I do not have {', '.join(unknown)} as a detail for that. Ask me again in plain words."

    for key, value in args.items():
        if value is None:
            continue
        want = (props.get(key) or {}).get("type")
        if want == "string" and not isinstance(value, str):
            return f"{key} has to be text."
        if want == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            return f"{key} has to be a whole number."
        if want == "boolean" and not isinstance(value, bool):
            return f"{key} has to be yes or no."
        if want == "object" and not isinstance(value, dict):
            return f"{key} has to be a set of named values."
        enum = (props.get(key) or {}).get("enum")
        if enum and value not in enum:
            return f"{key} has to be one of: {', '.join(str(e) for e in enum)}."
    return None


def _reversible_for(tool: Tool, args: dict) -> bool:
    if tool.name == "apply_fix":
        return args.get("action") in TIER2_FIXES
    if tool.name in ("forget_device", "schedule_reboot"):
        return False
    return tool.reversible


def _sentence_case(text: str) -> str:
    """`text`, capitalized, with any ` -- ` it carries turned into a comma.

    A `Tool.reversal` (`chat/tools.py`) is written as "<verdict> -- <detail>"
    for the many places that print it plain, on its own line; folded into the
    single confirmation sentence `_build_proposal` builds below, that inner
    dash would otherwise survive as a second, nested one even after the
    sentence around it stopped using dashes."""
    text = text.replace(" -- ", ", ")
    return text[:1].upper() + text[1:] if text else text


def _target_of(args: dict) -> str:
    # Order matters only where two keys coexist: a tool with both `target`
    # and `network` (set_importance) is about the target. The later keys are
    # the full-surface tools' own names for the thing being confirmed -- a
    # domain, a job, a network, a method -- and were absent from this list,
    # so every one of those proposals read "Sure, I will <tool>." with the
    # thing being approved left unsaid.
    for key in ("host", "mac", "target", "key", "ip", "url", "section",
                "domain", "job_id", "network", "method", "subject", "ticket_id"):
        value = args.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


#: How many of a bulk action's targets to name in the confirmation before
#: falling back to "+N more". Enough to recognise what is about to happen,
#: short enough to still read as one sentence.
_BULK_NAMES_SHOWN = 4


def _bulk_target(ctx: "ToolContext", args: dict) -> str:
    """Describe a list-valued argument for a confirmation line.

    `_target_of` reads string arguments only, so a tool whose target is a list
    (`trust_devices(macs=[...])`) produced no target at all and the proposal
    read "Sure, I will trust devices." -- asking someone to approve thirteen
    writes without telling them it is thirteen, or which. A bulk confirmation
    has to say what it covers or it is not a confirmation.

    MACs are resolved to the labels the user actually recognises; anything
    that has no device row is named by its MAC rather than dropped, so the
    count in the sentence always matches the list being approved.
    """
    keys = [str(v).strip() for v in (args.get("keys") or []) if str(v).strip()]
    if keys:
        # Alert keys are already the names the dashboard shows; no lookup.
        shown = ", ".join(keys[:_BULK_NAMES_SHOWN])
        extra = len(keys) - _BULK_NAMES_SHOWN
        if extra > 0:
            shown += f" and {extra} more"
        return f"{len(keys)} alerts ({shown})"
    values = [str(v).strip() for v in (args.get("macs") or []) if str(v).strip()]
    if not values:
        return ""
    names: list[str] = []
    try:
        rows = (ctx.db.query(m.Device)
                .filter(m.Device.tenant_id == ctx.tenant.id, m.Device.mac.in_(values))
                .all())
        by_mac = {r.mac.lower(): (r.label or r.name or r.vendor or r.mac) for r in rows}
    except Exception:
        by_mac = {}
    for v in values:
        names.append(by_mac.get(v.lower(), v))
    shown = ", ".join(names[:_BULK_NAMES_SHOWN])
    extra = len(names) - _BULK_NAMES_SHOWN
    if extra > 0:
        shown += f" and {extra} more"
    return f"{len(values)} devices ({shown})"


def _policy_summary(args: dict) -> tuple[str, str]:
    """(what changes, what gets emptied) for a `set_policy` proposal --
    the exact fields and their new values, plus an explicit warning for any
    whole-list field the call leaves out, because the underlying settings
    route replaces those lists rather than merging them."""
    section = args.get("section") or ""
    values = args.get("values") if isinstance(args.get("values"), dict) else {}
    fields = ", ".join(f"{k} = {v}" for k, v in sorted(values.items())) or "nothing"
    missing = [f for f in _POLICY_LIST_FIELDS.get(section, ()) if f not in values]
    warn = ""
    if missing:
        warn = (f"; careful, this replaces the whole {section} section, so "
                f"{' and '.join(missing)} would be emptied")
    return fields, warn


def _proposal_parts(ctx: ToolContext, tool: Tool, args: dict, reversible: bool) -> tuple[str, str, str]:
    """`(head, effect, reversibility)` for the proposal sentence, which is
    always "<action> on <host>. <effect>. This is <reversible|not reversible>."
    built from the args, so the user confirms text that names exactly what
    will happen rather than a generic tool description."""
    effect = tool.effect
    reversal = tool.reversal or ("reversible" if reversible else "not reversible")

    if tool.name == "apply_fix":
        # Every apply_fix proposal that reaches confirmation is disruptive
        # (tier 3) -- the owner asked to see the exact steps, not only the
        # action's name, before he says yes. Late import: `remedy` imports
        # this module (see its module docstring), so this cannot be a
        # module-level import without a cycle.
        from ..remedy import fix_steps
        effect = fix_steps(args.get("action") or "", args.get("host") or "", args.get("arg") or "")
        if touches_own_port(ctx.tenant, args.get("host", ""), args.get("action"), db=ctx.db):
            effect += ("; this is also the machine that serves Vault's own dashboard, "
                       "so it could cut off your access to Vault")

    if tool.name == "set_policy":
        fields, warn = _policy_summary(args)
        return f"change the {args.get('section') or 'policy'} settings ({fields})", effect + warn, reversal

    if tool.name == "dns_filtering":
        # The direction IS the thing being approved. "add a block rule" and
        # "add an allow rule" are opposite decisions with identical args
        # shapes, so the head has to say which.
        kind, op = args.get("kind") or "block", args.get("op") or "add"
        verb = (f"remove the {kind} rule for {{}}" if op == "remove"
                else ("block {} on every device" if kind == "block" else "allow {} through the filter"))
    else:
        verb = (args.get("action") or args.get("command") or _OP_VERBS.get(args.get("op") or "")
                or _VERBS.get(tool.name, tool.name.replace("_", " ")))
    bulk = _bulk_target(ctx, args)
    if bulk:
        # "trust 13 devices (Exam room 1 camera, ... and 9 more)" rather than
        # the bare "trust devices" a list argument used to produce.
        return f"{verb} {bulk}", effect, reversal
    target = _target_of(args)
    if "{}" in verb:
        head = verb.format(target) if target else verb.replace(" {}", "").replace(": {}", "")
    else:
        head = f"{verb} on {target}" if target else verb
    return head, effect, reversal


def _build_proposal(ctx: ToolContext, tool: Tool, tier: int, args: dict) -> ChatProposal:
    reversible = _reversible_for(tool, args)
    head, effect, reversal = _proposal_parts(ctx, tool, args, reversible)

    action = ProposedAction(tool=tool.name, args=dict(args), tier=tier,
                            effect=effect, reversible=reversible, host=_target_of(args))
    token, expires = acttokens.mint_chat(ctx.db, ctx.tenant, tool.name, args, ctx.user.id,
                                         action.model_dump(), minutes=_CONFIRM_MINUTES)
    ctx.db.commit()

    # Three real sentences, not three clauses stitched together with a dash
    # each — `head` already names the target plainly ("reboot on clinic-nas"),
    # so this only had to stop making that read like a parenthetical aside.
    summary = (f"Sure, I will {head}. {_sentence_case(effect)}. "
               f"{_sentence_case(reversal)}. Reply *yes* and I will do it.")
    return ChatProposal(token=token, summary=summary, actions=[action], expires=expires.isoformat())


def dispatch(ctx: ToolContext, name: str, args: dict, confirm_token: str | None = None,
             require_confirm: bool = False) -> ToolResult | ChatProposal:
    """Run one tool call, or return the `ChatProposal` that has to be
    confirmed first. `require_confirm=True` forces the proposal path even
    for a tier-2 tool -- `routers/chat.py` sets it when the model asks to
    write to a target the user never named, so untrusted text in the DATA
    blob can never produce an unconfirmed write."""
    tool = TOOLS.get(name)
    if tool is None:
        return ToolResult(ok=False, status="error", text="I do not have a tool for that.")

    # Before the role gate, the proposal and the confirm branch alike: a demo
    # session must not even mint a confirmation token, and one it somehow
    # holds must not be spent. Every caller -- the model loop, the direct
    # "trust" command, `_handle_confirm`, both hand-off routes -- comes
    # through here, so this one check covers them all.
    if ctx.via_demo and name not in DEMO_READ_ONLY_TOOLS:
        return ToolResult(ok=False, status="denied", tier=tool.tier, text=DEMO_REFUSAL)

    if ctx.role not in ROLE_ORDER or ROLE_ORDER[ctx.role] < ROLE_ORDER[tool.min_role]:
        shown = ctx.role if ctx.role in ROLE_ORDER else "unrecognised"
        return ToolResult(
            ok=False, status="denied", tier=tool.tier,
            text=(f"I cannot {_describe(tool)} with your **{shown}** access. An "
                  f"**{tool.min_role}** on this site can, or I can prepare it for them."),
        )

    args = args if isinstance(args, dict) else {}
    # Identifiers as the model meant them. A model re-types keys, MACs and
    # hostnames from the conversation, and the transport can hand back a
    # Unicode hyphen, a non-breaking space or a zero-width character where
    # ASCII was; an exact lookup on that finds nothing and the bot tells the
    # owner the thing in front of them does not exist. See `clean_text`.
    args = {k: (clean_text(v) if isinstance(v, str) else v) for k, v in args.items()}
    problem = validate_args(tool, args)
    if problem:
        return ToolResult(ok=False, status="error", tier=tool.tier, text=problem)

    if name == "apply_fix" and args.get("action") not in KNOWN_FIXES:
        return ToolResult(ok=False, status="error", tier=3,
                          text=(f"\"{args.get('action')}\" is not one of the remediations I know how to "
                                f"run, so I will not try it. Tell me what should change and I will find "
                                f"the right one."))

    gap = capability_gap(ctx, name, args)
    if gap:
        return ToolResult(ok=False, status="denied", tier=tool.tier, text=gap)

    tier = effective_tier(ctx.db, ctx.tenant, tool, args)

    if confirm_token:
        # Keyed on the token, not on the tier: an escalated tier-2 tool
        # must burn its confirmation too, or "yes" would stay replayable.
        if not acttokens.consume_chat(ctx.db, confirm_token, tenant_id=ctx.tenant.id,
                                      tool=name, args=args, user_id=ctx.user.id):
            # `_burn` deliberately does not roll back on a loss (it owns no
            # caller state); the losing caller must, or the failed UPDATE's
            # write lock outlives this turn.
            ctx.db.rollback()
            return ToolResult(ok=False, status="error", tier=tier,
                              text=("That confirmation is not valid any more. It was already used, it "
                                    "expired, or it was for something else. Ask me again and I will "
                                    "re-propose it."))
    elif tier == 3 or require_confirm:
        return _build_proposal(ctx, tool, tier, args)

    try:
        result = tool.handler(ctx, **args)
    except Exception:
        # The confirmation is already spent (consume-before-run is what
        # stops a double execution), so say so plainly rather than leaving
        # the user waiting on something that never ran.
        ctx.db.rollback()
        return ToolResult(ok=False, status="error", tier=tier,
                          text=("I could not complete that. Nothing was changed, and the confirmation "
                                "is used up. Ask me again and I will re-propose it."))
    result.tier = tier
    return result
