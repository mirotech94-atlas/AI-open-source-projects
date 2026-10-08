from guardian_sensor import VERSION
from guardian_sensor import cli


def test_version_prints_version(capsys):
    cli.main(["version"])
    out = capsys.readouterr().out
    assert out.strip() == f"guardian-sensor {VERSION}"


def test_run_dispatches_to_the_runner(monkeypatch):
    from guardian_sensor import runner
    seen = {}
    monkeypatch.setattr(runner, "main", lambda args: seen.update(once=args.once, no_lan=args.no_lan) or 0)
    assert cli.main(["run", "--once", "--no-lan"]) == 0
    assert seen == {"once": True, "no_lan": True}


def test_enrol_dispatches_to_sync(monkeypatch):
    from guardian_sensor import sync
    seen = {}
    monkeypatch.setattr(sync, "enrol", lambda code, hub: seen.update(code=code, hub=hub) or 0)
    assert cli.main(["enrol", "ABC123", "--hub", "https://example.invalid"]) == 0
    assert seen == {"code": "ABC123", "hub": "https://example.invalid"}


def test_install_dispatches_to_launchd_and_systemd(monkeypatch):
    from guardian_sensor import install
    calls = []
    monkeypatch.setattr(install, "launchd", lambda: calls.append("launchd") or 0)
    monkeypatch.setattr(install, "systemd", lambda: calls.append("systemd") or 0)
    assert cli.main(["install", "--launchd"]) == 0
    assert cli.main(["install", "--systemd"]) == 0
    assert calls == ["launchd", "systemd"]


def test_dns_dispatches_to_the_filter(monkeypatch):
    from guardian_sensor import dnsfilter
    calls = []
    monkeypatch.setattr(dnsfilter, "main", lambda args: calls.append(args) or 0)
    assert cli.main(["dns"]) == 0
    assert len(calls) == 1


def test_selftest_reports_not_implemented_yet(capsys):
    rc = cli.main(["selftest"])
    out = capsys.readouterr().out
    assert "not implemented yet" in out.lower()
    assert rc == 1
