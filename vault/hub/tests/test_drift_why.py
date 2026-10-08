"""Item 26: a drift alert has to say why the file matters, not just name it."""
from guardian_hub import drift


def test_every_fingerprinted_file_says_what_a_change_would_let_someone_do():
    # The five files the sensor fingerprints are exactly the five an
    # intruder edits to keep a foothold; each must say so in its own terms.
    assert set(drift.WHY_IT_MATTERS) == set(drift.FILE_LABELS), \
        "a file Guardian watches with no explanation is a log line, not a warning"
    for what, why in drift.WHY_IT_MATTERS.items():
        assert len(why) > 60, f"{what}: too short to explain anything"
        assert why.rstrip().endswith("."), what


def test_the_detail_carries_the_explanation(client_free_host=None):
    d = drift.detail({}, "sec:drift:vps1:authorized_keys", "2026-09-21T10:00:00-04:00")
    assert d is not None
    assert d["file"] == "root's authorized_keys"
    assert "without a password" in d["why"].lower()


def test_a_file_with_no_entry_still_gets_an_honest_sentence():
    d = drift.detail({}, "sec:drift:vps1:something_new", "2026-09-21T10:00:00-04:00")
    assert d is not None and d["why"] == drift.WHY_FALLBACK


def test_a_non_drift_key_is_still_none():
    assert drift.detail({}, "sec:ufw:vps1", "2026-09-21T10:00:00-04:00") is None
