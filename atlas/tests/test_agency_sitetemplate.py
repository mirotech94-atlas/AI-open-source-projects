"""The designed client-site templates, the page gate, and the call brief
(2026-09-30).

The eco-electrical-services-llc-38 preview had, as its body
copy, a model's account of the GetLayers output it had been handed. These
tests hold the three places that now stop that -- the copy validation, the
page gate in the builder, and launch item 22 that `agency.deploy_site`
obeys -- and check each trade family's template builds into a site the
launch check passes.
"""
from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path

import pytest

from atlas.modules.agency import (
    _store,
    build,
    calls,
    deploy,
    launchcheck,
    pagegate,
    sitekit,
    sitetemplate,
)
from atlas.modules.agency import client as client_mod
from atlas.modules.agency.client import ClientData, client_data
from atlas.modules.agency.launch import finish_site
from atlas.modules.agency.launchcheck import CONTENT_NUMBER, Status, launch_audit


@pytest.fixture(autouse=True)
def _box_deploy_path(monkeypatch):
    """The box deploy path is kept for rollback and tested here; since
    2026-10-30 it refuses live (Vercel only, test_agency_gmail_brand.py)."""
    from atlas.modules.agency import deploy as _deploy
    monkeypatch.setattr(_deploy, "VERCEL_ONLY", False)

# Word for word what the public preview said on 2026-09-30.
ECO_PARAGRAPH = (
    "The data contains a JSON object with information about a business called Eco "
    "Electrical Services, LLC ... The majority of the data consists of GetLayers search "
    "results ... the final section of the data appears to be an error message from a "
    "tool called GetLayers, indicating an input validation issue.")

LEADS = {
    "trades": {"id": 10, "name": "NYC Quality Plumbing", "category": "plumber",
               "phone": "(718) 555-0199", "address": "100 Example Ave, New York, NY 10001",
               "website": None, "status": "audited"},
    "beauty": {"id": 11, "name": "Court Street Salon", "category": "hair_salon",
               "phone": "(718) 555-0142", "address": "214 Court St, Brooklyn, NY 11201",
               "website": None, "status": "audited"},
    "food": {"id": 12, "name": "Orchard Corner Kitchen", "category": "restaurant",
             "phone": "(646) 555-0187", "address": "57 Orchard St, New York, NY 10002",
             "website": None, "status": "audited"},
}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("POSTHOG_PUBLIC_KEY", "POSTHOG_HOST", "AGENCY_FORM_ACTION"):
        monkeypatch.delenv(name, raising=False)


def _client(lead: dict, raw: dict | None = None, tmp: Path | None = None) -> ClientData:
    return client_data(lead, raw, tmp or Path("/nonexistent"))


def _page(family: str, *, raw: dict | None = None, text: str = "",
          form: str | None = None) -> str:
    client = sitetemplate.themed(_client(LEADS[family], raw), family)
    copy, _ = sitetemplate.parse_copy(text, client, family)
    faqs = sitekit.faq_list(client, list(copy.faqs), form=bool(form))
    return sitetemplate.render_home(client, copy, family, faqs, form_action=form)


# ------------------------------------------------------------------ the gate

def test_the_gate_rejects_the_eco_electrical_paragraph_every_way_it_can_arrive():
    assert pagegate.text_problem(ECO_PARAGRAPH)
    # as the whole answer, as a fragment, and as the body of a whole document
    assert pagegate.page_problem(ECO_PARAGRAPH)
    assert pagegate.page_problem(f"<p>{ECO_PARAGRAPH}</p>")
    doc = ("<!doctype html><html><head><title>Eco Electrical Services</title></head>"
           f"<body><header><a href='tel:2125550110'>Call</a></header><main><p>"
           f"{ECO_PARAGRAPH}</p></main></body></html>")
    problem = pagegate.page_problem(doc)
    assert problem and "tool output" in problem


def test_the_gate_reads_visible_text_only():
    """Every page carries application/ld+json; that is not a visitor's word."""
    words = " ".join(["plumbing"] * 30)
    doc = ('<!doctype html><html><head><title>t</title><script type="application/ld+json">'
           '{"a": "json tool validation"}</script><style>.tool{}</style></head>'
           f"<body><p>{words}</p><p>We stock the tools you need.</p></body></html>")
    assert pagegate.page_problem(doc) is None


@pytest.mark.parametrize("markup, why", [
    ("<html><head><title>x</title></head><body>" + "word " * 30 + "</body></html>",
     "doctype"),
    ("<!doctype html><html><body>" + "word " * 30 + "</body></html>", "<head>"),
    ("<!doctype html><html><head><title>x</title></head><body>hi</body></html>",
     "visible word"),
])
def test_the_gate_wants_a_whole_page(markup, why):
    assert why in (pagegate.page_problem(markup) or "")


@pytest.mark.parametrize("family", sitetemplate.FAMILIES)
def test_every_family_renders_a_page_the_gate_passes(family):
    assert pagegate.page_problem(_page(family)) is None


# ----------------------------------------------------------------- the copy

@pytest.mark.parametrize("category, family", [
    ("plumber", "trades"), ("electrician", "trades"), ("roofing_contractor", "trades"),
    ("locksmith", "trades"), ("painter", "trades"), ("moving_company", "trades"),
    ("hair_salon", "beauty"), ("barber_shop", "beauty"), ("nail_salon", "beauty"),
    ("pet_groomer", "beauty"), ("restaurant", "food"), ("bakery", "food"),
    ("florist", "food"), ("caterer", "food"), ("dry_cleaning", "food"),
    ("tailor", "food"), ("auto repair shop", "trades"), (None, "trades"),
])
def test_family_for_picks_by_category(category, family):
    assert sitetemplate.family_for(category) == family


@pytest.mark.parametrize("claim", [
    "Licensed and insured plumbers", "Available 24/7", "Serving Manhattan since 1998",
    "Over 20 years of experience", "Rated 4.9 by our customers", "Family-owned and local",
    "Same-day service", "Free estimates on every job", "Our team of experts",
    "Fast response, day or night", "The best plumber in New York",
])
def test_a_claim_only_the_business_can_make_is_never_used(claim):
    client = _client(LEADS["trades"])
    copy, notes = sitetemplate.parse_copy(json.dumps({"headline": claim}), client, "trades")
    assert copy.headline == sitetemplate.default_copy(client, "trades").headline
    assert notes and notes[0].startswith("headline:")


@pytest.mark.parametrize("bad", [
    "<script>alert(1)</script>", "Visit https://example.com", "Email us at a@b.co",
    "Our JSON tool output", "Input validation passed", "Call 555-9999 now",
])
def test_markup_links_tool_words_and_stray_figures_are_refused(bad):
    client = _client(LEADS["trades"])
    copy, _ = sitetemplate.parse_copy(json.dumps({"subhead": bad}), client, "trades")
    assert copy.subhead == sitetemplate.default_copy(client, "trades").subhead


def test_a_figure_from_the_leads_own_details_is_allowed():
    client = _client(LEADS["trades"])
    text = json.dumps({"subhead": "Find us at 100 Example Ave in New York."})
    copy, notes = sitetemplate.parse_copy(text, client, "trades")
    assert copy.subhead == "Find us at 100 Example Ave in New York."
    assert not [n for n in notes if n.startswith("subhead")]


def test_fenced_json_is_read_and_good_fields_are_kept_field_by_field():
    client = _client(LEADS["trades"])
    text = "```json\n" + json.dumps({
        "headline": "Plumbing help across Manhattan",
        "about": "Licensed since 1990.",
        "services": [{"name": "Drains", "line": "Blocked sinks cleared."},
                     {"name": "Leaks", "line": "Pipes that drip."},
                     {"name": "Heaters", "line": "When the hot water stops."}],
    }) + "\n```"
    copy, notes = sitetemplate.parse_copy(text, client, "trades")
    assert copy.headline == "Plumbing help across Manhattan"
    assert copy.about == sitetemplate.default_copy(client, "trades").about
    assert [s.name for s in copy.services] == ["Drains", "Leaks", "Heaters"]
    assert copy.source == "mixed" and any(n.startswith("about") for n in notes)


def test_the_default_about_line_reads_properly():
    lead = {**LEADS["trades"], "name": "Hitchingham Electric", "category": "electrician",
            "address": None}
    about = sitetemplate.default_copy(_client(lead), "trades").about
    assert about.startswith("Hitchingham Electric is an electrician.")


def test_everything_the_page_says_is_escaped():
    lead = {**LEADS["trades"], "name": 'Bob <script>x()</script> & "Sons"'}
    client = sitetemplate.themed(_client(lead), "trades")
    copy = sitetemplate.default_copy(client, "trades")
    html = sitetemplate.render_home(client, copy, "trades", [])
    assert "<script>x()" not in html and "&lt;script&gt;x()" in html


# --------------------------------------------------------------- the design

def test_the_design_system_styles_every_state_of_every_button():
    css = sitetemplate.css()
    for state in (".mf-btn:hover", ".mf-btn:focus-visible", ".mf-btn:active",
                  ".mf-btn:disabled", '.mf-btn[aria-disabled="true"]'):
        assert state in css, state
    # no browser-default link anywhere
    assert re.search(r"(?m)^a \{ color: inherit; text-decoration: none;", css)
    assert "prefers-reduced-motion" in css
    # fonts are self-hosted, nothing is loaded from another origin
    assert "fonts/archivo-var.woff2" in css and "googleapis" not in css


@pytest.mark.parametrize("family", sitetemplate.FAMILIES)
def test_every_home_page_link_is_a_designed_one(family):
    root = launchcheck.parse(_page(family, form="https://formspree.io/f/x"))
    styled = ("mf-preview", "mf-nav", "mf-foot", "mf-prose")
    for a in root.find_all("a"):
        inside = any(any(c in styled for c in n.classes()) for n in a.ancestors())
        assert a.get("class") or inside, a.attrs


@pytest.mark.parametrize("family", sitetemplate.FAMILIES)
def test_every_family_has_the_sections_the_brief_asks_for(family):
    html = _page(family, form="https://formspree.io/f/x")
    for marker in ('data-atlas="header"', 'data-atlas="scene"', 'data-atlas="facts"',
                   'id="services"', 'class="mf-cards"', 'id="how"', 'class="mf-steps"',
                   'id="visit"', 'class="mf-map"', 'id="book"', 'id="contact"',
                   'id="faq"', 'id="closing"', 'data-atlas="sticky-cta"',
                   'data-atlas="footer"', 'data-atlas="enquiry"', 'href="privacy.html"',
                   'href="terms.html"', 'class="mf-photo"',
                   'href="tel:7185550199"' if family == "trades" else 'href="tel:'):
        assert marker in html, marker
    # the marketing order: hero, facts, services, how, area, booking, contact,
    # questions, closing; the footer after everything
    order = ['class="mf-hero', 'data-atlas="facts"', 'id="services"', 'id="how"',
             'id="visit"', 'id="book"', 'id="contact"', 'id="faq"', 'id="closing"',
             'data-atlas="footer"']
    assert [html.index(m) for m in order] == sorted(html.index(m) for m in order)


def test_a_preview_says_so_and_stays_out_of_search_until_the_client_buys():
    preview = _page("trades")
    assert '<meta name="robots" content="noindex">' in preview
    assert "Preview made by" in preview and "for NYC Quality Plumbing" in preview
    sold = _page("trades", raw={"package": "100"})
    assert 'content="noindex"' not in sold and "Preview made by" not in sold


def test_the_chat_script_stays_on_a_chat_package_page(monkeypatch):
    # Off while ATLAS is local-only (2026-10-29); on behind a public door.
    assert 'data-atlas="chat"' not in _page("beauty", raw={"package": "500"})
    monkeypatch.setattr(client_mod, "PUBLIC_DOOR", True)
    assert 'data-atlas="chat"' in _page("beauty", raw={"package": "500"})
    assert 'data-atlas="chat"' not in _page("beauty", raw={"package": "100"})


# ---------------------------------------------- build, launch check, deploy

def _async(value):
    async def fake(*a, **kw):
        return value
    return fake


def _env(monkeypatch, tmp_path: Path, lead: dict, family: str, raw: dict | None = None,
         copy: dict | None = None) -> Path:
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "sites")
    monkeypatch.setattr(build, "UPLOADS_DIR", tmp_path / "uploads")
    (tmp_path / "uploads" / str(lead["id"])).mkdir(parents=True, exist_ok=True)
    if raw is not None:
        (tmp_path / "uploads" / str(lead["id"]) / "client.json").write_text(
            json.dumps(raw))
    mock = tmp_path / "mockups" / f"{lead['id']}.html"
    mock.parent.mkdir(parents=True, exist_ok=True)
    mock.write_text("<!doctype html><html></html>")
    mock.with_suffix(".copy.json").write_text(
        json.dumps({"family": family, "copy": copy or {}}))
    monkeypatch.setattr(_store, "get_lead", _async(lead))
    monkeypatch.setattr(_store, "latest_mockup_path", _async(str(mock)))
    monkeypatch.setattr(_store, "get_site", _async(None))
    monkeypatch.setattr(_store, "insert_site", _async(3))
    monkeypatch.setattr(_store, "set_lead_status", _async(None))
    return tmp_path / "sites" / build.slugify(lead["name"], lead["id"])


@pytest.mark.parametrize("family", sitetemplate.FAMILIES)
async def test_each_family_builds_a_site_with_no_launch_issue(monkeypatch, tmp_path,
                                                              family):
    site = _env(monkeypatch, tmp_path, LEADS[family], family,
                raw={"form_action": "https://formspree.io/f/abc"})
    result = await build.build_site(LEADS[family]["id"])
    assert result.ok, result.detail
    report = launch_audit(site)
    assert report.ready, report.summary()
    content = next(i for i in report.items if i.number == CONTENT_NUMBER)
    assert content.status is Status.OK and "preview" in content.evidence
    index = (site / "index.html").read_text()
    assert f'data-family="{family}"' in index
    assert index.count('data-atlas="header"') == 1          # enhance added no 2nd
    assert index.count('data-atlas="sticky-cta"') == 1
    assert 'data-atlas="launch"' not in index               # no launch CSS on top
    for page in ("contact.html", "privacy.html", "terms.html", "404.html"):
        assert f'data-family="{family}"' in (site / page).read_text(), page
    assert (site / "fonts" / "archivo-var.woff2").exists()


async def test_the_eco_paragraph_on_a_built_page_blocks_the_deploy(monkeypatch, tmp_path):
    lead = {**LEADS["trades"], "id": 38, "name": "Eco Electrical Services, LLC",
            "category": "electrician", "phone": "(212) 555-0110", "address": None}
    site = _env(monkeypatch, tmp_path, lead, "trades")
    assert (await build.build_site(38)).ok
    index = site / "index.html"
    index.write_text(re.sub(r"<main id=\"main\">.*</main>",
                            f'<main id="main"><p>{ECO_PARAGRAPH}</p></main>',
                            index.read_text(), flags=re.DOTALL))
    report = launch_audit(site)
    item = next(i for i in report.items if i.number == CONTENT_NUMBER)
    assert item.status is Status.ISSUE and "tool output" in item.evidence
    assert not report.ready

    monkeypatch.setattr(_store, "get_site", _async({"slug": site.name, "path": str(site)}))
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", "deployhost")
    monkeypatch.setenv("AGENCY_DEPLOY_ROOT", "/var/www/clients")

    async def no_rsync(argv):
        raise AssertionError("a page of tool output must never be published")
    monkeypatch.setattr(deploy, "_run", no_rsync)
    run = await deploy.deploy_site(site.name)
    assert not run.ok and "launch check failed" in run.detail
    assert "22 A real page, not tool output" in run.detail


def test_a_preview_without_its_noindex_is_an_issue(tmp_path):
    lead = LEADS["food"]
    client = sitetemplate.themed(_client(lead), "food")
    copy = sitetemplate.default_copy(client, "food")
    faqs = sitekit.faq_list(client, list(copy.faqs))
    site = tmp_path / "site"
    site.mkdir()
    html = sitetemplate.render_home(client, copy, "food", faqs)
    (site / "index.html").write_text(html.replace(
        '<meta name="robots" content="noindex">', ""))
    finish_site(site, client, analytics=None, form_action=None, faqs=faqs)
    item = next(i for i in launch_audit(site).items if i.number == CONTENT_NUMBER)
    assert item.status is Status.ISSUE and "noindex" in item.evidence


def test_the_builder_refuses_what_the_gate_refuses(monkeypatch):
    """render_brochure hands back the gate's verdict with the page; a page
    the gate refuses is never written (build_site returns before the write)."""
    lead = LEADS["trades"]
    monkeypatch.setattr(sitetemplate, "render_home",
                        lambda *a, **kw: f"<p>{ECO_PARAGRAPH}</p>")
    problem = build.render_brochure(
        lead, _client(lead), {"family": "trades", "copy": {}}, None)[3]
    assert problem and "not a full HTML document" in problem


async def test_build_site_writes_nothing_when_the_gate_refuses(monkeypatch, tmp_path):
    site = _env(monkeypatch, tmp_path, LEADS["trades"], "trades")
    monkeypatch.setattr(sitetemplate, "render_home",
                        lambda *a, **kw: f"<!doctype html><p>{ECO_PARAGRAPH}</p>")
    result = await build.build_site(LEADS["trades"]["id"])
    assert not result.ok and "nothing was written" in result.detail
    assert not site.exists()


# ------------------------------------------------------------ the call brief

def test_the_opening_line_only_mentions_a_website_the_lead_has():
    no_site = calls.opening_line({"name": "Eco Electrical Services", "website": None})
    assert "looked at your website" not in no_site
    assert "couldn't find a website" in no_site
    has_site = calls.opening_line({"name": "Joe's", "website": "joes.example"})
    assert "looked at your website" in has_site


def test_what_miro_built_is_the_public_preview_url_not_a_server_path():
    # 2026-10-30: no per-slug host; a slug alone is "publish it first".
    built = calls.built_line({"slug": "eco-electrical-services-llc-38", "deployed": True,
                              "mockup": "data/agency/mockups/38.html"})
    assert "mapfront" not in built and "agency.publish_vercel" in built
    assert calls.built_line({"domain": "atlas-agency-eco-38.vercel.app",
                             "slug": "eco-electrical-services-llc-38"}) == (
        "https://atlas-agency-eco-38.vercel.app")
    assert calls.built_line({"domain": "eco.example", "slug": "x-1"}) == "https://eco.example"
    only_mockup = calls.built_line({"mockup": "data/agency/mockups/38.html"})
    assert "data/agency" not in only_mockup


# ------------------------------------------------------------ the booking
# Miro, 2026-09-30: "design it better with buttons, reservations if it's a
# restaurant or booking system". sitebooking.py; the form's script is
# templates/booking.js.

def _forms(html: str) -> list:
    return [f for f in launchcheck.parse(html).find_all("form")
            if f.get("data-atlas") == "booking"]


@pytest.mark.parametrize("family, label", [
    ("trades", "Request a visit"), ("beauty", "Book an appointment"),
    ("food", "Reserve a table")])
def test_every_family_renders_its_booking_form_and_leads_with_it(family, label):
    html = _page(family)
    root = launchcheck.parse(html)
    (form,) = _forms(html)
    assert form.get("data-mode") == "preview"
    assert 'id="book"' in html and f" / {label}</p>" in html
    hero = next(n for n in root.iter() if "mf-hero" in n.classes())
    dock = next(n for n in root.iter() if n.get("data-atlas") == "sticky-cta")
    for where in (hero, dock):
        first = next(n for n in where.iter() if n.tag == "a")
        assert first.get("data-atlas") == "book-cta" and label in first.text()
        assert first.get("href") == "#book"
    assert 'id="book-sheet"' in html and 'data-atlas="booking"' in html
    marks = {"trades": "mf-docket", "beauty": "mf-glass", "food": "mf-canopy"}
    assert marks[family] in html                      # the family's one moment


@pytest.mark.parametrize("category, cta, controls", [
    ("plumber", "Request a visit", ('name="job"', 'id="b-where"', 'name="part"')),
    ("painter", "Get a quote", ('name="job"', 'id="b-where"')),
    ("hair_salon", "Book an appointment", ('name="service"', 'id="b-day"', 'id="b-time"')),
    ("restaurant", "Reserve a table", ('id="b-party"', 'class="mf-table"', 'id="b-time"')),
    ("cafe", "Reserve a table", ('id="b-party"',)),
    ("bakery", "Place an order", ('id="b-wish"', 'id="b-day"')),
    ("caterer", "Request a quote", ('id="b-guests"', 'id="b-wish"')),
    ("florist", "Order flowers", ('id="b-wish"',)),
    ("tailor", "Book a fitting", ('id="b-day"', 'id="b-time"')),
    ("dry_cleaning", "Send a request", ('id="b-wish"',)),
])
def test_the_booking_fits_what_the_business_is(category, cta, controls):
    family = sitetemplate.family_for(category)
    lead = {**LEADS[family], "category": category}
    client = sitetemplate.themed(_client(lead), family)
    copy = sitetemplate.default_copy(client, family)
    html = sitetemplate.render_home(client, copy, family, [])
    assert f'class="mf-eyebrow">04 / {cta}</p>' in html
    assert 'id="book-title" class="mf-title">' in html
    for control in controls:
        assert control in html, control
    # every form asks for a name and a phone to call back on
    assert 'id="b-name"' in html and 'type="tel"' in html


def test_a_preview_booking_sends_nothing_and_says_so():
    html = _page("trades")
    (form,) = _forms(html)
    assert not form.get("action") and form.get("method") == "dialog"
    typed = [n for n in form.find_all("input", "textarea", "select")
             if n.get("type") != "radio"]
    assert typed and not [n.get("name") for n in typed if n.get("name")]
    assert "_next" not in html.split('data-atlas="booking"')[1].split("</form>")[0]
    assert ("Bookings start when NYC Quality Plumbing launches this site. For now, "
            'call <span class="mf-nowrap">(718) 555-0199</span>.') in html
    assert "Nothing was sent" in html and "nothing you type here is sent" in html
    # a request, never a booking or a free slot
    lower = launchcheck.parse(html).text().lower()
    for claim in ("confirmed", "available", "availability", "slots", "booked"):
        assert claim not in lower, claim


def test_a_preview_without_a_phone_points_at_what_there_is():
    lead = {**LEADS["beauty"], "phone": None}
    client = sitetemplate.themed(_client(lead, {"email": "hi@court.example"}), "beauty")
    html = sitetemplate.render_home(client, sitetemplate.default_copy(client, "beauty"),
                                    "beauty", [])
    assert "launches this site. For now, email hi@court.example." in html


def test_a_preview_never_sends_even_with_a_form_service():
    html = _page("food", form="https://formspree.io/f/x")
    (form,) = _forms(html)
    assert form.get("data-mode") != "send" and not form.get("action")


async def test_a_preview_build_gets_no_form_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENCY_FORM_ACTION", "https://formspree.io/f/agency")
    monkeypatch.setattr(build, "UPLOADS_DIR", tmp_path)
    lead = dict(LEADS["food"])
    _, preview = await build._client_for(1, lead, None, None, None)
    assert preview is None
    _, sold = await build._client_for(1, lead, {"package": "100"}, None, None)
    assert sold == "https://formspree.io/f/agency"


def test_a_form_service_gets_the_booking_as_a_named_post():
    html = _page("food", raw={"package": "100"}, form="https://formspree.io/f/x")
    (form,) = _forms(html)
    assert form.get("data-mode") == "send"
    assert form.get("action") == "https://formspree.io/f/x" and form.get("method") == "post"
    names = {n.get("name") for n in form.find_all("input", "textarea") if n.get("name")}
    assert {"_next", "_subject", "_gotcha", "date", "time", "party_size", "name",
            "phone", "notes"} <= names
    nxt = next(n for n in form.find_all("input") if n.get("name") == "_next")
    assert nxt.get("value").endswith("thank-you.html")
    text = launchcheck.parse(html).text()
    assert "We'll call you to confirm." in text and "Nothing was sent" not in text


def test_a_booking_link_opens_the_booking_service_and_the_page_has_no_form():
    url = "https://booksy.com/en-us/000_court-street-salon"
    html = _page("beauty", raw={"booking_url": url})
    assert not _forms(html) and 'id="book-sheet"' not in html
    root = launchcheck.parse(html)
    ctas = [n for n in root.find_all("a") if n.get("data-atlas") == "book-cta"]
    assert ctas and all(n.get("href") == url for n in ctas)
    assert all(n.get("target") == "_blank" and "noopener" in n.get("rel") for n in ctas)
    assert "Book an appointment online with Booksy." in html
    named = _page("beauty", raw={"booking_url": url, "booking_provider": "Court Direct"})
    assert "online with Court Direct." in named


def test_a_sold_site_with_no_booking_path_offers_the_call():
    html = _page("food", raw={"package": "100"})
    assert not _forms(html) and 'id="book"' not in html and "book-cta" not in html
    assert 'href="tel:6465550187"' in html


@pytest.mark.parametrize("family", sitetemplate.FAMILIES)
def test_every_family_takes_call_me_markup_by_the_contact(family):
    html = _page(family)
    contact = html.split('id="contact"')[1].split("</section>")[0]
    assert "mf-callme" not in contact
    client = sitetemplate.themed(_client(LEADS[family]), family)
    filled = sitetemplate.render_home(client, sitetemplate.default_copy(client, family),
                                      family, [], callme="<button>x</button>")
    assert '<div class="mf-callme"><button>x</button></div>' in filled


def test_every_booking_control_has_its_states_and_motion_stands_down():
    css = sitetemplate.css()
    for state in (".mf-chip input:checked + span", ".mf-chip input:focus-visible + span",
                  ".mf-chip input:disabled + span", ".mf-step:disabled", ".mf-step:active",
                  ".mf-step:focus-visible", '.mf-bsubmit[data-state="loading"]',
                  '.mf-bsubmit[data-state="error"]', ".mf-bfield.is-bad .mf-input",
                  ".mf-x:focus-visible", "html.mf-typing .mf-dock"):
        assert state in css, state
    reduced = css.split("@media (prefers-reduced-motion: reduce)")[1]
    assert "animation-duration: 1ms" in reduced and "transition-duration: 1ms" in reduced
    from atlas.modules.agency import sitebooking
    script = sitebooking.script()
    assert "prefers-reduced-motion: reduce" in script
    # no network from the page's own script: a live form is the browser's POST
    for call in ("fetch(", "XMLHttpRequest", "sendBeacon", "http"):
        assert call not in script, call


def _reserve(raw: dict | None, form: str | None) -> tuple | None:
    from atlas.modules.agency import sitebooking
    client = sitetemplate.themed(_client(LEADS["food"], raw), "food")
    return sitebooking.reservations(client, form)


def test_reservations_are_claimed_only_with_a_real_path():
    assert _reserve(None, None) is None                        # preview: no path
    assert _reserve({"package": "100"}, None) is None          # sold, no path
    # a preview's form reaches an agency inbox at most, never the business
    assert _reserve(None, "https://formspree.io/f/x") is None
    sold = {"package": "100"}
    assert _reserve(sold, "https://formspree.io/f/x") == ("Restaurant", True)
    url = "https://www.opentable.com/r/orchard-corner"
    assert _reserve({**sold, "booking_url": url}, None) == ("Restaurant", url)
    from atlas.modules.agency import sitebooking
    salon = sitetemplate.themed(_client(LEADS["beauty"]), "beauty")
    assert sitebooking.reservations(salon, "https://formspree.io/f/x") is None
    bakery = sitetemplate.themed(_client({**LEADS["food"], "category": "bakery"}), "food")
    assert sitebooking.reservations(bakery, "https://formspree.io/f/x") is None


def _ld(site: Path) -> dict:
    page = launchcheck.page_from_markup("index.html", (site / "index.html").read_text())
    return next(o for o in page.ld_objects() if "LocalBusiness" in str(o.get("@type")))


@pytest.mark.parametrize("family", sitetemplate.FAMILIES)
async def test_a_preview_builds_with_its_booking_form_and_no_launch_issue(
        monkeypatch, tmp_path, family):
    """No form service, no booking link: the preview's form must not turn
    item 4 or 18 into an ISSUE and so block the deploy."""
    site = _env(monkeypatch, tmp_path, LEADS[family], family)
    assert (await build.build_site(LEADS[family]["id"])).ok
    report = launch_audit(site)
    issues = [i for i in report.items if i.status is Status.ISSUE]
    assert not issues, [(i.number, i.evidence) for i in issues]
    thanks = next(i for i in report.items if i.number == 4)
    assert thanks.status is Status.NEEDS_CLIENT and "sends nothing" in thanks.evidence
    privacy = (site / "privacy.html").read_text()
    assert "booking form is not connected yet" in privacy
    assert "acceptsReservations" not in json.dumps(_ld(site))


async def test_a_restaurant_with_a_form_service_says_it_takes_reservations(
        monkeypatch, tmp_path):
    site = _env(monkeypatch, tmp_path, LEADS["food"], "food",
                raw={"form_action": "https://formspree.io/f/abc", "package": "100"})
    assert (await build.build_site(LEADS["food"]["id"])).ok
    ld = _ld(site)
    assert ld["@type"] == ["LocalBusiness", "Restaurant"] and ld["acceptsReservations"]
    report = launch_audit(site)
    assert report.ready, report.summary()
    assert "a booking request with a form on this site" in (
        site / "privacy.html").read_text()


async def test_a_preview_form_that_could_send_is_a_launch_issue(monkeypatch, tmp_path):
    site = _env(monkeypatch, tmp_path, LEADS["beauty"], "beauty")
    assert (await build.build_site(LEADS["beauty"]["id"])).ok
    index = site / "index.html"
    original = index.read_text()
    index.write_text(original.replace('id="b-phone" type="tel"',
                                      'id="b-phone" type="tel" name="phone"'))
    item = next(i for i in launch_audit(site).items if i.number == 4)
    assert item.status is Status.ISSUE and 'names a field ("phone")' in item.evidence
    index.write_text(original.replace('data-mode="preview"',
                                      'data-mode="preview" action="https://x.example/f"'))
    item = next(i for i in launch_audit(site).items if i.number == 4)
    assert item.status is Status.ISSUE and "has an action" in item.evidence


async def test_reservations_in_the_json_ld_without_a_path_is_a_launch_issue(
        monkeypatch, tmp_path):
    site = _env(monkeypatch, tmp_path, LEADS["food"], "food")
    assert (await build.build_site(LEADS["food"]["id"])).ok
    index = site / "index.html"
    index.write_text(index.read_text().replace(
        '"@type": "LocalBusiness",',
        '"@type": "LocalBusiness", "acceptsReservations": true,', 1))
    item = next(i for i in launch_audit(site).items if i.number == 17)
    assert item.status is Status.ISSUE and "takes reservations" in item.evidence


# ------------------------------------------------ the marketing site (v3)
# Miro, 2026-09-30, on the desktop review: "very poor design -- make sure
# it's a frontend marketing website for each one". sitemedia.py and the
# new sections in sitetemplate.py.

from atlas.modules.agency import sitemedia


def _every_default_line(client, family) -> list[str]:
    copy = sitetemplate.default_copy(client, family)
    lines = [copy.headline, copy.subhead, copy.about, copy.closing]
    lines += [s.name for s in copy.services] + [s.line for s in copy.services]
    lines += [s.title for s in copy.steps] + [s.line for s in copy.steps]
    lines += [f.q for f in copy.faqs] + [f.a for f in copy.faqs]
    lines += [t for _i, t in sitetemplate.facts_for(client, family, "preview")]
    lines += list(copy.lines) + list(sitetemplate.statements(family).values())
    return lines


@pytest.mark.parametrize("category", [
    "plumber", "electrician", "roofing_contractor", "painter", "hair_salon",
    "barber_shop", "nail_salon", "pet_groomer", "restaurant", "bakery", "florist",
    "caterer", "dry_cleaning", "tailor", "cafe", None])
def test_no_default_line_makes_a_claim_the_business_did_not(category):
    """The facts strip, the steps, the closing line, the services: none of
    the code-written copy carries a year, licence, rating, response time,
    round-the-clock promise, price or guarantee (the same check the model's
    fields pass, applied to the defaults)."""
    family = sitetemplate.family_for(category)
    client = _client({**LEADS[family], "category": category})
    allowed = set(re.findall(r"\d+", client.address or ""))
    for line in _every_default_line(client, family):
        assert not sitetemplate._CLAIMS.search(line), line
        assert not re.search(r"24\s*/\s*7|\bfree\b|\bguarantee", line, re.IGNORECASE), line
        assert set(re.findall(r"\d+", line)) <= allowed, line


def test_the_facts_strip_says_only_what_the_lead_record_says():
    client = _client(LEADS["trades"])
    facts = [t for _i, t in sitetemplate.facts_for(client, "trades", "preview")]
    assert facts == ["Plumber", "In Manhattan", "Serving Manhattan and nearby",
                     "Call or book online"]
    salon = _client(LEADS["beauty"])
    assert [t for _i, t in sitetemplate.facts_for(salon, "beauty", "preview")] == [
        "Salon", "In Brooklyn", "Call or book online"]
    # no address, no place claims; no booking path, no "book online"
    bare = _client({**LEADS["trades"], "address": None})
    assert [t for _i, t in sitetemplate.facts_for(bare, "trades", None)] == [
        "Plumber", "Call to book"]


@pytest.mark.parametrize("address, borough", [
    ("512 E 11th St, New York, NY 10009", "Manhattan"),
    ("214 Court St, Brooklyn, NY 11201", "Brooklyn"),
    ("30-02 Steinway St, Astoria, NY 11103", "Queens"),
    ("12 Fore St, Portland, ME 04101", None), (None, None)])
def test_the_borough_is_read_off_the_zip_code_only(address, borough):
    assert sitetemplate.borough_of(address) == borough


@pytest.mark.parametrize("category, hero, card", [
    ("electrician", "photo-electric", "photo-electric-2"),
    ("plumber", "photo-plumbing", "photo-plumbing-2"),
    ("roofing_contractor", "photo-tools", None),
    ("hair_salon", "photo-salon", "photo-salon-2"),
    ("barber_shop", "photo-barber", "photo-barber-2"),
    ("pet_groomer", None, None),
    ("restaurant", "photo-restaurant", "photo-restaurant-2"),
    ("bakery", "photo-bakery", None), ("florist", "photo-florist", None),
    ("dry_cleaning", "photo-cleaners", None)])
def test_each_category_gets_an_honest_generic_photo_or_none(category, hero, card):
    family = sitetemplate.family_for(category)
    got_hero, got_card = sitemedia.photos_for(category, family)
    assert (got_hero.stem if got_hero else None) == hero
    assert (got_card.stem if got_card else None) == card
    for photo in (got_hero, got_card):
        if photo:
            assert photo.alt.startswith("Illustrative photo:")
            assert not re.search(r"\bour\b|\bteam\b|\bstaff\b", photo.alt, re.IGNORECASE)


def test_every_photo_and_background_file_is_in_the_templates_dir():
    for photo in sitemedia.PHOTOS.values():
        for rel in photo.files():
            assert (sitemedia.TEMPLATES_DIR / rel).is_file(), rel
        assert "Unsplash" in photo.source
    for background in sitemedia.BACKGROUNDS.values():
        for rel in background.files():
            assert (sitemedia.TEMPLATES_DIR / rel).is_file(), rel
        assert (sitemedia.TEMPLATES_DIR / background.video).stat().st_size < 1_200_000


_SRC = re.compile(r'(?:src|data-src|poster)="([^"]+)"|srcset="([^"]+)"')


@pytest.mark.parametrize("family", sitetemplate.FAMILIES)
def test_every_asset_is_referenced_locally_and_copied_beside_the_page(family, tmp_path):
    html = _page(family)
    client = _client(LEADS[family])
    written = set(sitetemplate.copy_assets(tmp_path, family, client.category))
    refs = set()
    for src, srcset in _SRC.findall(html):
        for part in (srcset or src).split(","):
            url = part.split()[0]
            if url.endswith(".js"):
                continue
            refs.add(url)
    assert refs and all(not r.startswith(("http", "//", "data:")) for r in refs)
    assert refs <= written, refs - written
    for rel in refs:
        assert (tmp_path / rel).is_file(), rel
    assert "getlayers.ai" not in html and "unsplash.com" not in html
    root = launchcheck.parse(html)
    for img in root.find_all("img"):
        assert img.get("alt") or img.get("role") == "presentation"
    eager = [i for i in root.find_all("img") if i.get("fetchpriority") == "high"]
    lazy = [i for i in root.find_all("img") if i.get("loading") == "lazy"]
    assert eager and lazy


@pytest.mark.parametrize("family", sitetemplate.FAMILIES)
def test_the_scene_is_a_poster_and_a_loop_that_a_phone_never_downloads(family):
    html = _page(family)
    scene = html.split('data-atlas="scene">')[1].split("</div>")[0]
    if family == "trades":
        # v4: the photograph is the hero, full bleed, under a CSS gradient;
        # there is no loop to attach and nothing extra for a phone to fetch
        assert "<video" not in scene and 'class="mf-photo"' in scene
        assert 'fetchpriority="high"' in scene and "Illustrative photo:" in scene
        assert "bg-trades" not in html and "media/bg-" not in html
        return
    assert "<picture>" in scene and 'role="presentation"' in scene
    video = scene.split("<video")[1].split(">")[0]
    assert 'data-src="media/bg-' in video and " src=" not in video
    assert "muted" in video and 'preload="none"' in video and "autoplay" not in video
    script = sitemedia.VIDEO_SCRIPT
    assert "prefers-reduced-motion: no-preference" in script and "min-width:56rem" in script
    for call in ("fetch(", "XMLHttpRequest", "http"):
        assert call not in script, call
    assert ".mf-scene video { display: none; }" in sitetemplate.css().split(
        "prefers-reduced-motion: reduce")[1]


@pytest.mark.parametrize("family, label", [("trades", "Request"), ("beauty", "Book"),
                                           ("food", "Ask")])
def test_every_service_card_has_an_icon_and_its_own_button(family, label):
    root = launchcheck.parse(_page(family))
    cards = [n for n in root.iter() if "mf-cardx" in n.classes()
             and "mf-cardx--photo" not in n.classes()]
    assert len(cards) >= 3
    for card in cards:
        assert card.find_all("svg") and card.find_all("h3")
        (btn,) = [a for a in card.find_all("a") if "mf-btn" in a.classes()]
        assert btn.text() == label and btn.get("href") == "#book"
        if family != "food":
            assert btn.get("data-book-pick") == card.find_all("h3")[0].text()


@pytest.mark.parametrize("family", sitetemplate.FAMILIES)
def test_how_it_works_is_three_numbered_steps(family):
    root = launchcheck.parse(_page(family))
    steps = next(n for n in root.iter() if "mf-steps" in n.classes())
    items = [n for n in steps.children if not isinstance(n, str) and n.tag == "li"]
    assert len(items) == 3
    assert [i.find_all("span")[0].text() for i in items] == ["01", "02", "03"]
    how = next(n for n in root.iter() if n.get("id") == "how")
    assert "mf-on-deep" in how.classes()
    assert "We call to confirm" in [i.find_all("h3")[0].text() for i in items]


def test_model_steps_and_closing_are_validated_field_by_field():
    client = _client(LEADS["trades"])
    good = {"steps": [{"title": "Tell us the job", "line": "A few words on what is wrong."},
                      {"title": "We call", "line": "To agree a time."},
                      {"title": "The visit", "line": "We come out and fix it."}],
            "closing": "Something dripping?"}
    copy, notes = sitetemplate.parse_copy(json.dumps(good), client, "trades")
    assert [s.title for s in copy.steps] == ["Tell us the job", "We call", "The visit"]
    assert copy.closing == "Something dripping?" and not [
        n for n in notes if n.startswith(("step", "closing"))]
    defaults = sitetemplate.default_copy(client, "trades")
    # a claim in a step drops the whole sequence back to the defaults
    bad = dict(good)
    bad["steps"] = [*good["steps"][:2], {"title": "Same-day visit", "line": "Fast."}]
    copy, notes = sitetemplate.parse_copy(json.dumps(bad), client, "trades")
    assert copy.steps == defaults.steps and any(n.startswith("steps:") for n in notes)
    # two steps are not a sequence of three; a figure in the closing line goes
    copy, _ = sitetemplate.parse_copy(json.dumps({"steps": good["steps"][:2],
                                                  "closing": "Call within 2 hours"}),
                                      client, "trades")
    assert copy.steps == defaults.steps and copy.closing == defaults.closing


def test_the_area_section_draws_a_map_and_never_embeds_one():
    html = _page("trades")
    assert '<svg class="mf-map"' in html and "<iframe" not in html
    assert 'aria-label="A drawn street map of Manhattan"' in html
    assert ">Manhattan</text>" in html and ">E 11th St</text>" not in html
    assert ">Example Ave</text>" in html                 # the street from the address
    assert "Manhattan and nearby</text>" in html and ">Brooklyn</text>" in html
    assert 'class="mf-map__pin"' in html and ">N</text>" in html   # the monogram
    assert 'transform="rotate(-14' in html
    assert 'data-atlas="directions"' in html and 'data-atlas="map"' in html


def test_the_closing_band_and_footer_carry_the_same_actions_as_the_hero():
    root = launchcheck.parse(_page("beauty"))
    closing = next(n for n in root.iter() if n.get("id") == "closing")
    hrefs = [a.get("href") for a in closing.find_all("a")]
    assert hrefs == ["#book", "tel:7185550142"]
    foot = next(n for n in root.iter() if n.get("data-atlas") == "footer")
    labels = [a.text() for a in foot.find_all("a")]
    for label in ("Home", "Treatments", "How it works", "Find us", "Questions", "Contact",
                  "Privacy", "Terms", "(718) 555-0142"):
        assert label in labels, label
    assert "Hours" not in foot.text()          # none supplied, none shown
    with_hours = _page("beauty", raw={"hours": ["Tu-Sa 10:00-19:00"]})
    assert "Hours" in with_hours and "Tuesday–Saturday 10:00–19:00" in with_hours


def test_the_preview_still_sends_nothing_with_the_new_structure():
    html = _page("food")
    root = launchcheck.parse(html)
    forms = root.find_all("form")
    assert len(forms) == 1 and forms[0].get("method") == "dialog"
    assert not forms[0].get("action")
    for tag in root.find_all("img", "source", "video", "a", "script", "link"):
        for key in ("src", "data-src", "srcset", "poster"):
            value = tag.get(key)
            assert not value.startswith(("http", "//")), (tag.tag, value)
    offsite = [a.get("href") for a in root.find_all("a")
               if a.get("href").startswith("http")]
    assert all(h.startswith(("https://www.google.com/maps", "https://agency.storefront.test"))
               for h in offsite), offsite


# ------------------------------------------------------ the Lovable port (v4)
# Miro, 2026-10-01: every client site's design goes frontend-design ->
# GetLayers -> Webild -> Lovable, and the best of each is ported into our
# templates. The trades reference is Lovable "Bright Spark Site"
# (1d613626-2177-490b-8e33-242910df26ec): a cinematic photo hero with a
# three-line headline, numbered section labels, statement headings with a
# marigold full stop, a navy "how it works" band, a tilted street map, a
# two-column booking with a white form card, and a marigold closing band.

HITCHINGHAM = {"id": 40, "name": "Hitchingham Electric Company", "category": "electrician",
               "phone": "(540) 729-4548", "address": "512 E 11th St, New York, NY 10009",
               "website": None, "status": "audited"}


def _home(lead: dict, text: str = "", raw: dict | None = None) -> str:
    family = sitetemplate.family_for(lead["category"])
    client = sitetemplate.themed(_client(lead, raw), family)
    copy, _ = sitetemplate.parse_copy(text, client, family)
    return sitetemplate.render_home(client, copy, family, list(copy.faqs))


@pytest.mark.parametrize("category, lines", [
    ("electrician", ("Good work.", "Good light.", "A better home.")),
    ("plumber", ("Good work.", "Hot water.", "A quieter home.")),
    ("roofing_contractor", ("Good work.", "Dry rooms.", "A roof that holds.")),
    ("painter", ("Clean lines.", "Fresh walls.", "A brighter home.")),
    ("moving_company", ("Packed right.", "Carried with care.", "Settled in.")),
    ("locksmith", ("Good locks.", "Spare keys.", "A safer door.")),
    ("handyman", ("Good work.", "Done properly.", "A better home.")),
    ("hair_salon", ("Your hair.", "Your way.", "Done well.")),
    ("restaurant", ("Good food.", "Good company.", "A table for you.")),
])
def test_every_trade_has_its_own_three_line_headline(category, lines):
    family = sitetemplate.family_for(category)
    client = _client({**LEADS[family], "category": category})
    copy = sitetemplate.default_copy(client, family)
    assert copy.lines == lines
    for line in lines:
        assert len(line) <= sitetemplate.LIMITS["line"] and line.endswith(".")
        assert sitetemplate.field_problem(line, sitetemplate.LIMITS["line"], set()) is None


def test_the_hero_stacks_three_lines_and_lights_the_second():
    html = _home(HITCHINGHAM)
    hero = html.split('<h1 id="hero-title"')[1].split("</h1>")[0]
    assert hero.count('class="mf-hero__line"') == 2
    assert hero.count('class="mf-hero__line mf-hero__line--mark"') == 1
    assert ('<span class="mf-hero__line">Good work.</span> '
            '<span class="mf-hero__line mf-hero__line--mark">Good light.</span> '
            '<span class="mf-hero__line">A better home.</span>') in hero
    # the headline field is now the kicker over the three lines
    assert '<p class="mf-hero__kind">Electrical work in New York</p>' in html
    # the trades hero: the photograph full bleed, the booking button marigold
    assert 'class="mf-scene mf-scene--photo" data-atlas="scene"' in html
    hero_html = html.split('<section class="mf-hero')[1].split("</section>")[0]
    assert "mf-btn--sign mf-btn--book-cta" in hero_html


def test_model_lines_are_validated_as_a_set_of_three():
    client = _client(HITCHINGHAM)
    defaults = sitetemplate.default_copy(client, "trades")
    good = {"lines": ["Lights on.", "Panels sorted.", "A calmer home."]}
    copy, notes = sitetemplate.parse_copy(json.dumps(good), client, "trades")
    assert copy.lines == ("Lights on.", "Panels sorted.", "A calmer home.")
    assert not [n for n in notes if n.startswith("line")]
    # a claim in one line sends the three back to the defaults
    copy, notes = sitetemplate.parse_copy(
        json.dumps({"lines": ["Lights on.", "Same-day fixes.", "A calmer home."]}),
        client, "trades")
    assert copy.lines == defaults.lines and any(n.startswith("lines:") for n in notes)
    # two lines are not a stack of three; a line too long goes
    for bad in (["Lights on.", "Panels sorted."],
                ["Lights on.", "Panels sorted.", "A calmer, brighter, better home."]):
        copy, _ = sitetemplate.parse_copy(json.dumps({"lines": bad}), client, "trades")
        assert copy.lines == defaults.lines
    # a record written before the field existed gets the defaults, no note
    copy, notes = sitetemplate.parse_copy(json.dumps({"headline": "Lights on"}),
                                          client, "trades")
    assert copy.lines == defaults.lines and not [n for n in notes if "line" in n]
    assert "lines" in copy.as_dict() and "lines" in sitetemplate.COPY_SYSTEM


@pytest.mark.parametrize("family", sitetemplate.FAMILIES)
def test_the_sections_are_numbered_in_page_order(family):
    html = _page(family)
    eyebrows = re.findall(r'<p class="mf-eyebrow">(\d\d) / ([^<]+)</p>', html)
    numbers = [n for n, _ in eyebrows]
    assert numbers == [f"{i:02d}" for i in range(1, len(numbers) + 1)] and len(numbers) == 5
    labels = [label for _, label in eyebrows]
    assert labels[1] == ("How booking works" if family == "beauty" else "How it works")
    assert labels[-1] == "Questions" and labels[3] in (
        "Request a visit", "Book an appointment", "Reserve a table")
    # a section that is not rendered takes no number
    lead = {**LEADS["beauty"], "address": None}
    client = sitetemplate.themed(_client(lead), "beauty")
    copy = sitetemplate.default_copy(client, "beauty")
    html = sitetemplate.render_home(client, copy, "beauty", list(copy.faqs))
    assert 'id="visit"' not in html
    found = re.findall(r'<p class="mf-eyebrow">(\d\d) / ', html)
    assert found == ["01", "02", "03", "04"]
    # the contact section is the tail of "get in touch": no number of its own
    contact = html.split('id="contact"')[1].split("</section>")[0]
    assert "mf-eyebrow" not in contact


@pytest.mark.parametrize("family", sitetemplate.FAMILIES)
def test_every_statement_heading_is_code_written_and_makes_no_claim(family):
    html = _page(family)
    for key, line in sitetemplate.statements(family).items():
        assert not sitetemplate._CLAIMS.search(line), (key, line)
        assert not re.search(r"\d", line), (key, line)
        if key != "how_lead":
            assert f'class="mf-title">{escape(line)}</h2>' in html, key
    css = sitetemplate.css()
    assert ".mf-title::after" in css and "var(--mark)" in css.split(".mf-title::after")[1]
    # the full stop is drawn, never read aloud: no text in the pseudo-element
    dot = css.split(".mf-title::after")[1].split("}")[0]
    assert 'content: ""' in dot


def test_the_how_band_lists_the_steps_on_the_right_in_the_family_colours():
    html = _home(HITCHINGHAM)
    how = html.split('id="how"')[1].split("</section>")[0]
    assert '<p class="mf-eyebrow">02 / How it works</p>' in how
    assert 'class="mf-title">From your call to the finished job</h2>' in how
    assert how.index("mf-title") < how.index('<ol class="mf-steps">')
    assert '<span class="mf-step__n" aria-hidden="true">01</span>' in how
    css = sitetemplate.css()
    assert ".mf-on-deep .mf-eyebrow { color: var(--mark-on-deep)" in css


def test_the_map_is_tilted_and_labelled_only_from_the_record():
    svg = sitetemplate.street_map("Manhattan", street="E 11th St", nearby=("Brooklyn",),
                                  initial="H")
    assert '<g transform="rotate(-14 200 150)">' in svg
    assert ">Manhattan</text>" in svg and ">Brooklyn</text>" in svg
    assert ">E 11th St</text>" in svg and ">Manhattan and nearby</text>" in svg
    assert '<g class="mf-map__pin">' in svg and ">H</text>" in svg
    assert "<iframe" not in svg and "http" not in svg
    # nothing the record does not say: no street, no neighbours, no monogram
    bare = sitetemplate.street_map("Queens")
    assert ">Queens</text>" in bare and "and nearby" in bare
    assert "<text" in bare and bare.count("<text") == 2
    assert sitetemplate.street_map(None).count("<text") == 0
    # the Bronx reads as a place, and its neighbour is Manhattan
    html = _home({**HITCHINGHAM, "address": "100 E 161st St, Bronx, NY 10451"})
    assert ">the Bronx</text>" in html and ">Manhattan</text>" in html


def test_the_booking_section_is_two_columns_with_a_card_and_a_way_to_talk():
    html = _home(HITCHINGHAM)
    book = html.split('id="book"')[1].split("</section>")[0]
    assert "mf-section--soft" in html.split('id="book"')[0][-120:]
    assert '<p class="mf-eyebrow">04 / Request a visit</p>' in book
    assert 'class="mf-title">Let&#x27;s talk about the job</h2>' in book
    assert "Prefer to talk?" in book
    assert 'class="mf-book__phone" href="tel:5407294548"' in book
    assert book.index("mf-book__talk") < book.index('data-book-panel')
    # the form is the white card; a client without a phone has nothing to talk on
    quiet = _home({**HITCHINGHAM, "phone": None})
    assert "Prefer to talk?" not in quiet.split('id="book"')[1].split("</section>")[0]


def test_the_faq_and_closing_band_took_the_reference_shape():
    html = _home(HITCHINGHAM)
    faq = html.split('id="faq"')[1].split("</section>")[0]
    assert '<p class="mf-eyebrow">05 / Questions</p>' in faq
    assert 'class="mf-title">A few things worth knowing</h2>' in faq
    closing = html.split('id="closing"')[1].split("</section>")[0]
    assert "mf-close--sign" in html.split('id="closing"')[0][-80:]
    assert "mf-close__bg" not in closing                  # marigold, not a poster
    assert "mf-btn--light" not in closing and "mf-btn--ghost" in closing
    # the other families keep their deep band and poster
    salon = _page("beauty")
    assert "mf-close mf-on-deep" in salon and "mf-close__bg" in salon
    css = sitetemplate.css()
    for state in (".mf-btn--sign", ".mf-btn--sign:hover", ".mf-head__phone:hover",
                  ".mf-head__phone:focus-visible", ".mf-book__phone:hover"):
        assert state in css, state


def test_the_header_shows_the_phone_beside_the_booking_button():
    root = launchcheck.parse(_home(HITCHINGHAM))
    head = next(n for n in root.iter() if n.get("data-atlas") == "header")
    phone = [a for a in head.find_all("a") if "mf-head__phone" in a.classes()]
    assert len(phone) == 1 and phone[0].get("href") == "tel:5407294548"
    assert phone[0].text() == "(540) 729-4548"


def test_the_lovable_reference_is_recorded_beside_the_getlayers_ids():
    record = json.loads((sitetemplate.TEMPLATES_DIR / "getlayers.json").read_text())
    assert record["version"] == 4
    lovable = record["lovable"]["trades"]
    assert lovable["projectId"] == "1d613626-2177-490b-8e33-242910df26ec"
    assert "token" not in json.dumps(record["lovable"]).lower()
    assert "bg-trades" not in json.dumps(record["media"]["backgrounds"]["trades"]["files"])
    assert "trades" not in sitemedia.BACKGROUNDS
    assert not list((sitemedia.TEMPLATES_DIR / "media").glob("bg-trades*"))
