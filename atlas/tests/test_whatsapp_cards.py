"""kernel/whatsapp_cards: the catch-up sweep that puts every pending Tier 2 card on
the owner's WhatsApp, and the wording of that message."""
from __future__ import annotations

import types
from datetime import UTC, datetime, timedelta

import pytest

from atlas.kernel import wa_approvals, whatsapp_cards
from atlas.kernel.contracts import ActionResult

NOW = datetime(2026, 11, 11, 15, 0, tzinfo=UTC)


@pytest.fixture
def cards(monkeypatch):
    st = types.SimpleNamespace(
        pending=[], executed=[], slack=[], claims=set(), sent_hour=0, released=[],
        quiet=False, window=True, watch=False, problem=None,
        result=ActionResult(True, "ok"))

    async def pending(now):
        return [dict(c) for c in st.pending
                if str(c["id"]) not in {k for a, k in st.claims
                                        if a == whatsapp_cards.TRY_ACTION}]

    async def claim(action, key):
        if (action, key) in st.claims:
            return False
        st.claims.add((action, key))
        return True

    async def release(a, k):
        st.released.append((a, k))
        st.claims.discard((a, k))

    async def window(now):
        return st.window

    async def sent():
        return st.sent_hour

    async def execute(req):
        st.executed.append(req)
        return st.result

    async def slack(pid, why):
        st.slack.append((pid, why))
        return True

    async def settle(*a, **k):
        return None

    monkeypatch.setenv("WHATSAPP_CARDS", "on")
    monkeypatch.delenv("WHATSAPP_CARDS_PER_HOUR", raising=False)
    monkeypatch.setattr(whatsapp_cards, "_pending", pending)
    monkeypatch.setattr(whatsapp_cards, "_claim", claim)
    monkeypatch.setattr("atlas.kernel.idempotency.release", release)
    monkeypatch.setattr(whatsapp_cards, "setup_problem", lambda: st.problem)
    monkeypatch.setattr(whatsapp_cards, "_attempts", {})
    monkeypatch.setattr(whatsapp_cards, "_watch_logged", False)
    monkeypatch.setattr(whatsapp_cards, "window_state", window)
    monkeypatch.setattr(whatsapp_cards, "sent_last_hour", sent)
    monkeypatch.setattr(whatsapp_cards, "execute", execute)
    monkeypatch.setattr(whatsapp_cards, "_slack_note", slack)
    monkeypatch.setattr(whatsapp_cards, "quiet_now", lambda: st.quiet)
    monkeypatch.setattr(whatsapp_cards, "watch_only", lambda: st.watch)
    monkeypatch.setattr("atlas.kernel.idempotency.settle", settle)
    return st


def _cards(*ids):
    return [{"id": i, "action": "academy.relaunch_tutorial", "created_at": NOW}
            for i in ids]


async def test_off_by_default(cards, monkeypatch):
    monkeypatch.delenv("WHATSAPP_CARDS", raising=False)
    cards.pending = _cards(445)
    assert whatsapp_cards.enabled() is False
    assert await whatsapp_cards.run(NOW) == {"enabled": False}
    assert cards.executed == []


async def test_a_pending_card_is_sent_once_through_the_executor(cards):
    cards.pending = _cards(445)
    out = await whatsapp_cards.run(NOW)
    assert out["sent"] == 1
    req = cards.executed[0]
    assert req.action == "mmt.notify_card" and req.params == {"proposal_id": 445}
    cards.claims.add(whatsapp_cards.CATCHUP_KEY)
    again = await whatsapp_cards.run(NOW)               # dedupe: the try-claim holds
    assert again.get("cards") == 0 and len(cards.executed) == 1


async def test_first_run_sends_only_the_newest_five_and_skips_older_silently(cards):
    cards.pending = _cards(*range(20, 0, -1))           # newest first, as the query
    out = await whatsapp_cards.run(NOW)
    assert out["sent"] == 5 and out["skipped"] == 15
    assert sorted(r.params["proposal_id"] for r in cards.executed) == [16, 17, 18, 19, 20]
    assert cards.slack == []                            # skipped without a word
    # ...and the skipped ones are never picked up again
    assert not await whatsapp_cards._claim(whatsapp_cards.TRY_ACTION, "3")


async def test_later_runs_are_not_catch_up_limited(cards):
    cards.claims.add(whatsapp_cards.CATCHUP_KEY)
    cards.pending = _cards(*range(8, 0, -1))
    assert (await whatsapp_cards.run(NOW))["sent"] == 8


async def test_watch_only_sends_nothing(cards):
    cards.watch = True
    cards.pending = _cards(445)
    out = await whatsapp_cards.run(NOW)
    assert out == {"watch_only": True, "would_send": 1} and cards.executed == []


async def test_quiet_hours_wait_for_morning_and_burn_no_claim(cards):
    cards.quiet = True
    cards.pending = _cards(445)
    out = await whatsapp_cards.run(NOW)
    assert out["quiet_hours"] and cards.executed == [] and cards.claims == set()
    cards.quiet = False
    assert (await whatsapp_cards.run(NOW))["sent"] == 1


async def test_a_closed_window_gives_one_slack_note_not_a_whatsapp(cards):
    cards.window = False
    cards.pending = _cards(445)
    out = await whatsapp_cards.run(NOW)
    assert out["slack_notes"] == 1 and cards.executed == []
    assert cards.slack[0][0] == 445


async def test_the_hourly_cap_stops_the_sweep(cards):
    cards.claims.add(whatsapp_cards.CATCHUP_KEY)
    cards.sent_hour = 5
    cards.pending = _cards(445)
    out = await whatsapp_cards.run(NOW)
    assert out["sent"] == 0 and cards.executed == []


async def test_the_query_leaves_out_expired_decided_and_already_sent_cards(monkeypatch):
    seen: dict = {}

    async def fetch_all(sql, *args):
        seen["sql"], seen["args"] = sql, args
        return []
    monkeypatch.setattr("atlas.kernel.state.fetch_all", fetch_all)
    await whatsapp_cards._pending(NOW)
    sql = seen["sql"]
    assert "status = 'pending'" in sql and "expires_at > $2" in sql
    assert "effect_claims" in sql and "mmt.notify_card" in sql
    assert seen["args"][0] == 2 and seen["args"][1] == NOW


# ------------------------------------------------------------------ the message

def test_a_portal_only_card_message_has_no_code_and_points_at_the_portal():
    from atlas.modules.mmt import notify
    body = notify.compose_card(445, "bizops.send_invoice",
                               {"series": "claude", "episode": 1},
                               NOW + timedelta(days=2), None)
    assert "card #445" in body
    assert "Decide it on the portal" in body
    assert "YES" not in body and "code" not in body.lower()


def test_an_unknown_action_falls_back_to_a_generic_line():
    from atlas.modules.mmt import notify
    body = notify.compose_card(7, "nope.unknown", {"secret": "sk-live-123"},
                               NOW + timedelta(days=1), None)
    assert "sk-live-123" not in body and "#7" in body


def test_chat_can_never_approve_a_portal_only_card():
    ok, reply = wa_approvals.eligibility("bizops.send_invoice", {})
    assert ok is False and reply


MARKER = whatsapp_cards.CATCHUP_KEY


async def test_unconfigured_burns_nothing(cards):
    cards.problem = "MMT_OWNER_WHATSAPP is not set"
    cards.pending = _cards(*range(10, 0, -1))
    out = await whatsapp_cards.run(NOW)
    assert out["not_configured"] and cards.executed == [] and cards.claims == set()
    cards.problem = None
    assert (await whatsapp_cards.run(NOW))["sent"] == 5     # still all there


async def test_a_certain_refusal_frees_the_card_but_is_bounded(cards):
    cards.claims.add(MARKER)
    cards.result = ActionResult(False, "not sent: could not read the card")
    cards.pending = _cards(445)
    await whatsapp_cards.run(NOW)
    assert (whatsapp_cards.TRY_ACTION, "445") not in cards.claims
    await whatsapp_cards.run(NOW)
    await whatsapp_cards.run(NOW)
    await whatsapp_cards.run(NOW)
    assert len(cards.executed) == whatsapp_cards.MAX_TRIES   # then it stays claimed
    assert (whatsapp_cards.TRY_ACTION, "445") in cards.claims


async def test_a_send_that_may_have_gone_out_keeps_its_claim(cards):
    cards.claims.add(MARKER)
    cards.result = ActionResult(False, "send failed: timed out")
    cards.pending = _cards(445)
    await whatsapp_cards.run(NOW)
    assert (whatsapp_cards.TRY_ACTION, "445") in cards.claims and not cards.released


async def test_an_unreadable_window_does_nothing_and_retries(cards):
    cards.window = None
    cards.pending = _cards(445)
    out = await whatsapp_cards.run(NOW)
    assert out["window"] == "unreadable" and cards.slack == [] and cards.claims == set()
    cards.window = True
    assert (await whatsapp_cards.run(NOW))["sent"] == 1


async def test_a_closed_window_notes_but_the_card_still_goes_out_later(cards):
    cards.window = False
    cards.pending = _cards(445)
    await whatsapp_cards.run(NOW)
    assert cards.slack and MARKER not in cards.claims
    assert (whatsapp_cards.TRY_ACTION, "445") not in cards.claims
    cards.window = True
    assert (await whatsapp_cards.run(NOW))["sent"] == 1


async def test_a_zero_cap_does_not_take_the_first_run_marker(cards, monkeypatch):
    monkeypatch.setenv("WHATSAPP_CARDS_PER_HOUR", "0")
    cards.pending = _cards(445)
    await whatsapp_cards.run(NOW)
    assert MARKER not in cards.claims and cards.executed == []


async def test_watch_only_logs_once_not_every_pass(cards, caplog):
    cards.watch = True
    cards.pending = _cards(445)
    with caplog.at_level("INFO", logger="atlas.whatsapp_cards"):
        await whatsapp_cards.run(NOW)
        await whatsapp_cards.run(NOW)
    assert len([r for r in caplog.records if "WATCH_ONLY" in r.message]) == 1


async def test_catch_up_skip_reason_is_about_the_newest_five(cards, monkeypatch):
    details: list = []

    async def settle(a, k, d=""):
        details.append(d)
    monkeypatch.setattr("atlas.kernel.idempotency.settle", settle)
    cards.pending = _cards(*range(7, 0, -1))
    await whatsapp_cards.run(NOW)
    assert details and all("newest 5" in d for d in details)
