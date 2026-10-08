"""The rules fallback never repeats itself, and "why is it down" is answered
about the thing that is down.

The owner's exchange (2026-09-16, Groq rate-limited so `rules.answer` was
the responder): "what is down" -> "1 thing(s) are down. - FlyCheap · site ·
down ... Next: ask me about the first one"; "why is it down" -> the SAME
text again. Two defects: "down" in the follow-up steered it back into the
status branch, and nothing stopped a verbatim repeat.
"""
from tests.test_seed_helpers import seeded_demo
from guardian_hub.db import SessionLocal
from guardian_hub import models as m


def _say(client, text, thread=None):
    body = {"message": text}
    if thread:
        body["thread"] = thread
    r = client.post("/api/v1/chat", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _take_a_site_down(tid, name="Patient portal"):
    db = SessionLocal()
    try:
        site = db.query(m.Site).filter_by(tenant_id=tid).order_by(m.Site.name).first()
        site.name = name
        site.last = {**(site.last or {}), "ok": False, "code": 502, "error": "", "latency_s": 0.4,
                     "url": "https://portal.example/"}
        db.commit()
        return name
    finally:
        db.close()


def test_why_is_it_down_is_answered_about_the_site_not_repeated(client):
    tid = seeded_demo(client)
    name = _take_a_site_down(tid)
    first = _say(client, "what is down")
    assert first["provider"] == "rules"
    assert name in first["text"] and "are down" in first["text"]

    second = _say(client, "why is it down", first["thread_id"])
    assert second["provider"] == "rules"
    assert second["text"] != first["text"], "verbatim repeat"
    assert name in second["text"]
    assert "502" in second["text"], "the reason it is down -- the HTTP status the check saw"
    assert "are down" not in second["text"], "fell back into the status list"


def test_the_same_question_twice_never_gets_the_same_answer_twice(client):
    tid = seeded_demo(client)
    _take_a_site_down(tid)
    first = _say(client, "what is down")
    second = _say(client, "what is down", first["thread_id"])
    assert second["text"] != first["text"]
    # and the second answer says something useful, not an apology
    assert "Next:" in second["text"]


def test_a_repeat_that_differs_only_by_the_data_stamp_still_counts_as_a_repeat(client):
    """Two scans apart, "Everything is up. (data from 18:41, 9 min ago)" and
    "... (data from 18:48, 2 min ago)" are the same answer to a reader."""
    seeded_demo(client)
    first = _say(client, "what is down")
    # Age the first reply's stamp so the two would differ only there.
    from guardian_hub.chat import rules
    assert rules._sans_stamp(first["text"] + "x") != rules._sans_stamp(first["text"])
    assert rules._sans_stamp("Up.\nNext: x. (data from 2026-09-16 18:41, 9 min ago)") == \
        rules._sans_stamp("Up.\nNext: x. (data from 2026-09-16 18:48, 2 min ago)")
    second = _say(client, "what is down", first["thread_id"])
    assert rules._sans_stamp(second["text"]) != rules._sans_stamp(first["text"])
    assert "nothing to explain" in second["text"] or "Next:" in second["text"]
