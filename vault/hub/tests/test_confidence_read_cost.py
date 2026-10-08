"""What scoring costs the read path, per alert, pinned by counting statements.

`remedy._apply_confidence` runs once per resolved alert, and the inbox pays it
twice per alert -- `payloads/inbox._counts` resolves every open alert to decide
the Needs-you badge, and `build` resolves again per row it returns. So the
cost of the rubric is inherently proportional to the number of open alerts,
and a flat total is the wrong thing to assert: it would encode the demo
fixture's own alert count and break the day the capture changes, while saying
nothing about the shape of the SQL.

What can be asserted is the SLOPE -- statements added per additional open
alert. This measures the same request at two alert counts and pins the
difference, the way `tests/test_platform_trends.py` measures at two customer
counts and demands they match. CLAUDE.md § Tests records why neither is a
ceiling: a ceiling has to be guessed, and a guess loose enough not to be
brittle is loose enough to sit above a real N+1 on a small fixture. That is
exactly how the wall-clock versions of these guards failed -- with one query
per device injected (20 statements to 55) the old `< 2.0s` assertion still
passed, in 0.195s.

Measured 2026-09-24 on the seeded demo, at +4 and +20 added alerts:

| what | slope |
|---|---|
| `_apply_confidence` stubbed out | 2 |
| live | 12 |

So 2 statements per alert are `remedy.resolve`'s own pre-existing work and 10
are the rubric: `confidence.assess` issues five queries (the host's fixes, the
alert row, the confirmed-repair count, the reopen count, the newest scan) and
the inbox resolves each alert twice. Two duplicate queries were removed to get
here and both are worth naming, because the shape of the next regression will
look like them:

* `confirmed_repairs` ran twice per alert -- once inside `assess` for the
  `verified` factor, once again from `_apply_confidence` for `gate`'s
  promotion bar. `Confidence.confirmed` now carries the number `assess`
  already paid for.
* the `undone` factor fetched every non-system `/api/alert` Edit of the last
  180 days into Python and counted the matches there. Ack, snooze, resolve and
  reopen all write one, so on an active tenant that is the alert list's whole
  recent history, per alert, per resolution. The predicate is now in SQL.

The slope is asserted EXACTLY, and it used to be rounded. The rounding was
there for a wobble whose cause is now known and removed: one statement in the
request is not per-alert at all -- `platform_settings`' 20-second process-global
cache reloading -- and depending on which of the two measurements it landed in,
`(more - few)` carried an error of ±1 and the ratio fell a fraction either side
of 12. `_slope` now primes that cache before each measurement, so the error is
zero and nothing has to be tolerated. That matters beyond tidiness: rounding
over sixteen added alerts also absorbed eight statements of genuine per-alert
regression, which is precisely the shape CLAUDE.md § Tests warns about -- a
guard loose enough not to be brittle is loose enough to sit above the thing it
exists to catch.

An equality rather than a `<=` catches both directions, which matters here.
Too high means a query was added inside the per-alert path. Too low almost
certainly means this guard went vacuous, and there are two ways for that to
happen:

* the added alerts must have keys `remedy.fix_for_key` resolves, because an
  unresolvable key returns through `remedy._human`, which never scores at all
  -- so a typo in the key shape would make the slope collapse and the test
  pass for the wrong reason;
* every alert this guard adds is `svc:{HOST}:svcN`, which resolves to exactly
  one action, `restart_service`. So a per-alert query added on a branch these
  alerts never take -- a lookup only `lan:` keys reach, or one only a tier-3
  action reaches -- leaves the slope at 12 and this file silent while the real
  read path pays it. The slope measures the cost of the path THIS key shape
  walks, not of every path `_apply_confidence` has. A change that adds work
  per alert for some alert kinds needs its own measurement with a key of that
  kind, not a re-run of this one.

If a change legitimately moves the number, the table above says which half to
look at.
"""
import datetime as dt

from guardian_hub import models as m, remedy
from guardian_hub.db import SessionLocal
from tests.querycount import queries_for
from tests.test_seed_helpers import seeded_demo

PATH = "/api/v1/inbox?view=alerts"

#: `restart_service` on a host the fixture really has, with the service as the
#: argument -- `remedy.fix_for_key` maps `svc:{host}:{svc}` onto it, so every
#: added alert reaches the scoring path instead of `remedy._human`.
HOST = "clinic-nas"


def _add_open_alerts(tid: str, n: int, start: int = 0) -> None:
    db = SessionLocal()
    # `first_seen`/`last_seen` are non-nullable strings carrying a UTC offset,
    # and `msg` is a non-nullable Text column.
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    for i in range(n):
        db.add(m.Alert(tenant_id=tid, key=f"svc:{HOST}:svc{start + i}", sev="WARN",
                       msg=f"service svc{start + i} is not running on {HOST}",
                       status="open", first_seen=stamp, last_seen=stamp))
    db.commit()
    db.close()


def _slope(client, tid, path=PATH) -> float:
    """Statements added per extra open alert, between +4 and +20. Exact.

    Both measurements are taken with `platform_settings`' cache already warm,
    and that is what makes the ratio a whole number rather than something that
    has to be rounded. `_apply_confidence` asks `platform_settings.auto_heal()`
    once per resolution; the values live in a process-global dict with a
    20-second TTL (`CACHE_TTL_S`, keyed off a module-global `_loaded_at`), and
    a stale one costs one extra `SELECT ... FROM platform_setting` for the
    whole request -- not one per alert. So whether that reload happened to
    fall inside `few` or inside `more` moved `(more - few)` by one either way,
    which is the entire wobble the old `round()` was absorbing. Measured with
    these two lines removed, the delta is 191 rather than 192 and the ratio
    11.9375: the cache is cold at the first request, so `few` pays the reload
    and `more` does not. Refreshing immediately before each request puts both
    in the same cache state and the error goes to zero, so the exact ratio can
    be asserted -- and a guard that
    tolerates nothing is the point: rounding over sixteen added alerts also
    tolerated eight statements of real regression, which is the failure
    CLAUDE.md § Tests describes for the wall-clock versions of these guards.

    Reset here and not in `conftest.py`'s `client` fixture, even though that
    fixture already resets four other process-global caches for exactly this
    hazard and pointedly not this one: priming it for every test in the suite
    changes what every other test measures, and this is the only test that
    counts statements finely enough to care.

    The one thing this cannot make deterministic is a request that itself
    takes longer than `CACHE_TTL_S`, which would expire the cache mid-flight.
    That is not a budget being asserted -- nothing here reads the clock -- and
    a request this far from 20 seconds has a different problem.
    """
    from guardian_hub import platform_settings

    _add_open_alerts(tid, 4)
    platform_settings.refresh()
    few = queries_for(client, path)
    _add_open_alerts(tid, 16, start=4)
    platform_settings.refresh()
    more = queries_for(client, path)
    return (more - few) / 16


def test_scoring_costs_a_fixed_number_of_statements_per_alert(client):
    tid = seeded_demo(client)
    assert _slope(client, tid) == 12


def test_the_needs_you_view_pays_the_same_slope_and_no_more(client):
    # Needs-you filters on the resolved remedy rather than on the key, so it
    # resolves every open alert too. It must not resolve them a third time.
    tid = seeded_demo(client)
    assert _slope(client, tid, "/api/v1/inbox?view=needs") == 12


def test_without_scoring_the_slope_is_the_read_paths_own(client, monkeypatch):
    """The other half of the measurement, so the number above is attributable.

    Without this, a future reader has one figure and no way to tell a rubric
    that got more expensive from a payload that did -- and the fix would be
    aimed at whichever of the two they guessed.
    """
    tid = seeded_demo(client)
    monkeypatch.setattr(remedy, "_apply_confidence", lambda *a, **k: None)
    assert _slope(client, tid) == 2
