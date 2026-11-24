"""The marketing layer: experiments, a playbook, and money found not spent.

Phase 17's ask was "a marketing agent that self-improves and finds different
ways of making money using the built-in neural network of the whole system".
Read against CLAUDE.md, that is four concrete things and no more:

* **Self-improving** means *measured*. The agent proposes one hypothesis at a
  time, the post goes out through the existing Tier 2 gate, and 48 hours later
  the platform's own number is compared against the trailing 4-week median for
  that platform and metric. The outcome — won, lost, flat, or `unmeasured`
  with the reason — is written as a lesson into the memory namespace
  `playbook`, and the next proposal reads it back. That is the whole loop.
* **The neural network of the whole system** is what already exists: the
  judgment layer's read of what the owner approves (`kernel.judgment.policy`), the
  memory graph, and the detector's baseline discipline. This package *reads*
  the judgment layer; it never writes to it and never imports anything that
  assigns a tier.
* **It cannot promote itself.** Nothing in this package imports
  `kernel.executor`, calls `permissions.classify`, or names a `Tier`. The
  actions and their tiers live in `atlas/modules/marketing/__init__.py`, all
  three Tier AUTO, all three writing only to Postgres and memory. Publishing
  stays where Task 29 and the social modules put it: Tier ASK, every time.
  At p >= 0.95 the judgment layer *drafts* a standing rule; `cycle.weekly()`
  carries that text to the owner and says in the same breath that only he can
  enact it. There is no runtime override and none may be added.
* **Money in, never money out.** `monetize.ideas()` composes only from facts
  something actually measured, each carrying the time it was read (invariant
  6). When nothing is measured the honest answer is zero ideas and a note
  saying so — never an estimate, never an invented revenue figure. Anything
  that would cost money to try is listed with its mechanism and
  `requires = "money out — Tier 3, you decide"`, never as an action.

Submodules:
  `playbook`     — the lessons, ranked by recency x outcome.
  `experiments`  — propose one, attach it to a post, measure it at 48h.
  `monetize`     — ideas that rest on measured facts, or none at all.
  `cycle`        — the Sunday plan: three experiments, three ideas, and any
                   standing-rule candidate the judgment layer has drafted.
"""
from __future__ import annotations

from . import cycle, experiments, monetize, playbook

__all__ = ["cycle", "experiments", "monetize", "playbook"]
