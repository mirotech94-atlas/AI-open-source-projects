"""Rebuild the tutorial watermark PNG from its SVG.

    .venv/bin/python tools/academy_watermark.py

atlas/modules/academy/assets/brand/watermark-video.svg is the source (the
navy video mark since the combined bump of 2026-10-18; its size is the
mark's size on the 1920x1080 frame, its opacity baked in); the PNG next to
it is what ffmpeg overlays. Since Miro picked the Grid A0 logo
(2026-10-16) the SVG is written by tools/academy_logo.py from
atlas/modules/academy/logo.py, which renders this PNG too; this script
re-renders the PNG alone. Unpublished renders pick a new PNG up (its hash
is in tutorials.script_key); published videos are never touched.
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from atlas.modules.academy.reel_design import fonts_css
from atlas.modules.academy.tutorial_design import WATERMARK, WATERMARK_SVG


async def main() -> None:
    from playwright.async_api import async_playwright
    svg = WATERMARK_SVG.read_text()
    w = int(re.search(r'width="(\d+)"', svg).group(1))
    h = int(re.search(r'height="(\d+)"', svg).group(1))
    doc = (f"<!doctype html><style>{fonts_css()}html,body{{margin:0;background:transparent}}"
           f"svg{{display:block}}</style>{svg}")
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": w, "height": h})
        await pg.set_content(doc)
        await pg.evaluate("document.fonts.ready.then(() => null)")
        await pg.screenshot(path=str(WATERMARK), omit_background=True)
        await b.close()
    print(WATERMARK, f"{w}x{h}")


if __name__ == "__main__":
    asyncio.run(main())
