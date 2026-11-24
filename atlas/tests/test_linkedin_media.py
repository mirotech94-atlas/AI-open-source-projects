"""Generated media for the `image` and `video` post kinds.

No network, no ffmpeg: `content.stills.still` and `content.ffmpeg` are
monkeypatched throughout. What must hold: an image kind always asks for a
square-ish aspect, a failure names its reason rather than raising, a missing
ffmpeg refuses the video kind loudly, and every prompt names the page's
brand and forbids a real person or a fake screenshot/stat.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from atlas.modules.content import ffmpeg as content_ffmpeg
from atlas.modules.content import stills as content_stills
from atlas.modules.linkedin import media


@pytest.fixture(autouse=True)
def _flycheap_as_fixture_page(monkeypatch):
    """These tests drive the page mechanics with FlyCheap as their example
    page. FlyCheap is retired by default (the owner, 2026-10-09); that default is
    pinned in test_linkedin_retired.py, not here."""
    from atlas.modules import linkedin as _li
    monkeypatch.setitem(_li.PAGES, "flycheap", _li.KNOWN_PAGES["flycheap"])



@pytest.fixture(autouse=True)
def media_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "MEDIA_DIR", tmp_path / "media")


def test_image_prompt_names_the_page_brand_and_forbids_fakes():
    p = media.image_prompt("flycheap", "cheap fares this week")
    assert "FlyCheap" in p
    assert "no real person" in p.lower()
    assert "fake screenshot" in p.lower()
    assert "fake statistic" in p.lower() or "fake stat" in p.lower()


def test_image_prompt_names_vault_not_guardian():
    p = media.image_prompt("guardian", "keep your files safe")
    assert "Vault" in p
    assert "Guardian" not in p


def test_video_prompt_is_a_video_variant_of_the_image_prompt():
    p = media.video_prompt("flycheap", "cheap fares")
    assert "video" in p.lower()


async def test_make_image_writes_the_bytes_under_media_dir(monkeypatch):
    async def fake_still(prompt, *, aspect="9:16"):
        assert aspect == media.IMAGE_ASPECT
        return b"PNGDATA", None

    monkeypatch.setattr(content_stills, "still", fake_still)
    path, note = await media.make_image("flycheap", "a prompt")
    assert path is not None
    assert path.startswith(str(media.MEDIA_DIR))
    assert Path(path).read_bytes() == b"PNGDATA"
    assert "still" in note.lower()


async def test_make_image_never_raises_on_a_refusal(monkeypatch):
    async def fake_still(prompt, *, aspect="9:16"):
        return None, "GEMINI_API_KEY not set"

    monkeypatch.setattr(content_stills, "still", fake_still)
    path, note = await media.make_image("flycheap", "a prompt")
    assert path is None
    assert "GEMINI_API_KEY" in note


async def test_make_video_refuses_loudly_without_ffmpeg(monkeypatch):
    monkeypatch.setattr(content_ffmpeg, "missing", lambda: "ffmpeg not installed")
    path, note = await media.make_video("flycheap", "a prompt")
    assert path is None
    assert "ffmpeg not installed" in note


async def test_make_video_stops_on_the_first_failed_still(monkeypatch):
    monkeypatch.setattr(content_ffmpeg, "missing", lambda: None)
    calls = []

    async def fake_still(prompt, *, aspect="9:16"):
        calls.append(prompt)
        return None, "Gemini returned no image"

    monkeypatch.setattr(content_stills, "still", fake_still)
    path, note = await media.make_video("flycheap", "a prompt")
    assert path is None
    assert "Gemini returned no image" in note
    assert len(calls) == 1


async def test_make_dispatches_by_kind():
    async def fake_make_image(page, prompt):
        return "img.png", "ok"

    async def fake_make_video(page, prompt):
        return "vid.mp4", "ok"

    import atlas.modules.linkedin.media as m
    orig_image, orig_video = m.make_image, m.make_video
    m.make_image, m.make_video = fake_make_image, fake_make_video
    try:
        assert (await m.make("image", "flycheap", "hook"))[0] == "img.png"
        assert (await m.make("video", "flycheap", "hook"))[0] == "vid.mp4"
        path, note = await m.make("poll", "flycheap", "hook")
        assert path is None and "poll" in note
    finally:
        m.make_image, m.make_video = orig_image, orig_video
