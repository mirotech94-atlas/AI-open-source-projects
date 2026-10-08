"""The factors, against real rows.

Each test writes only the rows its own factor reads, so a failure names the
factor rather than the fixture.
"""
import datetime as dt

from guardian_hub import confidence as c, models as m
from guardian_hub.db import SessionLocal
from guardian_hub.scans import latest_scan, parse_ts
from tests.test_seed_helpers import seeded_demo

NOW = dt.datetime(2026, 9, 23, 12, 0, tzinfo=dt.timezone.utc)


def _fix(db, tid, *, host="prod", action="security_updates", ok=True,
         verified=True, ts=None, by="auto"):
    db.add(m.Fix(tenant_id=tid, host=host, action=action, ok=ok, by=by,
                 ts=(ts or (NOW - dt.timedelta(days=1))).isoformat(timespec="seconds"),
                 verified_at=(NOW - dt.timedelta(days=1)) if verified else None))


def _assess(tid, **kw):
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        args = dict(alert_key="pkg:prod:security", host="prod",
                    action="security_updates", tier=2, now=NOW)
        args.update(kw)
        return c.assess(db, tenant, **args)
    finally:
        db.close()


def _factor(conf, name):
    return next(f for f in conf.factors if f.name == name)


def test_every_factor_is_returned_even_when_it_found_nothing(client):
    tid = seeded_demo(client)
    conf = _assess(tid)
    names = {f.name for f in conf.factors}
    assert names == {"host_history", "held", "verified", "undone",
                     "reversible", "sensor_fresh"}


def test_a_host_repaired_before_scores_higher_than_one_never_touched(client):
    tid = seeded_demo(client)
    cold = _assess(tid).score
    db = SessionLocal()
    try:
        for _ in range(4):
            _fix(db, tid)
        db.commit()
    finally:
        db.close()
    warm = _assess(tid)
    assert warm.score > cold
    assert "4" in _factor(warm, "host_history").answer


def test_an_alert_that_keeps_coming_back_costs_confidence(client):
    # `held` no longer reads `Alert.count` (the sensor bumps that every scan
    # a condition persists, which pinned the old version at its floor for
    # every genuine alert -- see confidence.assess's own comment). Instead it
    # compares the alert's `last_seen` against the newest SUCCESSFUL repair of
    # this exact host/action: a repair, then an alert still seen after it ran,
    # is the "did not hold" case this test has to set up.
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        for _ in range(4):
            _fix(db, tid)  # ts defaults to NOW - 1 day
        db.commit()
    finally:
        db.close()
    quiet = _assess(tid).score
    db = SessionLocal()
    try:
        db.add(m.Alert(tenant_id=tid, key="pkg:prod:security", sev="WARN",
                       msg="security updates pending",
                       first_seen=NOW.isoformat(timespec="seconds"),
                       last_seen=NOW.isoformat(timespec="seconds"),  # after the fix's ts
                       count=1, status="open"))
        db.commit()
    finally:
        db.close()
    noisy = _assess(tid)
    assert noisy.score < quiet
    assert _factor(noisy, "held").weight < 0


def test_a_repair_no_later_evidence_confirmed_is_worth_less_than_a_verified_one(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        for _ in range(3):
            _fix(db, tid, verified=False)
        db.commit()
    finally:
        db.close()
    unverified = _assess(tid)
    assert _factor(unverified, "verified").weight <= 0


def test_a_person_reopening_the_alert_marks_the_repair_undone(client):
    # The old `undone` factor searched `Edit.path.like("%undo%")`, a path
    # Vault never writes -- it could never fire. The real signal is a
    # human reopening THIS alert (`who != "system"`, since ingest.py writes
    # its own "system" reopen when a condition simply re-fires, and that is
    # not a person overruling Vault).
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        db.add(m.Edit(tenant_id=tid, who="demoadmin", path="/api/alert",
                       body={"key": "pkg:prod:security", "action": "reopen", "hours": None}))
        db.commit()
    finally:
        db.close()
    conf = _assess(tid)
    assert _factor(conf, "undone").weight < 0


def test_a_system_reopen_does_not_count_as_a_person_undoing_it(client):
    # The disambiguating case for the amendment above: `ingest.py`'s own
    # re-fire reopen (`who="system"`) is ordinary operation, not a human
    # overruling Vault, and must not trip the factor.
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        db.add(m.Edit(tenant_id=tid, who="system", path="/api/alert",
                       body={"key": "pkg:prod:security", "action": "reopen", "hours": 0}))
        db.commit()
    finally:
        db.close()
    conf = _assess(tid)
    assert _factor(conf, "undone").weight == 0


def test_a_tier_three_action_is_marked_not_reversible(client):
    tid = seeded_demo(client)
    conf = _assess(tid, action="reboot", tier=3)
    assert _factor(conf, "reversible").weight < 0
    assert conf.verdict == "approval"


def test_a_stale_sensor_costs_confidence_because_nothing_can_be_verified(client):
    # `seeded_demo` rebases the captured scan onto REAL now (`scanfixture`),
    # so a hardcoded literal here would make `now - seen` wander with the
    # time of day the suite happens to run -- exactly the class of bug
    # CLAUDE.md's Tests section calls out for `scanfixture`/`effective_presence`.
    # The base instant for "fresh" has to come from the seeded scan itself.
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        base = parse_ts(latest_scan(db, tid).ts)
    finally:
        db.close()
    fresh = _assess(tid, now=base)
    stale = _assess(tid, now=base + dt.timedelta(hours=6))
    assert stale.score < fresh.score
    assert _factor(stale, "sensor_fresh").weight < 0


def test_confirmed_repairs_counts_only_fixes_later_confirmed_to_hold(client):
    # `Fix.ok=True` means the command exited zero; `verified_at` is only set
    # by `verification.reconcile`, and only once a LATER scan showed the
    # finding actually cleared. `assess`'s `verified` factor and `gate`'s
    # promotion sentence both read this exact count (see
    # `confidence.confirmed_repairs`'s own docstring for why there is only
    # one function), so a successful-but-unconfirmed fix must not count.
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        _fix(db, tid, ok=True, verified=False)
        _fix(db, tid, ok=True, verified=True)
        db.commit()
    finally:
        db.close()
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        n = c.confirmed_repairs(db, tenant, host="prod", action="security_updates")
    finally:
        db.close()
    assert n == 1


def test_no_factor_claims_a_history_the_host_does_not_have(client):
    """Each factor's prose has to agree with its own answer, not only with the
    arithmetic.

    Nothing anywhere checked a factor's `why` against its own state, and one
    of them was lying because of it: with no fixes at all, `verified` answered
    "nothing to confirm" and explained itself with "Vault ran the repair
    but never saw it take effect" -- about a repair that never ran.
    `remedy._confidence_why` composes the sentence a customer reads out of
    exactly these strings, so the falsehood was reachable from the dashboard.

    The weight was correct throughout (0, not -10), which is why no
    score-based test could have noticed. This asserts the prose: on a host
    with no history at all, no factor may claim Vault did something there.
    The phrases are checked rather than the whole sentence so a rewording does
    not fail the test, but a claim of past action does.
    """
    tid = seeded_demo(client)          # `host="prod"` has no fixes at all
    conf = _assess(tid)
    for f in conf.factors:
        assert f.why, f.name
        assert f.question and f.answer, f.name
    verified = _factor(conf, "verified")
    assert verified.answer == "nothing to confirm"
    assert verified.weight == 0
    assert "nothing to confirm" in verified.why
    # The specific claims that would be false with no history behind them.
    for f in conf.factors:
        low = f.why.lower()
        assert "guardian ran the repair" not in low, f.name
        assert "it has done this here before" not in low, f.name


def test_no_factor_prints_the_catalogue_key_or_the_alert_key(client):
    """The drawer is customer-facing prose, and two identifiers do not belong
    in it: `action` is a catalogue key (`security_updates`) meant for
    `m.Fix.action` and `FIX_LABELS`'s own keys, and `alert_key` is an internal
    join key (`pkg:prod:security`) -- neither is a word, and a customer who
    reads either in a factor's `question`/`answer` is reading a leak, not an
    explanation.

    `host_history`, `held` and `reversible` used to interpolate `action`
    directly and `undone` used to print `alert_key` verbatim -- this is the
    regression guard that would have caught all four the day they shipped,
    since nothing else in this file or in `test_confidence_remedy.py` asserts
    on a factor's prose closely enough to notice an identifier riding inside
    it. Both leaks were found by reading the drawer in a browser, which is the
    only reason to think a fifth would be found the same slow way.

    The second draft's fix was to pass `FIX_LABELS`'s wording down for the
    questions to interpolate. That would pass this test and still read badly
    ("Has Vault run install pending security updates on records
    successfully before?"), so the assertion below is deliberately not the
    whole requirement: the questions name no repair at all, because the row
    that opens the drawer already named it. Nothing can assert that for us.
    """
    tid = seeded_demo(client)
    action = "security_updates"
    alert_key = "pkg:prod:security"

    conf = _assess(tid, action=action, alert_key=alert_key)
    for f in conf.factors:
        text = f"{f.question} {f.answer}"
        assert action not in text, (f.name, text)
        assert alert_key not in text, (f.name, text)
        # The underscore-stripped form too: an earlier draft defaulted to it,
        # so a revert to `action.replace("_", " ")` would otherwise slip past.
        assert action.replace("_", " ") not in text, (f.name, text)


def test_the_sensor_freshness_bar_is_the_tenants_own_threshold(client):
    """The factor reads `config.thresholds.sensor_silent_min`, not a constant.

    This used to be a flat 30 minutes for every tenant, with a comment
    claiming that past it `scheduler._watch_sensor` is "already raising" --
    which is not a relationship the two have. The watchdog's bound is the
    tenant's own `sensor_silent_min` (default 25, floored at 10, uncapped), so
    a tenant on the default had a 25-30 minute window where `sensor:silent`
    was open and the rubric still scored the sensor fresh and said "Vault
    will see whether this worked", and a tenant who raised the knob got the
    opposite.

    The two tenants below are the same estate seen through two thresholds, and
    the clock comes from the seeded scan rather than a literal: the demo
    capture is rebased onto the current time per test (CLAUDE.md § Tests), so
    an absolute `NOW` would make "20 minutes after the scan" mean something
    different depending on the hour.
    """
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        base = parse_ts(latest_scan(db, tid).ts)
    finally:
        db.close()
    at = base + dt.timedelta(minutes=20)

    # Default: nothing configured, so the rubric's own fallback of 30 minutes.
    assert _factor(_assess(tid, now=at), "sensor_fresh").weight > 0

    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        tenant.config = {**(tenant.config or {}), "thresholds": {"sensor_silent_min": 15}}
        db.commit()
    finally:
        db.close()
    # 20 minutes is past a tenant who asked to be told at 15, so the factor
    # now agrees with the alert the watchdog would already have raised.
    stale = _factor(_assess(tid, now=at), "sensor_fresh")
    assert stale.weight < 0
    # The question names the bar it applied, so the owner can see whose number
    # it is rather than having to trust that it used theirs.
    assert "15 min" in stale.question


def test_a_tenant_cannot_configure_the_freshness_bar_below_the_watchdogs_floor(client):
    # `scheduler.MIN_SILENT_MIN` exists because below one push interval the
    # watchdog would alarm on ordinary jitter. The rubric imports the same
    # floor rather than its own, so a tenant cannot make Vault distrust
    # every scan it takes.
    from guardian_hub.scheduler import MIN_SILENT_MIN

    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        tenant.config = {**(tenant.config or {}), "thresholds": {"sensor_silent_min": 1}}
        base = parse_ts(latest_scan(db, tid).ts)
        db.commit()
    finally:
        db.close()
    at = base + dt.timedelta(minutes=MIN_SILENT_MIN - 1)
    assert _factor(_assess(tid, now=at), "sensor_fresh").weight > 0
