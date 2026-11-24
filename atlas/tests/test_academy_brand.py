"""The Atlas Academy palettes. Video (tutorials, thumbnails, teaching
Shorts) uses the 2026-10-17 navy system (docs/academy-video-system,
video_tokens.py) and IBM Plex; the store keeps the 2026-10-12 black #0A0D14
and royal blue #5B8CFF. WCAG throughout."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from atlas.modules.academy import logo
from atlas.modules.academy import tutorial_design as design
from atlas.modules.academy import tutorial_thumbs as tt
from atlas.modules.academy import video_tokens as vt
from atlas.modules.academy.reel_design import fonts_css as store_fonts_css
from atlas.modules.academy.tutorial_series import SERIES
from atlas.modules.academy.video_tokens import fonts_css

ROOT = Path(__file__).resolve().parents[1]
EPISODES = [(s, ep) for s in SERIES for ep in s.episodes]
SCENES = [(s, sc) for s, ep in EPISODES
          for sc in tuple(x for x in (ep.payoff, ep.hook) if x) + ep.scenes]

# Miro's store values (2026-10-12), exactly: the shop, store, API pages, the
# product covers and the logo's store files keep them.
MIROS = {"black": "#0A0D14", "surfaces": ("#0F1420", "#131A28", "#1B2334"),
         "blue": "#5B8CFF", "on_blue": "#070A12", "light_blue": "#8FB0FF",
         "text": "#EEF2FA", "secondary": "#C9D2E3", "muted": "#8E99AE"}
STORE_HUES = tuple(h.lstrip("#").lower() for h in (
    MIROS["black"], *MIROS["surfaces"], MIROS["blue"], MIROS["on_blue"],
    MIROS["light_blue"], MIROS["text"], MIROS["secondary"], MIROS["muted"]))
# Every hue an earlier system drew: the plum rebrand (held 2026-10-12) and
# the ink/amber look before it. None may come back in what this lane draws.
OLD_HUES = ("291b36", "faf8f3", "f5c451", "cdbee0", "b9acc7", "5b4775", "6f6480", "1c1226",
            "3b2a4d", "4a3360", "3a3044", "3b2f48", "7d6a8f", "2a2033", "140d1b",
            "0e1b29", "e5a43c", "f4efe4", "8fb8c9", "8a96a3", "24425f", "15283c", "0b141e",
            "4b6378", "1b3550", "8a7a5c", "2a3644", "30363c", "1a1f26", "0b0e12", "ffdcae")
# What draws a video frame, a thumbnail or a Short (2026-10-17: navy).
VIDEO_SOURCES = ("atlas/modules/academy/tutorial_design.py",
                 "atlas/modules/academy/tutorial_thumbs.py",
                 "atlas/modules/academy/tutorial_short.py",
                 "atlas/modules/academy/tutorial_motion.py",
                 "atlas/modules/academy/tutorial_demos.py",
                 "atlas/modules/academy/tutorials.py",
                 "atlas/modules/academy/video_tokens.py",
                 "atlas/modules/academy/assets/brand/watermark-video.svg",
                 "tools/tutorial3d/kit.js", "tools/tutorial3d/kit.html",
                 "tools/tutorial_demos/record.py")
# The store's logo files (black + royal blue stays).
STORE_SOURCES = ("atlas/modules/academy/logo.py", "atlas/modules/academy/assets/icon.svg",
                 "atlas/modules/academy/assets/brand/watermark.svg", "tools/academy_logo.py",
                 "tools/academy_covers.py")
SOURCES = VIDEO_SOURCES + STORE_SOURCES


def test_the_video_palette_is_the_2026_10_17_tokens():
    """Miro, 2026-10-17: video uses the navy system; tutorial_design names
    exactly video_tokens' colours and nothing else."""
    assert design.NAVY == vt.NAVY == "#101b30" and design.INK == design.NAVY
    assert (design.NAVY_RAISED, design.NAVY_LINE) == (vt.NAVY_RAISED, vt.NAVY_LINE)
    assert (design.PAPER, design.PAPER_MUTED) == (vt.PAPER, vt.PAPER_MUTED)
    assert (design.BLUE, design.BLUE_TEXT, design.BLUE_TINT, design.ON_BLUE) == (
        vt.BLUE, vt.BLUE_TEXT, vt.BLUE_TINT, vt.ON_BLUE)
    assert (design.AMBER, design.SUCCESS, design.ERROR) == vt.STATE_COLORS
    assert set(design.PALETTE) == set(vt.COLORS.values()) and len(design.PALETTE) == 12


def test_the_contrast_maths_matches_wcag():
    black, white = design.over((), "#000000"), design.over((), "#FFFFFF")
    assert round(design.contrast(black, white), 1) == 21.0
    assert round(design.contrast(white, white), 1) == 1.0
    # blue on navy is the spec's 3.9:1: large text and marks only
    blue = design.over((), design.BLUE)
    assert 3.0 < design.contrast(blue, design.over((), design.NAVY)) < 4.5


SPEC = json.loads(vt.TOKENS_JSON.read_text())
NOTES = [(t["name"], float(m.group(1)), m.group(2))
         for t in SPEC["color"]["tokens"]
         for m in re.finditer(r"(\d+(?:\.\d+)?):1 on (navy-raised|navy)\b", t["usage"])]


@pytest.mark.parametrize("name,ratio,ground", NOTES, ids=lambda x: str(x))
def test_the_contrast_notes_in_tokens_json_hold(name, ratio, ground):
    """The spec's own ratios (rounded there), within one point."""
    got = design.contrast(design.over((), vt.COLORS[name]), design.over((), vt.COLORS[ground]))
    assert abs(got - ratio) <= 1.0, (name, ground, round(got, 2))


def test_the_contrast_notes_are_read():
    assert {n for n, *_ in NOTES} >= {"paper", "paper-muted", "blue", "blue-text", "amber",
                                       "success", "error"}


@pytest.mark.parametrize("report", [design.contrast_report, tt.contrast_report],
                         ids=["slides", "thumbnails"])
def test_every_text_and_background_pair_passes_wcag(report):
    """tokens.json: 4.5:1 for text under 24px, 3:1 at 24px and up (and for a
    state or mark); each pair at the px it is drawn."""
    rows = report()
    # the thumbnails draw far less since batch 7 (no drawn focal pictures)
    assert len(rows) >= (30 if report is design.contrast_report else 10)
    failing = [(name, ratio, least) for name, ratio, least in rows if ratio < least]
    assert not failing, failing
    assert all(least in (3.0, 4.5) for _n, _r, least in rows)


@pytest.mark.parametrize("table", [design.TEXT_PAIRS, tt.TEXT_PAIRS], ids=["slides", "thumbs"])
def test_small_text_is_held_to_4_5_and_blue_text_is_never_small(table):
    for name, fg, _layers, _base, px in table:
        colour = fg if isinstance(fg, str) else fg[0]
        assert design.need(px) == (4.5 if 0 < px < 24 else 3.0)
        if colour == design.BLUE and px:
            assert px >= 24, name                   # small blue is blue-text


def test_type_on_a_fill_is_on_blue_never_the_field():
    for table in (design.TEXT_PAIRS, tt.TEXT_PAIRS):
        for name, fg, layers, _base, _px in table:
            colour = fg if isinstance(fg, str) else fg[0]
            if layers and layers[-1] in ((design.BLUE, 1.0), (design.SUCCESS, 1.0)):
                assert colour in (design.ON_BLUE, design.NAVY), name
                assert colour == design.ON_BLUE or "edge" in name, name


HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")
RGBA = re.compile(r"rgba?\((\d+),\s*(\d+),\s*(\d+)")


def _allowed() -> set[str]:
    return {c.lower() for c in design.PALETTE} | {"#000", "#000000", "#c62828", "#fff"}


def _rgb_tokens() -> set[tuple[int, int, int]]:
    out = {(0, 0, 0)}
    for c in design.PALETTE:
        out.add(tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)))
    return out


def _foreign(doc: str) -> set[str]:
    doc = doc.replace(fonts_css(), "")
    css, _, body = doc.rpartition("</style>")
    doc = css + re.sub(r">[^<]*<", "><", body)      # words on screen ("Invoice #104") are not colours
    bad = {h for h in HEX.findall(doc) if h.lower() not in _allowed()}
    rgb = _rgb_tokens()
    bad |= {",".join(m) for m in RGBA.findall(doc) if tuple(map(int, m)) not in rgb}
    return bad


def _video_docs() -> list[str]:
    """Every page a video frame is drawn from: every step of every catalog
    slide, the demo layer, the cards round the lesson and the plates."""
    docs = []
    for s, sc in SCENES:
        for step in range(len(sc.said)):
            docs.append(design.slide(sc, step, mark=s.mark, chapter=0, chapters=3,
                                     progress=.5,
                                     countdown=2 if sc.kind == "quiz" else None))
    ep = SERIES[1].episodes[1]
    # 2026-10-17: the result-first opening, the BrandSting and the EndCard
    from atlas.modules.academy import tutorial_series as ts
    nxt = ts.next_up(SERIES[1].key, ep)
    docs += [design.intro(), design.topic(ep, minutes=6),
             design.sting_layer(True), design.result_title(ep),
             design.result_title(ts.replace(ep, verified=True)), design.scope_card(ep),
             design.sting_frame(0), design.sting_frame(20, True),
             design.end_card(ep, nxt, shop=True),
             design.slide(SCENES[0][1], 0, mark="m", chapter=0, chapters=1, preview=True)]
    return docs


def test_every_slide_draws_only_palette_colours():
    from atlas.modules.academy import tutorial_demos as td
    seen: set[str] = set()
    for doc in _video_docs():
        seen |= _foreign(doc)
    assert td.DEMOS                     # the demo layer is in the catalog's slides above
    assert not seen, seen


def test_no_video_frame_carries_the_ai_label():
    """Miro 2026-10-17: off screen; disclosed via containsSyntheticMedia +
    description."""
    for doc in _video_docs():
        assert "Made with AI" not in doc and 'class="tag"' not in doc
    assert ".tag{" not in design.BASE


SIZE = re.compile(r"(?:font:\s*\d+\s+|font-size:\s*)(\d+)px")


def test_nothing_on_a_16_9_frame_is_under_support():
    """tokens.json: nothing on a 1920x1080 frame is set smaller than
    `support` (28px)."""
    small = set()
    for doc in _video_docs():
        # value-tag (26px mono) is the spec's one exception: the WorkflowTrace
        # token and its input/output values (video_tokens.FLOOR_16_9)
        css = doc.replace(fonts_css(), "").replace(vt.VALUE_TAG.css(), "")
        small |= {int(n) for n in SIZE.findall(css) if int(n) < vt.FLOOR_16_9}
    assert not small, small


def test_slides_are_set_in_plex_only():
    for doc in _video_docs():
        css = doc.replace(fonts_css(), "")
        assert "Cabinet" not in css and '"Atk"' not in css and "SF Mono" not in css
        assert '"IBM Plex Sans"' in css
        weights = {int(w) for w in re.findall(r"font:\s*(\d{3})\s", css)}
        weights |= {int(w) for w in re.findall(r"font-weight:\s*(\d{3})", css)}
        assert weights <= set(vt.WEIGHTS_SANS), weights
    assert fonts_css() in design.slide(SCENES[0][1], 0, mark="m", chapter=0, chapters=1)


# Code that does not fit the card at the 28px floor: never shrunk under it;
# the card shows a window of its lines (2026-10-18, test_academy_safe_area).
CODE_TOO_LONG = {("n8n", 1, "The whole workflow as JSON")}


def test_code_is_set_at_the_code_size_and_never_under_the_floor():
    over = set()
    for s in SERIES:
        for ep in s.episodes:
            for sc in ep.scenes:
                if sc.kind != "code":
                    continue
                room = design.code_room(sc)
                size = design._code_size(sc.code, room)
                assert vt.FLOOR_16_9 <= size <= vt.CODE.size
                if design.code_fit(sc.code, room) < vt.FLOOR_16_9:
                    over.add((s.key, ep.number, sc.heading))
    assert over == CODE_TOO_LONG, over


@pytest.mark.parametrize("s,ep", EPISODES, ids=lambda x: getattr(x, "slug", ""))
def test_every_thumbnail_draws_only_palette_colours(s, ep):
    doc = design.thumbnail(ep.thumb_title, s.key, s.mark, ep.number, badge="6 MIN", ep=ep)
    assert not _foreign(doc)
    css = re.sub(r"data:[^\"')]+", "", doc.replace(fonts_css(), ""))  # the 3D still
    assert "Cabinet" not in css and "Atk" not in css
    # the 16:9 floor scaled to the 1280x720 frame
    floor = vt.FLOOR_16_9 * tt.TH / 1080
    assert all(int(n) >= floor for n in SIZE.findall(css))


@pytest.mark.parametrize("path", SOURCES)
def test_no_old_hue_is_left_in_the_source(path):
    text = (ROOT / path).read_text().lower()
    left = [h for h in OLD_HUES if f"#{h}" in text or f"0x{h}" in text]
    assert not left, left
    if "tutorial_demos" in path:          # the demo lane names no old look either
        assert "plum" not in text
    # the old rgb() and rgba() triplets too (the 3D kit's glow stops, the
    # extruded words' warm shadow)
    flat = text.replace(" ", "")
    for old in ("92,62,124", "58,38,82", "41,27,54", "34,80,122", "19,48,75", "14,27,41",
                "214,206,190", "214-i*9", "229,164,60"):
        assert old not in flat, old


@pytest.mark.parametrize("path", VIDEO_SOURCES)
def test_no_video_module_uses_the_store_black_or_royal_blue(path):
    """Miro, 2026-10-17: #0A0D14 and #5B8CFF stay on the shop, store and API
    pages; nothing that draws a video, thumbnail or Short uses them (nor
    the store's other 10-12 steps)."""
    text = (ROOT / path).read_text().lower()
    flat = text.replace(" ", "")
    left = [h for h in STORE_HUES if f"#{h}" in text or f"0x{h}" in text]
    assert not left, left
    for triplet in ("10,13,20", "91,140,255", "27,35,52"):
        assert f"rgba({triplet}" not in flat and f"rgb({triplet}" not in flat


def test_the_3d_kit_draws_only_palette_colours():
    """kit.js never goes through the slide renderer, so read its literals."""
    js = (ROOT / "tools/tutorial3d/kit.js").read_text()
    ok = {c.lower().lstrip("#") for c in design.PALETTE} | {"ffffff", "fff", "000000"}
    found = {m.lower() for m in re.findall(r"(?:0x|#)([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b", js)}
    assert found - ok == set(), found - ok
    rgb = {",".join(map(str, t)) for t in _rgb_tokens()}
    triplets = {",".join(m) for m in re.findall(r"rgba?\((\d+),\s*(\d+),\s*(\d+)", js)}
    assert triplets <= rgb, triplets - rgb
    assert "Cabinet" not in js and " Atk'" not in js


def _recorder():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_academy_demo_recorder", ROOT / "tools/tutorial_demos/record.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("name", ["OVERLAY", "TERMINAL", "EDITOR"])
def test_the_demo_recorder_paints_only_palette_colours(name):
    """The ring and cursor drawn over every recording, and the home-made
    terminal and editor pages: the video tokens, nothing else. (Real
    third-party UIs under the ring keep their own colours.)"""
    rec = _recorder()
    page = getattr(rec, name)
    assert not rec.TOKEN.search(page)
    doc = page.replace(fonts_css(), "")
    bad = {h for h in HEX.findall(doc) if h.lower() not in _allowed()}
    rgb = _rgb_tokens()
    bad |= {",".join(m) for m in RGBA.findall(doc) if tuple(map(int, m)) not in rgb}
    assert not bad, bad
    if name == "OVERLAY":
        assert f"border:3px solid {design.BLUE}" in page
    else:
        assert design.GRID in page and design.NAVY_RAISED in page
        assert '"Atk"' not in doc and "SF Mono" not in doc
    if name == "EDITOR":
        # monospace for the code only: the tab, path and toast are in Plex Sans
        assert doc.count('"IBM Plex Sans"') == 3 and f"color:{design.ON_BLUE}" in page


def test_working_slides_sit_on_the_raised_sheet_and_the_rest_on_the_scrim():
    by_kind = {}
    for s, sc in SCENES:
        by_kind.setdefault(sc.kind, (s, sc))
    for kind, (s, sc) in by_kind.items():
        if kind == "demo":
            continue
        doc = design.slide(sc, 0, mark=s.mark, chapter=0, chapters=2)
        sheet = kind in design.SHEET_KINDS
        assert ('<body class="sheet">' in doc) is sheet, kind
        assert ('class="sheet"></div>' in doc) is sheet and ('class="scrim"' in doc) is not sheet
    assert set(design.SHEET_KINDS) & set(by_kind)
    assert set(by_kind) - set(design.SHEET_KINDS) - {"demo"}
    # the sheet is navy-raised and gridded with a navy-line edge; beside it
    # the rail is solid navy, so the watermark never changes surface
    assert f"background-color:{design.NAVY_RAISED};{design.GRID}" in design.SHEET_CSS
    # the sheet's box is the div's alone: <body class="sheet"> must not take
    # its left offset (until 2026-10-17 it did, moving the whole page right)
    assert "div.sheet{" in design.SHEET_CSS and "\n.sheet{" not in design.SHEET_CSS
    assert f"border-right:2px solid {design.NAVY_LINE}" in design.SHEET_CSS
    assert f"body.sheet .rail{{background:{design.NAVY}" in design.SHEET_CSS


def test_cards_are_navy_raised_on_a_navy_line_hairline():
    for rule in (".node.on{", ".pl.on{", ".bubble{", ".opts li{", ".store{"):
        css = design.BASE[design.BASE.index(rule):design.BASE.index("}", design.BASE.index(rule))]
        assert design.NAVY_RAISED in css and design.NAVY_LINE in css, rule
    assert "background:#000" not in design.BASE    # never a pure black frame


def test_the_right_quiz_answer_is_neutral_blue_with_its_tick():
    """A state carries a glyph as well as its colour: the right answer is
    blue with a check, its on-blue type readable on the fill. Miro,
    2026-10-18: green is only for a verified step, never a quiz answer."""
    assert f"body.sheet .opts li.right{{background:{design.BLUE}}}" in design.SHEET_CSS
    assert f".opts li.right{{background:{design.BLUE};border-color:{design.BLUE};" \
           f"color:{design.ON_BLUE}}}" in design.BASE
    quiz = next(sc for _s, sc in SCENES if sc.kind == "quiz" and sc.answer >= 0)
    doc = design.slide(quiz, 1, mark="m", chapter=0, chapters=1)
    assert '<li class="right"><b>✓</b>' in doc


def test_free_text_on_the_scrim_stops_where_the_scrim_still_guarantees_it():
    assert design.TEXT_RIGHT < design.W * 0.75
    assert f"{design.rgba(design.NAVY, design.SCRIM_TEXT)} " \
           f"{round(design.TEXT_RIGHT / design.W * 100)}%" in design.LAYER_CSS
    assert f"max-width:{design.TEXT_RIGHT - design.MAIN_LEFT}px" in design.BASE


def test_readable_chrome_sits_inside_safe_16_9():
    """The store and the mark sit SAFE in from the edges; the rail's text
    runs in the column SAFE..RAIL_W."""
    css = design.BASE
    store = css[css.index(".store{"):css.index("}", css.index(".store{"))]
    assert f"right:{design.SAFE}px;bottom:{design.SAFE}px" in store
    assert design.WATERMARK_XY == (design.SAFE, design.SAFE)
    rail = css[css.index(".rail{"):css.index("}", css.index(".rail{"))]
    assert f"{design.SAFE}px}}" not in rail and f" {design.SAFE}px" in rail


def test_the_store_watermark_is_the_grid_a0_mark_over_its_wordmark():
    """Miro, 2026-10-16 ("use grid 0 logo only"): the edged tile at full
    strength, the "A" and the words at the baked alpha, the "0" in blue.
    The 10-16 file stays for the store; video moved to its navy twin with
    the combined bump (2026-10-18)."""
    svg = design.WATERMARK_STORE_SVG.read_text()
    assert svg == logo.watermark_svg()               # tools/academy_logo.py wrote it
    assert svg.count(f'opacity="{design.WATERMARK_ALPHA}"') == 2
    assert set(re.findall(r'fill="(#[0-9A-Fa-f]{6})"', svg)) == {
        logo.TILE, logo.LETTER, logo.ZERO}
    assert logo.A_PATH in svg and logo.ZERO_PATH in svg and logo.WORD_PATH in svg
    assert "<text" not in svg                        # the logo's own letterforms
    zero = svg.index(logo.ZERO_PATH)                 # the 0 sits outside the faded group
    assert svg.rfind("</g>", 0, zero) > svg.rfind("<g opacity", 0, zero)


def test_the_video_watermark_is_the_grid_a0_mark_in_navy_and_blue():
    """In video the mark takes navy for black and the video blue for royal
    blue; same paths, same alpha, same 104x64."""
    import struct
    svg = design.WATERMARK_VIDEO_SVG.read_text()
    assert svg == logo.watermark_svg(video=True)
    assert set(re.findall(r'fill="(#[0-9A-Fa-f]{6})"', svg)) == {
        vt.NAVY, vt.PAPER, vt.BLUE}
    assert set(re.findall(r'stroke="(#[0-9A-Fa-f]{6})"', svg)) == {vt.NAVY_LINE}
    assert logo.A_PATH in svg and logo.ZERO_PATH in svg and logo.WORD_PATH in svg
    assert svg.count(f'opacity="{design.WATERMARK_ALPHA}"') == 2
    head = design.WATERMARK_VIDEO.read_bytes()[:24]
    assert struct.unpack(">II", head[16:24]) == (logo.WATERMARK_W, logo.WATERMARK_H)
    assert not [h for h in STORE_HUES if h in svg.lower()]


# ------------------------------------------------------------ the logo

LOGO_COLOURS = {MIROS["black"], MIROS["text"], MIROS["blue"], MIROS["surfaces"][2]}
# Miro's pick; untracked, so only checked where it is on disk.
PICKED = ROOT / "brand/logos-2026-10-11/atlas-academy/3-grid-a0/lockup.svg"


def _logo_docs() -> dict[str, str]:
    from atlas.modules.academy import site
    return {"mark": logo.mark_svg(dark=False), "mark-dark": logo.mark_svg(dark=True),
            "lockup": logo.lockup_svg(dark=False), "lockup-dark": logo.lockup_svg(dark=True),
            "watermark": logo.watermark_svg(), "avatar": logo.icon_svg(800),
            "app icon": logo.icon_svg(180), "header": site.BRAND_SVG,
            "icon.svg": (ROOT / "atlas/modules/academy/assets/icon.svg").read_text()}


def test_the_store_logo_keeps_miros_10_12_colours():
    """The store and app-icon outputs keep black and royal blue (the video
    system of 2026-10-17 is for videos only)."""
    assert (logo.TILE, logo.LETTER, logo.ZERO, logo.EDGE) == (
        MIROS["black"], MIROS["text"], MIROS["blue"], MIROS["surfaces"][2])
    assert logo.WORDS_LIGHT == MIROS["black"] and logo.WORDS_DARK == MIROS["text"]
    for name, doc in _logo_docs().items():
        used = {c.upper() for c in re.findall(r'(?:fill|stroke)="(#[0-9A-Fa-f]{6})"', doc)}
        assert used and used <= LOGO_COLOURS, (name, used - LOGO_COLOURS)
        assert not [h for h in OLD_HUES if h in doc.lower()], name
    assert logo.VIDEO_COLOURS == (vt.NAVY, vt.PAPER, vt.BLUE, vt.NAVY_LINE)


def test_every_logo_file_draws_the_same_paths_and_only_dark_ones_an_edge():
    for name, doc in _logo_docs().items():
        assert logo.A_PATH in doc and logo.ZERO_PATH in doc, name
        assert (f'stroke="{logo.EDGE}"' in doc) is (name not in ("mark", "lockup")), name
        if name.startswith("lockup"):
            words = logo.WORDS_DARK if name.endswith("dark") else logo.WORDS_LIGHT
            assert f'<path d="{logo.WORD_PATH}" fill="{words}"/>' in doc
            assert f'<path d="{logo.TAG_PATH}" fill="{words}"/>' in doc
    if PICKED.is_file():                              # byte for byte the pick
        src = re.findall(r'd="([^"]+)"', PICKED.read_text())
        assert src == [logo.A_PATH, logo.ZERO_PATH, logo.WORD_PATH, logo.TAG_PATH]


def test_the_avatar_tile_stays_inside_youtubes_circle():
    """About 80% of the 800 px square, the rounded corners (and the edge)
    inside the circle YouTube crops to."""
    size = 800
    side = size * 0.78
    rx = 11 / 58 * side
    corner = (side / 2 - rx) * 2 ** 0.5 + rx
    edge = max(logo.EDGE_PX, size / 110)
    assert corner + edge / 2 < size / 2
    doc = logo.icon_svg(size)
    assert f'viewBox="0 0 {size} {size}"' in doc and f"scale({side / 58:.5f})" in doc


def test_the_app_icons_are_the_sizes_they_claim():
    import struct
    for name, n in (("apple-touch-icon.png", 180), ("icon-192.png", 192),
                    ("icon-512.png", 512)):
        head = (ROOT / "atlas/modules/academy/assets" / name).read_bytes()[:24]
        assert head[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">II", head[16:24]) == (n, n), name


def test_the_store_fonts_are_unchanged():
    css = store_fonts_css()
    assert '"Cabinet"' in css and '"Atk"' in css and "Plex" not in css


def test_the_split_words_stay_left_of_the_seam():
    # batch 7: the split's seam is 600 px in; its words start 56 px in
    assert 56 + tt.WORD_BOX["split"] < 600


def test_the_brand_change_renders_drafts_again():
    # 5 was the brand change; 6 (demo crop boxes, 2026-10-13) and 7 (the motion
    # guide, 2026-10-15) keep it re-rendered; 8 is the combined bump of the
    # navy video system (2026-10-18), with the thumbnails, the Short's look
    # and the music bed, so unposted drafts are re-voiced and re-rendered once.
    from atlas.modules.academy import tutorial_music as tm
    from atlas.modules.academy import tutorial_short as tsh
    assert design.DESIGN_VERSION == "8" and tt.THUMB_VERSION == "4"
    assert tsh.LOOK_VERSION == 3 and tm.MUSIC_VERSION == "2"
    assert design.WATERMARK == design.WATERMARK_VIDEO               # the navy mark
    marker = "TODO(" + "video-system bump)"
    for path in sorted((ROOT / "atlas").rglob("*.py")):
        assert marker not in path.read_text(), path                 # every one resolved


# Miro, 2026-10-18: green means a step was verified, and nothing else.
VERIFIED_SELECTORS = ("sm-ok", ".sm-n svg", ".sm-n em", "ck-ok", ".ck-k svg", ".ck-k em",
                      "er-ok", "nd-ok", ".nd-st", ".badge.ok", ".done.ok")


def test_success_green_is_drawn_only_for_a_verified_step():
    """The quiz's right answer and the recap ticks are the neutral blue; every
    CSS rule that paints `success` belongs to a verified-only state (the
    components' checks, the result badge and the end card on ep.verified)."""
    css = "".join(getattr(design, n) for n in dir(design)
                  if n.endswith("CSS") and isinstance(getattr(design, n), str))
    css += design.BASE
    for selector, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
        if vt.SUCCESS in body:
            assert any(v in selector for v in VERIFIED_SELECTORS), selector.strip()
    assert vt.SUCCESS not in design.ICON_TICK and design.BLUE in design.ICON_TICK
    assert f".check li.done .box,.check li.now .box{{border-color:{design.BLUE};" in design.BASE
    pairs = {name: fg for name, fg, *_ in design.TEXT_PAIRS}
    assert pairs["quiz answer right"] == design.ON_BLUE
    assert pairs["checklist tick"] == design.BLUE
    right = next(layers for name, _fg, layers, *_ in design.TEXT_PAIRS
                 if name == "quiz answer right")
    assert right == ((design.BLUE, 1.0),)
    unverified = [name for name, fg, *_ in design.TEXT_PAIRS
                  if fg == vt.SUCCESS and not any(
                      w in name for w in ("checked", "verified", "node ran"))]
    assert unverified == []


def test_the_shorts_and_the_thumbnails_never_paint_success_green():
    """Neither marks a step verified, so neither uses `success` at all: the
    Shorts' right answer is blue text, the split thumbnail's tick blue."""
    from atlas.modules.academy import short_frame as sf
    assert vt.SUCCESS not in sf.CSS
    assert f".right .m,.right .t{{color:{vt.BLUE_TEXT}}}" in sf.CSS
    assert not hasattr(tt, "SUCCESS")
    assert "SUCCESS" not in Path(tt.__file__).read_text()
    assert vt.SUCCESS not in {fg for _n, fg, *_ in tt.TEXT_PAIRS}
    assert {n: fg for n, fg, *_ in tt.TEXT_PAIRS}["split, the tick"] == vt.BLUE_TEXT
