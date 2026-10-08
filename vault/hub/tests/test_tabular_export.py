"""Every "Export" in the product renders through `tabular.render`."""
import io
import zipfile
from xml.etree import ElementTree as ET

import pytest

from guardian_hub import tabular

COLS = ["Device", "Owner", "Note"]
ROWS = [
    ["macbook", "miro", "fine"],
    ['=HYPERLINK("http://evil","click")', "attacker", "injection attempt"],
    ["ünïcode — dash", None, True],
]


@pytest.mark.parametrize("fmt,ext", [("csv", "csv"), ("xlsx", "xlsx"), ("pdf", "html")])
def test_each_format_names_its_file_and_its_type(fmt, ext):
    body, media, name = tabular.render(fmt, columns=COLS, rows=ROWS, title="Devices", slug="devices")
    assert body, "an export with rows must not be empty"
    assert name.endswith(f".{ext}") and name.startswith("vault-devices-")
    assert media


def test_an_unknown_format_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        tabular.render("docx", columns=COLS, rows=ROWS, title="Devices")


def test_csv_neutralises_a_formula_and_carries_a_bom():
    body, _, _ = tabular.render("csv", columns=COLS, rows=ROWS, title="Devices")
    assert body.startswith(b"\xef\xbb\xbf"), "Excel reads a BOM-less UTF-8 CSV as the local codepage"
    assert b"'=HYPERLINK" in body, "a device name must not become a live formula"


def test_xlsx_opens_keeps_values_verbatim_and_writes_no_formula():
    # The opposite rule to CSV, and deliberately so: an `inlineStr` cell is
    # text by construction, so the apostrophe would buy nothing and would
    # show the analyst a stray quote in front of their own data.
    openpyxl = pytest.importorskip("openpyxl")
    body, _, _ = tabular.render("xlsx", columns=COLS, rows=ROWS, title="Devices")
    ws = openpyxl.load_workbook(io.BytesIO(body)).active
    values = [[c.value for c in row] for row in ws.iter_rows()]
    assert values[0] == COLS
    assert values[2][0] == '=HYPERLINK("http://evil","click")'
    assert ws.cell(row=3, column=1).data_type == "s", "must be a string cell, never a formula"
    # None becomes an empty cell and True becomes "yes": an export is read by
    # a person, and "True" is a programmer's word.
    assert values[3] == ["ünïcode — dash", "", "yes"]


def test_xlsx_is_a_valid_package_and_byte_identical_between_runs():
    body, _, _ = tabular.render("xlsx", columns=COLS, rows=ROWS, title="Devices")
    z = zipfile.ZipFile(io.BytesIO(body))
    assert z.testzip() is None
    assert set(z.namelist()) == {
        "[Content_Types].xml", "_rels/.rels", "xl/workbook.xml",
        "xl/_rels/workbook.xml.rels", "xl/worksheets/sheet1.xml",
    }
    for part in z.namelist():
        ET.fromstring(z.read(part))
    again, _, _ = tabular.render("xlsx", columns=COLS, rows=ROWS, title="Devices")
    assert again == body, "same rows must give the same bytes, so an export can be checksummed"


def test_a_sheet_name_excel_would_reject_is_made_acceptable():
    body, _, _ = tabular.render(
        "xlsx", columns=COLS, rows=ROWS,
        title="Roots/prod:report*2026?[live] and a name far longer than Excel allows",
    )
    name = ET.fromstring(zipfile.ZipFile(io.BytesIO(body)).read("xl/workbook.xml"))[0][0].get("name")
    assert len(name) <= 31 and not set(name) & set("[]:*?/\\")


def test_the_pdf_route_is_printable_html_with_a_repeating_header():
    # No PDF library: the browser's own print engine makes the file, so what
    # matters is that the document tells it how to paginate.
    body, media, name = tabular.render("pdf", columns=COLS, rows=ROWS, title="Devices",
                                       subtitle="Preview Network")
    text = body.decode()
    assert media.startswith("text/html") and name.endswith(".html")
    assert "display: table-header-group" in text, "the header row must repeat on every page"
    assert "page-break-inside: avoid" in text, "a row must not be split across pages"
    assert "Save as PDF" in text and "Preview Network" in text
    assert "&lt;" not in "macbook"  # sanity
    assert "3 rows" in text


def test_an_empty_export_says_so_rather_than_rendering_a_bare_header():
    body, _, _ = tabular.render("pdf", columns=COLS, rows=[], title="Devices")
    assert "Nothing matched this export." in body.decode()
    csv_body, _, _ = tabular.render("csv", columns=COLS, rows=[], title="Devices")
    assert csv_body.decode("utf-8-sig").strip() == "Device,Owner,Note"


def test_html_escapes_markup_in_a_cell():
    body, _, _ = tabular.render("pdf", columns=["Device"], rows=[["<script>alert(1)</script>"]],
                                title="Devices")
    text = body.decode()
    assert "<script>alert(1)</script>" not in text
    assert "&lt;script&gt;" in text
