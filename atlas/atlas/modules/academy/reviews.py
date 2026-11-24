"""Verified-buyer reviews for the store. Miro, 2026-10-01: "add reviews for
my courses". The store carries no invented review, rating or count (the
site tests forbid them), so the only review that can appear is one a real
buyer wrote:

  1. The buyer writes it on their access page (atlas/api/academy.py
     `review`), which they reach only with a paid Stripe checkout; the
     post carries that session id and is checked against Stripe again
     before anything is stored. One review per checkout per offer; a
     second post replaces the first and goes back to "pending".
  2. The row waits in `academy_reviews` and Miro gets one Tier 2 card,
     `academy.approve_review`. The text on the card is wrapped as
     untrusted: a review is a buyer's words, never an instruction.
  3. Only an approved review reaches the product page, at the next
     `academy.publish_shop`, as "Verified buyer, <Month Year>" with the
     stars and the text, escaped. Nothing shows until one exists.

Rollback of an approval puts the review back to pending; the next publish
takes it off the page. Nothing here writes to Stripe or emails anyone.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from ...kernel.contracts import ActionResult
from .catalog import CatalogError, load
from .stripe_read import read_session, valid_session_id

log = logging.getLogger("atlas.academy.reviews")

ACTION = "academy.approve_review"
TEXT_MIN, TEXT_MAX, NAME_MAX = 10, 600, 40
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean_text(text: str) -> str:
    text = _CONTROL.sub("", text or "").replace("\r", "")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:TEXT_MAX + 1]


def clean_name(name: str) -> str:
    """A first name or initials as the buyer typed it, one line, no digits
    (the store shows no figure it did not put there)."""
    name = _CONTROL.sub("", name or "").replace("\n", " ").replace("\r", " ")
    name = re.sub(r"\d+", "", name)
    return re.sub(r"\s+", " ", name).strip()[:NAME_MAX]


def problems(stars: int, text: str, name: str) -> list[str]:
    out = []
    if not 1 <= stars <= 5:
        out.append("a rating from one to five")
    if len(text) < TEXT_MIN:
        out.append("a sentence or two about the product")
    elif len(text) > TEXT_MAX:
        out.append("a shorter review (a short paragraph is plenty)")
    if len(name) > NAME_MAX:
        out.append("a shorter name")
    return out


async def submit(session_id: str, offer_id: str, stars: int, text: str, name: str,
                 client: str) -> tuple[bool, str]:
    """Store a buyer's review as pending and raise Miro's card. (ok, why)."""
    if not valid_session_id(session_id):
        return False, "This page's link is incomplete. Open your access page again."
    try:
        cat = load()
    except (CatalogError, OSError, ValueError):
        return False, "The store is being updated. Please try again in a few minutes."
    v = await read_session(session_id, cat)
    if not v.paid:
        return False, "We can only take a review from a paid purchase."
    offer = next((o for o in v.offers if o.id == offer_id), None)
    if offer is None:
        return False, "That product isn't part of this purchase."
    product = next((p for p in cat.products if any(o.id == offer_id for o in p.offers)), None)
    slug = product.slug if product is not None else cat.bundle.slug
    from ...kernel.state import fetch_one
    row = await fetch_one(
        """INSERT INTO academy_reviews
               (session_id, offer_id, product_slug, stars, body, display_name, client)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           ON CONFLICT (session_id, offer_id) DO UPDATE
               SET stars = EXCLUDED.stars, body = EXCLUDED.body,
                   display_name = EXCLUDED.display_name, client = EXCLUDED.client,
                   status = 'pending', written_at = now(), decided_at = NULL
           RETURNING id""",
        session_id, offer_id, slug, stars, text, name or None, client)
    review_id = int((row or {}).get("id") or 0)
    if review_id:
        if clean_refusal(stars, text, name) is None and auto_enabled():
            # Miro, 2026-10-06: a clean verified-buyer review shows itself.
            await _auto(review_id)
        else:
            await _card(review_id, offer.name, stars, text, name)
    return True, ""


def auto_enabled() -> bool:
    """`ACADEMY_REVIEWS_AUTO=off` makes every review a card again."""
    import os
    return os.getenv("ACADEMY_REVIEWS_AUTO", "on").strip().lower() not in (
        "0", "off", "false", "no")


_LINK = re.compile(r"(https?://|www\.|\.(com|io|net|org|co)\b|@[A-Za-z0-9_]{3,})", re.IGNORECASE)
_CONTACT = re.compile(r"(\+?\d[\d .-]{7,}\d|[\w.+-]+@[\w-]+\.[\w.]+)")
_UNFIT = ("scam", "fraud", "refund", "lawsuit", "sue ", "porn", "sex", "kill", "nazi",
          "cheap trick", "better than ", "worse than ")
MIN_WORDS, MAX_CHARS = 6, 1200


def clean_refusal(stars: int, text: str, name: str) -> str | None:
    """Why this review needs Miro's eyes, or None when it may show itself:
    a plain opinion of the product, no link, no contact, no other product
    or person named, between six words and 1,200 characters. The vet is
    deliberately narrow; anything it cannot read as plain is a card."""
    body = " ".join((text or "").split())
    if not 1 <= int(stars or 0) <= 5:
        return "stars outside 1-5"
    if len(body.split()) < MIN_WORDS:
        return "too short to read as a review"
    if len(body) > MAX_CHARS:
        return "longer than a review"
    if _CONTACT.search(body) or _CONTACT.search(name or ""):
        return "carries contact details"
    if _LINK.search(body) or _LINK.search(name or ""):
        return "carries a link or a handle"
    low = body.lower()
    if any(w in low for w in _UNFIT):
        return "names something Miro should read first"
    if sum(c.isupper() for c in body) > len(body) * 0.5:
        return "shouting"
    if clean_name(name) != (name or "").strip():
        return "the name is not a plain name"
    return None


async def _auto(review_id: int) -> None:
    """Approve through the executor (Tier 1, `academy.auto_approve_review`)
    and republish so it shows; a refusal there becomes the card."""
    from ...kernel.executor import ExecutionRequest, execute
    done = await execute(ExecutionRequest(
        action="academy.auto_approve_review", params={"review_id": review_id},
        reason="a clean verified-buyer review (reviews.clean_refusal)"))
    if not done.ok:
        log.warning("review #%s not auto-approved: %s", review_id, done.detail[:200])
        r = await _row(review_id)
        if r is not None:
            await _card(review_id, r["offer_id"], int(r["stars"]), str(r["body"]),
                        str(r["display_name"] or ""))
        return
    await execute(ExecutionRequest(action="academy.publish_shop", params={},
                                   reason=f"review #{review_id} approved itself"))


async def auto_approve_review_dry(review_id: int = 0) -> ActionResult:
    r = await _row(review_id)
    if r is None:
        return ActionResult(False, f"no review #{review_id}")
    why = clean_refusal(int(r["stars"]), str(r["body"]), str(r["display_name"] or ""))
    if why is not None:
        return ActionResult(False, f"not clean: {why}; it stays a card")
    return await approve_review_dry(review_id)


async def auto_approve_review(review_id: int = 0) -> ActionResult:
    r = await _row(review_id)
    if r is None:
        return ActionResult(False, f"no review #{review_id}")
    why = clean_refusal(int(r["stars"]), str(r["body"]), str(r["display_name"] or ""))
    if why is not None:
        return ActionResult(False, f"not clean: {why}; it stays a card")
    return await approve_review(review_id)


async def _card(review_id: int, product: str, stars: int, text: str, name: str) -> None:
    from ...kernel import approvals
    from ...kernel.permissions import wrap_untrusted
    who = f"{name} (verified buyer)" if name else "a verified buyer"
    try:
        await approvals.propose(
            ACTION, {"review_id": review_id},
            reason=(f"{who} left {stars} of 5 for {product}. Approve to show it on "
                    f"the product page at the next publish; decline and it stays "
                    f"off the store.\n"
                    + wrap_untrusted(text, "store review form")),
            dedup_key=f"academy.review.{review_id}", automatic=True)
    except Exception as exc:  # noqa: BLE001
        # The row is stored; the pending list is Miro's to read either way.
        log.warning("review card not posted: %s", type(exc).__name__)


def vet(params: dict[str, Any]) -> str | None:
    try:
        if int(params.get("review_id") or 0) <= 0:
            return "review_id must be a stored review's id"
    except (TypeError, ValueError):
        return "review_id must be a stored review's id"
    return None


async def _row(review_id: int) -> dict | None:
    from ...kernel.state import fetch_one
    return await fetch_one(
        "SELECT id, offer_id, product_slug, stars, body, display_name, status "
        "FROM academy_reviews WHERE id = $1", int(review_id))


async def approve_review_dry(review_id: int = 0) -> ActionResult:
    r = await _row(review_id)
    if r is None:
        return ActionResult(False, f"no review #{review_id}")
    return ActionResult(True, (f"would show review #{review_id} ({r['stars']} of 5, "
                               f"{r['product_slug']}) on the product page at the next "
                               "publish"), evidence={"status_now": r["status"]})


async def approve_review(review_id: int = 0) -> ActionResult:
    from ...kernel.state import execute_sql
    r = await _row(review_id)
    if r is None:
        return ActionResult(False, f"no review #{review_id}")
    await execute_sql("UPDATE academy_reviews SET status = 'approved', decided_at = now() "
                      "WHERE id = $1", int(review_id))
    return ActionResult(True, (f"review #{review_id} approved; it appears on "
                               f"{r['product_slug']} at the next academy.publish_shop"),
                        evidence={"product_slug": r["product_slug"], "stars": r["stars"]},
                        rollback_token=str(int(review_id)))


async def approve_review_rollback(token: str) -> ActionResult:
    from ...kernel.state import execute_sql
    try:
        review_id = int(token)
    except ValueError:
        return ActionResult(False, "unreadable rollback token")
    await execute_sql("UPDATE academy_reviews SET status = 'pending', decided_at = NULL "
                      "WHERE id = $1", review_id)
    return ActionResult(True, f"review #{review_id} back to pending; the next publish "
                              "takes it off the page")


async def approved(limit: int = 200) -> list[dict]:
    """Approved reviews for the site builder: slug, stars, text, name, month."""
    from ...kernel.state import fetch_all
    rows = await fetch_all(
        "SELECT product_slug, stars, body, display_name, written_at FROM academy_reviews "
        "WHERE status = 'approved' ORDER BY written_at DESC LIMIT $1", int(limit))
    out = []
    for r in rows or []:
        at = r.get("written_at") or datetime.now(UTC)
        out.append({"slug": r["product_slug"], "stars": int(r["stars"]),
                    "text": str(r["body"] or ""), "name": clean_name(r.get("display_name") or ""),
                    "month": at.strftime("%B %Y")})
    return out


async def pending_for(session_id: str) -> dict[str, str]:
    """offer_id -> status for the reviews this checkout already wrote."""
    from ...kernel.state import fetch_all
    rows = await fetch_all("SELECT offer_id, status FROM academy_reviews WHERE session_id = $1",
                           session_id)
    return {str(r["offer_id"]): str(r["status"]) for r in rows or []}
