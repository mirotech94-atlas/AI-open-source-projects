"""The first ten seconds of a call ATLAS places (kernel/briefing.py): who is
calling, what is wrong in plain words, one yes-or-no question. It re-words
the alert and adds nothing; a card's machine lines, code and addresses are
never read aloud; ATLAS says it is ATLAS."""
from __future__ import annotations

import re
from datetime import datetime

import pytest

from atlas.kernel import briefing as b

CARD = "#284 — Restart the FlyCheap fare-polling worker"
CARD_BODY = ("The fare poller hit a rate wall and the queue is backing up.\n"
             "`flycheap.restart_worker` · Tier 2 · rollback: start it again")


def test_a_page_is_said_in_plain_words_not_as_a_check_key():
    said = b.opening("cloudhost.database is failing",
                     "connection refused: could not connect to server at 10.0.0.4:5432 "
                     "after 3 tries (psycopg.OperationalError). Last good read 14:02 UTC.",
                     at_night=False)
    assert said.startswith("Hey Miro, it's ATLAS. CloudHost's database check is failing.")
    assert "cloudhost.database" not in said
    assert "psycopg" not in said and "OperationalError" not in said
    assert "10.0.0.4" not in said and "5432" not in said
    assert "Last good read 14:02 UTC." in said          # a fact the alert stated


def test_night_says_sorry_once_and_day_does_not():
    night = b.opening("cloudhost.disk is failing", "Disk at 96%.", at_night=True)
    day = b.opening("cloudhost.disk is failing", "Disk at 96%.", at_night=False)
    assert night.count("Sorry to wake you.") == 1 and "Sorry" not in day
    assert "96 percent" in night and "%" not in night


def test_unknown_is_not_called_failing():
    said = b.opening("comms.slack is unknown", at_night=False)
    assert "can't be read" in said and "failing" not in said


def test_a_card_says_its_reason_and_asks_its_action_as_yes_or_no():
    said = b.opening(CARD, CARD_BODY, at_night=False)
    assert said == ("Hey Miro, it's ATLAS. The fare poller hit a rate wall and the "
                    "queue is backing up.")
    assert "Tier" not in said and "rollback" not in said and "#284" not in said
    assert b.action_of(CARD) == "Restart the FlyCheap fare-polling worker"
    assert b.question(b.action_of(CARD)) == (
        "Want me to restart the FlyCheap fare-polling worker, yes or no?")


def test_no_action_named_still_asks_one_plain_question():
    assert b.question("") == "Should I go ahead, yes or no?"
    assert b.action_of("flycheap.database is failing") == ""


def test_the_opening_is_short_enough_to_say_in_about_ten_seconds():
    body = " ".join(f"Sentence number {i} goes on for quite a few more words than needed."
                    for i in range(40))
    for title in ("flycheap.database is failing", CARD, "Stripe: three failed payments"):
        said = b.opening(title, body, at_night=True)
        assert len(said.split()) <= 8 + b.INCIDENT_WORDS + 1, said
    long_action = "Restart " + " ".join(["the"] * 60) + " worker"
    assert len(b.question(long_action).split()) <= b.QUESTION_WORDS + 6


def test_nothing_is_added_that_the_alert_did_not_say():
    """Every word of the incident beat is the alert's own, a spoken estate
    name, or the fixed frame; no number appears that the alert lacked."""
    title, body = "stripe_billing.payouts is failing", "Payout po_1 failed: bank declined."
    said = b.incident(title, body)
    assert set(re.findall(r"\d+", said)) <= set(re.findall(r"\d+", title + body))
    assert "customer" not in said.lower() and "impact" not in said.lower()


@pytest.mark.parametrize("clock,expected", [
    ("2026-10-04T23:30:00", True), ("2026-10-04T03:00:00", True),
    ("2026-10-04T07:00:00", False), ("2026-10-04T14:00:00", False)])
def test_night_is_his_quiet_window_or_eleven_to_seven(monkeypatch, clock, expected):
    monkeypatch.delenv("QUIET_HOURS", raising=False)
    assert b.night(datetime.fromisoformat(clock)) is expected


def test_quiet_hours_when_set_define_the_night(monkeypatch):
    monkeypatch.setenv("QUIET_HOURS", "22:00-06:00")
    assert b.night(datetime.fromisoformat("2026-10-04T22:30:00"))
    assert not b.night(datetime.fromisoformat("2026-10-04T06:30:00"))
    monkeypatch.setenv("QUIET_HOURS", "nonsense-here")
    assert b.night(datetime.fromisoformat("2026-10-04T23:30:00"))


def test_atlas_names_itself_and_never_another_name():
    for night in (True, False):
        said = b.opening("guardian.sensor_heartbeat is failing", at_night=night)
        assert "it's ATLAS" in said and "Victor" not in said
