"""Pins the sensor's and the hub's `dns.upstreams` default to `[]`, on both
sides, the same way `test_remedy.py` pins `remedy.py` to the sensor's own
`fix.py`: the hub cannot import `guardian_sensor` (it lives on the sensor
host), so `sensor/guardian_sensor/config.py` is read as *source* and
compared here rather than imported.

Why this needs its own test rather than trusting `test_tenant_defaults.py`'s
general POLICY-vs-sensor parity check: that test catches the two sides
drifting from EACH OTHER, but it would pass just as happily if both sides
drifted back to the same non-empty list together -- and that is exactly the
bug this guards. `dnsfilter.upstreams_for()` in
`sensor/guardian_sensor/dnsfilter.py` treats ANY non-empty `dns.upstreams`
as "an operator who has named their own resolver" and lets it win over
`DEFAULT_UPSTREAMS` (Cloudflare's SECURITY resolver, which actually filters
malware/phishing) and over `FAMILY_UPSTREAMS`. A shipped default was never
an operator's own choice, so a shipped, non-empty `upstreams` here is not a
reasonable value that merely needs to match its twin -- it is a bug on its
own, full stop, and both places that write it must be caught independently.
That is precisely what went unnoticed before this fix: both sides agreed
with each other on the *plain* Cloudflare pair, which resolves everything
and filters nothing, so an equality-only test would have shipped green the
whole time.
"""

import ast
import pathlib

from guardian_hub import tenant_defaults

SENSOR_CONFIG = pathlib.Path(__file__).resolve().parents[2] / "sensor" / "guardian_sensor" / "config.py"


def _sensor_dns_defaults() -> dict:
    """`DEFAULTS["dns"]` out of the sensor's config.py, parsed as source --
    see the module docstring for why this is never an import."""
    tree = ast.parse(SENSOR_CONFIG.read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "DEFAULTS" for t in node.targets):
            return ast.literal_eval(node.value)["dns"]
    raise AssertionError("sensor config.py no longer defines a literal DEFAULTS")


def test_the_sensor_default_upstreams_is_empty_not_an_explicit_resolver():
    upstreams = _sensor_dns_defaults()["upstreams"]
    assert upstreams == [], (
        "sensor/guardian_sensor/config.py's DEFAULTS['dns']['upstreams'] grew a value "
        f"again ({upstreams!r}). dnsfilter.upstreams_for() treats ANY non-empty "
        "`upstreams` as an operator's own deliberate resolver choice and lets it "
        "override both DEFAULT_UPSTREAMS (Cloudflare's SECURITY resolver, which "
        "actually filters malware/phishing) and FAMILY_UPSTREAMS -- so a shipped, "
        "non-empty default here is never correct no matter what URL it names: it "
        "silently disables both the security resolver and family filtering on "
        "every stock install, which is exactly the bug this test exists to catch."
    )


def test_the_hub_tenant_default_upstreams_is_empty_not_an_explicit_resolver():
    upstreams = tenant_defaults.POLICY["dns"]["upstreams"]
    assert upstreams == [], (
        "hub/guardian_hub/tenant_defaults.py's POLICY['dns']['upstreams'] grew a "
        f"value again ({upstreams!r}). This is what `new_tenant_config()` writes "
        "into every freshly created Tenant.config and the sensor then pulls "
        "verbatim, so a non-empty default here reaches production tenants even if "
        "the sensor's own default (checked separately, above) stays empty -- "
        "either side alone is enough to silently turn off Cloudflare's security "
        "resolver and family filtering for real customers."
    )


def test_the_two_sides_still_agree_on_empty():
    # Not a substitute for the two tests above -- see the module docstring --
    # but worth stating once directly: today the only value both sides may
    # ever agree on is `[]`.
    assert tenant_defaults.POLICY["dns"]["upstreams"] == _sensor_dns_defaults()["upstreams"] == []
