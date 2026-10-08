"""Planned maintenance windows, and what they are allowed to excuse.

WHY THIS IS SEPARATE FROM `maintenance_until`

`integrations.registry.maintenance_active` already reads a single
`tenant.settings["maintenance_until"]` timestamp and suppresses *delivery*
while it is in the future. That is a mute, and a mute is all it can ever be,
because it records only when the window ENDS. Nothing says when it began, so
nothing can say which checks fell inside it.

That is the gap the trust plan calls a top-3 accuracy fix (§2.5B): every
patch night, every planned reboot, every ISP cutover manufactures fake
downtime, a fake score drop and a fake outage in the uptime figure. The
operator muted the notifications and reasonably believed that was the end of
it; the numbers kept counting anyway, and a month later the availability
report is wrong in a direction the customer will notice.

So windows are recorded here as closed intervals with BOTH ends, appended to
`tenant.settings["maintenance_windows"]`, and the uptime figures skip samples
that fall inside one. `maintenance_until` is left exactly as it was and still
drives suppression -- the two are written together and neither reads the
other, so nothing that currently works changes behavior.

THE RULE THAT MATTERS: CANCELLING TRUNCATES

`set_maintenance(hours=0)` ends a window early. When it does, the recorded
window's end must be pulled back to now, not left at the time it was
originally going to expire. Otherwise an operator who opened an eight-hour
window, cancelled it after ten minutes, and then had a real outage at hour
three would have that outage silently excused by a window that was not
actually running. A maintenance window is a promise about when you were
working, and a promise that can be back-dated to cover an accident is not a
measurement -- it is a way to launder downtime out of an SLA.

Not implemented here, deliberately, and both are follow-ups rather than
oversights:

- RECURRENCE. The plan wants "every Tuesday 02:00". Windows are explicit
  intervals for now, so a recurring patch night is opened each time it runs.
- PER-TARGET SCOPE. A window covers the whole tenant, matching the mute it
  is written alongside. Rebooting one server currently excuses every site's
  uptime for that period, which is too generous; narrowing it needs the
  settings endpoint to take a target, which is an API change.
"""
import datetime as dt

#: Windows older than this are dropped when the list is next written. Matches
#: `retention.py`'s default `retention_days`, which is the age at which the
#: `metric` rows a window could exclude are themselves pruned -- keeping a
#: window past the samples it applies to has nothing left to exclude.
KEEP_DAYS = 90


def _parse(value) -> dt.datetime | None:
    """ISO-8601 in, aware UTC out. A naive stamp is read as UTC, the same
    convention `registry.maintenance_active` uses for `maintenance_until`."""
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return parsed.replace(tzinfo=dt.timezone.utc) if parsed.tzinfo is None else parsed


def windows(tenant) -> list[tuple[dt.datetime, dt.datetime]]:
    """Every recorded window as (start, end), skipping rows we cannot read.

    A malformed row is dropped rather than raising: this is called from the
    read path behind every uptime figure, and one bad settings entry must
    not take the Assets page down. It is also dropped rather than treated as
    an open-ended window -- an unreadable window that excused everything
    would be worse than one that excuses nothing.
    """
    out = []
    for row in (tenant.settings or {}).get("maintenance_windows") or []:
        if not isinstance(row, dict):
            continue
        starts, ends = _parse(row.get("starts")), _parse(row.get("ends"))
        if starts and ends and ends > starts:
            out.append((starts, ends))
    return out


def covers(wins: list[tuple[dt.datetime, dt.datetime]], when: dt.datetime) -> bool:
    """Was planned maintenance running at `when`?

    Half-open [start, end): a sample taken at the exact instant a window ends
    belongs to the world again. Windows are few per tenant, so a linear scan
    is the right shape -- an index would cost more than it saves.
    """
    return any(s <= when < e for s, e in wins)


def record(settings: dict, starts: dt.datetime, ends: dt.datetime) -> dict:
    """Append a window, returning a new settings dict (never mutating)."""
    rows = [r for r in (settings.get("maintenance_windows") or []) if isinstance(r, dict)]
    rows.append({"starts": starts.isoformat(), "ends": ends.isoformat()})
    return {**settings, "maintenance_windows": _pruned(rows, starts)}


def truncate_open(settings: dict, now: dt.datetime) -> dict:
    """End any window still running at `now`, at `now`.

    This is what stops a cancelled window from excusing downtime that
    happened after the operator stopped working -- see the module docstring.
    A window that already ended is left alone, so cancelling twice is a
    no-op rather than a way to rewrite history.
    """
    rows = []
    for row in (settings.get("maintenance_windows") or []):
        if not isinstance(row, dict):
            continue
        starts, ends = _parse(row.get("starts")), _parse(row.get("ends"))
        if starts and ends and starts <= now < ends:
            row = {**row, "ends": now.isoformat()}
        rows.append(row)
    return {**settings, "maintenance_windows": _pruned(rows, now)}


def _pruned(rows: list[dict], now: dt.datetime) -> list[dict]:
    cutoff = now - dt.timedelta(days=KEEP_DAYS)
    kept = []
    for row in rows:
        ends = _parse(row.get("ends"))
        if ends is None or ends >= cutoff:
            kept.append(row)
    return kept
