"""Rendering. UNKNOWN must never be able to look like OK.

A check that cannot run reports UNKNOWN rather than OK precisely so a broken
collector cannot read as healthy. That protection lives in detect.py — and it
dies the moment the UI paints the two states alike. These tests are the other
half of the invariant.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

TEMPLATES = Path(__file__).parent.parent / "atlas" / "api" / "templates"
TOKENS = (TEMPLATES / "_tokens.css").read_text()


@pytest.fixture(scope="module")
def env():
    return Environment(loader=FileSystemLoader(str(TEMPLATES)), autoescape=True)


def badge(env, health: str) -> str:
    return env.from_string(
        '{% import "_health.html" as h %}{{ h.badge(health) }}'
    ).render(health=health).strip()


# ------------------------------------------------- the markup differs

def test_unknown_and_ok_produce_different_markup(env):
    assert badge(env, "unknown") != badge(env, "ok")


def test_unknown_says_the_word_unknown(env):
    """Colour alone never carries the meaning."""
    assert "Unknown" in badge(env, "unknown")


@pytest.mark.parametrize("health,word", [
    ("ok", "OK"), ("degraded", "Degraded"),
    ("failing", "Failing"), ("unknown", "Unknown"),
])
def test_every_state_carries_its_word(env, health, word):
    assert word in badge(env, health)


def test_an_unrecognised_state_is_unknown_not_ok(env):
    """A health value the UI does not recognise is an absence of knowledge."""
    for value in ("", "banana", None):
        rendered = badge(env, value)
        assert "Unknown" in rendered
        assert 'data-health="unknown"' in rendered
        assert "OK" not in rendered


# ------------------------------- they cannot share a treatment by accident

def test_unknown_carries_a_marker_that_ok_does_not(env):
    """The distinction must be structural, not just a colour swap.

    If someone ever renders the two with the same classes and only a different
    colour, this fails — which is the point.
    """
    unknown, ok = badge(env, "unknown"), badge(env, "ok")
    assert 'data-health="unknown"' in unknown
    assert 'data-health="ok"' in ok
    assert "hatch" in unknown, "UNKNOWN lost its hatch treatment"
    assert "hatch" not in ok, "OK gained the hatch treatment reserved for UNKNOWN"


def test_unknown_resolves_to_a_different_colour_from_every_live_state():
    """UNKNOWN must not borrow a colour that means 'measured'."""
    def state_colour(health: str) -> str:
        rule = re.search(
            rf'\[data-health="{health}"\][^}}]*}}', TOKENS).group(0)
        return re.search(r"--state:\s*var\(--([a-z-]+)\)", rule).group(1)

    assert state_colour("unknown") == "grey"
    assert state_colour("ok") == "cyan"
    assert state_colour("degraded") == "amber"
    assert state_colour("failing") == "red"
    assert len({state_colour(h) for h in
                ("ok", "degraded", "failing", "unknown")}) == 4


def test_unknown_has_no_glow():
    """The core rule of this interface.

    Light is reserved for things ATLAS actually measured. If UNKNOWN ever gets
    a glow it becomes indistinguishable at a glance from a healthy module,
    which is the worst failure this interface can have.
    """
    rule = re.search(r'\[data-health="unknown"\][^}]*}', TOKENS).group(0)
    assert "--state-glow: transparent" in rule, "UNKNOWN was given a glow"

    badge = re.search(
        r'\.badge\[data-health="unknown"\]\s*{[^}]*}', TOKENS).group(0)
    assert "box-shadow: none" in badge, "UNKNOWN's badge glows"
    assert "dashed" in badge, "UNKNOWN lost its dashed border"

    rail = re.search(
        r'\.rail\[data-health="unknown"\]\s*{[^}]*}', TOKENS, re.DOTALL).group(0)
    assert "box-shadow: none" in rail, "UNKNOWN's rail glows"


def test_unknown_never_pulses():
    """A pulse says 'this is live'. An unmeasured thing is not live."""
    rule = re.search(r'\.live\[data-health="unknown"\][^}]*}', TOKENS).group(0)
    assert "animation: none" in rule


def test_the_health_rules_live_in_the_shared_stylesheet():
    """The React dashboard and the server-rendered pages import the same file.

    These rules were once defined only inside base.html. The server-rendered
    pages got badges and the SPA silently drew every state as identical plain
    text — UNKNOWN included. Keeping them here is what makes that impossible.
    """
    for selector in ('[data-health="ok"]', '[data-health="unknown"]',
                     '[data-health="failing"]', '[data-health="degraded"]',
                     ".badge-dot", ".rail", ".sev"):
        assert selector in TOKENS, f"{selector} is missing from _tokens.css"


def test_the_typeface_is_vendored_not_fetched():
    """This is a mesh-only tool; it should render without the public internet."""
    assert "@font-face" in TOKENS
    assert "/fonts/JetBrainsMono" in TOKENS
    assert "fonts.googleapis" not in TOKENS and "fonts.gstatic" not in TOKENS
    for weight in ("Regular", "Bold"):
        assert (Path(__file__).parent.parent / "ui" / "public" / "fonts"
                / f"JetBrainsMono-{weight}.woff2").is_file()


# --------------------------------------------- a section that could not run

def test_an_unavailable_brief_section_does_not_render_as_empty(env):
    """'I could not look' and 'there is nothing' must not share markup."""
    from atlas.kernel.brief import Section

    template = env.from_string(
        "{% if s.unavailable %}<p class='unavailable'>Could not check: "
        "{{ s.unavailable }}</p>{% elif not s.lines %}<p class='empty'>"
        "{{ s.empty }}</p>{% endif %}")

    broken = template.render(s=Section("X", unavailable="mailbox unreachable"))
    empty = template.render(s=Section("X", lines=[], empty="Nothing waiting."))

    assert broken != empty
    assert "Could not check" in broken and "unavailable" in broken
    assert "Nothing waiting" in empty and "unavailable" not in empty


# ------------------------- both surfaces must share the rendering rules

def test_the_spa_stylesheet_imports_the_shared_tokens():
    spa = (Path(__file__).parent.parent / "ui" / "src" / "hud.css").read_text()
    assert "_tokens.css" in spa, "the SPA stopped importing the shared tokens"


def test_base_html_does_not_redefine_health_rules():
    """A second copy is a second thing that can drift."""
    base = (TEMPLATES / "base.html").read_text()
    assert '.badge[data-health=' not in base


# ----------------------------- the texted pages must not need JavaScript

def test_the_server_rendered_pages_carry_no_script(env):
    """These open from a link on a phone. A bundle they have to fetch over a
    mesh link defeats the point of server-rendering them."""
    for name in ("base.html", "brief.html", "reports.html", "report.html",
                 "finding.html", "day.html", "briefs.html", "login.html"):
        markup = (TEMPLATES / name).read_text()
        assert "<script" not in markup.lower(), f"{name} pulls in JavaScript"


def test_the_report_page_shows_what_was_sent_not_a_rebuild(env):
    """A brief is built from live queries. Re-rendering an old one would show
    today's numbers under an older date."""
    markup = (TEMPLATES / "report.html").read_text()
    assert "r.body" in markup, "the report page stopped showing the stored body"


def test_the_reports_filter_is_a_plain_form(env):
    markup = (TEMPLATES / "reports.html").read_text()
    assert 'method="get"' in markup
    assert "onclick" not in markup.lower() and "addEventListener" not in markup


# ------------------------------- fleet and memory are client routes only

def test_fleet_and_memory_have_no_server_route(env):
    """Fleet and Memory are SPA pages, routed entirely inside App.tsx.

    A `@router.get("/fleet")` or `@router.get("/memory")` in the FastAPI app
    would mean the server, not React Router, decides what those paths show --
    and could shadow the SPA catch-all in `main.py` depending on registration
    order. Neither page gets one; both are reached only through the bundle
    `main.py`'s catch-all already serves for every unmatched path.

    (`/api/memory` is a real, pre-existing endpoint -- it lives under
    `dashboard.py`'s `/api` prefix and is a different route entirely from a
    top-level `/memory` page route, so it is excluded here on purpose.)
    """
    root = Path(__file__).parent.parent
    main_src = (root / "atlas" / "api" / "main.py").read_text()
    views_src = (root / "atlas" / "api" / "views.py").read_text()

    for src, name in ((main_src, "main.py"), (views_src, "views.py")):
        assert '"/fleet"' not in src, f"{name} defines a server route for /fleet"
        assert "'/fleet'" not in src, f"{name} defines a server route for /fleet"
        assert '"/memory"' not in src, f"{name} defines a server route for /memory"
        assert "'/memory'" not in src, f"{name} defines a server route for /memory"


def test_fleet_and_memory_are_registered_as_spa_routes():
    """The other half of the previous test: the paths must exist somewhere,
    or they would 404 through the SPA catch-all too -- just client-side."""
    # Since the two-tab dashboard (0449510c) both are client-side redirects:
    # nav.ts REDIRECTS lists them and App.tsx turns each entry into a Route.
    src = Path(__file__).parent.parent / "ui" / "src"
    app_tsx = (src / "App.tsx").read_text()
    nav_ts = (src / "nav.ts").read_text()
    assert "Object.entries(REDIRECTS).map" in app_tsx
    assert "path={from}" in app_tsx
    assert '"/fleet":' in nav_ts
    assert '"/memory":' in nav_ts


def test_the_built_index_html_carries_no_route_names():
    """The SPA shell is one script tag; React Router decides what a path
    shows entirely in the browser. If a route name ever leaked into the built
    HTML itself, that would mean something server-side had started templating
    the shell per-route -- which is exactly the split this test exists to
    catch before it happens.

    Skipped when `ui/dist` has not been built (it is gitignored and Task 16
    installs the JS test runner) -- this is a static check on the artifact,
    not a build step.
    """
    index = Path(__file__).parent.parent / "ui" / "dist" / "index.html"
    if not index.is_file():
        pytest.skip("ui/dist/index.html not built (run: cd ui && npm run build)")
    markup = index.read_text()
    assert "/fleet" not in markup
    assert "/memory" not in markup
    assert markup.count("<script") == 1
