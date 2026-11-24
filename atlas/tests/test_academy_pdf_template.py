"""The Academy PDF template (tools/academy_pdf): one template, two modes.

Miro, 2026-11-14: every PDF exists in a dark (screen) and a white (print)
version, built from the same template. Page size belongs to the product, not
to the mode. Render checks need headless Chrome and poppler (Mac only) and
skip where those are missing.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.academy_pdf import build, template, tokens

CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
HAVE_RENDER = CHROME.is_file() and shutil.which("pdftotext") is not None
needs_render = pytest.mark.skipif(not HAVE_RENDER, reason="needs Chrome and poppler (Mac)")

LABELS = ("Rule", "Try this", "Checkpoint", "Warning", "Example", "Takeaway")
SIZES = {"safe-ai-operator": "A4", "30-business-lessons": "A5", "safe-and-simple-ai-60plus": "Letter"}


def test_there_are_seven_products_and_two_modes():
    assert len(tokens.PRODUCTS) == 7
    assert tuple(tokens.MODES) == ("dark", "print")
    for key, spec in tokens.PRODUCTS.items():
        names = build.output_names(key)
        assert names == {"dark": f"{spec['file']}.pdf", "print": f"{spec['file']}-print.pdf"}
    assert len(build.all_outputs()) == 14


@pytest.mark.parametrize("label", LABELS)
def test_each_callout_has_its_word_label(label):
    html = template.callout(label, "<p>Body</p>")
    assert f'<span class="co-label">{label}</span>' in html
    assert 'class="callout co-' in html


def test_an_unknown_callout_label_is_refused():
    with pytest.raises(ValueError):
        template.callout("Hint", "<p>x</p>")


def test_source_headings_become_labelled_callouts():
    body = ("<h2>The rule</h2><p>a</p><h2>Try it</h2><p>b</p><h2>Quick check</h2><p>c</p>"
            "<h2>Safety reminder</h2><p>d</p><h2>The story</h2><p>e</p>"
            "<h2>What you have now</h2><p>f</p><h2>How it works</h2><p>g</p>")
    out = template.enrich(body)
    for label in LABELS:
        assert f'<span class="co-label">{label}</span>' in out
    # an ordinary section stays ordinary and keeps its heading text
    assert "<h2>How it works</h2>" in out
    # every source heading text is kept (no content dropped)
    for h in ("The rule", "Try it", "Quick check", "Safety reminder", "The story"):
        assert f"<h2>{h}</h2>" in out


def test_blockquote_what_you_should_see_is_a_checkpoint():
    out = template.enrich("<blockquote><p><strong>What you should see:</strong> a tick</p></blockquote>")
    assert '<span class="co-label">Checkpoint</span>' in out


@pytest.mark.parametrize("mode", tokens.MODES)
def test_contrast_pairs_pass_wcag_in_each_mode(mode):
    pairs = tokens.contrast_pairs(mode)
    assert len(pairs) >= 8
    for name, fg, bg, need in pairs:
        assert tokens.contrast(fg, bg) >= need, (mode, name, fg, bg)


def test_the_contrast_function_matches_wcag():
    assert round(tokens.contrast("#000000", "#FFFFFF"), 1) == 21.0
    assert round(tokens.contrast("#FFFFFF", "#FFFFFF"), 1) == 1.0


def test_no_retired_hue_is_used():
    old = ("291b36", "faf8f3", "f5c451", "cdbee0", "b9acc7", "5b4775", "e5a43c", "f4efe4")
    blob = (ROOT / "tools/academy_pdf/tokens.py").read_text().lower() + template.css("dark", "A4", 11).lower()
    assert not [h for h in old if h in blob]


@pytest.mark.parametrize("mode", tokens.MODES)
@pytest.mark.parametrize("key,size", list(SIZES.items()))
def test_page_size_is_per_product_not_per_mode(key, size, mode):
    assert tokens.PRODUCTS[key]["page"] == size
    css = template.css(mode, size, tokens.PRODUCTS[key]["body_pt"])
    assert f"size: {size};" in css or f"size:{size};" in css


def test_other_products_keep_their_current_file_size():
    assert tokens.PRODUCTS["everyday-ai-for-adults"]["page"] in tokens.PAGE_MM
    for spec in tokens.PRODUCTS.values():
        assert spec["page"] in tokens.PAGE_MM


@pytest.mark.parametrize("mode", tokens.MODES)
def test_sixty_plus_body_is_17pt_in_both_modes(mode):
    spec = tokens.PRODUCTS["safe-and-simple-ai-60plus"]
    assert spec["body_pt"] == 17
    assert re.search(r"body\s*\{[^}]*font-size:\s*17pt", template.css(mode, spec["page"], 17))


def test_the_modes_differ_only_in_colour():
    dark = template.css("dark", "A4", 11)
    prn = template.css("print", "A4", 11)
    assert "#0A0D14" in dark and "#0A0D14" not in prn.split(".cover")[0]
    strip = lambda s: re.sub(r"#[0-9A-Fa-f]{6}\b|rgba?\([^)]*\)", "C", s)
    assert strip(dark).replace("--mode:dark", "") == strip(prn).replace("--mode:print", "")


def test_the_cover_stays_dark_in_the_print_version():
    html = template.document(tokens.PRODUCTS["30-business-lessons"], "print", "<p>x</p>", [], {})
    assert 'class="cover"' in html
    assert re.search(r"\.cover\s*\{[^}]*background:\s*#0A0D14", html)


def test_the_contents_page_uses_the_page_numbers_it_is_given():
    entries = [("Part 1: Start", "", "", ""), ("", "l0-1", "0.1", "Welcome"), ("", "l0-2", "0.2", "Next")]
    out = template.toc(entries, {"l0-1": 5, "l0-2": 9})
    assert re.search(r"0\.1.*Welcome.*>5<", out, re.DOTALL) and re.search(r"0\.2.*Next.*>9<", out, re.DOTALL)
    assert "Part 1: Start" in out


def test_find_pages_reads_the_markers_from_the_printed_pages():
    pages = ["cover", "contents @@l0-1@@ no", "x", "Welcome\n@@l0-1@@\nbody", "@@l0-2@@ more"]
    # the contents page only lists keys it does not carry as markers: first real hit after toc wins
    found = build.find_pages(pages, skip_before=2)
    assert found == {"l0-1": 4, "l0-2": 5}


def test_markers_are_absent_from_the_final_html():
    unit = template.Unit(key="a", kicker="Lesson 1.1", title="T", html="<p>x</p>", num="1.1")
    with_m = template.lesson(unit, markers=True)
    without = template.lesson(unit, markers=False)
    assert "@@a@@" in with_m and "@@" not in without


def test_fill_in_shorthand_expands():
    out = build.fillins("[[lines 2]]\n\n[[field Name]]\n\n[[rows 2: A | B]]")
    assert out.count('class="lines"') == 2 and "Name" in out and out.count("<tr>") == 3


def test_the_old_walkthrough_numbers_become_steps():
    out = template.enrich("<p><strong>Step 3.</strong> Do it</p>")
    assert 'class="step"' in out and ">3<" in out


def test_repack_adds_only_the_print_guide(tmp_path):
    old = tmp_path / "old.zip"
    with zipfile.ZipFile(old, "w") as z:
        z.writestr("Pack/Pack.pdf", b"old")
        z.writestr("Pack/workflows/a.json", b"{}")
    dark = tmp_path / "d.pdf"
    prn = tmp_path / "p.pdf"
    dark.write_bytes(b"%PDF dark")
    prn.write_bytes(b"%PDF print")
    new = tmp_path / "new.zip"
    build.repack(old, new, "Pack/Pack.pdf", "Pack/Pack (print version).pdf", dark, prn)
    with zipfile.ZipFile(new) as z:
        assert z.testzip() is None
        names = sorted(z.namelist())
        assert names == sorted(["Pack/Pack.pdf", "Pack/Pack (print version).pdf", "Pack/workflows/a.json"])
        assert z.read("Pack/Pack.pdf") == b"%PDF dark"
        assert z.read("Pack/workflows/a.json") == b"{}"


def test_repack_refuses_an_unsafe_member_name(tmp_path):
    old = tmp_path / "old.zip"
    with zipfile.ZipFile(old, "w") as z:
        z.writestr("../evil.txt", b"x")
    with pytest.raises(ValueError):
        build.repack(old, tmp_path / "n.zip", "a.pdf", "b.pdf", tmp_path / "x", tmp_path / "y")


# ------------------------------------------------------------ render (Mac)

SAMPLE = """# Lesson 1.1: Sample lesson

## The story

A short story with enough words to sit on a page.

## The rule

> **Unknown is not OK.**

## Try it

1. Do a thing.
2. Do another.

## Safety reminder

Use made-up data.
"""


def _sample_units(tmp: Path):
    return [build.unit_from_markdown(SAMPLE, key="l1-1", fallback="Sample"),
            build.unit_from_markdown(SAMPLE, key="l1-2", fallback="Sample 2")]


@needs_render
@pytest.mark.parametrize("mode", tokens.MODES)
def test_a_sample_book_prints_with_matching_contents_numbers(mode):
    spec = dict(tokens.PRODUCTS["30-business-lessons"], title="Sample book")
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / f"s-{mode}.pdf"
        report = build.print_book(spec, mode, _sample_units(Path(d)), out)
        assert out.stat().st_size > 5000
        info = subprocess.run(["pdfinfo", str(out)], capture_output=True, text=True, check=False).stdout
        assert "A5" in info or "419.5 x 595" in info or "420 x 59" in info
        # every contents number points at a page that carries the lesson title
        for key, page in report["pages"].items():
            text = subprocess.run(["pdftotext", "-f", str(page), "-l", str(page), str(out), "-"],
                                  capture_output=True, text=True, check=False).stdout
            assert "Sample" in text, (key, page)
        # callout labels reached the paper
        full = subprocess.run(["pdftotext", str(out), "-"], capture_output=True, text=True, check=False).stdout
        for label in ("Rule", "Try this", "Warning", "Example"):
            assert label in full
        assert "@@" not in full
