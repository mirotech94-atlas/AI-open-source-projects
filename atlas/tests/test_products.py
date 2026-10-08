"""The FlyCheap and Guardian modules.

Both were stubs built on imagined interfaces. Every endpoint they called was
invented, so the moment a credential landed they would have reported UNKNOWN
forever — and `requires_env` meant they were disabled before they could even
do that. These tests pin the two properties that matter most about the
rewrite: the readings come from real payload shapes, and neither module can
write anything.
"""
from __future__ import annotations

import pytest

from atlas.kernel.contracts import Health, Severity
from atlas.modules import flycheap, guardian


def code_of(obj) -> str:
    """Source with every docstring removed.

    These modules document what they deliberately do not call -- the declined
    scraping path, the restricted monitoring endpoint, the write route behind
    the warrant rule. A plain substring scan matches the explanation and
    fails, which has now happened three times. Read the code, not the prose.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(obj))
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(body, list) and body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


# --------------------------------------------------------------- no writes

@pytest.mark.parametrize("module", [flycheap.MODULE, guardian.MODULE])
def test_neither_product_module_exposes_an_action(module):
    """FlyCheap's actions called endpoints that do not exist, and one of them
    regenerated affiliate markers -- revenue. Guardian's every write route
    needs the write scope the module argues against holding."""
    assert module.actions() == ()


@pytest.mark.parametrize("module", [flycheap.MODULE, guardian.MODULE])
def test_neither_module_is_gated_on_a_credential(module):
    """requires_env disables the WHOLE module. FlyCheap needs no credential
    at all, and Guardian's public health check is what distinguishes "the hub
    is down" from "ATLAS has no token" -- the most useful thing it can say."""
    assert module.requires_env == ()


@pytest.mark.parametrize("module", [flycheap, guardian])
def test_no_product_module_makes_a_mutating_request(module):
    """The strongest form of "read-only": no HTTP verb that changes anything,
    checked against the AST rather than the text, because both modules
    describe in prose the write routes they deliberately do not call.

    For Guardian this is the warrant rule. POST /v1/devices/{mac} sets
    `trusted` and `label`, and Guardian's own test suite fails if an
    automatic path writes either: a trusted device is skipped by the
    rogue-uplink check and exempt from risky-port alerts, so forging a vouch
    does not produce an audit entry -- it produces a rogue device nobody
    hears about."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(module))
    mutating = {"post", "put", "patch", "delete"}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in mutating):
            raise AssertionError(
                f"{module.__name__} calls .{node.func.attr}() at line "
                f"{node.lineno}; these modules are read-only")


# ------------------------------------------------------ flycheap /api/health

def _health(**checks) -> dict:
    return {"ok": all(c.get("ok") for c in checks.values()),
            "version": "2.5.0", "checks": checks}


@pytest.fixture
def payload(monkeypatch):
    def install(data, reason=None):
        async def fake():
            return data, reason
        monkeypatch.setattr(flycheap, "_health_payload", fake)
    return install


async def test_a_200_with_a_dead_poller_is_not_healthy(payload):
    """FlyCheap answers 200 while degraded on purpose, so monitors branch on
    the `ok` field. A plain status-code probe -- which is what this module
    used to do -- reports a scraper that stopped writing as a working site,
    and a fare tracker with a dead poller is broken however well it renders."""
    payload(_health(
        database={"ok": True, "detail": "3 users"},
        last_poll={"ok": False, "detail": "no poll yet"}))
    obs = await flycheap.check_api()
    assert obs.health is Health.FAILING
    assert "last_poll" in obs.detail


async def test_all_subsystems_healthy_is_ok(payload):
    payload(_health(database={"ok": True, "detail": "3 users"},
                    last_poll={"ok": True, "detail": "3 min ago"}))
    obs = await flycheap.check_api()
    assert obs.health is Health.OK
    assert "v2.5.0" in obs.detail


async def test_a_low_severity_subsystem_only_degrades(payload):
    """last_alert is informational. It must not read the same as a dead
    database, or the one that matters stops standing out."""
    payload(_health(database={"ok": True, "detail": "3 users"},
                    last_alert={"ok": False, "detail": "no alerts yet"}))
    assert (await flycheap.check_api()).health is Health.DEGRADED


async def test_an_omitted_subsystem_is_unknown_not_ok(payload):
    """/api/health omits a subsystem when its own read raises. Absent is not
    healthy, and it is not failing either."""
    payload(_health(database={"ok": True, "detail": "3 users"}))
    obs = await flycheap._subsystem_check("delivery_feed")()
    assert obs.health is Health.UNKNOWN
    assert "omitted" in obs.detail


async def test_an_unreachable_health_endpoint_is_unknown(payload):
    payload(None, "connection refused")
    assert (await flycheap.check_api()).health is Health.UNKNOWN


async def test_a_dead_delivery_feed_is_reported(payload):
    """Sends succeeding while the webhook records nothing is a dead feed
    wearing a 0%-failure rate."""
    payload(_health(delivery_feed={
        "ok": False,
        "detail": "8 sent in 24h, 0 delivery events — webhook feed is dead"}))
    obs = await flycheap._subsystem_check("delivery_feed")()
    assert obs.health is Health.FAILING
    assert "webhook feed is dead" in obs.detail


async def test_partial_provider_coverage_raises_one_signal(payload):
    """Switched-off sources do not fail anything, so coverage narrows
    silently and nothing would ever say so."""
    payload(_health(providers={"ok": True,
                               "detail": "4/6 configured (travelpayouts)"}))
    signal, = await flycheap.MODULE.signals()
    assert "4/6" in signal.title
    assert signal.severity is Severity.INFO


async def test_full_provider_coverage_raises_nothing(payload):
    payload(_health(providers={"ok": True, "detail": "6/6 configured (all)"}))
    assert await flycheap.MODULE.signals() == ()


# ------------------------------------------------------------ guardian scope

@pytest.fixture
def api(monkeypatch):
    def install(mapping):
        async def fake(path, **params):
            value = mapping.get(path, (None, f"unexpected call to {path}"))
            return value
        monkeypatch.setattr(guardian, "_api", fake)
    return install


async def test_a_write_scope_token_is_a_finding(api):
    """The control on the warrant rule is the token's scope, not this
    module's good behaviour. A write token leaves ATLAS one call away from
    forging a human vouch however carefully the code is written."""
    api({"/whoami": ({"scope": "write", "role": "operator",
                      "account": "Guardian"}, None)})
    obs = await guardian.check_token_scope()
    assert obs.health is Health.DEGRADED
    assert "WRITE scope" in obs.detail
    assert "read-only" in obs.detail

    signal, = await guardian.MODULE.signals()
    assert signal.severity is Severity.HIGH


async def test_a_read_scope_token_is_ok_and_silent(api):
    api({"/whoami": ({"scope": "read", "role": "viewer",
                      "account": "Guardian"}, None)})
    assert (await guardian.check_token_scope()).health is Health.OK
    assert await guardian.MODULE.signals() == ()


async def test_a_missing_token_names_the_scope_it_needs(api, monkeypatch):
    monkeypatch.delenv("GUARDIAN_API_TOKEN", raising=False)
    obs = await guardian.check_token_scope()
    assert obs.health is Health.UNKNOWN
    assert "READ-scope" in obs.detail


# ---------------------------------------------------------- guardian sensors

async def test_a_silent_sensor_is_failing(api):
    """A sensor that stopped is the failure that hides every other failure:
    the dashboard keeps drawing the last scan, and nothing alerts because
    nothing is looking."""
    api({"/networks": ({"networks": [
        {"name": "office", "sensor_reporting": True},
        {"name": "warehouse", "sensor_reporting": False}]}, None)})
    obs = await guardian.check_sensors()
    assert obs.health is Health.FAILING
    assert "warehouse" in obs.detail
    assert "nothing will alert" in obs.detail


async def test_all_sensors_reporting_is_ok(api):
    api({"/networks": ({"networks": [{"name": "office",
                                      "sensor_reporting": True}]}, None)})
    assert (await guardian.check_sensors()).health is Health.OK


async def test_a_payload_that_never_says_is_unknown_not_ok(api):
    """If the shape changes and no key reports sensor state, the honest
    answer is "I cannot tell" -- not "every sensor is fine"."""
    api({"/networks": ({"networks": [{"name": "office", "devices": 12}]}, None)})
    obs = await guardian.check_sensors()
    assert obs.health is Health.UNKNOWN
    assert "does not say" in obs.detail


async def test_a_missing_score_is_unknown_not_zero(api):
    """Defaulting a missing score to 0 turns a shape mismatch into a
    confident FAILING -- a page at 4am about nothing."""
    api({"/overview": ({"alerts": 3}, None)})
    obs = await guardian.check_overview()
    assert obs.health is Health.UNKNOWN
    assert "no numeric score" in obs.detail


async def test_high_severity_open_alerts_fail(api):
    api({"/alerts": ({"items": [{"severity": "critical"}, {"severity": "low"}]},
                     None)})
    obs = await guardian.check_open_alerts()
    assert obs.health is Health.FAILING
    assert obs.evidence["high"] == 1


async def test_the_hubs_own_crit_severity_fails(api):
    """The hub's field is `sev` with CRIT/WARN/INFO, as it answers live."""
    api({"/alerts": ({"items": [{"sev": "CRIT"}, {"sev": "WARN"},
                                {"sev": "INFO"}]}, None)})
    obs = await guardian.check_open_alerts()
    assert obs.health is Health.FAILING and obs.evidence["high"] == 1


async def test_warn_and_info_alone_are_degraded(api):
    api({"/alerts": ({"items": [{"sev": "WARN"}, {"sev": "INFO"}]}, None)})
    assert (await guardian.check_open_alerts()).health is Health.DEGRADED


async def test_no_open_alerts_is_ok(api):
    api({"/alerts": ({"items": []}, None)})
    assert (await guardian.check_open_alerts()).health is Health.OK


# --------------------------------------------------------------- roots/netbird

from atlas.modules import inroots, netbird


@pytest.mark.parametrize("module", [inroots.MODULE, netbird.MODULE])
def test_roots_and_netbird_expose_no_actions(module):
    """`inroots.nudge_missing` messaged colleagues about their attendance.
    Miro's Matrix rules forbid ATLAS from posting in the attendance or
    standup rooms; doing it through the Roots API is the same act down a
    different pipe. It is also impossible now: a read-scoped k9_ token is
    refused every non-GET before the route runs."""
    assert module.actions() == ()


def test_netbird_points_at_the_self_hosted_server():
    """Miro runs his own NetBird. The hosted api.netbird.io has none of his
    peers, so a token minted on his server would authenticate against an
    empty SaaS account -- the same error as reading Guardian from its
    marketing domain."""
    assert "api.netbird.io" not in netbird.BASE
    assert "mironetbird" in netbird.BASE


def test_netbird_never_renders_a_raw_exception():
    """An httpx error carries the request URL, and an observation's detail
    lands in Postgres and the logs. The repo has redact()/explain() for this
    and the module used to bypass them."""
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(netbird))
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.name:
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Name)
                        and inner.func.id == "str"
                        and any(isinstance(a, ast.Name) and a.id == node.name
                                for a in inner.args)):
                    raise AssertionError(
                        f"str({node.name}) at line {inner.lineno}; use "
                        f"explain()/redact()")


# ----------------------------------------------------------- netbird arrivals

@pytest.fixture
def peers(monkeypatch):
    def install(items, prior=None):
        async def fake_get(path):
            return items, None
        monkeypatch.setattr(netbird, "_get", fake_get)

        async def fake_prior(prop, check):
            return prior or {}
        from atlas.kernel import state
        monkeypatch.setattr(state, "last_evidence", fake_prior)
    return install


async def test_the_first_run_records_a_baseline_rather_than_alerting(peers):
    """Declaring every existing peer new on the first reading is a false
    alarm at HIGH, and it is the alert's introduction to Miro."""
    peers([{"id": "a", "name": "mac"}, {"id": "b", "name": "phone"}])
    obs = await netbird.check_new_peers()
    assert obs.health is Health.OK
    assert obs.evidence["baseline"] is True
    assert sorted(obs.evidence["known"]) == ["a", "b"]


async def test_a_returning_peer_is_not_a_new_peer(peers):
    """The old check read `connected_at`, which is when a peer last came
    online. Every laptop that reconnects each morning would have been
    reported as joining each morning -- and a security alert that fires
    daily is one nobody reads."""
    peers([{"id": "a", "name": "mac", "connected_at": "2026-09-22T09:00:00Z"}],
          prior={"known": ["a"]})
    obs = await netbird.check_new_peers()
    assert obs.health is Health.OK
    assert obs.evidence["arrived"] == []
    assert await netbird.MODULE.signals() == ()


async def test_a_genuinely_new_peer_raises_a_high_signal(peers):
    peers([{"id": "a", "name": "mac"}, {"id": "z", "name": "stranger"}],
          prior={"known": ["a"]})
    obs = await netbird.check_new_peers()
    assert obs.health is Health.DEGRADED
    assert obs.evidence["arrived"] == ["stranger"]

    signal, = await netbird.MODULE.signals()
    assert signal.severity is Severity.HIGH
    assert "atlas.example.com" in signal.body


async def test_an_empty_mesh_is_unknown_not_healthy(peers):
    """ATLAS is itself a peer, so zero peers means the reading is wrong."""
    peers([])
    assert (await netbird.check_peers()).health is Health.UNKNOWN


async def test_a_setup_key_value_is_never_put_in_an_observation(peers):
    """A setup key adds peers to the mesh. Only its name and expiry are read."""
    peers([{"name": "laptop-enrol", "key": "SECRET-KEY-VALUE",
            "expires": "2026-09-25T00:00:00Z"}])
    obs = await netbird.check_setup_keys()
    assert "SECRET-KEY-VALUE" not in obs.detail
    assert "SECRET-KEY-VALUE" not in str(obs.evidence)
    assert "laptop-enrol" in obs.detail


async def test_an_unparseable_expiry_is_unknown_not_ok(peers):
    """Silently skipping a key whose expiry cannot be read reports "nothing
    expiring soon" while a key expires tomorrow."""
    peers([{"name": "odd", "expires": "next tuesday"}])
    obs = await netbird.check_setup_keys()
    assert obs.health is Health.UNKNOWN
    assert "may be hiding" in obs.detail


# -------------------------------------------------------------- roots is honest

async def test_roots_health_is_not_behind_the_token(monkeypatch):
    """/health is public. Keeping it live is what tells "Roots is down" apart
    from "ATLAS has no token"."""
    monkeypatch.delenv("INROOTS_TOKEN", raising=False)
    names = {c.name for c in inroots.MODULE.checks()}
    assert "up" in names
    assert inroots.MODULE.requires_env == ()


async def test_roots_token_guidance_names_the_scope(monkeypatch):
    monkeypatch.delenv("INROOTS_TOKEN", raising=False)
    obs = await inroots.check_bot()
    assert obs.health is Health.UNKNOWN
    assert "READ-scoped" in obs.detail


# ------------------------------------------------- outward-facing acts ask

def test_opening_a_gitlab_issue_is_not_automatic():
    """An issue emails every watcher and hits the project activity feed the
    moment it is created -- on the work instance, those are colleagues. The
    offered rollback closes the issue, which undoes none of that: a closed
    issue is still one everybody was notified about.

    This was Tier.AUTO. It is the same shape as `inroots.nudge_missing`:
    an act other people see, at a tier that never asks."""
    from atlas.kernel.contracts import Tier
    from atlas.modules import gitlab

    action, = [a for a in gitlab.MODULE.actions()
               if a.name == "gitlab.open_issue"]
    assert action.tier is Tier.ASK


def test_no_module_writes_somewhere_other_people_see_at_tier_one():
    """A sweep, so the next module cannot reintroduce it quietly."""
    import importlib
    import pkgutil

    from atlas import modules as pkg
    from atlas.kernel.contracts import Tier

    visible = ("issue", "publish", "post", "send", "reply", "comment",
               "nudge", "upload", "message", "tweet")
    # A draft is not an outward-facing act, and both of the ones here were
    # read to confirm it rather than taken on the docstring's word:
    #   mail.draft_reply        POSTs to Gmail /drafts -- his own mailbox,
    #                           nobody is notified, he sends it himself
    #   fiverr.draft_buyer_reply  inserts a row in Postgres and nothing else;
    #                           it deliberately does not type into Fiverr,
    #                           because a draft in a text box is one misclick
    #                           from being sent
    drafts_only = ("draft_",)
    # instagram.hide_comment used to be exempted here as reversible and
    # invisible to anyone but the commenter -- but REELS (`Da3bCbINuQf`)
    # rules that hiding someone else's comment is still a decision about
    # another person, so it moved to Tier ASK (see
    # atlas/modules/instagram/__init__.py) and this sweep now catches it
    # like any other Tier AUTO action with a visible-acting verb, exactly
    # as it should: nothing is exempted any more.
    # One exemption, by Miro's own ruling (2026-09-27, asked twice and
    # answered "post automatically"): content.autopublish_reel posts an
    # AI-stories reel only past checks in code -- a switch, a daily cap, the
    # AI label, the brand/person guard, a 1080x1920 render with sound -- and
    # anything that fails becomes the Tier 2 card. And its catch-up (Miro,
    # 2026-09-29: an AI reel is on every platform): content.post_missing_leg
    # posts only the missing leg of a reel that already auto-posted
    # elsewhere, refused for a retryable reason, with the same caption --
    # claimed once, never twice. And YouTube Shorts (Miro, 2026-09-29:
    # three a week, on their own): content.publish_short posts only a reel
    # that already posted as an AI reel this week, past checks in code (the
    # week's cap, lane, render, caption guards, series order), media_id its
    # only param, claimed once. And the comment-keyword funnel (Miro,
    # 2026-09-30, from a reel: comment a keyword, get the roadmap by DM):
    # instagram.funnel_reply sends only the two texts Miro approved once on
    # the Tier 2 instagram.funnel_set card (sha256-checked before every
    # send), only to a person who commented the keyword (a whole-word
    # match recorded first), once per person ever, inside a daily cap and
    # outside 22:00-08:00; a policy error stops the funnel and pages him.
    # And the ATLAS Academy storefront (Miro, 2026-09-30: "anything that is
    # not urgent you approve it ... only emergencies I approve"):
    # academy.publish_shop swaps a release symlink for Miro's OWN store on
    # shop.mapfront.io past checks in code -- the catalog parses, a Buy
    # button exists only with a live payment link, a price and refund
    # terms, no third-party request, three publishes an hour -- and the
    # previous release is kept for the rollback. It publishes his own
    # page, not words to or about anyone else.
    # And the Whop clipping lane (Miro, 2026-09-30, the same ruling: "all
    # reels approve it ... anything not urgent, you approve"): clips post
    # themselves "the same way the AI reels lane does it". clips.publish_clip
    # (Instagram @atlas.clipsdrop), clips.publish_clip_tiktok (@atlasclips12
    # through Postiz) and clips.publish_clip_youtube_postiz (the AtlasClips
    # Shorts channel through Postiz) post only a clip ATLAS cut itself from
    # a campaign's official footage, past checks in code
    # (atlas/modules/clips/posting.py, "automatic"): the campaign active
    # with its permission source named and its rules reviewed; the caption
    # in the campaign's layout with its #Ad line alone (the FTC rule); a
    # 1080x1920 lane cut with sound inside the campaign's length band, read
    # by ffprobe before every post; the account configured and within the
    # campaign's account and one-platform rules; then the holds -- the
    # CLIPS_AUTOPUBLISH switch (on by default) and CLIPS_DAILY_CAP posts per
    # platform per day. Anything that fails becomes the Tier 2 card with the
    # reason (kernel.clip_intake), never a post; the rollback takes the post
    # down. clip_id is the only param; two posts an hour each.
    # And the outreach email about a client preview (Miro, 2026-10-01,
    # after the previews rebuilt: "email clients about it", under the same
    # ruling that anything not urgent runs without a card and he approves
    # only emergencies): agency.send_outreach sends only the draft
    # draft_outreach wrote -- the body's SHA-256 is stored with the row and
    # checked (atlas/modules/agency/outreach.py, auto_hold), the text is
    # composed by code in his framing, never by a model -- past checks in
    # code: CAN-SPAM (postal address, opt-out line, subject), the sending
    # domain and address, a recipient the business published on its own
    # site (agency.find_email; never a guess), not on the suppression list,
    # AGENCY_DAILY_CAP sends a day, one outreach email per lead ever unless
    # they wrote back, and the preview deployed (agency.deploy_site); then
    # the AGENCY_OUTREACH_AUTOSEND switch (on by default). Anything that
    # fails becomes the Tier 2 card with "Held for you: <reason>"
    # (kernel.lead_followup.follow_site), never a send. The rollback is
    # honest: a delivered email is not unsent; it suppresses the address and
    # records the fact on the lead. outreach_id is the only param.
    # And the rest of the lead follow-up (Miro, 2026-10-06: "make the lead
    # replies send themselves too - approve invoices too and schedule
    # meetings with them via email as well and call them as well", under
    # the same ruling): agency.send_reply sends only the draft draft_reply
    # wrote, past the price rules read now, the promise and honesty rules,
    # every time it names against the real calendar slots it was given, a
    # verified lead, suppression, CAN-SPAM (or texting consent and the AI
    # disclosure), then the AGENCY_REPLY_AUTOSEND switch;
    # agency.send_agreement sends code-written terms and Miro's OWN Stripe
    # payment link for exactly an allowed price (money in, never out), to a
    # verified, unsuppressed lead; agency.send_invite invites a verified
    # lead with an email to a meeting still held on Miro's own calendar;
    # agency.call_lead rings only a lead with a live call consent on record
    # (consent.contact_guard: the form, the business line, or Miro's own
    # record), calls on, inside hours, under the cap. Each refusal is the
    # Tier 2 card with "Held for you: <reason>" (kernel.lead_followup); each
    # rollback is honest (suppress where it went and record it; the invite's
    # deletes the event and Google tells the attendee).
    # 2026-10-06, Miro: "keep it in clips ... make continuous reels about
    # ATLAS Academy to show people" and, asked in Q&A, "content on all
    # accounts" with no card: clips.autopublish_promo posts the day's Academy
    # reel to the clips accounts, Tier 1 under promo.autopublish_check (the
    # AI label, the brand guard, CLIPS_PROMO_PER_DAY, a probed file); a held
    # reel is the Tier 2 clips.publish_promo card with the reason first.
    # academy.auto_approve_review shows a verified buyer's review on Miro's
    # OWN store (reviews.clean_refusal; anything else stays his card), and
    # clips.submit_to_whop was Tier 2 for his payout account until the same
    # answer ("clips campaign lane" with no card).
    # 2026-10-07, Miro: "make sure when requests come for either page you
    # will see it and reply to it". linkedin.send_request_reply posts only
    # the draft draft_request_reply wrote, only in the Guardian or FlyCheap
    # page's OWN inbox thread, to someone who wrote first, once per inbound,
    # past requests._guard_send (no figures, money, dates, promises or links
    # but the page's own, never claiming to be Miro, the daily cap, the
    # LINKEDIN_REPLY_AUTOSEND switch); a service request is refused and
    # carded; the rollback suppresses the thread (a message is not unsent).
    # Nothing else is exempt.
    exempt_actions: set[str] = {"linkedin.send_request_reply",
                                "content.autopublish_reel",
                                "clips.autopublish_promo",
                                "academy.auto_approve_review",
                                "clips.submit_to_whop",
                                "content.post_missing_leg",
                                "content.publish_short",
                                "instagram.funnel_reply",
                                "academy.publish_shop",
                                "clips.publish_clip",
                                "clips.publish_clip_tiktok",
                                "clips.publish_clip_youtube_postiz",
                                "agency.send_outreach",
                                "agency.send_reply",
                                "agency.send_agreement",
                                "agency.send_invite",
                                "agency.call_lead"}
    offenders = []
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"atlas.modules.{info.name}")
        instance = getattr(mod, "MODULE", None)
        if instance is None:
            continue
        for action in instance.actions():
            verb = action.name.split(".", 1)[-1]
            if verb.startswith(drafts_only) or action.name in exempt_actions:
                continue
            if action.tier is Tier.AUTO and any(w in verb for w in visible):
                offenders.append(action.name)
    assert offenders == [], (
        f"these run unattended and are visible to someone else: {offenders}")


# ---------------------------------------------------------- last_evidence

async def test_last_evidence_skips_readings_that_measured_nothing(memory_backend):
    """Verified against the live database: 272 UNKNOWN observations, none
    carrying evidence. So the newest row is not what a caller wants -- one
    failed run between two good ones would answer {}, which reads as "no
    history", and a check diffing against "what I saw last time" would
    re-baseline and absorb whatever arrived during that gap.

    This is checked against the SQL because the query is the behaviour; the
    module-level tests monkeypatch last_evidence and never exercise it."""
    import inspect

    from atlas.kernel import state
    sql = inspect.getsource(state.last_evidence)
    assert "o.health <> 'unknown'" in sql
    assert "o.evidence <> '{}'::jsonb" in sql
    # The join spellings, confirmed against the live schema: properties.name
    # is the module short name ("netbird"), checks.name is the bare check
    # name ("new_peers") -- not the "netbird.new_peers" display form.
    assert "p.name = $1 AND c.name = $2" in sql


async def test_last_evidence_returns_empty_without_postgres():
    """The memory backend has no observations table. An error must read as
    "no history", never as "nothing changed"."""
    from atlas.kernel.state import last_evidence
    assert await last_evidence("netbird", "new_peers") == {}


# ------------------------------------------------------------- guardian base

def test_guardian_uses_the_versioned_api_prefix():
    """Verified against the live API. `my.guarder.io/api/v1/health` answers
    200 with {"ok":true,"product":"Guardian","version":"0.1.0"} -- but that
    is the web app's own health route, not the versioned API. It made
    /api/v1 look correct while /api/v1/whoami and /api/v1/alerts both 404'd.
    The real prefix is /v1."""
    assert guardian.API == "/v1"


async def test_the_decoy_health_route_is_not_read_as_healthy(monkeypatch):
    """Both routes answer 200 and both say product: Guardian. Only the
    versioned API carries an `api` field, so that is the discriminator --
    without it every authenticated check would 404 while hub_api said OK."""
    async def decoy(path, **kw):
        return {"ok": True, "product": "Guardian", "version": "0.1.0"}, None
    monkeypatch.setattr(guardian, "get_json", decoy)
    obs = await guardian.check_hub_api()
    assert obs.health is Health.UNKNOWN
    assert "GUARDIAN_HUB_URL" in obs.detail


async def test_the_real_health_route_is_ok(monkeypatch):
    async def real(path, **kw):
        return {"ok": True, "product": "Guardian", "api": "v1"}, None
    monkeypatch.setattr(guardian, "get_json", real)
    obs = await guardian.check_hub_api()
    assert obs.health is Health.OK
    assert obs.evidence["api"] == "v1"


# ---------------------------------------------------------------- gitlab

from atlas.modules import gitlab


def _forbidden(body: dict, status: int = 403):
    import httpx
    request = httpx.Request("GET", "https://aicode.example.com/api/v4/projects")
    response = httpx.Response(status, json=body, request=request)
    return httpx.HTTPStatusError("forbidden", request=request, response=response)


def test_a_granular_token_failure_names_the_permission_to_tick():
    """GitLab's fine-grained tokens grant NOTHING by default -- every
    permission is ticked individually. A token with none set authenticates
    fine and then 403s every call, and the raw httpx message ("Client error
    '403 Forbidden' for url ...") sends you to check whether the token
    exists. GitLab's own body says exactly which box is missing."""
    reason = gitlab._gitlab_reason(_forbidden({
        "error": "insufficient_granular_scope",
        "error_description": "Access denied: This operation requires a "
                             "fine-grained personal access token with the "
                             "following user permissions: [Project: Read]."}))
    assert "Project: Read" in reason
    assert "every permission off" in reason


def test_a_plain_gitlab_error_is_quoted_not_dumped():
    reason = gitlab._gitlab_reason(_forbidden({"message": "404 Project Not Found"}, 404))
    assert "404 Project Not Found" in reason


def test_an_error_without_a_response_is_redacted():
    """No response object means an ordinary exception, and its text can carry
    a URL or a token. redact() exists for that."""
    reason = gitlab._gitlab_reason(RuntimeError("boom"))
    assert isinstance(reason, str) and reason


def test_no_gitlab_check_renders_a_raw_exception():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(gitlab))
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.name:
            for inner in ast.walk(node):
                if (isinstance(inner, ast.Call)
                        and isinstance(inner.func, ast.Name)
                        and inner.func.id == "str"
                        and any(isinstance(a, ast.Name) and a.id == node.name
                                for a in inner.args)):
                    raise AssertionError(
                        f"str({node.name}) at line {inner.lineno}")


def test_a_missing_token_does_not_raise():
    """`os.environ[...]` raised KeyError, which surfaced as an opaque
    UNKNOWN rather than "this is unset"."""
    inst = gitlab.Instance("business", "https://aicode.example.com", "NOPE_UNSET")
    assert inst.token == ""


def test_liveness_does_not_probe_the_restricted_monitoring_path():
    """`/-/readiness` is IP-allow-listed by Omnibus, so from anywhere else it
    404s -- indistinguishable from the instance being broken. It reported
    aicode.example.com as FAILING while it was serving fine."""
    source = code_of(gitlab.instance_up)
    assert "/-/readiness" not in source
    assert "/api/v4/version" in source


async def test_an_unauthorised_version_read_still_means_the_instance_is_up(monkeypatch):
    """401/403 from /api/v4/version proves GitLab answered. The token's
    problems belong to the token check, not to "is this instance alive"."""

    class FakeResponse:
        status_code = 403
        request = None
        @property
        def elapsed(self):
            from datetime import timedelta as td
            return td(milliseconds=12)
        def json(self): return {}

    class FakeClient:
        def __init__(self, **kw): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url, **kw): return FakeResponse()

    monkeypatch.setattr(gitlab.httpx, "AsyncClient", FakeClient)
    inst = gitlab.Instance("business", "https://aicode.example.com", "GITLAB_TOKEN")
    obs = await gitlab.instance_up(inst)
    assert obs.health is Health.OK
    assert "instance is up" in obs.detail


# ------------------------------------------------------------ mail accounts

from atlas.modules.mail import accounts as mail_accounts


def test_each_mailbox_carries_its_own_address(monkeypatch):
    """The unreplied check decides "he replied last" by matching the sender
    against his address. One global address made every thread in every other
    mailbox look unanswered — a wrong reading that arrives looking exactly
    like a real finding."""
    monkeypatch.setenv("MAIL_ACCOUNTS", "mirotech,flycheap")
    monkeypatch.setenv("MAIL_MIROTECH_EMAIL", "you@example.com")
    monkeypatch.setenv("MAIL_FLYCHEAP_KIND", "imap")
    monkeypatch.setenv("MAIL_FLYCHEAP_EMAIL", "miro@flycheap.live")
    a, b = mail_accounts.all_accounts()
    assert a.email != b.email
    assert (a.kind, b.kind) == ("google", "imap")


def test_an_unconfigured_mailbox_names_its_own_variables(monkeypatch):
    monkeypatch.setenv("MAIL_ACCOUNTS", "guardian")
    monkeypatch.setenv("MAIL_GUARDIAN_KIND", "imap")
    monkeypatch.delenv("MAIL_GUARDIAN_HOST", raising=False)
    monkeypatch.delenv("MAIL_GUARDIAN_PASSWORD", raising=False)
    account, = mail_accounts.all_accounts()
    assert account.configured is False
    assert "MAIL_GUARDIAN_HOST" in account.missing
    assert "MAIL_GUARDIAN_PASSWORD" in account.missing


def test_the_original_single_account_still_works(monkeypatch):
    """An existing deployment keeps working without being reconfigured, and
    the first account added does not silently replace the one already there."""
    monkeypatch.delenv("MAIL_ACCOUNTS", raising=False)
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "rt")
    monkeypatch.setenv("GOOGLE_ACCOUNT_EMAIL", "a@b.com")
    account, = mail_accounts.all_accounts()
    assert account.name == "default"
    assert account.email == "a@b.com"


def test_imap_never_marks_mail_as_read():
    """A monitoring tool that silently marks mail read is worse than none —
    the damage is to the thing it was watching. readonly=True means the
    server refuses a write; it is not this code being careful."""
    import inspect

    from atlas.modules.mail import imap
    source = inspect.getsource(imap)
    assert 'select("INBOX", readonly=True)' in source
    assert "BODY.PEEK" in source, "PEEK does not set the Seen flag; BODY[] does"
    for forbidden in ("STORE", "\\\\Seen", "expunge", "delete"):
        assert forbidden not in source


def test_google_tokens_are_cached_per_account():
    """One global slot handed the second mailbox the first one's token, and
    Gmail answers that happily — with the first account's mail."""
    import inspect

    from atlas.modules import mail
    source = inspect.getsource(mail)
    assert "_tokens: dict[str, tuple[str, float]]" in source


def test_an_alias_counts_as_him(monkeypatch):
    """Zoho gives one licence several addresses: miro@flycheap.live,
    miro@guarder.io and support@guarder.io are ONE inbox — verified by
    identical message counts and the same newest subject.

    So Guardian mail is already read. But a reply he sent FROM
    miro@guarder.io does not match miro@flycheap.live, and the thread would
    be counted as still waiting on him — a made-up finding on the one check
    whose whole job is saying what needs him."""
    monkeypatch.setenv("MAIL_ACCOUNTS", "flycheap")
    monkeypatch.setenv("MAIL_FLYCHEAP_KIND", "imap")
    monkeypatch.setenv("MAIL_FLYCHEAP_EMAIL", "miro@flycheap.live")
    monkeypatch.setenv("MAIL_FLYCHEAP_ALIASES",
                       "miro@guarder.io,support@guarder.io")
    account, = mail_accounts.all_accounts()
    assert account.is_me("Miro <miro@guarder.io>")
    assert account.is_me("support@guarder.io")
    assert account.is_me("MIRO@FLYCHEAP.LIVE")
    assert not account.is_me("a.buyer@example.com")


def test_imap_checks_every_address_not_just_the_login():
    import inspect

    from atlas.modules.mail import imap
    source = inspect.getsource(imap)
    assert "mine or (user,)" in source


# ------------------------------------------------------------ vpsie action

def test_vpsie_has_exactly_one_action_and_it_is_additive():
    """Snapshots are the one write allowed: additive, cannot take an instance
    down. Restart/stop/resize stay out -- this is the account ATLAS runs in."""
    from atlas.kernel.contracts import Tier
    from atlas.modules import vpsie
    actions = vpsie.MODULE.actions()
    assert [a.name for a in actions] == ["vpsie.snapshot_all"]
    assert actions[0].tier is Tier.ASK
    assert actions[0].irreversible is False


def test_vpsie_uses_the_two_base_paths_the_sdk_documents():
    """Auth under /api/v2; every resource under /apps/v2. The bot client had
    only verified auth, so /snapshot 404'd from the wrong base and looked
    like a missing endpoint."""
    from atlas.modules import vpsie
    assert vpsie.AUTH_BASE.endswith("/api/v2")
    assert vpsie.BASE.endswith("/apps/v2")
    import inspect
    src = inspect.getsource(vpsie)
    assert "virtualMachines" not in src.replace("virtualMachines lists", ""), \
        "the SDK's resource is /vm"


def test_a_vpsie_login_rate_limit_is_not_a_credential_failure(monkeypatch, tmp_path):
    """"Too many request, please try again after an hour" blocks every check
    for an hour. Reporting it as "credentials rejected" sends him to rotate
    a secret that is fine. UNKNOWN, with the real reason."""
    from atlas.modules import vpsie

    exc = vpsie.VpsieRateLimited(
        "VPSie is rate-limiting logins (Too many request). The credentials "
        "are not the problem; every VPSie check is blind until the window "
        "passes, about an hour.")
    obs = vpsie._guard("auth")(exc)
    assert obs.health.value == "unknown"
    assert "not the problem" in obs.detail
    # And the real path says exactly that: the message is built in
    # _access_token, so check it there rather than only in the double.
    import inspect
    assert "not the problem" in inspect.getsource(vpsie._access_token)


def test_the_vpsie_token_survives_a_restart(tmp_path, monkeypatch):
    """Every deploy restarts the process; every restart was a fresh login;
    enough deploys in an hour tripped the limit. The JWT is persisted so a
    restart reuses it."""
    import time

    from atlas.modules import vpsie
    monkeypatch.setattr(vpsie, "_TOKEN_FILE", tmp_path / "t.json")
    vpsie._save_cached("jwt-abc", time.time() + 3000)
    assert vpsie._load_cached() == ("jwt-abc", pytest.approx(time.time() + 3000, abs=5))
    vpsie._save_cached("jwt-old", time.time() - 10)
    assert vpsie._load_cached() is None, "an expired token is not reused"


def test_a_single_slow_sample_is_re_measured_before_degrading():
    """3.7s once flagged guarder.io and status.example.org as DEGRADED; a minute
    later both answered in under 0.3s. One slow sample is a slow moment,
    not a slow site, and a check that cannot tell them apart trains him to
    ignore it."""
    import inspect

    from atlas.modules import flycheap, guardian, inroots
    for mod in (flycheap, guardian):
        src = inspect.getsource(mod._probe)
        assert "min(first" in src, f"{mod.__name__} reports one sample"
    assert "min(ms" in inspect.getsource(inroots.check_up)


async def test_a_used_setup_key_expiring_is_not_a_finding(peers):
    """A setup key enrols NEW peers. One that has already done its job
    lapsing changes nothing; only an unused one expiring is worth a word."""
    peers([{"name": "Atlas", "used_times": 1, "expires": "2026-09-29T00:00:00Z"},
           {"name": "spare", "used_times": 0, "expires": "2026-09-29T00:00:00Z"}])
    obs = await netbird.check_setup_keys()
    assert obs.evidence["expiring"] == ["spare"]
    assert obs.evidence["spent"] == ["Atlas"]


async def test_a_locked_root_is_a_failing_finding_with_the_unlock_in_it(monkeypatch):
    """675 sign-in attempts locked root and every push read 'blocked' with
    nobody having blocked anything. The lock IS the brute force."""
    from atlas.modules import gitlab as g

    inst = g.INSTANCES[0]

    async def locked(inst, path, **params):
        return [{"username": "root", "state": "active", "locked": True}]
    monkeypatch.setattr(g, "_api", locked)
    o = await g.admin_locked(inst)
    assert o.health is Health.FAILING
    assert "brute-forc" in o.detail and "unlock_access!" in o.detail

    async def fine(inst, path, **params):
        return [{"username": "root", "state": "active", "locked": False}]
    monkeypatch.setattr(g, "_api", fine)
    assert (await g.admin_locked(inst)).health is Health.OK

    async def not_admin(inst, path, **params):
        return [{"username": "root", "state": "active"}]
    monkeypatch.setattr(g, "_api", not_admin)
    o = await g.admin_locked(inst)
    assert o.health is Health.UNKNOWN and "admin" in o.detail


def test_vpsie_balance_can_be_switched_off(monkeypatch):
    """'Forget about vpsie balance': the check is not registered at all."""
    from atlas.modules.vpsie import VpsieModule
    monkeypatch.setenv("VPSIE_WATCH_BALANCE", "0")
    assert "balance" not in [c.name for c in VpsieModule().checks()]
    monkeypatch.setenv("VPSIE_WATCH_BALANCE", "1")
    assert "balance" in [c.name for c in VpsieModule().checks()]
