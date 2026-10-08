"""Every topology node carries the worst OPEN alert on it (CRIT|WARN|None);
the map colours the link to it from this, so map and inbox never disagree."""
from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m

NOW = "2026-09-16T10:00:00-04:00"


def _alert(tid, key, sev):
    db = SessionLocal()
    try:
        db.query(m.Alert).filter_by(tenant_id=tid, key=key).delete()
        db.add(m.Alert(tenant_id=tid, key=key, sev=sev, status="open", msg=key, first_seen=NOW, last_seen=NOW))
        db.commit()
    finally:
        db.close()


def _node(client, node_id):
    return next(n for n in client.get("/api/v1/topology").json()["nodes"] if n["id"] == node_id)


def test_a_device_node_carries_its_worst_open_alert(client):
    tid = seeded_demo(client)
    t = client.get("/api/v1/topology").json()
    dev = next(n for n in t["nodes"] if n["mac"] and n["kind"] not in ("router", "internet"))
    assert dev["alert_sev"] in (None, "WARN", "CRIT")   # field is present on every node
    _alert(tid, f"lan:primary:port:{dev['mac']}:5900", "WARN")
    assert _node(client, dev["id"])["alert_sev"] == "WARN"
    _alert(tid, f"sec:attacker:booking:{dev['mac']}", "CRIT")
    assert _node(client, dev["id"])["alert_sev"] == "CRIT"   # CRIT beats WARN
    assert _node(client, "internet")["alert_sev"] is None


def test_node_carries_the_keys_of_its_open_alerts():
    """The coloured link opens these exact inbox rows."""
    from guardian_hub.payloads.topology import _alerts_for
    from guardian_hub.schemas_dash import TopoNode

    class A:  # minimal stand-in for m.Alert
        def __init__(self, key, sev): self.key, self.sev = key, sev

    rows = [A("lan:primary:port:aa:bb:cc:dd:ee:01:445", "WARN"),
            A("sec:panel:booking:/admin", "CRIT"),
            A("lan:primary:port:11:22:33:44:55:66:23", "WARN")]
    dev = TopoNode(id="d1", kind="device", label="TV", mac="aa:bb:cc:dd:ee:01")
    host = TopoNode(id="h1", kind="server", label="booking", mac=None)
    assert [a.key for a in _alerts_for(dev, rows)] == ["lan:primary:port:aa:bb:cc:dd:ee:01:445"]
    assert [a.key for a in _alerts_for(host, rows)] == ["sec:panel:booking:/admin"]
    assert _alerts_for(TopoNode(id="internet", kind="internet", label="Internet"), rows) == []
