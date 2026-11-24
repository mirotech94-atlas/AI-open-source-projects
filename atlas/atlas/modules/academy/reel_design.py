"""The look of the daily Academy reel: one edition per product, each its
own palette, type and signature motion, rendered frame by frame from HTML.

Miro, 2026-10-06: "each reel should be different than each other". A
product's reel is drawn in that product's edition (`EDITIONS`, keyed by
slug), so no two products share a look, and a day variant swaps the beat
order and the timing so the same product's next reel differs from its
last. Everything shown is still catalog text (`reel.script_for`) plus the
fixed hooks; the design adds no words.

How a frame is made: every beat is an HTML document whose CSS animations
run from t = 0. Chromium (Playwright, in the api image for the browser
module) loads it, pauses every animation, steps `currentTime` to each
frame and screenshots it, so the motion is deterministic and the beat
lasts exactly as long as its narration. ffmpeg joins frames and audio.
Without Chromium, `reel.py` falls back to the ffmpeg cards.

Fonts: Cabinet Grotesk (display) and Atkinson Hyperlegible Next (text),
embedded from the store's assets; the cover is the only picture.
"""
from __future__ import annotations

import asyncio
import base64
import html
import os
from dataclasses import dataclass
from pathlib import Path

from .. import store_origin

W, H, FPS = 1080, 1920, 24
ASSETS = Path(__file__).with_name("assets")
TAG = "Made with AI"


def _data(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def fonts_css() -> str:
    out = []
    for weight in (500, 700, 800):
        f = ASSETS / "fonts" / f"cabinet-{weight}.woff2"
        if f.is_file():
            out.append(f'@font-face{{font-family:"Cabinet";src:url("{_data(f, "font/woff2")}") '
                       f'format("woff2");font-weight:{weight}}}')
    a = ASSETS / "fonts" / "atkinson-next-var.woff"
    if a.is_file():
        out.append(f'@font-face{{font-family:"Atk";src:url("{_data(a, "font/woff")}") '
                   f'format("woff");font-weight:100 900}}')
    return "".join(out)


def cover_data(cover: str) -> str:
    p = ASSETS / cover if cover else None
    if p is None or not p.is_file():
        return ""
    mime = "image/svg+xml" if p.suffix == ".svg" else "image/jpeg"
    return _data(p, mime)


BASE = f"""
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{W}px;height:{H}px;overflow:hidden;font-family:"Atk",system-ui,sans-serif;
-webkit-font-smoothing:antialiased}}
.tag{{position:absolute;top:56px;right:60px;font:700 30px/1 "Atk";letter-spacing:.02em;
padding:12px 18px;border-radius:8px;z-index:9}}
.foot{{position:absolute;left:84px;right:84px;bottom:110px;font:500 32px/1.3 "Atk";z-index:9}}
h1{{font-family:"Cabinet";font-weight:800;letter-spacing:-.03em}}
.w{{display:inline-block;opacity:0;transform:translateY(.4em);
animation:land .45s cubic-bezier(.2,.8,.2,1) forwards}}
@keyframes land{{to{{opacity:1;transform:none}}}}
.hk{{position:absolute;left:96px;right:140px;top:560px}}
.hk .slab{{position:absolute;left:-44px;top:.12em;width:16px;height:0;background:var(--ha);
animation:slab .55s cubic-bezier(.2,.8,.2,1) .04s forwards}}
@keyframes slab{{to{{height:calc(100% - .2em)}}}}
.hk .ln{{display:block;font:800 124px/1.02 "Cabinet";letter-spacing:-.035em;color:var(--hi);
transform-origin:0 100%;animation:slam .3s cubic-bezier(.2,.9,.25,1.12) both}}
@keyframes slam{{from{{transform:scale(1.16) translateY(-.06em)}}to{{transform:none}}}}
.hk.lp .ln{{font-family:"Atk";letter-spacing:0;font-size:118px}}
.store{{position:absolute;left:84px;top:150px;z-index:9;font:700 50px/1 "Atk";
padding:20px 28px;border-radius:10px;background:var(--ha);color:var(--hb);
transform:translateY(-30px);opacity:0;animation:land .45s cubic-bezier(.2,.8,.2,1) .5s forwards}}
.endcover{{display:block;width:420px;height:420px;margin-top:70px;border-radius:12px;opacity:0;
animation:land .5s ease-out .35s forwards}}
"""

# The hook beat (2026-10-11, "make the shorts actually wanting to watch it"):
# the claim itself is the picture. It is set huge, left-aligned inside the
# Shorts safe area (clear of the title and buttons the app lays over the
# bottom and right), and readable from frame 0, so the first frame is never
# blank. Each line drops from a slight overscale in turn, and one slab in
# the edition's accent grows down the left margin. That is the one bold
# element: no blur and no full-frame shadow, so it stays cheap to render
# on the 1-CPU box. Colours per edition: (ink, accent, ink on the accent).
HOOK_COLORS: dict[str, tuple[str, str, str]] = {
    "Guardrail": ("#EEF2FA", "#5B8CFF", "#0A0D14"),
    "Ledger": ("#EEF2FA", "#EEF2FA", "#0F1420"),
    "Graph": ("#EEF2FA", "#5B8CFF", "#EEF2FA"),
    "Large print": ("#EEF2FA", "#EEF2FA", "#0F1420"),
    "Shopfront": ("#EEF2FA", "#5B8CFF", "#0A0D14"),
    "Fridge notice": ("#EEF2FA", "#5B8CFF", "#EEF2FA"),
    "Three lanes": ("#EEF2FA", "#5B8CFF", "#0A0D14"),
}
HOOK_WRAP = 13


def hook_html(ed_name: str, text: str) -> str:
    import textwrap
    lines = textwrap.wrap(text, HOOK_WRAP) or [text]
    spans = "".join(
        f'<span class="ln" style="animation-delay:{i * 0.16:.2f}s">{html.escape(line)}</span>'
        for i, line in enumerate(lines))
    lp = " lp" if ed_name == "Large print" else ""
    return f'<div class="hk{lp}"><i class="slab"></i>{spans}</div>'


def words(text: str, start: float = 0.1, step: float = 0.09) -> str:
    """Each word lands on its own beat (every edition's reading motion)."""
    return " ".join(
        f'<span class="w" style="animation-delay:{start + i * step:.2f}s">{html.escape(w)}</span>'
        for i, w in enumerate(text.split()))


@dataclass(frozen=True)
class Edition:
    name: str
    css: str
    # (kind, text, title, cover, variant) -> inner HTML for one beat
    layout: object


# ------------------------------------------------------------------ editions
#
# Beat kinds: title (the product's name, the cover), hook, fact, cta.

def _guardrail(kind, text, title, cover, v):
    if kind == "title":
        return (f'<img class="cover" src="{cover}" alt="">'
                f'<div class="rail"></div><h1>{words(title, .6)}</h1>')
    if kind == "cta":
        return (f'<div class="rail long"></div><p class="big">{words(text, .3)}</p>'
                f'<p class="under">{html.escape(title)}</p>')
    return f'<div class="rail"></div><p class="big">{words(text, .4)}</p>'


GUARDRAIL = Edition("Guardrail", """
body{background:#0A0D14;color:#EEF2FA}
.tag{background:#5B8CFF;color:#0A0D14}
.foot{color:#8E99AE}
.s{position:absolute;inset:0;padding:320px 84px 0}
.cover{width:560px;height:560px;border-radius:18px;display:block;margin:0 0 70px;
box-shadow:0 30px 80px -30px #000,0 0 0 2px rgba(47,208,255,.3)}
.rail{height:6px;width:0;background:#5B8CFF;margin:0 0 54px;animation:rail .9s cubic-bezier(.2,.8,.2,1) forwards;
box-shadow:0 0 30px rgba(47,208,255,.6)}
.rail.long{animation-delay:.2s}
@keyframes rail{to{width:100%}}
h1{font-size:112px;line-height:1.02;color:#EEF2FA}
.big{font:800 86px/1.1 "Cabinet";letter-spacing:-.025em;color:#EEF2FA}
.under{margin-top:48px;font:500 36px/1.3 "Atk";color:#5B8CFF}
""", _guardrail)


def _ledger(kind, text, title, cover, v):
    if kind == "title":
        return (f'<div class="count"><span class="n"></span><span class="of">of 30</span></div>'
                f'<h1>{words(title, .8)}</h1>')
    if kind == "cta":
        return f'<p class="big">{words(text, .3)}</p><img class="cover" src="{cover}" alt="">'
    return f'<p class="num"></p><p class="big">{words(text, .4)}</p>'


LEDGER = Edition("Ledger", """
body{background:#0F1420;color:#EEF2FA;
background-image:repeating-linear-gradient(0deg,transparent 0 88px,rgba(31,26,23,.12) 88px 90px)}
.tag{background:#EEF2FA;color:#0F1420}
.foot{color:#8E99AE}
.s{position:absolute;inset:0;padding:520px 84px 0}
.count{display:flex;align-items:baseline;gap:22px;margin-bottom:60px}
.n{font:800 300px/1 "Cabinet";letter-spacing:-.05em;color:#EEF2FA;counter-reset:n 0;animation:tick 2.4s steps(30) forwards}
.n::after{content:counter(n)}
@keyframes tick{to{counter-increment:n 30}}
@property --x{syntax:"<integer>";initial-value:0;inherits:false}
.of{font:500 44px/1 "Atk";color:#8E99AE}
h1{font-size:120px;line-height:.98;color:#EEF2FA}
.num{height:6px;width:140px;background:#EEF2FA;margin-bottom:50px;transform-origin:left;
transform:scaleX(0);animation:draw .6s ease-out forwards}
@keyframes draw{to{transform:none}}
.big{font:700 100px/1.1 "Cabinet";letter-spacing:-.025em;color:#EEF2FA}
.cover{width:460px;height:460px;margin-top:90px;border-radius:10px;display:block;
box-shadow:0 24px 50px -24px rgba(31,26,23,.6)}
""", _ledger)


def _graph(kind, text, title, cover, v):
    nodes = "".join(f'<i class="n n{i}"></i>' for i in range(6))
    edges = "".join(f'<b class="e e{i}"></b>' for i in range(5))
    if kind == "title":
        return (f'<div class="g">{nodes}{edges}<img class="cover" src="{cover}" alt=""></div>'
                f'<h1>{words(title, 1.2)}</h1>')
    if kind == "cta":
        return f'<div class="g small">{nodes}{edges}</div><p class="big">{words(text, .6)}</p>'
    return f'<p class="big">{words(text, .3)}</p><div class="g small low">{nodes}{edges}</div>'


GRAPH = Edition("Graph", """
body{background:#0A0D14;color:#EEF2FA}
.tag{background:#5B8CFF;color:#EEF2FA}
.foot{color:#8E99AE}
.s{position:absolute;inset:0;padding:300px 84px 0}
.g{position:relative;height:720px;margin-bottom:60px}
.g.small{height:300px;opacity:.55}.g.low{margin-top:80px}
.n{position:absolute;width:26px;height:26px;border-radius:50%;background:#5B8CFF;
box-shadow:0 0 24px #5B8CFF;opacity:0;animation:pop .4s ease-out forwards}
.n0{left:6%;top:12%}.n1{left:70%;top:6%;animation-delay:.2s}.n2{left:88%;top:60%;animation-delay:.4s}
.n3{left:40%;top:90%;animation-delay:.6s}.n4{left:14%;top:70%;animation-delay:.8s}.n5{left:50%;top:40%;animation-delay:1s}
@keyframes pop{to{opacity:1}}
.e{position:absolute;height:3px;background:rgba(138,92,246,.6);transform-origin:left;transform:scaleX(0);
animation:edge .5s ease-out forwards}
.e0{left:7%;top:13%;width:64%;transform:rotate(-5deg) scaleX(0);animation-delay:.3s}
.e1{left:71%;top:7%;width:58%;transform:rotate(72deg) scaleX(0);animation-delay:.5s}
.e2{left:88%;top:61%;width:56%;transform:rotate(148deg) scaleX(0);animation-delay:.7s}
.e3{left:41%;top:91%;width:34%;transform:rotate(-143deg) scaleX(0);animation-delay:.9s}
.e4{left:15%;top:71%;width:48%;transform:rotate(-40deg) scaleX(0);animation-delay:1.1s}
@keyframes edge{to{transform:rotate(var(--r,0)) scaleX(1)}}
.e0{--r:-5deg}.e1{--r:72deg}.e2{--r:148deg}.e3{--r:-143deg}.e4{--r:-40deg}
.cover{position:absolute;left:50%;top:40%;width:360px;height:360px;transform:translate(-50%,-50%) scale(.6);
border-radius:50%;opacity:0;animation:node .8s cubic-bezier(.2,.8,.2,1) 1.2s forwards;
box-shadow:0 0 0 10px #0A0D14,0 0 0 14px #5B8CFF}
@keyframes node{to{opacity:1;transform:translate(-50%,-50%)}}
h1{font-size:104px;line-height:1;color:#EEF2FA}
.big{font:700 82px/1.12 "Cabinet";letter-spacing:-.02em;color:#EEF2FA}
""", _graph)


def _largeprint(kind, text, title, cover, v):
    if kind == "title":
        return f'<h1 class="grow">{html.escape(title)}</h1><img class="cover" src="{cover}" alt="">'
    if kind == "cta":
        return f'<p class="big grow2">{html.escape(text)}</p>'
    return f'<p class="big">{words(text, .3, .12)}</p>'


LARGEPRINT = Edition("Large print", """
body{background:#0F1420;color:#EEF2FA}
.tag{background:#EEF2FA;color:#0F1420}
.foot{color:#8E99AE}
.s{position:absolute;inset:0;padding:460px 84px 0}
h1{font-family:"Atk";font-weight:800;letter-spacing:0;font-size:150px;line-height:1.02}
.grow{transform-origin:left top;transform:scale(.42);animation:grow 1.4s cubic-bezier(.2,.8,.2,1) .3s forwards}
.grow2{transform-origin:left top;transform:scale(.6);animation:grow 1.1s cubic-bezier(.2,.8,.2,1) .2s forwards}
@keyframes grow{to{transform:none}}
.big{font:800 96px/1.12 "Atk";letter-spacing:0}
.cover{width:440px;height:440px;margin-top:90px;border-radius:12px;display:block;opacity:0;
animation:land .6s ease-out 1.5s forwards}
""", _largeprint)


def _shopfront(kind, text, title, cover, v):
    tiles = "".join(f'<i class="t t{i}"></i>' for i in range(4))
    if kind == "title":
        return f'<div class="row">{tiles}</div><h1>{words(title, 1.2)}</h1>'
    if kind == "cta":
        return f'<p class="big">{words(text, .3)}</p><img class="cover" src="{cover}" alt="">'
    return f'<p class="big">{words(text, .3)}</p><div class="row low">{tiles}</div>'


SHOPFRONT = Edition("Shopfront", """
body{background:#0A0D14;color:#EEF2FA}
.tag{background:#5B8CFF;color:#0A0D14}
.foot{color:#8E99AE}
.s{position:absolute;inset:0;padding:420px 84px 0}
.row{display:flex;gap:22px;margin-bottom:70px}.row.low{margin-top:90px}
.t{flex:1;height:220px;background:#5B8CFF;border-radius:6px;transform:translateY(-900px);
animation:drop .6s cubic-bezier(.3,1.3,.4,1) forwards}
.t1{animation-delay:.15s;background:#3F6FE0}.t2{animation-delay:.3s}.t3{animation-delay:.45s;background:#3F6FE0}
@keyframes drop{to{transform:none}}
h1{font-size:118px;line-height:1;color:#EEF2FA}
.big{font:700 84px/1.12 "Cabinet";letter-spacing:-.02em}
.cover{width:420px;height:420px;margin-top:80px;border-radius:8px;display:block;opacity:0;
animation:land .6s ease-out .9s forwards}
""", _shopfront)


def _notice(kind, text, title, cover, v):
    if kind == "title":
        return (f'<h1>{words(title, .3)}</h1>'
                f'<div class="stamp">Keep it by the phone</div>')
    if kind == "cta":
        return f'<p class="big type2">{html.escape(text)}</p><img class="cover" src="{cover}" alt="">'
    return f'<p class="big">{words(text, .3)}</p><div class="line"></div>'


NOTICE = Edition("Fridge notice", """
body{background:#0F1420;color:#EEF2FA}
.tag{background:#5B8CFF;color:#EEF2FA}
.foot{color:#8E99AE}
.s{position:absolute;inset:0;padding:480px 84px 0}
h1{font-size:120px;line-height:1;color:#EEF2FA}
.type{overflow:hidden;white-space:normal;clip-path:inset(0 100% 0 0);animation:type 1.2s steps(24) .2s forwards}
.type2{clip-path:inset(0 100% 0 0);animation:type 1.2s steps(24) .2s forwards}
@keyframes type{to{clip-path:inset(0 -2% 0 0)}}
.stamp{display:inline-block;margin-top:70px;padding:22px 34px;border:8px solid #5B8CFF;color:#5B8CFF;
font:800 56px/1 "Cabinet";transform:rotate(-6deg) scale(2.2);opacity:0;
animation:stamp .35s cubic-bezier(.2,1.4,.3,1) 1.7s forwards}
@keyframes stamp{to{opacity:1;transform:rotate(-6deg)}}
.big{font:700 86px/1.12 "Cabinet";letter-spacing:-.02em}
.line{height:10px;width:0;margin-top:50px;background:#5B8CFF;border-radius:6px;
animation:line .7s ease-out .6s forwards}
@keyframes line{to{width:70%}}
.cover{width:400px;height:400px;margin-top:80px;border-radius:8px;display:block;opacity:0;
transform:rotate(3deg);animation:land .6s ease-out 1.4s forwards}
""", _notice)


def _lanes(kind, text, title, cover, v):
    bands = '<div class="bands"><i class="b b0"></i><i class="b b1"></i><i class="b b2"></i></div>'
    if kind == "title":
        return f'{bands}<img class="cover" src="{cover}" alt=""><h1>{words(title, 1.0)}</h1>'
    if kind == "cta":
        return f'{bands}<p class="big">{words(text, .5)}</p>'
    return f'{bands}<p class="big">{words(text, .4)}</p>'


LANES = Edition("Three lanes", """
body{background:#0A0D14;color:#EEF2FA}
.tag{background:#EEF2FA;color:#0A0D14}
.foot{color:#8E99AE}
.s{position:absolute;inset:0;padding:300px 84px 0}
.bands{position:absolute;left:0;right:0;bottom:0;height:44%;display:flex;gap:18px;padding:0 60px;z-index:0}
.b{flex:1;border-radius:14px 14px 0 0;transform:translateY(100%);animation:rise 1s cubic-bezier(.2,.8,.2,1) forwards;opacity:.95}
.b0{background:#5B8CFF}.b1{background:#5B8CFF;animation-delay:.18s}.b2{background:#5B8CFF;animation-delay:.36s}
@keyframes rise{to{transform:none}}
.cover{position:relative;z-index:1;width:480px;height:480px;border-radius:16px;display:block;margin-bottom:60px;
box-shadow:0 30px 80px -30px #000}
h1{position:relative;z-index:1;font-size:118px;line-height:1}
.big{position:relative;z-index:1;font:800 96px/1.08 "Cabinet";letter-spacing:-.025em;max-width:900px}
""", _lanes)

EDITIONS: dict[str, Edition] = {
    "safe-ai-operator": GUARDRAIL,
    "30-business-lessons": LEDGER,
    "claude-obsidian-vault-kit": GRAPH,
    "ai-for-every-age": LARGEPRINT,
    "local-business-automation-pack": SHOPFRONT,
    "family-ai-scam-defence-workbook": NOTICE,
    "sell-with-ai": LANES,
}
ORDER = tuple(EDITIONS.values())


def edition_for(slug: str) -> Edition:
    """The product's own edition; a product the table does not name takes
    one by its slug's hash, so it is still consistent day to day."""
    return EDITIONS.get(slug) or ORDER[sum(map(ord, slug)) % len(ORDER)]


def page(ed: Edition, kind: str, text: str, title: str, cover: str, variant: int,
         store: str = "") -> str:
    """One beat's document. A "hook" beat is the shared hook frame in the
    edition's colours. The "title" beat (the product, last) always shows the
    cover and the store."""
    ink, accent, on_accent = HOOK_COLORS.get(ed.name, ("#EEF2FA", "#5B8CFF", "#0A0D14"))
    extra = ""
    if kind == "hook":
        inner = hook_html(ed.name, text)
    else:
        inner = ed.layout(kind, text, title, cover, variant)
    if kind == "title":
        if cover and cover not in inner:
            inner += f'<img class="endcover" src="{cover}" alt="">'
        extra = f'<div class="store">{html.escape(store or store_origin.host())}</div>'
    style = f"--hi:{ink};--ha:{accent};--hb:{on_accent}"
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{fonts_css()}{BASE}{ed.css}'
            f'</style></head><body style="{style}"><div class="tag">{TAG}</div>'
            f'<div class="s">{inner}</div>{extra}'
            f'<div class="foot">{html.escape(title)} · ATLAS Academy</div></body></html>')


# ------------------------------------------------------------------ rendering

def available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


async def render_frames(pages: list[str], seconds: list[float], out_dir: str) -> int:
    """Screenshot every frame of every beat into `out_dir/NNNNN.png`;
    returns the frame count. Animations are paused and stepped, so a slow
    box renders the same video as a fast one."""
    from playwright.async_api import async_playwright
    os.makedirs(out_dir, exist_ok=True)
    n = 0
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-features=SharedWorker"])
        try:
            pg = await browser.new_page(viewport={"width": W, "height": H})
            for doc, secs in zip(pages, seconds, strict=True):
                await pg.set_content(doc)
                await pg.evaluate("document.fonts.ready.then(() => null)")
                await pg.evaluate("document.getAnimations().forEach(a => a.pause())")
                for f in range(max(1, round(secs * FPS))):
                    t = f * 1000 / FPS
                    await pg.evaluate(
                        "t => document.getAnimations().forEach(a => { a.currentTime = t; })", t)
                    await pg.screenshot(path=os.path.join(out_dir, f"{n:05d}.png"))
                    n += 1
        finally:
            await browser.close()
    return n


def assemble_argv(frames_dir: str, audio_list: str, out_path: str) -> list[str]:
    """ffmpeg: the frame sequence plus the concatenated per-beat audio."""
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-framerate", str(FPS), "-i", os.path.join(frames_dir, "%05d.png"),
            "-f", "concat", "-safe", "0", "-i", audio_list,
            "-map", "0:v", "-map", "1:a", "-shortest",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out_path]


def pad_argv(mp3: str, seconds: float, wav: str) -> list[str]:
    """One beat's narration padded to exactly `seconds` of 44.1 kHz stereo."""
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", mp3,
            "-af", f"apad,atrim=0:{seconds:.3f}", "-ar", "44100", "-ac", "2", wav]


async def to_thread(fn, *a):
    return await asyncio.to_thread(fn, *a)


# ------------------------------------------------------------- lesson shorts
#
# Miro, 2026-10-11: Shorts about each lesson, "entertaining and animated".
# A lesson Short is captions timed to the lesson's own voice, word by word:
# the words of the line on screen arrive in chunks a reader takes in at a
# glance, and the word being said lights up as it is said. Six themes, one
# per lesson by its key and the day, so consecutive Shorts never look alike;
# each theme has its own palette, its own drifting background and its own
# way a chunk arrives.

@dataclass(frozen=True)
class Theme:
    name: str
    bg: str          # black: hook and close frames
    surface: str     # lifted black: the lesson's line beats
    fg: str          # off-white text
    accent: str      # royal blue: the word being said, the bar, the rule


# Miro, 2026-10-12: one brand on every video, and since the store moved to
# it the same day, two colours only: black and royal blue. Text is a cool
# off-white; blue fills the word being said, the progress bar and the rule,
# and is text only for the store address (about 5.6:1 on black).
BRAND = Theme("ATLAS Academy", bg="#0A0D14", surface="#0F1420", fg="#EEF2FA", accent="#5B8CFF")
THEMES: tuple[Theme, ...] = (BRAND,)
CHUNK = 8                    # at most this many words on screen at once
CHUNK_MIN_AT_COMMA = 3
# A chunk never ends on one of these: "...clipping program, with" read as
# a fragment on the first previews (2026-10-11).
WEAK_END = frozenset({"with", "of", "the", "a", "an", "to", "and", "or", "in", "on", "for",
                      "at", "by", "from", "your", "my", "their", "its", "is", "are", "was",
                      "be", "that", "which", "who", "as", "into", "than", "not", "no"})
# Safe zones on a 1080x1920 Short: TikTok's and YouTube's action rail on
# the right, the caption and buttons at the bottom, the tabs at the top.
SAFE_LEFT, SAFE_RIGHT, SAFE_TOP, SAFE_BOTTOM = 84, 180, 220, 360


def theme_for(key: str, day: int) -> Theme:
    """One brand for every Short (the key and day no longer choose a look)."""
    return BRAND


def _lesson_css(t: Theme) -> str:
    width = W - SAFE_LEFT - SAFE_RIGHT
    return f"""
body{{color:{t.fg};background-color:{t.surface};
background-image:linear-gradient(rgba(143,176,255,.05) 2px,transparent 2px),
linear-gradient(90deg,rgba(143,176,255,.05) 2px,transparent 2px);background-size:120px 120px}}
body.dark{{background-color:{t.bg};
background-image:radial-gradient(900px 700px at 85% 8%,rgba(91,140,255,.28),transparent 70%),
linear-gradient(rgba(143,176,255,.04) 2px,transparent 2px),
linear-gradient(90deg,rgba(143,176,255,.04) 2px,transparent 2px);background-size:auto,120px 120px,120px 120px}}
.tag{{background:{t.accent};color:{t.bg}}}
.chip{{position:absolute;top:{SAFE_TOP}px;left:{SAFE_LEFT}px;width:{width}px;z-index:2;
font:700 34px/1.3 "Atk";opacity:.85}}
.chip b{{font-weight:800}}
.label{{position:absolute;left:{SAFE_LEFT}px;width:{width}px;top:{SAFE_TOP + 150}px;z-index:2;
font:700 42px/1.2 "Atk";padding-left:24px;border-left:10px solid {t.accent};opacity:0;
animation:enter .4s cubic-bezier(.2,.8,.2,1) .05s forwards}}
.stage{{position:absolute;left:{SAFE_LEFT}px;width:{width}px;top:{SAFE_TOP + 330}px;
bottom:{SAFE_BOTTOM + 80}px;z-index:2}}
.chunk{{position:absolute;left:0;right:0;top:0;opacity:0;font:800 92px/1.12 "Cabinet";
letter-spacing:-.02em}}
.hook .chunk{{font-size:112px;line-height:1.04}}
@keyframes enter{{from{{opacity:0;transform:translateY(36px)}}to{{opacity:1;transform:none}}}}
@keyframes leave{{to{{opacity:0;transform:translateY(-30px)}}}}
body{{--lit:{t.fg};--line:{t.accent}}}
body.dark{{--lit:{t.accent};--line:transparent}}
.wd{{opacity:.38;text-decoration:underline;text-decoration-color:transparent;
text-decoration-thickness:8px;text-underline-offset:12px;text-decoration-skip-ink:none}}
@keyframes lit{{to{{opacity:1;color:var(--lit);text-decoration-color:var(--line)}}}}
.bar{{position:absolute;left:{SAFE_LEFT}px;width:{width}px;bottom:{SAFE_BOTTOM}px;height:12px;
border-radius:6px;background:rgba(143,176,255,.16);z-index:2;overflow:hidden}}
.bar i{{display:block;height:100%;width:0;background:{t.accent};animation:fill linear forwards}}
@keyframes fill{{to{{width:100%}}}}
.foot{{left:{SAFE_LEFT}px;right:{SAFE_RIGHT}px;bottom:{SAFE_BOTTOM - 70}px;opacity:.7}}
.close{{position:absolute;left:{SAFE_LEFT}px;width:{width}px;top:{SAFE_TOP + 300}px;z-index:2;
font:700 60px/1.18 "Cabinet"}}
.url{{position:absolute;left:{SAFE_LEFT}px;width:{width}px;top:{SAFE_TOP + 700}px;z-index:2;
font:800 92px/1 "Cabinet";letter-spacing:-.03em;color:{t.accent};
clip-path:inset(0 100% 0 0);animation:type 1.1s steps(16) .5s forwards}}
@keyframes type{{to{{clip-path:inset(0 -2% 0 0)}}}}
.cover{{position:absolute;left:{SAFE_LEFT}px;top:{SAFE_TOP + 880}px;width:300px;height:300px;
border-radius:16px;z-index:2;opacity:0;animation:enter .6s cubic-bezier(.2,.8,.2,1) 1s forwards;
box-shadow:0 0 0 4px {t.accent}}}
"""


def _bare(word: str) -> str:
    return word.strip("\"'“”‘’()[],.;:!?").lower()


PHRASE_START = frozenset({"or", "and", "but", "in", "on", "with", "to", "for", "that", "which",
                          "where", "when", "because", "so", "if", "while", "without", "from"})


def _chunks(words: list[str]) -> list[list[str]]:
    """Caption chunks at clause boundaries: a sentence end, a colon or a
    semicolon always; a comma once the chunk has three words; past five
    words, before a word that starts a new phrase ("or in a clipping
    community"); never more than CHUNK words, and never ending on a word
    that needs the next one."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = _bare(words[i + 1]) if i + 1 < len(words) else ""
        hard = w.endswith((".", "!", "?", ":", ";", '."', '?"', '!"'))
        soft = w.endswith(",") and len(cur) >= CHUNK_MIN_AT_COMMA
        phrase = len(cur) >= 5 and nxt in PHRASE_START and _bare(w) not in WEAK_END
        full = len(cur) >= CHUNK and _bare(w) not in WEAK_END
        if hard or soft or phrase or full:
            out.append(cur)
            cur = []
    if cur:
        if out and len(cur) <= 2 and len(out[-1]) + len(cur) <= CHUNK + 2:
            out[-1] += cur            # a one- or two-word tail joins the line before it
        else:
            out.append(cur)
    return out


def lesson_page(t: Theme, kind: str, text: str, *, lesson: str, title: str, course: str,
                cover: str, seconds: float, label: str = "", panel: str = "",
                chip: str = "") -> str:
    """One beat of a lesson Short, every motion timed inside `seconds`.

    `hook` opens the Short on black (the lesson's point, said first); `line`
    beats are on lifted black with the lesson's own heading above them;
    `cta` closes on black with the store. The word being said lights blue.
    Motion is only arrival and the spoken word: nothing drifts."""
    dark = kind in ("hook", "cta")
    chip = chip or f"<b>Lesson {html.escape(lesson)}</b> · {html.escape(title)}"
    body = f'<div class="chip">{chip}</div>'
    if label and kind == "line":
        body += f'<div class="label">{html.escape(label)}</div>'
    if kind == "cta":
        body += (f'<div class="close">{words(text, .1, .06)}</div>'
                 f'<div class="url">{html.escape(store_origin.host())}</div>')
        if cover:
            body += f'<img class="cover" src="{cover}" alt="">'
    else:
        ws = text.split()
        per = max(.12, (seconds - .3) / max(1, len(ws)))
        t0, parts = .05, []
        for chunk in _chunks(ws):
            start, end = t0, t0 + per * len(chunk)
            spans = " ".join(
                f'<span class="wd" style="animation:lit .1s linear {start + j * per:.2f}s forwards">'
                f'{html.escape(w)}</span>' for j, w in enumerate(chunk))
            leave = f", leave .25s ease-in {end - .2:.2f}s forwards" if end < seconds - .4 else ""
            parts.append(f'<div class="chunk" style="animation:enter .3s cubic-bezier(.2,.8,.2,1) '
                         f'{start:.2f}s forwards{leave}">{spans}</div>')
            t0 = end
        stage = "stage hook" if kind == "hook" else "stage"
        body += f'<div class="{stage}">{"".join(parts)}</div>'
    body += panel
    body += f'<div class="bar"><i style="animation-duration:{seconds:.2f}s"></i></div>'
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{fonts_css()}{BASE}'
            f'{_lesson_css(t)}</style></head><body class="{"dark" if dark else ""}">'
            f'<div class="tag">{TAG}</div>{body}'
            f'<div class="foot">{html.escape(course)} · ATLAS Academy</div></body></html>')


# ------------------------------------------------------------ concept Shorts
#
# The growth plan's "motion ideas that explain" (2026-10-12): a request
# that lands in a draft tray and stops, an action that pauses at a gate, a
# kit that opens into its real files, scattered steps that become a
# checklist. Each is labelled "Concept animation": it explains a state, it
# never pretends to be a product screen, and a draft-only flow is never
# shown sending.

CONCEPT_TAG = "Concept animation"


def concept_panel(t: Theme, motion: str, *, step: int, files: list[str] | None = None) -> str:
    """The animated diagram under the captions for one beat. `step` is the
    beat's place in the Short, so the diagram advances with the story."""
    files = files or []
    css = f"""
<style>
.panel{{position:absolute;left:{SAFE_LEFT}px;width:{W - SAFE_LEFT - SAFE_RIGHT}px;top:1020px;height:470px;z-index:2}}
.ptag{{position:absolute;right:0;top:-46px;font:700 26px/1 "Atk";opacity:.6}}
.card{{position:absolute;width:300px;height:170px;border-radius:18px;background:#1B2334;color:{t.fg};border:2px solid rgba(143,176,255,.3);
padding:22px;font:700 30px/1.2 "Atk";box-shadow:0 18px 40px -20px rgba(0,0,0,.5)}}
.card small{{display:block;font:500 24px/1.3 "Atk";opacity:.8;margin-top:10px}}
.tray{{position:absolute;right:0;top:60px;width:360px;height:300px;border:5px dashed {t.accent};border-radius:22px}}
.tray b{{position:absolute;left:20px;bottom:-56px;font:800 34px/1 "Cabinet"}}
.stamp{{position:absolute;right:30px;top:20px;padding:12px 20px;border:5px solid {t.accent};border-radius:12px;
font:800 34px/1 "Cabinet";color:{t.accent};background:{t.bg};opacity:0;transform:scale(1.8) rotate(-6deg);
animation:stamp .35s cubic-bezier(.2,1.4,.3,1) 1.6s forwards}}
@keyframes stamp{{to{{opacity:1;transform:rotate(-6deg)}}}}
@keyframes slide{{to{{transform:translateX(var(--to))}}}}
.gate{{position:absolute;left:470px;top:20px;width:16px;height:360px;background:{t.accent};border-radius:8px;box-shadow:0 0 40px rgba(91,140,255,.6)}}
.gate span{{position:absolute;left:34px;top:120px;white-space:nowrap;font:800 40px/1.1 "Cabinet"}}
.file{{height:58px;margin:0 0 14px;padding:0 22px;border-radius:12px;display:flex;align-items:center;
font:700 30px/1 "Atk";background:#1B2334;color:{t.fg};opacity:0;transform:translateY(24px);
animation:enter .35s cubic-bezier(.2,.8,.2,1) forwards}}
.file i{{display:inline-block;width:18px;height:18px;border-radius:4px;background:{t.accent};margin-right:18px}}
</style>"""
    tag = f'<div class="ptag">{CONCEPT_TAG}</div>'
    if motion == "draft":
        moved = step >= 1
        card = ('<div class="card" style="left:0;top:110px;--to:'
                + ("420px;animation:slide 1s cubic-bezier(.2,.8,.2,1) .3s forwards" if moved else "0")
                + '">New inquiry<small>from your contact form (demo data)</small></div>')
        tray = '<div class="tray"><b>Drafts</b></div>'
        stamp = '<div class="stamp">sent: false</div>' if step >= 2 else ""
        return css + f'<div class="panel">{tag}{tray}{card}{stamp}</div>'
    if motion == "gate":
        card = ('<div class="card" style="left:0;top:100px;--to:150px;animation:slide .9s '
                'cubic-bezier(.2,.8,.2,1) .3s forwards">An action<small>not cleared to run alone</small></div>')
        return css + (f'<div class="panel">{tag}<div class="gate"><span>Waits for you</span></div>'
                      f'{card}</div>')
    if motion in ("files", "checklist"):
        shown = files[:6] if step >= 1 else []
        rows = "".join(f'<div class="file" style="animation-delay:{.3 + i * .25:.2f}s"><i></i>'
                       f'{html.escape(f)}</div>' for i, f in enumerate(shown))
        return css + f'<div class="panel">{tag}{rows}</div>'
    return ""
