"""One table, three files: CSV, XLSX and a print-ready HTML for PDF.

Every "Export" in the product hands its rows to `render()` and gets back
bytes plus the media type and filename to serve them under, so History,
Inbox, Security and Reports cannot drift into three different ideas of what
an export is.

**Why no library.** A real `.xlsx` is a zip of XML parts, and the subset a
data table needs is small enough to write against the stdlib. Vault is a
security product: every dependency is supply-chain surface the customer
inherits, and `openpyxl` (plus its own tree) is a lot of it to add for
"write cells into a grid". `zipfile` and `xml` are already in the runtime.

**Why PDF is the browser's job.** A faithful PDF needs a text layouter with
fonts, pagination and table breaking -- reportlab or weasyprint, the latter
pulling native cairo/pango. Instead `render(fmt="pdf")` returns a
self-contained HTML document carrying a print stylesheet (repeating header
row, no page break inside a row, landscape at A4), which the browser turns
into a real PDF through its own engine at "Save as PDF". No dependency, no
second rendering path to keep matching the screen, and the output paginates
correctly because a browser is very good at exactly this.
"""
from __future__ import annotations

import csv
import datetime as dt
import html
import io
import re
import zipfile

#: Excel refuses to open a workbook whose sheet name carries any of these, or
#: one longer than 31 characters.
_SHEET_BAD = re.compile(r"[\[\]:*?/\\]")

#: A leading one of these makes Excel (and Sheets) evaluate the cell as a
#: formula. A hostname or an alert message is never a formula, and an attacker
#: who can get text into a scan result should not be able to get it executed
#: in the analyst's spreadsheet. See `_guard`.
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")


def _text(value: object) -> str:
    """One cell's value as plain text. No escaping: that is the format's job."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def _guard(value: object) -> str:
    """`_text`, plus the CSV-injection guard -- **for CSV only**.

    A device whose name is `=HYPERLINK("http://evil","click")` is a device
    name in Vault and a live formula the moment Excel opens the CSV.
    Prefixing a single quote makes the cell literal text in Excel, Sheets and
    LibreOffice alike.

    XLSX deliberately does **not** use this. A cell written as `inlineStr` is
    text by construction -- a formula needs an `<f>` element, which this
    module never writes -- so the prefix would buy no safety and would show
    the analyst a stray apostrophe in front of their own data.
    """
    text = _text(value)
    if text.startswith(_FORMULA_LEAD):
        return "'" + text
    return text


def sheet_name(name: str) -> str:
    cleaned = _SHEET_BAD.sub(" ", name).strip() or "Export"
    return cleaned[:31]


def _csv_bytes(columns: list[str], rows: list[list[object]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_guard(v) for v in row])
    # BOM: Excel on Windows reads a BOM-less UTF-8 CSV as the local codepage,
    # which mangles every non-ASCII hostname and every degree sign.
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")


def _col_ref(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    ref = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        ref = chr(65 + rem) + ref
    return ref


def _xlsx_bytes(columns: list[str], rows: list[list[object]], title: str) -> bytes:
    """A minimal but genuinely valid single-sheet workbook.

    Every value is written as an inline string (`t="inlineStr"`) rather than a
    shared string table: it costs a little size and removes a whole class of
    index-mismatch corruption, and an export is read once, not edited.
    """
    def cell(ref: str, text: str) -> str:
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{html.escape(text)}</t></is></c>'

    body = [
        '<row r="1">' + "".join(cell(f"{_col_ref(i)}1", str(c)) for i, c in enumerate(columns)) + "</row>",
    ]
    for n, row in enumerate(rows, start=2):
        body.append(
            f'<row r="{n}">'
            + "".join(cell(f"{_col_ref(i)}{n}", _text(v)) for i, v in enumerate(row))
            + "</row>"
        )
    # `freezePane` on row 2 keeps the header visible while scrolling, which is
    # the one affordance that makes a long export usable at all.
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0" tabSelected="1">'
        '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
        "</sheetView></sheetViews>"
        f"<sheetData>{''.join(body)}</sheetData></worksheet>"
    )
    safe_title = html.escape(sheet_name(title))
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{safe_title}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"'
        ' Target="worksheets/sheet1.xml"/></Relationships>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
        ' Target="xl/workbook.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml"'
        ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml"'
        ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    out = io.BytesIO()
    # Deterministic: a fixed timestamp on every entry means the same rows
    # produce the same bytes, so an export can be diffed or checksummed.
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in (
            ("[Content_Types].xml", content_types),
            ("_rels/.rels", root_rels),
            ("xl/workbook.xml", workbook),
            ("xl/_rels/workbook.xml.rels", workbook_rels),
            ("xl/worksheets/sheet1.xml", sheet),
        ):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, text)
    return out.getvalue()


_PRINT_CSS = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 28px 30px; background: #fff; color: #10151d;
       font: 13px/1.5 ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
header { border-bottom: 2px solid #1257c4; padding-bottom: 12px; margin-bottom: 18px; }
h1 { margin: 0 0 4px; font-size: 19px; letter-spacing: -0.01em; }
.meta { font: 11px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
        letter-spacing: .04em; text-transform: uppercase; color: #4a6280; }
table { border-collapse: collapse; width: 100%; font-size: 11.5px; }
th { text-align: left; background: #f1f5fb; border-bottom: 1px solid #c3d3e6;
     padding: 7px 8px; font: 10px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
     letter-spacing: .08em; text-transform: uppercase; color: #29405c; }
td { padding: 6px 8px; border-bottom: 1px solid #e6edf7; vertical-align: top;
     word-break: break-word; }
tbody tr:nth-child(even) td { background: #fafcff; }
footer { margin-top: 16px; font-size: 10.5px; color: #4a6280; }
.empty { padding: 22px; text-align: center; color: #4a6280; }
@page { size: A4 landscape; margin: 12mm; }
@media print {
  body { padding: 0; }
  /* The header row repeats on every printed page, and no row is split
     across a page break -- the two things that make a printed table
     readable and that a screenshot of a scrolling div never gives you. */
  thead { display: table-header-group; }
  tr { page-break-inside: avoid; }
  .hint { display: none; }
}
.hint { margin: 0 0 14px; padding: 9px 11px; border: 1px solid #c3d3e6;
        border-radius: 4px; background: #f1f5fb; font-size: 12px; color: #29405c; }
"""


def _html_bytes(columns: list[str], rows: list[list[object]], title: str, subtitle: str) -> bytes:
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in columns)
    if rows:
        body = "".join(
            "<tr>" + "".join(f"<td>{html.escape(_text(v))}</td>" for v in row) + "</tr>"
            for row in rows
        )
        table = f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    else:
        table = '<p class="empty">Nothing matched this export.</p>'
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{html.escape(title)}</title><style>{_PRINT_CSS}</style></head><body>"
        "<p class=\"hint\">Use your browser's Print dialog and choose "
        "<b>Save as PDF</b>. The header row repeats on every page.</p>"
        f"<header><h1>{html.escape(title)}</h1>"
        f"<div class=\"meta\">{html.escape(subtitle)} &middot; generated {stamp} &middot; {len(rows)} rows</div>"
        "</header>"
        f"{table}"
        "<footer>Generated by Vault. Every row here is an observation the "
        "sensor recorded, not an estimate.</footer>"
        "</body></html>"
    ).encode("utf-8")


#: `fmt` -> (extension, media type). "pdf" is served as HTML on purpose: the
#: browser's own print engine makes the PDF (see the module docstring).
FORMAT_SPEC: dict[str, tuple[str, str]] = {
    "csv": ("csv", "text/csv; charset=utf-8"),
    "xlsx": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    "pdf": ("html", "text/html; charset=utf-8"),
}


def render(fmt: str, *, columns: list[str], rows: list[list[object]],
           title: str, subtitle: str = "", slug: str = "export") -> tuple[bytes, str, str]:
    """`(body, media_type, filename)` for one table in one of three formats.

    Raises `ValueError` for an unknown format so a caller can turn it into a
    422 rather than guessing on the client's behalf.
    """
    if fmt not in FORMAT_SPEC:
        raise ValueError(f"format must be one of {', '.join(sorted(FORMAT_SPEC))}")
    ext, media = FORMAT_SPEC[fmt]
    if fmt == "csv":
        body = _csv_bytes(columns, rows)
    elif fmt == "xlsx":
        body = _xlsx_bytes(columns, rows, title)
    else:
        body = _html_bytes(columns, rows, title, subtitle)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    # The `vault-` prefix is COPY, not an identifier: it is the filename a
    # customer sees in their Downloads folder next to exports from every other
    # tool they use, which is the whole reason it carries the product name at
    # all. Nothing keys off it -- `web/src/lib/api.ts` and `ExportMenu.tsx`
    # prefer whatever `Content-Disposition` says and only fall back to a name
    # of their own when the header is missing. Those two fallbacks are the one
    # place this spelling is duplicated; change them with it.
    return body, media, f"vault-{slug}-{stamp}.{ext}"
