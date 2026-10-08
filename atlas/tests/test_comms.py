"""comms.notify — Slack first, Matrix only when Slack is down, and quiet hours
must hold everything below CRITICAL. Getting this wrong is how ATLAS gets
muted in week three, which makes the whole system worthless."""
from __future__ import annotations

from datetime import datetime

import pytest

from atlas.kernel import comms, escalation
from atlas.kernel.comms import Button, Outbound, notify
from atlas.kernel.contracts import Severity


class FakeChannel:
    def __init__(self, name, *, up=True, rich=True):
        self.name = name
        self.rich = rich
        self.interactive = True
        self._up = up
        self.sent: list[Outbound] = []

    async def send(self, msg):
        self.sent.append(msg)
        return f"{self.name}-ts"

    async def available(self):
        return self._up


@pytest.fixture(autouse=True)
def clean_channels(monkeypatch):
    comms.CHANNELS.clear()
    escalation.reset()
    monkeypatch.delenv("QUIET_HOURS", raising=False)
    yield
    comms.CHANNELS.clear()


def install(*channels):
    for c in channels:
        comms.CHANNELS[c.name] = c
    return channels


# ------------------------------------------------- Slack, then Matrix

async def test_slack_is_the_first_choice():
    slack, matrix = install(FakeChannel("slack"), FakeChannel("matrix"))
    used = await notify(Outbound("t", "b", Severity.NORMAL))
    assert used == ["slack"]
    assert slack.sent and matrix.sent == [], "Matrix must not double-notify"


async def test_matrix_is_used_when_slack_is_down():
    install(FakeChannel("slack", up=False), FakeChannel("matrix"))
    used = await notify(Outbound("t", "b", Severity.NORMAL))
    assert used == ["matrix"]


@pytest.mark.parametrize("removed", ["imessage", "whatsapp"])
async def test_a_removed_channel_is_no_longer_routed(removed):
    """Removed at Miro's request, 2026-09-28. Even a stray channel registered
    under that name is never chosen."""
    assert all(removed not in route for route in comms.ROUTING.values())
    stray, _slack = install(FakeChannel(removed), FakeChannel("slack"))
    for sev in (Severity.NORMAL, Severity.CRITICAL):
        await notify(Outbound("t", "b", sev))
    assert stray.sent == []


async def test_critical_fans_out_to_every_available_channel():
    install(FakeChannel("voice"), FakeChannel("slack", up=False),
            FakeChannel("matrix"))
    used = await notify(Outbound("t", "b", Severity.CRITICAL))
    assert set(used) == {"voice", "matrix"}


async def test_non_critical_stops_at_the_first_working_channel():
    install(FakeChannel("slack"), FakeChannel("matrix"))
    used = await notify(Outbound("t", "b", Severity.HIGH))
    assert used == ["slack"]


# --------------------------------------------------------- quiet hours

async def test_quiet_hours_queue_everything_below_critical(monkeypatch, memory_backend):
    queued: list = []

    async def fake_queue(msg):
        queued.append(msg)

    monkeypatch.setattr(comms, "_quiet_now", lambda: True)
    monkeypatch.setattr("atlas.kernel.state.queue_for_brief", fake_queue)
    slack, = install(FakeChannel("slack"))

    for sev in (Severity.INFO, Severity.NORMAL, Severity.HIGH):
        used = await notify(Outbound("t", "b", sev))
        assert used == ["queued"]
    assert len(queued) == 3
    assert slack.sent == [], "nothing below CRITICAL may be delivered"


async def test_critical_breaks_quiet_hours(monkeypatch):
    monkeypatch.setattr(comms, "_quiet_now", lambda: True)
    _voice, = install(FakeChannel("voice"))
    used = await notify(Outbound("t", "b", Severity.CRITICAL))
    assert used == ["voice"]


@pytest.mark.parametrize("window,now,expected", [
    ("22:00-07:00", "23:30", True),     # overnight, after start
    ("22:00-07:00", "03:00", True),     # overnight, before end
    ("22:00-07:00", "12:00", False),
    ("22:00-07:00", "07:00", False),    # end is exclusive
    ("22:00-07:00", "22:00", True),     # start is inclusive
    ("09:00-17:00", "12:00", True),     # same-day window
    ("09:00-17:00", "08:00", False),
    ("", "12:00", False),               # unset: never quiet
])
def test_quiet_window_arithmetic(monkeypatch, window, now, expected):
    monkeypatch.setenv("QUIET_HOURS", window)
    hh, mm = now.split(":")

    # Quiet hours are deliberately local wall-clock time: they are about when
    # Miro is asleep, not about UTC. Freeze the clock in the local zone so
    # `datetime.now().astimezone().time()` reads the intended wall time.
    local_tz = datetime.now().astimezone().tzinfo

    class FrozenNow(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 21, int(hh), int(mm), tzinfo=local_tz)

    monkeypatch.setattr(comms, "datetime", FrozenNow)
    assert comms._quiet_now() is expected


# ----------------------------------------------------------- delivery

async def test_a_channel_that_raises_is_skipped():
    class Broken(FakeChannel):
        async def send(self, msg):
            raise RuntimeError("bridge down")

    install(Broken("slack"), FakeChannel("matrix"))
    used = await notify(Outbound("t", "b", Severity.NORMAL))
    assert used == ["matrix"]


async def test_no_channel_available_returns_empty():
    install(FakeChannel("slack", up=False))
    assert await notify(Outbound("t", "b", Severity.NORMAL)) == []


async def test_buttons_prefer_a_rich_channel():
    # HIGH no longer routes to voice at all (only CRITICAL rings him), so
    # with slack down, matrix really is the only one left.
    install(FakeChannel("slack", up=False), FakeChannel("matrix", rich=False))
    msg = Outbound("t", "b", Severity.HIGH, buttons=(Button("Approve", "approve:1"),))
    used = await notify(msg)
    assert used == ["matrix"], "the only channel available still gets it"


async def test_critical_with_buttons_still_reaches_the_phone():
    """Regression. The rule "don't send a buttonless copy to a channel that
    can't render buttons" was unconditional, so on a CRITICAL *proposal* —
    which always carries approve/decline/detail buttons — every rich=False
    channel after the first was skipped. sms went out (nothing used yet),
    then voice and every other buttonless channel were dropped.

    That silently removed the phone call: the one channel that works when
    Miro is away from a screen, on the one severity that is allowed to ring
    it. The channels with no buttons were never tested together with a
    message that had buttons, which is why this survived.

    (sms was the first channel in that fanout when this was found. It has
    since been removed entirely; voice now leads CRITICAL.)"""
    install(FakeChannel("voice", rich=False), FakeChannel("slack", rich=False),
            FakeChannel("matrix", rich=False))
    msg = Outbound("db down", "primary is unreachable", Severity.CRITICAL,
                   buttons=(Button("Approve", "approve:1"),
                            Button("Decline", "decline:1"),
                            Button("Details", "detail:1")))
    used = await notify(msg)
    assert "voice" in used, "a CRITICAL page must ring the phone"
    assert set(used) == {"voice", "slack", "matrix"}


# --------------------------- G3: "I won't call again once you've arrived"

async def test_notify_texts_instead_of_ringing_again_once_home(monkeypatch):
    """He answered a CRITICAL page while away; now he's home. The re-ring for
    that same still-open page goes out as a text, never a second call."""
    async def home_now():
        return {"home": True, "peers_seen": ["phone"], "read_at": "now",
                "reason": None}
    monkeypatch.setattr(comms, "presence", home_now)
    escalation.record(Severity.CRITICAL, home=False, title="db down")
    escalation.record_outcome(True)

    voice, _slack = install(FakeChannel("voice"), FakeChannel("slack"))
    used = await notify(Outbound("db down", "still down", Severity.CRITICAL))
    assert "voice" not in used
    assert voice.sent == []
    assert "slack" in used


async def test_notify_still_rings_a_genuinely_new_critical(monkeypatch):
    """The settled page above must not gag a fresh emergency."""
    async def home_now():
        return {"home": True, "peers_seen": ["phone"], "read_at": "now",
                "reason": None}
    monkeypatch.setattr(comms, "presence", home_now)
    escalation.record(Severity.CRITICAL, home=False, title="db down")
    escalation.record_outcome(True)

    voice, _slack = install(FakeChannel("voice"), FakeChannel("slack"))
    used = await notify(Outbound("fire", "new incident", Severity.CRITICAL))
    assert "voice" in used
    assert voice.sent


async def test_below_critical_still_prefers_one_rich_channel():
    """The exemption above must not leak downwards: HIGH still stops after a
    channel that can actually render the buttons."""
    install(FakeChannel("slack", rich=False), FakeChannel("matrix", rich=True))
    msg = Outbound("t", "b", Severity.HIGH, buttons=(Button("Approve", "approve:1"),))
    used = await notify(msg)
    assert used == ["slack"], "first available channel is still enough"


# ------------------------------------------------- a Slack channel named for the topic

class HomedSlack(FakeChannel):
    """Slack with SLACK_CHANNELS / SLACK_INSTAGRAM_REELS_CHANNEL naming some
    topics."""

    def __init__(self, homes, **kw):
        super().__init__("slack", **kw)
        self.homes = homes

    def home_for(self, topic):
        return self.homes.get(topic)


async def test_a_named_slack_channel_gets_the_full_card():
    slack, matrix = install(HomedSlack({"instagram_stories": "C0REELS"}),
                            FakeChannel("matrix"))
    card = Outbound("#901 — Post a reel", "the script", Severity.NORMAL,
                    buttons=(Button("Approve", "approve:901"),),
                    topic="instagram_stories")
    used = await notify(card)
    assert used == ["slack"]
    assert slack.sent == [card]                       # the full card, buttons and all
    assert matrix.sent == []
    assert comms.sent_ref("#901 — ", "slack") == "slack-ts"


async def test_an_unnamed_topic_takes_the_default_route():
    slack, matrix = install(HomedSlack({"instagram_stories": "C0REELS"}),
                            FakeChannel("matrix"))
    used = await notify(Outbound("t", "b", Severity.NORMAL, topic="vpsie"))
    assert used == ["slack"] and slack.sent and matrix.sent == []


async def test_a_named_channel_with_slack_down_falls_back_to_matrix():
    _slack, matrix = install(HomedSlack({"instagram_stories": "C0REELS"}, up=False),
                             FakeChannel("matrix"))
    used = await notify(Outbound("t", "b", Severity.NORMAL, topic="instagram_stories"))
    assert used == ["matrix"] and matrix.sent[0].body == "b"


async def test_the_real_slack_channel_names_its_homes(monkeypatch):
    from atlas.channels.slack import SlackChannel
    monkeypatch.setenv("SLACK_CHANNEL", "CDEFAULT")
    monkeypatch.setenv("SLACK_CHANNELS", "vpsie:CVPS")
    monkeypatch.setenv("SLACK_INSTAGRAM_REELS_CHANNEL", "C0REELS")
    ch = SlackChannel()
    assert ch.home_for("vpsie") == "CVPS"
    assert ch.home_for("instagram_stories") == "C0REELS"
    assert ch.home_for("mail") is None and ch.channel_for("mail") == "CDEFAULT"


async def test_a_critical_page_rings_first_even_on_a_named_topic(monkeypatch):
    """Review C3: the named-channel rule is below CRITICAL only. The phone
    rings first; Slack's named channel still gets the page, last."""
    order: list[str] = []

    class Recording(FakeChannel):
        async def send(self, msg):
            order.append(self.name)
            return await super().send(msg)

    class RecordingSlack(HomedSlack):
        async def send(self, msg):
            order.append(self.name)
            return await super().send(msg)

    install(Recording("voice"), RecordingSlack({"vpsie": "CVPS"}),
            Recording("matrix"))
    used = await notify(Outbound("vpsie is down", "b", Severity.CRITICAL,
                                 topic="vpsie"))
    assert order[0] == "voice"
    assert order == ["voice", "slack", "matrix"]
    assert set(used) == {"voice", "slack", "matrix"}


# ------------------------- quiet hours + a named Slack channel (Task 56)

async def test_a_reel_card_at_0300_reaches_slack_and_nothing_else(
        monkeypatch, memory_backend):
    """A card routed to its own Slack channel (#instagram-reels) is not a
    page and does not wake him -- it is delivered there straight through
    quiet hours. No phone, no other channel."""
    monkeypatch.setattr(comms, "_quiet_now", lambda: True)
    matrix, slack = install(
        FakeChannel("matrix"), HomedSlack({"instagram_stories": "C0REELS"}))
    card = Outbound("#901 — Post a reel", "the script", Severity.NORMAL,
                    buttons=(Button("Approve", "approve:901"),),
                    topic="instagram_stories")
    used = await notify(card)
    assert used == ["slack"]
    assert slack.sent == [card]
    assert matrix.sent == [], "no other delivery is added by this"


async def test_the_preview_upload_receipt_also_reaches_slack_in_quiet_hours(
        monkeypatch, memory_backend):
    """The card's preview-link receipt (kind='receipt') is the other half of
    'a card and its preview upload' -- it bypasses quiet hours the same way,
    even though it carries no buttons."""
    monkeypatch.setattr(comms, "_quiet_now", lambda: True)
    matrix, slack = install(
        FakeChannel("matrix"), HomedSlack({"instagram_stories": "C0REELS"}))
    receipt = Outbound("#901 — watch the reel before you decide", "link",
                       Severity.NORMAL, topic="instagram_stories", kind="receipt")
    used = await notify(receipt)
    assert used == ["slack"]
    assert slack.sent == [receipt]
    assert matrix.sent == []


async def test_the_1700_nudge_still_respects_quiet_hours(monkeypatch,
                                                          memory_backend):
    """The nudge shares the reels topic and its dedicated Slack channel, but
    it is a plain alert (no buttons, kind='alert') -- not a card, and not a
    preview. Quiet hours still hold it for the morning brief."""
    queued: list = []

    async def fake_queue(msg):
        queued.append(msg)

    monkeypatch.setattr(comms, "_quiet_now", lambda: True)
    monkeypatch.setattr("atlas.kernel.state.queue_for_brief", fake_queue)
    matrix, slack = install(
        FakeChannel("matrix"), HomedSlack({"instagram_stories": "C0REELS"}))
    nudge = Outbound("2 reels waiting", "approve one", Severity.NORMAL,
                     topic="instagram_stories", kind="alert")
    used = await notify(nudge)
    assert used == ["queued"]
    assert queued == [nudge]
    assert slack.sent == [] and matrix.sent == []


async def test_a_critical_named_channel_page_is_unaffected_by_the_bypass(
        monkeypatch):
    """CRITICAL already breaks quiet hours entirely (invariant unchanged);
    the new bypass must not change that fan-out."""
    monkeypatch.setattr(comms, "_quiet_now", lambda: True)
    install(FakeChannel("voice"), FakeChannel("matrix"),
            HomedSlack({"instagram_stories": "C0REELS"}))
    used = await notify(Outbound("fire", "b", Severity.CRITICAL,
                                 topic="instagram_stories"))
    assert "voice" in used and "slack" in used and "matrix" in used


async def test_tiktok_and_the_stories_account_share_the_reels_channel(monkeypatch):
    """Miro, 2026-09-27: Instagram reels and TikTok both go to #reels. TikTok's
    upload card used to have no home and fell to the default channel. The
    main Instagram account is not a reels account and keeps the default."""
    from atlas.channels.slack import SlackChannel
    monkeypatch.setenv("SLACK_CHANNEL", "CDEFAULT")
    monkeypatch.setenv("SLACK_CHANNELS", "vpsie:CVPS")
    monkeypatch.setenv("SLACK_INSTAGRAM_REELS_CHANNEL", "C0REELS")
    ch = SlackChannel()
    for topic in ("instagram_stories", "tiktok"):
        assert ch.home_for(topic) == "C0REELS", topic
    assert ch.home_for("instagram") is None
    # An explicit SLACK_CHANNELS entry still wins.
    monkeypatch.setenv("SLACK_CHANNELS", "vpsie:CVPS,tiktok:CTT")
    assert ch.home_for("tiktok") == "CTT"
