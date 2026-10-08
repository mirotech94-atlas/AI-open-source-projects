"""The report as a PDF: the same frozen document, rendered by a browser.

The spec's artifact is the PDF, and its one hard rule is that it must be
the same document as the share link rather than a second template that
drifts. So the PDF is rendered from the HTML Vault already froze — a
pure function of a file that never changes — and cached beside it.

Two tests carry the feature:

- `test_the_pdf_is_rendered_from_the_frozen_html_and_cached`, which runs
  real Chrome when the machine has one and skips honestly when it does
  not. A mocked render proves the plumbing; only a real one proves the
  fonts inline and the output is a PDF.
- `test_a_hub_without_a_browser_says_so_instead_of_failing_the_report`,
  because the scheduled job must keep sending reports on a hub with no
  Chrome, and only the PDF download should degrade.
"""
import pathlib

import pytest

from guardian_hub import models as m
from guardian_hub.db import SessionLocal
from guardian_hub.reports import builder, pdf
from tests.test_read_write import seeded

HAS_CHROME = pdf.chrome_binary() is not None


def _report(client):
    tid, _ = seeded(client)
    db = SessionLocal()
    try:
        tenant = db.get(m.Tenant, tid)
        report, path = builder.render_to_file(db, tenant, days=7)
        return report.id, path
    finally:
        db.close()


# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------

def test_the_vendored_fonts_are_inlined_so_chrome_cannot_substitute():
    """A missing font does not fail; Chrome swaps one in and reflows every
    page, so the PDF stops matching the web version a client already
    opened. Inlining the bytes is what makes the render self-contained."""
    html = '<style>@font-face{src:url("/fonts/manrope-latin-var.woff2") format("woff2")}</style>'
    out = pdf.inline_fonts(html)
    assert "/fonts/manrope-latin-var.woff2" not in out
    assert 'url("data:font/woff2;base64,' in out


def test_a_font_that_does_not_exist_is_left_alone_not_erased():
    """The absence should be visible in the output, not patched over."""
    html = '<style>src:url("/fonts/does-not-exist.woff2")</style>'
    assert pdf.inline_fonts(html) == html


def test_the_report_css_actually_references_fonts_the_inliner_can_find():
    """Guards the pairing: the CSS names five files and the inliner looks in
    one directory. If either moves, the render silently substitutes."""
    from guardian_hub.reports import css
    import re
    named = set(re.findall(r'/fonts/([A-Za-z0-9_.\-]+\.woff2)', css.REPORT_CSS + css.EXEC_REPORT_CSS))
    assert named, "the report CSS should declare web fonts"
    for name in named:
        assert (pdf.FONT_DIR / name).is_file(), f"{name} is referenced but not vendored"


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

@pytest.mark.skipif(not HAS_CHROME, reason="no Chrome/Chromium on this machine")
def test_the_pdf_is_rendered_from_the_frozen_html_and_cached(client):
    _rid, path = _report(client)
    out = pdf.ensure_pdf(path)
    assert out.suffix == ".pdf" and out.parent == path.parent
    data = out.read_bytes()
    assert data.startswith(b"%PDF"), "the output must actually be a PDF"
    assert len(data) < pdf.MAX_PDF_BYTES

    # Cached: a second call must not re-render.
    stamp = out.stat().st_mtime_ns
    assert pdf.ensure_pdf(path) == out
    assert out.stat().st_mtime_ns == stamp


class _FakeChrome:
    """Stands in for the browser: writes the bytes it is told to at the
    output path and then, like the real thing, does not exit on its own."""

    def __init__(self, payload: bytes, seen: dict | None = None):
        self.payload, self.seen = payload, seen if seen is not None else {}
        self.stderr = None

    def __call__(self, cmd, **kw):
        self.seen["cmd"] = cmd
        out = next(a for a in cmd if a.startswith("--print-to-pdf=")).split("=", 1)[1]
        pathlib.Path(out).write_bytes(self.payload)
        self._alive = True
        return self

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self._alive = False

    kill = terminate

    def wait(self, timeout=None):
        return 0


def test_render_uses_the_binary_it_is_given_and_reads_the_result(monkeypatch, tmp_path):
    """The plumbing, with a fake browser: args reach it, output comes back."""
    import subprocess
    seen = {}
    monkeypatch.setattr(subprocess, "Popen", _FakeChrome(b"%PDF-1.7 fake", seen))
    data = pdf.render_pdf("<html><body>hi</body></html>", binary="/fake/chrome")
    assert data.startswith(b"%PDF")
    assert seen["cmd"][0] == "/fake/chrome"
    assert "--headless=new" in seen["cmd"]
    assert "--print-to-pdf-no-header" in seen["cmd"]


def test_output_that_is_not_a_pdf_is_an_error_not_a_download(monkeypatch):
    """A silent empty or wrong file is the worst outcome of all."""
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", _FakeChrome(b"<html>oops</html>"))
    with pytest.raises(RuntimeError, match="not a PDF"):
        pdf.render_pdf("<html></html>", binary="/fake/chrome")


def test_an_oversized_pdf_is_refused_rather_than_bounced_later(monkeypatch):
    """A 40 MB attachment bounces silently. The cap turns that into an
    error someone sees."""
    import subprocess
    monkeypatch.setattr(pdf, "MAX_PDF_BYTES", 100)
    monkeypatch.setattr(subprocess, "Popen", _FakeChrome(b"%PDF-1.7 " + b"x" * 200))
    with pytest.raises(RuntimeError, match="over the"):
        pdf.render_pdf("<html></html>", binary="/fake/chrome")


def test_no_browser_is_a_named_error(monkeypatch):
    monkeypatch.setattr(pdf, "chrome_binary", lambda: None)
    with pytest.raises(pdf.PdfUnavailable):
        pdf.render_pdf("<html></html>")


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------

def test_a_hub_without_a_browser_says_so_instead_of_failing_the_report(client, monkeypatch):
    """The HTML report must keep working; only the PDF degrades, and it
    says why."""
    rid, _path = _report(client)
    monkeypatch.setattr(pdf, "chrome_binary", lambda: None)
    assert client.get(f"/api/v1/reports/{rid}").status_code == 200
    r = client.get(f"/api/v1/reports/{rid}/pdf")
    assert r.status_code == 503
    assert "GUARDIAN_CHROME" in r.json()["detail"]


@pytest.mark.skipif(not HAS_CHROME, reason="no Chrome/Chromium on this machine")
def test_the_dashboard_serves_the_pdf_as_a_download(client):
    rid, _path = _report(client)
    r = client.get(f"/api/v1/reports/{rid}/pdf")
    assert r.status_code == 200, r.text[:200]
    assert r.headers["content-type"] == "application/pdf"
    assert "attachment" in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF")


@pytest.mark.skipif(not HAS_CHROME, reason="no Chrome/Chromium on this machine")
def test_the_public_api_serves_the_same_pdf(client):
    from tests.test_api_v1 import bearer, seeded as api_seeded
    tid, secret, _ = api_seeded(client)
    db = SessionLocal()
    try:
        report, path = builder.render_to_file(db, db.get(m.Tenant, tid), days=7)
        rid = report.id
    finally:
        db.close()
    r = client.get(f"/v1/reports/{rid}?format=pdf", headers=bearer(secret))
    assert r.status_code == 200, r.text[:200]
    assert r.content.startswith(b"%PDF")
    # Identical bytes to the dashboard's copy: one cache, one document.
    assert r.content == pdf.pdf_path_for(path).read_bytes()


def test_an_unknown_report_is_404_for_the_pdf_too(client):
    seeded(client)
    assert client.get("/api/v1/reports/nope/pdf").status_code == 404
