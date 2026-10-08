#!/usr/bin/env python3
"""Render a Markdown file to PDF.

    python3 tools/md2pdf.py CLAUDE_CODE_AGENT.md

Exists so a generated PDF is never the only copy of anything. A PDF checked in
beside its source drifts the moment the source is edited, and a stale PDF that
contradicts the Markdown is worse than no PDF at all — so the PDFs are
git-ignored and this regenerates them.

Uses headless Chrome rather than a PDF library: it is already on the machine,
and it is the same engine that renders the HTML, so what is printed is what a
browser would show. Requires `markdown` (system Python has it).

One Markdown trap this guards against by construction: Python-Markdown will
not let a list interrupt a paragraph, so

    **A label**
    - first item

renders as one run-on paragraph rather than a list. `--check` reports that
instead of letting it reach the page.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tempfile

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

CSS = """
  @page { size: A4; margin: 18mm 16mm 20mm 16mm; }
  * { box-sizing: border-box; }
  body { font: 10.5pt/1.55 -apple-system, "Helvetica Neue", Arial, sans-serif;
         color: #16202e; margin: 0; }
  h1 { font-size: 20pt; line-height: 1.2; margin: 0 0 4pt; letter-spacing: -.01em;
       border-bottom: 2px solid #1257c4; padding-bottom: 6pt; }
  h2 { font-size: 13.5pt; margin: 20pt 0 6pt; color: #0f2f68;
       border-bottom: 1px solid #d5dde8; padding-bottom: 3pt;
       break-after: avoid; page-break-after: avoid; }
  h3 { font-size: 11.5pt; margin: 14pt 0 4pt; color: #24344b;
       break-after: avoid; page-break-after: avoid; }
  p, li { orphans: 2; widows: 2; }
  ul, ol { padding-left: 18pt; margin: 6pt 0; }
  li { margin: 2.5pt 0; }
  code { font: 9pt/1.4 "SF Mono", Menlo, Consolas, monospace;
         background: #eef2f7; padding: 1pt 3pt; border-radius: 2px; }
  pre { background: #f6f8fb; border: 1px solid #dde4ee; border-left: 3px solid #1257c4;
        padding: 8pt 10pt; border-radius: 3px; overflow-wrap: break-word;
        white-space: pre-wrap; break-inside: avoid; page-break-inside: avoid; }
  pre code { background: none; padding: 0; font-size: 8.5pt; }
  table { border-collapse: collapse; width: 100%; margin: 8pt 0; font-size: 9.5pt;
          break-inside: avoid; page-break-inside: avoid; }
  th, td { border: 1px solid #ccd6e3; padding: 5pt 7pt; text-align: left;
           vertical-align: top; }
  th { background: #eef3fa; font-weight: 600; }
  tr:nth-child(even) td { background: #fafcfe; }
  blockquote { margin: 8pt 0; padding: 6pt 12pt; border-left: 3px solid #1257c4;
               background: #f4f8fd; }
  blockquote p { margin: 0; }
  hr { border: 0; border-top: 1px solid #dde4ee; margin: 16pt 0; }
  strong { color: #0f2f68; }
  a { color: #1257c4; text-decoration: none; }
"""

LIST = re.compile(r"^\s*[-*]\s")


def collapsed_lists(text: str) -> list[int]:
    """1-based line numbers where a list starts under a paragraph line."""
    lines = text.split("\n")
    bad = []
    for i, line in enumerate(lines[:-1]):
        nxt = lines[i + 1]
        if (line.strip() and LIST.match(nxt) and not LIST.match(line)
                and not line.startswith("  ")
                and not line.lstrip().startswith(("|", ">", "#", "`"))):
            bad.append(i + 1)
    return bad


def to_html(src: pathlib.Path) -> str:
    import markdown

    body = markdown.markdown(
        src.read_text(encoding="utf-8"),
        extensions=["tables", "fenced_code", "codehilite", "toc", "sane_lists"],
        extension_configs={
            "codehilite": {"noclasses": True, "pygments_style": "friendly"}},
    )
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f"<title>{src.stem}</title><style>{CSS}</style></head>"
            f"<body>{body}</body></html>")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", type=pathlib.Path)
    ap.add_argument("-o", "--out", type=pathlib.Path)
    ap.add_argument("--check", action="store_true",
                    help="report Markdown that will not render, and stop")
    args = ap.parse_args()

    if not args.source.is_file():
        print(f"no such file: {args.source}", file=sys.stderr)
        return 1

    bad = collapsed_lists(args.source.read_text(encoding="utf-8"))
    if bad:
        where = ", ".join(str(n) for n in bad[:10])
        print(f"{args.source}: list starts directly under a paragraph at line(s) "
              f"{where} — it will render as one run-on paragraph. Add a blank "
              f"line above each.", file=sys.stderr)
        if args.check:
            return 1
    if args.check:
        print(f"{args.source}: renders cleanly")
        return 0

    out = args.out or args.source.with_suffix(".pdf")
    with tempfile.TemporaryDirectory() as tmp:
        html = pathlib.Path(tmp) / f"{args.source.stem}.html"
        html.write_text(to_html(args.source), encoding="utf-8")
        proc = subprocess.run(
            [CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
             f"--print-to-pdf={out.resolve()}", html.resolve().as_uri()],
            capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not out.exists():
        print(proc.stderr.strip()[-400:] or "chrome failed", file=sys.stderr)
        return 1
    print(f"{out} ({out.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
