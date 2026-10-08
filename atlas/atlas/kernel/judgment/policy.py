"""What the number may do — and, in code, what it may not.

    p >= 0.95, seen >= 20, reversible -> STANDING_RULE: draft a rule for him
                                         to confirm once. Not a promotion.
    p >= 0.80                          -> CONFIDENT: propose, "you usually say yes"
    0.30 <= p < 0.80                   -> ASK: propose, show the number
    p < 0.30, seen >= 20               -> SUPPRESS: don't raise it; one line
                                         in the brief
    ABSTAIN                            -> always ask, never guess

Hard limits, each with a test:

  * `predict` refuses a Tier 3 action before computing a feature. No
    probability, no sample count, no standing rule reaches money out, DNS,
    deletion, credential revocation or self-modification.
  * Nothing here can run an action: the package has no import path to
    executor.execute, and `Prediction` carries data only.
  * Nothing here changes a tier. STANDING_RULE hands text to rules.capture,
    which records his words for the brief and is itself read by nothing
    that gates. Promotion to Tier 1 is a code change he makes, once.
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field

import numpy as np

from ..contracts import Tier
from ..registry import REGISTRY
from . import abstain as _abstain
from . import explain as _explain
from . import log as _log
from . import registry as _models
from .encode import DecisionFeatures, encode

log = logging.getLogger("atlas.judgment")


class Advice(enum.StrEnum):
    STANDING_RULE = "standing_rule"
    CONFIDENT = "confident"
    ASK = "ask"
    SUPPRESS = "suppress"
    ABSTAIN = "abstain"


STANDING_P, STANDING_SEEN = 0.95, 20
CONFIDENT_P = 0.80
SUPPRESS_P, SUPPRESS_SEEN = 0.30, 20


@dataclass(frozen=True, slots=True)
class Prediction:
    p: float | None                       # None when abstaining
    head: str                             # serving head, or "abstain"
    reasons: tuple[str, ...]              # top three, in his words
    abstained: bool
    why: str                              # why abstained, if it did
    seen: int
    reversible: bool
    features: DecisionFeatures | None = None
    vector: np.ndarray | None = field(default=None, repr=False, compare=False)
    p_answer: float | None = None         # P(he answers at all)

    @property
    def advice(self) -> Advice:
        if self.abstained or self.p is None:
            return Advice.ABSTAIN
        if self.p >= STANDING_P and self.seen >= STANDING_SEEN and self.reversible:
            return Advice.STANDING_RULE
        if self.p >= CONFIDENT_P:
            return Advice.CONFIDENT
        if self.p < SUPPRESS_P and self.seen >= SUPPRESS_SEEN:
            return Advice.SUPPRESS
        return Advice.ASK

    def percent(self) -> str:
        return "—" if self.p is None else f"{round(self.p * 100)}%"

    def card_line(self) -> str:
        """The line that goes on the card. Never a number without reasons."""
        if self.abstained:
            return ""
        why = ", ".join(self.reasons[:3])
        lead = {Advice.STANDING_RULE: "you always say yes",
                Advice.CONFIDENT: "you usually say yes",
                Advice.SUPPRESS: "you usually say no"}.get(self.advice, "")
        head = f"I'd put this at {self.percent()}"
        return (f"{head} — {lead} ({why})" if lead else f"{head} ({why})")


def _refuse(action: str, why: str) -> Prediction:
    return Prediction(None, "abstain", (), True, why, 0, False)


async def predict(action: str, severity: int, *, at=None) -> Prediction:
    """P(Miro approves) for this action right now, with reasons — or ABSTAIN.

    Tier 3 is refused first. The registry's decision for a NEVER action is
    the only thing consulted, and it is read, never written.
    """
    decision = REGISTRY.decisions.get(action)
    if decision is None or decision.tier is Tier.NEVER or not decision.allowed:
        return _refuse(action, "tier 3 or unknown: the learning layer does not look")
    spec = REGISTRY.actions.get(action)
    reversible = getattr(spec, "rollback", None) is not None

    # A learning layer that cannot read its log abstains; it never blocks a
    # proposal and it never guesses. Asking is the safe default it was
    # given, and this is where that default is kept.
    try:
        f = await _log.features_for(action, severity, int(decision.tier),
                                    reversible=reversible, at=at)
        x = encode(f)
        served = await _models.serving("approve")
    except Exception as exc:                            # noqa: BLE001
        log.warning("judgment unavailable, abstaining: %s", type(exc).__name__)
        return _refuse(action, f"learning layer unavailable: {type(exc).__name__}")
    env = served.envelope if served else None
    pair = f"{f.module}.{f.action}"
    verdict = _abstain.check(env, x, pair)
    if verdict.abstain:
        return Prediction(None, "abstain", (), True, verdict.reason,
                          f.times_seen, reversible, f, x)

    assert served is not None
    p = float(served.model.predict_proba(x.reshape(1, -1))[0, 1])
    reasons = _explain.explain(served.model, x, served.mean, pair=pair,
                               seen=f.times_seen, rate=f.prior_approve_rate)
    p_answer = None
    ans = await _models.serving("answer")
    if ans is not None:
        p_answer = float(ans.model.predict_proba(x.reshape(1, -1))[0, 1])
    return Prediction(p, served.head, tuple(r.text for r in reasons), False, "",
                      f.times_seen, reversible, f, x, p_answer)


async def record(proposal_id: int, pred: Prediction, tier: int) -> None:
    """Write the row the moment the proposal exists. Called by approvals.py.
    A failure here is logged and swallowed: the proposal must still reach
    him even if the learning layer's table is unwritable."""
    if pred.features is None or pred.vector is None:
        return
    try:
        await _log.record(proposal_id, pred.features, tier, pred.vector, pred.p,
                          pred.head, list(pred.reasons))
    except Exception:
        log.exception("could not log proposal #%s for learning", proposal_id)


async def record_outcome(proposal_id: int, outcome: str, via: str = "") -> None:
    """approved | denied | expired. Called by approvals.py only."""
    try:
        await _log.resolve(proposal_id, outcome, via)
    except Exception:
        log.exception("could not record the outcome of #%s", proposal_id)


def standing_rule_text(action: str, pred: Prediction) -> str:
    """The rule ATLAS drafts at p >= 0.95. In his words' shape, for him to
    confirm once. It records the answer; it does not remove the gate."""
    spec = REGISTRY.actions.get(action)
    what = spec.summary.lower() if spec else action
    return (f"from now on, whenever ATLAS would {what} ({action}), just do it "
            f"and tell me after — you've said yes {pred.percent()} of the last "
            f"{pred.seen} times")
