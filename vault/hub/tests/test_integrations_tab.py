"""The Integrations page offers every channel Vault speaks -- except the
one being superseded.

ntfy stays registered so a tenant that already relies on it keeps its card
and its alerts, but it is no longer offered to anyone who has not set it
up: push notifications are moving to Vault's own mobile app.
"""
from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.integrations import registry
from tests.test_tenants import make_super

EXPECTED = {"slack", "teams", "pagerduty", "discord", "telegram", "homeassistant", "webhook", "jira", "servicenow"}


def test_every_advertised_channel_is_offered(client):
    make_super(client)
    kinds = {c["kind"] for c in client.get("/api/v1/integrations").json()["integrations"]}
    assert EXPECTED <= kinds, EXPECTED - kinds


def test_ntfy_is_not_offered_to_a_tenant_that_never_set_it_up(client):
    make_super(client)
    kinds = {c["kind"] for c in client.get("/api/v1/integrations").json()["integrations"]}
    assert "ntfy" not in kinds
    assert "ntfy" in registry.REGISTRY, "still registered: existing tenants must keep working"


def test_a_tenant_already_on_ntfy_keeps_its_card(client):
    make_super(client)
    tid = client.get("/api/v1/me").json()["active_tenant"]["id"]
    db = SessionLocal()
    db.add(m.Integration(tenant_id=tid, kind="ntfy", secret="", routes={"CRIT": True}, enabled=True, name="ntfy"))
    db.commit(); db.close()
    kinds = {c["kind"] for c in client.get("/api/v1/integrations").json()["integrations"]}
    assert "ntfy" in kinds


def test_inbound_channels_really_are_two_way():
    # The site says PagerDuty acks close in Vault, Telegram and Home
    # Assistant talk back, Discord takes commands. The registry must agree.
    d = {k: v.direction for k, v in registry.REGISTRY.items()}
    assert d["pagerduty"] == "out+in" and d["telegram"] == "out+in" and d["homeassistant"] == "out+in"
    assert d["discord"] == "out+command"
    assert d["slack"] == "out" and d["teams"] == "out" and d["webhook"] == "out"
