"""Every catalog thumbnail, really drawn (batch 7, the owner 2026-10-17: "create
thumbnails for each video and different as well").

Chromium draws each scripted episode's thumbnail exactly as a render does
(tutorial_thumbs.render: its real image, then the page), and the tests read
the pictures: 1280x720 PNG under YouTube's 2 MB, every text element large
enough at the 168x94 list size, and no two thumbnails of a series alike by a
perceptual hash (a 256-bit dHash of a 17x16 grey downscale, made by ffmpeg).
Skipped where Chromium or ffmpeg is missing.
"""
from __future__ import annotations

import asyncio
import itertools
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from atlas.modules.academy import tutorial_thumbs as tt
from atlas.modules.academy.tutorial_series import SERIES

# Fixed before the first measurement (2026-10-17) and held: a design that
# falls under them is redrawn, never the threshold lowered.
SERIES_MIN = 48                      # of 256 bits, any two in one series
NEIGHBOUR_MIN = 64                   # of 256 bits, two episodes side by side
WORDS_LIST_PX = 13.0                 # the words, as drawn at 168 px wide

SCRIPTED = [(s, ep) for s in SERIES for ep in s.episodes if ep.scripted]

MEASURE = """() => {
  const out = [];
  for (const el of document.querySelectorAll('body *')) {
    const own = [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim());
    if (!own) continue;
    const r = el.getBoundingClientRect();
    out.push({text: el.textContent.trim(), cls: el.className.baseVal ?? el.className,
              px: parseFloat(getComputedStyle(el).fontSize),
              left: r.left, top: r.top, right: r.right, bottom: r.bottom});
  }
  return {texts: out, svgText: document.querySelectorAll('svg text').length};
}"""


class Shooter:
    """tutorials.Shooter's shoot, plus the page's text measured as drawn."""

    def __init__(self, browser) -> None:
        self.b = browser
        self.measured: dict[str, dict] = {}

    async def shoot(self, doc: str, path: str, w: int = 1920, h: int = 1080) -> str:
        pg = await self.b.new_page(viewport={"width": w, "height": h})
        try:
            await pg.set_content(doc)
            await pg.evaluate("document.fonts.ready.then(() => null)")
            if (w, h) == (tt.TW, tt.TH):
                self.measured[path] = await pg.evaluate(MEASURE)
            await pg.screenshot(path=path, omit_background=True)
        finally:
            await pg.close()
        return path


def dhash(path: Path) -> int:
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf",
                          "scale=17:16:flags=area,format=gray", "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    bits = 0
    for y in range(16):
        row = raw[y * 17:(y + 1) * 17]
        for x in range(16):
            bits = (bits << 1) | (row[x] > row[x + 1])
    return bits


def _distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


@pytest.fixture(scope="module")
def drawn(tmp_path_factory):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    try:
        from playwright.async_api import Error, async_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    out = tmp_path_factory.mktemp("thumbs")

    async def draw():
        async with async_playwright() as p:
            try:
                browser = await p.chromium.launch(headless=True)
            except Error as exc:                     # no Chromium on this machine
                pytest.skip(f"Chromium will not launch: {exc}")
            sh = Shooter(browser)
            got = {}
            try:
                for s, ep in SCRIPTED:
                    path = out / f"{s.key}-{ep.number:02d}.png"
                    work = out / f"work-{s.key}-{ep.number}"
                    work.mkdir()
                    why = await tt.render(ep, sh, str(path), level=ep.level,
                                          badge="12 MIN", work=str(work))
                    got[(s.key, ep.number)] = (path, why, sh.measured.get(str(path)))
            finally:
                await browser.close()
            return got
    return asyncio.run(draw())


IDS = [f"{s.key}-{e.number}" for s, e in SCRIPTED]


@pytest.mark.parametrize("s,ep", SCRIPTED, ids=IDS)
def test_every_thumbnail_is_a_1280x720_png_under_2_mb(drawn, s, ep):
    path, why, _m = drawn[(s.key, ep.number)]
    assert why is None, why
    head = path.read_bytes()[:24]
    assert head[:8] == tt.PNG_MAGIC and struct.unpack(">II", head[16:24]) == (1280, 720)
    assert 0 < path.stat().st_size <= tt.MAX_BYTES


@pytest.mark.parametrize("s,ep", SCRIPTED, ids=IDS)
def test_every_text_element_reads_at_phone_list_size(drawn, s, ep):
    _p, _why, m = drawn[(s.key, ep.number)]
    assert m["svgText"] == 0
    words = set(tt.words_of(ep).split("\n"))
    for t in m["texts"]:
        shown = t["px"] * tt.LIST_SCALE
        least = WORDS_LIST_PX if t["text"] in words else tt.MIN_LIST_PX
        assert shown >= least, (t["text"], round(shown, 2))
        # nothing cut by the frame
        assert t["left"] >= 0 and t["top"] >= 0, t
        assert t["right"] <= tt.TW and t["bottom"] <= tt.TH, t
    assert words <= {t["text"] for t in m["texts"]}           # the result, on the picture


def test_no_two_thumbnails_of_a_series_look_alike(drawn):
    hashes = {k: dhash(p) for k, (p, _w, _m) in drawn.items()}
    least_series, least_next = 256, 256
    for s in SERIES:
        keys = [(s.key, e.number) for e in s.episodes if e.scripted]
        for a, b in itertools.combinations(keys, 2):
            d = _distance(hashes[a], hashes[b])
            least_series = min(least_series, d)
            assert d >= SERIES_MIN, (a, b, d)
        for a, b in itertools.pairwise(keys):
            d = _distance(hashes[a], hashes[b])
            least_next = min(least_next, d)
            assert d >= NEIGHBOUR_MIN, (a, b, d)
    print(f"dHash minimum: {least_series} in a series, {least_next} for neighbours")
