"""HTML snippets for the competitor-report panels on a product page.

Miro's report "Compete through useful results" (11 Oct 2026) asks each page
to show what the buyer will finish, the free lesson that leads to it, what
was tested and when, a troubleshooting preview, the approval step, real
samples, exactly what is in the box, a learn-together route and a bounded
help policy. The data is in catalog.json (catalog.py parses it); this module
only turns it into markup, so site.py can drop each panel where it fits.

Rules every function keeps:

* Empty data renders "" -- never a heading over nothing.
* Nothing from `unverified`, a `*_note` key or a null store value is ever
  printed: those are Miro's open questions (see `open_questions`).
* A free lesson or a sample is linked only once it has a URL; a planned or
  proposed video is never announced as if it existed.
* Every string is escaped. No script, no request, no storage; the 3D kit
  scene gets plain JSON (`kit_scene_json`) and the static list beside it
  (`inclusions_panel`) is the no-motion fallback.

Pure: imports catalog.py only (tutorial titles are looked up lazily).
"""
from __future__ import annotations

import json
from datetime import date
from html import escape
from pathlib import Path

from .catalog import Catalog, Offer, Product

PROPOSALS_PATH = Path(__file__).with_name("tutorial_proposals.json")
KIND_LABELS = {"video": "Videos", "guide": "Guide", "files": "Files",
               "worksheet": "Worksheets"}
TROUBLE_LABELS = {"setup": "Setup issue", "expected": "Working as designed",
                  "unsupported": "Not supported as shipped"}
EVIDENCE_LABELS = {"sample-lesson": "Sample lesson", "worksheet": "Worksheet",
                   "page-image": "Real page", "template": "Template",
                   "exercise": "Exercise"}


def _e(text: object) -> str:
    return escape(str(text), quote=True)


def _day(iso: str) -> str:
    """2026-10-01 -> 1 Oct 2026."""
    d = date.fromisoformat(iso)
    return f"{d.day} {d:%b %Y}"


def _section(anchor: str, eyebrow: str, title: str, body: str) -> str:
    return (f'<section id="{anchor}" class="op op-{anchor}" style="padding-top:0">'
            f'<div class="wrap"><div class="section-head"><div>'
            f'<p class="eyebrow">{_e(eyebrow)}</p><h2>{_e(title)}</h2></div></div>'
            f"{body}</div></section>")


# ------------------------------------------------------------- 01 finish

def finish_panel(p: Product) -> str:
    f = p.finish
    if f is None:
        return ""
    preview = f'<p class="op-preview">{_e(f.preview)}</p>' if f.preview else ""
    return _section("finish", "What you will finish", "The finished project",
                    f'<p class="lede">{_e(f.deliverable)}</p>{preview}')


# ------------------------------------------------------------ 02 journey

def _lesson_title(series: str, slug: str) -> str:
    if series == "proposal":
        try:
            props = json.loads(PROPOSALS_PATH.read_text(encoding="utf-8"))["proposals"]
        except (OSError, ValueError, KeyError):
            return ""
        return next((str(x["title"]) for x in props if x.get("key") == slug), "")
    try:
        from . import tutorial_series as ts
    except ImportError:          # the tutorial lane is optional to the store
        return ""
    s = ts.series(series)
    ep = next((e for e in s.episodes if e.slug == slug), None) if s else None
    return ep.title if ep else ""


def journey_panel(p: Product) -> str:
    j = p.journey
    if j is None:
        return ""
    linked = [(lr, _lesson_title(lr.series, lr.slug)) for lr in j.free_lessons if lr.url]
    watch = "".join(f'<li><a href="{_e(lr.url)}" rel="noopener">{_e(t or "Free lesson")}'
                    "</a></li>" for lr, t in linked)
    steps = ""
    if watch:
        steps += f'<div class="op-step"><h3>1. Watch, free</h3><ul>{watch}</ul></div>'
        if j.exercise:
            steps += f'<div class="op-step"><h3>2. Try</h3><p>{_e(j.exercise)}</p></div>'
    adds = "".join(f"<li><b>{_e(w)}</b> {_e(d)}</li>" for w, d in j.paid_adds)
    if adds:
        steps += (f'<div class="op-step op-adds"><h3>What the full {_e(p.kind.lower())} '
                  f"adds</h3><ul>{adds}</ul></div>")
    if not steps:
        return ""
    if watch:
        eyebrow, title = "From free lesson to finished task", "Watch, try, then go further"
    else:                        # nothing to watch yet: don't promise a lesson
        eyebrow, title = "Beyond the free material", f"What the full {p.kind.lower()} adds"
    return _section("journey", eyebrow, title, f'<div class="op-journey">{steps}</div>')


# ------------------------------------------------------------- 03 tested

def tested_panel(p: Product) -> str:
    if not p.tested:
        return ""
    rows = "".join(
        f"<li><b>{_e(c.tool)}</b>{(' · ' + _e(c.version)) if c.version else ''}"
        f' <span class="op-date">checked {_e(_day(c.checked_on))}</span>'
        f"<p>{_e(c.what)}</p><p class=\"muted\">{_e(c.by)}</p></li>" for c in p.tested)
    return _section("tested", "Tested with", "What was checked, and when",
                    f'<ul class="op-checks">{rows}</ul>')


# ---------------------------------------------------- 04 troubleshooting

def troubleshooting_panel(p: Product, limit: int = 4) -> str:
    items = p.troubleshooting[:limit]
    if not items:
        return ""
    out = ""
    for t in items:
        io = ""
        if t.example_input:
            io += f"<dt>Example input</dt><dd><code>{_e(t.example_input)}</code></dd>"
        if t.expected_output:
            io += f"<dt>What you see</dt><dd><code>{_e(t.expected_output)}</code></dd>"
        out += (f'<details><summary><span class="tag">{_e(TROUBLE_LABELS[t.kind])}</span> '
                f"{_e(t.problem)}</summary><div class=\"ans\">"
                f"{f'<dl>{io}</dl>' if io else ''}<p>{_e(t.recovery)}</p></div></details>")
    more = len(p.troubleshooting) - len(items)
    tail = f'<p class="muted">And {more} more like these.</p>' if more > 0 else ""
    return _section("troubleshooting", "If you get stuck", "Troubleshooting preview",
                    f'<div class="acc">{out}</div>{tail}')


# ---------------------------------------------------------- 06 review demo

def review_demo_panel(p: Product) -> str:
    """Request -> draft -> stop at the review step. Static and readable as
    is; a script may reveal one step at a time using data-step."""
    r = p.review_demo
    if r is None:
        return ""
    steps = (("request", "The request", r.request), ("draft", "The draft", r.draft),
             ("review", "It stops here for review", r.review_step),
             ("approve", "If you approve", r.on_approve),
             ("silence", "If nobody answers", r.on_silence))
    lis = "".join(f'<li data-step="{k}"><h3>{_e(h)}</h3><p>{_e(t)}</p></li>'
                  for k, h, t in steps if t)
    return _section("review", "Human review is the method", "The approval step, shown",
                    f'<ol class="op-review">{lis}</ol>'
                    f'<p class="op-limits"><b>What this control doesn\'t do.</b> '
                    f"{_e(r.limits)}</p>")


# ------------------------------------------------------------- 07 evidence

def evidence_panel(p: Product) -> str:
    """Only samples that are published (have a URL). Until Miro publishes
    one, this is empty; the vault paths are in the catalog for him."""
    items = [e for e in p.evidence if e.url]
    if not items:
        return ""
    lis = "".join(f'<li><span class="tag">{_e(EVIDENCE_LABELS[e.kind])}</span> '
                  f'<a href="{_e(e.url)}">{_e(e.label)}</a></li>' for e in items)
    return _section("evidence", "See the teaching first", "Samples you can inspect",
                    f'<ul class="op-evidence">{lis}</ul>')


# ------------------------------------------------------------ 05 audiences

def audiences_line(cat: Catalog, p: Product) -> str:
    names = [a for a in (cat.audience(k) for k in p.audiences) if a]
    if not names:
        return ""
    lis = "".join(f'<li title="{_e(a.description)}">{_e(a.name)}</li>' for a in names)
    return f'<ul class="op-audiences" aria-label="Built around">{lis}</ul>'


# --------------------------------------------------------- 08 family route

def family_route_panel(cat: Catalog, p: Product) -> str:
    out = ""
    for r in cat.routes_for(p.slug):
        titles = [t.title for t in (cat.product(s) for s in r.products) if t]
        steps = "".join(f"<li><h3>{_e(s.title)}</h3><p class=\"muted\">{_e(s.who)}</p>"
                        f"<p>{_e(s.do)}</p></li>" for s in r.steps)
        prints = "".join(f"<li>{_e(x)}</li>" for x in r.printables)
        bounds = "".join(f"<li>{_e(x)}</li>" for x in r.boundaries)
        overlap = f'<p class="muted">{_e(r.overlap)}</p>' if r.overlap else ""
        body = (f"<p>{_e(r.people)}</p>"
                f'<p class="muted">Uses: {_e(" and ".join(titles))}.</p>'
                f'<ol class="op-route">{steps}</ol>'
                f'<div class="two"><div><h3>Printable</h3><ul>{prints}</ul></div>'
                f"<div><h3>Ground rules</h3><ul>{bounds}</ul></div></div>{overlap}")
        out += _section("together", "For two people", r.title, body)
    return out


# ------------------------------------------------------ 09 what's in the box

def inclusions_data(offer: Offer) -> list[dict[str, object]]:
    """The kit-unfolding scene's labels, one per thing in the box."""
    return [{"kind": i.kind, "kind_label": KIND_LABELS[i.kind], "label": i.label,
             "detail": i.detail, **({"count": i.count} if i.count is not None else {})}
            for i in offer.inclusions]


def kit_scene_json(p: Product) -> str:
    """For <script type="application/json" id="kit-data">: every offer's box.
    `<` is escaped so the JSON can't close the script element."""
    data = {"product": p.slug,
            "offers": [{"id": o.id, "name": o.name, "items": inclusions_data(o)}
                       for o in p.offers if o.inclusions]}
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")


def inclusions_panel(p: Product) -> str:
    """The static list the 3D scene unfolds: readable with no motion."""
    groups = ""
    for o in p.offers:
        if not o.inclusions:
            continue
        lis = "".join(
            f'<li data-kind="{_e(i.kind)}"><b>{_e(i.label)}</b>'
            f"{(' · ' + _e(i.detail)) if i.detail else ''}</li>" for i in o.inclusions)
        head = f"<h3>{_e(o.name)}</h3>" if len(p.offers) > 1 else ""
        groups += f'<div class="op-box" data-offer="{_e(o.id)}">{head}<ul>{lis}</ul></div>'
    if not groups:
        return ""
    return _section("box", "Exactly what you get", "In the box",
                    f'<div class="op-boxes">{groups}</div>')


# ------------------------------------------------------------ 10 support

def support_panel(cat: Catalog) -> str:
    s = cat.support
    if not s:
        return ""

    def items(key: str, tag: str = "ul") -> str:
        lis = "".join(f"<li>{_e(x)}</li>" for x in s.get(key) or [])
        return f"<{tag}>{lis}</{tag}>"

    cols = [("access_help", "Access help"), ("covers", "What you can report"),
            ("not_covered", "Not included")]
    grid = "".join(f"<div><h3>{_e(h)}</h3>{items(k)}</div>" for k, h in cols if s.get(k))
    body = f'<div class="op-support">{grid}</div>'
    if s.get("report_broken"):
        body += f"<h3>Reporting a broken workflow</h3>{items('report_broken', 'ol')}"
    for key, head in (("response_time", "When to expect a reply"),
                      ("updates", "Updates"),
                      ("implementation_services", "Want it set up for you?")):
        if s.get(key):           # null until Miro decides: then nothing is said
            body += f"<h3>{_e(head)}</h3><p>{_e(s[key])}</p>"
    return _section("support", "Help", "What help is included", body)


# ------------------------------------------------------------- assembled

def product_panels(cat: Catalog, p: Product) -> str:
    """All product panels in the report's order, for one insertion point."""
    return "".join((finish_panel(p), journey_panel(p), inclusions_panel(p),
                    review_demo_panel(p), troubleshooting_panel(p), tested_panel(p),
                    evidence_panel(p), family_route_panel(cat, p)))


NAV = (("finish", "What you'll finish"), ("journey", "Free lesson"),
       ("box", "In the box"), ("review", "Approval step"),
       ("troubleshooting", "Troubleshooting"), ("tested", "Tested with"),
       ("evidence", "Samples"), ("together", "Learn together"))


def nav_links(cat: Catalog, p: Product) -> str:
    """<a> links for the page's on-this-page nav, only for panels present."""
    html = product_panels(cat, p)
    return "".join(f'<a href="#{a}">{_e(t)}</a>' for a, t in NAV
                   if f'<section id="{a}"' in html)


def open_questions(cat: Catalog) -> list[tuple[str, str]]:
    """Everything Miro must answer or verify before it can be shown:
    (where, question). Never rendered on the store."""
    out = [(p.slug, q) for p in cat.products for q in p.unverified]
    out += [(f"route {r.slug}", q) for r in cat.routes for q in r.unverified]
    out += [(f"store.{k.removesuffix('_note')}", str(v)) for k, v in cat.support.items()
            if k.endswith("_note") and cat.support.get(k.removesuffix("_note")) is None]
    return out
