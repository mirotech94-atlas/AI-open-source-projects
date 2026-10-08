"""`tests.scanfixture` -- the rebasing fixture loader every payload suite now
loads the captured scan through.

The loader exists so the suite stops going red purely because the capture's
fixed instant aged out of a 24-hour window. These tests guard the four
properties the rest of the suite silently depends on: the shift is ONE uniform
delta, relative order and gaps survive it, nothing that isn't a timestamp is
touched, and a rebased payload still ingests.
"""

import datetime as dt
import json

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from tests import scanfixture as sf
from tests.test_sensor_api import enrol
from tests.test_tenants import make_super


def _timestamps(obj, path=""):
    """Every full timestamp in `obj`, as (path, string) in document order."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            out += _timestamps(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            out += _timestamps(v, f"{path}[{i}]")
    elif isinstance(obj, str) and sf._TS.match(obj):
        out.append((path, obj))
    return out


def _skeleton(obj):
    """`obj` with every timestamp/date string replaced by a marker, so two
    payloads compare equal iff they differ ONLY in their timestamps."""
    if isinstance(obj, dict):
        return {("<date-key>" if sf._DATE_IN_KEY.search(str(k)) else k): _skeleton(v)
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_skeleton(v) for v in obj]
    if isinstance(obj, str):
        if sf._TS.match(obj):
            return "<ts>"
        if sf._DATE.match(obj):
            return "<date>"
    return obj


def test_the_delta_is_uniform_across_both_files():
    """One shift, not a per-field or per-file one: `state.last_run` is the
    same instant as `last.ts` and `known_macs.*.last_seen` is pinned to it,
    so anything but a single delta desynchronises device freshness from the
    scan that saw it."""
    seen = set()
    for name in ("last.json", "state.json"):
        before, after = _timestamps(sf.raw(name)), _timestamps(sf.load(name))
        # paths compared with any date inside a KEY masked -- day-bucket keys
        # (`state.incidents`) shift too, by design
        def mask(p):
            return sf._DATE_IN_KEY.sub("<date>", p)
        assert [mask(p) for p, _ in before] == [mask(p) for p, _ in after]
        assert before, name
        for (path, old), (_, new) in zip(before, after):
            seen.add(sf.anchor_of({"ts": new}) - sf.anchor_of({"ts": old}))
    assert seen == {sf.DELTA}, seen


def test_the_scan_now_reads_as_having_just_happened():
    """Asserted against the module's own anchor, NOT against elapsed wall time.

    `NOW`/`DELTA` are fixed when `tests.scanfixture` is imported, i.e. during
    collection, so a bound like "less than five minutes old" would really be
    measuring how long the suite had been running before this file's turn --
    passing in a small chunk and failing in a full run, and flipping whenever
    a file is added ahead of it. That is precisely the failure mode this whole
    task exists to remove, so it must not be reintroduced here.
    """
    last, state = sf.load_pair()
    assert sf.anchor_of(last) == sf.NOW
    assert state["last_run"] == last["ts"]
    # a sanity bound wide enough to be about the rebase rather than the clock
    assert dt.datetime.now(dt.timezone.utc) - sf.anchor_of(last) < dt.timedelta(hours=2)


def test_order_and_gaps_survive_the_shift():
    """Several suites assert on ordering and on the spacing between rows, so
    a "set everything to now" rebase would break more than it fixed."""
    for name in ("last.json", "state.json"):
        before = [sf.anchor_of({"ts": t}) for _, t in _timestamps(sf.raw(name))]
        after = [sf.anchor_of({"ts": t}) for _, t in _timestamps(sf.load(name))]
        assert [b - before[0] for b in before] == [a - after[0] for a in after]
        # and the sort order of the whole set is identical
        assert ([i for i, _ in sorted(enumerate(before), key=lambda x: x[1])]
                == [i for i, _ in sorted(enumerate(after), key=lambda x: x[1])])


def test_nothing_that_is_not_a_timestamp_is_touched():
    for name in ("last.json", "state.json"):
        assert _skeleton(sf.raw(name)) == _skeleton(sf.load(name))


def test_each_timestamps_own_rendering_is_preserved():
    """Three offset shapes are live in these fixtures -- `-04:00`, `-0400`
    and naive -- and a rebased payload has to round-trip through ingest
    unchanged, so each string keeps its own format rather than being
    normalised by `isoformat()`."""
    last, state = sf.load_pair()
    assert last["dns_filter"]["status"]["ts"].endswith("-0400")
    assert sf._TS.match(last["dns_filter"]["status"]["lists"]["urlhaus"]["ts"]).group("off") is None
    assert last["ts"].endswith("-04:00")
    assert state["acked"]["reboot:demo"]["ts"].endswith("-0400")
    for name in ("last.json", "state.json"):
        for (_, old), (_, new) in zip(_timestamps(sf.raw(name)), _timestamps(sf.load(name))):
            a, b = sf._TS.match(old), sf._TS.match(new)
            assert a.group("sep") == b.group("sep")
            assert a.group("off") == b.group("off")
            assert len(a.group("time")) == len(b.group("time"))


def test_day_bucket_keys_shift_without_collapsing():
    """`state.incidents` is keyed by a day bucket (`"home|2026-09-08"`,
    `"home|2026-09-09"`). Only the date portion moves, and because every date
    moves by the same delta two adjacent buckets can never merge into one and
    silently drop an incident."""
    raw, new = sf.raw("state.json")["incidents"], sf.load_state()["incidents"]
    assert len(new) == len(raw) == 3
    for old_key, new_key in zip(raw, new):
        prefix, old_day = old_key.rsplit("|", 1)
        assert new_key.startswith(prefix + "|")
        old_d = dt.date.fromisoformat(old_day)
        new_d = dt.date.fromisoformat(new_key.rsplit("|", 1)[1])
        assert new_d == (dt.datetime.combine(old_d, dt.time()) + sf.DELTA).date()
    assert sf.load_state()["digest_date"] != sf.raw("state.json")["digest_date"] or \
        sf.DELTA < dt.timedelta(days=1)


def test_loads_are_stable_within_one_process():
    """`seed.seed_demo` is documented as idempotent and `test_seed` re-seeds
    and asserts identical row counts -- which only holds if two loads in one
    run produce byte-identical payloads. So the delta is computed once, at
    import, never per call."""
    assert json.dumps(sf.load_last()) == json.dumps(sf.load_last())
    assert sf.rebased_dir() == sf.rebased_dir()
    for name in ("last.json", "state.json"):
        assert json.loads((sf.rebased_dir() / name).read_text()) == sf.load(name)


def test_the_loader_hands_back_a_copy_callers_may_mutate():
    first = sf.load_last()
    first["ts"] = "mutated"
    first.pop("lan")
    assert sf.load_last()["ts"] != "mutated" and "lan" in sf.load_last()


def test_raw_still_serves_the_original_fixed_instant():
    assert sf.raw("last.json")["ts"] == "2026-09-09T18:30:26-04:00"


def test_a_rebased_payload_still_ingests(client):
    tid = make_super(client)
    H = {"Authorization": f"Bearer {enrol(client, tid)}"}
    last, state = sf.load_pair()
    r = client.post("/api/v1/sensor/ingest", json=sf.ingest_body(), headers=H)
    assert r.status_code == 200, r.text
    db = SessionLocal()
    assert db.query(m.Scan).filter_by(tenant_id=tid).count() == 1
    assert db.query(m.Alert).filter_by(tenant_id=tid).count() == len(state["alerts_db"])
    assert (db.query(m.Device).filter_by(tenant_id=tid).count()
            == len(state["lan"]["known_macs"]))
    # and the stored scan is inside a 24h window, which is the whole point
    scan = db.query(m.Scan).filter_by(tenant_id=tid).one()
    assert dt.datetime.now(dt.timezone.utc) - sf.anchor_of({"ts": scan.ts}) \
        < dt.timedelta(hours=24)
    assert scan.ts == last["ts"]
