"""The compliance pack's value is set by what it refuses to claim.

Vault holds continuous evidence for a real slice of a cyber-insurance
form or a Cyber Essentials assessment, and the people filling those in are
mostly guessing. That is the commercial argument. It survives exactly as
long as the document does, and an MSP caught over-claiming to an
underwriter never opens it again.

So most of this file is about the refusals rather than the answers. The
load-bearing ones:

- `test_an_outside_scope_control_has_no_predicate_at_all` — there must be
  no code path by which Vault reports a pass for something it cannot
  observe. Not a predicate returning False; no predicate.
- `test_mfa_is_outside_scope` — the spec's named example, and the single
  question most likely to move an insurance premium.
- `test_an_evidenced_control_with_nothing_to_measure_is_unmeasured` — a
  tenant with no servers has not passed the patching control and has not
  failed it either.
- `test_every_framework_keeps_its_gaps` — a pack that silently drops the
  questions it cannot answer looks complete, which is the over-claim in its
  most dangerous form.
"""
import pytest

from guardian_hub import controls
from guardian_hub.controls import Scope

EVIDENCED = [c for c in controls.CONTROLS if c.scope == Scope.EVIDENCED]
NOT_EVIDENCED = [c for c in controls.CONTROLS if c.scope != Scope.EVIDENCED]


# --------------------------------------------------------------------------
# The refusals
# --------------------------------------------------------------------------

@pytest.mark.parametrize("control", NOT_EVIDENCED, ids=lambda c: c.key)
def test_an_outside_scope_control_has_no_predicate_at_all(control):
    """Not a predicate that returns False — none. A control Vault cannot
    observe must be unable to report a pass even by accident."""
    assert control.evidence is None


@pytest.mark.parametrize("control", controls.CONTROLS, ids=lambda c: c.key)
def test_every_control_says_what_guardian_holds_or_does_not(control):
    """A limitation nobody wrote down becomes one nobody mentions to the
    underwriter."""
    assert control.note.strip(), control.key
    assert len(control.note) > 30, f"{control.key}: the note has to be specific"


@pytest.mark.parametrize("control", controls.CONTROLS, ids=lambda c: c.key)
def test_every_control_reads_as_a_question_someone_is_asked(control):
    """The row has to be recognisable to the person copying an answer off
    it, which means the questionnaire's phrasing, not ours."""
    assert control.question.endswith("?"), control.key


def test_mfa_is_outside_scope():
    """The spec's own example of what a network sensor cannot see, and the
    question most likely to move an insurance premium. Vault evidences
    key-only SSH; that is not MFA and must never be offered as it."""
    mfa = controls.BY_KEY["mfa_enforcement"]
    assert mfa.scope == Scope.OUTSIDE
    assert mfa.evidence is None
    assert "MFA" in mfa.note or "multi-factor" in mfa.note.lower()

    ssh = controls.BY_KEY["remote_access_auth"]
    assert ssh.scope == Scope.EVIDENCED
    assert "MFA" in ssh.note, "the evidenced row must disclaim the thing it resembles"


def test_a_vulnerability_scan_is_not_offered_as_a_penetration_test():
    pen = controls.BY_KEY["penetration_testing"]
    assert pen.scope == Scope.OUTSIDE and pen.evidence is None
    assert "not a penetration test" in pen.note


def test_end_of_life_claims_the_observation_and_dates_the_reference_data():
    """Vault's view of the version is strong; the date it is compared
    against is a hand-maintained table. That asymmetry is why the row is
    partial, and the note has to name both limits."""
    c = controls.BY_KEY["asset_lifecycle"]
    assert c.scope == Scope.PARTIAL
    assert "unknown rather than supported" in c.note
    assert "hand" in c.note


def test_backup_verification_claims_the_half_guardian_can_see_and_no_more():
    """Vault now checks that backup jobs produce plausible artifacts, so
    this is no longer outside scope. It still cannot test a restore, and a
    backup that cannot be restored looks identical from outside — so the
    row must stay partial and say which half is missing."""
    c = controls.BY_KEY["backup_verification"]
    assert c.scope == Scope.PARTIAL
    assert c.evidence is None, "partial rows are not answered from a predicate"
    assert "does NOT test restores" in c.note or "not test restores" in c.note.lower()


def test_the_disclaimer_says_guardian_makes_nobody_compliant():
    assert "does not make" in controls.DISCLAIMER
    assert "compliant" in controls.DISCLAIMER


# --------------------------------------------------------------------------
# The mapping
# --------------------------------------------------------------------------

def test_every_mapping_points_at_a_real_control_and_framework():
    keys = {c.key for c in controls.CONTROLS}
    for key, framework, ref in controls.MAPPINGS:
        assert key in keys, key
        assert framework in controls.FRAMEWORKS, framework
        assert ref.strip(), f"{key}/{framework} needs a reference an assessor can look up"


def test_every_control_is_claimed_by_at_least_one_framework():
    """An unmapped control is one nobody will ever be shown, which makes
    writing it pointless and leaving it misleading."""
    mapped = {k for k, _fw, _ref in controls.MAPPINGS}
    assert {c.key for c in controls.CONTROLS} - mapped == set()


def test_every_framework_names_its_edition():
    """"Requirement 6.1" means different things in PCI DSS v3.2.1 and v4.0.
    Without the edition a citation cannot be checked, only trusted."""
    for key, name in controls.FRAMEWORKS.items():
        assert any(ch.isdigit() for ch in name) or "(" in name, key


@pytest.mark.parametrize("framework", sorted(controls.FRAMEWORKS))
def test_every_framework_keeps_its_gaps(framework):
    """A pack that lists only the questions it can answer looks complete.
    The assessor's list has those rows on it either way."""
    rows = controls.for_framework(framework)
    assert rows, framework
    assert any(c.scope == Scope.OUTSIDE for c, _ref in rows), (
        f"{framework}: no gaps listed — that is over-claiming by omission"
    )
    assert any(c.scope == Scope.EVIDENCED for c, _ref in rows), framework


@pytest.mark.parametrize("framework", sorted(controls.FRAMEWORKS))
def test_a_framework_lists_each_control_once(framework):
    rows = controls.for_framework(framework)
    keys = [c.key for c, _ref in rows]
    assert len(keys) == len(set(keys)), framework


def test_cyber_essentials_covers_its_five_technical_controls():
    """The whole framework is five named controls; missing one is a hole a
    reader will notice immediately."""
    refs = {ref for c, ref in controls.for_framework("cyber_essentials")}
    for expected in ("Firewalls", "Secure configuration", "Security update management",
                     "User access control", "Malware protection"):
        assert any(r.startswith(expected) for r in refs), expected


# --------------------------------------------------------------------------
# Answering against a scan
# --------------------------------------------------------------------------

FULL = {
    "ts": "2026-09-20T12:00:00+00:00",
    "ssh": [{"name": "vps1", "reachable": True, "fail2ban": "active", "cve_fixable": 0,
             "sec": {"ssh_password": False, "ufw": "active", "unattended": True,
                     "security_updates": [], "miners": []}}],
    "sites": [{"name": "shop", "cert_days_left": 60,
               "sec": {"old_tls": None, "https_redirect": True}}],
    "dns_filter": {"running": True},
    "home": {"unverified": [], "exposure": {"open_ports": []},
             "wifi": {"security": "WPA3"}, "devices": {"aa": {}}},
    "email_sec": {"example.com": {"has_mx": True, "spf": True, "dmarc": True}},
    "admin_probe": {"shop": {"hits": []}},
}


def test_a_healthy_scan_answers_the_evidenced_controls():
    answered = [c.key for c in EVIDENCED
                if controls.answer(c, FULL) == Scope.EVIDENCED]
    assert len(answered) >= 15, answered


def test_an_evidenced_control_with_nothing_to_measure_is_unmeasured():
    """A tenant with no servers has not passed the patching control and has
    not failed it. Scoring absence as either is the mirror of over-claiming."""
    empty = {"ts": "2026-09-20T12:00:00+00:00"}
    assert controls.answer(controls.BY_KEY["patch_cadence"], empty) == "unmeasured"
    assert controls.answer(controls.BY_KEY["host_firewall"], empty) == "unmeasured"


def test_answering_never_upgrades_a_controls_scope():
    """However good the scan, a partial stays partial and an outside stays
    outside. The data cannot talk Vault into a stronger claim."""
    for control in NOT_EVIDENCED:
        assert controls.answer(control, FULL) == control.scope


def test_a_predicate_that_raises_reports_unmeasured_not_a_pass():
    """Malformed scan data must degrade to "we could not tell", never to a
    green row."""
    exploding = controls.Control(
        key="x", title="x", question="x?", scope=Scope.EVIDENCED, note="n" * 40,
        evidence=lambda last: 1 / 0,
    )
    assert controls.answer(exploding, FULL) == "unmeasured"
