"""Time-rebasing loader for the captured scan fixtures.

`tests/fixtures/last.json` and `tests/fixtures/state.json` are a real capture
of one network, anchored to a fixed instant (`last["ts"]`, captured
2026-09-09T18:30:26-04:00). Every hub assertion about a "last 24 hours"
window therefore only holds while that instant is still inside the window --
so the suite went red purely because time passed, which is the fastest way to
teach everyone to ignore red.

This module loads the same payloads through a rebase: every timestamp in them
is shifted by ONE uniform delta, `now - last["ts"]`, so the capture always
reads as "a scan that just happened" while the internal spacing between
events, history lines, DNS queries, alerts, fixes and incidents is preserved
exactly. Several suites assert on ordering and on the gaps between rows, so a
naive "set everything to now" would break far more than it fixed.

The delta is computed ONCE, at import, and reused for every load in the
process. That is load-bearing: `seed.seed_demo` is documented as idempotent
(`tests/test_seed.py` re-seeds and asserts identical row counts), which only
holds if two loads inside one run produce byte-identical payloads.

What is shifted, as found by enumerating the two files (not guessed at):

* Full timestamps, at any depth, in three live offset shapes -- `-04:00`
  (most), `-0400` (`last.dns_filter.status.ts`, `state.acked.*.ts`) and naive
  (`last.dns_filter.status.lists.*.ts`). Each string's own shape is preserved
  on the way out -- separator, seconds/fraction precision and offset text --
  so a rebased payload still round-trips through `ingest.store_scan`
  unchanged. Keys carrying them include `ts`, `first_seen`, `last_seen`,
  `opened`, `started`, `resolved_at`, `last_run`, plus whole timestamp-valued
  maps (`state.fix_cooldown`, `state.notified`, and
  `state.lan.known_macs.*.ports`, which is keyed by PORT NUMBER and valued by
  a timestamp) and timestamp-valued lists (`state.lan.transitions.*`).
* Date-only strings: `state.digest_date`, `state.domains.*.expires` (a future
  expiry -- shifting it keeps "days left" stable instead of letting it drift
  toward zero), and `state.attacker_hist.*[]`.
* Dates embedded in dict KEYS: `state.incidents` is keyed by a day bucket
  (`"host:demo|2026-09-09"`, `"home|2026-09-08"`, `"home|2026-09-09"`). Only
  the date portion is rewritten; the rest of the key is left alone. Shifting
  all dates by the same delta is injective, so two adjacent day buckets can
  never collapse into one and silently drop a row.

Everything else is left untouched: a string only shifts if it matches a
timestamp or date shape end-to-end, and a key only shifts the `YYYY-MM-DD`
run inside it, so MACs, IPs, alert keys, versions and free text are inert.

Both files are rebased by the delta taken from `last["ts"]`, never a
per-file delta: `state.last_run` is the same instant as `last.ts` and
`state.lan.known_macs.*.last_seen` is pinned to it, so a per-file delta would
desynchronise device freshness from the scan that observed it.
"""

import copy
import datetime as dt
import json
import pathlib
import re
import tempfile

FX = pathlib.Path(__file__).resolve().parent / "fixtures"

# A full timestamp: date, separator, HH:MM[:SS[.ffffff]], optional offset in
# any of the three shapes present in the fixtures. Anchored, so only a string
# that is *entirely* a timestamp is ever rewritten.
_TS = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})(?P<sep>[T ])"
    r"(?P<time>\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?)"
    r"(?P<off>Z|[+-]\d{2}:\d{2}|[+-]\d{4})?$"
)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_IN_KEY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _shift_ts(value: str, delta: dt.timedelta) -> str:
    """Shift one full timestamp, preserving its exact rendering.

    The delta is applied to the wall-clock reading and the offset text is
    carried across verbatim (rather than re-derived), which keeps the
    tenant-offset inference in `scheduler._tenant_offset` -- which reads the
    offset back off the newest scan -- seeing the same offset it always saw.
    Every offset in these fixtures is -04:00, so a wall-clock shift and an
    instant shift are the same shift.
    """
    m = _TS.match(value)
    time = m.group("time")
    naive = dt.datetime.fromisoformat(f"{m.group('date')}T{time}") + delta
    if "." in time:
        digits = len(time.split(".")[1])
        rendered = naive.strftime("%H:%M:%S.%f")[: len("HH:MM:SS.") + digits]
    elif time.count(":") == 2:
        rendered = naive.strftime("%H:%M:%S")
    else:
        rendered = naive.strftime("%H:%M")
    return f"{naive.date().isoformat()}{m.group('sep')}{rendered}{m.group('off') or ''}"


def _shift_date(value: str, delta: dt.timedelta) -> str:
    """Shift one date-only string by the same delta, as a date."""
    return (dt.datetime.fromisoformat(value) + delta).date().isoformat()


def _shift_key(key, delta: dt.timedelta):
    """Rewrite any `YYYY-MM-DD` run inside a dict key, leaving the rest of
    the key (a host name, an `ip`, a `|` separator) exactly as it was."""
    if not isinstance(key, str):
        return key
    return _DATE_IN_KEY.sub(lambda m: _shift_date(m.group(0), delta), key)


def rebase(obj, delta: dt.timedelta):
    """Return a copy of `obj` with every timestamp shifted by `delta`.

    Pure: `obj` is never mutated. Dict insertion order is preserved, so a
    rebased payload serialises in the same order as the original.
    """
    if isinstance(obj, dict):
        return {_shift_key(k, delta): rebase(v, delta) for k, v in obj.items()}
    if isinstance(obj, list):
        return [rebase(v, delta) for v in obj]
    if isinstance(obj, str):
        if _TS.match(obj):
            return _shift_ts(obj, delta)
        if _DATE.match(obj):
            return _shift_date(obj, delta)
        return obj
    return obj


def _read(name: str) -> dict:
    return json.loads((FX / name).read_text())


def anchor_of(last: dict) -> dt.datetime:
    """The instant a `last.json` payload was captured, as an aware datetime."""
    m = _TS.match(last["ts"])
    naive = dt.datetime.fromisoformat(f"{m.group('date')}T{m.group('time')}")
    off = m.group("off")
    if off in (None, "Z"):
        return naive.replace(tzinfo=dt.timezone.utc)
    sign = 1 if off[0] == "+" else -1
    off = off[1:].replace(":", "")
    return naive.replace(tzinfo=dt.timezone(
        sign * dt.timedelta(hours=int(off[:2]), minutes=int(off[2:]))))


# Computed from last.json's own ts -- see the module docstring on why this
# must not be recomputed per LOAD, and `refresh` below on why it is
# nonetheless recomputed between tests.
_RAW_LAST = _read("last.json")
NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
DELTA = NOW - anchor_of(_RAW_LAST)

_CACHE: dict[str, dict] = {}
_DIR: list[pathlib.Path] = []


def raw(name: str = "last.json") -> dict:
    """The fixture exactly as captured, on its original fixed instant. For
    the few tests that assert on the capture itself rather than on a window."""
    return _read(name)


def load(name: str = "last.json") -> dict:
    """A rebased copy of one fixture file, shifted onto the current time."""
    if name not in _CACHE:
        _CACHE[name] = rebase(_read(name), DELTA)
    return copy.deepcopy(_CACHE[name])


def load_last() -> dict:
    return load("last.json")


def load_state() -> dict:
    return load("state.json")


def load_pair() -> tuple[dict, dict]:
    """`(last, state)` rebased together by the one delta."""
    return load_last(), load_state()


def ingest_body(**extra) -> dict:
    """A `POST /api/v1/sensor/ingest` body carrying the rebased pair, with
    the empty row lists most callers pass. `extra` overrides any of them."""
    last, state = load_pair()
    body = {"last": last, "state": state, "events": [], "history": [],
            "fixes": [], "dns_queries": []}
    body.update(extra)
    return body


def rebased_dir() -> pathlib.Path:
    """A directory holding the rebased `last.json`/`state.json`, for the code
    that takes a fixtures *path* (`seed.seed_demo(fixtures=...)`) rather than
    a loaded payload. Created once per process, for the same idempotency
    reason the delta is."""
    if not _DIR:
        d = pathlib.Path(tempfile.mkdtemp(prefix="guardian-fx-rebased-"))
        _DIR.append(d)
        _write_dir()
    return _DIR[0]


def _write_dir() -> None:
    for d in _DIR:
        for name in ("last.json", "state.json"):
            (d / name).write_text(json.dumps(load(name)))


def refresh() -> None:
    """Recompute the delta against the current clock. Called once per test by
    `conftest`'s `client` fixture, never inside one.

    The delta is fixed at import so that two loads in one test are
    byte-identical -- `seed_demo`'s idempotency rests on that, and
    `test_loads_are_stable_within_one_process` guards it. But a delta fixed at
    import also means the promise this module exists to make ("a scan that
    just happened") decays for the length of the run, and it does not take
    long: `_util.effective_presence` reads an ONLINE device last seen more
    than 900 seconds ago as UNKNOWN, so about fifteen minutes in, every device
    in the seeded capture reads unknown. The full hub suite takes an hour.
    That is how `test_device_tiles` and `test_read_write::test_fix_queues`
    came to fail in a full run and pass on their own -- not pollution, and not
    flakiness, just a capture that had gone stale by the time their turn came.

    Refreshing BETWEEN tests keeps both properties: identical loads inside a
    test, and a capture that is never more than one test old. The on-disk copy
    is rewritten in place rather than replaced, so `rebased_dir()` keeps its
    identity (and one process does not leave a thousand temp directories
    behind) while `seed_demo(fixtures=...)`, which reads the directory rather
    than `load`, sees the same instant everything else does.
    """
    global NOW, DELTA
    NOW = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    DELTA = NOW - anchor_of(_RAW_LAST)
    _CACHE.clear()
    _write_dir()
