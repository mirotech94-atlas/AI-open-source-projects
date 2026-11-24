import os

from atlas.modules.qa import words

BODY = ("Hi,\n\nI'm the owner, I run an agency called ATLAS Agency. Demo: https://x.vercel.app/\n\n"
        "$300 all in.\n\nOwner\n\nATLAS Agency · https://agency.example.com · 4087 Richmond Ave\n\n"
        "If you'd rather not hear from me, just reply stop.")


def test_outreach_may_be_signed_by_owner(monkeypatch):
    monkeypatch.setenv("AGENCY_POSTAL_ADDRESS", "4087 Richmond Ave")
    assert words.refusals(BODY, "outreach") == []


def test_email_still_refuses_owner_signature(monkeypatch):
    monkeypatch.setenv("AGENCY_POSTAL_ADDRESS", "4087 Richmond Ave")
    assert any("signed as the owner" in r for r in words.refusals(BODY, "email"))


def test_outreach_still_needs_opt_out(monkeypatch):
    monkeypatch.setenv("AGENCY_POSTAL_ADDRESS", "4087 Richmond Ave")
    bad = BODY.replace("just reply stop", "good luck")
    assert any("opt-out" in r for r in words.refusals(bad, "outreach"))


def test_owner_signed_body_passes_send_guard_shape():
    from atlas.modules.agency import outreach
    body = outreach.email_body({"name": "X"}, "https://x.vercel.app", {})
    assert outreach._owner_signed(body)
    assert not outreach._owner_signed("hi\n\nOwner\n\nthanks")


def test_vercel_project_name_is_capped_and_keeps_the_id():
    from atlas.modules.agency import vercel
    assert vercel.project_name("west-vibe-53") == "atlas-agency-west-vibe-53"
    n = vercel.project_name("the-cutting-edge-barbershop-70")
    assert len(n) <= 34 and n.endswith("-70") and n.startswith("atlas-agency-")
