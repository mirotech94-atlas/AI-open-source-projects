"""Top three reasons for any number, whichever head is serving.

A percentage is never shown without its reasons. A number he cannot
interrogate is a number he cannot trust, and that is exactly what goes wrong
when a network takes over: it is right more often and explains itself less.

The method is head-agnostic so the explanation survives the handover: for
each feature, put it at the training mean and see how far the probability
moves. The three that move it most are the reasons, and the direction says
"because" or "despite". For the logistic head this reduces to its
coefficients; for the others it is the only honest version.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .encode import FAMILIES, MODULES, NAMES, WIDTH


@dataclass(frozen=True, slots=True)
class Reason:
    feature: str
    delta: float          # how much p moved when the feature was neutralised
    text: str             # in his words

    def __str__(self) -> str:
        return self.text


def _phrase(name: str, value: float, delta: float, pair: str, seen: int,
            rate: float) -> str:
    up = delta > 0                       # this feature pushes p UP
    if name == "prior_approve_rate":
        if rate >= 0.9 and seen >= 5:
            return "you always approve this"
        if rate <= 0.1 and seen >= 5:
            return "you never approve this"
        return (f"you said yes {round(rate * 100)}% of the time"
                if seen else "no history for this action")
    if name == "times_seen":
        return f"seen {seen} times before" if seen else "first time"
    if name == "reversible":
        return "it's reversible" if value >= 0.5 else "it can't be undone"
    if name in ("hour_sin", "hour_cos"):
        return "it's the hour" if up else "it's the hour — you usually say no now"
    if name == "quiet_hours":
        return "it's quiet hours" if value >= 0.5 else "it's daytime"
    if name == "you_were_busy":
        return "you were busy" if value >= 0.5 else "you were free"
    if name == "severity":
        return "it's urgent" if value >= 0.66 else "it isn't urgent"
    if name == "blast_radius":
        return "it touches a lot" if value >= 0.66 else "it touches one thing"
    if name == "cost_usd":
        return "it costs money" if value > 0 else "it costs nothing"
    if name == "weekend":
        return "it's the weekend" if value >= 0.5 else "it's a weekday"
    if name == "weekday":
        return "it's the day of the week"
    if name == "days_since_similar":
        return "it's been a while since the last one" if value >= 0.5 \
            else "there was one recently"
    if name.startswith("module="):
        return f"it's {name.split('=', 1)[1]}"
    if name.startswith("family="):
        return f"it's a {name.split('=', 1)[1]} action"
    return name


def explain(model, x: np.ndarray, mean: np.ndarray, *, pair: str = "",
            seen: int = 0, rate: float = 0.5, top: int = 3) -> list[Reason]:
    """Top `top` reasons for model's probability on x.

    `mean` is the training-set mean vector (WIDTH wide). One-hot slots are
    neutralised together: turning "module=cloudhost" off is meaningless unless
    the other module slots move to their means too.
    """
    x = np.asarray(x, dtype=np.float32).reshape(1, -1)
    p0 = float(model.predict_proba(x)[0, 1])
    base = len(MODULES) + len(FAMILIES)
    deltas: list[tuple[str, float, float]] = []

    def probe(cols: list[int], label: str, value: float) -> None:
        z = x.copy()
        z[0, cols] = mean[cols]
        p1 = float(model.predict_proba(z)[0, 1])
        deltas.append((label, p0 - p1, value))

    on_module = [i for i in range(len(MODULES)) if x[0, i] > 0.5]
    if on_module:
        probe(list(range(len(MODULES))), NAMES[on_module[0]], 1.0)
    on_family = [i for i in range(len(MODULES), base) if x[0, i] > 0.5]
    if on_family:
        probe(list(range(len(MODULES), base)), NAMES[on_family[0]], 1.0)
    for i in range(base, WIDTH):
        probe([i], NAMES[i], float(x[0, i]))

    deltas.sort(key=lambda t: abs(t[1]), reverse=True)
    out: list[Reason] = []
    said: set[str] = set()
    for name, delta, value in deltas:
        text = _phrase(name, value, delta, pair, seen, rate)
        if text in said:                 # hour_sin and hour_cos say one thing
            continue
        said.add(text)
        out.append(Reason(name, round(delta, 4), text))
        if len(out) == top:
            break
    return out
