"""ATLAS Academy delivery: the buyer's access page and their downloads.

    GET  /public/academy/access?session_id=cs_...   the page Stripe redirects to
    GET  /public/academy/file/<signed token>         one download
    POST /webhooks/stripe-academy                    Stripe's signed event
    POST /public/academy/contact                     the store's contact form
    POST /public/academy/review                      a verified buyer's review

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
and posts the purchase to Miro's Slack, like /webhooks/stripe-mapfront.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
from html import escape
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import (FileResponse, HTMLResponse, PlainTextResponse,
                               RedirectResponse, Response)

from ..modules.academy import delivery, reviews
from ..modules.academy.catalog import Catalog, CatalogError, load
from ..modules.academy.stripe_read import Verdict, read_session, valid_session_id
from .peers import PerAddressLimiter, effective_client
from .stripe_mapfront import MAX_BODY, verify

router = APIRouter()
log = logging.getLogger("atlas.api.academy")

WEBHOOK_PATH = "/webhooks/stripe-academy"
WEBHOOK_SECRET_ENV = "ACADEMY_STRIPE_WEBHOOK_SECRET"
PAID_EVENTS = frozenset({"checkout.session.completed",
                         "checkout.session.async_payment_succeeded"})

# A buyer opens the page a handful of times and downloads a few files; past
# this, someone is walking session ids or tokens, and every request costs a
# Stripe read.
_pages = PerAddressLimiter(12, 600.0)
_files = PerAddressLimiter(30, 600.0)
# A person writes once or twice; past five an hour it is a script.
_contacts = PerAddressLimiter(5, 3600.0)

NO_STORE = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex"}


def _catalog() -> Catalog | None:
    try:
        return load()
    except (CatalogError, OSError, ValueError) as exc:
        log.error("academy catalog unreadable: %s", exc)
        return None


# ------------------------------------------------------------------ page

_CSS = """
:root{--ink:#1d2150;--soft:#454a78;--paper:#f3f4f9;--rule:#c8cbe4;--ultra:#2b35c8;
--deep:#1c248f;--off:#e6e7ef}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font:17px/1.55 system-ui,
-apple-system,"Segoe UI",sans-serif}
main{max-width:640px;margin:0 auto;padding:32px 16px 48px}
.mark{font-weight:800;letter-spacing:.02em;margin:0 0 28px}
.mark span{font-weight:400;color:var(--soft)}
h1{font-size:2rem;line-height:1.1;letter-spacing:-.02em;margin:0 0 12px}
h2{font-size:1.2rem;margin:28px 0 8px}
p{margin:0 0 14px}
.box{background:#fff;border:1px solid var(--rule);border-radius:10px;padding:16px;
margin:16px 0}
video{display:block;width:100%;margin:8px 0;border-radius:8px;background:#000}
.review{background:#fff;border:1px solid var(--rule);border-radius:10px;padding:16px;
margin:12px 0;display:grid;gap:10px}
.review label{display:grid;gap:4px;font-weight:600}
.review select,.review textarea,.review input{font:inherit;font-weight:400;padding:8px 10px;
border:1px solid var(--rule);border-radius:8px;width:100%}
.review button{font:inherit;font-weight:700;background:var(--ultra);color:#fff;border:0;
border-radius:8px;padding:10px 18px;justify-self:start;cursor:pointer}
.review .trap{display:none}
.bookmark{border-left:4px solid var(--ink);background:#fff;padding:12px 14px;
font-weight:700}
ul.files{list-style:none;padding:0;margin:0}
ul.files li{padding:10px 0;border-top:1px solid var(--rule)}
ul.files li:first-child{border-top:0}
a.dl{display:inline-flex;align-items:center;min-height:48px;padding:10px 18px;
margin-top:6px;border-radius:10px;background:var(--ultra);color:#fff;font-weight:700;
text-decoration:none}
a.dl:hover{background:var(--deep)}
a.dl:focus-visible,a:focus-visible{outline:3px solid #ffb000;outline-offset:3px}
.off{display:inline-block;margin-top:6px;padding:10px 14px;border:2px dashed #a9adc6;
border-radius:10px;background:var(--off);color:#62667f;font-weight:700}
.muted{color:var(--soft);font-size:.93rem}
a{color:var(--ultra)}
"""


def _page(title: str, body: str, status: int) -> HTMLResponse:
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{escape(title)} | ATLAS Academy</title><style>{_CSS}</style></head>
<body><main><p class="mark">ATLAS <span>Academy</span></p>{body}</main></body></html>"""
    return HTMLResponse(html, status_code=status, headers=NO_STORE)


def _contact(cat: Catalog | None) -> str:
    email = escape(cat.contact_email if cat else "miro@mapfront.io")
    return f'<a href="mailto:{email}">{email}</a>'


def _problem(cat: Catalog | None, title: str, text: str, status: int) -> HTMLResponse:
    body = (f"<h1>{escape(title)}</h1><p>{text}</p>"
            f"<p>Questions: email {_contact(cat)}, and include your Stripe receipt "
            "number if you have it.</p>")
    return _page(title, body, status)


def _read_line(v: Verdict) -> str:
    return f"Checked with Stripe at {v.read_at.strftime('%d %b %Y, %H:%M UTC')}."


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


def _paid_page(cat: Catalog, v: Verdict, written: dict[str, str] | None = None) -> HTMLResponse:
    key = delivery.signing_key()
    if key is None:
        return _problem(cat, "Your payment is confirmed, but downloads are off",
                        "The store can't sign download links right now (its download "
                        "key isn't set). Email us and we'll send your files.", 503)
    days = int(cat.store.get("download_link_days") or 7)
    rows = []
    seen: set[str] = set()
    for offer in v.offers:
        for f in offer.files:
            if f.key in seen:
                continue
            seen.add(f.key)
            label = escape(f.label)
            if delivery.resolve(f) is None:
                rows.append(f'<li>{label}<br><span class="off">Not on the server yet'
                            f"</span><br><span class=\"muted\">Email "
                            f"{_contact(cat)} and we'll send it.</span></li>")
                continue
            token = delivery.mint(v.session_id, f.key, days=days, key=key)
            href = escape(delivery.download_url(token or ""))
            player = (f'<video controls preload="metadata" src="{href}"></video>'
                      if f.path.lower().endswith(".mp4") else "")
            rows.append(f'<li>{label}{player}<br><a class="dl" href="{href}" download>'
                        f"Download</a></li>")
    names = escape(", ".join(o.name for o in v.offers))
    body = f"""<h1>Your downloads</h1>
<p>Thank you. You bought: <b>{names}</b>.</p>
<p class="bookmark">Bookmark this page. It keeps working while your purchase is
paid and not refunded, and it makes fresh download links each time you open it.</p>
<div class="box"><ul class="files">{"".join(rows)}</ul></div>
<p class="muted">Each download link works for {days} days. {escape(_read_line(v))}</p>
{_review_box(v, written or {})}
<h2>Need help?</h2>
<p>Email {_contact(cat)} from the address you paid with.</p>"""
    return _page("Your downloads", body, 200)


@router.get(delivery.ACCESS_PATH)
async def access(request: Request, session_id: str = "") -> HTMLResponse:
    cat = _catalog()
    if not _pages.allow(effective_client(request)):
        return _problem(cat, "Too many requests",
                        "Please wait a few minutes and open this page again.", 429)
    if not valid_session_id(session_id):
        return _problem(cat, "We couldn't find this checkout",
                        "This link is incomplete. Please use the link from the end "
                        "of your checkout, or your receipt.", 404)
    if cat is None:
        return _problem(None, "The store is being updated",
                        "Please try again in a few minutes.", 503)
    v = await read_session(session_id, cat)
    if v.recordable:
        try:
            await delivery.record(v, "access")
        except Exception as exc:                        # noqa: BLE001
            # The buyer still gets their page; the record is Miro's, not theirs.
            log.error("academy: purchase not recorded (%s)", type(exc).__name__)
    if not v.paid:
        return _verdict_page(cat, v)
    try:
        written = await reviews.pending_for(session_id)
    except Exception as exc:                            # noqa: BLE001
        log.warning("academy: reviews not read (%s)", type(exc).__name__)
        written = {}
    return _paid_page(cat, v, written)


# ------------------------------------------------------------------ files

@router.get(delivery.FILE_PREFIX + "{token}")
async def download(token: str, request: Request) -> Response:
    cat = _catalog()
    if not _files.allow(effective_client(request)):
        return _problem(cat, "Too many requests",
                        "Please wait a few minutes and try again.", 429)
    status, session_id, file_key = delivery.verify(token)
    if status == "bad" or cat is None:
        return _problem(cat, "This download link isn't valid",
                        "Open your access page again for a fresh link.", 404)
    if status == "expired":
        return _problem(cat, "This download link has expired",
                        "Download links last a few days. Open your access page again "
                        "(the one you bookmarked) for a fresh link.", 410)
    v = await read_session(session_id, cat)
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
    return FileResponse(path, media_type=media, filename=path.name,
                        headers={"Cache-Control": "no-store"})


# ------------------------------------------------------------------ webhook

@router.post(WEBHOOK_PATH)
async def stripe_academy(request: Request) -> Response:
    """Stripe's signed checkout event: read the session back with the read
    key (the event body is only a pointer) and record it, so a buyer who
    closed the tab before the redirect still reaches Miro's Slack."""
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
    session_id = str(obj.get("id") or "") if isinstance(obj, dict) else ""
    if kind not in PAID_EVENTS or not valid_session_id(session_id):
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


# ------------------------------------------------------------------ contact

CONTACT_PATH = "/public/academy/contact"
CONTACT_SENT_URL = "https://shop.mapfront.io/contact/sent/"
CONTACT_MAX_BODY = 16 * 1024
NAME_MAX, EMAIL_MAX, ORDER_MAX = 120, 254, 80
MESSAGE_MIN, MESSAGE_MAX = 10, 4000
_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _line(text: str, limit: int) -> str:
    """One line of someone else's text: no control characters, no newlines,
    capped. For titles and the stored name/email; the message body is
    wrapped as untrusted instead."""
    return _CONTROL.sub("", text).replace("\n", " ").replace("\r", " ").strip()[:limit]


def contact_problems(name: str, email: str, order: str, message: str) -> list[str]:
    out = []
    if not name:
        out.append("your name")
    elif len(name) > NAME_MAX:
        out.append("a shorter name")
    if not _EMAIL.fullmatch(email) or len(email) > EMAIL_MAX:
        out.append("an email address we can reply to")
    if len(order) > ORDER_MAX:
        out.append("a shorter receipt number")
    if len(message) < MESSAGE_MIN:
        out.append("a message of at least a sentence")
    elif len(message) > MESSAGE_MAX:
        out.append("a shorter message (a few paragraphs is plenty)")
    return out


async def _store_contact(name: str, email: str, order: str, message: str,
                         client: str) -> None:
    from ..kernel.state import execute_sql
    await execute_sql(
        """INSERT INTO academy_contacts (name, email, order_ref, message, client)
           VALUES ($1, $2, $3, $4, $5)""",
        name, email, order or None, message, client)


async def _post_contact(name: str, email: str, order: str, message: str) -> None:
    from ..kernel.comms import Outbound, notify
    from ..kernel.permissions import wrap_untrusted
    title = f"ATLAS Academy: message from {name} <{email}>"
    about = f"About order/receipt {order}. " if order else ""
    body = (f"{about}Reply to {email}. The message, as written by a visitor to the "
            "store's contact form:\n"
            + wrap_untrusted(message, "shop.mapfront.io contact form"))
    await notify(Outbound(title=title, body=body, topic="academy", kind="alert"))


@router.post(CONTACT_PATH)
async def contact(request: Request) -> Response:
    """The store's contact form (modules/academy/site.py). A plain HTML
    POST from shop.mapfront.io: no script, no JSON. The message is stored,
    posted to Miro's Slack as untrusted text, and the visitor is sent back
    to the store's "sent" page. A filled honeypot gets the same redirect
    and nothing else."""
    cat = _catalog()
    if not _contacts.allow(effective_client(request)):
        return _problem(cat, "Too many messages",
                        "Please wait an hour before sending another, or email us.", 429)
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype != "application/x-www-form-urlencoded":
        return PlainTextResponse("form posts only", status_code=415)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > CONTACT_MAX_BODY:
            return _problem(cat, "That message is too long",
                            "Please shorten it, or email us instead.", 413)
        chunks.append(chunk)
    form = parse_qs(b"".join(chunks).decode("utf-8", "replace"), keep_blank_values=True)

    def field(key: str, limit: int) -> str:
        return _line((form.get(key) or [""])[0], limit)

    if field("website", 8):                         # the honeypot: bots fill it
        return RedirectResponse(CONTACT_SENT_URL, status_code=303)
    name, email = field("name", NAME_MAX + 1), field("email", EMAIL_MAX + 1).lower()
    order = field("order", ORDER_MAX + 1)
    message = _CONTROL.sub("", (form.get("message") or [""])[0]).strip()[:MESSAGE_MAX + 1]
    if problems := contact_problems(name, email, order, message):
        return _problem(cat, "We couldn't send that",
                        "Please go back and add " + escape("; ".join(problems)) + ".", 422)
    try:
        await _store_contact(name, email, order, message, effective_client(request))
    except Exception as exc:                            # noqa: BLE001
        log.error("academy: contact message not stored (%s)", type(exc).__name__)
        return _problem(cat, "We couldn't take your message right now",
                        "Please try again in a few minutes, or email us.", 503)
    try:
        await _post_contact(name, email, order, message)
    except Exception as exc:                            # noqa: BLE001
        # Stored, so it reaches Miro on the next read; the visitor is done.
        log.warning("academy: contact message not posted to Slack (%s)",
                    type(exc).__name__)
    return RedirectResponse(CONTACT_SENT_URL, status_code=303)


# ------------------------------------------------------------------ reviews

REVIEW_PATH = "/public/academy/review"
_reviews = PerAddressLimiter(10, 3600.0)


@router.post(REVIEW_PATH)
async def review(request: Request) -> Response:
    """A verified buyer's review from their access page: checked against
    Stripe again, stored pending, carded to Miro (reviews.py). The buyer is
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
        if size > CONTACT_MAX_BODY:
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
