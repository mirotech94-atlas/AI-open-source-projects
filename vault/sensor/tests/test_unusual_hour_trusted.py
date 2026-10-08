"""Unusual-hour is for devices he has not vouched for: a trusted device online
at a new hour raises nothing, an untrusted one still does."""
import datetime as dt
import json

from guardian_sensor import alerts, paths


def _history(tmp_path, mac):
    now = dt.datetime.now().astimezone()
    with open(tmp_path / "history.jsonl", "w") as f:
        for d in range(1, 7):                      # six days, always at 09:00
            ts = (now - dt.timedelta(days=d)).replace(hour=9, minute=0, second=0, microsecond=0)
            f.write(json.dumps({"ts": ts.isoformat(timespec="seconds"), "live_macs": [mac]}) + "\n")
    return now.replace(hour=23 if now.hour != 23 else 22)


def _run(monkeypatch, tmp_path, known):
    mac = "aa:bb:cc:dd:ee:ff"
    monkeypatch.setattr(paths, "DATA_DIR", str(tmp_path))
    at = _history(tmp_path, mac)
    res = {"ts": at.isoformat(timespec="seconds"),
           "lan": {"slug": "home", "primary": True, "devices": {"192.168.1.9": mac}}}
    state = {"lan": {"known_macs": {mac: known}}}
    return alerts.unusual_hours(res, state)


def test_untrusted_device_at_a_new_hour_still_alerts(monkeypatch, tmp_path):
    assert len(_run(monkeypatch, tmp_path, {"label": "stranger"})) == 1


def test_trusted_device_at_a_new_hour_is_quiet(monkeypatch, tmp_path):
    assert _run(monkeypatch, tmp_path, {"label": "Miro iPhone", "trusted": True}) == []
