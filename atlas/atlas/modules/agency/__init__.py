"""agency — find local businesses with a bad website, and make the case in
one sentence a shop owner can check for themselves (AGENCY.md).

Watches: Google Places API spend against a hard monthly cap
(`AGENCY_PLACES_MONTHLY_CAP`, default 500), how many leads are sitting with
no decision made on them yet, and — once a site of ours is live — that
site's own reachability and TLS certificate.

May do:

* Tier AUTO, because none of it leaves the box: search Places for prospects
  and store them as leads (`agency.prospect`), fetch a lead's own public
  website once and check it against the eight qualifying findings
  (`agency.audit`), recompute every open lead's heuristic score
  (`agency.rank`), assemble a GetLayers mockup (`agency.mockup`), draft
  outreach text (`agency.draft_outreach`), record a suppression
  (`agency.suppress`), and turn a mockup into a static site on disk
  (`agency.build_site`) carrying the 20 launch items, checked by
  `launch_audit` (read-only; its report sits beside the site as
  `<slug>.LAUNCH_CHECK.md`, and any ISSUE stops `agency.deploy_site`).
  Draft a Webild prompt from the lead's own data (`agency.webild_brief`),
  and record the site the Mac agent published from it
  (`agency.record_webild_site`, reached only through the unattended door
  from `/webild/{lead_id}/submitted`), which runs the same 20-item check
  against the live URL (`webild.py`, `livecheck.py`).
* Tier ASK, `card_only`: hand a Webild brief to the agent on Miro's Mac
  (`agency.build_with_webild`), raised by `signals()` with the brief as
  the card's text -- the brief is the capability, so only the one ATLAS
  wrote and Miro read can be queued.
* Tier AUTO since 2026-10-01, by Miro's ruling that anything not urgent
  runs without a card: send the outreach email `draft_outreach` wrote about
  a lead's live preview (`agency.send_outreach`), gated in code
  (`outreach.auto_hold`: the draft's SHA-256, CAN-SPAM, a published address,
  suppression, the daily cap, one email per lead unless they replied, the
  preview deployed); a hold is the Tier 2 card with the reason. Its rollback
  cannot unsend a delivered email and says so: it suppresses the address and
  records the fact on the lead. Deploying a built site to its production
  host (`agency.deploy_site`) is Tier AUTO on the same ruling.
* Never: buying a domain or paying for anything. That is money out, Tier
  NEVER, and nothing in this module attempts it (AGENCY.md §3).

Lead follow-up (2026-09-29): a reply by email, text or call is taken in,
classified and alerted (`leads.py`); replies are drafted with the sales
playbook under code-enforced price rules (`replies.py`, `pricing.py`,
`playbook.py`); a lead is texted or called ONLY with their prior express
consent on record, after the AI disclosure, 08:00-21:00 their time, under a
contact cap (`consent.py`, `sms.py`, `calls.py`, `api/lead_call.py`); Miro
is phoned only for an emergency (`escalate.py`).

* Tier AUTO: `agency.draft_reply` (a draft row), `agency.call_brief` (a
  file for Miro to dial from), `agency.record_deal` (principal_only: Miro's
  own word that a deal is won, paid or lost), `agency.record_consent`
  (principal_only: a lead told him on his own call that the assistant may
  call them back -- the consent `agency.call_lead` needs, 2026-10-01).
* Tier AUTO since 2026-10-06 (Miro: "make the lead replies send themselves
  too -- approve invoices too and schedule meetings with them via email as
  well and call them as well", under his rule that anything not urgent runs
  without a card), each with every rule in code and a refusal becoming the
  Tier 2 card with the reason (`kernel.lead_followup`): `agency.send_reply`
  (email, or text with consent; prices, promises, honesty, offered times,
  CAN-SPAM, suppression, the `AGENCY_REPLY_AUTOSEND` hold),
  `agency.send_agreement` (the agreement and Miro's own Stripe payment link
  for an allowed price -- money IN, not out), `agency.send_invite` (the
  invitation to a held slot) and `agency.call_lead` (an AI call, only with
  the lead's prior consent: `consent.contact_guard` is the gate, and nothing
  cold-calls). None of these can be undone once delivered, so their
  rollbacks are honest: suppress where it went and record the fact
  (invariant 8 asks for a rollback, not a pretence).
* Tier ASK, irreversible: `agency.text_lead` (a code-composed text, with
  consent; texting leads is off).

Assumes the Places API "New" text-search endpoint
(`places.googleapis.com/v1/places:searchText`), PageSpeed Insights v5 for
the `slow` audit check when `GOOGLE_PAGESPEED_API_KEY` is set, the
GetLayers MCP server at `GETLAYERS_MCP_URL` (default
`https://mcp.getlayers.ai/mcp`), SMTP via `AGENCY_SMTP_HOST/PORT/USER/
PASSWORD`, and `rsync` over `ssh` to `AGENCY_DEPLOY_HOST:AGENCY_DEPLOY_ROOT`
— correct me if any of these shapes change.

Guardian registration (AGENCY.md §2.6, "the site goes into Guardian"):
`guardian/__init__.py` holds a READ-scope token by design and has no write
path at all — see that module's docstring — so there is no API call this
module could make to register anything with it. "New client, new monitored
endpoint" is implemented here instead: `setup()` reads every row out of
`agency_sites` (or `AGENCY_SITES`, a `slug:domain` comma list, when there is
no database — the same fallback tests use) and `checks()` yields one
`Check` per site, watching HTTPS reachability and TLS certificate days
remaining. ATLAS watches its own client sites directly; Guardian keeps
watching Miro's network the way it already does.

`agency_sites.domain` has no writer yet — `build_site` doesn't know a
client's eventual domain, so a site's check reports `Health.UNKNOWN` until
Miro sets `domain` on the row by hand once the client picks one.

For whoever builds outreach on top of this (Task 3 did): an `Audit`'s
findings can carry text read off the audited business's own site (see
`audit.py`'s docstring) and must be `wrap_untrusted()`-ed before reaching
any prompt, and so must anything a GetLayers MCP tool call returns (see
`mockup.py`'s docstring) — both are ingested bytes, invariant 4.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import ssl
from datetime import UTC, datetime, timedelta

import httpx

from ...kernel.contracts import (
    Action,
    Check,
    Health,
    InboxItem,
    Observation,
    Severity,
    Tier,
)
from .. import _getlayers_oauth
from .._http import explain
from .._mcp import McpClient
from . import (
    _store,
    calls,
    consent,
    contacts,
    escalate,
    leads,
    meetings,
    replies,
    sitechat,
    sms,
    verify,
)
from .audit import (
    Audit,
    Finding,
    audit_action,
    audit_dry,
    audit_lead,
    audit_rollback,
    audit_site,
)
from .build import build_site, build_site_dry, build_site_rollback
from .deploy import deploy_dry, deploy_rollback, deploy_site, published_domain
from .launchcheck import LaunchReport, launch_audit
from .mockup import make_mockup, make_mockup_dry, make_mockup_rollback
from .outreach import (
    can_spam_ok,
    daily_cap,
    draft_outreach,
    draft_outreach_dry,
    draft_outreach_rollback,
    send_dry,
    send_outreach,
    send_outreach_rollback,
    sends_today,
    suppress,
    suppress_dry,
    suppress_rollback,
)
from .prospect import (
    Place,
    cap,
    charge,
    check_prospect_budget,
    prospect_action,
    prospect_dry,
    prospect_rollback,
    search_places,
    spend_this_month,
)
from .score import features, heuristic_score, rank_action, rank_dry, rank_rollback
from .sitechat import check_site_chat
from .webild import (
    build_with_webild,
    build_with_webild_dry,
    build_with_webild_rollback,
    record_webild_site,
    record_webild_site_dry,
    record_webild_site_rollback,
    webild_brief,
    webild_brief_dry,
    webild_brief_rollback,
)
from .webild import signals as webild_signals

log = logging.getLogger("atlas.agency")

__all__ = [
    "MODULE",
    "Audit",
    "Finding",
    "LaunchReport",
    "Place",
    "audit_lead",
    "audit_site",
    "can_spam_ok",
    "cap",
    "charge",
    "check_prospect_budget",
    "daily_cap",
    "features",
    "heuristic_score",
    "launch_audit",
    "search_places",
    "sends_today",
    "spend_this_month",
]


async def check_leads_waiting() -> Observation:
    try:
        n = await _store.count_leads_waiting()
    except Exception as exc:                            # noqa: BLE001
        return Observation("leads_waiting", Health.UNKNOWN, detail=str(exc))
    return Observation(
        "leads_waiting", Health.OK, float(n), "leads",
        f"{n} lead(s) waiting on an audit or a decision")


async def check_getlayers() -> Observation:
    """Can the server actually authenticate to GetLayers' MCP server?

    OK only after a real authenticated `initialize` handshake succeeded --
    configuration alone proves nothing (the static `GETLAYERS_TOKEN` this
    module shipped with was configured and could never work: GetLayers has
    no API keys). Nothing configured is UNKNOWN, naming the fix. GetLayers
    refusing the refresh or access token is FAILING: it will not heal on
    its own, Miro has to reconnect. Anything else (unreachable, a protocol
    error) is UNKNOWN -- the question could not be asked.

    A handshake is a read. In OAuth mode it may also trade the refresh
    token for an access token (and GetLayers may rotate it): that is
    authentication, the same thing every mockup does first, and a daily
    exercise keeps an idle grant from lapsing unnoticed."""
    name = "getlayers_auth"
    if not _getlayers_oauth.configured():
        return Observation(
            name, Health.UNKNOWN,
            detail="GetLayers is not connected: no GETLAYERS_OAUTH_CLIENT_ID / "
                   "GETLAYERS_OAUTH_REFRESH_TOKEN (GetLayers has no API keys) — "
                   "run bootstrap/getlayers-connect.py on the Mac",
            evidence={"mode": "none"})
    client = McpClient()
    mode = "oauth" if client.uses_oauth else "static"
    _init, reason = await client.initialize()
    if reason is None:
        return Observation(
            name, Health.OK, detail=f"GetLayers MCP accepted ATLAS ({mode})",
            evidence={"mode": mode})
    if reason.startswith("GetLayers refused") or "not configured" in reason:
        if mode == "static":
            reason += (" — GetLayers has no API keys; connect with OAuth: "
                       "bootstrap/getlayers-connect.py")
        return Observation(name, Health.FAILING, detail=reason,
                           evidence={"mode": mode})
    return Observation(name, Health.UNKNOWN,
                       detail=f"could not reach GetLayers: {reason}",
                       evidence={"mode": mode})


def _tls_days_left(domain: str) -> tuple[int | None, str | None]:
    """Blocking (socket + OpenSSL); always run through `asyncio.to_thread`.
    Same parse `atlas/tools/builtin.py::cert_check` uses — OpenSSL reports
    `notAfter` in GMT, so it is read as UTC and compared against an aware
    `now()`, never two naive datetimes that happen to line up. Copied
    rather than imported: a module reaching into `atlas.tools` would invert
    the layering (tools depend on modules, not the other way round)."""
    try:
        ctx = ssl.create_default_context()
        with (socket.create_connection((domain, 443), timeout=10) as sock,
              ctx.wrap_socket(sock, server_hostname=domain) as tls):
            cert = tls.getpeercert()
        expires = datetime.strptime(
            cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
        return (expires - datetime.now(UTC)).days, None
    except Exception as exc:                            # noqa: BLE001
        return None, explain(exc)


def _make_site_check(slug: str, domain: str | None) -> Check:
    """One `Check` per deployed client site. Built by a factory, not a bare
    closure inside a loop, so each check captures ITS OWN `slug`/`domain`
    rather than every check reading back whatever the loop variable held
    last."""

    async def probe() -> Observation:
        name = f"site_{slug}"
        if not domain:
            return Observation(name, Health.UNKNOWN,
                               detail=f"no domain on record for site {slug!r}")
        url = f"https://{domain}"
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                response = await client.head(url)
        except Exception as exc:                        # noqa: BLE001
            return Observation(name, Health.UNKNOWN, detail=explain(exc))

        days_left, reason = await asyncio.to_thread(_tls_days_left, domain)
        if reason is not None:
            return Observation(name, Health.UNKNOWN, detail=reason)

        if response.status_code >= 400:
            return Observation(
                name, Health.FAILING, float(days_left), "days",
                f"{url} returned HTTP {response.status_code}")
        if days_left < 21:
            return Observation(
                name, Health.DEGRADED, float(days_left), "days",
                f"{url} is up, but its TLS cert expires in {days_left} day(s)")
        return Observation(
            name, Health.OK, float(days_left), "days",
            f"{url} is up, TLS cert has {days_left} day(s) left")

    return Check(f"site_{slug}", probe, timedelta(minutes=30), Severity.HIGH,
                "client site up + cert", baseline_sigma=None)


def _parse_sites_env(raw: str) -> list[tuple[str, str | None]]:
    """`slug:domain` comma list — the sync fallback `checks()` needs when
    there is no database to read `agency_sites` from (tests, `selftest`)."""
    sites: list[tuple[str, str | None]] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        slug, _, domain = item.partition(":")
        sites.append((slug.strip(), domain.strip() or None))
    return sites


class AgencyModule:
    name = "agency"
    title = "Agency"
    requires_env = ()
    optional_env = ("GOOGLE_PLACES_API_KEY", "GOOGLE_PAGESPEED_API_KEY")
    setup_note = ("needs GOOGLE_PLACES_API_KEY to prospect; "
                  "GOOGLE_PAGESPEED_API_KEY unlocks a real Lighthouse score "
                  "for the 'slow' audit check, otherwise a size/time "
                  "heuristic is used; GetLayers OAuth "
                  "(bootstrap/getlayers-connect.py sets "
                  "GETLAYERS_OAUTH_CLIENT_ID/_REFRESH_TOKEN) and "
                  "AGENCY_GETLAYERS_LICENCE_OK=true unlock mockups; "
                  "AGENCY_SMTP_* and AGENCY_SEND_DOMAIN unlock outreach; "
                  "deploys publish on this box at <slug>.mapfront.io unless "
                  "AGENCY_DEPLOY_HOST names a remote host (then with "
                  "AGENCY_DEPLOY_ROOT)")

    def __init__(self) -> None:
        self._sites: list[tuple[str, str | None]] = []

    async def setup(self) -> None:
        env_sites = os.getenv("AGENCY_SITES", "").strip()
        if env_sites:
            self._sites = _parse_sites_env(env_sites)
            return
        try:
            rows = await _store.list_sites()
        except Exception as exc:                        # noqa: BLE001
            log.warning("agency: could not read agency_sites at setup: %s", exc)
            return
        self._sites = [(r["slug"], published_domain(r)) for r in rows]

    async def teardown(self) -> None: ...

    def checks(self):
        base = (
            Check("prospect_budget", check_prospect_budget, timedelta(days=1),
                  Severity.NORMAL, "Places API spend against the monthly cap",
                  baseline_sigma=None),
            Check("leads_waiting", check_leads_waiting, timedelta(days=1),
                  Severity.INFO, "leads with no decision made on them yet",
                  baseline_sigma=None),
            Check("getlayers_auth", check_getlayers, timedelta(days=1),
                  Severity.NORMAL, "ATLAS can authenticate to GetLayers' MCP "
                  "server (mockups, site builds)", baseline_sigma=None),
            # One real round trip per run, booked under the chat's own cap.
            Check("site_chat", check_site_chat, sitechat.HEALTH_EVERY,
                  Severity.NORMAL, "client sites' AI chat assistant answers a "
                  "fixture site's question, under SITE_CHAT_MONTHLY_CAP_USD",
                  baseline_sigma=None, timeout=timedelta(seconds=45)),
        )
        site_checks = tuple(_make_site_check(slug, domain)
                            for slug, domain in self._sites)
        return base + site_checks

    def actions(self):
        return (
            Action(
                name="agency.prospect",
                tier=Tier.AUTO,          # read-only Places call, writes leads
                summary="Search Google Places for local businesses and store them as leads",
                run=prospect_action, dry_run=prospect_dry, rollback=prospect_rollback,
                params={
                    "query": "what to search for, e.g. 'plumber in Astoria'",
                    "lat": "search centre latitude",
                    "lng": "search centre longitude",
                    "radius_m": "search radius in meters",
                },
                max_per_hour=10,
            ),
            Action(
                name="agency.audit",
                tier=Tier.AUTO,          # one polite GET at a public page
                summary="Fetch a lead's website once and check it against the eight qualifying findings",
                run=audit_action, dry_run=audit_dry, rollback=audit_rollback,
                params={"lead_id": "the agency_leads.id to audit"},
                max_per_hour=30,
            ),
            Action(
                name="agency.rank",
                tier=Tier.AUTO,          # advisory: recomputes a stored score
                summary="Recompute the heuristic score and features for every open lead",
                run=rank_action, dry_run=rank_dry, rollback=rank_rollback,
                params={},
                max_per_hour=6,
            ),
            Action(
                name="agency.mockup",
                tier=Tier.AUTO,          # nothing leaves the box; writes a local HTML file
                summary="Build a designed preview website for this lead from the "
                        "checked-in templates",
                run=make_mockup, dry_run=make_mockup_dry, rollback=make_mockup_rollback,
                params={"lead_id": "the agency_leads.id to build a mockup for"},
                max_per_hour=10,
            ),
            Action(
                name="agency.find_email",
                tier=Tier.AUTO,          # a few polite GETs at a public site
                summary="Read a lead's own website for the email address it "
                        "publishes (mailto or page text; never a guess)",
                run=contacts.find_email, dry_run=contacts.find_email_dry,
                rollback=contacts.find_email_rollback,
                params={"lead_id": "the agency_leads.id whose site to read"},
                max_per_hour=30,
            ),
            Action(
                name="agency.draft_outreach",
                tier=Tier.AUTO,          # writes a draft row; nothing sent
                summary="Draft outreach text for a lead; never sends it",
                run=draft_outreach, dry_run=draft_outreach_dry,
                rollback=draft_outreach_rollback,
                params={"lead_id": "the agency_leads.id to draft outreach for",
                       "kind": "'email', 'call_script' or 'one_pager'"},
                max_per_hour=20,
            ),
            Action(
                name="agency.suppress",
                tier=Tier.AUTO,          # a safety list, not a send
                summary="Add an address to the permanent outreach suppression list",
                run=suppress, dry_run=suppress_dry, rollback=suppress_rollback,
                params={"address": "the email address to suppress",
                       "reason": "why"},
                max_per_hour=30,
            ),
            Action(
                name="agency.build_site",
                tier=Tier.AUTO,          # rearranges local files and a DB row
                summary="Turn a lead's mockup into a static site on disk, "
                        "or build a one-product store page",
                run=build_site, dry_run=build_site_dry, rollback=build_site_rollback,
                params={"lead_id": "the agency_leads.id to build a site for",
                       "kind": "'brochure' (default) or 'store'",
                       "product_photo": "store only: a .png/.jpg/.jpeg/.webp path "
                                        "under this lead's AGENCY_UPLOADS_DIR folder",
                       "product_description": "store only: the product's description",
                       "contact_email": "store only: the client's own order email",
                       "reviews_path": "store only: a .json path, under the same "
                                       "uploads folder, of real client-supplied reviews",
                       "variants": "store only: how many differently-angled versions "
                                   "to build and rank, 1 (default) to 5; the best "
                                   "deploys, the rest are attached, never shipped"},
                max_per_hour=10,
            ),
            Action(
                name="agency.webild_brief",
                tier=Tier.AUTO,          # writes a draft row; nothing leaves the box
                summary="Draft a Webild website prompt for a lead from its own data "
                        "and the client's client.json; never sends it",
                run=webild_brief, dry_run=webild_brief_dry,
                rollback=webild_brief_rollback,
                params={"lead_id": "the agency_leads.id to draft a Webild brief for"},
                max_per_hour=10,
            ),
            Action(
                name="agency.build_with_webild",
                tier=Tier.ASK,           # hands a brief to an agent in Miro's Chrome
                summary="Queue a drafted Webild brief for the agent on Miro's Mac to "
                        "build and publish on Webild's free subdomain",
                run=build_with_webild, dry_run=build_with_webild_dry,
                rollback=build_with_webild_rollback,
                params={"lead_id": "the agency_leads.id the brief is for",
                        "brief_id": "the agency_webild.id of the drafted brief"},
                card_only=True, verbose_card=True,
                card_detail=lambda p: (f"Webild brief #{p.get('brief_id')} for lead "
                                       f"{p.get('lead_id')}"),
                max_per_hour=6,
            ),
            Action(
                name="agency.record_webild_site",
                tier=Tier.AUTO,          # a status on ATLAS's own row + read-only GETs
                summary="Record the Webild site the Mac agent published for a lead, "
                        "and run the 20-item launch check against it",
                run=record_webild_site, dry_run=record_webild_site_dry,
                rollback=record_webild_site_rollback,
                params={"lead_id": "the agency_leads.id the site is for",
                        "url": "the published https URL on a Webild host"},
                card_only=True,
                max_per_hour=10,
            ),
            Action(
                name="agency.send_outreach",
                # Tier AUTO since 2026-10-01 (Miro: email the clients about
                # their previews; anything not urgent runs without a card).
                # Gated in code (outreach.auto_hold): the draft is the one
                # draft_outreach wrote (SHA-256), CAN-SPAM, a published
                # address never a guessed one, not suppressed, the daily cap,
                # one email per lead unless they replied, the preview live.
                # A failed hold is the Tier 2 card with the reason. The
                # rollback is honest: a delivered email is not unsent; the
                # address is suppressed and the lead carries the record.
                tier=Tier.AUTO,
                summary="Send the outreach email draft_outreach wrote about a "
                        "lead's live preview",
                run=send_outreach, dry_run=send_dry, rollback=send_outreach_rollback,
                params={"outreach_id": "the agency_outreach.id to send"},
                max_per_hour=10,
            ),
            Action(
                name="agency.draft_reply",
                tier=Tier.AUTO,          # writes a draft row; nothing sent
                summary="Draft a reply to a lead who wrote back, with the sales "
                        "playbook and the code-enforced price rules; never sends it",
                run=replies.draft_reply, dry_run=replies.draft_reply_dry,
                rollback=replies.draft_reply_rollback,
                params={"lead_id": "the agency_leads.id that replied",
                        "channel": "'email', or 'sms' (only with texting consent)"},
                max_per_hour=20,
            ),
            Action(
                name="agency.call_brief",
                tier=Tier.AUTO,          # one local file
                summary="Write a call brief for Miro to phone a lead himself "
                        "(who, their site's problems, talking points, objections, "
                        "price anchors)",
                run=calls.call_brief, dry_run=calls.call_brief_dry,
                rollback=calls.call_brief_rollback,
                params={"lead_id": "the agency_leads.id to brief"},
                max_per_hour=20,
            ),
            Action(
                name="agency.record_consent",
                tier=Tier.AUTO,          # a row on ATLAS's own table
                summary="Record that a lead told Miro, on his own call, that the "
                        "assistant may call them back (Miro's word only; the "
                        "number is the lead's listed one)",
                run=consent.record_consent, dry_run=consent.record_consent_dry,
                rollback=consent.record_consent_rollback,
                params={"lead_id": "the agency_leads.id",
                        "how": "what they said and when, e.g. 'said yes on my "
                               "call, 2026-10-01'"},
                principal_only=True,
                max_per_hour=20,
            ),
            Action(
                name="agency.record_deal",
                tier=Tier.AUTO,          # a row on ATLAS's own table
                summary="Record a lead's deal as won, paid or lost (Miro's word only)",
                run=replies.record_deal, dry_run=replies.record_deal_dry,
                rollback=replies.record_deal_rollback,
                params={"lead_id": "the agency_leads.id",
                        "status": "'won', 'paid' or 'lost'",
                        "amount": "optional: the figure in whole dollars"},
                principal_only=True,
                max_per_hour=10,
            ),
            Action(
                name="agency.send_reply",
                # Tier AUTO since 2026-10-06 (Miro: "make the lead replies
                # send themselves too"; anything not urgent runs without a
                # card). Gated in code (replies._guard_reply): the draft
                # draft_reply wrote, every price against the rules read now,
                # no promise the build does not keep, the honesty rules, no
                # time outside the real slots it was given, a verified lead,
                # not suppressed, CAN-SPAM and the sending domain (email) or
                # the lead's texting consent and the AI disclosure (text);
                # then the AGENCY_REPLY_AUTOSEND hold. A refusal is the
                # Tier 2 card with the reason. The rollback is honest: a
                # delivered message is not unsent; the address is suppressed
                # and the lead carries the record.
                tier=Tier.AUTO,
                summary="Send a drafted reply to a lead by email, or by text "
                        "when they consented to texts",
                run=replies.send_reply, dry_run=replies.send_reply_dry,
                rollback=replies.send_reply_rollback,
                params={"draft_id": "the agency_reply_drafts.id to send"},
                verbose_card=True,
                max_per_hour=10,
            ),
            Action(
                name="agency.text_lead",
                tier=Tier.ASK,           # a text to a stranger's phone
                irreversible=True,
                summary="Text a lead who consented to texts: the site link, or "
                        "a note after a missed call (words composed in code)",
                run=sms.text_lead, dry_run=sms.text_lead_dry, rollback=None,
                params={"lead_id": "the agency_leads.id to text",
                        "kind": "'site_link' or 'missed_call'"},
                verbose_card=True,
                max_per_hour=10,
            ),
            Action(
                name="agency.call_lead",
                # Tier AUTO since 2026-10-06 (Miro: "call them as well").
                # Never a cold call: consent.contact_guard is the gate in the
                # dry run and the run -- a live call consent the lead gave
                # on the form, by ringing the line, or to Miro on his own
                # call (recorded by him); not suppressed; AGENCY_CALLS_ENABLED;
                # Twilio configured; 08:00-21:00 lead time; the contact cap.
                # The call opens with the disclosure, written in code. A
                # refusal is the Tier 2 card. The rollback is honest: a call
                # is not un-rung; the number is suppressed and recorded.
                tier=Tier.AUTO,
                summary="Call a lead who asked to be called, as Miro's AI "
                        "assistant (disclosed first), within calling hours",
                run=calls.call_lead, dry_run=calls.call_lead_dry,
                rollback=calls.call_lead_rollback,
                params={"lead_id": "the agency_leads.id to call"},
                max_per_hour=6,
            ),
            Action(
                name="agency.verify",
                tier=Tier.AUTO,          # one research read, rows on our own tables
                summary="Confirm a lead is a real, operating NYC business with a "
                        "real web problem (Places status, address, Perplexity); "
                        "disqualify it otherwise",
                run=verify.verify_lead, dry_run=verify.verify_dry,
                rollback=verify.verify_rollback,
                params={"lead_id": "the agency_leads.id to verify"},
                max_per_hour=30,
            ),
            Action(
                name="agency.hold_meeting",
                tier=Tier.AUTO,          # Miro's own calendar, no attendee
                summary="Hold a free business-hours slot on Miro's calendar for a "
                        "call with a lead (nobody is invited)",
                run=meetings.hold_meeting, dry_run=meetings.hold_meeting_dry,
                rollback=meetings.release_hold,
                params={"lead_id": "the agency_leads.id",
                        "start": "the slot's start, ISO with offset, one offered "
                                 "from Miro's calendar",
                        "call_sid": "optional: the call it was booked on"},
                max_per_hour=10,
            ),
            Action(
                name="agency.send_invite",
                # Tier AUTO since 2026-10-06 (Miro: "schedule meetings with
                # them via email as well"). Gated in code
                # (meetings._invite_guard): a meeting still held, an email
                # on record and not suppressed, a verified lead, a time
                # still ahead. The rollback deletes the event and Google
                # tells the attendee.
                tier=Tier.AUTO,
                summary="Invite a lead to the call held on Miro's calendar, with "
                        "a short agenda",
                run=meetings.send_invite, dry_run=meetings.send_invite_dry,
                rollback=meetings.release_hold,
                params={"meeting_id": "the agency_lead_meetings.id"},
                verbose_card=True,
                max_per_hour=10,
            ),
            Action(
                name="agency.send_agreement",
                # Tier AUTO since 2026-10-06 (Miro: "approve invoices too").
                # Money IN -- Miro's own Stripe payment link, created by him
                # for exactly this price -- never money out, so not Tier 3.
                # Gated in code (replies._guard_agreement): a verified lead,
                # a price allowed by the rules read now, a link in
                # AGENCY_PAYMENT_LINKS for exactly that price, not
                # suppressed, CAN-SPAM. The text is written by code. The
                # rollback is honest: a delivered agreement is not unsent.
                tier=Tier.AUTO,
                summary="Send a lead the agreement and Miro's payment link at an "
                        "allowed price",
                run=replies.send_agreement, dry_run=replies.send_agreement_dry,
                rollback=replies.send_agreement_rollback,
                params={"lead_id": "the agency_leads.id that agreed",
                        "price": "the agreed price in whole dollars",
                        "channel": "'email', or 'sms' (only with texting consent)"},
                verbose_card=True,
                max_per_hour=6,
            ),
            Action(
                name="agency.deploy_site",
                # Tier AUTO since 2026-09-30 (Miro: "anything that is not
                # urgent, you approve it"): a preview on a mapfront.io
                # subdomain, the launch check gates it, and the previous
                # release is kept for the rollback.
                tier=Tier.AUTO,
                summary="Deploy a built client site to its production host",
                run=deploy_site, dry_run=deploy_dry, rollback=deploy_rollback,
                params={"slug": "the agency_sites.slug to deploy"},
                max_per_hour=6,
            ),
        )

    def brief(self) -> str:
        return "Agency: prospecting, audits, mockups, outreach drafts, sites"

    async def signals(self):
        """A card per fresh Webild brief (`agency.build_with_webild`). Also
        takes in lead replies forwarded into Miro's Gmail (`leads.sweep_inbox`,
        which posts its own alerts) and rings Miro for an emergency held
        back by the call gap (`escalate.flush`). A mailbox that cannot be
        read is logged, never read as "no replies"."""
        try:
            await leads.sweep_inbox()
        except Exception as exc:                        # noqa: BLE001
            log.warning("agency: lead inbox unreadable: %s", explain(exc))
        await escalate.flush()
        return await webild_signals()

    async def inbox(self, limit: int = 10):
        """Replied and bounced outreach — the two states that need Miro,
        not a scheduler, to look at them. A reply means a lead answered; a
        bounce means the address was bad and the row (or a suppression)
        needs fixing before anything else goes to it."""
        # A failed read raises: the brief names this inbox as unreadable
        # rather than folding it into "Nothing waiting on you".
        rows = await _store.list_outreach_needing_attention(limit)
        items = []
        for row in rows:
            replied = row.get("status") == "replied"
            items.append(InboxItem(
                at=row.get("at") or datetime.now(UTC),
                sender=row.get("lead_name") or f"lead {row.get('lead_id')}",
                subject=row.get("subject") or "(no subject)",
                urgency=Severity.HIGH if replied else Severity.NORMAL,
                why=("the lead replied — outreach is waiting on you" if replied
                    else "this send bounced — check the address, maybe suppress it"),
                reference=str(row.get("id")),
            ))
        return tuple(items)


MODULE = AgencyModule()
