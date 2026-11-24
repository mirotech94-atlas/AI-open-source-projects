"""Render the ATLAS Academy 3D kit on the Mac: loops, the intro sting, stills.

    .venv/bin/python tools/tutorial3d/build.py [--only n8n,claude] [--frames DIR]

The production server has one CPU and no GPU, so it never renders 3D. This
script does, once, here: three.js scenes (kit.js) in headless Chromium on
the Mac's GPU (ANGLE on Metal), one deterministic frame at a time
(`kit.render(t)`, t = frame / 30, never wall-clock), piped as JPEG into
ffmpeg. The output is committed under atlas/modules/academy/assets/tutorial3d/
and ships in the image with the code; the server only overlays the slides on
these files and encodes (tutorials.segment_argv).

  loop-<series>.mp4  1920x1080, 10 s, seamless (every motion is periodic in
                     10 s), H.264 with a keyframe every second so the server
                     can start a loop anywhere cheaply; no audio
  loop-<topic>.mp4   the same, one per topic (2026-10-11: the backdrop follows
                     what is being said -- tutorial_design.TOPICS); CRF 25,
                     to keep the whole kit under 120 MB
  loop-mark.mp4      the ATLAS mark idling: under the fixed intro's words
  sting.mp4          4 s: the mark assembling; ends on loop-mark's first frame
  loop-outro.mp4     the Subscribe button and the store card: under the ending
  still-<series>.jpg 1280x720: the series scene framed closer, for thumbnails
  kit.json           every file with its size and sha256, and the three.js
                     version that drew them; the 2026-10-18 series' scenes
                     (SERIES_AI) too, since the combined video-system bump
                     folded their kit-ai.json into it

three.js is the project's own pinned dependency (ui/package.json, MIT
licence), read from ui/node_modules/three: `cd ui && npm ci` first, or pass
--three. Nothing is fetched from the network.

Every re-render adds the binaries to git again: look at the frames
(--frames) before committing a new kit.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import mimetypes
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))

from atlas.modules.academy.video_tokens import fonts_css

OUT = REPO / "atlas" / "modules" / "academy" / "assets" / "tutorial3d"
FPS = 30
LOOP = 10
STING = 4
SERIES = ("channel", "claude", "n8n", "project", "business", "agents", "local")
# 2026-10-18: the AI series' scenes. "vault" is AI Security's: "security" is
# already a topic loop, and drawing the series over it would change every
# render that stands on that topic.
SERIES_AI = ("explained", "vault", "future")
TOPICS = ("trigger", "api", "message", "code", "memory", "tools", "server", "cloud",
          "security", "quiz", "recap")
STILL_AT = {"n8n": 2.4, "claude": 1.2, "project": 4.0, "business": 2.5, "agents": 1.0,
            "local": 1.6, "channel": 2.0, "explained": 1.3, "vault": 4.0, "future": 2.0}
ORIGIN = "http://kit.local"


def _three_dir(given: str | None) -> Path:
    for cand in ([Path(given)] if given else []) + [
            REPO / "ui" / "node_modules" / "three",
            REPO.parent.parent.parent / "ui" / "node_modules" / "three"]:
        if (cand / "build" / "three.module.js").is_file():
            return cand
    raise SystemExit("three.js not found: run `npm ci` in ui/ or pass --three DIR")


def _encoder(out: Path, crf: int = 23) -> subprocess.Popen:
    return subprocess.Popen(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "image2pipe",
         "-c:v", "mjpeg", "-framerate", str(FPS), "-i", "-",
         "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-g", str(FPS),
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", "-an", str(out)],
        stdin=subprocess.PIPE)


async def _page(browser, three: Path, name: str, w: int, h: int, still: bool = False):
    pg = await browser.new_page(viewport={"width": w, "height": h})
    html = (HERE / "kit.html").read_text().replace(
        '<style id="fonts"></style>', f"<style>{fonts_css()}</style>")

    async def serve(route) -> None:
        path = route.request.url.removeprefix(ORIGIN).split("?")[0]
        if path.startswith("/kit/kit.html"):
            await route.fulfill(body=html, content_type="text/html")
            return
        base, rel = ((HERE, path.removeprefix("/kit/")) if path.startswith("/kit/") else
                     (three, path.removeprefix("/three/")) if path.startswith("/three/") else
                     (None, ""))
        f = (base / rel).resolve() if base else None
        if f is None or not f.is_file() or base.resolve() not in f.parents:
            await route.fulfill(status=404, body="")
            return
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if f.suffix == ".js":
            ctype = "text/javascript"
        await route.fulfill(body=f.read_bytes(), content_type=ctype)

    await pg.route(f"{ORIGIN}/**", serve)
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    mode = "&mode=still" if still else ""
    await pg.goto(f"{ORIGIN}/kit/kit.html?scene={name}&w={w}&h={h}{mode}")
    await pg.wait_for_function("window.kit !== undefined", timeout=30000)
    await pg.evaluate("window.kit.ready")
    if errors:
        raise SystemExit(f"{name}: {errors}")
    return pg


async def _shot(pg, t: float, quality: int = 93) -> bytes:
    await pg.evaluate(f"window.kit.render({t:.6f})")
    return await pg.screenshot(type="jpeg", quality=quality)


async def _clip(browser, three: Path, name: str, out: Path, start: float, seconds: float,
                frames_dir: Path | None, crf: int = 23) -> None:
    pg = await _page(browser, three, name, 1920, 1080)
    enc = _encoder(out, crf)
    n = round(seconds * FPS)
    try:
        for f in range(n):
            buf = await _shot(pg, start + f / FPS)
            enc.stdin.write(buf)
            if frames_dir and f in (0, n // 2):
                (frames_dir / f"{out.stem}-{f:03d}.jpg").write_bytes(buf)
        # A loop's frame at L must equal its frame at 0 (the seam).
        if start == 0 and seconds == LOOP:
            first, last = await _shot(pg, 0), await _shot(pg, LOOP)
            if first != last:
                print(f"  warning: {name} frame at {LOOP}s differs from frame 0")
    finally:
        enc.stdin.close()
        enc.wait()
        await pg.close()
    if enc.returncode != 0:
        raise SystemExit(f"ffmpeg failed for {out.name}")
    print(f"  {out.name}: {out.stat().st_size / 1e6:.2f} MB")


async def _still(browser, three: Path, name: str, out: Path) -> None:
    pg = await _page(browser, three, name, 1280, 720, still=True)
    out.write_bytes(await _shot(pg, STILL_AT.get(name, 2.0), quality=90))
    await pg.close()
    print(f"  {out.name}: {out.stat().st_size / 1e3:.0f} kB")


def _manifest(three: Path, name: str) -> None:
    version = json.loads((three / "package.json").read_text())["version"]
    files = {p.name: {"bytes": p.stat().st_size,
                      "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in sorted(OUT.iterdir()) if p.suffix in (".mp4", ".jpg")}
    (OUT / name).write_text(json.dumps(
        {"three": version, "fps": FPS, "loop_seconds": LOOP, "sting_seconds": STING,
         "width": 1920, "height": 1080, "files": files}, indent=1) + "\n")
    total = sum(f["bytes"] for f in files.values())
    print(f"{name}: {len(files)} files, {total / 1e6:.1f} MB")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--three")
    ap.add_argument("--only", default="")
    ap.add_argument("--frames", help="also write a few frames here to look at")
    ap.add_argument("--stills-only", action="store_true")
    ap.add_argument("--peek", type=float, help="with --frames: one frame at this t, no video")
    args = ap.parse_args()
    three = _three_dir(args.three)
    only = {x for x in args.only.split(",") if x}
    frames = Path(args.frames) if args.frames else None
    if frames:
        frames.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--use-angle=metal", "--enable-gpu",
                                                               "--ignore-gpu-blocklist"])
        for name in (*SERIES, *SERIES_AI, *TOPICS, "mark", "outro"):
            if only and name not in only:
                continue
            print(name)
            if args.peek is not None and frames:
                pg = await _page(browser, three, name, 1920, 1080)
                (frames / f"peek-{name}.jpg").write_bytes(await _shot(pg, args.peek))
                await pg.close()
                if name in SERIES or name in SERIES_AI:
                    await _still(browser, three, name, frames / f"still-{name}.jpg")
                continue
            if name in SERIES or name in SERIES_AI:
                await _still(browser, three, name, OUT / f"still-{name}.jpg")
            if args.stills_only:
                continue
            await _clip(browser, three, name, OUT / f"loop-{name}.mp4", 0, LOOP, frames,
                        25 if name in TOPICS else 23)
            if name == "mark":
                await _clip(browser, three, name, OUT / "sting.mp4", -STING, STING, frames)
        await browser.close()
    if args.peek is None:
        _manifest(three, "kit.json")


if __name__ == "__main__":
    asyncio.run(main())
