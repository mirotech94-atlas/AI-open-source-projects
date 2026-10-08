"""Concrete tool implementations.

Read tools run immediately and change nothing. Write tools are declared here as
thin references to catalog actions, so they inherit that action's tier — an
agent calling `instagram_post` gets an approval card, not a post.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import ssl
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ..kernel.permissions import wrap_untrusted
from ..kernel.private_data import allowed_rows as private_rows
from ..kernel.private_data import refusal as private_refusal
from ..kernel.registry import REGISTRY
from ..kernel.state import fetch_all
from ..kernel.tools import Tool, register
from ..modules._netguard import get_public
from ..modules.calendar.booking import free_slots_tool as calendar_free_slots
from ..modules.calendar.guard import alternatives_tool as calendar_alternatives

STR = {"type": "string"}
INT = {"type": "integer"}


def _schema(**props) -> dict[str, Any]:
    required = [k for k, v in props.items() if not v.pop("_optional", False)]
    return {"type": "object", "properties": props, "required": required}


# ------------------------------------------------------------ data / recall

# Whitelist, not sanitisation. An agent composing SQL is a liability; an agent
# choosing from named queries is not.
NAMED_QUERIES: dict[str, str] = {
    "latest_by_check": """
        SELECT p.name AS property, c.name AS check, o.at, o.health, o.value,
               o.unit, o.detail
        FROM observations o
        JOIN checks c ON c.id = o.check_id
        JOIN properties p ON p.id = c.property_id
        WHERE p.name = $1
        ORDER BY o.at DESC LIMIT $2""",
    "check_history": """
        SELECT o.at, o.value, o.health, o.detail
        FROM observations o
        JOIN checks c ON c.id = o.check_id
        JOIN properties p ON p.id = c.property_id
        WHERE p.name = $1 AND c.name = $2 AND o.at > now() - ($3 || ' hours')::interval
        ORDER BY o.at DESC LIMIT 500""",
    "open_incidents": """
        SELECT i.id, p.name AS property, i.opened_at, i.severity, i.title, i.summary
        FROM incidents i LEFT JOIN properties p ON p.id = i.property_id
        WHERE i.resolved_at IS NULL ORDER BY i.severity DESC, i.opened_at""",
    "recent_proposals": """
        SELECT id, created_at, action, tier, status, reason, decided_via
        FROM proposals ORDER BY created_at DESC LIMIT $1""",
    "cross_property_window": """
        SELECT p.name AS property, c.name AS check, o.at, o.health, o.detail
        FROM observations o
        JOIN checks c ON c.id = o.check_id
        JOIN properties p ON p.id = c.property_id
        WHERE o.health IN ('degraded','failing')
          AND o.at BETWEEN $1::timestamptz - interval '10 minutes'
                       AND $1::timestamptz + interval '10 minutes'
        ORDER BY o.at""",
    "property_summary": """
        SELECT p.name AS property, c.name AS check,
               (SELECT health FROM observations WHERE check_id = c.id
                ORDER BY at DESC LIMIT 1) AS health,
               (SELECT value FROM observations WHERE check_id = c.id
                ORDER BY at DESC LIMIT 1) AS value,
               (SELECT at FROM observations WHERE check_id = c.id
                ORDER BY at DESC LIMIT 1) AS measured_at
        FROM checks c JOIN properties p ON p.id = c.property_id
        WHERE c.enabled ORDER BY p.name, c.name""",
}


async def sql_query(query: str, params: list | None = None) -> Any:
    sql = NAMED_QUERIES.get(query)
    if sql is None:
        return {"error": f"unknown query {query!r}",
                "available": sorted(NAMED_QUERIES)}
    if (query in ("latest_by_check", "check_history") and params
            and (refused := private_refusal(str(params[0]))) is not None):
        return refused
    rows = await fetch_all(sql, *(params or []))
    # Cross-property queries carry observation detail: a private module's
    # rows are left out for an agent that is not local (kernel/private_data).
    return private_rows([dict(r) for r in rows])


async def read_metrics(property: str, limit: int = 30) -> Any:
    if (refused := private_refusal(property)) is not None:
        return refused
    rows = await fetch_all(NAMED_QUERIES["latest_by_check"], property, limit)
    if not rows:
        return {"error": f"no observations recorded for {property!r}"}
    return {"as_of": max(r["at"] for r in rows).isoformat(),
            "rows": [dict(r) for r in rows]}


async def read_history(property: str, check: str, hours: int = 24) -> Any:
    if (refused := private_refusal(property)) is not None:
        return refused
    rows = await fetch_all(NAMED_QUERIES["check_history"], property, check, str(hours))
    if not rows:
        return {"error": f"no history for {property}.{check} in the last {hours}h"}
    values = [r["value"] for r in rows if r["value"] is not None]
    return {
        "points": len(rows),
        "window_hours": hours,
        "latest": dict(rows[0]),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "mean": sum(values) / len(values) if values else None,
        "thin_baseline": len(values) < 5,
    }


async def module_status(module: str) -> Any:
    """The latest observation for every check on one module, each with the
    time it was measured. What a business agent calls before saying anything
    about its own numbers — never a figure without an `observed_at`.

    A private module (mail, messages, ...) is refused to any agent that is
    not a local-model one; its details can quote his messages. Health is
    as it stands now: a stale OK reads unknown, as on the board."""
    if (refused := private_refusal(module)) is not None:
        return refused
    from ..kernel.freshness import row_health
    from ..kernel.state import fetch_all as _fetch_all  # respects monkeypatching
    rows = await _fetch_all(
        """SELECT c.name AS check, o.health, o.value, o.unit, o.detail,
                  o.at AS observed_at, c.interval_s
           FROM checks c
           JOIN properties p ON p.id = c.property_id
           LEFT JOIN LATERAL (
               SELECT health, value, unit, detail, at FROM observations
               WHERE check_id = c.id ORDER BY at DESC LIMIT 1
           ) o ON TRUE
           WHERE p.name = $1
           ORDER BY c.name""",
        module,
    )
    if not rows:
        # Name the real modules, so a model that guessed ("Miro", a person's
        # name, 2026-09-28 commerce shift) corrects itself on the next call.
        known = await _fetch_all(
            "SELECT DISTINCT p.name FROM properties p "
            "JOIN checks c ON c.property_id = p.id ORDER BY p.name")
        names = ", ".join(str(r["name"]) for r in known)
        return {"error": f"no checks recorded for module {module!r}"
                         + (f"; modules with checks: {names}" if names else "")}
    return {
        "module": module,
        "checks": [{
            "check": r["check"],
            # A check with no observation yet has not been measured, and a
            # reading older than three intervals is not a reading of now:
            # both unknown, never "ok" — the board's rule, here too.
            "health": row_health(r["health"], r["observed_at"],
                                 r.get("interval_s")),
            "value": r["value"],
            "unit": r["unit"],
            "detail": r["detail"] or "",
            "observed_at": r["observed_at"].isoformat() if r["observed_at"] else None,
        } for r in rows],
    }


async def module_brief(module: str) -> Any:
    """A module's own one-liner, plus whatever it has for the next 72 hours
    and its top 5 inbox items — only for modules that define those; most
    don't, and this never invents an empty one to fill the gap."""
    if (refused := private_refusal(module)) is not None:
        return refused
    mod = REGISTRY.modules.get(module)
    if mod is None:
        return {"error": f"no such module {module!r}",
                "available": sorted(REGISTRY.modules)}
    out: dict[str, Any] = {"module": module, "brief": mod.brief()}
    if hasattr(mod, "agenda"):
        items = await mod.agenda(timedelta(hours=72))
        out["agenda"] = [{
            "when": i.when.isoformat(), "title": i.title, "kind": i.kind,
            "detail": i.detail,
        } for i in items]
    if hasattr(mod, "inbox"):
        # An inbox that could not be read is named, never an empty list, and
        # never takes the brief and agenda down with it.
        try:
            items = await mod.inbox(5)
        except Exception as exc:                        # noqa: BLE001
            from ..modules._http import explain
            out["inbox"] = None
            out["inbox_unreadable"] = explain(exc)
        else:
            out["inbox"] = [{
                "at": i.at.isoformat(), "sender": i.sender, "subject": i.subject,
                "urgency": int(i.urgency), "why": i.why,
            } for i in items]
    return out


async def revenue_rollup() -> Any:
    """One number per business, each with the time it was measured. Never
    synthesised — a business with no recent observation is reported as stale."""
    rows = await fetch_all("""
        SELECT p.name AS property, c.name AS check,
               (SELECT value FROM observations WHERE check_id=c.id ORDER BY at DESC LIMIT 1) AS value,
               (SELECT at FROM observations WHERE check_id=c.id ORDER BY at DESC LIMIT 1) AS at
        FROM checks c JOIN properties p ON p.id=c.property_id
        WHERE c.name LIKE '%mrr%' OR c.name LIKE '%earnings%'
           OR c.name LIKE '%revenue%' OR c.name LIKE '%commission%'""")
    now = datetime.now(UTC)
    out = []
    for r in rows:
        stale = r["at"] is None or (now - r["at"]) > timedelta(hours=12)
        out.append({"property": r["property"], "metric": r["check"],
                    "value": r["value"],
                    "measured_at": r["at"].isoformat() if r["at"] else None,
                    "stale": stale})
    return {"lines": out,
            "note": "values marked stale are older than 12h; do not present "
                    "them as current without saying so"}


async def read_logs(service: str, lines: int = 100) -> Any:
    """Container logs from the local Docker socket, read-only."""
    path = "/var/run/docker.sock"
    if not os.path.exists(path):
        return {"error": "docker socket not available in this container"}
    transport = httpx.AsyncHTTPTransport(uds=path)
    async with httpx.AsyncClient(transport=transport, base_url="http://docker",
                                 timeout=20) as c:
        r = await c.get(f"/containers/{service}/logs",
                        params={"stdout": 1, "stderr": 1, "tail": lines})
        if r.status_code != 200:
            return {"error": f"docker returned {r.status_code}"}
    text = r.content.decode("utf-8", "replace")
    return wrap_untrusted(text[-12000:], f"logs:{service}")


async def audit_verify() -> Any:
    from ..kernel.audit import verify_chain
    ok, detail = await verify_chain()
    return {"intact": ok, "detail": detail}


async def messages_unanswered() -> Any:
    """Open Instagram messages waiting on a reply, oldest business
    first, each flagged whether a draft is already sitting there for
    `messages_reply` to send."""
    # Instagram only: rows left by a removed bridge stay stored
    # but are not something to reply to (`messages.PLATFORM`).
    rows = await fetch_all(
        """SELECT id, at, platform, sender, text, draft FROM messages_inbound
           WHERE replied_at IS NULL AND platform = 'instagram'
           ORDER BY at DESC LIMIT 20""")
    return [{"id": r["id"], "at": r["at"].isoformat(), "platform": r["platform"],
             "sender": r["sender"], "text": r["text"][:80],
             "status": "draft ready" if r["draft"] else "no draft yet"}
            for r in rows]


# ------------------------------------------------------------------ network

async def http_probe(url: str, expect_status: int = 200) -> Any:
    """Public addresses only, every redirect hop included (audit 2026-09-28):
    a URL the model was handed must never reach loopback, the mesh, the
    Docker bridge or a cloud metadata address."""
    from ..modules._http import explain
    from ..modules._netguard import get_public
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=False) as c:
            r, why = await get_public(c, url)
        if r is None:
            return {"error": f"not fetched: {why}", "ok": False}
        return {"status": r.status_code, "ms": round(r.elapsed.total_seconds() * 1000),
                "ok": r.status_code == expect_status, "bytes": len(r.content),
                "final_url": str(r.url)}
    except Exception as exc:                            # noqa: BLE001
        return {"error": explain(exc), "ok": False}


def _peer_cert(hostname: str, port: int) -> dict:
    """The blocking half: a TCP connect and a TLS handshake, up to 10 s."""
    ctx = ssl.create_default_context()
    with (socket.create_connection((hostname, port), timeout=10) as sock,
          ctx.wrap_socket(sock, server_hostname=hostname) as tls):
        return tls.getpeercert()


async def cert_check(hostname: str, port: int = 443) -> Any:
    # Public addresses only (plan S03): the model can name any host and
    # port, and a TLS connect to loopback, the mesh or the Docker bridge is
    # a port scan of ATLAS's own neighbourhood. Same guard as http_probe.
    from ..modules._netguard import assert_public
    why = await assert_public(f"https://{hostname}:{int(port)}/")
    if why is not None:
        return {"error": f"not checked: {why}"}
    # In a worker thread: the flycheap and guardian CHECKS call this, and a
    # slow host must not freeze the scheduler's event loop for ten seconds.
    try:
        cert = await asyncio.to_thread(_peer_cert, hostname, port)
        # OpenSSL reports notAfter in GMT. Parse it as UTC and compare against
        # an aware now(), rather than two naive datetimes that happen to line up.
        expires = datetime.strptime(
            cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
        days = (expires - datetime.now(UTC)).days
        return {"host": hostname, "expires": expires.isoformat(),
                "days_left": days, "issuer": dict(x[0] for x in cert["issuer"]),
                "urgent": days < 21}
    except Exception as exc:                            # noqa: BLE001
        return {"error": str(exc)}


async def dns_check(hostname: str) -> Any:
    try:
        # getaddrinfo blocks on the resolver; off the event loop.
        infos = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
        return {"host": hostname,
                "addresses": sorted({i[4][0] for i in infos})}
    except Exception as exc:                            # noqa: BLE001
        return {"error": str(exc)}


async def hibp_check(account: str) -> Any:
    key = os.getenv("HIBP_API_KEY")
    if not key:
        return {"error": "HIBP_API_KEY not configured"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(
            f"https://haveibeenpwned.com/api/v3/breachedaccount/{account}",
            headers={"hibp-api-key": key, "user-agent": "ATLAS"},
            params={"truncateResponse": "false"})
    if r.status_code == 404:
        return {"account": account, "breaches": []}
    if r.status_code != 200:
        return {"error": f"HIBP returned {r.status_code}"}
    return {"account": account,
            "breaches": [{"name": b["Name"], "date": b["BreachDate"],
                          "data": b["DataClasses"]} for b in r.json()]}


async def web_fetch(url: str) -> Any:
    # Redirects by hand, each hop asked `assert_public` first: the URL is
    # agent- or page-chosen and ATLAS's own API trusts loopback and the mesh.
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as c:
        r, refused = await get_public(c, url, headers={"User-Agent": "ATLAS/1.0"})
        if refused is not None:
            return {"error": wrap_untrusted(refused, url)}
        r.raise_for_status()
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return wrap_untrusted(text[:14000], url)


async def web_search(query: str, limit: int = 5) -> Any:
    key = os.getenv("BRAVE_API_KEY")
    if not key:
        return {"error": "no search provider configured (BRAVE_API_KEY)"}
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get("https://api.search.brave.com/res/v1/web/search",
                        headers={"X-Subscription-Token": key,
                                 "Accept": "application/json"},
                        params={"q": query, "count": limit})
        r.raise_for_status()
    results = [{"title": w["title"], "url": w["url"],
                "snippet": w.get("description", "")}
               for w in r.json().get("web", {}).get("results", [])[:limit]]
    return wrap_untrusted(json.dumps(results, indent=2), f"search:{query}")


# ------------------------------------------------------------------- browser

# Fields a page or a search engine chose: a URL, or a reason that quotes one
# (a redirect target, Playwright's error line). Title and text arrive wrapped
# from the module; these are wrapped here, at the tool boundary (invariant 4).
_PAGE_CHOSEN = ("url", "error", "denied", "why", "not_opened")


def _wrap_chosen(value: Any, source: str) -> Any:
    if isinstance(value, list):
        return [_wrap_chosen(v, source) for v in value]
    if isinstance(value, dict):
        return {k: (wrap_untrusted(v, source) if k in _PAGE_CHOSEN and isinstance(v, str)
                    else _wrap_chosen(v, source)) for k, v in value.items()}
    return value


def _asking_agent() -> str | None:
    from ..kernel.tools import CURRENT_AGENT
    return CURRENT_AGENT.get()


async def browser_open(url: str) -> Any:
    """One page through the server browser: title and text wrapped as
    untrusted, a screenshot path, the status, and when it was read."""
    from ..modules.browser import session
    page, reason = await session.open(url, agent=_asking_agent())
    return _wrap_chosen({"error": reason} if page is None else page, "browser")


async def browser_search(query: str, limit: int = 5) -> Any:
    """Web search through Brave or DuckDuckGo — never Google's results page."""
    from ..modules.browser import session
    hits, reason = await session.search(query, limit)
    if reason is not None:
        return _wrap_chosen({"error": reason}, f"search:{query}")
    return {"query": query, "read_at": datetime.now(UTC).isoformat(),
            "hits": _wrap_chosen(hits, f"search:{query}")}


async def browser_find_business(name: str, area: str) -> Any:
    """Search for a local business and open its own site, skipping directories.
    A denied likely site is reported, never swapped for the next result."""
    from ..modules.browser import session
    page, reason = await session.find_business(name, area, agent=_asking_agent())
    return _wrap_chosen({"error": reason} if page is None else page, "browser")


# ------------------------------------------------------------------- youtube

async def youtube_stats(channel: str) -> Any:
    """Subscriber/view/video counts for one channel, read fresh — never a
    number without saying when it was read."""
    from ..modules.youtube import api
    data, reason = await api.channel_stats(channel)
    if reason is not None:
        return {"error": reason}
    return {**data, "read_at": data["read_at"].isoformat()}


async def youtube_videos(channel: str, limit: int = 25) -> Any:
    """Recent videos and their stats for one channel. A missing statistic
    (likes/comments disabled, a hidden count) comes back as None, not 0.
    Every video carries the same `read_at` (the whole batch was read
    together), also surfaced at the top level, matching `youtube_stats`."""
    from ..modules.youtube import api
    videos, reason = await api.videos(channel, limit)
    if reason is not None:
        return {"error": reason}
    read_at = videos[0]["read_at"] if videos else datetime.now(UTC)
    return {
        "channel": channel,
        "read_at": read_at.isoformat(),
        "videos": [{**v, "read_at": v["read_at"].isoformat()} for v in videos],
    }


# ----------------------------------------------------------------- marketing

async def playbook_read(business: str, limit: int = 12) -> Any:
    """What previous experiments actually measured, best first. An empty
    playbook is reported as empty — not as "nothing worked"."""
    from ..kernel.marketing import playbook
    lessons = await playbook.read(business, limit=limit)
    if not lessons:
        return {"business": business, "lessons": [],
                "note": "nothing has been measured for this business yet"}
    return {
        "business": business,
        "lessons": [{"lesson": lesson.content, "platform": lesson.platform,
                     "variant": lesson.variant, "outcome": lesson.outcome,
                     "at": lesson.at.isoformat() if lesson.at else None,
                     "rank": lesson.score}
                    for lesson in lessons],
    }


async def course_channels() -> Any:
    """Where a course post can go, read now: the Postiz text channels that are
    connected (never a clips account), the two hand-off channels, and what is
    not a channel and why."""
    from ..modules.course import channels
    return await channels.listing()


async def course_posts() -> Any:
    """This week's course posts and the weekly cap, read now."""
    from ..modules.course import posts
    return await posts.week()


async def course_catalog() -> Any:
    """The products Nora may market (no prices: those are read live at every
    draft), which are due next by rotation, and the playbook as quoted data."""
    from ..modules.course import catalog, posts
    out: dict[str, Any] = {"products": catalog.describe(),
                           "playbook": catalog.playbook()}
    sellable = await posts.sellable_now()
    eligible = [k for k, why in sellable.items() if why is None]
    out["not_marketable_now"] = {k: why for k, why in sellable.items() if why}
    try:
        counts = await posts.week_counts()
    except Exception as exc:                            # noqa: BLE001
        out["rotation"] = {"status": "unknown",
                           "why": f"this week's posts could not be counted: "
                                  f"{type(exc).__name__}"}
        return out
    out["rotation"] = {"status": "read", "this_week": counts,
                       "due_next": posts.due_products(counts, eligible)}
    return out


async def experiments_list(status: str = "open") -> Any:
    """Experiments by status: open (no outcome yet), due (posted and past the
    48-hour window), or closed (measured)."""
    clauses = {
        "open": "outcome IS NULL",
        "due": ("outcome IS NULL AND posted_ref IS NOT NULL "
                "AND measure_at IS NOT NULL AND measure_at <= now()"),
        "closed": "outcome IS NOT NULL",
    }
    where = clauses.get(status)
    if where is None:
        return {"error": f"unknown status {status!r}",
                "available": sorted(clauses)}
    rows = await fetch_all(
        f"""SELECT id, at, business, platform, hypothesis, variant, metric,
                   baseline, observed, measured_at, outcome, lesson
            FROM marketing_experiments WHERE {where}
            ORDER BY at DESC LIMIT 25""")
    return {"status": status, "experiments": [dict(r) for r in rows]}


async def monetization_list(business: str) -> Any:
    """Recorded monetization ideas and the measured facts behind each one."""
    rows = await fetch_all(
        """SELECT id, at, idea, mechanism, evidence, requires, est_note,
                  score, status
           FROM monetization_ideas WHERE business = $1
           ORDER BY score DESC NULLS LAST, at DESC LIMIT 25""", business)
    if not rows:
        return {"business": business, "ideas": [],
                "note": "no idea has been recorded for this business yet"}
    return {"business": business, "ideas": [dict(r) for r in rows]}


# ------------------------------------------------------------ vault recall

async def vault_recall(query: str, limit: int = 8) -> Any:
    """Recall over his documents and reading (`vault`, `library`) — the
    same recall `school.draft_work` uses for course material. Documents were
    ingested from outside, so what comes back is wrapped as untrusted.

    Without embeddings recall is keyword-only, and this says so: "nothing
    matches" from a keyword search is not "nothing is there"."""
    from ..kernel.memory import degraded_note, recall
    memories = await recall(query, ["vault", "library"],
                            limit=max(1, min(int(limit), 20)))
    degraded = getattr(memories, "degraded", None)
    note = degraded_note(degraded) if degraded else None
    if not memories:
        said = "nothing in vault or library matches"
        if note:
            said = f"nothing in vault or library matches by keyword; {note}"
        out: dict[str, Any] = {"query": query, "count": 0, "memories": said}
    else:
        lines = "\n".join(
            f"[{m.namespace}] {m.content}" + (f"  ({m.at:%Y-%m-%d})" if m.at else "")
            for m in memories)
        out = {"query": query, "count": len(memories),
               "memories": wrap_untrusted(lines, "vault")}
    if note:
        out["search"] = note
    return out


# -------------------------------------------------------------------- school

async def school_upcoming(days: int = 7) -> Any:
    """Brightspace items due in the next `days`, read fresh from the
    calendar feed, each with the id school_draft_work takes."""
    from ..modules import school
    return await school.upcoming(days)


async def school_grades(days: int = 7) -> Any:
    """Grade-released notification emails from the last `days`."""
    from ..modules import school
    return await school.grades(days)


async def school_alerts(days: int = 7) -> Any:
    """News, alerts and new-assignment emails from the last `days`."""
    from ..modules import school
    return await school.alerts(days)


# ---------------------------------------------------------------- perplexity
#
# Research, reasoning and understanding from Perplexity, over its MCP server
# (`atlas.modules._perplexity`). A source, never an oracle: the answer comes
# back wrapped as untrusted data with its Sources and the time it was read,
# and a question naming one of ATLAS's own modules, businesses, agents or
# checks is refused before the call — those go to the estate. Read tools
# only; nothing here reaches the executor.

# The agent loops clip a tool result at 8,000 characters of JSON. The answer
# text is capped below that before it is wrapped, and `render()` puts
# Sources and the read time first, so the clip can never cut the wrapper's
# closing tag, its trailer, the citations or the read time.
PERPLEXITY_TOOL_CHARS = 5000


async def _perplexity(tool: str, text: str) -> Any:
    from ..modules import _perplexity
    answer, reason = await _perplexity.call(tool, text, caller="tool",
                                            max_chars=PERPLEXITY_TOOL_CHARS)
    if answer is None:
        return {"error": reason}
    return answer.render()


async def perplexity_ask(question: str) -> Any:
    return await _perplexity("perplexity_ask", question)


async def perplexity_reason(question: str) -> Any:
    return await _perplexity("perplexity_reason", question)


async def perplexity_research(topic: str) -> Any:
    return await _perplexity("perplexity_research", topic)


# ------------------------------------------------------------------ handoff

async def ask_specialist(agent: str, task: str) -> Any:
    """Concierge handoff. Returns the specialist's answer for relay."""
    from ..kernel.agents import ROSTER
    from ..kernel.harness import run_agent
    target = ROSTER.get(agent)
    if target is None:
        return {"error": f"no agent {agent!r}", "available": sorted(ROSTER)}
    result = await run_agent(target, task, remember_outcome=False)
    return {"agent": agent, "answer": result.text,
            "pending_approvals": result.pending_approvals}


async def mail_suggest_labels() -> Any:
    """Read-only: the classifier's category for every unread thread, so an
    agent can propose `mail_apply_labels` calls without ATLAS touching
    Gmail first."""
    from ..modules import mail
    return await mail.suggest_labels()


async def self_status() -> Any:
    """ATLAS's own state, from tables and process facts only. Every figure
    it could not read is null, never a number."""
    from ..kernel import selfmodel
    return await selfmodel.status()

# ------------------------------------------------------------------ guardian

async def guardian_incident(alert_id: str) -> Any:
    """Read-only incident analysis of one Guardian alert: the vectors, the
    operator classification, the smallest-first containment steps (each a
    Tier 2 proposal) and the persistence hunt. Everything in it came from
    the hub's sensors -- a beacon's domain is the attacker's choice -- so it
    reaches the agent wrapped as untrusted data."""
    from ..modules.guardian import incident
    plan, reason = await incident.analyse(alert_id)
    if plan is None:
        # The reason can carry the request path, and the path is the alert
        # key -- which names a domain the attacker chose.
        return {"error": wrap_untrusted(reason or "no plan", "guardian hub error")}
    body = (incident.narrate(plan) + "\n\n" + plan.say + "\n\n"
            + json.dumps(incident.as_dict(plan), indent=2, default=str))
    return wrap_untrusted(body, f"guardian hub, alert {alert_id}")


# --------------------------------------------------------------- registration

def register_all() -> None:
    reads = [
        Tool("sql_query", "Run one of the named, pre-written queries over the "
             "observation store. Call with no query name to see the list.",
             _schema(query=STR, params={"type": "array", "_optional": True}),
             read=sql_query),
        Tool("read_metrics", "Latest observation for every check on a property, "
             "with the time each was measured.",
             _schema(property=STR, limit={**INT, "_optional": True}),
             read=read_metrics),
        Tool("read_history", "Time series for one check, with min/max/mean and a "
             "flag when the baseline is too thin to trust.",
             _schema(property=STR, check=STR, hours={**INT, "_optional": True}),
             read=read_history),
        Tool("module_status", "Latest observation for every check on one module, "
             "each with when it was measured.",
             _schema(module=STR), read=module_status),
        Tool("module_brief", "A module's own one-line brief, plus its agenda and "
             "inbox where it defines them.",
             _schema(module=STR), read=module_brief),
        Tool("revenue_rollup", "Revenue and MRR lines across all businesses, each "
             "marked stale if older than 12 hours.", _schema(), read=revenue_rollup),
        Tool("read_logs", "Tail a service's container logs.",
             _schema(service=STR, lines={**INT, "_optional": True}), read=read_logs),
        Tool("audit_verify", "Verify the hash chain of the audit log.",
             _schema(), read=audit_verify),
        Tool("messages_unanswered", "Open Instagram messages waiting "
             "on a reply, each flagged whether a draft is ready to send.",
             _schema(), read=messages_unanswered),
        Tool("http_probe", "Fetch a URL and report status, latency and size.",
             _schema(url=STR, expect_status={**INT, "_optional": True}),
             read=http_probe),
        Tool("cert_check", "TLS certificate expiry for a hostname.",
             _schema(hostname=STR, port={**INT, "_optional": True}), read=cert_check),
        Tool("dns_check", "Resolve a hostname to its current addresses.",
             _schema(hostname=STR), read=dns_check),
        Tool("hibp_check", "Check an email address against known breaches.",
             _schema(account=STR), read=hibp_check),
        Tool("web_fetch", "Fetch a page and return its text as untrusted data.",
             _schema(url=STR), read=web_fetch),
        Tool("web_search", "Search the web.",
             _schema(query=STR, limit={**INT, "_optional": True}), read=web_search),
        Tool("browser_open", "Open a public web page in a headless browser and "
             "read it (text as untrusted data, plus a screenshot). Never signs in.",
             _schema(url=STR), read=browser_open),
        Tool("browser_search", "Search the web (Brave or DuckDuckGo) and list "
             "the results, each marked whether the browser may open it.",
             _schema(query=STR, limit={**INT, "_optional": True}),
             read=browser_search),
        Tool("browser_find_business", "Find a local business's own website "
             "by name and area and read it, skipping directories and maps.",
             _schema(name=STR, area=STR), read=browser_find_business),
        Tool("ask_specialist", "Hand a task to another agent and get its answer "
             "back for relay.", _schema(agent=STR, task=STR), read=ask_specialist),
        Tool("calendar_free_slots", "Candidate meeting slots clear of the "
             "calendar and any focus block.",
             _schema(days={**INT, "_optional": True},
                     minutes={**INT, "_optional": True}), read=calendar_free_slots),
        Tool("calendar_alternatives", "Three slots after a given time that "
             "clear every focus block.",
             _schema(start=STR, minutes={**INT, "_optional": True}),
             read=calendar_alternatives),
        Tool("youtube_stats", "Subscriber, view and video counts for a YouTube "
             "channel (flycheap or guardian), with the time it was read.",
             _schema(channel=STR), read=youtube_stats),
        Tool("youtube_videos", "Recent videos and their stats for a YouTube "
             "channel.",
             _schema(channel=STR, limit={**INT, "_optional": True}),
             read=youtube_videos),
        Tool("mail_suggest_labels", "The classifier's category for every "
             "unread thread, to propose labels from.", _schema(),
             read=mail_suggest_labels),
        Tool("playbook_read", "What previous marketing experiments measured "
             "for one business, ranked by recency and outcome.",
             _schema(business=STR, limit={**INT, "_optional": True}),
             read=playbook_read),
        Tool("experiments_list", "Marketing experiments by status: open, due "
             "(past their 48-hour window) or closed.",
             _schema(status={**STR, "_optional": True}), read=experiments_list),
        Tool("course_channels", "Where a course post can go right now: the "
             "connected Postiz text channels (clips accounts never), LinkedIn "
             "and the Shopify blog by hand, and what is not a channel.",
             _schema(), read=course_channels),
        Tool("course_posts", "This week's course posts, their status and the "
             "weekly cap.", _schema(), read=course_posts),
        Tool("course_catalog", "The products you may market (no others), their "
             "audiences, channels and languages, which are due next by fair "
             "rotation, and the organic playbook (quoted data).",
             _schema(), read=course_catalog),
        Tool("monetization_list", "Recorded monetization ideas for one "
             "business, with the measured facts behind each.",
             _schema(business=STR), read=monetization_list),
        Tool("self_status", "ATLAS's own state: uptime, deploy, errors since "
             "deploy, checks by health, memories, open lessons, proposals "
             "waiting, and whether Miro is home. Nulls are unknowns.",
             _schema(), read=self_status),

        Tool("guardian_incident", "Analyse one Guardian alert as an incident: "
             "vectors, operator, smallest-first containment steps and the "
             "persistence hunt, each with the time it was read.",
             _schema(alert_id=STR), read=guardian_incident),


        Tool("vault_recall", "Search his vault documents and reading log "
             "(vault, library) by meaning; by keyword when embeddings are "
             "unavailable, and the result says so.",
             _schema(query=STR, limit={**INT, "_optional": True}),
             read=vault_recall),
        Tool("school_upcoming", "Brightspace items due in the next N days "
             "(default 7), read fresh from the calendar feed, each with its "
             "id and the time it was read.",
             _schema(days={**INT, "_optional": True}), read=school_upcoming),
        Tool("school_grades", "Grade-released emails from Brightspace in the "
             "last N days (default 7).",
             _schema(days={**INT, "_optional": True}), read=school_grades),
        Tool("school_alerts", "Brightspace news, alerts and new-assignment "
             "emails in the last N days (default 7).",
             _schema(days={**INT, "_optional": True}), read=school_alerts),
        Tool("perplexity_ask", "Ask Perplexity a general question and get a "
             "cited answer (untrusted data, with Sources and read time). A "
             "question about the world, never about ATLAS's own businesses or "
             "numbers, and never a document, message or coursework text — "
             "emails and phone numbers are stripped before it leaves.",
             _schema(question=STR), read=perplexity_ask),
        Tool("perplexity_reason", "Ask Perplexity to reason through a hard "
             "question step by step, with Sources. Same rules as "
             "perplexity_ask: a question, not a document; never about "
             "ATLAS's own systems.",
             _schema(question=STR), read=perplexity_reason),
        Tool("perplexity_research", "Deep research on a topic from Perplexity "
             "(slow, minutes), with Sources. A topic, not a document; never "
             "about ATLAS's own systems. Counts against a monthly cap.",
             _schema(topic=STR), read=perplexity_research),
    ]
    for tool in reads:
        register(tool)

    # Write tools: each names a catalog action and inherits its tier.
    writes = [
        ("gitlab_issue", "File a GitLab issue about something you noticed.",
         "gitlab.open_issue",
         _schema(instance=STR, project=STR, title=STR,
                 body={**STR, "_optional": True})),
        ("instagram_post", ("Publish a feed post or Reel to Instagram. "
                            "content_kind says what it is: the main account "
                            "takes 'flycheap' or 'travel', the stories account "
                            "takes 'ai_story' only."),
         "instagram.publish",
         _schema(caption=STR, image_url=STR, kind={**STR, "_optional": True},
                 account={**STR, "_optional": True}, content_kind=STR)),
        ("instagram_hide_comment",
         "Hide a comment on one of his own posts (main or stories account).",
         "instagram.hide_comment", _schema(account=STR, comment_id=STR)),
        ("instagram_reply_comment", "Publicly reply to an Instagram comment.",
         "instagram.reply_comment",
         _schema(account=STR, comment_id=STR, text=STR)),
        ("tiktok_post", "Upload a video to TikTok drafts.",
         "tiktok.upload_draft",
         _schema(video_url=STR, title={**STR, "_optional": True})),
        ("stripe_retry", "Retry collection on a failed invoice.",
         "stripe.retry_invoice", _schema(business=STR, invoice_id=STR)),
        ("agency_send_outreach", "Send a drafted agency outreach email.",
         "agency.send_outreach", _schema(outreach_id=INT)),
        ("agency_deploy_site", "Deploy a built client site to its production host.",
         "agency.deploy_site", _schema(slug=STR)),
        ("calendar_offer_slots", "Email a third party a set of proposed meeting times.",
         "calendar.offer_slots", _schema(to=STR, subject=STR, slots=STR)),
        ("calendar_book_with", "Book a meeting with a third party and send the invite.",
         "calendar.book_with",
         _schema(to=STR, summary=STR, start=STR, minutes={**INT, "_optional": True})),
        ("calendar_decline_invite",
         "Decline an invite that lands in a focus block, with alternatives.",
         "calendar.decline_invite", _schema(event_id=STR, alternatives=STR)),
        ("research_report", "Research a question overnight and file a cited report.",
         "research.report", _schema(question=STR)),
        ("vault_log_reading", "Log a book, article or video and what was learned.",
         "vault.log_reading",
         _schema(kind=STR, title=STR, notes=STR, source={**STR, "_optional": True})),
        ("messages_reply",
         "Reply to an Instagram DM. Empty text sends the stored draft.",
         "messages.reply",
         _schema(inbound_id=INT, text={**STR, "_optional": True})),
        ("content_draft", "Draft a post, listing, deal or script from a source.",
         "content.draft", _schema(kind=STR, source=STR)),
        ("content_render_video",
         ("Render an approved story_script draft into a video via OpenArt, "
          "under the monthly credit cap."),
         "content.render_video", _schema(draft_id=INT)),
        ("mail_apply_labels", "Apply a Gmail label to one or more threads.",
         "mail.apply_labels", _schema(thread_ids=STR, label=STR)),
        ("nightshift_run", "Hand a build task to the overnight sandbox.",
         "nightshift.run", _schema(task=STR, repo=STR)),
        ("nightshift_merge", "Merge a finished overnight branch.",
         "nightshift.merge", _schema(job_id=INT)),
        ("purchasing_prepare_flight",
         "Search fares and prepare a checkout link. Never pays.",
         "purchasing.prepare_flight", _schema(origin=STR, destination=STR, date=STR)),
        ("purchasing_prepare_cart",
         "Find best prices and prepare a cart link. Never pays.",
         "purchasing.prepare_cart", _schema(items=STR)),
        ("browser_submit",
         ("Fill and submit a form on a public web page (selector=value;...). "
          "Never a login, a checkout or a password."),
         "browser.submit_form", _schema(url=STR, fields=STR)),
        ("youtube_upload",
         "Upload a video to a YouTube channel. It always lands private.",
         "youtube.upload",
         _schema(channel=STR, path=STR, title=STR, description=STR,
                 tags={"type": "array", "items": STR})),
        ("guardian_contain",
         ("Guardian's v1 API has no containment route: block_ip and "
          "isolate_host always refuse. Kept so the playbook can still show "
          "what containment cannot do. Disabling an account is credential "
          "revocation (Tier 3) and is never offered."),
         "guardian.contain", _schema(alert_id=STR, target=STR, kind=STR)),
        ("guardian_alert_op",
         ("Act on one Guardian alert: ack, snooze, resolve or reopen. "
          "Reversible: rollback reads the alert's own prior status first and "
          "restores it, never a guessed one. hours (1-168) is only for "
          "snooze."),
         "guardian.alert_op",
         _schema(alert_key=STR, op=STR, hours={**INT, "_optional": True})),
        ("guardian_rescan",
         ("Ask the Guardian sensor to scan now. Irreversible: a scan cannot "
          "be un-run."),
         "guardian.rescan", _schema(network=STR, alert_key=STR)),
        ("youtube_publish",
         "Change a YouTube video's privacy status. Going public is your word, every time.",
         "youtube.set_visibility",
         _schema(channel=STR, video_id=STR, privacy=STR,
                 experiment_id={**INT, "_optional": True})),
        ("youtube_draft_metadata",
         ("Draft a title, description and tags for a YouTube upload. Nothing "
          "reaches YouTube."),
         "youtube.draft_metadata", _schema(business=STR, source=STR)),
        ("experiment_propose",
         ("Record one marketing experiment and draft the post it would "
          "publish. Publishing stays a separate decision, yours."),
         "marketing.propose_experiment",
         _schema(business=STR, platform=STR, hypothesis=STR, variant=STR,
                 draft_kind=STR, draft_source=STR,
                 metric={**STR, "_optional": True},
                 baseline={"type": "number", "_optional": True})),
        ("course_propose",
         ("Propose one organic post as a measured experiment: one catalog "
          "product (course_catalog lists them and which are due), one channel, "
          "one of that product's lessons, one language (en; the operator also "
          "es, pt, ar). ATLAS reads the price live, drafts the post and raises "
          "its own card; Miro approves every post."),
         "course.propose",
         _schema(product=STR, channel=STR, lesson_id=STR, language=STR,
                 hypothesis=STR, variant={**STR, "_optional": True})),
        ("course_draft_post",
         ("Draft a course post again from a fresh price read, before it "
          "reaches a card."),
         "course.draft_post", _schema(post_id=INT)),
        ("monetization_propose",
         ("Record one monetization idea with the measured facts it rests on. "
          "It spends nothing."),
         "marketing.propose_idea",
         _schema(business=STR, idea=STR, mechanism=STR,
                 evidence={"type": "object"},
                 requires={**STR, "_optional": True},
                 est_note={**STR, "_optional": True},
                 score={"type": "number", "_optional": True})),
        ("school_draft_work",
         ("Draft one school item's work in its course's kind. He writes it up "
          "and submits; nothing reaches Brightspace."),
         "school.draft_work",
         _schema(item_id=INT, notes={**STR, "_optional": True})),
        ("school_set_course",
         ("Set how a course's work is drafted: handwritten, webwork, files or "
          "lab_report."),
         "school.set_course", _schema(code=STR, work_kind=STR)),
        # Catalog actions that had no tool (Task 17). Registered so the names
        # resolve; no agent holds them yet. `screening_book_slot` in
        # particular stays out of every scope: its one door is the screening
        # webhook through `unattended.run_tier1`, and an agent holding it
        # would be a second, unattended way onto his calendar.
        ("mail_draft_reply",
         "Save a reply into Gmail drafts for Miro to review. Nothing is sent.",
         "mail.draft_reply", _schema(thread_id=STR, body=STR)),
        ("mail_draft_nudge",
         ("Draft a check-in nudge on a follow-up that got no reply, into "
          "Gmail drafts. Nothing is sent."),
         "mail.draft_nudge", _schema(thread_id=STR)),
        ("screening_book_slot",
         ("Hold 30 minutes on his calendar for a screened caller, inside the "
          "pre-approved availability only."),
         "screening.book_slot", _schema(call_sid=STR, start=STR)),
    ]
    for name, desc, action, schema in writes:
        register(Tool(name, desc, schema, action=action))
