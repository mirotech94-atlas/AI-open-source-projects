"""The learning layer — JUDGMENT.md, built to it.

The hard limits come first, because they are the contract: no import path
to the executor, no tier change, no Tier 3, no external data, abstain below
40 rows. Everything after that is whether it learns well.
"""
from __future__ import annotations

import ast
import pathlib
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from atlas.kernel.judgment import abstain, calibrate, encode, explain, monitor, tournament
from atlas.kernel.judgment.heads import BY_NAME, HEADS
from atlas.kernel.judgment.policy import Advice, Prediction

ROOT = pathlib.Path(__file__).resolve().parent.parent / "atlas" / "kernel"
PKG = ROOT / "judgment"


# ------------------------------------------------------------ hard limits

def _imports_of(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:                       # relative: resolve to kernel.*
                base = "atlas.kernel" if node.level == 2 else "atlas.kernel.judgment"
                mod = f"{base}.{mod}" if mod else base
            names.add(mod)
            names.update(f"{mod}.{a.name}" for a in node.names)
    return names


def _kernel_file(mod: str) -> pathlib.Path | None:
    if not mod.startswith("atlas.kernel"):
        return None
    tail = mod.removeprefix("atlas.kernel").strip(".")
    cand = ROOT / (tail.replace(".", "/") + ".py") if tail else None
    if cand and cand.exists():
        return cand
    cand = ROOT / tail.replace(".", "/") / "__init__.py" if tail else None
    return cand if cand and cand.exists() else None


def test_no_import_path_from_judgment_to_the_executor():
    """Transitively: judgment, and everything in the kernel it imports,
    and everything THOSE import. If any of it names the executor, a model
    has a path to running something, and that is the one thing it may
    never have."""
    seen: set[pathlib.Path] = set()
    todo = list(PKG.glob("*.py"))
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        for mod in _imports_of(f):
            assert not mod.startswith("atlas.kernel.executor"), \
                f"{f.name} reaches the executor via {mod}"
            assert "execute" not in mod.rsplit(".", 1)[-1] or "sql" in mod, \
                f"{f.name} imports {mod}"
            nxt = _kernel_file(mod)
            if nxt is not None and nxt not in seen:
                todo.append(nxt)
    assert any(p.name == "policy.py" for p in seen)       # the walk ran


def test_judgment_never_assigns_a_tier_or_a_decision():
    """No statement in the package writes to REGISTRY, to a Decision, or to
    an Action's tier. It reads the tier to refuse Tier 3; it changes nothing."""
    for f in PKG.glob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                src = ast.unparse(t)
                assert "REGISTRY" not in src, f"{f.name}: assigns to {src}"
                assert not src.endswith(".tier"), f"{f.name}: assigns a tier: {src}"
                assert "decisions" not in src, f"{f.name}: assigns {src}"
        text = f.read_text()
        assert "standing_rules" not in text.lower() or f.name == "policy.py"
        assert "classify(" not in text, f"{f.name} calls permissions.classify"


async def test_tier_3_is_refused_before_a_single_feature_is_computed(monkeypatch):
    from atlas.kernel import registry as reg
    from atlas.kernel.contracts import Tier
    from atlas.kernel.judgment import log as jlog
    from atlas.kernel.judgment import policy
    from atlas.kernel.permissions import Decision

    monkeypatch.setitem(reg.REGISTRY.decisions, "cloudco.delete_vm",
                        Decision(False, Tier.NEVER, "permanently denied: destroys data"))

    async def boom(*a, **k):
        raise AssertionError("features were computed for a Tier 3 action")
    monkeypatch.setattr(jlog, "features_for", boom)

    pred = await policy.predict("cloudco.delete_vm", 2)
    assert pred.abstained and pred.p is None
    assert pred.advice is Advice.ABSTAIN
    assert "tier 3" in pred.why


def test_the_only_training_source_is_the_decision_log():
    text = (PKG / "log.py").read_text()
    assert "FROM decision_log" in text
    for f in PKG.glob("*.py"):
        t = f.read_text()
        assert "httpx" not in t and "urllib" not in t and "requests" not in t, \
            f"{f.name} can reach the network"
        assert "read_csv" not in t and "load_dataset" not in t


def test_below_forty_rows_every_head_abstains():
    env = abstain.Envelope(n_rows=39, pair_counts={"cloudco.snapshot_all": 39},
                           mean=(0.5,) * 13, std=(0.1,) * 13)
    v = abstain.check(env, np.zeros(encode.WIDTH), "cloudco.snapshot_all")
    assert v.abstain and "39" in v.reason
    assert abstain.check(None, np.zeros(encode.WIDTH), "x.y").abstain


# --------------------------------------------------------------- encoder

def _feat(**over) -> encode.DecisionFeatures:
    base = {"module": "cloudco", "action": "cloudco.snapshot_all", "severity": 1,
            "reversible": True, "blast_radius": 1, "cost_usd": 0.0, "hour": 14,
            "weekday": 2, "quiet_hours": False, "times_seen": 12,
            "prior_approve_rate": 0.9, "days_since_similar": 3.0,
            "you_were_busy": False}
    base.update(over)
    return encode.DecisionFeatures(**base)


def test_encoder_is_forty_wide_and_every_slot_is_named():
    assert encode.WIDTH == 40
    assert len(encode.NAMES) == 40 and len(set(encode.NAMES)) == 40
    x = encode.encode(_feat())
    assert x.shape == (40,) and x.dtype == np.float32
    assert x[encode.NAMES.index("module=cloudco")] == 1.0
    assert x[encode.NAMES.index("family=snapshot")] == 1.0
    assert 0 <= x.min() and x.max() <= 1.0


def test_unknown_module_and_verb_fall_into_other_not_an_error():
    x = encode.encode(_feat(module="newthing", action="newthing.frobnicate"))
    assert x[encode.NAMES.index("module=other")] == 1.0
    assert x[encode.NAMES.index("family=other")] == 1.0


def test_hour_is_his_clock_and_wraps():
    a = encode.encode(_feat(hour=23))
    b = encode.encode(_feat(hour=0))
    i, j = encode.NAMES.index("hour_sin"), encode.NAMES.index("hour_cos")
    assert abs(a[i] - b[i]) < 0.15 and abs(a[j] - b[j]) < 0.15


# ---------------------------------------------------------------- labels

def test_three_classes_and_expiry_is_never_a_denial():
    from atlas.kernel.judgment.log import label
    assert label("approved", "approve") == 1
    assert label("denied", "approve") == 0
    assert label("expired", "approve") is None          # excluded, not 0
    assert label("expired", "answer") == 0              # its own question
    assert label("approved", "answer") == 1
    assert label("denied", "answer") == 1
    with pytest.raises(ValueError):
        label("approved", "nope")


# ------------------------------------------------------------ tournament

def _synthetic(n: int, seed: int = 0, drift_at: float | None = None):
    """Rows oldest-first. He approves reversible, cheap, daytime things and
    denies the rest — with a taste that CHANGES at `drift_at` if given."""
    rng = np.random.default_rng(seed)
    X, y = [], []
    for i in range(n):
        f = _feat(reversible=bool(rng.random() < 0.6),
                  cost_usd=float(rng.choice([0, 0, 0, 40, 200])),
                  hour=int(rng.integers(0, 24)),
                  prior_approve_rate=float(rng.random()),
                  times_seen=int(rng.integers(0, 40)))
        score = (1.5 * f.reversible - 0.01 * f.cost_usd
                 - (1.0 if f.hour < 7 else 0.0) + 2 * (f.prior_approve_rate - 0.5))
        if drift_at is not None and i >= drift_at * n:
            score = -score
        p = 1 / (1 + np.exp(-score))
        X.append(encode.encode(f))
        y.append(int(rng.random() < p))
    return np.asarray(X), np.asarray(y)


def test_split_is_by_time_oldest_train_newest_test():
    X, y = _synthetic(100)
    Xtr, Xte, ytr, yte = tournament.split_by_time(X, y)
    assert len(ytr) == 70 and len(yte) == 30
    assert np.array_equal(Xtr, X[:70]) and np.array_equal(Xte, X[70:])


def test_random_split_flatters_a_model_that_time_split_exposes():
    """The failure the spec names. Taste flips two-thirds of the way in. A
    random split mixes the eras and scores well; the time split trains on
    the old taste and is judged on the new, and says so."""
    X, y = _synthetic(600, drift_at=0.7)
    Xtr, Xte, ytr, yte = tournament.split_by_time(X, y)
    honest = calibrate.brier(yte, calibrate.fit_calibrated(
        BY_NAME["logistic"], Xtr, ytr).predict_proba(Xte)[:, 1])
    rng = np.random.default_rng(1)
    idx = rng.permutation(len(y))
    cut = int(0.7 * len(y))
    flattering = calibrate.brier(y[idx[cut:]], calibrate.fit_calibrated(
        BY_NAME["logistic"], X[idx[:cut]], y[idx[:cut]]).predict_proba(X[idx[cut:]])[:, 1])
    assert honest > flattering + 0.05


def test_heads_without_data_lose_and_are_logged_not_raised():
    X, y = _synthetic(120)
    r = tournament.run(X, y, incumbent=None)
    assert r.scores["logistic"] < 0.25            # beats a coin flip
    assert r.scores["gradient"] == float("inf")   # needs 400
    assert r.scores["neural"] == float("inf")     # needs 2000
    assert r.winner == "logistic" and r.changed
    assert "neural —" in r.line()


def test_neural_competes_and_loses_below_two_thousand_rows():
    X, y = _synthetic(900)
    r = tournament.run(X, y, incumbent="logistic")
    assert r.scores["neural"] == float("inf")
    assert r.scores["gradient"] < 0.25
    assert r.winner in ("logistic", "gradient")


def test_a_tie_keeps_the_incumbent(monkeypatch):
    from atlas.kernel.judgment import tournament as t

    class Same:
        def predict_proba(self, X):
            return np.tile([0.4, 0.6], (len(X), 1))

    monkeypatch.setattr(t, "fit_calibrated", lambda head, X, y: Same())
    X, y = _synthetic(100)
    r = t.run(X, y, incumbent="logistic")
    assert len({v for v in r.scores.values()}) == 1
    assert r.winner == "logistic" and not r.changed
    assert "holds" in r.line()


def test_a_strictly_better_head_takes_over(monkeypatch):
    from atlas.kernel.judgment import tournament as t

    class Fixed:
        def __init__(self, p): self.p = p
        def predict_proba(self, X):
            return np.tile([1 - self.p, self.p], (len(X), 1))

    X, y = _synthetic(100)
    y[:] = 1
    monkeypatch.setattr(t, "fit_calibrated",
                        lambda head, X, y: Fixed({"logistic": 0.6, "gradient": 0.9,
                                                  "neural": 0.7}[head.name]))
    r = t.run(X, y, incumbent="logistic")
    assert r.winner == "gradient" and r.changed
    assert "gradient takes over" in r.line()


def test_demotion_after_three_rising_weeks_and_not_before():
    assert tournament.should_demote([0.10, 0.12, 0.14, 0.16])
    assert not tournament.should_demote([0.10, 0.12, 0.11, 0.16])
    assert not tournament.should_demote([0.10, 0.12, 0.14])
    assert tournament.simpler_than("neural") == "gradient"
    assert tournament.simpler_than("gradient") == "logistic"
    assert tournament.simpler_than("logistic") is None


def test_the_neural_head_is_exactly_the_specified_shape():
    net = BY_NAME["neural"].build()
    assert net.hidden_layer_sizes == (32, 16)
    assert net.activation == "relu" and net.solver == "adam"
    assert net.alpha == 1e-4 and net.learning_rate_init == 1e-3
    assert net.batch_size == 32 and net.n_iter_no_change == 15
    assert net.early_stopping
    assert BY_NAME["neural"].min_rows == 2000
    assert [h.name for h in HEADS] == ["logistic", "gradient", "neural"]


# ------------------------------------------------------------- calibrate

def test_platt_below_a_thousand_rows_isotonic_above():
    assert calibrate.method_for(999) == "sigmoid"
    assert calibrate.method_for(1000) == "isotonic"


def test_calibrated_eighty_means_roughly_eighty():
    X, y = _synthetic(1200, seed=3)
    Xtr, Xte, ytr, yte = tournament.split_by_time(X, y)
    pipe = calibrate.fit_calibrated(BY_NAME["logistic"], Xtr, ytr)
    p = pipe.predict_proba(Xte)[:, 1]
    rel = monitor.reliability(p, yte, "logistic")
    for b in rel.bins:
        if b.n >= 30:
            assert abs(b.predicted - b.observed) < 0.2, (b.predicted, b.observed, b.n)
    assert rel.brier < 0.25


def test_calibration_refuses_a_single_class():
    X, y = _synthetic(80)
    y[:] = 1
    assert calibrate.fit_calibrated(BY_NAME["logistic"], X, y) is None


# --------------------------------------------------------------- abstain

def test_abstains_on_a_pair_it_has_barely_seen():
    env = abstain.Envelope(200, {"cloudco.snapshot_all": 150, "mail.draft_reply": 2},
                           (0.5,) * 13, (0.2,) * 13)
    x = encode.encode(_feat())
    assert not abstain.check(env, x, "cloudco.snapshot_all").abstain
    v = abstain.check(env, x, "mail.draft_reply")
    assert v.abstain and "2 time" in v.reason
    assert abstain.check(env, x, "never.seen").abstain


def test_abstains_on_a_value_outside_everything_seen():
    X, _ = _synthetic(300)
    env = abstain.Envelope.from_training(X, ["cloudco.snapshot_all"] * 300)
    normal = encode.encode(_feat(cost_usd=0))
    assert not abstain.check(env, normal, "cloudco.snapshot_all").abstain
    wild = encode.encode(_feat(cost_usd=500.0))
    v = abstain.check(env, wild, "cloudco.snapshot_all")
    assert v.abstain and "cost_usd" in v.reason


# --------------------------------------------------------------- explain

def test_every_number_comes_with_three_reasons_in_his_words():
    X, y = _synthetic(400, seed=5)
    pipe = calibrate.fit_calibrated(BY_NAME["logistic"], X, y)
    x = encode.encode(_feat(reversible=True, hour=4, prior_approve_rate=0.95,
                            times_seen=30))
    reasons = explain.explain(pipe, x, X.mean(axis=0), pair="cloudco.snapshot_all",
                              seen=30, rate=0.95)
    assert len(reasons) == 3
    texts = [r.text for r in reasons]
    assert len(set(texts)) == 3
    assert any(t in ("you always approve this", "it's reversible", "it's the hour",
                     "it's the hour — you usually say no now") for t in texts), texts


def test_explain_works_for_the_gradient_head_too():
    X, y = _synthetic(600, seed=7)
    pipe = calibrate.fit_calibrated(BY_NAME["gradient"], X, y)
    assert pipe is not None
    reasons = explain.explain(pipe, encode.encode(_feat()), X.mean(axis=0),
                              pair="cloudco.snapshot_all", seen=12, rate=0.9)
    assert len(reasons) == 3 and all(r.text for r in reasons)


# --------------------------------------------------------------- monitor

def test_the_one_line_that_matters():
    p = np.array([0.8] * 30 + [0.2] * 4)
    y = np.array([1] * 23 + [0] * 7 + [0] * 4)
    rel = monitor.reliability(p, y, "neural")
    assert rel.line() == ("When I said 80%, you said yes 77% of the time. "
                          "30 samples. Serving: neural.")
    assert monitor.reliability(np.array([]), np.array([]), None).line() \
        == "No scored decisions yet. Serving: nobody."


def test_weekly_brier_is_oldest_first():
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    at = [t0 + timedelta(days=d) for d in (0, 1, 8, 9, 16)]
    p = np.array([0.9, 0.9, 0.5, 0.5, 0.1])
    y = np.array([1, 1, 1, 0, 1])
    w = monitor.weekly_brier(at, p, y)
    assert len(w) == 3
    assert w[0] < w[1] < w[2]


# ---------------------------------------------------------------- policy

def _pred(p, seen=25, reversible=True, abstained=False):
    return Prediction(p, "logistic", ("you always approve this", "it's reversible",
                                      "it's daytime"), abstained, "", seen, reversible)


def test_what_the_number_may_do():
    assert _pred(0.97).advice is Advice.STANDING_RULE
    assert _pred(0.97, reversible=False).advice is Advice.CONFIDENT
    assert _pred(0.97, seen=10).advice is Advice.CONFIDENT
    assert _pred(0.85).advice is Advice.CONFIDENT
    assert _pred(0.5).advice is Advice.ASK
    assert _pred(0.2).advice is Advice.SUPPRESS
    assert _pred(0.2, seen=5).advice is Advice.ASK        # not enough history to hush
    assert _pred(None, abstained=True).advice is Advice.ABSTAIN


def test_a_card_never_carries_a_number_without_its_reasons():
    line = _pred(0.85).card_line()
    assert "85%" in line and "you always approve this" in line
    assert "it's reversible" in line
    assert _pred(None, abstained=True).card_line() == ""


def test_registry_refuses_a_model_of_the_wrong_width():
    from atlas.kernel.judgment import registry
    row = {"id": 1, "width": 39, "at": None, "target": "approve", "head": "x",
           "brier": 0.1, "n_train": 1, "n_test": 1, "blob": b"", "envelope": "{}",
           "mean": []}
    assert registry._served(row) is None
    assert registry._served(None) is None


# ------------------------------------------------------- restart durability

async def test_a_restart_after_the_hour_does_not_rerun_the_tournament(monkeypatch):
    """The scheduler's once-a-day guard is process memory, so every restart
    after 03:00 used to start a full bake-off. The tournament log is the
    durable record: if it has a row since midnight, run_all does nothing."""
    from atlas.kernel.judgment import nightly

    async def ran(*a, **k):
        return {"x": 1}
    monkeypatch.setattr(nightly, "fetch_one", ran)

    async def boom(*a, **k):
        raise AssertionError("tournament ran although one already ran today")
    monkeypatch.setattr(nightly, "tournament", boom)
    assert await nightly.run_all() == []


async def test_new_decisions_are_counted_from_the_last_logged_tournament(monkeypatch):
    """'Since the last run' comes from judgment_tournaments, not a module
    global that a fresh process starts without — which made the gate count
    the whole log and always open."""
    from datetime import UTC, datetime

    from atlas.kernel.judgment import nightly

    last = datetime(2026, 9, 24, 3, 0, tzinfo=UTC)
    seen = {}

    async def fetch(sql, *args):
        return {"at": last}
    monkeypatch.setattr(nightly, "fetch_one", fetch)

    async def new_since(at):
        seen["at"] = at
        return 3                      # fewer than ten: nothing to do
    monkeypatch.setattr(nightly._log, "new_since", new_since)

    async def boom(*a, **k):
        raise AssertionError("training rows loaded although the gate is shut")
    monkeypatch.setattr(nightly._log, "training_rows", boom)

    assert await nightly.tournament("approve") == []
    assert seen["at"] == last
