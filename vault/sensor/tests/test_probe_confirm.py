"""confirm(): one bad reading is re-checked before it can raise anything;
humanize(): raw errors become one plain phrase."""
from guardian_sensor import probe, checks


def test_confirm_returns_a_good_first_reading_without_retrying():
    calls = []
    r = probe.confirm(lambda: calls.append(1) or "good", lambda r: r == "bad", pause=0)
    assert r == "good" and len(calls) == 1


def test_confirm_retries_a_bad_reading_and_believes_the_retry():
    seq = iter(["bad", "good"]); calls = []
    r = probe.confirm(lambda: calls.append(1) or next(seq), lambda r: r == "bad", pause=0)
    assert r == "good" and len(calls) == 2   # a transient blip cleared


def test_confirm_reports_a_persistent_problem():
    seq = iter(["bad", "still-bad"])
    assert probe.confirm(lambda: next(seq), lambda r: "bad" in r, pause=0) == "still-bad"


def test_humanize_maps_raw_errors_to_plain_words():
    assert probe.humanize("<urlopen error [Errno 111] Connection refused>") == "connection refused (nothing is listening)"
    assert probe.humanize("timeout") == "timed out (no answer in time)"
    assert probe.humanize("HTTP Error 502: Bad Gateway").startswith("502 bad gateway")
    assert probe.humanize("") == ""


def test_check_one_is_callable_on_its_own_for_retries():
    r = checks.check_one({"name": "x", "type": "nope", "target": "127.0.0.1"})
    assert r["name"] == "x" and r["ok"] is False and r["detail"] == "unknown type"
