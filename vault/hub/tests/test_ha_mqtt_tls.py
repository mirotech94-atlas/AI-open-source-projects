"""MQTT over TLS lets the broker live on a public address -- the owner's Home
Assistant on a VPSie VM -- without the MQTT password crossing the internet
in the clear. Plain TCP stays private-only."""
from guardian_hub.integrations import homeassistant as ha, urlpolicy


def test_tls_is_implied_by_8883_or_a_ca():
    assert ha._mqtt_uses_tls(8883, None) is True
    assert ha._mqtt_uses_tls(1883, "-----BEGIN CERTIFICATE-----\nx\n-----END CERTIFICATE-----") is True
    assert ha._mqtt_uses_tls(1883, None) is False


def test_a_public_broker_is_refused_without_tls_and_allowed_with_it():
    validate = ha.HomeAssistantIntegration().validate
    assert validate({"mqtt_host": "203.0.113.10", "mqtt_port": "1883"}), "plain TCP to a public host must be refused"
    assert not validate({"mqtt_host": "203.0.113.10", "mqtt_port": "8883"}), "TLS to a public host is fine"
    assert not validate({"mqtt_host": "192.168.1.50", "mqtt_port": "1883"}), "plain TCP to a private host is still fine"
    assert validate({"mqtt_host": "127.0.0.1", "mqtt_port": "8883"}), "loopback is refused either way"


def test_public_host_policy_still_blocks_loopback_and_linklocal():
    assert urlpolicy.check_public_host("127.0.0.1", "MQTT host")
    assert urlpolicy.check_public_host("169.254.1.1", "MQTT host")
    assert urlpolicy.check_public_host("203.0.113.10", "MQTT host") == ""
    assert urlpolicy.check_public_host("203.0.113.10:8883", "MQTT host") == ""
