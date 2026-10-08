"""A device about to stop using Vault's resolver announces itself first.

DNS figures only describe the devices that ask Vault (trust plan §2.5G),
and the device doing the lookups you would most want to see is the one most
likely to be on DoH or DoT — where it appears as perfect silence. Silence is
the worst possible rendering, because it is indistinguishable from a quiet,
well-behaved device.

The signal is that encrypted DNS has to BOOTSTRAP over ordinary DNS. Firefox
resolves `use-application-dns.net` to ask the network's permission, then
`mozilla.cloudflare-dns.com` to find its provider; Chrome and the system
resolvers do the equivalent. Those lookups come through Vault's own
resolver, so the one query a device makes before it goes dark is the query
that names it.

This is evidence of intent and capability, not proof of use — a device can
resolve a DoH endpoint and never send a query to it, and a device configured
by IP bootstraps nothing at all. So the finding is worded as "lookups may
not be visible", never as a count of hidden queries. Claiming to know the
size of a gap you cannot see would be the same overreach as scoring an
unmeasured host as healthy.
"""
import datetime as dt
import json
import os

from guardian_sensor import dnsstats


def _log(tmp_path, rows):
    ddir = tmp_path / "dns"
    ddir.mkdir()
    qf = ddir / ("queries-" + dt.date.today().isoformat() + ".jsonl")
    now = dt.datetime.now().astimezone().isoformat()
    with open(qf, "w") as f:
        for r in rows:
            f.write(json.dumps({"ts": now, **r}) + "\n")
    return str(tmp_path)


def _run(monkeypatch, tmp_path, rows, cfg=None):
    root = _log(tmp_path, rows)
    monkeypatch.setattr(dnsstats.paths, "p", lambda name: os.path.join(root, name))
    monkeypatch.setattr(dnsstats.lan, "my_lan_ip", lambda: "192.168.1.2")
    return dnsstats.dns_analyse(cfg or {"dns": {"enabled": True}}, {})


def test_a_firefox_canary_lookup_flags_the_device(monkeypatch, tmp_path):
    out = _run(monkeypatch, tmp_path, [
        {"client": "192.168.1.50", "name": "use-application-dns.net"},
        {"client": "192.168.1.50", "name": "example.com"},
    ])
    flagged = {row["client"] for row in out["encrypted_dns"]}
    assert "192.168.1.50" in flagged


def test_a_known_doh_endpoint_flags_the_device(monkeypatch, tmp_path):
    out = _run(monkeypatch, tmp_path, [
        {"client": "192.168.1.51", "name": "mozilla.cloudflare-dns.com"},
    ])
    row = next(r for r in out["encrypted_dns"] if r["client"] == "192.168.1.51")
    assert row["endpoint"] == "mozilla.cloudflare-dns.com"
    assert row["count"] == 1


def test_a_subdomain_of_a_known_provider_counts(monkeypatch, tmp_path):
    out = _run(monkeypatch, tmp_path, [
        {"client": "192.168.1.52", "name": "abcdef.dns.nextdns.io"},
    ])
    assert any(r["client"] == "192.168.1.52" for r in out["encrypted_dns"])


def test_a_blocked_bootstrap_lookup_still_counts_as_intent(monkeypatch, tmp_path):
    """Blocking the bootstrap is a good outcome and does not make the
    device's intent go away — it is still configured to bypass Vault the
    moment the block is lifted or it reaches a provider by IP."""
    out = _run(monkeypatch, tmp_path, [
        {"client": "192.168.1.53", "name": "dns.google", "blocked": True},
    ])
    assert any(r["client"] == "192.168.1.53" for r in out["encrypted_dns"])


def test_an_ordinary_network_flags_nobody(monkeypatch, tmp_path):
    """The false-positive case. `dns.google` is a DoH endpoint; `google.com`
    is not, and a substring match would flag most of the internet."""
    out = _run(monkeypatch, tmp_path, [
        {"client": "192.168.1.54", "name": "google.com"},
        {"client": "192.168.1.54", "name": "mail.google.com"},
        {"client": "192.168.1.54", "name": "cloudflare.com"},
        {"client": "192.168.1.54", "name": "news.bbc.co.uk"},
    ])
    assert out["encrypted_dns"] == []


def test_repeated_lookups_are_one_row_per_device_and_endpoint(monkeypatch, tmp_path):
    """One device polling its DoH provider all day is one finding, not
    hundreds — the same root-cause dedupe the plan asks for in §2.5D."""
    out = _run(monkeypatch, tmp_path, [
        {"client": "192.168.1.55", "name": "dns.google"} for _ in range(40)
    ])
    rows = [r for r in out["encrypted_dns"] if r["client"] == "192.168.1.55"]
    assert len(rows) == 1
    assert rows[0]["count"] == 40


def test_the_key_is_present_even_when_nothing_was_found(monkeypatch, tmp_path):
    """An absent key and an empty list read the same to a careless caller
    but mean different things; the hub should never have to guess."""
    out = _run(monkeypatch, tmp_path, [{"client": "192.168.1.56", "name": "example.com"}])
    assert out["encrypted_dns"] == []
