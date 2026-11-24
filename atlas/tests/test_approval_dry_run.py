"""The dry-run output reaches the card (BUILD_ORDER 10.1).

10.1 asks that a Tier 2 proposal arrive "with the dry-run output and the
rollback plan in the message". The rollback plan was there; the dry run was
stored on the row and rendered on the web view, so the card said "requires
approval" and the evidence for that decision sat one click away on a mesh-only
page. Deciding from a phone meant approving without it.

The distinctions these tests exist for:

* a dry run that REFUSED must say so on the card, because that is the line
  that changes his answer;
* a dry run that could not be reached must read as not knowing, never as a
  refusal — those are opposite facts;
* a failed or slow preview must not cost him the card; and
* Tier 1 must not pay for a preview at all, since nobody decides on it and
  `execute` dry-runs it anyway.
"""
from __future__ import annotations

import pytest

from atlas.kernel import approvals
from atlas.kernel.contracts import ActionResult, Tier


@pytest.fixture
def card(monkeypatch):
    """Capture the card body instead of sending it."""
    sent: list[str] = []

    async def fake_notify(msg, **kw):
        sent.append(getattr(msg, "body", "") or "")
        return ["slack"]

    monkeypatch.setattr("atlas.kernel.comms.notify", fake_notify)
    return sent


def preview_returns(monkeypatch, result: ActionResult | Exception):
    async def fake_preview(action: str, params: dict) -> ActionResult:
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr("atlas.kernel.executor.preview", fake_preview)


# ------------------------------------------------------ what lands on the card

async def test_a_successful_dry_run_is_quoted_on_the_card(monkeypatch):
    preview_returns(monkeypatch, ActionResult(True, "Would open 1 issue in flycheap/ops"))

    text, ok = await approvals._preview_for_card("gitlab.open_issue", {})

    assert ok is True
    assert "Would open 1 issue" in text


async def test_a_refusal_is_carried_as_a_refusal(monkeypatch):
    """Approving something whose dry run already refused is the mistake this
    prevents."""
    preview_returns(monkeypatch, ActionResult(False, "refused: the project is archived"))

    text, ok = await approvals._preview_for_card("gitlab.open_issue", {})

    assert ok is False
    assert "archived" in text


async def test_an_unreachable_preview_reads_as_not_knowing(monkeypatch):
    """`ok is None`, never False. "This would be refused" and "I could not find
    out" must not render the same."""
    preview_returns(monkeypatch, RuntimeError("no database"))

    text, ok = await approvals._preview_for_card("gitlab.open_issue", {})

    assert ok is None, "an unreachable preview is not a refusal"
    assert "could not be previewed" in text
    assert "RuntimeError" in text


async def test_a_slow_preview_gives_up_rather_than_holding_the_card(monkeypatch):
    import asyncio

    async def never(action: str, params: dict) -> ActionResult:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    monkeypatch.setattr("atlas.kernel.executor.preview", never)
    monkeypatch.setattr(approvals, "PREVIEW_TIMEOUT_S", 0.05)

    text, ok = await approvals._preview_for_card("slow.action", {})

    assert ok is None
    assert "still running" in text


async def test_an_empty_dry_run_says_so_rather_than_rendering_blank(monkeypatch):
    """A blank line on a card reads as a missing feature, not as a quiet
    success."""
    preview_returns(monkeypatch, ActionResult(True, ""))

    text, ok = await approvals._preview_for_card("x.y", {})

    assert ok is True
    assert "no output" in text


# ----------------------------------------------- only the tier that asks pays

def test_only_tier_2_and_above_is_previewed():
    """Tier 1 runs unattended and dry-runs inside `execute`, so previewing it
    here does the work twice to decorate a card nobody decides on. Measured at
    roughly ten times the runtime across the approval tests before this.
    """
    import inspect

    source = inspect.getsource(approvals.propose)
    assert "decision.tier >= Tier.ASK" in source
    assert "_preview_for_card" in source
    # And the guard comes before the call, not after it.
    assert source.index("decision.tier >= Tier.ASK") < source.index("_preview_for_card(action")


def test_the_card_distinguishes_refused_from_unknown():
    """Read from the body builder: three branches, and the refusal is the one
    that shouts."""
    import inspect

    source = inspect.getsource(approvals.propose)
    assert "dry run REFUSED" in source
    assert "dry_ok is False" in source
    # the unknown branch must not reuse the REFUSED wording
    unknown = source.split("dry_ok is False")[1]
    assert "REFUSED" not in unknown.split("else")[-1]


def test_tier_3_is_not_previewed_because_it_never_proposes():
    """PERMANENT_DENY actions raise before a card exists; nothing here should
    imply a Tier 3 preview is a thing that happens."""
    assert Tier.NEVER > Tier.ASK
    import inspect
    source = inspect.getsource(approvals.propose)
    # the gate is >= ASK, which includes NEVER numerically — but permissions
    # refuses a Tier 3 proposal long before this point, so the gate's wording
    # must not be the thing anyone relies on for that.
    assert "decision.tier >= Tier.ASK" in source
