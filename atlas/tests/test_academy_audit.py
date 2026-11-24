"""tools/academy_audit.py: every promise the catalog makes about a file is backed by a
file that opens (final-plan Part D, D1/D2). Probes (pdfinfo, pdftotext, ffprobe) are
injected, so these run anywhere; one test uses the real tools and skips without them."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import academy_audit as audit

NOW = datetime(2026, 11, 14, 15, 0, 0, tzinfo=UTC)
cat_mod = audit.catalog_module()
File, Inclusion, Offer = cat_mod.File, cat_mod.Inclusion, cat_mod.Offer


class FakeProbe:
    """Pages, text and seconds come from dicts keyed by file name."""

    def __init__(self, pages=None, text=None, seconds=None):
        self.pages = pages or {}
        self.text = text or {}
        self.seconds = seconds or {}

    def pdf_pages(self, path):
        return self.pages.get(Path(path).name, 10)

    def pdf_text(self, path):
        return self.text.get(Path(path).name, "A clean chapter about safe work.")

    def video_seconds(self, path):
        return self.seconds.get(Path(path).name, 300.0)


def offer(oid, files, inclusions=(), product="p"):
    return Offer(id=oid, name=oid, price_usd=10.0, payment_link="", stripe_price_id="",
                 files=tuple(files), product=product, inclusions=tuple(inclusions))


def catalog(*offers, cover="covers/p.jpg"):
    prod = SimpleNamespace(slug="p", cover=cover, offers=tuple(offers))
    return SimpleNamespace(products=(prod,), offers=lambda: tuple(offers))


def pdf(root: Path, rel: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"%PDF-1.4 fake")
    return p


def run(root, cat, probe=None, tmp_path=None, **kw):
    assets = kw.pop("assets_dir", None)
    if assets is None:
        assets = (tmp_path or root.parent) / "assets"
        (assets / "covers").mkdir(parents=True, exist_ok=True)
        (assets / "covers" / "p.jpg").write_bytes(b"jpg")
    return audit.audit(root, cat, assets_dir=assets, probe=probe or FakeProbe(),
                       now=NOW, **kw)


def misses(report):
    return [ln for ln in report.lines if ln.status == audit.MISS]


def said(report, text):
    return [ln for ln in misses(report) if text in ln.detail]


# --------------------------------------------------------------- the happy path

def guide_offer():
    return offer("g", [File("g-pdf", "Guide (PDF, 35 pages)", "p/guide.pdf")])


def test_a_complete_tree_passes_with_a_read_time_on_every_line(tmp_path):
    root = tmp_path / "files"
    pdf(root, "p/guide.pdf")
    pdf(root, "p/guide-print.pdf")
    r = run(root, catalog(guide_offer()), FakeProbe(pages={"guide.pdf": 35}),
            tmp_path=tmp_path)
    assert r.ok and not misses(r)
    assert r.lines and all(ln.read_at == NOW.isoformat() for ln in r.lines)


def test_a_missing_file_is_a_miss(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    r = run(root, catalog(guide_offer()), tmp_path=tmp_path)
    assert not r.ok and said(r, "not on disk")


def test_an_empty_file_is_a_miss(tmp_path):
    root = tmp_path / "files"
    (root / "p").mkdir(parents=True)
    (root / "p" / "guide.pdf").write_bytes(b"")
    r = run(root, catalog(guide_offer()), tmp_path=tmp_path)
    assert said(r, "empty")


def test_a_wrong_page_count_is_a_miss(tmp_path):
    root = tmp_path / "files"
    pdf(root, "p/guide.pdf")
    pdf(root, "p/guide-print.pdf")
    r = run(root, catalog(guide_offer()), FakeProbe(pages={"guide.pdf": 30}),
            tmp_path=tmp_path)
    assert said(r, "35 pages promised, file has 30")


def test_a_pdf_that_does_not_open_is_a_miss(tmp_path):
    root = tmp_path / "files"
    pdf(root, "p/guide.pdf")
    r = run(root, catalog(guide_offer()), FakeProbe(pages={"guide.pdf": None}),
            tmp_path=tmp_path)
    assert said(r, "does not open")


def test_page_count_comes_from_the_one_guide_inclusion_when_the_label_has_none(tmp_path):
    root = tmp_path / "files"
    pdf(root, "p/guide.pdf")
    pdf(root, "p/guide-print.pdf")
    o = offer("g", [File("g-pdf", "Guide (PDF)", "p/guide.pdf")],
              [Inclusion("guide", "Written guide (PDF)", "93 pages, A4")])
    r = run(root, catalog(o), FakeProbe(pages={"guide.pdf": 90}), tmp_path=tmp_path)
    assert said(r, "93 pages promised, file has 90")


def test_placeholder_text_in_a_pdf_is_a_miss(tmp_path):
    root = tmp_path / "files"
    pdf(root, "p/guide.pdf")
    pdf(root, "p/guide-print.pdf")
    bad = {"guide.pdf": "Chapter 2\nLorem ipsum dolor sit amet"}
    r = run(root, catalog(guide_offer()), FakeProbe(pages={"guide.pdf": 35}, text=bad),
            tmp_path=tmp_path)
    assert said(r, "placeholder")


@pytest.mark.parametrize("text", ["TODO: write this", "see [TBD]", "{{CLIENT_NAME}}", "FIXME"])
def test_each_placeholder_pattern_is_caught(text):
    assert audit.placeholder_hits(text)


@pytest.mark.parametrize("text", ["A to-do list for the week", "Todo apps are fine",
                                  "Use the placeholder attribute in your form"])
def test_ordinary_words_are_not_placeholders(text):
    assert not audit.placeholder_hits(text)


# ------------------------------------------------------------------ print version

def test_a_missing_print_pdf_is_a_miss(tmp_path):
    root = tmp_path / "files"
    pdf(root, "p/guide.pdf")
    r = run(root, catalog(guide_offer()), FakeProbe(pages={"guide.pdf": 35}),
            tmp_path=tmp_path)
    assert said(r, "print version missing: p/guide-print.pdf")


def test_a_print_pdf_is_not_itself_asked_for_a_print_version(tmp_path):
    root = tmp_path / "files"
    pdf(root, "p/guide-print.pdf")
    o = offer("g", [File("g-print", "Guide (print version)", "p/guide-print.pdf")])
    r = run(root, catalog(o), tmp_path=tmp_path)
    assert not said(r, "print version missing")


def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


def pack_offer():
    return offer("z", [File("z-zip", "Pack (zip)", "p/pack.zip")],
                 [Inclusion("guide", "Guide (PDF)", "29 pages, US Letter"),
                  Inclusion("files", "n8n workflow files", "4, nine nodes each"),
                  Inclusion("files", "README.md", "Import and test"),
                  Inclusion("files", "docker-compose.yml", "n8n on your computer")])


def good_pack(root):
    return make_zip(root / "p" / "pack.zip", {
        "Pack/Pack.pdf": b"%PDF fake", "Pack/Pack-print.pdf": b"%PDF fake",
        "Pack/workflows/1.json": b"{}", "Pack/workflows/2.json": b"{}",
        "Pack/workflows/3.json": b"{}", "Pack/workflows/4.json": b"{}",
        "Pack/workflows/README.md": b"hi", "Pack/workflows/docker-compose.yml": b"x"})


def test_a_good_zip_passes_with_both_pdf_versions_inside(tmp_path):
    root = tmp_path / "files"
    good_pack(root)
    r = run(root, catalog(pack_offer()), FakeProbe(pages={"Pack.pdf": 29}),
            tmp_path=tmp_path)
    assert r.ok, [(ln.item, ln.detail) for ln in misses(r)]


def test_a_broken_zip_is_a_miss(tmp_path):
    root = tmp_path / "files"
    (root / "p").mkdir(parents=True)
    (root / "p" / "pack.zip").write_bytes(b"not a zip at all")
    r = run(root, catalog(pack_offer()), tmp_path=tmp_path)
    assert said(r, "zip does not open")


def test_zip_members_must_match_the_inclusions(tmp_path):
    root = tmp_path / "files"
    make_zip(root / "p" / "pack.zip", {
        "Pack/Pack.pdf": b"%PDF fake", "Pack/Pack-print.pdf": b"%PDF fake",
        "Pack/workflows/1.json": b"{}", "Pack/workflows/2.json": b"{}",
        "Pack/workflows/3.json": b"{}"})
    r = run(root, catalog(pack_offer()), FakeProbe(pages={"Pack.pdf": 29}),
            tmp_path=tmp_path)
    assert said(r, "4 workflow files promised, zip has 3")
    assert said(r, "README")
    assert said(r, "docker-compose")


def test_a_zip_without_the_print_pdf_inside_is_a_miss(tmp_path):
    root = tmp_path / "files"
    make_zip(root / "p" / "pack.zip", {
        "Pack/Pack.pdf": b"%PDF fake", "Pack/workflows/1.json": b"{}",
        "Pack/workflows/2.json": b"{}", "Pack/workflows/3.json": b"{}",
        "Pack/workflows/4.json": b"{}", "Pack/workflows/README.md": b"x",
        "Pack/workflows/docker-compose.yml": b"x"})
    r = run(root, catalog(pack_offer()), FakeProbe(pages={"Pack.pdf": 29}),
            tmp_path=tmp_path)
    assert said(r, "print version missing inside zip: Pack/Pack-print.pdf")


def test_the_pdf_inside_a_zip_is_page_counted_and_scanned(tmp_path):
    root = tmp_path / "files"
    good_pack(root)
    r = run(root, catalog(pack_offer()), FakeProbe(pages={"Pack.pdf": 20}),
            tmp_path=tmp_path)
    assert said(r, "29 pages promised, file has 20")
    r = run(root, catalog(pack_offer()),
            FakeProbe(pages={"Pack.pdf": 29}, text={"Pack.pdf": "TODO fix"}),
            tmp_path=tmp_path)
    assert said(r, "placeholder")


def test_note_count_inclusion_counts_markdown_members(tmp_path):
    root = tmp_path / "files"
    o = offer("v", [File("v-zip", "Vault (zip)", "p/v.zip")],
              [Inclusion("files", "Vault (zip of Markdown notes)", "10 notes: 4 at top")])
    make_zip(root / "p" / "v.zip", {f"Vault/n{i}.md": b"note" for i in range(9)})
    r = run(root, catalog(o), tmp_path=tmp_path)
    assert said(r, "10 notes promised, zip has 9")


# ------------------------------------------------------------------------ videos

def video_offer(n=2, rng="4 to 8 minutes each"):
    files = [File(f"v{i}", f"Lesson 1.{i}: x (video)", f"p/videos/lesson-1.{i}.mp4")
             for i in range(1, n + 1)]
    return offer("c", files, [Inclusion("video", "Narrated video lessons", rng, n)])


def videos(root, n=2):
    for i in range(1, n + 1):
        pdf(root, f"p/videos/lesson-1.{i}.mp4")


def test_videos_inside_the_stated_range_pass(tmp_path):
    root = tmp_path / "files"
    videos(root)
    r = run(root, catalog(video_offer()), FakeProbe(seconds={
        "lesson-1.1.mp4": 4 * 60.0, "lesson-1.2.mp4": 8 * 60.0}), tmp_path=tmp_path)
    assert r.ok, [(ln.item, ln.detail) for ln in misses(r)]


def test_a_video_outside_the_stated_range_is_a_miss(tmp_path):
    root = tmp_path / "files"
    videos(root)
    r = run(root, catalog(video_offer(rng="5 to 7 minutes each")),
            FakeProbe(seconds={"lesson-1.1.mp4": 4.27 * 60, "lesson-1.2.mp4": 330.0}),
            tmp_path=tmp_path)
    assert said(r, "4.3 min is outside the stated 5 to 7")


def test_a_video_that_does_not_play_is_a_miss(tmp_path):
    root = tmp_path / "files"
    videos(root)
    r = run(root, catalog(video_offer()), FakeProbe(seconds={"lesson-1.1.mp4": None}),
            tmp_path=tmp_path)
    assert said(r, "does not play")


def test_two_courses_in_one_offer_use_their_own_range(tmp_path):
    root = tmp_path / "files"
    for i in (1, 2):
        pdf(root, f"p/a/lesson-1.{i}.mp4")
    pdf(root, "p/b/lesson-2.1.mp4")
    o = offer("both", [File("a1", "Lesson 1.1: a", "p/a/lesson-1.1.mp4"),
                       File("a2", "Lesson 1.2: a", "p/a/lesson-1.2.mp4"),
                       File("b1", "Lesson 2.1: b", "p/b/lesson-2.1.mp4")],
              [Inclusion("video", "Course A", "4 to 8 minutes each", 2),
               Inclusion("video", "Course B", "3 to 7 minutes each", 1)])
    sec = {"lesson-1.1.mp4": 300.0, "lesson-1.2.mp4": 470.0, "lesson-2.1.mp4": 200.0}
    r = run(root, catalog(o), FakeProbe(seconds=sec), tmp_path=tmp_path)
    assert r.ok, [(ln.item, ln.detail) for ln in misses(r)]


def test_a_failed_gate_file_is_a_miss_and_a_missing_one_is_not(tmp_path):
    root = tmp_path / "files"
    videos(root)
    (root / "p/videos/lesson-1.1.mp4.gate.json").write_text(
        json.dumps({"pass": False, "checks": {"freeze": False}, "read_at": "x"}))
    (root / "p/videos/lesson-1.2.mp4.gate.json").write_text(
        json.dumps({"pass": True, "checks": {}, "read_at": "x"}))
    r = run(root, catalog(video_offer()), tmp_path=tmp_path)
    assert [ln.item for ln in said(r, "gate failed")] == ["c / v1"]
    (root / "p/videos/lesson-1.1.mp4.gate.json").unlink()
    assert run(root, catalog(video_offer()), tmp_path=tmp_path).ok


def test_an_unreadable_gate_file_is_a_miss(tmp_path):
    root = tmp_path / "files"
    videos(root)
    (root / "p/videos/lesson-1.1.mp4.gate.json").write_text("{not json")
    r = run(root, catalog(video_offer()), tmp_path=tmp_path)
    assert said(r, "gate file unreadable")


def test_measured_range_text_rounds_outward():
    assert audit.measured_range([213.2, 364.7]) == (3, 7)         # 60+: 3.55 to 6.08
    assert audit.measured_range([256.4, 381.0]) == (4, 7)         # Sell: 4.27 to 6.35
    assert audit.range_text((3, 7)) == "3 to 7 minutes"


# ---------------------------------------------------------------------- symlinks

def test_a_symlink_is_refused(tmp_path):
    root = tmp_path / "files"
    real = pdf(tmp_path, "elsewhere/guide.pdf")
    (root / "p").mkdir(parents=True)
    os.symlink(real, root / "p" / "guide.pdf")
    r = run(root, catalog(guide_offer()), tmp_path=tmp_path)
    assert said(r, "symlink refused")


def test_a_symlinked_folder_is_refused(tmp_path):
    root = tmp_path / "files"
    pdf(tmp_path, "elsewhere/guide.pdf")
    root.mkdir()
    os.symlink(tmp_path / "elsewhere", root / "p")
    r = run(root, catalog(guide_offer()), tmp_path=tmp_path)
    assert said(r, "symlink refused")


def test_a_hard_link_is_fine(tmp_path):
    root = tmp_path / "files"
    videos(root)
    old = pdf(tmp_path, "old/lesson-1.1.mp4")
    (root / "p/videos/lesson-1.1.mp4").unlink()
    os.link(old, root / "p/videos/lesson-1.1.mp4")
    assert os.stat(old).st_nlink == 2
    assert run(root, catalog(video_offer()), tmp_path=tmp_path).ok


# ------------------------------------------------------------------------ covers

def test_a_product_without_a_cover_is_a_miss(tmp_path):
    root = tmp_path / "files"
    pdf(root, "p/guide.pdf")
    pdf(root, "p/guide-print.pdf")
    r = run(root, catalog(guide_offer(), cover=""), FakeProbe(pages={"guide.pdf": 35}),
            tmp_path=tmp_path)
    assert said(r, "no cover set")


def test_a_cover_missing_on_disk_is_a_miss(tmp_path):
    root = tmp_path / "files"
    pdf(root, "p/guide.pdf")
    pdf(root, "p/guide-print.pdf")
    r = run(root, catalog(guide_offer(), cover="covers/gone.jpg"),
            FakeProbe(pages={"guide.pdf": 35}), tmp_path=tmp_path)
    assert said(r, "cover not on disk")


# ---------------------------------------------------------------------- sidecars

def test_sidecars_are_written_only_when_asked(tmp_path):
    root = tmp_path / "files"
    videos(root)
    sec = {"lesson-1.1.mp4": 312.4, "lesson-1.2.mp4": 301.0}
    r = run(root, catalog(video_offer()), FakeProbe(seconds=sec), tmp_path=tmp_path)
    side = root / "p" / "videos" / "index.json"
    assert not side.exists()
    audit.write_sidecars(root, r)
    data = json.loads(side.read_text())
    assert data == {"read_at": NOW.isoformat(),
                    "lessons": {"lesson-1.1.mp4": {"seconds": 312.4},
                                "lesson-1.2.mp4": {"seconds": 301.0}}}
    assert not list(side.parent.glob("*.tmp*"))


def test_a_sidecar_write_replaces_by_rename_never_in_place(tmp_path):
    root = tmp_path / "files"
    videos(root)
    side = root / "p" / "videos" / "index.json"
    side.write_text("{}")
    held = tmp_path / "held.json"
    os.link(side, held)                                      # the old inode stays put
    r = run(root, catalog(video_offer()), tmp_path=tmp_path)
    audit.write_sidecars(root, r)
    assert held.read_text() == "{}"
    assert "lessons" in json.loads(side.read_text())


def test_no_sidecar_for_a_video_that_did_not_play(tmp_path):
    root = tmp_path / "files"
    videos(root)
    r = run(root, catalog(video_offer()),
            FakeProbe(seconds={"lesson-1.1.mp4": None, "lesson-1.2.mp4": 200.0}),
            tmp_path=tmp_path)
    audit.write_sidecars(root, r)
    lessons = json.loads((root / "p/videos/index.json").read_text())["lessons"]
    assert list(lessons) == ["lesson-1.2.mp4"]


# --------------------------------------------------------------------------- CLI

def test_cli_exits_1_on_a_miss_and_0_when_clean(tmp_path, capsys):
    root = tmp_path / "files"
    root.mkdir()
    cpath = tmp_path / "catalog.json"
    cpath.write_text("{}")
    fake = catalog(guide_offer())
    args = ["--root", str(root), "--assets", str(tmp_path / "assets")]
    assert audit.main(args, cat=fake, probe=FakeProbe(), now=NOW) == 1
    out = capsys.readouterr().out
    assert "MISS" in out and NOW.isoformat() in out
    pdf(root, "p/guide.pdf")
    pdf(root, "p/guide-print.pdf")
    (tmp_path / "assets" / "covers").mkdir(parents=True)
    (tmp_path / "assets" / "covers" / "p.jpg").write_bytes(b"j")
    probe = FakeProbe(pages={"guide.pdf": 35})
    assert audit.main(args, cat=fake, probe=probe, now=NOW) == 0


def test_cli_without_the_flag_writes_nothing(tmp_path):
    root = tmp_path / "files"
    videos(root)
    (tmp_path / "assets" / "covers").mkdir(parents=True)
    (tmp_path / "assets" / "covers" / "p.jpg").write_bytes(b"j")
    args = ["--root", str(root), "--assets", str(tmp_path / "assets")]
    audit.main(args, cat=catalog(video_offer()), probe=FakeProbe(), now=NOW)
    assert not (root / "p/videos/index.json").exists()
    audit.main(args + ["--write-sidecars"], cat=catalog(video_offer()),
               probe=FakeProbe(), now=NOW)
    assert (root / "p/videos/index.json").exists()


def test_a_missing_root_is_exit_2(tmp_path):
    assert audit.main(["--root", str(tmp_path / "nope")], cat=catalog(),
                      probe=FakeProbe(), now=NOW) == 2


# --------------------------------------------------------- real tools (Mac only)

@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                    reason="needs ffmpeg and ffprobe (Mac only)")
def test_real_ffprobe_reads_a_clip_length(tmp_path):
    clip = tmp_path / "t.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=64x64:d=2", "-pix_fmt", "yuv420p", str(clip)],
                   check=True)
    assert 1.9 < audit.RealProbe().video_seconds(clip) < 2.2
    (tmp_path / "bad.mp4").write_bytes(b"nope")
    assert audit.RealProbe().video_seconds(tmp_path / "bad.mp4") is None
