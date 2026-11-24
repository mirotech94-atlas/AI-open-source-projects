"""the owner 2026-10-30: ATLAS Agency brand, Gmail SMTP for agency mail, Vercel only
for demos. No socket, no real mail: SMTP is faked and fails the test if it
is reached when it must not be."""
from __future__ import annotations

import sys

import pytest

from atlas.modules.agency import _store, demo_host, deploy, outreach, vercel

SENDER = "owner@example.com"


def _async_returns(value):
    async def fake(*_a, **_k):
        return value
    return fake


@pytest.fixture(autouse=True)
def _verified(monkeypatch):
    from atlas.modules.agency import verify
    monkeypatch.setattr(verify, "contact_refusal", _async_returns(None))
    monkeypatch.setattr(verify, "us_refusal", _async_returns(None))
    monkeypatch.setattr(outreach, "auto_hold", _async_returns(None))


def _no_smtp(monkeypatch):
    """Any SMTP connection attempt fails the test."""
    def boom(*_a, **_k):
        raise AssertionError("SMTP must not be reached")

    async def aboom(*_a, **_k):
        raise AssertionError("SMTP must not be reached")
    monkeypatch.setitem(sys.modules, "smtplib", type("smtplib", (), {"SMTP": boom}))
    monkeypatch.setitem(sys.modules, "aiosmtplib",
                        type("aiosmtplib", (), {"send": staticmethod(aboom)}))


def _fake_aiosmtplib(monkeypatch) -> dict:
    real = outreach.importlib.util.find_spec
    monkeypatch.setattr(outreach.importlib.util, "find_spec",
                        lambda n: object() if n == "aiosmtplib" else real(n))
    sent: dict = {}

    async def fake_send(message, *, hostname, port, username, password, start_tls):
        sent.update(message=message, hostname=hostname, port=port,
                    username=username, password=password, start_tls=start_tls)
    monkeypatch.setitem(sys.modules, "aiosmtplib",
                        type("aiosmtplib", (), {"send": staticmethod(fake_send)}))
    return sent


def _draft(body: str | None = None):
    return {"id": 1, "lead_id": 1, "kind": "email", "to_address": "owner@example.com",
            "subject": "Your free preview", "status": "draft", "followup_of": None,
            "body": body or ("This is ATLAS Agency.\n\n123 Main St, Springfield\n\n"
                             "Reply STOP to opt out.\n\n" + outreach.TRAILER)}


def _guard_env(monkeypatch, password: str = "app-pass-for-test"):
    monkeypatch.setattr(_store, "is_suppressed", _async_returns(False))
    monkeypatch.setattr(_store, "count_sent_between", _async_returns(0))
    monkeypatch.setattr(_store, "get_lead", _async_returns({"id": 1, "features": {}}))
    monkeypatch.setattr(_store, "lead_links", _async_returns({}))
    monkeypatch.setenv("AGENCY_POSTAL_ADDRESS", "123 Main St, Springfield")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", password)


# ------------------------------------------------------------------ sender

def test_agency_sender_is_fixed_and_ignores_old_env(monkeypatch):
    monkeypatch.setenv("AGENCY_SEND_FROM", "owner@old.example")
    monkeypatch.setenv("AGENCY_SMTP_USER", "relay@old.example")
    assert outreach.agency_sender() == SENDER
    assert outreach.BRAND == "ATLAS Agency"


async def test_missing_app_password_fails_closed_and_reaches_no_smtp(monkeypatch):
    for name, value in (("AGENCY_SMTP_HOST", "smtp.x.example"),
                        ("AGENCY_SMTP_PORT", "587"), ("AGENCY_SMTP_USER", "u@x.example"),
                        ("AGENCY_SMTP_PASSWORD", "pw"), ("RESEND_API_KEY", "re_x")):
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "")
    _no_smtp(monkeypatch)
    ok, reason = await outreach._gmail_send("owner@example.com", "s", "body")
    assert not ok and "GMAIL_APP_PASSWORD missing" in reason


async def test_send_outreach_with_no_password_sends_nothing(monkeypatch):
    _guard_env(monkeypatch, password="")
    monkeypatch.setattr(_store, "get_outreach", _async_returns(_draft()))
    marked = []
    monkeypatch.setattr(_store, "mark_outreach_sent",
                        lambda *a: marked.append(a) or _async_returns(None)())
    _no_smtp(monkeypatch)
    dry = await outreach.send_dry(1)
    assert not dry.ok and "GMAIL_APP_PASSWORD missing" in dry.detail
    result = await outreach.send_outreach(1)
    assert not result.ok and "GMAIL_APP_PASSWORD missing" in result.detail
    assert marked == []


async def test_gmail_send_uses_gmail_starttls_with_brand_and_reply_to(monkeypatch):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-pass-for-test")
    sent = _fake_aiosmtplib(monkeypatch)
    ok, reason = await outreach._gmail_send("owner@example.com", "Hi", "Body.")
    assert ok and reason is None
    assert (sent["hostname"], sent["port"], sent["username"], sent["start_tls"]) == (
        "smtp.gmail.com", 587, SENDER, True)
    message = sent["message"]
    assert message["From"] == f"ATLAS Agency <{SENDER}>"
    assert message["Reply-To"] == SENDER


async def test_send_outreach_goes_through_gmail(monkeypatch):
    _guard_env(monkeypatch)
    monkeypatch.setattr(_store, "get_outreach", _async_returns(_draft()))
    monkeypatch.setattr(_store, "mark_outreach_sent", _async_returns(None))
    sent = _fake_aiosmtplib(monkeypatch)
    result = await outreach.send_outreach(1)
    assert result.ok, result.detail
    assert sent["hostname"] == "smtp.gmail.com"
    assert result.evidence["message_id"].endswith("@gmail.com>")


async def test_an_old_mapfront_draft_is_refused(monkeypatch):
    _guard_env(monkeypatch)
    old = _draft("This is ATLAS Academy (Mapfront). https://x.old.example/\n\n"
                 "123 Main St, Springfield\n\nReply STOP to opt out.\n\n"
                 + outreach.TRAILER)
    monkeypatch.setattr(_store, "get_outreach", _async_returns(old))
    result = await outreach.send_dry(1)
    assert not result.ok and "mapfront" in result.detail.lower()


def test_email_body_is_atlas_agency_with_the_public_link(monkeypatch):
    monkeypatch.setenv("AGENCY_POSTAL_ADDRESS", "123 Main St, Springfield")
    monkeypatch.delenv("RECEPTION_ENABLED", raising=False)
    links = {"deployed": True, "domain": "demo-site.vercel.example", "slug": "joe"}
    body = outreach.email_body({"name": "Joe's", "website": None},
                               "https://demo-site.vercel.example/", links)
    assert body.startswith("Hi,") and "I'm the owner, I run an agency called ATLAS Agency." in body
    assert "doesn't have a website yet" in body and "$300" in body
    assert "https://agency.example.com" in body
    assert "mapfront" not in body.lower() and outreach.TRAILER not in body
    assert outreach.can_spam_ok(body, "123 Main St, Springfield") == []


# --------------------------------------------------------- Vercel-only demos

def test_preview_url_never_falls_back_to_mapfront():
    assert outreach.preview_url({"deployed": True, "slug": "joe"}) is None
    links = {"deployed": True, "domain": "demo-site.vercel.example", "slug": "joe"}
    assert outreach.preview_url(links) == "https://demo-site.vercel.example/"
    assert outreach.call_me_url({"deployed": True, "slug": "joe"}) is None


@pytest.mark.parametrize("host", ["", "local", "vercel", "user@box"])
async def test_box_deploys_refuse_on_every_setting(monkeypatch, host):
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", host)
    for call in (deploy.deploy_site, deploy.deploy_dry,
                 demo_host.deploy_demo, demo_host.deploy_demo_dry):
        result = await call("joe")
        assert not result.ok and "agency.publish_vercel" in result.detail


def test_a_box_deployed_site_gives_a_lead_no_link(monkeypatch):
    # The read-back may still check an old box deploy where it is; a lead is
    # only ever given the recorded (Vercel or own) domain.
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", "local")
    old = {"slug": "joe", "deployed_at": "x", "deployed": True}
    assert outreach.preview_url(old) is None
    vercel_links = {"slug": "joe", "domain": "a.vercel.app"}
    assert deploy.published_domain(vercel_links) == "a.vercel.app"
    assert demo_host.hostname_of({"slug": "joe"}) == "demo-site.vercel.example"


def test_sender_problem_refuses_18dn(monkeypatch):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-pass-for-test")
    assert outreach.sender_problem() is None
    monkeypatch.setattr(outreach, "AGENCY_SENDER", "hello@agency.example.com")
    assert "example.com" in outreach.sender_problem()


async def test_a_gmail_error_never_shows_the_app_password(monkeypatch):
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcdefghijklmnop")
    real = outreach.importlib.util.find_spec
    monkeypatch.setattr(outreach.importlib.util, "find_spec",
                        lambda n: object() if n == "aiosmtplib" else real(n))

    async def fail(message, **kw):
        raise RuntimeError(f"535 bad login {kw['password']}")
    monkeypatch.setitem(sys.modules, "aiosmtplib",
                        type("aiosmtplib", (), {"send": staticmethod(fail)}))
    ok, reason = await outreach._gmail_send("owner@example.com", "s", "b")
    assert not ok and "Gmail refused" in reason
    assert "abcdefghijklmnop" not in reason


async def test_without_aiosmtplib_stdlib_smtp_does_starttls_and_scrubs(monkeypatch):
    """aiosmtplib is not a declared dependency: the stdlib path is the live one."""
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "abcdefghijklmnop")
    real = outreach.importlib.util.find_spec
    monkeypatch.setattr(outreach.importlib.util, "find_spec",
                        lambda n: None if n == "aiosmtplib" else real(n))
    calls: list = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def starttls(self):
            calls.append(("starttls",))

        def login(self, user, password):
            calls.append(("login", user))

        def send_message(self, message):
            calls.append(("send", message["From"], message["Reply-To"]))

    monkeypatch.setitem(sys.modules, "smtplib", type("smtplib", (), {"SMTP": FakeSMTP}))
    ok, reason = await outreach._gmail_send("owner@example.com", "Hi", "Body.")
    assert ok and reason is None
    assert calls == [("connect", "smtp.gmail.com", 587), ("starttls",),
                     ("login", SENDER), ("send", f"ATLAS Agency <{SENDER}>", SENDER)]

    class Refusing(FakeSMTP):
        def login(self, user, password):
            raise RuntimeError(f"535 bad login {password}")

    monkeypatch.setitem(sys.modules, "smtplib", type("smtplib", (), {"SMTP": Refusing}))
    ok, reason = await outreach._gmail_send("owner@example.com", "Hi", "Body.")
    assert not ok and "nothing was sent" in reason
    assert "abcdefghijklmnop" not in reason


def test_vercel_project_is_atlas_agency():
    assert vercel.public_host("joe") == "demo-site.vercel.example"


def test_banner_texts_are_atlas_agency():
    from atlas.modules.agency import sitetemplate
    assert sitetemplate.PREVIEW_LINE == "Preview made by ATLAS Agency for"
    assert demo_host.BANNER == "Preview made by ATLAS Agency"


def test_variant_follows_whether_the_lead_has_its_own_site(monkeypatch):
    monkeypatch.setenv("AGENCY_POSTAL_ADDRESS", "123 Main St, Springfield")
    for web in (None, "", "https://www.instagram.com/x/", "http://x.booksy.com/a/"):
        lead = {"name": "Joe's", "website": web}
        assert not outreach.has_own_site(lead)
        assert outreach.email_subject(lead) == "A free website for Joe's"
        assert "doesn't have a website yet" in outreach.email_body(lead, "https://d/", {})
    lead = {"name": "Joe's", "website": "https://joes.com/"}
    body = outreach.email_body(lead, "https://d/", {})
    assert outreach.has_own_site(lead)
    assert outreach.email_subject(lead) == "A free redesign demo for Joe's"
    assert "the website for Joe's" in body and "under a week" in body
    assert "Just reply with what you'd want" in body and 'reply "yes"' not in body
    assert outreach.can_spam_ok(body, "123 Main St, Springfield") == []
