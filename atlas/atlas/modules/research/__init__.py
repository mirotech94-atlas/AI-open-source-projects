"""research — "have it for me by morning".

Watches nothing continuously; it acts once, on request. What it may do:
search the web (`BRAVE_API_KEY`, or `PERPLEXITY_API_KEY` as a fallback
backend when Brave is not set), fetch up to six public pages of text, ask a
model (`Need.HARD`) to write a cited markdown report, and file it as a
`reports` row (kind='research') plus a `research_reports` row that can later
be withdrawn. Every fetched page is `wrap_untrusted()` before it reaches a
prompt — a web page is somebody else's text, not an instruction — and every
number in the finished report is checked against the raw source text before
filing: one that cannot be found is listed under "Unverified figures" rather
than trusted. Assumes Brave Search's HTTP API or Perplexity's chat
completions API (`atlas.modules._web.search`, which picks whichever key is
set); with neither set, `research.report` fails cleanly and says so rather
than guessing. Perplexity is a wrapped source like any web page, never an
oracle: its own answer is quoted and cited, not written into the report as
if ATLAS had said it.

Phase 11 (six agents) adds the competitor watch, all Tier 1 on our own
rows: `add_watch`, `add_competitor`, `daily_scan` (06:00 New York),
`refresh_profile` and `weekly_report` (Sunday 18:00), `pricing_table`,
`draft_client_report`, `propose_opportunity`. Tier 2 cards:
`send_client_report` (behind the QA gate), `adopt_opportunity` (writes a
`build` task on the board; ATLAS never edits its own code) and the paid
`deep_dive`, which nothing offers, proposes or calls while
`RESEARCH_PAID_CHECKS` is off.

Apart from the client report e-mail, which only goes out on an approved
card, it never posts, sends, deletes or spends anything.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    FailureClass,
    Health,
    Observation,
    Severity,
    Tier,
)
from . import profiles, store, watch
from .jobs import run_scan as run_scan
from .jobs import run_weekly as run_weekly
from .report import due_signals, report_dry, run_report, take_finished, withdraw

TOPIC = "agency_decisions"
PAID_ENV = "RESEARCH_PAID_CHECKS"
WEEKLY_KIND = "research_weekly"
DEEP_KIND = "research_deep_dive"
FRESH = timedelta(days=2)
_OFF = (f"the paid deep dive is off ({PAID_ENV}; CONFIGURE.md, six agents step 14)")


def paid() -> bool:
    return os.getenv(PAID_ENV, "").strip().lower() in {"1", "on", "true", "yes"}


def _refused(why: str) -> ActionResult:
    return ActionResult(False, why, failure_class=FailureClass.VALIDATION)


def _day(at: Any) -> str:
    if isinstance(at, datetime):
        return f"{at.astimezone(UTC):%Y-%m-%d}"
    return str(at or "")[:10]


def _price_line(p: dict[str, Any]) -> str:
    per = f"/{p['period']}" if p.get("period") else ""
    who = p.get("name") or "?"
    label = f" {p['label']}" if p.get("label") else ""
    return (f"- {who}{label}: {p['currency']} {p['amount']}{per} "
            f"({p['source_url']}, read {_day(p['fetched_at'])})")


def _sourced(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("source_url") and r.get("fetched_at")]


def _latest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The newest read of each price (same page, label, currency and period);
    an older read of a price that has since changed is history, not a price."""
    best: dict[tuple[Any, ...], dict[str, Any]] = {}
    for r in rows:
        key = (r["source_url"], r.get("label"), r.get("currency"), r.get("period"))
        if key not in best or str(r["fetched_at"]) > str(best[key]["fetched_at"]):
            best[key] = r
    return list(best.values())


# ------------------------------------------------------------ pricing table

def _scope(scope: str) -> tuple[str | None, int | None] | None:
    scope = str(scope or "all").strip().lower()
    if scope == "all":
        return None, None
    if scope in profiles.LANES and scope != "client":
        return scope, None
    head, _, lead = scope.partition(":")
    if head == "client" and lead.isdigit():
        return "client", int(lead)
    return None


async def pricing_table_dry(scope: str = "all") -> ActionResult:
    if _scope(scope) is None:
        return _refused("scope is all, academy, mapfront or client:<lead id>")
    return ActionResult(True, f"would list measured prices ({scope})")


async def pricing_table(scope: str = "all") -> ActionResult:
    """Measured prices only, each with its source and read date."""
    parsed = _scope(scope)
    if parsed is None:
        return _refused("scope is all, academy, mapfront or client:<lead id>")
    rows = _latest(_sourced(await store.prices(*parsed)))
    if not rows:
        return ActionResult(True, f"no measured prices yet ({scope}); a price is "
                                  "listed only with its source and read date")
    return ActionResult(True, f"Measured prices ({scope}, last 35 days):\n"
                        + "\n".join(_price_line(r) for r in rows),
                        evidence={"rows": len(rows)}, rollback_token="none")


async def _nothing(_token: str) -> ActionResult:
    return ActionResult(True, "a read; nothing to roll back")


# ----------------------------------------------------------- weekly report

async def _weekly_body() -> tuple[str, list[dict[str, Any]]]:
    now = datetime.now(UTC)
    changes = _sourced(await store.changes_since(7))
    prices = _latest(_sourced(await store.prices()))
    comps = await store.competitors()
    opps = await store.opportunities("proposed")
    stats = await store.watch_stats()
    lines = [f"# Competitor watch, week to {now:%Y-%m-%d} {watch.stamp(now)}", "",
             (f"Watches: {stats.get('active', 0)} active, {stats.get('failing', 0)} "
              f"failing; last read {_day(stats.get('last_read')) or 'never'}."), "",
             "## Changes"]
    lines += [f"- {c.get('label') or c['source_url']}: {c['excerpt'][:300]} "
              f"({c['source_url']}, read {_day(c['fetched_at'])})" for c in changes] \
        or ["- none measured this week"]
    lines += ["", "## Measured prices"]
    lines += [_price_line(p) for p in prices] or ["- none measured"]
    lines += ["", "## Profiles"]
    lines += [profiles.render(c) for c in comps[:20]] or ["- no competitors yet"]
    lines += ["", "## Proposed opportunities"]
    lines += [f"- {o['title']}" for o in opps] or ["- none"]
    sections = [{"title": "changes", "count": len(changes)},
                {"title": "prices", "count": len(prices)},
                {"title": "profiles", "count": len(comps)}]
    return "\n".join(lines), sections


async def weekly_report_dry() -> ActionResult:
    return ActionResult(True, "would file this week's competitor report")


async def weekly_report() -> ActionResult:
    body, sections = await _weekly_body()
    rid = await store.insert_report(WEEKLY_KIND, "Competitor watch (weekly)", body,
                                    sections)
    return ActionResult(True, f"weekly competitor report filed (report {rid}) "
                              f"{watch.stamp()}",
                        evidence={"report_id": rid}, rollback_token=str(rid))


async def withdraw_weekly(token: str) -> ActionResult:
    if not str(token).isdigit():
        return _refused("not a report id")
    await store.withdraw_report(int(token), WEEKLY_KIND)
    return ActionResult(True, f"report {token} withdrawn")


# ------------------------------------------------------------ opportunities

def _urls(source_urls: Any) -> list[str]:
    items = source_urls if isinstance(source_urls, list) else [source_urls]
    return [str(u) for u in items if str(u or "").startswith(("http://", "https://"))]


async def propose_opportunity_dry(title: str, detail: str,
                                  source_urls: list[str]) -> ActionResult:
    if not _urls(source_urls) or not (title or "").strip():
        return _refused("an opportunity needs a title and at least one source URL")
    return ActionResult(True, f"would propose: {title}")


async def propose_opportunity(title: str, detail: str,
                              source_urls: list[str]) -> ActionResult:
    urls = _urls(source_urls)
    if not urls or not (title or "").strip():
        return _refused("an opportunity needs a title and at least one source URL")
    oid = await store.insert_opportunity(title.strip()[:200], (detail or "")[:2000],
                                         urls[:10])
    carded = await store.raise_card(
        "research.adopt_opportunity", {"opportunity_id": oid},
        f"Opportunity: {title.strip()[:200]}", "ATLAS",
        f"{(detail or '')[:600]}\nSources: {', '.join(urls[:5])}\nAdopting adds a "
        "build task to the board; nothing is built or changed by itself.",
        f"research.adopt_opportunity:{oid}")
    return ActionResult(True, f"opportunity {oid} proposed"
                        + ("" if carded else " (card held)"),
                        evidence={"opportunity_id": oid}, rollback_token=str(oid))


async def withdraw_opportunity(token: str) -> ActionResult:
    if not str(token).isdigit():
        return _refused("not an opportunity id")
    await store.set_opportunity(int(token), "withdrawn")
    return ActionResult(True, f"opportunity {token} withdrawn")


async def adopt_opportunity_dry(opportunity_id: int) -> ActionResult:
    o = await store.opportunity(int(opportunity_id))
    if o is None or o.get("status") != "proposed":
        return _refused("no proposed opportunity with that id")
    return ActionResult(True, f"would add a build task: {o['title']}")


async def adopt_opportunity(opportunity_id: int) -> ActionResult:
    """A build task on the board. ATLAS never edits its own code."""
    from ..projects import store as pstore
    oid = int(opportunity_id)
    o = await store.opportunity(oid)
    if o is None or o.get("status") != "proposed":
        return _refused("no proposed opportunity with that id")
    tid, token = await pstore.add_task(
        project_id=None, title=f"Build: {o['title']}"[:200], kind="build", owner="you",
        due_at=None, evidence={"detail": o.get("detail"),
                               "source_urls": o.get("source_urls")},
        suggested_fix="Scope and build this yourself; ATLAS never edits its own code.",
        source_action="research.adopt_opportunity",
        source_ref=f"research_opportunities:{oid}")
    await store.set_opportunity(oid, "adopted", tid)
    return ActionResult(True, f"build task {tid} added for: {o['title']}",
                        evidence={"task_id": tid}, rollback_token=f"{oid}:{token}")


async def unadopt(token: str) -> ActionResult:
    oid, _, task_token = str(token).partition(":")
    if not oid.isdigit() or not task_token:
        return _refused("not an adoption token")
    from ..projects import store as pstore
    await pstore.restore(task_token)
    await store.set_opportunity(int(oid), "proposed")
    return ActionResult(True, f"opportunity {oid} back to proposed; its task cancelled")


# ------------------------------------------------------------ client report

async def _client_body(lead_id: int) -> str | None:
    comps = await store.competitors("client", int(lead_id))
    if not comps:
        return None
    prices = _latest(_sourced(await store.prices("client", int(lead_id)))
                     )
    lines = ["Hello,", "", ("Here is this month's look at your competitors. Every "
                           "price below was read from the competitor's own page on "
                           "the date shown; points marked 'model judgement' are our "
                           "reading, not a measurement."), ""]
    lines += [profiles.render(c) for c in comps]
    lines += ["", "Measured prices:"]
    lines += [_price_line(p) for p in prices] or ["- none measured yet"]
    lines += ["", f"Prepared {watch.stamp()}. PDF: not rendered (plain text only).",
              "", "Miro, Mapfront"]
    return "\n".join(lines)


async def draft_client_report_dry(lead_id: int) -> ActionResult:
    if not await store.competitors("client", int(lead_id)):
        return _refused(f"no competitors on file for client lead {lead_id}")
    return ActionResult(True, f"would draft the competitor report for lead {lead_id}")


async def draft_client_report(lead_id: int) -> ActionResult:
    from ..agency import replies
    body = await _client_body(int(lead_id))
    if body is None:
        return _refused(f"no competitors on file for client lead {lead_id}")
    to, why = await replies._recipient(int(lead_id), "email")
    if to is None:
        return _refused(f"no address to send it to: {why}")
    subject = "Your competitor report"
    rid = await store.insert_client_report(int(lead_id), to, subject, body)
    carded = await store.raise_card(
        "research.send_client_report", {"report_id": rid},
        f"Send the competitor report to lead {lead_id}", f"lead {lead_id}",
        body[:1500], f"research.send_client_report:{rid}")
    return ActionResult(True, f"client report {rid} drafted for lead {lead_id}"
                        + ("" if carded else " (card held)"),
                        evidence={"report_id": rid}, rollback_token=str(rid))


async def withdraw_client_report(token: str) -> ActionResult:
    if not str(token).isdigit():
        return _refused("not a report id")
    row = await store.client_report(int(token))
    if row is None or row.get("status") != "drafted":
        return _refused("only a drafted report can be withdrawn")
    await store.set_client_report(int(token), "withdrawn")
    return ActionResult(True, f"client report {token} withdrawn")


async def _send_refusal(rid: int) -> tuple[dict[str, Any] | None, str | None]:
    from ..bizops.mail import guard
    row = await store.client_report(rid)
    if row is None:
        return None, "no such client report"
    if row.get("status") != "drafted":
        return None, f"client report {rid} is {row.get('status')}"
    why = await guard(int(row["client_lead_id"]), str(row["to_address"]),
                      str(row["subject"]), str(row["body"]))
    return (None, why) if why is not None else (row, None)


async def send_client_report_dry(report_id: int) -> ActionResult:
    row, why = await _send_refusal(int(report_id))
    if row is None:
        return _refused(str(why))
    return ActionResult(True, f"would e-mail report {report_id} to {row['to_address']}")


async def send_client_report(report_id: int) -> ActionResult:
    from ..bizops.mail import send
    row, why = await _send_refusal(int(report_id))
    if row is None:
        return _refused(str(why))
    got = await send("research.send_client_report", f"rcr-{int(report_id)}",
                     str(row["to_address"]), str(row["subject"]), str(row["body"]))
    if got.ok:
        await store.set_client_report(int(report_id), "sent")
    return got


# ---------------------------------------------------------- paid deep dive

async def offer_deep_dive(topic: str) -> ActionResult:
    """Raise the card for one paid call. Refused, with no card, while off."""
    if not paid():
        return _refused(_OFF)
    topic = " ".join(str(topic or "").split())[:200]
    if not topic:
        return _refused("a deep dive needs a topic")
    carded = await store.raise_card(
        "research.deep_dive", {"topic": topic},
        f"Deep dive on '{topic}': 1 paid Perplexity call.", "ATLAS",
        "One perplexity_research call; the answer is filed as a cited report.",
        f"research.deep_dive:{topic.lower()}")
    return ActionResult(True, "deep dive card raised (1 paid Perplexity call)"
                        if carded else "deep dive card held")


async def deep_dive_dry(topic: str) -> ActionResult:
    if not paid():
        return _refused(_OFF)
    return ActionResult(True, f"would make 1 paid Perplexity call on '{topic}'")


async def deep_dive(topic: str) -> ActionResult:
    if not paid():
        return _refused(_OFF)
    from .. import _perplexity
    topic = " ".join(str(topic or "").split())[:200]
    ans, why = await _perplexity.call("perplexity_research", topic, caller="research")
    if ans is None:
        return _refused(f"not measured: {why}")
    body = (f"# Deep dive: {topic} (read {ans.read_at:%Y-%m-%d %H:%M} UTC)\n\n"
            f"{ans.text}\n\n"
            f"{_perplexity.sources_line(ans.citations)}")
    rid = await store.insert_report(DEEP_KIND, f"Deep dive: {topic}", body, [])
    return ActionResult(True, f"deep dive filed (report {rid})",
                        evidence={"report_id": rid})


# ------------------------------------------------------------------ checks

async def check_watch_health() -> Observation:
    now = datetime.now(UTC)
    try:
        s = await store.watch_stats()
    except Exception as exc:  # noqa: BLE001
        return Observation("watch_health", Health.UNKNOWN,
                           detail=f"research_watches unreadable: {type(exc).__name__}")
    active, failing = int(s.get("active") or 0), int(s.get("failing") or 0)
    if active == 0:
        return Observation("watch_health", Health.UNKNOWN,
                           detail="not measured: no watched pages yet "
                                  f"(checked {now:%Y-%m-%d %H:%M} UTC)")
    return Observation("watch_health", Health.DEGRADED if failing else Health.OK,
                       float(failing), "failing",
                       f"{failing} of {active} watched page(s) failing 3+ reads "
                       f"(checked {now:%Y-%m-%d %H:%M} UTC)")


async def check_scan_freshness() -> Observation:
    now = datetime.now(UTC)
    try:
        s = await store.watch_stats()
    except Exception as exc:  # noqa: BLE001
        return Observation("scan_freshness", Health.UNKNOWN,
                           detail=f"research_watches unreadable: {type(exc).__name__}")
    at = s.get("last_read")
    if not isinstance(at, datetime):
        return Observation("scan_freshness", Health.UNKNOWN,
                           detail="not measured: no page has been read yet")
    age = now - at
    return Observation("scan_freshness", Health.OK if age <= FRESH else Health.DEGRADED,
                       round(age.total_seconds() / 3600, 1), "hours",
                       f"last watched page read {at:%Y-%m-%d %H:%M} UTC")


def _detail(params: dict[str, Any]) -> str:
    key, value = next(iter(params.items()), ("", ""))
    return f"Research {key.replace('_', ' ')} {value} (text in the card's evidence)"


class ResearchModule:
    name = "research"
    title = "Research"
    requires_env = ()                    # never fails to load
    optional_env = ("BRAVE_API_KEY", "PERPLEXITY_API_KEY",  # neither: report fails
                    "YOUTUBE_API_KEY", PAID_ENV)
    setup_note = ("no search provider configured yet — set BRAVE_API_KEY or "
                  "PERPLEXITY_API_KEY, or research.report will run and fail "
                  "cleanly, saying so")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self) -> tuple[Check, ...]:
        return (
            Check("watch_health", check_watch_health, timedelta(hours=6), Severity.INFO,
                  "watched competitor pages that fail 3+ reads in a row",
                  baseline_sigma=None),
            Check("scan_freshness", check_scan_freshness, timedelta(hours=6),
                  Severity.INFO, "a watched page read within two days",
                  baseline_sigma=None),
        )

    def actions(self) -> tuple[Action, ...]:
        card: dict[str, Any] = {"card_only": True, "verbose_card": True,
                                "topic_for": lambda _p: TOPIC, "card_detail": _detail,
                                "max_per_hour": 6}
        cid = {"competitor_id": "the competitor_profiles id"}
        return (
            Action("research.add_watch", Tier.AUTO,
                   "Watch a public page for changes (scraper rules apply)",
                   run=watch.add_watch, dry_run=watch.add_watch_dry,
                   rollback=watch.archive_watch,
                   params={"url": "an http(s) URL", "label": "a short name",
                           "competitor_id": "optional competitor id"},
                   max_per_hour=20),
            Action("research.add_competitor", Tier.AUTO,
                   "Add a competitor (academy, mapfront or client lane) and watch "
                   "its homepage",
                   run=profiles.add_competitor, dry_run=profiles.add_competitor_dry,
                   rollback=profiles.archive_competitor,
                   params={"lane": "academy / mapfront / client", "name": "its name",
                           "homepage": "optional http(s) URL",
                           "client_lead_id": "the client's lead id (client lane)",
                           "youtube_channel_id": "optional YouTube channel id"},
                   max_per_hour=20),
            Action("research.daily_scan", Tier.AUTO,
                   "Read every watched page; store changes (dates ignored) and "
                   "measured prices with source and read time",
                   run=watch.daily_scan, dry_run=watch.daily_scan_dry,
                   rollback=watch.withdraw_scan, params={}, max_per_hour=2),
            Action("research.refresh_profile", Tier.AUTO,
                   "Refresh a competitor profile: measured prices and cadence, "
                   "labelled model judgements for the rest",
                   run=profiles.refresh_profile, dry_run=profiles.refresh_profile_dry,
                   rollback=profiles.undo_refresh, params=cid, max_per_hour=20),
            Action("research.weekly_report", Tier.AUTO,
                   "File the weekly competitor report (changes, prices, profiles)",
                   run=weekly_report, dry_run=weekly_report_dry,
                   rollback=withdraw_weekly, params={}, max_per_hour=2),
            Action("research.pricing_table", Tier.AUTO,
                   "List measured competitor prices with source and read date",
                   run=pricing_table, dry_run=pricing_table_dry, rollback=_nothing,
                   params={"scope": "all / academy / mapfront / client:<lead id>"},
                   max_per_hour=20),
            Action("research.draft_client_report", Tier.AUTO,
                   "Draft a client's competitor report and raise the send card",
                   run=draft_client_report, dry_run=draft_client_report_dry,
                   rollback=withdraw_client_report,
                   params={"lead_id": "the client's lead id"}, max_per_hour=6),
            Action("research.propose_opportunity", Tier.AUTO,
                   "Record a sourced opportunity and raise the adopt card",
                   run=propose_opportunity, dry_run=propose_opportunity_dry,
                   rollback=withdraw_opportunity,
                   params={"title": "short title", "detail": "why, from the sources",
                           "source_urls": "list of http(s) URLs"},
                   max_per_hour=6),
            Action("research.send_client_report", Tier.ASK,
                   "E-mail an approved competitor report to the client",
                   run=send_client_report, dry_run=send_client_report_dry,
                   irreversible=True,
                   params={"report_id": "the research_client_reports id"}, **card),
            Action("research.adopt_opportunity", Tier.ASK,
                   "Adopt an opportunity: add a build task to the board (ATLAS never "
                   "edits its own code)",
                   run=adopt_opportunity, dry_run=adopt_opportunity_dry,
                   rollback=unadopt,
                   params={"opportunity_id": "the research_opportunities id"}, **card),
            Action("research.deep_dive", Tier.ASK,
                   "Paid: one Perplexity research call filed as a cited report (off "
                   "unless RESEARCH_PAID_CHECKS is on)",
                   run=deep_dive, dry_run=deep_dive_dry, irreversible=True,
                   moves_money_or_credentials=True,           # a paid call
                   params={"topic": "what to research"}, **card),
            Action(
                name="research.report",
                tier=Tier.AUTO,     # read-only: search, fetch, write a note
                summary="Research a question overnight: search, fetch "
                        "sources, and file a cited report",
                run=run_report, dry_run=report_dry, rollback=withdraw,
                params={"question": "the question to research"},
                max_per_hour=6,
            ),
        )

    async def signals(self):
        return await due_signals()

    def brief(self) -> str:
        """Reports finished since the last brief, not a static description —
        `take_finished` drains the buffer `run_report` fills on success."""
        lines = take_finished()
        return "; ".join(lines) if lines else ""

    async def agenda(self, within):
        return ()

    async def inbox(self, limit: int = 10):
        return ()


MODULE = ResearchModule()
