"""Build the seven Academy PDFs, each in a dark (screen) and a white (print) version.

    python -m tools.academy_pdf.build pdf [key ...] [--vault DIR] [--out DIR]
    python -m tools.academy_pdf.build zip [--vault DIR] [--out DIR]
    python -m tools.academy_pdf.build check [--out DIR]

Sources are the markdown files in the ATLAS Vault (the same ones today's PDFs came from). Output goes
only under data/academy-staging/ (never the live files/ folder). The contents page gets real page
numbers from a two-pass print: pass one carries hidden markers that pdftotext finds, the final print
carries the numbers. PDFs are printed with headless Chrome, which on macOS sometimes stays open after
writing, so the file is watched until it stops growing and Chrome is then stopped.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.academy_pdf import template, tokens
from tools.academy_pdf.template import Unit

CHROME = Path(os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"))
VAULT = Path(os.environ.get("ATLAS_VAULT_PRODUCTS", "/Users/mac/Documents/ATLAS Vault/Products"))
STAGING = ROOT / "data" / "academy-staging"
MD_EXT = ["tables", "fenced_code", "attr_list", "md_in_html", "sane_lists"]
KICKER_RE = re.compile(r"^((?:Lesson|Chapter|Part|Module)\s+[\d.]*\d|Bonus)\s*[-–—:]\s*(.+)$")


# ------------------------------------------------------------------ names

def output_names(key: str) -> dict[str, str]:
    f = tokens.PRODUCTS[key]["file"]
    return {"dark": f"{f}.pdf", "print": f"{f}-print.pdf"}


def all_outputs() -> list[str]:
    return [name for key in tokens.PRODUCTS for name in output_names(key).values()]


# ------------------------------------------------------------------ markdown

def fillins(src: str) -> str:
    """Expand the [[...]] fill-in shorthand into raw HTML blocks."""

    def lines(m: re.Match) -> str:
        return '<div class="wlines">' + '<div class="lines"></div>' * int(m.group(1)) + "</div>"

    def field(m: re.Match) -> str:
        return (f'<div class="field"><span class="lab">{html.escape(m.group(1).strip())}</span>'
                '<span class="ln"></span></div>')

    def pair(m: re.Match) -> str:
        cells = "".join(
            f'<div class="field"><span class="lab">{html.escape(c.strip())}</span><span class="ln"></span></div>'
            for c in m.group(1).split("|"))
        return f'<div class="pair">{cells}</div>'

    def rows(m: re.Match) -> str:
        heads = [h.strip() for h in m.group(2).split("|")]
        head = "".join(f"<th>{html.escape(h)}</th>" for h in heads)
        body = ("<tr>" + "<td></td>" * len(heads) + "</tr>") * int(m.group(1))
        return f'<table class="fill"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'

    src = re.sub(r"^\[\[lines (\d+)\]\]$", lines, src, flags=re.MULTILINE)
    src = re.sub(r"^\[\[field (.+?)\]\]$", field, src, flags=re.MULTILINE)
    src = re.sub(r"^\[\[pair (.+?)\]\]$", pair, src, flags=re.MULTILINE)
    src = re.sub(r"^\[\[rows (\d+): (.+?)\]\]$", rows, src, flags=re.MULTILINE)
    return src


def md_to_html(src: str) -> str:
    import markdown
    return markdown.markdown(fillins(src), extensions=MD_EXT, output_format="html5")


def unit_from_markdown(md: str, key: str, fallback: str, num: str = "", part: str = "",
                       desc: str = "") -> Unit:
    lines = md.splitlines()
    title, kicker = fallback, ""
    if lines and lines[0].startswith("# "):
        h1 = lines[0][2:].strip()
        lines = lines[1:]
        m = KICKER_RE.match(h1)
        if m:
            kicker, title = m.group(1), m.group(2)
        else:
            title = h1
    if not num:
        n = re.search(r"\d+(?:\.\d+)*", kicker)
        num = n.group(0) if n else ""
    body = template.enrich(md_to_html("\n".join(lines)))
    return Unit(key=key, kicker=kicker, title=title, html=body, num=num, part=part, desc=desc)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ------------------------------------------------------------------ page numbers

def find_pages(pages_text: list[str], skip_before: int = 2) -> dict[str, int]:
    """First page (1-based) after `skip_before` that carries each @@key@@ marker."""
    found: dict[str, int] = {}
    for i, text in enumerate(pages_text, start=1):
        if i <= skip_before:
            continue
        for key in re.findall(r"@@([A-Za-z0-9_-]+)@@", text):
            found.setdefault(key, i)
    return found


def page_texts(pdf: Path) -> list[str]:
    out = subprocess.run(["pdftotext", "-layout", str(pdf), "-"], capture_output=True, text=True, check=False).stdout
    pieces = out.split("\f")
    if pieces and not pieces[-1].strip():
        pieces.pop()
    return pieces


def word_count(pdf: Path) -> int:
    out = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True, check=False).stdout
    return len(out.split())


# ------------------------------------------------------------------ chrome

def _run_chrome(out: Path, args: list[str], profile: Path, timeout_s: int = 240) -> None:
    out.unlink(missing_ok=True)
    proc = subprocess.Popen(
        [str(CHROME), "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
         f"--user-data-dir={profile}", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    last = -1
    try:
        for _ in range(timeout_s * 2):
            time.sleep(0.5)
            if proc.poll() is not None:
                break
            if out.is_file() and out.stat().st_size > 0:
                size = out.stat().st_size
                if size == last:
                    break
                last = size
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    if not out.is_file() or out.stat().st_size == 0:
        raise RuntimeError(f"Chrome did not write {out}")


def chrome_pdf(html_path: Path, pdf_path: Path, profile: Path) -> None:
    _run_chrome(pdf_path, ["--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
                           "--virtual-time-budget=20000", f"--print-to-pdf={pdf_path}",
                           f"file://{html_path}"], profile)


def chrome_png(html_path: Path, png_path: Path, profile: Path, width: int, height: int) -> None:
    _run_chrome(png_path, ["--hide-scrollbars", "--force-device-scale-factor=1",
                           f"--window-size={width},{height}", "--virtual-time-budget=15000",
                           f"--screenshot={png_path}", f"file://{html_path}"], profile)


# ------------------------------------------------------------------ assembling a book

def assemble(units: list[Unit], markers: bool) -> tuple[list[tuple], str]:
    entries: list[tuple] = []
    blocks: list[str] = []
    last = None
    openers = 0
    for u in units:
        if u.part and u.part != last:
            last = u.part
            entries.append((u.part, "", "", ""))
            label, sep, name = u.part.partition(": ")
            if sep:
                openers += 1
                blocks.append(template.opener(f"part-{openers}", label, name, u.opener_html, markers))
        if u.raw:
            mark = f'<span class="pm">@@{u.key}@@</span>' if markers else ""
            blocks.append(f'<article class="cert-page" id="{u.key}">{mark}{u.html}</article>')
        else:
            blocks.append(template.lesson(u, markers))
        entries.append(("", u.key, u.num, u.title, u.desc))
    return entries, "".join(blocks)


def print_book(spec: dict, mode: str, units: list[Unit], out_path: Path, intro: str = "",
               heading: str = "Contents") -> dict:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pages: dict[str, int] = {}
    stable = False
    with tempfile.TemporaryDirectory(prefix="acpdf-") as t, tempfile.TemporaryDirectory(prefix="acprof-") as p:
        tmp, prof = Path(t), Path(p)
        marked_count = 0
        for _ in range(3):
            entries, body = assemble(units, True)
            doc = template.document(spec, mode, body, entries, pages, heading, intro)
            (tmp / "pass.html").write_text(doc, encoding="utf-8")
            chrome_pdf(tmp / "pass.html", tmp / "pass.pdf", prof)
            texts = page_texts(tmp / "pass.pdf")
            marked_count = len(texts)
            found = find_pages(texts, skip_before=1)
            if found == pages:
                stable = True
                break
            pages = found
        entries, body = assemble(units, False)
        doc = template.document(spec, mode, body, entries, pages, heading, intro)
        (tmp / "final.html").write_text(doc, encoding="utf-8")
        chrome_pdf(tmp / "final.html", tmp / "final.pdf", prof)
        final_count = len(page_texts(tmp / "final.pdf"))
        shutil.move(str(tmp / "final.pdf"), str(out_path))
    keys = {u.key for u in units}
    return {"pages": {k: v for k, v in pages.items() if k in keys}, "page_count": final_count,
            "stable": stable, "pagination_drift": final_count != marked_count,
            "missing": sorted(keys - set(pages))}


# ------------------------------------------------------------------ overflow check

def _margins_pt(page: str) -> tuple[float, float]:
    top, side, _bottom = (float(v.replace("mm", "")) for v in template.MARGINS[page].split())
    return side * 72 / 25.4, top * 72 / 25.4


def overflow_words(pdf: Path, page: str, tolerance_pt: float = 4.0) -> list[tuple[int, str]]:
    """Words printed outside the side margins (not the cover and back cover)."""
    side, _ = _margins_pt(page)
    out = subprocess.run(["pdftotext", "-bbox", str(pdf), "-"], capture_output=True, text=True, check=False).stdout
    bad: list[tuple[int, str]] = []
    pages = re.split(r"<page ", out)[1:]
    for i, chunk in enumerate(pages, start=1):
        if i == 1 or i == len(pages):
            continue
        width = float(re.search(r'width="([\d.]+)"', chunk).group(1))
        for m in re.finditer(r'<word xMin="([\d.]+)" yMin="[\d.]+" xMax="([\d.]+)" yMax="[\d.]+">(.*?)</word>',
                             chunk):
            x0, x1 = float(m.group(1)), float(m.group(2))
            if x0 < side - tolerance_pt or x1 > width - side + tolerance_pt:
                bad.append((i, html.unescape(m.group(3))))
    return bad


# ------------------------------------------------------------------ zips

def _safe_member(name: str) -> None:
    parts = name.replace("\\", "/").split("/")
    if name.startswith("/") or ".." in parts:
        raise ValueError(f"unsafe zip member name: {name!r}")


def repack(old_zip: Path, new_zip: Path, dark_member: str, print_member: str,
           dark_pdf: Path, print_pdf: Path) -> None:
    """Copy `old_zip`, swap in the dark guide, and add the print guide beside it."""
    for member in (dark_member, print_member):
        _safe_member(member)
    new_zip = Path(new_zip)
    new_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp = new_zip.with_suffix(".zip.tmp")
    with zipfile.ZipFile(old_zip) as zin:
        infos = zin.infolist()
        for info in infos:
            _safe_member(info.filename)
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for info in infos:
                if info.filename == dark_member:
                    zout.write(dark_pdf, dark_member)
                    zout.write(print_pdf, print_member)
                elif info.filename == print_member:
                    continue
                else:
                    zout.writestr(info, zin.read(info.filename))
            if dark_member not in {i.filename for i in infos}:
                zout.write(dark_pdf, dark_member)
                zout.write(print_pdf, print_member)
    os.replace(tmp, new_zip)


# ------------------------------------------------------------------ sources

def _lessons(folder: Path, regex: str) -> list[tuple[int, int, Path]]:
    rx = re.compile(regex)
    found = []
    for p in folder.glob("*.md"):
        m = rx.match(p.name)
        if m:
            found.append((int(m.group(1)), int(m.group(2)), p))
    return sorted(found)


def _strip_h1(md: str) -> tuple[str, str]:
    lines = md.splitlines()
    if lines and lines[0].startswith("# "):
        return lines[0][2:].strip(), "\n".join(lines[1:])
    return "", md


def _plain_md(text: str) -> str:
    return re.sub(r"\*+([^*]+)\*+", r"\1", text).strip()


def operator_units(vault: Path) -> tuple[list[Unit], str]:
    folder = vault / "Build a Safe AI Operator"
    intro, modules, descs, bonus_desc = "", {}, {}, ""
    cur = None
    for line in _read(folder / "Outline.md").splitlines():
        m = re.match(r"^## Module (\d+):\s*(.+)$", line)
        if m:
            cur = int(m.group(1))
            modules[cur] = m.group(2).strip()
            continue
        if line.startswith("## Bonus"):
            cur = "bonus"
            continue
        b = re.match(r"^- \*\*(\d+\.\d+)\s+(.*?)\*\*\s*(.*)$", line)
        if b:
            descs[b.group(1)] = _plain_md(b.group(3))
        elif cur is None and line and not line.startswith("#"):
            intro = line.strip()
        elif cur == "bonus" and line.strip():
            bonus_desc = _plain_md(line)
    units = []
    for m, n, p in _lessons(folder, r"^Lesson (\d+)\.(\d+)\.md$"):
        part = f"Module {m}: {modules.get(m, '')}".rstrip(": ")
        units.append(unit_from_markdown(_read(p), f"l{m}-{n}", p.stem, part=part, desc=descs.get(f"{m}.{n}", "")))
    units.append(unit_from_markdown(_read(folder / "Bonus - From course to client.md"), "bonus",
                                    "From course to client", part="Bonus", desc=bonus_desc))
    return units, intro


def thirty_units(vault: Path) -> tuple[list[Unit], str]:
    text = re.sub(r"\A---\n.*?\n---\n", "", _read(vault / "30 Business Lessons.md"), flags=re.DOTALL)
    sections = re.split(r"(?m)^(?=## )", text)[1:]
    units: list[Unit] = []
    for sec in sections:
        head, _, rest = sec.partition("\n")
        title = head[3:].strip()
        if title == "Contents":
            continue
        rest = re.sub(r"(?m)^(\s+)-\s*$", r"\1- ", rest)
        m = re.match(r"Lesson (\d+)\.\s*(.+)$", title)
        if m:
            n, name = int(m.group(1)), m.group(2)
            topic = ""
            t = re.match(r"\s*\*([^*\n]+)\*\s*\n", rest)
            if t:
                topic, rest = t.group(1).strip(), rest[t.end():]
            kicker = f"Lesson {n}" + (f" · {topic}" if topic else "")
            units.append(Unit(key=f"l{n}", kicker=kicker, title=name, html=template.enrich(md_to_html(rest)),
                              num=str(n), part="The thirty lessons" if n == 1 else ""))
        elif title == "Introduction":
            units.append(Unit(key="intro", kicker="", title=title, html=template.enrich(md_to_html(rest)),
                              part="Start here"))
        elif title == "The Offer Worksheet":
            units.append(Unit(key="worksheet", kicker="Appendix", title=title,
                              html=template.enrich(md_to_html(rest)), part="Appendix"))
        else:
            units.append(Unit(key="closing", kicker="", title=title, html=template.enrich(md_to_html(rest))))
    return units, ""


ADULT_MODULES = {0: "Start here", 1: "What AI is", 2: "Ask better, check better", 3: "Your Claude account",
                 4: "Everyday AI studio", 5: "Claude's toolbox: Skills, connectors, plugins",
                 6: "Stay safe", 7: "n8n starter: your first automations", 8: "Builder Lab",
                 9: "Capstone and certificate"}
SENIOR_ORDER = [0, 6, 2, 3, 4, 9]
SENIOR_PARTS = {
    0: ("Start here", "How this book works, who can use which AI, and getting your phone ready."),
    6: ("Stay safe", "Scams first. This part comes early on purpose. Read it before you use any AI."),
    2: ("Keep private things private", "What to take out before you show AI anything."),
    3: ("Your Claude account", "Set up Claude on your phone, choose a plan, check your privacy, and talk to it."),
    4: ("Everyday help", "Health and money limits, explaining a letter, and a routine that suits you."),
    9: ("Your capstone", "Pick one small project and make it yours."),
}
LESSON_FILE = r"^Lesson (\d+)\.(\d+)\s*[-–—:]\s*(.+)\.md$"


def _workflows_unit(wf: Path, key: str, kicker: str, title: str, lead: str, fallback_guide: str = "") -> Unit:
    rows, names = [], []
    for f in sorted(wf.glob("*.json")) if wf.exists() else []:
        try:
            data = json.loads(_read(f))
            name = data.get("name", f.stem) if isinstance(data, dict) else f.stem
            nodes = len(data.get("nodes", [])) if isinstance(data, dict) else 0
        except (ValueError, OSError):
            name, nodes = f.stem, 0
        names.append(f.name)
        rows.append(f"<tr><td><code>{html.escape(f.name)}</code></td><td>{html.escape(str(name))}</td>"
                    f"<td>{nodes}</td></tr>")
    others = sorted(p.name + ("/" if p.is_dir() else "") for p in wf.iterdir()
                    if p.suffix != ".json" and not p.name.startswith(".")) if wf.exists() else []
    readme_html = ""
    if (wf / "README.md").exists():
        _, body = _strip_h1(_read(wf / "README.md"))
        readme_html = template.enrich(md_to_html(body))
    table = ("<table><thead><tr><th>File</th><th>Workflow</th><th>Steps (nodes)</th></tr></thead><tbody>"
             + "".join(rows) + "</tbody></table>") if rows else (
        "<p><strong>The workflow files were not in this build.</strong> They ship in the "
        "<code>workflows</code> folder of your download.</p>")
    extra = ("<p>Also in the folder: " + ", ".join(f"<code>{html.escape(n)}</code>" for n in others) + ".</p>"
             if others else "")
    body = f"<p>{lead}</p>{table}{extra}{readme_html or fallback_guide}"
    return Unit(key=key, kicker=kicker, title=title, html=body, part="Appendix")


def _certificate() -> Unit:
    body = """<div class="cert"><div class="cert-series">AI for Every Age</div>
<div class="cert-title">Certificate of completion</div>
<div class="cert-course">Everyday AI for Adults</div>
<p class="cert-text">This records that</p>
<div class="cert-line"></div>
<div class="cert-cap">Name of the adult who completed the course</div>
<p class="cert-text">finished the course and scored their own capstone against the course rubric.</p>
<div class="cert-grid">
<div><div class="cert-line short"></div><div class="cert-cap">Capstone score (80 or more out of 100)</div></div>
<div><div class="cert-box"></div><div class="cert-cap">Every safety item met (tick)</div></div>
<div><div class="cert-line short"></div><div class="cert-cap">Capstone title</div></div>
<div><div class="cert-line short"></div><div class="cert-cap">Date</div></div>
</div>
<p class="cert-note">Self-assessed against the rubric in Lessons 9.2 and 9.4. This certificate records completion of this course only. It is not an accreditation, a qualification, a licence or academic credit. For adults 18+ only: do not add a child's name or details.</p>
</div>"""
    return Unit(key="cert", kicker="", title="Your certificate", html=body, raw=True)


def adults_units(vault: Path) -> tuple[list[Unit], str]:
    root = vault / "AI for Every Age"
    folder = root / "adults"
    intros: dict[int, tuple[str, str]] = {}
    for p in folder.glob("Module *.md"):
        m = re.match(r"^Module (\d+)\s*[-–—:]\s*(.+)\.md$", p.name)
        if m:
            h1, body = _strip_h1(_read(p))
            name = re.sub(r"^Module \d+\s*[-–—:]\s*", "", h1) or m.group(2)
            intros[int(m.group(1))] = (name, md_to_html(body))
    units: list[Unit] = []
    for mod, num, p in _lessons(folder, LESSON_FILE):
        name, intro_html = intros.get(mod, (ADULT_MODULES.get(mod, f"Module {mod}"), ""))
        u = unit_from_markdown(_read(p), f"l{mod}-{num}", p.stem, part=f"Module {mod}: {name}")
        u.opener_html = f'<div class="intro">{intro_html}</div>' if intro_html else ""
        units.append(u)
    units.append(_workflows_unit(
        root / "workflows", "wf", "Appendix", "The n8n workflow pack",
        "Your download includes a <code>workflows</code> folder next to this PDF. Each file is an n8n workflow "
        "you can import, matching the builds in Module 7."))
    units.append(_certificate())
    return units, ""


def seniors_units(vault: Path) -> tuple[list[Unit], str]:
    folder = vault / "AI for Every Age" / "seniors"
    by_mod: dict[int, list] = {}
    for mod, num, p in _lessons(folder, LESSON_FILE):
        by_mod.setdefault(mod, []).append((num, p))
    order = SENIOR_ORDER + sorted(m for m in by_mod if m not in SENIOR_ORDER)
    units: list[Unit] = []
    part_no = 0
    for mod in order:
        if mod not in by_mod:
            continue
        part_no += 1
        name, blurb = SENIOR_PARTS.get(mod, (f"Module {mod}", ""))
        for num, p in by_mod[mod]:
            u = unit_from_markdown(_read(p), f"l{mod}-{num}", p.stem, part=f"Part {part_no}: {name}")
            u.opener_html = f"<p>{html.escape(blurb)}</p>" if blurb else ""
            units.append(u)
    return units, ""


def workbook_units(vault: Path) -> tuple[list[Unit], str]:
    folder = vault / "Family AI-Scam Defense Workbook" / "workbook"
    units = []
    for p in sorted(folder.glob("Part *.md"), key=lambda p: int(re.match(r"Part (\d+)", p.name).group(1))):
        m = re.match(r"^Part (\d+)\s*[-–—:]\s*(.+)\.md$", p.name)
        units.append(unit_from_markdown(_read(p), f"p{m.group(1)}", m.group(2), num=m.group(1)))
    return units, ""


SELL_PARTS = {0: "Start here", 1: "Clipping, with permission", 2: "One digital product",
              3: "An online store, done honestly", 4: "Running it as one system"}


def sell_units(vault: Path) -> tuple[list[Unit], str]:
    units = []
    for mod, num, p in _lessons(vault / "Sell With AI", LESSON_FILE):
        units.append(unit_from_markdown(_read(p), f"l{mod}-{num}", p.stem,
                                        part=f"Part {mod + 1}: {SELL_PARTS.get(mod, f'Module {mod}')}"))
    return units, ""


def pack_units(vault: Path) -> tuple[list[Unit], str]:
    root = vault / "Local Business Automation Pack"
    units = []
    chapters = []
    for p in (root / "guide").glob("Chapter *.md"):
        m = re.match(r"^Chapter (\d+)\s*[-–—:]\s*(.+)\.md$", p.name)
        if m:
            chapters.append((int(m.group(1)), m.group(2), p))
    for n, name, p in sorted(chapters):
        units.append(unit_from_markdown(_read(p), f"c{n}", name, num=str(n)))
    units.append(_workflows_unit(
        root / "workflows", "wf", "Appendix", "The workflow files",
        "Your download has a <code>workflows</code> folder next to this PDF. Each file is an n8n workflow "
        "you can import, matching Chapters 3 to 6."))
    return units, ""


LOADERS = {
    "safe-ai-operator": operator_units, "30-business-lessons": thirty_units,
    "everyday-ai-for-adults": adults_units, "safe-and-simple-ai-60plus": seniors_units,
    "family-ai-scam-defence-workbook": workbook_units, "sell-with-ai": sell_units,
    "local-business-automation-pack": pack_units,
}

# Today's files (the Vault dist copies): the word-count baseline and the zips to repack.
BASELINE_PDF = {
    "safe-ai-operator": "Build a Safe AI Operator/Build a Safe AI Operator.pdf",
    "30-business-lessons": "dist/30-business-lessons.pdf",
    "everyday-ai-for-adults": "AI for Every Age/dist/Everyday AI for Adults.pdf",
    "safe-and-simple-ai-60plus": "AI for Every Age/dist/Safe and Simple AI 60+.pdf",
    "family-ai-scam-defence-workbook": "Family AI-Scam Defense Workbook/dist/Family AI-Scam Defense Workbook.pdf",
    "sell-with-ai": "Sell With AI/dist/sell-with-ai.pdf",
    "local-business-automation-pack": "Local Business Automation Pack/dist/Local Business Automation Pack.pdf",
}
ZIPS = {
    "everyday-ai-for-adults": ("AI for Every Age/dist/Everyday-AI-for-Adults.zip", "everyday-ai-for-adults.zip",
                               "Everyday AI for Adults/Everyday AI for Adults.pdf",
                               "Everyday AI for Adults/Everyday AI for Adults (print version).pdf"),
    "local-business-automation-pack": (
        "Local Business Automation Pack/dist/local-business-automation-pack.zip",
        "local-business-automation-pack.zip",
        "Local Business Automation Pack/Local Business Automation Pack.pdf",
        "Local Business Automation Pack/Local Business Automation Pack (print version).pdf"),
}


def intro_for(key: str, vault: Path) -> str:
    return load(key, vault)[1]


def load(key: str, vault: Path = VAULT) -> tuple[list[Unit], str]:
    return LOADERS[key](vault)


def build_product(key: str, out_dir: Path, vault: Path = VAULT) -> dict:
    spec = tokens.PRODUCTS[key]
    if "@font-face" not in template.fonts_css():
        raise RuntimeError("the Academy fonts did not load: run with the repo .venv python (3.12+)")
    units, intro = load(key, vault)
    report = {}
    for mode, name in output_names(key).items():
        out = out_dir / name
        r = print_book(spec, mode, units, out, intro=intro)
        r["words"] = word_count(out)
        r["overflow"] = overflow_words(out, spec["page"])
        r["bytes"] = out.stat().st_size
        report[mode] = r
    base = vault / BASELINE_PDF[key]
    if base.is_file():
        report["baseline_words"] = word_count(base)
    return report


def build_zips(out_dir: Path, vault: Path = VAULT) -> dict:
    result = {}
    for key, (old, new, dark_member, print_member) in ZIPS.items():
        names = output_names(key)
        dest = out_dir.parent / "zip" / new
        repack(vault / old, dest, dark_member, print_member, out_dir / names["dark"], out_dir / names["print"])
        result[key] = str(dest)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["pdf", "zip"])
    ap.add_argument("keys", nargs="*")
    ap.add_argument("--vault", default=str(VAULT))
    ap.add_argument("--out", default=str(STAGING / "pdf"))
    a = ap.parse_args(argv)
    out = Path(a.out).resolve()
    if STAGING.resolve() not in out.parents and out != STAGING.resolve() and "scratch" not in str(out):
        print(f"refusing to write outside {STAGING}", file=sys.stderr)
        return 2
    vault = Path(a.vault)
    if a.cmd == "zip":
        print(json.dumps(build_zips(out, vault), indent=1))
        return 0
    keys = a.keys or list(tokens.PRODUCTS)
    summary = {k: build_product(k, out, vault) for k in keys}
    print(json.dumps(summary, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
