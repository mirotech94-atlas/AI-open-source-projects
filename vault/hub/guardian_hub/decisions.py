"""The ledger of repair decisions, and what became of each one.

Two calls, deliberately small. `record` writes what was decided and the
features it was decided from; `settle` writes what happened, once. Nothing
here reads the features back -- that is a model's job, and there is no model
yet (see `confidence`'s docstring for why).

**What this table can honestly be fitted to answer, and what it cannot.**
Rows are written from the write paths that queue a repair -- `routers/
write.queue_fix` (the `/fix` route and every chat tool that reaches it) and
`routers/write.rec_decide` (accepting a recommendation, which queues through
`queue_and_audit` directly because its audit row is deliberately different;
that route's own comment says why it records here separately). So the table
holds EXECUTIONS, not every decision Vault reached. Two kinds of decision
leave no row at all:

* a `human` verdict, because nothing ran and there is therefore no outcome to
  settle;
* an `approval` the customer never confirmed, because the repair was offered
  and then nothing happened.

Both of those are the CAUTIOUS cases, which means the absences are not
random: the dataset is missing exactly the decisions where Vault held
back. That is survivorship bias in the direction that flatters the rubric.

So:

* **"Will this repair hold?"** is a question this table can be fitted to.
  The population is right -- every row is a repair that really ran on a real
  network, and `settle` records what a later scan saw.
* **"Should Vault have asked?"** is a question it CANNOT answer, and
  fitting a model to it here would systematically conclude that Vault is
  too cautious, because every occasion on which caution was the right call
  is missing from the rows. Answering that one needs the decisions that did
  not execute, which means recording from somewhere else, on purpose, with
  its own read-path cost accounted for -- not a `WHERE` clause over these
  rows.
* **"Did the rubric ever change a decision?"** is a question it cannot answer
  either, and this is the one most likely to be asked first, because anyone
  fitting a model here will want to know what baseline they have to beat.
  `confidence.gate` -- the only thing that may demote or promote -- is never
  called on the write path at all. `verdict` is `confidence.verdict_of(score,
  tier)`, which returns `"auto"` at any score >= `HIGH_WATER` on a tier-2
  action, INCLUDING the cases where `gate` looked at the same estate and
  refused to promote; and `chosen` is the effective tier restated, which is
  the tier rules' own answer. So the two columns that look like "what the
  rubric said" and "what happened" are both computed without the gate, and a
  row where they differ says the tier changed, not that the rubric
  intervened. Recording the intervention means carrying `gate`'s verdict out
  of `remedy._apply_confidence` and into the queue call, which is a signature
  change through `queue_fix`, not a column that can be derived here.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy.orm import Session as DbSession

from . import models as m
from .confidence import Confidence


def record(db: DbSession, tenant, *, alert_key: str, host: str, action: str,
           conf: Confidence, chosen: str, arg: str | None = "") -> str:
    """Store one decision. Commits, and returns the row id.

    `arg` is part of the decision's identity, not a detail: it is what
    `settle` needs to tell `restart_service nginx` from `restart_service
    postgres` on one host. `None` is normalised to `""` here and at every
    match, so the column never holds NULL and the comparison never has to
    reason about it -- the queue body stores whatever the caller sent, which
    for a repair that takes no argument is `None` from the dashboard and `""`
    from elsewhere, and those are the same decision.

    `features` is the factor weights AS THEY WERE WHEN THE REPAIR WAS QUEUED,
    and they are never recomputed: a model trained on features recomputed
    when the outcome landed would be trained on facts the decision did not
    have, which is the most flattering and least useful kind of dataset.

    They are therefore not byte-identical to the score the dashboard rendered
    for the same alert minutes earlier, and that is correct rather than a
    drift to be reconciled. For predicting whether a repair will hold, the
    state of the estate at the moment the command was sent is the more honest
    feature set -- it is the only one that was true when the thing being
    predicted was still in the future.
    """
    row = m.DecisionOutcome(
        tenant_id=tenant.id, alert_key=alert_key, host=host, action=action,
        arg=arg or "", features={f.name: f.weight for f in conf.factors},
        score=conf.score, verdict=conf.verdict, chosen=chosen)
    db.add(row)
    db.commit()
    return row.id


def settle(db: DbSession, tenant, *, host: str, action: str, outcome: str,
           arg: str | None = "", alert_key: str = "") -> int:
    """Fill in what happened, for decisions about this repair that are still
    open. Returns how many rows settled.

    Only unsettled rows are touched, so the FIRST thing that happened is what
    the dataset keeps -- an alert that recurs, is fixed, and recurs again
    must not rewrite the earlier decision's outcome. That also makes this
    idempotent across the two places `verification.reconcile` calls it from,
    which can both be true of the same repair in the same sweep.

    `alert_key` is OPTIONAL, and when it is empty it is not filtered on at
    all -- it does not mean "a decision whose key is empty". The caller that
    settles is `verification.reconcile`, which knows the tenant, the host, the
    action and the argument, and has no alert key anywhere in scope (the job
    it reads carries `body["host"]`/`body["action"]`/`body["arg"]`; the
    `m.Fix` row carries `host`/`action`/`arg`). Filtering on a key the caller
    had to invent would settle nothing at all.

    **`(tenant, host, action, arg)` is as precise as the evidence gets, and
    that is not the same as exact.** `arg` is matched (which is what keeps one
    service's restart from settling another's on the same host), but two kinds
    of cross-talk survive and are better named here than papered over:

    * An `/api/reboot-plan` job settles a `reboot` decision queued through
      `/fix`: `verification.reconcile` substitutes `act="reboot"` for that
      path, because that is the repair whose recovery it verified. The
      evidence is real -- the host did come back -- it just cannot say which
      of the two routes asked for it.
    * A decision whose own evidence never arrived is settled by a LATER
      repair of the same identity. "Only unsettled rows are touched" protects
      a row from being OVERWRITTEN; it does not protect it from being settled
      by somebody else's evidence. A model fitted on this should treat
      `settled_at - decided_at` as a feature and be suspicious of a long one.

    Narrowing either of those needs the queue/fix row to carry the decision's
    id, which is a schema change on `m.Queue` and `m.Fix` rather than a
    filter here.
    """
    q = db.query(m.DecisionOutcome).filter_by(
        tenant_id=tenant.id, host=host, action=action, arg=arg or "", outcome="")
    if alert_key:
        q = q.filter(m.DecisionOutcome.alert_key == alert_key)
    rows = q.all()
    for row in rows:
        row.outcome = outcome
        row.settled_at = dt.datetime.now(dt.timezone.utc)
    if rows:
        db.commit()
    return len(rows)
