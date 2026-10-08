"""The ebook edition of a course: the same lesson files as the videos, as
one PDF, plus a square store cover. Miro, 2026-10-04: "teach them via
course and ebook, they choose". Runs on the Mac beside build.py (the vault
is here); Playwright prints the page.

  python tools/lesson_video/ebook.py "<vault course dir>" <out dir> \
      --title "Sell With AI" --sub "Clips, digital products and an online store"
"""
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

from build import _blocks, _table, lesson_files, shown

MODULES_DEFAULT = "Module"

CSS = """
@page { size: Letter; margin: 22mm 20mm 22mm 20mm; }
* { box-sizing: border-box; }
body { font: 11.5pt/1.55 "Atkinson Hyperlegible Next","Atkinson Hyperlegible",-apple-system,
       "Helvetica Neue",Arial,sans-serif; color: #14212b; margin: 0; }
.cover { page-break-after: always; height: 235mm; display: flex; flex-direction: column;
         justify-content: center; }
.cover .mark { font-weight: 800; letter-spacing: .08em; color: #0a7fa6; font-size: 12pt; }
.cover h1 { font-size: 46pt; line-height: 1.02; margin: 18mm 0 6mm; letter-spacing: -.02em; }
.cover p { font-size: 15pt; color: #3a4c5a; max-width: 130mm; margin: 0 0 4mm; }
.cover .by { margin-top: 20mm; font-size: 11pt; color: #5b6f7d; }
.toc { page-break-after: always; }
.toc h2 { font-size: 20pt; margin: 0 0 6mm; }
.toc h3 { font-size: 11pt; color: #0a7fa6; margin: 6mm 0 1mm; letter-spacing: .02em; }
.toc ol { list-style: none; padding: 0; margin: 0; }
.toc li { padding: 1.2mm 0; border-bottom: 1px solid #e3e9ee; }
.toc li b { display: inline-block; width: 12mm; color: #5b6f7d; font-weight: 600; }
.lesson { page-break-before: always; }
.lesson .num { font-size: 10.5pt; color: #0a7fa6; font-weight: 700; letter-spacing: .04em; }
.lesson h1 { font-size: 24pt; line-height: 1.1; margin: 1mm 0 7mm; letter-spacing: -.01em; }
h2 { font-size: 14pt; margin: 8mm 0 2mm; page-break-after: avoid; }
p { margin: 0 0 3.2mm; }
ul, ol { margin: 0 0 3.5mm; padding-left: 6mm; }
li { margin: 1.2mm 0; }
blockquote { margin: 4mm 0; padding: 3mm 5mm; border-left: 3px solid #0a7fa6; background: #f1f7fa;
             font-size: 12.5pt; page-break-inside: avoid; }
table { border-collapse: collapse; width: 100%; margin: 3mm 0 4mm; font-size: 10.5pt;
        page-break-inside: avoid; }
th, td { text-align: left; padding: 2mm 2.5mm; border-bottom: 1px solid #d5dee5; vertical-align: top; }
th { color: #0a7fa6; }
code { font-family: ui-monospace, Menlo, monospace; font-size: .9em; background: #eef3f6;
       padding: 0 3px; border-radius: 3px; }
pre { background: #f3f6f8; padding: 3mm; border-radius: 4px; font-size: 9.5pt; white-space: pre-wrap; }
.end { page-break-before: always; }
"""


def lesson_html(md: str, lid: str) -> tuple[str, str]:
    blocks = _blocks(md.splitlines())
    title = next((b[1][0] for b in blocks if b[0] == "h1"), lid)
    short = re.sub(r"^Lesson\s+[\d.]+\s*[:\-–]\s*", "", title)
    out = [f'<section class="lesson"><p class="num">Lesson {lid}</p><h1>{html.escape(short)}</h1>']
    for kind, lines in blocks:
        if kind == "h1":
            continue
        if kind in ("h2", "h3"):
            out.append(f"<h2>{shown(lines[0])}</h2>")
        elif kind == "p":
            if lines[0].startswith("Last verified:"):
                continue
            out.append(f"<p>{shown(lines[0])}</p>")
        elif kind in ("ul", "ol"):
            out.append(f"<{kind}>" + "".join(f"<li>{shown(x)}</li>" for x in lines) + f"</{kind}>")
        elif kind == "quote":
            out.append(f"<blockquote>{shown(' '.join(lines))}</blockquote>")
        elif kind == "table":
            head, rows = _table(lines)
            out.append("<table><tr>" + "".join(f"<th>{shown(c)}</th>" for c in head) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{shown(c)}</td>" for c in r) + "</tr>"
                                 for r in rows) + "</table>")
        elif kind == "code":
            out.append("<pre>" + html.escape("\n".join(lines)) + "</pre>")
    out.append("</section>")
    return short, "".join(out)


def document(course_dir: Path, title: str, sub: str, author: str, modules: list[str]) -> str:
    toc: dict[str, list[tuple[str, str]]] = {}
    body = []
    for lid, path in lesson_files(course_dir):
        short, block = lesson_html(path.read_text(), lid)
        toc.setdefault(lid.split(".")[0], []).append((lid, short))
        body.append(block)
    toc_html = ['<section class="toc"><h2>Contents</h2>']
    for n, (mod, items) in enumerate(toc.items()):
        name = modules[n] if n < len(modules) else f"Part {mod}"
        toc_html.append(f"<h3>{html.escape(name)}</h3><ol>"
                        + "".join(f"<li><b>{lid}</b>{html.escape(t)}</li>" for lid, t in items)
                        + "</ol>")
    toc_html.append("</section>")
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>{CSS}</style></head><body>
<section class="cover"><p class="mark">ATLAS ACADEMY</p><h1>{html.escape(title)}</h1>
<p>{html.escape(sub)}</p><p class="by">{html.escape(author)}</p></section>
{''.join(toc_html)}{''.join(body)}
</body></html>"""


COVER_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:800px;height:800px;background:#06111a;color:#fff;
font-family:"Atkinson Hyperlegible Next",-apple-system,"Helvetica Neue",Arial,sans-serif;overflow:hidden}
body::before{content:"";position:absolute;inset:0;
background-image:linear-gradient(rgba(47,208,255,.09) 1px,transparent 1px),linear-gradient(90deg,rgba(47,208,255,.09) 1px,transparent 1px);
background-size:50px 50px}
body::after{content:"";position:absolute;inset:0;background:radial-gradient(circle at 80% 15%,rgba(47,208,255,.35),transparent 55%)}
.c{position:relative;z-index:1;padding:64px;height:100%;display:flex;flex-direction:column;justify-content:space-between}
.m{font-weight:800;letter-spacing:.1em;color:#2fd0ff;font-size:24px}.m span{color:#b9c9d6;font-weight:400;letter-spacing:.02em}
h1{font-size:112px;line-height:.98;letter-spacing:-.03em;font-weight:800}
p{font-size:30px;line-height:1.3;color:#d5e3ec;max-width:620px}
.l{display:flex;gap:14px;font-size:22px;color:#06111a}.l b{background:#2fd0ff;padding:8px 16px;border-radius:999px;font-weight:700}
"""


def cover_html(title: str, sub: str, chips: list[str]) -> str:
    chip_html = "".join(f"<b>{html.escape(c)}</b>" for c in chips)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{COVER_CSS}</style></head><body>
<div class="c"><div class="m">ATLAS <span>Academy</span></div>
<div><h1>{html.escape(title)}</h1><p style="margin-top:28px">{html.escape(sub)}</p></div>
<div class="l">{chip_html}</div></div></body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("course_dir")
    ap.add_argument("out")
    ap.add_argument("--title", required=True)
    ap.add_argument("--sub", default="")
    ap.add_argument("--author", default="Miro (Myron Mekhael)")
    ap.add_argument("--modules", default="")
    ap.add_argument("--chips", default="")
    ap.add_argument("--slug", default="ebook")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    doc = document(Path(a.course_dir), a.title, a.sub, a.author,
                   [m.strip() for m in a.modules.split("|") if m.strip()])
    (out / f"{a.slug}.html").write_text(doc)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_content(doc)
        page.pdf(path=str(out / f"{a.slug}.pdf"), format="Letter", print_background=True,
                 display_header_footer=True, header_template="<span></span>",
                 footer_template=('<div style="font-size:8pt;color:#7b8a96;width:100%;'
                                  'text-align:center"><span class="pageNumber"></span></div>'),
                 margin={"top": "22mm", "bottom": "22mm", "left": "20mm", "right": "20mm"})
        cover = browser.new_page(viewport={"width": 800, "height": 800})
        cover.set_content(cover_html(a.title, a.sub, [c.strip() for c in a.chips.split("|") if c.strip()]))
        cover.screenshot(path=str(out / f"{a.slug}-cover.png"))
        browser.close()
    print(f"built {out / (a.slug + '.pdf')}")


if __name__ == "__main__":
    main()
