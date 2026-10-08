"""school — college (CUNY, Fall 2026) through Brightspace's open doors.

What it watches, every 30 minutes:
  * `calendar_feed` — the Brightspace calendar subscription feed
    (`BRIGHTSPACE_ICS_URL`, a secret-bearing URL that is never logged and is
    scrubbed from every reason). Value: items due in the next 7 days.
    UNKNOWN without the URL or when the feed cannot be read.
  * `notifications` — Brightspace notification emails from the last 48 hours
    in the Gmail the `mail` module already reads. UNKNOWN when Gmail is
    unreachable or not configured.
  * `grades` — grade-released emails in the last 7 days (INFO).
Everything it reads is kept in `school_items` (keyed by `ics:<uid>`,
`email:<gmail id>` or `valence:<event id>`), and signals are told once per
item per threshold — due in 72 hours, due in 24 hours, grade released, news,
alert, new assignment — each naming the course, the source and the time it
was read. The email kinds are marked in `school_items.signalled_at`, so they
survive a restart: anything stored but never told (a crash between the two)
is told on the next run, and nothing told is told again. Deadline thresholds
are remembered in memory only, so a restart re-tells a deadline still inside
its 72h/24h window once (the scheduler's own title dedupe absorbs most of
these). An email whose From address is not at brightspace.com, d2l.com or
cuny.edu is kept as an `alert` marked `sender_unverified` and never signalled.

What it may do, both Tier AUTO and both local:
  * `school.draft_work` — drafts one item's work in the course's configured
    kind (handwritten, webwork, files, lab_report) under `data/school/`, plus
    a `content_drafts` row. ATLAS drafts; Miro writes, types and submits.
    Nothing is ever submitted to Brightspace — there is no login path.
  * `school.set_course` — sets a course's work kind; rollback restores it.

Endpoints assumed, so Miro can correct them: the D2L calendar feed at the URL
he pastes; Gmail search `from:(brightspace OR d2l OR cuny.edu)`; and, only if
CUNY ever grants the four `BRIGHTSPACE_*` Valence variables, the Valence
route described in `brightspace.py`.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta

from ...kernel import state
from ...kernel.contracts import (
    Action,
    AgendaItem,
    Check,
    Health,
    Observation,
    Severity,
    Signal,
    Tier,
)
from ...kernel.permissions import wrap_untrusted
from . import brightspace, work

log = logging.getLogger("atlas.school")

INTERVAL = timedelta(minutes=30)
DUE_KINDS = ("assignment", "quiz", "exam", "lab")
MAIL_KINDS = ("grade", "news", "alert")
SOURCE_LABEL = {"ics": "the calendar feed", "email": "a Brightspace email",
                "valence": "the Valence API"}

# What the last successful reads saw, for the synchronous brief() only. A
# figure in the brief always comes from here, with the time it was read.
_LAST: dict[str, dict] = {}
# (item key, threshold) pairs already told. Deadlines live only here, so a
# restart re-tells a deadline still inside its window, once. Email kinds are
# also marked durably in `school_items.signalled_at`; this set only stops a
# repeat when that UPDATE could not be written.
_SIGNALLED: set[tuple[str, str]] = set()


def _now() -> datetime:
    return datetime.now(UTC)


def _local(dt: datetime) -> datetime:
    return dt.astimezone(brightspace.local_tz())


def _when(dt: datetime, all_day: bool = False) -> str:
    return f"{_local(dt):%a} (all day)" if all_day else f"{_local(dt):%a %H:%M}"


def _label(course: str | None, title: str | None) -> str:
    title = (title or "untitled").strip()
    if course and course not in title:
        return f"{course} {title}"
    return title


# ----------------------------------------------------------------- storage

async def store(items: list[dict]) -> tuple[dict[str, int], str | None]:
    """Upsert into school_items. Returns {external_id: id} and, if the store
    could not be reached, why."""
    ids: dict[str, int] = {}
    try:
        for i in items:
            row = await state.fetch_one(
                """INSERT INTO school_items
                     (course, title, due_at, kind, external_id, course_code,
                      source, grade, url, meta)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
                   ON CONFLICT (external_id) DO UPDATE SET
                     course = EXCLUDED.course, title = EXCLUDED.title,
                     due_at = EXCLUDED.due_at, kind = EXCLUDED.kind,
                     course_code = EXCLUDED.course_code,
                     source = EXCLUDED.source,
                     grade = COALESCE(EXCLUDED.grade, school_items.grade),
                     url = COALESCE(EXCLUDED.url, school_items.url),
                     meta = EXCLUDED.meta
                   RETURNING id""",
                i.get("course_code") or i.get("course") or i.get("categories")
                or "unknown course",
                (i.get("title") or i.get("summary") or "untitled")[:300],
                i.get("due_at"), i["kind"], i["external_id"],
                i.get("course_code"), i["source"], i.get("grade"),
                i.get("url") or None, json.dumps(i.get("meta") or {}))
            if row is not None:
                ids[i["external_id"]] = row["id"]
    except Exception as exc:                            # noqa: BLE001
        return ids, brightspace.scrub(f"could not record items: {exc}")
    return ids, None


def _due_within(items: list[dict], now: datetime, days: int) -> list[dict]:
    horizon = now + timedelta(days=days)
    due = [i for i in items if i.get("kind") in DUE_KINDS and i.get("due_at")
           and now <= i["due_at"] <= horizon]
    return sorted(due, key=lambda i: i["due_at"])


# ------------------------------------------------------------------ checks

async def check_calendar_feed() -> Observation:
    if not os.getenv(brightspace.ICS_ENV):
        return Observation("calendar_feed", Health.UNKNOWN, detail=(
            f"{brightspace.ICS_ENV} not set — Brightspace → Calendar → "
            "Settings → Subscribe gives the feed URL"))
    items, reason = await brightspace.fetch_ics()
    if reason is not None:
        _LAST.setdefault("ics_down", _now())
        return Observation("calendar_feed", Health.UNKNOWN,
                           detail=brightspace.scrub(reason))
    _LAST.pop("ics_down", None)
    read_at = items[0]["read_at"] if items else _now()
    _ids, store_reason = await store(items)
    due = _due_within(items, _now(), 7)
    _LAST["ics"] = {"read_at": read_at, "due": due}
    detail = f"{len(due)} item(s) due in 7 days"
    if due:
        nxt = due[0]
        detail += (f"; next {_label(nxt.get('course_code'), nxt.get('summary'))} "
                   f"{_when(nxt['due_at'], nxt.get('all_day', False))}")
    health = Health.OK
    if store_reason:
        health = Health.DEGRADED
        detail += f"; {store_reason}"
    return Observation(
        "calendar_feed", health, value=float(len(due)), unit="items",
        detail=brightspace.scrub(detail),
        evidence={"due": [{"course": d.get("course_code"), "kind": d["kind"],
                           "due_at": d["due_at"].isoformat()} for d in due],
                  "events": len(items)},
        at=read_at)


async def _mail_check(name: str, hours: int, kinds: tuple[str, ...]) -> Observation:
    info: dict = {}
    items, reason = await brightspace.scan_emails(since_hours=hours, info=info)
    if reason is not None:
        return Observation(name, Health.UNKNOWN, detail=reason)
    read_at = _now()
    picked = [i for i in items if i["kind"] in kinds]
    unverified = sum(1 for i in items if (i.get("meta") or {}).get("sender_unverified"))
    _ids, store_reason = await store(items)
    counts: dict[str, int] = {}
    for i in items:
        counts[i["kind"]] = counts.get(i["kind"], 0) + 1
    truncated = bool(info.get("truncated"))
    _LAST[name] = {"read_at": read_at, "counts": counts, "picked": len(picked),
                   "truncated": truncated}
    detail = (f"{len(picked)}{'+' if truncated else ''} Brightspace email(s) "
              f"in {hours}h"
              + (f" ({', '.join(f'{v} {k}' for k, v in sorted(counts.items()))})"
                 if counts else ""))
    if truncated:
        detail += (f"; only the first {info.get('listed')} matching messages were "
                   "read — more exist, so this is a floor")
    if unverified:
        detail += f"; {unverified} from an unverified sender, kept as alerts"
    health = Health.OK
    if store_reason:
        health = Health.DEGRADED
        detail += f"; {store_reason}"
    return Observation(name, health, value=float(len(picked)), unit="emails",
                       detail=detail, evidence={"counts": counts}, at=read_at)


async def check_notifications() -> Observation:
    return await _mail_check("notifications", 48,
                             ("grade", "assignment", "news", "alert"))


async def check_grades() -> Observation:
    return await _mail_check("grades", 24 * 7, ("grade",))


# ---------------------------------------------------- reads for the agents

def _compact(i: dict, ids: dict[str, int]) -> dict:
    due = i.get("due_at")
    return {
        "id": ids.get(i["external_id"]),
        "course": i.get("course_code") or i.get("course"),
        "kind": i["kind"],
        "title": i.get("title") or i.get("summary"),
        "due_at": due.isoformat() if due else None,
        "all_day": i.get("all_day", False),
        "grade": i.get("grade"),
        "at": i["at"].isoformat() if i.get("at") else None,
        "source": i["source"],
        "sender_unverified": bool((i.get("meta") or {}).get("sender_unverified")),
    }


def _answer(items: list[dict], ids: dict[str, int], read_at: datetime,
            sources: list[str], notes: list[str]) -> dict:
    body = json.dumps([_compact(i, ids) for i in items], default=str)
    out = {"read_at": read_at.isoformat(), "sources": sources,
           "count": len(items),
           "items": wrap_untrusted(body, "brightspace")}
    if notes:
        out["notes"] = notes
    return out


async def upcoming(days: int = 7) -> dict:
    """Items due in the next `days`, read fresh from the feed (and Valence,
    if granted). Every item carries its `id` for school.draft_work."""
    items: list[dict] = []
    notes: list[str] = []
    sources: list[str] = []
    ics, reason = await brightspace.fetch_ics()
    if reason is not None:
        notes.append(reason)
    else:
        items += ics
        sources.append("calendar feed")
    val, reason = await brightspace.valence_upcoming(max(int(days), 1))
    if reason is not None:
        notes.append(reason)
    else:
        items += val
        sources.append("valence")
    if not sources:
        return {"error": "; ".join(notes)}
    ids, store_reason = await store(items)
    if store_reason:
        notes.append(store_reason + " — ids missing, drafts need them")
    now = _now()
    return _answer(_due_within(items, now, int(days)), ids, now, sources, notes)


async def _mail_read(days: int, kinds: tuple[str, ...]) -> dict:
    info: dict = {}
    items, reason = await brightspace.scan_emails(
        since_hours=max(int(days), 1) * 24, info=info)
    if reason is not None:
        return {"error": reason}
    read_at = _now()
    ids, store_reason = await store(items)
    picked = sorted((i for i in items if i["kind"] in kinds),
                    key=lambda i: i["at"], reverse=True)
    notes = [store_reason] if store_reason else []
    if info.get("truncated"):
        notes.append(f"only the first {info.get('listed')} matching messages were "
                     "read — more exist, so this list is incomplete")
    return _answer(picked, ids, read_at, ["notification emails"], notes)


async def grades(days: int = 7) -> dict:
    return await _mail_read(days, ("grade",))


async def alerts(days: int = 7) -> dict:
    return await _mail_read(days, ("news", "alert", "assignment"))


# ----------------------------------------------------------------- signals

_SIGNAL_ROWS = """
    SELECT id, course, course_code, title, due_at, kind, external_id, source,
           grade, seen_at, signalled_at, meta
    FROM school_items
    WHERE NOT done
      AND ((due_at > now() AND due_at <= now() + interval '72 hours')
           OR (due_at IS NULL
               AND signalled_at IS NULL
               AND kind IN ('grade', 'news', 'alert', 'assignment')
               AND seen_at > now() - interval '7 days'
               AND NOT COALESCE((meta->>'sender_unverified')::boolean, FALSE)))"""


def _meta(row: dict) -> dict:
    raw = row.get("meta")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _read_time(row: dict) -> datetime | None:
    last = _LAST.get("ics") if row.get("source") == "ics" else None
    if last:
        return last["read_at"]
    last = _LAST.get("notifications") if row.get("source") == "email" else None
    if last:
        return last["read_at"]
    return row.get("seen_at")


def _provenance(row: dict) -> str:
    source = SOURCE_LABEL.get(row.get("source") or "", row.get("source") or "unknown")
    read = _read_time(row)
    return f"from {source}, read {_local(read):%H:%M}" if read else f"from {source}"


def _deadline_signal(r: dict, key: str, now: datetime) -> Signal | None:
    hours = (r["due_at"] - now).total_seconds() / 3600
    if hours < 0 or hours > 72:
        return None
    threshold = "24h" if hours <= 24 else "72h"
    if (key, threshold) in _SIGNALLED:
        return None
    _SIGNALLED.add((key, threshold))
    if threshold == "24h":
        _SIGNALLED.add((key, "72h"))
    course = r.get("course_code") or r.get("course")
    kind = r.get("kind")
    draftable = kind in ("assignment", "lab")
    return Signal(
        title=f"Due within {threshold}: {_label(course, r.get('title'))}",
        body=(f"{course} {kind} '{r.get('title')}' is due {_when(r['due_at'])} "
              f"({_provenance(r)}). ATLAS can draft it; you write it up and "
              "submit."),
        severity=Severity.HIGH if threshold == "24h" else Severity.NORMAL,
        suggests="school.draft_work" if draftable else None,
        params={"item_id": r["id"]} if draftable else {})


def _mail_signal(r: dict) -> Signal:
    course = r.get("course_code") or r.get("course")
    label = _label(course, r.get("title"))
    kind = r.get("kind")
    if kind == "assignment":
        return Signal(
            title=f"New assignment: {label}",
            body=(f"{course or 'Brightspace'}: '{r.get('title')}' — no due date "
                  f"in the email; the calendar feed will carry it "
                  f"({_provenance(r)})."),
            severity=Severity.NORMAL,
            suggests="school.draft_work", params={"item_id": r["id"]})
    if kind == "grade":
        grade = f" — {r['grade']}" if r.get("grade") else ""
        return Signal(
            title=f"Grade released: {label}",
            body=(f"{course}: grade released for '{r.get('title')}'{grade} "
                  f"({_provenance(r)})."),
            severity=Severity.NORMAL)
    word = "News" if kind == "news" else "Brightspace alert"
    return Signal(
        title=f"{word}: {label}",
        body=f"{course or 'Brightspace'}: '{r.get('title')}' ({_provenance(r)}).",
        severity=Severity.INFO if kind == "news" else Severity.NORMAL)


async def _signals() -> tuple[Signal, ...]:
    try:
        rows = await state.fetch_all(_SIGNAL_ROWS)
    except Exception as exc:                            # noqa: BLE001
        log.warning("school signals: %s", brightspace.scrub(exc))
        return ()
    now = _now()
    out: list[Signal] = []
    told: list[int] = []
    for r in rows:
        key = r.get("external_id") or str(r.get("id"))
        kind = r.get("kind")
        if kind in DUE_KINDS and r.get("due_at"):
            if (sig := _deadline_signal(r, key, now)) is not None:
                out.append(sig)
        elif kind in MAIL_KINDS or kind == "assignment":
            if (r.get("signalled_at") is not None or (key, kind) in _SIGNALLED
                    or _meta(r).get("sender_unverified")):
                continue
            _SIGNALLED.add((key, kind))
            out.append(_mail_signal(r))
            told.append(int(r["id"]))
    if told:
        try:
            await state.execute_sql(
                """UPDATE school_items SET signalled_at = now()
                   WHERE id = ANY($1::bigint[])""", told)
        except Exception as exc:                        # noqa: BLE001
            log.warning("school: could not mark %d item(s) signalled: %s",
                        len(told), brightspace.scrub(exc))
    return tuple(out)


# ------------------------------------------------------------------ module

class SchoolModule:
    name = "school"
    title = "College (CUNY, Fall 2026)"
    requires_env = ()
    optional_env = (brightspace.ICS_ENV, *brightspace.VALENCE_ENV, "SCHOOL_TERM")
    setup_note = ("Brightspace → Calendar → Settings → Subscribe, then install "
                  "the URL with bootstrap/set-secret.sh BRIGHTSPACE_ICS_URL; "
                  "and turn on instant notification emails. See CONFIGURE.md.")

    async def setup(self) -> None:
        """Seed the course work kinds once; he confirms or changes them with
        school.set_course. An existing row is never overwritten."""
        term = os.getenv("SCHOOL_TERM", "2026FA")
        try:
            for code, kind in work.DEFAULT_COURSES.items():
                await state.execute_sql(
                    """INSERT INTO school_courses (code, term, work_kind)
                       VALUES ($1, $2, $3) ON CONFLICT (code) DO NOTHING""",
                    code, term, kind)
        except Exception as exc:                        # noqa: BLE001
            log.warning("school: could not seed school_courses: %s", exc)

    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("calendar_feed", check_calendar_feed, INTERVAL, Severity.NORMAL,
                  "items due in 7 days, from the Brightspace calendar feed",
                  baseline_sigma=None),
            Check("notifications", check_notifications, INTERVAL, Severity.NORMAL,
                  "Brightspace notification emails in the last 48 hours",
                  baseline_sigma=None),
            Check("grades", check_grades, INTERVAL, Severity.INFO,
                  "grade-released emails in the last 7 days",
                  baseline_sigma=None),
        )

    def actions(self):
        return (
            Action(
                name="school.draft_work",
                tier=Tier.AUTO,
                summary=("Draft one school item's work in its course's kind, "
                         "under data/school; he writes it up and submits — "
                         "nothing reaches Brightspace"),
                run=work.draft_work, dry_run=work.draft_work_dry,
                rollback=work.draft_work_rollback,
                params={"item_id": "school_items id, from school_upcoming",
                        "notes": "optional: his own notes on the assignment"},
                max_per_hour=10,
            ),
            Action(
                name="school.set_course",
                tier=Tier.AUTO,
                summary="Set how a course's work is drafted",
                run=work.set_course, dry_run=work.set_course_dry,
                rollback=work.set_course_rollback,
                params={"code": "course code, e.g. PHY 120, or a department, e.g. MAT",
                        "work_kind": "handwritten | webwork | files | lab_report"},
                max_per_hour=10,
            ),
        )

    async def signals(self):
        return await _signals()

    async def agenda(self, within: timedelta):
        try:
            rows = await state.fetch_all(
                """SELECT id, course, course_code, title, due_at, kind, source
                   FROM school_items
                   WHERE NOT done AND due_at > now() AND due_at <= now() + $1::interval
                   ORDER BY due_at""", within)
        except Exception as exc:                        # noqa: BLE001
            log.warning("school agenda: %s", brightspace.scrub(exc))
            return ()
        now = _now()
        out = []
        for r in rows:
            due = r.get("due_at")
            if not due or not (now < due <= now + within):
                continue
            if r.get("kind") in MAIL_KINDS:
                continue
            course = r.get("course_code") or r.get("course")
            source = SOURCE_LABEL.get(r.get("source") or "", "Brightspace")
            out.append(AgendaItem(
                when=due, title=_label(course, r.get("title")), kind="deadline",
                detail=f"{r.get('kind')}, from {source}", source="school"))
        return tuple(out)

    def brief(self) -> str:
        if not os.getenv(brightspace.ICS_ENV) and "ics" not in _LAST:
            head = "College: calendar feed not configured (BRIGHTSPACE_ICS_URL)"
        elif "ics_down" in _LAST:
            head = (f"College: calendar feed unreachable since "
                    f"{_local(_LAST['ics_down']):%H:%M}")
        elif "ics" not in _LAST:
            head = "College: calendar feed not read yet"
        else:
            ics = _LAST["ics"]
            due = ics["due"]
            read = f"{_local(ics['read_at']):%H:%M}"
            head = f"College: {len(due)} due this week"
            if due:
                nxt = due[0]
                head += (f" — next {_label(nxt.get('course_code'), nxt.get('summary'))} "
                         f"({_when(nxt['due_at'], nxt.get('all_day', False))}, "
                         f"from the calendar feed, read {read})")
            else:
                head += f" (calendar feed, read {read})"
        parts = [head]
        if "grades" in _LAST:
            g = _LAST["grades"]
            n = g["picked"]
            parts.append(f"{n}{'+' if g.get('truncated') else ''} "
                         f"grade{'s' if n != 1 else ''} released "
                         f"(read {_local(g['read_at']):%H:%M})")
        if "notifications" in _LAST:
            counts = _LAST["notifications"]["counts"]
            n = counts.get("news", 0) + counts.get("alert", 0)
            read = f"{_local(_LAST['notifications']['read_at']):%H:%M}"
            parts.append(f"{n} alert{'s' if n != 1 else ''} (mail read {read})")
        if "grades" not in _LAST and "notifications" not in _LAST:
            parts.append("notification emails not read yet")
        return "; ".join(parts)


MODULE = SchoolModule()
