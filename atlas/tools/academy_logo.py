"""Build every ATLAS Academy logo file from atlas/modules/academy/logo.py.

    .venv/bin/python tools/academy_logo.py [--final DIR]

Miro picked the Grid A0 logo on 2026-10-16. This writes, in the repo:

    atlas/modules/academy/assets/icon.svg            the store's favicon
    atlas/modules/academy/assets/apple-touch-icon.png  180, opaque
    atlas/modules/academy/assets/icon-192.png, icon-512.png
    atlas/modules/academy/assets/brand/watermark.svg + .png  the tutorials'

and with --final (the untracked brand/atlas-academy-final folder of the
main checkout) the hand-off set: mark, mark-dark, lockup, lockup-dark (SVG
and PNG), favicon-32/16, avatar-800 (for YouTube Studio: the API cannot set
a channel picture), the app icons and the watermark. A new watermark.png
renders unpublished tutorials again (its hash is in tutorials.script_key);
published videos are never touched.
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from atlas.modules.academy import logo

ASSETS = ROOT / "atlas/modules/academy/assets"
APP_ICONS = {"apple-touch-icon.png": 180, "icon-192.png": 192, "icon-512.png": 512}


async def _render(jobs: list[tuple[str, int, int, Path]]) -> None:
    """(svg, width, height, png) each, transparent where the SVG draws nothing."""
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for svg, w, h, out in jobs:
            pg = await b.new_page(viewport={"width": w, "height": h})
            await pg.set_content(
                "<!doctype html><style>html,body{margin:0;background:transparent}"
                f"svg{{display:block;width:{w}px;height:{h}px}}</style>{svg}")
            out.parent.mkdir(parents=True, exist_ok=True)
            await pg.screenshot(path=str(out), omit_background=True)
            await pg.close()
            print(out, f"{w}x{h}")
        await b.close()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    print(path)


async def main(final: Path | None) -> None:
    icon = logo.mark_svg(dark=True)
    wm = logo.watermark_svg()
    wmv = logo.watermark_svg(video=True)         # the navy video mark, 2026-10-17
    _write(ASSETS / "icon.svg", icon)
    _write(ASSETS / "brand/watermark.svg", wm)
    _write(ASSETS / "brand/watermark-video.svg", wmv)
    jobs = [(wm, logo.WATERMARK_W, logo.WATERMARK_H, ASSETS / "brand/watermark.png"),
            (wmv, logo.WATERMARK_W, logo.WATERMARK_H, ASSETS / "brand/watermark-video.png")]
    jobs += [(logo.icon_svg(n), n, n, ASSETS / name) for name, n in APP_ICONS.items()]
    if final is not None:
        svgs = {"mark.svg": logo.mark_svg(dark=False), "mark-dark.svg": icon,
                "lockup.svg": logo.lockup_svg(dark=False),
                "lockup-dark.svg": logo.lockup_svg(dark=True),
                "avatar-800.svg": logo.icon_svg(800), "watermark.svg": wm}
        for name, text in svgs.items():
            _write(final / name, text)
        lw = round(logo.LOCKUP_W * 4)
        jobs += [(logo.mark_svg(dark=False), 512, 512, final / "mark-512.png"),
                 (icon, 512, 512, final / "mark-dark-512.png"),
                 (logo.lockup_svg(dark=False), lw, 256, final / "lockup.png"),
                 (logo.lockup_svg(dark=True), lw, 256, final / "lockup-dark.png"),
                 (icon, 32, 32, final / "favicon-32.png"),
                 (icon, 16, 16, final / "favicon-16.png"),
                 (logo.icon_svg(800), 800, 800, final / "avatar-800.png")]
    await _render(jobs)
    if final is not None:
        for name in (*APP_ICONS, "brand/watermark.png"):
            shutil.copyfile(ASSETS / name, final / Path(name).name)
            print(final / Path(name).name)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--final", type=Path, help="also write the hand-off set here")
    asyncio.run(main(ap.parse_args().final))
