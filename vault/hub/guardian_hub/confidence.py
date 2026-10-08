"""How sure Vault is that it should make a repair itself, and why.

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
import datetime as dt

from sqlalchemy.orm import Session as DbSession

#: Where a decision with no evidence either way sits. Deliberately below
#: HIGH_WATER: knowing nothing about a host is not a reason to act on it
#: unattended, so silence scores as caution rather than as neutrality.
BASELINE = 50

#: Below this, Vault asks even where the tier would let it act.
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

    #: How many confirmed repairs of this exact `(host, action)` `assess`
    #: counted -- `confirmed_repairs`' answer, carried out rather than left
    #: to be asked for a second time.
    #:
    #: `gate` needs this integer for its promotion bar and prints it verbatim
    #: to the customer, and `assess` has already paid for the query to weigh
    #: the `verified` factor. Before this field existed, `remedy.
    #: _apply_confidence` called `confirmed_repairs` again for the same host
    #: and action, so the identical query ran twice for every open alert on
    #: every dashboard load -- and the read path resolves a remedy once per
    #: alert (`payloads/incidents.py`), which the inbox then pays twice over
    #: because `_counts` resolves per open alert and `build` resolves per row.
    #:
    #: DEFAULTED on purpose, and the default is load-bearing rather than
    #: tidiness: `Confidence` is constructed by hand in `test_confidence_gate.
    #: py`, whose invariant tests drive `gate` with no database at all, and
    #: by `decisions.record`'s callers in tests, none of which have a
    #: count to supply or a reason to care. A required field would have made
    #: every one of them declare a number that has no meaning there.
    #:
    #: `0` therefore means "nobody counted", which is indistinguishable from
    #: "counted, and there were none" -- and that is the safe direction: both
    #: read as no history, and no history never promotes.
    confirmed: int = 0


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


#: FALLBACK for how stale the newest scan may be before Vault stops
#: trusting that it could verify a repair afterwards -- used only when the
#: tenant has not configured a silence threshold of its own.
#:
#: The real bound is per-tenant: `config.thresholds.sensor_silent_min`, which
#: `scheduler._watch_sensor` uses to decide when to raise `sensor:silent`
#: (default 25, floored at 10, and uncapped). This constant used to be the
#: only bound, with a comment claiming that past 30 minutes the watchdog is
#: "already raising" -- a relationship it does not have. A scan 25-30 minutes
#: old read as fresh, worth `+5` and "Vault will see whether this worked",
#: while `sensor:silent` was already open for that tenant; and a tenant who
#: raised the knob to 60 got the reverse, with the rubric calling the sensor
#: stale while the watchdog was content. So `assess` reads the tenant's own
#: threshold, and this is what it falls back to.
#:
#: 30 rather than the watchdog's 25 when nothing is configured, on purpose and
#: not by oversight: the two numbers answer different questions. The
#: watchdog's is "should I wake somebody up", where a false alarm is
#: expensive; the rubric's is "can I check my own work afterwards", where the
#: cost of being wrong is one avoidable confirmation. A tenant that has
#: expressed no opinion gets the rubric's own slightly more forgiving
#: tolerance. A tenant that HAS expressed one gets exactly that, because the
#: alternative is two definitions of "the sensor is reporting" disagreeing on
#: the same estate -- the drift this module already guards against for
#: `confirmed_repairs`.
SENSOR_FRESH_MIN = 30

#: How far back `undone` looks for a person's reopen of THIS alert. Edit rows
#: are never pruned on their own, so an unbounded scan-back would grow with
#: the tenant's whole history; 180 days is long enough to catch a repair
#: someone is still annoyed about and short enough to stay a cheap query.
UNDONE_LOOKBACK_DAYS = 180


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _fresh_threshold_min(tenant) -> int:
    """How old the newest scan may be for THIS tenant before the rubric stops
    believing it could verify a repair.

    The tenant's own `config.thresholds.sensor_silent_min` when it has one,
    floored at the watchdog's `MIN_SILENT_MIN` so a tenant cannot configure the
    rubric past a bound the watchdog would have clamped -- because a tenant
    that has told Vault when to call its sensor dead has already answered
    this question and must not be told something different on the same page.
    `SENSOR_FRESH_MIN` when it has not: see that constant for why the
    unconfigured default deliberately differs from the watchdog's.

    That difference in the unconfigured default is the whole of what this adds,
    and it is now the only thing this function says. It used to re-derive the
    read and the floor rather than delegate, on the honest grounds that
    `scheduler._silent_threshold_min` substituted the watchdog's own default;
    the cost was two readers of one setting key, so renaming that key took two
    edits and no test failed if they drifted. The accessor now takes a
    `default` and the duplicate is gone -- the hazard CLAUDE.md names twice
    over, for `remedy`'s fix tables and for `payloads/history.py`.

    This stays a named function rather than an inline call because the import
    has to be function-local and the `default=` says which question is being
    asked; `assess` reads better for it.
    """
    # Function-local, like every other `guardian_hub` import here (see
    # `_tier3`). `scheduler` reaches `reports.builder` and `config`, none of
    # which this module may need at import time -- `confidence.py` imports
    # nothing from the package at module level, and that is what keeps it
    # unit-testable with no app and no database.
    from .scheduler import _silent_threshold_min

    return _silent_threshold_min(tenant, default=SENSOR_FRESH_MIN)


def confirmed_repairs(db: DbSession, tenant, *, host: str, action: str) -> int:
    """How many times `action` has been run on `host` and later CONFIRMED to work.

    This is a named function rather than an inline query because the count
    is used twice for two different audiences: `assess`'s `verified` factor
    folds it into a weight a person reads as prose, and `gate`'s promotion
    sentence prints the same integer verbatim to the customer ("Vault has
    done exactly this on this host N times..."). If each call site ran its
    own `m.Fix` query, "how many confirmed repairs has Vault done here"
    would have two definitions, and the one nobody tests is the one that
    drifts -- the same failure mode `payloads/history.py` documents for "new
    alerts over 30 days" (three surfaces, one function) and `remedy.py`
    documents for the fix-tier tables (ported from `fix.FIX_ALERT_KEY` and
    checked against it by `tests/test_remedy.py`). Here there is only one
    hub-side caller of the number today, but the sentence a customer reads
    and the score that gates it must never be free to say different things.

    `ok=True` alone is not enough: it only means the command exited zero.
    `verified_at` is set by `verification.reconcile`, and only when a LATER
    scan showed the finding actually cleared -- so this counts repairs with
    confirmed outcomes, not merely commands that ran without error. The name
    says that: it is confirmed repairs, not raw exit codes.

    `gate` does not call this itself -- it takes no `db` and has to stay a
    pure function so the invariant tests in `test_confidence_gate.py` can
    drive it with no database at all. Its `verified_successes` parameter
    is this count, computed once by the caller (via `assess`, or directly)
    and handed in.
    """
    from . import models as m

    return (
        db.query(m.Fix)
        .filter_by(tenant_id=tenant.id, host=host, action=action, ok=True)
        .filter(m.Fix.verified_at.isnot(None))
        .count()
    )


def assess(db: DbSession, tenant, *, alert_key: str, host: str, action: str,
           tier: int, now: dt.datetime | None = None) -> Confidence:
    """How sure Vault is about repairing `alert_key` with `action` on `host`.

    Reads only rows that already exist. Nothing here is collected for the
    rubric's benefit, which is why it could be switched on for every tenant
    the day it shipped.

    **No factor's `question` or `answer` names the action or the alert.** Both
    identifiers reach here -- `action` is a catalogue key (`security_updates`)
    keyed to `m.Fix.action` and `FIX_LABELS`, `alert_key` an internal join key
    (`sec:cve:clinic-nas`) -- and neither is a word a customer should read.
    The prose says "this" and "this repair" instead, which is also the shorter
    sentence: the row that opens this drawer already names the repair and the
    host one line above, so repeating either here would be noise even if both
    were phrased in English. An earlier draft did print them raw, and a second
    passed `FIX_LABELS`'s wording down for the questions to interpolate --
    which read as "Has Vault run install pending security updates on
    records successfully before?", a verb phrase in a verb slot. Neither is
    worth a parameter. `test_confidence_factors` pins the absence of both keys.
    """
    from . import models as m, networks
    from .scans import latest_scan, parse_ts

    now = now or _now()
    factors: list[Factor] = []

    fixes = db.query(m.Fix).filter_by(tenant_id=tenant.id, host=host,
                                      action=action, ok=True).all()
    n = len(fixes)
    factors.append(Factor(
        name="host_history",
        question=f"Has Vault run this on {host} successfully before?",
        answer=f"yes, {n} times" if n else "never on this host",
        weight=min(30, 10 * n) if n else -20,
        why=("It has done this here before and the command succeeded."
             if n else "Vault has never repaired this host this way."),
    ))

    # `held` used to be read off `Alert.count`, but `ingest.store_scan` bumps
    # `count` on every scan a condition is still open (see its own comment,
    # "the sensor bumps `count` every scan a condition persists") -- so for
    # any alert more than a few minutes old `count` is in the hundreds and
    # "recurred = count - 1" pins this factor at its floor for every genuine
    # alert, which is a constant wearing a factor's clothes. The real
    # question is narrower and answerable: did the LAST successful repair of
    # this exact (host, action) actually make the alert stop firing? So this
    # compares the alert's `last_seen` against the newest successful `Fix`
    # for this host/action (reusing `fixes` above, since only `ok=True` rows
    # count as a repair having "happened").
    #
    # `Fix.ts` and `Alert.last_seen` are both plain `String` columns carrying
    # whatever UTC offset the sensor wrote (see `payloads/history.py`'s notes
    # on `Alert.first_seen` for the same trap), so a SQL `>=`/`>` comparison
    # on either is lexicographic and can silently pick the wrong "later" row
    # across offsets. Both are parsed with `scans.parse_ts` and compared as
    # aware datetimes instead, never compared as strings.
    _MIN_TS = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    newest_fix = max(fixes, key=lambda f: parse_ts(f.ts) or _MIN_TS) if fixes else None
    # `alert_key` is matched RAW here, deliberately not canonicalized -- the
    # opposite of what the `undone` block does with the same value eleven
    # lines below. The two dialects are real and both load-bearing:
    # `m.Alert.key` is stored NAMESPACED (`ingest.py` rewrites a LAN alert to
    # `networks.lan_alert_key(...)` on the way in), while `Edit.body["key"]`
    # is written in the sensor's legacy CANONICAL dialect
    # (`routers/write.py` stores `networks.canonical_lan_key(key)`). This is
    # not the general "caller might hold either shape" case that
    # `networks.find_alert()` exists to tolerate -- `assess()` always
    # receives `alert.key` verbatim from its caller (Task 4 passes it
    # straight through for exactly this reason), so canonicalizing it here,
    # or "tidying" this to match the `undone` block below, would silently
    # fail to find the row for every namespaced LAN alert and pin `held` at
    # its positive case ("has not been seen again") for every one of them.
    alert = db.query(m.Alert).filter_by(tenant_id=tenant.id, key=alert_key).first()
    did_not_hold = False
    if newest_fix is not None and alert is not None:
        fix_ts = parse_ts(newest_fix.ts)
        last_seen = parse_ts(alert.last_seen)
        did_not_hold = bool(fix_ts and last_seen and last_seen > fix_ts)
    factors.append(Factor(
        name="held",
        question=f"Did the last successful repair on {host} make the alert stop firing?",
        answer=("seen again after that repair ran" if did_not_hold
                else "no repair of this kind has run here yet" if newest_fix is None
                else "not seen again since that repair"),
        weight=-25 if did_not_hold else 10,
        why=("The alert kept firing after Vault's own repair, so running "
             "it again unattended repeats something that is not working."
             if did_not_hold else
             "Nothing on record says this repair does not hold."),
    ))

    verified = confirmed_repairs(db, tenant, host=host, action=action)
    factors.append(Factor(
        name="verified",
        question="Did later evidence confirm those repairs worked?",
        answer=f"{verified} of {n} confirmed" if n else "nothing to confirm",
        weight=15 if verified >= PROMOTE_MIN_SUCCESSES else (0 if not n else -10),
        # Three cases, not two. The no-history case used to fall into the
        # sentence below and say "Vault ran the repair but never saw it
        # take effect" while the answer beside it said "nothing to confirm" --
        # a factor contradicting itself, about a repair that never ran, and
        # `remedy._confidence_why` can put that sentence in front of a
        # customer. The weight was always right (0, not -10); only the prose
        # was wrong, which is the harder kind to notice because nothing
        # arithmetical moves.
        why=("Later scans showed the finding actually cleared."
             if verified >= PROMOTE_MIN_SUCCESSES else
             "No repair of this kind has run here, so there is nothing to "
             "confirm either way." if not n else
             "Vault ran the repair but never saw it take effect."),
    ))

    # `undone` used to search `Edit.path.like("%undo%")`, but no path Vault
    # ever writes contains "undo" (see the enumeration in `Edit.path`'s own
    # comment in models.py) -- the factor could never fire, which meant the
    # plan's safety invariant ("a repair a person undid is never promoted")
    # had nothing backing it. The real record of a person overruling Vault
    # is an `/api/alert` Edit whose body is a "reopen" of THIS alert's key.
    #
    # Two qualifiers matter:
    #   - `who != "system"`: `ingest.py` writes its own "system" reopen when
    #     a resolved condition re-fires on the next scan. That is ordinary
    #     operation, not a person overruling Vault, and counting it would
    #     make the factor trip on every flapping alert.
    #   - the body's `key` must equal THIS alert's key: an unscoped query
    #     would let one reopen anywhere on the tenant set -25 on every
    #     decision for that customer forever.
    # `Edit.ts` is a real `TZDateTime` (unlike the sensor-authored strings
    # above), so the SQL bound below is honestly chronological.
    #
    # The whole predicate is in SQL, `body` included, and it has to be. This
    # used to fetch every non-system `/api/alert` Edit of the last 180 days
    # and count the matches in Python, on the grounds that the lookback kept
    # the query from growing with the tenant's whole history -- but ack,
    # snooze, resolve and reopen ALL write an `/api/alert` Edit
    # (`routers/write.py`, `routers/public.py`), so on an active tenant six
    # months of them is the working set of the alert list itself, not a
    # handful of reopens. And `assess` runs once per open alert on every
    # dashboard load (`payloads/incidents.py` resolves per row, and the inbox
    # pays it twice because `_counts` resolves per open alert and `build`
    # resolves again per row), so the rows-into-Python cost is multiplied by
    # the number of alerts on the page.
    #
    # Both halves are comparable in SQL because neither depends on the row:
    # the canonical key is computed here, before the query, and the action is
    # the literal "reopen". `body` is a plain `sqlalchemy.JSON` column (see
    # `models.Edit`), so indexing it renders `JSON_EXTRACT` on SQLite -- which
    # is what the suite and the demo run on -- and `->>` on Postgres.
    # `.as_string()` is required rather than decorative: without it the
    # comparison is made against the JSON *representation* and a quoted
    # `"reopen"` never equals `reopen`.
    #
    # A row whose `body` has no `key` yields SQL NULL, and `NULL = 'x'` is not
    # true, which is the same answer `dict.get(...) == canon_key` gave -- so
    # this is the identical predicate, evaluated one layer down. It is still
    # `count()`, not `all()`: the number of reopens is the only thing the
    # factor reads.
    canon_key = networks.canonical_lan_key(alert_key)
    cutoff = now - dt.timedelta(days=UNDONE_LOOKBACK_DAYS)
    undone = (
        db.query(m.Edit)
        .filter(
            m.Edit.tenant_id == tenant.id,
            m.Edit.path == "/api/alert",
            m.Edit.who != "system",
            m.Edit.ts >= cutoff,
            m.Edit.body["action"].as_string() == "reopen",
            m.Edit.body["key"].as_string() == canon_key,
        )
        .count()
    )
    # The factor MATCHES on `canon_key` above and NAMES neither key below --
    # see this function's docstring for why no question prints an identifier.
    # Worth stating here too, because this is the one factor whose match is
    # per-alert rather than per-action, so "scope it to this alert" and "say
    # which alert" read as the same requirement and are not.
    factors.append(Factor(
        name="undone",
        question="Has a person reopened this alert after Vault called it resolved?",
        answer=f"reopened by a person {undone} times" if undone else "never reopened by a person",
        weight=-25 if undone else 0,
        why=("Somebody overruled Vault's own resolve on this exact alert, "
             "which is a judgement Vault should not act past unattended."
             if undone else "No person has reopened this alert."),
    ))

    reversible = action not in _tier3()
    factors.append(Factor(
        name="reversible",
        question="Is this repair reversible?",
        answer="yes" if reversible else "no, it is disruptive or hard to undo",
        weight=10 if reversible else -40,
        why=("It can be put back if it turns out to be wrong."
             if reversible else "Getting this wrong cannot be quietly undone."),
    ))

    scan = latest_scan(db, tenant.id)
    seen = parse_ts(scan.ts) if scan else None
    age_min = (now - seen).total_seconds() / 60 if seen else None
    # THIS tenant's threshold, not a module constant -- see
    # `_fresh_threshold_min`. `assess` holds the tenant, so there is no reason
    # to answer "is the sensor reporting" differently from the watchdog that
    # raises `sensor:silent` on the same estate.
    fresh_min = _fresh_threshold_min(tenant)
    # A scan stamped AHEAD of `now` is not fresh -- it is not evidence that
    # anything could be verified afterwards, whether from real clock skew on
    # the sensor host or a caller-supplied `now` that predates the sensor's
    # own clock. `age_min <= fresh_min` alone lets a negative age
    # (from-the-future scan) through, so the lower bound is required too.
    fresh = age_min is not None and 0 <= age_min <= fresh_min
    factors.append(Factor(
        name="sensor_fresh",
        question=(f"Is the sensor reporting often enough to confirm the result "
                  f"(within {fresh_min} min)?"),
        answer=(f"last scan {int(age_min)} min ago" if age_min is not None
                else "no scan at all"),
        weight=5 if fresh else -15,
        why=("A repair Vault cannot check afterwards is one it should be "
             "slower to make on its own."
             if not fresh else "Vault will see whether this worked."),
    ))

    score = score_of(factors)
    return Confidence(score=score, verdict=verdict_of(score, tier), factors=factors,
                      confirmed=verified)


def gate(kind: str, conf: Confidence, *, action: str, tier: int,
         verified_successes: int, recurred_since: bool, ever_undone: bool,
         sensor_fresh: bool, auto_heal: bool, caller_may_run: bool) -> tuple[str, str]:
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
    means nothing Vault runs repairs this, and no number changes that.

    **Sharpened, because its absence caused a real defect: removing caution
    can only ever mean SKIPPING A CONFIRMATION, never GRANTING ACCESS.**
    Those are two different things that arrive here wearing one word.
    `remedy.resolve_action` reaches `kind="approval"` from two unrelated
    causes -- "anyone may run this, but a person must confirm it first"
    (`tier >= 3`) and "this caller has no right to run it at all"
    (`role != "admin"`, which `AlertRemedy.min_role` and every write route
    independently enforce). Only the first is a confirmation this function
    may ever decide to skip. `caller_may_run` is how it tells them apart,
    and it has no default on purpose: a caller that forgets it gets a
    `TypeError`, not a promotion. Before it existed, the promotion branch
    was reachable ONLY for a caller who may not run the repair -- an admin
    below tier 3 is already `auto` and never enters the branch, and an admin
    at tier 3 is refused by the tier guard below -- so the one case it could
    fire on was the one the spec's invariant forbids. The operator's row
    then left the Needs-you list and its badge count, still offered no Fix
    button (`min_role` held), and minted no chat proposal (the handoff keys
    on `kind == "approval"`): a repair with no route to it and no row to
    click. Widening `min_role` is a decision for a person, never for a
    score.

    The return is `(kind, reason)`, and a caller MUST branch on `kind`
    changing, never on whether `reason` is non-empty. Two of the branches
    below leave `kind` exactly as it came in ("kill switch is off",
    "not enough verified history yet") but still hand back prose, because
    the customer is owed a reason `gate` looked and declined, not only a
    reason it acted. Treating a non-empty `reason` as "the gate promoted or
    demoted this" would read those declines as changes that never happened;
    treating an empty `reason` as "nothing to say" would miss that several
    of the do-nothing branches (tier 3, a caller who may not run it at all,
    recurrence, undone, stale sensor, score under `HIGH_WATER`) are silent on
    purpose, because they are the ordinary case of "still approval, no news."
    """
    if kind == "human":
        return kind, ""

    if kind == "auto" and conf.score < LOW_WATER:
        return "approval", (
            f"Vault is only {conf.score}% sure about this one, so it is asking "
            f"first even though it would normally just do it.")

    if kind != "approval":
        return kind, ""

    # First of the promotion bars, and the only one that is about the person
    # rather than the repair. Silent, like the tier-3 bar below and for the
    # same reason: this caller could not have run it before the rubric looked
    # and cannot after, so there is no news -- the sentence they are owed is
    # the one `resolve_action` already composed about needing an admin, and
    # `remedy._confidence_why` still fills `confidence_why` from the factors.
    if not caller_may_run:
        return kind, ""

    if tier >= 3 or action in _tier3():
        return kind, ""
    if not auto_heal:
        return kind, "Automatic repair is switched off for the whole platform."
    if verified_successes < PROMOTE_MIN_SUCCESSES:
        return kind, (
            "Vault does not have enough verified history of this repair "
            "on this host to run it unattended yet.")
    if recurred_since or ever_undone or not sensor_fresh:
        return kind, ""
    if conf.score < HIGH_WATER:
        return kind, ""

    return "auto", (
        f"Vault has done exactly this on this host {verified_successes} times, "
        f"each confirmed afterwards, and the finding has not come back — so it is "
        f"doing it rather than asking.")


def _tier3() -> frozenset:
    """The disruptive actions, as `chat.tiers` defines them. The ONE accessor.

    Both readers in this module go through here -- `assess`'s `reversible`
    factor and `gate`'s promotion bar. `assess` used to import `TIER3_FIXES`
    directly while `gate` came via this function, which is two accessors for
    one set inside one module: not wrong today, and exactly the shape that
    ends up with one of them pointing at a stale copy after somebody moves
    the table.

    The import is function-local, like every `guardian_hub` reference in this
    module, and that is the reason this is a function at all. `confidence.py`
    imports nothing from the rest of the package at module level, so it stays
    importable and unit-testable with no app and no database -- which is what
    lets `test_confidence_gate.py` drive every promotion invariant as a pure
    function -- named that way rather than counted, because how many invariants
    there are goes stale the next time somebody adds one and what they pin does
    not. A module-level `from .chat.tiers import TIER3_FIXES` would pull
    in `chat.tiers`, which imports `..config` and reaches the settings and
    database machinery this module must not need.

    `frozenset` rather than the imported name straight through: the only
    operation either caller performs is membership, and handing back an
    immutable copy means neither of them can mutate the tier table that
    `tests/test_remedy.py` pins against the sensor's own `fix.FIX_ALERT_KEY`.
    """
    from .chat.tiers import TIER3_FIXES
    return frozenset(TIER3_FIXES)
