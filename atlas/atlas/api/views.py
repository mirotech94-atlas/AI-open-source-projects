"""Server-rendered pages.

Two things get texted to a phone and must open instantly, with no JavaScript
and no bundle to download over a mesh link on mobile data: the morning brief,
and a permalink to a single finding. Everything else is the SPA.

These pages are read-only. There is no form here that can change anything —
approving from a link would mean a GET that acts, and a link preview fetcher
would then be able to approve things.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..kernel.state import fetch_all, fetch_one
from . import auth, peers

log = logging.getLogger("atlas.views")

router = APIRouter()
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

HEALTH_LABEL = {
    "ok": "OK", "degraded": "DEGRADED", "failing": "FAILING",
    "unknown": "UNKNOWN",
}


def page(request: Request, template: str, context: dict | None = None,
         status_code: int = 200):
    """Render a page with the context every page needs.

    `generated_at` is added here rather than at each call site: the masthead
    shows it on every page, and a handler that forgets it produces a 500 in
    front of the owner on his phone.
    """
    return TEMPLATES.TemplateResponse(
        request, template,
        {"generated_at": datetime.now(UTC), **(context or {})},
        status_code=status_code)


def _require_session(request: Request):
    """Server-rendered pages redirect rather than returning 401 — these open
    from a texted link, so a JSON error would be useless on a phone."""
    session = auth.current(request)
    if session is None:
        raise HTTPException(
            status_code=307,
            headers={"Location": f"/login?next={request.url.path}"},
            detail="sign in")
    return session


@router.get("/brief", response_class=HTMLResponse)
async def brief_page(request: Request):
    _require_session(request)
    from ..kernel.brief import build
    built = await build()
    return TEMPLATES.TemplateResponse(request, "brief.html", {
        "brief": built,
        "generated_at": datetime.now(UTC),
    })


@router.get("/briefs", response_class=HTMLResponse)
async def brief_archive(request: Request):
    """Past briefs, by the day they covered."""
    _require_session(request)
    rows = await fetch_all(
        """SELECT (at AT TIME ZONE 'UTC')::date AS day,
                  count(*) AS findings,
                  count(*) FILTER (WHERE severity >= 2) AS serious
           FROM findings
           GROUP BY 1 ORDER BY 1 DESC LIMIT 60""")
    return TEMPLATES.TemplateResponse(request, "briefs.html", {
        "days": rows,
        "generated_at": datetime.now(UTC),
    })


@router.get("/findings/day/{day}", response_class=HTMLResponse)
async def findings_for_day(request: Request, day: str):
    """Findings recorded on one day. Server-rendered, for the same reason the
    brief is: it opens from a link on a phone."""
    _require_session(request)
    try:
        date.fromisoformat(day)
    except ValueError as exc:
        raise HTTPException(400, "day must be YYYY-MM-DD") from exc
    rows = await fetch_all(
        """SELECT id, at, check_key, property, severity, health, title, detail
           FROM findings
           WHERE (at AT TIME ZONE 'UTC')::date = $1::date
           ORDER BY severity DESC, at DESC""", day)
    return page(request, "day.html", {"day": day, "findings": rows})


@router.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request, kind: str = "", q: str = ""):
    """The archive of what ATLAS has sent.

    Server-rendered because this is the page whose link gets texted: it has to
    open on a phone with no JS and no bundle to fetch over a mesh link.
    """
    _require_session(request)
    rows = await fetch_all(
        """SELECT id, at, kind, title, delivered_via, left(body, 200) AS excerpt
           FROM reports
           WHERE ($1 = '' OR kind = $1)
             AND ($2 = '' OR body ILIKE '%' || $2 || '%')
           ORDER BY at DESC LIMIT 100""", kind, q)
    return page(request, "reports.html",
                {"reports": rows, "kind": kind, "q": q})


@router.get("/reports/{rid}", response_class=HTMLResponse)
async def report_page(request: Request, rid: int):
    """One report, in full. The thing a texted link actually points at."""
    _require_session(request)
    row = await fetch_one(
        """SELECT id, at, kind, title, body, delivered_via
           FROM reports WHERE id = $1""", rid)
    if row is None:
        raise HTTPException(404, "no such report")
    return page(request, "report.html", {"r": row})


@router.get("/f/{fid}", response_class=HTMLResponse)
async def finding_permalink(request: Request, fid: int):
    """A single finding, for texting. No JS, no bundle."""
    _require_session(request)
    row = await fetch_one(
        """SELECT f.*, p.status AS proposal_status, p.action AS proposal_action,
                  p.reason AS proposal_reason, p.dry_run, p.dry_run_ok
           FROM findings f LEFT JOIN proposals p ON p.id = f.proposal_id
           WHERE f.id = $1""", fid)
    if row is None:
        raise HTTPException(404, "no such finding")
    return TEMPLATES.TemplateResponse(request, "finding.html", {
        "f": row,
        "health_label": HEALTH_LABEL.get((row["health"] or "unknown").lower(),
                                         "UNKNOWN"),
        "generated_at": datetime.now(UTC),
    })


def safe_next(target: str | None) -> str:
    """Where to send the browser after login: a path on THIS site, or "/".

    `next` arrives in a link anyone can craft, and a redirect straight to it
    is an open redirect -- ATLAS's own login page vouching for a phishing
    site (audit #26). Only a same-site relative path passes: one leading "/"
    that is not "//" or "/\\" (both of which a browser reads as another
    host), and no control characters or whitespace, which browsers strip or
    fold before they parse the URL.
    """
    target = target or ""
    if (not target.startswith("/") or target.startswith(("//", "/\\"))
            or any(ord(ch) < 0x21 or ch == "\x7f" for ch in target)):
        return "/"
    return target


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, next: str = "/"):
    target = safe_next(next)
    if auth.current(request) is not None:
        return RedirectResponse(target, status_code=303)
    return page(request, "login.html", {
        "next": target, "configured": auth.configured(), "error": None})


@router.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    form = await request.form()
    target = safe_next(str(form.get("next") or "/"))
    client = peers.effective_client(request)
    if auth.rate_limited(client):
        return page(request, "login.html", {
            "next": target, "configured": auth.configured(),
            "error": "Too many attempts. Wait a few minutes."}, status_code=429)
    if not auth.verify_password(str(form.get("password") or ""), client):
        return page(request, "login.html", {
            "next": target, "configured": auth.configured(),
            "error": "Wrong password."}, status_code=401)
    response = RedirectResponse(target, status_code=303)
    auth.set_cookie(response, auth.issue(), request)
    return response


@router.get("/logout")
async def logout_page(request: Request):
    response = RedirectResponse("/login", status_code=303)
    auth.clear_cookie(response, request)
    return response
