"""Ending a maintenance window early.

The route only ever accepted a positive number of hours, so a window could be
started and never cancelled: an operator who typed 8 when they meant 1 had to
sit through seven hours of suppressed non-critical alerts. `hours: 0` now ends
it, and a negative value is still refused.
"""
from tests.test_tenants import make_super


def _hdr(client):
    make_super(client)
    return {}


def test_zero_hours_ends_the_window(client):
    _hdr(client)
    assert client.post("/api/v1/maintenance", json={"hours": 2}).status_code == 200
    r = client.get("/api/v1/settings")
    assert r.status_code == 200

    end = client.post("/api/v1/maintenance", json={"hours": 0})
    assert end.status_code == 200, end.text
    assert end.json()["until"] == ""


def test_a_negative_window_is_still_refused(client):
    _hdr(client)
    assert client.post("/api/v1/maintenance", json={"hours": -3}).status_code == 422
