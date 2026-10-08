"""Ask Vault reaches every surface the customer's own dashboard reaches.

Spec: docs/superpowers/specs/2026-09-20-chat-full-surface-design.md

The point of this file is the two matrix guards at the bottom
(`test_every_client_write_route_has_a_tool`, `test_each_tool_matches_its_route_role`),
not the per-tool cases above them. A per-tool test proves the tool I wrote
today works; the matrix guards are what stay true after this document is
forgotten -- they fail, by name, when somebody adds a route next month and
does not give the assistant a way to reach it.

That failure mode is the one worth guarding. Vault now tells the customer
on the dashboard that DNS filtering is off and they should switch it on. The
assistant sitting beside that prompt could not switch it on, because the
route was added and the tool was not. Nothing failed; the gap was simply
invisible until somebody looked.
"""

import pytest
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.chat import registry
from tests.test_seed_helpers import seeded_demo


def ctx_for(role="admin", username="demoadmin"):
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    u = db.query(m.User).filter_by(username=username).one()
    return registry.ToolContext(db=db, tenant=t, user=u, role=role, request_ip="127.0.0.1")


# --------------------------------------------------------------------------
# Write -- "fix anything"
# --------------------------------------------------------------------------

def test_dns_filtering_blocks_a_domain_and_the_rule_row_exists(client):
    """The headline gap. A customer reading the dashboard's standing "DNS
    filtering is off" prompt can now ask the assistant beside it to act."""
    seeded_demo(client)
    ctx = ctx_for()
    res = registry.dispatch(ctx, "dns_filtering",
                            {"domain": "casino.example", "kind": "block", "op": "add"})
    assert res.ok, res.text
    db = SessionLocal()
    assert db.query(m.DnsRule).filter_by(tenant_id=ctx.tenant.id, kind="block",
                                         domain="casino.example").first() is not None
    # The route -- not the handler -- owns the audit row, so its presence is
    # what proves the handler went through the route rather than around it.
    assert db.query(m.Audit).filter_by(user="chat:demoadmin", action="dns.rule").count() == 1


def test_dns_filtering_removes_a_rule_it_added(client):
    seeded_demo(client)
    ctx = ctx_for()
    registry.dispatch(ctx, "dns_filtering", {"domain": "casino.example", "kind": "block", "op": "add"})
    res = registry.dispatch(ctx, "dns_filtering", {"domain": "casino.example", "kind": "block", "op": "remove"})
    assert res.ok, res.text
    assert SessionLocal().query(m.DnsRule).filter_by(
        tenant_id=ctx.tenant.id, kind="block", domain="casino.example").first() is None


def test_dns_filtering_rejects_a_bad_domain_in_words(client):
    """The route raises HTTPException(422); the customer gets a sentence."""
    seeded_demo(client)
    res = registry.dispatch(ctx_for(), "dns_filtering",
                            {"domain": "not a domain", "kind": "block", "op": "add"})
    assert res.ok is False and "domain" in res.text.lower()


def test_dns_filtering_needs_an_admin(client):
    """`POST /dns/rules` is guarded `ctx=ADMIN`; the tool mirrors it."""
    seeded_demo(client)
    res = registry.dispatch(ctx_for(role="operator"), "dns_filtering",
                            {"domain": "casino.example", "kind": "block", "op": "add"})
    assert res.ok is False and res.status == "denied" and "admin" in res.text


def test_dns_exception_opens_a_domain_for_a_bounded_window(client):
    seeded_demo(client)
    ctx = ctx_for()
    res = registry.dispatch(ctx, "dns_exception", {"domain": "supplier.example", "hours": 4})
    assert res.ok, res.text
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    domains = [x["domain"] for x in ((t.config or {}).get("dns") or {}).get("exceptions") or []]
    assert "supplier.example" in domains


def test_cancel_work_stops_a_queued_job(client):
    seeded_demo(client)
    ctx = ctx_for()
    started = registry.dispatch(ctx, "rescan", {})
    assert started.queue_id
    res = registry.dispatch(ctx, "cancel_work", {"job_id": started.queue_id})
    assert res.ok, res.text
    assert SessionLocal().query(m.Queue).filter_by(id=started.queue_id).one().status == "cancelled"


def test_set_importance_marks_a_device_critical(client):
    seeded_demo(client)
    ctx = ctx_for()
    mac = SessionLocal().query(m.Device).filter_by(tenant_id=ctx.tenant.id).first().mac
    res = registry.dispatch(ctx, "set_importance", {"target": mac, "importance": "critical"})
    assert res.ok, res.text


def test_reset_baseline_is_tier_3_even_though_its_route_is_a_plain_admin_post(client):
    """Not a mirror of the route's guard but a deliberate escalation.

    Re-anchoring the LAN baseline marks every device currently on the
    network as known-from-the-start, which is the same suppression the
    warrant rule protects `trusted` for -- silent, not undoable by re-running
    anything, and invisible afterwards. It asks first.
    """
    seeded_demo(client)
    prop = registry.dispatch(ctx_for(), "reset_baseline", {})
    assert isinstance(prop, registry.ChatProposal)
    assert prop.actions[0].tier == 3 and prop.actions[0].reversible is False


def test_bulk_alerts_moves_several_and_reports_what_did_not_match(client):
    """The count is reported, not rounded up to what was asked for. A customer
    told "done" for eight when six moved has been told something false."""
    seeded_demo(client)
    ctx = ctx_for()
    keys = [a.key for a in SessionLocal().query(m.Alert)
            .filter_by(tenant_id=ctx.tenant.id).limit(2).all()]
    assert keys, "the demo tenant should seed alerts"
    res = registry.dispatch(ctx, "bulk_alerts", {"keys": keys + ["nope:not-a-real-key"], "op": "ack"})
    assert res.ok, res.text
    assert "matched nothing" in res.text
    db = SessionLocal()
    assert all(db.query(m.Alert).filter_by(tenant_id=ctx.tenant.id, key=k).one().status == "acked"
               for k in keys)


def test_decide_check_refuses_to_accept_a_risk_with_no_reason(client):
    """The route insists on a reason and the tool does not invent one.

    An accepted risk with no recorded reason is indistinguishable, six months
    later, from one nobody noticed -- so the refusal is the right answer
    rather than something to paper over in the handler.
    """
    seeded_demo(client)
    res = registry.dispatch(ctx_for(), "decide_check",
                            {"key": "wifi.guard", "op": "leave", "reason": ""})
    assert res.ok is False
    # Specifically the missing-reason refusal, not the 404 a wrong key would
    # give: those read very differently to a customer and only one of them is
    # the behaviour under test.
    assert "reason" in res.text.lower() and "not found" not in res.text.lower()


def test_block_attacker_sets_the_standing_policy(client):
    seeded_demo(client)
    res = registry.dispatch(ctx_for(), "block_attacker", {"method": "block_ip"})
    assert res.ok, res.text
    t = SessionLocal().query(m.Tenant).filter_by(slug="demo").one()
    # The exact key the route writes, not a substring of the whole config:
    # "block_ip" is also a FIX_ACTIONS name and appears in seeded rows, so a
    # loose search would pass without the tool having done anything.
    assert ((t.config or {}).get("auto_fix") or {}).get("attacker_method") == "block_ip"


def test_raise_ticket_is_open_to_a_viewer(client):
    """Deliberately viewer, mirroring the route. Requiring an operator to
    report a problem would mean the person most likely to notice is the one
    who cannot tell anybody."""
    seeded_demo(client)
    res = registry.dispatch(ctx_for(role="viewer"), "raise_ticket",
                            {"subject": "Sensor stopped reporting",
                             "body": "Nothing since yesterday afternoon."})
    assert res.ok, res.text
    assert SessionLocal().query(m.Ticket).filter_by(
        subject="Sensor stopped reporting").first() is not None


# --------------------------------------------------------------------------
# Read -- "advise about anything"
# --------------------------------------------------------------------------

def test_threat_detail_says_what_was_found_and_how_it_was_settled(client):
    """Shipped earlier today and reachable only from the drawer until now: an
    assistant that can run a repair but cannot read the finding it repaired
    cannot advise about it."""
    seeded_demo(client)
    res = registry.dispatch(ctx_for(role="viewer"), "threat_detail", {"kind": "breakin"})
    assert res.ok, res.text
    assert res.text.strip()


def test_work_status_says_what_guardian_is_doing_right_now(client):
    seeded_demo(client)
    ctx = ctx_for()
    registry.dispatch(ctx, "rescan", {})
    res = registry.dispatch(ctx, "work_status", {})
    assert res.ok and res.text.strip()


def test_setup_status_names_the_next_step(client):
    seeded_demo(client)
    res = registry.dispatch(ctx_for(role="viewer"), "setup_status", {})
    assert res.ok and res.text.strip()


def test_dns_activity_reads_what_is_being_blocked(client):
    seeded_demo(client)
    res = registry.dispatch(ctx_for(role="viewer"), "dns_activity", {})
    assert res.ok and res.text.strip()


def test_trend_answers_whether_it_is_getting_better(client):
    seeded_demo(client)
    res = registry.dispatch(ctx_for(role="viewer"), "trend", {"days": 30})
    assert res.ok and res.text.strip()


def test_network_map_describes_what_is_plugged_into_what(client):
    seeded_demo(client)
    res = registry.dispatch(ctx_for(role="viewer"), "network_map", {})
    assert res.ok and res.text.strip()


def test_update_status_lists_what_needs_patching(client):
    seeded_demo(client)
    res = registry.dispatch(ctx_for(role="viewer"), "update_status", {})
    assert res.ok and res.text.strip()


def test_camera_status_reads_the_census(client):
    seeded_demo(client)
    res = registry.dispatch(ctx_for(role="viewer"), "camera_status", {})
    assert res.ok and res.text.strip()


def test_check_detail_says_in_words_when_a_check_is_not_failing(client):
    """The payload returns None and the route 404s; the customer gets a
    sentence naming the key, not a status code."""
    seeded_demo(client)
    res = registry.dispatch(ctx_for(role="viewer"), "check_detail", {"key": "no.such.check"})
    assert res.ok is False and "no.such.check" in res.text


def test_check_detail_reads_a_real_failing_check(client):
    seeded_demo(client)
    ctx = ctx_for(role="viewer")
    from guardian_hub.payloads import security as sp
    page = sp.build(ctx.db, ctx.tenant)
    failing = [c for c in page.checks if c.passed is False]
    if not failing:
        pytest.skip("demo fixture has no failing check to read")
    c = failing[0]
    res = registry.dispatch(ctx, "check_detail", {"key": c.key, "target": getattr(c, "target", "")})
    assert res.ok, res.text
    assert c.title.lower()[:12] in res.text.lower()


def test_build_report_refuses_a_preset_that_does_not_exist(client):
    """Same shape as KNOWN_FIXES: an LLM can invent a report name, and the
    honest answer is a refusal, not a default report under the wrong title."""
    seeded_demo(client)
    res = registry.dispatch(ctx_for(), "build_report", {"preset": "quarterly_board_pack"})
    assert res.ok is False
    # The refusal has to come from the schema, in words that name the real
    # choices. Without the enum the builder raised and the customer got
    # dispatch's generic "the confirmation is used up" -- on a tier-1 tool
    # that never asked for one -- so `ok is False` alone proved nothing about
    # the enum, and this test passed before it existed.
    assert "has to be one of" in res.text and "threat_report" in res.text
    from guardian_hub import models as m2
    assert SessionLocal().query(m2.Report).filter_by(tenant_id=ctx_for().tenant.id).count() == 0


def test_every_read_tool_is_readable_by_a_viewer(client):
    """A read tool gated above `viewer` is a bug in the tool, not a policy:
    the dashboard shows all of these to a viewer, so refusing one in chat
    would make the assistant less capable than the page beside it."""
    seeded_demo(client)
    for name in ("threat_detail", "work_status", "setup_status", "dns_activity",
                 "trend", "network_map", "update_status", "camera_status", "check_detail"):
        assert registry.TOOLS[name].min_role == "viewer", name
        assert registry.TOOLS[name].tier == 1, name


# --------------------------------------------------------------------------
# The matrix guards
# --------------------------------------------------------------------------

#: Client-facing write routes that deliberately have no chat tool, each with
#: the reason. Anything not listed here and not covered fails the guard
#: below -- which is the whole point: the exemption has to be argued in
#: writing, in this file, rather than simply never noticed.
UNCOVERED_ON_PURPOSE = {
    # --- Authentication and identity. Nothing whose arguments an LLM chooses
    # goes near a session, a password or a role grant. A tool that could add a
    # user or change a role would make "talk to the assistant" a privilege
    # escalation path, and the blast radius is the whole tenant.
    "/auth/start", "/auth/logout", "/profile", "/settings/users",
    "/{tid}", "/{tid}/users", "/{tid}/users/{username}", "/{tid}/enrol-code",
    "/settings/tenants/{tid}", "/enrol-code", "/config",

    # --- Credentials in the request body. An integration's webhook URL, bot
    # token or API key is a secret being *written*, and `Scan.raw` taught this
    # codebase that a secret on a write path is a disclosure surface. The
    # assistant can say what is configured; it does not type the secret.
    "/integrations", "/integrations/{kind}", "/integrations/{kind}/test",
    "/integrations/telegram/pair",

    # --- Inbound callbacks from a third party, not customer surfaces at all.
    # Both live in a file that IS a customer surface, which is the only reason
    # they are here rather than in the file-level skip list above: Stripe POSTs
    # the first when a subscription changes and a provider POSTs the second
    # when something happens on their side, each authenticated by a signature
    # or a path token rather than by a session, so no signed-in customer
    # reaches either one on the dashboard. A tool for either would mean the
    # assistant forging a webhook to itself.
    "/billing/stripe/webhook", "/hooks/{token}",

    # --- The assistant's own ear and mouth. Neither is a missing tool.
    # `/voice/transcribe` is the path a question takes on its way IN, so a
    # tool that let the assistant transcribe would be the assistant listening
    # to itself. `/voice/speak` is plainer still: a text-to-audio utility any
    # signed-in viewer may call with arbitrary text, reading nothing and
    # writing nothing, so there is no action for a tool to expose. Both are
    # covered by tests/test_voice_routes.py and tests/test_voice_authority.py
    # instead -- the second because the interesting claim about voice is that
    # it has no authority, which a chat tool would be the wrong shape to make.
    "/voice/transcribe", "/voice/speak",

    # --- What counts as a problem. These move the thresholds, the autofix
    # overrides and the exception policy -- so relaxing one does not fix
    # anything, it makes alerts stop appearing. That is the warrant rule's
    # failure mode one level up: silent, wide, and invisible afterwards,
    # because the evidence that something is wrong is exactly what goes away.
    # A person should be looking at the screen that says so.
    "/settings/thresholds", "/settings/autofix/overrides",
    "/settings/exception-policy", "/settings/targets", "/maintenance",

    # --- Applying a firewall rule to the gateway. The DECLINE side has a
    # tool (`leave_firewall_rule`); this one deliberately does not, and not
    # by oversight. Vault has no write path to a gateway today, and the
    # day it has one, applying a rule has to stamp its automatic rollback
    # deadline in the same breath -- a tool that could apply one without
    # that is the version of this feature that locks somebody out at 2am.
    "/security/firewall/{rule_id}/apply",

    # --- Router administration. `/router/disable-remote-admin` already has
    # its own two-step confirm flow with a test-connection precondition, and
    # a second confirmation path through chat would be a way around the first.
    "/router", "/router/test", "/router/station",
    "/router/disable-remote-admin", "/router/disable-remote-admin/confirm",
    # The DNS change is the same shape and the same reasoning: it goes
    # through a test-connection precondition and a tier-3 single-use
    # confirmation, and a second route to it through chat would be a way
    # around the first. `read-dns` changes nothing, but it QUEUES a job the
    # sensor picks up seconds later, so a chat tool could only ever answer
    # "queued" — which is not an answer.
    "/router/read-dns", "/router/set-dns", "/router/set-dns/confirm",
    "/router/restore-dns",

    # --- Publishing. A report share link is readable by anyone holding it,
    # so minting one is a disclosure decision a person makes deliberately.
    "/reports/{report_id}", "/reports/{report_id}/share",

    # --- Per-user display settings. Not capabilities; nothing to advise about
    # and nothing that changes what Vault does.
    "/preferences", "/timezone", "/site-context",

    # --- Hardware configuration that needs someone at the device.
    "/recorders/{rid}",

    # --- Billing and the commercial relationship. Money is the one place a
    # wrong argument cannot be undone by asking again.
    "/billing/checkout", "/billing/portal", "/billing/trial",

    # --- API tokens and sensor credentials. Minting a token or rotating a
    # heartbeat secret is handing out a key; revoking a sensor is taking
    # Vault's eyes off a network. Both belong on a screen.
    "/settings/api-tokens", "/settings/api-tokens/{token_id}",
    "/heartbeats/{name}/rotate", "/settings/sensors/{sensor_id}/revoke",

    # --- The auto-heal pause itself (`/settings/autofix`). `tiers.py` reads
    # it to escalate every repair to "ask first" while it is on, so a tool
    # that could switch it off would be a tool for removing its own
    # confirmation step. It stays a deliberate act on a screen.
    "/settings/autofix",

    # --- Account and appearance settings, and the tenant switcher. Display
    # preferences and which tenant a session is looking at; neither is a
    # capability, and the switcher decides whose data the next answer is about.
    "/settings/appearance", "/settings/profile", "/settings/tenants",
    "/me/tenant", "/auth/verify",

    # --- Chat's own escalation path, already reachable: `raise_ticket` is the
    # tool for handing a conversation to a person. A second route into the
    # same thing would give the model two ways to do one job.
    "/chat/handoff", "/chat/checks/handoff",

    # --- Scheduled report delivery. A schedule mails a report repeatedly to
    # an address, which is a standing outbound commitment rather than a
    # one-off answer; `build_report` covers asking for a report now.
    "/reports/schedule",

    # --- Camera and recorder setup, which needs someone at the device.
    "/recorders",

    # --- Chat's own plumbing. A tool that drives chat from chat is a loop.
    "/chat", "/chat/confirm", "/chat/feedback",

    # --- The dashboard's half of the fix confirmation. Chat has its own token
    # path (`acttokens.consume_chat`); reaching the dashboard's would let one
    # confirmation serve two flows.
    "/fix/confirm",

    # --- Onboarding's own state cursor, written by the wizard as the customer
    # clicks through it. `setup_status` reads it. Writing it from chat would
    # let the assistant mark steps done that the customer never did.
    "/onboarding",
}


def _client_write_routes() -> dict[str, str]:
    """Every `POST`/`PATCH`/`DELETE` path a signed-in customer can reach,
    mapped to the role its `ctx=` guard requires. Owner-console routes
    (`/platform/...`) and the sensor's own upload path are not customer
    surfaces and are skipped."""
    import pathlib
    import re
    out: dict[str, str] = {}
    routers = pathlib.Path(__file__).resolve().parents[1] / "guardian_hub" / "routers"
    # `(?:async\s+)?def` rather than a bare `def`: a handler that `await`s
    # anything has to be declared `async def`, and a regex that only matched
    # `def` would silently skip every such route, discovering none of them
    # rather than flagging them uncovered. `/voice/transcribe` is the route
    # that found this, and it has since been made synchronous on purpose
    # (see `routers/voice.py`) -- but `integrations.inbound_hook`,
    # `billing.stripe_webhook`, `inbound.inbound` and `slackbot.slack_events`
    # all still `await request.body()`, so this alternation is still what
    # keeps them visible to the gate.
    pat = re.compile(
        r'@router\.(?:post|patch|put|delete)\("(?P<path>[^"]+)"[^\n]*\)\s*\n'
        r'\s*(?:async\s+)?def [a-z_0-9]+\((?P<args>[^)]*)\)', re.S)
    for f in sorted(routers.glob("*.py")):
        # Not customer surfaces: the sensor's upload path, the owner console
        # (`platform.py`), the MSP level above tenants that the platform owner
        # manages (`partners.py` -- its `/{pid}/...` routes act on partner and
        # staff rows, which no customer can see, let alone ask about), the
        # public unauthenticated routes, inbound webhooks, the Slack bot's own
        # plumbing, the versioned public API and the marketing lead form.
        if f.name in ("sensor.py", "platform.py", "partners.py", "public.py", "inbound.py",
                      "slackbot.py", "api_v1.py", "leads.py"):
            continue
        for mt in pat.finditer(f.read_text()):
            path = mt.group("path")
            if path.startswith("/platform"):
                continue
            role = "viewer"
            for want in ("ADMIN", "OPERATOR", "VIEWER"):
                if re.search(rf"ctx\s*=\s*{want}\b", mt.group("args")):
                    role = want.lower()
                    break
            out[path] = role
    return out


def test_every_client_write_route_has_a_tool():
    """The guard that keeps the spec true after the spec is forgotten.

    Matching is by the route path a tool declares in `Tool.route`, not by
    name, so renaming a tool cannot silently open a hole.
    """
    covered = {t.route for t in registry.TOOLS.values() if getattr(t, "route", "")}
    missing = sorted(p for p in _client_write_routes()
                     if p not in covered and p not in UNCOVERED_ON_PURPOSE)
    assert not missing, (
        "These customer write routes have no Ask Guardian tool, so a customer "
        "can do them on the dashboard and cannot ask for them:\n  "
        + "\n  ".join(missing)
        + "\n\nAdd a tool in chat/tools.py, or add the path to "
          "UNCOVERED_ON_PURPOSE in this file with the reason.")


def test_each_tool_matches_its_route_role():
    """A tool one step below its route's guard hands a viewer a write. It is
    a one-character mistake and the worst one available in this file, so it
    is asserted rather than reviewed.

    A tool may be *stricter* than its route -- `reset_baseline` deliberately
    is -- but never looser.
    """
    order = {"viewer": 0, "operator": 1, "admin": 2}
    routes = _client_write_routes()
    for name, tool in registry.TOOLS.items():
        route = getattr(tool, "route", "")
        if not route or route not in routes:
            continue
        assert order[tool.min_role] >= order[routes[route]], (
            f"{name} requires {tool.min_role} but its route {route} requires "
            f"{routes[route]}: chat would be a way around the dashboard's guard.")


# --------------------------------------------------------------------------
# What the customer is asked to confirm
# --------------------------------------------------------------------------
#
# `routers/chat.py` forces the proposal path for ANY tier-2 tool the moment
# the model names a target the user did not type -- so every write tool here
# can end up as a confirmation sentence, not only the tier-3 one. A
# confirmation that does not name what it confirms is not a confirmation:
# "Sure, I will dns filtering." asks for a yes to something unstated.

def _head(prop) -> str:
    return prop.summary.lower()


def test_dns_filtering_proposal_names_the_domain_and_the_direction(client):
    seeded_demo(client)
    prop = registry.dispatch(ctx_for(), "dns_filtering",
                             {"domain": "casino.example", "kind": "block", "op": "add"},
                             require_confirm=True)
    assert isinstance(prop, registry.ChatProposal)
    assert "casino.example" in _head(prop) and "block" in _head(prop)


def test_bulk_alerts_proposal_says_how_many_and_which(client):
    """Thirteen writes approved as one sentence must say thirteen, and name
    enough of them to recognise -- the same argument `_bulk_target` makes for
    `trust_devices`."""
    seeded_demo(client)
    ctx = ctx_for()
    keys = [a.key for a in SessionLocal().query(m.Alert)
            .filter_by(tenant_id=ctx.tenant.id).limit(3).all()]
    prop = registry.dispatch(ctx, "bulk_alerts", {"keys": keys, "op": "ack"}, require_confirm=True)
    assert isinstance(prop, registry.ChatProposal)
    assert "3 alerts" in _head(prop) and keys[0].lower() in _head(prop)
    assert "acknowledge" in _head(prop)


def test_reset_baseline_proposal_reads_as_a_sentence_not_a_tool_name(client):
    seeded_demo(client)
    prop = registry.dispatch(ctx_for(), "reset_baseline", {})
    assert "reset baseline" not in _head(prop)
    assert "baseline" in _head(prop)


def test_block_attacker_proposal_names_the_method(client):
    seeded_demo(client)
    prop = registry.dispatch(ctx_for(), "block_attacker", {"method": "block_ip"}, require_confirm=True)
    assert "block_ip" in _head(prop)


def test_send_sensor_invite_proposal_names_the_network(client):
    seeded_demo(client)
    ctx = ctx_for()
    net = SessionLocal().query(m.Network).filter_by(tenant_id=ctx.tenant.id).first()
    prop = registry.dispatch(ctx, "send_sensor_invite", {"network": net.name}, require_confirm=True)
    assert net.name.lower() in _head(prop) and " on " not in _head(prop)


# --------------------------------------------------------------------------
# Traffic -- "who is talking to whom" (Task 11)
# --------------------------------------------------------------------------

def test_traffic_tools_are_viewer_tier_1(client):
    for name in ("top_talkers", "device_traffic", "server_peers"):
        assert registry.TOOLS[name].tier == 1 and registry.TOOLS[name].min_role == "viewer", name


def test_router_or_none_mode_answer_opens_with_the_limitation(client):
    tid = seeded_demo(client)
    # The demo declares AND reports a mirror port now (`seed.py`: the practice
    # has traffic to look at), so on it the tool rightly answers with the
    # listing. This test is about the other case, so it declares "none" on
    # every network -- `payloads.traffic.mode_for`: a declared "none" stays
    # "none" whatever the sensor reports -- and expects the limitation.
    from guardian_hub.db import SessionLocal
    db = SessionLocal()
    try:
        for n in db.query(m.Network).filter_by(tenant_id=tid).all():
            n.traffic_mode = "none"
        db.commit()
    finally:
        db.close()
    res = registry.dispatch(ctx_for(role="viewer"), "top_talkers", {"hours": 1})
    assert res.ok and res.text.lower().startswith("i can")   # "I can only see..." / "I cannot see traffic..."


def test_device_traffic_asks_which_device_when_ambiguous(client):
    seeded_demo(client)
    res = registry.dispatch(ctx_for(role="viewer"), "device_traffic", {"target": "zz:zz:not-here"})
    assert res.ok is False and "no device" in res.text.lower()
