"""Requests and messages to the Guardian and FlyCheap LinkedIn pages: seen,
tracked by what people want, and answered.

the owner, 2026-10-07: "go to guardian and flycheap pages on linkedin and track
the services that clients want and need and reply to them always based on
what they want -- i have linkedin premium", and "make sure when requests
come for either page you will see it and reply to it".

What was verified on 2026-10-07, through the server's signed-in session (two
read-only discovery runs; nothing clicked):

* Guardian has a service page ("Guardian's Services", vanity name
  80052234733835a927) whose admin view links its Requests tab to
  `GUARDIAN_REQUESTS_URL`. That tab shows two sections, "Premium requests"
  and "Direct requests", each empty ("No Premium requests." / "No direct
  requests."). The service page lists Network Support, Computer Networking
  and Cybersecurity, says "Contact for pricing", and carries the overview
  text in `FACTS["guardian"]`.
* The member-level provider page `PROVIDER_REQUESTS_URL` shows "Requests for
  your services will appear here." when empty.
* Both company page inboxes show "No messages yet" / "New messages your page
  receives will show up here." FlyCheap has no service page, so it cannot
  receive service requests -- only page messages.
* Every one of those pages also carries LinkedIn's messaging overlay (the owner's
  PERSONAL inbox, with its own contenteditable). Nothing here reads or types
  into it.

What has NOT been seen, because every surface was empty: a request or a
thread with something in it, and the reply UI of either. So:

* The collector counts 0 only when a page positively shows its empty-state
  string (the constants below). Anything else -- signed out, a hard stop, a
  failed read, a layout without the empty string and without rows ATLAS can
  recognise -- is `Health.UNKNOWN` with the reason, a screenshot and a
  stripped outline of the page (tags, roles, labels; never raw HTML) saved
  for the next session. Rows it does find in an unseen layout are recorded
  best effort and said to be so.
* `page_message` threads are answered through the page inbox's composer
  (`post_in_thread`): only on a URL under that page's own
  `/company/<id>/admin/inbox`, only with exactly one composer inside the
  thread pane and outside the overlay, the approved draft typed byte for
  byte and checked before Send, and the thread re-read afterwards. Its
  selectors are UNVERIFIED against a real thread; any miss refuses with
  nothing sent and a screenshot.
* `service_request` items are NEVER sent from here: LinkedIn's proposal form
  has not been seen, and guessing a form that may cost credits is not
  acceptable. The send guard refuses them with "the proposal form has not
  been seen yet; draft attached", which becomes a Tier 2 card carrying the
  draft for the owner to paste. The scan saves a screenshot and outline of the
  request page when it first sees one, so the next session can finish the
  flow. Anything that asks for credits or payment stops the run: money out
  is Tier 3.

Premium requests, first seen 2026-10-12 (a read-only discovery run that
clicked only the list items): the Requests tab lists them as
`li[role=tab]` rows (requester, service, age) with no link, and clicking one
loads its detail pane (title, location, age, requester card, "Project
details" as question/answer pairs). The URL never changes and no id is on
the page, so a request's key is a hash of what the requester wrote (service,
location, the Q&A) -- never the list position, the age or the name, which
LinkedIn hides ("--") until the request is unlocked. With Page Premium
lapsed every pane showed an "Unlock this request ... Reactivate Premium"
box and no message, reply or proposal control at all: such a request is
recorded 'held' with `LOCKED_NOTE`, is never drafted or answered, and the owner
gets one alert per request. The upsell is never clicked: buying Premium is
money out, Tier 3.

The narrow rule for unattended replies (the owner's words above, his date): ATLAS
answers only someone who wrote to the page or requested its services first --
never first contact -- once per inbound message, from a code-held fact sheet
per page, past `content_refusal` and `_guard_send` in code. Both actions are
Tier AUTO with a dry run (the guard) and an honest rollback: a sent LinkedIn
message cannot be unsent (deletion is Tier 3, permanently denied), so the
rollback suppresses the thread and records it, like `agency.send_reply`.
`LINKEDIN_REPLY_AUTOSEND=off` holds every send for a card. The reply signs
off as the page's assistant with "the owner sees this thread", and
`kernel.linkedin_replies` posts a Slack receipt of every send so that it is
true.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Any

from ...kernel.contracts import ActionResult, Health, Observation
from ...kernel.voice_persona import EMAIL_STYLE, reask, voice_problems, voice_refusal

log = logging.getLogger("atlas.linkedin.requests")

# ------------------------------------------------------------- surfaces

PROVIDER_REQUESTS_URL = "https://www.linkedin.com/service-marketplace/provider/requests/"
GUARDIAN_SERVICE_VANITY = "80052234733835a927"
SERVICE_PAGE_URL = f"https://www.linkedin.com/services/page/{GUARDIAN_SERVICE_VANITY}"
# Read off the service page's admin view on 2026-10-07 (its "Requests" link).
GUARDIAN_REQUESTS_URL = ("https://www.linkedin.com/service-marketplace/provider/"
                         f"requests?serviceVanityName={GUARDIAN_SERVICE_VANITY}")

# The empty states, verbatim from the 2026-10-07 reads. A count of 0 is
# reported only when one of these is on the page.
PROVIDER_EMPTY = "Requests for your services will appear here."
PREMIUM_EMPTY = "No Premium requests."
DIRECT_EMPTY = "No direct requests."
INBOX_EMPTY = "No messages yet"
INBOX_EMPTY_LINE = "New messages your page receives will show up here."

# The Requests tab's premium layout, by the data-test hooks read on
# 2026-10-12 (tests/fixtures/linkedin_requests_premium.json).
PREMIUM_LIST = "[data-test-service-marketplace-premium-service-requests-list]"
PREMIUM_ITEM = "[data-test-service-marketplace-premium-service-requests__list-item]"
DIRECT_NONE = "[data-test-service-marketplace-direct-requests-list__no-direct-requests]"
DETAIL = "[data-test-service-marketplace-service-requests__detail]"
LOCKED_NOTE = "locked: LinkedIn needs Page Premium to reply"
# The upsell box on a locked request. Its link is never clicked.
LOCK_MARK = re.compile(r"unlock this request|reactivate premium|upgrade to premium",
                       re.IGNORECASE)
# Any control in the pane that could answer the requester.
REPLY_CONTROL = re.compile(r"\b(?:message|reply|respond|send|submit|proposal|contact|"
                           r"chat)\b", re.IGNORECASE)

SOURCES = ("service_request", "page_message")
STATUSES = ("new", "drafted", "replied", "held", "suppressed")
OPEN = ("new", "drafted", "held")

WORDS_MAX = 1200
STALE_HOURS = float(os.getenv("LINKEDIN_REQUEST_STALE_HOURS", "2"))
MIN_GAP_MIN = float(os.getenv("LINKEDIN_REQUESTS_MIN_GAP_MIN", "25"))
LOCK_WAIT_S = 150.0
HARD_STOP_BACKOFF_H = 24


def inbox_url(page_key: str) -> str:
    from . import KNOWN_PAGES
    return f"https://www.linkedin.com/company/{KNOWN_PAGES[page_key]['id']}/admin/inbox/"


def inbox_prefix(page_key: str) -> str:
    from . import KNOWN_PAGES
    return f"https://www.linkedin.com/company/{KNOWN_PAGES[page_key]['id']}/admin/inbox"


@dataclass(frozen=True, slots=True)
class Surface:
    label: str
    page: str            # PAGES key the items belong to
    source: str          # service_request | page_message
    url: str
    kind: str            # provider | requests_tab | inbox


def surfaces() -> tuple[Surface, ...]:
    """What the scan reads, in order. Only FlyCheap and Guardian: those are
    the pages the owner named. The provider page is member-level; Guardian's is
    the only service page that exists (FlyCheap has none), so its items are
    Guardian's."""
    from . import PAGES
    every = (
        Surface("guardian-requests", "guardian", "service_request",
                GUARDIAN_REQUESTS_URL, "requests_tab"),
        Surface("provider-requests", "guardian", "service_request",
                PROVIDER_REQUESTS_URL, "provider"),
        Surface("guardian-inbox", "guardian", "page_message",
                inbox_url("guardian"), "inbox"),
        Surface("flycheap-inbox", "flycheap", "page_message",
                inbox_url("flycheap"), "inbox"),
    )
    # Only pages ATLAS still uses (LINKEDIN_PAGES_ACTIVE; FlyCheap retired by
    # the owner 2026-10-09): a retired page's inbox is never opened.
    return tuple(s for s in every if s.page in PAGES)


# ------------------------------------------------------------- parsing

@dataclass(slots=True)
class Item:
    page: str
    source: str
    surface: str
    external_key: str
    requester: str
    words: str
    service_raw: str = ""
    url: str | None = None
    from_us: bool = False
    locked: bool = False     # LinkedIn's Premium lock: no way to reply
    age: str = ""            # as LinkedIn shows it ("2d ago"); never in `words`
    location: str = ""

    @property
    def inbound_sha(self) -> str:
        return sha(self.words)


@dataclass(slots=True)
class Read:
    surface: Surface
    ok: bool                         # positively read: empty, or rows recognised
    items: list[Item] = field(default_factory=list)
    reason: str = ""
    empty: bool = False
    unverified_layout: bool = False


def sha(text: str) -> str:
    return hashlib.sha256(_norm(text).encode()).hexdigest()[:16]


def _norm(text: str) -> str:
    """Whitespace-insensitive, quote-insensitive text for comparisons."""
    t = re.sub(r"[‘’]", "'", text or "")
    t = re.sub(r"[“”]", '"', t)
    return re.sub(r"\s+", " ", t).strip().lower()


def _clean(line: str) -> str:
    return re.sub(r"\s+", " ", str(line or "")).strip()


def _thread_href(page_key: str, href: str) -> bool:
    """A link into one thread of THIS page's inbox. The shape is unverified
    (no thread existed on 2026-10-07): a path below /admin/inbox/ naming a
    thread or conversation, or a conversation/thread id in the query."""
    if not href.startswith(inbox_prefix(page_key)):
        return False
    rest = href[len(inbox_prefix(page_key)):]
    return bool(re.search(r"^/(?:thread|conversation)s?/[^/?#]+|"
                          r"[?&](?:conversationUrn|threadUrn|conversationId|threadId)=",
                          rest))


def _request_href(href: str) -> bool:
    """A link to one service request (unverified shape): anything under
    /service-marketplace/ that is not the list page itself, or a request or
    project id in the query."""
    m = re.match(r"https://www\.linkedin\.com(/service-marketplace/[^?#]*)(\?[^#]*)?", href)
    if not m:
        return False
    path, query = m.group(1).rstrip("/"), m.group(2) or ""
    if re.search(r"[?&](?:requestId|projectId|serviceRequestId)=", query):
        return True
    return path not in ("/service-marketplace/provider/requests",
                        "/service-marketplace/provider", "/service-marketplace")


SELF_PREFIX = re.compile(r"^(?:you|you sent|sent)\s*[:·-]\s*")
OUR_SIGNOFF = "(owner sees this thread)"
MATCH_HEAD = 32


def _stripped(text: str, page_name: str = "") -> str:
    """A list preview reduced to its words: no "You:" or "<page name>:"
    prefix, no trailing ellipsis (LinkedIn truncates previews)."""
    p = _norm(text)
    p = SELF_PREFIX.sub("", p)
    if page_name and p.startswith(page_name.lower() + ":"):
        p = p[len(page_name) + 1:].strip()
    return re.sub(r"\s*(?:…|\.\.\.)\s*$", "", p).strip()


def _is_ours(text: str, ours: list[str], page_name: str = "") -> bool:
    """Is `text` (a preview, possibly truncated) the start of, or inside, a
    reply ATLAS sent?"""
    p = _stripped(text, page_name)
    if OUR_SIGNOFF in p:
        return True
    head = p[:MATCH_HEAD]
    if len(head) < 12:
        return False
    return any(head in _norm(body) for body in ours if body)


def _from_us(preview: str, ours: list[str], page_name: str = "") -> bool:
    """The thread's last line is the page's own: LinkedIn prefixes it "You:"
    (or the page's name) in most lists, and failing that it is the start of a
    reply ATLAS sent, even truncated. Without this the next scan would read
    ATLAS's own reply as a new inbound and answer it. `_guard_send` checks
    the same thing again at send time, so this heuristic is not the only
    protection."""
    p = _norm(preview)
    if SELF_PREFIX.match(p):
        return True
    if page_name and p.startswith(page_name.lower() + ":"):
        return True
    return _is_ours(preview, ours, page_name)


def _service_in(lines: list[str], page_key: str) -> str:
    known = {s.lower() for s in FACTS[page_key].services} if page_key in FACTS else set()
    for line in lines:
        if line.lower() in known:
            return line
    return ""


def parse_rows(surface: Surface, rows: list[dict[str, Any]],
               ours: list[str] | None = None) -> list[Item]:
    """Items from the rows the page script returned ({href, lines}), keeping
    only links of the right shape for this surface. Best effort: the shapes
    are unverified."""
    items: list[Item] = []
    seen: set[str] = set()
    for row in rows or []:
        href = str(row.get("href") or "").split("#")[0]
        lines = [_clean(x) for x in (row.get("lines") or []) if _clean(x)]
        if not href or not lines:
            continue
        if surface.kind == "inbox":
            if not _thread_href(surface.page, href):
                continue
        elif not _request_href(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        requester = lines[0][:120]
        rest = [x for x in lines[1:] if not re.fullmatch(
            r"\d{1,2}:\d{2}\s*(?:am|pm)?|\w{3} \d{1,2}|yesterday|today|now|\d+[mhdw]",
            x, re.IGNORECASE)]
        words = " ".join(rest)[:WORDS_MAX]
        items.append(Item(
            page=surface.page, source=surface.source, surface=surface.label,
            external_key=href, requester=requester, words=words,
            service_raw=_service_in(rest, surface.page), url=href,
            from_us=surface.kind == "inbox" and _from_us(
                words, ours or [],
                FACTS[surface.page].name if surface.page in FACTS else "")))
    return items


def pane_lock(pane: dict[str, Any]) -> tuple[bool, bool]:
    """(the lock box is shown, a control that could answer is shown)."""
    lock = bool(LOCK_MARK.search(str(pane.get("text") or "")))
    control = False
    for ctl in pane.get("controls") or []:
        tag = str(ctl.get("tag") or "").lower()
        words = f"{ctl.get('text') or ''} {ctl.get('label') or ''}"
        if (tag in ("textarea", "form") or ctl.get("editable")
                or (not LOCK_MARK.search(words) and REPLY_CONTROL.search(words))):
            control = True
    return lock, control


def pane_matches(row: dict[str, Any], pane: dict[str, Any] | None) -> bool:
    """The pane shows this row's request: the same service, requester and
    age. Two requests can share a service ("Cybersecurity" twice on
    2026-10-12), so a pane still showing the previous one never passes."""
    if not isinstance(pane, dict):
        return False
    title, name, age = (_clean(row.get(k)) for k in ("title", "name", "age"))
    if not title or _clean(pane.get("title")) != title:
        return False
    creator, when = _clean(pane.get("creator")), _clean(pane.get("age"))
    return (not name or creator == name) and (not age or when == age)


def premium_key(title: str, location: str, qa: list[tuple[str, str]]) -> str:
    """A premium request's identity: what the requester wrote. LinkedIn puts
    no id on the page and the URL does not change when one is opened."""
    body = "\n".join([title, location] + [f"{q}\n{a}" for q, a in qa])
    return f"service-request:premium:{sha(body)}"


def parse_premium(surface: Surface, premium: dict[str, Any]
                  ) -> tuple[list[Item], list[str]]:
    """Items from the premium list and each request's detail pane, as the
    page script read them ({items: [{name, title, age}], panes: [pane|None]}).
    Pure. Returns (items, problems); a request whose pane was not read is a
    problem and is not recorded, so its key never comes from half a read."""
    items: list[Item] = []
    problems: list[str] = []
    rows = premium.get("items") or []
    panes = list(premium.get("panes") or [])
    panes += [None] * (len(rows) - len(panes))
    for n, (row, pane) in enumerate(zip(rows, panes, strict=False)):
        name = _clean(row.get("name"))[:120]
        title = _clean(row.get("title"))
        if not title:
            problems.append(f"premium request {n + 1} has no service on the list")
            continue
        if pane is None or not pane_matches(row, pane):
            problems.append(f"premium request {n + 1} ({title}): its detail pane "
                            "did not load")
            continue
        location = _clean(pane.get("location"))
        qa = [(_clean(q), _clean(a)) for q, a in (pane.get("qa") or []) if _clean(q)]
        lock, control = pane_lock(pane)
        if lock and control:
            problems.append(f"premium request {n + 1} ({title}) shows both the "
                            "Premium lock and a control that could answer; not "
                            "seen before")
        lines = ([f"Location: {location}"] if location else []) + [
            f"{q} {a}" for q, a in qa]
        key = premium_key(title, location, qa)
        if key in {i.external_key for i in items}:
            problems.append(f"premium request {n + 1} ({title}) reads exactly like "
                            "another one on the list; not recorded twice")
            continue
        items.append(Item(
            page=surface.page, source=surface.source, surface=surface.label,
            external_key=key,
            requester=name or _clean(pane.get("creator"))[:120],
            words="\n".join(lines)[:WORDS_MAX], service_raw=title, url=surface.url,
            locked=lock, age=_clean(row.get("age") or pane.get("age")),
            location=location))
    return items, problems


def _interpret_premium(surface: Surface, text: str, rows: list[dict[str, Any]],
                       ours: list[str] | None, premium: dict[str, Any]) -> Read:
    """The premium list as read on 2026-10-12. Recognised (ok) only when every
    listed request was read through its pane and the Direct section says it
    is empty; a Direct section with something in it is still unseen, and a
    recognised premium list never hides it."""
    items, problems = parse_premium(surface, premium)
    if not (DIRECT_EMPTY in text or premium.get("direct_empty")):
        extra = [i for i in parse_rows(surface, rows, ours)
                 if i.external_key not in {x.external_key for x in items}]
        return Read(surface, False, items + extra, (
            f"the Direct requests section shows something in a layout ATLAS has not "
            f"seen; {len(items)} premium and {len(extra)} other item(s) recorded"),
            unverified_layout=True)
    if problems:
        # Something is listed that was not fully read: like an unseen
        # layout, it outweighs the other page reading empty.
        return Read(surface, False, items, "; ".join(problems), unverified_layout=True)
    return Read(surface, True, items)


def interpret(surface: Surface, text: str, rows: list[dict[str, Any]],
              ours: list[str] | None = None,
              premium: dict[str, Any] | None = None) -> Read:
    """What one loaded page says. Pure: the text, rows and premium list are
    already read.

    0 only on a positive empty state. On the Requests tab both sections must
    say empty or be read; if one is neither, it holds something whose layout
    has not been seen, and that is UNKNOWN, never 0."""
    text = text or ""
    if (surface.kind in ("requests_tab", "provider") and premium
            and premium.get("list") and premium.get("items")):
        return _interpret_premium(surface, text, rows, ours, premium)
    if surface.kind == "requests_tab":
        premium_empty, direct = PREMIUM_EMPTY in text, DIRECT_EMPTY in text
        if premium_empty and direct:
            return Read(surface, True, empty=True)
        headers = "Premium requests" in text or "Direct requests" in text
        items = parse_rows(surface, rows, ours)
        # Which section is NOT saying empty: that is where something is.
        which = "premium" if direct else "direct" if premium_empty else "both"
        why = (f"the Requests tab shows something in the {which} section(s), in a "
               f"layout ATLAS has not seen; {len(items)} item(s) recorded best effort"
               if headers else "the Requests tab did not look like the one read on "
                               "2026-10-07 (no section headers, no empty state)")
        return Read(surface, False, items, why, unverified_layout=True)
    if surface.kind == "provider":
        if PROVIDER_EMPTY in text:
            return Read(surface, True, empty=True)
        items = parse_rows(surface, rows, ours)
        return Read(surface, False, items,
                    f"the provider requests page is not empty and its layout has not "
                    f"been seen; {len(items)} item(s) recorded best effort",
                    unverified_layout=True)
    # inbox
    if INBOX_EMPTY in text or INBOX_EMPTY_LINE in text:
        return Read(surface, True, empty=True)
    items = parse_rows(surface, rows, ours)
    if items:
        # Rows of the expected link shape were found: a positive read of
        # something, never a claim of 0. The layout itself is first seen now.
        return Read(surface, True, items, unverified_layout=True)
    return Read(surface, False, [],
                "the inbox shows no empty state and no thread rows ATLAS recognises")


# --------------------------------------------------------- demand labels

# Closed label sets, written from what each product is (the Guardian service
# page as read on 2026-10-07; FlyCheap's own /api/health facts: fare polls,
# routes, alerts). A model may only pick from these; anything else is
# 'other'.
LABELS: dict[str, tuple[str, ...]] = {
    "guardian": ("network_monitoring", "automatic_repair", "network_support",
                 "computer_networking", "cybersecurity", "other"),
    "flycheap": ("fare_alerts", "cheap_flight_search", "booking_help",
                 "account_help", "partnership", "other"),
}

# First match wins, so the most specific rules come first.
KEYWORDS: dict[str, tuple[tuple[str, str], ...]] = {
    "guardian": (
        ("automatic_repair", (r"auto(?:matic(?:ally)?)?[- ]?(?:repair|fix|remediat)|"
                              r"self[- ]heal|fix(?:es)? itself|"
                              r"restart(?:s|ing)? services")),
        ("cybersecurity", (r"cyber|secur|hack|breach|vulnerab|pen ?test|malware|ransom|"
                           r"phish|threat|intrusion|rogue device")),
        ("network_monitoring", (r"monitor|uptime|downtime|outage|alert|"
                                r"watch(?:es|ing)? (?:our|my|the)|certificate|ssl|tls|"
                                r"is (?:it|the site) down")),
        ("computer_networking", (r"wi-?fi|router|switch(?:es)?\b|cabl|vlan|access point|"
                                 r"network (?:design|setup|install)|"
                                 r"set ?up (?:a|our|my) network|computer networking")),
        ("network_support", (r"network support|it support|help ?desk|troubleshoot|"
                             r"slow (?:network|internet|connection)|"
                             r"managed (?:it|network)|support")),
    ),
    "flycheap": (
        ("partnership", (r"partner|affiliate|advertis|sponsor|collab|white[- ]label|"
                         r"api access|integrat")),
        ("account_help", (r"account|log ?in|sign ?up|password|unsubscribe|"
                          r"(?:not|never) (?:get|receiv)\w* (?:the )?(?:email|alert)")),
        ("booking_help", (r"book(?:ing|ed)?\b|ticket|refund|cancel|reservation|"
                          r"change (?:my|the) flight")),
        ("fare_alerts", (r"alert|notif|price drop|"
                         r"track(?:ing)? (?:a |the )?(?:fare|price|route)|"
                         r"watch (?:a |the )?(?:fare|price|route)")),
        ("cheap_flight_search", (r"cheap|deal|fare|flight|fly(?:ing)? (?:to|from)|"
                                 r"airline|round ?trip|one[- ]way")),
    ),
}


def keyword_label(page_key: str, text: str) -> str | None:
    low = (text or "").lower()
    for label, pattern in KEYWORDS.get(page_key, ()):
        if re.search(pattern, low):
            return label
    return None


def valid_label(page_key: str, raw: str) -> str:
    """The model's answer, kept only if it is exactly one label of the set."""
    word = re.sub(r"[^a-z_]", "", (raw or "").strip().lower().split("\n")[0])
    return word if word in LABELS.get(page_key, ()) else "other"


async def classify(page_key: str, service_raw: str, words: str) -> tuple[str, str]:
    """(label, how). Keyword rules first; then a model may pick a label from
    the closed set, with the request text wrapped as untrusted data; anything
    else is 'other'. The service LinkedIn names wins over a word in their
    text ("Computer Networking" stays that, even if they mention security)."""
    hit = (keyword_label(page_key, service_raw) if service_raw else None) \
        or keyword_label(page_key, f"{service_raw}\n{words}")
    if hit is not None:
        return hit, "keyword"
    if not (service_raw or words).strip():
        return "other", "none"
    try:
        from ...kernel.llm import Message, Need, think
        from ...kernel.permissions import wrap_untrusted
        labels = LABELS[page_key]
        system = ("Classify which service the person wants. Answer with exactly one "
                  "label from this list and nothing else: " + ", ".join(labels) + ".")
        material = wrap_untrusted(f"Service named: {service_raw}\nTheir words: {words}",
                                  f"linkedin {page_key} request")
        completion = await think(Need.TRIVIAL, system, [Message("user", material)],
                                 max_tokens=12)
        return valid_label(page_key, getattr(completion, "text", "") or ""), "model"
    except Exception:
        log.info("request label: model unavailable", exc_info=True)
        return "other", "none"


_AMOUNT = r"\d(?:[\d,]*\d)?(?:\.\d+)?\s*[kK]?"
_BUDGET = re.compile(
    rf"\bbudget\b[^\d\n]{{0,16}}{_AMOUNT}(?:\s*(?:-|to)\s*[$€£]?\s*{_AMOUNT})?|"
    rf"(?:[$€£]\s*|\b(?:usd|eur)\s*){_AMOUNT}(?:\s*(?:-|to)\s*[$€£]?\s*{_AMOUNT})?",
    re.IGNORECASE)
_TIMELINE = re.compile(r"\b(?:asap|urgent(?:ly)?|this week|next week|this month|"
                       r"next month|by (?:monday|tuesday|wednesday|thursday|friday|"
                       r"saturday|sunday|the end of \w+)|within \w+ (?:days?|weeks?|"
                       r"months?)|in \d+ (?:days?|weeks?|months?)|deadline[^.]{0,40})",
                       re.IGNORECASE)


def budget_and_timeline(words: str) -> tuple[str, str]:
    """As stated by the requester, verbatim snippets; '' when not stated."""
    b = _BUDGET.search(words or "")
    t = _TIMELINE.search(words or "")
    return (b.group(0).strip()[:80] if b else "", t.group(0).strip()[:80] if t else "")


# ------------------------------------------------------------- the store
#
# A thin layer over Postgres (db/schema.sql: linkedin_requests,
# linkedin_request_drafts). Tests replace these functions.

async def _all(sql: str, *args: Any) -> list[Any]:
    from ...kernel.state import fetch_all
    return await fetch_all(sql, *args)


async def _one(sql: str, *args: Any) -> Any | None:
    from ...kernel.state import fetch_one
    return await fetch_one(sql, *args)


async def _exec(sql: str, *args: Any) -> Any:
    from ...kernel.state import execute_sql
    return await execute_sql(sql, *args)


async def upsert_seen(item: Item, label: str, label_by: str, budget: str,
                      timeline: str) -> dict[str, Any] | None:
    """Record an inbound item. A thread that was answered, or held for the owner,
    and has new words from them since goes back to 'new' (one reply per
    inbound; the held card's draft answered the older message). A suppressed
    one stays suppressed.

    A request LinkedIn shows locked (`item.locked`) is written 'held' with
    `LOCKED_NOTE` in the same statement, so no reply driver can claim it in
    between, and stays held while it is locked whatever its words; it goes
    to 'new' only once a scan reads it unlocked. Returns {id, inserted,
    status, note, prior_status, prior_note} (prior_* None when it is new)."""
    note = locked_note(item) if item.locked else ""
    row = await _one(
        """WITH prior AS (
             SELECT status, note FROM linkedin_requests
             WHERE page = $1 AND source = $2 AND external_key = $3),
           up AS (
           INSERT INTO linkedin_requests
             (page, source, external_key, surface, requester, words, inbound_sha,
              service_raw, label, label_by, budget, timeline, url, status, note)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,
                   CASE WHEN $14::boolean THEN 'held' ELSE 'new' END, $15)
           ON CONFLICT (page, source, external_key) DO UPDATE SET
             last_seen = now(),
             requester = EXCLUDED.requester,
             words = EXCLUDED.words,
             last_inbound_at = CASE WHEN linkedin_requests.inbound_sha
                                         <> EXCLUDED.inbound_sha
                                    THEN now() ELSE linkedin_requests.last_inbound_at END,
             inbound_sha = EXCLUDED.inbound_sha,
             status = CASE WHEN linkedin_requests.status = 'suppressed'
                           THEN 'suppressed'
                           WHEN $14::boolean
                            AND linkedin_requests.status IN ('new', 'drafted', 'held')
                           THEN 'held'
                           WHEN NOT $14::boolean AND linkedin_requests.status = 'held'
                            AND linkedin_requests.note LIKE 'locked:%'
                           THEN 'new'
                           WHEN linkedin_requests.status = 'replied'
                            AND linkedin_requests.replied_sha
                                IS DISTINCT FROM EXCLUDED.inbound_sha
                           THEN 'new'
                           WHEN linkedin_requests.status = 'held'
                            AND linkedin_requests.inbound_sha <> EXCLUDED.inbound_sha
                           THEN 'new'
                           ELSE linkedin_requests.status END,
             note = CASE WHEN linkedin_requests.status = 'suppressed'
                         THEN linkedin_requests.note
                         WHEN $14::boolean
                          AND linkedin_requests.status IN ('new', 'drafted', 'held')
                         THEN $15
                         WHEN NOT $14::boolean AND linkedin_requests.status = 'held'
                          AND linkedin_requests.note LIKE 'locked:%'
                         THEN ''
                         ELSE linkedin_requests.note END
           RETURNING id, (xmax = 0) AS inserted, status, note)
           SELECT up.id, up.inserted, up.status, up.note,
                  (SELECT status FROM prior) AS prior_status,
                  (SELECT note FROM prior) AS prior_note
           FROM up""",
        item.page, item.source, item.external_key, item.surface, item.requester,
        item.words[:WORDS_MAX], item.inbound_sha, item.service_raw, label, label_by,
        budget, timeline, item.url, bool(item.locked), note)
    return dict(row) if row is not None else None


def locked_note(item: Item) -> str:
    """`LOCKED_NOTE` and what the scan saw, the age as read at that time."""
    seen = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    age = f", listed {item.age}" if item.age else ""
    return f"{LOCKED_NOTE} (read {seen} UTC{age})"[:500]


def newly_locked(item: Item, row: dict[str, Any] | None) -> bool:
    """This scan is the first to see the request locked: insert, or a row
    that was not already held as locked. The one alert hangs on this, kept
    in Postgres, so a deploy or a second surface never repeats it."""
    if not item.locked or not row or row.get("status") != "held":
        return False
    return not (row.get("prior_status") == "held"
                and str(row.get("prior_note") or "").startswith("locked:"))


async def mark_answered_on_page(item: Item) -> None:
    """The thread's latest line is the page's own (the owner answered it himself,
    or ATLAS did): it is answered. Its current line becomes the inbound the
    answer covers, so the requester's next message reopens it. Suppressed
    stays suppressed."""
    await _exec(
        """UPDATE linkedin_requests SET status = 'replied', claimed_at = NULL,
             inbound_sha = $4, replied_sha = $4, last_seen = now(),
             replied_at = COALESCE(replied_at, now())
           WHERE page = $1 AND source = $2 AND external_key = $3
             AND status <> 'suppressed'""",
        item.page, item.source, item.external_key, item.inbound_sha)


async def sent_in_thread(request_id: int) -> list[dict[str, Any]]:
    """Every reply posted (or attempted) in this thread, newest first."""
    rows = await _all(
        """SELECT body, send_attempted_at,
                  send_attempted_at > date_trunc('day', now()) AS today
           FROM linkedin_request_drafts
           WHERE request_id = $1 AND send_attempted_at IS NOT NULL
           ORDER BY id DESC""", int(request_id))
    return [dict(r) for r in rows]


async def known_keys() -> set[tuple[str, str, str]]:
    rows = await _all("SELECT page, source, external_key FROM linkedin_requests")
    return {(r["page"], r["source"], r["external_key"]) for r in rows}


async def our_sent_bodies(limit: int = 50) -> list[str]:
    rows = await _all(
        """SELECT body FROM linkedin_request_drafts
           WHERE status IN ('sent', 'unverified') ORDER BY id DESC LIMIT $1""", limit)
    return [str(r["body"]) for r in rows]


async def get_request(request_id: int) -> dict[str, Any] | None:
    row = await _one("SELECT * FROM linkedin_requests WHERE id = $1", int(request_id))
    return dict(row) if row is not None else None


async def awaiting_reply(limit: int) -> list[dict[str, Any]]:
    """New or drafted requests whose latest inbound is under three days old.
    Older ones are the owner's: the check has been red about them for days."""
    rows = await _all(
        """SELECT id, page, source FROM linkedin_requests
           WHERE status IN ('new', 'drafted')
             AND last_inbound_at > now() - interval '3 days'
           ORDER BY first_seen LIMIT $1""", limit)
    from . import PAGES
    # A retired page's rows stay exactly as they are; nothing is drafted or sent.
    return [dict(r) for r in rows if str(r["page"]) in PAGES]


async def claim(request_id: int) -> bool:
    """One driver at a time per request: the atomic claim. A claim older than
    an hour is a crashed run and may be taken again."""
    row = await _one(
        """UPDATE linkedin_requests SET claimed_at = now()
           WHERE id = $1 AND status IN ('new', 'drafted')
             AND (claimed_at IS NULL OR claimed_at < now() - interval '1 hour')
           RETURNING id""", int(request_id))
    return row is not None


async def release(request_id: int) -> None:
    await _exec("UPDATE linkedin_requests SET claimed_at = NULL WHERE id = $1",
                int(request_id))


async def set_status(request_id: int, status: str, note: str = "") -> None:
    if status not in STATUSES:
        raise ValueError(f"unknown status {status!r}")
    await _exec(
        """UPDATE linkedin_requests SET status = $2, note = $3, claimed_at = NULL
           WHERE id = $1 AND (status <> 'suppressed' OR $2 = 'suppressed')""",
        int(request_id), status, note[:500])


async def insert_draft(request_id: int, inbound_sha: str, body: str) -> int | None:
    row = await _one(
        """INSERT INTO linkedin_request_drafts (request_id, inbound_sha, body, body_sha)
           VALUES ($1, $2, $3, $4) RETURNING id""",
        int(request_id), inbound_sha, body, hashlib.sha256(body.encode()).hexdigest())
    return int(row["id"]) if row is not None else None


async def get_draft(draft_id: int) -> dict[str, Any] | None:
    row = await _one("SELECT * FROM linkedin_request_drafts WHERE id = $1", int(draft_id))
    return dict(row) if row is not None else None


async def latest_draft(request_id: int) -> dict[str, Any] | None:
    row = await _one(
        """SELECT * FROM linkedin_request_drafts WHERE request_id = $1
           ORDER BY id DESC LIMIT 1""", int(request_id))
    return dict(row) if row is not None else None


async def set_draft_status(draft_id: int, status: str) -> None:
    await _exec(
        """UPDATE linkedin_request_drafts SET status = $2,
             sent_at = CASE WHEN $2 = 'sent' THEN now() ELSE sent_at END
           WHERE id = $1""", int(draft_id), status)


async def mark_send_attempt(draft_id: int) -> None:
    await _exec("UPDATE linkedin_request_drafts SET send_attempted_at = now() "
                "WHERE id = $1", int(draft_id))


async def mark_replied(request_id: int, inbound_sha: str) -> None:
    await _exec(
        """UPDATE linkedin_requests SET status = 'replied', replied_at = now(),
             replied_sha = $2, claimed_at = NULL, note = ''
           WHERE id = $1""", int(request_id), inbound_sha)


async def sent_today(page_key: str) -> int:
    row = await _one(
        """SELECT count(*) AS n FROM linkedin_request_drafts d
           JOIN linkedin_requests r ON r.id = d.request_id
           WHERE r.page = $1 AND d.send_attempted_at > date_trunc('day', now())""",
        page_key)
    return int(row["n"]) if row is not None else 0


async def open_requests() -> list[dict[str, Any]]:
    rows = await _all(
        """SELECT id, page, source, requester, status, label, first_seen,
                  last_inbound_at, note
           FROM linkedin_requests WHERE status IN ('new', 'drafted', 'held')
           ORDER BY last_inbound_at""")
    from . import PAGES
    # A retired page's open rows are kept but never turn the check red:
    # nothing would answer them, so nobody could clear it.
    return [dict(r) for r in rows if str(r["page"]) in PAGES]


async def demand_rows() -> list[dict[str, Any]]:
    rows = await _all(
        """SELECT page, label,
                  count(*) FILTER (WHERE first_seen > now() - interval '7 days') AS d7,
                  count(*) AS d30
           FROM linkedin_requests WHERE first_seen > now() - interval '30 days'
           GROUP BY page, label ORDER BY page, d30 DESC""")
    return [dict(r) for r in rows]


# ------------------------------------------------------------- demand

_DEMAND: dict[str, Any] = {}


async def demand() -> dict[str, Any]:
    """Counts by page and label over 7 and 30 days, with when they were
    read. Read-only. Cached for `brief()`, which cannot query."""
    rows = await demand_rows()
    out: dict[str, Any] = {"read_at": datetime.now(UTC).isoformat(timespec="seconds"),
                           "pages": {}}
    for r in rows:
        page = out["pages"].setdefault(str(r["page"]), {"7d": {}, "30d": {}})
        if int(r["d7"]):
            page["7d"][str(r["label"])] = int(r["d7"])
        page["30d"][str(r["label"])] = int(r["d30"])
    _DEMAND.clear()
    _DEMAND.update(out)
    return out


def demand_line() -> str:
    """One clause for the module brief, from the last check's read. Empty
    when no check has read it in this process."""
    if not _DEMAND:
        return ""
    parts = []
    for page, spans in sorted(_DEMAND.get("pages", {}).items()):
        top = sorted(spans["30d"].items(), key=lambda kv: -kv[1])[:3]
        if top:
            parts.append(f"{page}: " + ", ".join(f"{k} {v}" for k, v in top))
    when = str(_DEMAND.get("read_at", ""))[11:16]
    return (("page requests (30d) " + "; ".join(parts)) if parts
            else "no page requests in 30 days") + f" (read {when} UTC)"


# ------------------------------------------------------------- facts

@dataclass(frozen=True, slots=True)
class Facts:
    name: str                 # as LinkedIn shows the page
    sign: str                 # who signs the reply
    link: str                 # the one link a reply may carry
    host: str
    contact: str              # the one address a reply may carry
    what: str
    services: tuple[str, ...]
    role: str
    extra: str = ""


# No digit appears in a fact sheet: the send guard refuses every figure, so
# a fact with a number in it could never be sent. No price exists in code
# for either product, and the Guardian service page says "Contact for
# pricing", so neither sheet carries one.
FACTS: dict[str, Facts] = {
    "guardian": Facts(
        name="Guardian", sign="Guardian's assistant",
        link="https://vault.example", host="vault.example", contact="support@vault.example",
        what=("Guardian watches the network a business is responsible for: devices, "
              "servers, sites and certificates. For routine failures it performs the "
              "repair instead of sending another alert: services restarted, log "
              "directories cleared, updates installed. The repair list is fixed and "
              "allow-listed, and anything disruptive asks first. One sensor per site, "
              "every site on one page, and what it fixed is already written down "
              "when the client asks."),
        services=("Network Support", "Computer Networking", "Cybersecurity"),
        role=("the owner (the author) founded and owns Guardian. Never call him a "
              "co-founder of it."),
        extra=("Availability: remote, or in person in your city. "
               "Pricing: on request only. Never state or estimate a price; say the owner "
               "will follow up on pricing in this thread."),
    ),
    "flycheap": Facts(
        name="FlyCheap", sign="FlyCheap's assistant",
        link="https://flycheap.example", host="flycheap.example", contact="owner@flycheap.example",
        what=("FlyCheap is a flight-deal site. It tracks fares on the routes people "
              "follow and sends an email alert when a price drops, with a link to "
              "book the deal."),
        services=("Fare alerts", "Flight deals"),
        role=("the owner (the author) is co-founder and developer of FlyCheap. Never "
              "call him its owner or founder."),
        extra=("Never state a fare, a price or a saving. Never promise that a fare "
               "will drop or that a seat is available."),
    ),
}


def signoff(page_key: str) -> str:
    return f"\n\n— {FACTS[page_key].sign} (the owner sees this thread)"


MAX_CHARS = int(os.getenv("LINKEDIN_REPLY_MAX_CHARS", "600"))
DAILY_CAP = int(os.getenv("LINKEDIN_REPLY_DAILY_CAP", "10"))
THREAD_DAILY_CAP = int(os.getenv("LINKEDIN_REPLY_THREAD_CAP", "2"))
HELD = "held for you"
SERVICE_FORM_UNSEEN = ("the proposal form has not been seen yet; draft attached -- "
                       "approving cannot post it, paste it on the request yourself")

SYSTEM = (
    "You write a short reply from {name}'s assistant on LinkedIn to someone who "
    "wrote to the {name} page or requested its services. Answer what they asked "
    "for, using only the facts below; if the facts do not cover it, say the owner will "
    "answer here. Two to four short sentences, plain text, no greeting line with "
    "a title, no sign-off (one is added).\n"
    "Hard rules: no numbers or digits at all; no prices, fees, discounts or "
    "anything free; no dates, days, times or delivery promises; no guarantees or "
    "promises; no link other than {link}; no email address other than {contact}; "
    "never claim to be the owner or a person -- you are the page's assistant and the owner "
    "reads this thread. For a quote, a date or a decision, say the owner will follow "
    "up here.\n\n" + EMAIL_STYLE + "\n\n"
    "Facts about {name}:\n{what}\nServices: {services}.\n{role}\n{extra}"
)


def autosend_enabled() -> bool:
    """`LINKEDIN_REPLY_AUTOSEND`: on by default (the owner, 2026-10-07); 'off'
    holds every reply for a card."""
    return os.getenv("LINKEDIN_REPLY_AUTOSEND", "on").strip().lower() not in (
        "0", "off", "false", "no")


def answered_by_card() -> bool:
    """An approved card's run (`approvals.decide` names it `proposal:<id>`):
    the holds are passed, the guards are not."""
    from ...kernel import idempotency
    return (idempotency.current_run() or "").startswith("proposal:")


# ------------------------------------------------------------- the rules

_DIGIT = re.compile(r"\d")
_MONEY = re.compile(r"[$€£¥%]|\b(?:usd|eur|gbp|dollars?|euros?|bucks|percent|per\s+cent|"
                    r"cents?)\b", re.IGNORECASE)
_FREE = re.compile(r"\b(?:for\s+free|free\s+of\s+charge|free\s+trial|at\s+no\s+cost|"
                   r"no\s+charge|discount(?:ed|s)?|coupon|promo\s+code|"
                   r"complimentary)\b", re.IGNORECASE)
_WHEN = re.compile(
    r"\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|"
    r"tonight|today|yesterday|next\s+(?:week|month|year)|this\s+(?:week|month|"
    r"weekend)|end\s+of\s+(?:the\s+)?(?:day|week|month)|january|february|april|june|"
    r"july|august|september|october|november|december|asap|right\s+away|"
    r"same[-\s]day|overnight|within\s+(?:an?\s+|one\s+|two\s+|three\s+|a\s+few\s+|"
    r"a\s+couple\s+of\s+)?(?:minutes?|hours?|days?|weeks?|months?)|"
    r"in\s+(?:an?\s+|one\s+|two\s+|three\s+|a\s+few\s+)(?:minutes?|hours?|days?|"
    r"weeks?|months?))\b", re.IGNORECASE)
_PROMISE = re.compile(r"\b(?:guarantee[sd]?|guaranteeing|promise[sd]?|promising|"
                      r"warrant(?:y|ies)|we\s+will\s+deliver|delivered\s+by|"
                      r"deliver\s+(?:it|this|that|them)?\s*(?:by|within|in)|"
                      r"100\s*%|risk[-\s]free)\b", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_HOST = re.compile(r"(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})(/[^\s)]*)?", re.IGNORECASE)


def content_refusal(text: str, page_key: str) -> str | None:
    """Every content rule a page reply must pass, first failure first. The
    same function judges the model's draft and, again, the text at send."""
    from ..agency.playbook import honesty_refusal
    facts = FACTS.get(page_key)
    if facts is None:
        return f"no fact sheet for page {page_key!r}"
    body = text or ""
    if len(body) > MAX_CHARS:
        return f"it is {len(body)} characters; the limit is {MAX_CHARS}"
    if not body.endswith(signoff(page_key)):
        return "it does not end with the assistant sign-off"
    # Only the contact address is lifted out before the scans. The link is
    # judged by the host rule below, so "https://vault.example/anything" is
    # still caught for its path.
    bare = body.replace(facts.contact, " ")
    for addr in _EMAIL.findall(bare):
        return f"it names an email address other than the page's own ({addr!r})"
    for m in _HOST.finditer(bare):
        host = m.group(1).lower().removeprefix("www.")
        path = (m.group(2) or "").rstrip("/.,")
        if host != facts.host or path:
            return f"it carries a link other than the page's own ({m.group(0)!r})"
    if (hit := _DIGIT.search(bare)) is not None:
        return (f"it contains a figure ({hit.group(0)!r}); prices, dates, times and "
                "counts are the owner's to give")
    if (hit := _MONEY.search(bare)) is not None:
        return f"it talks money ({hit.group(0)!r}); prices are the owner's to give"
    if (hit := _FREE.search(bare)) is not None:
        return f"it offers something free or discounted ({hit.group(0)!r})"
    if (hit := _WHEN.search(bare)) is not None:
        return f"it names a date or a time ({hit.group(0)!r})"
    if (hit := _PROMISE.search(bare)) is not None:
        return f"it makes a promise or a guarantee ({hit.group(0)!r})"
    return honesty_refusal(bare) or voice_refusal(bare)


# ------------------------------------------------------- draft_request_reply

async def draft_request_reply(request_id: int) -> ActionResult:
    """Tier AUTO: writes a draft row from the page's fact sheet; sends
    nothing. The requester's words reach the model wrapped as untrusted."""
    from ...kernel.llm import Message, Need, think
    from ...kernel.permissions import wrap_untrusted
    request, why = await _draftable(int(request_id))
    if request is None:
        return ActionResult(False, f"refused: {why}")
    page_key = str(request["page"])
    facts = FACTS[page_key]
    system = SYSTEM.format(name=facts.name, link=facts.link, contact=facts.contact,
                           what=facts.what, services=", ".join(facts.services),
                           role=facts.role, extra=facts.extra)
    kind = ("requested the page's services" if request["source"] == "service_request"
            else "messaged the page")
    material = wrap_untrusted(
        f"{request.get('requester') or 'Someone'} {kind}.\n"
        f"Service they named: {request.get('service_raw') or '(none)'}\n"
        f"Their words:\n{request.get('words') or '(none read)'}",
        f"linkedin {page_key} {request['source']}")
    turns = [Message("user", material)]
    completion = await think(Need.DRAFT, system, turns, max_tokens=300, answer=True)
    text = (getattr(completion, "text", "") or "").strip()
    if not text:
        return ActionResult(False, "the model returned nothing")
    if bad := voice_problems(text):
        text = await reask(partial(think, answer=True), Need.DRAFT, system, turns, text, bad,
                           max_tokens=300) or text
    body = text + signoff(page_key)
    refused = content_refusal(body, page_key)
    if refused is not None:
        return ActionResult(False, f"refused the draft: {refused}",
                            evidence={"refused": refused, "text": body})
    draft_id = await insert_draft(int(request_id), str(request["inbound_sha"]), body)
    if draft_id is None:
        return ActionResult(False, "failed to write the reply draft")
    await set_status(int(request_id), "drafted")
    return ActionResult(
        True, f"drafted reply #{draft_id} to {request.get('requester')} on the "
              f"{facts.name} page",
        evidence={"draft_id": draft_id, "text": body, "page": page_key,
                  "source": request["source"], "requester": request.get("requester"),
                  "url": request.get("url")},
        rollback_token=str(draft_id))


async def _draftable(request_id: int) -> tuple[dict[str, Any] | None, str]:
    request = await get_request(request_id)
    if request is None:
        return None, f"no such request: {request_id}"
    if request["page"] not in FACTS:
        return None, f"no fact sheet for page {request['page']!r}"
    from . import PAGES
    if request["page"] not in PAGES:
        return None, (f"the {request['page']} page is not active "
                      "(LINKEDIN_PAGES_ACTIVE); nothing is drafted for it")
    if request["status"] == "suppressed":
        return None, "the thread is suppressed"
    if request["status"] not in ("new", "drafted"):
        return None, f"request {request_id} is not waiting for a reply ({request['status']})"
    if request.get("replied_sha") and request["replied_sha"] == request["inbound_sha"]:
        return None, "their latest message is already answered (one reply per inbound)"
    return request, ""


async def draft_request_reply_dry(request_id: int) -> ActionResult:
    request, why = await _draftable(int(request_id))
    if request is None:
        return ActionResult(False, why)
    return ActionResult(True, f"would draft a reply to {request.get('requester')} on "
                              f"the {FACTS[request['page']].name} page")


async def draft_request_reply_rollback(token: str) -> ActionResult:
    if not token:
        return ActionResult(True, "nothing was written; nothing to roll back")
    await set_draft_status(int(token), "discarded")
    return ActionResult(True, f"withdrew reply draft {token}")


# ------------------------------------------------------- send_request_reply

async def _guard_send(draft_id: int) -> tuple[dict[str, Any] | None, str | None]:
    """Every rule at send time, first failure first; then the holds, which an
    approved card passes."""
    draft = await get_draft(int(draft_id))
    if draft is None:
        return None, f"no such reply draft: {draft_id}"
    if draft.get("send_attempted_at") is not None:
        return None, (f"a send of draft {draft_id} was already attempted at "
                      f"{draft['send_attempted_at']} and could not be confirmed; look "
                      "at the thread yourself -- it is never sent twice")
    if draft.get("status") != "draft":
        return None, f"draft {draft_id} is not an unsent draft ({draft.get('status')})"
    request = await get_request(int(draft["request_id"]))
    if request is None:
        return None, f"no such request: {draft['request_id']}"
    page_key = str(request["page"])
    from . import PAGES
    if page_key not in PAGES:
        return None, (f"the {page_key} page is not active (LINKEDIN_PAGES_ACTIVE); "
                      "nothing is sent from its inbox")
    if request["status"] == "suppressed":
        return None, "the thread is suppressed; nothing more goes to it"
    if request["source"] == "service_request":
        return None, SERVICE_FORM_UNSEEN
    if request["source"] != "page_message":
        return None, f"unknown source {request['source']!r}"
    url = str(request.get("url") or "")
    if not url.startswith(inbox_prefix(page_key)) or "/messaging/" in url:
        return None, ("the thread's link is not under the page's own inbox, so "
                      "nothing is typed anywhere")
    if draft["inbound_sha"] != request["inbound_sha"]:
        return None, "they wrote again since this draft; it answers an older message"
    if request.get("replied_sha") and request["replied_sha"] == request["inbound_sha"]:
        return None, "their latest message is already answered (one reply per inbound)"
    # The backstop to the scan's `_from_us`: if what is on record as their
    # latest words is the start of a reply already posted in this thread,
    # it is ATLAS's own message and is never answered.
    sent = await sent_in_thread(int(request["id"]))
    if _is_ours(str(request.get("words") or ""), [str(s["body"]) for s in sent],
                FACTS[page_key].name):
        return None, ("their 'latest message' reads as the page's own reply; ATLAS "
                      "never answers itself")
    refused = content_refusal(str(draft["body"]), page_key)
    if refused is not None:
        return None, f"the draft no longer passes: {refused}"
    if not answered_by_card():
        if sum(1 for s in sent if s.get("today")) >= THREAD_DAILY_CAP:
            return None, (f"{HELD}: {THREAD_DAILY_CAP} replies already went to this "
                          "thread today (LINKEDIN_REPLY_THREAD_CAP)")
        if await sent_today(page_key) >= DAILY_CAP:
            return None, (f"{HELD}: {DAILY_CAP} replies already went out on the "
                          f"{FACTS[page_key].name} page today (LINKEDIN_REPLY_DAILY_CAP)")
        if not autosend_enabled():
            return None, f"{HELD}: automatic replies are off (LINKEDIN_REPLY_AUTOSEND)"
    return {"draft": draft, "request": request}, None


# A page that asks for credits or payment to answer: stop. Money out is Tier 3.
PAYWALL = re.compile(r"inmail credits?|credits? (?:left|remaining|to (?:send|submit|"
                     r"reply))|submit(?:ting)? (?:a )?proposal costs?|costs? \d+ credits?|"
                     r"buy (?:more )?credits|add (?:a )?payment method|"
                     r"upgrade to (?:send|reply|respond)", re.IGNORECASE)

Sender = Callable[[str, str, str, Callable[[], Awaitable[None]]],
                  Awaitable[tuple[str, str]]]


async def send_request_reply(draft_id: int) -> ActionResult:
    """Tier AUTO, only past `_guard_send`. Posts the approved draft in the
    page's own inbox thread, byte for byte, and confirms it there."""
    ready, why = await _guard_send(int(draft_id))
    if ready is None:
        return ActionResult(False, f"refused: {why}")
    draft, request = ready["draft"], ready["request"]
    page_key = str(request["page"])

    async def before_click() -> None:
        await mark_send_attempt(int(draft_id))

    state, detail = await post_in_thread(page_key, str(request["url"]),
                                         str(draft["body"]), before_click)
    evidence = {"draft_id": int(draft_id), "request_id": int(request["id"]),
                "page": page_key, "requester": request.get("requester"),
                "text": draft["body"], "url": request.get("url")}
    if state == "not_sent":
        return ActionResult(False, f"not sent: {detail}", evidence=evidence)
    if state == "unverified":
        await set_draft_status(int(draft_id), "unverified")
        await set_status(int(request["id"]), "held",
                         f"Send was clicked but the reply could not be seen in the "
                         f"thread afterwards: {detail}")
        return ActionResult(False, f"Send was clicked but the reply could not be "
                                   f"confirmed in the thread ({detail}); it is never "
                                   "retried -- look at the thread yourself",
                            evidence={**evidence, "unverified": True})
    await set_draft_status(int(draft_id), "sent")
    await mark_replied(int(request["id"]), str(draft["inbound_sha"]))
    return ActionResult(True, f"replied to {request.get('requester')} on the "
                              f"{FACTS[page_key].name} page ({detail})",
                        evidence=evidence, rollback_token=str(int(draft_id)))


async def send_request_reply_dry(draft_id: int) -> ActionResult:
    ready, why = await _guard_send(int(draft_id))
    if ready is None:
        return ActionResult(False, str(why))
    request, draft = ready["request"], ready["draft"]
    return ActionResult(
        True, f"would reply to {request.get('requester')} in the "
              f"{FACTS[request['page']].name} page inbox:\n{draft['body']}",
        evidence={"text": draft["body"], "url": request.get("url")})


async def send_request_reply_rollback(token: str) -> ActionResult:
    """A sent LinkedIn message cannot be unsent -- deleting it is Tier 3,
    permanently denied -- and this does not pretend to. It suppresses the
    thread, so nothing further goes to it, and records why."""
    if not token:
        return ActionResult(True, "nothing was sent for this run; nothing to roll back")
    draft = await get_draft(int(token))
    if draft is None:
        return ActionResult(False, f"no such reply draft: {token}")
    await set_draft_status(int(token), "rolled_back")
    await set_status(int(draft["request_id"]), "suppressed",
                     f"reply #{token} rolled back after it was sent "
                     f"({datetime.now(UTC).isoformat(timespec='minutes')}): a LinkedIn "
                     "message cannot be unsent, so nothing further goes to this thread")
    return ActionResult(
        True, f"reply #{token} was already posted and cannot be unsent (deletion is "
              "permanently denied); the thread is suppressed so nothing follows it",
        evidence={"suppressed": int(draft["request_id"]), "unsent": False})


# ------------------------------------------------------------- the browser

# The page script: rows inside <main>, never inside the messaging overlay
# (the owner's personal inbox) or the global nav. Text and hrefs only.
ROWS_JS = r"""
() => {
  const main = document.querySelector('main') || document.body;
  const outside = el => !!el.closest(
    'aside, header, nav, footer, #msg-overlay, [class*="msg-overlay"]');
  const out = [];
  for (const a of main.querySelectorAll('a[href]')) {
    if (outside(a)) continue;
    let href = a.getAttribute('href');
    try { href = new URL(href, location.href).href } catch (e) {}
    const box = a.closest('li, [role=listitem], article') || a;
    const lines = (box.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
    out.push({href: href.split('#')[0], lines: lines.slice(0, 14)});
  }
  return out;
}
"""

# The premium list by its data-test hooks. LinkedIn renders each field twice
# (visible and screen-reader); the first line is the visible one. `controls`
# counts links and buttons inside a row: a row with any is never clicked.
PREMIUM_JS = r"""
() => {
  const first = el => el ? ((el.innerText || '').split('\n').map(s => s.trim())
                             .filter(Boolean)[0] || '') : '';
  const list = document.querySelector('__LIST__');
  const items = Array.from(document.querySelectorAll('__ITEM__')).map(li => ({
    name: first(li.querySelector('[data-test-service-requests-list-item__creator-name]')),
    title: first(li.querySelector('[data-test-service-requests-list-item__project-title]')),
    age: first(li.querySelector('[data-test-service-requests-list-item__project-status]')),
    selected: li.getAttribute('aria-selected') === 'true',
    controls: li.querySelectorAll('a, button, input, textarea, form').length,
  }));
  return {list: !!list, items, direct_empty: !!document.querySelector('__DIRECT__')};
}
""".replace("__LIST__", PREMIUM_LIST).replace("__ITEM__", PREMIUM_ITEM).replace(
    "__DIRECT__", DIRECT_NONE)

# The open request's detail pane: its fields, the Q&A, every control in it
# (to tell a locked request from one that could be answered), and its text.
DETAIL_JS = r"""
() => {
  const d = document.querySelector('__DETAIL__');
  if (!d) return null;
  const first = el => el ? ((el.innerText || '').split('\n').map(s => s.trim())
                             .filter(Boolean)[0] || '') : '';
  const f = name => first(d.querySelector('[data-test-service-requests-detail__' + name + ']'));
  const qa = [];
  for (const q of d.querySelectorAll('[data-test-service-requests-detail__description-question]')) {
    const n = q.getAttribute('data-test-service-requests-detail__description-question');
    const a = d.querySelector('[data-test-service-requests-detail__description-answer="' + n + '"]');
    qa.push([(q.innerText || '').trim(), a ? (a.innerText || '').trim() : '']);
  }
  const controls = Array.from(d.querySelectorAll(
      'a, button, textarea, input, select, form, [contenteditable="true"], ' +
      '[role=button], [role=textbox]')).map(el => ({
    tag: el.tagName.toLowerCase(), label: el.getAttribute('aria-label') || '',
    text: first(el).slice(0, 80), editable: el.isContentEditable || false}));
  return {title: f('project-title'), location: f('location'), age: f('project-status'),
          creator: f('creator-title'), subtitle: f('creator-subtitle'), qa, controls,
          text: (d.innerText || '').slice(0, 4000)};
}
""".replace("__DETAIL__", DETAIL)


PANE_TRIES = 5


async def read_premium(page: Any) -> dict[str, Any] | None:
    """The premium list and each request's pane. Read-only: the only clicks
    are on the list rows themselves (LinkedIn's tabs), scoped to their
    data-test hook, never on a row holding a link or button, and the run
    stops if a click moved the page. The open row's pane is read without a
    click. None when the page has no premium list."""
    from .browser import pause
    listed = await page.evaluate(PREMIUM_JS)
    if not isinstance(listed, dict) or not listed.get("list") or not listed.get("items"):
        return None
    here = page.url
    panes: list[dict[str, Any] | None] = []
    rows = page.locator(PREMIUM_ITEM)
    for n, row in enumerate(listed["items"]):
        pane: dict[str, Any] | None = None
        if int(row.get("controls") or 0) == 0:
            if not row.get("selected"):
                await pause(page, 2000, 4500)
                await rows.nth(n).click()
                await pause(page, 1500, 3000)
                if page.url != here:
                    raise RuntimeError("opening a premium request moved the page to "
                                       f"{page.url.split('?')[0]}; stopped")
            # LinkedIn re-renders the pane late: wait for it to show this row.
            for _ in range(PANE_TRIES):
                got = await page.evaluate(DETAIL_JS)
                pane = got if isinstance(got, dict) else None
                if pane_matches(row, pane):
                    break
                await pause(page, 800, 1500)
        panes.append(pane)
    return {**listed, "panes": panes}


# A stripped outline for the next session: structure, never raw HTML (which
# can carry embedded tokens).
OUTLINE_JS = r"""
() => {
  const main = document.querySelector('main') || document.body;
  const out = [];
  for (const el of main.querySelectorAll('*')) {
    if (out.length >= 400) break;
    const role = el.getAttribute('role') || '';
    const label = el.getAttribute('aria-label') || '';
    const tests = Array.from(el.attributes).filter(a => a.name.startsWith('data-test'))
      .map(a => a.name + '=' + a.value.slice(0, 40));
    const tag = el.tagName.toLowerCase();
    const isCtl = ['button', 'a', 'form', 'textarea', 'input', 'h1', 'h2', 'h3', 'li'].includes(tag)
      || el.isContentEditable;
    if (!role && !label && !tests.length && !isCtl) continue;
    out.push({tag, role, label: label.slice(0, 60), tests,
              text: (tag === 'button' || tag.startsWith('h')) ?
                    (el.innerText || '').trim().slice(0, 60) : '',
              editable: el.isContentEditable || false});
  }
  return out;
}
"""


async def save_outline(page: Any, label: str) -> str | None:
    from .browser import SHOTS
    try:
        outline = await page.evaluate(OUTLINE_JS)
        SHOTS.mkdir(parents=True, exist_ok=True)
        path = SHOTS / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{label}.outline.json"
        path.write_text(json.dumps({"url": page.url.split("?")[0], "outline": outline},
                                   indent=1, ensure_ascii=False))
        return str(path)
    except Exception:                                    # noqa: BLE001
        return None


async def _record_hard_stop(what: str) -> None:
    """Where `account_alerts` sees it, the sweep's way: the state file's
    alerts, under the state lock."""
    from .store import file_lock, load, save, stamp
    try:
        async with file_lock(timeout_s=60):
            state = load()
            state.setdefault("alerts", []).append({"when": stamp(), "what": what})
            save(state)
    except Exception:
        log.warning("could not record the hard stop in state", exc_info=True)


@dataclass(slots=True)
class Scan:
    reads: list[Read] = field(default_factory=list)
    new: list[dict[str, Any]] = field(default_factory=list)
    read_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    shots: list[str] = field(default_factory=list)
    locked: list[dict[str, Any]] = field(default_factory=list)   # newly locked


async def scan() -> Scan:
    """Read every surface once, at a person's pace, and record what is there.
    A collector: it reads LinkedIn and writes ATLAS's own table, nothing else.
    Raises NoSession / SignedOut / HardStop / TimeoutError for the check."""
    from .browser import (
        HardStop,
        SignedOut,
        browser,
        goto,
        keep_session,
        lock,
        pause,
        snapshot,
    )
    result = Scan()
    try:
        ours = await our_sent_bodies()
        known = await known_keys()
    except Exception as exc:
        raise RuntimeError(f"the requests table could not be read: "
                           f"{type(exc).__name__}") from exc
    await asyncio.wait_for(lock().acquire(), timeout=LOCK_WAIT_S)
    hard: HardStop | None = None
    try:
        pw, b, ctx = await browser()
        try:
            page = await ctx.new_page()
            any_read = False
            for surface in surfaces():
                try:
                    text = await goto(page, surface.url)
                    await pause(page, 1200, 2600)
                    rows = await page.evaluate(ROWS_JS)
                    premium = (await read_premium(page)
                               if surface.kind != "inbox" else None)
                except (HardStop, SignedOut):
                    raise
                except Exception as exc:                 # noqa: BLE001
                    shot = await snapshot(page, f"requests-{surface.label}")
                    result.shots += [s for s in (shot,) if s]
                    result.reads.append(Read(surface, False, reason=(
                        f"could not read it ({type(exc).__name__}: {str(exc)[:120]})")))
                    continue
                any_read = True
                read = interpret(surface, text, rows, ours, premium)
                result.reads.append(read)
                fresh = [i for i in read.items
                         if (i.page, i.source, i.external_key) not in known]
                if not read.ok or (read.unverified_layout and fresh):
                    shot = await snapshot(page, f"requests-{surface.label}")
                    outline = await save_outline(page, f"requests-{surface.label}")
                    result.shots += [s for s in (shot, outline) if s]
                for item in fresh:
                    # A premium request has no page of its own (its url is the
                    # list): its pane was read above.
                    if (item.source == "service_request" and item.url
                            and item.url != surface.url):
                        # The proposal UI has never been seen: keep a picture
                        # and an outline of the request itself for the next
                        # session to finish the flow from.
                        await pause(page, 2500, 5000)
                        try:
                            await goto(page, item.url)
                            shot = await snapshot(page, "service-request")
                            outline = await save_outline(page, "service-request")
                            result.shots += [s for s in (shot, outline) if s]
                        except HardStop:
                            raise
                        except Exception:                # noqa: BLE001
                            log.info("could not open request %s", item.url)
                await pause(page, 2500, 6000)
            if any_read:
                await keep_session(ctx)
        finally:
            await ctx.close()
            await b.close()
            await pw.stop()
    except HardStop as exc:
        hard = exc
    finally:
        lock().release()
    if hard is not None:
        # Recorded after the browser lock is released: the sweep takes the
        # state lock first and the browser lock second, and holding them the
        # other way round here would time the record out behind a sweep.
        await _record_hard_stop(str(hard))
        raise hard
    for read in result.reads:
        for item in read.items:
            key = (item.page, item.source, item.external_key)
            if item.from_us:
                if key in known:
                    # The page has the last word (the owner answered, or ATLAS
                    # did): answered, until they write again.
                    await mark_answered_on_page(item)
                continue
            if key in known:
                # Labelled when first seen; an update keeps the label.
                label, how = "other", "none"
            else:
                label, how = await classify(item.page, item.service_raw, item.words)
            budget, timeline = budget_and_timeline(item.words)
            row = await upsert_seen(item, label, how, budget, timeline)
            if row and row.get("inserted"):
                result.new.append({"id": row.get("id"), "page": item.page,
                                   "source": item.source, "requester": item.requester,
                                   "label": label})
            if newly_locked(item, row):
                result.locked.append({"id": row.get("id") if row else None,
                                      "page": item.page, "requester": item.requester,
                                      "service": item.service_raw, "age": item.age,
                                      "location": item.location, "url": item.url})
            known.add(key)
    return result


async def post_in_thread(page_key: str, url: str, body: str,
                         before_click: Callable[[], Awaitable[None]]
                         ) -> tuple[str, str]:
    """('sent' | 'not_sent' | 'unverified', detail).

    UNVERIFIED against a real thread: no page thread existed on 2026-10-07.
    Before Send is clicked any miss is 'not_sent' and nothing was posted;
    after it, a read-back that does not show the text is 'unverified', and
    the caller never retries it."""
    from .browser import (
        HardStop,
        body_text,
        browser,
        goto,
        keep_session,
        lock,
        pause,
        snapshot,
    )
    if not url.startswith(inbox_prefix(page_key)) or "/messaging/" in url:
        return "not_sent", "the thread is not under the page's own inbox"
    try:
        await asyncio.wait_for(lock().acquire(), timeout=60)
    except TimeoutError:
        return "not_sent", "the LinkedIn browser is busy (a sweep or a publish)"
    clicked = False

    async def click_send(send: Any) -> None:
        nonlocal clicked
        await before_click()
        clicked = True
        await send.click()

    async def drive() -> tuple[str, str]:
        pw, b, ctx = await browser()
        try:
            page = await ctx.new_page()
            text = await goto(page, url)
            if not page.url.startswith(inbox_prefix(page_key)):
                return "not_sent", f"LinkedIn moved the thread to {page.url.split('?')[0]}"
            if (hit := PAYWALL.search(text)) is not None:
                await snapshot(page, "requests-paywall")
                return "not_sent", (f"LinkedIn asks for credits or payment to answer "
                                    f"({hit.group(0)!r}); money out is Tier 3")
            composer = page.locator(
                'main form [contenteditable="true"][role="textbox"], '
                'main form div[contenteditable="true"]')
            send = page.locator('main form button[type="submit"]')
            if await composer.count() != 1 or await send.count() != 1:
                await snapshot(page, "requests-no-composer")
                await save_outline(page, "requests-no-composer")
                return "not_sent", "the composer was not found"
            overlay = await composer.evaluate(
                "el => !!el.closest('aside, #msg-overlay, [class*=\"msg-overlay\"]')")
            label = (await send.inner_text()).strip().lower()
            if overlay or label != "send":
                await snapshot(page, "requests-no-composer")
                return "not_sent", "the composer was not found"
            await composer.click()
            await pause(page, 600, 1400)
            await composer.fill(body)
            await pause(page, 800, 1600)
            if _norm(await composer.inner_text()) != _norm(body):
                await composer.fill("")
                await snapshot(page, "requests-composer-mismatch")
                return "not_sent", "the composer did not hold the draft exactly; cleared"
            if await send.is_disabled():
                await composer.fill("")
                return "not_sent", "the Send button stayed disabled; cleared"
            await click_send(send)
            await pause(page, 3000, 5000)
            await goto(page, url)
            after = _norm(await body_text(page))
            await keep_session(ctx)
            if _norm(body) in after:
                return "sent", "seen in the thread after sending"
            await snapshot(page, "requests-unverified")
            return "unverified", "the text was not found on re-reading the thread"
        finally:
            await ctx.close()
            await b.close()
            await pw.stop()

    hard: HardStop | None = None
    try:
        outcome = await drive()
    except HardStop as exc:
        hard = exc
        outcome = (("unverified" if clicked else "not_sent"), f"hard stop: {exc}")
    except Exception as exc:
        log.warning("page reply failed", exc_info=True)
        # After the click the message may be out: never 'not_sent' then.
        outcome = (("unverified" if clicked else "not_sent"),
                   f"{type(exc).__name__}: {str(exc)[:160]}")
    finally:
        lock().release()
    if hard is not None:
        # After the browser lock, like the scan: the sweep's lock order.
        await _record_hard_stop(str(hard))
    return outcome


# ------------------------------------------------------------- the check

_LAST_SCAN: dict[str, Any] = {}


async def _prior_scan() -> dict[str, Any]:
    """The `requests` check's last stored evidence ({} when none or not
    readable: then LinkedIn is read, never assumed read)."""
    from ...kernel.state import last_evidence
    try:
        return await last_evidence("linkedin", "requests")
    except Exception:                                    # noqa: BLE001
        return {}


def _iso(raw: Any) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo is not None else None


def _hard_stop_recent(state: dict[str, Any]) -> str | None:
    """A hard stop recorded in the last day (by the sweep or by this scan).
    Hitting a challenge page every half hour is how an account ends."""
    from . import _parse_when
    now = datetime.now(UTC)
    for alert in reversed(state.get("alerts") or []):
        if not isinstance(alert, dict):
            continue
        what = str(alert.get("what") or "")
        when = _parse_when(alert.get("when"))
        if when is None or (now - when).total_seconds() > HARD_STOP_BACKOFF_H * 3600:
            continue
        if what.startswith("LinkedIn said"):
            return f"{what} (at {alert.get('when')})"
    return None


async def alert_locked(locked: list[dict[str, Any]]) -> None:
    """One Slack alert, in the page's topic, for the requests this scan was
    the first to see locked (`newly_locked`, kept in Postgres: never again
    for the same request). The names and services are LinkedIn's text,
    shown as read."""
    from ...kernel.comms import Outbound, notify
    by_page: dict[str, list[dict[str, Any]]] = {}
    for r in locked:
        by_page.setdefault(str(r["page"]), []).append(r)
    for page_key, rows in by_page.items():
        name = FACTS[page_key].name if page_key in FACTS else page_key
        lines = []
        for r in rows:
            who = r.get("requester") or ""
            who = "name hidden" if who in ("", "--") else who
            where = f", {r['location']}" if r.get("location") else ""
            lines.append(f"- {who} · {r.get('service') or 'a service'} · "
                         f"{r.get('age') or 'age not shown'}{where}")
        body = ("LinkedIn shows each one with an \"Unlock this request\" box and no "
                "message or reply control, so ATLAS cannot answer them and has not "
                "tried. Unlocking means paying for Page Premium; that is yours to "
                "decide, ATLAS never clicks it.\n\n" + "\n".join(lines)
                + f"\n\n{rows[0].get('url') or GUARDIAN_REQUESTS_URL}")
        try:
            await notify(Outbound(
                title=(f"LinkedIn: {len(rows)} {name} service request(s) locked "
                       "behind Page Premium"),
                body=body, topic=page_key, kind="alert"))
        except Exception:
            log.warning("could not send the locked-requests alert", exc_info=True)


async def requests_check() -> Observation:
    """Read the request surfaces and both inboxes; report what is waiting.

    FAILING when a request is still unanswered more than STALE_HOURS after
    it arrived; UNKNOWN when any surface could not be positively read (the
    Guardian service requests count as read when either of its two pages
    was); OK otherwise, never on a guess."""
    from . import StateUnavailable, _load
    from .browser import HardStop, NoSession, SignedOut
    check = "requests"
    try:
        state = _load()
    except StateUnavailable as exc:
        return Observation(check, Health.UNKNOWN, detail=str(exc))
    stopped = _hard_stop_recent(state)
    unread: list[str] = []
    scan_note = ""
    if not _LAST_SCAN:
        # A deploy restarts the process and every due check runs at boot;
        # the last stored observation says when LinkedIn was last read.
        prior = await _prior_scan()
        when = _iso(prior.get("scanned_at"))
        if when is not None:
            _LAST_SCAN.update({"at": when, "clean": bool(prior.get("clean"))})
    last = _LAST_SCAN.get("at")
    age_min = ((datetime.now(UTC) - last).total_seconds() / 60) if last else None
    if stopped:
        unread.append(f"not reading LinkedIn: a hard stop was recorded: {stopped}")
    elif age_min is not None and age_min < MIN_GAP_MIN and _LAST_SCAN.get("clean"):
        scan_note = f"last clean read {age_min:.0f} min ago; not re-read on restart"
    else:
        try:
            result = await scan()
        except (NoSession, SignedOut) as exc:
            return Observation(check, Health.UNKNOWN, detail=f"not read: {exc}")
        except HardStop as exc:
            return Observation(check, Health.UNKNOWN,
                               detail=f"hard stop, nothing more read: {exc}")
        except TimeoutError:
            return Observation(check, Health.UNKNOWN, detail=(
                f"the LinkedIn browser stayed busy for {LOCK_WAIT_S:.0f}s (a sweep "
                "or a publish); read next cycle"))
        except Exception as exc:                         # noqa: BLE001
            return Observation(check, Health.UNKNOWN, detail=(
                f"the scan could not run: {type(exc).__name__}: {str(exc)[:200]}"))
        service = [r for r in result.reads if r.surface.source == "service_request"]
        if service and not any(r.ok for r in service):
            unread += [f"{r.surface.label}: {r.reason}" for r in service]
        elif any(r.unverified_layout and not r.ok for r in service):
            # One page read empty, the other shows something unseen: the
            # something wins. A request must never be lost to the empty one.
            unread += [f"{r.surface.label}: {r.reason}" for r in service if not r.ok]
        unread += [f"{r.surface.label}: {r.reason}" for r in result.reads
                   if r.surface.source == "page_message" and not r.ok]
        _LAST_SCAN.update({"at": result.read_at, "clean": not unread,
                           "new": result.new, "shots": result.shots})
        if result.locked:
            await alert_locked(result.locked)
        scan_note = (f"read {len(result.reads)} surface(s) at "
                     f"{result.read_at:%H:%M} UTC; {len(result.new)} new")
    try:
        rows = await open_requests()
        summary = await demand()
    except Exception as exc:                             # noqa: BLE001
        return Observation(check, Health.UNKNOWN, detail=(
            f"the requests table could not be read: {type(exc).__name__}"))
    now = datetime.now(UTC)
    # Unanswered = nobody has it: still new or drafted after STALE_HOURS. A
    # held one is with the owner already (a card or an alert carries it), so it
    # is counted and named, not paged on again every half hour.
    stale = [r for r in rows if r["status"] in ("new", "drafted")
             and (now - r["last_inbound_at"]).total_seconds() > STALE_HOURS * 3600]
    held = [r for r in rows if r["status"] == "held"]
    locked = [r for r in held if str(r.get("note") or "").startswith("locked:")]
    if len(held) > len(locked):
        scan_note += f"; {len(held) - len(locked)} held for you on a card"
    if locked:
        scan_note += (f"; {len(locked)} locked (LinkedIn needs Page Premium to reply; "
                      "you were alerted once)")
    scanned = _LAST_SCAN.get("at")
    evidence = {"open": len(rows), "stale": len(stale), "held": len(held),
                "locked": len(locked),
                "unread": unread,
                "scanned_at": scanned.isoformat() if scanned else None,
                "clean": bool(_LAST_SCAN.get("clean")) and not unread,
                "demand": summary, "new": _LAST_SCAN.get("new", []),
                "shots": _LAST_SCAN.get("shots", [])[:6]}
    names = "; ".join(f"{r['requester'] or 'someone'} on {r['page']} ({r['source']}, "
                      f"{r['status']}{', ' + r['note'][:80] if r['note'] else ''})"
                      for r in stale[:5])
    if stale:
        return Observation(
            check, Health.FAILING, float(len(stale)), "requests",
            f"{len(stale)} LinkedIn page request(s) unanswered for over "
            f"{STALE_HOURS:.0f}h: {names}. {scan_note}", evidence=evidence)
    if unread:
        return Observation(
            check, Health.UNKNOWN,
            detail=(f"{'; '.join(unread)}. {len(rows)} open request(s) on record. "
                    f"{scan_note}"),
            evidence=evidence)
    return Observation(
        check, Health.OK, float(len(rows)), "open",
        f"{len(rows)} open LinkedIn page request(s); {scan_note}. "
        f"{demand_line()}", evidence=evidence)
