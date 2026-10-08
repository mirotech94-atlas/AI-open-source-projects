# Decision confidence: a score Guardian can show its working for

**Status:** approved design, not yet implemented
**Date:** 2026-09-23

## What was asked, and what the data allowed

The ask was a neural network that makes better decisions from percentages,
decision questions and yes/no answers. The percentages and the yes/no
questions are exactly right and are what this builds. The network is not,
yet, and the reason is the data rather than the idea.

Measured on production, 2026-09-23:

| Table | Rows | What it could label |
|---|---|---|
| `fix` | 78 | `ok` is 1 on **all 78** -- a single class |
| `fix.verified_at` | 5 of 78 | `verification.py` is new; the signal is real but young |
| `alert` | 93 | 39 keys have `count > 1` -- they came back |
| `tenant` | 2 | |

A classifier trained on 78 examples that are all one class learns "always
yes" and nothing else; there is no decision boundary in a single class. Two
tenants and thirteen distinct actions will not generalise to a third tenant.
So the first job is not to fit a model, it is to **create the dataset that
does not exist**, and in the meantime to make the decision explainable with
weights a person chose and can argue with.

The second reason is posture rather than data. Guardian is a security
product whose repair decisions are deterministic and auditable today
(`chat/tiers.effective_tier`, `remedy`'s three kinds, `tests/test_remedy.py`
pinning the fix tables against the sensor's own). "The model was confident"
is not an answer a customer can be given for why their server rebooted.
Everything below keeps the explanation in the product.

## What is scored

One alert's remedy decision: **fix it myself (`auto`) / ask first
(`approval`) / hand it to a person (`human`)**. That decision is made today
in `remedy.py` (~line 662), where `base.kind` starts at `"approval"` and
becomes `"auto"` when the caller is an admin and `effective_tier` came back
below 3.

The score is advisory input to that choice. It never becomes the choice.

## The factors

Each is a question with a yes/no or a ratio answer, carrying a weight, drawn
from rows Guardian already stores. None requires new collection.

| Question | Source |
|---|---|
| Has this exact action succeeded on this exact host before, and how often? | `Fix` where `(host, action, ok)` |
| Did those repairs hold, or did the alert key come back? | `Alert.count` and `first_seen` vs the fix's `ts` |
| Was the most recent one confirmed by later evidence? | `Fix.verified_at` (`verification.reconcile`) |
| Has a person ever undone it? | `Edit` rows for that key |
| Is the action reversible? | membership of `TIER2_FIXES` vs `TIER3_FIXES` |
| Is the sensor fresh enough to verify the outcome at all? | newest `Scan.ts` age vs the silent threshold |

The last one matters more than it looks: a repair Guardian cannot verify
afterwards is a repair it should be less willing to make unattended, and on
2026-09-21 an overloaded sensor host made every scan stale for half an hour.

## The model

`hub/guardian_hub/confidence.py`, one module, no dependencies:

```python
@dataclass(frozen=True)
class Factor:
    name: str        # "host_history"
    question: str    # "Has Guardian fixed security_updates on prod before?"
    answer: str      # "yes, 12 times, all verified"
    weight: int      # +30
    why: str         # one sentence, shown to the owner

@dataclass(frozen=True)
class Confidence:
    score: int               # 0-100
    verdict: str             # "auto" | "approval" | "human"
    factors: list[Factor]    # every factor considered, including the zero ones
```

Weights are constants in the module with the reasoning in a comment beside
each, the way `remedy`'s fix tables carry theirs. They are chosen, not
fitted, and that is stated in the UI copy: this is a rubric, not a
prediction.

A factor that contributed nothing is still returned with `weight=0`, so the
owner sees what was considered and found absent rather than only what fired.

## The three behaviours

**1. Advisory.** The score and its factors are recorded on the decision and
shown in the Inbox drawer and the chat handoff. Guardian's behaviour does not
change. This alone is worth shipping: it makes the existing decision legible.

**2. Demotion.** A score below `LOW_WATER` turns an `auto` into an
`approval`. Guardian asks where it would otherwise have acted.

**3. Promotion.** An `approval` becomes `auto` only with receipts, never on a
score alone:

- the action is in `TIER2_FIXES` (tier 3 is never promotable, at any score);
- this exact action on this exact host has `>= PROMOTE_MIN_SUCCESSES`
  verified successes;
- the alert key has not recurred since the last of them;
- no person has ever undone it;
- the sensor is fresh;
- `platform_settings.auto_heal()` is on.

Promotion is therefore a statement about history, not about confidence. A
high score with no history promotes nothing.

**Amended 2026-09-24, after implementation: behaviour 3 is unreachable, and
every reason it is unreachable is a reason it should be.** This spec assumed
`kind == "approval"` meant "anyone may run this, but a person must confirm
first". In Guardian it means one of two different things, and `resolve_action`
reaches it by two different routes (`remedy.py`: `if role != "admin"` … `elif
tier >= 3`). Enumerating them:

- An **admin** below tier 3 is already `auto`. There is no approval to promote.
- An **admin** at tier 3 is refused by the tier bar, and each way of arriving
  at tier 3 is independently a reason not to promote: the action is in
  `TIER3_FIXES`; the `auto_heal` kill switch is off, which the gate refuses on
  anyway; the action touches the hub's own listening port; the action is not in
  `KNOWN_FIXES`, where `effective_tier` fails closed; or **the host has no
  successful fix yet** -- which is mutually exclusive with the promotion bar of
  `>= PROMOTE_MIN_SUCCESSES` verified successes on that same host.
- A **non-admin** is refused by `caller_may_run`, added after the first
  implementation promoted exactly this case. That was the only reachable
  promotion in the shipped code, and it violated the fourth invariant below: it
  widened what that human may authorise. It also broke the row it touched --
  dropping it out of Needs-you, minting no chat proposal, and leaving
  `can_run_now` true beside prose saying an admin has to run it.

So the promotion branch ships as an **enforcement point rather than a
behaviour**: nine tests pin its five conditions, and it is the place a future
path that does create a promotable approval will be caught. Promotion as this
spec imagined it needs a different shape -- a person's standing decision to let
Guardian act unattended for a named repair on a named host, which is a
different feature and a different conversation. Behaviour 1 is therefore the
only one of the three a user sees, which raises rather than lowers the priority
of the surfaces it names.

### Invariants, each with a test

- A `TIER3_FIXES` action is never promoted, at any score, for any history.
- An action never seen on this host is never promoted.
- `auto_heal` off disables promotion entirely (the existing kill switch wins).
- The score never widens what a *human* may authorise -- `min_role` and the
  single-use token path are untouched.
- Demotion is always available; promotion is the only thing gated.

## The outcome ledger

The half that makes a real model possible later. New table, alembic
`0032_decision_outcome`:

| Column | |
|---|---|
| `tenant_id`, `alert_key`, `host`, `action` | what was decided about |
| `features` (JSON) | the factor inputs, frozen at decision time |
| `score`, `verdict` | what the rubric said |
| `chosen` | what actually happened: `auto` / `approved` / `refused` / `ignored` |
| `outcome` | `held` / `recurred` / `undone` / `unknown`, filled in later |
| `decided_at`, `settled_at` | |

`outcome` is written by the same pass that already reconciles repairs
(`verification.reconcile`) plus a recurrence check when an alert key
reappears. Nothing new has to be collected by hand.

Frozen features matter: a model trained on features recomputed later would
be trained on facts the decision did not have.

## Out of scope, deliberately

- Fitting any model. Revisit when the ledger has a few thousand rows across
  more than two tenants and both classes are represented.
- Scoring anything but the remedy decision. `Alert.confidence` is currently
  a mess of `''`, `'65'`, `'90'` and `'high'`; tidying it is separate work.
- Changing severity, `min_role`, or the token-bound approval path.

## Testing

- `tests/test_confidence.py` -- each factor in isolation; a score that is
  reproducible from the same inputs; factors always explain themselves.
- `tests/test_confidence_gate.py` -- the five invariants above, each as its
  own named test.
- `tests/test_decision_ledger.py` -- a decision writes one row; the outcome
  pass fills it; features are frozen, not recomputed.
- Existing `tests/test_remedy.py` must pass untouched: the tier tables and
  the sensor parity check are unaffected by design.
