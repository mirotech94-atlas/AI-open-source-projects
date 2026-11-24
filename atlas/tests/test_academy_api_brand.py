"""The API's own pages (access, library, recover, review) wear the store's
brand: black field + royal blue, dark only (2026-10-12 brand system; the
push owner caught the recover page still in the old light palette)."""
from __future__ import annotations

import re

from atlas.api import academy


def _luminance(hex_colour: str) -> float:
    rgb = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _vars() -> dict[str, str]:
    return dict(re.findall(r"--([a-z]+):(#[0-9A-Fa-f]{6})", academy._CSS))


def test_the_field_is_the_brand_black_and_the_accent_royal_blue():
    v = _vars()
    assert v["paper"].upper() == "#0A0D14"
    assert v["ultra"].upper() == "#5B8CFF"
    assert v["ink"].upper() == "#EEF2FA"
    assert "color-scheme:dark" in academy._CSS


def test_no_old_light_palette_is_left():
    for old in ("#f3f4f9", "#2b35c8", "#1c248f", "#62667f", "#1d2150", "#ffb000"):
        assert old.lower() not in academy._CSS.lower()


def test_every_text_pair_passes_wcag_aa():
    v = _vars()
    pairs = [("ink", "paper"), ("ink", "card"), ("soft", "paper"), ("soft", "card"),
             ("ultra", "paper"), ("ultra", "card"), ("off", "paper"),
             ("paper", "ultra"), ("paper", "deep")]
    for fg, bg in pairs:
        assert _contrast(v[fg], v[bg]) >= 4.5, (fg, bg)


def test_the_page_tells_the_phone_its_bar_is_black():
    html = academy._page("t", "<p>x</p>", 200).body.decode()
    assert '<meta name="theme-color" content="#0A0D14">' in html
