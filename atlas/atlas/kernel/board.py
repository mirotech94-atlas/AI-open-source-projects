"""The "Waiting for you" board: every pending card, in one place.

the owner, 2026-10-11: "organize Slack approval messages, as I don't know which
one is which and where they all are." Thirteen cards sat in eight topic
channels, each headed "#357 — Post a rendered tutorial…" over a paragraph.

Each card still lands in its topic channel, where the thing is followed. In
addition, #approvals (`SLACK_APPROVALS_CHANNEL`, or the channel the
`approvals_board.create_channel` action made) holds ONE pinned message,
edited in place (`chat.update`, never a new post), listing every pending card:
grouped by business area, one line each -- the card number linked to the card,
what approving does in plain words, its risk, and when it expires.

The board only LISTS. It carries no buttons and no button values, so
`slack_decisions.card_id` finds no card on it and a click or a reply under it
decides nothing. Approving stays on the card, from an approver
(SLACK_APPROVER_IDS), through `approvals.decide` -- nothing here imports it.

What it shows comes from the action and its params, through the renderers
below, never from the card's `reason`, which may quote an email or a web page
(invariant 4). Values are stripped of Slack markup before they are shown.

The board is redrawn by the scheduler step `approvals_board`: whenever the
set of pending cards changed (a card raised, decided or expired, by any
process), when `mark_dirty()` was called, and every REFRESH_MIN minutes as a
safety net. If the queue cannot be read, the board is left as it was -- it
never says "Nothing waiting" on a guess (invariant 7).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger("atlas.board")

NY = ZoneInfo("America/New_York")
CHANNEL_ENV = "SLACK_APPROVALS_CHANNEL"
DEFAULT_NAME = "approvals"
REFRESH_ENV = "APPROVALS_BOARD_REFRESH_MIN"
SECTION_LIMIT = 2900
# Cards whose Slack message is looked up (a read) per refresh, when the card
# was raised before its channel and ts were stored.
BACKFILL_PER_PASS = 5
BOARD_TITLE = "Waiting for you"
EMPTY = "Nothing waiting."

# ------------------------------------------------------------------ areas

AREAS: tuple[str, ...] = ("ATLAS Academy", "LinkedIn", "Clips",
                          "Agency/Mapfront", "School", "System")
# By the action's NAMESPACE, not its topic: a LinkedIn post for a company
# page is a LinkedIn decision, whichever channel its card lands in.
AREA_OF_NAMESPACE: dict[str, str] = {
    "academy": "ATLAS Academy", "course": "ATLAS Academy", "shop": "ATLAS Academy",
    "linkedin": "LinkedIn",
    "clips": "Clips", "content": "Clips", "instagram": "Clips", "tiktok": "Clips",
    "youtube": "Clips", "messages": "Clips", "football": "Clips",
    "agency": "Agency/Mapfront",
    "school": "School",
}


def area_of(action: str) -> str:
    return AREA_OF_NAMESPACE.get((action or "").split(".", 1)[0], "System")


# ------------------------------------------------------------------ risk

# Moves money, or asks someone for it.
MONEY = {"stripe.retry_invoice", "shop.create_payment_link", "agency.send_agreement",
         "purchasing.prepare_cart", "purchasing.prepare_flight"}
MONEY_NAMESPACES = {"stripe"}
# Reaches people outside: posted publicly, or sent to someone.
_PUBLIC_LEAF = re.compile(
    r"(^|_)(publish|autopublish|post|reshare|send|reply|text|submit|resend|invite|"
    r"call)(_|$)")
NOT_PUBLIC = {"school.draft_work", "course.hand_off", "clips.hand_off",
              "content.draft", "agency.draft_reply", "agency.draft_outreach",
              "linkedin.draft_request_reply", "academy.draft_support_reply",
              "mail.draft_reply", "fiverr.draft_buyer_reply",
              "agency.call_brief"}


def risk_of(action: str, spec: object | None) -> str:
    """money / public / irreversible / reversible, from the action's name and
    its declared `irreversible` -- never from the reason. An action whose
    module is not loaded here says so instead of guessing "reversible"."""
    action = action or ""
    if action in MONEY or action.split(".", 1)[0] in MONEY_NAMESPACES:
        return "money"
    leaf = action.split(".", 1)[-1]
    public = (action not in NOT_PUBLIC and not leaf.startswith("draft")
              and bool(_PUBLIC_LEAF.search(leaf)))
    irreversible = bool(getattr(spec, "irreversible", False))
    if public:
        return "public, irreversible" if irreversible else "public"
    if spec is None:
        return "risk unknown"
    return "irreversible" if irreversible else "reversible"


# ------------------------------------------------------------------ what

_MARKUP = re.compile(r"[<>`*_~|\r\n\t]+")


def safe(value: Any, limit: int = 48) -> str:
    """A param value fit to show: no Slack markup (`<url|text>`, `<@U…>`,
    `<!channel>` cannot render live), no line breaks, cut at a word."""
    text = " ".join(_MARKUP.sub(" ", str(value if value is not None else "")).split())
    text = text.replace("&", "and")
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0]
    return (cut if len(cut) > limit // 2 else text[:limit - 1]) + "…"


def _page(params: dict) -> str:
    page = safe(params.get("page") or "", 20)
    return {"flycheap": "FlyCheap", "guardian": "Guardian (Vault)",
            "academy": "ATLAS Academy"}.get(page.lower(), page.title() or "a")


def _entry(params: dict) -> str:
    entry = safe(params.get("entry_id") or "", 12)
    return entry or "the next approved post"


Render = Callable[[dict], str]
WHAT: dict[str, Render] = {
    "cameras.mark_household": lambda p: (
        f"Do you know this person? Treat Person {safe(p.get('cluster'), 12)}, an "
        f"unfamiliar face the home cameras saw, as someone in your household"),
    "linkedin.publish_post": lambda p: (
        f"Post {_entry(p)} on the {_page(p)} LinkedIn page, then reshare it to "
        f"your profile"),
    "linkedin.reshare_post": lambda p: (
        f"Reshare {_entry(p)} from the {_page(p)} LinkedIn page to your profile"),
    "linkedin.approve_draft": lambda p: (
        f"Mark {_entry(p)} for the {_page(p)} LinkedIn page approved (not posted yet)"),
    "linkedin.update_page_identity": lambda p: (
        f"Change the {_page(p)} LinkedIn page's name or logo"),
    "linkedin.send_request_reply": lambda p: (
        f"Send reply draft {safe(p.get('draft_id'))} in the LinkedIn page inbox"),
    "school.draft_work": lambda p: (
        f"Draft school item {safe(p.get('item_id'))} into data/school "
        f"(you write it up and submit)"),
    "school.set_course": lambda p: (
        f"Set how course {safe(p.get('code'))} is drafted"),
    "gitlab.open_issue": lambda p: (
        f"Open a GitLab issue in {safe(p.get('project'), 40)}: "
        f"{safe(p.get('title'), 60)}"),
    "clips.publish_clip_tiktok": lambda p: (
        f"Post clip {safe(p.get('clip_id'))} to TikTok @example_handle, public"),
    "clips.publish_clip": lambda p: (
        f"Post clip {safe(p.get('clip_id'))} to Instagram @example_handle, public"),
    "clips.publish_clip_youtube": lambda p: (
        f"Post clip {safe(p.get('clip_id'))} as a public YouTube Short"),
    "clips.publish_clip_youtube_postiz": lambda p: (
        f"Post clip {safe(p.get('clip_id'))} as a public YouTube Short"),
    "clips.publish_promo": lambda p: (
        f"Post promo render {safe(p.get('media_id'))} to the clips accounts"),
    "clips.hand_off": lambda p: (
        f"Hand you clips {safe(p.get('clip_ids'))} to post by hand"),
    "clips.set_campaign_status": lambda p: (
        f"Set clip campaign {safe(p.get('campaign'))} to {safe(p.get('status'))}"),
    "content.publish_reel": lambda p: (
        f"Post reel {safe(p.get('media_id'))} to Instagram, Facebook and TikTok"),
    "agency.send_outreach": lambda p: (
        f"Send outreach email {safe(p.get('outreach_id'))} to its lead"),
    "agency.send_reply": lambda p: (
        f"Send reply draft {safe(p.get('draft_id'))} to its lead"),
    "agency.text_lead": lambda p: (
        f"Text lead {safe(p.get('lead_id'))} ({safe(p.get('kind'))})"),
    "agency.send_agreement": lambda p: (
        f"Send lead {safe(p.get('lead_id'))} the agreement and payment link at "
        f"${safe(p.get('price'))}"),
    "agency.build_with_webild": lambda p: (
        f"Queue the Webild site build for lead {safe(p.get('lead_id'))}"),
    "academy.send_support_reply": lambda p: (
        f"Email support reply draft {safe(p.get('draft_id'))} to the customer"),
    "academy.publish_tutorial": lambda p: (
        f"Post tutorial {safe(p.get('series'))} episode {safe(p.get('episode'))} "
        f"to YouTube, public"),
    "academy.resend_access": lambda p: (
        "Email the buyer their download link again"),
    "academy.approve_review": lambda p: (
        f"Show review {safe(p.get('review_id'))} on its product page"),
    "academy.add_to_series_playlist": lambda p: (
        f"Add a video to the {safe(p.get('series'))} series playlist"),
    "course.publish_post": lambda p: (
        f"Post course post {safe(p.get('post_id'))} through Postiz"),
    "course.hand_off": lambda p: (
        f"Hand you course post {safe(p.get('post_id'))} to publish by hand"),
    "shop.create_payment_link": lambda p: (
        f"Put {safe(p.get('product_key'))} on sale with a Stripe payment link"),
    "youtube.upload": lambda p: (
        f"Upload “{safe(p.get('title'), 50)}” to YouTube, private"),
    "youtube.set_visibility": lambda p: (
        f"Make YouTube video {safe(p.get('video_id'))} {safe(p.get('privacy'))}"),
    "instagram.publish": lambda p: (
        f"Publish a {safe(p.get('kind') or 'post')} to Instagram "
        f"{safe(p.get('account'))}".rstrip()),
    "instagram.reply_comment": lambda p: "Reply publicly to an Instagram comment",
    "instagram.hide_comment": lambda p: "Hide a comment on your Instagram post",
    "instagram.funnel_set": lambda p: (
        f"Set the Instagram keyword funnel for “{safe(p.get('keyword'), 30)}”"),
    "messages.reply": lambda p: "Send an Instagram DM reply",
    "delegate.claude_code": lambda p: (
        f"Hand handoff {safe(p.get('handoff_id'))} to the coding agent on your Mac"),
    "nightshift.merge": lambda p: (
        f"Merge overnight branch job {safe(p.get('job_id'))}"),
    "lessons.clear": lambda p: (
        f"Lift the never-repeat block on {safe(p.get('action'))}"),
    "browser.submit_form": lambda p: "Fill and submit a form on a public web page",
    "stripe.retry_invoice": lambda p: (
        f"Retry charging invoice {safe(p.get('invoice_id'))} "
        f"({safe(p.get('business'))})"),
    "cloudco.snapshot_all": lambda p: "Snapshot every CloudCo instance",
    "markets.paper_idea": lambda p: (
        f"Book a paper trade: {safe(p.get('side'))} {safe(p.get('shares'))} "
        f"{safe(p.get('ticker'))} (paper only)"),
    "markets.paper_rebalance": lambda p: (
        "Rebalance the paper playlist to this week's weights (paper only)"),
}


def _summary_line(summary: str) -> str:
    """The catalog's summary, cut to its first clause."""
    first = re.split(r";| \(| — | -- |\. ", summary or "", maxsplit=1)[0]
    return safe(first, 90)


def what_of(action: str, params: dict | None, spec: object | None = None) -> str:
    """One plain line: what happens if he approves. A renderer per action
    family, else the action's own summary, else its name."""
    params = params if isinstance(params, dict) else {}
    render = WHAT.get(action)
    if render is not None:
        try:
            line = " ".join(render(params).split())
            if line:
                return line
        except Exception:                               # noqa: BLE001
            log.debug("what_of(%s) failed; using the summary", action)
    summary = getattr(spec, "summary", "") if spec is not None else ""
    return _summary_line(summary) if summary else safe(action, 90)


# The six agency agents (the owner, 2026-10-27): every card names the owning agent
# AND the client, so he knows what belongs to what. By namespace, like AREAS.
AGENT_OF_NAMESPACE: dict[str, str] = {
    "sales": "Sales",
    "academy": "Content", "youtube": "Content", "church": "Content",
    "reddit": "Content",
    "bizops": "BizOps", "projects": "BizOps",
    "seo": "SEO",
    "research": "Research",
    "qa": "QA",
    "agency": "Websites",
}
# Whose card it is when nothing in the params or evidence names a client.
CLIENT_DEFAULT: dict[str, str] = {
    "academy": "Atlas Academy", "youtube": "Atlas Academy", "church": "ECC",
    "reddit": "Atlas Agency", "research": "Mapfront", "qa": "Mapfront",
    "sales": "Mapfront", "bizops": "Mapfront", "projects": "Mapfront",
    "seo": "Mapfront", "agency": "Mapfront",
}
_CLIENT_KEYS = ("client", "client_name", "business", "business_name", "lead_name")


def agent_of(action: str) -> str | None:
    return AGENT_OF_NAMESPACE.get((action or "").split(".", 1)[0])


def client_of(action: str, params: dict | None, evidence: dict | None) -> str:
    """The client a card is for: the evidence's `client` (written by the code
    that drafted it), then a client-naming param, then the namespace default.
    Pure: never a database read."""
    for source in (evidence, params):
        if isinstance(source, dict):
            for key in _CLIENT_KEYS:
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    return safe(value.strip(), 40)
    return CLIENT_DEFAULT.get((action or "").split(".", 1)[0], "Mapfront")


def card_header(pid: int, action: str, params: dict | None, spec: object | None,
                evidence: dict | None = None) -> str:
    """The card's Slack header: `[Agent · Client] #<id> · <what>` for the
    agency agents, `[Area] #<id> · <what>` for everything else."""
    what = what_of(action, params, spec)
    agent = agent_of(action)
    # The drafting code may name the agent who raised it (Sales raises an
    # agency.send_outreach card); only a known agent name is taken.
    named = (evidence or {}).get("agent") if isinstance(evidence, dict) else None
    if agent is not None and named in set(AGENT_OF_NAMESPACE.values()):
        agent = named
    if agent is not None:
        return f"[{agent} · {client_of(action, params, evidence)}] #{pid} · {what}"
    return f"[{area_of(action)}] #{pid} · {what}"


# Why an action that stays Tier 2 is still a card (the owner, 2026-10-12: only
# what really needs him should reach him; memory approval-policy). Shown
# under the card's first line; display only, it decides nothing.
WHY_A_CARD: dict[str, str] = {
    "matrix_rooms.send_reply": "it speaks to people as you",
    "messages.reply": "it messages someone as you",
    "delegate.claude_code": "it runs code on your Mac",
    "linkedin.update_page_identity": "it changes a page's public name or logo",
    "linkedin.publish_post": "a post cannot be taken back without deleting it",
    "linkedin.reshare_post": "it posts on your own profile",
    "markets.paper_idea": "you asked to approve or decline every idea yourself",
    "clips.set_campaign_status": "it decides which brand work ATLAS takes on",
    "clips.hand_off": "only you can post on that account",
    "clips.submit_to_whop": "it records a Whop submission only you can make",
    "content.publish_reel": "it failed an automatic check",
    "academy.send_support_reply": "a customer is waiting and a check said no",
    "stripe.retry_invoice": "it charges a customer",
    "shop.create_payment_link": "it sets a price customers pay",
    "agency.send_outreach": "it emails a business as Mapfront",
    "agency.send_invite": "it invites a client to a call with you",
    "agency.send_agreement": "it sends a client a price and your payment link",
}


def card_lead(action: str, params: dict | None, spec: object | None) -> str:
    """The card's first line: what Approve does and what Decline does, and
    for an action that deliberately stays Tier 2, why it is a card."""
    what = what_of(action, params, spec)
    what = what[0].lower() + what[1:] if what[:1].isupper() and what[1:2].islower() \
        else what
    lead = (f"*Approve:* {what} ({risk_of(action, spec)}).  "
            f"*Decline:* nothing is done, and ATLAS remembers the no.")
    if why := WHY_A_CARD.get(action):
        lead += f"\n_Why this is a card: {why}._"
    return lead


# ------------------------------------------------------------------ render

@dataclass(frozen=True, slots=True)
class Line:
    pid: int
    area: str
    what: str
    risk: str
    expires_at: datetime | None
    created_at: datetime | None
    link: str


def _params(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            got = json.loads(raw or "{}")
        except ValueError:
            return {}
        return got if isinstance(got, dict) else {}
    return {}


def _spec(action: str) -> object | None:
    from .registry import REGISTRY
    return REGISTRY.actions.get(action)


def lines_of(rows: Iterable[Any]) -> list[Line]:
    out: list[Line] = []
    for r in rows:
        r = dict(r)
        action = str(r.get("action") or "")
        spec = _spec(action)
        link = str(r.get("slack_permalink") or "")
        out.append(Line(int(r["id"]), area_of(action),
                        what_of(action, _params(r.get("params")), spec),
                        risk_of(action, spec), r.get("expires_at"),
                        r.get("created_at"), link if link.startswith("https://") else ""))
    return out


def _when(at: datetime | None, now: datetime) -> str:
    if at is None:
        return "no expiry read"
    left = at - now
    if left <= timedelta(0):
        return "expired"
    if left < timedelta(hours=1):
        return f"expires in {max(1, int(left.total_seconds() // 60))} min"
    if left < timedelta(hours=24):
        return f"expires in {int(left.total_seconds() // 3600)} h"
    return f"expires {at.astimezone(NY):%a %d %b}"


def _waited(at: datetime | None, now: datetime) -> str:
    if at is None:
        return ""
    days = int((now - at).total_seconds() // 86400)
    return f", waiting {days} d" if days >= 1 else ""


def line_text(line: Line, now: datetime) -> str:
    num = f"<{line.link}|#{line.pid}>" if line.link else f"#{line.pid}"
    return (f"• {num}  {line.what} · _{line.risk}_ · "
            f"{_when(line.expires_at, now)}{_waited(line.created_at, now)}")


def render(rows: Iterable[Any], now: datetime | None = None) -> list[str]:
    """The board as mrkdwn sections: the counts first, then each area with
    any card waiting, in AREAS order, soonest expiry first. Pure."""
    now = now or datetime.now(UTC)
    lines = lines_of(rows)
    stamp = f"as of {now.astimezone(NY):%a %d %b %H:%M} New York time"
    if not lines:
        return [f"*{BOARD_TITLE}: 0*\n{EMPTY}\n_{stamp}_"]
    by_area: dict[str, list[Line]] = {a: [] for a in AREAS}
    for ln in lines:
        by_area.setdefault(ln.area, []).append(ln)
    far = datetime.max.replace(tzinfo=UTC)
    counts = " · ".join(f"{a} {len(v)}" for a, v in by_area.items() if v)
    sections = [(f"*{BOARD_TITLE}: {len(lines)}*\n{counts}\n"
                 f"_{stamp}. Approve or decline on each card (the number opens "
                 f"it); this board only lists them._")]
    for area, items in by_area.items():
        if not items:
            continue
        items.sort(key=lambda x: (x.expires_at or far, x.pid))
        text = f"*{area} ({len(items)})*"
        for ln in items:
            row = line_text(ln, now)
            if len(text) + len(row) + 1 > SECTION_LIMIT:
                sections.append(text)
                text = f"*{area} (continued)*"
            text += "\n" + row
        sections.append(text)
    return sections


# Slack takes at most 50 blocks a message; a section and its divider are two.
MAX_SECTIONS = 24


def blocks_of(sections: list[str]) -> list[dict[str, Any]]:
    """Sections and dividers only: never an actions block, never a button.
    Every text through `redact_stored` (plan S01), as `comms.notify` does --
    the board is posted beside notify, not through it. Past MAX_SECTIONS the
    rest is one line saying how many sections are not shown."""
    from ..modules._http import redact_stored
    shown = sections[:MAX_SECTIONS]
    if len(sections) > MAX_SECTIONS:
        hidden = sum(t.count("\n• ") for t in sections[MAX_SECTIONS - 1:])
        shown = [*sections[:MAX_SECTIONS - 1],
                 f"_+{hidden} more card(s) not shown here; each is in its own channel._"]
    blocks: list[dict[str, Any]] = []
    for i, text in enumerate(shown):
        if i:
            blocks.append({"type": "divider"})
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": redact_stored(text)}})
    return blocks


def fallback_text(n: int) -> str:
    return f"{BOARD_TITLE}: {n}" if n else f"{BOARD_TITLE}: {EMPTY}"


# ------------------------------------------------------------------ state

_dirty = True
_last_at: datetime | None = None
_last_fp: str | None = None
_last_error: str = ""
_create_tried_at: datetime | None = None
_lock: asyncio.Lock | None = None


def mark_dirty() -> None:
    """A card was raised, decided or expired: redraw on the next cycle. In
    memory and free -- `approvals` calls it on paths that must not wait."""
    global _dirty
    _dirty = True


def refresh_every() -> timedelta:
    try:
        return timedelta(minutes=max(1, int(os.getenv(REFRESH_ENV, "5"))))
    except ValueError:
        return timedelta(minutes=5)


def enabled() -> bool:
    return (bool(os.getenv("SLACK_BOT_TOKEN", "").strip())
            and os.getenv("APPROVALS_BOARD", "on").strip().lower()
            not in ("0", "off", "false", "no"))


def status() -> dict[str, Any]:
    """For the module's check: when the board was last drawn, and why not."""
    return {"at": _last_at, "error": _last_error, "enabled": enabled()}


def _get_lock() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


_CHANNEL_ID = re.compile(r"^[CG][A-Z0-9]{6,20}$")


def channel_name() -> str:
    """The name to create when SLACK_APPROVALS_CHANNEL is a name or unset."""
    raw = os.getenv(CHANNEL_ENV, "").strip().lstrip("#").lower()
    if raw and not _CHANNEL_ID.match(raw.upper()) and re.fullmatch(
            r"[a-z0-9][a-z0-9_-]{0,79}", raw):
        return raw
    return DEFAULT_NAME


async def channel() -> str | None:
    """The board's channel id: SLACK_APPROVALS_CHANNEL when it is an id,
    else the one `approvals_board.create_channel` made (saved), else None."""
    raw = os.getenv(CHANNEL_ENV, "").strip()
    if _CHANNEL_ID.match(raw):
        return raw
    from .state import fetch_one
    row = await fetch_one(
        "SELECT channel FROM approvals_board WHERE key = 'channel'")
    found = str(row["channel"]) if row and row.get("channel") else ""
    return found if _CHANNEL_ID.match(found) else None


async def _slack(method: str, payload: dict[str, Any], *,
                 form: bool = False) -> dict[str, Any]:
    import httpx
    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    if not token:
        return {"ok": False, "error": "SLACK_BOT_TOKEN unset"}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            if form:
                r = await c.post(f"https://slack.com/api/{method}", headers=headers,
                                 data=payload)
            else:
                r = await c.post(f"https://slack.com/api/{method}", headers=headers,
                                 json=payload)
            data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "error": f"unreachable ({type(exc).__name__})"}
    return data if isinstance(data, dict) else {"ok": False, "error": "no answer"}


PENDING_SQL = """SELECT id, action, params, created_at, expires_at,
                        slack_channel, slack_ts, slack_permalink, slack_link_tried_at
                   FROM proposals
                  WHERE status = 'pending' AND expires_at > now()
                  ORDER BY expires_at, id"""


async def pending() -> list[dict[str, Any]]:
    """Every card waiting. Raises when the queue cannot be read: the caller
    must not take an error for an empty queue."""
    from .state import fetch_all
    return [dict(r) for r in await fetch_all(PENDING_SQL)]


def fingerprint(rows: list[dict[str, Any]]) -> str:
    return ",".join(f"{r['id']}:{r.get('slack_permalink') or ''}" for r in rows)


# ------------------------------------------------------------------ cards

async def record_card(pid: int, topic: str) -> None:
    """Where card #pid's Slack message is, right after `propose()` sent it:
    the ts `comms` kept for its title, in the channel the topic routes to.
    One UPDATE; never raises. A card queued for the morning brief has no ts
    and is listed without a link."""
    try:
        from .comms import CHANNELS, sent_ref
        ts = sent_ref(f"#{pid} — ", "slack")
        slack = CHANNELS.get("slack")
        if not ts or slack is None:
            return
        where = slack.channel_for(topic)
        if not where:
            return
        from .state import execute_sql
        await execute_sql(
            """UPDATE proposals SET slack_channel = $2, slack_ts = $3
                WHERE id = $1""", pid, where, str(ts))
    except Exception:
        log.warning("card #%s: its Slack message was not recorded", pid,
                    exc_info=True)


async def _permalink(where: str, ts: str) -> str:
    data = await _slack("chat.getPermalink", {"channel": where, "message_ts": ts},
                        form=True)
    link = str(data.get("permalink") or "") if data.get("ok") else ""
    return link if link.startswith("https://") else ""


async def _find_card(pid: int, action: str, params: dict,
                     created_at: datetime | None) -> tuple[str, str, bool]:
    """A card raised before its ts was stored: read its topic channel's
    history around the moment it was raised and match the card's own button
    values (`card_of`). A read. (channel, ts, settled): settled is True only
    when Slack answered -- found or truly not there -- so a refusal
    (`not_in_channel`, `ratelimited`) is tried again on a later pass."""
    from .approvals import topic_for_action
    from .comms import CHANNELS
    slack = CHANNELS.get("slack")
    if slack is None or created_at is None:
        return "", "", True
    where = slack.channel_for(topic_for_action(action, params))
    if not where:
        return "", "", True
    start = created_at.timestamp() - 120
    data = await _slack("conversations.history",
                        {"channel": where, "oldest": f"{start:.6f}",
                         "latest": f"{start + 3600:.6f}", "limit": "100",
                         "inclusive": "true"}, form=True)
    if not data.get("ok"):
        log.info("card #%s: history of %s unread (%s)", pid, where, data.get("error"))
        return "", "", False
    for msg in data.get("messages") or []:
        if card_of(msg) == pid:
            return where, str(msg.get("ts") or ""), True
    return "", "", True


_CARD_BUTTON = re.compile(r"^(?:approve|always|decline|detail):(\d{1,7})$")


def card_of(msg: dict) -> int | None:
    """The proposal a Slack message is the card of, from its own button
    values -- the same reading as `api.slack_decisions.card_id`. The board
    has no buttons, so this is None for it."""
    for block in (msg or {}).get("blocks") or []:
        for el in block.get("elements") or []:
            m = _CARD_BUTTON.match(str(el.get("value") or ""))
            if m:
                return int(m.group(1))
    return None


async def fill_links(rows: list[dict[str, Any]]) -> None:
    """Permalinks for the cards that lack one, a few per pass. A card is
    marked tried (`slack_link_tried_at`) only once it is settled: linked, or
    looked for in a history Slack did return and not there. Any Slack
    refusal leaves it to the next pass."""
    from .state import execute_sql
    done = 0
    for r in rows:
        if done >= BACKFILL_PER_PASS:
            return
        if r.get("slack_permalink") or r.get("slack_link_tried_at"):
            continue
        done += 1
        where, ts = str(r.get("slack_channel") or ""), str(r.get("slack_ts") or "")
        try:
            settled = True
            if not ts:
                where, ts, settled = await _find_card(
                    int(r["id"]), str(r.get("action") or ""), _params(r.get("params")),
                    r.get("created_at"))
            if not settled:
                continue
            link = await _permalink(where, ts) if ts else ""
            if ts and not link:
                settled = False                 # getPermalink refused: retry later
            await execute_sql(
                """UPDATE proposals
                      SET slack_channel = COALESCE(NULLIF($2, ''), slack_channel),
                          slack_ts = COALESCE(NULLIF($3, ''), slack_ts),
                          slack_permalink = NULLIF($4, ''),
                          slack_link_tried_at = CASE WHEN $5 THEN now()
                                                     ELSE slack_link_tried_at END
                    WHERE id = $1""", int(r["id"]), where, ts, link, settled)
            r.update(slack_channel=where or r.get("slack_channel"),
                     slack_ts=ts or r.get("slack_ts"), slack_permalink=link or None,
                     slack_link_tried_at=datetime.now(UTC) if settled else None)
        except Exception:
            log.warning("card #%s: no link this pass", r.get("id"), exc_info=True)


# ------------------------------------------------------------------ publish

async def _saved_board() -> tuple[str, str]:
    from .state import fetch_one
    row = await fetch_one("SELECT channel, ts FROM approvals_board WHERE key = 'board'")
    if not row:
        return "", ""
    return str(row.get("channel") or ""), str(row.get("ts") or "")


async def _save_board(where: str, ts: str) -> None:
    from .state import execute_sql
    await execute_sql(
        """INSERT INTO approvals_board (key, channel, ts) VALUES ('board', $1, $2)
           ON CONFLICT (key) DO UPDATE SET channel = $1, ts = $2, updated_at = now()""",
        where, ts)


# chat.update answers these when the board message is gone; a new one is
# posted and pinned in its place.
_GONE = {"message_not_found", "cant_update_message", "channel_not_found",
         "is_archived", "edit_window_closed"}


async def publish_board(where: str, text: str, blocks: list[dict[str, Any]]) -> str:
    """Edit the one board message in place; post (and pin) it only when there
    is none in this channel yet, or Slack says it is gone. Returns the
    board's ts. Raises RuntimeError with Slack's error otherwise."""
    saved_channel, saved_ts = await _saved_board()
    if saved_ts and saved_channel == where:
        data = await _slack("chat.update", {"channel": where, "ts": saved_ts,
                                            "text": text, "blocks": blocks})
        if data.get("ok"):
            return saved_ts
        if str(data.get("error")) not in _GONE:
            raise RuntimeError(f"chat.update: {data.get('error')}")
    data = await _slack("chat.postMessage", {"channel": where, "text": text,
                                             "blocks": blocks, "unfurl_links": False,
                                             "unfurl_media": False})
    if not data.get("ok"):
        raise RuntimeError(f"chat.postMessage: {data.get('error')}")
    ts = str(data.get("ts") or "")
    await _save_board(where, ts)
    pinned = await _slack("pins.add", {"channel": where, "timestamp": ts})
    if not pinned.get("ok") and pinned.get("error") != "already_pinned":
        log.warning("approvals board posted but not pinned: %s", pinned.get("error"))
    return ts


async def _ensure_channel(now: datetime) -> str | None:
    """The board's channel, made through the executor when there is none:
    `approvals_board.create_channel` is Tier 1 with a dry run and a rollback
    (invariants 1 and 8). Tried at most once an hour; watch-only refuses it,
    and then there is no board -- nothing spills into another channel."""
    global _create_tried_at
    found = await channel()
    if found:
        return found
    if _create_tried_at is not None and now - _create_tried_at < timedelta(hours=1):
        return None
    _create_tried_at = now
    from .executor import ExecutionRequest, execute
    made = await execute(ExecutionRequest(
        "approvals_board.create_channel", {},
        reason="the approvals board has no channel yet"))
    log.info("approvals channel: %s", made.detail)
    return await channel() if made.ok else None


async def refresh(now: datetime | None = None, *, force: bool = False) -> bool:
    """Redraw the board when something changed or it is due. True when the
    board message was written this call."""
    global _dirty, _last_at, _last_fp, _last_error
    if not enabled():
        return False
    now = now or datetime.now(UTC)
    async with _get_lock():
        try:
            rows = await pending()
        except Exception as exc:                        # noqa: BLE001
            _last_error = f"the queue could not be read ({type(exc).__name__})"
            log.warning("approvals board not redrawn: %s", _last_error)
            return False
        due = _last_at is None or now - _last_at >= refresh_every()
        if not (force or _dirty or due or fingerprint(rows) != _last_fp):
            return False
        try:
            where = await _ensure_channel(now)
        except Exception as exc:
            _last_error = f"no channel ({type(exc).__name__})"
            log.warning("approvals board: %s", _last_error, exc_info=True)
            return False
        if not where:
            _last_error = "no approvals channel"
            return False
        await fill_links(rows)
        sections = render(rows, now)
        try:
            await publish_board(where, fallback_text(len(rows)), blocks_of(sections))
        except Exception as exc:                        # noqa: BLE001
            _last_error = str(exc)[:200]
            log.warning("approvals board not written: %s", _last_error)
            return False
        _dirty, _last_at, _last_fp, _last_error = False, now, fingerprint(rows), ""
        return True


async def step() -> None:
    """The scheduler step `approvals_board`."""
    await refresh()
