"""The buyer's course page (ATLAS Academy access page, paid view).

`render` is pure: the catalog, the Stripe verdict, a signed link per file and
the duration sidecars go in, an HTML fragment comes out. Nothing here signs
a link, calls Stripe or reads a video. `load_sidecar` is the one file read,
and it is separate.

What the page promises, and where each part comes from:
  * lesson numbers and titles  <- the file label ("Lesson 1.2: Title (video)")
  * module headings            <- the product's `inside` list in the catalog,
                                  matched by lesson number within the edition
  * lengths and the total      <- `<videos dir>/index.json`, written by the
                                  audit tool; absent or malformed = none shown
  * progress ("watched")       <- the buyer's own browser (localStorage, keyed
                                  by offer id + file key); never sent anywhere

Every label is escaped. File paths and checkout session ids never appear in
the page's script; the only per-file value the script keeps is the file key.
"""
from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import escape
from pathlib import Path

from . import lesson_youtube
from .catalog import Catalog, File, Offer, Product
from .stripe_read import Verdict

SIDECAR_NAME = "index.json"
PRINT_SUFFIX = "-print"
_LESSON_RE = re.compile(r"^Lesson\s+(\d+(?:\.\d+)?):\s*(.*?)(?:\s*\(video\))?\s*$")
_ITEM_NUM_RE = re.compile(r"^(\d+(?:\.\d+)?)\b")
# edition folder -> the word its module headings carry in the catalog
_EDITIONS = {"adults": "adults", "60plus": "60+"}


# ------------------------------------------------------------------ inputs

@dataclass(frozen=True)
class FileLink:
    """What the page may say about one file. `href` is the signed download
    link, `view_href` the same link read inline; both empty when the file is
    not on the server."""

    present: bool
    href: str = ""
    view_href: str = ""
    facts: str = ""                 # trusted HTML: size and date, from disk


@dataclass(frozen=True)
class Sidecar:
    read_at: datetime
    seconds: Mapping[str, float] = field(default_factory=dict)


_sidecar_cache: dict[str, tuple[int, int, Sidecar]] = {}


def load_sidecar(root: Path, rel_dir: str) -> Sidecar | None:
    """`<root>/<rel_dir>/index.json` as a Sidecar, or None when it is missing,
    a symlink, or not exactly the agreed shape. Re-read only when its mtime
    or size changes."""
    path = Path(root) / rel_dir / SIDECAR_NAME
    try:
        if path.is_symlink():
            return None
        st = path.stat()
    except OSError:
        return None
    key = str(path)
    held = _sidecar_cache.get(key)
    if held and held[0] == st.st_mtime_ns and held[1] == st.st_size:
        return held[2]
    try:
        raw = json.loads(path.read_text("utf-8"))
        side = _parse_sidecar(raw)
    except (OSError, ValueError):
        return None
    if side is None:
        return None
    if len(_sidecar_cache) > 64:
        _sidecar_cache.clear()
    _sidecar_cache[key] = (st.st_mtime_ns, st.st_size, side)
    return side


def _parse_sidecar(raw: object) -> Sidecar | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("lessons"), dict):
        return None
    try:
        read_at = datetime.fromisoformat(str(raw.get("read_at", "")))
    except ValueError:
        return None
    if read_at.tzinfo is None:
        read_at = read_at.replace(tzinfo=UTC)
    seconds: dict[str, float] = {}
    for name, entry in raw["lessons"].items():
        value = entry.get("seconds") if isinstance(entry, dict) else None
        if (not isinstance(name, str) or isinstance(value, bool)
                or not isinstance(value, (int, float)) or not math.isfinite(value)
                or value <= 0):
            return None
        seconds[name] = float(value)
    return Sidecar(read_at=read_at, seconds=seconds)


def video_dirs(v: Verdict) -> list[str]:
    """Each folder that holds this purchase's lesson videos, once, in order."""
    out: list[str] = []
    for o in v.offers:
        for f in o.files:
            if f.path.lower().endswith(".mp4"):
                d = f.path.rsplit("/", 1)[0] if "/" in f.path else ""
                if d not in out:
                    out.append(d)
    return out


YOUTUBE_FRAME = "https://www.youtube-nocookie.com"


def csp(nonce: str) -> str:
    return ("default-src 'none'; "
            f"script-src 'nonce-{nonce}'; "
            "style-src 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self'; "
            f"frame-src 'self' {YOUTUBE_FRAME}; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'")


# ------------------------------------------------------------------ grouping

@dataclass
class _Lesson:
    file: File
    num: str
    title: str


@dataclass
class _Course:
    title: str
    offer_id: str
    cover: str
    directory: str
    edition: str
    modules: list[tuple[str, list[_Lesson]]] = field(default_factory=list)
    lessons: list[_Lesson] = field(default_factory=list)
    product_slug: str = ""


def _is_video(f: File) -> bool:
    return f.path.lower().endswith(".mp4")


def _is_pdf(f: File) -> bool:
    return f.path.lower().endswith(".pdf")


def _parse_label(label: str) -> tuple[str, str]:
    m = _LESSON_RE.match(label)
    return (m.group(1), m.group(2)) if m else ("", label)


def _owner(cat: Catalog, key: str) -> Product | None:
    for p in cat.products:
        for o in p.offers:
            if any(f.key == key for f in o.files):
                return p
    return None


def _edition(directory: str) -> str:
    last = directory.rsplit("/", 1)[-1]
    return last.split("-", 1)[1] if "-" in last else ""


def _course_offer(product: Product, keys: set[str]) -> Offer | None:
    """The smallest single offer of the product that holds all these lessons."""
    fits = [o for o in product.offers if keys <= {f.key for f in o.files}]
    return min(fits, key=lambda o: len(o.files)) if fits else None


def _courses(cat: Catalog, v: Verdict) -> list[_Course]:
    groups: dict[tuple[str, str], list[File]] = {}
    seen: set[str] = set()
    for o in v.offers:
        for f in o.files:
            if not _is_video(f) or f.key in seen:
                continue
            seen.add(f.key)
            product = _owner(cat, f.key)
            directory = f.path.rsplit("/", 1)[0] if "/" in f.path else ""
            groups.setdefault((product.slug if product else "", directory), []).append(f)
    courses = []
    for (slug, directory), files in groups.items():
        product = next((p for p in cat.products if p.slug == slug), None)
        offer = _course_offer(product, {f.key for f in files}) if product else None
        multi = product is not None and len({
            f.path.rsplit("/", 1)[0] for o in product.offers for f in o.files
            if _is_video(f)}) > 1
        if product is None:
            title = "Lessons"
        else:
            title = offer.name if (multi and offer) else product.title
        course = _Course(title=title, offer_id=offer.id if offer else slug,
                         cover=(offer.cover if offer and offer.cover else
                                (product.cover if product else "")),
                         directory=directory, edition=_edition(directory),
                         product_slug=slug)
        for f in files:
            num, name = _parse_label(f.label)
            course.lessons.append(_Lesson(f, num, name))
        _place(course, product)
        courses.append(course)
    return courses


def _place(course: _Course, product: Product | None) -> None:
    """Fill course.modules: catalog order, lessons matched by number."""
    mods = list(product.inside) if product else []
    word = _EDITIONS.get(course.edition)
    if word:
        chosen = [m for m in mods if word in m[0].lower()]
        mods = chosen or mods
    by_num: dict[str, int] = {}
    for i, (_title, items) in enumerate(mods):
        for item in items:
            m = _ITEM_NUM_RE.match(item)
            if m:
                by_num.setdefault(m.group(1), i)
    buckets: dict[int, list[_Lesson]] = {}
    rest: list[_Lesson] = []
    for lesson in course.lessons:
        idx = by_num.get(lesson.num)
        if idx is None:
            rest.append(lesson)
        else:
            buckets.setdefault(idx, []).append(lesson)
    for i in sorted(buckets):
        title = mods[i][0]
        if word and title.lower().startswith(word):
            title = title.split(",", 1)[1].strip() if "," in title else title
        course.modules.append((title, buckets[i]))
    if rest:
        course.modules.append(("Lessons", rest))


# ------------------------------------------------------------------ rendering

def _clock(seconds: float) -> str:
    s = round(seconds)
    return f"{s // 60}:{s % 60:02d}"


def _length(seconds: float) -> str:
    minutes = round(seconds / 60)
    h, m = divmod(minutes, 60)
    return f"{h} h {m} min" if h else f"{m} min"


def _measured(read_at: datetime) -> str:
    d = read_at.astimezone(UTC)
    return f"{d.day} {d:%b %Y}"


def _contact_row(label: str, contact_html: str) -> str:
    return (f'<li class="missing">{escape(label)}<br><span class="off">Not on the '
            f'server yet</span><br><span class="muted">Email {contact_html} and '
            "we'll send it.</span></li>")


def _watch(preview_ids: Mapping[str, str] | None, key: str) -> str:
    """The "Watch on YouTube" link, only for a PUBLIC preview id the caller
    read from the database (`lesson_youtube.public_ids`) and only if it is a
    well-formed id. Never an unlisted id: those come from `embed_ids`, which
    never reaches this."""
    return lesson_youtube.watch_link_html((preview_ids or {}).get(key, ""))


def _lesson_row(c: _Course, lesson: _Lesson, links: Mapping[str, FileLink],
                side: Sidecar | None, contact_html: str,
                youtube_ids: Mapping[str, str] | None = None,
                preview_ids: Mapping[str, str] | None = None) -> str:
    link = links.get(lesson.file.key)
    yt = (youtube_ids or {}).get(lesson.file.key, "")
    if not lesson_youtube.valid_video_id(yt):
        yt = ""
    if (link is None or not link.present) and not yt:
        return _contact_row(lesson.file.label, contact_html)
    link = link if link is not None and link.present else FileLink(present=False)
    name = lesson.file.path.rsplit("/", 1)[-1]
    secs = side.seconds.get(name) if side else None
    dur = f'<span class="dur">{_clock(secs)}</span>' if secs else ""
    num = f'<span class="num">{escape(lesson.num)}</span>' if lesson.num else ""
    ytattr = f' data-yt="{escape(yt)}"' if yt else ""
    return (f'<li><a class="lesson" href="{escape(link.view_href or "#")}" '
            f'data-dl="{escape(link.href)}"{ytattr} data-offer="{escape(c.offer_id)}" '
            f'data-key="{escape(lesson.file.key)}" data-num="{escape(lesson.num)}" '
            f'data-title="{escape(lesson.title)}">{num}'
            f'<span class="name">{escape(lesson.title)}</span>{dur}</a>'
            '<span class="tick" aria-hidden="true">Watched</span>'
            f"{_watch(preview_ids, lesson.file.key)}</li>")


def _course_section(c: _Course, links: Mapping[str, FileLink],
                    sidecars: Mapping[str, Sidecar], contact_html: str,
                    cover_url: str,
                    youtube_ids: Mapping[str, str] | None = None,
                    preview_ids: Mapping[str, str] | None = None) -> str:
    side = sidecars.get(c.directory)
    meta = f"{len(c.lessons)} lessons"
    if side and all(les.file.path.rsplit("/", 1)[-1] in side.seconds
                    for les in c.lessons):
        total = sum(side.seconds[les.file.path.rsplit("/", 1)[-1]] for les in c.lessons)
        meta += f", {_length(total)}, measured {_measured(side.read_at)}"
    cover = (f'<img class="cover" src="{escape(cover_url)}" alt="" width="64" height="64">'
             if cover_url else "")
    parts = [(f'<section class="course" aria-label="{escape(c.title)}">'
              f'<div class="chead">{cover}<div><h2>{escape(c.title)}</h2>'
              f'<p class="muted">{escape(meta)}</p>'
              f'{_watch(preview_ids, "product-" + c.product_slug) if c.product_slug else ""}'
              '</div></div>')]
    for title, lessons in c.modules:
        if not (len(c.modules) == 1 and title == c.title):
            parts.append(f'<h3 class="module">{escape(title)}</h3>')
        rows = "".join(_lesson_row(c, les, links, side, contact_html, youtube_ids, preview_ids)
                        for les in lessons)
        parts.append(f'<ul class="lessons">{rows}</ul>')
    parts.append("</section>")
    return "".join(parts)


def _pdf_block(heading: str, label: str, link: FileLink, *, print_version: bool) -> str:
    view = escape(link.view_href)
    return (f'<div class="reader"><h3>{escape(heading)}</h3>'
            f'<iframe class="pdf" title="{escape(label)}" src="{view}" '
            'loading="lazy"></iframe>'
            f'<a class="alt" href="{view}" target="_blank" rel="noopener">'
            f"{'Open full screen and print' if not print_version else 'Open print version full screen'}"
            f'</a><a class="dl" href="{escape(link.href)}" download>Download PDF</a></div>')


def _materials(v: Verdict, links: Mapping[str, FileLink], contact_html: str,
               skip_video: bool = True) -> str:
    files: list[File] = []
    seen: set[str] = set()
    for o in v.offers:
        for f in o.files:
            if f.key not in seen and not (skip_video and _is_video(f)):
                seen.add(f.key)
                files.append(f)
    keys = {f.key for f in files}
    twins = {k for k in keys if k.endswith(PRINT_SUFFIX)
             and k[:-len(PRINT_SUFFIX)] in keys}
    items = []
    for f in files:
        if f.key in twins:
            continue                                      # shown with its screen version
        link = links.get(f.key)
        if link is None or not link.present:
            items.append(_contact_row(f.label, contact_html))
            continue
        twin = f.key + PRINT_SUFFIX
        if _is_pdf(f) and twin in keys:
            tl = links.get(twin)
            block = _pdf_block("Read on screen", f.label, link, print_version=False)
            if tl is not None and tl.present:
                block += _pdf_block("Print version", f.label + " (print version)", tl,
                                    print_version=True)
            items.append(f"<li><b>{escape(f.label)}</b>{link.facts}{block}</li>")
        elif _is_pdf(f):
            items.append(f"<li>{escape(f.label)}{link.facts}"
                         f"{_pdf_block('Read on screen', f.label, link, print_version=False)}</li>")
        else:
            items.append(f'<li>{escape(f.label)}{link.facts}<br><a class="dl" '
                         f'href="{escape(link.href)}" download>Download</a></li>')
    if not items:
        return ""
    return f'<h2>Materials</h2><div class="box"><ul class="files">{"".join(items)}</ul></div>'


_SCRIPT = """(function(){
var v=document.getElementById('player'),yf=document.getElementById('yt');if(!v){return;}
var links=[].slice.call(document.querySelectorAll('a.lesson'));
if(!links.length){return;}
var box=document.getElementById('player-box'),now=document.getElementById('now'),
dl=document.getElementById('dl'),prev=document.getElementById('prev'),
next=document.getElementById('next'),cur=-1;
function store(){try{return window.localStorage;}catch(e){return null;}}
function key(a){return 'atlas-academy:'+a.getAttribute('data-offer')+':'+a.getAttribute('data-key');}
function seen(a){try{var s=store();return !!(s&&s.getItem(key(a)));}catch(e){return false;}}
function tick(a){a.parentNode.className='watched';}
function mark(a){try{var s=store();if(s){s.setItem(key(a),'1');}}catch(e){}tick(a);}
function show(i,go){
 if(i<0||i>=links.length){return;}
 var a=links[i];if(cur>=0){links[cur].removeAttribute('aria-current');}
 cur=i;a.setAttribute('aria-current','true');
 var yid=a.getAttribute('data-yt'),d=a.getAttribute('data-dl');
 if(yid&&/^[A-Za-z0-9_-]{11}$/.test(yid)&&yf){
  try{v.pause();}catch(e){}v.removeAttribute('src');v.hidden=true;yf.hidden=false;
  yf.src='https://www.youtube-nocookie.com/embed/'+yid+'?rel=0&modestbranding=1&playsinline=1'+(go?'&autoplay=1':'');
  if(go){mark(a);}
 }else{
  if(yf){yf.hidden=true;yf.removeAttribute('src');}v.hidden=false;v.src=a.getAttribute('href');
 }
 dl.href=d||'#';dl.hidden=!d;
 var n=a.getAttribute('data-num');
 now.textContent=(n?'Lesson '+n+': ':'')+a.getAttribute('data-title');
 prev.disabled=(i===0);next.disabled=(i===links.length-1);
 if(go){var p=v.play();if(p&&p.catch){p.catch(function(){});}}
}
box.hidden=false;
var first=0;
for(var i=0;i<links.length;i++){if(seen(links[i])){tick(links[i]);}}
for(var j=0;j<links.length;j++){if(!seen(links[j])){first=j;break;}}
links.forEach(function(a,i){a.addEventListener('click',function(e){
 e.preventDefault();show(i,true);
 try{box.scrollIntoView({block:'center',behavior:'smooth'});}catch(x){}
});});
prev.addEventListener('click',function(){show(cur-1,true);});
next.addEventListener('click',function(){show(cur+1,true);});
v.addEventListener('ended',function(){if(cur>=0){mark(links[cur]);}
 if(cur<links.length-1){show(cur+1,true);}});
show(first,false);
})();"""

COURSE_CSS = """
.player{margin:16px 0}
.player h2{margin:0 0 6px}
.player .bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:6px}
.player button{font:inherit;font-weight:700;min-height:48px;padding:10px 18px;
border-radius:10px;border:2px solid var(--ultra);background:transparent;color:var(--ink);
cursor:pointer}
.player button:disabled{opacity:.45;cursor:default}
.player button:focus-visible{outline:3px solid var(--ink);outline-offset:3px}
iframe#yt{width:100%;aspect-ratio:16/9;border:0;border-radius:10px;background:#000}
.chead{display:flex;gap:14px;align-items:center;margin:28px 0 4px}
.chead h2{margin:0}
.chead .muted{margin:0}
img.cover{border-radius:8px;object-fit:cover;background:var(--card)}
h3.module{font-size:1.02rem;margin:20px 0 6px;color:var(--soft)}
ul.lessons{list-style:none;margin:0;padding:0;border:1px solid var(--rule);
border-radius:10px;overflow:hidden}
ul.lessons li{display:flex;align-items:stretch;border-top:1px solid var(--rule)}
ul.lessons li:first-child{border-top:0}
ul.lessons li.missing{display:block;padding:12px 14px}
a.lesson{flex:1;display:flex;gap:12px;align-items:center;min-height:48px;padding:10px 14px;
color:var(--ink);text-decoration:none}
a.lesson:hover{background:var(--card)}
a.lesson[aria-current="true"]{background:var(--card);box-shadow:inset 4px 0 0 var(--ultra)}
a.lesson .num{color:var(--deep);font-weight:700;min-width:2.4em}
a.lesson .name{flex:1}
a.lesson .dur{color:var(--soft);font-size:.9rem}
.tick{display:none;align-self:center;padding:0 12px;color:var(--deep);font-size:.85rem;
font-weight:700}
li.watched .tick{display:block}
a.yt{align-self:center;padding:0 12px;font-size:.85rem;white-space:nowrap}
.reader{margin:10px 0 18px}
.reader h3{font-size:1rem;margin:12px 0 0}
@media (max-width:520px){main{padding:24px 16px 40px}a.lesson{flex-wrap:wrap}
iframe.pdf{height:70vh}}
"""


def _player() -> str:
    return ('<div class="player box" id="player-box" hidden>'
            '<h2 id="now">Pick a lesson</h2>'
            '<video id="player" controls preload="none" playsinline></video>'
            '<iframe id="yt" title="Lesson video" hidden allowfullscreen '
            'allow="fullscreen; picture-in-picture; encrypted-media" '
            'referrerpolicy="strict-origin-when-cross-origin"></iframe>'
            '<div class="bar"><button type="button" id="prev">Previous</button>'
            '<button type="button" id="next">Next</button>'
            '<a class="alt" id="dl" href="#" download>Download this lesson</a></div></div>')


def render(cat: Catalog, v: Verdict, links: Mapping[str, FileLink],
           sidecars: Mapping[str, Sidecar], *, contact_html: str, nonce: str,
           cover_base: str = "",
           youtube_ids: Mapping[str, str] | None = None,
           preview_ids: Mapping[str, str] | None = None) -> str:
    """The course page body for a paid purchase: player, one section per
    course, then the materials. `cover_base` is the absolute folder that
    serves catalog cover paths ("" = no cover pictures)."""
    courses = _courses(cat, v)
    names = escape(", ".join(o.name for o in v.offers))
    if not courses:
        return (f"<h1>Your files</h1><p>Thank you. You bought: <b>{names}</b>.</p>"
                + _materials(v, links, contact_html, skip_video=False))
    sections = "".join(
        _course_section(c, links, sidecars, contact_html,
                        f"{cover_base.rstrip('/')}/{c.cover}" if cover_base and c.cover else "",
                        youtube_ids, preview_ids)
        for c in courses)
    return (f"<h1>Your course</h1><p>Thank you. You bought: <b>{names}</b>.</p>"
            "<noscript><style>.player{display:none}</style>"
            "<p class=\"muted\">Turn on JavaScript to play lessons here, or open any "
            "lesson below on its own.</p></noscript>"
            f"{_player()}{sections}{_materials(v, links, contact_html)}"
            f'<script nonce="{escape(nonce)}">{_SCRIPT}</script>')
