"""ATLAS Academy delivery: the buyer's access page and their downloads.

    GET  /public/academy/access?session_id=cs_...   the page Stripe redirects to
    GET  /public/academy/file/<signed token>         one download
    POST /webhooks/stripe-academy                    Stripe's signed event
    POST /public/academy/review                      a verified buyer's review
    POST /public/academy/recover                     email + receipt -> library
    GET  /public/academy/library?t=<signed token>    every purchase of one buyer

Public, like /deliver. The checkout session id Stripe puts in the redirect
is unguessable, and it is only half the gate: every visit, and every
download, asks Stripe again (`academy.stripe_read`) and hands over files
only while the checkout is paid, not refunded, not disputed, and matches a
catalog price. Nothing is assumed paid: with the read key unset, or Stripe
unreachable, the page says exactly that.

The session id and the download token are secrets in the URL: Caddy does
not log /public/academy/*, and main.py's access-log filter redacts both.
Rate-limited per address (the X-Forwarded-For Caddy sets). The page is
served under atlas.example.com's strict CSP, so it is one HTML document with
inline styles and nothing else.

Nothing here writes to the outside world. The webhook reads and records,
and posts the purchase to the owner's Slack, like /webhooks/stripe-mapfront.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import mimetypes
import os
import re
import secrets
import time
from datetime import UTC, datetime
from html import escape
from typing import Annotated, Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from ..modules import store_origin
from ..modules._http import explain
from ..modules.academy import course_page, delivery, lesson_youtube, reviews, subscribers
from ..modules.academy.catalog import Catalog, CatalogError, load
from ..modules.academy.stripe_read import (
    Verdict,
    email_hash,
    find_by_receipt,
    normalise_email,
    normalise_receipt,
    read_session,
    session_for_payment_intent,
    valid_session_id,
)
from . import auth
from .peers import PerAddressLimiter, effective_client
from .stripe_mapfront import MAX_BODY, verify

router = APIRouter()
log = logging.getLogger("atlas.api.academy")

WEBHOOK_PATH = "/webhooks/stripe-academy"
WEBHOOK_SECRET_ENV = "ACADEMY_STRIPE_WEBHOOK_SECRET"
PAID_EVENTS = frozenset({"checkout.session.completed",
                         "checkout.session.async_payment_succeeded"})
# A refund or a dispute names the charge (and its payment), not the checkout:
# the payment intent is resolved to its checkout session, read fresh, and the
# purchase row updated (status, refunded amount).
CHARGE_EVENTS = frozenset({"charge.refunded", "charge.dispute.created",
                           "charge.dispute.closed"})

# Behind Caddy every visitor reaches the API from one address, so a limiter
# keyed by address is a store-wide limiter. Paid traffic is therefore limited
# per *verified* checkout session (the id inside a signed link, or one that
# matched the session-id pattern); only input that fails verification is
# counted in a shared bucket, and none of it reaches Stripe.
#
#   _pages           global ceiling for the access page, sized for the store
#   _pages_bad       malformed session ids (someone walking ids)
#   _session_pages   one buyer's opens of their access page
#   _files           links that fail verification (bad signature)
#   _session_files   one buyer's file and video-range requests
_SESSION_PAGES_LIMIT = 20
_SESSION_FILES_LIMIT = 300
_pages = PerAddressLimiter(600, 600.0)
_pages_bad = PerAddressLimiter(12, 600.0)
_session_pages = PerAddressLimiter(_SESSION_PAGES_LIMIT, 600.0)
_files = PerAddressLimiter(30, 600.0)
_session_files = PerAddressLimiter(_SESSION_FILES_LIMIT, 600.0)

# A confirmed payment is remembered this long, so playing and seeking a video
# does not ask Stripe for every chunk (the owner, 2026-11-14). A refund or dispute
# therefore stops a video at most this much later; only a paid verdict is
# ever remembered.
PAID_MEMORY_SECONDS = 120.0
_PAID_MEMORY_MAX = 2048
_paid_memory: dict[str, tuple[float, Verdict]] = {}


def _now() -> float:
    return time.monotonic()


def _clear_limiters() -> None:
    for lim in (_pages, _pages_bad, _session_pages, _files, _session_files):
        lim.clear()
    _paid_memory.clear()


def _remember_paid(v: Verdict) -> None:
    if not v.paid:
        _paid_memory.pop(v.session_id, None)
        return
    now = _now()
    if len(_paid_memory) >= _PAID_MEMORY_MAX:
        for k in [k for k, (t, _) in _paid_memory.items() if t <= now]:
            del _paid_memory[k]
        while len(_paid_memory) >= _PAID_MEMORY_MAX:
            del _paid_memory[min(_paid_memory, key=lambda k: _paid_memory[k][0])]
    _paid_memory[v.session_id] = (now + PAID_MEMORY_SECONDS, v)


async def _read_for_file(session_id: str, cat: Catalog) -> Verdict:
    """The verdict for a file request: a remembered paid one, else Stripe."""
    held = _paid_memory.get(session_id)
    if held is not None:
        if held[0] > _now():
            return held[1]
        del _paid_memory[session_id]
    v = await read_session(session_id, cat)
    _remember_paid(v)
    return v


NO_STORE = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}


def _catalog() -> Catalog | None:
    try:
        return load()
    except (CatalogError, OSError, ValueError) as exc:
        log.error("academy catalog unreadable: %s", exc)
        return None


# ------------------------------------------------------------------ page

_CSS = """
:root{--ink:#EEF2FA;--soft:#A9B4CC;--paper:#0A0D14;--card:#121826;--rule:#26304A;
--ultra:#5B8CFF;--deep:#7FA4FF;--off:#8A93A8;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:17px/1.55 system-ui,
-apple-system,"Segoe UI",sans-serif}
main{max-width:960px;margin:0 auto;padding:32px 16px 48px}
.mark{font-weight:800;letter-spacing:.02em;margin:0 0 28px}
.mark span{font-weight:400;color:var(--soft)}
h1{font-size:2rem;line-height:1.1;letter-spacing:-.02em;margin:0 0 12px}
h2{font-size:1.2rem;margin:28px 0 8px}
p{margin:0 0 14px}
.box{background:var(--card);border:1px solid var(--rule);border-radius:10px;
padding:16px;margin:16px 0}
video{display:block;width:100%;margin:8px 0;border-radius:8px;background:#000}
iframe.pdf{display:block;width:100%;height:78vh;min-height:480px;margin:10px 0;
border:1px solid var(--rule);border-radius:10px;background:#fff}
a.alt{display:inline-flex;align-items:center;min-height:48px;padding:10px 18px;margin:6px 8px 0 0;
border-radius:10px;border:2px solid var(--ultra);color:var(--ink);font-weight:700;
text-decoration:none}
a.alt:hover{background:var(--card)}
.review{background:var(--card);border:1px solid var(--rule);border-radius:10px;
padding:16px;margin:12px 0;display:grid;gap:10px}
.review label{display:grid;gap:4px;font-weight:600}
.review select,.review textarea,.review input{font:inherit;font-weight:400;padding:8px 10px;
border:1px solid var(--rule);border-radius:8px;width:100%;background:var(--paper);
color:var(--ink)}
.review button{font:inherit;font-weight:700;background:var(--ultra);color:var(--paper);
border:0;border-radius:8px;padding:10px 18px;justify-self:start;cursor:pointer}
.review .trap{display:none}
.bookmark{border-left:4px solid var(--ultra);background:var(--card);padding:12px 14px;
font-weight:700}
ul.files{list-style:none;padding:0;margin:0}
ul.files li{padding:10px 0;border-top:1px solid var(--rule)}
ul.files li:first-child{border-top:0}
a.dl{display:inline-flex;align-items:center;min-height:48px;padding:10px 18px;
margin-top:6px;border-radius:10px;background:var(--ultra);color:var(--paper);
font-weight:700;text-decoration:none}
a.dl:hover{background:var(--deep)}
a.dl:focus-visible,a:focus-visible{outline:3px solid var(--ink);outline-offset:3px}
.off{display:inline-block;margin-top:6px;padding:10px 14px;border:2px dashed var(--off);
border-radius:10px;background:transparent;color:var(--off);font-weight:700}
.muted{color:var(--soft);font-size:.93rem}
a{color:var(--ultra)}
"""


def _page(title: str, body: str, status: int, *, csp: str = "",
          extra_css: str = "") -> HTMLResponse:
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#0A0D14">
<meta name="robots" content="noindex">
<title>{escape(title)} | ATLAS Academy</title><style>{_CSS}{extra_css}</style></head>
<body><main><p class="mark">ATLAS <span>Academy</span></p>{body}</main></body></html>"""
    headers = {**NO_STORE, "Content-Security-Policy": csp} if csp else NO_STORE
    return HTMLResponse(html, status_code=status, headers=headers)


def _contact(cat: Catalog | None) -> str:
    email = escape(cat.contact_email if cat else "owner@example.com")
    return f'<a href="mailto:{email}">{email}</a>'


def _problem(cat: Catalog | None, title: str, text: str, status: int) -> HTMLResponse:
    body = (f"<h1>{escape(title)}</h1><p>{text}</p>"
            f"<p>Questions: email {_contact(cat)}, and include your Stripe receipt "
            "number if you have it.</p>")
    return _page(title, body, status)


def _read_line(v: Verdict) -> str:
    return f"Checked with Stripe at {v.read_at.strftime('%d %b %Y, %H:%M UTC')}."


def human_size(n: int) -> str:
    """1536 -> "1.5 KB": binary units, as a file manager shows them."""
    size = float(n)
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} bytes" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _file_facts(f) -> str:
    """The file's real size and last-changed date, read from disk now; empty
    when it can't be read (never a guess)."""
    path = delivery.resolve(f)
    if path is None:
        return ""
    try:
        st = path.stat()
    except OSError:
        return ""
    changed = datetime.fromtimestamp(st.st_mtime, UTC).strftime("%d %b %Y")
    return (f' <span class="muted">({escape(human_size(st.st_size))}, updated '
            f"{escape(changed)})</span>")


def _verdict_page(cat: Catalog, v: Verdict) -> HTMLResponse:
    """What the buyer sees for anything but a paid checkout."""
    read = escape(_read_line(v))
    if v.status == "unconfigured":
        return _problem(cat, "We can't check your payment yet",
                        "The store's payment check isn't switched on, so this page "
                        "can't confirm your purchase. Your payment itself is not "
                        f"affected. {read}", 503)
    if v.status in ("unreachable", "refused"):
        return _problem(cat, "We can't reach Stripe right now",
                        "This page asks Stripe about your payment every time it "
                        "opens, and Stripe didn't answer. Try again in a few "
                        f"minutes. {read}", 503)
    if v.status == "not_found":
        return _problem(cat, "We couldn't find this checkout",
                        "Stripe has no checkout with this reference. Please use the "
                        "link from the end of your checkout, or your receipt.", 404)
    if v.status == "unpaid":
        return _problem(cat, "This checkout isn't paid",
                        "Stripe says this checkout hasn't been paid, so there is "
                        f"nothing to download yet. {read}", 402)
    if v.status == "refunded":
        return _problem(cat, "This purchase was refunded",
                        "Stripe shows this payment as refunded, so its downloads "
                        f"are no longer available. {read}", 403)
    if v.status == "disputed":
        return _problem(cat, "This payment is under dispute",
                        "Stripe shows a dispute on this payment, so downloads are "
                        f"paused until it is settled. {read}", 403)
    if v.status == "expired":
        return _problem(cat, "This access page has expired",
                        f"Access lasts for a set time after purchase. {read}", 403)
    if v.status == "unknown_price":
        return _problem(cat, "Your payment went through, but we can't match it",
                        "Stripe confirms the payment, but what you bought isn't in "
                        "our catalog, so this page can't list your files. We have "
                        f"been told. Email us and we'll sort it out. {read}", 409)
    return _problem(cat, "Something went wrong", "Please try again later.", 503)


_STARS = ((5, "Excellent"), (4, "Good"), (3, "Fine"), (2, "Poor"), (1, "Bad"))


def _review_box(v: Verdict, written: dict[str, str]) -> str:
    """One short form per product bought, or the state of the review the
    buyer already wrote. Posts carry the checkout session id, which the API
    checks against Stripe again before storing anything."""
    parts = []
    for offer in v.offers:
        state = written.get(offer.id)
        if state == "approved":
            parts.append(f"<p class=\"muted\">Your review of <b>{escape(offer.name)}</b> is "
                         "on the product page. Thank you.</p>")
            continue
        if state in ("pending", "rejected"):
            parts.append(f"<p class=\"muted\">Your review of <b>{escape(offer.name)}</b> is "
                         "with us. You can write it again below to replace it.</p>")
        options = "".join(f'<option value="{n}">{n} of 5, {label}</option>'
                          for n, label in _STARS)
        parts.append(f"""<form class="review" method="post" action="{reviews_path()}">
<input type="hidden" name="session_id" value="{escape(v.session_id)}">
<input type="hidden" name="offer" value="{escape(offer.id)}">
<p><b>{escape(offer.name)}</b></p>
<label>Rating <select name="stars" required><option value="">Choose</option>{options}</select></label>
<label>Your review <textarea name="text" required minlength="{reviews.TEXT_MIN}" maxlength="{reviews.TEXT_MAX}" rows="4"></textarea></label>
<label>Name to show (optional) <input name="name" type="text" maxlength="{reviews.NAME_MAX}" autocomplete="given-name"></label>
<div class="trap" aria-hidden="true"><label>Leave this empty <input name="website" type="text" tabindex="-1" autocomplete="off"></label></div>
<button type="submit">Send review</button>
</form>""")
    return ("<h2>Leave a review</h2><p class=\"muted\">Only buyers can review, and every "
            "review is read before it goes on the store. It appears as \"Verified "
            "buyer\" with the month you wrote it.</p>" + "".join(parts))


def reviews_path() -> str:
    return REVIEW_PATH


def _paid_page(cat: Catalog, v: Verdict, written: dict[str, str] | None = None,
               youtube_ids: dict[str, str] | None = None,
               preview_ids: dict[str, str] | None = None) -> HTMLResponse:
    key = delivery.signing_key()
    if key is None:
        return _problem(cat, "Your payment is confirmed, but downloads are off",
                        "The store can't sign download links right now (its download "
                        "key isn't set). Email us and we'll send your files.", 503)
    days = int(cat.store.get("download_link_days") or 7)
    links: dict[str, course_page.FileLink] = {}
    for offer in v.offers:
        for f in offer.files:
            if f.key in links:
                continue
            if delivery.resolve(f) is None:
                links[f.key] = course_page.FileLink(present=False)
                continue
            token = delivery.mint(v.session_id, f.key, days=days, key=key)
            href = delivery.download_url(token or "")
            links[f.key] = course_page.FileLink(
                present=True, href=href, view_href=href + "?view=1",
                facts=_file_facts(f))
    root = delivery.files_root()
    sidecars = {d: side for d in course_page.video_dirs(v)
                if (side := course_page.load_sidecar(root, d))}
    nonce = secrets.token_urlsafe(16)
    main = course_page.render(cat, v, links, sidecars, contact_html=_contact(cat),
                              nonce=nonce, youtube_ids=youtube_ids,
                              preview_ids=preview_ids)
    body = f"""{main}
<p class="bookmark">Bookmark this page. It keeps working while your purchase is
paid and not refunded, and it makes fresh download links each time you open it.</p>
<p class="muted">Each download link works for {days} days. {escape(_read_line(v))}</p>
{_review_box(v, written or {})}
<h2>Need help?</h2>
<p>Email {_contact(cat)} from the address you paid with.</p>"""
    page = _page("Your downloads", body, 200, csp=course_page.csp(nonce),
                 extra_css=course_page.COURSE_CSS)
    # A YouTube embed refuses to play without a referrer; the public host's
    # default is no-referrer, so this page asks for the origin only.
    page.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return page


@router.get(delivery.ACCESS_PATH)
async def access(request: Request, session_id: str = "") -> HTMLResponse:
    cat = _catalog()
    busy = ("Too many requests", "Please wait a few minutes and open this page again.")
    if not _pages.allow(effective_client(request)):
        return _problem(cat, *busy, 429)
    if not valid_session_id(session_id):
        if not _pages_bad.allow(effective_client(request)):
            return _problem(cat, *busy, 429)
        return _problem(cat, "We couldn't find this checkout",
                        "This link is incomplete. Please use the link from the end "
                        "of your checkout, or your receipt.", 404)
    if not _session_pages.allow(session_id):
        return _problem(cat, *busy, 429)
    if cat is None:
        return _problem(None, "The store is being updated",
                        "Please try again in a few minutes.", 503)
    v = await read_session(session_id, cat)
    _remember_paid(v)
    if v.recordable:
        try:
            await delivery.record(v, "access")
        except Exception as exc:                        # noqa: BLE001
            # The buyer still gets their page; the record is the owner's, not theirs.
            log.error("academy: purchase not recorded (%s)", type(exc).__name__)
    if not v.paid:
        return _verdict_page(cat, v)
    try:
        written = await reviews.pending_for(session_id)
    except Exception as exc:                            # noqa: BLE001
        log.warning("academy: reviews not read (%s)", type(exc).__name__)
        written = {}
    return _paid_page(cat, v, written, await lesson_youtube.embed_ids(),
                      await lesson_youtube.public_ids())


# ------------------------------------------------------------------ files

@router.get(delivery.FILE_PREFIX + "{token}")
async def download(token: str, request: Request, view: int = 0) -> Response:
    cat = _catalog()
    status, session_id, file_key = delivery.verify(token)
    if status == "bad" or cat is None:
        if not _files.allow(effective_client(request)):
            return _problem(cat, "Too many requests",
                            "Please wait a few minutes and try again.", 429)
        return _problem(cat, "This download link isn't valid",
                        "Open your access page again for a fresh link.", 404)
    # The signature held: the session id is real input from a real link, so
    # this buyer's own requests are counted, not the whole store's.
    if not _session_files.allow(session_id):
        return _problem(cat, "Too many requests",
                        "Please wait a few minutes and try again.", 429)
    if status == "expired":
        return _problem(cat, "This download link has expired",
                        "Download links last a few days. Open your access page again "
                        "(the one you bookmarked) for a fresh link.", 410)
    v = await _read_for_file(session_id, cat)
    if not v.paid:
        return _verdict_page(cat, v)
    f = cat.file(file_key)
    if f is None or file_key not in v.file_keys():
        return _problem(cat, "This download link isn't valid",
                        "Open your access page again for a fresh link.", 404)
    path = delivery.resolve(f)
    if path is None:
        return _problem(cat, "This file isn't on the server yet",
                        "Your purchase is confirmed. Email us and we'll send it.", 503)
    media = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    inline = bool(view) and (media == "application/pdf" or media.startswith("video/"))
    return FileResponse(path, media_type=media, filename=path.name,
                        content_disposition_type="inline" if inline else "attachment",
                        headers={"Cache-Control": "no-store",
                                 "X-Frame-Options": "SAMEORIGIN"})


# ------------------------------------------------------------------ webhook

@router.post(WEBHOOK_PATH)
async def stripe_academy(request: Request) -> Response:
    """Stripe's signed checkout event: read the session back with the read
    key (the event body is only a pointer) and record it, so a buyer who
    closed the tab before the redirect still reaches the owner's Slack."""
    secret = os.getenv(WEBHOOK_SECRET_ENV, "").strip()
    if not secret:
        return PlainTextResponse("not configured", status_code=503)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY:
            return PlainTextResponse("too large", status_code=413)
        chunks.append(chunk)
    body = b"".join(chunks)
    header = request.headers.get("stripe-signature", "")
    if not verify(body, header, secret):
        return Response(status_code=401)
    try:
        event = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return PlainTextResponse("invalid json", status_code=400)
    if not isinstance(event, dict):
        return PlainTextResponse("invalid json", status_code=400)
    kind = str(event.get("type") or "")
    obj = ((event.get("data") or {}).get("object") or {})
    if not isinstance(obj, dict):
        return PlainTextResponse("ignored", status_code=200)
    if kind in CHARGE_EVENTS:
        # Both a Charge and a Dispute carry the payment intent's id.
        look = await session_for_payment_intent(str(obj.get("payment_intent") or ""))
        if not look.readable:
            log.warning("academy webhook %s: %s", kind, look.status)
            return PlainTextResponse(look.status, status_code=503)
        if not look.session_ids:
            return PlainTextResponse("ignored", status_code=200)
        session_id = look.session_ids[0]
    elif kind in PAID_EVENTS:
        session_id = str(obj.get("id") or "")
    else:
        session_id = ""
    if not valid_session_id(session_id):
        return PlainTextResponse("ignored", status_code=200)
    cat = _catalog()
    if cat is None:
        return PlainTextResponse("catalog unreadable", status_code=500)
    v = await read_session(session_id, cat)
    if v.status in ("unconfigured", "unreachable", "refused"):
        # Not recorded: a 5xx makes Stripe retry, and nothing is assumed.
        log.warning("academy webhook %s: %s", kind, v.status)
        return PlainTextResponse(v.status, status_code=503)
    try:
        new = await delivery.record(v, "webhook")
    except Exception as exc:                            # noqa: BLE001
        log.error("academy webhook not recorded (%s)", type(exc).__name__)
        return PlainTextResponse("not recorded", status_code=500)
    return PlainTextResponse("recorded" if new else v.status, status_code=200)


# ------------------------------------------------------------------ forms

# The store's contact form opens the visitor's mail app since 2026-10-29
# (ATLAS is local-only), so there is no POST /public/academy/contact.
FORM_MAX_BODY = 16 * 1024
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _line(text: str, limit: int) -> str:
    """One line of someone else's text: no control characters, no newlines,
    capped. For titles and the stored name/email; the message body is
    wrapped as untrusted instead."""
    return _CONTROL.sub("", text).replace("\n", " ").replace("\r", " ").strip()[:limit]


# ------------------------------------------------------------------ reviews

REVIEW_PATH = "/public/academy/review"
_reviews = PerAddressLimiter(10, 3600.0)


@router.post(REVIEW_PATH)
async def review(request: Request) -> Response:
    """A verified buyer's review from their access page: checked against
    Stripe again, stored pending, carded to the owner (reviews.py). The buyer is
    sent back to their access page, which shows the review's state."""
    cat = _catalog()
    if not _reviews.allow(effective_client(request)):
        return _problem(cat, "Too many reviews", "Please wait an hour.", 429)
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/x-www-form-urlencoded":
        return PlainTextResponse("form posts only", status_code=415)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > FORM_MAX_BODY:
            return _problem(cat, "That review is too long", "Please shorten it.", 413)
        chunks.append(chunk)
    form = parse_qs(b"".join(chunks).decode("utf-8", "replace"), keep_blank_values=True)

    def field(key: str) -> str:
        return (form.get(key) or [""])[0]

    session_id = _line(field("session_id"), 80)
    if not valid_session_id(session_id):
        return _problem(cat, "We couldn't find this checkout",
                        "Open your access page again and write the review there.", 404)
    back = delivery.access_url(session_id)
    if _line(field("website"), 8):                    # the honeypot
        return RedirectResponse(back, status_code=303)
    try:
        stars = int(field("stars") or 0)
    except ValueError:
        stars = 0
    text = reviews.clean_text(field("text"))
    name = reviews.clean_name(field("name"))
    offer_id = _line(field("offer"), 80)
    if problems := reviews.problems(stars, text, name):
        return _problem(cat, "We couldn't take that review",
                        "Please go back and add " + escape("; ".join(problems)) + ".", 422)
    try:
        ok, why = await reviews.submit(session_id, offer_id, stars, text, name,
                                       effective_client(request))
    except Exception as exc:                            # noqa: BLE001
        log.error("academy: review not stored (%s)", type(exc).__name__)
        return _problem(cat, "We couldn't take your review right now",
                        "Please try again in a few minutes.", 503)
    if not ok:
        return _problem(cat, "We couldn't take that review", escape(why), 403)
    return RedirectResponse(back, status_code=303)


# ------------------------------------------------------------------ recovery

RECOVER_PATH = "/public/academy/recover"
RECOVER_PAGE_URL = store_origin.url("recover/")
CONTACT_PAGE_URL = store_origin.url("contact/")
RECOVER_MAX_BODY = 4 * 1024
# A person who lost a link tries a few times; past five an hour from one
# address, or five a day for one email (whoever types it), it is guessing.
# The email limiter holds sha256(email), never the address.
_recover_ip = PerAddressLimiter(5, 3600.0)
_recover_email = PerAddressLimiter(5, 86400.0)
_library = PerAddressLimiter(12, 600.0)

# Every answer that is not a match is this exact page: the same status, the
# same bytes, whatever was typed. No time, no echo of the input, no product.
_NO_MATCH_BODY = (
    "<h1>Check your receipt</h1>"
    "<p>If that receipt matches a purchase, you'll be taken to your downloads.</p>"
    "<p>Didn't work? Check the number in your Stripe receipt email (it looks like "
    "<b>#1234-5678</b>, near the top) and use the address that email was sent to. "
    f'<a href="{RECOVER_PAGE_URL}">Try again</a>, or '
    f'<a href="{CONTACT_PAGE_URL}#lost">contact us</a>.</p>')


def _no_match() -> HTMLResponse:
    return _page("Check your receipt", _NO_MATCH_BODY, 200)


def _recover_busy() -> HTMLResponse:
    return _page("Too many tries",
                 "<h1>Too many tries</h1><p>Please wait a while before trying again, "
                 f'or <a href="{CONTACT_PAGE_URL}#lost">contact us</a>.</p>', 429)


def _recover_down(cat: Catalog | None) -> HTMLResponse:
    return _problem(cat, "We couldn't check with the payment provider right now",
                    "Your purchase is not affected: we just couldn't ask Stripe about "
                    "it. Please try again in a few minutes.", 503)


# Every answer from `recover` takes at least this long from the request's
# start, so the time it takes says nothing about what matched. Tests set it.
RECOVER_FLOOR_S = 1.5


@router.post(RECOVER_PATH)
async def recover(request: Request) -> Response:
    """Self-service access recovery (plan F17). The buyer types the address
    their Stripe receipt went to and the receipt number on it; Stripe is
    asked (read-only, the same two list GETs whatever was typed); a match on
    both sends them to a one-hour library link. Anything else gets
    `_no_match`, byte for byte. Every answer waits out RECOVER_FLOOR_S.
    Neither field is stored, logged, posted or put in a prompt."""
    started = time.monotonic()
    try:
        return await _recover(request)
    finally:
        left = RECOVER_FLOOR_S - (time.monotonic() - started)
        if left > 0:
            await asyncio.sleep(left)


async def _recover(request: Request) -> Response:
    cat = _catalog()
    if not _recover_ip.allow(effective_client(request)):
        return _recover_busy()
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/x-www-form-urlencoded":
        return PlainTextResponse("form posts only", status_code=415)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > RECOVER_MAX_BODY:
            return PlainTextResponse("too large", status_code=413)
        chunks.append(chunk)
    form = parse_qs(b"".join(chunks).decode("utf-8", "replace"), keep_blank_values=True)
    typed_email = (form.get("email") or [""])[0][:300]
    email = normalise_email(typed_email)
    receipt = normalise_receipt((form.get("receipt") or [""])[0][:64])
    if not _recover_email.allow(email_hash(email or typed_email)):
        return _recover_busy()
    look = await find_by_receipt(typed_email, receipt)
    if not look.readable:
        return _recover_down(cat)
    if not look.session_ids:
        return _no_match()
    if cat is None:
        return _problem(None, "The store is being updated",
                        "Please try again in a few minutes.", 503)
    matched = []
    owner = email_hash(email)
    for sid in look.session_ids:
        v = await read_session(sid, cat)
        if v.status in ("unconfigured", "unreachable", "refused"):
            return _recover_down(cat)
        if v.email and email_hash(v.email) != owner:
            # The fresh read names another buyer: never theirs to open.
            continue
        if v.paid:
            try:
                # A paid purchase whose webhook never arrived is recorded now
                # (without a Slack post: see delivery.record).
                await delivery.record(v, "recover")
            except Exception as exc:                    # noqa: BLE001
                log.error("academy: recovered purchase not recorded (%s)",
                          type(exc).__name__)
        if v.paid:
            matched.append(sid)
    if not matched:
        return _no_match()
    token = delivery.mint_library(owner, matched)
    if token is None:
        return _problem(cat, "We found your purchase, but can't open it right now",
                        "The store can't sign links at the moment (its key isn't "
                        "set). Please contact us.", 503)
    return RedirectResponse(delivery.library_url(token), status_code=303,
                            headers=NO_STORE)


# ------------------------------------------------------------------ free updates
#
# A visitor gives an email address and nothing else (modules/academy/
# subscribers.py). Double opt-in: the form only queues a confirmation mail
# (sent by the executor, never from here); the address counts only after its
# owner opens the emailed link. One-click unsubscribe, always honoured.

SUBSCRIBE_PATH = subscribers.SUBSCRIBE_PATH
CONFIRM_PATH = subscribers.CONFIRM_PATH
UNSUBSCRIBE_PATH = subscribers.UNSUBSCRIBE_PATH
UPDATES_PAGE_URL = store_origin.url("updates/")
THANKS_PAGE_URL = store_origin.url("updates/thanks/")
SUBSCRIBE_MAX_BODY = 2 * 1024
# Behind the public edge every visitor shares one bucket (peers.PUBLIC_CLIENT),
# so the per-source limiter is a flood guard for the whole store. The per-address
# one holds sha256(email), never the address: a third try in a day is answered
# exactly like the first and does nothing.
_sub_ip = PerAddressLimiter(60, 3600.0)
_sub_email = PerAddressLimiter(3, 86400.0)
_sub_links = PerAddressLimiter(60, 600.0)
SUB_HEADERS = {**NO_STORE, "Referrer-Policy": "no-referrer"}


def _sub_page(title: str, body: str, status: int) -> HTMLResponse:
    page = _page(title, body, status)
    page.headers["Referrer-Policy"] = "no-referrer"
    return page


def _thanks() -> Response:
    """Every accepted signup -- new, known, pending, confirmed or unsubscribed,
    throttled or a bot's -- is this exact response."""
    return RedirectResponse(THANKS_PAGE_URL, status_code=303, headers=SUB_HEADERS)


@router.post(SUBSCRIBE_PATH)
async def subscribe(request: Request) -> Response:
    """Free updates signup: `email` (+ a hidden `source`, and `website`, a
    honeypot a person never fills). Plain form post, answered by a redirect to
    the store's "check your email" page. The address is stored once, pending;
    it is never logged, posted, put in a prompt or echoed back."""
    if not _sub_ip.allow(effective_client(request)):
        return _sub_page("Too many tries", "<h1>Too many tries</h1><p>Please wait a "
                         f'while and try again. <a href="{UPDATES_PAGE_URL}">Back</a></p>',
                         429)
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/x-www-form-urlencoded":
        return PlainTextResponse("form posts only", status_code=415)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > SUBSCRIBE_MAX_BODY:
            return PlainTextResponse("too large", status_code=413)
        chunks.append(chunk)
    form = parse_qs(b"".join(chunks).decode("utf-8", "replace"), keep_blank_values=True)
    if any(v.strip() for v in form.get("website", [])):
        return _thanks()                                    # a bot: same answer, no row
    email = subscribers.clean_email((form.get("email") or [""])[0])
    if not email:
        return _sub_page("Check the address", "<h1>Check the address</h1><p>That does "
                         "not look like an email address. "
                         f'<a href="{UPDATES_PAGE_URL}">Try again</a>.</p>', 400)
    if not _sub_email.allow(email_hash(email)):
        return _thanks()
    try:
        await subscribers.signup(email, (form.get("source") or [""])[0])
    except Exception as exc:                                # noqa: BLE001
        log.error("academy: a signup could not be stored (%s)", type(exc).__name__)
        return _sub_page("Try again in a few minutes", "<h1>We couldn't save that "
                         f'right now</h1><p>Nothing was signed up. <a href="{UPDATES_PAGE_URL}">'
                         "Please try again</a> in a few minutes.</p>", 503)
    return _thanks()


def _token_key(t: str) -> str:
    """The limiter key for a link token: a hash, never the token itself."""
    return hashlib.sha256((t or "").encode()).hexdigest()


def _press_page(title: str, lead: str, action: str, button: str) -> HTMLResponse:
    """A page with one button that POSTs. Opening a link changes nothing, so a
    mail scanner or a browser prefetch cannot confirm or unsubscribe anyone."""
    return _sub_page(title, f"<h1>{escape(title)}</h1><p>{escape(lead)}</p>"
                     f'<form method="post" action="{escape(action)}">'
                     f'<button class="dl" type="submit">{escape(button)}</button>'
                     "</form>", 200)


@router.get(CONFIRM_PATH)
async def subscribe_confirm_page(t: str = "") -> Response:
    """Shows the button; changes nothing."""
    if not (20 <= len(t) <= 100) or not t.replace("-", "").replace("_", "").isalnum():
        return _sub_page("This link doesn't work", "<h1>This link doesn't work</h1>"
                         "<p>It was not copied whole. "
                         f'<a href="{UPDATES_PAGE_URL}">Sign up again</a> and we will '
                         "send a new one.</p>", 400)
    return _press_page("Confirm your email", "Press the button to get free ATLAS "
                       "Academy updates by email.", f"{CONFIRM_PATH}?t={t}",
                       "Yes, send me updates")


@router.post(CONFIRM_PATH)
async def subscribe_confirm(request: Request, t: str = "") -> Response:
    try:
        ok = await subscribers.confirm(t)
    except Exception as exc:                                # noqa: BLE001
        log.error("academy: a confirmation could not be read (%s)", type(exc).__name__)
        return _sub_page("Try again", "<h1>We couldn't check that link right now</h1>"
                         "<p>Please open it again in a few minutes.</p>", 503)
    if not ok:
        # Only a failing token counts against the limiter, and per token.
        if not _sub_links.allow(_token_key(t)):
            return _sub_page("Too many tries", "<h1>Too many tries</h1><p>Please "
                             "wait a while.</p>", 429)
        return _sub_page("This link doesn't work", "<h1>This link doesn't work</h1>"
                         "<p>It has expired (links last 7 days), was already used, or "
                         f'was not copied whole. <a href="{UPDATES_PAGE_URL}">Sign up '
                         "again</a> and we will send a new one.</p>", 400)
    return _sub_page("You're signed up", "<h1>You're signed up</h1><p>Thank you. We "
                     "will email you about new free lessons and store news. Every email "
                     "has a link to stop them in one click.</p>"
                     f'<p><a class="dl" href="{store_origin.url()}">Back to the store</a></p>',
                     200)


async def _unsubscribe(t: str) -> Response:
    """A correctly signed token is honoured always, with no limiter in front of
    it (behind the edge all visitors share one bucket). Only a failing token is
    counted, per token."""
    if subscribers.verify_unsubscribe(t) is None:
        if not _sub_links.allow(_token_key(t)):
            return PlainTextResponse("too many requests", status_code=429)
        return _sub_page("This link doesn't work", "<h1>This link doesn't work</h1>"
                         "<p>It was not copied whole. Email "
                         f"{_contact(_catalog())} and we will remove you.</p>", 400)
    try:
        await subscribers.unsubscribe(t)
    except Exception as exc:                                # noqa: BLE001
        log.error("academy: an unsubscribe could not be stored (%s)", type(exc).__name__)
        return _sub_page("Try again", "<h1>We couldn't do that right now</h1><p>Please "
                         "open the link again in a few minutes, or email "
                         f"{_contact(_catalog())} and we will remove you.</p>", 503)
    return _sub_page("You're unsubscribed", "<h1>You're unsubscribed</h1><p>We will not "
                     "email you again. Buying a course never signs you up for these "
                     f'emails.</p><p><a class="dl" href="{store_origin.url()}">Back to '
                     "the store</a></p>", 200)


@router.get(UNSUBSCRIBE_PATH)
async def unsubscribe_link(t: str = "") -> Response:
    """From the link in every email: shows one button; changes nothing."""
    if subscribers.verify_unsubscribe(t) is None:
        return await _unsubscribe(t)
    return _press_page("Unsubscribe", "Press the button to stop all free ATLAS Academy "
                       "emails.", f"{UNSUBSCRIBE_PATH}?t={t}", "Unsubscribe me")


@router.post(UNSUBSCRIBE_PATH)
async def unsubscribe_one_click(t: str = "") -> Response:
    """The button above, and RFC 8058 `List-Unsubscribe=One-Click` from a mail app."""
    return await _unsubscribe(t)


# ------------------------------------------------------------------ library

_STATUS_WORDS = {
    "paid": "Paid", "refunded": "Refunded", "disputed": "Under dispute",
    "unpaid": "Not paid", "expired": "Access period ended",
    "unknown_price": "Paid, but not matched to our catalog (contact us)",
    "not_found": "Not found on Stripe",
}


def _library_row(v: Verdict) -> str:
    if v.status in ("unconfigured", "unreachable", "refused"):
        return ('<li><b>A purchase we couldn\'t check</b><br><span class="off">'
                "UNKNOWN: Stripe didn't answer just now</span><br>"
                f'<a href="{escape(delivery.access_url(v.session_id))}">Open its access '
                "page</a> <span class=\"muted\">(it asks Stripe again)</span></li>")
    names = escape(", ".join(o.name for o in v.offers) or "A purchase")
    when = v.created.strftime("%d %b %Y") if v.created else "date not stated"
    status = escape(_STATUS_WORDS.get(v.status, v.status))
    refunded = (f", {v.refunded_minor / 100:,.2f} {escape(v.currency.upper())} refunded"
                if v.refunded_minor and v.status == "paid" else "")
    link = ""
    if v.paid:
        link = (f'<br><a class="dl" href="{escape(delivery.access_url(v.session_id))}">'
                "Open downloads</a>")
    return (f"<li><b>{names}</b><br><span class=\"muted\">Bought {escape(when)}, "
            f"{escape(v.amount_text())} as Stripe stated it{refunded}. "
            f"Status: {status}.</span>{link}</li>")


@router.get(delivery.LIBRARY_PATH)
async def library(request: Request, t: str = "") -> HTMLResponse:
    """Every purchase paid from one address (plan F16, F47), for an hour
    after recovery. Each is read from Stripe now and shown as it is."""
    cat = _catalog()
    if not _library.allow(effective_client(request)):
        return _problem(cat, "Too many requests",
                        "Please wait a few minutes and open this page again.", 429)
    status, owner, bound = delivery.verify_library(t)
    if status == "expired":
        return _page("This link has expired",
                     "<h1>This link has expired</h1><p>Library links last one hour. "
                     f'<a href="{RECOVER_PAGE_URL}">Recover again</a> with your email '
                     "and Stripe receipt number.</p>", 410)
    if status != "ok":
        return _problem(cat, "This link isn't valid",
                        f'<a href="{RECOVER_PAGE_URL}">Recover your purchases</a> '
                        "with your email and Stripe receipt number.", 404)
    if cat is None:
        return _problem(None, "The store is being updated",
                        "Please try again in a few minutes.", 503)
    note = ""
    try:
        rows = await delivery.purchases_for(owner)
        listed = [r["session_id"] for r in rows if valid_session_id(r.get("session_id"))]
    except Exception as exc:                            # noqa: BLE001
        log.warning("academy: library purchases not read (%s)", type(exc).__name__)
        listed = []
        note = ("<p class=\"off\">UNKNOWN: our purchase record couldn't be read just "
                "now, so only the purchase you recovered is shown.</p>")
    ids = list(dict.fromkeys([*bound, *listed]))[:20]
    items = []
    for sid in ids:
        v = await read_session(sid, cat)
        # A recorded row is listed only while Stripe still names this owner.
        if sid not in bound and v.email and email_hash(v.email) != owner:
            continue
        items.append(_library_row(v))
    body = f"""<h1>Your purchases</h1>
<p>Everything paid for from your address, read from Stripe just now.</p>
{note}<div class="box"><ul class="files">{"".join(items)}</ul></div>
<p class="muted">Read {datetime.now(UTC):%d %b %Y, %H:%M} UTC. This page lasts one hour;
bookmark each purchase's own downloads page instead, which keeps working while it is
paid.</p>
<h2>Need help?</h2>
<p>Email {_contact(cat)}. For a refund, write from the address you paid with.</p>"""
    return _page("Your purchases", body, 200)


# ------------------------------------------------------- content calendar
# The Agency tab's Content calendar (dashboard, 127.0.0.1, signed in). Read
# only: what went up, per series, from `academy_tutorial_posts`, each row
# with its own time; and today's `tutorials.lineup()` order as "next up".
# No date exists for a next-up episode, so none is ever sent.

CALENDAR_PATH = "/api/academy/calendar"
POSTED_LIMIT = 50                     # per series, newest first

SignedIn = Annotated[auth.Session, Depends(auth.require)]


def _ep_title(series_key: str, episode: int) -> str:
    from ..modules.academy import tutorials
    found = tutorials.resolve(series_key, episode)
    return found[1].title if isinstance(found, tuple) else ""


@router.get(CALENDAR_PATH)
async def calendar(_s: SignedIn) -> dict[str, Any]:
    """Posted rows per series and today's next-up order, with the read time.
    An unreadable table is a 503, never an empty calendar."""
    from ..kernel import state
    from ..modules.academy import tutorials
    try:
        rows = await state.fetch_all(
            """SELECT series, episode, video_id, at, public
                 FROM academy_tutorial_posts
                WHERE NOT withdrawn
                ORDER BY at DESC, id DESC""")
        done, today, last, month = await tutorials._done()
    except Exception as exc:                                  # noqa: BLE001
        why = explain(exc)
        log.warning("academy calendar not read: %s", why)
        raise HTTPException(
            503, f"I could not read the content calendar — {why}") from None
    read_at = datetime.now(UTC).isoformat()
    todo, notes = tutorials.lineup(done, today, last, month)

    keys = [s.key for s in tutorials.SERIES]
    names = {s.key: (s.playlist or s.key) for s in tutorials.SERIES}
    posted: dict[str, list[dict[str, Any]]] = {k: [] for k in keys}
    for r in rows:
        key = str(r["series"])
        if key not in posted:
            posted[key] = []
            keys.append(key)
        if len(posted[key]) >= POSTED_LIMIT:
            continue
        at = r.get("at")
        posted[key].append({
            "episode": int(r["episode"]),
            "title": _ep_title(key, int(r["episode"])),
            "video_id": str(r.get("video_id") or ""),
            "at": at.isoformat() if isinstance(at, datetime) else None,
            "public": bool(r.get("public")),
        })
    next_up = [{"series": k, "episode": int(n), "title": _ep_title(k, int(n))}
               for k, n in todo]
    series = [{"key": k, "name": names.get(k, k), "posted": posted[k],
               "next_up": [{"episode": n["episode"], "title": n["title"]}
                           for n in next_up if n["series"] == k]}
              for k in keys]
    return {"as_of": read_at, "read_at": read_at, "series": series,
            "next_up": next_up, "notes": [str(n) for n in notes]}
