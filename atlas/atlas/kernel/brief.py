"""The 07:00 morning brief.

The one thing ATLAS produces every single day, so it is the thing most likely
to get muted. It follows two rules:

  * **Never guess a number.** Every figure is queried when the brief is built.
    Nothing is carried over, estimated, or reused from an earlier run.
  * **Say when something could not be measured.** A section that failed says so
    rather than rendering as though the answer were "nothing to report". An
    empty inbox and an unreachable mailbox are different sentences.

The kernel composes the brief from its own tables plus whatever modules choose
to contribute through `agenda()`, `inbox()` and `brief()`. It knows nothing
about calendars or email.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .contracts import AgendaItem, InboxItem, Severity, implements
from .registry import REGISTRY
from .state import fetch_all

log = logging.getLogger("atlas.brief")

DEADLINE_HORIZON = timedelta(hours=72)


@dataclass
class Section:
    title: str
    lines: list[str] = field(default_factory=list)
    # Set when the section could not be built. Rendered instead of the lines,
    # because "I could not look" must never read as "there is nothing".
    unavailable: str | None = None
    empty: str = "Nothing."

    def render(self) -> str:
        if self.unavailable:
            return f"{self.title}\n  ⚠ could not check: {self.unavailable}"
        if not self.lines:
            return f"{self.title}\n  {self.empty}"
        body = "\n".join(f"  {line}" for line in self.lines)
        return f"{self.title}\n{body}"


@dataclass
class Brief:
    at: datetime
    sections: list[Section]
    watch_only: bool

    def render(self) -> str:
        stamp = self.at.astimezone().strftime("%A %d %B, %H:%M")
        head = f"Morning brief — {stamp}"
        mode = ("\n(ATLAS is in watch-only mode: it is reporting, not acting.)"
                if self.watch_only else "")
        body = "\n\n".join(s.render() for s in self.sections)
        return f"{head}{mode}\n\n{body}"

    @property
    def needs_attention(self) -> int:
        return sum(len(s.lines) for s in self.sections if s.title.startswith(
            ("Waiting on you", "Needs a reply")))


def _sev(value: int) -> str:
    return {0: "info", 1: "", 2: "HIGH", 3: "CRITICAL"}.get(int(value), "")


async def _section(title: str, coro, *, empty: str) -> Section:
    """Run one section's query, converting a failure into a visible warning."""
    try:
        lines = await coro
    except Exception as exc:                            # noqa: BLE001
        log.warning("brief section %r failed: %s", title, exc)
        return Section(title, unavailable=f"{type(exc).__name__}: {exc}")
    return Section(title, list(lines), empty=empty)


# --------------------------------------------------------------- sections

async def _overnight(since: datetime) -> list[str]:
    """Incidents since the last brief, and what ATLAS did about them."""
    rows = await fetch_all(
        """SELECT i.id, i.opened_at, i.resolved_at, i.severity, i.title
           FROM incidents i
           WHERE i.opened_at >= $1
           ORDER BY i.severity DESC, i.opened_at""", since)

    fixed = await fetch_all(
        """SELECT subject, payload, at FROM actions_log
           WHERE event = 'execute.finish' AND at >= $1
           ORDER BY at""", since)
    refused = await fetch_all(
        """SELECT count(*) AS n FROM actions_log
           WHERE event = 'execute.watch_only' AND at >= $1""", since)

    lines: list[str] = []
    for row in rows:
        mark = _sev(row["severity"])
        state = "resolved" if row["resolved_at"] else "still open"
        when = row["opened_at"].astimezone().strftime("%H:%M")
        lines.append(f"{when} {mark + ' ' if mark else ''}{row['title']} — {state}")

    for row in fixed:
        when = row["at"].astimezone().strftime("%H:%M")
        lines.append(f"{when} auto-fixed: {row['subject']}")

    held = refused[0]["n"] if refused else 0
    if held:
        lines.append(f"{held} action(s) were proposed and withheld "
                     "(watch-only mode)")
    return lines


async def agenda_items(now: datetime, horizon: timedelta = DEADLINE_HORIZON,
                       unread: list[str] | None = None) -> list[AgendaItem]:
    """Every module's agenda inside the horizon, oldest first.

    Split out of `_agenda` so the dashboard can render a timeline from the
    same collection the brief reads. The brief needs sentences; a screen needs
    a time it can subtract from. Sharing the gathering rather than the
    formatting is what stops the two disagreeing about what is on today.

    `horizon` is how far ahead to read (the brief's 72 hours by default; the
    dashboard's week asks for 168). A module whose agenda raises is left out
    of the items, as before, and -- when `unread` is passed -- its name and
    the exception's type are appended there, so a screen can say "the
    calendar could not be read" instead of drawing an empty day.
    """
    until = now + horizon
    items: list[AgendaItem] = []

    async def collect(name, module):
        if not implements(module, "agenda"):
            return []       # documented default: nothing timed
        try:
            got = await module.agenda(horizon)
        except Exception as exc:                        # noqa: BLE001
            log.warning("module %s agenda failed: %s", name, exc)
            if unread is not None:
                unread.append(f"{name} ({type(exc).__name__})")
            return []
        return [
            AgendaItem(when=i.when, title=i.title, kind=i.kind, detail=i.detail,
                       minutes=i.minutes, source=name)
            for i in got
        ]

    results = await asyncio.gather(
        *(collect(n, m) for n, m in REGISTRY.modules.items()))
    for batch in results:
        items.extend(batch)

    items = [i for i in items if i.when <= until]
    items.sort(key=lambda i: i.when)
    return items


async def _agenda(now: datetime) -> tuple[list[str], list[str]]:
    """Today's timed items, and anything due inside 72 hours.

    Conflicts are computed here rather than trusted from a module, so overlap
    is flagged consistently no matter which module supplied the item.
    """
    items = await agenda_items(now)

    end_of_day = now.astimezone().replace(hour=23, minute=59, second=59)
    today = [i for i in items
             if i.kind == "event" and i.when.astimezone() <= end_of_day]

    # Pairwise overlap on today's events only.
    clashing: set[int] = set()
    for a in range(len(today)):
        for b in range(a + 1, len(today)):
            first, second = today[a], today[b]
            finish = first.when + timedelta(minutes=first.minutes or 30)
            if second.when < finish:
                clashing.add(a)
                clashing.add(b)

    day_lines = []
    for index, item in enumerate(today):
        when = item.when.astimezone().strftime("%H:%M")
        flag = "  ⚠ CLASHES" if index in clashing else ""
        extra = f" — {item.detail}" if item.detail else ""
        day_lines.append(f"{when} {item.title}{extra}{flag}")

    due_lines = []
    for item in items:
        if item.kind == "event" and item.when.astimezone() <= end_of_day:
            continue
        hours = (item.when - now).total_seconds() / 3600
        if hours < 0:
            when = "OVERDUE"
        elif hours < 24:
            when = f"in {hours:.0f}h"
        else:
            when = item.when.astimezone().strftime("%a %H:%M")
        extra = f" — {item.detail}" if item.detail else ""
        due_lines.append(f"{when}: {item.title}{extra} [{item.source}]")

    return day_lines, due_lines


async def _inbox() -> list[str]:
    """Anything awaiting a reply, most urgent first -- and, first of all, any
    inbox that could not be read. A module whose read failed is named, so
    the section never says "Nothing waiting on you" over a read that did
    not happen (invariant 7)."""
    items: list[InboxItem] = []
    unreadable: list[str] = []

    async def collect(name, module):
        if not implements(module, "inbox"):
            return []       # documented default: nothing awaiting a reply
        try:
            got = await module.inbox(limit=10)
        except Exception as exc:                        # noqa: BLE001
            log.warning("module %s inbox failed: %s", name, exc)
            unreadable.append(name)
            return []
        return [
            InboxItem(at=i.at, sender=i.sender, subject=i.subject,
                      urgency=i.urgency, why=i.why, reference=i.reference,
                      source=name)
            for i in got
        ]

    results = await asyncio.gather(
        *(collect(n, m) for n, m in REGISTRY.modules.items()))
    for batch in results:
        items.extend(batch)

    items.sort(key=lambda i: (-int(i.urgency), i.at))
    lines = [f"inbox for {name} unreadable — what is waiting there is not "
             f"known" for name in sorted(unreadable)]
    for item in items[:12]:
        mark = _sev(int(item.urgency))
        age = (datetime.now(UTC) - item.at).total_seconds() / 3600
        age_text = f"{age:.0f}h" if age < 48 else f"{age/24:.0f}d"
        why = f" — {item.why}" if item.why else ""
        lines.append(f"{mark + ' ' if mark else ''}{item.sender}: "
                     f"{item.subject} ({age_text} old, {item.source}){why}")
    return lines


async def _circumstances() -> list[str]:
    """What the owner told ATLAS about his schedule, still in force."""
    from .rhythm import active_notes, circumstance_line
    notes = await active_notes()
    return [circumstance_line(n) for n in notes]


async def _businesses() -> list[str]:
    """One line per module, from the module itself."""
    lines = []
    for name in sorted(REGISTRY.modules):
        module = REGISTRY.modules[name]
        if not implements(module, "brief"):
            continue        # documented default: nothing to say
        try:
            line = module.brief()
        except Exception as exc:                        # noqa: BLE001
            line = f"could not summarise: {type(exc).__name__}"
        if line:
            lines.append(f"{name}: {line}")
    for name in sorted(REGISTRY.disabled):
        lines.append(f"{name}: not watched — {REGISTRY.disabled[name]}")
    return lines


async def _decisions() -> list[str]:
    rows = await fetch_all(
        """SELECT id, action, reason, created_at, expires_at, confidence
           FROM proposals WHERE status = 'pending'
           ORDER BY expires_at""")
    lines = []
    now = datetime.now(UTC)
    for row in rows:
        left = (row["expires_at"] - now).total_seconds() / 3600
        expiry = (f"expires in {left:.0f}h" if left >= 1
                  else "expires within the hour" if left > 0
                  else "EXPIRED")
        lines.append(f"#{row['id']} {row['action']} — {row['reason']} ({expiry})")
    # A pause waits for him too: one set by a refused login (R09) holds its
    # queued sends until he resumes it, so it is said every morning.
    from .pause import lines as paused
    lines.extend(f"Paused: {line}" for line in await paused())
    return lines


async def _judgment(since: datetime) -> list[str]:
    """What the learning layer has to say: a handover or a demotion (one
    sentence each, rare), the reliability line, and anything it decided
    not to raise — which is the one place a hushed proposal is still seen."""
    from .judgment.nightly import take_lines
    from .judgment.status import reliability_line
    lines = take_lines()
    rel = await reliability_line()
    if rel:
        lines.append(rel)
    rows = await fetch_all(
        """SELECT id, action, predicted FROM proposals
            WHERE status = 'suppressed' AND created_at >= $1
            ORDER BY created_at DESC LIMIT 8""", since)
    for r in rows:
        pct = f"{round((r['predicted'] or 0) * 100)}%"
        lines.append(f"Didn't raise #{r['id']} {r['action']} — you say no to "
                     f"this ({pct} yes). Say the word if you want it.")
    return lines


async def _marketing(since: datetime) -> list[str]:
    """What the experiments measured overnight, what is waiting, and the one
    monetization idea worth his attention.

    Every line carries the measurement it rests on. An experiment that closed
    `unmeasured` says so with its reason rather than being left out — a
    learning loop that quietly stopped measuring is exactly the failure this
    section exists to surface.
    """
    measured = await fetch_all(
        """SELECT business, platform, variant, outcome, lesson, measured_at
           FROM marketing_experiments
           WHERE measured_at >= $1
           ORDER BY measured_at DESC LIMIT 6""", since)
    waiting = await fetch_all(
        """SELECT business, platform, variant, hypothesis, at
           FROM marketing_experiments
           WHERE outcome IS NULL AND posted_ref IS NULL
           ORDER BY at DESC LIMIT 5""")
    money = await fetch_all(
        """SELECT business, idea, requires, est_note, score
           FROM monetization_ideas
           WHERE status = 'proposed'
           ORDER BY score DESC NULLS LAST, at DESC LIMIT 1""")

    lines: list[str] = []
    for row in measured:
        lines.append(row["lesson"] or
                     f"{row['business']} · {row['platform']} · "
                     f"{row['variant']}: {row['outcome']}")
    for row in waiting:
        lines.append(f"waiting on you: {row['business']} · {row['platform']} "
                     f"— {row['variant']} ({row['hypothesis']})")
    for row in money:
        lines.append(f"money: {row['idea']} ({row['business']}) — requires "
                     f"{row['requires'] or 'nothing stated'}")

    # The executor refuses silently to everyone but its caller. If the
    # weekly plan hit the hourly ceiling, watch-only or a bad parameter, the
    # scheduler counted it; say so here rather than let a half-filed plan
    # look like a quiet week.
    from .marketing.cycle import last_filing
    filing = last_filing()
    if filing.refused and filing.at is not None and filing.at >= since:
        why = "; ".join(filing.reasons[:3]) or "no reason given"
        lines.append(f"weekly plan: {filing.refused} item(s) not filed, "
                     f"{filing.filed} filed ({filing.at:%a %H:%M} UTC) — {why}")
    return lines


async def _held_overnight(since: datetime) -> list[str]:
    """Anything quiet hours held back."""
    rows = await fetch_all(
        """SELECT title, body, severity FROM brief_queue
           WHERE delivered = FALSE AND at >= $1
           ORDER BY severity DESC, at""", since)
    return [f"{_sev(r['severity']) + ' ' if _sev(r['severity']) else ''}"
            f"{r['title']}" for r in rows]


async def _checked_my_work(since: datetime) -> list[str]:
    """The automation on itself, in two lines at most: what did NOT take
    effect or needs a person since yesterday, and what is paused. Nothing
    when clean. A read that failed is named -- never taken as "all clear"."""
    from . import pause
    from .weekly import _named, _plural, _why

    unread: list[str] = []
    wrong: list[str] = []
    human = 0
    try:
        rows = await fetch_all(
            """SELECT event, subject FROM actions_log
                WHERE at >= $1 AND event IN ('verify.contradicted',
                      'reconcile.failed', 'reconcile.needs_human')
                ORDER BY at, id""", since)
        for r in rows:
            if r["event"] == "reconcile.needs_human":
                human += 1
            elif str(r["subject"]) not in wrong:
                wrong.append(str(r["subject"]))
    except Exception as exc:                            # noqa: BLE001
        log.warning("brief: could not read the read-backs: %s", exc)
        unread.append("the read-backs")
    try:
        got = await fetch_all(
            """SELECT count(*) AS n FROM outbox
                WHERE status = 'needs_human' AND finished_at >= $1""", since)
        human += int(got[0]["n"]) if got else 0
    except Exception as exc:                            # noqa: BLE001
        log.warning("brief: could not read the outbox: %s", exc)
        unread.append("the outbox")
    paused: list[str] = []
    try:
        for p in await pause.active():
            scope = p.scope.removeprefix(pause.SHADOW)
            what = "everything" if scope == pause.ALL else scope
            why = _why(p.reason, 50)
            paused.append(what + (" (shadow)" if p.shadow else "")
                          + (f" — {why}" if why else ""))
    except Exception as exc:                            # noqa: BLE001
        log.warning("brief: could not read the pauses: %s", exc)
        unread.append("the pauses")

    first = []
    if wrong:
        first.append(f"Didn't take effect: {_named(wrong)}")
    if human:
        first.append(f"{human} {_plural(human, 'needs', 'need')} you to check "
                     f"by hand")
    lines = ["; ".join(first) + "."] if first else []
    if paused:
        lines.append(f"Paused: {_named(paused)}.")
    if unread:
        lines.append(f"Couldn't read {', '.join(unread)}, so that part's unknown.")
    return lines


# ------------------------------------------------------------------ build

async def build(since: datetime | None = None) -> Brief:
    from .executor import watch_only

    now = datetime.now(UTC)
    since = since or (now - timedelta(hours=24))

    day_lines: list[str] = []
    due_lines: list[str] = []
    agenda_error: str | None = None
    try:
        day_lines, due_lines = await _agenda(now)
    except Exception as exc:                            # noqa: BLE001
        agenda_error = f"{type(exc).__name__}: {exc}"

    sections = [
        await _section("Overnight", _overnight(since),
                       empty="No incidents. Nothing was auto-fixed."),
        await _section("Held by quiet hours", _held_overnight(since),
                       empty="Nothing was held."),
        Section("Today", day_lines, unavailable=agenda_error,
                empty="Nothing scheduled."),
        Section("Due inside 72 hours", due_lines, unavailable=agenda_error,
                empty="Nothing due."),
        await _section("Needs a reply", _inbox(),
                       empty="Nothing waiting on you."),
        await _section("Circumstances", _circumstances(),
                       empty="Nothing noted."),
        await _section("Where things stand", _businesses(),
                       empty="No modules are loaded."),
        await _section("Waiting on you", _decisions(),
                       empty="No decisions pending."),
        await _section("Marketing", _marketing(since),
                       empty="No experiment closed overnight and none is "
                             "waiting."),
        await _section("Judgment", _judgment(since),
                       empty="Nothing to report; still learning."),
    ]
    # Silent when clean: only a section with something in it is added.
    checked = await _section("Checked my work", _checked_my_work(since), empty="")
    if checked.lines or checked.unavailable:
        sections.insert(8, checked)
    return Brief(at=now, sections=sections, watch_only=watch_only())


async def store(brief: Brief, kind: str = "brief",
                delivered_via: str | None = None) -> int | None:
    """Keep the rendered brief.

    A brief is built from live queries, so without this there is no archive: a
    link texted on Tuesday would show Thursday's brief by Thursday.
    """
    import json

    from .state import fetch_one

    row = await fetch_one(
        """INSERT INTO reports (at, kind, title, body, sections, delivered_via)
           VALUES ($1,$2,$3,$4,$5,$6) RETURNING id""",
        brief.at, kind,
        f"Morning brief — {brief.at.astimezone():%a %d %b}",
        brief.render(),
        json.dumps([{"title": s.title, "lines": s.lines,
                     "unavailable": s.unavailable, "empty": s.empty}
                    for s in brief.sections]),
        delivered_via,
    )
    return row["id"] if row else None


async def already_sent_today() -> bool:
    """Was a brief stored since local midnight? Read from the archive, which
    survives a restart — the scheduler's own guard is process memory.

    Without this every api restart after BRIEF_HOUR sent the brief again:
    three on 2026-09-24 (07:00, then 19:18 and 21:30, one per deploy) and four
    in one evening on 2026-09-22. A brief he has already read, arriving again
    in the evening, trains him to skip the one that matters.
    """
    from .state import fetch_one

    midnight = datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0)
    row = await fetch_one(
        "SELECT 1 AS x FROM reports WHERE kind = 'brief' AND at >= $1 LIMIT 1",
        midnight)
    return row is not None


async def send(*, force: bool = False) -> Brief | None:
    """Build and deliver once a day. Called by the scheduler at 07:00;
    `force` is for a brief he asked for by hand (`atlas send-brief`).
    Returns None when today's brief already went out."""
    from .comms import Outbound, notify
    from .state import execute_sql

    if not force and await already_sent_today():
        log.info("brief already sent today; not sending it again after a restart")
        return None

    brief = await build()
    used = await notify(Outbound(
        title=f"Morning brief — {brief.at.astimezone():%a %d %b}",
        body=brief.render(),
        severity=Severity.NORMAL,
        kind="digest",
        # #atlas-brief exists for this. Without a topic it went to the
        # default channel with everything else that had no home.
        topic="brief",
    ))
    # Stored after the attempt, with how it went out — including "queued" or
    # nothing at all, so the archive does not imply a delivery that failed.
    await store(brief, delivered_via=",".join(used) or "undelivered")
    # Mark the quiet-hours queue delivered only after it has been sent.
    await execute_sql(
        "UPDATE brief_queue SET delivered = TRUE WHERE delivered = FALSE")
    return brief
