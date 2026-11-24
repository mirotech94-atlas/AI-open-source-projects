"""The approvals board (kernel/board.py) and the card's new face.

Miro, 2026-10-11: "organize Slack approval messages, as I don't know which
one is which and where they all are." One pinned board in #approvals, edited
in place, grouped by area; every card headed "[Area] #id · what approving
does". The board lists; it can never approve or decline anything.
"""
from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta

import pytest

from atlas.channels.slack import SlackChannel
from atlas.kernel import approvals, board, comms
from atlas.kernel.comms import CARD_RULE, Button, Outbound
from atlas.kernel.contracts import ActionResult

NOW = datetime(2026, 10, 11, 18, 0, tzinfo=UTC)        # 14:00 New York


def row(pid, action, params=None, *, expires_days=10.0, age_days=1.5, link=""):
    return {"id": pid, "action": action, "params": params or {},
            "created_at": NOW - timedelta(days=age_days),
            "expires_at": NOW + timedelta(days=expires_days),
            "slack_permalink": link}


@pytest.fixture(autouse=True)
def _fresh_board_state(monkeypatch):
    monkeypatch.setattr(board, "_dirty", True)
    monkeypatch.setattr(board, "_last_at", None)
    monkeypatch.setattr(board, "_last_fp", None)
    monkeypatch.setattr(board, "_last_error", "")
    monkeypatch.setattr(board, "_create_tried_at", None)
    monkeypatch.setattr(board, "_lock", None)


# ------------------------------------------------------------ rendering

def test_the_board_groups_by_area_in_order_and_soonest_expiry_first(monkeypatch):
    from atlas.kernel.registry import REGISTRY

    class Post:
        summary = "Publish the next approved post to a LinkedIn page"
        irreversible = True

    monkeypatch.setitem(REGISTRY.actions, "linkedin.publish_post", Post())
    rows = [row(346, "linkedin.publish_post", {"page": "guardian", "entry_id": "G13"},
                expires_days=13),
            row(313, "school.draft_work", {"item_id": 587}, expires_days=9),
            row(320, "linkedin.publish_post", {"page": "flycheap", "entry_id": "B15"},
                expires_days=10),
            row(357, "academy.send_support_reply", {"draft_id": 1}, expires_days=14),
            row(319, "gitlab.open_issue", {"title": "Portal slow", "project": "m/f"}),
            row(332, "clips.publish_clip_tiktok", {"clip_id": 4}),
            row(333, "agency.send_outreach", {"outreach_id": 2}),
            row(318, "guardian.rescan", {"network": "all"})]
    sections = board.render(rows, NOW)
    head = sections[0]
    assert head.startswith("*Waiting for you: 8*")
    assert ("ATLAS Academy 1 · LinkedIn 2 · Clips 1 · "
            "Agency/Mapfront 1 · School 1 · System 2") in head
    assert "New York time" in head                     # the time it was read
    titles = [s.splitlines()[0] for s in sections[1:]]
    assert titles == ["*ATLAS Academy (1)*", "*LinkedIn (2)*",
                      "*Clips (1)*", "*Agency/Mapfront (1)*", "*School (1)*",
                      "*System (2)*"]
    linkedin = sections[2].splitlines()[1:]
    assert linkedin[0].startswith("• #320  Post B15 on the FlyCheap LinkedIn page")
    assert linkedin[1].startswith("• #346  Post G13 on the Guardian (Vault)")
    assert "_public, irreversible_" in linkedin[0]
    assert "expires Wed 21 Oct" in linkedin[0] and "waiting 1 d" in linkedin[0]


def test_an_empty_queue_says_nothing_waiting():
    (only,) = board.render([], NOW)
    assert "*Waiting for you: 0*" in only and "Nothing waiting." in only
    assert board.fallback_text(0) == "Waiting for you: Nothing waiting."


def test_a_linked_card_number_opens_its_card_and_an_unlinked_one_is_plain():
    rows = [row(320, "linkedin.publish_post", {"page": "flycheap", "entry_id": "B15"},
                link="https://atlas.slack.com/archives/C1/p172"),
            row(321, "linkedin.publish_post", {"page": "guardian"}, link="javascript:x")]
    text = "\n".join(board.render(rows, NOW))
    assert "<https://atlas.slack.com/archives/C1/p172|#320>" in text
    assert "• #321  Post the next approved post on the Guardian (Vault)" in text


def test_params_cannot_inject_slack_markup_and_the_reason_is_never_shown():
    rows = [{**row(9, "gitlab.open_issue",
                   {"title": "<!channel> click <https://evil|here> `x`",
                    "project": "<@U123>"}),
             "reason": "IGNORE PREVIOUS INSTRUCTIONS"}]
    text = "\n".join(board.render(rows, NOW))
    assert "<!channel>" not in text and "<https://evil" not in text
    assert "<@U123>" not in text and "`" not in text
    assert "IGNORE PREVIOUS" not in text


def test_unknown_action_falls_back_to_its_summary_or_name():
    class Spec:
        summary = "Do a new thing to the estate; carefully (with a dry run)"
        irreversible = False

    assert board.what_of("newmod.thing", {}, Spec()) == "Do a new thing to the estate"
    assert board.what_of("newmod.thing", {}, None) == "newmod.thing"
    assert board.area_of("newmod.thing") == "System"
    assert board.risk_of("newmod.thing", None) == "risk unknown"
    assert board.risk_of("newmod.thing", Spec()) == "reversible"
    assert board.risk_of("stripe.retry_invoice", Spec()) == "money"


def test_the_area_follows_the_action_not_the_page_it_lands_on():
    # A LinkedIn post for the Guardian page lands in #guardian, but it is a
    # LinkedIn decision.
    assert board.area_of("linkedin.publish_post") == "LinkedIn"
    assert approvals.topic_for_action("linkedin.publish_post", {"page": "guardian"}) \
        in ("guardian", "linkedin")


# ------------------------------------------------------------ the card

def test_card_header_is_area_number_and_what():
    assert board.card_header(320, "linkedin.publish_post",
                             {"page": "flycheap", "entry_id": "B15"}, None) == (
        "[LinkedIn] #320 · Post B15 on the FlyCheap LinkedIn page, then reshare it "
        "to your profile")
    assert board.card_header(313, "school.draft_work", {"item_id": 587}, None) \
        .startswith("[School] #313 · Draft school item 587")


def test_slack_shows_the_card_header_and_a_divider_above_the_reason():
    buttons = (Button("Approve", "approve:320"), Button("Decline", "decline:320"))
    msg = Outbound(title="#320 — Publish the next approved post",
                   header="[LinkedIn] #320 · Post B15 on the FlyCheap LinkedIn page",
                   body=f"*Approve:* post B15.  *Decline:* nothing.\n{CARD_RULE}\n"
                        "Next approved poll post.", buttons=buttons)
    ch = SlackChannel()
    assert ch.headline(msg) == "[LinkedIn] #320 · Post B15 on the FlyCheap LinkedIn page"
    blocks = ch._blocks(msg)
    assert [b["type"] for b in blocks] == ["header", "section", "divider", "section",
                                           "actions"]
    assert blocks[1]["text"]["text"].startswith("*Approve:*")
    assert "Next approved poll post." in blocks[3]["text"]["text"]
    assert [e["value"] for e in blocks[-1]["elements"]] == ["approve:320", "decline:320"]


async def test_propose_heads_the_card_and_says_what_each_button_does(
        memory_backend, monkeypatch):
    monkeypatch.setenv("APPROVALS_SLACK", "on")      # the Slack card is opt-in now
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
    memory_backend.responses["SELECT id FROM proposals"] = None
    memory_backend.responses["INSERT INTO proposals"] = {"id": 5}
    memory_backend.responses["FROM lessons"] = None
    memory_backend.responses["SELECT"] = []
    board._dirty = False
    await approvals.propose(act.name, {"page": "flycheap", "entry_id": "B1"},
                            reason="Next approved poll post. Approve and it goes out.")
    (msg,) = sent
    assert msg.title == "#5 — Publish"                  # the key everything reads
    assert msg.header.startswith("[LinkedIn] #5 · Post B1 on the FlyCheap LinkedIn page")
    first, rule, rest = msg.body.partition(f"\n{CARD_RULE}\n")
    assert rule and first.startswith("*Approve:* post B1") and "*Decline:*" in first
    assert "Next approved poll post." in rest
    assert [b.value for b in msg.buttons] == ["approve:5", "always:5", "decline:5",
                                              "detail:5"]
    assert board._dirty is True


# ------------------------------------------------------------ links

async def test_the_cards_slack_message_is_recorded_when_it_is_sent(monkeypatch):
    from atlas.kernel import state

    class FakeSlack:
        def channel_for(self, topic):
            return {"flycheap": "C0FLY"}.get(topic, "C0DEF")

    monkeypatch.setitem(comms.CHANNELS, "slack", FakeSlack())
    monkeypatch.setattr(comms, "_REFS", {"#41 — Publish": {"slack": "1727.000100"}})
    wrote = []

    async def execute_sql(sql, *args):
        wrote.append((" ".join(sql.split()), args))

    monkeypatch.setattr(state, "execute_sql", execute_sql)
    await board.record_card(41, "flycheap")
    ((sql, args),) = wrote
    assert sql.startswith("UPDATE proposals SET slack_channel = $2, slack_ts = $3")
    assert args == (41, "C0FLY", "1727.000100")
    wrote.clear()
    await board.record_card(42, "flycheap")             # queued, never sent
    assert wrote == []


async def test_missing_permalinks_are_filled_and_old_cards_found_by_their_buttons(
        monkeypatch):
    from atlas.kernel import state

    class FakeSlack:
        def channel_for(self, topic):
            return "C0SCHOOL"

    monkeypatch.setitem(comms.CHANNELS, "slack", FakeSlack())
    calls = []

    async def slack(method, payload, *, form=False):
        calls.append((method, dict(payload)))
        if method == "conversations.history":
            return {"ok": True, "messages": [
                {"ts": "1.1", "blocks": [{"type": "actions", "elements": [
                    {"value": "approve:999"}]}]},
                {"ts": "2.2", "blocks": [{"type": "actions", "elements": [
                    {"value": "approve:313"}, {"value": "decline:313"}]}]}]}
        if method == "chat.getPermalink":
            return {"ok": True, "permalink": f"https://x.slack.com/p{payload['message_ts']}"}
        raise AssertionError(method)

    wrote = []

    async def execute_sql(sql, *args):
        wrote.append(args)

    monkeypatch.setattr(board, "_slack", slack)
    monkeypatch.setattr(state, "execute_sql", execute_sql)
    rows = [{**row(320, "linkedin.publish_post"), "slack_channel": "C0FLY",
             "slack_ts": "9.9"},
            row(313, "school.draft_work", {"item_id": 587}),
            {**row(321, "linkedin.publish_post"), "slack_link_tried_at": NOW}]
    await board.fill_links(rows)
    assert ("chat.getPermalink", {"channel": "C0FLY", "message_ts": "9.9"}) in calls
    assert ("chat.getPermalink", {"channel": "C0SCHOOL", "message_ts": "2.2"}) in calls
    assert wrote == [(320, "C0FLY", "9.9", "https://x.slack.com/p9.9", True),
                     (313, "C0SCHOOL", "2.2", "https://x.slack.com/p2.2", True)]
    assert rows[1]["slack_permalink"] == "https://x.slack.com/p2.2"
    assert not any(p.get("channel") == "C0FLY" and m == "conversations.history"
                   for m, p in calls)


async def test_a_slack_refusal_leaves_the_card_to_be_tried_again(monkeypatch):
    from atlas.kernel import state

    class FakeSlack:
        def channel_for(self, topic):
            return "C0SCHOOL"

    monkeypatch.setitem(comms.CHANNELS, "slack", FakeSlack())
    answers = {"conversations.history": {"ok": False, "error": "not_in_channel"},
               "chat.getPermalink": {"ok": False, "error": "ratelimited"}}

    async def slack(method, payload, *, form=False):
        return answers[method]

    wrote = []

    async def execute_sql(sql, *args):
        wrote.append(args)

    monkeypatch.setattr(board, "_slack", slack)
    monkeypatch.setattr(state, "execute_sql", execute_sql)
    old = row(313, "school.draft_work", {"item_id": 587})
    new = {**row(320, "linkedin.publish_post"), "slack_channel": "C0FLY",
           "slack_ts": "9.9"}
    await board.fill_links([old, new])
    # The history refusal writes nothing; the permalink refusal keeps the ts
    # but is not marked tried.
    assert wrote == [(320, "C0FLY", "9.9", "", False)]
    assert not old.get("slack_link_tried_at") and not new.get("slack_link_tried_at")
    # A history Slack did return, without the card in it: settled, tried once.
    answers["conversations.history"] = {"ok": True, "messages": []}
    wrote.clear()
    await board.fill_links([old])
    assert wrote == [(313, "", "", "", True)]


def test_a_long_board_stays_inside_slacks_block_limit():
    rows = [row(i, f"ns{i}.thing") for i in range(1, 1200)]
    sections = board.render(rows, NOW)
    assert len(sections) > board.MAX_SECTIONS
    blocks = board.blocks_of(sections)
    assert len(blocks) <= 50
    assert "more card(s) not shown" in blocks[-1]["text"]["text"]


# ------------------------------------------------------------ in place

class FakeSlackApi:
    def __init__(self, update_error=None):
        self.calls = []
        self.saved = ("", "")
        self.update_error = update_error
        self.posted = 0

    async def slack(self, method, payload, *, form=False):
        self.calls.append(method)
        if method == "chat.update":
            if self.update_error:
                return {"ok": False, "error": self.update_error}
            return {"ok": True, "ts": payload["ts"]}
        if method == "chat.postMessage":
            self.posted += 1
            assert not any(b["type"] == "actions" for b in payload["blocks"])
            return {"ok": True, "ts": f"100.{self.posted}"}
        if method == "pins.add":
            return {"ok": True}
        raise AssertionError(method)

    async def saved_board(self):
        return self.saved

    async def save_board(self, where, ts):
        self.saved = (where, ts)


@pytest.fixture
def api(monkeypatch):
    fake = FakeSlackApi()
    monkeypatch.setattr(board, "_slack", fake.slack)
    monkeypatch.setattr(board, "_saved_board", fake.saved_board)
    monkeypatch.setattr(board, "_save_board", fake.save_board)
    return fake


async def test_the_board_is_posted_and_pinned_once_then_edited_in_place(api):
    blocks = board.blocks_of(board.render([], NOW))
    assert await board.publish_board("C0APPR", "t", blocks) == "100.1"
    assert api.calls == ["chat.postMessage", "pins.add"]
    for _ in range(3):
        assert await board.publish_board("C0APPR", "t", blocks) == "100.1"
    assert api.calls[2:] == ["chat.update"] * 3 and api.posted == 1


async def test_a_board_message_that_is_gone_is_posted_again(api):
    api.saved = ("C0APPR", "50.0")
    api.update_error = "message_not_found"
    assert await board.publish_board("C0APPR", "t", []) == "100.1"
    assert api.calls == ["chat.update", "chat.postMessage", "pins.add"]
    assert api.saved == ("C0APPR", "100.1")


async def test_another_update_error_posts_nothing_new(api):
    api.saved = ("C0APPR", "50.0")
    api.update_error = "ratelimited"
    with pytest.raises(RuntimeError, match="ratelimited"):
        await board.publish_board("C0APPR", "t", [])
    assert api.calls == ["chat.update"] and api.posted == 0


@pytest.fixture
def live(monkeypatch, api):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("SLACK_APPROVALS_CHANNEL", "C0APPR123")
    queue = {"rows": [row(313, "school.draft_work", {"item_id": 587}, link="https://l/1"),
                      row(320, "linkedin.publish_post", {"page": "flycheap"},
                          link="https://l/2")]}

    async def pending():
        if isinstance(queue["rows"], Exception):
            raise queue["rows"]
        return [dict(r) for r in queue["rows"]]

    monkeypatch.setattr(board, "pending", pending)
    return queue


async def test_refresh_redraws_on_a_change_and_not_otherwise(live, api):
    assert await board.refresh(NOW) is True
    assert api.posted == 1
    # Nothing changed, nothing due: no write at all.
    assert await board.refresh(NOW + timedelta(seconds=30)) is False
    assert api.calls.count("chat.update") == 0
    # A card expired (or was decided in another process): the pending set
    # changed, so it is redrawn in place.
    live["rows"] = live["rows"][1:]
    assert await board.refresh(NOW + timedelta(seconds=60)) is True
    assert api.calls.count("chat.update") == 1 and api.posted == 1
    # mark_dirty (a card raised or decided here) redraws too.
    board.mark_dirty()
    assert await board.refresh(NOW + timedelta(seconds=90)) is True
    # And the safety net: every few minutes regardless.
    assert await board.refresh(NOW + timedelta(minutes=20)) is True
    assert api.posted == 1


async def test_expiry_marks_the_board_for_a_redraw(monkeypatch):
    async def fetch_all(sql, *a):
        return [{"id": 7, "action": "school.draft_work"}]

    async def nothing(*a, **k):
        return None

    monkeypatch.setattr(approvals, "fetch_all", fetch_all)
    monkeypatch.setattr(approvals, "audit", nothing)
    monkeypatch.setattr(approvals, "_expiry_notice", nothing)
    monkeypatch.setattr(approvals, "_open_cards_notice", nothing)
    from atlas.kernel import judgment
    monkeypatch.setattr(judgment, "record_outcome", nothing)
    board._dirty = False
    assert await approvals.expire_stale() == 1
    assert board._dirty is True


async def test_an_unreadable_queue_never_reads_as_nothing_waiting(live, api):
    live["rows"] = ConnectionError("postgres down")
    assert await board.refresh(NOW) is False
    assert api.calls == []
    assert "could not be read" in board.status()["error"]


async def test_no_channel_and_watch_only_means_no_board_anywhere(monkeypatch, api):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.delenv("SLACK_APPROVALS_CHANNEL", raising=False)

    async def pending():
        return []

    async def channel():
        return None

    asked = []

    async def execute(req):
        asked.append(req.action)
        return ActionResult(False, "watch-only")

    from atlas.kernel import executor
    monkeypatch.setattr(board, "pending", pending)
    monkeypatch.setattr(board, "channel", channel)
    monkeypatch.setattr(executor, "execute", execute)
    assert await board.refresh(NOW) is False
    assert asked == ["approvals_board.create_channel"]    # through the executor
    assert api.calls == []
    assert await board.refresh(NOW + timedelta(minutes=5)) is False
    assert asked == ["approvals_board.create_channel"]    # at most once an hour


# ------------------------------------------------------------ cannot decide

def test_the_board_carries_no_button_so_nothing_can_be_decided_from_it():
    from atlas.api.slack_decisions import card_id

    rows = [row(313, "school.draft_work", {"item_id": 587}, link="https://l/1")]
    blocks = board.blocks_of(board.render(rows, NOW))
    assert {b["type"] for b in blocks} <= {"section", "divider"}
    assert card_id({"blocks": blocks}) is None
    assert board.card_of({"blocks": blocks}) is None
    assert "approve:" not in str(blocks) and "decline:" not in str(blocks)


def test_the_board_module_never_calls_decide():
    tree = ast.parse(inspect.getsource(board))
    called = {getattr(n.func, "attr", getattr(n.func, "id", ""))
              for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "decide" not in called and "_decide" not in called
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                for a in n.names}
    assert not {"decide", "_decide"} & imported


async def test_deciding_marks_the_board_whatever_the_outcome(memory_backend):
    async def fetchrow(sql, *args):
        """No such proposal: the claim matches nothing, the read finds no row."""

    memory_backend.fetchrow = fetchrow
    board._dirty = False
    verdict = await approvals.decide(404, "decline", via="slack:U1")
    assert verdict.outcome == "missing" and board._dirty is True


# ------------------------------------------------------------ the 9:00 digest

@pytest.fixture
def digest(monkeypatch):
    seen = {"last": None, "sent": [], "audited": [],
            "rows": [row(313, "school.draft_work", {"item_id": 587}, age_days=4),
                     row(357, "academy.send_support_reply", {"draft_id": 1},
                         age_days=0)]}

    async def fetch_one(sql, *a):
        return {"at": seen["last"]}

    async def fetch_all(sql, *a):
        return seen["rows"]

    async def notify(msg):
        seen["sent"].append(msg)

    async def audit(event, subject, data=None):
        seen["audited"].append(event)

    async def channel():
        return "C0APPR123"

    monkeypatch.setattr(approvals, "fetch_one", fetch_one)
    monkeypatch.setattr(approvals, "fetch_all", fetch_all)
    monkeypatch.setattr(approvals, "notify", notify)
    monkeypatch.setattr(approvals, "audit", audit)
    monkeypatch.setattr(board, "channel", channel)
    return seen


NINE_NY = datetime(2026, 10, 12, 13, 5, tzinfo=UTC)      # 09:05 EDT


async def test_the_digest_goes_at_nine_new_york_with_the_board_text(digest):
    await approvals._open_cards_notice(NINE_NY - timedelta(hours=1))   # 08:05
    assert digest["sent"] == []
    await approvals._open_cards_notice(NINE_NY)
    (msg,) = digest["sent"]
    assert msg.title == "2 card(s) waiting for you, 1 for over a day"
    assert msg.channel == "C0APPR123" and msg.kind == "digest"
    assert msg.body.startswith("*Waiting for you: 2*")
    assert "*School (1)*" in msg.body and "*ATLAS Academy (1)*" in msg.body
    assert digest["audited"] == ["proposal.open_notice"]


async def test_the_digest_is_once_a_day_and_never_in_the_evening(digest):
    digest["last"] = NINE_NY - timedelta(minutes=30)      # 08:35 the same day
    await approvals._open_cards_notice(NINE_NY)
    assert digest["sent"] == []
    digest["last"] = NINE_NY - timedelta(days=1)
    await approvals._open_cards_notice(NINE_NY + timedelta(hours=9))    # 18:05
    assert digest["sent"] == []


async def test_no_digest_when_nothing_has_waited_a_day(digest):
    digest["rows"] = [row(357, "academy.send_support_reply", {"draft_id": 1},
                          age_days=-0.5)]                # raised 7 h before 09:05
    await approvals._open_cards_notice(NINE_NY)
    assert digest["sent"] == [] and digest["audited"] == []
