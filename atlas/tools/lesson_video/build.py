"""Video lessons from the course markdown: one 5-10 minute narrated video
per lesson, slides in the Academy's look, the lesson's own words spoken.

Miro, 2026-10-01: "courses should be videos only, not PDF; only ebooks and
kits are PDF." This turns each `Lesson X.Y.md` of a course (the vault copy,
which the server cannot read, so this runs on the Mac) into:

  1. `parse`   -> <out>/<id>/segments.json: the lesson cut into slides, each
                  with what is shown (HTML) and what is said (plain text).
                  Nothing is written that is not in the lesson: the
                  narration IS the lesson text, markdown stripped, tables
                  read row by row.
  2. `render`  -> <out>/<id>/slides/NNN.png, 1920x1080, via Playwright.
  3. (tts.py, on the server where the Fish key lives)
                  <out>/<id>/audio/NNN.mp3, one per segment.
  4. `assemble`-> <out>/<id>/<id>.mp4: each slide held for its narration,
                  concatenated, H.264 + AAC, 30 fps.

Usage:
  python tools/lesson_video/build.py parse  "<vault course dir>" <out> [ids...]
  (LESSON_COURSE="AI for Every Age: Adults" names another course on the slides;
   lesson files are "Lesson X.Y.md" or "Lesson X.Y - Title.md")
  python tools/lesson_video/build.py render <out> [ids...]
  python tools/lesson_video/build.py assemble <out> [ids...]
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path

COURSE = os.getenv("LESSON_COURSE", "Build a Safe AI Operator")
STORE = "ATLAS Academy"
PAD = 1.0            # seconds of silence after each narration
TEMPO = 0.94         # the voice, slowed a touch: a lesson, not a reel
MIN_HOLD = 2.5       # a slide is never shown shorter than this
FPS = 24
W, H = 1920, 1080

# ------------------------------------------------------------------ markdown

_INLINE = [
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    (re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)"), r"\1"),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),
    (re.compile(r"~~(.+?)~~"), r"\1"),
]


def spoken(text: str) -> str:
    """Markdown inline marks removed; what the voice says."""
    for rx, rep in _INLINE:
        text = rx.sub(rep, text)
    text = text.replace("&", " and ")
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace(" …", "...").replace("…", "...")
    return text


def shown(text: str) -> str:
    """Markdown inline marks to HTML, everything else escaped."""
    out = html.escape(text, quote=False)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", out)
    out = re.sub(r"`([^`]*)`", r"<code>\1</code>", out)
    out = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", out)
    return out


def _blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    """Top-level blocks: ('h1'|'h2'|'p'|'ul'|'ol'|'quote'|'table'|'code', lines)."""
    blocks: list[tuple[str, list[str]]] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if s.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                j += 1
            blocks.append(("code", lines[i + 1:j]))
            i = j + 1
            continue
        if s.startswith("# "):
            blocks.append(("h1", [s[2:].strip()])); i += 1; continue
        if s.startswith("## "):
            blocks.append(("h2", [s[3:].strip()])); i += 1; continue
        if s.startswith("### "):
            blocks.append(("h3", [s[4:].strip()])); i += 1; continue
        if s.startswith("|"):
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1
            blocks.append(("table", [x.strip() for x in lines[i:j]]))
            i = j
            continue
        if s.startswith(">"):
            j = i
            while j < len(lines) and lines[j].strip().startswith(">"):
                j += 1
            blocks.append(("quote", [x.strip()[1:].strip() for x in lines[i:j]]))
            i = j
            continue
        if re.match(r"^(-|\*|\d+\.)\s", s):
            kind = "ol" if s[0].isdigit() else "ul"
            j = i
            items: list[str] = []
            while j < len(lines):
                t = lines[j].strip()
                if re.match(r"^(-|\*|\d+\.)\s", t):
                    items.append(re.sub(r"^(-|\*|\d+\.)\s+", "", t))
                elif t and lines[j].startswith(("  ", "\t")):
                    items[-1] += " " + t
                else:
                    break
                j += 1
            blocks.append((kind, items))
            i = j
            continue
        j = i
        para: list[str] = []
        while j < len(lines) and lines[j].strip() and not re.match(
                r"^(#|>|\||```|-\s|\*\s|\d+\.\s)", lines[j].strip()):
            para.append(lines[j].strip())
            j += 1
        blocks.append(("p", [" ".join(para)]))
        i = j
    return blocks


def _table(rows: list[str]) -> tuple[list[str], list[list[str]]]:
    cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
    head = cells[0]
    body = [r for r in cells[1:] if not all(re.fullmatch(r":?-+:?", c) for c in r)]
    return head, body


def segments(md: str, lesson_id: str, next_title: str = "") -> list[dict]:
    blocks = _blocks(md.splitlines())
    title = next((b[1][0] for b in blocks if b[0] == "h1"), lesson_id)
    short = re.sub(r"^Lesson\s+[\d.]+\s*[:\-\u2013]\s*", "", title)
    segs: list[dict] = [{
        "kind": "title",
        "heading": f"Lesson {lesson_id}",
        "html": f"<h1>{html.escape(short)}</h1><p class=sub>{COURSE} · {STORE}</p>",
        "say": f"Lesson {lesson_id.replace('.', ' point ')}. {spoken(short)}.",
    }]
    heads = [b[1][0] for b in blocks if b[0] == "h2" and b[1][0].lower() != "next"]
    if heads:
        segs.append({
            "kind": "body", "heading": "In this lesson",
            "html": "<ul>" + "".join(f"<li>{shown(h)}</li>" for h in heads) + "</ul>",
            "say": "In this lesson: " + ", ".join(spoken(h).rstrip(".") for h in heads) + ".",
        })
    section = ""
    first_in_section = False
    for kind, lines in blocks:
        if kind == "h1":
            continue
        if kind in ("h2", "h3"):
            section = lines[0]
            first_in_section = True
            continue
        say_prefix = (spoken(section) + ". ") if first_in_section and section else ""
        first_in_section = False
        if kind == "p" and re.match(r"^Last verified:", lines[0]):
            continue                                   # a note for the editor, not the viewer
        if kind == "p":
            body = f"<p>{shown(lines[0])}</p>"
            say = spoken(lines[0])
        elif kind in ("ul", "ol"):
            tag = "ol" if kind == "ol" else "ul"
            body = f"<{tag}>" + "".join(f"<li>{shown(x)}</li>" for x in lines) + f"</{tag}>"
            say = " ".join(spoken(x).rstrip(".,;:") + "." for x in lines)
        elif kind == "quote":
            text = " ".join(lines)
            body = f"<blockquote>{shown(text)}</blockquote>"
            say = spoken(text)
        elif kind == "table":
            head, rows = _table(lines)
            body = ("<table><tr>" + "".join(f"<th>{shown(c)}</th>" for c in head) + "</tr>"
                    + "".join("<tr>" + "".join(f"<td>{shown(c)}</td>" for c in r) + "</tr>"
                              for r in rows) + "</table>")
            said = []
            for r in rows:
                said.append(" ".join(f"{spoken(h)}: {spoken(c).rstrip('.')}."
                                     for h, c in zip(head, r) if c.strip()))
            say = " ".join(said)
        elif kind == "code":
            body = "<pre>" + html.escape("\n".join(lines)) + "</pre>"
            say = "Here is the code. Pause the video if you want to copy it."
        else:
            continue
        segs.append({"kind": "body", "heading": section, "html": body,
                     "say": (say_prefix + say).strip()})
    return segs


def lesson_files(course_dir: Path) -> list[tuple[str, Path]]:
    out = []
    for p in sorted(course_dir.glob("Lesson *.md")):
        m = re.match(r"Lesson (\d+\.\d+)(?: - .*)?\.md", p.name)
        if m:
            out.append((m.group(1), p))
    return sorted(out, key=lambda t: tuple(int(x) for x in t[0].split(".")))


# ------------------------------------------------------------------ slides

CSS = f"""
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{W}px;height:{H}px;overflow:hidden;background:#06111a;color:#e8f1f7;
font-family:"Atkinson Hyperlegible Next","Atkinson Hyperlegible",-apple-system,"Helvetica Neue",Arial,sans-serif}}
body::before{{content:"";position:absolute;inset:0;z-index:0;
background-image:linear-gradient(rgba(47,208,255,.07) 1px,transparent 1px),linear-gradient(90deg,rgba(47,208,255,.07) 1px,transparent 1px);
background-size:80px 80px;mask-image:radial-gradient(ellipse at 70% 20%,#000 20%,transparent 75%)}}
body::after{{content:"";position:absolute;inset:0;z-index:0;
background:radial-gradient(ellipse at 85% 10%,rgba(47,208,255,.22),transparent 45%)}}
.slide{{position:relative;z-index:1;width:100%;height:100%;padding:72px 110px 84px;display:flex;flex-direction:column}}
.top{{display:flex;justify-content:space-between;align-items:center;font-size:26px;color:#8fa3b3;letter-spacing:.01em}}
.top b{{color:#2fd0ff;font-weight:800;letter-spacing:.08em}}
.top span{{color:#b9c9d6}}
.cube{{width:38px;height:38px;border:2px solid #2fd0ff;transform:rotate(45deg);box-shadow:0 0 24px rgba(47,208,255,.5);margin-right:18px}}
.head{{margin-top:48px;font-size:40px;font-weight:700;color:#2fd0ff}}
.body{{flex:1;display:flex;flex-direction:column;justify-content:center;font-size:50px;line-height:1.42;max-width:1560px}}
.body p{{margin:0}}
.body b{{color:#fff}}
.body code{{font-family:ui-monospace,Menlo,monospace;font-size:.86em;color:#9fe8ff;background:rgba(47,208,255,.1);padding:2px 10px;border-radius:8px}}
ul,ol{{padding-left:1.1em}}
li{{margin:14px 0}}
li::marker{{color:#2fd0ff}}
blockquote{{border-left:8px solid #2fd0ff;padding:18px 0 18px 44px;font-size:58px;line-height:1.3;color:#fff}}
table{{border-collapse:collapse;font-size:34px;line-height:1.3;width:100%}}
th,td{{text-align:left;padding:16px 22px;border-bottom:1px solid rgba(120,170,200,.3);vertical-align:top}}
th{{color:#2fd0ff;font-weight:700}}
pre{{font-family:ui-monospace,Menlo,monospace;font-size:30px;line-height:1.35;color:#d7f6ff;background:rgba(0,0,0,.35);border:1px solid rgba(120,170,200,.3);border-radius:14px;padding:30px;white-space:pre-wrap}}
.title .body{{justify-content:center}}
h1{{font-size:96px;line-height:1.05;font-weight:800;color:#fff;letter-spacing:-.02em;max-width:1400px}}
.sub{{margin-top:36px;font-size:36px;color:#8fa3b3}}
.foot{{font-size:24px;color:#5f7686}}
"""


def slide_html(seg: dict, lesson_id: str, n: int, total: int) -> str:
    cls = "title" if seg["kind"] == "title" else "body-slide"
    head = "" if seg["kind"] == "title" else f'<div class=head>{html.escape(seg["heading"])}</div>'
    return f"""<!doctype html><html><head><meta charset=utf-8><style>{CSS}</style></head>
<body><div class="slide {cls}">
<div class=top><div style="display:flex;align-items:center"><div class=cube></div><b>ATLAS</b>&nbsp;<span>Academy</span></div>
<div>{html.escape(COURSE)} · Lesson {lesson_id}</div></div>
{head}
<div class=body>{seg["html"]}</div>
<div class=foot>{n} / {total}</div>
</div></body></html>"""


def fit(page, seg_dir: Path, seg: dict, lesson_id: str, n: int, total: int) -> None:
    """Render; if the body overflows, shrink its font until it fits."""
    doc = slide_html(seg, lesson_id, n, total)
    page.set_content(doc)
    size = 50
    while size > 24:
        over = page.evaluate("""() => { const b=document.querySelector('.body');
            return b.scrollHeight > b.clientHeight + 2; }""")
        if not over:
            break
        size -= 2
        page.evaluate(f"document.querySelector('.body').style.fontSize='{size}px'")
        page.evaluate(f"document.querySelectorAll('.body table').forEach(t=>t.style.fontSize='{int(size*0.68)}px')")
        page.evaluate(f"document.querySelectorAll('.body blockquote').forEach(t=>t.style.fontSize='{int(size*1.15)}px')")
    page.screenshot(path=str(seg_dir / f"{n:03d}.png"))


def render(out: Path, ids: list[str]) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        for lid in ids:
            d = out / lid
            segs = json.loads((d / "segments.json").read_text())
            sd = d / "slides"
            sd.mkdir(exist_ok=True)
            for n, seg in enumerate(segs, 1):
                fit(page, sd, seg, lid, n, len(segs))
            print(f"rendered {lid}: {len(segs)} slides")
        browser.close()


# ------------------------------------------------------------------ assemble

def duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True,
                       check=False)
    return float(r.stdout.strip() or 0)


def assemble(out: Path, ids: list[str]) -> None:
    """One encode per lesson: the slides as a timed image list, the narration
    as one padded audio track. x264 skips identical frames, so a still
    slide costs almost nothing; encoding slide by slide was 20x slower."""
    for lid in ids:
        d = out / lid
        segs = json.loads((d / "segments.json").read_text())
        work = d / "parts"
        work.mkdir(exist_ok=True)
        vlist, alist, total = [], [], 0.0
        for n in range(1, len(segs) + 1):
            png = d / "slides" / f"{n:03d}.png"
            mp3 = d / "audio" / f"{n:03d}.mp3"
            dur = max(MIN_HOLD, duration(mp3) / TEMPO + PAD)
            wav = work / f"{n:03d}.wav"
            # The narration, slowed, padded to exactly the slide's hold.
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3),
                            "-af", f"atempo={TEMPO},apad,atrim=0:{dur:.3f}",
                            "-ar", "44100", "-ac", "2", str(wav)], check=True)
            vlist += [f"file '{png.resolve()}'", f"duration {dur:.3f}"]
            alist.append(f"file '{wav.resolve()}'")
            total += dur
        vlist.append(f"file '{(d / 'slides' / f'{len(segs):03d}.png').resolve()}'")
        (d / "video.txt").write_text("\n".join(vlist) + "\n")
        (d / "audio.txt").write_text("\n".join(alist) + "\n")
        final = d / f"lesson-{lid}.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error",
                        "-f", "concat", "-safe", "0", "-i", str(d / "video.txt"),
                        "-f", "concat", "-safe", "0", "-i", str(d / "audio.txt"),
                        "-map", "0:v", "-map", "1:a", "-t", f"{total:.3f}",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                        "-pix_fmt", "yuv420p", "-r", str(FPS), "-vsync", "cfr",
                        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                        str(final)], check=True)
        print(f"assembled {lid}: {len(segs)} slides, {total/60:.1f} min -> {final}", flush=True)


# ------------------------------------------------------------------ main

def main(argv: list[str]) -> None:
    cmd = argv[0]
    if cmd == "parse":
        course_dir, out = Path(argv[1]), Path(argv[2])
        want = set(argv[3:])
        files = lesson_files(course_dir)
        for k, (lid, path) in enumerate(files):
            if want and lid not in want:
                continue
            nxt = files[k + 1][1].read_text().splitlines()[0] if k + 1 < len(files) else ""
            segs = segments(path.read_text(), lid, nxt)
            d = out / lid
            d.mkdir(parents=True, exist_ok=True)
            (d / "segments.json").write_text(json.dumps(segs, indent=1, ensure_ascii=False))
            words = sum(len(s["say"].split()) for s in segs)
            print(f"{lid}: {len(segs)} slides, {words} words (~{words/140:.1f} min)")
        return
    out = Path(argv[1])
    ids = argv[2:] or sorted(p.name for p in out.iterdir() if (p / "segments.json").exists())
    if cmd == "render":
        render(out, ids)
    elif cmd == "assemble":
        assemble(out, ids)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
