"""The weekly SEO report per client site, its email draft, and the Academy
keyword gaps (2026-10-29). Tier 1, our own rows; nothing here sends.

`build` assembles, in code, what was already read: the Search Console
check's per-site readings (this one and the one a week before), the Core
Web Vitals check's latest reading, our rank rows (this week against last),
and the fixes applied this week (on-page changes, published blog posts).
The next three actions are picked by rules, worst first. Every figure keeps
the time it was read; a reading that does not exist is "not measured",
never 0 (invariant 6).

`draft_report_email` turns the stored report into a `content_drafts` row
(kind `seo_report_email`): a draft the sites and sales agents attach to a
client email. No path sends that kind; sending stays with the approved
agency send cards. `keyword_gaps` compares, for the Academy store and the
channel, the store's Search Console queries and its keywords with the free
search results (Brave or Google CSE through `_scrape.search`, capped at
GAP_QUERIES a week), in code: no model, and no result text is kept, only
positions and hosts.

Query strings are what strangers typed into Google: they are cleaned to
plain short text here and never reach a prompt from this module."""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from ...kernel.contracts import ActionResult, FailureClass
from .. import _scrape
from . import store

CLIENT_KIND = "client_weekly"
GAPS_KIND = "academy_gaps"
GAP_QUERIES = 8
WEEK = timedelta(days=7)
_CTRL = re.compile(r"[\x00-\x1f\x7f<>]")
_TOKEN = re.compile(r"[a-z0-9]{3,}")


def _refused(why: str) -> ActionResult:
    return ActionResult(False, why, failure_class=FailureClass.VALIDATION)


def clean(text: Any, limit: int = 80) -> str:
    return " ".join(_CTRL.sub(" ", str(text or "")).split())[:limit]


def week_of(now: datetime) -> str:
    y, w, _ = now.astimezone(UTC).isocalendar()
    return f"{y}-W{w:02d}"


def _at(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        got = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return got if got.tzinfo else got.replace(tzinfo=UTC)


def _stamp(value: Any) -> str:
    at = _at(value)
    return f"read {at:%Y-%m-%d %H:%M} UTC" if at else "read time unknown"


def _site_in(evidence: Any, slug: str) -> dict[str, Any] | None:
    if not isinstance(evidence, dict):
        return None
    for s in evidence.get("sites") or []:
        if isinstance(s, dict) and s.get("slug") == slug:
            return s
    return None


# ------------------------------------------------------------------ build

def _search(slug: str, history: list[Any]) -> tuple[dict[str, Any] | None, str]:
    readings = [s for ev in history if (s := _site_in(ev, slug)) and s.get("totals")]
    if not readings:
        return None, "Search Console: not measured (no reading of this site yet)"
    cur = readings[0]
    cur_at = _at(cur.get("read_at"))
    before = next((s for s in readings[1:]
                   if cur_at and (a := _at(s.get("read_at"))) and a <= cur_at - WEEK
                   + timedelta(hours=12)), None)
    t, b = cur["totals"], (before or {}).get("totals") or {}
    out = {"read_at": cur.get("read_at"), "range": cur.get("range"),
           "clicks": t.get("clicks"), "impressions": t.get("impressions"),
           "ctr": t.get("ctr"), "position": t.get("position"),
           "clicks_before": b.get("clicks"), "position_before": b.get("position"),
           "before_read_at": (before or {}).get("read_at"),
           "top_queries": [{**q, "query": clean(q.get("query"))}
                           for q in cur.get("top_queries") or []][:5],
           "striking": [{**q, "query": clean(q.get("query"))}
                        for q in cur.get("striking") or []][:5],
           "losing": list(cur.get("losing") or [])[:5],
           "sitemaps": cur.get("sitemaps"), "inspected": cur.get("inspected"),
           "issues": list(cur.get("issues") or [])}
    return out, ""


def _vitals(slug: str, history: list[Any]) -> tuple[dict[str, Any] | None, str]:
    for ev in history:
        s = _site_in(ev, slug)
        if s and (s.get("mobile") or s.get("desktop")):
            return {"read_at": s.get("read_at"), "mobile": s.get("mobile"),
                    "desktop": s.get("desktop"),
                    "regressions": list(s.get("regressions") or []),
                    "issues": list(s.get("issues") or [])}, ""
    return None, "Core Web Vitals: not measured (no PageSpeed reading of this site yet)"


def next_actions(r: dict[str, Any], n: int = 3) -> list[str]:
    """Worst first, by rule."""
    out: list[str] = []
    v, sc = r.get("vitals") or {}, r.get("search") or {}
    for reg in v.get("regressions") or []:
        out.append(f"Fix the speed regression ({reg}): compress the largest image "
                   "and defer scripts above the fold")
    for issue in v.get("issues") or []:
        out.append(f"Speed up the site ({issue}); target LCP under 2.5 s on mobile")
    insp = sc.get("inspected") or {}
    for page in (insp.get("not_indexed") or [])[:2]:
        out.append(f"Get {clean(page.get('url'), 120)} indexed: request indexing in "
                   "Search Console and link it from the home page")
    if sc and sc.get("sitemaps") == []:
        out.append("Submit the sitemap in Search Console")
    for m in sc.get("sitemaps") or []:
        if m.get("errors"):
            out.append(f"Fix {m['errors']} error(s) in the sitemap {clean(m.get('path'))}")
    for q in sc.get("striking") or []:
        out.append(f"Strengthen the page for \"{q['query']}\" (position "
                   f"{q.get('position')}, {q.get('impressions')} impressions): put it "
                   "in the title and a section heading")
    for p in sc.get("losing") or []:
        out.append(f"Check why {clean(p.get('page'), 120)} lost clicks "
                   f"({p.get('prior')} -> {p.get('clicks')} a week)")
    for m in r.get("ranks") or []:
        if m.get("position") is None and m.get("prior") is not None:
            out.append(f"Win back \"{m['keyword']}\" (was #{m['prior']}, now out of "
                       "the results read)")
    if len(out) < n:
        for m in r.get("ranks") or []:
            if m.get("position") is None:
                out.append(f"Draft a blog post for \"{m['keyword']}\" "
                           "(seo.draft_blog_post)")
    seen: list[str] = []
    for a in out:
        if a not in seen:
            seen.append(a)
    return seen[:n]


async def build(slug: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    since = now - WEEK
    search, s_note = _search(slug, await store.recent_evidence("search_console", 60))
    vit, v_note = _vitals(slug, await store.recent_evidence("core_web_vitals", 5))
    ranks = [{"keyword": clean(m.get("keyword")), "position": m.get("position"),
              "prior": m.get("prior"), "read_at": (_at(m.get("at")) or now).isoformat()}
             for m in await store.rank_moves(slug, since)]
    fixes = [{"what": clean(f.get("what"), 120),
              "at": (_at(f.get("at")) or now).isoformat()}
             for f in await store.fixes_done(slug, since)]
    r: dict[str, Any] = {"slug": slug, "week": week_of(now), "built_at": now.isoformat(),
                         "search": search, "search_note": s_note, "vitals": vit,
                         "vitals_note": v_note, "ranks": ranks, "fixes": fixes}
    r["next_actions"] = next_actions(r)
    return r


def _fmt(value: Any, spec: str = "") -> str:
    if value is None:
        return "not measured"
    try:
        return format(value, spec)
    except (TypeError, ValueError):
        return str(value)


def render_text(r: dict[str, Any], client: str) -> str:
    """Plain text for a client email draft; every figure with its read time."""
    lines = [f"SEO report for {clean(client)}, week {r['week']}", ""]
    sc = r.get("search")
    if sc:
        rng = sc.get("range") or {}
        lines.append(f"Google Search, {rng.get('start')} to {rng.get('end')} "
                     f"({_stamp(sc.get('read_at'))}):")
        lines.append(f"- {_fmt(sc.get('clicks'))} clicks, "
                     f"{_fmt(sc.get('impressions'))} impressions, average position "
                     f"{_fmt(sc.get('position'))}")
        if sc.get("clicks_before") is not None:
            lines.append(f"- a week earlier: {sc['clicks_before']} clicks, average "
                         f"position {_fmt(sc.get('position_before'))} "
                         f"({_stamp(sc.get('before_read_at'))})")
        for q in sc.get("top_queries") or []:
            lines.append(f"- \"{q['query']}\": {q.get('clicks')} clicks, position "
                         f"{q.get('position')}")
    else:
        lines.append(r.get("search_note") or "Search Console: not measured")
    lines.append("")
    if r.get("ranks"):
        lines.append("Rankings we track:")
        for m in r["ranks"]:
            now_ = f"#{m['position']}" if m.get("position") else "not in the results read"
            was = f" (was #{m['prior']})" if m.get("prior") else ""
            lines.append(f"- {m['keyword']}: {now_}{was} ({_stamp(m.get('read_at'))})")
        lines.append("")
    vit = r.get("vitals")
    if vit:
        lines.append(f"Speed, Google PageSpeed ({_stamp(vit.get('read_at'))}):")
        for strategy in ("mobile", "desktop"):
            m = vit.get(strategy) or {}
            lab = m.get("lab") or {}
            lcp = lab.get("lcp_ms")
            lines.append(f"- {strategy}: score {_fmt(m.get('score'))}, largest paint "
                         + (f"{lcp / 1000:.1f} s" if isinstance(lcp, (int, float))
                            else "not measured")
                         + f", layout shift {_fmt(lab.get('cls'))}")
    else:
        lines.append(r.get("vitals_note") or "Core Web Vitals: not measured")
    lines.append("")
    if r.get("fixes"):
        lines.append("Done this week:")
        lines += [f"- {f['what']}" for f in r["fixes"]]
    else:
        lines.append("Done this week: no on-page change or post was published.")
    lines.append("")
    lines.append("Next:")
    lines += [f"{i}. {a}" for i, a in enumerate(r.get("next_actions") or [], 1)]
    if not r.get("next_actions"):
        lines.append("Nothing urgent from the readings we have.")
    return "\n".join(lines).strip() + "\n"


# --------------------------------------------------------------- actions

async def weekly_report_dry(slug: str) -> ActionResult:
    site = await store.site(str(slug))
    if site is None:
        return _refused(f"no site {slug!r}")
    return ActionResult(True, f"would build and store the weekly SEO report for {slug} "
                              "from the recorded readings (no outside call)")


async def weekly_report(slug: str) -> ActionResult:
    site = await store.site(str(slug))
    if site is None:
        return _refused(f"no site {slug!r}")
    r = await build(str(slug))
    text = render_text(r, str(site.get("client") or slug))
    rid = await store.insert_report(str(slug), CLIENT_KIND, r["week"], r, text)
    return ActionResult(True, f"SEO report {r['week']} for {slug} stored "
                              f"({len(r['next_actions'])} next action(s))",
                        evidence={"report_id": rid, "week": r["week"], "text": text},
                        rollback_token=str(rid))


async def withdraw_report(token: str) -> ActionResult:
    if not str(token).isdigit():
        return _refused("not a report id")
    await store.withdraw_report(int(token))
    return ActionResult(True, f"report {token} withdrawn")


async def _client_report(slug: str) -> tuple[dict[str, Any] | None, dict | None, str]:
    if slug in store.OWN:
        return None, None, f"{slug} is our own store, not a client: no client email"
    site = await store.site(str(slug))
    if site is None:
        return None, None, f"no site {slug!r}"
    rep = await store.latest_report(str(slug), CLIENT_KIND)
    if rep is None:
        return site, None, f"no SEO report for {slug} yet; seo.weekly_report first"
    return site, rep, ""


async def draft_report_email_dry(slug: str) -> ActionResult:
    _site, rep, why = await _client_report(str(slug))
    if rep is None:
        return _refused(why)
    return ActionResult(True, f"would save report {rep['week']} for {slug} as an email "
                              "draft (draft only; this action never sends)")


async def draft_report_email(slug: str) -> ActionResult:
    site, rep, why = await _client_report(str(slug))
    if rep is None or site is None:
        return _refused(why)
    client = clean(site.get("client") or slug)
    title = f"Your website's search report, week {rep['week']}"
    body = (f"Hi {client},\n\nHere is this week's search report for "
            f"{clean(site.get('domain'), 120)}.\n\n{rep['text']}\nMiro\nMapfront\n")
    did = await store.insert_email_draft(str(slug), str(rep["week"]), title, body)
    return ActionResult(True, f"email draft {did} for {slug} saved (not sent)",
                        evidence={"draft_id": did, "title": title, "text": body},
                        rollback_token=str(did))


async def drop_report_email(token: str) -> ActionResult:
    if not str(token).isdigit():
        return _refused("not a draft id")
    await store.drop_email_draft(int(token))
    return ActionResult(True, f"email draft {token} dropped")


# ----------------------------------------------------------- Academy gaps

def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def covered(keyword: str, titles: list[str]) -> bool:
    words = set(_TOKEN.findall(keyword.lower()))
    return bool(words) and any(words <= set(_TOKEN.findall(t.lower())) for t in titles)


def title_idea(keyword: str) -> str:
    kw = clean(keyword, 60)
    return f"{kw[:1].upper()}{kw[1:]}: Step by Step in 10 Minutes"


async def _candidates(history: list[Any]) -> list[str]:
    slug = store.ACADEMY["slug"]
    out: list[str] = []
    s = next((x for ev in history if (x := _site_in(ev, slug)) and x.get("totals")),
             None)
    for q in ((s or {}).get("top_queries") or []) + ((s or {}).get("striking") or []):
        out.append(clean(q.get("query")))
    out += [clean(k) for k in await store.keywords(slug, 5)]
    seen: list[str] = []
    for k in out:
        if k and k.lower() not in {x.lower() for x in seen}:
            seen.append(k)
    return seen[:GAP_QUERIES]


async def keyword_gaps_dry() -> ActionResult:
    return ActionResult(True, f"would search up to {GAP_QUERIES} keyword(s) with the "
                              "free provider and store the Academy gaps")


async def keyword_gaps() -> ActionResult:
    history = await store.recent_evidence("search_console", 60)
    kws = await _candidates(history)
    titles = [str(t) for t in await store.channel_titles()]
    own = _host(f"https://{store.academy()['domain']}/")
    gaps: list[dict[str, Any]] = []
    for kw in kws:
        hits, why = await _scrape.search(kw, agent="seo", limit=10)
        now = datetime.now(UTC).isoformat()
        if why is not None:
            gaps.append({"keyword": kw, "read_at": now, "store_position": None,
                         "video_in_results": None, "video_gap": None,
                         "title_idea": None, "note": f"not measured: {why}"})
            continue
        hosts = [_host(str(h.get("url") or "")) for h in hits]
        pos = next((i for i, h in enumerate(hosts, 1) if h == own), None)
        video = any(h in {"youtube.com", "m.youtube.com", "youtu.be"} for h in hosts)
        gap = video and not covered(kw, titles)
        gaps.append({"keyword": kw, "read_at": now, "store_position": pos,
                     "video_in_results": video, "video_gap": gap,
                     "title_idea": title_idea(kw) if gap else None,
                     "note": "" if pos else f"the store is not in the top {len(hits)}"})
    week = week_of(datetime.now(UTC))
    body = {"week": week, "gaps": gaps, "titles_compared": len(titles)}
    lines = [f"{g['keyword']}: store "
             + (f"#{g['store_position']}" if g["store_position"] else
                g["note"] or "not in the results")
             + (f"; video idea: {g['title_idea']}" if g.get("title_idea") else "")
             for g in gaps]
    text = "\n".join(lines) or "no keyword to compare yet"
    rid = await store.insert_report(store.ACADEMY["slug"], GAPS_KIND, week, body, text)
    store_gaps = sum(1 for g in gaps if g["store_position"] is None)
    video_gaps = sum(1 for g in gaps if g["video_gap"])
    return ActionResult(True, f"Academy gaps {week}: {len(gaps)} keyword(s), "
                              f"{store_gaps} without the store, {video_gaps} video "
                              "idea(s)", evidence={"report_id": rid, "text": text},
                        rollback_token=str(rid))
