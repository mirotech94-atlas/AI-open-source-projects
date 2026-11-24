"""One print template for every Academy PDF, in a dark (screen) and a white (print) mode.

The two modes share every rule; only the colours change. The cover is dark in both. Page size
belongs to the product. Callouts carry a word label as well as a colour, so the page reads in
black and white and for readers who do not see the colours.
"""
from __future__ import annotations

import base64
import html
import re
from dataclasses import dataclass
from pathlib import Path

from tools.academy_pdf import tokens

ROOT = Path(__file__).resolve().parents[2]
ICON = ROOT / "atlas/modules/academy/assets/icon.svg"

# Source heading (lower case) -> callout label.
HEADING_LABELS = {
    "the rule": "Rule", "rule": "Rule", "the rules": "Rule",
    "try it": "Try this", "try this": "Try this", "try it yourself": "Try this",
    "your turn": "Try this", "exercise": "Try this", "one thing to do today": "Try this",
    "quick check": "Checkpoint", "checkpoint": "Checkpoint", "check": "Checkpoint",
    "check yourself": "Checkpoint",
    "safety reminder": "Warning", "warning": "Warning", "safety note": "Warning",
    "watch out": "Warning", "stay safe": "Warning", "not legal advice": "Warning",
    "the story": "Example", "example": "Example", "for example": "Example",
    "worked example": "Example",
    "what you have now": "Takeaway", "takeaway": "Takeaway", "key takeaway": "Takeaway",
    "what you learned": "Takeaway",
}
LABEL_CLASS = {"Rule": "co-rule", "Try this": "co-try", "Checkpoint": "co-check",
               "Warning": "co-warn", "Example": "co-example", "Takeaway": "co-take"}


@dataclass
class Unit:
    """One lesson, chapter or part: what the contents page lists and the book prints."""
    key: str
    kicker: str
    title: str
    html: str
    num: str = ""
    part: str = ""          # "Module 2: Name" starts a part (a contents row and, with a colon, an opener)
    opener_html: str = ""   # intro text shown on the part opener page
    desc: str = ""          # one-line description shown under the title on the contents page
    raw: bool = False       # print `html` as is (certificate)


def slug(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", "", text)).lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def callout(label: str, inner: str, heading: str = "") -> str:
    if label not in LABEL_CLASS:
        raise ValueError(f"unknown callout label: {label}")
    return (f'<section class="callout {LABEL_CLASS[label]}"><span class="co-label">{label}</span>'
            f"{heading}{inner}</section>")


def enrich(body: str) -> str:
    """Turn the plain-markdown conventions into styled, labelled blocks."""
    body = re.sub(
        r"<p><strong>Step (\d+)\.</strong>\s*(.*?)</p>",
        r'<div class="step"><span class="n">\1</span><div class="sb"><p>\2</p></div></div>',
        body, flags=re.DOTALL)
    # "What you should see" boxes are checkpoints.
    body = re.sub(r"<blockquote>\s*(<p><strong>What you should see.*?)</blockquote>",
                  lambda m: callout("Checkpoint", m.group(1)), body, flags=re.DOTALL)
    # Single-paragraph conventions used by the 30-lessons guide.
    body = re.sub(r"<p><strong>Try this\.?</strong>\s*(.*?)</p>",
                  lambda m: callout("Try this", f"<p>{m.group(1)}</p>"), body, flags=re.DOTALL)
    body = re.sub(r"<p><strong>Takeaway:?</strong>\s*(.*?)</p>",
                  lambda m: callout("Takeaway", f"<p>{m.group(1)}</p>"), body, flags=re.DOTALL)
    body = re.sub(r"<p>(Last verified:[^<]*)</p>", r'<p class="verified">\1</p>', body)
    # A step and its checkpoint stay together.
    body = re.sub(
        r'(<div class="step">.*?</div></div>)\s*(<section class="callout co-check">.*?</section>)',
        r'<div class="stepgroup">\1\2</div>', body, flags=re.DOTALL)
    body = re.sub(r"<li>\s*(<p>)?\[ \]\s*", lambda m: '<li class="tick">' + (m.group(1) or ""), body)
    parts = re.split(r"(?=<h2[ >])", body)
    out = [parts[0]]
    for part in parts[1:]:
        m = re.match(r"<h2[^>]*>(.*?)</h2>", part, flags=re.DOTALL)
        text = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip().lower().rstrip(".:") if m else ""
        label = HEADING_LABELS.get(text)
        if label:
            heading = m.group(0)
            out.append(callout(label, part[len(heading):], heading))
        else:
            out.append(f'<section class="s-{slug(m.group(1)) if m else ""}">{part}</section>')
    return "".join(out)


def _icon_uri() -> str:
    if ICON.is_file():
        return "data:image/svg+xml;base64," + base64.b64encode(ICON.read_bytes()).decode()
    return ""


def fonts_css() -> str:
    try:
        from atlas.modules.academy.reel_design import fonts_css as f
        return f()
    except Exception:  # noqa: BLE001
        return ""


MARGINS = {"A4": "20mm 18mm 22mm", "A5": "15mm 13mm 18mm", "Letter": "20mm 19mm 22mm"}


def css(mode: str, page: str, body_pt: float, footer: str = "") -> str:
    m = tokens.MODES[mode]
    foot = footer.replace("\\", "").replace('"', "")
    v = (f"--mode:{mode};--bg:{m['bg']};--surface:{m['surface']};--raised:{m['raised']};"
         f"--lift:{m['lift']};--line:{m['line']};--text:{m['text']};--text2:{m['text2']};"
         f"--muted:{m['muted']};--accent:{m['accent']};--accent-text:{m['accent_text']};"
         f"--on-accent:{m['on_accent']};--warn:{m['warn']};--code:{m['code_bg']}")
    return f"""
:root{{{v}}}
@page {{ size: {page}; margin: {MARGINS[page]}; background: {m['bg']};
  @bottom-left {{ content: "{foot}"; font: 500 8.5pt "Atk", system-ui, sans-serif; color: {m['muted']}; }}
  @bottom-right {{ content: counter(page); font: 500 8.5pt "Atk", system-ui, sans-serif; color: {m['muted']}; }} }}
@page cover {{ margin: 0; @bottom-left {{ content: none; }} @bottom-right {{ content: none; }} }}
*{{ box-sizing: border-box; }}
html{{ background: var(--bg); -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
body {{ margin: 0; background: var(--bg); color: var(--text); font-family: "Atk", system-ui, sans-serif;
  font-size: {body_pt}pt; line-height: 1.5; font-weight: 400; orphans: 3; widows: 3; }}
h1, h2, h3, h4 {{ font-family: "Cabinet", "Atk", system-ui, sans-serif; color: var(--text);
  line-height: 1.2; break-after: avoid; }}
h1 {{ font-size: 1.9em; font-weight: 800; margin: 0 0 .5em; }}
h2 {{ font-size: 1.22em; font-weight: 700; margin: 1.5em 0 .5em; }}
h3 {{ font-size: 1.05em; font-weight: 700; margin: 1.2em 0 .4em; }}
p {{ margin: 0 0 .8em; }}
a {{ color: var(--accent-text); text-decoration: none; }}
strong {{ font-weight: 700; color: var(--text); }}
ul, ol {{ margin: 0 0 .9em; padding-left: 1.4em; }}
ol {{ padding-left: 2.1em; }}
li {{ margin: 0 0 .3em; }}
li.tick {{ list-style: none; margin-left: -1.4em; }}
li.tick::before {{ content: ""; display: inline-block; width: .8em; height: .8em; margin-right: .6em;
  border: 1.5px solid var(--accent); border-radius: 2px; vertical-align: -.1em; }}
blockquote {{ margin: .8em 0; padding: .1em 1em; border-left: 3px solid var(--accent); color: var(--text2); }}
code {{ font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace; font-size: .88em;
  background: var(--code); color: var(--text); padding: .08em .3em; border-radius: 3px;
  overflow-wrap: anywhere; }}
pre {{ background: var(--code); border: 1px solid var(--line); border-radius: 4px; padding: .7em .9em;
  margin: .9em 0; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word;
  font-size: .86em; line-height: 1.45; break-inside: avoid; }}
pre code {{ background: none; padding: 0; font-size: 1em; }}
table {{ width: 100%; border-collapse: collapse; margin: .9em 0; font-size: .92em; table-layout: auto; }}
th, td {{ border: 1px solid var(--line); padding: .4em .6em; text-align: left; vertical-align: top;
  overflow-wrap: anywhere; }}
th {{ background: var(--raised); color: var(--text); font-weight: 700; }}
tr {{ break-inside: avoid; }}
img {{ max-width: 100%; height: auto; }}
hr {{ border: 0; border-top: 1px solid var(--line); margin: 1.4em 0; }}
.verified {{ color: var(--muted); font-size: .85em; }}
.pm {{ font-size: 1pt; color: var(--bg); letter-spacing: 0; text-transform: none; }}

.callout {{ background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--accent);
  border-radius: 4px; padding: .7em 1em .35em; margin: 1.1em 0; }}
.callout > h2, .callout > h3 {{ font-size: 1.02em; margin: .15em 0 .5em; }}
.callout blockquote {{ border: 0; padding: 0; margin: 0 0 .8em; font-weight: 600; color: var(--text); }}
.co-label {{ display: inline-block; background: var(--accent); color: var(--on-accent);
  font-family: "Cabinet", "Atk", system-ui, sans-serif; font-weight: 800; font-size: .66em;
  letter-spacing: .1em; text-transform: uppercase; padding: .2em .65em; border-radius: 3px;
  margin: 0 0 .5em; break-after: avoid; }}
.co-rule, .co-check, .co-take, .co-warn {{ break-inside: avoid; }}
.co-warn {{ border-left-color: var(--warn); }}
.co-warn .co-label {{ background: transparent; color: var(--warn); border: 1.5px solid var(--warn); }}
.co-try {{ background: var(--raised); }}
.co-take {{ background: var(--raised); border-left-color: var(--accent-text); }}
.step {{ display: flex; gap: .8em; align-items: flex-start; margin: .7em 0; break-inside: avoid; }}
.step .n {{ flex: 0 0 auto; width: 1.7em; height: 1.7em; border-radius: 50%; background: var(--accent);
  color: var(--on-accent); font-family: "Cabinet", "Atk", sans-serif; font-weight: 800;
  text-align: center; line-height: 1.7em; font-size: .95em; }}
.step .sb {{ flex: 1 1 auto; min-width: 0; }}
.step p {{ margin: .15em 0 .3em; }}
.stepgroup {{ break-inside: avoid; }}

.lesson {{ break-before: page; }}
.lesson-head {{ margin-bottom: 1.1em; padding-bottom: .7em; border-bottom: 2px solid var(--accent); }}
.lesson-head .kicker {{ font-family: "Cabinet", "Atk", sans-serif; font-weight: 700; font-size: .78em;
  letter-spacing: .12em; text-transform: uppercase; color: var(--accent-text); margin-bottom: .35em; }}
.lesson-head h1 {{ margin: 0; }}
.opener {{ break-before: page; break-after: page; padding-top: 28%; }}
.opener .label {{ font-family: "Cabinet", "Atk", sans-serif; font-weight: 700; font-size: .9em;
  letter-spacing: .14em; text-transform: uppercase; color: var(--accent-text); }}
.opener h2 {{ font-size: 2.3em; font-weight: 800; margin: .3em 0 .6em; line-height: 1.12; }}
.opener .rule {{ width: 4.5em; height: 4px; background: var(--accent); margin-bottom: 1.1em; }}
.opener p, .opener .intro {{ color: var(--text2); max-width: 32em; }}
.toc {{ break-before: page; }}
.toc h2 {{ font-size: 1.7em; margin: 0 0 .9em; }}
.toc ol {{ list-style: none; padding: 0; margin: 0; }}
.toc li.part {{ font-family: "Cabinet", "Atk", sans-serif; font-weight: 700; color: var(--accent-text);
  margin: 1.1em 0 .35em; font-size: .95em; letter-spacing: .02em; break-after: avoid; }}
.toc .toc-intro {{ color: var(--text2); margin: 0 0 1em; }}
.toc li.desc {{ color: var(--text2); font-size: .88em; margin: 0 0 .6em 2.9em; line-height: 1.4; }}
.toc li.entry {{ display: flex; align-items: baseline; gap: .5em; margin: 0 0 .28em; }}
.toc li.entry a {{ color: var(--text); flex: 0 1 auto; }}
.toc li.entry .num {{ display: inline-block; min-width: 2.6em; color: var(--muted); }}
.toc li.entry .dots {{ flex: 1 1 auto; border-bottom: 1px dotted var(--line); transform: translateY(-.25em); }}
.toc li.entry .pg {{ flex: 0 0 auto; color: var(--text2); min-width: 1.8em; text-align: right; }}
.fill {{ margin: .6em 0; }}
.fill td {{ height: 2.1em; }}
.wlines {{ margin: .5em 0 1em; }}
.lines {{ border-bottom: 1px solid var(--muted); height: 1.9em; }}
.field {{ display: flex; align-items: flex-end; gap: .6em; margin: .7em 0; }}
.field .lab {{ flex: 0 0 auto; color: var(--text2); }}
.field .ln {{ flex: 1 1 auto; border-bottom: 1px solid var(--muted); height: 1.4em; }}
.pair {{ display: flex; gap: 1.5em; }}
.pair .field {{ flex: 1 1 0; }}
.cert-page {{ break-before: page; }}
.cert {{ border: 2px solid var(--accent); padding: 2em; text-align: center; }}
.cert-title {{ font-family: "Cabinet", sans-serif; font-weight: 800; font-size: 1.8em; margin: .4em 0; }}
.cert-line {{ border-bottom: 1px solid var(--muted); height: 1.6em; margin: .6em 2em; }}
.cert-line.short {{ margin: .6em 0; }}
.cert-box {{ width: 1.6em; height: 1.6em; border: 1.5px solid var(--accent); margin: .6em auto; }}
.cert-cap, .cert-note, .cert-series {{ color: var(--muted); font-size: .82em; }}
.cert-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 1em 2em; margin-top: 1.4em; }}

.cover {{ page: cover; background: #0A0D14; color: #EEF2FA; position: relative; overflow: hidden;
  width: 100%; height: 100vh; break-after: page; padding: 22mm 20mm 18mm; display: flex;
  flex-direction: column; justify-content: space-between; }}
.cover::before {{ content: ""; position: absolute; inset: 0; background:
  radial-gradient(circle at 88% 8%, rgba(91,140,255,.34), rgba(91,140,255,0) 46%),
  linear-gradient(180deg, rgba(91,140,255,.08), rgba(91,140,255,0) 40%); }}
.cover .top, .cover .mid, .cover .base {{ position: relative; }}
.cover .brand {{ display: flex; align-items: center; gap: .8em; font-family: "Cabinet", sans-serif;
  font-weight: 700; font-size: 1.05em; color: #C9D2E3; }}
.cover .brand img {{ width: 2.6em; height: 2.6em; }}
.cover .series {{ display: inline-block; background: #5B8CFF; color: #070A12; font-family: "Cabinet", sans-serif;
  font-weight: 800; font-size: .72em; letter-spacing: .12em; text-transform: uppercase;
  padding: .35em .9em; border-radius: 3px; margin-bottom: 1.4em; }}
.cover h1 {{ font-size: 3.1em; font-weight: 800; color: #EEF2FA; line-height: 1.08; margin: 0 0 .5em;
  max-width: 11em; }}
.cover .promise {{ font-size: 1.18em; color: #C9D2E3; max-width: 25em; line-height: 1.45; }}
.cover .by {{ margin-top: 1.6em; font-family: "Cabinet", sans-serif; font-weight: 700; color: #EEF2FA; }}
.cover .bar {{ width: 5em; height: 5px; background: #5B8CFF; margin: 0 0 1.4em; }}
.cover .foot {{ font-size: .82em; color: #8FB0FF; max-width: 36em; line-height: 1.45; }}
.backcover {{ page: cover; break-before: page; background: #0A0D14; color: #C9D2E3; height: 100vh;
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 1em;
  font-family: "Cabinet", sans-serif; font-weight: 700; }}
.backcover img {{ width: 4.5em; height: 4.5em; }}
"""


def cover(spec: dict) -> str:
    icon = _icon_uri()
    logo = f'<img src="{icon}" alt="">' if icon else ""
    by = f'<div class="by">{html.escape(spec["author"])}</div>' if spec.get("author") else ""
    return (f'<div class="cover"><div class="top"><div class="brand">{logo}<span>ATLAS Academy</span></div></div>'
            f'<div class="mid"><div class="series">{html.escape(spec["series"])}</div>'
            f'<h1>{html.escape(spec["title"])}</h1><div class="bar"></div>'
            f'<div class="promise">{html.escape(spec["promise"])}</div>{by}</div>'
            f'<div class="base"><div class="foot">{html.escape(spec["foot"])}</div></div></div>')


def backcover() -> str:
    icon = _icon_uri()
    logo = f'<img src="{icon}" alt="">' if icon else ""
    return f'<div class="backcover">{logo}<div>ATLAS Academy</div></div>'


def toc(entries: list[tuple], pages: dict, heading: str = "Contents", intro: str = "") -> str:
    """entries: (part label, '', '', '') for a part row, or ('', key, number, title[, description])."""
    rows = []
    for part, key, num, title, *rest in entries:
        if not key:
            rows.append(f'<li class="part">{html.escape(part)}</li>')
            continue
        pg = pages.get(key, "00")
        rows.append(f'<li class="entry"><a href="#{key}"><span class="num">{html.escape(num)}</span>'
                    f'{html.escape(title)}</a><span class="dots"></span><span class="pg">{pg}</span></li>')
        if rest and rest[0]:
            rows.append(f'<li class="desc">{html.escape(rest[0])}</li>')
    lead = f'<p class="toc-intro">{html.escape(intro)}</p>' if intro else ""
    return f'<nav class="toc"><h2>{html.escape(heading)}</h2>{lead}<ol>{"".join(rows)}</ol></nav>'


def lesson(unit: Unit, markers: bool) -> str:
    mark = f'<span class="pm">@@{unit.key}@@</span>' if markers else ""
    return (f'<article class="lesson" id="{unit.key}"><header class="lesson-head">'
            f'<div class="kicker">{html.escape(unit.kicker)}{mark}</div>'
            f"<h1>{html.escape(unit.title)}</h1></header>{unit.html}</article>")


def opener(key: str, label: str, name: str, blurb_html: str, markers: bool) -> str:
    mark = f'<span class="pm">@@{key}@@</span>' if markers else ""
    return (f'<div class="opener" id="{key}"><div class="label">{html.escape(label)}{mark}</div>'
            f'<h2>{html.escape(name)}</h2><div class="rule"></div>{blurb_html}</div>')


def document(spec: dict, mode: str, body: str, entries: list, pages: dict,
             contents_heading: str = "Contents", contents_intro: str = "") -> str:
    footer = spec["title"]
    toc_html = toc(entries, pages, contents_heading, contents_intro) if entries else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<title>{html.escape(spec["title"])}</title><style>{fonts_css()}</style>'
            f'<style>{css(mode, spec["page"], spec["body_pt"], footer)}</style></head>'
            f'<body class="mode-{mode}">{cover(spec)}{toc_html}{body}{backcover()}</body></html>')
