"""The DNS collector on a day with no queries logged yet.

The query log is per-day -- `queries-YYYY-MM-DD.jsonl` -- and it is only
created when guardian-dns actually answers something. So between midnight and
the first lookup of the day there is no file, every day, on every sensor. A
resolver that is up and simply has not been asked anything yet is the most
ordinary state there is, and it must not read as a broken collector.
"""

import json
import os

from guardian_sensor import dnsstats, paths


def _resolver_is_up(monkeypatch, tmp_path):
    ddir = tmp_path / "dns"
    ddir.mkdir()
    (ddir / "status.json").write_text(json.dumps(
        {"uptime_s": 52641, "queries": 0, "blocked": 0, "total_blocked_domains": 215479}))
    monkeypatch.setattr(paths, "p", lambda *a: str(ddir))
    monkeypatch.setattr(dnsstats.paths, "p", lambda *a: str(ddir))
    monkeypatch.setattr(dnsstats.lan, "my_lan_ip", lambda: "192.168.1.9")
    return ddir


def test_a_quiet_day_still_reports_its_totals(monkeypatch, tmp_path):
    ddir = _resolver_is_up(monkeypatch, tmp_path)
    assert not os.path.exists(os.path.join(str(ddir), "queries-"))  # no log today

    out = dnsstats.dns_analyse({"dns": {"enabled": True, "port": 53}}, {})

    assert out["running"] is True
    # Zero, not absent. The hub reads these keys straight through to the DNS
    # tab and to `selftest`, and an absent counter is indistinguishable there
    # from a collector that fell over.
    assert out["queries_24h"] == 0
    assert out["blocked_24h"] == 0
    assert out["block_pct"] == 0


def test_a_day_with_traffic_counts_it(monkeypatch, tmp_path):
    import datetime as dt
    ddir = _resolver_is_up(monkeypatch, tmp_path)
    now = dt.datetime.now().astimezone().isoformat()
    qf = ddir / ("queries-" + dt.date.today().isoformat() + ".jsonl")
    qf.write_text("\n".join(json.dumps(r) for r in [
        {"ts": now, "name": "example.com", "client": "192.168.1.20", "blocked": False},
        {"ts": now, "name": "ads.example", "client": "192.168.1.20", "blocked": True},
    ]))

    out = dnsstats.dns_analyse({"dns": {"enabled": True, "port": 53}}, {})

    assert out["queries_24h"] == 2
    assert out["blocked_24h"] == 1
