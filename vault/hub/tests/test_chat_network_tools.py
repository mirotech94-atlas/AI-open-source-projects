"""Anything an admin can do to a network in the app, they can also ask for.

The Networks screen has had create/rename/delete since Task 52; the assistant
had no tool for any of it, so "add the guest wifi" was a thing Vault could
see, explain and alert on but not do. These three tools close that, and every
one of them calls the same route function the dashboard button calls -- which
is what keeps the audit row, the validation and (the part nothing else here
can substitute for) the single whole-config `Edit` the sensor replays.

The assertions worth keeping when this file is next edited:

- the rename leaves the SLUG alone. It is the network segment of every LAN
  alert key (`lan:<slug>:new:<mac>`) and the `?network=` handle in links, so
  re-slugging on a rename would orphan the network's open alerts -- they
  could never close, and the next ingest would open duplicates under the new
  slug. A rename is a display change and nothing more.
- a name that matches nothing, or matches twice, is a question. People name
  networks, not UUIDs, and picking one of two "Lab" networks to delete is a
  data move nobody watching the chat can see happen.
"""

import pytest

from guardian_hub import models as m, networks
from guardian_hub.chat import registry
from guardian_hub.chat.registry import TOOLS, dispatch
from guardian_hub.chat.tools import h_add_network, h_remove_network, h_rename_network
from guardian_hub.db import SessionLocal

from tests.test_chat_tools import ctx_for
from tests.test_seed_helpers import seeded_demo


def _tenant_id() -> str:
    db = SessionLocal()
    try:
        return db.query(m.Tenant).filter_by(slug="demo").one().id
    finally:
        db.close()


def _network(slug: str):
    db = SessionLocal()
    try:
        return db.query(m.Network).filter_by(tenant_id=_tenant_id(), slug=slug).one()
    finally:
        db.close()


def _primary():
    db = SessionLocal()
    try:
        return networks.primary(db, _tenant_id())
    finally:
        db.close()


def _add(name="Clinic annex", subnet_prefix="10.0.5", **kw):
    out = h_add_network(ctx_for(), name=name, subnet_prefix=subnet_prefix, **kw)
    assert out.ok is True, out.text
    return out


# --------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------

def test_all_three_are_admin_only_like_the_routes_they_call():
    for name in ("add_network", "rename_network", "remove_network"):
        assert TOOLS[name].min_role == "admin", name
        # Tier 3, the weight `set_policy` carries: each one writes the whole
        # config back and the sensor replays it.
        assert TOOLS[name].tier == 3, name
    assert TOOLS["add_network"].reversible is True
    assert TOOLS["rename_network"].reversible is True
    assert TOOLS["remove_network"].reversible is False


def test_deleting_a_network_has_nowhere_to_default_to():
    # `reassign` is required in the schema, not defaulted to the primary:
    # moving someone's devices somewhere they did not name is the silent data
    # shuffle this tool exists to avoid.
    assert TOOLS["remove_network"].schema["required"] == ["target", "reassign"]


# --------------------------------------------------------------------------
# Create
# --------------------------------------------------------------------------

def test_adding_a_network_through_chat_really_inserts_it(client):
    seeded_demo(client)
    out = _add(name="Clinic annex", subnet_prefix="10.0.5", kind="lab",
               expected_gateway="10.0.5.1", colour="#2f6fed")

    row = _network("clinic-annex")
    assert (row.name, row.kind, row.subnet_prefix) == ("Clinic annex", "lab", "10.0.5")
    assert row.expected_gateway == "10.0.5.1"
    assert row.colour == "#2f6fed"
    assert row.is_primary is False
    assert out.data["id"] == row.id


def test_a_chat_added_network_reaches_the_sensor_as_a_whole_config_edit(client):
    # The one thing a hand-rolled insert would skip. The sensor replays
    # `/api/config` with `cfg.clear(); cfg.update(body["config"])`, so a
    # network that never landed in an Edit exists on the hub only.
    seeded_demo(client)
    _add(name="Guest wifi", subnet_prefix="192.168.9")

    db = SessionLocal()
    edit = (db.query(m.Edit).filter_by(tenant_id=_tenant_id(), path="/api/config")
            .order_by(m.Edit.id.desc()).first())
    names = [n.get("name") for n in edit.body["config"]["networks"]]
    assert "Guest wifi" in names
    assert edit.who == "chat:demoadmin"
    # The whole config, not just the networks block.
    assert set(edit.body["config"]) - {"networks"}
    db.close()


def test_a_bad_subnet_is_refused_in_the_routes_own_words(client):
    seeded_demo(client)
    out = h_add_network(ctx_for(), name="Broken", subnet_prefix="10.0.5.7")
    assert out.ok is False
    assert "three octets" in out.text


# --------------------------------------------------------------------------
# Rename
# --------------------------------------------------------------------------

def test_renaming_changes_the_name_and_leaves_the_slug_alone(client):
    seeded_demo(client)
    _add()
    before = _network("clinic-annex")

    out = h_rename_network(ctx_for(), target="Clinic annex", name="Treatment wing")
    assert out.ok is True

    after = _network("clinic-annex")
    assert after.id == before.id
    assert after.name == "Treatment wing"
    # The invariant: `slugify("Treatment wing")` would be `treatment-wing`, and
    # a rename must NOT produce it. Every `lan:clinic-annex:new:<mac>` alert
    # already open on this network, and every `?network=clinic-annex` link,
    # keeps resolving.
    assert after.slug == "clinic-annex"

    db = SessionLocal()
    assert networks.resolve(db, _tenant_id(), "clinic-annex").id == before.id
    db.close()


def test_a_rename_that_says_nothing_about_colour_keeps_the_colour(client):
    # `update_network` reads its patch with `exclude_unset`, so passing an
    # unmentioned `colour=None` through would blank the chip's colour.
    seeded_demo(client)
    _add(colour="#2f6fed")
    h_rename_network(ctx_for(), target="clinic-annex", name="Treatment wing")
    assert _network("clinic-annex").colour == "#2f6fed"


def test_the_rename_says_the_links_still_work(client):
    seeded_demo(client)
    _add()
    out = h_rename_network(ctx_for(), target="Clinic annex", name="Treatment wing")
    assert "keep working" in out.text


# --------------------------------------------------------------------------
# Delete
# --------------------------------------------------------------------------

def _device_onto(network_id: str) -> str:
    """Move one demo device onto `network_id`; returns its MAC."""
    db = SessionLocal()
    row = db.query(m.Device).filter_by(tenant_id=_tenant_id()).first()
    row.network_id = network_id
    mac = row.mac
    db.commit()
    db.close()
    return mac


def test_deleting_moves_the_devices_to_the_network_that_was_named(client):
    seeded_demo(client)
    _add()
    annex, prim = _network("clinic-annex"), _primary()
    mac = _device_onto(annex.id)

    out = h_remove_network(ctx_for(), target="Clinic annex", reassign=prim.name)
    assert out.ok is True, out.text

    db = SessionLocal()
    assert db.query(m.Network).filter_by(id=annex.id).first() is None
    assert db.query(m.Device).filter_by(tenant_id=_tenant_id(), mac=mac).first().network_id == prim.id
    db.close()
    assert prim.name in out.text
    assert "cannot be undone" in out.text


def test_the_primary_network_cannot_be_deleted_and_the_refusal_says_why(client):
    seeded_demo(client)
    _add()
    prim = _primary()

    # A valid reassign target, so the refusal that comes back is the route's
    # primary guard rather than this tool's own "which network?".
    out = h_remove_network(ctx_for(), target=prim.name, reassign="Clinic annex")
    assert out.ok is False
    assert "primary network cannot be deleted" in out.text
    assert "attribution fallback" in out.text
    assert _network(prim.slug) is not None


def test_a_reassign_target_that_is_not_a_network_is_a_question_not_a_guess(client):
    seeded_demo(client)
    _add()
    out = h_remove_network(ctx_for(), target="Clinic annex", reassign="the other one")
    assert out.ok is False
    assert "where to move" in out.text
    assert _network("clinic-annex") is not None


# --------------------------------------------------------------------------
# Naming a network the way people do
# --------------------------------------------------------------------------

def test_a_network_can_be_named_by_slug_or_by_any_case_of_its_name(client):
    seeded_demo(client)
    _add()
    for ref in ("clinic-annex", "CLINIC ANNEX", "clinic annex", "annex"):
        out = h_rename_network(ctx_for(), target=ref, name="Clinic annex")
        assert out.ok is True, f"{ref}: {out.text}"


def test_an_exact_name_wins_over_one_it_is_only_part_of(client):
    # The demo watches a network called "Lab"; add "Lab annex" and "lab" is a
    # substring of two names at once. Exact-before-substring is the whole
    # safety property of the two-stage lookup: reorder those branches and
    # naming a network exactly starts coming back as "which one do you mean?"
    # the moment somebody adds a longer name that contains it.
    seeded_demo(client)
    _add(name="Lab annex", subnet_prefix="10.0.6")
    out = h_rename_network(ctx_for(), target="lab", name="Main lab")
    assert out.ok is True, out.text
    assert _network("lab").name == "Main lab"
    assert _network("lab-annex").name == "Lab annex"


def test_a_name_that_matches_nothing_says_which_names_do_exist(client):
    seeded_demo(client)
    prim = _primary()
    out = h_rename_network(ctx_for(), target="Wharehouse", name="Warehouse")
    assert out.ok is False
    assert "no network called" in out.text
    assert prim.name in out.text


def test_two_networks_of_the_same_name_are_asked_about_never_picked(client):
    seeded_demo(client)
    # Two rows, one name, distinct slugs -- the shape a sensor-pushed config
    # and a hub-side create can produce together. `create_network` refuses a
    # slug collision, so this is built directly.
    db = SessionLocal()
    tid = _tenant_id()
    for slug in ("twin-lab", "twin-lab-2"):
        db.add(m.Network(tenant_id=tid, name="Twin lab", slug=slug, kind="lab",
                          subnet_prefix="10.0.5", is_primary=False, status="no-sensor"))
    db.commit()
    db.close()

    out = h_remove_network(ctx_for(), target="Twin lab", reassign=_primary().name)
    assert out.ok is False
    assert "more than one" in out.text
    assert "Which one" in out.text
    # Nothing was deleted while it was ambiguous.
    db = SessionLocal()
    assert db.query(m.Network).filter_by(tenant_id=tid, name="Twin lab").count() == 2
    db.close()


# --------------------------------------------------------------------------
# The gates around them
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name,args", [
    ("add_network", {"name": "Clinic annex", "subnet_prefix": "10.0.5"}),
    ("rename_network", {"target": "Lab", "name": "Treatment wing"}),
    ("remove_network", {"target": "Lab", "reassign": "Office Wi-Fi"}),
])
def test_an_operator_is_refused_and_told_who_can(client, name, args):
    seeded_demo(client)
    result = dispatch(ctx_for(role="operator"), name, args)
    assert result.status == "denied"
    assert "admin" in result.text


def test_deleting_a_network_always_proposes_first_and_names_it(client):
    # Tier 3: it comes back as a proposal, and the sentence the owner says yes
    # to has to name the network being deleted -- not "remove network".
    seeded_demo(client)
    _add()
    args = {"target": "Clinic annex", "reassign": _primary().name}
    proposal = dispatch(ctx_for(), "remove_network", args)
    assert isinstance(proposal, registry.ChatProposal)
    assert "Clinic annex" in proposal.summary
    assert proposal.actions[0].reversible is False
    assert "Not reversible" in proposal.summary
    assert _network("clinic-annex") is not None

    result = dispatch(ctx_for(), "remove_network", args, confirm_token=proposal.token)
    assert result.ok is True, result.text
