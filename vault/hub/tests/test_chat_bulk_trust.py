"""'Trust all devices' must act on all of them, or ask -- never pick one.

Reported from the live app. `trust_device` takes exactly one required `mac`,
so a collective request had no honest shape to take: the model narrowed "all"
to a single arbitrary MAC and proposed that. Asked twice, it proposed two
different devices. The user cannot see which one was chosen, which makes it
worse than a refusal.
"""
import pytest

from guardian_hub import models as m
from guardian_hub.chat import registry
from guardian_hub.chat.registry import TOOLS, dispatch
from guardian_hub.chat.tools import MAX_BULK_DEVICES, h_trust_devices
from guardian_hub.db import SessionLocal

from tests.test_chat_tools import ctx_for
from tests.test_seed_helpers import seeded_demo


def test_bulk_trust_is_tier_3_even_though_single_trust_is_tier_2():
    # One "yes" covering six devices deserves to be read first.
    assert TOOLS["trust_device"].tier == 2
    assert TOOLS["trust_devices"].tier == 3
    assert TOOLS["trust_devices"].min_role == "operator"


def test_the_bulk_tool_takes_a_list_so_all_really_means_all():
    schema = TOOLS["trust_devices"].schema
    assert schema["properties"]["macs"]["type"] == "array"
    assert schema["required"] == ["macs"]


def test_its_description_tells_the_model_not_to_pick_one():
    d = TOOLS["trust_devices"].description.lower()
    assert "several" in d
    assert "arbitrary" in d or "never" in d


@pytest.mark.parametrize("macs", [[], None, ["", "  "]])
def test_an_empty_list_asks_rather_than_doing_nothing_quietly(macs, client):
    from guardian_hub.chat.tools import h_trust_devices
    out = h_trust_devices(object(), macs=macs)
    assert out.ok is False
    assert "which devices" in out.text.lower()


def test_a_runaway_list_is_refused_with_a_narrower_question(client):
    from guardian_hub.chat.tools import h_trust_devices
    out = h_trust_devices(object(), macs=[f"aa:bb:cc:00:00:{i:02x}" for i in range(MAX_BULK_DEVICES + 1)])
    assert out.ok is False
    assert str(MAX_BULK_DEVICES) in out.text
    assert "narrow" in out.text.lower()


def _untrusted_macs(limit=3):
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    rows = (db.query(m.Device)
            .filter_by(tenant_id=t.id, trusted=False)
            .limit(limit).all())
    macs = [d.mac for d in rows]
    db.close()
    return macs


def test_it_trusts_every_device_it_was_given(client):
    seeded_demo(client)
    macs = _untrusted_macs(3)
    assert len(macs) >= 2, "demo fixture should have untrusted devices to work with"

    out = h_trust_devices(ctx_for(), macs=macs)
    assert out.ok is True
    assert out.data["trusted"] == macs
    assert not out.data["failed"]

    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    for mac in macs:
        assert db.query(m.Device).filter_by(tenant_id=t.id, mac=mac).one().trusted is True
    db.close()


def test_a_partial_failure_is_never_reported_as_a_clean_sweep(client):
    seeded_demo(client)
    macs = _untrusted_macs(2)
    out = h_trust_devices(ctx_for(), macs=macs + ["ff:ff:ff:ff:ff:ff"])
    assert out.data["trusted"] == macs
    assert out.data["failed"], "an unknown MAC must be reported, not swallowed"
    assert "could not be trusted" in out.text


def test_the_same_device_twice_is_trusted_once(client):
    seeded_demo(client)
    macs = _untrusted_macs(1)
    out = h_trust_devices(ctx_for(), macs=[macs[0], macs[0].upper(), macs[0]])
    assert out.data["trusted"] == [macs[0]]


def test_a_viewer_cannot_bulk_trust_and_is_told_who_can(client):
    seeded_demo(client)
    result = dispatch(ctx_for(role="viewer"), "trust_devices", {"macs": _untrusted_macs(2)})
    assert result.status == "denied"
    assert "operator" in result.text


def test_bulk_trust_always_proposes_before_it_writes(client):
    # Tier 3: it must come back as a proposal, never as a completed write,
    # however the request was phrased.
    seeded_demo(client)
    result = dispatch(ctx_for(), "trust_devices", {"macs": _untrusted_macs(2)})
    assert isinstance(result, registry.ChatProposal)


# --------------------------------------------------------------------------
# The confirmation has to say what it covers
# --------------------------------------------------------------------------

def test_the_confirmation_names_the_count_and_the_devices(client):
    # `_target_of` reads string arguments only, so a list target produced no
    # target at all and the proposal read "Sure, I will trust devices." --
    # thirteen writes behind a yes, with no way to see that from the sentence.
    seeded_demo(client)
    macs = _untrusted_macs(3)
    proposal = dispatch(ctx_for(), "trust_devices", {"macs": macs})
    assert isinstance(proposal, registry.ChatProposal)
    assert f"{len(macs)} devices" in proposal.summary

    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    first = db.query(m.Device).filter_by(tenant_id=t.id, mac=macs[0]).one()
    db.close()
    # Named the way the owner sees it on the dashboard, not as a raw MAC.
    assert (first.label or first.name or first.vendor or first.mac) in proposal.summary


def test_a_long_list_is_summarised_rather_than_recited(client):
    seeded_demo(client)
    macs = _untrusted_macs(12)
    if len(macs) <= registry._BULK_NAMES_SHOWN:
        import pytest as _p
        _p.skip("demo fixture has too few untrusted devices for this case")
    proposal = dispatch(ctx_for(), "trust_devices", {"macs": macs})
    assert f"{len(macs)} devices" in proposal.summary
    assert f"and {len(macs) - registry._BULK_NAMES_SHOWN} more" in proposal.summary


def test_a_single_target_tool_is_unchanged(client):
    # The bulk branch must not touch how every other proposal reads.
    seeded_demo(client)
    mac = _untrusted_macs(1)[0]
    # quarantine_device is tier 2, so it runs straight away when the target is
    # named; `require_confirm` is how a proposal is forced for comparison.
    proposal = dispatch(ctx_for(), "quarantine_device", {"mac": mac, "on": True},
                        require_confirm=True)
    assert isinstance(proposal, registry.ChatProposal)
    assert mac in proposal.summary
    assert "devices (" not in proposal.summary


def test_the_bulk_sentence_is_grammatical(client):
    # The verb falls back to the tool name with the underscore swapped for a
    # space, which for `trust_devices` produced "trust devices 30 devices".
    seeded_demo(client)
    proposal = dispatch(ctx_for(), "trust_devices", {"macs": _untrusted_macs(3)})
    assert "devices 3 devices" not in proposal.summary
    assert proposal.summary.startswith("Sure, I will trust 3 devices (")
