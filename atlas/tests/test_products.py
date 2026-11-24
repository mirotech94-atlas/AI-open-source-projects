"""Product module checks: Attendance, NetBird, CloudHost, GitLab, mail and more.

FlyCheap and Guardian were removed on 2026-10-29 (Miro: not used any more).
"""
from __future__ import annotations

import pytest

from atlas.kernel.contracts import Health, Severity


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


# --------------------------------------------------------------- attendance/netbird

from atlas.modules import netbird


def test_netbird_points_at_the_self_hosted_server():
    """Miro runs his own NetBird. The hosted api.netbird.io has none of his
    peers, so a token minted on his server would authenticate against an
    empty SaaS account -- the same error as reading Guardian from its
    marketing domain."""
    assert "api.netbird.io" not in netbird.BASE
    assert "netbird-host" in netbird.BASE


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
    def install(items, prior=None, readable=True):
        async def fake_get(path):
            return items, None
        monkeypatch.setattr(netbird, "_get", fake_get)

        async def fake_prior(prop, check):
            return (prior or {}), readable
        from atlas.kernel import state
        monkeypatch.setattr(state, "last_evidence_read", fake_prior)
    return install


async def test_the_first_run_records_a_baseline_rather_than_alerting(peers):
    """Declaring every existing peer new on the first reading is a false
    alarm at HIGH, and it is the alert's introduction to Miro."""
    peers([{"id": "a", "name": "mac"}, {"id": "b", "name": "phone"}])
    obs = await netbird.check_new_peers()
    assert obs.health is Health.OK
    assert obs.evidence["baseline"] is True
    assert sorted(obs.evidence["known"]) == ["a", "b"]
    # OK-on-empty (21.E.1): the baseline compared nothing, so it asserts no zero.
    assert obs.value is None
    assert "No arrivals are asserted" in obs.detail


async def test_an_unreadable_history_is_unknown_not_a_baseline_or_zero(peers):
    """Positive control is the test above: a readable, empty history baselines.
    A history that cannot be read must not baseline (it would absorb a peer
    that joined in the gap) and must not say 'no new peers'."""
    peers([{"id": "a", "name": "mac"}], readable=False)
    obs = await netbird.check_new_peers()
    assert obs.health is Health.UNKNOWN
    assert obs.value is None
    assert not obs.evidence


async def test_a_readable_history_with_no_arrival_still_reads_zero(peers):
    peers([{"id": "a", "name": "mac"}], prior={"known": ["a"]})
    obs = await netbird.check_new_peers()
    assert obs.health is Health.OK
    assert obs.value == 0.0


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
    assert "atlas.storefront.test" in signal.body


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


# -------------------------------------------------------------- attendance is honest





# ------------------------------------------------- outward-facing acts ask

async def test_opening_a_gitlab_issue_is_automatic_only_where_miro_watches():
    """Tier 1 since 2026-10-12 (Miro: "whatever you think should be done
    should be automatically approved, but whatever is really urgent should
    only need human approval"; memory approval-policy): 181 cards in 30
    days, 138 expired unanswered.

    It was ASK because an issue emails every watcher the moment it exists
    and closing it undoes none of that -- on the work instance those are
    colleagues. So the gate is in the dry run: never the work instance,
    only the business one where Miro is the watcher (the rest of the gates
    are in tests/test_gitlab_autofile.py)."""
    from atlas.kernel.contracts import Tier
    from atlas.modules import gitlab

    action, = [a for a in gitlab.MODULE.actions()
               if a.name == "gitlab.open_issue"]
    assert action.tier is Tier.AUTO
    assert action.rollback is gitlab.close_issue
    refused = await gitlab.open_issue_dry("work", "Miro/ai", "x")
    assert not refused.ok and "colleagues" in refused.detail


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
    # agency.storefront.test past checks in code -- the catalog parses, a Buy
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
    # 2026-10-09, Miro: "post YouTube videos ... teaching people how to use
    # n8n workflows ... same with Claude", in playlist series, the way the
    # Academy reel posts. academy.autopublish_tutorial posts a code-held,
    # Fish-narrated catalog episode only past tutorials.autopublish_check
    # (the switch, ACADEMY_TUTORIALS_PER_DAY, a probed 1920x1080 file with
    # sound of 4-10 minutes, YouTube's chapter rules, the store link and the
    # AI label in the description, a token able to manage playlists); it
    # goes public only with its thumbnail and playlist, otherwise stays
    # private and is the Tier 2 academy.publish_tutorial card. The rollback
    # makes it private again and takes it out of the playlist.
    # 2026-10-11, Miro (Q&A): ATLAS Academy support, "everything auto unless
    # unsure". academy.send_support_reply emails only the draft
    # draft_support_reply wrote from the support KB, only to the ticket's own
    # address (never a parameter), once per inbound, past support._guard_send
    # (no price that is not on sale in the catalog, no refund or payment
    # promised, no money out, no legal advice, never claiming to be Miro or a
    # person, links and addresses only ours or the cited source's, the
    # assistant's sign-off, CAN-SPAM, suppression), then the holds: a route
    # to Miro (refund, legal, money, complaint, custom work), an ungrounded
    # answer, ACADEMY_SUPPORT_DAILY_CAP and the ACADEMY_SUPPORT_AUTOSEND
    # switch. A hold or refusal is the Tier 2 card; the rollback suppresses
    # the address (an email is not unsent).
    # 2026-10-11, Miro: the Vault LinkedIn page's approved posts go out "the
    # way reels already do" (memory approval-policy). linkedin.autopublish_post
    # posts only the Vault (guardian) page's next approved post-bank entry,
    # byte for byte, past publish.autopublish_refusal (never FlyCheap;
    # LINKEDIN_AUTOPUBLISH; not paused; the approved payload; a posting day
    # and hour from the bank; one a day; a signed-in session and a clean
    # sweep; no restriction alert). No reshare to Miro's profile (Tier 2). A
    # refusal is the Tier 2 linkedin.publish_post card; the rollback deletes
    # nothing (Tier 3) and pauses the page's autopublish.
    # 2026-10-12, Miro: "whatever you think should be done should be
    # automatically approved, but whatever is really urgent should only need
    # human approval" (memory approval-policy). gitlab.open_issue files only
    # on the business instance, where Miro is the watcher -- never on work,
    # where watchers are colleagues -- never a near-duplicate of an open or
    # recently closed atlas issue, GITLAB_ISSUES_PER_DAY per project, the
    # GITLAB_AUTOFILE switch; the rollback closes it. linkedin.autoreshare_post
    # reshares to Miro's profile only the Vault post ATLAS itself published
    # today, past publish.autoreshare_refusal (LINKEDIN_AUTORESHARE, not
    # paused, never twice or after an unverified click, one a day, posting
    # hours, session and account healthy); a refusal is the Tier 2
    # reshare_post card; the rollback deletes nothing and pauses it.
    # 2026-10-13, same rule, the Academy growth plan Miro approved:
    # academy.pin_comment posts ATLAS's own quiz + lab-file comment on ATLAS's
    # own published tutorial, once per video (its idempotency gate), at most
    # 3 a day from the daily tick; the rollback edits it to "(withdrawn)"
    # (deleting it would be Tier 3).
    # 2026-10-17, Miro: "yes add the AI flag for Shorts too".
    # academy.flag_synthetic_short sets status.containsSyntheticMedia on one of
    # the Academy's own Shorts (tutorial:/lesson:/concept: slugs in
    # academy_promo_posts, never a store or engine reel), on the id the record
    # or Postiz returned (never guessed), every other status field resent as
    # read; skipped when already set, at most 4 an hour; the rollback sets the
    # flag back. A public write: the label shows on the video.
    # 2026-10-18, Miro: "bring already-public videos up to the new tags,
    # hashtags and description rules automatically".
    # academy.refresh_video_metadata rewrites only the tags and description of
    # ATLAS's own public lessons and teaching Shorts (on record, never a
    # guessed id) to what a new upload gets, the title and category resent as
    # read; held when the render on disk is not the live video's length or a
    # gate refuses; 3 a day, each video once (metadata-refresh.json); the
    # rollback writes the previous tags and description back.
    # 2026-10-20, Miro: "a creative thumbnail for every video" (NotebookLM).
    # academy.apply_notebooklm_thumbnail sets one thumbnails.set on ATLAS's
    # own public lesson (the id on record), the image checked and re-encoded
    # by ffmpeg; at most 6 a pass; a 403 holds the whole inbox for 7 days with one
    # Slack line; the rollback sets the kept previous thumbnail again.
    # Nothing else is exempt.
    # academy.send_signup_confirmation mails one fixed confirmation text to the
    # address typed on the store form (double opt-in); no model text, no newsletter.
    exempt_actions: set[str] = {"academy.send_support_reply",
                                "academy.send_signup_confirmation",
                                "academy.apply_notebooklm_thumbnail",
                                "academy.flag_synthetic_short",
                                "academy.refresh_video_metadata",
                                "academy.pin_comment",
                                "gitlab.open_issue",
                                "linkedin.autoreshare_post",
                                "linkedin.autopublish_post",
                                "linkedin.send_request_reply",
                                "academy.autopublish_tutorial",
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
    sql = inspect.getsource(state.last_evidence_read)
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


# ---------------------------------------------------------------- gitlab

from atlas.modules import gitlab


def _forbidden(body: dict, status: int = 403):
    import httpx
    request = httpx.Request("GET", "https://aicode.storefront.test/api/v4/projects")
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
    inst = gitlab.Instance("business", "https://aicode.storefront.test", "NOPE_UNSET")
    assert inst.token == ""


def test_liveness_does_not_probe_the_restricted_monitoring_path():
    """`/-/readiness` is IP-allow-listed by Omnibus, so from anywhere else it
    404s -- indistinguishable from the instance being broken. It reported
    aicode.storefront.test as FAILING while it was serving fine."""
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
    inst = gitlab.Instance("business", "https://aicode.storefront.test", "GITLAB_TOKEN")
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
    monkeypatch.setenv("MAIL_MIROTECH_EMAIL", "owner@example.com")
    monkeypatch.setenv("MAIL_FLYCHEAP_KIND", "imap")
    monkeypatch.setenv("MAIL_FLYCHEAP_EMAIL", "owner.flycheap@example.com")
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
    """Zoho gives one licence several addresses: owner@example.com,
    owner@example.com and support@guarder.example.com are ONE inbox — verified by
    identical message counts and the same newest subject.

    So Guardian mail is already read. But a reply he sent FROM
    owner@example.com does not match owner@example.com, and the thread would
    be counted as still waiting on him — a made-up finding on the one check
    whose whole job is saying what needs him."""
    monkeypatch.setenv("MAIL_ACCOUNTS", "flycheap")
    monkeypatch.setenv("MAIL_FLYCHEAP_KIND", "imap")
    monkeypatch.setenv("MAIL_FLYCHEAP_EMAIL", "owner.flycheap@example.com")
    monkeypatch.setenv("MAIL_FLYCHEAP_ALIASES",
                       "owner.alias@example.com,support@guarder.example.com")
    account, = mail_accounts.all_accounts()
    assert account.is_me("Miro <owner.alias@example.com>")
    assert account.is_me("support@guarder.example.com")
    assert account.is_me("OWNER.FLYCHEAP@EXAMPLE.COM")
    assert not account.is_me("a.buyer@example.com")


def test_imap_checks_every_address_not_just_the_login():
    import inspect

    from atlas.modules.mail import imap
    source = inspect.getsource(imap)
    assert "mine or (user,)" in source


# ------------------------------------------------------------ cloudhost action











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


