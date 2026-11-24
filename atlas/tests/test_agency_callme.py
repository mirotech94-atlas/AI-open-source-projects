""""Have Mapfront call me": written consent from the lead, then the call itself.

The form is on preview sites only; the endpoint records a `web_form` consent
row only with the box ticked, a real US number and the page's own origin;
suppression wins; and the only thing that follows is `agency.call_lead`
through the kernel (`lead_followup.call_due`, Tier 1 since 2026-10-06), past
the consent guard -- a refusal is the Tier 2 card. The route itself never
touches the executor. Fake 555 numbers throughout.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from atlas.api import call_me
from atlas.api.peers import PerAddressLimiter
from atlas.kernel import lead_followup
from atlas.kernel.contracts import ActionResult
from atlas.modules.agency import _store, callme, consent, sitetemplate
from atlas.modules.agency import client as client_mod
from atlas.modules.agency.client import client_data

SLUG = "joes-plumbing-7"
ORIGIN = "https://agency.example.com"
PATH = f"/public/call-me/{SLUG}"
PAGE = f"{ORIGIN}/preview/{SLUG}/"
OUTSIDER = "203.0.113.9"
LEAD = {"id": 7, "name": "Joe's Plumbing", "category": "plumber",
        "phone": "(718) 555-0101", "address": "1 Main St, Queens, NY 11101",
        "website": None, "status": "audited"}
SITE = {"id": 3, "slug": SLUG, "lead_id": 7, "domain": None,
        "deployed_at": "2026-09-30T12:00:00Z", "path": "/x"}
GOOD = {"name": "Joe Russo", "phone": "(718) 555-0199", "consent": True,
        "version": callme.CURRENT_VERSION, "page": PAGE, "website": ""}


def _returns(value):
    async def fake(*_a, **_kw):
        return value
    return fake


# ------------------------------------------------------------------ pure

@pytest.mark.parametrize(("raw", "e164"), [
    ("(718) 555-0199", "+17185550199"), ("+1 718 555 0199", "+17185550199"),
    ("17185550199", "+17185550199"),
    ("(018) 555-0199", None), ("(555) 010-0100", None), ("(911) 555-0199", None),
    ("555-0199", None), ("+44 20 7123 4567", None), ("", None), (None, None),
    ("7" * 50, None)])
def test_only_a_real_us_number_is_called(raw, e164):
    assert callme.us_phone(raw) == e164


def test_the_consent_is_the_forms_words_and_calls_only():
    grants = consent.consent_from_web_form("+17185550199", callme.CONSENT_TEXT)
    assert [(g.phone, g.scope, g.quoted) for g in grants] == [
        ("+17185550199", "call", callme.CONSENT_TEXT)]
    assert consent.consent_from_web_form("12", callme.CONSENT_TEXT) == []
    assert "AI assistant" in callme.CONSENT_TEXT
    assert "isn't required to buy" in callme.CONSENT_TEXT
    assert "STOP" in callme.CONSENT_TEXT and "don't call" in callme.CONSENT_TEXT


def test_the_ip_is_stored_keyed(monkeypatch):
    monkeypatch.setenv(callme.KEY_ENV, "k1")
    one, keyed = callme.ip_hash("203.0.113.9")
    assert keyed and "203.0.113.9" not in one and len(one) == 64
    monkeypatch.setenv(callme.KEY_ENV, "k2")
    assert callme.ip_hash("203.0.113.9")[0] != one


def _page(raw: dict | None = None) -> str:
    from pathlib import Path

    from atlas.modules.agency import sitekit
    client = sitetemplate.themed(client_data(LEAD, raw, Path("/nonexistent")), "trades")
    copy, _ = sitetemplate.parse_copy("", client, "trades")
    return sitetemplate.render_home(client, copy, "trades", sitekit.faq_list(client, []))


@pytest.fixture(autouse=True)
def _public_door(monkeypatch):
    # These tests cover the widget itself, which ships only behind a public door.
    monkeypatch.setattr(client_mod, "PUBLIC_DOOR", True)


def test_without_a_public_door_a_preview_carries_no_call_me(monkeypatch):
    # ATLAS is local-only (2026-10-29): the form would post to a 404.
    monkeypatch.setattr(client_mod, "PUBLIC_DOOR", False)
    assert 'data-atlas="callme"' not in _page()


def test_the_form_is_on_a_preview_home_page_only():
    preview = _page()
    assert f'<div id="{callme.ANCHOR}" data-atlas="callme"></div>' in preview
    assert f'<script src="{callme.WIDGET}" defer data-atlas="callme"></script>' in preview
    # No static <form> of Mapfront's: the launch check holds every static
    # form to the client's own form service. The only one on a preview is
    # the booking request, which cannot send.
    forms = preview.split("<form")[1:]
    assert all('method="dialog"' in f.split(">")[0] for f in forms)
    sold = _page({"package": "100"})
    assert 'data-atlas="callme"' not in sold


def test_the_widget_carries_the_endpoint_the_text_and_mapfronts_line(monkeypatch):
    monkeypatch.setenv("RECEPTION_ENABLED", "1")
    monkeypatch.setenv("TWILIO_FROM", "+17185550000")
    from pathlib import Path
    client = sitetemplate.themed(client_data(LEAD, None, Path("/nonexistent")), "trades")
    js = callme.widget_js(SLUG, client)
    assert f'"endpoint": "https://agency.example.com/public/call-me/{SLUG}"' in js
    assert callme.CONSENT_TEXT[:60] in js
    assert callme.CURRENT_VERSION in js and "(718) 555-0000" in js
    assert "innerHTML" not in js and "localStorage" not in js
    assert "document.cookie" not in js and 'credentials: "omit"' in js
    assert 'type: "checkbox"' in js and "checked" not in js.split("okLabel")[0]


async def test_a_built_preview_ships_the_widget_and_passes_the_launch_check(
        monkeypatch, tmp_path):
    from atlas.modules.agency import build
    from atlas.modules.agency.launchcheck import launch_audit
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "sites")
    monkeypatch.setattr(build, "UPLOADS_DIR", tmp_path / "uploads")
    (tmp_path / "uploads" / "7").mkdir(parents=True)
    mock = tmp_path / "mockups" / "7.html"
    mock.parent.mkdir(parents=True)
    mock.write_text("<!doctype html><html></html>")
    mock.with_suffix(".copy.json").write_text(json.dumps({"family": "trades",
                                                          "copy": {}}))
    monkeypatch.setattr(_store, "get_lead", _returns(LEAD))
    monkeypatch.setattr(_store, "latest_mockup_path", _returns(str(mock)))
    monkeypatch.setattr(_store, "get_site", _returns(None))
    monkeypatch.setattr(_store, "insert_site", _returns(3))
    monkeypatch.setattr(_store, "set_lead_status", _returns(None))
    result = await build.build_site(7)
    assert result.ok, result.detail
    site = tmp_path / "sites" / build.slugify(LEAD["name"], 7)
    assert (site / callme.WIDGET).is_file()
    assert "/public/call-me/" in (site / callme.WIDGET).read_text()
    privacy = (site / "privacy.html").read_text()
    assert 'data-atlas="callme-privacy"' in privacy and "not to Joe" in privacy
    report = launch_audit(site)
    assert report.ready, report.summary()


# ------------------------------------------------------------- the route

class World:
    def __init__(self, monkeypatch, *, suppressed=False, guard_ok=True, site=SITE):
        self.consents: list[dict] = []
        self.messages: list[tuple] = []
        self.cards: list[tuple] = []
        self.alerts: list = []
        self.marked: list = []
        self.ran: list = []
        monkeypatch.setattr(_store, "get_site", _returns(dict(site) if site else None))
        monkeypatch.setattr(_store, "get_lead", _returns(dict(LEAD)))
        monkeypatch.setattr(_store, "is_suppressed", _returns(suppressed))
        monkeypatch.setattr(_store, "active_consent", _returns(None))

        async def insert_consent(lead_id, phone, scope, channel, source_id, quoted,
                                 evidence=None):
            self.consents.append({"id": 41, "lead_id": lead_id, "phone": phone,
                                  "scope": scope, "source_channel": channel,
                                  "source_id": source_id, "quoted": quoted,
                                  "evidence": evidence, "revoked_at": None,
                                  "call_card_at": None})
            return 41
        monkeypatch.setattr(_store, "insert_consent", insert_consent)

        async def insert_lead_message(*args):
            self.messages.append(args)
            return 9
        monkeypatch.setattr(_store, "insert_lead_message", insert_lead_message)

        async def get_consent(consent_id):
            return self.consents[-1] if self.consents else None
        monkeypatch.setattr(_store, "get_consent", get_consent)

        async def mark(consent_id, note):
            self.marked.append(note)
            return True
        monkeypatch.setattr(_store, "mark_call_card", mark)
        monkeypatch.setattr(_store, "note_call_card", _returns(None))

        async def guard(lead_id, channel, *, now=None):
            if guard_ok:
                return SimpleNamespace(lead=LEAD, phone="+17185550199",
                                       opening="Hi, it's the owner's assistant..."), None
            return None, "calling leads is off: AGENCY_CALLS_ENABLED is not set"
        monkeypatch.setattr(consent, "contact_guard", guard)

        async def propose(action, params, reason, **kw):
            self.cards.append((action, params, reason, kw))
            return 77
        monkeypatch.setattr("atlas.kernel.approvals.propose", propose)

        async def execute(request):
            # Reached only through kernel.lead_followup.call_due, never by
            # the route (tests/test_api.py holds atlas/api to that).
            self.ran.append((request.action, request.params, request.approved_by))
            return ActionResult(True, "calling lead 7 at +17185550199",
                                evidence={"contact_id": 5, "to": "+17185550199"})
        monkeypatch.setattr("atlas.kernel.executor.execute", execute)

        async def notify(out):
            self.alerts.append(out)
        monkeypatch.setattr("atlas.kernel.comms.notify", notify)


@pytest.fixture
def app(monkeypatch, memory_backend):
    monkeypatch.setenv("MESH_ALLOWED_CIDRS", "100.64.0.0/10")
    monkeypatch.setenv(callme.KEY_ENV, "test-key")
    monkeypatch.setattr(call_me, "_per_ip", PerAddressLimiter(callme.PER_IP, 600.0))
    monkeypatch.setattr(call_me, "_per_site", PerAddressLimiter(callme.PER_SITE, 86400.0))
    from atlas.api.main import app as the_app

    async def no_boot() -> None:
        return None
    monkeypatch.setattr("atlas.api.main.boot", no_boot)
    monkeypatch.setattr("atlas.api.main.scheduler.loop", no_boot)
    return the_app


def _post(app, body=None, origin=ORIGIN, host=OUTSIDER, path=PATH):
    headers = {"Origin": origin, "User-Agent": "TestBrowser/1.0"} if origin else {}
    with TestClient(app, client=(host, 4000)) as c:
        return c.post(path, json=GOOD if body is None else body, headers=headers)


def test_a_ticked_box_records_the_consent_and_the_kernel_places_the_call(
        app, monkeypatch):
    """2026-10-06 (the owner: "call them as well"): the consent is the gate, and
    once it passes the call goes through the executor with nobody attached;
    no card, a receipt."""
    world = World(monkeypatch)
    r = _post(app)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert r.headers["access-control-allow-origin"] == ORIGIN
    [row] = world.consents
    assert row["source_channel"] == "web_form" and row["scope"] == "call"
    assert row["phone"] == "+17185550199" and row["quoted"] == callme.CONSENT_TEXT
    ev = row["evidence"]
    assert ev["page_url"] == PAGE and ev["user_agent"] == "TestBrowser/1.0"
    assert ev["consent_text"] == callme.CONSENT_TEXT and ev["ip_hash_keyed"]
    assert OUTSIDER not in json.dumps(ev) and ev["name"] == "Joe Russo"
    assert ev["matches_listed_phone"] is False
    assert world.messages and world.messages[0][1] == "web_form"
    assert world.ran == [("agency.call_lead", {"lead_id": 7}, None)]
    assert world.cards == []
    assert world.marked == ["calling"]
    titles = [a.title for a in world.alerts]
    assert any(t.startswith("Calling Joe's Plumbing at +17185550199") for t in titles)
    assert any("asked Mapfront to call" in t for t in titles)
    [alert] = [a for a in world.alerts if "asked Mapfront" in a.title]
    assert "calling Joe's Plumbing at +17185550199 (contact #5)" in alert.body


@pytest.mark.parametrize(("change", "field"), [
    ({"consent": False}, "consent"), ({"consent": "true"}, "consent"),
    ({"consent": None}, "consent"), ({"phone": "555-0199"}, "phone"),
    ({"phone": "(011) 555-0199"}, "phone"), ({"name": ""}, "name"),
    ({"name": "x" * 81}, "name"), ({"page": "https://evil.example/"}, "page")])
def test_nothing_is_recorded_without_the_box_a_real_number_or_a_name(
        app, monkeypatch, change, field):
    world = World(monkeypatch)
    body = {**GOOD, **change}
    r = _post(app, body)
    assert r.status_code == 422 and r.json()["field"] == field
    assert world.consents == [] and world.cards == []


def test_a_missing_box_is_not_consent(app, monkeypatch):
    world = World(monkeypatch)
    body = {k: v for k, v in GOOD.items() if k != "consent"}
    assert _post(app, body).status_code == 422
    assert world.consents == []


def test_a_stale_consent_text_is_refused(app, monkeypatch):
    world = World(monkeypatch)
    assert _post(app, {**GOOD, "version": "abc"}).status_code == 409
    assert world.consents == []


def test_a_suppressed_number_gets_the_same_answer_and_nothing_else(app, monkeypatch):
    world = World(monkeypatch, suppressed=True)
    r = _post(app)
    assert r.status_code == 200 and r.json()["message"] == callme.DONE_MESSAGE
    assert world.consents == [] and world.cards == [] and world.alerts == []


def test_the_honeypot_records_nothing(app, monkeypatch):
    world = World(monkeypatch)
    r = _post(app, {**GOOD, "website": "http://spam.example"})
    assert r.status_code == 200
    assert world.consents == [] and world.cards == []


def test_blocked_by_the_guard_means_no_call_and_owner_is_told_why(app, monkeypatch):
    world = World(monkeypatch, guard_ok=False)
    assert _post(app).status_code == 200
    assert len(world.consents) == 1 and world.cards == [] and world.ran == []
    assert "AGENCY_CALLS_ENABLED" in world.alerts[0].body
    assert world.marked and world.marked[0].startswith("no call")


def test_rate_limited_per_address_and_per_site(app, monkeypatch):
    World(monkeypatch)
    codes = [_post(app).status_code for _ in range(callme.PER_IP + 1)]
    assert codes[-1] == 429 and set(codes[:-1]) == {200}
    monkeypatch.setattr(call_me, "_per_ip", PerAddressLimiter(999, 600.0))
    monkeypatch.setattr(call_me, "_per_site", PerAddressLimiter(2, 86400.0))
    codes = [_post(app, host=f"203.0.113.{i}").status_code for i in range(20, 23)]
    assert codes == [200, 200, 429]


@pytest.mark.parametrize("origin", [
    "https://evil.example", "http://agency.example.com",
    "https://joes-plumbing-7.agency.example.com", "https://evilagency.example.com", None])
def test_any_other_origin_is_refused(app, monkeypatch, origin):
    world = World(monkeypatch)
    assert _post(app, origin=origin).status_code == 403
    assert world.consents == []


def test_a_sandboxed_preview_sends_origin_null_and_is_taken_for_a_deployed_slug(
        app, monkeypatch):
    """The preview is served with a CSP sandbox lacking allow-same-origin, so
    its fetches carry `Origin: null`; the slug in the URL path is the check."""
    world = World(monkeypatch)
    r = _post(app, origin="null")
    assert r.status_code == 200, r.text
    assert r.headers["access-control-allow-origin"] == "null"
    assert len(world.consents) == 1
    with TestClient(app, client=(OUTSIDER, 4000)) as c:
        pre = c.options(PATH, headers={"Origin": "null",
                                       "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == "null"
    World(monkeypatch, site={**SITE, "deployed_at": None})
    assert _post(app, origin="null").status_code == 404


@pytest.mark.parametrize("page", [
    f"{ORIGIN}/", f"{ORIGIN}/preview/other-3/", f"{ORIGIN}/preview/", f"{ORIGIN}/x/{SLUG}/",
    f"{ORIGIN}/preview/{SLUG}x/", f"http://agency.example.com/preview/{SLUG}/"])
def test_a_page_that_is_not_this_sites_preview_is_refused(app, monkeypatch, page):
    world = World(monkeypatch)
    assert _post(app, body={**GOOD, "page": page}).status_code == 422
    assert world.consents == []


def test_a_nested_page_of_this_preview_is_accepted(app, monkeypatch):
    world = World(monkeypatch)
    assert _post(app, body={**GOOD, "page": f"{PAGE}contact.html?x=1"}).status_code == 200
    assert len(world.consents) == 1


def test_an_undeployed_or_unknown_site_is_404(app, monkeypatch):
    world = World(monkeypatch, site={**SITE, "deployed_at": None})
    assert _post(app).status_code == 404
    World(monkeypatch, site=None)
    assert _post(app).status_code == 404
    assert world.consents == []


@pytest.mark.parametrize(("path", "public"), [
    (PATH, True), ("/public/call-me/a", True), ("/public/call-me/../api/x", False),
    (f"{PATH}/", False), (f"{PATH}/x", False), ("/public/call-me/", False),
    ("/public/call-me/Joe", False), ("/public/call-meX/a", False)])
def test_the_allowlist_is_one_label_exactly(path, public):
    from atlas.api import main
    assert main._is_public(path) is public


def test_the_caddyfile_proxies_the_form():
    from pathlib import Path
    caddy = (Path(__file__).resolve().parent.parent / "Caddyfile").read_text()
    block = caddy[caddy.index("handle /public/call-me/*"):]
    block = block[:block.index("}\n\t}")]
    assert "reverse_proxy api:8080" in block and "X-Forwarded-For {remote_host}" in block
    assert "-X-Atlas-Device" in block


async def test_outside_calling_hours_the_call_waits_for_a_tick(monkeypatch):
    """The consent is left unclaimed, so the first tick inside hours places it."""
    world = World(monkeypatch)
    world.consents.append({"id": 41, "lead_id": 7, "scope": "call", "revoked_at": None,
                           "call_card_at": None, "source_channel": "web_form",
                           "evidence": {}, "quoted": callme.CONSENT_TEXT})

    async def guard(lead_id, channel, *, now=None):
        if now is None:
            return None, "it is 22:10 for the lead; calls only 08:00-21:00"
        return SimpleNamespace(lead=LEAD, phone="+17185550199", opening="Hi"), None
    monkeypatch.setattr(consent, "contact_guard", guard)
    said = await lead_followup.call_due(41)
    assert "first tick inside calling hours" in said
    assert world.cards == [] and world.marked == [] and world.ran == []


async def test_a_lead_who_asked_is_top_of_the_brief(monkeypatch, tmp_path):
    """A lead that submitted the form (or rang the line) is the priority, and
    the brief gives Mapfront's line for the owner to hand out."""
    from datetime import UTC, datetime

    from atlas.modules.agency import calls, pricing
    from atlas.modules.agency.audit import Audit
    monkeypatch.setenv("AGENCY_BRIEFS_DIR", str(tmp_path))
    monkeypatch.setenv("RECEPTION_ENABLED", "1")
    monkeypatch.setenv("TWILIO_FROM", "+17185550000")
    monkeypatch.setattr(_store, "get_lead", _returns(LEAD))
    monkeypatch.setattr(_store, "lead_links", _returns(
        {"domain": None, "slug": SLUG, "deployed": True, "webild_url": None,
         "mockup": None}))
    monkeypatch.setattr(_store, "lead_returning", _returns(False))
    monkeypatch.setattr(calls, "audit_lead", _returns(Audit(None, [], {}, 0)))
    monkeypatch.setattr(_store, "consent_for_lead", _returns(
        {"phone": "+17185550199", "source_channel": "web_form",
         "granted_at": datetime(2026, 9, 30, 14, 2, tzinfo=UTC)}))
    text = await calls.brief_text(7)
    assert "Priority: they asked to be called" in text and "+17185550199" in text
    assert "Have Mapfront call me" in text and "2026-09-30 14:02 UTC" in text
    assert "agency.call_lead" in text and "no consent on record" not in text
    assert "ATLAS Agency's line (the AI assistant answers, any time): (718) 555-0000" in text
    assert set(pricing.find_prices(text)) <= {500, 300, 100}


def test_the_mount_sits_once_in_main_after_the_marketing_sections():
    """The marketing pass (2026-09-30) added sections around #contact; the
    call-me mount is still exactly one, inside <main>, after the contact
    section and before the closing band the footer follows."""
    preview = _page()
    assert preview.count('data-atlas="callme"') == 2          # the mount and its script
    main = preview.split('<main id="main">')[1].split("</main>")[0]
    assert f'<div id="{callme.ANCHOR}" data-atlas="callme"></div>' in main
    assert main.index('id="contact"') < main.index('data-atlas="callme"')
    assert main.index('id="closing"') < main.index('data-atlas="callme"')
    assert '<script src="mapfront-callme.js" defer data-atlas="callme">' in main
