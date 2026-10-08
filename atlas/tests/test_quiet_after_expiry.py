"""RA-B6: a cause ATLAS raises by itself is not re-asked the moment its card
expires (151 gitlab.open_issue cards from 33 causes) -- but what Miro asks
for himself is never held back."""
from __future__ import annotations

import contextlib
from datetime import UTC, datetime

import pytest

from atlas.kernel import approvals
from atlas.kernel.contracts import Action, ActionResult, Tier
from atlas.kernel.permissions import classify
from atlas.kernel.registry import REGISTRY


@pytest.fixture
def action(monkeypatch, memory_backend):
    async def run(**kw):
        return ActionResult(True, "ok")

    a = Action(name="gitlab.open_issue", tier=Tier.ASK, summary="open an issue",
               run=run, dry_run=run, irreversible=True)
    monkeypatch.setitem(REGISTRY.actions, a.name, a)
    monkeypatch.setitem(REGISTRY.decisions, a.name, classify(a))
    memory_backend.responses["WHERE dedup_key = $1 AND status = 'pending'"] = None
    return memory_backend


QUIET = "SELECT id, status, decided_at, expires_at FROM proposals WHERE dedup_key"


@pytest.mark.parametrize("status", ["expired", "declined"])
async def test_an_automatic_cause_stays_quiet(action, status):
    action.responses[QUIET] = {"id": 124, "status": status,
                               "decided_at": datetime.now(UTC),
                               "expires_at": datetime.now(UTC)}
    with pytest.raises(approvals.Unrunnable, match="#124"):
        await approvals.propose("gitlab.open_issue", {}, "same cause again",
                                dedup_key="gitlab.open_issue@stripe:key",
                                automatic=True)


async def test_what_he_asks_for_himself_is_never_held_back(action, monkeypatch):
    action.responses[QUIET] = {"id": 124, "status": "expired",
                               "decided_at": None, "expires_at": datetime.now(UTC)}
    seen = []

    async def fake_fetch_one(sql, *args):
        seen.append(" ".join(sql.split()))
        if "INSERT INTO proposals" in sql:
            return {"id": 200}
        return None

    monkeypatch.setattr(approvals, "fetch_one", fake_fetch_one)
    with contextlib.suppress(Exception):   # later steps need more fakes
        await approvals.propose("gitlab.open_issue", {}, "he asked",
                                dedup_key="gitlab.open_issue@slack:open an issue")
    assert not any(q.startswith(QUIET) for q in seen)
