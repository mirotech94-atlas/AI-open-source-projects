"""Draw the ATLAS Academy product covers (assets/covers/*.jpg) from one place.

    .venv/bin/python tools/academy_covers.py [--only slug,slug] [--out DIR]

Miro, 2026-10-12: black + royal blue for everything, the covers included.
Until then each cover was a one-off export from its product's build in the
vault, in that product's own colours; this script redraws all seven on the
store's brand tokens (below) and the store's own faces (Cabinet for
titles, Atkinson Hyperlegible Next for everything read), keeping every
cover's words and its picture: the arcs, the shield, the flow, the chips.

Each cover is HTML screenshotted in headless Chromium at twice its size and
scaled down by ffmpeg, at the exact pixel size of the file it replaces. The words are
the product's; a format tag must say what the catalog sells (catalog.json
`label`), so when a format changes, change it here and run this again.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from atlas.modules.academy.reel_design import fonts_css

# The store's palette (Miro, 2026-10-12: black + royal blue). The covers are
# the store's, so they keep it; the videos moved to the navy system on
# 2026-10-17 (video_tokens.py) and no longer share these values.
BLACK = "#0A0D14"
SURFACE = "#0F1420"
SHEET_BG = "#131A28"
LIFT = "#1B2334"
BLUE = "#5B8CFF"
BLUE_L = "#8FB0FF"
TEXT = "#EEF2FA"
TEXT2 = "#C9D2E3"
MUTED = "#8E99AE"
ON_BLUE = "#070A12"


def rgba(colour: str, alpha: float) -> str:
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r},{g},{b},{alpha:g})"


OUT = ROOT / "atlas" / "modules" / "academy" / "assets" / "covers"
SCALE = 2

BASE = f"""
*{{box-sizing:border-box;margin:0;padding:0}}
body{{position:relative;overflow:hidden;font-family:"Atk",system-ui,sans-serif;
-webkit-font-smoothing:antialiased;background:{BLACK};color:{TEXT}}}
.grid{{position:absolute;inset:0;background-image:linear-gradient({rgba(TEXT, .045)} 1px,
transparent 1px),linear-gradient(90deg,{rgba(TEXT, .045)} 1px,transparent 1px);
background-size:40px 40px}}
.glow{{position:absolute;inset:0;background:radial-gradient(circle at 82% 8%,{rgba(BLUE, .26)} 0,
{rgba(BLUE, .08)} 30%,{rgba(BLACK, 0)} 60%)}}
.eyebrow{{font:800 17px/1 "Atk";letter-spacing:.16em;text-transform:uppercase;color:{BLUE_L}}}
h1{{font:800 88px/1 "Cabinet";letter-spacing:-.025em;color:{TEXT}}}
.promise{{font:500 25px/1.38 "Atk";color:{TEXT2}}}
.tag{{display:inline-block;font:800 17px/1 "Atk";letter-spacing:.03em;padding:11px 16px;
background:{BLUE};color:{ON_BLUE};border-radius:4px}}
.rings{{position:absolute;border-radius:50%}}
.rings::after{{content:"";position:absolute;inset:44%;border-radius:50%}}
.mono{{font-family:"SF Mono",Menlo,"DejaVu Sans Mono",monospace}}
"""


def _rings(colour: str, line: int, gap: int) -> str:
    return (f"background:repeating-radial-gradient(circle,{colour} 0 {line}px,"
            f"transparent {line}px {gap}px)")


# slug: (width, height, extra css, body). The words are each cover's own.
COVERS: dict[str, tuple[int, int, str, str]] = {
    "everyday-ai-for-adults": (800, 800, f"""
body{{background:{BLUE};color:{ON_BLUE}}}
.rings{{width:1000px;height:1000px;right:-480px;bottom:-506px;{_rings(ON_BLUE, 3, 35)}}}
.rings::after{{background:{ON_BLUE}}}
.wrap{{position:absolute;left:60px;top:76px;width:620px}}
.eyebrow{{color:{ON_BLUE}}}
h1{{color:{ON_BLUE};margin:30px 0 26px}}
.promise{{color:{ON_BLUE};width:470px}}
.tag{{position:absolute;left:60px;bottom:64px;background:{BLACK};color:{TEXT}}}
""", """<div class="rings"></div><div class="wrap"><div class="eyebrow">AI for Every Age</div>
<h1>Everyday AI<br>for Adults</h1>
<div class="promise">Question AI, use Claude safely for everyday tasks, and build your first
automation.</div></div><div class="tag">60 narrated video lessons</div>"""),

    "safe-and-simple-ai-60plus": (800, 800, f"""
.band{{position:absolute;left:0;right:0;top:0;height:38px;background:{BLUE}}}
.rings{{width:1000px;height:1000px;right:-546px;bottom:-574px;{_rings(BLUE, 6, 40)}}}
.rings::after{{background:{BLUE}}}
.wrap{{position:absolute;left:60px;top:100px;width:660px}}
h1{{font-size:100px;margin:26px 0 30px}}
.promise{{font-size:27px;width:430px;color:{TEXT}}}
.tag{{position:absolute;left:60px;bottom:64px}}
""", """<div class="band"></div><div class="rings"></div><div class="wrap">
<div class="eyebrow">AI for Every Age · Large print</div><h1>Safe and<br>Simple AI<br>60+</h1>
<div class="promise">Stay safe from scams and use Claude on your phone. One step at a time.</div>
</div><div class="tag">17 large-print video lessons</div>"""),

    "local-business-automation-pack": (800, 800, f"""
.rings{{width:1000px;height:1000px;right:-506px;bottom:-534px;{_rings(rgba(BLUE, .8), 3, 35)}}}
.rings::after{{background:{BLUE}}}
.wrap{{position:absolute;left:60px;top:76px;width:700px}}
h1{{font-size:80px;margin:26px 0 26px}}
.promise{{width:440px}}
.flow{{position:absolute;left:60px;bottom:64px;display:flex;align-items:center;gap:14px}}
.chip{{font:700 17px/1 "SF Mono",Menlo,monospace;padding:16px 18px;background:{SURFACE};
color:{TEXT};border:2px solid {BLUE_L}}}
.chip.out{{background:{BLUE};color:{ON_BLUE};border-color:{BLUE}}}
.arrow{{color:{BLUE};font:700 22px/1 "Atk"}}
""", """<div class="grid"></div><div class="rings"></div><div class="wrap">
<div class="eyebrow">ATLAS Academy · n8n workflow pack</div>
<h1>Local Business<br>Automation Pack</h1>
<div class="promise">Four n8n automations that draft, never send. Site health, lead replies,
missed calls, review requests.</div></div>
<div class="flow"><span class="chip">Webhook</span><span class="arrow">→</span>
<span class="chip">Validate</span><span class="arrow">→</span>
<span class="chip out">Draft, sent: false</span></div>"""),

    "family-ai-scam-defence-workbook": (800, 800, f"""
.rings{{width:1000px;height:1000px;right:-506px;bottom:-534px;{_rings(rgba(BLUE, .8), 3, 35)}}}
.rings::after{{background:{BLUE}}}
.wrap{{position:absolute;left:60px;top:76px;width:700px}}
h1{{font-size:78px;margin:26px 0 26px}}
.promise{{width:540px;color:{TEXT}}}
.flow{{position:absolute;left:60px;bottom:64px;display:flex;align-items:center;gap:14px}}
.chip{{font:700 18px/1 "Atk";padding:16px 18px;background:{SURFACE};
color:{TEXT};border:2px solid {BLUE_L}}}
.chip.out{{background:{BLUE};color:{ON_BLUE};border-color:{BLUE}}}
""", """<div class="grid"></div><div class="rings"></div><div class="wrap">
<div class="eyebrow">ATLAS Academy · Printable workbook</div>
<h1>The Family<br>AI-Scam Defense<br>Workbook</h1>
<div class="promise">A household plan you make in one sitting.<br>A plan before the phone rings.
</div></div><div class="flow"><span class="chip">Safe word</span>
<span class="chip">Callback rule</span><span class="chip out">By the phone</span></div>"""),

    "safe-ai-operator": (800, 800, f"""
.brand{{position:absolute;left:60px;top:62px;display:flex;align-items:center;gap:14px;
font:700 17px/1 "Atk";letter-spacing:.06em;color:{TEXT2}}}
.brand b{{display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;
border-radius:6px;background:{BLUE};color:{ON_BLUE};font:800 14px/1 "Cabinet";letter-spacing:0}}
.shield{{position:absolute;right:60px;top:56px;width:120px;height:144px}}
h1{{position:absolute;left:60px;top:170px;font-size:72px;line-height:1.02}}
h1 em{{font-style:normal;color:{BLUE}}}
.promise{{position:absolute;left:60px;top:430px;width:620px;font-weight:700;font-size:22px}}
.chips{{position:absolute;left:60px;top:500px;display:flex;gap:12px}}
.chips span{{font:700 17px/1 "Atk";padding:13px 18px;border-radius:999px;background:{LIFT};
color:{TEXT};border:1px solid {rgba(BLUE_L, .35)}}}
.by{{position:absolute;left:60px;bottom:58px;font:500 16px/1.3 "Atk";color:{MUTED}}}
""", f"""<div class="grid"></div><div class="glow"></div>
<div class="brand"><b>AA</b>ATLAS ACADEMY</div>
<svg class="shield" viewBox="0 0 120 144"><path d="M60 6 L112 26 V70 C112 104 88 126 60 138
C32 126 8 104 8 70 V26 Z" fill="none" stroke="{BLUE}" stroke-width="7" stroke-linejoin="round"/>
<path d="M38 72 L54 88 L84 56" fill="none" stroke="{BLUE}" stroke-width="8" stroke-linecap="round"
stroke-linejoin="round"/></svg>
<h1>The AI that<br>can't <em>burn</em><br>your business.</h1>
<div class="promise">Build a Safe AI Operator: give an agent real work, keep every key, every
dollar and the final say.</div>
<div class="chips"><span>28 video lessons + bonus</span><span>93-page guide</span>
<span>Python starter agent</span></div>
<div class="by">by Miro · built from the rules his own agent, ATLAS, runs on</div>"""),

    "sell-with-ai": (800, 800, f"""
.brand{{position:absolute;left:64px;top:66px;font:800 22px/1 "Atk";letter-spacing:.02em;
color:{BLUE}}}
.brand span{{font-weight:500;color:{TEXT2};margin-left:6px}}
h1{{position:absolute;left:62px;top:262px;font-size:112px}}
.promise{{position:absolute;left:64px;top:408px;width:600px;font-size:26px;color:{TEXT}}}
.chips{{position:absolute;left:64px;bottom:66px;display:flex;gap:14px}}
.chips span{{font:800 19px/1 "Atk";padding:13px 16px;border-radius:999px;background:{BLUE};
color:{ON_BLUE}}}
""", """<div class="grid"></div><div class="glow"></div>
<div class="brand">ATLAS<span>Academy</span></div><h1>Sell With AI</h1>
<div class="promise">Clips, digital products and an online store: three ways one person sells
with AI doing the repetitive work.</div>
<div class="chips"><span>27 lessons</span><span>Video or ebook</span></div>"""),

    "claude-obsidian-vault-kit": (800, 800, f"""
.rings{{width:1000px;height:1000px;right:-560px;bottom:-590px;{_rings(rgba(BLUE, .8), 3, 35)}}}
.rings::after{{background:{BLUE}}}
.wrap{{position:absolute;left:60px;top:76px;width:700px}}
h1{{font-size:80px;margin:26px 0 26px}}
h1 em{{font-style:normal;color:{BLUE}}}
.promise{{width:520px}}
.tree{{position:absolute;left:60px;top:508px;font:500 22px/1.7 "SF Mono",Menlo,monospace;
color:{TEXT2}}}
.tree b{{font-weight:700;color:{BLUE_L}}}
.tree i{{font-style:normal;color:{MUTED}}}
.tag{{position:absolute;left:60px;bottom:64px}}
""", """<div class="grid"></div><div class="rings"></div><div class="wrap">
<div class="eyebrow">ATLAS Academy · Starter vault</div>
<h1>Claude +<br>Obsidian<br><em>Vault Kit</em></h1>
<div class="promise">A starter vault for running a small business with Claude.</div></div>
<div class="tree"><div><b>Home.md</b> <i>start here</i></div><div><b>CLAUDE.md</b> <i>house rules</i></div>
<div><b>Weekly Review.md</b></div><div><b>Prompts/</b> <i>16 prompts</i></div></div>
<div class="tag">Zip of plain Markdown notes</div>"""),

    "30-business-lessons": (706, 1000, f"""
body{{background:{SHEET_BG}}}
.eyebrow{{position:absolute;left:76px;top:126px;font-size:14px;letter-spacing:.2em;color:{BLUE_L}}}
h1{{position:absolute;left:76px;top:190px;font-size:60px;line-height:1.04}}
.promise{{position:absolute;left:76px;top:342px;width:480px;font-size:20px;line-height:1.5}}
.rule{{position:absolute;left:76px;top:446px;width:86px;height:3px;background:{BLUE}}}
.author{{position:absolute;left:76px;top:856px;font:500 17px/1 "Atk";color:{TEXT2}}}
.note{{position:absolute;left:76px;top:898px;width:560px;font:500 12px/1.4 "Atk";color:{MUTED}}}
""", """<div class="grid"></div><div class="eyebrow">A practical guide</div>
<h1>30 Business<br>Lessons</h1>
<div class="promise">Starting a small AI-assisted service business, one honest step at a time</div>
<div class="rule"></div><div class="author">Miro (Myron Mekhael)</div>
<div class="note">Educational content, not an income guarantee. Examples and figures are labeled
hypotheticals.</div>"""),
}


def page(slug: str) -> str:
    w, h, css, body = COVERS[slug]
    return (f'<!doctype html><html><head><meta charset="utf-8"><style>{fonts_css()}{BASE}'
            f"html,body{{width:{w}px;height:{h}px}}{css}</style></head><body>{body}</body></html>")


async def render(slugs: list[str], out: Path) -> None:
    from playwright.async_api import async_playwright
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for slug in slugs:
            w, h, _css, _body = COVERS[slug]
            pg = await b.new_page(viewport={"width": w, "height": h}, device_scale_factor=SCALE)
            await pg.set_content(page(slug))
            await pg.evaluate("document.fonts.ready.then(() => null)")
            png = out / f".{slug}@{SCALE}x.png"
            await pg.screenshot(path=str(png), type="png")
            await pg.close()
            # ffmpeg scales it down (no image library in the tools: test_selfmodel)
            dest = out / f"{slug}.jpg"
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y", "-loglevel", "error", "-i", str(png), "-vf",
                f"scale={w}:{h}:flags=lanczos", "-pix_fmt", "yuvj444p", "-q:v", "2", str(dest))
            if await proc.wait() != 0:
                raise SystemExit(f"ffmpeg failed for {slug}")
            png.unlink()
            print(f"{dest}  {w}x{h}  {dest.stat().st_size / 1e3:.0f} kB")
        await b.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    only = [s for s in args.only.split(",") if s]
    unknown = set(only) - set(COVERS)
    if unknown:
        raise SystemExit(f"no cover for {sorted(unknown)}")
    asyncio.run(render(only or list(COVERS), Path(args.out)))


if __name__ == "__main__":
    main()
