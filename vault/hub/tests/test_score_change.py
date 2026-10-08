"""Why the score moved -- the sentence under the number.

The score has always been a figure, a grade and a trend line, none of which
answer the question an owner asks when it drops: what happened? Every scan
already stores its own scored breakdown, so the answer is a diff of two rows
that are already on disk.

The rules that matter here are about NOT saying something untrue: compare by
time rather than row order (the sensor sweeps every few minutes, so the
previous row is nearly always identical), and render nothing at all when
there is no scan old enough to compare against.
"""
import datetime as dt

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.payloads import scorechange
from guardian_hub.payloads.scorechange import _was_fragment, score_change


def _scan(db, tenant_id, *, hours_ago: float, total: int, deductions: list[dict]):
    ts = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)).isoformat()
    row = m.Scan(tenant_id=tenant_id, ts=ts, score=total,
                 raw={"score": {"total": total, "grade": "B", "deductions": deductions}})
    db.add(row)
    db.commit()
    return row


def _tenant(db, slug="sc"):
    t = m.Tenant(slug=slug, name="Score T")
    db.add(t)
    db.commit()
    return t


def _ded(category, points, why):
    return {"category": category, "points": points, "why": why}


# --------------------------------------------------------------------------
# Saying nothing rather than saying zero
# --------------------------------------------------------------------------

def test_nothing_is_reported_when_there_is_no_older_scan(client):
    db = SessionLocal()
    t = _tenant(db)
    now = _scan(db, t.id, hours_ago=0, total=80, deductions=[])
    assert score_change(db, t.id, now) is None
    db.close()


def test_a_scan_from_ten_minutes_ago_is_not_a_comparison(client):
    # The sensor sweeps every few minutes. Comparing against the previous row
    # would answer "no change" almost always, which is worse than no line.
    db = SessionLocal()
    t = _tenant(db, "sc-recent")
    _scan(db, t.id, hours_ago=0.16, total=80, deductions=[])
    now = _scan(db, t.id, hours_ago=0, total=75, deductions=[])
    assert score_change(db, t.id, now) is None
    db.close()


def test_no_current_scan_means_no_line(client):
    db = SessionLocal()
    t = _tenant(db, "sc-none")
    assert score_change(db, t.id, None) is None
    db.close()


# --------------------------------------------------------------------------
# The diff
# --------------------------------------------------------------------------

def test_a_new_finding_is_named_and_the_delta_is_signed(client):
    db = SessionLocal()
    t = _tenant(db, "sc-new")
    _scan(db, t.id, hours_ago=25, total=90, deductions=[])
    now = _scan(db, t.id, hours_ago=0, total=87,
                deductions=[_ded("Exposure", 3, "extra internet ports on safehome")])
    out = score_change(db, t.id, now)
    assert out.delta == -3 and out.was == 90
    assert [d.why for d in out.appeared] == ["extra internet ports on safehome"]
    assert out.cleared == [] and out.changed == []
    assert "Down 3 since yesterday" in out.summary
    assert "extra internet ports on safehome is new" in out.summary
    db.close()


def test_a_finding_that_went_away_is_named_too(client):
    db = SessionLocal()
    t = _tenant(db, "sc-gone")
    _scan(db, t.id, hours_ago=25, total=87,
          deductions=[_ded("Exposure", 3, "extra internet ports on safehome")])
    now = _scan(db, t.id, hours_ago=0, total=90, deductions=[])
    out = score_change(db, t.id, now)
    assert out.delta == 3
    assert [d.why for d in out.cleared] == ["extra internet ports on safehome"]
    assert "Up 3 since yesterday" in out.summary and "is gone" in out.summary
    db.close()


def test_a_count_that_moved_is_one_change_not_two_events(client):
    # The bug this exists to prevent: keying a deduction on its exact text
    # makes "4 unverified devices" -> "3 unverified devices" read as one
    # finding appearing and a different one disappearing.
    db = SessionLocal()
    t = _tenant(db, "sc-moved")
    _scan(db, t.id, hours_ago=25, total=76,
          deductions=[_ded("Coverage", 12, "4 unverified device(s) on the Wi-Fi")])
    now = _scan(db, t.id, hours_ago=0, total=79,
                deductions=[_ded("Coverage", 9, "3 unverified device(s) on the Wi-Fi")])
    out = score_change(db, t.id, now)

    assert out.appeared == [] and out.cleared == []
    assert len(out.changed) == 1
    shift = out.changed[0]
    assert shift.category == "Coverage"
    assert (shift.was_points, shift.now_points) == (12, 9)
    # And it reads as a correction rather than two long strings to diff by eye.
    assert "3 unverified device(s) on the Wi-Fi (was 4)" in out.summary
    db.close()


def test_two_different_findings_in_one_category_do_not_cancel_out(client):
    db = SessionLocal()
    t = _tenant(db, "sc-two")
    _scan(db, t.id, hours_ago=25, total=90,
          deductions=[_ded("Exposure", 3, "port 8443 open on safehome")])
    now = _scan(db, t.id, hours_ago=0, total=84,
                deductions=[_ded("Exposure", 3, "port 8443 open on safehome"),
                            _ded("Exposure", 6, "port 3389 open on prod")])
    out = score_change(db, t.id, now)
    assert [d.why for d in out.appeared] == ["port 3389 open on prod"]
    assert out.cleared == [] and out.changed == []
    db.close()


def test_an_unchanged_day_says_so_plainly(client):
    db = SessionLocal()
    t = _tenant(db, "sc-same")
    d = [_ded("Coverage", 9, "3 unverified device(s) on the Wi-Fi")]
    _scan(db, t.id, hours_ago=25, total=79, deductions=d)
    now = _scan(db, t.id, hours_ago=0, total=79, deductions=list(d))
    out = score_change(db, t.id, now)
    assert out.delta == 0 and out.summary == "Unchanged since yesterday."
    db.close()


def test_it_compares_against_the_newest_scan_old_enough(client):
    # Not the oldest on record: "since yesterday" means yesterday, so a week
    # of history must not drag the comparison back a week.
    db = SessionLocal()
    t = _tenant(db, "sc-pick")
    _scan(db, t.id, hours_ago=120, total=50, deductions=[])
    _scan(db, t.id, hours_ago=26, total=85, deductions=[])
    now = _scan(db, t.id, hours_ago=0, total=88, deductions=[])
    out = score_change(db, t.id, now)
    assert out.was == 85 and out.delta == 3
    db.close()


# --------------------------------------------------------------------------
# "(was 4)" -- only when it is honest
# --------------------------------------------------------------------------

def test_a_substituted_value_is_trimmed_to_just_the_difference():
    assert _was_fragment("3 unverified device(s) on the Wi-Fi",
                         "4 unverified device(s) on the Wi-Fi") == "4"


def test_a_dropped_clause_keeps_the_full_text():
    # "extra ports on safehome (was 1 risky LAN service)" reads as if the
    # ports used to BE a LAN service. Only a substitution may be trimmed.
    now = "extra internet ports on safehome.example.com"
    was = "extra internet ports on safehome.example.com; 1 risky LAN service(s)"
    assert _was_fragment(now, was) == was


def test_two_unrelated_texts_are_not_reduced_to_a_fragment():
    assert _was_fragment("disk 91% full on prod", "reboot pending on clinic-nas") == \
        "reboot pending on clinic-nas"


def test_a_missing_old_text_yields_nothing_to_show():
    assert _was_fragment("anything", "") == ""


# --------------------------------------------------------------------------
# It rides on the overview payload
# --------------------------------------------------------------------------

def test_the_overview_carries_it_and_tolerates_its_absence(client):
    from tests.test_seed_helpers import seeded_demo
    seeded_demo(client)
    body = client.get("/api/v1/overview").json()
    assert "score_change" in body, "the payload must always carry the field"
    # The seeded demo tenant may or may not have a day of history; either way
    # the field is present and is null or a well-formed object.
    if body["score_change"] is not None:
        assert "summary" in body["score_change"]


# --------------------------------------------------------------------------
# Saying the movement the shortest true way
# --------------------------------------------------------------------------

def test_a_clause_appearing_is_reported_as_that_clause(client):
    # The live case. Exposure went from "extra internet ports on safehome" to
    # "extra internet ports on safehome; 1 risky LAN service(s)". Naming both
    # in full makes the reader diff two sentences by eye; the news is the
    # clause that arrived.
    db = SessionLocal()
    t = _tenant(db, "sc-added")
    _scan(db, t.id, hours_ago=25, total=88,
          deductions=[_ded("Exposure", 3, "extra internet ports on safehome.example.com")])
    now = _scan(db, t.id, hours_ago=0, total=86,
                deductions=[_ded("Exposure", 5,
                                 "extra internet ports on safehome.example.com; 1 risky LAN service(s)")])
    out = score_change(db, t.id, now)
    assert "1 risky LAN service(s) is new" in out.summary
    # And it does not recite the unchanged half back at the reader.
    assert "(was extra internet ports" not in out.summary
    db.close()


def test_a_clause_disappearing_is_reported_as_that_clause(client):
    db = SessionLocal()
    t = _tenant(db, "sc-removed")
    _scan(db, t.id, hours_ago=25, total=86,
          deductions=[_ded("Exposure", 5,
                           "extra internet ports on safehome.example.com; 1 risky LAN service(s)")])
    now = _scan(db, t.id, hours_ago=0, total=88,
                deductions=[_ded("Exposure", 3, "extra internet ports on safehome.example.com")])
    out = score_change(db, t.id, now)
    assert "1 risky LAN service(s) is gone" in out.summary
    db.close()


def test_two_findings_too_different_to_be_an_edit_are_named_in_full(client):
    db = SessionLocal()
    t = _tenant(db, "sc-unrelated")
    _scan(db, t.id, hours_ago=25, total=90,
          deductions=[_ded("Patch status", 3, "reboot pending on clinic-nas")])
    now = _scan(db, t.id, hours_ago=0, total=85,
                deductions=[_ded("Patch status", 8, "disk 91% full on prod")])
    out = score_change(db, t.id, now)
    assert "disk 91% full on prod" in out.summary
    assert "reboot pending on clinic-nas" in out.summary
    db.close()


# --------------------------------------------------------------------------
# An unchanged day still explains something
# --------------------------------------------------------------------------

def test_an_unchanged_day_says_when_the_score_last_moved_and_why(client):
    db = SessionLocal()
    t = _tenant(db, "sc-lastmoved")
    old = [_ded("Coverage", 12, "4 unverified device(s) on the Wi-Fi")]
    new = [_ded("Coverage", 9, "3 unverified device(s) on the Wi-Fi")]
    _scan(db, t.id, hours_ago=80, total=76, deductions=old)
    _scan(db, t.id, hours_ago=70, total=79, deductions=new)   # the move, ~3 days ago
    _scan(db, t.id, hours_ago=25, total=79, deductions=list(new))
    now = _scan(db, t.id, hours_ago=0, total=79, deductions=list(new))
    out = score_change(db, t.id, now)
    assert out.delta == 0
    assert out.last_moved is not None and out.last_moved.delta == 3
    assert out.summary.startswith("Unchanged since yesterday. Last moved 3 days ago, up 3 — ")
    assert "3 unverified device(s) on the Wi-Fi (was 4)" in out.summary
    db.close()


def test_a_move_inside_the_window_is_not_repeated_as_last_moved(client):
    # If it moved 10 hours ago the day's own diff already says so; the tail
    # must not restate it.
    db = SessionLocal()
    t = _tenant(db, "sc-inside")
    _scan(db, t.id, hours_ago=25, total=90, deductions=[])
    _scan(db, t.id, hours_ago=10, total=87, deductions=[_ded("Exposure", 3, "port open")])
    now = _scan(db, t.id, hours_ago=0, total=87, deductions=[_ded("Exposure", 3, "port open")])
    out = score_change(db, t.id, now)
    assert out.delta == -3 and out.last_moved is None
    assert "Last moved" not in out.summary
    db.close()


def test_never_moved_means_no_tail(client):
    db = SessionLocal()
    t = _tenant(db, "sc-never")
    _scan(db, t.id, hours_ago=50, total=90, deductions=[])
    _scan(db, t.id, hours_ago=25, total=90, deductions=[])
    now = _scan(db, t.id, hours_ago=0, total=90, deductions=[])
    out = score_change(db, t.id, now)
    assert out.summary == "Unchanged since yesterday." and out.last_moved is None
    db.close()
