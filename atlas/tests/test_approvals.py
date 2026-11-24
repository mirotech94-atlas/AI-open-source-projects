"""The approval path. A double-submitted approval must execute exactly once,
and a tab left open overnight must not approve something that lapsed."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest

from atlas.kernel import approvals
from atlas.kernel.approvals import decide, propose
from atlas.kernel.contracts import ActionResult


@pytest.fixture
def armed(monkeypatch):
    """Most of these tests are about what happens when ATLAS can act."""
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "false")


@pytest.fixture
def executed(monkeypatch):
    """Record every call that reached the executor."""
    calls: list = []

    async def fake_execute(req):
        calls.append(req)
        return ActionResult(True, "did the thing")

    monkeypatch.setattr(approvals, "execute", fake_execute)
    return calls


def claimable(backend, *, action="flycheap.regenerate_links", once=True):
    """The conditional UPDATE matches a row the first time and never again —
    which is exactly what Postgres does with `WHERE status='pending'`."""
    state = {"claimed": False}

    row = {"action": action, "params": "{}", "reason": "links are 404ing",
           "incident_id": None}

    async def fetchrow(sql, *args):
        flat = " ".join(sql.split())
        backend.sql_log.append((flat, args))
        if flat.startswith("UPDATE proposals SET status="):
            if once and state["claimed"]:
                return None                 # someone else already claimed it
            state["claimed"] = True
            return dict(row)
        if flat.startswith("SELECT status, expires_at FROM proposals"):
            return {"status": "approved" if state["claimed"] else "pending",
                    "expires_at": datetime.now(UTC) + timedelta(hours=1)}
        if "FROM restores WHERE fenced_at IS NULL" in flat:
            return {"due": False}           # no restore awaiting its fence
        if flat.startswith("SELECT catalog_version, approved_payload_hash"):
            # What was recorded with the card (P04, S08); a card raised
            # before that existed has neither, and is not checked.
            return dict(state.get("recorded") or {
                "catalog_version": None, "approved_payload_hash": None,
                "dedup_key": None, "evidence": "{}", "rollback_plan": ""})
        raise NotImplementedError(flat)

    backend.fetchrow = fetchrow
    return state


# ----------------------------------------------------------- happy path

async def test_approving_a_pending_proposal_executes_it(
        memory_backend, armed, executed):
    claimable(memory_backend)
    verdict = await decide(41, "approve", via="dashboard")
    assert verdict.ok and verdict.outcome == "executed"
    assert len(executed) == 1
    assert executed[0].approved_by == "dashboard"


async def test_declining_does_not_execute(memory_backend, armed, executed):
    claimable(memory_backend)
    verdict = await decide(41, "decline", via="dashboard")
    assert verdict.ok and verdict.outcome == "declined"
    assert executed == []


# -------------------------------------------------- the four required cases

async def test_an_unknown_proposal_is_refused(memory_backend, armed, executed):
    async def fetchrow(sql, *args):
        return None                         # nothing claims, nothing exists
    memory_backend.fetchrow = fetchrow

    verdict = await decide(9999, "approve", via="dashboard")
    assert not verdict.ok and verdict.outcome == "missing"
    assert executed == []


async def test_an_expired_proposal_is_refused(memory_backend, armed, executed):
    """The UPDATE carries `expires_at > now()`, so expiry is enforced in the
    same statement that claims the row — not in a check before it."""
    async def fetchrow(sql, *args):
        flat = " ".join(sql.split())
        if flat.startswith("UPDATE proposals SET status="):
            return None                     # expires_at > now() did not match
        if flat.startswith("SELECT status, expires_at"):
            return {"status": "pending",
                    "expires_at": datetime.now(UTC) - timedelta(hours=5)}
        if "FROM restores WHERE fenced_at IS NULL" in flat:
            return {"due": False}
        raise NotImplementedError(flat)
    memory_backend.fetchrow = fetchrow

    verdict = await decide(41, "approve", via="dashboard")
    assert not verdict.ok and verdict.outcome == "expired"
    assert "Nothing was done" in verdict.detail
    assert executed == []


async def test_a_retired_agents_card_expires_and_never_runs(
        memory_backend, armed, executed):
    """2026-10-28: a card a retired agent raised (dedup key `...@agent:markets`)
    is excluded by the claiming UPDATE itself, so approving it expires it and
    nothing reaches the executor. A kept agent's card is unaffected."""
    import fnmatch

    def like_any(key: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatchcase(key, p.replace("%", "*")) for p in patterns)

    cards = {41: "instagram.post@agent:growth", 42: "agency.send_preview@agent:sites"}
    status = dict.fromkeys(cards, "pending")

    async def fetchrow(sql, *args):
        flat = " ".join(sql.split())
        if flat.startswith("UPDATE proposals SET status='approved'"):
            assert "LIKE ANY($4::text[])" in flat
            pid, patterns = args[0], args[3]
            if like_any(cards[pid], patterns):
                return None
            status[pid] = "approved"
            return {"action": cards[pid].split("@")[0], "params": "{}",
                    "reason": "r", "incident_id": None}
        if flat.startswith("SELECT status, expires_at"):
            return {"status": status[args[0]],
                    "expires_at": datetime.now(UTC) + timedelta(hours=1)}
        if "FROM restores WHERE fenced_at IS NULL" in flat:
            return {"due": False}
        if flat.startswith("SELECT catalog_version, approved_payload_hash"):
            return {"catalog_version": None, "approved_payload_hash": None,
                    "dedup_key": None, "evidence": "{}", "rollback_plan": ""}
        return None
    memory_backend.fetchrow = fetchrow

    verdict = await decide(41, "approve", via="dashboard")
    assert not verdict.ok and verdict.outcome == "expired"
    assert executed == []
    keys = approvals.retired_agent_keys()
    assert "%@agent:growth" in keys and "%@agent:sites" not in keys
    assert len(keys) == 18


async def test_a_dropped_reddit_card_expires_and_never_runs(
        memory_backend, armed, executed):
    """2026-10-29: the Reddit posting lane is dropped. Its cards carry the key
    `reddit.submit_post:<draft>` and no agent suffix, so the claim matches the
    retired action itself: approving one expires it and nothing runs. A kept
    action's card is unaffected."""
    import fnmatch

    def like_any(value: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatchcase(value, p.replace("%", "*")) for p in patterns)

    cards = {51: ("reddit.submit_post", "reddit.submit_post:7"),
             52: ("church.schedule_youtube_shorts", "church:day:1")}
    status = dict.fromkeys(cards, "pending")

    async def fetchrow(sql, *args):
        flat = " ".join(sql.split())
        if flat.startswith("UPDATE proposals SET status='approved'"):
            assert "action LIKE ANY($5::text[])" in flat
            pid = args[0]
            action, key = cards[pid]
            if like_any(key, args[3]) or like_any(action, args[4]):
                return None
            status[pid] = "approved"
            return {"action": action, "params": "{}", "reason": "r",
                    "incident_id": None}
        if flat.startswith("SELECT status, expires_at"):
            return {"status": status[args[0]],
                    "expires_at": datetime.now(UTC) + timedelta(hours=1)}
        if "FROM restores WHERE fenced_at IS NULL" in flat:
            return {"due": False}
        if flat.startswith("SELECT catalog_version, approved_payload_hash"):
            return {"catalog_version": None, "approved_payload_hash": None,
                    "dedup_key": None, "evidence": "{}", "rollback_plan": ""}
        return None
    memory_backend.fetchrow = fetchrow

    verdict = await decide(51, "approve", via="dashboard")
    assert not verdict.ok and verdict.outcome == "expired"
    assert executed == []
    assert approvals.retired_action_patterns() == ["reddit.%"]


async def test_expire_stale_expires_a_dropped_reddit_card(monkeypatch):
    """The sweep expires a pending Reddit card even before its deadline."""
    seen: list[tuple[str, tuple]] = []

    async def fetch_all(sql, *args):
        seen.append((" ".join(sql.split()), args))
        return [{"id": 51, "action": "reddit.submit_post"}]

    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(approvals, "fetch_all", fetch_all)
    monkeypatch.setattr(approvals, "audit", nothing)
    monkeypatch.setattr(approvals, "_expiry_notice", nothing)
    monkeypatch.setattr(approvals, "_open_cards_notice", nothing)
    from atlas.kernel import judgment
    monkeypatch.setattr(judgment, "record_outcome", nothing)
    assert await approvals.expire_stale() == 1
    sql, args = seen[0]
    assert "OR action LIKE ANY($2::text[])" in sql
    assert args[1] == ["reddit.%"]


async def test_an_already_decided_proposal_is_refused(
        memory_backend, armed, executed):
    async def fetchrow(sql, *args):
        flat = " ".join(sql.split())
        if flat.startswith("UPDATE proposals SET status="):
            return None
        if flat.startswith("SELECT status, expires_at"):
            return {"status": "declined",
                    "expires_at": datetime.now(UTC) + timedelta(hours=1)}
        if "FROM restores WHERE fenced_at IS NULL" in flat:
            return {"due": False}
        raise NotImplementedError(flat)
    memory_backend.fetchrow = fetchrow

    verdict = await decide(41, "approve", via="dashboard")
    assert not verdict.ok and verdict.outcome == "already"
    assert "already declined" in verdict.detail
    assert executed == []


async def test_a_double_submit_executes_exactly_once(
        memory_backend, armed, executed):
    """Two concurrent approvals of the same proposal. Only one may act."""
    claimable(memory_backend)

    first, second = await asyncio.gather(
        decide(41, "approve", via="dashboard"),
        decide(41, "approve", via="dashboard"),
    )

    assert len(executed) == 1, "the action ran more than once"
    outcomes = sorted([first.outcome, second.outcome])
    assert outcomes == ["already", "executed"]


async def test_ten_concurrent_submits_execute_exactly_once(
        memory_backend, armed, executed):
    claimable(memory_backend)
    results = await asyncio.gather(
        *(decide(41, "approve", via="dashboard") for _ in range(10)))
    assert len(executed) == 1
    assert sum(1 for r in results if r.executed) == 1


# ------------------------------------------------------------- watch-only

async def test_watch_only_refuses_approval_and_leaves_it_pending(
        memory_backend, monkeypatch, executed):
    """A disabled button is not enforcement. The server refuses too — and must
    not consume the proposal, so it can still be answered once ATLAS is armed."""
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    state = claimable(memory_backend)

    verdict = await decide(41, "approve", via="dashboard")
    assert not verdict.ok and verdict.outcome == "refused"
    assert "watch-only" in verdict.detail
    assert "still pending" in verdict.detail
    assert executed == []
    assert state["claimed"] is False, "the proposal was consumed"


async def test_watch_only_still_allows_declining(
        memory_backend, monkeypatch, executed):
    monkeypatch.setenv("ATLAS_WATCH_ONLY", "true")
    claimable(memory_backend)
    verdict = await decide(41, "decline", via="dashboard")
    assert verdict.ok and verdict.outcome == "declined"
    assert executed == []


# ----------------------------------------------------------------- misc

async def test_an_unknown_verdict_is_refused(memory_backend, armed, executed):
    verdict = await decide(41, "obliterate", via="dashboard")
    assert not verdict.ok and verdict.outcome == "refused"
    assert executed == []


async def test_a_failed_execution_is_reported_as_failed(
        memory_backend, armed, monkeypatch):
    async def fails(req):
        return ActionResult(False, "the admin API said no")
    monkeypatch.setattr(approvals, "execute", fails)
    claimable(memory_backend)

    verdict = await decide(41, "approve", via="dashboard")
    assert not verdict.ok and verdict.outcome == "failed"
    assert "the admin API said no" in verdict.detail


@pytest.fixture
def proposing(monkeypatch):
    """One allowed Tier 2 action and a fake proposals table.

    MemoryBackend deliberately implements only what audit and detect need,
    so propose()'s SQL has nowhere to go. The rows are faked at the state
    layer instead: what is under test is the dedup decision, not asyncpg.
    """
    from atlas.kernel.contracts import Tier
    from atlas.kernel.registry import REGISTRY

    class A:
        summary = "File an issue"
        params: ClassVar[dict] = {"title": "the title"}

    class D:
        tier = Tier.ASK
        allowed = True
        reason = ""

    monkeypatch.setitem(REGISTRY.actions, "gitlab.open_issue", A())
    monkeypatch.setitem(REGISTRY.decisions, "gitlab.open_issue", D())

    rows: list[dict] = []
    next_id = [100]

    async def fake_fetch_one(sql, *args):
        if "SELECT id FROM proposals" in sql:
            key = args[0]
            for row in rows:
                if row["dedup_key"] == key and row["status"] == "pending":
                    return {"id": row["id"]}
            return None
        if "INSERT INTO proposals" in sql:
            next_id[0] += 1
            rows.append({"id": next_id[0], "dedup_key": args[9],
                         "status": "pending", "evidence": json.loads(args[6])})
            return {"id": next_id[0]}
        return None

    async def quiet(*a, **k):
        return []

    monkeypatch.setattr("atlas.kernel.approvals.fetch_one", fake_fetch_one)
    monkeypatch.setattr("atlas.kernel.approvals.audit", quiet)
    monkeypatch.setattr("atlas.kernel.approvals.publish",
                        lambda *a, **k: None)
    monkeypatch.setattr("atlas.kernel.comms.notify", quiet)
    return rows


# ------------------------------------------------------------ deduplication

async def test_the_same_cause_does_not_raise_a_second_card(proposing):
    """A check that stays UNKNOWN produces a finding every cycle. Without
    this, each one became a fresh proposal: 70 identical "file an issue about
    the missing FlyCheap Stripe key" cards in a single day, which does not
    just make noise -- it buries every real decision in the same list."""
    first = await propose("gitlab.open_issue", {"title": "a"},
                          reason="stripe key missing",
                          dedup_key="gitlab.open_issue@stripe.flycheap_key")
    second = await propose("gitlab.open_issue", {"title": "slightly different"},
                           reason="stripe key still missing",
                           dedup_key="gitlab.open_issue@stripe.flycheap_key")
    assert second == first, "the second call must return the pending one"
    assert len(proposing) == 1, "and must not insert a second row"


async def test_a_different_cause_still_raises_its_own_card(proposing):
    """Dedup must not merge unrelated decisions."""
    a = await propose("gitlab.open_issue", {"title": "a"}, reason="one",
                      dedup_key="gitlab.open_issue@stripe.flycheap_key")
    b = await propose("gitlab.open_issue", {"title": "b"}, reason="two",
                      dedup_key="contact40@example.com")
    assert a != b
    assert len(proposing) == 2


async def test_without_a_key_nothing_is_deduplicated(proposing):
    """Callers that do not name a cause keep the old behaviour, so this
    cannot silently swallow a proposal somebody meant to raise twice."""
    a = await propose("gitlab.open_issue", {"title": "a"}, reason="one")
    b = await propose("gitlab.open_issue", {"title": "a"}, reason="one")
    assert a != b


def test_the_scheduler_names_the_check_as_the_cause():
    """Keyed on the CAUSE, not on action-and-parameters: the reasoner writes
    a slightly different issue title every time, so identical parameters
    would never match and the dedup would never fire."""
    import inspect

    from atlas.kernel import scheduler
    source = inspect.getsource(scheduler)
    assert 'dedup_key=f"{proposal.action}@{_cause_key(finding)}"' in source


def test_cause_key_joins_checks_blocked_by_one_thing():
    """Six Stripe checks blind on one missing key are one decision, not six.
    A check that RAN and found something keeps its own key."""
    from atlas.kernel.contracts import Health, Observation, Severity
    from atlas.kernel.detect import Finding
    from atlas.kernel.scheduler import _cause_key

    def f(check, health, detail):
        return Finding(check=check, severity=Severity.NORMAL, title=check,
                       detail=detail, kind="rule",
                       observation=Observation(check.split(".")[1], health,
                                               detail=detail))
    blind = "no STRIPE_FLYCHEAP_KEY set. Create a RESTRICTED key"
    assert (_cause_key(f("stripe.flycheap_mrr", Health.UNKNOWN, blind))
            == _cause_key(f("stripe.flycheap_disputes", Health.UNKNOWN, blind))
            == "stripe:missing:STRIPE_FLYCHEAP_KEY")
    wall = "Fiverr's bot detection (PerimeterX) blocked this request"
    assert (_cause_key(f("fiverr.gig_stats", Health.UNKNOWN, wall))
            == _cause_key(f("fiverr.session", Health.UNKNOWN, wall)))
    assert (_cause_key(f("cloudco.balance", Health.FAILING, "0.7 months"))
            == "cloudco.balance")


# ------------------------------------- a card that cannot run is not raised
#
# The missing half of invariant 2. `executor._validate_params` refuses a
# parameter the action did not declare; nothing refused one it NEEDS. So
# "build them a website", said out loud, became a card for agency.build_site
# with no lead_id, which raised TypeError on approval, went into the lessons
# ledger, and after two of those never-repeat blocked the action outright.

@pytest.fixture
def build_site(proposing, monkeypatch):
    """A Tier 2 action whose run() needs a lead and may take a style."""
    from atlas.kernel.contracts import Tier
    from atlas.kernel.registry import REGISTRY

    async def run(lead_id: int, style: str = "clean"):
        return ActionResult(True, "built")

    class A:
        summary = "Build a site for a lead"
        params: ClassVar[dict] = {"lead_id": "the agency lead id",
                                  "style": "optional"}

    A.run = staticmethod(run)

    class D:
        tier = Tier.ASK
        allowed = True
        reason = ""

    monkeypatch.setitem(REGISTRY.actions, "agency.build_site", A())
    monkeypatch.setitem(REGISTRY.decisions, "agency.build_site", D())
    return proposing


async def test_a_card_missing_a_required_parameter_is_refused_with_a_reason(
        build_site):
    with pytest.raises(approvals.MissingParams) as refused:
        await propose("agency.build_site", {}, reason="build them a website")
    assert refused.value.missing == ["lead_id"]
    # Said, not logged: this is what voice reads back to him.
    assert "which lead" in refused.value.spoken
    assert build_site == [], "no card may be inserted"


@pytest.mark.parametrize("blank", [None, "", "   "])
async def test_a_blank_required_parameter_counts_as_missing(build_site, blank):
    """The model writes "" or null for an id it does not know."""
    with pytest.raises(approvals.MissingParams):
        await propose("agency.build_site", {"lead_id": blank}, reason="x")
    assert build_site == []


async def test_a_well_formed_card_is_raised_and_optional_ones_may_be_left_out(
        build_site):
    pid = await propose("agency.build_site", {"lead_id": 7}, reason="x")
    assert pid and len(build_site) == 1


async def test_the_refusal_is_still_a_value_error_for_every_older_caller(
        build_site):
    """scheduler, tools and intent.queue already catch ValueError from
    propose(); a new refusal must not escape them."""
    with pytest.raises(ValueError):
        await propose("agency.build_site", {}, reason="x")


def test_required_params_reads_the_run_signature():
    async def run(thread_ids, label, *args, note="", **kw):
        return None

    class A:
        pass
    A.run = staticmethod(run)
    assert approvals.required_params(A()) == ["thread_ids", "label"]
    # No run, or one whose signature cannot be read, requires nothing here --
    # the executor still refuses at run time.
    assert approvals.required_params(object()) == []


@pytest.mark.parametrize("name,said", [
    ("lead_id", "which lead"), ("thread_ids", "which threads"),
    ("body", "the body"), ("room_name", "the room name")])
def test_the_reason_names_what_is_missing_in_words(name, said):
    assert said in approvals.missing_reason([name])


# ---------------------- I-1: nor is a card carrying what it does not take

async def test_a_card_with_an_undeclared_parameter_is_refused(build_site):
    """The other half of invariant 2. The executor refuses an undeclared
    parameter on approval; a card carrying one could only fail."""
    with pytest.raises(approvals.UndeclaredParams) as refused:
        await propose("agency.build_site", {"lead_id": 7, "domain": "x.com"},
                      reason="x")
    assert refused.value.undeclared == ["domain"]
    assert "undeclared parameter(s): domain" in refused.value.spoken
    assert isinstance(refused.value, ValueError)
    assert build_site == [], "no card may be inserted"


def test_undeclared_params_uses_the_actions_own_declaration():
    class A:
        params: ClassVar[dict] = {"title": "t"}
    assert approvals.undeclared_params(A(), {"title": "x", "b": 1, "a": 2}) == [
        "a", "b"]
    assert approvals.undeclared_params(object(), {"x": 1}) == []


async def test_a_verdict_carries_what_the_action_reported(
        memory_backend, armed, monkeypatch):
    """A summary says what landed, not what was intended (invariant 6)."""
    claimable(memory_backend)

    async def fake_execute(req):
        return ActionResult(True, "labelled 2/3", evidence={"threads": ["a", "b"]})

    monkeypatch.setattr(approvals, "execute", fake_execute)
    verdict = await decide(41, "approve", via="dashboard")
    assert verdict.evidence == {"threads": ["a", "b"]}


async def test_a_card_can_land_in_the_channel_a_parameter_names(memory_backend, monkeypatch):
    """linkedin.publish_post concerns a page, not "linkedin": the FlyCheap
    post belongs in the FlyCheap channel, with its text whole."""
    from atlas.kernel import approvals
    from atlas.kernel.contracts import Action, Tier
    from atlas.kernel.permissions import classify
    from atlas.kernel.registry import REGISTRY

    async def ok(**kw):
        return ActionResult(True, "ok")

    act = Action(name="linkedin.publish_post", tier=Tier.ASK, summary="Publish",
                 run=ok, dry_run=ok, irreversible=True,
                 params={"page": "which", "entry_id": "which"},
                 topic_param="page", verbose_card=True)
    monkeypatch.setitem(REGISTRY.actions, act.name, act)
    monkeypatch.setitem(REGISTRY.decisions, act.name, classify(act))
    sent = []

    async def fake_notify(msg):
        sent.append(msg)
        return ["slack"]

    monkeypatch.setattr(approvals, "notify", fake_notify)
    monkeypatch.setenv("APPROVALS_SLACK", "on")      # the Slack card is opt-in now
    memory_backend.responses["SELECT id FROM proposals"] = None
    memory_backend.responses["INSERT INTO proposals"] = {"id": 5}
    # The never-repeat ledger (lessons.py) reads with fetch_one; the catch-all
    # below would hand it a list. No lesson on record for this action.
    memory_backend.responses["FROM lessons"] = None
    memory_backend.responses["SELECT"] = []
    text = "Line one of the post.\n\nLine two, and a third sentence. Then more."
    await propose(act.name, {"page": "flycheap", "entry_id": "B1"}, reason=text)
    assert sent and sent[0].topic == "flycheap"
    assert "Line two" in sent[0].body and "Then more" in sent[0].body


# ------------------------------------------------------------- receipts

@pytest.mark.parametrize("outcome, ok, detail, expected", [
    ("executed", True, "Done: opened issue 12", "Proposal #180 completed. Result: opened issue 12"),
    ("failed", False, "Failed: HTTP 403", "Proposal #180 was approved but did not complete: HTTP 403"),
    ("started", True, "Posting #180 now", "Proposal #180 approved and started. Nothing is confirmed published yet"),
    ("declined", True, "Declined #180.", "Proposal #180 declined. It will not execute."),
    ("expired", False, "#180 expired", "Proposal #180 had expired. Nothing was executed."),
    ("missing", False, "No proposal #180.", "There is no proposal #180. Nothing was executed."),
])
def test_a_receipt_names_the_id_and_the_true_state(outcome, ok, detail, expected):
    from atlas.kernel.approvals import Verdict
    assert Verdict(ok, outcome, detail, 180).receipt().startswith(expected)


async def test_declining_something_already_running_says_it_does_not_undo_it(
        memory_backend):
    from atlas.kernel.approvals import _why_not_claimed
    memory_backend.responses["SELECT status, expires_at FROM proposals"] = {
        "status": "approved", "expires_at": None}
    v = await _why_not_claimed(180, "decline")
    assert "declining does not undo it" in v.receipt()
    memory_backend.responses["SELECT status, expires_at FROM proposals"] = {
        "status": "executed", "expires_at": None}
    v = await _why_not_claimed(180, "decline")
    assert "already ran; declining does not undo it" in v.receipt()


async def test_expiry_notices_are_batched_to_one_a_day(monkeypatch):
    """2026-09-28: five "1 proposal(s) expired unanswered" in #atlas-decisions
    in 23 minutes, one per proposal as each clock ran out."""
    from datetime import UTC, datetime, timedelta

    from atlas.kernel import approvals

    now = datetime.now(UTC)
    state = {"last": now - timedelta(minutes=10)}
    sent, audited = [], []

    async def fetch_one(sql, *a):
        return {"at": state["last"]}

    async def fetch_all(sql, *a):
        return [{"id": 173, "action": "gitlab.open_issue"},
                {"id": 174, "action": "gitlab.open_issue"}]

    async def notify(msg):
        sent.append(msg)

    async def audit(event, subject, data=None):
        audited.append(event)

    monkeypatch.setattr(approvals, "fetch_one", fetch_one)
    monkeypatch.setattr(approvals, "fetch_all", fetch_all)
    monkeypatch.setattr(approvals, "notify", notify)
    monkeypatch.setattr(approvals, "audit", audit)

    await approvals._expiry_notice()                    # 10 min after the last one
    assert sent == []
    state["last"] = now - timedelta(hours=2)
    await approvals._expiry_notice()                    # 2 h: still the same day
    assert sent == []
    state["last"] = now - timedelta(hours=25)
    await approvals._expiry_notice()
    (msg,) = sent
    assert msg.title == "2 proposal(s) expired unanswered"
    assert "#173" in msg.body and "#174" in msg.body
    assert audited == ["proposal.expiry_notice"]


async def test_an_unreadable_restore_fence_runs_nothing(
        memory_backend, armed, executed):
    """R12, fail closed: if ATLAS cannot tell whether the database was just
    restored from a backup, no card is claimed or run."""
    claimable(memory_backend)
    inner = memory_backend.fetchrow

    async def broken(sql, *args):
        if "FROM restores" in sql:
            raise ConnectionError("db gone")
        return await inner(sql, *args)

    memory_backend.fetchrow = broken
    verdict = await decide(41, "approve", via="dashboard")
    assert not verdict.ok and verdict.outcome == "refused"
    assert "restored" in verdict.detail and executed == []
    assert not any(sql.startswith("UPDATE proposals SET status='approved'")
                   for sql, _ in memory_backend.sql_log)


def test_a_routine_card_waits_two_weeks_and_an_urgent_one_does_not():
    """the owner, 2026-10-06: "cards should never expire". They expire (silence is
    never consent) but a routine one waits 14 days; urgent clocks stay."""
    from datetime import timedelta

    from atlas.kernel import approvals
    from atlas.kernel.contracts import Severity

    assert approvals.TTL[Severity.NORMAL] == timedelta(days=14)
    assert approvals.TTL[Severity.INFO] == timedelta(days=14)
    assert approvals.TTL[Severity.HIGH] == timedelta(hours=4)
    assert approvals.TTL[Severity.CRITICAL] == timedelta(minutes=30)


# The once-a-day list of waiting cards is now the 9:00 New York digest with
# the approvals board's text: tests/test_approvals_board.py.


async def test_no_open_cards_means_no_notice(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from atlas.kernel import approvals

    async def fetch_one(sql, *a):
        return {"at": datetime.now(UTC) - timedelta(days=2)}

    async def fetch_all(sql, *a):
        return []

    async def notify(msg):
        raise AssertionError("nothing to say")

    monkeypatch.setattr(approvals, "fetch_one", fetch_one)
    monkeypatch.setattr(approvals, "fetch_all", fetch_all)
    monkeypatch.setattr(approvals, "notify", notify)
    nine_ny = datetime(2026, 10, 12, 13, 5, tzinfo=UTC)
    await approvals._open_cards_notice(nine_ny)



# ------------------------------------------------------------------ urgent

async def test_urgent_is_set_only_by_the_proposer_keyword(proposing):
    """`evidence.urgent` is what `owner_calls` rings the phone for. It comes from
    `propose(urgent=True)` in code, never from the evidence dict, which carries
    collector and ingested text (invariant 4)."""
    await propose("gitlab.open_issue", {"title": "a"}, reason="r",
                  evidence={"urgent": True, "subject": "URGENT call me now"},
                  dedup_key="k1")
    await propose("gitlab.open_issue", {"title": "b"}, reason="r",
                  evidence={"note": "x"}, urgent=True, dedup_key="k2")
    await propose("gitlab.open_issue", {"title": "c"}, reason="r",
                  evidence={"note": "x"}, urgent="true", dedup_key="k3")   # not a bool
    await propose("gitlab.open_issue", {"title": "d"}, reason="r", dedup_key="k4")
    by_key = {r["dedup_key"]: r["evidence"] for r in proposing}
    assert "urgent" not in by_key["k1"] and by_key["k1"]["subject"].startswith("URGENT")
    assert by_key["k2"] == {"note": "x", "urgent": True}
    assert "urgent" not in by_key["k3"] and "urgent" not in by_key["k4"]
