"""The deterministic fallback has to hold a conversation.

Every test here runs with no provider key configured (the test environment
never sets one), so `providers.chat` falls all the way through to
`rules.answer` and the replies below are genuinely what the owner's
deployment produces. Each test asserts `provider == "rules"` so a future
install that does have a key cannot make these pass vacuously.
"""

from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m

# The demo tenant already carries the alert this task is about: the seeded
# scan is a real capture, and it contains a heartbeat that never checked in.
# Using the seeded row rather than inserting one keeps the test on the same
# data path the owner's hub is on -- the message reached `Alert.msg` through
# `ingest.store_scan`, scrubbing included.
# `demoscrub` renames the captured job, so this is the key as it lands in
# the demo tenant, not the name in the fixture file.
HB_NAME = "nightly-backup"
HB_KEY = f"hb:{HB_NAME}:setup"


def _say(client, text: str, thread: str | None = None) -> dict:
    body = {"message": text}
    if thread:
        body["thread"] = thread
    r = client.post("/api/v1/chat", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_the_owners_exchange(client):
    """The reported failure, end to end: hand off the heartbeat alert, then
    ask the follow-up he actually typed."""
    seeded_demo(client)

    handoff = client.post("/api/v1/chat/handoff", json={"key": HB_KEY}).json()
    assert "cannot fix this one myself" in handoff["opening"]

    out = _say(client, "what can i do to help you solve it", handoff["thread_id"])
    assert out["provider"] == "rules", "this must be the no-provider path"
    text = out["text"]
    assert HB_NAME in text, f"the reply is not about the heartbeat:\n{text}"
    assert "I can answer about:" not in text, f"answered a question with a menu:\n{text}"
    assert "still runs" in text or "check that the job" in text.lower(), text
    # Format the product enforces: a verdict, bullets, a Next line.
    assert out["verdict"] and out["bullets"] and out["next"]


def test_other_follow_ups_stay_on_the_subject(client):
    seeded_demo(client)
    thread = client.post("/api/v1/chat/handoff", json={"key": HB_KEY}).json()["thread_id"]

    for question in ("how do i fix it", "why not", "is that bad", "what do you need from me",
                     "and then?"):
        out = _say(client, question, thread)
        assert out["provider"] == "rules"
        assert "I can answer about:" not in out["text"], f"{question!r} got the menu"
        assert HB_KEY in out["text"] or HB_NAME in out["text"], \
            f"{question!r} drifted off the subject:\n{out['text']}"


def test_how_do_i_fix_it_does_not_fall_into_the_generic_fix_branch(client):
    """"fix" is a keyword branch; the follow-up has to beat it, or the owner
    gets an answer about pending host reboots instead of his heartbeat."""
    seeded_demo(client)
    thread = client.post("/api/v1/chat/handoff", json={"key": HB_KEY}).json()["thread_id"]
    out = _say(client, "how do i fix it", thread)
    assert HB_NAME in out["text"]
    assert "my AI model is unavailable" not in out["text"]


def test_the_reply_admits_what_it_is(client):
    """Honest about the machinery: the fallback says a provider key would do
    better, rather than implying it reasoned about the answer."""
    seeded_demo(client)
    thread = client.post("/api/v1/chat/handoff", json={"key": HB_KEY}).json()["thread_id"]
    out = _say(client, "what can i do to help you solve it", thread)
    assert "fixed rule set" in out["text"] and "provider key" in out["text"]


def test_the_menu_survives_as_the_answer_to_an_opening_capability_question(client):
    seeded_demo(client)
    out = _say(client, "what can you do?")
    assert out["provider"] == "rules"
    assert "I can answer about:" in out["text"]


def test_a_capability_question_mid_thread_still_gets_the_menu(client):
    seeded_demo(client)
    thread = client.post("/api/v1/chat/handoff", json={"key": HB_KEY}).json()["thread_id"]
    out = _say(client, "what can you do?", thread)
    assert "I can answer about:" in out["text"]


def test_a_plain_question_still_gets_its_factual_branch(client):
    """The conversational layer must not swallow the lookups that worked."""
    seeded_demo(client)
    out = _say(client, "what is my security score?")
    assert out["provider"] == "rules"
    assert "security score is" in out["text"]


def test_an_unrecognised_follow_up_with_no_subject_is_not_a_menu(client):
    seeded_demo(client)
    thread = _say(client, "what is my security score?")["thread_id"]
    out = _say(client, "and what about the other one", thread)
    assert "I can answer about:" not in out["text"]
    assert "not sure which thing you mean" in out["text"]


def test_a_new_domain_question_mid_thread_keeps_its_own_branch(client):
    """The follow-up gate must not swallow a question that names its own
    subject: "how is that cert doing" has a pronoun in it but is about
    certificates, and answering it about the heartbeat would replace a right
    answer with a wrong one."""
    seeded_demo(client)
    thread = client.post("/api/v1/chat/handoff", json={"key": HB_KEY}).json()["thread_id"]
    out = _say(client, "how is that cert doing", thread)
    assert "certificate expiry" in out["text"], out["text"]
    assert HB_NAME not in out["text"]

    out = _say(client, "what about that device", thread)
    assert "known devices online" in out["text"], out["text"]


def test_heartbeats_get_a_hub_row_so_the_panel_and_rotation_work(client):
    """The alert points the owner at Security -> Heartbeats. That panel and
    the setup/rotate routes read `m.Heartbeat`, so ingest has to create the
    row -- otherwise the alert points at an empty table."""
    tid = seeded_demo(client)
    db = SessionLocal()
    rows = db.query(m.Heartbeat).filter_by(tenant_id=tid).all()
    assert rows, "the seeded scan declares a heartbeat; the hub must have a row for it"
    assert client.get(f"/api/v1/heartbeats/{rows[0].name}/setup").status_code == 200


def test_a_rotation_survives_the_next_scan(client):
    """`Heartbeat.token` is insert-only. A hub-side rotation that the next
    sensor push reverted would be no rotation at all -- and the sensor keeps
    sending its own copy of the token on every scan."""
    from guardian_hub import ingest

    tid = seeded_demo(client)
    db = SessionLocal()
    name = db.query(m.Heartbeat).filter_by(tenant_id=tid).one().name
    rotated = client.post(f"/api/v1/heartbeats/{name}/rotate").json()
    new_token = rotated["url"].rsplit("/", 1)[1]

    # The next scan, still carrying the sensor's own (now stale) token.
    sensor_token = "FAKE-SENSOR-SIDE-TOKEN-DO-NOT-USE"
    db2 = SessionLocal()
    ingest.store_scan(db2, db2.get(m.Tenant, tid), None, {"last": {
        "ts": "2026-09-09T01:00:00+00:00",
        "heartbeats": {name: {"every_min": 720, "token": sensor_token, "late": True}},
    }}, deliver=False)
    db2.commit()

    db3 = SessionLocal()
    row = db3.query(m.Heartbeat).filter_by(tenant_id=tid, name=name).one()
    assert row.token == new_token, "the scan reverted the rotation"
    assert row.expect_minutes == 720, "expect_minutes is configuration and must follow the sensor"
    assert client.get(f"/hb/{name}/{sensor_token}").status_code == 404
    assert client.get(f"/hb/{name}/{new_token}").json()["ok"] is True
