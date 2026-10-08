"""The owner's per-account view must not become a credential exporter.

`GET /api/v1/platform/tenants/{tid}` reads the three tables in the product that
carry bearer credentials -- `integration.secret`, `sensor.token_hash` and
`sensor.enrol_code` -- because the owner legitimately needs to know that Slack
is wired and that a sensor is enrolled. Knowing a channel exists and being able
to post to it are different powers, and only the first belongs on this screen.

CLAUDE.md makes the general rule: this is a security product, so treat its own
output as a disclosure surface, and strip credentials BEFORE the row is built
rather than mutating them out afterwards. `store_scan` pulls heartbeat tokens
out above the insert for exactly this reason.

These tests are the mechanical version of that rule. They assert on the
response CONTRACT rather than on one sampled response, because a payload that
happens to be empty in a fixture tells you nothing about one with a Slack token
in it. The schema cannot express a field it does not have.

Field NAMES only, deliberately: the docstrings on these models explain at
length why the secrets are absent, so a naive substring search over the whole
JSON schema matches its own explanation and reports a leak that is prose.
"""
from guardian_hub import schemas_dash as sd

#: Anything whose name suggests it authenticates. A field matching one of these
#: on an owner-facing model is a bug until someone argues otherwise in review.
FORBIDDEN = ("secret", "token", "enrol", "password", "hash", "credential", "key")


def _fields(model) -> set[str]:
    return set(model.model_fields)


def _leaks(model) -> list[str]:
    return sorted(f for f in _fields(model) if any(bad in f.lower() for bad in FORBIDDEN))


def test_the_account_view_has_no_field_that_authenticates():
    for model in (sd.AccountPayload, sd.AccountSensor, sd.AccountIntegration,
                  sd.AccountUser, sd.AccountNetwork, sd.AccountAuditRow,
                  sd.AccountTrend, sd.AccountTrendsPayload):
        assert _leaks(model) == [], f"{model.__name__} exposes {_leaks(model)}"


#: `HistoryChart` is deliberately NOT in the list above, and this note is here
#: so nobody adds it and then loosens FORBIDDEN to make the suite green again.
#: It carries a field called `key` -- the chart's identifier, "alerts" or
#: "devices" -- which matches FORBIDDEN's "key" without being a credential.
#: The check is top-level field names on purpose (see this module's docstring),
#: so a nested chart is covered by `AccountTrend` having no such field of its
#: own. The right response to it failing is to check the new field, not to
#: shrink the word list.


def test_a_sensor_is_described_without_the_thing_that_speaks_for_it():
    """The owner needs the sensor's name, platform, version, status and when it
    last spoke. None of those authenticate; the token and the enrol code do."""
    assert _fields(sd.AccountSensor) == {"id", "name", "platform", "version", "status", "last_seen"}


def test_an_integration_says_only_that_it_is_wired():
    """`kind` and `configured`, and nothing to post with. The secret is absent
    rather than blanked: a field that is sometimes empty invites being filled."""
    assert _fields(sd.AccountIntegration) == {"kind", "configured"}


def test_the_audit_list_carries_no_body():
    """`Audit.body` is the free-form detail of an act -- where a changed
    setting's old and new values live. "What happened lately" and "what exactly
    changed" are different questions, and this payload only answers the first."""
    assert "body" not in _fields(sd.AccountAuditRow)
    assert "body" not in _fields(sd.PlatformAuditRow)


def test_a_platform_act_is_distinguishable_from_one_done_to_a_customer():
    """`audit.log`'s own docstring says a platform row must not borrow a
    customer's id. The console relies on being able to tell them apart, so the
    distinction is carried explicitly rather than inferred from a blank name."""
    assert "platform" in _fields(sd.PlatformAuditRow)
