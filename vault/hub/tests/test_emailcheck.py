"""Is this address safe to trust?

The moment it serves: a letter arrives asking for a payment detail to be
changed, and somebody has thirty seconds to decide whether the sender is
who it claims to be.

The load-bearing rule is what the checker will NOT say. No DNS check can
establish that an address is safe, and a green tick on a well-configured
phishing domain is worse than having no tool at all — so the best verdict
available is "nothing wrong found".
"""
from unittest.mock import patch

from guardian_hub import emailcheck as ec


def _dns(records):
    """Patch DNS with a {(name, type): [values]} map. Anything unlisted
    answers empty, which is what a real missing record looks like."""
    return patch.object(ec, "_dig", lambda n, t: records.get((n.lower(), t), []))


GOOD = {
    ("acme.example", "MX"): ["10 mail.acme.example."],
    ("acme.example", "TXT"): ["v=spf1 include:_spf.google.com -all"],
    ("_dmarc.acme.example", "TXT"): ["v=DMARC1; p=reject; rua=mailto:a@acme.example"],
}


def test_a_domain_with_no_dns_at_all_is_called_forged(client):
    with _dns({}):
        v = ec.check("billing@nowhere.example")
    assert v.verdict == "forged"
    assert "does not exist" in v.headline
    assert any("forged" in f.text for f in v.findings)


def test_a_well_configured_domain_is_never_called_safe(client):
    """The rule the whole module is written around."""
    with _dns(GOOD):
        v = ec.check("billing@acme.example")
    assert v.verdict == "unproven"
    assert v.headline == "Nothing wrong found with acme.example."
    assert "safe" not in v.headline.lower()
    # And it says what it could not check, so the reader can weigh it.
    assert any("not the person" in l for l in v.limits)


def test_a_domain_anyone_can_forge_says_so_in_plain_words(client):
    no_auth = {("acme.example", "MX"): ["10 mail.acme.example."]}
    with _dns(no_auth):
        v = ec.check("billing@acme.example")
    assert v.verdict == "risky"
    assert any("Anyone can forge this address" in f.text for f in v.findings)
    assert any("neither SPF nor DMARC" in f.text for f in v.findings)


def test_dmarc_p_none_counts_as_forgeable(client):
    """p=none asks receivers to do nothing, which is not protection —
    guarder.io itself was in this state when this was written."""
    weak = dict(GOOD)
    weak[("_dmarc.acme.example", "TXT")] = ["v=DMARC1; p=none"]
    with _dns(weak):
        v = ec.check("billing@acme.example")
    assert v.verdict == "risky"
    assert any("p=none" in f.text for f in v.findings)


def test_a_throwaway_provider_is_named_even_when_its_dns_is_perfect(client):
    """mailinator.com enforces DMARC properly. The DNS is not the point."""
    d = {("mailinator.com", "MX"): ["10 mail.mailinator.com."],
         ("mailinator.com", "TXT"): ["v=spf1 -all"],
         ("_dmarc.mailinator.com", "TXT"): ["v=DMARC1; p=reject"]}
    with _dns(d):
        v = ec.check("a@mailinator.com")
    assert v.verdict == "risky"
    assert any("throwaway" in f.text for f in v.findings)


def test_a_lookalike_of_one_of_YOUR_domains_is_the_check_nobody_else_can_do(client):
    d = {("guarder.io.example", "MX"): ["10 x."]}
    d = {("guardor.io", "MX"): ["10 mail.guardor.io."],
         ("guardor.io", "TXT"): ["v=spf1 -all"],
         ("_dmarc.guardor.io", "TXT"): ["v=DMARC1; p=reject"]}
    with _dns(d):
        v = ec.check("billing@guardor.io", own_domains=["guarder.io"])
    assert v.verdict == "risky"
    assert any("resembles one of your own domains" in f.text for f in v.findings)


def test_a_supplier_is_not_flagged_just_for_being_a_different_company(client):
    """A looser lookalike rule turns every supplier into a warning, and a
    warning everybody dismisses is not a warning."""
    with _dns(GOOD):
        v = ec.check("billing@acme.example", own_domains=["guarder.io", "flycheap.live"])
    assert not any("resembles" in f.text for f in v.findings)


def test_a_free_consumer_mailbox_is_noted_not_condemned(client):
    d = {("gmail.com", "MX"): ["10 gmail-smtp-in.l.google.com."],
         ("gmail.com", "TXT"): ["v=spf1 redirect=_spf.google.com"],
         ("_dmarc.gmail.com", "TXT"): ["v=DMARC1; p=reject"]}
    with _dns(d):
        v = ec.check("someone@gmail.com")
    assert v.verdict == "unproven", "using gmail is not a finding"
    assert any(f.tone == "info" and "consumer mailbox" in f.text for f in v.findings)


def test_nonsense_input_is_refused_without_a_dns_lookup(client):
    with patch.object(ec, "_dig", lambda n, t: (_ for _ in ()).throw(AssertionError("no lookup"))):
        v = ec.check("not an address")
    assert v.verdict == "unknown"
    assert "does not look like" in v.headline


def test_the_address_never_leaves_the_hub(client):
    """The privacy promise, asserted rather than documented: the only thing
    this module may do to the outside world is a DNS lookup for the DOMAIN.
    A reputation provider is off until somebody decides otherwise."""
    seen = []
    with patch.object(ec, "_dig", lambda n, t: seen.append(n) or []):
        ec.check("verysecret.person@acme.example")
    assert seen, "it should have looked the domain up"
    assert not any("verysecret" in n for n in seen), "the local part must never be queried"


# --------------------------------------------------------------------------
# The two surfaces, sharing one checker
# --------------------------------------------------------------------------

def test_the_route_knows_which_domains_are_YOURS(client):
    """The one check a generic tool cannot make. It works because the route
    reads the tenant's own configured sites."""
    from tests.test_seed_helpers import seeded_demo
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    seeded_demo(client)
    db = SessionLocal()
    t = db.query(m.Tenant).filter_by(slug="demo").one()
    t.config = {**(t.config or {}), "sites": [{"name": "Portal", "url": "https://acme.example/"}]}
    db.commit(); db.close()

    d = {("acrne.example", "MX"): ["10 mail.acrne.example."],
         ("acrne.example", "TXT"): ["v=spf1 -all"],
         ("_dmarc.acrne.example", "TXT"): ["v=DMARC1; p=reject"]}
    with _dns(d):
        out = client.get("/api/v1/security/email-check?address=billing@acrne.example").json()
    assert out["verdict"] == "risky"
    assert any("resembles one of your own domains" in f["text"] for f in out["findings"])


def test_the_assistant_never_rounds_nothing_wrong_up_to_safe(client):
    """A green tick on a well-run phishing domain is worse than no tool."""
    import guardian_hub.main  # noqa: F401  (resolves the chat import cycle)
    from guardian_hub.chat import registry
    from guardian_hub.db import SessionLocal
    from guardian_hub import models as m
    from tests.test_seed_helpers import seeded_demo
    seeded_demo(client)
    db = SessionLocal()
    ctx = registry.ToolContext(
        db=db, tenant=db.query(m.Tenant).filter_by(slug="demo").one(),
        user=db.query(m.User).filter_by(username="demoadmin").one(),
        role="admin", request_ip="127.0.0.1")
    with _dns(GOOD):
        res = registry.dispatch(ctx, "check_email", {"address": "billing@acme.example"})
    db.close()
    assert res.ok
    assert "Nothing wrong found" in res.text
    assert "not the same as safe" in res.text


def test_the_rn_that_looks_like_an_m_is_caught(client):
    """The commonest domain-spoofing trick there is, and the one an
    edit-distance rule alone never catches: acrne/acme is distance 2 and
    identical at a glance."""
    for fake in ("acrne.example", "vvidget.example"):
        d = {(fake, "MX"): ["10 x."], (fake, "TXT"): ["v=spf1 -all"],
             ("_dmarc." + fake, "TXT"): ["v=DMARC1; p=reject"]}
        with _dns(d):
            v = ec.check(f"billing@{fake}", own_domains=["acme.example", "widget.example"])
        assert v.verdict == "risky", fake
        assert any("resembles one of your own domains" in f.text for f in v.findings), fake


def test_folding_confusables_does_not_make_everything_a_lookalike(client):
    with _dns(GOOD):
        v = ec.check("billing@acme.example", own_domains=["northwind.example"])
    assert not any("resembles" in f.text for f in v.findings)
