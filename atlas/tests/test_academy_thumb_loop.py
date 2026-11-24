"""thumb_loop: a still becomes a silent looping MP4 and WebP; bad input refused."""
import shutil
import subprocess

import pytest

from atlas.modules.academy import thumb_loop

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg")


def _png(path):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    "color=c=0x0A0D14:s=1280x720:d=1", "-frames:v", "1", str(path)],
                   check=True)


def test_animate_writes_silent_mp4_and_webp(tmp_path):
    src = tmp_path / "t.png"
    _png(src)
    mp4, webp = thumb_loop.animate(src, tmp_path / "out")
    assert mp4.stat().st_size > 0 and webp.stat().st_size > 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height",
         "-of", "csv=p=0", str(mp4)], capture_output=True, text=True, check=True).stdout
    assert "audio" not in probe and "640,360" in probe


def test_non_image_is_refused(tmp_path):
    bad = tmp_path / "x.png"
    bad.write_text("#EXTM3U\nhttp://127.0.0.1/secret\n")
    with pytest.raises(ValueError):
        thumb_loop.animate(bad, tmp_path / "out")


def test_missing_source_is_refused(tmp_path):
    with pytest.raises(ValueError):
        thumb_loop.animate(tmp_path / "nope.png", tmp_path / "out")
