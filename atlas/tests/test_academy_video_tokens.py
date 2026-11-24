"""video_tokens.py mirrors docs/academy-video-system/tokens.json exactly
(Miro's video system, 2026-10-17)."""
from __future__ import annotations

import json
import re

import pytest

from atlas.modules.academy import video_tokens as vt

SPEC = json.loads(vt.TOKENS_JSON.read_text())


def _const(name: str) -> str:
    return name.upper().replace("-", "_")


def test_every_colour_token_has_its_constant():
    tokens = SPEC["color"]["tokens"]
    assert len(tokens) == len(vt.COLORS)
    for t in tokens:
        assert getattr(vt, _const(t["name"])) == t["value"], t["name"]
        assert vt.COLORS[t["name"]] == t["value"]


def test_every_type_style_has_its_constant():
    styles = [(g["family"], s) for g in SPEC["type"]["groups"] for s in g["styles"]]
    assert len(styles) == len(vt.TYPE_STYLES)
    for family, s in styles:
        c = getattr(vt, _const(s["name"]))
        assert (c.name, c.family, f"{c.size}px", f"{c.line}px", c.weight) == (
            s["name"], family, s["fontSize"], s["lineHeight"], s["fontWeight"])
        assert c.tracking == s.get("letterSpacing", "")
    fams = SPEC["type"]["families"]
    assert vt.SANS_FAMILY == fams["sans"] and vt.MONO_FAMILY == fams["mono"]


def test_every_spacing_and_radius_token_has_its_constant():
    for group in ("spacing", "radius"):
        for t in SPEC[group]["tokens"]:
            assert f"{getattr(vt, _const(t['name']))}px" == t["value"], t["name"]


@pytest.mark.parametrize("t", SPEC["duration"]["tokens"], ids=lambda t: t["name"])
def test_every_duration_has_its_ms_and_frame_count(t):
    ms = getattr(vt, _const(t["name"]) + "_MS")
    assert f"{ms}ms" == t["value"]
    n = getattr(vt, _const(t["name"]) + "_FRAMES")
    assert n == round(ms * vt.FPS / 1000)
    said = re.match(r"(\d+) frames", t["usage"])
    if said:                                  # the note's own count, where it gives one
        assert n == int(said.group(1))


def test_every_easing_has_its_constant():
    for t in SPEC["easing"]["tokens"]:
        assert getattr(vt, _const(t["name"])) == t["value"]


def test_the_store_colours_are_not_video_tokens():
    store = {"#0a0d14", "#5b8cff"}            # the shop, store and API pages (10-12)
    assert not store & {c.lower() for c in vt.COLORS.values()}


# ------------------------------------------------------------------ fonts

def test_the_video_fonts_embed_plex_in_the_token_weights_only():
    import base64
    css = vt.fonts_css()
    faces = re.findall(r'@font-face\{font-family:"(IBM Plex (?:Sans|Mono))";'
                       r'src:url\("data:font/woff2;base64,([^"]+)"\) format\("woff2"\);'
                       r'font-weight:(\d+)', css)
    got = sorted((fam, int(w)) for fam, _, w in faces)
    assert got == sorted([("IBM Plex Sans", w) for w in vt.WEIGHTS_SANS]
                         + [("IBM Plex Mono", w) for w in vt.WEIGHTS_MONO])
    for _fam, data, _w in faces:
        assert base64.b64decode(data)[:4] == b"wOF2"
    for style in vt.TYPE_STYLES:
        assert style.weight in (vt.WEIGHTS_SANS if style.family == "sans" else vt.WEIGHTS_MONO)


def test_the_plex_files_are_licensed_and_sourced():
    d = vt.FONTS_DIR
    readme = (d / "README.md").read_text()
    assert "SIL Open Font License" in (d / "OFL-plex.txt").read_text()
    assert "Atkinson" in (d / "OFL.txt").read_text()      # the store's licence stays
    for f in [*d.glob("plex-*.woff2"), *vt.LIBASS_FONTS_DIR.glob("*.ttf")]:
        import hashlib
        assert hashlib.sha256(f.read_bytes()).hexdigest() in readme, f.name
    assert "1.1.0" in readme and "2.5.0" in readme


def test_the_store_fonts_stay_cabinet_and_atkinson():
    from atlas.modules.academy.reel_design import fonts_css
    assert "Plex" not in fonts_css() and '"Cabinet"' in fonts_css()
