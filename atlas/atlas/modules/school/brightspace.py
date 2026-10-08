"""school.brightspace — Brightspace (D2L) read through the doors it leaves open.

CUNY's Brightspace sits behind SSO with MFA and gives students no API key,
and ATLAS never logs in anywhere (the Phase 16 ruling). So it reads what
D2L hands every student without a login:

  * **The calendar subscription feed.** Calendar → Settings → Subscribe
    gives an `.ics` URL with its own token in it; it carries every course's
    due dates and events. That URL *is* a credential — anyone holding it
    reads his calendar — so it is never logged, and `scrub()` takes it out
    of every reason and detail, whole and in pieces (the token may sit in
    the path, where `_http.redact`'s query-parameter pattern cannot see it,
    and `BRIGHTSPACE_ICS_URL` is not a name `_http.redact` recognises as
    secret). Parsed by a small hand parser, not the `icalendar` package:
    VEVENT only, folded lines unfolded, `DTSTART;TZID=…`, `…Z`, floating
    times (read in `ATLAS_TZ`) and `VALUE=DATE`, and the `\\,` `\\;` `\\n`
    `\\\\` escapes.
  * **Notification emails.** Notifications → Instant notifications, which
    land in the Gmail the `mail` module already reads. Searched with
    `from:(brightspace OR d2l OR cuny.edu) newer_than:Nd` through the mail
    module's own `_api`. Gmail's `from:` also matches the display name, so
    the From *address* is checked afterwards: its domain must be
    brightspace.com, d2l.com or cuny.edu (or a subdomain). Anything else is
    kept only as an `alert` marked `sender_unverified` — never an
    assignment, never a signal. Subjects are kept (they are how an email is
    classified); bodies are wrapped as untrusted before they go anywhere.

The one guarantee this file cannot give on its own: httpx logs every request
URL at INFO. `_http.install_log_redaction()` (called by `boot()`) filters
every log record through `_http.redact`, which knows this URL
(`_http.SECRET_URL_ENV`), and raises the httpx loggers to WARNING.

And one door that is not open yet: the Valence API, behind the four
`BRIGHTSPACE_APP_ID/APP_KEY/USER_ID/USER_KEY` variables, used only if CUNY
ever grants them. Assumed, so Miro can correct it: ID-key auth (`x_a` app
id, `x_b` user id, `x_c`/`x_d` HMAC-SHA256 signatures of
`METHOD&/path&timestamp` with the app and user keys, base64url without
padding, `x_t` the timestamp) against
`GET {BRIGHTSPACE_HOST}/d2l/api/le/1.67/calendar/events/myEvents/`
(`startDateTime`/`endDateTime`), answering an `ObjectListPage` whose
`Objects` carry `CalendarEventId`, `Title`, `Description`,
`StartDateTime`, `EndDateTime`, `OrgUnitCode` and `OrgUnitName`.

Nothing here writes to Brightspace, and nothing could: there is no login.
"""
from __future__ import annotations

import base64
import email.utils
import hashlib
import hmac
import math
import os
import re
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from ...kernel.permissions import wrap_untrusted
from .. import _http

ICS_ENV = "BRIGHTSPACE_ICS_URL"
VALENCE_ENV = ("BRIGHTSPACE_APP_ID", "BRIGHTSPACE_APP_KEY",
               "BRIGHTSPACE_USER_ID", "BRIGHTSPACE_USER_KEY")
HOST_ENV = "BRIGHTSPACE_HOST"
DEFAULT_HOST = "https://brightspace.cuny.edu"
LE_VERSION = "1.67"
EMAIL_QUERY = "from:(brightspace OR d2l OR cuny.edu)"
SENDER_DOMAINS = ("brightspace.com", "d2l.com", "cuny.edu")
EMAIL_PAGE = 50
EMAIL_MAX_PAGES = 2
NOT_GRANTED = ("Valence not granted — using the calendar feed and "
               "notification emails")
FEED_LABEL = "<brightspace-feed-url>"


def _now() -> datetime:
    return datetime.now(UTC)


def local_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("ATLAS_TZ") or "America/New_York")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


# ----------------------------------------------------------------- secrecy

_SIGNATURE = re.compile(r"([?&]x_[a-d]=)[^&\s\"']+", re.IGNORECASE)


def scrub(text: object) -> str:
    """Take the calendar-feed URL out of `text`, whole and in pieces, then
    everything `_http.redact` already knows about."""
    out = str(text)
    url = os.getenv(ICS_ENV, "")
    if url:
        variants = {url, url.split("://", 1)[-1]}
        parts = urlsplit(url)
        if parts.path and parts.path != "/":
            variants.add(parts.path)
        if parts.query:
            variants.add(parts.query)
        variants.update(seg for seg in parts.path.split("/") if len(seg) >= 12)
        variants.update(v for _k, v in parse_qsl(parts.query) if len(v) >= 6)
        for value in sorted(variants, key=len, reverse=True):
            out = out.replace(value, FEED_LABEL)
    out = _SIGNATURE.sub(r"\1<redacted>", out)
    return _http.redact(out)


# ------------------------------------------------------------- course codes

_CODE = re.compile(r"\b([A-Z]{2,4})\s?-?\s?(\d{3})(?!\d)")


def course_code(*texts: str | None) -> str | None:
    """"PHY 120", "CSC221", "MAT301-0412" -> "PHY 120" / "CSC 221" / "MAT 301"."""
    for text in texts:
        if not text:
            continue
        m = _CODE.search(text)
        if m:
            return f"{m.group(1)} {m.group(2)}"
    return None


# -------------------------------------------------------------- classifying

_EXAM = re.compile(
    r"\b(?:exams?|midterms?|finals?)\b(?!\s+(?:project|paper|report|draft))",
    re.IGNORECASE)
_QUIZ = re.compile(r"\bquiz", re.IGNORECASE)
_LAB = re.compile(r"\blabs?\d*\b", re.IGNORECASE)
_ASSIGNMENT = re.compile(r"\b(?:due|assignments?|homework|hw\d*)\b", re.IGNORECASE)


def _classify_one(text: str) -> str:
    if _EXAM.search(text):
        return "exam"
    if _QUIZ.search(text):
        return "quiz"
    if _LAB.search(text):
        return "lab"
    if _ASSIGNMENT.search(text):
        return "assignment"
    return "event"


def classify_event(summary: str, description: str) -> str:
    """assignment | quiz | exam | lab | event. The summary decides; the
    description is only consulted when the summary says nothing."""
    kind = _classify_one(summary or "")
    if kind == "event" and description:
        kind = _classify_one(description)
    return kind


# ---------------------------------------------------------------- ICS parse

def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        elif raw:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _split_property(line: str) -> tuple[str, dict[str, str], str] | None:
    quoted = False
    for i, ch in enumerate(line):
        if ch == '"':
            quoted = not quoted
        elif ch == ":" and not quoted:
            head, value = line[:i], line[i + 1:]
            break
    else:
        return None
    name, *raw_params = head.split(";")
    params: dict[str, str] = {}
    for p in raw_params:
        key, _, val = p.partition("=")
        params[key.strip().upper()] = val.strip().strip('"')
    return name.strip().upper(), params, value


# D2L has been seen emitting Windows zone names in TZID.
_WINDOWS_ZONES = {
    "eastern standard time": "America/New_York",
    "central standard time": "America/Chicago",
    "mountain standard time": "America/Denver",
    "pacific standard time": "America/Los_Angeles",
    "utc": "UTC",
}


def _zone(tzid: str | None) -> ZoneInfo | None:
    if not tzid:
        return None
    for name in (tzid, _WINDOWS_ZONES.get(tzid.lower(), "")):
        if not name:
            continue
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return None


def _parse_dt(value: str, params: dict[str, str]) -> tuple[datetime | None, bool]:
    """(UTC datetime, all_day). A date-only value is the start of that day
    in ATLAS_TZ — the feed did not give a time, so none is invented."""
    v = value.strip()
    try:
        if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"\d{8}", v):
            day = datetime.strptime(v[:8], "%Y%m%d").replace(tzinfo=local_tz())
            return day.astimezone(UTC), True
        if v.endswith("Z"):
            return datetime.strptime(v[:15], "%Y%m%dT%H%M%S").replace(tzinfo=UTC), False
        tz = _zone(params.get("TZID")) or local_tz()
        wall = datetime.strptime(v[:15], "%Y%m%dT%H%M%S").replace(tzinfo=tz)
        return wall.astimezone(UTC), False
    except ValueError:
        return None, False


def parse_ics(text: str) -> list[dict]:
    """Every VEVENT as a dict: uid, summary, dtstart, dtend, description,
    location, url, categories, plus course_code, kind and all_day."""
    events: list[dict] = []
    current: dict | None = None
    nested = 0            # depth of VALARM (or any other block) inside a VEVENT
    for line in _unfold(text or ""):
        upper = line.strip().upper()
        if current is not None and nested == 0 and upper == "END:VEVENT":
            events.append(_finish(current))
            current = None
            continue
        if upper == "BEGIN:VEVENT" and current is None:
            current, nested = {}, 0
            continue
        if current is None:
            continue
        if upper.startswith("BEGIN:"):
            nested += 1
            continue
        if upper.startswith("END:"):
            nested = max(0, nested - 1)
            continue
        if nested:
            continue          # an alarm's SUMMARY is not the event's
        prop = _split_property(line)
        if prop is None:
            continue
        name, params, value = prop
        if name in ("DTSTART", "DTEND"):
            when, all_day = _parse_dt(value, params)
            current[name.lower()] = when
            if name == "DTSTART":
                current["all_day"] = all_day
        elif name in ("UID", "SUMMARY", "DESCRIPTION", "LOCATION", "URL",
                      "CATEGORIES"):
            current[name.lower()] = _unescape(value)
    return events


def _finish(raw: dict) -> dict:
    summary = raw.get("summary", "")
    description = raw.get("description", "")
    categories = raw.get("categories", "")
    uid = raw.get("uid") or f"{summary}|{raw.get('dtstart')}"
    return {
        "uid": uid,
        "summary": summary,
        "dtstart": raw.get("dtstart"),
        "dtend": raw.get("dtend"),
        "description": description,
        "location": raw.get("location", ""),
        "url": raw.get("url", ""),
        "categories": categories,
        "all_day": bool(raw.get("all_day", False)),
        "course_code": course_code(summary, categories),
        "kind": classify_event(summary, description),
        "external_id": f"ics:{uid}",
        "source": "ics",
    }


# ---------------------------------------------------------------- ICS fetch

async def _http_get(url: str) -> str:
    """Raw GET. Raises; `fetch_ics` turns that into a scrubbed reason. Not
    `_web.fetch_text`, which strips tags and collapses the line structure a
    calendar feed depends on."""
    async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "ATLAS/1.0"})
        response.raise_for_status()
        return response.text


async def fetch_ics() -> tuple[list[dict], str | None]:
    url = os.getenv(ICS_ENV)
    if not url:
        return [], (f"{ICS_ENV} not set — Brightspace → Calendar → Settings → "
                    "Subscribe gives the feed URL")
    try:
        text = await _http_get(url)
    except Exception as exc:                            # noqa: BLE001
        return [], scrub(f"calendar feed unreachable: {_http.explain(exc)}")
    if "BEGIN:VCALENDAR" not in (text or "")[:2000].upper():
        return [], scrub("calendar feed answered, but not with a calendar — "
                         "the subscription URL may have been reset")
    read_at = _now()
    events = parse_ics(text)
    for e in events:
        e["read_at"] = read_at
        e["due_at"] = e["dtstart"]
    return events, None


# ------------------------------------------------------------------- emails

_GRADE_SUBJECT = re.compile(r"^\s*grades?\s+released\s*:\s*(.+?)\s+in\s+(.+?)\s*$",
                            re.IGNORECASE)
_NEWS_SUBJECT = re.compile(r"^\s*news\s*:\s*(.+?)\s*$", re.IGNORECASE)
_NEW_ASSIGNMENT = re.compile(
    r"^\s*new\s+assignment\s*:?\s*(.*?)(?:\s+in\s+(.+?))?\s*$", re.IGNORECASE)
_DUE = re.compile(r"\bdue\b", re.IGNORECASE)
_GRADE_VALUE = re.compile(
    r"(?i:\bgrade)\s*:\s*(\d+(?:\.\d+)?\s*/\s*\d+(?:\.\d+)?|\d+(?:\.\d+)?\s*%|"
    r"[A-F][+-]?(?![A-Za-z]))")
_LINK = re.compile(r"https://[^\s<>\"']*(?:brightspace|d2l)[^\s<>\"']*", re.IGNORECASE)


def classify_subject(subject: str) -> tuple[str, str, str | None]:
    """(kind, title, course text) from a notification subject."""
    if m := _GRADE_SUBJECT.match(subject):
        return "grade", m.group(1), m.group(2)
    if m := _NEWS_SUBJECT.match(subject):
        return "news", m.group(1), None
    if m := _NEW_ASSIGNMENT.match(subject):
        return "assignment", m.group(1) or subject, m.group(2)
    if _DUE.search(subject):
        return "assignment", subject, None
    return "alert", subject, None


def sender_verified(sender: str) -> bool:
    """True when the From ADDRESS (not the display name) is at
    brightspace.com, d2l.com or cuny.edu, or a subdomain of one."""
    _name, address = email.utils.parseaddr(sender or "")
    domain = address.rpartition("@")[2].strip().lower().rstrip(".")
    if not domain or "@" not in address:
        return False
    return any(domain == d or domain.endswith("." + d) for d in SENDER_DOMAINS)


def email_item(mid: str, subject: str, sender: str, body: str,
               at: datetime) -> dict:
    kind, title, course_text = classify_subject(subject or "")
    verified = sender_verified(sender)
    if not verified:
        # A display name saying "Brightspace" proves nothing. Kept, so he can
        # see it arrived, but never an assignment, a grade or a signal.
        kind, title = "alert", subject or "(no subject)"
    code = course_code(course_text, subject)
    grade = None
    if verified and kind == "grade" and (g := _GRADE_VALUE.search(body or "")):
        grade = re.sub(r"\s+", "", g.group(1))
    link = _LINK.search(body or "")
    return {
        "external_id": f"email:{mid}",
        "kind": kind,
        "title": title.strip() or subject,
        "subject": subject,
        "sender": sender,
        "course_code": code,
        "course": code or (course_text or "unknown course"),
        "body": wrap_untrusted((body or "")[:4000], "brightspace-email"),
        "at": at,
        "due_at": None,
        "grade": grade,
        "url": link.group(0) if link and verified else None,
        "source": "email",
        "meta": {} if verified else {"sender_unverified": True},
    }


async def scan_emails(since_hours: int = 48, *,
                      info: dict | None = None) -> tuple[list[dict], str | None]:
    """Brightspace notification emails from the last `since_hours`, through
    the mail module's `_api` (the same GOOGLE_* token it already holds).

    `from:cuny.edu` matches all CUNY mail, so the listing is paged — at most
    `EMAIL_MAX_PAGES` of `EMAIL_PAGE`. If more remain, `info["truncated"]` is
    set (with `info["listed"]`), so a count can say it is a floor."""
    info = info if info is not None else {}
    info.update(truncated=False, listed=0)
    if not os.getenv("GOOGLE_REFRESH_TOKEN"):
        return [], ("Gmail not configured (GOOGLE_REFRESH_TOKEN unset) — "
                    "Brightspace notification emails cannot be read")
    from ..mail import _api, _body_text, _header

    days = max(1, math.ceil(since_hours / 24))
    cutoff = _now() - timedelta(hours=since_hours)
    read_at = _now()
    out: list[dict] = []
    try:
        refs: list[dict] = []
        token: str | None = None
        for _page in range(EMAIL_MAX_PAGES):
            params = {"q": f"{EMAIL_QUERY} newer_than:{days}d",
                      "maxResults": EMAIL_PAGE}
            if token:
                params["pageToken"] = token
            listing = await _api("/messages", **params)
            refs += listing.get("messages", []) or []
            token = listing.get("nextPageToken")
            if not token:
                break
        info.update(truncated=bool(token), listed=len(refs))
        for ref in refs:
            full = await _api(f"/messages/{ref['id']}", format="full")
            at = datetime.fromtimestamp(int(full.get("internalDate", 0)) / 1000, UTC)
            if at < cutoff:
                continue
            item = email_item(ref["id"], _header(full, "Subject"),
                              _header(full, "From"),
                              _body_text(full.get("payload", {})), at)
            item["read_at"] = read_at
            out.append(item)
    except Exception as exc:                            # noqa: BLE001
        return [], scrub(f"Gmail unreachable: {_http.explain(exc)}")
    return out, None


# ------------------------------------------------------------------ valence

def _sign(key: str, base: str) -> str:
    digest = hmac.new(key.encode(), base.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _valence_params(method: str, path: str) -> dict[str, str]:
    ts = str(int(time.time()))
    base = f"{method.upper()}&{path.lower()}&{ts}"
    return {
        "x_a": os.environ["BRIGHTSPACE_APP_ID"],
        "x_b": os.environ["BRIGHTSPACE_USER_ID"],
        "x_c": _sign(os.environ["BRIGHTSPACE_APP_KEY"], base),
        "x_d": _sign(os.environ["BRIGHTSPACE_USER_KEY"], base),
        "x_t": ts,
    }


def _iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def valence_upcoming(days: int = 14) -> tuple[list[dict], str | None]:
    if _http.env_missing(*VALENCE_ENV):
        return [], NOT_GRANTED
    host = (os.getenv(HOST_ENV) or DEFAULT_HOST).rstrip("/")
    path = f"/d2l/api/le/{LE_VERSION}/calendar/events/myEvents/"
    start = _now()
    params = {
        **_valence_params("GET", path),
        "startDateTime": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "endDateTime": (start + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }
    data, reason = await _http.get_json(f"{host}{path}", params=params)
    if reason is not None:
        return [], scrub(f"Valence: {reason}")
    objects, reason = _http.first_list(data, "Objects")
    if reason is not None:
        return [], scrub(f"Valence: {reason}")
    read_at = _now()
    out = []
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        title = str(obj.get("Title") or "")
        description = str(obj.get("Description") or "")
        org = f"{obj.get('OrgUnitCode') or ''} {obj.get('OrgUnitName') or ''}"
        eid = obj.get("CalendarEventId")
        start_at = _iso(obj.get("StartDateTime"))
        out.append({
            "uid": str(eid), "summary": title, "description": description,
            "dtstart": start_at, "dtend": _iso(obj.get("EndDateTime")),
            "due_at": start_at, "all_day": False,
            "course_code": course_code(org, title), "categories": org.strip(),
            "kind": classify_event(title, description),
            "url": "", "location": "",
            "external_id": f"valence:{eid}", "source": "valence",
            "read_at": read_at,
        })
    return out, None
