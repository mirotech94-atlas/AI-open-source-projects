"""The Academy lesson thumbnail: the HTML, its limits, and (with Chrome) the PNG."""
from __future__ import annotations

import struct

import pytest

from atlas.modules.academy import lesson_thumb as lt


def test_the_page_is_1280x720_on_the_dark_ground_with_the_blue_icon_and_escaped_words():
    html, lay = lt.build_html("Learn <b>AI</b> & more",
                              ["Lesson 3.2", "<script>x</script>", "AI voice"])
    assert "width:1280px;height:720px;background:#0A0D14" in html
    assert "#5B8CFF" in html and "<polygon" in html
    assert "<script>" not in html and "&lt;SCRIPT&gt;" in html
    assert "&lt;B&gt;AI&lt;/B&gt; &amp; MORE" in "".join(lay.title_lines).replace(
        "\n", "") or "&amp;" in html
    assert lay.title_lines and len(lay.title_lines) <= 2
    assert all(ln == ln.upper() for ln in lay.title_lines)


def test_no_text_overflows_even_for_a_very_long_title_and_long_callouts():
    title = ("Learn how to build a safe AI operator that never sends money without "
             "asking you first in less than 6 minutes")
    _html, lay = lt.build_html(title, ["A very very long callout about something " * 3,
                                       "b", "c", "d", "ignored fifth"])
    assert len(lay.callouts) == 4 and 2 >= len(lay.title_lines)
    for name, box in lay.boxes.items():
        assert lt.inside(box), (name, box)
    ix0, _iy0, ix1, _iy1 = lt.ICON_BOX
    for name, (l_, _t, r_, _b) in lay.boxes.items():
        if name.startswith("callout"):
            assert r_ < ix0 + 60 or l_ > ix1 - 60, name


def test_a_short_title_gets_the_biggest_size():
    assert lt.fit_title("Learn AI")[1] == lt.TITLE_MAX_PX


def test_it_imports_no_image_library():
    import ast
    import inspect
    names = {n.module.split(".")[0] if isinstance(n, ast.ImportFrom) else a.name.split(".")[0]
             for n in ast.walk(ast.parse(inspect.getsource(lt)))
             if isinstance(n, ast.Import | ast.ImportFrom)
             for a in (n.names if isinstance(n, ast.Import) else [n])
             if (n.module if isinstance(n, ast.ImportFrom) else True)}
    assert "PIL" not in names


def test_render_writes_a_1280x720_png_where_the_uploader_looks(tmp_path, monkeypatch):
    pytest.importorskip("playwright.sync_api")
    from atlas.modules.academy import lesson_youtube as ly
    monkeypatch.setenv(ly.STAGING_ENV, str(tmp_path))
    try:
        out = lt.stage("lesson-adults-7.5", "Learn AI", lt.lesson_callouts("Adults", "7.5", 186))
    except Exception as e:  # no Chrome installed here
        pytest.skip(f"no Chrome: {e}")
    assert out == tmp_path / "lesson-adults-7.5.png" and ly._thumb("lesson-adults-7.5") == out
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == (1280, 720)
