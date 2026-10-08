"""Typing "trust" at an alert has to trust the device.

Miro did exactly that and Vault repeated the advice back at him. The
reason was structural: with no AI key the turn falls through to
`chat.rules`, which ANSWERS and cannot act — every path that did something
went through a model deciding to call a tool. So the one thing a person is
most likely to type at an alert was the one thing that could not work.

The other half of his instruction was "if it pings then it's trusted". The
ping is shown as EVIDENCE beside the decision, never as the decision: a
device answering a ping proves it is reachable, not that it is yours, and
an intruder's laptop answers perfectly.
"""
from guardian_hub.db import SessionLocal
from guardian_hub import models as m
from guardian_hub.chat import commands
from tests.test_seed_helpers import seeded_demo


def _fresh() -> str:
    """A last_seen inside `effective_presence`'s 15-minute window — outside
    it, an ONLINE row is correctly reported as UNKNOWN."""
    import datetime as dt
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _device(tid, **over):
    row = dict(tenant_id=tid, mac="aa:bb:cc:dd:ee:f0", ip="192.168.1.203",
               name="", label="", trusted=False, presence="ONLINE",
               first_seen="2026-09-22T10:00:00Z", last_seen=_fresh())
    row.update(over)
    db = SessionLocal(); db.add(m.Device(**row)); db.commit(); db.close()


def _match(tid, text):
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(id=tid).one()
    out = commands.match(db, t, text)
    db.close()
    return out


def test_bare_trust_acts_when_exactly_one_device_is_unverified(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    for d in db.query(m.Device).filter_by(tenant_id=tid).all():
        d.trusted = True          # leave exactly one unverified below
    db.commit(); db.close()
    _device(tid)

    tool, args, note = _match(tid, "trust")
    assert tool == "trust_device"
    assert args["mac"] == "aa:bb:cc:dd:ee:f0"
    assert "the one device here that is not verified" in note


def test_bare_trust_stays_silent_when_it_could_mean_two_things(client):
    """Two candidates is a question, not a coin flip — acting on the wrong
    device leaves nothing on any screen saying which one it meant."""
    tid = seeded_demo(client)
    db = SessionLocal()
    for d in db.query(m.Device).filter_by(tenant_id=tid).all():
        d.trusted = True
    db.commit(); db.close()
    _device(tid)
    _device(tid, mac="aa:bb:cc:dd:ee:f1", ip="192.168.1.204")
    assert _match(tid, "trust") is None


def test_trust_by_ip_mac_or_name_all_resolve(client):
    tid = seeded_demo(client)
    _device(tid, label="Back office printer")
    for ref in ("192.168.1.203", "aa:bb:cc:dd:ee:f0", "Back office printer"):
        tool, args, _ = _match(tid, f"trust {ref}")
        assert (tool, args["mac"]) == ("trust_device", "aa:bb:cc:dd:ee:f0"), ref


def test_the_ping_is_evidence_beside_the_decision_not_the_decision(client):
    """Miro's rule was "if it pings then it's trusted". A device answering
    proves it is reachable, not that it is his — so the sweep result is
    quoted next to the action, and the action is still his."""
    tid = seeded_demo(client)
    _device(tid, label="Printer")
    _, _, note = _match(tid, "trust Printer")
    assert "answered Vault's last sweep" in note

    _device(tid, mac="aa:bb:cc:dd:ee:f2", label="Quiet thing",
            presence="OFFLINE", last_seen="2026-09-22T09:00:00+00:00")
    _, _, note2 = _match(tid, "trust Quiet thing")
    assert "did not answer Vault's last sweep" in note2


def test_the_evidence_never_claims_the_hub_pinged_anything(client):
    """The hub runs on a VPS and cannot reach 192.168.x at all. Any "I
    pinged it" here would be invented; the sensor's sweep is what is real,
    and the sentence says which."""
    tid = seeded_demo(client)
    _device(tid, label="Printer")
    _, _, note = _match(tid, "trust Printer")
    assert "sensor on your network" in note
    assert "I pinged" not in note


def test_a_question_about_trust_is_not_a_command(client):
    tid = seeded_demo(client)
    _device(tid)
    for q in ("what does trust mean", "should I trust 192.168.1.203?",
              "why is this device not trusted"):
        assert _match(tid, q) is None, q


def test_distrust_is_never_swallowed_by_the_trust_pattern(client):
    """There is no distrust tool. Inventing one in a command layer is how a
    second, quieter way to change a device's state gets built — it falls
    through to the model, which has `update_device`."""
    tid = seeded_demo(client)
    _device(tid, label="Printer")
    assert _match(tid, "distrust Printer") is None
    assert _match(tid, "untrust it") is None


def test_trusting_an_unknown_device_asks_rather_than_guesses(client):
    tid = seeded_demo(client)
    assert _match(tid, "trust 10.0.0.99") is None


def test_trust_it_through_the_real_chat_endpoint_actually_trusts(client):
    """End to end, the way Miro types it.

    The regex matching is not the claim worth making — the claim is that
    the device comes back trusted afterwards, with no AI provider
    configured, which is the exact situation his hub is in.
    """
    tid = seeded_demo(client)
    db = SessionLocal()
    for d in db.query(m.Device).filter_by(tenant_id=tid).all():
        d.trusted = True
    db.commit(); db.close()
    _device(tid)          # one unverified device, 192.168.1.203

    r = client.post("/api/v1/chat", json={"message": "trust it"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "direct", "it must not fall through to the rules answer"
    assert "Trusted" in body["text"]
    # The evidence rides along with the action.
    assert "sweep" in body["text"]

    db = SessionLocal()
    d = db.query(m.Device).filter_by(tenant_id=tid, mac="aa:bb:cc:dd:ee:f0").one()
    trusted = d.trusted
    db.close()
    assert trusted is True, "the device has to actually come back trusted"


def test_every_way_a_person_says_it_reaches_the_same_action(client):
    tid = seeded_demo(client)
    db = SessionLocal()
    for d in db.query(m.Device).filter_by(tenant_id=tid).all():
        d.trusted = True
    db.commit(); db.close()

    for i, phrase in enumerate(["trust it", "trust", "Trust it.", "trust that device",
                                "mark it trusted"]):
        mac = f"aa:bb:cc:dd:ff:{i:02x}"
        _device(tid, mac=mac, ip=f"192.168.1.{210 + i}")
        out = _match(tid, phrase)
        assert out is not None, phrase
        assert out[1]["mac"] == mac, phrase
        # Trust it so the next loop again has exactly one candidate.
        db = SessionLocal()
        db.query(m.Device).filter_by(tenant_id=tid, mac=mac).one().trusted = True
        db.commit(); db.close()
