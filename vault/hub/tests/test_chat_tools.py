import pytest
from guardian_hub import demoscrub
from tests.test_seed_helpers import seeded_demo

#: The demo host these tests ask to reboot: the fixture's third host as
#: `guardian_hub.demoscrub` renames it for the public demo tenant.
HOST = demoscrub.map_host_name("safehome")
#: A different demo host that also exists, for the "a confirmation does not
#: transfer to other args" case.
OTHER_HOST = demoscrub.map_host_name("prod")
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.chat import registry, tiers


def ctx_for(role="admin", username="demoadmin"):
    db = SessionLocal(); t = db.query(m.Tenant).filter_by(slug="demo").one()
    u = db.query(m.User).filter_by(username=username).one()
    return registry.ToolContext(db=db, tenant=t, user=u, role=role, request_ip="127.0.0.1")


def test_every_tool_declares_a_tier_a_role_and_an_effect():
    for name, tool in registry.TOOLS.items():
        assert tool.tier in (1, 2, 3) and tool.min_role in ("viewer", "operator", "admin"), name
        assert tool.description and tool.schema and tool.effect and tool.handler, name


def test_viewer_is_refused_a_tier2_tool_and_told_who_can(client):
    seeded_demo(client)
    r = registry.dispatch(ctx_for(role="viewer"), "ack_alert", {"key": "lan:unverified"})
    assert r.ok is False and "operator" in r.text and "sudo" not in r.text


def test_tier3_returns_a_proposal_then_executes_with_the_token(client):
    seeded_demo(client)
    ctx = ctx_for()
    prop = registry.dispatch(ctx, "apply_fix", {"host": HOST, "action": "reboot"})
    assert isinstance(prop, registry.ChatProposal) and prop.token
    assert prop.actions[0].tier == 3 and prop.actions[0].reversible is False
    assert SessionLocal().query(m.Queue).filter_by(path="/api/fix").count() == 0
    res = registry.dispatch(ctx, "apply_fix", {"host": HOST, "action": "reboot"},
                            confirm_token=prop.token)
    assert res.ok and res.queue_id
    db = SessionLocal()
    assert db.query(m.Queue).filter_by(id=res.queue_id).one().body["action"] == "reboot"
    assert db.query(m.Audit).filter_by(user="chat:demoadmin", action="fix").count() == 1


def test_tier2_executes_immediately_and_states_the_undo(client):
    seeded_demo(client)
    ctx = ctx_for()
    mac = SessionLocal().query(m.Device).filter_by(tenant_id=ctx.tenant.id).first().mac
    res = registry.dispatch(ctx, "quarantine_device", {"mac": mac, "on": True})
    assert res.ok and "undo" in res.text.lower()
    assert SessionLocal().query(m.Device).filter_by(mac=mac).first().quarantined is True


def test_unseen_host_escalates_a_tier2_fix_to_a_proposal(client):
    seeded_demo(client)
    ctx = ctx_for()
    # 'demo' has fix history in the fixtures; invent a host with none
    ctx.tenant.config = {**ctx.tenant.config,
                         "ssh_hosts": ctx.tenant.config["ssh_hosts"] + [{"name": "brandnew", "target": "root@1.1.1.1"}]}
    ctx.db.commit()
    assert tiers.effective_tier(ctx.db, ctx.tenant, registry.TOOLS["apply_fix"],
                               {"host": "brandnew", "action": "enable_ufw"}) == 3
    out = registry.dispatch(ctx, "apply_fix", {"host": "brandnew", "action": "enable_ufw"})
    assert isinstance(out, registry.ChatProposal)


def test_capability_gap_names_exactly_what_is_missing(client):
    seeded_demo(client)
    ctx = ctx_for()
    gap = registry.capability_gap(ctx, "apply_fix", {"host": "not-configured", "action": "enable_ufw"})
    assert gap and "not-configured" in gap and "ssh" in gap.lower()


def test_no_tool_ever_returns_a_shell_recipe(client):
    seeded_demo(client)
    ctx = ctx_for()
    for name, tool in registry.TOOLS.items():
        assert "sudo " not in tool.effect and "ssh " not in tool.effect.lower(), name


# --------------------------------------------------------------------------
# Fix round 1: confirmation binding, the two capability axes, site_check,
# argument validation and the ToolResult tier/status contract.
# --------------------------------------------------------------------------

import datetime as dt
import threading

from guardian_hub import acttokens


REBOOT = {"host": HOST, "action": "reboot"}


def _propose(ctx, tool=None, args=None):
    out = registry.dispatch(ctx, tool or "apply_fix", dict(args or REBOOT))
    assert isinstance(out, registry.ChatProposal), out
    return out


def test_operator_is_refused_a_tier3_tool_and_told_who_can(client):
    seeded_demo(client)
    r = registry.dispatch(ctx_for(role="operator"), "forget_device", {"mac": "aa:bb:cc:dd:ee:ff"})
    assert r.ok is False and r.status == "denied"
    assert "admin" in r.text and "sudo" not in r.text and "ssh " not in r.text.lower()


def test_build_report_is_operator_only(client):
    seeded_demo(client)
    r = registry.dispatch(ctx_for(role="viewer"), "build_report", {"days": 7})
    assert r.ok is False and r.status == "denied" and "operator" in r.text
    assert SessionLocal().query(m.Report).filter_by(kind="manual").count() == 0


def test_an_unknown_role_is_denied_not_crashed(client):
    seeded_demo(client)
    r = registry.dispatch(ctx_for(role="superuser"), "lookup_ip", {"ip": "1.1.1.1"})
    assert r.ok is False and r.status == "denied"


def test_a_proposal_names_the_action_the_host_the_effect_and_reversibility(client):
    seeded_demo(client)
    prop = _propose(ctx_for())
    summary = prop.summary.lower()
    assert "reboot" in summary and HOST in summary
    # F13: this used to read "Sure -- I will reboot on X -- ... -- ...", three
    # clauses stitched together with a literal double hyphen where a
    # sentence was meant. Plain sentences now, and no ` -- ` anywhere.
    assert " -- " not in prop.summary and "revers" in summary
    assert "yes" in summary and "sudo" not in summary


def test_a_set_policy_proposal_names_the_fields_and_warns_about_emptying(client):
    seeded_demo(client)
    prop = _propose(ctx_for(), "set_policy", {"section": "targets", "values": {"sites": []}})
    assert "targets" in prop.summary and "sites" in prop.summary
    assert "hosts" in prop.summary and "emptied" in prop.summary


def test_a_confirmation_is_single_use(client):
    seeded_demo(client)
    ctx = ctx_for()
    prop = _propose(ctx)
    first = registry.dispatch(ctx, "apply_fix", dict(REBOOT), confirm_token=prop.token)
    assert first.ok and first.status == "queued" and first.tier == 3
    replay = registry.dispatch(ctx, "apply_fix", dict(REBOOT), confirm_token=prop.token)
    assert replay.ok is False and replay.status == "error" and "again" in replay.text
    assert SessionLocal().query(m.Queue).filter_by(path="/api/fix").count() == 1


def test_a_confirmation_is_single_use_under_concurrency(client):
    seeded_demo(client)
    prop = _propose(ctx_for())
    results, lock = [], threading.Lock()

    def worker():
        ctx = ctx_for()
        try:
            out = registry.dispatch(ctx, "apply_fix", dict(REBOOT), confirm_token=prop.token)
            ok = bool(getattr(out, "ok", False))
        finally:
            ctx.db.close()
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for th in threads: th.start()
    for th in threads: th.join()
    assert results.count(True) == 1
    assert SessionLocal().query(m.Queue).filter_by(path="/api/fix").count() == 1


def test_a_confirmation_does_not_transfer_to_other_args(client):
    seeded_demo(client)
    ctx = ctx_for()
    prop = _propose(ctx)
    out = registry.dispatch(ctx, "apply_fix", {"host": OTHER_HOST, "action": "reboot"},
                            confirm_token=prop.token)
    assert isinstance(out, registry.ToolResult) and out.ok is False and out.status == "error"
    assert SessionLocal().query(m.Queue).filter_by(path="/api/fix").count() == 0


def test_a_confirmation_does_not_transfer_to_another_user(client):
    seeded_demo(client)
    prop = _propose(ctx_for())
    other = ctx_for(role="admin", username="demo")     # same tenant, same role, different person
    out = registry.dispatch(other, "apply_fix", dict(REBOOT), confirm_token=prop.token)
    assert isinstance(out, registry.ToolResult) and out.ok is False and out.status == "error"
    assert SessionLocal().query(m.Queue).filter_by(path="/api/fix").count() == 0


def test_a_confirmation_does_not_transfer_to_another_tenant(client):
    seeded_demo(client)
    ctx = ctx_for()
    prop = _propose(ctx)
    assert acttokens.consume_chat(ctx.db, prop.token, tenant_id="not-this-tenant",
                                  tool="apply_fix", args=dict(REBOOT), user_id=ctx.user.id) is False
    # ... and the rightful owner can still spend it
    assert acttokens.consume_chat(ctx.db, prop.token, tenant_id=ctx.tenant.id,
                                  tool="apply_fix", args=dict(REBOOT), user_id=ctx.user.id) is True


def test_a_chat_token_cannot_be_spent_on_the_public_act_route(client):
    seeded_demo(client)
    prop = _propose(ctx_for())
    assert client.get(f"/act/{prop.token}/trust").status_code == 410
    assert SessionLocal().query(m.Queue).filter_by(path="/api/fix").count() == 0


def test_an_act_token_cannot_be_spent_as_a_chat_confirmation(client):
    seeded_demo(client)
    ctx = ctx_for()
    mac = ctx.db.query(m.Device).filter_by(tenant_id=ctx.tenant.id).first().mac
    tok = acttokens.mint(ctx.db, ctx.tenant, "device", mac, ["trust"]); ctx.db.commit()
    assert acttokens.consume_chat(ctx.db, tok, tenant_id=ctx.tenant.id, tool="trust_device",
                                  args={"mac": mac}, user_id=ctx.user.id) is False
    assert ctx.db.get(m.ActToken, tok).used == ""


def test_unconsumed_confirmations_are_capped_per_user(client):
    seeded_demo(client)
    ctx = ctx_for()
    tokens = [_propose(ctx, args={**REBOOT, "arg": str(i)}).token for i in range(7)]
    now = dt.datetime.now(dt.timezone.utc)
    rows = ctx.db.query(m.ActToken).filter_by(tenant_id=ctx.tenant.id, kind="chat", used="").all()
    live = [r for r in rows if r.expires > now]
    assert len(live) <= acttokens.CHAT_TOKEN_CAP
    stale = registry.dispatch(ctx, "apply_fix", {**REBOOT, "arg": "0"}, confirm_token=tokens[0])
    assert stale.ok is False and stale.status == "error"


def test_capability_gap_blocks_execution_through_dispatch(client):
    seeded_demo(client)
    ctx = ctx_for()
    r = registry.dispatch(ctx, "host_command", {"host": "not-configured", "command": "disk"})
    assert r.ok is False and r.status == "denied" and "ssh" in r.text.lower()
    assert "role" not in r.text                     # a role grant does not create an SSH key
    assert SessionLocal().query(m.Queue).filter_by(path="/api/host-command").count() == 0


def test_an_unknown_fix_action_is_rejected_never_run_at_tier2(client):
    seeded_demo(client)
    ctx = ctx_for()
    r = registry.dispatch(ctx, "apply_fix", {"host": "prod", "action": "rm_rf_slash"})
    assert isinstance(r, registry.ToolResult) and r.ok is False and r.status == "error"
    assert SessionLocal().query(m.Queue).filter_by(path="/api/fix").count() == 0


def test_site_check_only_probes_configured_public_sites(client, monkeypatch):
    seeded_demo(client)
    ctx = ctx_for()
    meta = registry.dispatch(ctx, "site_check", {"url": "http://169.254.169.254/latest/meta-data"})
    assert meta.ok is False and "not one of them" in meta.text and meta.data == {}
    loop = registry.dispatch(ctx, "site_check", {"url": "http://127.0.0.1:8800/health"})
    assert loop.ok is False

    # even a *configured* loopback/private target is refused from chat
    ctx.tenant.config = {**ctx.tenant.config,
                         "sites": list(ctx.tenant.config["sites"]) + [{"name": "loop", "url": "http://127.0.0.1:8800/"}]}
    ctx.db.commit()
    configured_loopback = registry.dispatch(ctx, "site_check", {"url": "loop"})
    assert configured_loopback.ok is False and "public" in configured_loopback.text

    # a configured public site is probed -- and only ever at its configured
    # url. The demo tenant's own sites are `.example` names (see
    # `guardian_hub.demoscrub`) and so deliberately do not resolve, which
    # `_is_public_http` refuses -- so this leg configures a site on a name
    # with a public address, keeping this test independent of external DNS.
    ctx.tenant.config = {**ctx.tenant.config,
                         "sites": list(ctx.tenant.config["sites"]) + [{"name": "resolvable", "url": "https://93.184.216.34/"}]}
    ctx.db.commit()
    seen: dict = {}

    def fake_probe(site):
        seen.update(site)
        return site["name"], {"ms": 12, "code": 200, "ok": True}

    import guardian_hub.routers.live as live
    monkeypatch.setattr(live, "_probe", fake_probe)
    wanted = next(x for x in ctx.tenant.config["sites"] if x["name"] == "resolvable")
    out = registry.dispatch(ctx, "site_check", {"url": wanted["name"]})
    assert out.ok and seen["url"] == wanted["url"] and out.data["code"] == 200


def test_a_tier2_write_is_audited_with_the_callers_ip(client):
    seeded_demo(client)
    ctx = ctx_for()
    mac = ctx.db.query(m.Device).filter_by(tenant_id=ctx.tenant.id).first().mac
    res = registry.dispatch(ctx, "trust_device", {"mac": mac})
    assert res.ok and res.status == "ok" and res.tier == 2
    row = (SessionLocal().query(m.Audit).filter_by(action="device.update", user="chat:demoadmin")
           .order_by(m.Audit.id.desc()).first())
    assert row is not None and row.ip == "127.0.0.1"


def test_tool_results_carry_a_tier_and_one_of_the_four_statuses(client):
    seeded_demo(client)
    ctx = ctx_for()
    unknown = registry.dispatch(ctx, "not_a_tool", {})
    assert (unknown.ok, unknown.status) == (False, "error")
    denied = registry.dispatch(ctx_for(role="viewer"), "ack_alert", {"key": "lan:unverified"})
    assert (denied.ok, denied.status, denied.tier) == (False, "denied", 2)
    ok = registry.dispatch(ctx, "lookup_ip", {"ip": "203.0.113.9"})
    assert (ok.ok, ok.status, ok.tier) == (True, "ok", 1)
    queued = registry.dispatch(ctx, "rescan", {})
    assert (queued.ok, queued.status, queued.tier) == (True, "queued", 1)
    for res in (unknown, denied, ok, queued):
        assert res.status in ("ok", "error", "queued", "denied")


def test_bad_arguments_are_refused_not_crashed(client):
    seeded_demo(client)
    ctx = ctx_for()
    assert registry.dispatch(ctx, "trust_device", {}).status == "error"
    assert registry.dispatch(ctx, "trust_device", {"mac": "aa", "nope": 1}).status == "error"
    assert registry.dispatch(ctx, "snooze_alert", {"key": "k", "hours": "eight"}).status == "error"
    assert registry.dispatch(ctx, "quarantine_device", {"mac": "aa", "on": "yes"}).status == "error"
    assert registry.dispatch(ctx, "host_command", {"host": "prod", "command": "rm"}).status == "error"


def test_a_queue_only_tool_publishes_on_the_live_bus_like_the_dashboard(client):
    seeded_demo(client)
    ctx = ctx_for()
    from guardian_hub.live import bus
    seen = []
    orig = bus.publish
    bus.publish = lambda tid, kind, data: (seen.append((tid, kind, data)), orig(tid, kind, data))[1]
    try:
        res = registry.dispatch(ctx, "rescan", {})
    finally:
        bus.publish = orig
    assert res.ok and any(kind == "queue" and tid == ctx.tenant.id for tid, kind, _ in seen)


def test_explain_alert_only_blames_a_guardian_action_that_lines_up(client):
    seeded_demo(client)
    ctx = ctx_for()
    a = m.Alert(tenant_id=ctx.tenant.id, key="sec:attacker:prod:198.51.100.7", sev="CRIT",
                msg="Repeated failed SSH logins on prod", status="open",
                first_seen=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                last_seen=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    ctx.db.add(a)
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=40)).isoformat(timespec="seconds")
    ctx.db.add(m.Fix(tenant_id=ctx.tenant.id, ts=old, host="prod", action="limit_ssh", ok=True))
    ctx.db.commit()
    stale = registry.dispatch(ctx, "explain_alert", {"key": a.key})
    assert stale.ok and stale.data["caused_by"] == "" and "this was us" not in stale.text

    fresh = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    ctx.db.add(m.Fix(tenant_id=ctx.tenant.id, ts=fresh, host="prod", action="limit_ssh", ok=True))
    ctx.db.commit()
    now_ours = registry.dispatch(ctx, "explain_alert", {"key": a.key})
    assert now_ours.data["caused_by"] == "fix" and "this was us" in now_ours.text


def test_tier3_proposal_carries_the_exact_steps_not_only_the_action_name(client):
    """The owner asked to see "your steps on how you will do it" before
    saying yes, not just the bare action name -- a tier-3 `apply_fix`
    proposal's `effect` must name the host, what will actually run, and
    what happens if a safety check fails."""
    seeded_demo(client)
    prop = _propose(ctx_for())      # apply_fix reboot on HOST, see REBOOT above
    summary = prop.summary
    assert HOST in summary
    assert "systemctl reboot" in summary
    assert "rollback" in summary.lower() or "no rollback" in summary.lower()


def test_security_headers_proposal_names_host_file_and_rollback(client):
    """Same contract for the new security-headers fix specifically -- the
    proposal must name the host, that it edits nginx, and that a failed
    `nginx -t` restores the backup rather than reloading anything."""
    seeded_demo(client)
    ctx = ctx_for()
    args = {"host": HOST, "action": "security_headers", "arg": "example.com|hsts,nosniff"}
    prop = _propose(ctx, args=args)
    summary = prop.summary
    assert HOST in summary
    assert "nginx" in summary.lower()
    assert "nginx -t" in summary or "test" in summary.lower()
    assert "restore" in summary.lower() or "back up" in summary.lower() or "backup" in summary.lower()
    assert prop.actions[0].tier == 3
