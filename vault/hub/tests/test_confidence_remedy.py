"""The rubric reaching the decision `remedy.resolve_action` returns.

**What this file is for, and the hole it was written to close.** The
invariants themselves are pinned against `confidence.gate` as a pure function
(`test_confidence_gate.py`) and each factor's weight is pinned in isolation
(`test_confidence_factors.py`). Neither touches the WIRE between them, and
the wire is where `_apply_confidence` does its own reasoning: it derives three
of `gate`'s four safety inputs from the SIGNS of factor weights. So a
plausible-looking edit -- softening `held`'s penalty from `-25` to `0` --
makes `recurred_since` permanently `False`, opens promotion to a recurring
alert, and leaves every other test in the repository green.

The measure of that hole: replacing `confidence.gate` with an identity
function left 46 tests across six files passing. Every test below was written
to fail under one of two deliberate breakages -- neutering `gate`, or
flipping a factor's sign -- and the ones that can only bite in one of those
ways say so in their own docstring, because a test that cannot fail is worse
than an absent one and the only defence against writing another is naming out
loud which breakage each one catches.

The fixture host matters too. These tests used to pass `host="prod"`, which
does not exist in the demo capture, so all three resolved through
`resolve_action`'s capability-gap branch with `kind="human"` and `tier=0` and
never reached the tier block they were written about. `clinic-nas` is a real
host in the scrubbed Northwind Dental fixture with a real open alert
(`sec:cve:clinic-nas`) whose key `remedy.fix_for_key` resolves to
`security_updates` on it.
"""
import datetime as dt

from guardian_hub import confidence as conf_mod, models as m, networks, remedy
from guardian_hub.db import SessionLocal
from guardian_hub.scans import parse_ts
from tests.test_seed_helpers import seeded_demo

#: A real open alert in the demo fixture whose key resolves, via
#: `remedy.fix_for_key`, to `security_updates` on a real host.
KEY = "sec:cve:clinic-nas"
HOST = "clinic-nas"
ACTION = "security_updates"


def _resolve(db, tenant, role, *, action=ACTION, host=HOST, key=KEY):
    return remedy.resolve_action(db, tenant, role, action, host, "",
                                 f"{host}: pending security updates", msg_key=key)


def _alert(db, tid):
    return db.query(m.Alert).filter_by(tenant_id=tid, key=KEY).first()


def _fix(db, tid, *, ts, verified=None, action=ACTION, host=HOST):
    db.add(m.Fix(tenant_id=tid, host=host, action=action, ok=True,
                 ts=ts.isoformat(), verified_at=verified))


def test_a_decision_carries_the_score_the_factors_add_up_to(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        out = _resolve(db, db.get(m.Tenant, tid), "admin")
        # The demo fixture's own arithmetic, spelled out so a change to the
        # capture fails here loudly instead of silently moving the number:
        # BASELINE 50, never repaired this way on this host (-20), no repair
        # of this kind has run so nothing says it does not hold (+10),
        # nothing to confirm (0), never reopened by a person (0), reversible
        # (+10), sensor fresh because `scanfixture` rebases the capture onto
        # now (+5) = 55.
        assert out.confidence == 55
        # 55 is above LOW_WATER, so the rubric leaves an admin's tier-2
        # decision exactly as the tier rules set it.
        assert out.kind == "auto" and out.can_run_now is True
        assert out.confidence_why
    finally:
        db.close()


def test_a_reboot_scores_low_and_still_asks(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        out = _resolve(db, db.get(m.Tenant, tid), "admin", action="reboot")
        # `reversible` is -40 for a `TIER3_FIXES` action, which on this host's
        # history floors the score -- and the decision is an approval for the
        # tier's own reason, not the score's. Both halves are asserted: a
        # reboot that scored high would mean the rubric had stopped reading
        # `TIER3_FIXES`, and one that was promoted would mean `gate` had.
        assert out.confidence == 0
        assert out.kind == "approval" and out.can_run_now is False
        assert out.tier == 3
    finally:
        db.close()


def test_a_low_score_demotes_an_auto_to_an_approval_through_resolve_action(client):
    """Demotion, end to end, not against `gate` in isolation.

    This is the test that fails if `gate` is neutered: without it the
    decision `resolve_action` hands back for this fixture is `auto`, which is
    what the tier rules alone say.

    The setup is one successful but UNVERIFIED repair of this exact action on
    this exact host, stamped BEFORE the alert was last seen -- so the alert
    outlived Vault's own repair. That is `host_history` +10, `held` -25
    (seen again after the repair ran), `verified` -10 (a repair ran and
    nothing confirmed it), `reversible` +10, `sensor_fresh` +5: 40, under
    `LOW_WATER`. The clock comes from the alert's own `last_seen` rather than
    a literal, because the demo capture is rebased onto the current time per
    test (see CLAUDE.md § Tests) and a hardcoded instant would make the
    comparison mean something different depending on the hour.
    """
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        assert _resolve(db, tenant, "admin").kind == "auto"   # before
        _fix(db, tid, ts=parse_ts(_alert(db, tid).last_seen) - dt.timedelta(hours=1))
        db.commit()
        out = _resolve(db, tenant, "admin")
        assert out.confidence < conf_mod.LOW_WATER
        assert out.kind == "approval" and out.can_run_now is False
    finally:
        db.close()


def test_the_held_factor_reaches_the_gate_as_recurred_since(client):
    """The wire itself: a factor's sign becoming a safety input.

    `_apply_confidence` computes `recurred_since = held.weight < 0`, and that
    line is what this asserts, by capturing the keyword arguments
    `resolve_action` actually hands `confidence.gate`. Flipping `held`'s
    weight from `-25` to `0` -- the plausible "soften this" edit -- fails
    here and nowhere else in the repository: the factor tests pin the weight
    in isolation and `test_confidence_gate.py` drives `gate` with booleans a
    test supplied, so neither notices that the two stopped being connected.

    It is deliberately a capture rather than an assertion about `out.kind`.
    With the receipts below in place the alert has recurred, so `gate` refuses
    to promote -- but a NEUTERED `gate` does not promote either, and after the
    `caller_may_run` fix the promotion branch is not reachable from
    `resolve_action` at all (an admin under tier 3 is already `auto`, an admin
    at tier 3 is refused by the tier bar, and a non-admin by `caller_may_run`).
    So "did not promote" is true here for several reasons at once and proves
    none of them. What the recorded boolean proves is the one thing no other
    test does: that the sign of the weight is still the thing `gate` is told.
    """
    tid = seeded_demo(client)
    db = SessionLocal()
    seen: dict = {}
    real_gate = conf_mod.gate

    def spy(kind, conf, **kw):
        seen.update(kw)
        seen["kind_in"] = kind
        seen["factors"] = {f.name: f.weight for f in conf.factors}
        return real_gate(kind, conf, **kw)

    try:
        tenant = db.get(m.Tenant, tid)
        last_seen = parse_ts(_alert(db, tid).last_seen)
        # Three CONFIRMED repairs of this exact action on this exact host --
        # every receipt `gate`'s promotion bar asks for -- but all of them
        # ran before the alert was last seen, so the finding came back.
        for i in range(conf_mod.PROMOTE_MIN_SUCCESSES):
            _fix(db, tid, ts=last_seen - dt.timedelta(hours=i + 1),
                 verified=dt.datetime.now(dt.timezone.utc))
        # A person reopening THIS alert, which is the only record the `undone`
        # factor reads (see its long comment in `confidence.assess`). Three
        # details are load-bearing, and each one silently zeroes the factor --
        # and with it the assertion below -- if it is got wrong:
        #   - `who` is a person's name, never `"system"`. `ingest.py` writes a
        #     system reopen of its own when a resolved condition re-fires on
        #     the next scan, and `assess` excludes it: Vault re-opening its
        #     own alert is not a human overruling Vault.
        #   - the body's key is the CANONICAL dialect. `m.Alert.key` is
        #     namespaced per network and `Edit.body["key"]` is not, so `assess`
        #     canonicalises before comparing and a raw `KEY` here matches
        #     nothing.
        #   - `ts` is left to `m.Edit`'s own `default=utcnow`. `assess` bounds
        #     the lookback at `now - UNDONE_LOOKBACK_DAYS`, so a literal stamp
        #     would be a fixed instant measured against a fixture
        #     `scanfixture` rebases onto the current time per test -- the trap
        #     CLAUDE.md § Tests describes. The default cannot fall out of the
        #     window however long the run takes.
        db.add(m.Edit(tenant_id=tid, path="/api/alert", who="amy",
                      body={"key": networks.canonical_lan_key(KEY),
                            "action": "reopen"}))
        db.commit()
        conf_mod.gate = spy
        out = _resolve(db, tenant, "operator")
    finally:
        conf_mod.gate = real_gate
        db.close()

    assert seen["factors"]["held"] < 0, seen["factors"]
    assert seen["recurred_since"] is True
    # `ever_undone` against a LITERAL, and the reopen above is what earns the
    # right to write one. This assertion used to read
    # `is (seen["factors"]["undone"] < 0)`, comparing the derivation with
    # itself: no fixture row made `undone` fire, so the weight was 0 and the
    # line reduced to `False is False` -- true whatever `_apply_confidence`
    # did with the sign. Flipping its `< 0` to `> 0`, which permanently
    # disables the "a person undid this repair" promotion bar, left every one
    # of the 52 tests across the confidence and decision files green.
    assert seen["factors"]["undone"] < 0, seen["factors"]
    assert seen["ever_undone"] is True
    # `sensor_fresh` stays a comparison against its factor and that is sound,
    # for the reason the line above was not: the demo fixture's newest scan is
    # fresh, so the weight is a non-zero `+5` and both sides carry real
    # information -- flip `_apply_confidence`'s `> 0` and the two disagree.
    assert seen["sensor_fresh"] is (seen["factors"]["sensor_fresh"] > 0)
    assert seen["verified_successes"] >= conf_mod.PROMOTE_MIN_SUCCESSES
    assert out.kind == "approval" and out.can_run_now is False


def test_an_operator_is_never_promoted_however_good_the_history(client):
    """The regression test for the defect this round exists for, end to end.

    `kind="approval"` here means "you may not run this at all" -- `apply_fix`
    is an admin tool -- not "a person must confirm it first". Before
    `caller_may_run`, the rubric read the two as one word and promoted this
    exact case to `auto`: the row left the Needs-you list and its badge
    count, the Fix button stayed absent because `min_role` still held, the
    chat handoff minted nothing because it keys on `kind == "approval"`, and
    `opening` was never composed. The operator was left with no route to the
    repair and no row to click.

    The receipts are stamped AFTER the alert's `last_seen`, so `held` is
    positive and the score reaches 100 -- every promotion bar except the
    caller's own access is cleared, which is what makes this a test of that
    bar and not of the others.
    """
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        last_seen = parse_ts(_alert(db, tid).last_seen)
        for i in range(conf_mod.PROMOTE_MIN_SUCCESSES):
            _fix(db, tid, ts=last_seen + dt.timedelta(minutes=i + 1),
                 verified=dt.datetime.now(dt.timezone.utc))
        db.commit()
        # The admin's own decision proves the history really is as good as it
        # looks: same estate, same score, and Vault will act on it.
        admin = _resolve(db, tenant, "admin")
        assert admin.confidence >= conf_mod.HIGH_WATER and admin.kind == "auto"

        out = _resolve(db, tenant, "operator")
        assert out.confidence == admin.confidence
        assert out.kind == "approval" and out.can_run_now is False
        # The row still has something to show and somewhere to go: an empty
        # `opening` is what the promoted version produced, because
        # `resolve_action` only composes it for a non-`auto` decision.
        assert out.opening and out.min_role == "admin"
    finally:
        db.close()


def test_the_existing_remedy_contract_is_unchanged(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        out = _resolve(db, db.get(m.Tenant, tid), "admin")
        assert out.action == ACTION and out.host == HOST and out.tier == 2
        assert out.tool == "apply_fix" and out.min_role == "admin"
    finally:
        db.close()


def test_the_factors_reach_the_payload_a_dashboard_reads(client):
    """All six, over HTTP, on a real row -- not just off `resolve_action`.

    The two near-misses this project has already had were both a feature that
    was inert on the only path that matters while its own unit tests passed
    (Ruling R1's empty `alert_key`, and Task 6's happy-path row written with
    `alert_key=""`). Everything below `resolve_action` is a pydantic model and
    a route, either of which can drop a field without a word: `_alert_row`
    could pass the remedy by a field list instead of whole, or `AlertRemedy`
    could gain a `model_dump` exclusion. So this asserts on the JSON the
    browser actually gets, keyed to the fixture's own alert.
    """
    seeded_demo(client)
    rows = client.get("/api/v1/inbox?view=alerts").json()["rows"]
    row = next(r for r in rows if r["id"] == KEY)
    factors = row["remedy"]["confidence_factors"]
    assert [f["name"] for f in factors] == [
        "host_history", "held", "verified", "undone", "reversible", "sensor_fresh"]
    # Every one of them says what was asked and what the rows answered,
    # including the ones that found nothing -- an empty `answer` beside a
    # `weight` of 0 would be a blank line the owner cannot interpret.
    for f in factors:
        assert f["question"] and f["answer"], f
        assert isinstance(f["weight"], int)
    # The field list is CLOSED, asserted as a set rather than as "no `why`".
    # `why` is deliberately off the wire -- `_confidence_why` already joins the
    # loudest three into `confidence_why`, which the same row carries, and the
    # row and the drawer are on one screen, so shipping both prints the same
    # prose twice. But `assert "why" not in f` could not fail: pydantic
    # serialises exactly the fields the model declares, so it restates
    # `ConfidenceFactor`'s definition rather than guarding it. The set form
    # fails on a field ADDED as well as one dropped, which is the actual
    # contract between this payload and `web/src/types.ts`.
    assert all(set(f) == {"name", "question", "answer", "weight"} for f in factors)
    assert row["remedy"]["confidence_why"]
    assert 0 <= row["remedy"]["confidence"] <= 100


def test_the_handoff_tells_the_owner_how_sure_it_is(client):
    """The sentence is in the message someone reads before authorising.

    Ordering is asserted, not just presence: the hedge belongs above the ask,
    because a reader weighing "should I say yes" wants it while they are still
    weighing. "What I need from you" stays last -- it is the call to action,
    and anything printed under it reads as an afterthought.
    """
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        # An operator, so the decision is `approval` and an opening is composed
        # at all (`resolve_action` writes `opening` only for a non-`auto` kind).
        out = _resolve(db, db.get(m.Tenant, tid), "operator")
        assert out.kind == "approval"
        assert f"How sure I am: {out.confidence_why}" in out.opening
        assert out.opening.index("How sure I am:") < out.opening.index("What I need from you:")
    finally:
        db.close()


def test_a_broken_rubric_leaves_no_factors_rather_than_some(client, monkeypatch):
    """Fail-open, on the new field as much as on the old two.

    `_apply_confidence` runs on the read path, once per alert, so an exception
    there must leave the decision exactly as the tier rules made it rather than
    blank the dashboard. The new list has to fail the same way, and the failure
    that matters is a PARTIAL one: six factors is a complete answer and two is
    a misleading one, because the drawer's whole promise is that the owner is
    seeing everything that was considered. Raising from `assess` is the honest
    way to check that, since that is where all six are computed.
    """
    tid = seeded_demo(client)

    def boom(*a, **k):
        raise RuntimeError("rubric is broken")

    monkeypatch.setattr(conf_mod, "assess", boom)
    db = SessionLocal()
    try:
        out = _resolve(db, db.get(m.Tenant, tid), "admin")
        assert out.confidence_factors == []
        assert out.confidence == 0 and out.confidence_why == ""
        # The decision itself is untouched: an admin, a tier-2 action on a host
        # Vault can reach, is what the tier rules alone make `auto`.
        assert out.kind == "auto" and out.can_run_now is True
        # And the handoff is not composed for an `auto` decision, with or
        # without a score -- nothing about the fail-open invents one.
        assert out.opening == ""
    finally:
        db.close()
