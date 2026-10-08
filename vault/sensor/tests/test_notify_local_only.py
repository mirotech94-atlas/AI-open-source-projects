"""Task 40: the hub now owns alert delivery, so notify() must never touch
the network -- see guardian_sensor/notify.py's module docstring."""

from guardian_sensor import notify


def test_notify_never_reaches_the_network_when_the_hub_owns_delivery(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("sensor must not push alerts; the hub delivers")
    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    banners = []
    monkeypatch.setattr(notify, "_mac_banner", lambda title, msg: banners.append(title))
    out = notify.notify("Guardian", "a new device joined", sev="WARN",
                        env={}, cfg={"notify": {"local_only": True}})
    assert out == ["local"] and banners == ["Guardian"]


def test_notify_stays_silent_when_the_hub_is_reachable_and_owns_delivery(monkeypatch):
    """local_only False + enrolled -> no push of any kind (the hub is
    about to deliver this same alert itself)."""
    monkeypatch.setattr(notify.sync, "client_from_disk", lambda: object())
    banners = []
    monkeypatch.setattr(notify, "_mac_banner", lambda title, msg: banners.append(title))
    out = notify.notify("Guardian", "a new device joined", sev="WARN",
                        env={}, cfg={"notify": {"local_only": False}})
    assert out == [] and banners == []


def test_notify_stays_local_when_unenrolled_even_with_local_only_false(monkeypatch):
    """A standalone (never-enrolled) sensor must not be left silent just
    because its config.json still has the default local_only=False."""
    monkeypatch.setattr(notify.sync, "client_from_disk", lambda: None)
    banners = []
    monkeypatch.setattr(notify, "_mac_banner", lambda title, msg: banners.append(title))
    out = notify.notify("Guardian", "still watching", sev="INFO",
                        env={}, cfg={"notify": {"local_only": False}})
    assert out == ["local"] and banners == ["Guardian"]
