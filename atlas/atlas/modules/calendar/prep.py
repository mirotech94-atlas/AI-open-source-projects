"""Meeting prep -- a card 30 minutes before every meeting.

Who, the last three interactions on record, open items between you, what
they probably want, and one thing worth raising. Entirely read-only: nothing
here touches the calendar or sends anything, so it is not registered as an
Action at all (there is nothing to gate at Tier 1 or ask approval for).

Uses `Need.PRIVATE` for the write-up itself -- this is relationship history,
the same class of content as personal mail, and it never needs to leave the
box to produce a decent paragraph.

Their company's latest news (the owner, 2026-09-28, from a "top 6 agents" reel:
"company news scraper" before each meeting): the one outside attendee's
company domain -- never a name, never the history -- goes to Perplexity's
search, and the answer is shown with the time it was read. Free-mail
domains and our own domain are skipped. `MEETING_NEWS=off` stops it.
"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from ...kernel.llm import LocalModelUnavailable, Message, Need, think
from ...kernel.memory import recall
from ...kernel.permissions import wrap_untrusted


def _attendee_names(event: dict) -> list[str]:
    names = []
    for attendee in event.get("attendees") or []:
        if attendee.get("self"):
            continue
        name = attendee.get("displayName") or attendee.get("email", "")
        if name:
            names.append(name)
    return names


# Mail providers, not companies: news about "gmail.com" says nothing about
# the person on the invite.
FREE_MAIL = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
    "msn.com", "yahoo.com", "ymail.com", "icloud.com", "me.com", "mac.com",
    "aol.com", "proton.me", "protonmail.com", "gmx.com", "gmx.net", "zoho.com",
})


def _domain(email: str) -> str:
    return email.rpartition("@")[2].strip().lower()


def company_domain(event: dict) -> str | None:
    """The first outside attendee's company domain, or None: our own domain
    (the `self` attendee's) and free-mail domains are skipped."""
    attendees = event.get("attendees") or []
    ours = {_domain(a.get("email", "")) for a in attendees if a.get("self")}
    for attendee in attendees:
        if attendee.get("self"):
            continue
        domain = _domain(attendee.get("email", ""))
        if domain and "." in domain and domain not in ours and domain not in FREE_MAIL:
            return domain
    return None


async def company_news(domain: str) -> tuple[str | None, datetime | None, str | None]:
    """`(text, read_at, None)`, or `(None, None, why)`. Only the domain leaves
    the box. The text is Perplexity's, shown to the owner as read, never acted on."""
    if os.getenv("MEETING_NEWS", "on").strip().lower() in {"off", "0", "false", "no"}:
        return None, None, "switched off (MEETING_NEWS=off)"
    from .. import _perplexity
    answer, why = await _perplexity.call(
        "perplexity_search",
        f"Latest news from the last 30 days about the company whose website is "
        f"{domain}. Three short bullet points, newest first.",
        caller="meeting_prep", max_chars=900)
    if answer is None:
        return None, None, why or "no answer"
    return answer.unwrapped.strip(), answer.read_at, None


async def upcoming_within(minutes_lo: int, minutes_hi: int) -> list[dict]:
    """Events starting between `minutes_lo` and `minutes_hi` minutes from
    now. Looks two days ahead so a window near a day boundary still sees
    everything relevant."""
    from . import _start, _upcoming  # lazy: avoid the init cycle
    now = datetime.now(UTC)
    lo = now + timedelta(minutes=minutes_lo)
    hi = now + timedelta(minutes=minutes_hi)
    events = await _upcoming(days=2)
    return [e for e in events if (s := _start(e)) and lo <= s <= hi]


async def prep_card(event: dict) -> str:
    """Who, the last three interactions, open items, what they probably
    want, one thing to raise -- in that order."""
    summary = event.get("summary") or "(no title)"
    names = _attendee_names(event)
    who = ", ".join(names) if names else "no other attendee listed"

    query = " ".join(names) or summary
    interactions = await recall(query, ["people", "threads", "conversations"], limit=6)
    commitments = await recall(query, ["commitments"], limit=3)

    def _dated(entry) -> str:
        # Every figure that reaches a brief carries when it was read. `at` is
        # when the memory was recorded, not fetched fresh, so it is shown
        # rather than implied.
        return (f"- {entry.content}  ({entry.at:%Y-%m-%d})" if entry.at
               else f"- {entry.content}")

    history = ("\n".join(_dated(m) for m in interactions[:3])
              if interactions else "no prior interactions on record")
    open_items = ("\n".join(_dated(c) for c in commitments)
                 if commitments else "nothing open on record")

    context = (
        f"Meeting: {summary}\n"
        f"Attendee(s): {who}\n\n"
        f"Last interactions:\n{history}\n\n"
        f"Open items:\n{open_items}"
    )
    # The facts above are read from memory, not written by a model, so a
    # local model that is down costs the write-up and nothing else. The
    # write-up is never sent to a cloud model instead.
    try:
        reply = await think(
            Need.PRIVATE,
            "You are preparing the owner for a meeting that starts in 30 minutes. "
            "Given the context below, write in under 100 words: what this "
            "person probably wants from the meeting, and one thing worth "
            "raising. No preamble, no repeating the history back verbatim.",
            [Message("user", wrap_untrusted(context, "calendar+memory"))],
            max_tokens=300)
        writeup = reply.text.strip()
    except LocalModelUnavailable as exc:
        writeup = f"No write-up: {exc}."

    news = ""
    if (domain := company_domain(event)) is not None:
        text, read_at, why = await company_news(domain)
        news = (f"\n\nTheir company ({domain}), news read "
                f"{read_at.astimezone(UTC):%H:%M} UTC:\n{text}"
                if text and read_at else
                f"\n\nTheir company ({domain}): no news read ({why}).")

    return (
        f"Who: {who}\n\n"
        f"Last interactions:\n{history}\n\n"
        f"Open items:\n{open_items}\n\n"
        f"{writeup}{news}"
    )
