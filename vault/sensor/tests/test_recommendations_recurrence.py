"""Task: "No thanks" hides a recommendation, and it comes back only when the
condition it warned about goes away and then happens again -- not on a
timer, and not merely because the sensor keeps regenerating the same
still-true finding every scan. See `alerts.recommendations()`'s own
docstring-comment for the exact rule.
"""
import copy
import json
import pathlib

from guardian_sensor import alerts

FX = pathlib.Path(__file__).parent / "fixtures"


def load():
    with open(FX / "last.json") as f:
        last = json.load(f)
    with open(FX / "config.json") as f:
        cfg = json.load(f)
    with open(FX / "state.json") as f:
        state = json.load(f)
    return last, cfg, state


def _keys(recs):
    return {r["key"] for r in recs}


def test_declined_recommendation_stays_hidden_while_condition_persists():
    last, cfg, state = load()
    state = copy.deepcopy(state)
    key = "reboot:safehome"
    assert key in _keys(alerts.recommendations(copy.deepcopy(last), cfg, copy.deepcopy(state)))

    state["rec_decisions"] = {key: {"decision": "no"}}
    recs = alerts.recommendations(copy.deepcopy(last), cfg, state)
    assert key not in _keys(recs)

    # Still true on the next scan too, with the SAME still-true condition:
    # must not come back on its own.
    recs = alerts.recommendations(copy.deepcopy(last), cfg, state)
    assert key not in _keys(recs)
    assert state["rec_decisions"][key]["decision"] == "no"


def test_declined_recommendation_returns_only_after_the_condition_recurs():
    last, cfg, state = load()
    state = copy.deepcopy(state)
    key = "reboot:safehome"
    state["rec_decisions"] = {key: {"decision": "no"}}

    # Condition still true: stays hidden, and gets no "absent" mark yet.
    alerts.recommendations(copy.deepcopy(last), cfg, state)
    assert state["rec_decisions"][key]["decision"] == "no"
    assert not state["rec_decisions"][key].get("absent")

    # The condition clears (safehome no longer needs a reboot): the key
    # drops out of this scan's live recommendations, and the decline is
    # marked absent so a later reappearance can be recognised as new.
    gone = copy.deepcopy(last)
    for h in gone["ssh"]:
        if h["name"] == "safehome":
            h["reboot_required"] = False
    recs = alerts.recommendations(gone, cfg, state)
    assert key not in _keys(recs)
    assert state["rec_decisions"][key]["decision"] == "no"
    assert state["rec_decisions"][key]["absent"] is True

    # The condition happens again: this is a recurrence, so the old decline
    # is forgotten outright and the row comes back as a fresh recommendation
    # -- not a lingering "no" that silently flipped back to open.
    recs = alerts.recommendations(copy.deepcopy(last), cfg, state)
    assert key in _keys(recs)
    assert key not in state["rec_decisions"]


def test_declining_one_host_does_not_decline_another():
    last, cfg, state = load()
    state = copy.deepcopy(state)
    # Two low-priority CVE recommendations exist for prod and demo already
    # (fixtures/last.json); declining prod's must not touch demo's.
    state["rec_decisions"] = {"sec:cve:prod": {"decision": "no"}}
    recs = _keys(alerts.recommendations(copy.deepcopy(last), cfg, state))
    assert "sec:cve:prod" not in recs
    assert "sec:cve:demo" in recs


def test_clearing_a_decline_restores_it_immediately():
    last, cfg, state = load()
    state = copy.deepcopy(state)
    key = "reboot:safehome"
    state["rec_decisions"] = {key: {"decision": "no"}}
    assert key not in _keys(alerts.recommendations(copy.deepcopy(last), cfg, state))

    # The owner's manual "bring it back" clears the decision outright (the
    # existing `/api/rec` "clear" path already pops the key) -- no need to
    # wait for the condition to go away and come back.
    state["rec_decisions"].pop(key, None)
    assert key in _keys(alerts.recommendations(copy.deepcopy(last), cfg, state))
