"""Vault sets the router's DNS itself, so nobody has to open 192.168.1.1.

Miro's call, and the narrowest possible version of it: the one field this
writes is the DNS server, it reads the config first so it only writes names
that config actually contained, and it records what was there so the change
can be put back exactly.

The rule everything here protects: **one server, not two.** A second entry
is a way around Vault — devices take whichever answers first, so half the
queries would skip the filter and the live stream would quietly be half the
truth.
"""
from guardian_sensor import router_fios as rf


ROD = {
    "lan": {"dns_server_1": "192.168.1.1", "dns_server_2": "8.8.8.8",
            "dns_mode": "auto", "dns_enable": "1"},
}


class _Fake:
    """Stands in for the gateway: records what was posted."""
    def __init__(self, rod=ROD, apply_code=200):
        self.rod, self.apply_code, self.posted = rod, apply_code, []

    def install(self, monkeypatch):
        import json
        def client(base_url, timeout):
            def get(path):
                if "loginStatus" in path:
                    return json.dumps({"loginToken": "lt", "token": "ft", "salt": "s"})
                return 'addROD("lan", ' + json.dumps(self.rod["lan"]) + ');'
            def post(path, form):
                self.posted.append((path, form))
                return (self.apply_code if "apply" in path else 200), ""
            return get, post
        monkeypatch.setattr(rf, "_client", client)
        monkeypatch.setattr(rf, "_login_status",
                            lambda get: {"loginToken": "lt", "token": "ft"})
        return self


def test_it_reads_what_the_router_is_actually_handing_out(monkeypatch):
    """The fact that decides whether DNS filtering works at all — and the
    page used to ask the customer to go and look."""
    f = _Fake().install(monkeypatch)
    out = rf.read_dns("https://192.168.1.1", "pw")
    assert out["ok"]
    assert out["fields"]["dns_server_1"] == "192.168.1.1"
    assert "8.8.8.8" in out["servers"]
    assert not any("apply" in p for p, _ in f.posted), "a read must change nothing"


def test_the_second_box_is_CLEARED_not_filled(monkeypatch):
    """The whole point. Leaving a second server is leaving a way around
    Vault, and the logs would quietly be half the truth."""
    f = _Fake().install(monkeypatch)
    out = rf.set_dns("https://192.168.1.1", "pw", "192.168.1.170")
    assert out["ok"], out
    applied = [form for path, form in f.posted if "apply" in path][0]
    assert applied["dns_server_1"] == "192.168.1.170"
    assert applied["dns_server_2"] == "", "a second entry is a way around the filter"


def test_a_router_that_demands_two_gets_the_same_address_twice(monkeypatch):
    f = _Fake().install(monkeypatch)
    rf.set_dns("https://192.168.1.1", "pw", "192.168.1.170", both_boxes=True)
    applied = [form for path, form in f.posted if "apply" in path][0]
    assert applied["dns_server_1"] == applied["dns_server_2"] == "192.168.1.170", \
        "never somebody else's resolver"


def test_it_records_what_was_there_so_the_change_can_be_put_back(monkeypatch):
    f = _Fake().install(monkeypatch)
    out = rf.set_dns("https://192.168.1.1", "pw", "192.168.1.170")
    assert out["previous"] == {"dns_server_1": "192.168.1.1", "dns_server_2": "8.8.8.8"}

    f.posted.clear()
    back = rf.restore_dns("https://192.168.1.1", "pw", out["previous"])
    assert back["ok"]
    applied = [form for path, form in f.posted if "apply" in path][0]
    assert applied["dns_server_1"] == "192.168.1.1" and applied["dns_server_2"] == "8.8.8.8"


def test_flags_that_merely_LOOK_like_dns_fields_are_never_overwritten(monkeypatch):
    """`dns_mode` and `dns_enable` match the name pattern. Writing an IP
    into a mode flag is how a router ends up in a state nobody can explain."""
    f = _Fake().install(monkeypatch)
    rf.set_dns("https://192.168.1.1", "pw", "192.168.1.170")
    applied = [form for path, form in f.posted if "apply" in path][0]
    assert "dns_mode" not in applied and "dns_enable" not in applied


def test_unknown_config_refuses_instead_of_guessing_a_field_name(monkeypatch):
    """A guessed name posts a change the router silently ignores — the page
    then says "done" and the customer's DNS never moved."""
    f = _Fake(rod={"lan": {"something_else": "1"}}).install(monkeypatch)
    out = rf.set_dns("https://192.168.1.1", "pw", "192.168.1.170")
    assert out["ok"] is False
    assert "nothing was changed" in out["error"]
    assert not any("apply" in p for p, _ in f.posted)


def test_a_bad_address_never_reaches_the_router(monkeypatch):
    f = _Fake().install(monkeypatch)
    assert rf.set_dns("https://192.168.1.1", "pw", "1.1.1.1.1")["ok"] is False
    assert rf.set_dns("https://192.168.1.1", "pw", "")["ok"] is False
    assert f.posted == []


def test_a_refused_apply_is_reported_not_swallowed(monkeypatch):
    f = _Fake(apply_code=500).install(monkeypatch)
    out = rf.set_dns("https://192.168.1.1", "pw", "192.168.1.170")
    assert out["ok"] is False and "apply failed" in out["error"]
    # And it still hands back what it found, so nothing is lost.
    assert out["previous"]["dns_server_1"] == "192.168.1.1"
