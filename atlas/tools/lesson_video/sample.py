"""A sample reel from a course lesson: 30 to 40 seconds, vertical, the
lesson's own words. Miro, 2026-10-05: "post lesson samples, make one
first for me to watch".

The shape is the course's own Lesson 2.6: a hook in the words of someone
with the problem, one useful idea from the lesson, and a pointer to the
whole thing. Nothing is promised, no number appears that the lesson lacks,
and the end card says it was made with AI. The narration is the same
Fish voice as the lessons (tts.py, on the server); the pictures are
slides in the Academy's look, portrait, captions large enough for a phone.

  python tools/lesson_video/sample.py <promo.json> <lesson id> <out dir> \
      [--course "Sell With AI"] [--lessons 27] [--url shop.mapfront.io]
  then tts.py on the server for <out dir>, then
  python tools/lesson_video/build.py assemble <out dir> <id>
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

W, H = 1080, 1920

CSS = f"""
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:{W}px;height:{H}px;overflow:hidden;background:#06111a;color:#e8f1f7;
font-family:"Atkinson Hyperlegible Next","Atkinson Hyperlegible",-apple-system,"Helvetica Neue",Arial,sans-serif}}
body::before{{content:"";position:absolute;inset:0;z-index:0;
background-image:linear-gradient(rgba(47,208,255,.08) 1px,transparent 1px),linear-gradient(90deg,rgba(47,208,255,.08) 1px,transparent 1px);
background-size:72px 72px;mask-image:radial-gradient(ellipse at 50% 30%,#000 25%,transparent 80%)}}
body::after{{content:"";position:absolute;inset:0;z-index:0;
background:radial-gradient(circle at 80% 12%,rgba(47,208,255,.28),transparent 50%)}}
.s{{position:relative;z-index:1;height:100%;padding:150px 84px 220px;display:flex;flex-direction:column}}
.top{{display:flex;align-items:center;gap:16px;font-size:34px;color:#8fa3b3}}
.top b{{color:#2fd0ff;letter-spacing:.1em;font-weight:800}}
.cube{{width:36px;height:36px;border:2px solid #2fd0ff;transform:rotate(45deg);box-shadow:0 0 22px rgba(47,208,255,.5)}}
.tag{{margin-top:44px;font-size:34px;color:#2fd0ff;font-weight:700}}
.body{{flex:1;display:flex;align-items:center}}
.body p{{font-size:78px;line-height:1.18;font-weight:800;letter-spacing:-.02em;color:#fff}}
.body p.quiet{{font-size:60px;font-weight:600;line-height:1.28;color:#e8f1f7}}
.body p b{{color:#2fd0ff}}
.foot{{font-size:30px;color:#5f7686}}
.cta .body p{{font-size:66px}}
.pill{{display:inline-block;margin-top:40px;padding:18px 34px;border-radius:999px;background:#2fd0ff;color:#06111a;font-size:40px;font-weight:800}}
"""


def slide(kind: str, text: str, *, tag: str, course: str, foot: str, pill: str = "") -> str:
    quiet = "quiet" if kind in ("idea",) else ""
    pill_html = f'<div><span class="pill">{html.escape(pill)}</span></div>' if pill else ""
    return f"""<!doctype html><html><head><meta charset=utf-8><style>{CSS}</style></head>
<body><div class="s {'cta' if kind == 'cta' else ''}">
<div class=top><div class=cube></div><b>ATLAS</b><span>Academy</span></div>
<div class=tag>{html.escape(tag)}</div>
<div class=body><div><p class="{quiet}">{text}</p>{pill_html}</div></div>
<div class=foot>{html.escape(foot)}</div>
</div></body></html>"""


def build(promo: dict, lesson_id: str, course: str, lessons: int, url: str,
          extra: str = "") -> list[dict]:
    item = next(x for x in promo["lessons"] if x["id"] == lesson_id)
    title = item["title"]
    hook, line = item["hook"], item["line"]
    foot = f"{course} · Lesson {lesson_id}: {title}"
    pointer_said = (f"That's one lesson of {lessons} in {course}, on ATLAS Academy. "
                    f"The link is in my profile.")
    return [
        {"kind": "hook", "heading": title, "say": hook,
         "html": slide("hook", html.escape(hook), tag="From the course", course=course, foot=foot)},
        {"kind": "idea", "heading": title, "say": line,
         "html": slide("idea", html.escape(line), tag=f"Lesson {lesson_id}: {title}",
                       course=course, foot=foot)},
        *([{"kind": "idea", "heading": title, "say": extra,
            "html": slide("idea", html.escape(extra), tag=f"Lesson {lesson_id}: {title}",
                          course=course, foot=foot)}] if extra else []),
        {"kind": "cta", "heading": title, "say": pointer_said,
         "html": slide("cta", f"One of <b>{lessons} lessons</b> in {html.escape(course)}.",
                       tag="ATLAS Academy", course=course, foot="Made with AI · " + foot,
                       pill=f"{url} · link in bio")},
    ]


def from_script(script: list[dict], course: str, foot: str) -> list[dict]:
    """A hand-written reel: each item {kind, tag, text, say, pill?}. `text`
    may carry <b> for the accent colour; everything else is escaped."""
    segs = []
    for item in script:
        kind = item.get("kind", "idea")
        shown = html.escape(item["text"]).replace("&lt;b&gt;", "<b>").replace("&lt;/b&gt;", "</b>")
        segs.append({"kind": kind, "heading": item.get("tag", ""), "say": item["say"],
                     "html": slide(kind, shown, tag=item.get("tag", ""), course=course,
                                   foot=foot, pill=item.get("pill", ""))})
    return segs


def render(out: Path, lid: str, segs: list[dict]) -> None:
    from playwright.sync_api import sync_playwright
    d = out / lid
    (d / "slides").mkdir(parents=True, exist_ok=True)
    (d / "segments.json").write_text(json.dumps(segs, indent=1, ensure_ascii=False))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": W, "height": H})
        for n, seg in enumerate(segs, 1):
            page.set_content(seg["html"])
            page.screenshot(path=str(d / "slides" / f"{n:03d}.png"))
        browser.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("promo", help="a promo.json, or a script.json with --script")
    ap.add_argument("lesson", help="the lesson id, or a name for a --script reel")
    ap.add_argument("out")
    ap.add_argument("--course", default="Sell With AI")
    ap.add_argument("--lessons", type=int, default=27)
    ap.add_argument("--url", default="shop.mapfront.io")
    ap.add_argument("--extra", default="", help="one more sentence from the lesson")
    ap.add_argument("--script", action="store_true", help="promo is a hand-written script")
    ap.add_argument("--foot", default="")
    a = ap.parse_args()
    data = json.loads(Path(a.promo).read_text())
    if a.script:
        segs = from_script(data, a.course, a.foot or f"ATLAS Academy · {a.url}")
    else:
        segs = build(data, a.lesson, a.course, a.lessons, a.url, a.extra)
    render(Path(a.out), a.lesson, segs)
    words = sum(len(s["say"].split()) for s in segs)
    print(f"sample {a.lesson}: {len(segs)} slides, {words} words (~{words / 2.3:.0f} s)")


if __name__ == "__main__":
    main()
