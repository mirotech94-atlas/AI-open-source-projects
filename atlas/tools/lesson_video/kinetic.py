"""A kinetic store reel: motion rendered frame by frame, stitched to the
Fish narration. Miro, 2026-10-05: "make it more designable, make it
interactive" -- for a video that means motion in the frame.

Every slide is an HTML document whose CSS animations run from t = 0. The
renderer pauses every animation, steps `currentTime` to each frame, and
screenshots it, so the output is deterministic and the timing follows the
narration's real length (read from the mp3 that tts.py made). ffmpeg then
joins the frames with the audio.

  python tools/lesson_video/kinetic.py <dir with audio/NNN.mp3> <out.mp4>
"""
from __future__ import annotations

import base64
import html
import json
import subprocess
import sys
from pathlib import Path

W, H, FPS = 1080, 1920, 24
PAD = 0.8
ASSETS = Path(__file__).resolve().parents[2] / "atlas" / "modules" / "academy" / "assets"


def _data(path: Path, mime: str) -> str:
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def _font() -> str:
    f = next(ASSETS.glob("fonts/*.woff"), None)
    return (f'@font-face{{font-family:"Atk";src:url("{_data(f, "font/woff")}") format("woff");'
            f'font-weight:100 900}}' if f else "")


def _cover(name: str) -> str:
    p = ASSETS / "covers" / name
    return _data(p, "image/jpeg") if p.exists() else ""


BASE = """
*{box-sizing:border-box;margin:0;padding:0}
html,body{width:1080px;height:1920px;overflow:hidden;background:#06111a;color:#e8f1f7;
font-family:"Atk","Atkinson Hyperlegible Next",-apple-system,"Helvetica Neue",Arial,sans-serif}
.grid{position:absolute;inset:0;background-image:linear-gradient(rgba(47,208,255,.07) 1px,transparent 1px),
linear-gradient(90deg,rgba(47,208,255,.07) 1px,transparent 1px);background-size:72px 72px;
mask-image:radial-gradient(ellipse at 50% 35%,#000 20%,transparent 78%)}
.glow{position:absolute;inset:0;background:radial-gradient(circle at 82% 10%,rgba(47,208,255,.30),transparent 48%)}
.s{position:relative;height:100%;padding:140px 80px 200px;display:flex;flex-direction:column}
.brand{display:flex;align-items:center;gap:16px;font-size:34px;color:#8fa3b3}
.brand b{color:#2fd0ff;letter-spacing:.1em;font-weight:800}
.cube{width:34px;height:34px;border:2px solid #2fd0ff;transform:rotate(45deg);box-shadow:0 0 24px rgba(47,208,255,.55);
animation:spin 6s linear infinite}
@keyframes spin{to{transform:rotate(405deg)}}
.foot{position:absolute;left:80px;right:80px;bottom:120px;font-size:28px;color:#5f7686;line-height:1.3}
.w{display:inline-block;opacity:0;transform:translateY(40px);animation:land .5s cubic-bezier(.2,.8,.2,1) forwards}
@keyframes land{to{opacity:1;transform:none}}
h1{font-size:92px;line-height:1.06;font-weight:800;letter-spacing:-.03em;color:#fff;margin-top:150px}
h1 .dim{color:#8fa3b3}
.con{margin-top:70px;border-left:3px solid rgba(47,208,255,.5);padding-left:34px;font-size:36px;line-height:1.6;color:#b9c9d6}
.ln{overflow:hidden;white-space:nowrap;width:0;animation:type .9s steps(34) forwards}
.ln.no{color:#6b7f8e;text-decoration:line-through;text-decoration-color:#2fd0ff;text-decoration-thickness:3px}
.ln i{color:#2fd0ff;font-style:normal;margin-right:18px}
@keyframes type{to{width:100%}}
.shelf{perspective:1600px;margin-top:40px;height:720px;display:flex;align-items:center}
.cov{width:620px;height:620px;border-radius:22px;background-size:cover;box-shadow:0 40px 90px -30px #000,0 0 0 2px rgba(47,208,255,.25);
transform:rotateY(42deg) rotateX(8deg) translateX(-140px);opacity:0;animation:tilt 1.1s cubic-bezier(.2,.8,.2,1) forwards,bob 5s ease-in-out 1.1s infinite}
@keyframes tilt{to{opacity:1;transform:rotateY(-10deg) rotateX(4deg) translateX(0)}}
@keyframes bob{50%{transform:rotateY(-6deg) rotateX(2deg) translateY(-14px)}}
.course h1{margin-top:40px;font-size:74px}
.fact{margin-top:28px;font-size:42px;line-height:1.3;color:#b9c9d6}
.fact .acc{color:#2fd0ff;font-weight:800}
.url{margin-top:120px;font-size:84px;font-weight:800;letter-spacing:-.02em;color:#fff;white-space:nowrap}
.url .ty{display:inline-block;white-space:nowrap;clip-path:inset(0 100% 0 0);animation:reveal 1.3s steps(16) .3s forwards;
border-right:6px solid #2fd0ff;padding-right:8px}
@keyframes reveal{to{clip-path:inset(0 -8px 0 0)}}
@keyframes blink{50%{opacity:0}}
.pill{display:inline-block;margin-top:50px;padding:20px 38px;border-radius:999px;background:#2fd0ff;color:#06111a;font-size:44px;
font-weight:800;opacity:0;transform:scale(.6);animation:pop .5s cubic-bezier(.2,1.4,.3,1) 1.9s forwards}
@keyframes pop{to{opacity:1;transform:none}}
.rule{margin-top:90px;font-size:40px;line-height:1.45;color:#b9c9d6}
"""


def words(text: str, start: float, step: float = 0.11, cls: str = "") -> str:
    """Each word lands on its own beat. `<b>...</b>` marks words to accent."""
    out, accent = [], False
    for i, raw in enumerate(text.split()):
        w = raw
        if w.startswith("<b>"):
            accent, w = True, w[3:]
        closing = w.endswith("</b>")
        if closing:
            w = w[:-4]
        extra = " acc" if accent else ""
        out.append(f'<span class="w {cls}{extra}" style="animation-delay:{start + i * step:.2f}s">'
                   f"{html.escape(w)}</span>")
        if closing:
            accent = False
    return " ".join(out)


def hook() -> str:
    log = [("03:12", "restarted the fare poller", ""), ("03:13", "refund $40, asked Miro first", ""),
           ("03:13", "move $400 to a new account: never", "no")]
    lines = "".join(
        f'<div class="ln {no}" style="animation-delay:{2.0 + i * 1.0:.1f}s"><i>{t}</i>{html.escape(x)}</div>'
        for i, (t, x, no) in enumerate(log))
    return f"""<h1>{words("My AI runs my businesses while I sleep.", 0.1)}<br>
{words("It can't touch my money.", 1.0, cls="dim")}</h1>
<div class="con">{lines}</div>
<div class="foot">ATLAS Academy · agency.storefront.test</div>"""


def course(cover: str, title: str, fact: str, delay: float = 0.0) -> str:
    return f"""<div class="shelf"><div class="cov" style="background-image:url('{_cover(cover)}')"></div></div>
<div class="course"><h1>{words(title, 0.6)}</h1>
<p class="fact">{words(fact, 1.3, 0.07)}</p></div>
<div class="foot">ATLAS Academy · agency.storefront.test</div>"""


def end() -> str:
    return f"""<h1>{words("Courses, kits and ebooks.", 0.1)}<br>{words("From $9.", 0.9, cls="dim")}</h1>
<p class="rule">{words("No hype. No fake reviews. No promises I can't keep.", 1.2, 0.07)}</p>
<div class="url"><span class="ty">agency.storefront.test</span></div>
<div><span class="pill">link in bio</span></div>
<div class="foot">Made with AI · ATLAS Academy</div>"""


def page(body: str) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>{_font()}{BASE}</style></head>
<body><div class="grid"></div><div class="glow"></div><div class="s">
<div class="brand"><div class="cube"></div><b>ATLAS</b><span>Academy</span></div>{body}</div></body></html>"""


SLIDES = [
    page(hook()),
    page(course("safe-ai-operator.jpg", "Build a Safe AI Operator",
                "<b>28 video lessons</b> on an agent that does real work for your business, and can't move money or act without your OK.")),
    page(course("sell-with-ai.jpg", "Sell With AI",
                "<b>27 lessons</b>: clipping with permission, one digital product, an honest online store. The three things I actually do.")),
    page(course("everyday-ai-for-adults.jpg", "AI for Every Age",
                "Everyday AI for adults, and a <b>large-print edition</b> for the over sixties. Scam-safe from lesson one.")),
    page(end()),
]


def duration(path: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=False)
    return float(r.stdout.strip() or 0)


def render(src: Path, out: Path) -> None:
    from playwright.sync_api import sync_playwright
    frames = src / "frames"
    frames.mkdir(exist_ok=True)
    for old in frames.glob("*.png"):
        old.unlink()
    n = 0
    alist = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": W, "height": H})
        for i, doc in enumerate(SLIDES, 1):
            mp3 = src / "audio" / f"{i:03d}.mp3"
            secs = duration(mp3) / 0.94 + PAD
            wav = src / f"a{i:03d}.wav"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp3), "-af",
                            f"atempo=0.94,apad,atrim=0:{secs:.3f}", "-ar", "44100", "-ac", "2",
                            str(wav)], check=True)
            alist.append(f"file '{wav.resolve()}'")
            pg.set_content(doc)
            pg.evaluate("document.getAnimations().forEach(a => a.pause())")
            for f in range(int(secs * FPS)):
                t = f * 1000 / FPS
                pg.evaluate(f"document.getAnimations().forEach(a => {{ a.currentTime = {t}; }})")
                pg.screenshot(path=str(frames / f"{n:05d}.png"))
                n += 1
            print(f"slide {i}: {secs:.1f}s, {int(secs * FPS)} frames", flush=True)
        browser.close()
    (src / "audio.txt").write_text("\n".join(alist) + "\n")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-framerate", str(FPS),
                    "-i", str(frames / "%05d.png"), "-f", "concat", "-safe", "0",
                    "-i", str(src / "audio.txt"), "-map", "0:v", "-map", "1:a", "-shortest",
                    "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(out)], check=True)
    print("wrote", out)


if __name__ == "__main__":
    src = Path(sys.argv[1])
    (src / "script.json").write_text(json.dumps({"slides": len(SLIDES)}))
    render(src, Path(sys.argv[2]))
