#!/usr/bin/env python3
"""The agent on Miro's Mac: ATLAS asks, this does, within a second.

Runs as Miro under launchd (com.atlas.mac-agent), holding a long-poll open
to local ATLAS (http://127.0.0.1:8080)/mac/next. It runs ONLY the operations below, each
with its parameters re-checked here, whatever the server sent, and it never
starts a shell: every command is an argument list. Standard library only.

The bearer token is read from ~/.config/atlas/mac-agent-token (0600) and
handed to curl on stdin, so it never appears in a process list or a log.

AppleScript never has a value spliced into its source. Every script is a
fixed `on run argv` handler and every value arrives as an argument after
`--`, so a title of `-e 'do shell script ...'` is a title, not an option,
and a quote in a message is a quote, not the end of a string.

There is no operation that types into whatever app is in front (it could
be Terminal) and none that touches the camera. Notes gets one thing: a new
note, its HTML escaped here, never an edit and never a delete.

Install or update this copy with bootstrap/mac/install-agent.sh. An older
copy answers "unknown operation" for anything added since, and runs nothing.

Shortcuts: only a name listed, exactly, one per line, in
~/.config/atlas/shortcuts-allow.txt runs (blank lines and `#` lines are
skipped; no file means none). The file is read on every request and never
sent anywhere; the server only ever learns yes or no for the name it asked.

Runs under /usr/bin/python3 (3.9): no syntax newer than that.
"""
from __future__ import annotations

import html
import json
import os
import pathlib
import re
import socket
import subprocess
import sys
import time
import urllib.parse

# Local ATLAS (docker-compose.local.yml api); atlas.storefront.test is deleted.
BASE = os.environ.get("ATLAS_BASE", "http://127.0.0.1:8080")
CONFIG = pathlib.Path.home() / ".config/atlas"
TOKEN_FILE = CONFIG / "mac-agent-token"
SHORTCUTS_ALLOW = CONFIG / "shortcuts-allow.txt"
HOST = socket.gethostname().split(".")[0][:60]
LOG = pathlib.Path.home() / "Library/Logs/atlas-mac-agent.log"


def token() -> str:
    """Read when a request is made, not at import."""
    return TOKEN_FILE.read_text().strip()


APP_DIRS = [pathlib.Path("/Applications"), pathlib.Path.home() / "Applications",
            pathlib.Path.home() / "Applications/Chrome Apps.localized",
            pathlib.Path("/System/Applications"),
            pathlib.Path("/System/Applications/Utilities")]
DENIED = {a.lower() for a in (
    "Terminal", "iTerm", "iTerm2", "Script Editor", "Automator", "Keychain Access",
    "System Settings", "System Preferences", "Disk Utility", "Installer",
    "Activity Monitor", "Console", "Migration Assistant", "Boot Camp Assistant",
    "Photo Booth", "FaceTime")}
APP_NAME = re.compile(r"^[\w][\w .&'+-]{0,60}$")
MUSIC_APP = "YouTube Music"
MUSIC_KEYS = {"play": ("space", False), "pause": ("space", False),
              "next": ("n", True), "previous": ("p", True)}
# Never quit, whatever the name: the desktop, the login session, the menu bar.
QUIT_DENIED = {a.lower() for a in (
    "Finder", "loginwindow", "Dock", "SystemUIServer", "WindowServer",
    "ControlCenter", "Control Center", "NotificationCenter", "Spotlight",
    "System Events", "Siri", "SecurityAgent", "UserNotificationCenter")}

# The same rules the server applies (atlas/modules/mac), checked again here.
E164 = re.compile(r"^\+[1-9]\d{6,14}\Z")
EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}"
                   r"(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,24}\Z")
CONTACT = re.compile(r"^[^\W\d_][\w .'-]{0,59}\Z")
HIDDEN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u200e\u200f\u202a-\u202e\u2066-\u2069]")
TEXT_MAX, URL_MAX, SHORTCUT_MAX = 1000, 2048, 100
NOTE_TITLE_MAX, NOTE_BODY_MAX = 100, 500
NOTE_TEXT_MAX = 2000                   # a note's text (new_note)


def log(msg: str) -> None:
    with open(LOG, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def curl(method: str, path: str, body: dict | None = None, timeout: int = 35):
    config = (f'header = "Authorization: Bearer {token()}"\n'
              f'header = "X-Mac-Agent: {HOST}"\n'
              'header = "Content-Type: application/json"\n')
    args = ["curl", "-sS", "-m", str(timeout), "-K", "-", "-X", method,
            "-w", "\n%{http_code}", BASE + path]
    if body is not None:
        args[args.index("-w"):args.index("-w")] = ["--data-binary", json.dumps(body)]
    out = subprocess.run(args, input=config, capture_output=True, text=True, check=False)
    if out.returncode:
        return 0, out.stderr.strip()[:200]
    text, _, code = out.stdout.rpartition("\n")
    return int(code or 0), text


def osa(*lines: str, args: tuple = ()) -> subprocess.CompletedProcess:
    """Run fixed AppleScript. `lines` are constants in this file; every value
    goes in `args`, after `--`, and reaches the script as `item n of argv`."""
    cmd = ["osascript"]
    for line in lines:
        cmd += ["-e", line]
    cmd += ["--", *[str(a) for a in args]]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=20, check=False)


def find_app(name: str) -> pathlib.Path | None:
    if not APP_NAME.match(name or "") or name.strip().lower() in DENIED:
        return None
    want = name.strip().lower().removesuffix(".app") + ".app"
    for d in APP_DIRS:
        try:
            for p in d.iterdir():
                if p.name.lower() == want and p.suffix == ".app":
                    return p
        except OSError:
            continue
    return None


def running(name: str) -> bool:
    r = osa("on run argv", "return application (item 1 of argv) is running",
            "end run", args=(name,))
    return r.stdout.strip() == "true"


# ------------------------------------------------------------------ operations

def op_open_app(p: dict) -> tuple[bool, str, str | None]:
    path = find_app(str(p.get("app", "")))
    if path is None:
        return False, f"no app called {p.get('app')!r} that ATLAS may open", None
    name = path.stem
    was = running(name)
    subprocess.run(["open", "-a", str(path)], capture_output=True, timeout=20, check=False)
    for _ in range(20):
        if running(name):
            return True, (f"{name} is open" + (" (it already was)" if was else "")), \
                json.dumps({"app": name, "opened_by_us": not was})
        time.sleep(0.25)
    return False, f"{name} did not start", None


def op_quit_app(p: dict) -> tuple[bool, str, str | None]:
    """Also the rollback of an open_app that opened it."""
    asked = str(p.get("app", ""))
    if asked.strip().lower() in QUIT_DENIED:
        return False, f"{asked} is part of macOS; ATLAS never quits it", None
    path = find_app(asked)
    if path is None or path.stem.lower() in QUIT_DENIED:
        return False, f"no app called {asked!r} that ATLAS may quit", None
    name = path.stem
    if not running(name):
        return True, f"{name} wasn't open", None
    osa("on run argv", "tell application (item 1 of argv) to quit", "end run",
        args=(name,))
    for _ in range(20):
        if not running(name):
            # The token says this quit closed it, so the undo may reopen it.
            return True, f"{name} quit", json.dumps({"app": name, "quit_by_us": True})
        time.sleep(0.25)
    return False, (f"asked {name} to quit; it is still open (it may be asking "
                   f"you to save something)"), None


def op_music_search(p: dict) -> tuple[bool, str, str | None]:
    query = str(p.get("query", "")).strip()
    if not query or len(query) > 120:
        return False, "nothing to search for", None
    path = find_app(MUSIC_APP)
    if path is None:
        return False, "YouTube Music isn't installed as an app on this Mac", None
    url = "https://music.youtube.com/search?q=" + urllib.parse.quote(query)
    subprocess.run(["open", "-a", str(path), url], capture_output=True, timeout=20, check=False)
    return True, (f"YouTube Music is showing results for {query!r}; press play on "
                  f"the one you want"), json.dumps({"query": query})


def op_music_control(p: dict) -> tuple[bool, str, str | None]:
    cmd = str(p.get("command", ""))
    if cmd not in MUSIC_KEYS or not running(MUSIC_APP):
        return False, ("YouTube Music isn't open" if cmd in MUSIC_KEYS
                       else f"unknown command {cmd!r}"), None
    key, shift = MUSIC_KEYS[cmd]
    osa(f'tell application "{MUSIC_APP}" to activate')
    time.sleep(0.4)
    stroke = ('key code 49' if key == "space" else f'keystroke "{key}"') + \
             (" using shift down" if shift else "")
    r = osa(f'tell application "System Events" to {stroke}')
    if r.returncode:
        return False, ("macOS needs you to allow the ATLAS agent under Privacy & "
                       "Security > Accessibility before it can press play/pause"), None
    return True, f"pressed {cmd} on YouTube Music", json.dumps({"command": cmd})


def op_add_reminder(p: dict) -> tuple[bool, str, str | None]:
    title = str(p.get("title", "")).strip()
    due = str(p.get("due", "")).strip()
    if not title or len(title) > 200:
        return False, "a reminder needs a title", None
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?$", due) if due else None
    if due and not m:
        return False, "due must be YYYY-MM-DD or YYYY-MM-DD HH:MM", None
    parts = []
    if m:
        y, mo, d, h, mi = m.groups()
        parts = [int(y), int(mo), int(d), int(h or 9), int(mi or 0)]
    script = ['on run argv',
              'set t to item 1 of argv',
              'tell application "Reminders"',
              'if (count of argv) > 1 then',
              'set d to current date',
              'set year of d to (item 2 of argv as integer)',
              'set month of d to (item 3 of argv as integer)',
              'set day of d to (item 4 of argv as integer)',
              'set hours of d to (item 5 of argv as integer)',
              'set minutes of d to (item 6 of argv as integer)',
              'set seconds of d to 0',
              'set r to make new reminder with properties {name:t, due date:d}',
              'else',
              'set r to make new reminder with properties {name:t}',
              'end if',
              'return id of r',
              'end tell',
              'end run']
    r = osa(*script, args=(title, *[str(x) for x in parts]))
    if r.returncode:
        return False, ("macOS needs you to allow the ATLAS agent to control "
                       "Reminders (a one-time prompt)"), None
    rid = r.stdout.strip()
    when = f", due {due}" if due else ""
    return True, f"reminder added: {title!r}{when}", rid


def op_complete_reminder(p: dict) -> tuple[bool, str, str | None]:
    rid = str(p.get("id", ""))
    if not re.match(r"^x-apple-reminder://[\w-]+$", rid):
        return False, "not a reminder id", None
    r = osa('on run argv', 'tell application "Reminders" to set completed of '
            '(reminder id (item 1 of argv)) to true', 'end run', args=(rid,))
    return r.returncode == 0, "reminder marked completed", None


# ---- Notes: a new note, never an edit, never a delete

NOTE_SCRIPT = (
    "on run argv",
    'tell application "Notes"',
    "set f to default folder of default account",
    "set n to make new note at f with properties {body:(item 1 of argv)}",
    "activate",
    "try",
    "show n",
    "end try",
    "return id of n",
    "end tell",
    "end run")


def note_refusal(title: object, body: object) -> str | None:
    """The server's rules (atlas/modules/mac note_problem), checked again."""
    if not isinstance(title, str) or len(title) > NOTE_TITLE_MAX:
        return f"a note's title must be text under {NOTE_TITLE_MAX} characters"
    if not isinstance(body, str) or len(body) > NOTE_TEXT_MAX:
        return f"a note's text must be under {NOTE_TEXT_MAX} characters"
    if not title.strip() and not body.strip():
        return "a note needs a title or some text"
    if "\n" in title or HIDDEN.search(title) or HIDDEN.search(body):
        return "the note has hidden control characters in it"
    return None


def note_html(title: str, body: str) -> str:
    """Notes stores a note as HTML and takes its first line as the title.
    Every character he said is escaped here, so `<b>` in a title is the
    three characters, not bold, and nothing in it can close a tag."""
    parts = []
    if title.strip():
        parts.append(f"<div><h1>{html.escape(title.strip())}</h1></div>")
    for line in (body.split("\n") if body.strip() else []):
        parts.append(f"<div>{html.escape(line) if line.strip() else '<br>'}</div>")
    return "".join(parts)


def op_new_note(p: dict) -> tuple[bool, str, str | None]:
    title, body = p.get("title", ""), p.get("body", "")
    why = note_refusal(title, body)
    if why is not None:
        return False, why, None
    title, body = str(title), str(body).strip("\n")
    r = osa(*NOTE_SCRIPT, args=(note_html(title, body),))
    if r.returncode:
        err = (r.stderr or "").strip()
        if "-1743" in err or "not authori" in err.lower():
            return False, ("macOS needs you to allow the ATLAS agent to control "
                           "Notes (System Settings > Privacy & Security > "
                           "Automation)"), None
        # Anything else is said as itself, never as a permission prompt.
        return False, "Notes refused the new note: " + (err[-160:] or "no reason given"), None
    named = repr(title.strip()) if title.strip() else "an untitled note"
    return True, f"created {named} in Notes", json.dumps(
        {"id": r.stdout.strip()[:200], "title": title.strip()})


# ---- iMessage: resolve (read-only) and send

CONTACT_SCRIPT = (
    "on run argv",
    "set n to item 1 of argv",
    'set out to ""',
    'tell application "Contacts"',
    "set ps to (every person whose name is n)",
    'set out to "count" & tab & ((count of ps) as text) & linefeed',
    "repeat with p in ps",
    "repeat with ph in (phones of p)",
    'set out to out & "phone" & tab & (value of ph) & linefeed',
    "end repeat",
    "repeat with em in (emails of p)",
    'set out to out & "email" & tab & (value of em) & linefeed',
    "end repeat",
    "end repeat",
    "end tell",
    "return out",
    "end run")


def resolve(to: str) -> tuple[str | None, str]:
    """(handle, label) or (None, why). A name must match exactly one person
    in Contacts, who has exactly one phone number -- or, with none, exactly
    one email address. Anything else is refused: never a guess."""
    if E164.match(to) or EMAIL.match(to):
        return to, to
    if not CONTACT.match(to) or to != to.strip():
        return None, "not a phone number, email address or contact name"
    r = osa(*CONTACT_SCRIPT, args=(to,))
    if r.returncode:
        return None, ("macOS needs you to allow the ATLAS agent to read "
                      "Contacts (a one-time prompt)")
    count, phones, emails = 0, [], []
    for line in r.stdout.splitlines():
        kind, _, value = line.partition("\t")
        value = value.strip()
        if kind == "count" and value.isdigit():
            count = int(value)
        elif kind == "phone" and value and not HIDDEN.search(value):
            phones.append(value)
        elif kind == "email" and value and not HIDDEN.search(value):
            emails.append(value)
    if count == 0:
        return None, f"no one called {to!r} in your Contacts"
    if count > 1:
        return None, (f"{count} people are called {to!r} in your Contacts; "
                      f"say the number instead")
    if len(phones) == 1:
        return phones[0], f"{to} ({phones[0]})"
    if not phones and len(emails) == 1:
        return emails[0], f"{to} ({emails[0]})"
    return None, (f"{to} has {len(phones)} phone number(s) and {len(emails)} email "
                  f"address(es) in Contacts; say which one")


def op_resolve_recipient(p: dict) -> tuple[bool, str, str | None]:
    handle, label = resolve(str(p.get("to", "")))
    return handle is not None, label, None


def op_send_imessage(p: dict) -> tuple[bool, str, str | None]:
    to, text = str(p.get("to", "")), p.get("text")
    if (not isinstance(text, str) or not text.strip() or len(text) > TEXT_MAX
            or HIDDEN.search(text)):
        return False, "the message is empty, too long or has hidden characters", None
    handle, label = resolve(to)
    if handle is None:
        return False, label, None
    r = osa("on run argv",
            'tell application "Messages"',
            "set s to 1st account whose service type = iMessage",
            "send (item 2 of argv) to participant (item 1 of argv) of s",
            "end tell",
            "end run", args=(handle, text))
    if r.returncode:
        return False, ("Messages did not send it (is Messages signed in, and the "
                       "ATLAS agent allowed to control it?)"), None
    return True, f"sent to {label}", None


# ---- URL

def url_ok(url: object) -> bool:
    if not isinstance(url, str) or not url or len(url) > URL_MAX:
        return False
    if any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7f for c in url):
        return False
    if HIDDEN.search(url) or any(c in url for c in '\\<>"'):
        return False
    try:
        parts = urllib.parse.urlsplit(url)
        _ = parts.port
    except ValueError:
        return False
    return (parts.scheme in ("http", "https") and "@" not in parts.netloc
            and url.startswith(parts.scheme + "://") and bool(parts.hostname))


def op_open_url(p: dict) -> tuple[bool, str, str | None]:
    url = p.get("url")
    if not url_ok(url):
        return False, "not a plain http(s) address", None
    r = subprocess.run(["open", str(url)], capture_output=True, timeout=20, check=False)
    host = urllib.parse.urlsplit(str(url)).hostname
    return r.returncode == 0, (f"opened {host} in your browser" if r.returncode == 0
                               else f"macOS could not open {host}"), None


# ---- Shortcuts: only names on the local allowlist

def shortcut_name_ok(name: object) -> bool:
    return (isinstance(name, str) and 0 < len(name) <= SHORTCUT_MAX
            and name == name.strip() and not name.startswith("-")
            and not any(ord(c) < 0x20 or ord(c) == 0x7f for c in name)
            and not HIDDEN.search(name))


def allowed_shortcuts() -> set:
    """Read now, every time. No file, or an unreadable one, allows nothing."""
    try:
        lines = SHORTCUTS_ALLOW.read_text().splitlines()
    except OSError:
        return set()
    return {ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")}


def _shortcut_refusal(name: object) -> str | None:
    if not shortcut_name_ok(name):
        return "not a Shortcut name ATLAS may pass on"
    if name not in allowed_shortcuts():
        return (f"{name!r} is not in ~/.config/atlas/shortcuts-allow.txt on the "
                f"Mac, so it does not run")
    r = subprocess.run(["shortcuts", "list"], capture_output=True, text=True,
                       timeout=20, check=False)
    if r.returncode or name not in r.stdout.splitlines():
        return f"there is no Shortcut called {name!r} on this Mac"
    return None


def op_shortcut_allowed(p: dict) -> tuple[bool, str, str | None]:
    name = p.get("name")
    why = _shortcut_refusal(name)
    return why is None, why or f"would run the Shortcut {name!r}", None


def op_run_shortcut(p: dict) -> tuple[bool, str, str | None]:
    name = p.get("name")
    why = _shortcut_refusal(name)
    if why is not None:
        return False, why, None
    try:
        r = subprocess.run(["shortcuts", "run", "--", str(name)], capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=25, check=False)
    except subprocess.TimeoutExpired:
        return False, (f"the Shortcut {name!r} was still running after 25 s; "
                       f"whether it finished is unknown"), None
    return r.returncode == 0, (f"ran the Shortcut {name!r}" if r.returncode == 0
                               else f"the Shortcut {name!r} failed"), None


# ---- volume and notifications

def level_ok(level: object) -> int | None:
    if isinstance(level, bool) or not isinstance(level, int):
        return None
    return level if 0 <= level <= 100 else None


def current_volume() -> int | None:
    r = osa("return output volume of (get volume settings)")
    out = r.stdout.strip()
    return int(out) if r.returncode == 0 and out.isdigit() else None


def op_get_volume(p: dict) -> tuple[bool, str, str | None]:
    now = current_volume()
    if now is None:
        return False, "the volume could not be read", None
    return True, f"the volume is {now}", json.dumps({"level": now})


def op_set_volume(p: dict) -> tuple[bool, str, str | None]:
    n = level_ok(p.get("level"))
    if n is None:
        return False, "the volume is a whole number from 0 to 100", None
    before = current_volume()
    if before is None:
        return False, "the volume could not be read, so nothing was changed", None
    r = osa("on run argv", "set volume output volume ((item 1 of argv) as integer)",
            "end run", args=(n,))
    if r.returncode:
        return False, "the volume could not be set", None
    return True, f"volume set to {n} (it was {before})", json.dumps({"previous": before})


def op_notify(p: dict) -> tuple[bool, str, str | None]:
    title, body = p.get("title"), p.get("body", "")
    if (not isinstance(title, str) or not title.strip() or len(title) > NOTE_TITLE_MAX
            or not isinstance(body, str) or len(body) > NOTE_BODY_MAX
            or HIDDEN.search(title) or HIDDEN.search(body)):
        return False, "not a notification ATLAS may show", None
    r = osa("on run argv",
            "display notification (item 2 of argv) with title (item 1 of argv)",
            "end run", args=(title, body))
    return r.returncode == 0, ("notification shown" if r.returncode == 0
                               else "the notification could not be shown"), None


# ------------------------------------------------------------------ the vault

VAULT = pathlib.Path(os.environ.get("ATLAS_VAULT", str(pathlib.Path.home()
                                                       / "Documents/ATLAS Vault")))
SYNC_EVERY_S = 600
_last_sync = 0.0


def sync_vault() -> None:
    """Write ATLAS's export under VAULT/ATLAS/. Only there, only .md files,
    never a delete: his own notes elsewhere in the vault are never touched."""
    global _last_sync
    if time.monotonic() - _last_sync < SYNC_EVERY_S or not VAULT.is_dir():
        return
    _last_sync = time.monotonic()
    code, text = curl("GET", "/mac/vault", timeout=60)
    if code != 200:
        log(f"vault: HTTP {code}")
        return
    base = (VAULT / "ATLAS").resolve()
    written = 0
    for doc in json.loads(text).get("docs", []):
        rel = str(doc.get("path", ""))
        if not rel.startswith("ATLAS/") or not rel.endswith(".md") or ".." in rel:
            continue
        target = (VAULT / rel).resolve()
        if base not in target.parents and target != base:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        body = str(doc.get("content", ""))
        if not target.exists() or target.read_text() != body:
            target.write_text(body)
            written += 1
    log(f"vault: {written} note(s) updated")


# The whole of what this agent does. Anything else is refused by name.
OPS = {"open_app": op_open_app, "quit_app": op_quit_app,
       "music_search": op_music_search, "music_control": op_music_control,
       "add_reminder": op_add_reminder, "complete_reminder": op_complete_reminder,
       "resolve_recipient": op_resolve_recipient, "send_imessage": op_send_imessage,
       "open_url": op_open_url,
       "shortcut_allowed": op_shortcut_allowed, "run_shortcut": op_run_shortcut,
       "get_volume": op_get_volume, "set_volume": op_set_volume,
       "notify": op_notify, "new_note": op_new_note}


def main() -> None:
    log(f"agent up as {HOST}")
    while True:
        try:
            sync_vault()
        except Exception as exc:  # noqa: BLE001
            log(f"vault: failed ({type(exc).__name__})")
        code, text = curl("GET", "/mac/next")
        if code == 204:
            continue
        if code != 200:
            log(f"poll: HTTP {code} {text[:120]}")
            time.sleep(10)
            continue
        try:
            job = json.loads(text)
        except ValueError:
            job = None
        if not isinstance(job, dict) or not isinstance(job.get("id"), int):
            log("poll: the reply was not a job; waiting")
            time.sleep(10)
            continue
        try:
            fn = OPS.get(job.get("op"))
            if fn is None:
                ok, detail, token = False, f"unknown operation {job.get('op')!r}", None
            else:
                ok, detail, token = fn(job.get("params") or {})
        except Exception as exc:  # noqa: BLE001
            ok, detail, token = False, f"failed: {type(exc).__name__}", None
        log(f"job {job.get('id')} {job.get('op')}: {'ok' if ok else 'no'} - {detail}")
        curl("POST", f"/mac/jobs/{int(job['id'])}/result",
             {"ok": ok, "detail": detail, "token": token}, timeout=15)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
