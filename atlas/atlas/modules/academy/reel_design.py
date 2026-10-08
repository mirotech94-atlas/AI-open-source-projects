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
"""


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
body{background:#06111a;color:#e8f1f7}
.tag{background:#2fd0ff;color:#06111a}
.foot{color:#7f96a8}
.s{position:absolute;inset:0;padding:320px 84px 0}
.cover{width:560px;height:560px;border-radius:18px;display:block;margin:0 0 70px;
box-shadow:0 30px 80px -30px #000,0 0 0 2px rgba(47,208,255,.3)}
.rail{height:6px;width:0;background:#2fd0ff;margin:0 0 54px;animation:rail .9s cubic-bezier(.2,.8,.2,1) forwards;
box-shadow:0 0 30px rgba(47,208,255,.6)}
.rail.long{animation-delay:.2s}
@keyframes rail{to{width:100%}}
h1{font-size:112px;line-height:1.02;color:#fff}
.big{font:800 86px/1.1 "Cabinet";letter-spacing:-.025em;color:#fff}
.under{margin-top:48px;font:500 36px/1.3 "Atk";color:#2fd0ff}
""", _guardrail)


def _ledger(kind, text, title, cover, v):
    if kind == "title":
        return (f'<div class="count"><span class="n"></span><span class="of">of 30</span></div>'
                f'<h1>{words(title, .8)}</h1>')
    if kind == "cta":
        return f'<p class="big">{words(text, .3)}</p><img class="cover" src="{cover}" alt="">'
    return f'<p class="num"></p><p class="big">{words(text, .4)}</p>'


LEDGER = Edition("Ledger", """
body{background:#E9E4DA;color:#1F1A17;
background-image:repeating-linear-gradient(0deg,transparent 0 88px,rgba(31,26,23,.12) 88px 90px)}
.tag{background:#1F1A17;color:#E9E4DA}
.foot{color:#6b625b}
.s{position:absolute;inset:0;padding:520px 84px 0}
.count{display:flex;align-items:baseline;gap:22px;margin-bottom:60px}
.n{font:800 300px/1 "Cabinet";letter-spacing:-.05em;color:#1F1A17;counter-reset:n 0;animation:tick 2.4s steps(30) forwards}
.n::after{content:counter(n)}
@keyframes tick{to{counter-increment:n 30}}
@property --x{syntax:"<integer>";initial-value:0;inherits:false}
.of{font:500 44px/1 "Atk";color:#6b625b}
h1{font-size:120px;line-height:.98;color:#1F1A17}
.num{height:6px;width:140px;background:#1F1A17;margin-bottom:50px;transform-origin:left;
transform:scaleX(0);animation:draw .6s ease-out forwards}
@keyframes draw{to{transform:none}}
.big{font:700 100px/1.1 "Cabinet";letter-spacing:-.025em;color:#1F1A17}
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
body{background:#140F22;color:#EDE8FF}
.tag{background:#8A5CF6;color:#fff}
.foot{color:#9D8FC7}
.s{position:absolute;inset:0;padding:300px 84px 0}
.g{position:relative;height:720px;margin-bottom:60px}
.g.small{height:300px;opacity:.55}.g.low{margin-top:80px}
.n{position:absolute;width:26px;height:26px;border-radius:50%;background:#8A5CF6;
box-shadow:0 0 24px #8A5CF6;opacity:0;animation:pop .4s ease-out forwards}
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
box-shadow:0 0 0 10px #140F22,0 0 0 14px #8A5CF6}
@keyframes node{to{opacity:1;transform:translate(-50%,-50%)}}
h1{font-size:104px;line-height:1;color:#fff}
.big{font:700 82px/1.12 "Cabinet";letter-spacing:-.02em;color:#fff}
""", _graph)


def _largeprint(kind, text, title, cover, v):
    if kind == "title":
        return f'<h1 class="grow">{html.escape(title)}</h1><img class="cover" src="{cover}" alt="">'
    if kind == "cta":
        return f'<p class="big grow2">{html.escape(text)}</p>'
    return f'<p class="big">{words(text, .3, .12)}</p>'


LARGEPRINT = Edition("Large print", """
body{background:#FAFAF7;color:#10254A}
.tag{background:#10254A;color:#FAFAF7}
.foot{color:#4b5b78}
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
body{background:#1C1B19;color:#F4EFE6}
.tag{background:#F5A524;color:#1C1B19}
.foot{color:#9a9184}
.s{position:absolute;inset:0;padding:420px 84px 0}
.row{display:flex;gap:22px;margin-bottom:70px}.row.low{margin-top:90px}
.t{flex:1;height:220px;background:#F5A524;border-radius:6px;transform:translateY(-900px);
animation:drop .6s cubic-bezier(.3,1.3,.4,1) forwards}
.t1{animation-delay:.15s;background:#E08E1B}.t2{animation-delay:.3s}.t3{animation-delay:.45s;background:#E08E1B}
@keyframes drop{to{transform:none}}
h1{font-size:118px;line-height:1;color:#fff}
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
body{background:#FFF3B0;color:#1b1b1b}
.tag{background:#C8102E;color:#fff}
.foot{color:#6c6446}
.s{position:absolute;inset:0;padding:480px 84px 0}
h1{font-size:120px;line-height:1;color:#1b1b1b}
.type{overflow:hidden;white-space:normal;clip-path:inset(0 100% 0 0);animation:type 1.2s steps(24) .2s forwards}
.type2{clip-path:inset(0 100% 0 0);animation:type 1.2s steps(24) .2s forwards}
@keyframes type{to{clip-path:inset(0 -2% 0 0)}}
.stamp{display:inline-block;margin-top:70px;padding:22px 34px;border:8px solid #C8102E;color:#C8102E;
font:800 56px/1 "Cabinet";transform:rotate(-6deg) scale(2.2);opacity:0;
animation:stamp .35s cubic-bezier(.2,1.4,.3,1) 1.7s forwards}
@keyframes stamp{to{opacity:1;transform:rotate(-6deg)}}
.big{font:700 86px/1.12 "Cabinet";letter-spacing:-.02em}
.line{height:10px;width:0;margin-top:50px;background:#C8102E;border-radius:6px;
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
body{background:#050505;color:#fff}
.tag{background:#fff;color:#050505}
.foot{color:#8c8c8c}
.s{position:absolute;inset:0;padding:300px 84px 0}
.bands{position:absolute;left:0;right:0;bottom:0;height:44%;display:flex;gap:18px;padding:0 60px;z-index:0}
.b{flex:1;border-radius:14px 14px 0 0;transform:translateY(100%);animation:rise 1s cubic-bezier(.2,.8,.2,1) forwards;opacity:.95}
.b0{background:#FF6B57}.b1{background:#2FD0FF;animation-delay:.18s}.b2{background:#8A5CF6;animation-delay:.36s}
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


def page(ed: Edition, kind: str, text: str, title: str, cover: str, variant: int) -> str:
    inner = ed.layout(kind, text, title, cover, variant)
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{fonts_css()}{BASE}{ed.css}'
            f'</style></head><body><div class="tag">{TAG}</div><div class="s">{inner}</div>'
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
