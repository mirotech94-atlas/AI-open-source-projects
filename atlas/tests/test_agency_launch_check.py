"""The 20 launch items every client site carries, and the check that reads
them back (Miro, 2026-09-29, reel DdH5iyOzkZH).

Each item is tested present (OK), absent (ISSUE, or NEEDS_CLIENT when only
the client can supply it) and, where it matters, dishonest (ISSUE). The
honesty rule gets its own tests: no reviews, case studies or team photo
unless the client gave them, and no path by which a model can add one.
"""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

from atlas.kernel.llm import Completion
from atlas.modules import agency
from atlas.modules.agency import _store, build, deploy, sitekit
from atlas.modules.agency.client import ClientData, client_data, read_client_file
from atlas.modules.agency.launch import finish_site, write_launch_files
from atlas.modules.agency.launchcheck import (
    ITEMS,
    Status,
    launch_audit,
    parse,
    provenance_path,
    report_path,
)
from atlas.modules.agency.ogimage import png_size, share_image


@pytest.fixture(autouse=True)
def _box_deploy_path(monkeypatch):
    """The box deploy path is kept for rollback and tested here; since
    2026-10-30 it refuses live (Vercel only, test_agency_gmail_brand.py)."""
    from atlas.modules.agency import deploy as _deploy
    monkeypatch.setattr(_deploy, "VERCEL_ONLY", False)

MOCKUP = (
    '<!doctype html><html><head><meta charset="utf-8"><title>Harbor Dental</title>'
    "<style>:root{--primary:#225588}</style></head><body>"
    '<section id="hero"><h1>Gentle dentistry</h1>'
    '<img src="hero.jpg" alt="A bright treatment room"><p>Family dentist</p></section>'
    "<footer>mockup footer</footer></body></html>")

LEAD = {"id": 7, "name": "Harbor Dental", "category": "dentist", "phone": "207-555-0100",
        "address": "12 Fore St, Portland, ME 04101", "website": "harbordental.example",
        "status": "audited"}

FULL = {
    "tagline": "Gentle family dentistry on the waterfront",
    "email": "hello@harbordental.example",
    "hours": ["Mo-Fr 08:00-17:00", "Sa 09:00-12:00"],
    "response_time_hours": 4,
    "geo": {"lat": 43.66, "lng": -70.25},
    "form_action": "https://formspree.io/f/abc123",
    "reviews": [{"author": "Sam R.", "text": "Best cleaning I have ever had.",
                 "rating": 5, "source_url": "https://g.page/r/harbor"}],
    "case_studies": [{"title": "A smile rebuilt", "summary": "Six visits, one crown.",
                      "outcome": "Eating comfortably again"}],
    "faqs": [{"q": "Do you take new patients?", "a": "Yes, most weeks."},
             {"q": "Do you see children?", "a": "From age three."}],
    "team_photo": "team.jpg",
    "team_photo_alt": "Dr Lee and the Harbor Dental hygienists",
}


@pytest.fixture(autouse=True)
def _no_analytics(monkeypatch):
    monkeypatch.delenv("POSTHOG_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("POSTHOG_HOST", raising=False)
    monkeypatch.delenv("AGENCY_FORM_ACTION", raising=False)


def _site(tmp_path: Path, raw: dict | None = None, *, lead: dict | None = None,
          mockup: str = MOCKUP, analytics=None) -> Path:
    """A site built the way build_site builds one: the mockup as index.html,
    then launch.finish_site with the client's own data."""
    uploads = tmp_path / "uploads" / "7"
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / "team.jpg").write_bytes(b"\xff\xd8team-photo")
    site = tmp_path / "sites" / "harbor-dental-7"
    site.mkdir(parents=True, exist_ok=True)
    (site / "index.html").write_text(mockup)
    (site / "hero.jpg").write_bytes(b"\xff\xd8hero")
    client = client_data(lead or LEAD, raw, uploads)
    finish_site(site, client, analytics=analytics, form_action=client.form_action,
                colours_given=bool(raw and "colours" in raw))
    return site


def _item(site: Path, n: int):
    return launch_audit(site).items[n - 1]


def _edit(site: Path, name: str, old: str, new: str, count: int = 1) -> None:
    path = site / name
    text = path.read_text()
    assert old in text, f"{old!r} not in {name}"
    path.write_text(text.replace(old, new, count))


def _ld(site: Path, name: str, kind: str) -> dict:
    root = parse((site / name).read_text())
    for script in root.find_all("script"):
        if script.get("type") == "application/ld+json":
            data = json.loads(script.raw())
            if data.get("@type") == kind:
                return data
    raise AssertionError(f"no {kind} JSON-LD in {name}")


# ------------------------------------------------------------ whole-site view

def test_a_site_with_the_clients_full_data_passes_every_item(tmp_path):
    report = launch_audit(_site(tmp_path, {**FULL, "package": "100"}))
    statuses = {i.number: i.status for i in report.items}
    assert [i.number for i in report.items] == [*range(1, 21), 22, 23, 24]
    assert statuses == {n: Status.OK for n in (*range(1, 21), 22, 23, 24)} | {19: Status.NA}
    assert report.ready
    for item in report.items:
        assert item.evidence, f"item {item.number} carries no evidence"


def test_a_site_with_no_client_data_is_deployable_and_names_what_is_missing(tmp_path):
    report = launch_audit(_site(tmp_path))
    assert report.issues == []
    assert report.ready
    # 24: a site nobody has bought publishes nothing for AI assistants yet
    assert {i.number for i in report.needs_client} == {4, 6, 7, 8, 15, 17, 20, 24}


def test_the_report_is_written_beside_the_site_not_inside_it(tmp_path, monkeypatch):
    site = _site(tmp_path)
    from atlas.modules.agency.launchcheck import write_report
    path = write_report(site, launch_audit(site))
    assert path == report_path(site) and path.parent == site.parent
    assert not (site / "LAUNCH_CHECK.md").exists()
    text = path.read_text()
    assert "| 15 | Real reviews | NEEDS_CLIENT |" in text
    assert "## Needed from the client" in text
    assert "UTC" in text        # the time it was read travels with it (invariant 6)
    # The provenance record also sits beside the site, never in the deploy.
    assert provenance_path(site).parent == site.parent


def test_a_missing_site_directory_is_all_issue_never_ok(tmp_path):
    report = launch_audit(tmp_path / "nope")
    assert {i.status for i in report.items} == {Status.ISSUE}
    assert not report.ready


# ------------------------------------------------------------------ 1. 404

def test_1_custom_404_present_and_links_home(tmp_path):
    site = _site(tmp_path)
    assert _item(site, 1).status == Status.OK
    assert 'href="/"' in (site / "404.html").read_text()


def test_1_missing_404_is_an_issue(tmp_path):
    site = _site(tmp_path)
    (site / "404.html").unlink()
    assert _item(site, 1).status == Status.ISSUE


def test_1_a_404_with_no_way_home_is_an_issue(tmp_path):
    site = _site(tmp_path)
    (site / "404.html").write_text("<html><head><title>x</title></head><body>gone"
                                   "</body></html>")
    assert _item(site, 1).status == Status.ISSUE


# ---------------------------------------------------------- 2. CTA above fold

def test_2_the_cta_is_in_the_first_header(tmp_path):
    site = _site(tmp_path)
    item = _item(site, 2)
    assert item.status == Status.OK
    assert "tel:2075550100" in item.evidence and "first <header>" in item.evidence


def test_2_booking_link_wins_the_cta_when_the_client_has_one(tmp_path):
    site = _site(tmp_path, {"booking_url": "https://book.example/harbor"})
    assert "https://book.example/harbor" in _item(site, 2).evidence


def test_2_a_page_whose_first_section_has_no_action_is_an_issue(tmp_path):
    site = _site(tmp_path)
    html = (site / "index.html").read_text()
    html = re.sub(r'<header class="atlas-bar".*?</header>', "", html, flags=re.DOTALL)
    (site / "index.html").write_text(html)
    assert _item(site, 2).status == Status.ISSUE


# ------------------------------------------------------- 3. internal links

def test_3_every_page_links_to_another_and_every_link_resolves(tmp_path):
    assert _item(_site(tmp_path, FULL), 3).status == Status.OK


def test_3_a_broken_internal_link_is_an_issue(tmp_path):
    site = _site(tmp_path)
    _edit(site, "index.html", "</body>", '<a href="/about.html">About</a></body>')
    item = _item(site, 3)
    assert item.status == Status.ISSUE and "about.html" in item.evidence


def test_3_an_orphan_page_is_an_issue(tmp_path):
    site = _site(tmp_path)
    (site / "extra.html").write_text(
        '<html><head><title>Extra</title></head><body><a href="index.html">Home</a>'
        "</body></html>")
    item = _item(site, 3)
    assert item.status == Status.ISSUE and "extra.html" in item.evidence


# ------------------------------------------------------------ 4. thank-you

def test_4_the_form_redirects_to_the_thank_you_page(tmp_path):
    site = _site(tmp_path, FULL)
    assert _item(site, 4).status == Status.OK
    contact = (site / "contact.html").read_text()
    assert 'name="_next" value="https://harbordental.example/thank-you.html"' in contact
    assert 'action="https://formspree.io/f/abc123"' in contact
    thanks = (site / "thank-you.html").read_text()
    assert '<meta name="robots" content="noindex">' in thanks


def test_4_without_a_form_service_there_is_no_form_and_it_needs_the_client(tmp_path):
    site = _site(tmp_path)
    assert not (site / "thank-you.html").exists()
    assert "<form" not in (site / "contact.html").read_text()
    assert _item(site, 4).status == Status.NEEDS_CLIENT


async def test_4_an_agency_wide_form_endpoint_is_used_when_the_client_has_none(
        tmp_path, monkeypatch):
    monkeypatch.setattr(build, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setenv("AGENCY_FORM_ACTION", "https://forms.example/in")
    lead = {"id": 7, "name": "Harbor Dental", "phone": "555"}
    sold = {"package": "500"}
    _client, form_action = await build._client_for(7, lead, sold, None, None)
    assert form_action == "https://forms.example/in"
    # The client's own form service wins over the agency-wide one.
    _client, form_action = await build._client_for(
        7, lead, {**sold, "form_action": "https://formspree.io/f/own"}, None, None)
    assert form_action == "https://formspree.io/f/own"
    # An unsold preview gets neither.
    _client, form_action = await build._client_for(7, lead, None, None, None)
    assert form_action is None


def test_4_a_form_that_does_not_redirect_to_thank_you_is_an_issue(tmp_path):
    site = _site(tmp_path, FULL)
    _edit(site, "contact.html", 'name="_next"', 'name="_ignored"')
    assert _item(site, 4).status == Status.ISSUE


# ---------------------------------------------------------- 5. breadcrumbs

def test_5_every_sub_page_has_a_trail_and_valid_breadcrumblist(tmp_path):
    site = _site(tmp_path, FULL)
    assert _item(site, 5).status == Status.OK
    ld = _ld(site, "contact.html", "BreadcrumbList")
    assert [e["position"] for e in ld["itemListElement"]] == [1, 2]
    assert ld["itemListElement"][1]["item"] == "https://harbordental.example/contact.html"
    assert 'aria-label="Breadcrumb"' in (site / "terms.html").read_text()


def test_5_a_sub_page_without_breadcrumb_json_ld_is_an_issue(tmp_path):
    site = _site(tmp_path)
    html = (site / "terms.html").read_text()
    html = re.sub(r'<script type="application/ld\+json">\{[^<]*BreadcrumbList[^<]*'
                  r"</script>", "", html)
    (site / "terms.html").write_text(html)
    item = _item(site, 5)
    assert item.status == Status.ISSUE and "terms.html" in item.evidence


def test_5_breadcrumb_positions_out_of_order_are_an_issue(tmp_path):
    site = _site(tmp_path)
    _edit(site, "privacy.html", '"position": 2', '"position": 7')
    assert _item(site, 5).status == Status.ISSUE


# --------------------------------------------------------- 6. case studies

def test_6_client_case_studies_render_and_pass(tmp_path):
    site = _site(tmp_path, FULL)
    assert _item(site, 6).status == Status.OK
    assert "A smile rebuilt" in (site / "index.html").read_text()


def test_6_no_case_studies_means_no_section_and_needs_client(tmp_path):
    site = _site(tmp_path)
    assert 'id="case-studies"' not in (site / "index.html").read_text()
    assert _item(site, 6).status == Status.NEEDS_CLIENT


def test_6_a_case_study_the_client_never_gave_is_an_issue(tmp_path):
    site = _site(tmp_path, FULL)
    _edit(site, "index.html", "<h3>A smile rebuilt</h3>", "<h3>We fixed 500 smiles</h3>")
    assert _item(site, 6).status == Status.ISSUE


# ------------------------------------------------------------------ 7. FAQs

def test_7_five_or_more_faqs_with_matching_faqpage_json_ld(tmp_path):
    site = _site(tmp_path, FULL)
    assert _item(site, 7).status == Status.OK
    ld = _ld(site, "index.html", "FAQPage")
    assert len(ld["mainEntity"]) >= 5
    assert ld["mainEntity"][0]["name"] == "Do you take new patients?"   # client's first
    for q in ld["mainEntity"]:
        assert q["@type"] == "Question" and q["acceptedAnswer"]["@type"] == "Answer"
        assert q["acceptedAnswer"]["text"].strip()


def test_7_fewer_than_five_faqs_needs_the_client(tmp_path):
    item = _item(_site(tmp_path), 7)
    assert item.status == Status.NEEDS_CLIENT
    assert "needs 2 more" in item.evidence


def test_7_derived_answers_only_restate_facts_on_record(tmp_path):
    ld = _ld(_site(tmp_path), "index.html", "FAQPage")
    answers = " ".join(q["acceptedAnswer"]["text"] for q in ld["mainEntity"])
    assert "207-555-0100" in answers and "12 Fore St" in answers
    assert not re.search(r"\bwithin \d", answers)     # no response time on record
    assert "open" not in answers.lower()               # no hours on record


def test_7_faqpage_json_ld_that_does_not_parse_is_an_issue(tmp_path):
    site = _site(tmp_path, FULL)
    _edit(site, "index.html", '{"@context": "https://schema.org", "@type": "FAQPage"',
          '{"@context": "https://schema.org", "@type": "FAQPage",,')
    assert _item(site, 7).status == Status.ISSUE
    assert _item(site, 17).status == Status.ISSUE       # invalid JSON-LD anywhere


def test_7_a_faqpage_question_not_on_the_page_is_an_issue(tmp_path):
    site = _site(tmp_path, FULL)
    _edit(site, "index.html", "<dt>Do you see children?</dt>", "<dt>Something else</dt>")
    assert _item(site, 7).status == Status.ISSUE


# -------------------------------------------------------- 8. response time

def test_8_the_clients_stated_response_time_sits_beside_the_cta(tmp_path):
    site = _site(tmp_path, FULL)
    item = _item(site, 8)
    assert item.status == Status.OK and "We reply within 4 hours." in item.evidence
    assert "We reply within 4 hours." in (site / "contact.html").read_text()


def test_8_without_a_stated_time_the_promise_carries_no_number(tmp_path):
    site = _site(tmp_path)
    html = (site / "index.html").read_text()
    assert "as soon as we can" in html
    assert not re.search(r"within \d", html)
    assert _item(site, 8).status == Status.NEEDS_CLIENT


def test_8_sub_hour_times_read_as_minutes():
    assert sitekit.response_time_text(0.5) == "30 minutes"
    assert sitekit.response_time_text(1) == "1 hour"
    assert sitekit.response_time_text(24) == "24 hours"


def test_8_no_promise_at_all_is_an_issue(tmp_path):
    site = _site(tmp_path)
    for name in ("index.html", "contact.html"):
        html = (site / name).read_text()
        (site / name).write_text(re.sub(r'<p class="atlas-promise"[^>]*>.*?</p>', "",
                                        html))
    assert _item(site, 8).status == Status.ISSUE


# ----------------------------------------------------------- 9. sticky CTA

def test_9_sticky_cta_is_fixed_on_small_viewports_only_and_pads_the_body(tmp_path):
    site = _site(tmp_path)
    html = (site / "index.html").read_text()
    css = re.search(r'<style data-atlas="launch">(.*?)</style>', html, re.DOTALL).group(1)
    assert ".atlas-sticky{display:none}" in css
    media = css[css.index("@media (max-width: 640px)"):]
    assert "position:fixed" in media and "bottom:0" in media
    assert "body{padding-bottom:" in media         # never sits over the content
    assert 'aria-label="Quick contact"' in html
    assert "min-height:48px" in media              # a thumb-sized target
    assert _item(site, 9).status == Status.OK


def test_9_a_sticky_bar_that_covers_content_is_an_issue(tmp_path):
    site = _site(tmp_path)
    _edit(site, "index.html", "body{padding-bottom:", "main{padding-bottom:")
    assert _item(site, 9).status == Status.ISSUE


def test_9_no_sticky_bar_is_an_issue(tmp_path):
    site = _site(tmp_path)
    _edit(site, "index.html", "position:fixed", "position:static")
    assert _item(site, 9).status == Status.ISSUE


# ------------------------------------------------------------- 10. robots

def test_10_robots_allows_crawling_and_names_the_sitemap(tmp_path):
    site = _site(tmp_path)
    assert _item(site, 10).status == Status.OK
    assert "Sitemap: https://harbordental.example/sitemap.xml" in (
        site / "robots.txt").read_text()
    sitemap = (site / "sitemap.xml").read_text()
    assert "<loc>https://harbordental.example/contact.html</loc>" in sitemap
    assert "thank-you" not in sitemap and "404" not in sitemap


def test_10_missing_or_blocking_robots_is_an_issue(tmp_path):
    site = _site(tmp_path)
    (site / "robots.txt").write_text("User-agent: *\nDisallow: /\n")
    assert _item(site, 10).status == Status.ISSUE
    (site / "robots.txt").unlink()
    assert _item(site, 10).status == Status.ISSUE


# ------------------------------------------------- 11 / 12. titles and meta

def test_11_12_every_page_has_its_own_title_and_description(tmp_path):
    site = _site(tmp_path, FULL)
    titles, metas = set(), set()
    pages = sorted(site.glob("*.html"))
    for page in pages:
        root = parse(page.read_text())
        titles.add(root.find_all("title")[0].text())
        metas.add(next(m.get("content") for m in root.find_all("meta")
                       if m.get("name") == "description"))
    assert len(titles) == len(pages) and len(metas) == len(pages)
    assert "" not in titles and "" not in metas
    assert _item(site, 11).status == Status.OK and _item(site, 12).status == Status.OK


def test_11_the_mockups_own_title_is_kept_not_doubled(tmp_path):
    html = (_site(tmp_path) / "index.html").read_text()
    assert html.count("<title>") == 1 and "<title>Harbor Dental</title>" in html


def test_11_a_duplicate_title_is_an_issue(tmp_path):
    site = _site(tmp_path)
    _edit(site, "terms.html", "<title>Terms | Harbor Dental</title>",
          "<title>Privacy | Harbor Dental</title>")
    item = _item(site, 11)
    assert item.status == Status.ISSUE and "terms.html" in item.evidence


def test_12_a_missing_meta_description_is_an_issue(tmp_path):
    site = _site(tmp_path)
    html = (site / "contact.html").read_text()
    (site / "contact.html").write_text(re.sub(r'<meta name="description"[^>]*>', "", html))
    item = _item(site, 12)
    assert item.status == Status.ISSUE and "contact.html" in item.evidence


# --------------------------------------------------------- 13. share image

def test_13_a_1200x630_share_image_is_generated_when_the_client_gave_none(tmp_path):
    site = _site(tmp_path)
    assert png_size((site / "og-image.png").read_bytes()) == (1200, 630)
    html = (site / "index.html").read_text()
    assert '<meta property="og:image" content="https://harbordental.example/og-image.png">' \
        in html
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    assert _item(site, 13).status == Status.OK


def test_13_the_generated_image_is_deterministic_and_uses_the_colours():
    colours = {"primary": "#225588", "background": "#ffffff", "text": "#1b2430"}
    a = share_image("Harbor Dental", "Gentle dentistry", colours)
    assert a == share_image("Harbor Dental", "Gentle dentistry", colours)
    assert a != share_image("Harbor Dental", "Gentle dentistry",
                            {**colours, "primary": "#aa2200"})
    assert png_size(a) == (1200, 630)
    assert share_image("Café Ñandú — 24/7!", None, colours)[:8] == b"\x89PNG\r\n\x1a\n"


def test_13_the_clients_own_share_image_is_used_when_given(tmp_path):
    uploads = tmp_path / "uploads" / "7"
    uploads.mkdir(parents=True)
    (uploads / "share.png").write_bytes(share_image("x", None, {
        "primary": "#000000", "background": "#ffffff", "text": "#000000"}))
    site = _site(tmp_path, {"og_image": "share.png"})
    assert (site / "og-image.png").read_bytes() == (uploads / "share.png").read_bytes()


def test_13_without_the_domain_the_share_image_needs_the_client(tmp_path):
    site = _site(tmp_path, lead={**LEAD, "website": None})
    item = _item(site, 13)
    assert item.status == Status.NEEDS_CLIENT and "absolute" in item.evidence


def test_13_no_share_tags_is_an_issue(tmp_path):
    site = _site(tmp_path)
    html = (site / "index.html").read_text()
    (site / "index.html").write_text(re.sub(r'<meta property="og:image"[^>]*>', "", html))
    assert _item(site, 13).status == Status.ISSUE


# --------------------------------------------------------- 14. map + route

def test_14_address_gives_a_map_link_and_get_directions(tmp_path):
    site = _site(tmp_path)
    html = (site / "contact.html").read_text()
    assert ("https://www.google.com/maps/dir/?api=1&amp;destination="
            "12+Fore+St%2C+Portland%2C+ME+04101") in html
    assert ">Get directions</a>" in html
    assert "<iframe" not in html          # no Google cookies on every visit
    assert _item(site, 14).status == Status.OK


def test_14_no_address_means_no_map_and_needs_the_client(tmp_path):
    site = _site(tmp_path, lead={**LEAD, "address": None})
    assert "google.com/maps" not in (site / "index.html").read_text()
    assert _item(site, 14).status == Status.NEEDS_CLIENT


def test_14_an_address_without_directions_is_an_issue(tmp_path):
    site = _site(tmp_path)
    for name in ("index.html", "contact.html"):
        html = (site / name).read_text()
        (site / name).write_text(html.replace("maps/dir/", "maps/x/"))
    assert _item(site, 14).status == Status.ISSUE


# ------------------------------------------------------------ 15. reviews

def test_15_the_clients_reviews_render_word_for_word_with_rating_and_source(tmp_path):
    site = _site(tmp_path, FULL)
    html = (site / "index.html").read_text()
    assert "<blockquote>Best cleaning I have ever had.<footer>— Sam R., rated 5/5, " \
        '<a href="https://g.page/r/harbor" rel="noopener">source</a></footer>' in html
    assert _item(site, 15).status == Status.OK


def test_15_no_reviews_given_means_none_rendered_anywhere(tmp_path):
    site = _site(tmp_path)
    for page in site.glob("*.html"):
        html = page.read_text()
        assert "<blockquote" not in html and 'id="reviews"' not in html
        assert "aggregateRating" not in html
    assert _item(site, 15).status == Status.NEEDS_CLIENT


def test_15_a_testimonial_in_the_mockup_is_an_issue(tmp_path):
    invented = MOCKUP.replace(
        "<footer>", '<section class="testimonials"><blockquote>Five stars, amazing!'
        "<footer>— Happy patient</footer></blockquote></section><footer>")
    item = _item(_site(tmp_path, mockup=invented), 15)
    assert item.status == Status.ISSUE and "no client supplied" in item.evidence


def test_15_a_testimonial_that_is_not_one_of_the_clients_is_an_issue(tmp_path):
    site = _site(tmp_path, FULL)
    _edit(site, "index.html", "Best cleaning I have ever had.", "Best dentist in Maine!")
    assert _item(site, 15).status == Status.ISSUE


def test_15_rating_json_ld_is_an_issue(tmp_path):
    site = _site(tmp_path, FULL)
    _edit(site, "index.html", '"@type": "LocalBusiness",',
          '"@type": "LocalBusiness", "aggregateRating": {"ratingValue": 4.9},')
    assert _item(site, 15).status == Status.ISSUE


def test_15_client_reviews_are_validated_not_repaired(tmp_path):
    raw = {"reviews": [
        {"author": "Ann", "text": "Lovely.", "rating": 9, "source_url": "javascript:x"},
        {"text": "No author"},
        {"author": "Bo", "text": "Kind staff.", "rating": 4.5}]}
    client = client_data(LEAD, raw, tmp_path)
    assert [(r.author, r.rating, r.source_url) for r in client.reviews] == [
        ("Ann", None, None), ("Bo", 4.5, None)]
    assert len(client.problems) == 3


def test_15_build_site_takes_no_review_or_case_study_argument():
    """The honesty rule, structurally: reviews reach a page only from a file
    a person put in the lead's uploads folder. A model picks build_site's
    arguments (invariant 2), so none of them may carry a review."""
    params = set(inspect.signature(build.build_site).parameters)
    action = next(a for a in agency.MODULE.actions() if a.name == "agency.build_site")
    for forbidden in ("reviews", "case_studies", "team_photo", "response_time_hours",
                      "hours", "testimonials"):
        assert forbidden not in params
        assert forbidden not in action.params


async def test_15_a_store_draft_cannot_inject_reviews(monkeypatch, tmp_path):
    """think() writes store copy; a "reviews" key in its JSON goes nowhere,
    and a testimonial smuggled into the FAQ is escaped text, not a quote."""
    monkeypatch.setenv("AGENCY_GETLAYERS_LICENCE_OK", "true")
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "sites")
    uploads = tmp_path / "uploads"
    monkeypatch.setattr(build, "UPLOADS_DIR", uploads)
    (uploads / "4").mkdir(parents=True)
    (uploads / "4" / "vase.jpg").write_bytes(b"img")
    lead = {"id": 4, "name": "Kiln", "category": "pottery", "phone": "555-0101"}
    monkeypatch.setattr(_store, "get_lead", _async(lead))
    monkeypatch.setattr(_store, "get_site", _async(None))
    monkeypatch.setattr(_store, "insert_site", _async(9))
    monkeypatch.setattr(_store, "set_lead_status", _async(None))
    monkeypatch.setattr(build, "McpClient", _FakeMcp)

    async def fake_think(need, system, messages, max_tokens=2000):
        return Completion(text=json.dumps({
            "headline": "A vase", "subheadline": "Hand thrown",
            "reviews": [{"author": "Fake", "text": "I love it!"}],
            "testimonials": ["Best vase ever"],
            "faq": [{"q": "Is it good?", "a": "<blockquote>Best vase ever</blockquote>"}],
        }), provider="t", model="t")
    monkeypatch.setattr(build, "think", fake_think)

    result = await build.build_site(4, kind="store", product_photo="vase.jpg",
                                    product_description="a vase")
    assert result.ok, result.detail
    html = (tmp_path / "sites" / "kiln-4" / "index.html").read_text()
    assert "I love it!" not in html and "<blockquote" not in html
    assert 'id="reviews"' not in html
    assert result.evidence["launch_check"]["issues"] == []
    assert "15 Real reviews" in result.evidence["launch_check"]["needs_client"]


# ---------------------------------------------------------------- 16. alt

def test_16_every_image_has_alt_text(tmp_path):
    site = _site(tmp_path, FULL)
    assert 'alt="Dr Lee and the Harbor Dental hygienists"' in (
        site / "index.html").read_text()
    assert _item(site, 16).status == Status.OK


def test_16_missing_or_empty_alt_is_an_issue(tmp_path):
    for img in ('<img src="a.jpg">', '<img src="a.jpg" alt="  ">'):
        site = _site(tmp_path / img[-8:-1].replace(" ", "_").replace('"', ""),
                     mockup=MOCKUP.replace("<p>Family", f"{img}<p>Family"))
        item = _item(site, 16)
        assert item.status == Status.ISSUE and "a.jpg" in item.evidence


def test_16_a_decorative_image_marked_presentation_passes(tmp_path):
    site = _site(tmp_path, mockup=MOCKUP.replace(
        "<p>Family", '<img src="wave.svg" alt="" role="presentation"><p>Family'))
    assert _item(site, 16).status == Status.OK


# ------------------------------------------------------- 17. LocalBusiness

def test_17_local_business_json_ld_is_valid_with_only_known_fields(tmp_path):
    site = _site(tmp_path, FULL)
    ld = _ld(site, "index.html", "LocalBusiness")
    assert ld["@context"] == "https://schema.org"
    assert ld["name"] == "Harbor Dental"
    assert ld["address"] == "12 Fore St, Portland, ME 04101"
    assert ld["telephone"] == "207-555-0100"
    assert ld["openingHours"] == ["Mo-Fr 08:00-17:00", "Sa 09:00-12:00"]
    assert ld["url"] == "https://harbordental.example/"
    assert ld["geo"] == {"@type": "GeoCoordinates", "latitude": 43.66,
                         "longitude": -70.25}
    assert "aggregateRating" not in ld and "review" not in ld
    assert _item(site, 17).status == Status.OK


def test_17_unknown_fields_are_left_out_never_filled(tmp_path):
    site = _site(tmp_path)
    ld = _ld(site, "index.html", "LocalBusiness")
    assert "geo" not in ld and "openingHours" not in ld and "email" not in ld
    item = _item(site, 17)
    assert item.status == Status.NEEDS_CLIENT and "openingHours" in item.evidence


def test_17_json_ld_cannot_be_broken_out_of_by_a_name(tmp_path):
    site = _site(tmp_path, {"name": "Tom </script><script>alert(1)</script>"})
    html = (site / "index.html").read_text()
    assert "<script>alert(1)" not in html
    assert _ld(site, "index.html", "LocalBusiness")["name"].startswith("Tom </script>")
    assert _item(site, 17).status in (Status.OK, Status.NEEDS_CLIENT)


def test_17_no_local_business_is_an_issue(tmp_path):
    site = _site(tmp_path)
    _edit(site, "index.html", '"@type": "LocalBusiness"', '"@type": "Thing"')
    assert _item(site, 17).status == Status.ISSUE


# -------------------------------------------------------- 18. privacy+terms

def test_18_privacy_and_terms_exist_and_are_linked_from_home(tmp_path):
    site = _site(tmp_path)
    html = (site / "index.html").read_text()
    assert 'href="privacy.html"' in html and 'href="terms.html"' in html
    assert "does not use tracking" in (site / "privacy.html").read_text()
    assert _item(site, 18).status == Status.OK


def test_18_privacy_names_the_form_service_when_there_is_a_form(tmp_path):
    privacy = (_site(tmp_path, FULL) / "privacy.html").read_text()
    assert "formspree.io" in privacy and "no forms" not in privacy


def test_18_missing_terms_is_an_issue(tmp_path):
    site = _site(tmp_path)
    (site / "terms.html").unlink()
    assert _item(site, 18).status == Status.ISSUE


# ------------------------------------------------------------ 19. analytics

def test_19_posthog_is_off_when_unset(tmp_path):
    assert sitekit.analytics_from_env() is None
    site = _site(tmp_path)
    for page in site.glob("*.html"):
        assert "posthog" not in page.read_text().lower()
    assert _item(site, 19).status == Status.NA


def test_19_posthog_on_every_page_privacy_respecting_and_disclosed(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTHOG_PUBLIC_KEY", "phc_TestProjectKey123")
    monkeypatch.setenv("POSTHOG_HOST", "https://us.posthog.com")
    analytics = sitekit.analytics_from_env()
    assert analytics == sitekit.Analytics("phc_TestProjectKey123", "https://us.i.posthog.com")
    site = _site(tmp_path, analytics=analytics)
    for page in site.glob("*.html"):
        html = page.read_text()
        assert 'posthog.init("phc_TestProjectKey123"' in html
        assert '"persistence":"memory"' in html.replace(" ", "")
        assert '"disable_session_recording":true' in html.replace(" ", "")
    assert "PostHog" in (site / "privacy.html").read_text()
    assert _item(site, 19).status == Status.OK
    assert _item(site, 18).status == Status.OK


def test_19_only_a_public_phc_key_turns_posthog_on(monkeypatch):
    for key in ("phx_personal_secret_key", "", "phc_", "sk_live_123"):
        monkeypatch.setenv("POSTHOG_PUBLIC_KEY", key)
        assert sitekit.analytics_from_env() is None
    monkeypatch.setenv("POSTHOG_PUBLIC_KEY", "phc_abcdefgh123")
    monkeypatch.setenv("POSTHOG_HOST", "http://insecure.example")
    assert sitekit.analytics_from_env() is None
    monkeypatch.delenv("POSTHOG_HOST")
    assert sitekit.analytics_from_env().host == "https://us.i.posthog.com"


def test_19_key_set_but_site_built_without_it_is_an_issue(tmp_path, monkeypatch):
    site = _site(tmp_path)
    monkeypatch.setenv("POSTHOG_PUBLIC_KEY", "phc_abcdefgh123")
    assert _item(site, 19).status == Status.ISSUE


def test_19_a_google_tag_in_the_mockup_is_an_issue(tmp_path):
    site = _site(tmp_path, mockup=MOCKUP.replace(
        "</head>", '<script async src="https://www.googletagmanager.com/gtag/js">'
        "</script></head>"))
    item = _item(site, 19)
    assert item.status == Status.ISSUE and "googletagmanager" in item.evidence


def test_19_privacy_that_denies_tracking_while_posthog_runs_is_an_issue(
        tmp_path, monkeypatch):
    monkeypatch.setenv("POSTHOG_PUBLIC_KEY", "phc_abcdefgh123")
    site = _site(tmp_path, analytics=sitekit.analytics_from_env())
    (site / "privacy.html").write_text(
        (site / "privacy.html").read_text().replace(
            "This site counts visits with PostHog", "This site does not use tracking"))
    assert _item(site, 18).status == Status.ISSUE


# ---------------------------------------------------------------- 20. team

def test_20_the_clients_team_photo_is_copied_and_shown(tmp_path):
    site = _site(tmp_path, FULL)
    assert (site / "team.jpg").read_bytes() == b"\xff\xd8team-photo"
    assert _item(site, 20).status == Status.OK


def test_20_no_team_photo_means_no_section_and_needs_the_client(tmp_path):
    site = _site(tmp_path)
    assert 'id="team"' not in (site / "index.html").read_text()
    assert _item(site, 20).status == Status.NEEDS_CLIENT


def test_20_a_stock_our_team_image_in_the_mockup_is_an_issue(tmp_path):
    site = _site(tmp_path, mockup=MOCKUP.replace(
        "<p>Family", '<img src="https://images.example/stock.jpg" alt="Our team">'
        "<p>Family"))
    assert _item(site, 20).status == Status.ISSUE


def test_20_a_team_photo_outside_the_uploads_folder_is_refused(tmp_path):
    outside = tmp_path / "elsewhere.jpg"
    outside.write_bytes(b"x")
    client = client_data(LEAD, {"team_photo": str(outside)}, tmp_path / "uploads" / "7")
    assert client.team_photo is None
    assert any("uploads folder" in p or "not found" in p for p in client.problems)


# ---------------------------------------------------- never over client edits

def test_a_rebuild_rewrites_atlas_files_but_never_a_client_edit(tmp_path):
    site = _site(tmp_path)
    (site / "terms.html").write_text("<html>the client's own terms</html>")
    finish_site(site, client_data(LEAD, FULL, tmp_path / "uploads" / "7"),
                analytics=None, form_action="https://formspree.io/f/abc123")
    assert (site / "terms.html").read_text() == "<html>the client's own terms</html>"
    # An unedited ATLAS page is refreshed with the new data.
    assert "We reply within 4 hours." in (site / "contact.html").read_text()
    # And the edited file stays protected on the next rebuild too.
    finish_site(site, client_data(LEAD, None, tmp_path / "uploads" / "7"),
                analytics=None, form_action=None)
    assert (site / "terms.html").read_text() == "<html>the client's own terms</html>"


def test_write_launch_files_keeps_a_file_it_has_no_record_of(tmp_path):
    (tmp_path / "robots.txt").write_text("mine")
    written = write_launch_files(tmp_path, {"robots.txt": "new", "x.png": b"\x89"})
    assert (tmp_path / "robots.txt").read_text() == "mine"
    assert written == ["x.png"] and (tmp_path / "x.png").read_bytes() == b"\x89"


# ----------------------------------------------------------- client.json

def test_a_client_json_that_does_not_parse_stops_the_build(tmp_path):
    (tmp_path / "client.json").write_text("{not json")
    raw, reason = read_client_file(tmp_path)
    assert raw is None and "client.json" in reason
    assert read_client_file(tmp_path / "missing") == (None, None)


# ---------------------------------------------------- build + deploy gate

class _FakeMcp:
    def __init__(self, *a, **kw) -> None:
        pass

    async def initialize(self):
        return {}, None

    async def list_tools(self):
        return [{"name": n} for n in ("getlayers_search", "getlayers_compositions",
                                       "getlayers_materialize")], None

    async def call(self, name, arguments):
        return f"<result from {name}>", None


def _async(value):
    async def fake(*a, **kw):
        return value
    return fake


def _brochure(monkeypatch, tmp_path, raw: dict | None, mockup: str = MOCKUP):
    monkeypatch.setattr(build, "SITES_DIR", tmp_path / "sites")
    uploads = tmp_path / "uploads"
    monkeypatch.setattr(build, "UPLOADS_DIR", uploads)
    (uploads / "7").mkdir(parents=True, exist_ok=True)
    (uploads / "7" / "team.jpg").write_bytes(b"\xff\xd8team")
    if raw is not None:
        (uploads / "7" / "client.json").write_text(json.dumps(raw))
    mock = tmp_path / "mockup.html"
    mock.write_text(mockup)
    # what agency.mockup saves beside its page; build_site renders from it
    mock.with_suffix(".copy.json").write_text(json.dumps({"family": "trades", "copy": {}}))
    monkeypatch.setattr(_store, "get_lead", _async(LEAD))
    monkeypatch.setattr(_store, "latest_mockup_path", _async(str(mock)))
    monkeypatch.setattr(_store, "get_site", _async(None))
    monkeypatch.setattr(_store, "insert_site", _async(3))
    monkeypatch.setattr(_store, "set_lead_status", _async(None))


async def test_build_site_runs_the_launch_check_and_writes_the_report_beside(
        monkeypatch, tmp_path):
    _brochure(monkeypatch, tmp_path, FULL)
    result = await build.build_site(7)
    assert result.ok, result.detail
    site = tmp_path / "sites" / "harbor-dental-7"
    assert result.evidence["launch_check"]["ready"] is True
    assert "ready to deploy" in result.detail
    assert Path(result.evidence["launch_report"]) == report_path(site)
    assert report_path(site).exists() and not (site / "LAUNCH_CHECK.md").exists()
    # The recorded index hash is of the finished page, so the read-back holds.
    assert result.evidence["index_sha"] == build.index_sha(site)


async def test_build_site_refuses_a_broken_client_json(monkeypatch, tmp_path):
    _brochure(monkeypatch, tmp_path, None)
    (tmp_path / "uploads" / "7" / "client.json").write_text("[1, 2")
    assert not (await build.build_site(7)).ok
    assert not (await build.build_site_dry(7)).ok


async def test_build_site_rollback_removes_the_report_and_provenance(monkeypatch, tmp_path):
    _brochure(monkeypatch, tmp_path, None)
    monkeypatch.setattr(_store, "delete_site", _async(None))
    result = await build.build_site(7)
    site = tmp_path / "sites" / "harbor-dental-7"
    assert report_path(site).exists() and provenance_path(site).exists()
    undo = await build.build_site_rollback(result.rollback_token)
    assert undo.ok
    assert not site.exists() and not report_path(site).exists()
    assert not provenance_path(site).exists()


async def test_deploy_refuses_a_site_with_an_issue_and_never_runs_rsync(
        monkeypatch, tmp_path):
    _brochure(monkeypatch, tmp_path, None)
    result = await build.build_site(7)
    assert result.ok
    site = tmp_path / "sites" / "harbor-dental-7"
    # An image without alt text added to the built page by hand: the deploy
    # reads the files back, so it is the files that are refused.
    _edit(site, "index.html", "</main>", '<img src="x.jpg"></main>')
    monkeypatch.setattr(_store, "get_site", _async({"slug": "harbor-dental-7",
                                                    "path": str(site)}))
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", "deployhost")
    monkeypatch.setenv("AGENCY_DEPLOY_ROOT", "/var/www/clients")

    async def no_rsync(argv):
        raise AssertionError("rsync must not run for a site with an ISSUE")
    monkeypatch.setattr(deploy, "_run", no_rsync)

    preview = await deploy.deploy_dry("harbor-dental-7")
    assert not preview.ok
    assert "16 Alt text on every image" in preview.detail
    run = await deploy.deploy_site("harbor-dental-7")
    assert not run.ok and "launch check failed" in run.detail


async def test_deploy_card_lists_needs_client_and_does_not_block(monkeypatch, tmp_path):
    _brochure(monkeypatch, tmp_path, None)
    await build.build_site(7)
    site = tmp_path / "sites" / "harbor-dental-7"
    monkeypatch.setattr(_store, "get_site", _async({"slug": "harbor-dental-7",
                                                    "path": str(site)}))
    monkeypatch.setenv("AGENCY_DEPLOY_HOST", "deployhost")
    monkeypatch.setenv("AGENCY_DEPLOY_ROOT", "/var/www/clients")
    preview = await deploy.deploy_dry("harbor-dental-7")
    assert preview.ok, preview.detail
    assert "needed from client" in preview.detail
    assert "15 Real reviews" in preview.detail and "20 Team photo" in preview.detail
    assert "0 ISSUE" in preview.detail and "UTC" in preview.detail


async def test_rolling_back_a_rebuild_leaves_the_restored_site_deployable(
        monkeypatch, tmp_path):
    """The provenance record goes back with the files it describes: without
    it the restored build's real client reviews would read as invented and
    every launch page as someone else's, frozen for good."""
    _brochure(monkeypatch, tmp_path, FULL)
    site = tmp_path / "sites" / "harbor-dental-7"
    assert (await build.build_site(7)).ok
    row = {"slug": "harbor-dental-7", "path": str(site), "variants": None}
    monkeypatch.setattr(_store, "get_site", _async(row))
    monkeypatch.setattr(_store, "set_site_variants", _async(None))

    changed = {**FULL, "reviews": [{"author": "Kim", "text": "New review text."}],
               "response_time_hours": 2}
    (tmp_path / "uploads" / "7" / "client.json").write_text(json.dumps(changed))
    rebuild = await build.build_site(7)
    assert rebuild.ok
    assert "within 2 hours" in (site / "contact.html").read_text()

    assert (await build.build_site_rollback(rebuild.rollback_token)).ok
    report = launch_audit(site)
    assert report.ready, report.summary()
    statuses = {i.number: i.status for i in report.items}
    assert statuses[15] == statuses[6] == statuses[20] == Status.OK
    assert "Best cleaning I have ever had." in (site / "index.html").read_text()
    assert "Ready to deploy: yes" in report_path(site).read_text()

    # ATLAS's own pages are still ATLAS's: the next rebuild refreshes them.
    assert "within 4 hours" in (site / "contact.html").read_text()
    assert (await build.build_site(7)).ok
    assert "within 2 hours" in (site / "contact.html").read_text()


async def test_a_site_built_before_the_20_items_is_upgraded_not_frozen(
        monkeypatch, tmp_path):
    """Sites built on 2026-09-28 carry robots, sitemap, privacy and 404 from
    the old generator and no provenance record. Byte-identical ones are
    ATLAS's and get replaced; a hand-edited one is still left alone."""
    from atlas.modules.agency.launch import legacy_launch_files
    _brochure(monkeypatch, tmp_path, None)
    site = tmp_path / "sites" / "harbor-dental-7"
    site.mkdir(parents=True)
    old = legacy_launch_files(LEAD["name"], LEAD["website"], LEAD["phone"], None)
    for name, content in old.items():
        (site / name).write_text(content.replace("<lastmod>x</lastmod>",
                                                 "<lastmod>2026-09-28</lastmod>"))
    (site / "404.html").write_text(old["404.html"].replace("mistyped", "mis-typed"))

    result = await build.build_site(7)
    assert result.ok
    for name in ("privacy.html", "sitemap.xml"):
        assert (site / name).read_text() != old[name].replace(
            "<lastmod>x</lastmod>", "<lastmod>2026-09-28</lastmod>"), name
    assert 'aria-label="Breadcrumb"' in (site / "privacy.html").read_text()
    assert "contact.html" in (site / "sitemap.xml").read_text()
    # robots.txt comes out the same, but it is recorded as ATLAS's now.
    recorded = json.loads(provenance_path(site).read_text())["files"]
    assert {"privacy.html", "sitemap.xml", "robots.txt"} <= set(recorded)
    assert "404.html" not in recorded
    assert "mis-typed" in (site / "404.html").read_text()      # the hand edit stays
    assert "404.html" in result.detail                          # and is named
    issues = {i.number for i in launch_audit(site).issues}
    assert issues <= {11, 12}          # only what the kept, hand-edited 404 lacks


def test_launch_audit_is_exported_and_the_list_has_twenty_items():
    assert agency.launch_audit is launch_audit
    assert len(ITEMS) == 20


def test_client_data_defaults_are_honest():
    c = ClientData(name="X")
    assert c.reviews == () and c.case_studies == () and c.team_photo is None
    assert c.response_time_hours is None and c.hours == () and c.geo is None


# ------------------------------------------------- 23. Self-hosted media

def test_23_a_page_whose_media_is_its_own_passes(tmp_path):
    site = _site(tmp_path, FULL)
    item = launch_audit(site).items[-2]
    assert item.number == 23 and item.status == Status.OK
    assert "on this site and present" in item.evidence


def test_23_a_hotlinked_image_or_video_is_an_issue(tmp_path):
    for tag in ('<img src="https://images.unsplash.com/photo-1?w=800" alt="A van">',
                '<video data-src="https://storage.getlayers.ai/x.mp4" muted></video>',
                ('<picture><source srcset="https://cdn.example/a.avif 800w" '
                 'type="image/avif"><img src="hero.jpg" alt="x"></picture>')):
        site = _site(tmp_path / str(abs(hash(tag))),
                     mockup=MOCKUP.replace("<p>Family", f"{tag}<p>Family"))
        item = launch_audit(site).items[-2]
        assert item.status == Status.ISSUE and "another host" in item.evidence, tag


def test_23_a_referenced_file_that_is_not_there_is_an_issue(tmp_path):
    site = _site(tmp_path, mockup=MOCKUP.replace(
        "<p>Family", '<img src="media/van.webp" alt="A van"><p>Family'))
    item = launch_audit(site).items[-2]
    assert item.status == Status.ISSUE and "missing" in item.evidence


def test_23_no_media_at_all_is_not_applicable(tmp_path):
    site = _site(tmp_path, mockup=MOCKUP.replace(
        '<img src="hero.jpg" alt="A bright treatment room">', ""))
    assert launch_audit(site).items[-2].status == Status.NA


# ------------------------------------------------- the v4 trades template
# The Lovable port (2026-10-01): the trades hero is a full-bleed photograph
# under a gradient, the booking pill is marigold, the phone sits in words in
# the header, and every section carries a numbered label. None of that may
# cost a launch item.

def test_the_v4_trades_hero_still_passes_the_cta_alt_sticky_and_media_items(tmp_path):
    from atlas.modules.agency import sitetemplate
    lead = {"id": 40, "name": "Hitchingham Electric Company", "category": "electrician",
            "phone": "(540) 729-4548", "address": "512 E 11th St, New York, NY 10009",
            "website": None, "status": "audited"}
    site = tmp_path / "sites" / "hitchingham-electric-company-40"
    site.mkdir(parents=True)
    client = sitetemplate.themed(client_data(lead, None, tmp_path), "trades")
    copy = sitetemplate.default_copy(client, "trades")
    faqs = sitekit.faq_list(client, list(copy.faqs), form=False)
    (site / "index.html").write_text(sitetemplate.render_home(client, copy, "trades", faqs))
    sitetemplate.copy_assets(site, "trades", client.category)
    finish_site(site, client, analytics=None, form_action=None, colours_given=False)
    report = launch_audit(site)
    assert not report.issues, report.summary()
    by = {item.number: item for item in report.items}
    for number in (2, 9, 16, 23):
        assert by[number].status is Status.OK, (number, by[number].evidence)
    index = (site / "index.html").read_text()
    root = parse(index)
    # the hero photograph is content with an honest caption, not decoration
    hero = next(n for n in root.iter() if "mf-hero" in n.classes())
    (img,) = hero.find_all("img")
    assert img.get("alt").startswith("Illustrative photo:")
    assert img.get("fetchpriority") == "high" and not hero.find_all("video")
    # the first action in the first section is the booking pill, marigold
    first = next(n for n in hero.iter() if n.tag == "a")
    assert first.get("data-atlas") == "book-cta" and "mf-btn--sign" in first.classes()
    # the retired loop is referenced nowhere and copied nowhere
    assert "bg-trades" not in index and not list(site.glob("media/bg-*"))
    assert index.count('class="mf-eyebrow"') == 5


# -------------------------------------------------------- 24, every report

def test_24_a_sold_site_is_readable_by_ai_assistants(tmp_path):
    site = _site(tmp_path, {**FULL, "package": "100"})
    item = launch_audit(site).items[-1]
    assert item.number == 24 and item.status == Status.OK
    assert "not a ranking" in item.evidence
    text = (site / "llms.txt").read_text()
    assert text.startswith("# Harbor Dental") and "Phone:" in text or "Email:" in text
    assert "Do you take new patients?" in text
    assert "supplied by the business" in text
    robots = (site / "robots.txt").read_text()
    for bot in ("GPTBot", "ClaudeBot", "PerplexityBot"):
        assert f"User-agent: {bot}\nAllow: /" in robots


def test_24_a_sold_site_without_an_address_needs_the_client_not_an_issue(tmp_path):
    site = _site(tmp_path, {"package": "100"}, lead={**LEAD, "address": None,
                                                     "phone": None})
    item = launch_audit(site).items[-1]
    assert item.number == 24 and item.status == Status.NEEDS_CLIENT
    assert "address" in item.evidence
    assert launch_audit(site).ready


def test_24_an_older_sold_build_is_named_not_passed(tmp_path):
    site = _site(tmp_path, {**FULL, "package": "100"})
    (site / "llms.txt").unlink()
    (site / "robots.txt").write_text("User-agent: *\nAllow: /\n")
    item = launch_audit(site).items[-1]
    assert item.status == Status.NEEDS_CLIENT and "rebuild" in item.evidence


def test_24_a_broken_llms_txt_or_blocked_crawlers_is_an_issue(tmp_path):
    site = _site(tmp_path, {**FULL, "package": "100"})
    (site / "llms.txt").write_text("hello\n")
    assert launch_audit(site).items[-1].status == Status.ISSUE
    site = _site(tmp_path, {**FULL, "package": "100"})
    (site / "robots.txt").write_text("User-agent: *\nAllow: /\nUser-agent: GPTBot\nAllow: /\n")
    item = launch_audit(site).items[-1]
    assert item.status == Status.ISSUE and "crawlers" in item.evidence


def test_24_a_preview_publishes_nothing_for_ai_assistants(tmp_path):
    site = _site(tmp_path, FULL)
    assert not (site / "llms.txt").exists()
    assert "GPTBot" not in (site / "robots.txt").read_text()
