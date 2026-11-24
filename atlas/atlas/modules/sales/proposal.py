"""The proposal: one HTML page composed here from the configured scope and
the lead's own price (`pricing.terms(lead_id)`), never by a model, so it
can name no other figure. A PDF only through the qa-browser service, which
renders an allowlisted https URL: `SALES_PROPOSAL_BASE_URL` must say where
the HTML is served. Either missing: "PDF not rendered", said, not hidden."""
from __future__ import annotations

import hashlib
import html
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ..agency import pricing

ROOT = Path("data/sales/proposals")
NY = ZoneInfo("America/New_York")
BASE_ENV = "SALES_PROPOSAL_BASE_URL"


def compose(business: str, price: int, returning: bool, at: datetime) -> str:
    who = "a returning client" if returning else "a new client"
    items = "".join(f"<li>{html.escape(d)}</li>" for d in pricing.deliverables())
    name = html.escape(business)
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>Proposal for {name}</title>"
        "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:640px;margin:40px "
        "auto;padding:0 16px;color:#111}h1{font-size:24px}.price{font-size:20px}"
        "</style></head><body>"
        f"<h1>A website for {name}</h1>"
        f"<p>Prepared by Mapfront on {at:%B %-d, %Y}.</p>"
        f"<h2>What is included</h2><ul>{items}</ul>"
        f"<p class=\"price\">Price: <b>${price}</b> ({who}), after {pricing.DEMO}.</p>"
        "<p>Work starts once the agreement is accepted and payment is in; the "
        "timeline is confirmed in writing.</p>"
        "</body></html>")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def render_pdf(html_name: str) -> tuple[str | None, str]:
    """(pdf bytes path or None, what happened)."""
    if not (os.getenv("QA_BROWSER_URL") or "").strip():
        return None, "PDF not rendered: QA_BROWSER_URL is not set"
    base = (os.getenv(BASE_ENV) or "").strip().rstrip("/")
    if not base.startswith("https://"):
        return None, f"PDF not rendered: {BASE_ENV} is not an https URL"
    from ..qa import browser
    got = await browser.pdf(f"{base}/{html_name}")
    if not got.ok:
        return None, f"PDF {got.detail}"
    out = ROOT / (Path(html_name).stem + ".pdf")
    out.write_bytes(got.data)
    return str(out), "PDF rendered"
