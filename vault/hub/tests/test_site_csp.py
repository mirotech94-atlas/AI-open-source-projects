"""The marketing site under its own Content-Security-Policy.

`deploy/nginx-site.conf.tmpl` serves guarder.io with `default-src 'self'` and
no `style-src` of its own, so the browser refuses an inline `<style>` block or
a `style=` attribute outright. Nothing warns you: the page still loads, the
rules simply never apply.

That is not hypothetical. The monthly/yearly pricing toggle shipped with its
~14 lines of CSS in an inline `<style>` inside `#pricing`, to keep the section
self-contained, and it reached production unstyled. The CSS now lives in
`site/styles.css`; this test is what stops it drifting back.

The nine `site/<locale>/index.html` files that `site/i18n/build_locales.py`
generates are copies of the same template with only text swapped -- an
inline style couldn't get into one without getting into all of them the same
way -- but "couldn't" is exactly the kind of claim a test should check
rather than assert from memory, so every locale file is walked here too.
"""

import pathlib
import re

SITE = pathlib.Path(__file__).resolve().parents[2] / "site"

# English at the root, plus every locale build_locales.py writes to its own
# subdirectory. Kept as a literal list for the same reason as the identical
# one in test_billing_plans.py: an unexpectedly missing or extra locale
# directory should fail this test, not go unnoticed because it was read from
# the same script that might have introduced the bug.
SITE_HTML_FILES = ["index.html", "signup.html", "terms.html", "privacy.html",
                   "status.html"] + [
    f"{locale}/index.html" for locale in ("ar", "es", "fr", "de", "pt", "it", "zh", "hi")
]


def test_the_site_carries_no_inline_css():
    for name in SITE_HTML_FILES:
        html = (SITE / name).read_text()
        assert "<style" not in html, f"{name}: an inline <style> block is refused by this host's CSP"
        assert not re.search(r'\sstyle="', html), (
            f"{name}: an inline style attribute is refused by this host's CSP"
        )


def test_the_pricing_toggle_is_styled_from_the_stylesheet():
    """The rules the toggle needs are in the file the page is allowed to load."""
    css = (SITE / "styles.css").read_text()
    for rule in (".interval-input", ".interval-toggle", ".switch"):
        assert rule in css, rule


# Every script the page loads, not just the first one. The three below arrived
# with borrowed design work -- a WebGL lattice and scene from an external
# library, plus the reveal choreography -- and that is exactly the kind of
# arrival that brings a CDN link with it: the library's own delivery format
# hands you a Google Fonts URL, a pinned three.js importmap and signed links to
# storage buckets. All of it was vendored instead (CLAUDE.md "Design assets"
# explains why), and this list is what keeps it vendored. A new file under
# site/ that the page loads belongs here on the day it is added.
SITE_SCRIPTS = ["app.js", "hero-gradient.js", "reveal.js", "watch-scene.js", "status.js",
                "styles.css"]


def test_the_site_fetches_nothing_from_a_third_party():
    """`default-src 'self'` refuses it, and a watchdog's own site should not be
    telling a CDN who is reading about it."""
    for name in SITE_HTML_FILES + SITE_SCRIPTS:
        body = (SITE / name).read_text()
        for bad in ("fonts.googleapis", "fonts.gstatic", "//cdn", "unpkg", "jsdelivr", "gtag("):
            assert bad not in body, f"{name}: {bad}"


def test_every_script_the_page_loads_is_covered_above():
    """The guard is only worth having if it walks the real set. A <script src>
    added to the page without a matching entry in SITE_SCRIPTS would be
    unchecked, and nothing else would notice."""
    html = (SITE / "index.html").read_text()
    loaded = set(re.findall(r'<script[^>]+src="/([^"]+)"', html))
    missing = loaded - set(SITE_SCRIPTS)
    assert not missing, f"script(s) loaded by index.html but not in SITE_SCRIPTS: {sorted(missing)}"


def test_every_locale_file_sets_html_lang_and_the_asset_paths_are_root_absolute():
    """Root-absolute `/styles.css` etc. so a locale subdirectory still finds
    the shared assets; `<html lang>` (and `dir="rtl"` for Arabic) so a screen
    reader or search engine gets the right language for the right file."""
    expected_lang = {
        "index.html": ("en", False),
        "signup.html": ("en", False),
        "terms.html": ("en", False),
        "privacy.html": ("en", False),
        "status.html": ("en", False),
        "ar/index.html": ("ar", True),
        "es/index.html": ("es", False),
        "fr/index.html": ("fr", False),
        "de/index.html": ("de", False),
        "pt/index.html": ("pt", False),
        "it/index.html": ("it", False),
        "zh/index.html": ("zh", False),
        "hi/index.html": ("hi", False),
    }
    for name in SITE_HTML_FILES:
        html = (SITE / name).read_text()
        lang, rtl = expected_lang[name]
        tag = f'<html lang="{lang}" dir="rtl">' if rtl else f'<html lang="{lang}">'
        assert tag in html, f"{name}: expected {tag!r}"
        assert 'href="/styles.css"' in html, f"{name}: stylesheet must be root-absolute"
        assert 'src="/app.js"' in html, f"{name}: script must be root-absolute"
        assert 'href="/favicon.svg' in html, f"{name}: favicon must be root-absolute"


def test_the_status_page_is_allowed_to_reach_the_api_it_checks():
    """/status.html asks the browser whether api.guarder.io answers. Under
    `default-src 'self'` alone that fetch is refused with NO visible error:
    the page loads, the check never runs, and it sits on "Checking…"
    forever. That is the same silent-refusal trap this file was written
    about for inline CSS, so the policy is asserted rather than assumed.
    """
    conf = (pathlib.Path(__file__).resolve().parents[2]
            / "deploy" / "nginx-site.conf.tmpl").read_text()
    csp = [ln for ln in conf.splitlines() if "Content-Security-Policy" in ln]
    assert csp, "the site must set a CSP at all"
    assert "connect-src 'self' https://api.guarder.io" in csp[0], (
        "status.html's health check needs the API host in connect-src"
    )


def test_the_status_page_says_what_it_cannot_tell_you():
    """A status page hosted on the thing it reports on cannot report its own
    absence, and a reader who does not know that will trust it in the one
    moment it is useless."""
    html = (SITE / "status.html").read_text()
    assert "not an independent monitor" in html
    assert "assume everything is down" in html
