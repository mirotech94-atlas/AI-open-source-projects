"""WAN packet-loss probe: a single dropped ping must not raise an alert.

Reported: both anchors showed exactly "20% packet loss" and Vault punted to
"check your router". The probe sent 5 pings, so ONE dropped packet = 20% = the
WARN threshold. Now it sends 20 (one drop = 5%) and confirms any loss with a
second back-to-back run, reporting the lower — a transient blip clears, a real
lossy line survives.
"""
import types

from guardian_sensor import home


class _Run:
    """Feeds home.subprocess.run canned ping output, one call at a time."""
    def __init__(self, losses):
        self.losses = list(losses)
        self.calls = 0

    def __call__(self, *a, **k):
        loss = self.losses[self.calls] if self.calls < len(self.losses) else 0.0
        self.calls += 1
        out = (f"20 packets transmitted, x received, {loss}% packet loss\n"
               "round-trip min/avg/max/stddev = 8.0/10.0/12.0/1.0 ms\n")
        return types.SimpleNamespace(stdout=out)


def _probe(monkeypatch, losses):
    monkeypatch.setattr(home.subprocess, "run", _Run(losses))
    # one anchor only: cfg with no external host still pings cloudflare+google,
    # so restrict by calling the inner ping through wan_path and reading one key
    out = home.wan_path({"home": {}, "ssh_hosts": []})
    return out


def test_a_single_blip_is_confirmed_away(monkeypatch):
    # First sample 20% (a blip), retry 0% -> reported as 0, no alert.
    run = _Run([20.0, 0.0, 20.0, 0.0])  # cloudflare: 20 then 0; google: 20 then 0
    monkeypatch.setattr(home.subprocess, "run", run)
    out = home.wan_path({"home": {}, "ssh_hosts": []})
    assert all(v["loss_pct"] == 0.0 for v in out.values())


def test_sustained_loss_is_reported(monkeypatch):
    # Both samples show real loss -> the lower (25) is reported, above threshold.
    run = _Run([25.0, 25.0, 30.0, 25.0])
    monkeypatch.setattr(home.subprocess, "run", run)
    out = home.wan_path({"home": {}, "ssh_hosts": []})
    assert all(v["loss_pct"] >= 20.0 for v in out.values())


def test_zero_loss_does_not_double_probe(monkeypatch):
    run = _Run([0.0, 0.0])   # only two anchors, one sample each, no retries
    monkeypatch.setattr(home.subprocess, "run", run)
    out = home.wan_path({"home": {}, "ssh_hosts": []})
    assert all(v["loss_pct"] == 0.0 for v in out.values())
    assert run.calls == 2   # no confirm run when the first sample is clean
