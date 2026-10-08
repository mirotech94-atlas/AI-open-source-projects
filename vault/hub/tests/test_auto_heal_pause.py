"""The platform-wide pause: Vault may watch, but not act on its own.

The one setting in this product that changes what Vault DOES rather than
what it offers or says, so it gets its own file and its own argument.

WHAT "PAUSED" HAS TO MEAN

Not "no repair may happen" -- that would strand an owner whose disk is full at
2am with a switch they flipped in June. It means **nothing runs without a
person saying yes to a specific proposal.** Every repair still works; every
repair now costs an approval.

And it must not revoke consent already given. `registry.dispatch` checks a
`confirm_token` BEFORE it checks the tier, so a proposal somebody authorised
ten seconds before the pause still runs. Taking that back would make the
switch unsafe in the other direction: an owner would not know whether the fix
they just approved had happened.

WHERE IT IS ENFORCED, AND WHY THERE

`chat/tiers.effective_tier` has exactly two callers, and they are the two that
matter: `remedy.resolve_action` decides whether a row offers **Fix** or asks
first, and `registry.dispatch` is what actually runs a tool. One escalation
covers the offer and the execution together. A pause implemented in the UI
alone would leave chat able to run the same repair -- which is the failure this
file exists to prevent, and which no amount of testing the button would catch.
"""

from guardian_hub import models as m, platform_settings, remedy
from guardian_hub.chat import tiers
from guardian_hub.chat.tools import TOOLS
from guardian_hub.db import SessionLocal
from tests.test_remedy import ATTACKER_HOST
from tests.test_seed_helpers import seeded_demo


def _pause(db, on: bool) -> None:
    platform_settings.set_value(db, "auto_heal", on, by="miro")
    platform_settings.refresh(db)


def test_guardian_repairs_on_its_own_by_default():
    """The default is ON. A watchdog that asks before every repair is a pager.

    Asserted against the SPEC's default rather than a stored row, because the
    thing that would break this is somebody changing the default, not somebody
    changing the setting.
    """
    assert platform_settings.SPEC["auto_heal"].default() is True


def test_pausing_turns_a_repair_guardian_would_have_run_into_one_that_asks(client):
    """The offer side: the row stops saying "Fix" and starts asking."""
    tid = seeded_demo(client)
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    alert = db.query(m.Alert).filter(
        m.Alert.tenant_id == tid,
        m.Alert.key.like(f"sec:attacker:{ATTACKER_HOST}:%")).first()
    assert alert is not None

    _pause(db, True)
    running = remedy.resolve(db, tenant, alert, "admin")
    assert (running.kind, running.can_run_now) == ("auto", True)

    _pause(db, False)
    paused = remedy.resolve(db, tenant, alert, "admin")
    db.close()

    assert paused.kind == "approval"
    assert paused.can_run_now is False
    # ...and it says WHY. Telling an owner that blocking an attacker is
    # "disruptive or hard to undo" when in fact they switched Vault off
    # themselves is how a person decides the product is being precious.
    assert "Automatic repair is switched off" in paused.need
    assert "disruptive or hard to undo" not in paused.need


def test_pausing_stops_chat_running_the_same_repair(client):
    """The execution side, which is the half a UI-only pause would miss.

    `effective_tier` is asserted directly because it is the single gate:
    `registry.dispatch` refuses to run anything it returns 3 for, and mints a
    token-bound proposal instead.
    """
    tid = seeded_demo(client)
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    args = {"host": ATTACKER_HOST, "action": "block_ip", "arg": "203.0.113.9"}

    _pause(db, True)
    before = tiers.effective_tier(db, tenant, TOOLS["apply_fix"], args)

    _pause(db, False)
    after = tiers.effective_tier(db, tenant, TOOLS["apply_fix"], args)
    db.close()

    assert before == 2, "precondition: this repair is one Guardian may normally run"
    assert after == 3, "paused, every repair must ask first"


def test_the_pause_does_not_touch_anything_that_is_not_a_repair(client):
    """Reading stays free.

    Escalating the wrong thing would make the pause a product-wide freeze --
    an owner who cannot list their own devices while "paused" would conclude
    Vault had broken, not that they had switched something off.
    """
    tid = seeded_demo(client)
    db = SessionLocal()
    tenant = db.get(m.Tenant, tid)
    _pause(db, False)
    for name in ("list_devices", "get_overview"):
        tool = TOOLS.get(name)
        if tool is None:
            continue
        assert tiers.effective_tier(db, tenant, tool, {}) == tool.tier, name
    db.close()
