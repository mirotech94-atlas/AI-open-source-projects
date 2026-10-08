"""What a connected channel says it answers must be what it answers.

The Integrations page now prints each connected channel's commands. That is
the kind of copy that rots quietly: someone renames a handler, the page keeps
advertising the old word, and the first person to find out is a customer
typing it into Slack at 2am.

So two things are pinned here. A command that names a slash word must appear
in the module that handles it, and the commands only ever reach a browser for
a channel that is actually connected.
"""
import inspect
import re

from guardian_hub.integrations import registry
from tests.test_seed_helpers import seeded_demo

#: "/status — the score" -> "/status". A line that does not start with a
#: slash word is prose about the channel ("Only the chat you paired..."),
#: which has nothing to grep for.
_SLASH = re.compile(r"^(/[a-z]+)")


def test_every_declared_slash_command_exists_in_its_handler():
    checked = 0
    for kind, integ in registry.REGISTRY.items():
        if not integ.commands:
            continue
        source = inspect.getsource(inspect.getmodule(integ))
        for line in integ.commands:
            m = _SLASH.match(line.strip())
            if not m:
                continue
            word = m.group(1)
            assert word in source, (
                f"{kind} advertises {word} but nothing in its module mentions it")
            checked += 1
    # A guard that silently checks nothing is worse than no guard: if the
    # commands are ever moved elsewhere, this fails rather than passing.
    # 4 is today's floor (Telegram's three plus Discord's one), not a
    # ceiling -- raise it when a provider gains commands.
    assert checked >= 4, f"expected to check several slash commands, checked {checked}"


def test_only_two_way_channels_claim_commands():
    """A purely outbound channel must not advertise anything. Slack is the
    one 'out' provider with commands, and deliberately so: its two-way half
    is the Vault Slack APP (routers/slackbot.py), not the webhook this
    card configures."""
    for kind, integ in registry.REGISTRY.items():
        if not integ.commands:
            continue
        assert kind in ("slack", "telegram", "discord"), (
            f"{kind} declares commands; add it here and say why it can answer")


def test_the_page_never_advertises_commands_for_an_unconnected_channel(client):
    """An unconfigured channel's commands would be a list of things that do
    not answer -- nine channels' worth of them on a page where a tenant has
    set up one."""
    seeded_demo(client)
    cards = {c["kind"]: c for c in client.get("/api/v1/integrations").json()["integrations"]}
    for kind, card in cards.items():
        if not card["configured"]:
            assert card["commands"] == [], f"{kind} is not connected but lists commands"


def test_a_connected_channel_carries_its_commands(client):
    seeded_demo(client)
    r = client.post("/api/v1/integrations", json={
        "kind": "telegram", "enabled": True,
        "fields": {"bot_token": "123:abc", "webhook_secret": "shh"},
    })
    assert r.status_code in (200, 201), r.text
    cards = {c["kind"]: c for c in client.get("/api/v1/integrations").json()["integrations"]}
    tg = cards["telegram"]
    assert tg["configured"] is True
    assert any(c.startswith("/status") for c in tg["commands"]), tg["commands"]


def test_a_paused_channel_advertises_nothing_and_answers_nothing(client):
    """The card's toggle used to mean "stop sending me alerts" only. A paused
    Slack channel still accepted instructions -- and a Slack message runs as
    the tenant's ADMIN, so an owner who thought they had cut the integration
    still had a room full of people able to change their network."""
    seeded_demo(client)
    r = client.post("/api/v1/integrations", json={
        "kind": "telegram", "enabled": False,
        "fields": {"bot_token": "123:abc", "webhook_secret": "shh"},
    })
    assert r.status_code in (200, 201), r.text
    cards = {c["kind"]: c for c in client.get("/api/v1/integrations").json()["integrations"]}
    tg = cards["telegram"]
    assert tg["configured"] is True and tg["enabled"] is False
    # The page badges this "paused"; four things to type under that badge
    # would contradict it.
    assert tg["commands"] == []


def test_a_paused_channels_inbound_webhook_is_refused(client):
    """And the commands really are dead, not just hidden."""
    from guardian_hub import models as m
    from guardian_hub.db import SessionLocal

    seeded_demo(client)
    client.post("/api/v1/integrations", json={
        "kind": "telegram", "enabled": True,
        "fields": {"bot_token": "123:abc", "webhook_secret": "shh"},
    })
    db = SessionLocal()
    row = db.query(m.Integration).filter_by(kind="telegram").one()
    token = row.inbound_token
    db.close()
    assert token, "an inbound-capable card should have minted a token"
    # Live: the route accepts the call.
    assert client.post(f"/inbound/telegram/{token}", json={}).status_code != 404

    client.post("/api/v1/integrations", json={
        "kind": "telegram", "enabled": False, "fields": {},
    })
    # Paused: the same token is refused, and looks exactly like an unknown
    # one -- "real but paused" is not something a caller is owed.
    assert client.post(f"/inbound/telegram/{token}", json={}).status_code == 404


def test_slack_states_the_gate_that_changes_who_can_make_changes(client):
    """With `allowed_users` blank, `routers/slackbot.py` gives every person in
    the channel the viewer role (admin until the 2026-09-28 audit). That is a
    fact about who can change the network, so it belongs in the copy a reader
    sees, not only in a form label they may never scroll to."""
    slack = registry.REGISTRY["slack"]
    joined = " ".join(slack.commands).lower()
    assert "viewer" in joined and "everyone in the channel" in joined

    # And the claim still matches the branch. Asserting only the declared
    # string would keep passing after somebody flipped the default to
    # admin again, leaving the page promising a safety that no longer holds.
    from guardian_hub.routers import slackbot
    source = inspect.getsource(slackbot)
    assert '"admin" if (allowed and' in source, (
        "slackbot no longer defaults a blank allowed_users to viewer; "
        "update SlackIntegration.commands to match")
