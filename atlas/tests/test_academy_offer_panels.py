"""The competitor-report data in the catalog, and its render helpers.

Miro's report "Compete through useful results" (11 Oct 2026) asks each
product page for: what you will finish, a free-to-paid journey, tested-with
dates, a troubleshooting preview, the audiences, an approval-step demo, real
learning evidence, a family route, the exact box contents and a bounded
support policy. These tests hold the data to the report's own discipline:
optional fields with defaults (an old catalog still builds), no absolute
safety promise in the copy, every product either filled in or carrying an
explicit open question, counts that match the delivered files, and no
invented numbers or guarantees in the new fields.
"""
from __future__ import annotations

import copy
import json
import re
from html import escape

import pytest

from atlas.modules.academy import catalog as cat_mod
from atlas.modules.academy import offer_panels as op

PROPOSALS = cat_mod.CATALOG_PATH.with_name("tutorial_proposals.json")


def _raw() -> dict:
    return json.loads(cat_mod.CATALOG_PATH.read_text())


# ----------------------------------------------------------------- schema

def test_a_catalog_without_any_new_field_still_parses_with_empty_defaults() -> None:
    raw = _raw()
    new = ("finish", "journey", "tested", "troubleshooting", "audiences",
           "review_demo", "evidence", "testimonials", "unverified")
    for p in raw["products"]:
        for k in new:
            p.pop(k, None)
        for o in p["offers"]:
            o.pop("inclusions", None)
    raw.pop("routes", None)
    raw["store"].pop("audiences", None)
    raw["store"].pop("support", None)
    cat = cat_mod.parse(raw)
    p = cat.products[0]
    assert p.finish is None and p.journey is None and p.review_demo is None
    assert p.tested == p.troubleshooting == p.evidence == ()
    assert p.audiences == p.testimonials == p.unverified == ()
    assert all(o.inclusions == () for o in cat.offers())
    assert cat.routes == () and cat.audiences == () and cat.support == {}


def test_a_video_count_that_differs_from_the_delivered_files_is_refused() -> None:
    raw = _raw()
    offer = raw["products"][0]["offers"][0]
    offer["inclusions"] = [{"kind": "video", "label": "Videos", "count": 999}]
    with pytest.raises(cat_mod.CatalogError, match="lists 999 videos"):
        cat_mod.parse(raw)


def test_inclusions_that_omit_delivered_videos_are_refused() -> None:
    raw = _raw()
    offer = raw["products"][0]["offers"][0]
    assert any(f["path"].endswith(".mp4") for f in offer["files"])
    offer["inclusions"] = [{"kind": "guide", "label": "Guide"}]
    with pytest.raises(cat_mod.CatalogError, match="videos its inclusions don't list"):
        cat_mod.parse(raw)


@pytest.mark.parametrize(("path", "value", "match"), [
    (("audiences",), ["nobody"], "unknown audience"),
    (("tested",), [{"tool": "x", "checked_on": "11 Oct", "by": "me", "what": "y"}],
     "not YYYY-MM-DD"),
    (("troubleshooting",), [{"kind": "oops", "problem": "p", "recovery": "r"}],
     "troubleshooting kind"),
    (("evidence",), [{"kind": "testimonial", "label": "l", "source": "s"}],
     "evidence kind"),
    (("journey",), {"free_lessons": [{"series": "n8n", "slug": "x", "status": "live"}]},
     "has status"),
])
def test_panel_fields_are_validated(path: tuple[str, ...], value: object,
                                    match: str) -> None:
    raw = copy.deepcopy(_raw())
    raw["products"][0][path[0]] = value
    with pytest.raises(cat_mod.CatalogError, match=match):
        cat_mod.parse(raw)


def test_a_route_naming_an_unknown_product_is_refused() -> None:
    raw = _raw()
    raw["routes"] = [{"slug": "r", "title": "t", "products": ["no-such-title"],
                      "people": "p"}]
    with pytest.raises(cat_mod.CatalogError, match="unknown product"):
        cat_mod.parse(raw)


# ----------------------------------------------------------------- content

CAT = cat_mod.load()

# The report's "Keep the brand promise credible": no copy may promise what a
# control can't. Lesson titles, file labels and the cover's alt text describe
# what a buyer receives and are exempt (their wording is a TO DECIDE for Miro).
ABSOLUTE = re.compile(
    r"can['’]t (?:move money|burn|go rogue|be faked|act without)|without your ok\b"
    r"|never acts? without|100% safe|completely safe|fully secure"
    r"|(?<!anyone )scam[- ]proof",
    re.IGNORECASE)
EXEMPT_KEYS = {"cover_alt", "inside", "files", "sources", "unverified", "not_listed"}


def _copy_strings(node: object, key: str = "") -> list[tuple[str, str]]:
    """Every buyer-facing string in the catalog, with the key it sits under."""
    if key in EXEMPT_KEYS or key.endswith("_note"):
        return []
    if isinstance(node, str):
        return [(key, node)]
    if isinstance(node, dict):
        return [x for k, v in node.items() for x in _copy_strings(v, k)]
    if isinstance(node, list):
        return [x for v in node for x in _copy_strings(v, key)]
    return []


def test_no_absolute_safety_promise_in_the_product_copy() -> None:
    hits = [(k, s) for k, s in _copy_strings(_raw()) if ABSOLUTE.search(s)]
    assert hits == []


def test_the_pattern_catches_the_old_promises_and_spares_honest_negations() -> None:
    assert ABSOLUTE.search("can't move money, delete data or act without your OK")
    assert ABSOLUTE.search("the version that can't burn them")
    assert ABSOLUTE.search("approvals that can't be faked")
    assert not ABSOLUTE.search("It does not make anyone scam-proof.")


@pytest.mark.parametrize("p", CAT.products, ids=lambda p: p.slug)
def test_every_product_has_finish_journey_and_inclusions_or_says_why_not(
        p: cat_mod.Product) -> None:
    open_q = " ".join(p.unverified)
    assert p.finish is not None or "finish:" in open_q
    assert p.journey is not None or "journey:" in open_q
    if p.journey is not None and not p.journey.free_lessons:
        assert "journey:" in open_q, "a journey with no free lesson must say so"
    for o in p.offers:
        assert o.inclusions or "inclusions:" in open_q, o.id


@pytest.mark.parametrize("p", CAT.products, ids=lambda p: p.slug)
def test_no_invented_numbers_or_guarantees_in_the_new_fields(
        p: cat_mod.Product) -> None:
    assert not any(re.search(r"\d", t) for t in p.testimonials)
    raw = next(x for x in _raw()["products"] if x["slug"] == p.slug)
    new = {k: raw.get(k) for k in ("finish", "journey", "tested", "troubleshooting",
                                   "review_demo", "evidence", "testimonials")}
    new["inclusions"] = [o.get("inclusions") for o in raw["offers"]]
    for _, s in _copy_strings(new):
        assert "guarantee" not in s.lower(), s
        assert not re.search(r"\$\s?\d|\d\s?%", s), s   # no money or percentage claims


def test_the_report_s_two_audiences_are_defined_and_used() -> None:
    keys = {a.key for a in CAT.audiences}
    assert keys == {"local-service", "freelancer"}
    assert {a for p in CAT.products for a in p.audiences} == keys


def test_checks_are_dated_and_name_who_ran_them() -> None:
    for p in CAT.products:
        for c in p.tested:
            assert cat_mod.DATE_RE.match(c.checked_on) and c.by and c.what


def test_free_lessons_name_a_real_episode_or_a_proposal() -> None:
    from atlas.modules.academy import tutorial_series as ts
    proposals = {x["key"] for x in json.loads(PROPOSALS.read_text())["proposals"]}
    for p in CAT.products:
        for lr in p.journey.free_lessons if p.journey else ():
            if lr.series == "proposal":
                assert lr.slug in proposals, lr.slug
                assert lr.status == "proposed"
                continue
            s = ts.series(lr.series)
            assert s is not None, lr.series
            # Existence only: the tutorial lane may script a planned episode
            # without this catalog's status going stale into a red build.
            assert any(e.slug == lr.slug for e in s.episodes), lr.slug
            assert lr.status in ("scripted", "planned"), lr.slug


def test_the_family_route_is_two_adults_and_names_no_price() -> None:
    (route,) = CAT.routes_for("family-ai-scam-defence-workbook")
    assert CAT.routes_for("ai-for-every-age") == (route,)
    assert "18" in route.people and "never Claude" in route.people
    assert not re.search(r"\$\s?\d", json.dumps(_raw()["routes"]))
    assert any("TO DECIDE" in u for u in route.unverified)


def test_the_four_video_proposals_fit_the_channel_rules() -> None:
    from atlas.modules.academy import tutorial_series as ts
    props = json.loads(PROPOSALS.read_text())["proposals"]
    assert len(props) == 4
    assert len({p["key"] for p in props}) == 4
    for p in props:
        assert len(p["title"]) <= 100, p["title"]
        assert p["level"] in ts.LEVEL_NAMES
        assert p["status"] == "proposed"
        assert p["series"] == "" or ts.series(p["series"]) is not None
        assert CAT.product(p["product"]) is not None
        assert CAT.product(p.get("also_relevant") or p["product"]) is not None
        assert p["outline"] and p["demo"]["description"] and p["demo"]["source"]
        assert len(p["hashtags"]) <= 3
        text = json.dumps(p).lower()
        assert "guarantee" not in text and not re.search(r"\$\s?\d|\d\s?%", text)
    # each product the report pairs with a video points back at it
    for p in props:
        j = CAT.product(p["product"]).journey  # type: ignore[union-attr]
        assert j is not None and p["key"] in {lr.slug for lr in j.free_lessons}


def test_support_policy_is_bounded_and_leaves_miro_s_calls_unset() -> None:
    s = CAT.support
    assert s["access_help"] and s["covers"] and s["not_covered"] and s["report_broken"]
    for key in ("updates", "implementation_services"):
        assert s[key] is None and s[f"{key}_note"].startswith("TO DECIDE")
    # Decided by Miro on 2026-10-11: the same promise as contact/sent.
    assert s["response_time"] == "Miro replies by email within 2 business days."


# ----------------------------------------------------------------- render

def _all_html() -> str:
    return "".join(op.product_panels(CAT, p) + op.nav_links(CAT, p)
                   + op.audiences_line(CAT, p) + op.kit_scene_json(p)
                   for p in CAT.products) + op.support_panel(CAT)


def test_no_open_question_ever_reaches_the_page() -> None:
    html = _all_html()
    assert "TO VERIFY" not in html and "TO DECIDE" not in html
    for _, q in op.open_questions(CAT):
        assert q[:60] not in html


def test_open_questions_list_every_unverified_item_and_store_decision() -> None:
    qs = op.open_questions(CAT)
    assert len(qs) == (sum(len(p.unverified) for p in CAT.products)
                       + sum(len(r.unverified) for r in CAT.routes) + 2)
    assert {w for w, _ in qs} >= {"store.updates", "store.implementation_services"}
    assert "store.response_time" not in {w for w, _ in qs}      # decided


def test_an_empty_product_renders_nothing() -> None:
    raw = _raw()
    for p in raw["products"]:
        for k in ("finish", "journey", "tested", "troubleshooting", "audiences",
                  "review_demo", "evidence"):
            p.pop(k, None)
        for o in p["offers"]:
            o.pop("inclusions", None)
    raw.pop("routes")
    raw["store"].pop("support")
    cat = cat_mod.parse(raw)
    for p in cat.products:
        assert op.product_panels(cat, p) == "" and op.nav_links(cat, p) == ""
        assert op.audiences_line(cat, p) == ""
    assert op.support_panel(cat) == ""


def test_unpublished_videos_and_samples_are_not_linked() -> None:
    """No free lesson has a URL yet and no sample is published: the journey
    shows only what the kit adds, and the evidence panel is empty."""
    for p in CAT.products:
        assert op.evidence_panel(p) == ""
        j = op.journey_panel(p)
        assert "Watch, free" not in j and "<a " not in j
        assert "Watch, try" not in j and "2. Try" not in j


def test_a_published_lesson_is_linked_with_its_real_title() -> None:
    raw = _raw()
    lbap = next(p for p in raw["products"] if p["slug"] == "local-business-automation-pack")
    lbap["journey"]["free_lessons"][0]["url"] = "https://www.youtube.com/watch?v=abc"
    p = cat_mod.parse(raw).product("local-business-automation-pack")
    html = op.journey_panel(p)  # type: ignore[arg-type]
    assert 'href="https://www.youtube.com/watch?v=abc"' in html
    assert "Customer-Reply Draft That Waits for Your Approval" in html
    assert "2. Try" in html


def test_the_kit_scene_data_matches_the_static_list_and_is_script_safe() -> None:
    for p in CAT.products:
        data = json.loads(op.kit_scene_json(p))
        static = op.inclusions_panel(p)
        for o in data["offers"]:
            for i in o["items"]:
                assert escape(i["label"]) in static
        assert "</" not in op.kit_scene_json(p)
    box = op.inclusions_data(CAT.offer("safe-ai-operator"))  # type: ignore[arg-type]
    assert box[0] == {"kind": "video", "kind_label": "Videos",
                      "label": "Narrated video lessons", "detail": "4 to 8 minutes each",
                      "count": 28}


def test_panels_escape_catalog_text() -> None:
    raw = _raw()
    raw["products"][0]["finish"]["deliverable"] = "<script>alert(1)</script>"
    p = cat_mod.parse(raw).products[0]
    html = op.finish_panel(p)
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_support_panel_states_no_response_time_until_miro_sets_one() -> None:
    raw = _raw()
    raw["store"]["support"]["response_time"] = None
    html = op.support_panel(cat_mod.parse(raw))
    assert "Reporting a broken workflow" in html and "When to expect a reply" not in html
    html = op.support_panel(CAT)                   # set by Miro, 2026-10-11
    assert "When to expect a reply" in html and "within 2 business days" in html


# ------------------------------------------------- on the product pages

def _site() -> dict[str, str]:
    from datetime import UTC, datetime

    from atlas.modules.academy import site
    files = site.render_root(CAT, datetime(2026, 10, 11, tzinfo=UTC))
    return {k: v.decode() for k, v in files.items()
            if k.endswith((".html", ".json", ".txt", ".xml"))}


def test_every_product_page_carries_its_panels_where_the_report_puts_them() -> None:
    from html import unescape

    pages = _site()
    for p in CAT.products:
        page = pages[f"{p.slug}/index.html"]
        hero = page.split('<section class="p-hero', 1)[1].split("</section>", 1)[0]
        assert op.audiences_line(CAT, p) in hero
        nav = page.split('<nav class="p-nav"', 1)[1].split("</nav>", 1)[0]
        for link in re.findall(r"<a [^>]*>.*?</a>", op.nav_links(CAT, p)):
            assert link in nav, (p.slug, link)
        before_learn, after_learn = page.split('<section id="learn">', 1)
        assert op.finish_panel(p) in before_learn
        between = after_learn.split('<section id="inside">', 1)[0]
        for panel in (op.inclusions_panel(p), op.journey_panel(p),
                      op.review_demo_panel(p), op.troubleshooting_panel(p),
                      op.tested_panel(p), op.evidence_panel(p),
                      op.family_route_panel(CAT, p)):
            assert panel in between
        assert op.support_panel(CAT) in page.split('<section id="terms">', 1)[1]
        # The kit scene's data rides on a data-* attribute, not a script.
        scene = re.search(r'data-kit-scene="([^"]*)"', page)
        assert scene and json.loads(unescape(scene.group(1))) == json.loads(
            op.kit_scene_json(p))
        for a in re.findall(r'<a href="#([\w-]+)"', nav):
            assert f'id="{a}"' in page, (p.slug, a)
    assert "op-finish" in pages["safe-ai-operator/index.html"]


def test_no_open_question_reaches_any_built_file() -> None:
    for path, text in _site().items():
        if path == "catalog.json":
            continue            # the public catalog is checked below
        assert "TO VERIFY" not in text and "TO DECIDE" not in text, path
        for _, q in op.open_questions(CAT):
            assert q[:60] not in text, (path, q[:60])
    public = _site()["catalog.json"]
    assert "TO VERIFY" not in public and "TO DECIDE" not in public


def test_meta_descriptions_fit_a_search_result_and_never_cut_a_word() -> None:
    from atlas.modules.academy import site
    pages = _site()
    for p in CAT.products:
        page = pages[f"{p.slug}/index.html"]
        got = re.search(r'<meta name="description" content="([^"]*)">', page).group(1)
        from html import unescape
        desc = unescape(got)
        assert len(desc) <= site.META_DESCRIPTION_MAX, (p.slug, len(desc))
        assert p.summary.startswith(desc.removesuffix("…")), p.slug
        assert p.summary in page                     # the page keeps the whole text
    assert site.meta_description("short.") == "short."
    assert site.meta_description("word " * 50).endswith("word…")


def test_the_contact_page_promises_miro_s_reply_time() -> None:
    # The sent page went with the posted form (2026-10-29, ATLAS local-only).
    sent = _site()["contact/index.html"]
    assert "within 2 business days" in sent and "day or two" not in sent


def test_the_on_page_nav_follows_the_page_and_ids_are_unique() -> None:
    """app.js scroll-spies .p-nav: its links must run in page order."""
    pages = _site()
    for p in CAT.products:
        page = pages[f"{p.slug}/index.html"]
        ids = re.findall(r'\sid="([^"]+)"', page)
        assert len(ids) == len(set(ids)), (p.slug, sorted(i for i in ids if ids.count(i) > 1))
        nav = page.split('<nav class="p-nav"', 1)[1].split("</nav>", 1)[0]
        order = [page.index(f'id="{a}"') for a in re.findall(r'<a href="#([\w-]+)"', nav)]
        assert order == sorted(order), p.slug


def test_in_the_box_is_the_kit_scene_s_no_motion_fallback() -> None:
    css = (cat_mod.CATALOG_PATH.parent / "assets" / "style.css").read_text()
    block = css.split("@media (prefers-reduced-motion: no-preference) {", 1)[1].split("}", 1)[0]
    assert ".js #box" in block and '.js .p-nav a[href="#box"]' in block
