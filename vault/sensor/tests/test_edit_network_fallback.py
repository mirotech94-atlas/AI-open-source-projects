"""A hub edit naming a network slug this sensor does not have is an edit for
the primary network -- and it must land in the slice the next scan commits,
not in a legacy map that gets thrown away.

Seen live: the hub calls the primary "primary" (migration 0007), this sensor
called it "home-network". 56 "trust this device" edits were applied to the
flat map, which is no longer the same object as the primary's slice, so the
next scan committed the unchanged slice and every trust was lost.
"""
from guardian_sensor import sync


def _state():
    slice_known = {"aa:aa:aa:aa:aa:01": {"ip": "192.168.1.5", "trusted": None}}
    return {"lan": {"known_macs": {"aa:aa:aa:aa:aa:01": {"ip": "192.168.1.5", "trusted": None}},   # drifted copy
                    "networks": {"home-network": {"primary": True, "known_macs": slice_known}}}}


def test_an_unknown_slug_resolves_to_the_primary_slice():
    st = _state()
    r = sync.apply_edit({"path": "/api/device", "body": {"mac": "aa:aa:aa:aa:aa:01", "net": "primary", "trusted": True}}, st, {})
    assert r == "state"
    assert st["lan"]["networks"]["home-network"]["known_macs"]["aa:aa:aa:aa:aa:01"]["trusted"] is True


def test_a_known_slug_still_goes_to_its_own_slice():
    st = _state()
    st["lan"]["networks"]["lab"] = {"known_macs": {"bb:bb:bb:bb:bb:02": {"ip": "10.0.0.2"}}}
    sync.apply_edit({"path": "/api/device", "body": {"mac": "bb:bb:bb:bb:bb:02", "net": "lab", "trusted": True}}, st, {})
    assert st["lan"]["networks"]["lab"]["known_macs"]["bb:bb:bb:bb:bb:02"]["trusted"] is True
    assert st["lan"]["networks"]["home-network"]["known_macs"]["aa:aa:aa:aa:aa:01"]["trusted"] is None


def test_no_network_slices_at_all_falls_back_to_the_flat_map():
    st = {"lan": {"known_macs": {"aa:aa:aa:aa:aa:01": {"ip": "x"}}}}
    sync.apply_edit({"path": "/api/device", "body": {"mac": "aa:aa:aa:aa:aa:01", "net": "primary", "trusted": True}}, st, {})
    assert st["lan"]["known_macs"]["aa:aa:aa:aa:aa:01"]["trusted"] is True


def test_the_first_slice_wins_when_none_is_marked_primary():
    st = _state(); st["lan"]["networks"]["home-network"].pop("primary")
    sync.apply_edit({"path": "/api/device", "body": {"mac": "aa:aa:aa:aa:aa:01", "net": "primary", "trusted": True}}, st, {})
    assert st["lan"]["networks"]["home-network"]["known_macs"]["aa:aa:aa:aa:aa:01"]["trusted"] is True
