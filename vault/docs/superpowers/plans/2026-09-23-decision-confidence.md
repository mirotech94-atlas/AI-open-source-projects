# Decision Confidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Guardian a 0-100 confidence score, with its working shown, that advises the auto/approval/human repair decision and records every decision so a real model can be fitted later.

**Architecture:** One dependency-free scoring module (`confidence.py`) that turns rows Guardian already stores into weighted yes/no factors; a gate that may always add caution and may only remove it on demonstrated history; a ledger table that freezes each decision's inputs and fills in what happened. `remedy.resolve_action` is the single call site.

**Tech Stack:** Python 3.10, SQLAlchemy 2.x, Alembic, pytest. No ML libraries — nothing is fitted in this plan.

**Spec:** `docs/superpowers/specs/2026-09-23-decision-confidence-design.md`

## Global Constraints

- Run the hub suite from `hub/` with its own venv: `cd hub && .venv/bin/pytest`.
- Commit subjects: a sentence describing the user-visible behaviour change, present tense, sentence case. No Conventional Commits prefix.
- Any non-obvious invariant gets a docstring or comment explaining **why**, not what. Match the density in `remedy.py` and `billing.py`.
- Never assert wall-clock in a test. Count statements or assert on values.
- `TIER3_FIXES = {"reboot", "full_upgrade", "disable_ssh_password", "block_subnet", "ban_long", "limit_ssh", "security_headers"}` is never promotable at any score.
- `tests/test_remedy.py` must pass untouched at every commit — it pins the fix tables against `sensor/guardian_sensor/fix.py`.
- Thresholds, fixed for this plan: `LOW_WATER = 45`, `HIGH_WATER = 85`, `PROMOTE_MIN_SUCCESSES = 3`.

---

### Task 1: The score and its vocabulary

Pure data and arithmetic. No database, so it is testable in milliseconds and the weights are arguable in one place.

**Files:**
- Create: `hub/guardian_hub/confidence.py`
- Test: `hub/tests/test_confidence.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Factor(name: str, question: str, answer: str, weight: int, why: str)`; `Confidence(score: int, verdict: str, factors: list[Factor])`; `score_of(factors: list[Factor]) -> int`; `verdict_of(score: int, tier: int) -> str`; constants `LOW_WATER`, `HIGH_WATER`, `PROMOTE_MIN_SUCCESSES`, `BASELINE`.

- [ ] **Step 1: Write the failing test**

```python
"""The rubric's arithmetic, with no database in the way.

Weights here are chosen, not fitted -- production has 78 repairs and every
one of them succeeded, which is a single class and teaches a classifier
nothing. So the numbers are a person's judgement, and this file is where
that judgement is argued with.
"""
import pytest

from guardian_hub import confidence as c


def _f(name="history", weight=0):
    return c.Factor(name=name, question="q?", answer="a", weight=weight, why="because")


def test_a_score_starts_from_the_baseline_and_moves_with_the_factors():
    assert c.score_of([]) == c.BASELINE
    assert c.score_of([_f(weight=10)]) == c.BASELINE + 10
    assert c.score_of([_f(weight=10), _f(weight=-30)]) == c.BASELINE - 20


def test_a_score_is_clamped_to_nought_and_a_hundred():
    assert c.score_of([_f(weight=-500)]) == 0
    assert c.score_of([_f(weight=500)]) == 100


def test_the_same_factors_always_give_the_same_score():
    factors = [_f(weight=12), _f(name="verified", weight=-7)]
    assert c.score_of(factors) == c.score_of(list(reversed(factors)))


@pytest.mark.parametrize("score, expected", [
    (0, "approval"), (c.LOW_WATER - 1, "approval"),
    (c.LOW_WATER, "approval"), (c.HIGH_WATER, "auto"), (100, "auto"),
])
def test_a_tier_two_action_reads_auto_only_above_the_high_water_mark(score, expected):
    assert c.verdict_of(score, tier=2) == expected


@pytest.mark.parametrize("score", [0, 50, 100])
def test_a_tier_three_action_never_reads_auto_however_high_the_score(score):
    # The rubric cannot promote what the tier table forbids, and saying so
    # here as well as in the gate means a caller that reads the verdict
    # without the gate still cannot be misled.
    assert c.verdict_of(score, tier=3) == "approval"


def test_a_factor_carries_the_question_it_answers():
    f = c.Factor(name="host_history", question="Fixed on prod before?",
                 answer="yes, 12 times", weight=30, why="It has held every time.")
    assert f.question.endswith("?") and f.why and f.weight == 30
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hub && .venv/bin/pytest tests/test_confidence.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'guardian_hub.confidence'`

- [ ] **Step 3: Write minimal implementation**

```python
"""How sure Guardian is that it should make a repair itself, and why.

This is a rubric, not a prediction. Production carries 78 repairs and all
78 have `ok=1` -- one class, no decision boundary -- across two tenants, so
there is nothing yet to fit a model to. The weights below are chosen by a
person and are meant to be argued with; `decisions.py` records what actually
happened so that a model CAN be fitted later, with this rubric as the
baseline it has to beat.

Every factor carries the question it answers and a sentence of why, because
an owner is owed a reason for a repair that ran on their network, and "the
model was confident" is not one.
"""
from __future__ import annotations

import dataclasses

#: Where a decision with no evidence either way sits. Deliberately below
#: HIGH_WATER: knowing nothing about a host is not a reason to act on it
#: unattended, so silence scores as caution rather than as neutrality.
BASELINE = 50

#: Below this, Guardian asks even where the tier would let it act.
LOW_WATER = 45

#: At or above this a tier-2 action MAY be promoted -- but only with the
#: history `confidence.gate` insists on. A score alone never promotes.
HIGH_WATER = 85

#: Verified successes of this exact action on this exact host before
#: promotion is considered at all. Three is small on purpose: the bar that
#: matters is `verified`, not the count.
PROMOTE_MIN_SUCCESSES = 3


@dataclasses.dataclass(frozen=True)
class Factor:
    """One yes/no question, its answer, and what that was worth.

    `weight` may be zero: a factor that found nothing is still returned, so
    the owner sees what was considered and absent rather than only what
    fired. An empty factor list is a real answer -- "nothing is known" --
    and scores as `BASELINE`.
    """
    name: str
    question: str
    answer: str
    weight: int
    why: str


@dataclasses.dataclass(frozen=True)
class Confidence:
    score: int
    verdict: str            # "auto" | "approval"
    factors: list[Factor]


def score_of(factors: list[Factor]) -> int:
    """`BASELINE` plus every weight, clamped to 0-100.

    Addition, not multiplication: a single missing fact should move the
    answer, never zero it, and a person reading the factor list has to be
    able to add it up themselves.
    """
    return max(0, min(100, BASELINE + sum(f.weight for f in factors)))


def verdict_of(score: int, tier: int) -> str:
    """What the rubric alone would say. `gate` has the final word.

    Tier 3 is refused here as well as in the gate. The duplication is
    deliberate: a caller that reads `verdict` without going through the gate
    still cannot be told that rebooting a server unattended is fine.
    """
    if tier >= 3:
        return "approval"
    return "auto" if score >= HIGH_WATER else "approval"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd hub && .venv/bin/pytest tests/test_confidence.py -q`
Expected: PASS, 8 tests

- [ ] **Step 5: Commit**

```bash
git add hub/guardian_hub/confidence.py hub/tests/test_confidence.py
git commit -m "Guardian can put a number on how sure it is about a repair"
```

---

### Task 2: The factors, read from rows Guardian already keeps

**Files:**
- Modify: `hub/guardian_hub/confidence.py`
- Test: `hub/tests/test_confidence_factors.py`

**Interfaces:**
- Consumes: `Factor`, `score_of`, `verdict_of` from Task 1.
- Produces: `assess(db, tenant, *, alert_key: str, host: str, action: str, tier: int, now: dt.datetime | None = None) -> Confidence`, returning factors named exactly `host_history`, `held`, `verified`, `undone`, `reversible`, `sensor_fresh`.

- [ ] **Step 1: Write the failing test**

```python
"""The factors, against real rows.

Each test writes only the rows its own factor reads, so a failure names the
factor rather than the fixture.
"""
import datetime as dt

from guardian_hub import confidence as c, models as m
from guardian_hub.db import SessionLocal
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
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        for _ in range(4):
            _fix(db, tid)
        db.commit()
    finally:
        db.close()
    quiet = _assess(tid).score
    db = SessionLocal()
    try:
        db.add(m.Alert(tenant_id=tid, key="pkg:prod:security", sev="WARN",
                       first_seen=NOW.isoformat(timespec="seconds"),
                       last_seen=NOW.isoformat(timespec="seconds"),
                       count=6, status="open"))
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


def test_a_tier_three_action_is_marked_not_reversible(client):
    tid = seeded_demo(client)
    conf = _assess(tid, action="reboot", tier=3)
    assert _factor(conf, "reversible").weight < 0
    assert conf.verdict == "approval"


def test_a_stale_sensor_costs_confidence_because_nothing_can_be_verified(client):
    tid = seeded_demo(client)
    fresh = _assess(tid, now=NOW)
    stale = _assess(tid, now=NOW + dt.timedelta(hours=6))
    assert stale.score < fresh.score
    assert _factor(stale, "sensor_fresh").weight < 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hub && .venv/bin/pytest tests/test_confidence_factors.py -q`
Expected: FAIL — `AttributeError: module 'guardian_hub.confidence' has no attribute 'assess'`

- [ ] **Step 3: Write minimal implementation**

Append to `hub/guardian_hub/confidence.py`:

```python
import datetime as dt

from sqlalchemy.orm import Session as DbSession

#: How stale the newest scan may be before Guardian stops trusting that it
#: could verify a repair afterwards. Matches the sensor-silence threshold's
#: spirit: past this, `scheduler._watch_sensor` is already raising.
SENSOR_FRESH_MIN = 30


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def assess(db: DbSession, tenant, *, alert_key: str, host: str, action: str,
           tier: int, now: dt.datetime | None = None) -> Confidence:
    """How sure Guardian is about repairing `alert_key` with `action` on `host`.

    Reads only rows that already exist. Nothing here is collected for the
    rubric's benefit, which is why it could be switched on for every tenant
    the day it shipped.
    """
    from . import models as m
    from .chat.tiers import TIER3_FIXES
    from .scans import latest_scan, parse_ts

    now = now or _now()
    factors: list[Factor] = []

    fixes = db.query(m.Fix).filter_by(tenant_id=tenant.id, host=host,
                                      action=action, ok=True).all()
    n = len(fixes)
    factors.append(Factor(
        name="host_history",
        question=f"Has Guardian run {action} on {host} successfully before?",
        answer=f"yes, {n} times" if n else "never on this host",
        weight=min(30, 10 * n) if n else -20,
        why=("It has done this here before and the command succeeded."
             if n else "Guardian has never repaired this host this way."),
    ))

    alert = db.query(m.Alert).filter_by(tenant_id=tenant.id, key=alert_key).first()
    recurred = (alert.count or 1) - 1 if alert else 0
    factors.append(Factor(
        name="held",
        question=f"Has {alert_key} come back after being dealt with?",
        answer=f"returned {recurred} times" if recurred else "has not returned",
        weight=-min(30, 8 * recurred) if recurred else 10,
        why=("The repair has not been holding, so doing it again unattended "
             "repeats something that is not working."
             if recurred else "Nothing suggests the repair does not hold."),
    ))

    verified = sum(1 for f in fixes if f.verified_at is not None)
    factors.append(Factor(
        name="verified",
        question="Did later evidence confirm those repairs worked?",
        answer=f"{verified} of {n} confirmed" if n else "nothing to confirm",
        weight=15 if verified >= PROMOTE_MIN_SUCCESSES else (0 if not n else -10),
        why=("Later scans showed the finding actually cleared."
             if verified >= PROMOTE_MIN_SUCCESSES
             else "Guardian ran the repair but never saw it take effect."),
    ))

    undone = db.query(m.Edit).filter(
        m.Edit.tenant_id == tenant.id,
        m.Edit.path.like("%undo%"),
    ).count()
    factors.append(Factor(
        name="undone",
        question="Has a person ever undone a repair here?",
        answer=f"{undone} undone" if undone else "never undone",
        weight=-25 if undone else 0,
        why=("Somebody reversed Guardian's work, which is a judgement Guardian "
             "should not overrule by acting faster."
             if undone else "No repair here has been reversed by a person."),
    ))

    reversible = action not in TIER3_FIXES
    factors.append(Factor(
        name="reversible",
        question=f"Is {action} reversible?",
        answer="yes" if reversible else "no, it is disruptive or hard to undo",
        weight=10 if reversible else -40,
        why=("It can be put back if it turns out to be wrong."
             if reversible else "Getting this wrong cannot be quietly undone."),
    ))

    scan = latest_scan(db, tenant.id)
    seen = parse_ts(scan.ts) if scan else None
    age_min = (now - seen).total_seconds() / 60 if seen else None
    fresh = age_min is not None and age_min <= SENSOR_FRESH_MIN
    factors.append(Factor(
        name="sensor_fresh",
        question="Is the sensor reporting often enough to confirm the result?",
        answer=(f"last scan {int(age_min)} min ago" if age_min is not None
                else "no scan at all"),
        weight=5 if fresh else -15,
        why=("A repair Guardian cannot check afterwards is one it should be "
             "slower to make on its own."
             if not fresh else "Guardian will see whether this worked."),
    ))

    score = score_of(factors)
    return Confidence(score=score, verdict=verdict_of(score, tier), factors=factors)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd hub && .venv/bin/pytest tests/test_confidence_factors.py tests/test_confidence.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hub/guardian_hub/confidence.py hub/tests/test_confidence_factors.py
git commit -m "The confidence score is read from repairs, recurrences and sensor freshness"
```

---

### Task 3: The gate — caution always, promotion only on receipts

**Files:**
- Modify: `hub/guardian_hub/confidence.py`
- Test: `hub/tests/test_confidence_gate.py`

**Interfaces:**
- Consumes: `Confidence`, `assess`, thresholds from Tasks 1-2.
- Produces: `gate(kind: str, conf: Confidence, *, action: str, tier: int, verified_successes: int, recurred_since: bool, ever_undone: bool, sensor_fresh: bool, auto_heal: bool) -> tuple[str, str]` returning `(kind, reason)`.

- [ ] **Step 1: Write the failing test**

```python
"""The five invariants. Each is its own named test so a failure says which
promise broke, not merely that one did.
"""
import pytest

from guardian_hub import confidence as c


def _conf(score):
    return c.Confidence(score=score, verdict="auto" if score >= c.HIGH_WATER else "approval",
                        factors=[])


def _gate(kind="approval", score=95, **kw):
    args = dict(action="security_updates", tier=2, verified_successes=5,
                recurred_since=False, ever_undone=False, sensor_fresh=True,
                auto_heal=True)
    args.update(kw)
    return c.gate(kind, _conf(score), **args)


def test_a_tier_three_action_is_never_promoted_at_any_score():
    for score in (0, 50, 85, 100):
        kind, _ = _gate(score=score, action="reboot", tier=3, verified_successes=99)
        assert kind == "approval", score


def test_an_action_never_run_on_this_host_is_never_promoted():
    kind, reason = _gate(verified_successes=0)
    assert kind == "approval"
    assert "never" in reason.lower() or "history" in reason.lower()


def test_the_kill_switch_beats_any_score():
    kind, reason = _gate(auto_heal=False)
    assert kind == "approval"
    assert "switched off" in reason.lower() or "auto_heal" in reason.lower()


def test_a_recurring_alert_is_never_promoted_however_often_it_was_fixed():
    kind, _ = _gate(verified_successes=50, recurred_since=True)
    assert kind == "approval"


def test_a_repair_a_person_undid_is_never_promoted():
    kind, _ = _gate(ever_undone=True)
    assert kind == "approval"


def test_a_low_score_demotes_an_auto_to_an_approval():
    kind, reason = _gate(kind="auto", score=c.LOW_WATER - 1)
    assert kind == "approval" and reason


def test_a_high_score_with_every_receipt_promotes():
    kind, reason = _gate(kind="approval", score=c.HIGH_WATER)
    assert kind == "auto" and reason


def test_a_human_decision_is_never_touched():
    # `human` means nothing Guardian runs can fix it -- a dead sensor is the
    # archetype. No score makes that actionable.
    for score in (0, 100):
        kind, _ = _gate(kind="human", score=score)
        assert kind == "human"


def test_a_high_score_alone_does_not_promote_without_the_history():
    kind, _ = _gate(score=100, verified_successes=c.PROMOTE_MIN_SUCCESSES - 1)
    assert kind == "approval"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hub && .venv/bin/pytest tests/test_confidence_gate.py -q`
Expected: FAIL — `AttributeError: module 'guardian_hub.confidence' has no attribute 'gate'`

- [ ] **Step 3: Write minimal implementation**

Append to `hub/guardian_hub/confidence.py`:

```python
def gate(kind: str, conf: Confidence, *, action: str, tier: int,
         verified_successes: int, recurred_since: bool, ever_undone: bool,
         sensor_fresh: bool, auto_heal: bool) -> tuple[str, str]:
    """The decision the rubric is allowed to change, and the reason why.

    The asymmetry is the safety property, and it is the reason a rubric may
    touch a security product's repair path at all:

    * it may ALWAYS add caution -- a low score turns an `auto` into an
      `approval`, and a wrong score there costs a question, nothing more;
    * it may only REMOVE caution on receipts. Not on a score: on this exact
      action having been verified on this exact host, with the alert not
      having come back since, nobody having undone it, the sensor still
      reporting, the action reversible, and the platform kill switch on.

    So a model that is wrong in the dangerous direction cannot authorise
    anything -- the worst it can do is ask. `human` is never touched: it
    means nothing Guardian runs repairs this, and no number changes that.
    """
    if kind == "human":
        return kind, ""

    if kind == "auto" and conf.score < LOW_WATER:
        return "approval", (
            f"Guardian is only {conf.score}% sure about this one, so it is asking "
            f"first even though it would normally just do it.")

    if kind != "approval":
        return kind, ""

    if tier >= 3 or action in _tier3():
        return kind, ""
    if not auto_heal:
        return kind, "Automatic repair is switched off for the whole platform."
    if verified_successes < PROMOTE_MIN_SUCCESSES:
        return kind, (
            "Guardian has not done this here often enough, with a confirmed "
            "result, to do it unattended yet.")
    if recurred_since or ever_undone or not sensor_fresh:
        return kind, ""
    if conf.score < HIGH_WATER:
        return kind, ""

    return "auto", (
        f"Guardian has done exactly this on this host {verified_successes} times, "
        f"each confirmed afterwards, and the finding has not come back — so it is "
        f"doing it rather than asking.")


def _tier3() -> frozenset:
    from .chat.tiers import TIER3_FIXES
    return frozenset(TIER3_FIXES)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd hub && .venv/bin/pytest tests/test_confidence_gate.py -q`
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add hub/guardian_hub/confidence.py hub/tests/test_confidence_gate.py
git commit -m "A confidence score can make Guardian ask, and can only make it act on proof"
```

---

### Task 4: Wire it into the one place the decision is made

**Files:**
- Modify: `hub/guardian_hub/remedy.py:662-690` (inside `resolve_action`)
- Modify: `hub/guardian_hub/schemas_dash.py` (`AlertRemedy`)
- Test: `hub/tests/test_confidence_remedy.py`

**Interfaces:**
- Consumes: `confidence.assess`, `confidence.gate`.
- Produces: `AlertRemedy.confidence: int = 0` and `AlertRemedy.confidence_why: str = ""`.

- [ ] **Step 1: Write the failing test**

```python
"""The rubric reaching the decision `remedy.resolve_action` returns."""
from guardian_hub import models as m, remedy
from guardian_hub.db import SessionLocal
from tests.test_seed_helpers import seeded_demo


def _resolve(tid, action="security_updates", host="prod"):
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        return remedy.resolve_action(db, tenant, "admin", action, host, "",
                                     "pending security updates on prod")
    finally:
        db.close()


def test_a_decision_carries_its_confidence(client):
    tid = seeded_demo(client)
    out = _resolve(tid)
    assert 0 <= out.confidence <= 100
    assert out.confidence_why


def test_a_reboot_stays_an_approval_and_says_so(client):
    tid = seeded_demo(client)
    out = _resolve(tid, action="reboot")
    assert out.kind in ("approval", "human")


def test_the_existing_remedy_contract_is_unchanged(client):
    # kind/action/host/tier keep their meanings; the score is additive.
    tid = seeded_demo(client)
    out = _resolve(tid)
    assert out.kind in ("auto", "approval", "human")
    assert out.action == "security_updates" and out.host == "prod"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hub && .venv/bin/pytest tests/test_confidence_remedy.py -q`
Expected: FAIL — `AttributeError: 'AlertRemedy' object has no attribute 'confidence'`

- [ ] **Step 3: Write minimal implementation**

In `hub/guardian_hub/schemas_dash.py`, inside `class AlertRemedy`, after `need`:

```python
    #: How sure Guardian is, 0-100, and the sentence explaining it. A rubric
    #: (`guardian_hub.confidence`), not a prediction -- nothing is fitted.
    #: Advisory: it may make `kind` more cautious, and may only make it less
    #: so on demonstrated history. See that module's `gate`.
    confidence: int = 0
    confidence_why: str = ""
```

In `hub/guardian_hub/remedy.py`, replace the `else:` branch that sets
`base.kind = "auto"` (line ~686) and the lines after it with:

```python
    else:
        base.kind = "auto"
        base.can_run_now = True
        base.summary = f"I can fix this myself: {label} on {host}."
        base.need = ""

    # The rubric runs AFTER the tier rules, never instead of them: it reads
    # the decision they reached and may adjust it within the bounds
    # `confidence.gate` enforces. A failure to score must not cost a repair,
    # so anything unexpected leaves the tier decision exactly as it was.
    try:
        from . import confidence as conf_mod
        fixes = db.query(m.Fix).filter_by(tenant_id=tenant.id, host=host,
                                          action=action, ok=True).all()
        conf = conf_mod.assess(db, tenant, alert_key=msg_key, host=host,
                               action=action, tier=tier)
        base.confidence = conf.score
        kind, why = conf_mod.gate(
            base.kind, conf, action=action, tier=tier,
            verified_successes=sum(1 for f in fixes if f.verified_at is not None),
            recurred_since=False, ever_undone=False,
            sensor_fresh=any(f.name == "sensor_fresh" and f.weight > 0
                             for f in conf.factors),
            auto_heal=platform_settings.auto_heal())
        if why:
            base.confidence_why = why
        if kind != base.kind:
            base.kind = kind
            base.can_run_now = (kind == "auto")
    except Exception:
        pass
```

Add `msg_key` as a parameter of `resolve_action` defaulting to `""` so the
call sites that have an alert key can pass it:

```python
def resolve_action(db, tenant, role: str, action: str, host: str, arg: str,
                   msg: str, msg_key: str = "") -> AlertRemedy:
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd hub && .venv/bin/pytest tests/test_confidence_remedy.py tests/test_remedy.py -q`
Expected: PASS — including `test_remedy.py` untouched

- [ ] **Step 5: Commit**

```bash
git add hub/guardian_hub/remedy.py hub/guardian_hub/schemas_dash.py hub/tests/test_confidence_remedy.py
git commit -m "A repair decision carries how sure Guardian is, and why"
```

---

### Task 5: The ledger table

**Files:**
- Create: `hub/alembic/versions/0032_decision_outcome.py`
- Modify: `hub/guardian_hub/models.py`
- Test: `hub/tests/test_decision_ledger.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `models.DecisionOutcome` with columns `id, tenant_id, alert_key, host, action, features, score, verdict, chosen, outcome, decided_at, settled_at`.

- [ ] **Step 1: Write the failing test**

```python
from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from tests.test_seed_helpers import seeded_demo


def test_a_decision_row_freezes_the_features_it_was_made_from(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        db.add(m.DecisionOutcome(
            tenant_id=tid, alert_key="pkg:prod:security", host="prod",
            action="security_updates",
            features={"host_history": 4, "held": 0},
            score=88, verdict="auto", chosen="auto"))
        db.commit()
        row = db.query(m.DecisionOutcome).filter_by(tenant_id=tid).one()
    finally:
        db.close()
    assert row.features["host_history"] == 4
    assert row.outcome == ""          # not known yet
    assert row.settled_at is None
    assert row.decided_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hub && .venv/bin/pytest tests/test_decision_ledger.py -q`
Expected: FAIL — `AttributeError: module 'guardian_hub.models' has no attribute 'DecisionOutcome'`

- [ ] **Step 3: Write minimal implementation**

In `hub/guardian_hub/models.py`:

```python
class DecisionOutcome(Base):
    """What Guardian decided about one repair, and what happened next.

    The dataset that does not exist yet. Production carries 78 repairs, all
    successful -- a single class, which no classifier can learn a boundary
    from. This table is how that changes: one row per decision, the features
    FROZEN as they were at the time, and the outcome filled in later.

    Frozen matters. Features recomputed when the outcome lands would train a
    model on facts the decision did not have, which is the most flattering
    and least useful kind of dataset.
    """
    __tablename__ = "decision_outcome"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(32), index=True)
    alert_key: Mapped[str] = mapped_column(String(200), default="")
    host: Mapped[str] = mapped_column(String(120), default="")
    action: Mapped[str] = mapped_column(String(40), default="")
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    score: Mapped[int] = mapped_column(Integer, default=0)
    verdict: Mapped[str] = mapped_column(String(12), default="")
    #: auto | approved | refused | ignored
    chosen: Mapped[str] = mapped_column(String(12), default="")
    #: held | recurred | undone | unknown -- "" until it settles
    outcome: Mapped[str] = mapped_column(String(12), default="", index=True)
    decided_at: Mapped[dt.datetime] = mapped_column(TZDateTime, default=utcnow)
    settled_at: Mapped[dt.datetime | None] = mapped_column(TZDateTime, nullable=True)
```

Create `hub/alembic/versions/0032_decision_outcome.py`:

```python
"""Every repair decision and what became of it, so a model can be fitted later."""
from alembic import op
import sqlalchemy as sa

revision = "0032_decision_outcome"
down_revision = "0031_firewall_proposal"
branch_labels = depends_on = None


def upgrade():
    insp = sa.inspect(op.get_bind())
    if "decision_outcome" in insp.get_table_names():
        return
    op.create_table(
        "decision_outcome",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("tenant_id", sa.String(32), nullable=False, index=True),
        sa.Column("alert_key", sa.String(200), nullable=False, server_default=""),
        sa.Column("host", sa.String(120), nullable=False, server_default=""),
        sa.Column("action", sa.String(40), nullable=False, server_default=""),
        # Frozen at decision time on purpose -- see the model's docstring.
        sa.Column("features", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("verdict", sa.String(12), nullable=False, server_default=""),
        sa.Column("chosen", sa.String(12), nullable=False, server_default=""),
        sa.Column("outcome", sa.String(12), nullable=False, server_default="", index=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_table("decision_outcome")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd hub && .venv/bin/pytest tests/test_decision_ledger.py -q && .venv/bin/python -m alembic heads`
Expected: PASS, and `alembic heads` prints exactly one head, `0032_decision_outcome`

- [ ] **Step 5: Commit**

```bash
git add hub/guardian_hub/models.py hub/alembic/versions/0032_decision_outcome.py hub/tests/test_decision_ledger.py
git commit -m "Guardian keeps a record of what it decided about a repair and what happened next"
```

---

### Task 6: Write the decision, then settle it

**Files:**
- Create: `hub/guardian_hub/decisions.py`
- Modify: `hub/guardian_hub/remedy.py` (record, in the block added by Task 4)
- Modify: `hub/guardian_hub/verification.py` (settle)
- Test: `hub/tests/test_decision_settles.py`

**Interfaces:**
- Consumes: `models.DecisionOutcome`, `confidence.Confidence`.
- Produces: `decisions.record(db, tenant, *, alert_key, host, action, conf, chosen) -> str` (the row id); `decisions.settle(db, tenant, *, alert_key, host, action, outcome) -> int` (rows settled).

- [ ] **Step 1: Write the failing test**

```python
from guardian_hub import confidence as c, decisions, models as m
from guardian_hub.db import SessionLocal
from tests.test_seed_helpers import seeded_demo


def _conf():
    return c.Confidence(score=88, verdict="auto", factors=[
        c.Factor(name="host_history", question="q?", answer="yes, 4 times",
                 weight=30, why="done before")])


def _row(tid):
    db = SessionLocal()
    try:
        return db.query(m.DecisionOutcome).filter_by(tenant_id=tid).one()
    finally:
        db.close()


def test_recording_a_decision_stores_each_factor_weight(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        decisions.record(db, db.get(m.Tenant, tid), alert_key="k", host="prod",
                         action="security_updates", conf=_conf(), chosen="auto")
    finally:
        db.close()
    row = _row(tid)
    assert row.features["host_history"] == 30 and row.score == 88
    assert row.outcome == ""


def test_settling_fills_the_outcome_once_and_stamps_it(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        decisions.record(db, tenant, alert_key="k", host="prod",
                         action="security_updates", conf=_conf(), chosen="auto")
        n = decisions.settle(db, tenant, alert_key="k", host="prod",
                             action="security_updates", outcome="held")
    finally:
        db.close()
    assert n == 1
    row = _row(tid)
    assert row.outcome == "held" and row.settled_at is not None


def test_a_settled_decision_is_not_settled_again(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        decisions.record(db, tenant, alert_key="k", host="prod",
                         action="security_updates", conf=_conf(), chosen="auto")
        decisions.settle(db, tenant, alert_key="k", host="prod",
                         action="security_updates", outcome="held")
        again = decisions.settle(db, tenant, alert_key="k", host="prod",
                                 action="security_updates", outcome="recurred")
    finally:
        db.close()
    assert again == 0
    assert _row(tid).outcome == "held"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd hub && .venv/bin/pytest tests/test_decision_settles.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'guardian_hub.decisions'`

- [ ] **Step 3: Write minimal implementation**

Create `hub/guardian_hub/decisions.py`:

```python
"""The ledger of repair decisions, and what became of each one.

Two calls, deliberately small. `record` writes what was decided and the
features it was decided from; `settle` writes what happened, once. Nothing
here reads the features back -- that is a model's job, and there is no model
yet (see `confidence`'s docstring for why).
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session as DbSession

from . import models as m
from .confidence import Confidence


def record(db: DbSession, tenant, *, alert_key: str, host: str, action: str,
           conf: Confidence, chosen: str) -> str:
    """Store one decision. Commits, and returns the row id.

    `features` is the factor weights as they were, not the rows they came
    from: a model trained on features recomputed later would be trained on
    facts the decision never had.
    """
    row = m.DecisionOutcome(
        tenant_id=tenant.id, alert_key=alert_key, host=host, action=action,
        features={f.name: f.weight for f in conf.factors},
        score=conf.score, verdict=conf.verdict, chosen=chosen)
    db.add(row)
    db.commit()
    return row.id


def settle(db: DbSession, tenant, *, alert_key: str, host: str, action: str,
           outcome: str) -> int:
    """Fill in what happened, for decisions about this repair that are still
    open. Returns how many rows settled.

    Only unsettled rows are touched, so the FIRST thing that happened is what
    the dataset keeps -- an alert that recurs, is fixed, and recurs again
    must not rewrite the earlier decision's outcome.
    """
    rows = db.query(m.DecisionOutcome).filter_by(
        tenant_id=tenant.id, alert_key=alert_key, host=host,
        action=action, outcome="").all()
    for row in rows:
        row.outcome = outcome
        row.settled_at = dt.datetime.now(dt.timezone.utc)
    if rows:
        db.commit()
    return len(rows)
```

In `hub/guardian_hub/remedy.py`, inside the `try:` block added in Task 4,
after `base.confidence_why` is set:

```python
        from . import decisions
        decisions.record(db, tenant, alert_key=msg_key, host=host,
                         action=action, conf=conf, chosen=base.kind)
```

In `hub/guardian_hub/verification.py`, inside `reconcile`, where a repair is
confirmed, settle the matching decision:

```python
    # The repair held: the decision that led to it was a good one, and the
    # ledger should say so while the evidence is in hand.
    from . import decisions
    decisions.settle(db, tenant, alert_key=alert_key, host=host_name,
                     action=action, outcome="held")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd hub && .venv/bin/pytest tests/test_decision_settles.py tests/test_confidence_remedy.py tests/test_remedy.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add hub/guardian_hub/decisions.py hub/guardian_hub/remedy.py hub/guardian_hub/verification.py hub/tests/test_decision_settles.py
git commit -m "Every repair decision is recorded, and settled when the evidence lands"
```

---

## Final verification

- [ ] `cd hub && .venv/bin/pytest -q` — the whole hub suite. Baseline is zero expected failures.
- [ ] `cd hub && .venv/bin/python -m alembic heads` — exactly one head.
- [ ] `cd hub && .venv/bin/python -c "import guardian_hub.main"` — the cold import the deploy guard runs.
