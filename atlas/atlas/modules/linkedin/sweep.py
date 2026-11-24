"""The twice-daily sweep, on the server.

Reads the surfaces the Mac agent read -- sent and received invitations,
notifications, the message list, each page's followers and post analytics,
the profile dashboard -- diffs them against state, and writes state back.
It reads LinkedIn and writes ATLAS's own file; it never sends, accepts,
reacts or posts. The one LinkedIn write, publishing an approved post, is a
catalog action in publish.py and goes through the executor.

Everything here is best effort and says so. A surface that could not be
read is reported as unread, with a screenshot in the run log, never as
"nothing new". Any sign of a restriction ends the run at once.

Refill: when a page's approved+draft bank runs low, three drafts are written
into the bank as `draft`. Drafts never publish; each becomes an approval
card through `linkedin.approve_draft`.
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
from typing import Any

from . import KNOWN_PAGES, PAGES
from .browser import (
    HardStop,
    NoSession,
    SignedOut,
    body_text,
    browser,
    goto,
    keep_session,
    lock,
    snapshot,
)
from .store import (
    bank,
    file_lock,
    has_banned_link,
    load,
    log_run,
    page_obj,
    save,
    stamp,
    today,
)

log = logging.getLogger("atlas.linkedin.sweep")

PROFILE = "https://www.linkedin.com/in/example-profile/"
BUDGET_S = 600


def _names_in(text: str, names: list[str]) -> set[str]:
    low = text.lower()
    return {n for n in names if n and n.lower() in low}


_SENT_LIST_MARKERS = ("withdraw", "no pending invitations", "no sent invitations",
                      "no pending invites", "haven't sent", "have not sent")


def _sent_list_rendered(text: str) -> bool:
    """True when the page text shows a rendered Sent list: a Withdraw button
    on a row, or LinkedIn's own empty-list wording. Without one of these an
    empty match is a page that did not load, not a list with nobody in it."""
    low = (text or "").lower()
    return any(m in low for m in _SENT_LIST_MARKERS)


def _int(pattern: str, text: str) -> int | None:
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


async def _profile_links(page) -> list[str]:
    """Visible member names from profile links on the page, in order,
    deduplicated. Works on follower lists and invitation lists without
    depending on LinkedIn's class names.

    A failed read raises. It used to return [], which the surface counted as
    read -- "0 new" from a page ATLAS never saw (RA-E1); raising lets
    `Sweep.surface` record it as unread instead."""
    raw = await page.evaluate(
        "Array.from(document.querySelectorAll(\"a[href*='/in/']\"))"
        ".map(a => (a.innerText || '').trim().split('\\n')[0])"
        ".filter(t => t && t.length < 80)")
    seen: list[str] = []
    for name in raw:
        n = clean_name(str(name))
        if n and n not in seen and not n.lower().startswith(("view ", "see ")):
            seen.append(n)
    return seen


def clean_name(name: str) -> str:
    """LinkedIn renders "John" with curly quotes in one place and straight
    ones in another; the ledger must not hold the same person twice."""
    n = re.sub(r"[\u201c\u201d\u2033]", '"', name)
    n = re.sub(r"[\u2018\u2019]", "'", n)
    return re.sub(r"\s+", " ", n).strip()


# Rows in the message list that are LinkedIn's own furniture, not threads.
NOT_A_THREAD = ("sponsored", "promoted")
NAV_NAMES = {"home", "my network", "jobs", "messaging", "notifications", "me",
             "for business", "focused", "other", "unread", "my connections",
             "inmail", "starred", "new feed updates notifications"}


async def _conversations(page) -> list[tuple[str, str]]:
    """(name, last preview) per thread in the message list. A failed read
    raises, for the same reason as `_profile_links` (RA-E1)."""
    rows = await page.evaluate("""
            Array.from(document.querySelectorAll('li')).map(li => {
              const t = (li.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
              return t;
            }).filter(t => t.length >= 2 && t.length <= 8)""")
    out: list[tuple[str, str]] = []
    for lines in rows:
        name = clean_name(str(lines[0]))
        preview = " ".join(str(x) for x in lines[1:])[:240]
        if name.lower() in NAV_NAMES or any(w in preview.lower() for w in NOT_A_THREAD):
            continue
        if 2 <= len(name) <= 80 and (name, preview) not in out:
            out.append((name, preview))
    return out[:60]


async def pending_draft_ids(key: str) -> set[str] | None:
    """Entry ids in `key`'s bank that currently carry a pending
    `linkedin.approve_draft` card. A read-only query of ATLAS's own
    proposals, not of LinkedIn -- this is what `refill` asks before it may
    touch a draft's text.

    None means the read itself failed. Callers must treat that as EVERY
    draft frozen, never as none of them: silence is never consent, and a
    query that could not run is not evidence that nothing is pending.
    """
    from ...kernel.state import fetch_all
    try:
        rows = await fetch_all(
            """SELECT params->>'entry_id' AS entry_id FROM proposals
               WHERE status = 'pending' AND expires_at > now()
                 AND action = 'linkedin.approve_draft'
                 AND params->>'page' = $1""", key)
    except Exception:                                   # noqa: BLE001
        return None
    return {str(row["entry_id"]) for row in rows if row["entry_id"]}


class Sweep:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.news: list[str] = []      # what changed, for the notification
        self.log: list[str] = []       # everything, for the run log
        self.unread: list[str] = []    # surfaces that could not be read
        self.bounced: list[str] = []   # surfaces that bounced to sign-in
        self.read: int = 0             # surfaces that loaded signed in

    def note(self, line: str, *, news: bool = False) -> None:
        self.log.append(line)
        if news:
            self.news.append(line)

    async def surface(self, page, label: str, fn) -> None:
        try:
            await fn(page)
        except HardStop:
            raise
        except SignedOut as exc:
            # One page bouncing to the sign-in screen is not the same as the
            # session being dead: on the first server run the feed, invites
            # and notifications all loaded and only /messaging/ bounced.
            # Report the surface as unread and keep going; if every surface
            # bounces, the outcome says signed out.
            self.unread.append(label)
            self.bounced.append(label)
            self.note(f"{label}: LinkedIn asked to sign in again ({str(exc)[:100]})")
        except Exception as exc:                        # noqa: BLE001
            shot = await snapshot(page, label)
            self.unread.append(label)
            self.note(f"{label}: could not read ({type(exc).__name__}: "
                      f"{str(exc)[:120]}){' — ' + shot if shot else ''}")
        else:
            self.read += 1

    # ------------------------------------------------------------- surfaces

    async def sent_invites(self, page) -> None:
        text = await goto(page, "https://www.linkedin.com/mynetwork/invitation-manager/sent/")
        pending = [i for i in self.state.get("sent_invites", [])
                   if isinstance(i, dict) and i.get("status") == "pending"]
        still = _names_in(text, [i["name"] for i in pending])
        if pending and not still and not _sent_list_rendered(text):
            # Pending invites, none found, and nothing on the page that a
            # rendered Sent list always has. That is a page that did not
            # load its list, not "all of them were accepted": raising makes
            # the surface count as unread (UNKNOWN) and flips no status.
            raise RuntimeError(
                "the sent-invitations list did not render; "
                f"{len(pending)} pending invite(s) left unchanged")
        for inv in pending:
            inv["last_checked"] = stamp()
            if inv["name"] not in still:
                inv["status"] = "accepted_or_withdrawn"
                inv["changed"] = today()
                self.note(f"invite to {inv['name']} is no longer pending "
                          f"(accepted or withdrawn; verify on their profile)", news=True)
        self.note(f"sent invites: {len(pending)} pending checked, {len(still)} still pending")

    async def received_invites(self, page) -> None:
        await goto(page, "https://www.linkedin.com/mynetwork/invitation-manager/")
        names = await _profile_links(page)
        seen = self.state.setdefault("received_invites_seen", [])
        new = [n for n in names if n not in seen][:20]
        for n in new:
            seen.append(n)
            self.note(f"invitation received from {n} (not accepted; report only)", news=True)
        self.note(f"received invites: {len(names)} listed, {len(new)} new")

    async def notifications(self, page) -> None:
        text = await goto(page, "https://www.linkedin.com/notifications/")
        seen = self.state.setdefault("notifications_seen", [])
        keep = [ln.strip() for ln in text.split("\n")
                if re.search(r"viewed your profile|reacted|commented|mentioned you|"
                             r"started following|accepted your", ln, re.IGNORECASE)]
        new = [ln for ln in keep if ln not in seen][:15]
        for ln in new:
            seen.append(ln)
            self.note(f"notification: {ln[:140]}", news=True)
        del seen[:-200]
        self.note(f"notifications: {len(keep)} relevant, {len(new)} new")

    async def messages(self, page) -> None:
        await goto(page, "https://www.linkedin.com/messaging/")
        threads = await _conversations(page)
        pitches = {m.get("to"): m.get("text", "") for m in self.state.get("sent_messages", [])
                   if isinstance(m, dict)}
        inbox_seen = self.state.setdefault("inbox_seen", [])
        seen_keys = {(s.get("thread"), s.get("last_message_snippet"))
                     for s in inbox_seen if isinstance(s, dict)}
        for name, preview in threads:
            key = (name, preview[:120])
            if key in seen_keys:
                continue
            inbox_seen.append({"thread": name, "last_message_snippet": preview[:120],
                               "seen": stamp()})
            pitch = pitches.get(name)
            if pitch and pitch[:60].lower() in preview.lower():
                continue        # our pitch is still the last message: silence
            self.note(f"message thread changed: {name} — {preview[:100]}", news=True)
            if pitch:
                responders = self.state.setdefault("responders", [])
                if not any(r.get("name") == name for r in responders if isinstance(r, dict)):
                    responders.append({"name": name, "words": preview[:200],
                                       "when": stamp(), "answered": False,
                                       "status": "replied_unanswered"})
        del inbox_seen[:-300]
        self.note(f"messages: {len(threads)} threads read")

    async def page_followers(self, page, key: str) -> None:
        spec = PAGES[key]
        text = await goto(page, f"https://www.linkedin.com/company/{spec['id']}"
                                f"/admin/analytics/followers/")
        obj = page_obj(self.state, key)
        count = _int(r"(\d[\d,]*)\s+(?:total\s+)?followers", text)
        names = await _profile_links(page)
        seen = obj.setdefault("followers_seen", [])
        new = [n for n in names if n not in seen]
        for n in new:
            seen.append(n)
            self.note(f"{spec['name']}: new page follower {n}", news=True)
        if count is not None:
            before = obj.get(spec["followers_field"])
            obj[spec["followers_field"]] = count
            if before is not None and count != before:
                self.note(f"{spec['name']}: followers {before} → {count}", news=True)
            if names and count != len(seen):
                self.note(f"{spec['name']}: count {count} vs {len(seen)} names on the ledger")
        self.note(f"{spec['name']} followers: count={count} names_read={len(names)} new={len(new)}")

    async def page_analytics(self, page, key: str) -> None:
        spec = PAGES[key]
        text = await goto(page, f"https://www.linkedin.com/company/{spec['id']}"
                                f"/admin/analytics/updates/")
        obj = page_obj(self.state, key)
        posts = [p for p in obj.get("posts", []) if isinstance(p, dict)]
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
        for post in posts:
            first = str(post.get("first_line") or "")[:40].lower()
            if not first:
                continue
            for i, ln in enumerate(lines):
                if first in ln.lower():
                    window = " ".join(lines[i:i + 8])
                    imp = _int(r"(\d[\d,]*)\s+impressions?", window)
                    if imp is not None and imp != post.get("impressions"):
                        self.note(f"{spec['name']}: '{first[:30]}…' impressions "
                                  f"{post.get('impressions')} → {imp}", news=True)
                        post["impressions"] = imp
                    break
        self.note(f"{spec['name']} post analytics: {len(posts)} posts on ledger")

    async def page_notifications(self, page, key: str) -> None:
        """The company page's own admin notifications (read-only). Reactions,
        comments, mentions and new followers on the page, not on the owner's
        profile. Only the count and what is new are kept."""
        spec = PAGES[key]
        text = await goto(page, f"https://www.linkedin.com/company/{spec['id']}"
                                f"/admin/notifications/all/")
        obj = page_obj(self.state, key)
        seen = obj.setdefault("notifications_seen", [])
        keep = [ln.strip() for ln in text.split("\n")
                if re.search(r"commented|reacted|mentioned|shared|reposted|"
                             r"started following|followed|liked|tagged",
                             ln, re.IGNORECASE)]
        new = [ln for ln in keep if ln not in seen][:15]
        for ln in new:
            seen.append(ln)
            self.note(f"{spec['name']} page notification: {ln[:140]}", news=True)
        del seen[:-200]
        obj["notifications_read"] = {"when": stamp(), "relevant": len(keep),
                                     "new": len(new)}
        self.note(f"{spec['name']} page notifications: {len(keep)} relevant, "
                  f"{len(new)} new")

    async def dashboard(self, page) -> None:
        text = await goto(page, "https://www.linkedin.com/dashboard/")
        row = {"date": today(),
               "profile_followers": _int(r"(\d[\d,]*)\s+followers", text),
               "post_impressions_7d": _int(r"(\d[\d,]*)\s+post impressions", text),
               "profile_viewers_90d": _int(r"(\d[\d,]*)\s+profile viewers", text),
               "search_appearances": _int(r"(\d[\d,]*)\s+search appearances", text)}
        ptext = await goto(page, PROFILE)
        row["connections"] = _int(r"(\d[\d,]*)\s+connections", ptext)
        metrics = self.state.setdefault("improver", {}).setdefault("metrics", [])
        # One row per calendar day; a second run the same day amends it.
        for existing in metrics:
            if isinstance(existing, dict) and existing.get("date") == row["date"]:
                existing.update({k: v for k, v in row.items() if v is not None})
                break
        else:
            metrics.append(row)
        del metrics[:-60]
        self.note("dashboard: " + ", ".join(f"{k}={v}" for k, v in row.items() if v is not None))

    # -------------------------------------------------------------- refill

    async def refill(self, key: str) -> None:
        if key == "mmt":
            # A fresh MMT bank holder is stored in state, and its posts are
            # never reshared to the owner's profile unless he turns that on.
            hold = self.state.setdefault(KNOWN_PAGES[key]["bank_key"], {})
            hold.setdefault("post_bank", [])
            post = hold.setdefault("posting", {})
            post.setdefault("enabled", True)
            post.setdefault("reshare_to_profile", False)
        entries, posting, holder = bank(self.state, key)

        # A draft with a pending approval card is frozen: `approve_draft`
        # checks the text against a hash the card was raised with, and
        # rewriting the draft under this sweep would fail every such
        # approval with "the draft changed since you saw it" for a change
        # nobody asked for. `pending_draft_ids` returns None when the read
        # itself failed -- silence is never consent, so that reads as EVERY
        # draft frozen, never as none of them.
        pending = await pending_draft_ids(key)
        if pending is None:
            self.note(f"{PAGES[key]['name']}: could not read pending approval "
                      f"cards; every draft treated as frozen this sweep")
        for e in entries:
            if e.get("status") == "draft":
                e["frozen"] = pending is None or str(e.get("id")) in pending
        holder["post_bank"] = entries

        # "Ready" means offerable soon, not yet approved: an image or video
        # still waiting on its picture (`needs_media`) is as committed to
        # the bank as a draft is, and counting only approved+draft would
        # refill again on top of work already queued.
        live = [e for e in entries
                if e.get("status") in ("approved", "draft", "needs_media")]
        # At least 3 approved-or-ready posts per page, always (Task 64) --
        # a lower `refill_below` in state can shrink the trigger point but
        # never the floor this promises.
        below = max(3, int(posting.get("refill_below", 4)))
        if len(live) >= below:
            return
        drafts_waiting = [e for e in entries
                          if e.get("status") in ("draft", "needs_media")]
        if len(drafts_waiting) >= 3:
            self.note(f"{PAGES[key]['name']}: refill skipped — {len(drafts_waiting)} drafts "
                      f"already wait on approval or media; more would add "
                      f"nothing publishable")
            return
        if key == "mmt":
            from ..sagemmt.weekly import enabled as sage_owns_calendar
            if sage_owns_calendar():
                # Sage owns the page calendar while SAGE_MMT_LINKEDIN is on;
                # her weekly job drafts, the sweep does not refill the series.
                self.note(f"{PAGES[key]['name']}: calendar is Sage's, no series refill")
                return
            # The fixed fact-backed series, never the model (invariant 6).
            from .mmt_series import series_due
            new = series_due(self.state, live_ok=None)[:3]
            if not new:
                self.note(f"{PAGES[key]['name']}: series complete, nothing to draft")
                return
            used = {str(e.get("id")) for e in entries} | (pending or set())
            for item in new:
                if item["series_id"] in used:
                    continue
                entry = _new_entry(item["series_id"], item)
                entry["series_id"] = item["series_id"]
                entry["fact_ids"] = item["fact_ids"]
                entry["drafted_by"] = item["drafted_by"]
                entries.append(entry)
            holder["post_bank"] = entries
            self.note(f"{PAGES[key]['name']}: drafted {len(new)} series post(s), "
                      f"awaiting approval", news=True)
            return
        try:
            new = await draft_posts(self.state, key, 3)
        except Exception as exc:                        # noqa: BLE001
            self.note(f"{PAGES[key]['name']}: refill failed: {exc}")
            return
        prefix = {"flycheap": "B", "guardian": "G"}.get(key, "A")   # A: Academy
        # An id with a pending card stays reserved even if its entry has
        # somehow vanished from the bank -- a fresh draft must never inherit
        # an approval meant for different words.
        used = {str(e.get("id")) for e in entries} | (pending or set())
        n = 1
        made_kinds = []
        for item in new:
            while f"{prefix}{n}" in used:
                n += 1
            entries.append(_new_entry(f"{prefix}{n}", item))
            used.add(f"{prefix}{n}")
            made_kinds.append(item.get("kind", "text"))
        holder["post_bank"] = entries
        self.note(f"{PAGES[key]['name']}: drafted {len(new)} post(s) "
                  f"({', '.join(made_kinds)}), awaiting media/approval",
                  news=True)

    # ----------------------------------------------------------------- run

    async def run(self) -> None:
        async with lock():
            await self._run()

    async def _run(self) -> None:
        pw, b, ctx = await browser()
        try:
            page = await ctx.new_page()
            await self.surface(page, "sent-invites", self.sent_invites)
            await self.surface(page, "received-invites", self.received_invites)
            await self.surface(page, "notifications", self.notifications)
            await self.surface(page, "messages", self.messages)
            for key in PAGES:
                await self.surface(page, f"{key}-followers",
                                   lambda p, k=key: self.page_followers(p, k))
                await self.surface(page, f"{key}-analytics",
                                   lambda p, k=key: self.page_analytics(p, k))
                await self.surface(page, f"{key}-notifications",
                                   lambda p, k=key: self.page_notifications(p, k))
            await self.surface(page, "dashboard", self.dashboard)
            if self.read:
                await keep_session(ctx)
        finally:
            await ctx.close()
            await b.close()
            await pw.stop()
        if self.read:
            for key in PAGES:
                await self.refill(key)


def _new_entry(entry_id: str, item: dict[str, Any]) -> dict[str, Any]:
    """A fresh post-bank entry for one drafted `item` (see `draft_posts`).
    `image`/`video` start `needs_media`: `fill_media_for` generates the
    picture outside the sweep's browser lock and time budget, then flips
    the entry to `draft` -- an image or video post is never offered for
    approval without the picture its kind promised (Task 64)."""
    kind = item.get("kind", "text")
    base = {"id": entry_id, "kind": kind, "created": today(),
            "drafted_by": "atlas", "card_rev": 1, "frozen": False, "media": None}
    if kind == "poll":
        base["status"] = "draft"
        base["poll"] = item["poll"]
        base["text"] = item["poll"].get("intro", "")
    elif kind in ("image", "video"):
        base["status"] = "needs_media"
        base["text"] = item["text"]
        base["media_prompt"] = item.get("media_prompt", "")
    else:
        base["status"] = "draft"
        base["text"] = item.get("text", "")
    return base


# The short text every image/video post is limited to -- "about a third of
# today's [text-only] length" (the owner, Task 64): a hook, at most 2 more short
# lines, the link, 1-2 hashtags. Enforced in code, not only asked of the
# model: clamped rather than trusted.
MAX_SHORT_CHARS = 280
MAX_POLL_QUESTION_CHARS = 140     # LinkedIn's own limit, read live 2026-10-13
MAX_POLL_OPTION_CHARS = 30        # LinkedIn's own limit, read live 2026-10-13
MIN_POLL_OPTIONS = 2
MAX_POLL_OPTIONS = 4

_FIELD_NAMES = ("KIND", "TEXT", "IMAGE_PROMPT", "VIDEO_PROMPT", "INTRO",
               "QUESTION", "OPTIONS")
_FIELD_RE = re.compile(r"^([A-Z_]+):\s*(.*)$")


def _parse_fields(block: str) -> dict[str, str]:
    """One drafted block's fields, keyed by the FIELD: name the prompt
    asked for. A field's value may run over several lines -- everything up
    to the next recognised FIELD: line belongs to it."""
    fields: dict[str, list[str]] = {}
    current: str | None = None
    for line in block.splitlines():
        m = _FIELD_RE.match(line.strip())
        if m and m.group(1) in _FIELD_NAMES:
            current = m.group(1)
            fields[current] = [m.group(2)]
        elif current:
            fields[current].append(line)
    return {k: "\n".join(v).strip() for k, v in fields.items()}


def _links_ok(text: str, spec: dict[str, str]) -> bool:
    """The same rule `publish.gate` enforces on an approved entry, asked
    here too so a banned or missing link never even reaches the bank."""
    if has_banned_link(text):
        return False
    return spec["link"] in text


def _to_item(fields: dict[str, str], spec: dict[str, str]) -> dict[str, Any] | None:
    kind = fields.get("KIND", "").strip().lower()
    if kind in ("image", "video"):
        text = fields.get("TEXT", "").strip()
        prompt_key = "IMAGE_PROMPT" if kind == "image" else "VIDEO_PROMPT"
        prompt = fields.get(prompt_key, "").strip()
        if not text or not prompt or not _links_ok(text, spec):
            return None
        return {"kind": kind, "text": text[:MAX_SHORT_CHARS],
                "media_prompt": prompt}
    if kind == "poll":
        intro = fields.get("INTRO", "").strip()
        question = fields.get("QUESTION", "").strip()
        options = [o.strip() for o in fields.get("OPTIONS", "").split("|")
                  if o.strip()][:MAX_POLL_OPTIONS]
        # A poll intro carries no link requirement -- LinkedIn's poll has no
        # room for one -- but a banned one still refuses the block, the same
        # as an image or video's TEXT.
        if has_banned_link(intro):
            return None
        if not question or len(options) < MIN_POLL_OPTIONS:
            return None
        return {"kind": "poll",
                "poll": {"intro": intro, "question": question[:MAX_POLL_QUESTION_CHARS],
                         "options": [o[:MAX_POLL_OPTION_CHARS] for o in options],
                         "duration": "1w", "visibility": "public"}}
    return None


_MD_PROMPT = re.compile(r"^\W*(IMAGE_PROMPT|VIDEO_PROMPT)\s*:\s*(.+?)\W*$", re.IGNORECASE)
_MD_OPTIONS = re.compile(r"^\W*options?\s*:\s*(.+?)\W*$", re.IGNORECASE)


def _loose_fields(block: str) -> dict[str, str]:
    """The fields of a block that ignored the FIELD: shape -- the free
    models write markdown (2026-10-06: '**Hook: ...**', '*IMAGE_PROMPT:
    ...*', 'Options: a | b | c'); every block of a good batch was dropped.
    The prompt line names the kind, 'Options:' names a poll, and the rest of
    the lines are the text. Nothing is invented: a block with neither is
    still nothing."""
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    if not lines:
        return {}
    out: dict[str, str] = {}
    text: list[str] = []
    for ln in lines:
        if (m := _MD_PROMPT.match(ln)):
            out[m.group(1).upper()] = m.group(2).strip()
            continue
        if (m := _MD_OPTIONS.match(ln)):
            out["OPTIONS"] = m.group(1).strip()
            continue
        # A "Block 3 -- KIND: poll" heading is the model echoing the prompt,
        # never post text (2026-10-13, G18).
        if re.match(r"^\W*block\s*\d+\b", ln, flags=re.IGNORECASE):
            continue
        clean = re.sub(r"^\W*(hook|intro|text|question)\s*:\W*", "",
                       ln.strip("*_ ").strip(), flags=re.IGNORECASE)
        clean = clean.strip("*_ ").strip()
        if clean:
            text.append(clean)
    if "IMAGE_PROMPT" in out or "VIDEO_PROMPT" in out:
        out["KIND"] = "image" if "IMAGE_PROMPT" in out else "video"
        out["TEXT"] = "\n".join(text)
    elif "OPTIONS" in out:
        out["KIND"] = "poll"
        question = next((ln for ln in reversed(text) if ln.endswith("?")), "")
        out["QUESTION"] = re.sub(r"^\W*quick poll\s*:\s*", "", question, flags=re.IGNORECASE)
        out["INTRO"] = next((ln for ln in text if ln != question), "")
    return out


def _parse_batch(text: str, spec: dict[str, str]) -> list[dict[str, Any]]:
    blocks = re.split(r"(?m)^\s*-{3,}\s*$", text)
    items = []
    for block in blocks:
        fields = _parse_fields(block)
        if "KIND" not in fields:
            fields = _loose_fields(block)
        item = _to_item(fields, spec)
        if item is not None:
            items.append(item)
    return items


async def draft_posts(state: dict[str, Any], key: str, count: int) -> list[dict[str, Any]]:
    """Up to `count` drafts, rotating image / video / poll, from the model.
    They go into the bank `needs_media` (image/video) or `draft` (poll) and
    never publish without his approval.

    Each item is `{"kind": "image"|"video", "text": ..., "media_prompt": ...}`
    or `{"kind": "poll", "poll": {"intro", "question", "options", ...}}`.
    A block the model wrote that does not parse into one of those shapes
    (missing link, one-option poll, no image prompt) is dropped rather than
    guessed at -- fewer than 3 drafted this round is not a bug, an invented
    field would be.
    """
    from ...kernel.llm import Message, Need, think

    if key == "mmt":
        return []          # a second lock: MMT posts come only from mmt_series
    spec = PAGES[key]
    entries, _, _ = bank(state, key)
    recent = [str(e.get("text") or (e.get("poll") or {}).get("question", ""))[:200]
             for e in entries[-6:]]
    framing = {
        "flycheap": "the owner is co-founder and developer of FlyCheap",
        "academy": ("the owner built ATLAS Academy: practical AI courses for work and "
                    "everyday life, written from running his own AI agent. Name "
                    "one course or lesson idea per post (Build a Safe AI Operator, "
                    "Sell With AI, AI for Every Age), teach one concrete thing, "
                    "and point to the store. Never an income or earnings claim, "
                    "never a price, never a guarantee"),
    }.get(key, "the owner founded and owns Vault")
    rules = (
        f"Write exactly 3 LinkedIn posts for the {spec['name']} company page, "
        f"one of each kind in this order: image, video, poll. Pictures speak "
        f"louder than words, so keep every line short.\n\n"
        f"Block 1 -- KIND: image. TEXT: a hook first line, then at most 2 more "
        f"short lines, then the link {spec['link']} exactly once, then 1-2 "
        f"hashtags. Under {MAX_SHORT_CHARS} characters total -- about a third "
        f"of a normal post. IMAGE_PROMPT: one line describing a still image in "
        f"the {spec['name']} brand look -- abstract or illustrative, never a "
        f"real person, never a fake screenshot or a fake statistic.\n\n"
        f"Block 2 -- KIND: video. TEXT: same shape and limit as the image "
        f"block. VIDEO_PROMPT: one line describing a short video or slideshow "
        f"in the same brand look, same restrictions as the image prompt.\n\n"
        f"Block 3 -- KIND: poll. INTRO: one short line introducing the "
        f"question. QUESTION: the poll question. OPTIONS: 2 to 4 short "
        f"options separated by | .\n\n"
        f"Separate the 3 blocks with a line containing only ---. {framing}; "
        f"never call him owner/founder of FlyCheap or co-founder of Vault. No "
        f"invented numbers beyond '700+ airlines' for FlyCheap; no pricing for "
        f"Vault. No em-dashes anywhere. Different angle from each of these "
        f"recent posts.")
    completion = await think(
        Need.DRAFT, rules,
        [Message("user", "Recent posts, do not repeat:\n" + "\n\n".join(recent))],
        max_tokens=1200)
    text = getattr(completion, "text", "") or ""
    items = _parse_batch(text, spec)
    if key == "academy":
        # The course lane's own rule (content/guard.py, the reels): a post
        # that claims income is dropped here, before it can be a draft.
        from ..content import guard
        items = [i for i in items if guard.income_refusal(
            str(i.get("text") or "") + " " + str((i.get("poll") or {}).get("question", ""))
        ) is None]
    return items[:count]


# ------------------------------------------------------------------ media

async def fill_media_for(key: str) -> dict[str, Any]:
    """Generate the picture for every `needs_media` draft in `key`'s bank.

    Deliberately outside the browser `lock()` and the sweep's
    `asyncio.wait_for(..., BUDGET_S)`: a Gemini still can take a minute and
    Veo-style retries can take minutes more, and either one running inside
    the sweep's own budget would time out a run that has nothing to do with
    the browser. `file_lock` is instead taken twice, briefly: once to read
    the pending list, once per entry to attach what was made -- never held
    across the slow network/ffmpeg call itself, so a publish waiting on the
    same lock is never stuck behind it.
    """
    async with file_lock():
        state = load()
    entries, _, _ = bank(state, key)
    pending = [dict(e) for e in entries if e.get("status") == "needs_media"]
    made = 0
    for pending_entry in pending:
        from . import media
        path, note = await media.make(pending_entry.get("kind", ""), key,
                                       pending_entry.get("media_prompt")
                                       or pending_entry.get("text", ""))
        async with file_lock():
            state = load()
            entries, _, holder = bank(state, key)
            for cur in entries:
                if str(cur.get("id")) == str(pending_entry["id"]) \
                        and cur.get("status") == "needs_media":
                    if path is not None:
                        cur["media"] = path
                        cur["media_note"] = note
                        cur["status"] = "draft"
                        made += 1
                    else:
                        cur["media_note"] = note
                    break
            holder["post_bank"] = entries
            save(state)
    return {"attempted": len(pending), "made": made}


async def run_sweep() -> dict[str, Any]:
    """The check's entry point. Returns a summary; raises nothing the check
    cannot turn into an honest observation."""
    async with file_lock():
        return await _run_sweep()


async def _run_sweep() -> dict[str, Any]:
    # No state file yet is a first run, not an error: starting from nothing
    # lets the run record how it went (even "no session"), so every check that
    # reads the file can say why it cannot measure instead of "no state".
    try:
        state = load()
    except FileNotFoundError:
        state = {}
    sweep = Sweep(state)
    outcome = "ok"
    try:
        await asyncio.wait_for(sweep.run(), timeout=BUDGET_S)
    except NoSession as exc:
        outcome = f"no session: {exc}"
    except SignedOut as exc:
        outcome = f"signed out: {exc}"
    except HardStop as exc:
        outcome = f"hard stop: {exc}"
        state.setdefault("alerts", []).append({"when": stamp(), "what": str(exc)})
    except TimeoutError:
        outcome = f"time budget of {BUDGET_S}s exhausted"
    else:
        if sweep.bounced and sweep.read == 0:
            outcome = ("signed out: every surface bounced to the sign-in page; "
                       "re-run ./bootstrap/linkedin-login.sh")
    state["last_run"] = stamp()
    state["runs"] = int(state.get("runs") or 0) + 1
    state["last_run_result"] = {"when": state["last_run"], "outcome": outcome,
                                "news": sweep.news[:20], "unread": sweep.unread,
                                "by": "atlas-server"}
    save(state)
    log_run([f"outcome: {outcome}", *sweep.log])
    return {"outcome": outcome, "news": sweep.news, "unread": sweep.unread,
            "log": sweep.log}


async def _cli() -> None:
    from ...kernel.llm import build_routes
    build_routes()
    if "--check" in sys.argv:
        pw, b, ctx = await browser()
        try:
            page = await ctx.new_page()
            await goto(page, "https://www.linkedin.com/feed/", expect="/feed")
            text = await body_text(page)
            print("signed in; feed loaded", f"({len(text)} chars of page text)")
        finally:
            await ctx.close()
            await b.close()
            await pw.stop()
        return
    result = await run_sweep()
    print(result["outcome"])
    for line in result["log"]:
        print(" -", line)


if __name__ == "__main__":
    asyncio.run(_cli())
