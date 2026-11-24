"""SEO & Growth (six agents phase 10): audit and draft only.

Tier 1, our own rows: `audit_site`, `keyword_research`, `rank_snapshot`,
`draft_blog_post`, `propose_change`. Tier 2 cards behind the QA gate:
`publish_blog_post`, `apply_change` (both edit our build copy only; going
live is still `agency.deploy_site`). `ai_visibility_check` is a paid
Perplexity read: while `SEO_PAID_CHECKS` is off nothing offers it, nothing
proposes it and nothing calls Perplexity; when on, its card names how many
paid calls it makes.

The `seo_rank` job (07:00 New York) takes a rank snapshot of every site with
a domain, plus the Academy store (agency.example.com); on Mondays an audit,
the weekly client report (`weekly_report`; `draft_report_email` turns it into
a draft that is never sent from here) and the Academy `keyword_gaps`, each
through the executor, so watch-only holds all of it."""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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
from . import audit, blog, gsc, gsc_write, report, store, vitals

TOPIC = "agency_decisions"
PAID_ENV = "SEO_PAID_CHECKS"
MAX_PAID = 3
FRESH = timedelta(days=2)
NY = ZoneInfo("America/New_York")


def paid() -> bool:
    return os.getenv(PAID_ENV, "").strip().lower() in {"1", "on", "true", "yes"}


def _refused(why: str) -> ActionResult:
    return ActionResult(False, why, failure_class=FailureClass.VALIDATION)


_OFF = (f"the paid AI visibility check is off ({PAID_ENV}; CONFIGURE.md, six agents "
        "step 13)")


# ------------------------------------------------------- paid visibility

def _calls(n: int) -> str:
    return f"{n} paid Perplexity call" + ("" if n == 1 else "s")


async def offer_ai_visibility(slug: str) -> ActionResult:
    """Raise the card for the paid check. Refused, with no card, while off."""
    if not paid():
        return _refused(_OFF)
    site = await store.site(str(slug))
    if site is None or not site.get("domain"):
        return _refused(f"no site with a domain: {slug}")
    kws = await store.keywords(str(slug), MAX_PAID)
    if not kws:
        return _refused(f"no keywords for {slug} yet; seo.keyword_research first")
    client = str(site.get("client") or slug)
    carded = await store.raise_card(
        "seo.ai_visibility_check", {"slug": str(slug)},
        f"AI visibility check for {client}: {_calls(len(kws))}, one per keyword.",
        client, "\n".join(f"- {k}" for k in kws), f"seo.ai_visibility_check:{slug}")
    return ActionResult(True, f"AI visibility card for {slug} ({_calls(len(kws))})"
                        if carded else f"AI visibility card for {slug} held")


async def ai_visibility_check_dry(slug: str) -> ActionResult:
    if not paid():
        return _refused(_OFF)
    kws = await store.keywords(str(slug), MAX_PAID)
    if not kws:
        return _refused(f"no keywords for {slug} yet")
    return ActionResult(True, f"would make {_calls(len(kws))} for {slug}")


async def ai_visibility_check(slug: str) -> ActionResult:
    """Whether an AI answer for each keyword names the site. Read only."""
    if not paid():
        return _refused(_OFF)
    from .. import _perplexity
    site = await store.site(str(slug))
    if site is None or not site.get("domain"):
        return _refused(f"no site with a domain: {slug}")
    kws = await store.keywords(str(slug), MAX_PAID)
    if not kws:
        return _refused(f"no keywords for {slug} yet")
    domain = str(site["domain"]).lower().removeprefix("www.")
    name = str(site.get("client") or "").lower()
    lines: list[str] = []
    for kw in kws:
        ans, why = await _perplexity.call(
            "perplexity_search", f"Which businesses are recommended for {kw}?",
            caller="seo")
        if ans is None:
            lines.append(f"{kw}: not measured ({why})")
            continue
        text = ans.text.lower()
        named = domain in text or (bool(name) and name in text)
        lines.append(f"{kw}: {'named' if named else 'not named'} "
                     f"(read {ans.read_at:%Y-%m-%d %H:%M} UTC)")
    return ActionResult(True, f"{slug}: " + "; ".join(lines),
                        evidence={"lines": lines})


# ------------------------------------------------------------------ checks

async def check_rank_freshness() -> Observation:
    now = datetime.now(UTC)
    try:
        at = await store.last_measured_rank_at()
    except Exception as exc:                            # noqa: BLE001
        return Observation("rank_freshness", Health.UNKNOWN,
                           detail=f"seo_ranks unreadable: {type(exc).__name__}")
    if at is None:
        return Observation("rank_freshness", Health.UNKNOWN,
                           detail=f"not measured: no rank has been read (provider: "
                                  f"{audit.provider()}) (checked {now:%Y-%m-%d %H:%M}"
                                  " UTC)")
    age = now - at
    return Observation("rank_freshness", Health.OK if age <= FRESH else Health.DEGRADED,
                       round(age.total_seconds() / 3600, 1), "hours",
                       f"last measured rank read {at:%Y-%m-%d %H:%M} UTC")


async def check_blog_cadence() -> Observation:
    now = datetime.now(UTC)
    try:
        counts = await store.blog_counts_month()
    except Exception as exc:                            # noqa: BLE001
        return Observation("blog_cadence", Health.UNKNOWN,
                           detail=f"content_drafts unreadable: {type(exc).__name__}")
    total = sum(counts.values())
    per = ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none"
    return Observation("blog_cadence", Health.OK, float(total), "posts",
                       f"{total} blog draft(s) this month ({per}; cap "
                       f"{blog.PER_MONTH} per client) (read {now:%Y-%m-%d %H:%M} UTC)")


# -------------------------------------------------------------------- jobs

async def _run(action: str, params: dict[str, Any], why: str) -> str:
    from ...kernel import executor
    got = await executor.execute(executor.ExecutionRequest(action, params, reason=why))
    return str(got.detail)


async def run_daily(now: datetime | None = None) -> dict[str, str]:
    """The seo_rank job: ranks every day; on Mondays (New York) an audit, the
    weekly report of every client site and the Academy keyword gaps."""
    local = (now or datetime.now(UTC)).astimezone(NY)
    out: dict[str, str] = {}
    try:
        sites = await store.targets()
    except Exception as exc:                            # noqa: BLE001
        return {"targets": f"unreadable: {type(exc).__name__}"}
    for s in sites:
        slug = str(s["slug"])
        out[f"rank:{slug}"] = await _run("seo.rank_snapshot", {"slug": slug},
                                         "the daily rank snapshot")
        if local.weekday() == 0:
            out[f"audit:{slug}"] = await _run("seo.audit_site", {"slug": slug},
                                              "the Monday audit")
            if slug not in store.OWN:
                out[f"report:{slug}"] = await _run(
                    "seo.weekly_report", {"slug": slug}, "the Monday client report")
    try:
        for host, got in (await gsc_write.raise_cards(store.academy()["domain"])).items():
            out[f"gsc_card:{host}"] = got
    except Exception as exc:                            # noqa: BLE001
        out["gsc_cards"] = f"failed: {type(exc).__name__}"
    if local.weekday() == 0:
        out["gaps"] = await _run("seo.keyword_gaps", {},
                                 "the Monday Academy keyword gaps")
    return out


# ------------------------------------------------------------------ module

def _detail(params: dict[str, Any]) -> str:
    key, value = next(iter(params.items()), ("", ""))
    return f"SEO {key.replace('_', ' ')} {value} (text in the card's evidence)"


class SeoModule:
    name = "seo"
    title = "SEO & Growth"
    requires_env: tuple[str, ...] = ()
    optional_env = (*gsc.ENV, *vitals.KEY_ENV, "BRAVE_API_KEY", "GOOGLE_CSE_KEY",
                    "GOOGLE_CSE_CX", PAID_ENV)
    setup_note = ("Search Console needs the webmasters.readonly scope and each site "
                  "verified; ranks need BRAVE_API_KEY or GOOGLE_CSE_KEY + "
                  "GOOGLE_CSE_CX (CONFIGURE.md, six agents step 13). Until then "
                  "those read UNKNOWN or 'not measured'.")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self) -> tuple[Check, ...]:
        return (
            Check("search_console", gsc.check, timedelta(hours=6), Severity.INFO,
                  "Search Console per site: clicks, queries, striking distance, "
                  "losing pages, sitemaps, inspection (read only)",
                  baseline_sigma=None, timeout=timedelta(minutes=4)),
            Check(vitals.CHECK, vitals.check, timedelta(hours=24), Severity.INFO,
                  "PageSpeed Core Web Vitals per site, mobile and desktop, against "
                  "the last reading (read only)", baseline_sigma=None,
                  timeout=timedelta(minutes=10)),
            Check("rank_freshness", check_rank_freshness, timedelta(hours=6),
                  Severity.INFO, "a measured search rank within two days",
                  baseline_sigma=None),
            Check("blog_cadence", check_blog_cadence, timedelta(hours=12),
                  Severity.INFO, "blog drafts this month per client",
                  baseline_sigma=None),
        )

    def actions(self) -> tuple[Action, ...]:
        card: dict[str, Any] = {"card_only": True, "verbose_card": True,
                                "topic_for": lambda _p: TOPIC, "card_detail": _detail,
                                "max_per_hour": 6}
        slug = {"slug": "the agency site slug (or academy-store)"}
        return (
            Action("seo.audit_site", Tier.AUTO,
                   "Read a live site (GET only, its own host) and store an SEO audit; "
                   "unreadable is UNKNOWN",
                   run=audit.audit_site, dry_run=audit.audit_site_dry,
                   rollback=audit.withdraw_audit, params=slug, max_per_hour=20),
            Action("seo.keyword_research", Tier.AUTO,
                   "Keep up to 5 phrases from a site's own text (volume: not measured)",
                   run=audit.keyword_research, dry_run=audit.keyword_research_dry,
                   rollback=audit.withdraw_keywords, params=slug, max_per_hour=10),
            Action("seo.rank_snapshot", Tier.AUTO,
                   "Record where a site ranks for its keywords (free search only; "
                   "otherwise 'not measured')",
                   run=audit.rank_snapshot, dry_run=audit.rank_snapshot_dry,
                   rollback=audit.withdraw_ranks, params=slug, max_per_hour=20),
            Action("seo.draft_blog_post", Tier.AUTO,
                   "Draft a blog post from a keyword and the site's own text "
                   "(2 per client a month) and raise the publish card",
                   run=blog.draft_blog_post, dry_run=blog.draft_blog_post_dry,
                   rollback=blog.drop_draft,
                   params={**slug, "keyword": "the keyword the post is about"},
                   max_per_hour=4),
            Action("seo.propose_change", Tier.AUTO,
                   "Record an on-page change (title, meta description, h1, alt) and "
                   "raise the apply card",
                   run=blog.propose_change, dry_run=blog.propose_change_dry,
                   rollback=blog.withdraw_change,
                   params={**slug, "page": "a page name like index.html",
                           "spec": "title / meta_description / h1 / alt {src: text}"},
                   max_per_hour=10),
            Action("seo.weekly_report", Tier.AUTO,
                   "Build and store a client site's weekly SEO report from the "
                   "recorded readings (rank moves, vitals, fixes, next 3 actions)",
                   run=report.weekly_report, dry_run=report.weekly_report_dry,
                   rollback=report.withdraw_report, params=slug, max_per_hour=20),
            Action("seo.draft_report_email", Tier.AUTO,
                   "Save the latest weekly report as a client email draft for the "
                   "sites and sales agents to attach (draft only; never sends)",
                   run=report.draft_report_email, dry_run=report.draft_report_email_dry,
                   rollback=report.drop_report_email,
                   params={"slug": "a client site slug"}, max_per_hour=20),
            Action("seo.keyword_gaps", Tier.AUTO,
                   f"Compare up to {report.GAP_QUERIES} Academy keywords with the free "
                   "search results: store position and video title gaps (no model)",
                   run=report.keyword_gaps, dry_run=report.keyword_gaps_dry,
                   rollback=report.withdraw_report, max_per_hour=2),
            Action("seo.publish_blog_post", Tier.ASK,
                   "Write an approved blog post into our build copy of the site "
                   "(shipping is a separate deploy)",
                   run=blog.publish_blog_post, dry_run=blog.publish_blog_post_dry,
                   rollback=blog.unpublish,
                   params={"draft_id": "the content_drafts id"}, **card),
            Action("seo.apply_change", Tier.ASK,
                   "Apply an approved on-page change to our build copy of the site "
                   "(shipping is a separate deploy)",
                   run=blog.apply_change, dry_run=blog.apply_change_dry,
                   rollback=blog.revert_change,
                   params={"change_id": "the seo_changes id"}, **card),
            Action("seo.gsc_add_site", Tier.ASK,
                   "Add a real https site as a Search Console property (URL prefix; "
                   "needs the write consent; never a Vercel preview, never DNS)",
                   run=gsc_write.add_site, dry_run=gsc_write.add_site_dry,
                   rollback=gsc_write.remove_site, params=gsc_write.params_site(),
                   **{**card, "card_detail": gsc_write.describe}),
            Action("seo.gsc_verify_site", Tier.ASK,
                   "Ask Google to verify a property by the META tag, only when the "
                   "tag is already live on the page",
                   run=gsc_write.verify_site, dry_run=gsc_write.verify_site_dry,
                   rollback=gsc_write.unverify_site, params=gsc_write.params_site(),
                   **{**card, "card_detail": gsc_write.describe}),
            Action("seo.gsc_submit_sitemap", Tier.ASK,
                   "Submit a sitemap that already serves on the property's own host",
                   run=gsc_write.submit_sitemap, dry_run=gsc_write.submit_sitemap_dry,
                   rollback=gsc_write.remove_sitemap,
                   params={**gsc_write.params_site(),
                           "sitemap_url": "https://<host>/sitemap.xml, same host"},
                   **{**card, "card_detail": gsc_write.describe}),
            Action("seo.ai_visibility_check", Tier.ASK,
                   "Paid: ask Perplexity whether AI answers name the site (one call "
                   "per keyword, up to 3; off unless SEO_PAID_CHECKS is on)",
                   run=ai_visibility_check, dry_run=ai_visibility_check_dry,
                   irreversible=True, moves_money_or_credentials=True,  # paid calls
                   params=slug, **card),
        )

    async def signals(self) -> tuple:
        return ()


MODULE = SeoModule()
