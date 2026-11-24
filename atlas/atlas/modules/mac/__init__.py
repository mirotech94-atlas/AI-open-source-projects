"""Mac -- Miro's laptop, driven instantly through a small agent on it.

Miro, 2026-09-28: "it should be able to open things automatically and
straight away when I ask". The agent on the Mac (bootstrap/mac/mac_agent.py,
a LaunchAgent running as him) holds a long-poll open to /mac/next; a job
queued here reaches it within a second. It runs only the closed list below,
re-validates every parameter itself, and runs no shell.

* Every action is `principal_only`: it runs only when Miro asked on a
  channel that proves it is him. No agent, reasoner, fix sweep or injected
  text can reach his laptop (executor.execute enforces it).
* Tier 1, straight away, for what stays on his own screen and can be put
  back or left alone: apps (open and quit), YouTube Music, reminders, a new
  note, a web address or a web search in his browser, the volume (the dry
  run reads the level, the rollback restores it) and a local notification.
  `open_url`, `web_search` and `quit_app` were Tier 2 cards until Miro's
  standing ruling of 2026-09-30 (anything not urgent runs without a card)
  and his ask of 2026-10-06 to drive the Mac by voice without opening
  ATLAS. Their guards are in code: the same closed parameter checks here
  and on the Mac, `principal_only`, the hourly ceiling, and the system
  processes `quit_app` never touches. Their rollbacks say exactly what they
  can put back: a quit app is reopened; a page load and a new note are
  recorded, not undone (a GET cannot be taken back, and deleting a note is
  Tier 3).
* Tier 2, a card he approves, for what reaches past his screen or runs
  something he did not read: `send_imessage` (a message in his name, no
  undo) and `run_shortcut` (whatever that Shortcut does). Each has a pure
  `vet`, so a card that could only fail is never raised, and a
  `card_detail`, so the card carries the exact recipient and text, or name.
  `send_imessage` is also `card_always`: he approves the words that will be
  sent, not the words he said. The spoken doors run Tier 1 only
  (kernel.spoken).
* A job expires 60 s after it is queued, inside the same UPDATE that claims
  it: a request made while the lid is shut never runs hours later. The dry
  run refuses at once when the agent has not polled lately ("asleep").
* A job the agent claimed and never answered is "outcome unknown", not
  "failed" -- the app may well have opened.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote_plus, urlsplit

from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    Health,
    Observation,
    Severity,
    Tier,
)

FRESH = timedelta(seconds=90)          # a live agent polls at least this often
JOB_TTL = timedelta(seconds=60)
WAIT_S = 30.0
OLD_AGENT = ("the agent on your Mac is older than this and has no '{op}' "
             "operation, so nothing ran -- update it with "
             "bootstrap/mac/install-agent.sh")

# Parameters are checked here AND again on the Mac. An app is a bare name
# resolved there inside the application folders; never a path.
_APP = re.compile(r"^[\w][\w .&'+-]{0,60}$")
DENIED_APPS = frozenset(a.lower() for a in (
    "Terminal", "iTerm", "iTerm2", "Script Editor", "Automator", "Keychain Access",
    "System Settings", "System Preferences", "Disk Utility", "Installer",
    "Activity Monitor", "Console", "Migration Assistant", "Boot Camp Assistant",
    # The camera comes on as these open (2026-10-06: no camera by voice).
    "Photo Booth", "FaceTime"))
MUSIC_COMMANDS = ("play", "pause", "next", "previous")
_INVERSE = {"play": "pause", "pause": "play", "next": "previous", "previous": "next"}


def _app_problem(app: str) -> str | None:
    if not _APP.match(app or "") or "/" in app or ".." in app:
        return f"{app!r} is not an app name"
    if app.strip().lower() in DENIED_APPS:
        return f"{app} is one ATLAS never opens on the Mac"
    return None


# Never quit, whatever a name resolves to: the desktop, the login session,
# the menu bar. Most of these are not in the folders `open_app` searches, so
# they cannot resolve at all; this says it in code regardless.
QUIT_DENIED = frozenset(a.lower() for a in (
    "Finder", "loginwindow", "Dock", "SystemUIServer", "WindowServer",
    "ControlCenter", "Control Center", "NotificationCenter", "Spotlight",
    "System Events", "Siri", "SecurityAgent", "UserNotificationCenter"))


def _quit_problem(app: str) -> str | None:
    if (why := _app_problem(app)) is not None:
        return why
    if app.strip().lower() in QUIT_DENIED:
        return f"{app} is part of macOS itself; ATLAS never quits it"
    return None


# ---- iMessage: who and what, checked before a card exists and on the Mac
E164 = re.compile(r"^\+[1-9]\d{6,14}\Z")
EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}"
                   r"(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,24}\Z")
# A contact's name as he says it: a letter first, then letters, digits,
# spaces, dots, apostrophes and hyphens. Matched exactly on the Mac.
CONTACT = re.compile(r"^[^\W\d_][\w .'-]{0,59}\Z")
TEXT_MAX = 1000
# Controls other than newline and tab, and the bidi and direction marks that
# can make a card read differently from what is sent.
HIDDEN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def recipient_kind(to: Any) -> str | None:
    """'phone', 'email' or 'contact' -- or None when it is none of them."""
    if not isinstance(to, str):
        return None
    if E164.match(to):
        return "phone"
    if EMAIL.match(to):
        return "email"
    if CONTACT.match(to) and to == to.strip() and "  " not in to:
        return "contact"
    return None


def imessage_problem(to: Any, text: Any) -> str | None:
    if recipient_kind(to) is None:
        return ("the recipient must be a phone number in +E.164 form, an email "
                "address, or a contact's name exactly as it is in Contacts")
    if not isinstance(text, str) or not text.strip():
        return "the message is empty"
    if len(text) > TEXT_MAX:
        return f"the message is {len(text)} characters; the limit is {TEXT_MAX}"
    if HIDDEN.search(text):
        return "the message has hidden control characters in it"
    return None


def vet_imessage(params: dict[str, Any]) -> str | None:
    return imessage_problem(params.get("to"), params.get("text"))


def shown(value: Any) -> str:
    """A value as Slack must display it: `&`, `<` and `>` escaped, so
    `<https://phish|Your parcel>` in a message is shown as those characters,
    not as a link labelled "Your parcel", and `<!here>` pings no one."""
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


def card_imessage(params: dict[str, Any]) -> str:
    to, text = str(params.get("to", "")), str(params.get("text", ""))
    how = {"phone": "phone number", "email": "email address",
           "contact": ("a contact's name: your Mac resolves it to exactly one "
                       "person with exactly one number or address, or refuses; "
                       "the receipt names the number it went to")
           }.get(recipient_kind(to) or "", "not a valid recipient")
    quoted = "\n".join("> " + shown(line) for line in text.split("\n"))
    return (f"*To:* {shown(to)} ({how})\n*Text* ({len(text)} characters, sent "
            f"exactly as written, cannot be unsent):\n{quoted}")


# ---- URLs
URL_MAX = 2048
# His own sites and the platforms he posts to. The dry run says whether a
# host is one of them, so an address that only looks like one stands out in
# the audit. Every URL is the same tier whatever its host (Tier 1 since
# 2026-10-06): a tier chosen from a parameter would be a tier inferred at
# runtime (CLAUDE.md invariant 3). A page load in his signed-in browser has
# no real rollback, and the undo says so.
OWN_SITES = ("atlas.storefront.test", "flycheap.example.com", "my.guarder.example.com",
             "atlasacademy-ai.myshopify.com", "admin.shopify.com",
             "youtube.com", "instagram.com", "tiktok.com")


def url_host(url: Any) -> str | None:
    """The host, ASCII (punycode), of an http(s) URL ATLAS may hand to
    `open`; None for anything else."""
    if not isinstance(url, str) or not url or len(url) > URL_MAX:
        return None
    if any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7f for c in url):
        return None
    if HIDDEN.search(url) or any(c in url for c in '\\<>"'):
        return None
    try:
        parts = urlsplit(url)
        _ = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or "@" in parts.netloc:
        return None
    if not url.startswith(f"{parts.scheme}://"):
        return None
    host = (parts.hostname or "").rstrip(".")
    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return None
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
        return None
    return host


def is_own_site(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in OWN_SITES)


def url_problem(url: Any) -> str | None:
    if url_host(url) is None:
        return ("only a plain http:// or https:// address opens: no other "
                "scheme, no user:password@, no spaces or hidden characters, "
                f"under {URL_MAX} characters")
    return None


def vet_url(params: dict[str, Any]) -> str | None:
    return url_problem(params.get("url"))


# ---- Shortcuts: the Mac holds the allowlist; the server only has a name
SHORTCUT_MAX = 100


def shortcut_problem(name: Any) -> str | None:
    if not isinstance(name, str) or not name or len(name) > SHORTCUT_MAX:
        return f"a Shortcut's exact name, under {SHORTCUT_MAX} characters"
    if name != name.strip() or name.startswith("-"):
        return "a Shortcut's name cannot start with '-' or a space"
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in name) or HIDDEN.search(name):
        return "a Shortcut's name cannot contain control characters"
    return None


def vet_shortcut(params: dict[str, Any]) -> str | None:
    return shortcut_problem(params.get("name"))


def card_shortcut(params: dict[str, Any]) -> str:
    return (f"*Shortcut:* {shown(params.get('name', ''))}\nRuns only if that exact "
            "name is a line in ~/.config/atlas/shortcuts-allow.txt on your Mac.")


def vet_quit(params: dict[str, Any]) -> str | None:
    return _quit_problem(str(params.get("app", "")))


# ---- volume and notifications
def volume_level(level: Any) -> int | None:
    """0-100 as an int, from an int or a string of digits. Never a bool."""
    if isinstance(level, bool):
        return None
    if isinstance(level, int):
        n = level
    elif isinstance(level, str) and re.fullmatch(r"\d{1,3}", level.strip()):
        n = int(level.strip())
    else:
        return None
    return n if 0 <= n <= 100 else None


NOTE_TITLE_MAX, NOTE_BODY_MAX = 100, 500
NOTE_TEXT_MAX = 2000                   # a note's text (mac.new_note)


def notify_problem(title: Any, body: Any) -> str | None:
    if (not isinstance(title, str) or not title.strip()
            or len(title) > NOTE_TITLE_MAX):
        return f"a notification needs a title under {NOTE_TITLE_MAX} characters"
    if not isinstance(body, str) or len(body) > NOTE_BODY_MAX:
        return f"the notification's text must be under {NOTE_BODY_MAX} characters"
    if HIDDEN.search(title) or HIDDEN.search(body):
        return "the notification has hidden control characters in it"
    return None


async def agent_state() -> tuple[datetime | None, str | None]:
    """(last poll, why it is not usable now or None)."""
    from ...kernel.state import fetch_one
    try:
        row = await fetch_one("SELECT max(last_seen) AS at FROM mac_agent")
    except Exception as exc:  # noqa: BLE001
        return None, f"the Mac agent's state could not be read ({type(exc).__name__})"
    at = row["at"] if row else None
    if at is None:
        return None, "the Mac agent has never connected -- it is not installed yet"
    if datetime.now(UTC) - at > FRESH:
        return at, (f"your Mac is asleep or offline -- its agent last checked in "
                    f"{at:%a %H:%M} UTC, so nothing was sent to it")
    return at, None


async def _dispatch(op: str, params: dict) -> ActionResult:
    """Queue one job, wait for the Mac's answer."""
    from ...kernel.state import fetch_one
    _seen, why = await agent_state()
    if why:
        return ActionResult(False, why)
    row = await fetch_one(
        """INSERT INTO mac_jobs (op, params, expires_at)
           VALUES ($1, $2::jsonb, now() + $3::interval) RETURNING id""",
        op, json.dumps(params), JOB_TTL)
    jid = int(row["id"])
    deadline = asyncio.get_running_loop().time() + WAIT_S
    while asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.4)
        r = await fetch_one("SELECT status, ok, detail, token FROM mac_jobs WHERE id = $1",
                            jid)
        if r and r["status"] == "done":
            detail = str(r["detail"] or "")
            if not r["ok"] and detail.startswith("unknown operation"):
                # An agent installed before this operation existed refuses
                # it by name, and runs nothing: said as what it is.
                detail = OLD_AGENT.format(op=op)
            return ActionResult(bool(r["ok"]), detail,
                                evidence={"job": jid}, rollback_token=r["token"] or None)
    gone = await fetch_one(
        """UPDATE mac_jobs SET status = 'expired'
            WHERE id = $1 AND status = 'queued' RETURNING id""", jid)
    if gone is not None:
        return ActionResult(False, "your Mac didn't pick it up in time; nothing ran")
    await fetch_one("UPDATE mac_jobs SET status = 'unknown' WHERE id = $1 "
                    "AND status = 'claimed' RETURNING id", jid)
    return ActionResult(False, "your Mac took the job but never answered -- whether it "
                               "happened is unknown; check the Mac")


# ------------------------------------------------------------------ actions

async def open_app_dry(app: str = "") -> ActionResult:
    if (why := _app_problem(app)) is not None:
        return ActionResult(False, why)
    _seen, why = await agent_state()
    return ActionResult(why is None, why or f"would open {app} on your Mac")


async def open_app(app: str = "") -> ActionResult:
    if (why := _app_problem(app)) is not None:
        return ActionResult(False, why)
    return await _dispatch("open_app", {"app": app.strip()})


async def undo_open_app(token: str) -> ActionResult:
    """Quit the app only if this action is what opened it."""
    try:
        t = json.loads(token)
    except ValueError:
        return ActionResult(False, "unreadable rollback token")
    if not t.get("opened_by_us"):
        return ActionResult(True, f"{t.get('app')} was already open; left as it was")
    return await _dispatch("quit_app", {"app": str(t.get("app"))})


async def play_music_dry(query: str = "") -> ActionResult:
    if not (query or "").strip() or len(query) > 120:
        return ActionResult(False, "say what to play, in under 120 characters")
    _seen, why = await agent_state()
    return ActionResult(why is None, why or f"would search YouTube Music for {query!r}")


async def play_music(query: str = "") -> ActionResult:
    if not (query or "").strip() or len(query) > 120:
        return ActionResult(False, "say what to play, in under 120 characters")
    return await _dispatch("music_search", {"query": query.strip()})


async def undo_music(token: str) -> ActionResult:
    return await _dispatch("music_control", {"command": "pause"})


async def music_control_dry(command: str = "") -> ActionResult:
    if command not in MUSIC_COMMANDS:
        return ActionResult(False, f"one of {', '.join(MUSIC_COMMANDS)}")
    _seen, why = await agent_state()
    return ActionResult(why is None, why or f"would press {command} on YouTube Music")


async def music_control(command: str = "") -> ActionResult:
    if command not in MUSIC_COMMANDS:
        return ActionResult(False, f"one of {', '.join(MUSIC_COMMANDS)}")
    return await _dispatch("music_control", {"command": command})


async def undo_music_control(token: str) -> ActionResult:
    try:
        cmd = json.loads(token).get("command", "")
    except ValueError:
        cmd = ""
    back = _INVERSE.get(cmd)
    if back is None:
        return ActionResult(False, "unreadable rollback token")
    return await _dispatch("music_control", {"command": back})


_DUE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?"
                  r"(?:Z|[+-]\d{2}:?\d{2})?)?$")


def local_due(due: str) -> str:
    """The wall-clock time he said, as 'YYYY-MM-DD[ HH:MM]'. A model wrote
    '2026-09-29T17:00:00+00:00' for "tomorrow at 5pm": the offset is its
    guess, the 17:00 is his words, so the offset is dropped, never applied."""
    m = _DUE.match((due or "").strip())
    if not m:
        return due
    day, hh, mm = m.groups()
    return f"{day} {hh}:{mm}" if hh else day


def _reminder_problem(title: str, due: str) -> str | None:
    if not (title or "").strip() or len(title) > 200:
        return "a reminder needs a title under 200 characters"
    if due and not re.match(r"^\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2})?$", due):
        return "due must be YYYY-MM-DD or YYYY-MM-DD HH:MM (his local time)"
    return None


async def add_reminder_dry(title: str = "", due: str = "") -> ActionResult:
    due = local_due(due)
    if (why := _reminder_problem(title, due)) is not None:
        return ActionResult(False, why)
    _seen, why = await agent_state()
    when = f", due {due}" if due else ""
    return ActionResult(why is None, why or f"would add the reminder {title!r}{when}")


async def add_reminder(title: str = "", due: str = "") -> ActionResult:
    due = local_due(due)
    if (why := _reminder_problem(title, due)) is not None:
        return ActionResult(False, why)
    return await _dispatch("add_reminder", {"title": title.strip(), "due": due})


async def undo_reminder(token: str) -> ActionResult:
    """Marks it completed. A reminder is never deleted (deletion is denied)."""
    return await _dispatch("complete_reminder", {"id": token})


# ---- iMessage (Tier 2, no undo)

async def send_imessage_dry(to: str = "", text: str = "") -> ActionResult:
    """Resolves the recipient on the Mac: a read of Contacts, never a send."""
    if (why := imessage_problem(to, text)) is not None:
        return ActionResult(False, why)
    _seen, why = await agent_state()
    if why:
        return ActionResult(False, why)
    found = await _dispatch("resolve_recipient", {"to": to})
    if not found.ok:
        return found
    return ActionResult(True, f"would send {len(text)} characters to {found.detail}",
                        evidence=found.evidence)


async def send_imessage(to: str = "", text: str = "") -> ActionResult:
    if (why := imessage_problem(to, text)) is not None:
        return ActionResult(False, why)
    # The Mac resolves a name again and refuses on any ambiguity.
    return await _dispatch("send_imessage", {"to": to, "text": text})


# ---- URL (Tier 1: a page in his browser; the undo is recorded, not done)

async def open_url_dry(url: str = "") -> ActionResult:
    if (why := url_problem(url)) is not None:
        return ActionResult(False, why)
    _seen, why = await agent_state()
    host = url_host(url) or "?"
    whose = "one of your sites" if is_own_site(host) else "not one of your sites"
    return ActionResult(why is None,
                        why or f"would open {host} ({whose}) in your browser")


async def open_url(url: str = "") -> ActionResult:
    if (why := url_problem(url)) is not None:
        return ActionResult(False, why)
    return await _dispatch("open_url", {"url": url})


async def undo_open_url(token: str) -> ActionResult:
    """Recorded, not performed. A page that loaded has loaded: whatever the
    GET did on that site is not taken back by closing a tab, and ATLAS does
    not close his tabs. Said, so an undo never reads as more than it is."""
    return ActionResult(True, "nothing undone: a page that opened can't be "
                              "un-opened; close the tab if you don't want it")


# ---- web search (Tier 1: an address built here, opened like any other)

SEARCH_ENGINES = {
    "google": "https://www.google.com/search?q=",
    "duckduckgo": "https://duckduckgo.com/?q=",
    "bing": "https://www.bing.com/search?q=",
    "youtube": "https://www.youtube.com/results?search_query=",
}
QUERY_MAX = 300


def search_problem(query: Any, engine: Any) -> str | None:
    if engine not in SEARCH_ENGINES:
        return f"the engine is one of {', '.join(SEARCH_ENGINES)}"
    if not isinstance(query, str) or not query.strip() or len(query) > QUERY_MAX:
        return f"say what to search for, in under {QUERY_MAX} characters"
    if HIDDEN.search(query) or any(ord(c) < 0x20 or ord(c) == 0x7f for c in query):
        return "the search has hidden control characters in it"
    return None


def search_url(query: str, engine: str = "google") -> str:
    """The address, every character of the query percent-encoded: the words
    are only ever the value of `q`, never a path, a host or another key."""
    return SEARCH_ENGINES[engine] + quote_plus(" ".join(query.split()), safe="")


async def web_search_dry(query: str = "", engine: str = "google") -> ActionResult:
    if (why := search_problem(query, engine)) is not None:
        return ActionResult(False, why)
    if (why := url_problem(search_url(query, engine))) is not None:
        return ActionResult(False, why)
    _seen, why = await agent_state()
    return ActionResult(why is None,
                        why or f"would search {engine} for {query.strip()!r}")


async def web_search(query: str = "", engine: str = "google") -> ActionResult:
    """Through the agent's existing `open_url` operation, so it works on an
    agent installed before this action existed."""
    if (why := search_problem(query, engine)) is not None:
        return ActionResult(False, why)
    url = search_url(query, engine)
    if (why := url_problem(url)) is not None:
        return ActionResult(False, why)
    done = await _dispatch("open_url", {"url": url})
    if done.ok:
        return ActionResult(True, f"searched {engine} for {query.strip()!r}",
                            evidence=done.evidence,
                            rollback_token=json.dumps({"url": url}))
    return done


# ---- Notes (Tier 1: a new note; never edited, never deleted)

def note_problem(title: Any, body: Any) -> str | None:
    if not isinstance(title, str) or len(title) > NOTE_TITLE_MAX:
        return f"a note's title is under {NOTE_TITLE_MAX} characters"
    if not isinstance(body, str) or len(body) > NOTE_TEXT_MAX:
        return f"a note's text is under {NOTE_TEXT_MAX} characters"
    if not title.strip() and not body.strip():
        return "say what the note is called or what it says"
    if "\n" in title or HIDDEN.search(title) or HIDDEN.search(body):
        return "the note has hidden control characters in it"
    return None


async def new_note_dry(title: str = "", body: str = "") -> ActionResult:
    if (why := note_problem(title, body)) is not None:
        return ActionResult(False, why)
    _seen, why = await agent_state()
    return ActionResult(why is None,
                        why or f"would create a note in Notes titled {title.strip()!r}")


async def new_note(title: str = "", body: str = "") -> ActionResult:
    if (why := note_problem(title, body)) is not None:
        return ActionResult(False, why)
    return await _dispatch("new_note", {"title": title.strip(), "body": body})


async def undo_new_note(token: str) -> ActionResult:
    """Recorded, not performed. Deleting is Tier 3 (CLAUDE.md), moving a
    note to Recently Deleted is deleting it, and ATLAS never edits his notes:
    the note stays, and the undo says it is his to remove."""
    try:
        title = str(json.loads(token).get("title", ""))
    except (ValueError, AttributeError):
        title = ""
    named = f"the note {title!r}" if title else "the note"
    return ActionResult(True, f"nothing undone: ATLAS never deletes a note; "
                              f"{named} is in Notes for you to remove")


# ---- Shortcut (Tier 2)

async def run_shortcut_dry(name: str = "") -> ActionResult:
    """Asks the Mac whether the name is on ITS allowlist: yes or no."""
    if (why := shortcut_problem(name)) is not None:
        return ActionResult(False, why)
    _seen, why = await agent_state()
    if why:
        return ActionResult(False, why)
    return await _dispatch("shortcut_allowed", {"name": name})


async def run_shortcut(name: str = "") -> ActionResult:
    if (why := shortcut_problem(name)) is not None:
        return ActionResult(False, why)
    return await _dispatch("run_shortcut", {"name": name})


# ---- quit (Tier 1: the undo reopens what this quit)

async def quit_app_dry(app: str = "") -> ActionResult:
    if (why := _quit_problem(app)) is not None:
        return ActionResult(False, why)
    _seen, why = await agent_state()
    return ActionResult(why is None, why or (
        f"would quit {app} on your Mac (an app with unsaved work asks you first)"))


async def quit_app(app: str = "") -> ActionResult:
    if (why := _quit_problem(app)) is not None:
        return ActionResult(False, why)
    return await _dispatch("quit_app", {"app": app.strip()})


async def undo_quit_app(token: str) -> ActionResult:
    """Reopen the app only when the Mac says this quit is what closed it
    (`quit_by_us`). An older agent sends no token, and an app that was not
    open has nothing to reopen: either way nothing is opened."""
    try:
        t = json.loads(token)
    except (ValueError, TypeError):
        t = None
    if not isinstance(t, dict) or not t.get("quit_by_us"):
        return ActionResult(True, "nothing to reopen: this quit closed nothing "
                                  "ATLAS can name")
    app = str(t.get("app", ""))
    if (why := _app_problem(app)) is not None:
        return ActionResult(False, why)
    return await _dispatch("open_app", {"app": app})


# ---- volume (Tier 1: the dry run reads the level, the rollback restores it)

_VOLUME = "the volume is a whole number from 0 to 100"


async def set_volume_dry(level: Any = None) -> ActionResult:
    n = volume_level(level)
    if n is None:
        return ActionResult(False, _VOLUME)
    _seen, why = await agent_state()
    if why:
        return ActionResult(False, why)
    now = await _dispatch("get_volume", {})
    if not now.ok:
        return now
    return ActionResult(True, f"{now.detail}; would set it to {n}")


async def set_volume(level: Any = None) -> ActionResult:
    n = volume_level(level)
    if n is None:
        return ActionResult(False, _VOLUME)
    return await _dispatch("set_volume", {"level": n})


async def undo_volume(token: str) -> ActionResult:
    try:
        previous = volume_level(json.loads(token).get("previous"))
    except (ValueError, AttributeError, TypeError):
        previous = None
    if previous is None:
        return ActionResult(False, "unreadable rollback token")
    return await _dispatch("set_volume", {"level": previous})


# ---- notification (Tier 1: nothing leaves the Mac)

async def notify_dry(title: str = "", body: str = "") -> ActionResult:
    if (why := notify_problem(title, body)) is not None:
        return ActionResult(False, why)
    _seen, why = await agent_state()
    return ActionResult(why is None, why or f"would show {title!r}: {body!r}")


async def notify(title: str = "", body: str = "") -> ActionResult:
    if (why := notify_problem(title, body)) is not None:
        return ActionResult(False, why)
    return await _dispatch("notify", {"title": title, "body": body})


async def undo_notify(token: str) -> ActionResult:
    """Recorded, not performed: a notification on his own screen reached no
    one else and clears itself; there is nothing to put back."""
    return ActionResult(True, "nothing to undo: the notification stayed on your "
                              "Mac and clears itself")


# ------------------------------------------------------------------ check

async def check_agent() -> Observation:
    seen, why = await agent_state()
    if seen is None:
        return Observation("agent", Health.UNKNOWN, detail=why or "never connected")
    age = int((datetime.now(UTC) - seen).total_seconds())
    if why:
        # An asleep laptop is not an outage: said as what it is, and not OK.
        return Observation("agent", Health.UNKNOWN, float(age), "s", why)
    return Observation("agent", Health.OK, float(age), "s",
                       f"the Mac agent is connected (last check-in {age}s ago)")


class MacModule:
    name = "mac"
    title = "Mac"
    requires_env: tuple[str, ...] = ("MAC_AGENT_TOKEN",)

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (Check("agent", check_agent, timedelta(minutes=5), Severity.INFO,
                      "the agent on Miro's Mac is connected", baseline_sigma=None),)

    def actions(self):
        common = {"tier": Tier.AUTO, "principal_only": True, "max_per_hour": 60}
        return (
            Action(name="mac.open_app", summary="Open an app on Miro's Mac",
                   run=open_app, dry_run=open_app_dry, rollback=undo_open_app,
                   params={"app": "the app's name, e.g. Notes or YouTube Music"},
                   **common),
            Action(name="mac.play_music",
                   summary="Search YouTube Music on Miro's Mac for something to play",
                   run=play_music, dry_run=play_music_dry, rollback=undo_music,
                   params={"query": "what to play: an artist, song, mood or playlist"},
                   **common),
            Action(name="mac.music_control",
                   summary="Play, pause, next or previous on YouTube Music",
                   run=music_control, dry_run=music_control_dry,
                   rollback=undo_music_control,
                   params={"command": "play, pause, next or previous"}, **common),
            Action(name="mac.add_reminder",
                   summary="Add a reminder to Reminders on Miro's Mac",
                   run=add_reminder, dry_run=add_reminder_dry, rollback=undo_reminder,
                   params={"title": "what to be reminded of",
                           "due": "optional, YYYY-MM-DD or YYYY-MM-DD HH:MM local"},
                   **common),
            Action(name="mac.set_volume", summary="Set the volume on Miro's Mac",
                   run=set_volume, dry_run=set_volume_dry, rollback=undo_volume,
                   params={"level": "0 to 100"}, **common),
            Action(name="mac.notify", summary="Show a notification on Miro's Mac",
                   run=notify, dry_run=notify_dry, rollback=undo_notify,
                   params={"title": "a short title",
                           "body": "the notification's text, may be empty"},
                   **common),
            Action(name="mac.open_url",
                   summary="Open a web address in the browser on Miro's Mac",
                   run=open_url, dry_run=open_url_dry, rollback=undo_open_url,
                   params={"url": "a full http:// or https:// address"},
                   vet=vet_url, **common),
            Action(name="mac.quit_app", summary="Quit an app on Miro's Mac",
                   run=quit_app, dry_run=quit_app_dry, rollback=undo_quit_app,
                   params={"app": "the app's name, e.g. Notes"},
                   vet=vet_quit, **common),
            Action(name="mac.web_search",
                   summary="Search the web in the browser on Miro's Mac",
                   run=web_search, dry_run=web_search_dry, rollback=undo_open_url,
                   params={"query": "what to search for",
                           "engine": ("optional: google (the default), duckduckgo, "
                                      "bing or youtube")},
                   **common),
            Action(name="mac.new_note",
                   summary="Create a new note in Notes on Miro's Mac",
                   run=new_note, dry_run=new_note_dry, rollback=undo_new_note,
                   params={"title": "the note's title",
                           "body": "optional, the note's text under the title"},
                   **common),
            *self._cards(),
        )

    def _cards(self) -> tuple[Action, ...]:
        """Tier 2: each one a card carrying exactly what will happen."""
        card = {"tier": Tier.ASK, "principal_only": True, "irreversible": True,
                "max_per_hour": 20, "verbose_card": True,
                # A Slack button is answered at once; the Mac round trips
                # (resolve, then send; a Shortcut up to 25 s) run after it,
                # with the receipt in the card's thread.
                "runs_in_background": True}
        return (
            Action(name="mac.send_imessage",
                   summary="Send an iMessage from Miro's Mac",
                   run=send_imessage, dry_run=send_imessage_dry,
                   params={"to": ("a phone number in +E.164 form, an email "
                                  "address, or a contact's name exactly as in "
                                  "Contacts"),
                           "text": f"the message, up to {TEXT_MAX} characters"},
                   vet=vet_imessage, card_detail=card_imessage, card_always=True,
                   **card),
            Action(name="mac.run_shortcut",
                   summary="Run one of Miro's macOS Shortcuts by its exact name",
                   run=run_shortcut, dry_run=run_shortcut_dry,
                   params={"name": "the Shortcut's exact name"},
                   vet=vet_shortcut, card_detail=card_shortcut, **card),
        )

    async def signals(self):
        return ()


MODULE = MacModule()
