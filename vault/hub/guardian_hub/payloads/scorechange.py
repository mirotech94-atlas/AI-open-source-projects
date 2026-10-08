"""Why the security score moved.

The score is drawn as a number, a grade and a trend line, and none of those
say what changed. "83, grade B" answers "how am I doing"; it does not answer
the question an owner actually asks when the number drops, which is "what
happened?" Reading it off the trend chart means eyeballing a line and then
going hunting through the Security tab for a deduction that looks new.

Every scan already stores its own scored breakdown in
`Scan.raw["score"]["deductions"]`, so the answer is a diff of two scans that
are already on disk -- no new column, no migration, and nothing recomputed.

Two rules, both about not saying something untrue:

Comparison is by TIME, not by row order. The sensor sweeps about every six
minutes, so the previous row is six minutes old and almost always identical.
A "since last scan" line would read "no change" nearly always, which is worse
than no line at all, so the comparison is against the newest scan at least a
day older than the current one.

When there is nothing honest to compare against -- a tenant enrolled this
morning, a tenant whose history has aged out -- this returns None and the UI
renders nothing. It never reports a delta of zero it cannot stand behind,
the same rule `staleness.stale_cutoff` follows when it has too few sweeps to
judge.
"""
import datetime as dt

from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..schemas_dash import Deduction, DeductionShift, ScoreChange
from ..scans import parse_ts

#: How far back the comparison reaches. A day is the window an owner thinks
#: in ("it was fine yesterday") and comfortably longer than the sweep cadence,
#: so the diff is of two genuinely different pictures.
LOOKBACK_HOURS = 24

#: How many deductions the one-line summary will name before it stops
#: listing and starts counting.
_NAMED_IN_SUMMARY = 2


def _key(d: dict) -> tuple[str, str]:
    """What makes two deductions "the same finding" across scans.

    Category alone is too coarse -- two different exposure problems would
    cancel out and the diff would show nothing. The `why` text is what names
    the actual finding, and the sensor writes it deterministically.
    """
    return (str(d.get("category") or ""), str(d.get("why") or ""))


def _deductions(scan: "m.Scan | None") -> dict[tuple[str, str], dict]:
    if scan is None:
        return {}
    score = (scan.raw or {}).get("score") or {}
    return {_key(d): d for d in (score.get("deductions") or []) if isinstance(d, dict)}


def _total(scan: "m.Scan | None") -> int | None:
    """The scored total. The `Scan.score` column is authoritative; the raw
    blob's `total` is the fallback for a row written before that column was
    populated."""
    if scan is None:
        return None
    if scan.score is not None:
        return int(scan.score)
    total = ((scan.raw or {}).get("score") or {}).get("total")
    return int(total) if isinstance(total, (int, float)) else None


def _as_deduction(d: dict) -> Deduction:
    return Deduction(category=str(d.get("category") or ""),
                     points=int(d.get("points") or 0),
                     why=str(d.get("why") or ""),
                     key=str(d.get("key") or ""))


def earlier_scan(db: DbSession, tenant_id: str, current: "m.Scan",
                 hours: int = LOOKBACK_HOURS) -> "m.Scan | None":
    """The newest scan at least `hours` older than `current`, or None.

    `Scan.ts` is an ISO string carrying a UTC offset, and the offset changes
    across a daylight-saving boundary -- so ordering is done on the parsed
    datetime rather than on the string, which would compare "…-04:00" against
    "…-05:00" as text and pick the wrong row for one night a year.
    """
    now = parse_ts(current.ts)
    if now is None:
        return None
    cutoff = now - dt.timedelta(hours=hours)
    # Columns only. This used to load every Scan row -- each one carrying the
    # sensor's full last.json -- on every Overview request: ~1,000 rows of raw
    # JSON per page load on a tenant a few days old, which was most of the
    # hub's memory and a good part of its load average. Only the chosen row
    # is fetched whole.
    best_id, best_ts = None, None
    for sid, ts_s in db.query(m.Scan.id, m.Scan.ts).filter(m.Scan.tenant_id == tenant_id).all():
        ts = parse_ts(ts_s)
        if ts is None or ts > cutoff:
            continue
        if best_ts is None or ts > best_ts:
            best_id, best_ts = sid, ts
    return db.get(m.Scan, best_id) if best_id else None


#: Longest "was" fragment worth showing on its own. Past this the trimmed
#: remainder stops reading as a correction and the full text is clearer.
_SHORT_WAS = 40


def _was_fragment(now_why: str, was_why: str) -> str:
    """The part of `was_why` worth showing beside `now_why`.

    These two texts are usually the same sentence with one number changed --
    "4 unverified device(s) on the Wi-Fi" against "3 unverified device(s) on
    the Wi-Fi" -- and printing both in full makes the reader diff two long
    strings by eye to find the digit. Trimming the shared prefix and suffix
    leaves "(was 4)", which is the whole point of the line.

    Only a SUBSTITUTION is trimmed -- both sides must have something in the
    differing middle. When one side's middle is empty the change is a clause
    added or dropped, not a value replaced, and the fragment would lie:
    "extra internet ports on safehome (was 1 risky LAN service)" reads as if
    the ports used to be a LAN service, when the old text was in fact "extra
    internet ports on safehome; 1 risky LAN service". Those get the full text.

    Falls back to the full text when the difference is not a small edit, so a
    genuinely different finding is never reduced to a confusing fragment.
    """
    if not was_why:
        return ""
    kind, fragment, _shared = _edit(now_why, was_why)
    return fragment if kind == "swapped" else was_why


def _edit(now_why: str, was_why: str) -> tuple[str, str, str]:
    """(kind, fragment, shared) describing how one finding's text changed.

    kind is "added" when the new text is the old one plus a clause, "removed"
    when it is the old one minus a clause, "swapped" when a value inside was
    replaced, and "" when the two are too different to describe as an edit.
    """
    a, b = now_why or "", was_why or ""
    head = 0
    while head < len(a) and head < len(b) and a[head] == b[head]:
        head += 1
    tail = 0
    while (tail < len(a) - head and tail < len(b) - head
           and a[len(a) - 1 - tail] == b[len(b) - 1 - tail]):
        tail += 1
    now_mid = a[head:len(a) - tail].strip(" ;,-")
    was_mid = b[head:len(b) - tail].strip(" ;,-")
    shared = (a[:head] + a[len(a) - tail:]).strip(" ;,-")
    if now_mid and not was_mid:
        return "added", now_mid, shared
    if was_mid and not now_mid:
        return "removed", was_mid, shared
    if now_mid and was_mid and len(was_mid) <= _SHORT_WAS:
        return "swapped", was_mid, shared
    return "", "", shared


def _shift_phrase(shift: DeductionShift) -> str:
    """One finding's movement, said the shortest true way.

    A clause appearing inside a longer deduction is news about that clause --
    "1 risky LAN service(s) is new" -- not an invitation to compare two long
    sentences and spot the difference, which is what naming both in full
    amounts to.
    """
    kind, fragment, _shared = _edit(shift.now_why, shift.was_why)
    if kind == "added":
        return f"{fragment} is new"
    if kind == "removed":
        return f"{fragment} is gone"
    if kind == "swapped":
        return f"{shift.now_why} (was {fragment})"
    return f"{shift.now_why or shift.category} (was {shift.was_why or shift.category})"


def _pair_moves(appeared: list[Deduction],
                cleared: list[Deduction]) -> tuple[list[Deduction], list[Deduction], list[DeductionShift]]:
    """Split an appeared/cleared diff into genuine arrivals, genuine
    departures, and findings that merely moved.

    Identity is (category, why), which is right for telling two different
    exposure problems apart but wrong for a finding whose text carries a
    count: "4 unverified device(s)" becoming "3 unverified device(s)" is one
    finding changing, and reporting it as one arrival plus one departure is
    the opposite of an explanation. Anything left in both lists under the same
    category after the exact match is that case.
    """
    by_cat: dict[str, list[Deduction]] = {}
    for d in cleared:
        by_cat.setdefault(d.category, []).append(d)

    arrivals, moves = [], []
    for now in appeared:
        bucket = by_cat.get(now.category) or []
        if not bucket:
            arrivals.append(now)
            continue
        was = bucket.pop(0)
        moves.append(DeductionShift(category=now.category, was_points=was.points,
                                    now_points=now.points, was_why=was.why,
                                    now_why=now.why, key=now.key or was.key))
    departures = [d for rows in by_cat.values() for d in rows]
    departures.sort(key=lambda d: -d.points)
    moves.sort(key=lambda s: -abs(s.now_points - s.was_points))
    return arrivals, departures, moves


def _movement_parts(appeared: list[Deduction], cleared: list[Deduction],
                    changed: list[DeductionShift]) -> list[str]:
    """The clauses naming what moved, heaviest first, in the shortest true
    words -- shared by the day's summary and the "last moved" tail."""
    def name(rows: list[Deduction]) -> str:
        shown = [r.why or r.category for r in rows[:_NAMED_IN_SUMMARY] if (r.why or r.category)]
        if not shown:
            return ""
        extra = len(rows) - len(shown)
        text = ", ".join(shown)
        return f"{text} and {extra} more" if extra > 0 else text

    parts = [_shift_phrase(shift) for shift in changed[:_NAMED_IN_SUMMARY]]
    if appeared:
        parts.append(f"{name(appeared)} is new" if len(appeared) == 1 else f"new: {name(appeared)}")
    if cleared:
        parts.append(f"{name(cleared)} is gone" if len(cleared) == 1 else f"gone: {name(cleared)}")
    return parts


def _signed(delta: int) -> str:
    return f"up {delta}" if delta > 0 else f"down {abs(delta)}" if delta < 0 else "unchanged"


def _ago(hours: int) -> str:
    if hours < 48:
        return "yesterday" if hours <= 30 else f"{hours} hours ago"
    days = round(hours / 24)
    return f"{days} days ago"


def _summary(delta: int, appeared: list[Deduction], cleared: list[Deduction],
             changed: list[DeductionShift], hours: int,
             last_moved: "ScoreChange | None" = None) -> str:
    """One sentence a person can act on, naming the findings rather than the
    categories -- "3 unverified devices on the Wi-Fi is new" beats
    "Coverage changed".

    An unchanged day is not a dead line: it says when the score last moved
    and why, so the sentence under the number always explains something.
    """
    window = "since yesterday" if hours == 24 else f"over the last {hours} hours"
    head = _signed(delta).capitalize() + " " + window
    parts = _movement_parts(appeared, cleared, changed)

    if parts:
        return f"{head} — " + "; ".join(parts) + "."
    if delta != 0:
        # The score moved but nothing arrived, left or shifted -- the same
        # findings are simply weighted differently now. Say that rather than
        # leaving a bare number with no reason beside it.
        return f"{head}, with the same findings weighted differently."
    if last_moved is not None:
        tail = "; ".join(_movement_parts(last_moved.appeared, last_moved.cleared, last_moved.changed))
        tail = f" — {tail}" if tail else ""
        return f"{head}. Last moved {_ago(last_moved.hours)}, {_signed(last_moved.delta)}{tail}."
    return f"{head}."


def _diff(current: "m.Scan", previous: "m.Scan", hours: int) -> "ScoreChange | None":
    now_total, was_total = _total(current), _total(previous)
    if now_total is None or was_total is None:
        return None
    now_d, was_d = _deductions(current), _deductions(previous)
    appeared = [_as_deduction(d) for k, d in now_d.items() if k not in was_d]
    cleared = [_as_deduction(d) for k, d in was_d.items() if k not in now_d]
    # Heaviest first: the biggest mover is the one worth naming.
    appeared.sort(key=lambda d: -d.points)
    cleared.sort(key=lambda d: -d.points)
    appeared, cleared, changed = _pair_moves(appeared, cleared)
    return ScoreChange(delta=now_total - was_total, was=was_total, since=previous.ts,
                       hours=hours, appeared=appeared, cleared=cleared, changed=changed)


def last_movement(db: DbSession, tenant_id: str, current: "m.Scan") -> "ScoreChange | None":
    """The most recent time the score changed at all, and why.

    Walks the (ts, score) columns only -- never every scan's raw blob, which
    on a tenant with a thousand sweeps would be tens of megabytes per page
    load -- and loads exactly the two rows either side of the last change.
    """
    now = parse_ts(current.ts)
    if now is None:
        return None
    rows = [(parse_ts(ts), sid, score) for ts, sid, score in
            db.query(m.Scan.ts, m.Scan.id, m.Scan.score).filter(m.Scan.tenant_id == tenant_id).all()]
    rows = sorted((r for r in rows if r[0] is not None and r[2] is not None), key=lambda r: r[0], reverse=True)
    for after, before in zip(rows, rows[1:]):
        if after[2] != before[2]:
            a = db.get(m.Scan, after[1])
            b = db.get(m.Scan, before[1])
            if a is None or b is None:
                return None
            hours = max(1, int((now - before[0]).total_seconds() // 3600))
            out = _diff(a, b, hours)
            if out is not None:
                out.summary = _summary(out.delta, out.appeared, out.cleared, out.changed, hours)
            return out
    return None


def score_change(db: DbSession, tenant_id: str,
                 current: "m.Scan | None", hours: int = LOOKBACK_HOURS) -> "ScoreChange | None":
    """What moved the score since `hours` ago, or None when nothing can be
    honestly compared."""
    if current is None:
        return None
    previous = earlier_scan(db, tenant_id, current, hours)
    if previous is None:
        return None
    out = _diff(current, previous, hours)
    if out is None:
        return None
    moved = None
    if out.delta == 0 and not (out.appeared or out.cleared or out.changed):
        moved = last_movement(db, tenant_id, current)
        # Only worth telling if it is older than the window already covered.
        if moved is not None and moved.hours <= hours:
            moved = None
    out.last_moved = moved
    out.summary = _summary(out.delta, out.appeared, out.cleared, out.changed, hours, moved)
    return out
