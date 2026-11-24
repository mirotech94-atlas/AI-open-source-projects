"""The Websites lane's approval card (agent `sites`, Miro 2026-10-28).

"All the live projections that are done should be seen by me first to
approve and you send out email to each owner." A preview the lane built
reaches its owner only through one `agency.send_outreach` card in
#agency-decisions: the card shows the preview link, the owner and the
address, the subject and the whole email; approving runs every send guard
again (US only, verified, suppression, CAN-SPAM, the daily cap) and sends
that one email through the executor. Nothing here sends. An unanswered card
expires (`approvals.propose` TTL); it never sends on timeout.

This file is pure (the card's words, the "was the link shared" read); the
steps run in `kernel.lead_followup.pitch_preview`, since nothing under
modules/agency imports the executor. Sales only hands leads over
(`modules.sales.shift`); `lead_followup.follow_site` hands over the sites it
just published.
"""
from __future__ import annotations

import re
from typing import Any

ACTION = "agency.send_outreach"
# `approvals.propose` shows the first 1500 characters of a verbose card's
# reason; `_send_card` puts up to ~200 around it. The body is cut to fit and
# the card says so; Details shows the whole stored reason.
CARD_LIMIT = 1250
# The Slack markup a scraped business name could carry that acts rather than
# styles (`<!channel>`, `<@U…>`, `<url|text>`): shown as plain text, never live.
_MARKUP = re.compile(r"[<>]+")

# What `kernel.lead_followup.pitch_preview` returns.
CARD, HELD, SKIPPED, NO_EMAIL, NEEDS_DEMO, QA_FAILED, NOT_RAISED = (
    "card", "held", "skipped", "no_email", "needs_demo", "qa_failed", "not_raised")


def _plain(text: Any) -> str:
    return _MARKUP.sub(" ", str(text if text is not None else "")).strip()


def card_text(*, name: str, to: str, subject: str | None, body: str,
              preview_url: str | None, why: str, followup: bool = False) -> str:
    """The card, in plain words: what approving does, the preview, the owner,
    the subject, then the email itself. The body goes last and is cut to fit
    the card, with a note, never silently."""
    head = "\n".join([
        (f"Follow-up email to {_plain(name)} about the preview Websites built."
         if followup else
         f"Email {_plain(name)} the preview of their site, built by Websites."),
        f"Why now: {_plain(why)}",
        f"Preview: {_plain(preview_url) or 'none on record (the send will refuse)'}"
        + ("" if followup else " (not sent to anyone yet)"),
        f"Owner: {_plain(name)}, {_plain(to)}",
        f"Subject: {_plain(subject) or '(none)'}",
        "Email text:",
    ])
    text = _plain(body)
    room = CARD_LIMIT - len(head) - 1
    if len(text) > room:
        note = "\n[cut here to fit the card; Details shows the whole email]"
        text = text[:max(0, room - len(note))].rstrip() + note
    return f"{head}\n{text}"


async def shared(lead_id: int) -> bool:
    """True once an email to this lead left (sent, bounced or replied): only
    then may anything else (an AI call, an automatic reply) name the preview
    link. Before that the link is Miro's to approve on the card."""
    from . import _store
    try:
        return bool(await _store.has_outreach(int(lead_id)))
    except Exception:  # noqa: BLE001
        return False                    # unreadable: not shown to be shared
