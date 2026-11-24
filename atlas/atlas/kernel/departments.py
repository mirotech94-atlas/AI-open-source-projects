"""The Brain: ATLAS's real agents, jobs and actions grouped into departments.

the owner, 2026-09-29, after a reel ("37 AI agents run my entire company"): make
the Agency tab read like a company -- departments with a head, how much of
each runs without him, what is waiting on him, and each seat's standard
operating procedure. The twist is ATLAS's own rule: nothing is invented.
There are no seats here that the code does not register, and no numbers
here at all (`kernel/brain.py` reads those from the record).

What this file declares, and why it has to be declared:

  * `DEPARTMENTS` -- which department each roster agent, scheduler job
    (`scheduler.POLICIES`) and module sits in, and who heads it. An action
    is never listed by hand: it lands where its namespace (`MODULE.name`)
    does, so a new action in an existing module is placed without an edit.
    The head is the roster agent that already leads the department in code
    (a `missions.OWNERS` entry, or the agents that hand off to it), or
    "you" where none does. `tests/test_departments.py` proves every agent,
    job and action lands in exactly one department and that every name here
    exists.
  * `JOBS` -- for each job, what it reads, what it decides and which actions
    it may raise: code has the schedule (`POLICIES`) and the actions' tiers,
    but not a sentence saying what the job is for. Short on purpose.
  * The layer of each seat (`Layer`): an Automation is a fixed job with no
    model deciding anything; an AI agent is one agent with a model deciding
    one job; an Agentic system is several agents (or an agent and a loop
    around it) coordinating.

Everything else in an SOP -- tiers, card-only, where the card lands, whether
a read-back verifier exists, the trigger's missed-run policy -- is generated
from the registered objects, never written down here.

Pure: no database, no network, no model. Read-only visualisation: nothing
here changes an action, a tier, a job or a policy.
"""
from __future__ import annotations

import enum
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("atlas.departments")

YOU = "you"


class Layer(enum.StrEnum):
    AUTOMATION = "automation"
    AGENT = "agent"
    AGENTIC = "agentic"


LAYERS: tuple[dict[str, str], ...] = (
    {"key": Layer.AUTOMATION, "label": "Automation",
     "meaning": "a fixed scheduled job; no model decides anything"},
    {"key": Layer.AGENT, "label": "AI agent",
     "meaning": "one agent with a model deciding one job"},
    {"key": Layer.AGENTIC, "label": "Agentic system",
     "meaning": "several agents, or an agent inside a loop, coordinating"},
)


@dataclass(frozen=True)
class Department:
    key: str
    name: str
    # A roster agent's name, or YOU.
    head: str
    # Why that head: the agents that hand work to it, and/or its OWNERS entry.
    head_why: str
    agents: tuple[str, ...]
    jobs: tuple[str, ...]
    # Module names (`MODULE.name`, the action namespace), not directories:
    # `stripe` lives in `atlas/modules/stripe_billing`.
    modules: tuple[str, ...]
    # Roster agents that name the head in their `handoff` -- checked by the
    # test, so the "why" cannot drift from the roster.
    handoffs_to_head: tuple[str, ...] = ()
    # The head is a `missions.OWNERS` entry -- also checked.
    head_owns_missions: bool = False
    # Agents the head names in its own `handoff` -- checked.
    head_hands_off_to: tuple[str, ...] = ()
    # A tool the head holds that the "why" cites -- checked.
    head_has_tool: str = ""
    core: bool = False


# the owner, 2026-10-28: eight agents, nine with mmt (agents.AGENT_NAMES). The retired agents'
# seats are gone; their jobs and modules stay where their work still lands,
# and a department with no agent of its own is headed by "you".
DEPARTMENTS: tuple[Department, ...] = (
    Department(
        "knowledge", "Knowledge Core", "research",
        "owns the research module (missions.OWNERS): competitor watches, "
        "profiles and the weekly report",
        agents=("research",),
        jobs=("judgment", "research_scan", "research_weekly", "assistant_notices"),
        modules=("vault", "lessons", "research", "perplexity", "assistant",
                 "scrape", "owner_memory"),
        head_owns_missions=True,
        core=True),
    Department(
        "money", "Money & Markets", YOU,
        "no agent leads it since 2026-10-28: the markets cards and alerts "
        "are written to you",
        agents=(),
        jobs=("playlist", "ideas", "market_alerts"),
        # `futures` keeps its own clock (modules/futures/job.py: futures_tick,
        # futures_daily, futures_weekly), so its jobs are not scheduler
        # POLICIES and are not listed here; the module is.
        modules=("markets", "stripe", "purchasing", "realestate", "futures")),
    Department(
        "content", "Content & YouTube", "content",
        "owns academy, youtube, church and reddit (missions.OWNERS)",
        agents=("content",),
        jobs=("reels", "reels_nudge", "calendar", "clips", "clip_links", "funnels",
              "academy_reel", "academy_tutorial", "academy_teaching", "academy_videos",
              "youtube_learn", "linkedin_replies", "linkedin_autopost", "church",
              "content_weekly"),
        modules=("content", "instagram", "tiktok", "youtube", "linkedin",
                 "clips", "football", "church", "reddit", "ytlearn"),
        head_owns_missions=True),
    Department(
        "sales", "Sales & Sites", "sales",
        "owns the sales module (missions.OWNERS); BizOps and SEO hand off to it",
        agents=("sales", "sites", "bizops", "seo", "mmt"),
        jobs=("marketing", "measure", "lead_replies", "agency_lead_board",
              "academy_support", "academy_subscribe", "agency_contact", "sales_shift", "sites_shift", "bizops_digest",
              "bizops_find_repeats", "seo_rank", "mmt_shift", "sage_mmt_weekly"),
        modules=("marketing", "course", "agency", "fiverr", "exampleco", "shop",
                 "academy", "sales", "bizops", "seo", "mmt", "sagemmt"),
        handoffs_to_head=("bizops", "seo"),
        head_owns_missions=True),
    Department(
        "ops", "Ops & QA", "qa",
        "owns the qa module (missions.OWNERS): the gate, passive checks and "
        "missed runs",
        agents=("qa",),
        jobs=("checks", "signals", "fixes", "outbox", "retention", "approvals",
              "approvals_board", "qa_verify_runs", "whatsapp_daily", "whatsapp_alerts",
              "whatsapp_cards", "owner_calls", "draft_notice"),
        modules=("cloudco", "inroots", "netbird",
                 "backups", "gitlab", "nightshift", "delegate", "comms", "voice",
                 "approvals_board", "projects", "qa", "localstack", "atlas"),
        head_owns_missions=True),
    Department(
        "admin", "Personal Ops", "inbox",
        "the one agent that drafts his mail (mail_draft_reply), on the local "
        "model; it also answers what no other agent claims",
        agents=("inbox",),
        jobs=("rhythm", "inbox_shift"),
        modules=("mail", "calendar", "messages", "messaging", "matrix_rooms",
                 "mac", "browser", "screening", "school", "rhythm", "cameras"),
        head_has_tool="mail_draft_reply"),
    Department(
        "strategy", "Strategy", YOU,
        "no agent leads it: the brief, the weekly summary, the nightly review "
        "and the agency meeting are all written to you",
        agents=(),
        jobs=("brief", "weekly", "review", "agency_meeting", "agent_shifts",
              "missions"),
        modules=("posthog",)),
)

BY_KEY: dict[str, Department] = {d.key: d for d in DEPARTMENTS}


@dataclass(frozen=True)
class Job:
    """What code does not say about a scheduler job, in a few words."""

    when: str
    reads: str
    decides: str
    layer: Layer
    # Actions the job itself may run or raise, by name. Checked by the test
    # against the catalog: a name here that no module declares fails it.
    actions: tuple[str, ...] = ()
    # When the job can reach actions it does not name (an agent picks from
    # its tools, a module's signal names one): said, not listed.
    open_actions: str = ""
    # Where its own output goes when it is not an action's card.
    result: str = ""


JOBS: dict[str, Job] = {
    "checks": Job(
        "every scheduler cycle (30 s), each check at its own interval",
        "every module's checks", "nothing: a fixed reading, scored by the detector",
        Layer.AUTOMATION, result="findings; a CRITICAL pages you"),
    "approvals": Job(
        "every scheduler cycle", "pending cards past their expiry",
        "nothing: an expired card is expired, never run (silence is not consent)",
        Layer.AUTOMATION, result="the card's status"),
    "sales_shift": Job(
        "daily at SALES_SHIFT_HOUR (09:00 New York); a missed day is skipped",
        "new CSV files in data/sales/inbox/, new and qualified Sales leads, "
        "their own sites, today's card count",
        "code rules only: verified, researched, qualified; QA passes the draft "
        "before a card", Layer.AUTOMATION,
        actions=("sales.import_csv", "sales.find_leads", "agency.verify",
                 "sales.research_lead",
                 "sales.qualify", "agency.find_email", "agency.draft_outreach",
                 "qa.check_draft"),
        result="agency.send_outreach cards (Tier 2), at most SALES_DAILY_CARDS a "
               "day; nothing is sent"),
    "inbox_shift": Job(
        "daily 07:00 New York; a missed day is skipped",
        "every MAIL_ACCOUNTS mailbox (owner over IMAP, read-only) and the "
        "commitments classify() already stored",
        "code rules and the capped classifier: who is owed a reply today; an "
        "unreadable mailbox is UNKNOWN, never zero", Layer.AUTOMATION,
        result="a schedule_runs detail; nothing drafted or sent (IMAP has no "
               "draft path)"),
    "sites_shift": Job(
        "daily 08:00 New York; a missed day is skipped",
        "agency_leads, agency_sites, agency_outreach, pending cards, site_<slug> "
        "checks", "code rules only: with AGENCY_AUTO_BUILD=on, up to three "
        "waiting leads get a mockup and a built site", Layer.AUTOMATION,
        actions=("agency.mockup", "agency.build_site", "agency.publish_vercel"),
        result="agency.publish_vercel cards (Tier 2); the owner email card only "
               "after the owner approves the publish; nothing published or sent here"),
    "mmt_shift": Job(
        "daily 09:00 New York; a missed day is skipped",
        "public tutoring-centre pages (robots, daily cap, none of linkedin, "
        "instagram or facebook), mmt_prospects, mmt_intros",
        "code rules and a closed-schema extract: discover, score by code, draft "
        "one intro per prospect from facts with a verbatim quote; off unless "
        "MMT_DISCOVERY is on", Layer.AUTOMATION,
        actions=("mmt.discover", "mmt.score", "mmt.draft_intro"),
        result="mmt.send_intro cards (Tier 2); nothing sent here"),
    "whatsapp_daily": Job(
        "daily at WHATSAPP_DAILY_HOUR (20:00) New York; off unless WHATSAPP_DAILY=on; "
        "unsent, it retries every 30 min until midnight",
        "the same 24 h reads as the agent report: agent_runs, actions_log, checks, "
        "the work board",
        "nothing: lines are built by code per agent (Mark, Bea, Sage, Sam, Rex, "
        "Felix); an unreadable source says UNKNOWN", Layer.AUTOMATION,
        actions=("mmt.notify_daily",),
        result="one WhatsApp to the owner per New York date (Tier 1, notify only); "
               "nothing sent under ATLAS_WATCH_ONLY; a failed send leaves one Slack "
               "note"),
    "whatsapp_alerts": Job(
        "every pass; off unless WHATSAPP_ALERTS=on; at most WHATSAPP_ALERTS_PER_HOUR "
        "(3) an hour",
        "the health checks: a reading that turned from OK to failing in the last "
        "30 minutes (UNKNOWN and degraded never count)",
        "nothing: one plain line per reading built by code", Layer.AUTOMATION,
        actions=("mmt.notify_alert",),
        result="one WhatsApp to the owner per failing reading (Tier 1, notify only); "
               "nothing sent under ATLAS_WATCH_ONLY; a refused send leaves one Slack "
               "note"),
    "draft_notice": Job(
        "every pass, at the interval set in code; first run starts at the newest "
        "draft so old drafts are not replayed",
        "content_drafts: rows newer than the stored mark",
        "nothing is decided by a model: one fixed Slack card per new draft, the "
        "draft text wrapped as untrusted",
        Layer.AUTOMATION,
        actions=(),
        result="one Slack card per new content draft; reads drafts, writes only the "
               "mark; sends nothing outward but Slack"),
    "assistant_notices": Job(
        "every pass; off unless the assistant is on; at most 1 an hour and 3 a day "
        "(rolling), nothing in QUIET_HOURS, the same thing once per window",
        "what ATLAS already holds: a check that went from OK to failing, a "
        "proposal waiting over 2 hours, the cameras-online count dropping",
        "nothing: the text is fixed templates filled from fixed fields in code, "
        "outside words defanged; severity is NORMAL, never a page",
        Layer.AUTOMATION,
        actions=("assistant.update_owner",),
        result="one Slack DM to the owner per thing worth telling (Tier 1); nothing "
               "under ATLAS_WATCH_ONLY"),
    "owner_calls": Job(
        "every pass; off unless OWNER_CALLS=on; at most CALL_MAX_PER_DAY (3) a day, "
        "nothing in quiet hours",
        "the WhatsApp alerts and urgent cards already sent: one the owner has not "
        "answered for CALL_ESCALATE_MINUTES (15) while the problem is still there",
        "nothing: the words are fixed text in code, <Say> only; one call per event",
        Layer.AUTOMATION,
        actions=("mmt.call_owner",),
        result="one phone call to the owner per unanswered event (Tier 1); nothing "
               "under ATLAS_WATCH_ONLY; a call that could not be placed leaves one "
               "Slack note"),
    "whatsapp_cards": Job(
        "every pass; off unless WHATSAPP_CARDS=on; at most WHATSAPP_CARDS_PER_HOUR "
        "(5) an hour, nothing in quiet hours",
        "the approvals board: pending, unexpired Tier 2 cards nobody has messaged "
        "about (first run: the newest 5 only)",
        "nothing: one plain line per card built by code from the action; a code "
        "only for cards on the WhatsApp allowlist", Layer.AUTOMATION,
        actions=("mmt.notify_card",),
        result="one WhatsApp to the owner per pending card (Tier 1, notify only; "
               "the decision stays on the portal); nothing sent under "
               "ATLAS_WATCH_ONLY; a closed WhatsApp window leaves one Slack note"),
    "sage_mmt_weekly": Job(
        "weekly, Monday 10:00 New York; a missed week is skipped",
        "the TutorSite page's own numbers from the LinkedIn sweep's state file "
        "(UNKNOWN if unreadable, each with its read time), the four public "
        "tutoring.example pages (robots, wrapped as untrusted)",
        "code rules and fixed fact-backed angles: up to SAGE_MMT_MAX_POSTS_PER_WEEK "
        "(default 3) post drafts that pass the claim check, and an SEO note of "
        "three suggested edits; off unless SAGE_MMT_LINKEDIN is on", Layer.AUTOMATION,
        actions=("sagemmt.draft_week", "sagemmt.draft_seo_note"),
        result="linkedin.approve_draft cards and one sagemmt.accept_seo_note card "
               "(Tier 2); nothing posted or changed here"),
    "qa_verify_runs": Job(
        "every QA_VERIFY_RUNS_MIN (hourly)", "the scheduled windows of the last day "
        "(POLICIES) against schedule_runs",
        "nothing: a window with no done row is a miss, by code; each miss is one "
        "remediation task on the local board, never a retry", Layer.AUTOMATION,
        actions=("qa.verify_runs",),
        result="a qa_results row; open remediation tasks turn qa.open_remediation "
               "DEGRADED"),
    "approvals_board": Job(
        "every scheduler cycle when the pending cards changed, else every "
        "APPROVALS_BOARD_REFRESH_MIN", "pending cards: action, params, expiry, link",
        "nothing: each line is rendered from the action and its params in code; it "
        "lists cards and can approve none", Layer.AUTOMATION,
        actions=("approvals_board.create_channel",),
        result="the one pinned \"Waiting for you\" message in #approvals, edited "
               "in place"),
    "signals": Job(
        "every SIGNAL_INTERVAL_S", "what each module wants to say",
        "nothing: a signal naming an action becomes a Tier 2 card, one without "
        "is told", Layer.AUTOMATION,
        open_actions="any action a module's signal names, always as a Tier 2 card",
        result="the module's own topic"),
    "brief": Job(
        "daily at BRIEF_HOUR", "checks, findings, pending cards, calendar",
        "nothing: assembled from tables, no model", Layer.AUTOMATION,
        result="the brief (Slack)"),
    "agency_meeting": Job(
        "daily at BRIEF_HOUR", "agent_runs, readiness and next shifts of every agent",
        "which agents speak and what they ask each other; a model may only "
        "reword a line that passes the grounding check", Layer.AGENTIC,
        result="agency_meetings, and a short Slack summary"),
    "weekly": Job(
        "weekly on WEEKLY_DAY at BRIEF_HOUR", "the week's actions_log, "
        "proposals, reports and read-backs", "nothing: no model",
        Layer.AUTOMATION, result="the weekly summary (Slack)"),
    "marketing": Job(
        "weekly on MARKETING_DAY", "each business's playbook, experiments and "
        "outside research", "next week's experiments and ideas (a model plans)",
        Layer.AGENT,
        actions=("marketing.propose_experiment", "marketing.propose_idea"),
        result="the experiment ledger"),
    "measure": Job(
        "daily", "experiments whose 48 hours are up and their platform numbers",
        "measured or unmeasured, by rule", Layer.AUTOMATION,
        actions=("marketing.record_outcome",), result="the experiment ledger"),
    "playlist": Job(
        "weekly, Sunday 15:00 New York", "closes ATLAS read itself over the "
        "watchlist", "target weights (a model proposes; checked strictly, never "
        "repaired)", Layer.AGENTIC, actions=("markets.paper_rebalance",),
        result="a paper rebalance card; the weekly report compares it"),
    "ideas": Job(
        "weekdays 16:30 New York", "closes, volume, earnings dates, headlines",
        "rules find the setups; a model may only rank them", Layer.AGENTIC,
        actions=("markets.paper_idea",),
        result="trade idea cards; the weekly report scores approved vs declined"),
    "market_alerts": Job(
        "every MARKETS_ALERT_MIN", "the markets readings",
        "nothing: each event is keyed and sent once under a daily cap",
        Layer.AUTOMATION, actions=("markets.alert",)),
    "judgment": Job(
        "nightly at the tournament hour", "every decision and its outcome since "
        "the last run", "nothing at run time: it retrains the judgment heads",
        Layer.AUTOMATION, result="the judgment layer's weights"),
    "reels": Job(
        "daily at BRIEF_HOUR", "overnight results, research, the reel calendar",
        "the day's reels (a model drafts each script)", Layer.AGENT,
        actions=("content.reel_research", "content.draft_reel",
                 "content.draft_course_reel", "content.draft_football_reel",
                 "content.render_video", "content.publish_reel",
                 "content.autopublish_reel"),
        result="the reels digest"),
    "church": Job(
        "daily from CHURCH_SHIFT_HOUR New York time", "church_posts (the Mac's "
        "synced calendar), church_events, church_ideas and the community calendar",
        "nothing by model: which reviewed posts fit today's YouTube budget and "
        "the Facebook window, by rule", Layer.AUTOMATION,
        actions=("church.schedule_youtube_shorts", "church.schedule_facebook_reels",
                 "church.weekly_plan"),
        result="a shift entry in church_events and at most one batch card per "
               "platform"),
    "bizops_digest": Job(
        "daily at 08:00 New York time", "agency_deals, bizops_invoices, "
        "bizops_onboardings, bizops_automation_ideas",
        "nothing by model: code rules (a won or paid deal, a past due date)",
        Layer.AUTOMATION,
        actions=("bizops.start_onboarding", "bizops.draft_payment_reminder",
                 "bizops.daily_digest"),
        result="onboarding projects, reminder cards and a reports row (kind "
               "bizops_digest) to #agency-alerts; nothing is sent"),
    "bizops_find_repeats": Job(
        "daily at 07:00 New York time", "14 days of sent-mail subjects and board "
        "task titles (wrapped as untrusted)",
        "code rules only: a pattern seen 3+ times", Layer.AUTOMATION,
        actions=("bizops.find_repeats",),
        result="bizops.propose_automation cards (Tier 2)"),
    "seo_rank": Job(
        "daily at 07:00 New York time (audits, client reports and Academy "
        "gaps on Mondays)", "each client site with a domain, agency.example.com "
        "and agency.example.com (GET only), free search results, the recorded "
        "Search Console and PageSpeed readings",
        "nothing by model: code picks the rank, the audit items, the next "
        "actions and the gaps", Layer.AUTOMATION,
        actions=("seo.rank_snapshot", "seo.audit_site", "seo.weekly_report",
                 "seo.keyword_gaps"),
        result="seo_ranks rows ('not measured' without a free provider), "
               "seo_audits rows (UNKNOWN when a site cannot be read) and "
               "seo_reports rows (a missing reading says 'not measured')"),
    "research_scan": Job(
        "daily at 06:00 New York time", "each watched competitor page (through "
        "the scraper: robots.txt, deny list, daily cap; text wrapped as "
        "untrusted)", "nothing by model: dates are stripped and pages compared "
        "in code; prices are parsed in code", Layer.AUTOMATION,
        actions=("research.daily_scan",),
        result="research_changes and research_prices rows with source and read "
               "time; 3 failed reads in a row make one remediation task"),
    "research_weekly": Job(
        "Sunday 18:00 New York time", "competitor homepages and, with "
        "YOUTUBE_API_KEY, their upload dates", "a free model labels offer, "
        "positioning, strengths and weaknesses as model judgement; prices and "
        "cadence are measured in code", Layer.AUTOMATION,
        actions=("research.refresh_profile", "research.weekly_report"),
        result="competitor_profiles with history, and a reports row (kind "
               "research_weekly)"),
    "content_weekly": Job(
        "Monday 08:00 New York time", "youtube_videos, academy_tutorial_posts, "
        "academy_content_plan, academy_title_tests, schedule_runs, handoffs, "
        "church_posts and PostHog store views",
        "nothing by model: the report, the plan and the title decisions are "
        "code rules", Layer.AUTOMATION,
        actions=("academy.weekly_growth_report", "academy.plan_content",
                 "academy.switch_title"),
        result="a reports row (kind weekly), planned topics, title test steps and "
               "deduplicated #agency-alerts lines"),
    "reels_nudge": Job(
        "daily at 17:00", "today's reel cards", "nothing: a reminder if one waits",
        Layer.AUTOMATION, result="a reminder in the reels channel"),
    "calendar": Job(
        "every CALENDAR_TICK_MIN", "the 30-day reel calendar",
        "nothing: renders the next days' clips and raises today's card by plan",
        Layer.AUTOMATION,
        actions=("content.render_calendar_job", "content.publish_reel")),
    "clips": Job(
        "every CLIPS_INTAKE_MIN", "official campaign footage and the cut clips",
        "nothing: registers and cuts by sha256 under a daily cap, and posts "
        "one waiting clip per platform once it passes every coded check "
        "(CLIPS_AUTOPUBLISH, CLIPS_DAILY_CAP); a clip that does not is a card "
        "with the reason",
        Layer.AUTOMATION,
        actions=("clips.register_sources", "clips.cut", "clips.publish_clip",
                 "clips.publish_clip_tiktok", "clips.publish_clip_youtube_postiz"),
        result="the posted clip, with its receipt in the clips channel"),
    "funnels": Job(
        "every 5 minutes", "new comments on each active funnel's media",
        "nothing: a whole-word keyword match, once per commenter, the approved "
        "texts verbatim, none in quiet hours or past the daily cap",
        Layer.AUTOMATION, actions=("instagram.funnel_reply",),
        result="the send record (ig_funnel_sends); a stopped funnel pages you"),
    "missions": Job(
        "every MISSIONS_TICK_MIN", "each live mission's own tables",
        "the owning agent's next step from a coded recipe; a model only fills "
        "its slots", Layer.AGENTIC,
        open_actions="the owning agent's module actions (never Tier 3 or card-only)",
        result="the mission's report"),
    "clip_links": Job(
        "every CLIPS_LINKS_MIN", "Postiz's own record of each posted clip",
        "nothing: a published link copied only when it is the clip's own "
        "video URL", Layer.AUTOMATION,
        result="the clip's post_url, so its Whop submission can go"),
    "academy_reel": Job(
        "daily at ACADEMY_PROMO_HOUR_UTC", "the Academy catalog and "
        "academy_promo_posts (which product was shown longest ago)",
        "nothing: the next product in rotation, its catalog words, its "
        "edition; the post is gated in code (promo.autopublish_check)",
        Layer.AUTOMATION,
        actions=("academy.render_promo_reel", "clips.autopublish_promo"),
        result="the day's Academy reel on the clips accounts, or its card"),
    "academy_tutorial": Job(
        "daily at ACADEMY_TUTORIALS_HOUR_UTC", "the tutorial catalog "
        "(tutorial_series.py) and academy_tutorial_posts (what is already up)",
        "nothing: every word is code-held copy; the post is gated in code "
        "(tutorials.autopublish_check)", Layer.AUTOMATION,
        actions=("academy.render_tutorial", "academy.autopublish_tutorial",
                 "academy.pin_comment", "academy.add_to_lab_playlist",
                 "academy.take_out_of_lab_playlist"),
        result="the next episode on YouTube in its series playlist, or its card"),
    # the owner, 2026-10-13: two Shorts a day -- the teaching Short has
    # its own slot, 23 UTC on alternate New York days, not the tutorial tick.
    "academy_teaching": Job(
        "daily at ACADEMY_TEACHING_HOUR_UTC on alternate New York days",
        "academy_tutorial_posts (public tutorials), academy_promo_posts (cuts "
        "already posted, the day's count) and each episode's render",
        "nothing: the cut and its words are code-held (tutorial_short.py); the "
        "post is gated in code (promo.autopublish_check)", Layer.AUTOMATION,
        actions=("academy.render_teaching_short", "clips.autopublish_promo",
                 "academy.flag_synthetic_short"),
        result="a teaching Short cut from a public tutorial on the clips "
               "accounts, or held by the day's cap; every day, the AI flag on "
               "the last 14 days of Academy Shorts that lack it"),
    "academy_videos": Job(
        "every ACADEMY_VIDEOS_REFRESH_MIN", "academy_tutorial_posts (public "
        "tutorials) and the live videos.json", "nothing: the page is built from "
        "catalog and post rows in code", Layer.AUTOMATION,
        actions=("academy.publish_shop",),
        result="agency.example.com/videos/ listing every public tutorial"),
    "youtube_learn": Job(
        "daily at YOUTUBE_LEARN_HOUR New York time; a missed day is skipped",
        "the links the owner pasted (video_lessons, queued) and the Academy channel's "
        "public videos (academy_tutorial_posts); each transcript is read-only "
        "(UNKNOWN when it has no captions or cannot be read), wrapped as untrusted",
        "nothing: a model only writes the takeaways, which code counts, trims and "
        "screens; at most 5 pasted and 3 channel videos a day; off unless "
        "YOUTUBE_LEARN=on and held under ATLAS_WATCH_ONLY", Layer.AUTOMATION,
        actions=("ytlearn.save_video_notes",),
        result="a lessons card per video; approving only saves the note for you "
               "to read, it changes no prompt, rule, code or setting"),
    "lead_replies": Job(
        "every AGENCY_REPLY_TICK_MIN", "lead messages queued for a reply",
        "the reply's words (a model drafts with the sales playbook; every "
        "figure, promise and honesty rule checked in code)", Layer.AGENTIC,
        actions=("agency.draft_reply", "agency.send_reply"),
        result="a draft and its Tier 2 send card"),
    "linkedin_replies": Job(
        "every LINKEDIN_REPLY_TICK_MIN", "linkedin_requests: people who wrote "
        "to an active company page or requested its services",
        "the reply's words (a model drafts from the page's fact sheet; every "
        "figure, date, promise, link and honesty rule checked in code)",
        Layer.AGENTIC,
        actions=("linkedin.draft_request_reply", "linkedin.send_request_reply"),
        result="a reply in the page's own inbox thread and a Slack receipt, or "
               "its Tier 2 card with the reason"),
    "linkedin_autopost": Job(
        "every LINKEDIN_AUTOPUBLISH_TICK_MIN", "the LinkedIn state file: the Vault "
        "page's post bank, posting days and hours, the last sweep and the alerts",
        "nothing: the post is the bank's next approved entry, byte for byte, and "
        "every gate is in code (publish.autopublish_refusal)", Layer.AUTOMATION,
        actions=("linkedin.autopublish_post", "linkedin.publish_post"),
        result="the Vault page's post and a Slack receipt, or the Tier 2 publish "
               "card with \"Held for you: <reason>\""),
    "academy_support": Job(
        "every ACADEMY_SUPPORT_TICK_MIN", "academy_support_tickets: the store's Help "
        "form, the support mailbox (support_mail, a collector) and chat questions "
        "sent on",
        "the reply's words (a model drafts from the support KB only; routing, every "
        "price, refund, money, legal and honesty rule and the grounding checked in "
        "code)",
        Layer.AGENTIC,
        actions=("academy.draft_support_reply", "academy.send_support_reply",
                 "academy.create_support_channel"),
        result="an email reply from the support address and a Slack receipt, or its "
               "Tier 2 card with the reason; tickets near two business days posted"),
    "academy_subscribe": Job(
        "every 5 minutes", "academy_subscribers: free-updates signups from the "
        "store form, pending until their owner confirms",
        "nothing is decided by a model: fixed confirmation text, the address "
        "typed on the form, a 24-hour resend limit and a daily cap, all in code",
        Layer.AUTOMATION,
        actions=("academy.send_signup_confirmation",),
        result="one confirmation email per pending signup; an unconfirmed signup "
               "is deleted after 7 days; never a newsletter"),
    "agency_contact": Job(
        "every minute", "agency_site_messages: messages typed into the website "
        "contact forms, stored first, then sent to the owner",
        "nothing is decided by a model: the owner email (Reply-To the visitor), the "
        "WhatsApp text and the auto-reply are fixed templates, each channel retried "
        "up to 3 times, all in code",
        Layer.AUTOMATION,
        actions=("agency.deliver_contact",),
        result="one email and one WhatsApp message to the owner per site message, plus an "
               "auto-reply to the visitor; a channel that cannot deliver is reported, "
               "never marked sent"),
    "agency_lead_board": Job(
        "daily at BRIEF_HOUR", "agency leads, outreach, replies, consent, "
        "contacts and deals", "nothing: counts read from tables, no model",
        Layer.AUTOMATION, result="the lead board (Slack, agency channel)"),
    "review": Job(
        "nightly at REVIEW_HOUR", "the day's observations, actions, cards",
        "nothing: a score from rules, no model", Layer.AUTOMATION,
        result="the nightly review"),
    "rhythm": Job(
        "hourly", "when you start and stop", "nothing: a rolling baseline",
        Layer.AUTOMATION),
    "retention": Job(
        "hourly", "rows past their retention", "nothing: purges what is due",
        Layer.AUTOMATION),
    "fixes": Job(
        "every FIX_SWEEP_HOURS", "checks DEGRADED or FAILING for hours",
        "an agent diagnoses the check and picks a fix from its tools",
        Layer.AGENT,
        open_actions="whatever the agent's tools reach; a Tier 2 fix is a card",
        result="the check's own channel"),
    "agent_shifts": Job(
        "each agent's shift hour (default daily)", "each agent's own tools and memory",
        "every agent, in turn, what to do on its shift", Layer.AGENTIC,
        open_actions="each agent's own write tools"),
    "outbox": Job(
        "every cycle with the durable core on", "queued effects that are due",
        "nothing: dispatches each row once, reconciles the unknown",
        Layer.AUTOMATION,
        open_actions="any queued action, already approved or Tier 1"),
}


# ------------------------------------------------------------ the lookups

def department_of_module(module: str) -> str | None:
    for d in DEPARTMENTS:
        if module in d.modules:
            return d.key
    return None


def department_of_action(action: str) -> str | None:
    """By namespace (`markets.paper_idea` -> markets -> money)."""
    return department_of_module(action.split(".", 1)[0]) if action else None


def department_of_agent(agent: str) -> str | None:
    for d in DEPARTMENTS:
        if agent in d.agents:
            return d.key
    return None


def department_of_job(job: str) -> str | None:
    for d in DEPARTMENTS:
        if job in d.jobs:
            return d.key
    return None


def department_of_scope(scope: str) -> str | None:
    """A pause scope's department: `instagram:clips` -> content. `all` and
    anything unplaceable are None (the whole business, or unmapped)."""
    base = scope.removeprefix("shadow:").split(":", 1)[0]
    return department_of_module(base)


def agent_layer(tools: Iterable[str]) -> Layer:
    """An agent that can hand work to another agent (`ask_specialist`) is
    part of a system of agents; one that cannot is one agent."""
    return Layer.AGENTIC if "ask_specialist" in set(tools) else Layer.AGENT


# ------------------------------------------------------------ SOP building

@dataclass
class ActionFacts:
    """What an SOP says about one action, all read from code."""

    name: str
    tier: int
    summary: str
    card_only: bool
    verifier: bool
    topic: str
    topic_depends_on: str | None
    own_channel: bool | None
    department: str | None
    principal_only: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "tier": self.tier, "summary": self.summary,
            "card_only": self.card_only, "verifier": self.verifier,
            "topic": self.topic, "topic_depends_on": self.topic_depends_on,
            "own_channel": self.own_channel, "department": self.department,
            "principal_only": self.principal_only,
            "needs_you": self.tier >= 2,
        }


def action_facts(name: str, spec: Any, tier: int, *,
                 has_verifier: Callable[[str], bool],
                 home_for: Callable[[str], str | None] | None) -> ActionFacts:
    """One action's facts. `tier` is the classified decision's, never the
    raw declaration. Where the card lands is the namespace unless the action
    routes by a parameter; then it is said to depend on that parameter, not
    resolved against a made-up value."""
    topic_param = getattr(spec, "topic_param", None)
    topic_for = getattr(spec, "topic_for", None)
    namespace = name.split(".", 1)[0]
    depends: str | None = None
    if topic_for is not None:
        depends = "its parameters"
    elif topic_param:
        depends = str(topic_param)
    topic = namespace
    own: bool | None = None
    if home_for is not None:
        try:
            own = bool(home_for(topic))
        except Exception:                                   # noqa: BLE001
            own = None
    try:
        verifier = bool(has_verifier(name))
    except Exception:
        log.debug("verifier lookup for %s failed", name, exc_info=True)
        verifier = False
    return ActionFacts(
        name=name, tier=int(tier), summary=str(getattr(spec, "summary", "") or ""),
        card_only=bool(getattr(spec, "card_only", False)), verifier=verifier,
        topic=topic, topic_depends_on=depends, own_channel=own,
        department=department_of_action(name),
        principal_only=bool(getattr(spec, "principal_only", False)))


def _where(f: ActionFacts) -> str:
    base = f"#{f.topic}"
    if f.topic_depends_on:
        base += f" (or by {f.topic_depends_on})"
    if f.own_channel is True:
        return base + ", its own Slack channel"
    if f.own_channel is False:
        return base + ", the default Slack channel"
    return base


def _step(kind: str, text: str, actions: list[ActionFacts] | None = None
          ) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": kind, "text": text}
    if actions is not None:
        out["actions"] = [a.as_dict() for a in actions]
    return out


def _acts_and_signoff(facts: list[ActionFacts], open_actions: str
                      ) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    auto = [f for f in facts if f.tier < 2]
    asks = [f for f in facts if f.tier >= 2]
    if facts:
        steps.append(_step(
            "acts",
            f"{len(auto)} it may run on its own (Tier 1, with a dry run and a "
            f"rollback), {len(asks)} only as a card for you (Tier 2)", facts))
    if open_actions:
        steps.append(_step("acts", open_actions))
    if not facts and not open_actions:
        steps.append(_step("acts", "no action: it only reads and reports"))
    if asks:
        card_only = [f.name for f in asks if f.card_only]
        text = (f"needs your sign-off on {len(asks)}: "
                + ", ".join(f.name for f in asks))
        if card_only:
            text += (f". Card-only (raised only by the code that drafted it, "
                     f"never picked by a model): {', '.join(card_only)}")
        steps.append(_step("signoff", text, asks))
    elif facts:
        steps.append(_step("signoff", "nothing it may do needs your sign-off"))
    return steps


def _verify_line(facts: list[ActionFacts]) -> str:
    if not facts:
        return ""
    with_v = [f.name for f in facts if f.verifier]
    if not with_v:
        return "; no read-back verifier on any of its actions"
    return (f"; read back after it runs: {', '.join(with_v)}"
            + (f" ({len(facts) - len(with_v)} without a verifier)"
               if len(with_v) < len(facts) else ""))


def job_sop(name: str, policy: Any, facts: Mapping[str, ActionFacts]
            ) -> list[dict[str, Any]]:
    """trigger -> reads -> decides -> acts -> sign-off -> result, for one
    scheduler job, from its policy and its actions' facts."""
    job = JOBS.get(name)
    missed = getattr(getattr(policy, "missed", None), "value", None) or "unknown"
    windowed = getattr(policy, "windows", None) is not None
    why = str(getattr(policy, "why", "") or "")
    trigger = (f"{job.when if job else 'schedule unknown'} · missed-run policy "
               f"{missed}" + ("" if windowed else " (interval: no per-run record)"))
    if why:
        trigger += f" — {why}"
    steps = [_step("trigger", trigger)]
    if job is None:
        steps.append(_step("reads", "not described"))
        return steps
    steps.append(_step("reads", job.reads))
    steps.append(_step("decides", job.decides))
    own = [facts[a] for a in job.actions if a in facts]
    missing = [a for a in job.actions if a not in facts]
    steps += _acts_and_signoff(own, job.open_actions)
    if missing:
        steps.append(_step("acts", "not registered in this process (module off): "
                           + ", ".join(missing)))
    where = "; ".join(sorted({_where(f) for f in own}))
    result = job.result or ""
    if where:
        result = f"{result}; cards and receipts in {where}" if result else \
            f"cards and receipts in {where}"
    steps.append(_step("result", (result or "its own record") + _verify_line(own)))
    return steps


def agent_actions(tools: Iterable[str], owner_modules: Iterable[str],
                  tool_action: Mapping[str, str | None],
                  facts: Mapping[str, ActionFacts],
                  in_catalog: Callable[[str], bool]) -> list[ActionFacts]:
    """What an agent may take: its write tools' actions, and -- when it owns
    missions -- its modules' actions a mission may run (the model-facing
    catalog: never Tier 3, never card-only)."""
    names: set[str] = set()
    for t in tools:
        a = tool_action.get(t)
        if a and a in facts:
            names.add(a)
    owned = set(owner_modules)
    for a in facts:
        if a.split(".", 1)[0] in owned and in_catalog(a):
            names.add(a)
    return [facts[n] for n in sorted(names)]


def agent_sop(name: str, *, title: str, brief: str, need: str,
              memory_scope: Iterable[str], handoff: Iterable[str],
              read_tools: Iterable[str], shift_hours: float,
              acts: list[ActionFacts]) -> list[dict[str, Any]]:
    handoff = sorted(handoff)
    shift = ("no shift (off)" if shift_hours <= 0 else
             "a daily shift" if shift_hours == 24 else
             f"a shift every {shift_hours:g} h")
    steps = [_step("trigger", f"when you ask it (chat, voice, a mission), when "
                   f"another agent hands off to it, and {shift}")]
    scope = ", ".join(memory_scope) or "no memory scope"
    reads = sorted(read_tools)
    steps.append(_step("reads", f"memory: {scope}; read tools: "
                       + (", ".join(reads) if reads else "none")))
    decides = f"{brief.strip().rstrip('.')} — a model decides ({need} route)"
    if handoff:
        decides += f"; may hand off to {', '.join(handoff)}"
    steps.append(_step("decides", decides))
    steps += _acts_and_signoff(acts, "")
    where = "; ".join(sorted({_where(f) for f in acts}))
    steps.append(_step("result", "its answer, recorded in agent_runs"
                       + (f"; cards in {where}" if where else "")
                       + _verify_line(acts)))
    return steps


def action_sop(f: ActionFacts, raised_by: list[str]) -> list[dict[str, Any]]:
    """One action's own procedure: who may raise it, its gate, where it
    lands, whether it is read back."""
    by = ", ".join(raised_by) if raised_by else "an agent or you, from the catalog"
    if f.card_only:
        by += " (card-only: only the code that drafted it raises it)"
    steps = [_step("trigger", f"raised by {by}")]
    steps.append(_step("decides", f.summary or "no summary"))
    if f.tier >= 2:
        steps.append(_step("signoff", "Tier 2: a card for you; it runs only when "
                           "you approve, and expires unanswered", [f]))
    else:
        steps.append(_step("acts", "Tier 1: runs on its own after a dry run, "
                           "with a rollback", [f]))
    if f.principal_only:
        steps.append(_step("signoff", "runs only when you asked for it yourself"))
    steps.append(_step("result", _where(f) + (
        "; read back after it runs" if f.verifier else "; no read-back verifier")))
    return steps


@dataclass
class Placement:
    """Where every item went, for the completeness test and the endpoint."""

    agents: dict[str, str] = field(default_factory=dict)
    jobs: dict[str, str] = field(default_factory=dict)
    actions: dict[str, str] = field(default_factory=dict)
    unplaced: dict[str, list[str]] = field(default_factory=dict)


def place(agents: Iterable[str], jobs: Iterable[str], actions: Iterable[str]
          ) -> Placement:
    p = Placement()
    for kind, names, fn, into in (
            ("agents", agents, department_of_agent, p.agents),
            ("jobs", jobs, department_of_job, p.jobs),
            ("actions", actions, department_of_action, p.actions)):
        for n in names:
            d = fn(n)
            if d is None:
                p.unplaced.setdefault(kind, []).append(n)
            else:
                into[n] = d
    return p
