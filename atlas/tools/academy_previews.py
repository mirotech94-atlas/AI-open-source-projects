"""Build the store's sneak peeks from the real product files.

Miro, 2026-10-12: "add previews for each course video and ebooks and
product so people know what they will buy before they do ... a small demo
preview for each course". Every preview is cut from the file a buyer gets,
never mocked up:

  * video courses: the first 60 seconds of lesson 0.1, re-encoded to 720p;
  * books and guides: a few real pages (the cover, the contents and the
    first lesson), each labelled with its page number out of the total;
  * kits: the zip's real file list and the opening lines of its README.

Run on a machine with ffmpeg, ffprobe and pdftoppm (poppler), pointing at a
copy of the delivered files (the server's data/academy/files):

    python tools/academy_previews.py <files-dir>

It writes atlas/modules/academy/assets/previews/<slug>/... (published with
the store, like the covers) and atlas/modules/academy/previews.json (read by
site.py; not published). Re-run it when a product's files change.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACADEMY = ROOT / "atlas" / "modules" / "academy"
OUT = ACADEMY / "assets" / "previews"
MANIFEST = ACADEMY / "previews.json"
CLIP_SECONDS = 60
PAGE_WIDTH = 1000

# Per product: what to cut from which file. Paths are relative to the
# files dir; "zip:<archive>!<member>" reads a member inside a zip.
SPEC: dict[str, dict] = {
    "safe-ai-operator": {
        "videos": [("safe-ai-operator/videos/lesson-0.1.mp4", "Lesson 0.1: The week-one failure")],
        "books": [("safe-ai-operator/build-a-safe-ai-operator.pdf", "The guide", (1, 2, 4, 5))],
        "kit": ("safe-ai-operator/safe-operator-starter.zip", "The starter agent (zip)",
                "safe-operator/README.md", 21),
    },
    "30-business-lessons": {
        "books": [("30-business-lessons/30-business-lessons.pdf", "The ebook", (1, 3, 4, 5))],
    },
    "claude-obsidian-vault-kit": {
        "kit": ("vault-kit/claude-obsidian-vault-kit.zip", "The vault (zip)",
                "Vault Kit/README.md", 11),
    },
    "ai-for-every-age": {
        "videos": [("ai-for-every-age/videos-adults/lesson-0.1.mp4",
                    "Everyday AI for Adults, lesson 0.1: How this course works"),
                   ("ai-for-every-age/videos-60plus/lesson-0.1.mp4",
                    "Safe and Simple AI 60+, lesson 0.1: How this book works")],
        "books": [(("zip:ai-for-every-age/everyday-ai-for-adults.zip!Everyday AI for Adults/"
                    "Everyday AI for Adults.pdf"), "Everyday AI for Adults", (1, 2, 5)),
                  ("ai-for-every-age/safe-and-simple-ai-60plus.pdf",
                   "Safe and Simple AI 60+ (large print)", (1, 2, 5))],
    },
    "local-business-automation-pack": {
        "books": [(("zip:local-business-automation-pack/local-business-automation-pack.zip!"
                    "Local Business Automation Pack/Local Business Automation Pack.pdf"),
                   "The guide", (1, 2, 3, 5))],
        "kit": ("local-business-automation-pack/local-business-automation-pack.zip",
                "The pack (zip)", "Local Business Automation Pack/workflows/README.md", 5),
    },
    "family-ai-scam-defence-workbook": {
        "books": [("family-ai-scam-defence-workbook/family-ai-scam-defence-workbook.pdf",
                   "The workbook", (1, 2, 3, 6))],
    },
    "sell-with-ai": {
        "videos": [("sell-with-ai/videos/lesson-0.1.mp4",
                    "Lesson 0.1: What this course is, and what it isn't")],
        "books": [("sell-with-ai/sell-with-ai.pdf", "The ebook edition", (1, 2, 4, 5))],
    },
}


def _run(*cmd: str) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def _source(src: Path, ref: str, tmp: Path) -> Path:
    if not ref.startswith("zip:"):
        return src / ref
    archive, member = ref[4:].split("!", 1)
    with zipfile.ZipFile(src / archive) as z:
        out = tmp / Path(member).name
        out.write_bytes(z.read(member))
    return out


def _video(path: Path, dest: Path, n: int) -> dict:
    clip, poster = dest / f"clip-{n}.mp4", dest / f"clip-{n}.jpg"
    _run("ffmpeg", "-v", "error", "-y", "-ss", "0", "-t", str(CLIP_SECONDS), "-i", str(path),
         "-vf", "scale=1280:-2", "-c:v", "libx264", "-preset", "slow", "-crf", "28",
         "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k",
         "-movflags", "+faststart", str(clip))
    _run("ffmpeg", "-v", "error", "-y", "-ss", "14", "-i", str(path), "-frames:v", "1",
         "-vf", "scale=1280:-2", "-q:v", "4", str(poster))
    full = float(_run("ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "csv=p=0", str(path)).strip())
    return {"src": clip.name, "poster": poster.name, "seconds": CLIP_SECONDS,
            "full_minutes": max(1, round(full / 60))}


def _pages(path: Path, dest: Path, n: int, pages: tuple[int, ...]) -> dict:
    total = int(next(l.split()[1] for l in _run("pdfinfo", str(path)).splitlines()
                     if l.startswith("Pages:")))
    out = []
    for p in pages:
        stem = dest / f"book-{n}-p{p}"
        _run("pdftoppm", "-f", str(p), "-l", str(p), "-singlefile", "-scale-to-x",
             str(PAGE_WIDTH), "-scale-to-y", "-1", "-jpeg", "-jpegopt", "quality=82",
             str(path), str(stem))
        dims = _run("ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                    "-of", "csv=p=0", f"{stem}.jpg").strip().split(",")
        out.append({"src": f"{stem.name}.jpg", "page": p, "w": int(dims[0]), "h": int(dims[1])})
    return {"total": total, "pages": out}


def _kit(path: Path, readme: str, lines: int) -> dict:
    with zipfile.ZipFile(path) as z:
        names = [i.filename for i in z.infolist() if not i.is_dir()]
        sizes = {i.filename: i.file_size for i in z.infolist()}
        text = z.read(readme).decode("utf-8").splitlines()
    excerpt = text[:lines]
    while excerpt and not excerpt[-1].strip():
        excerpt.pop()
    return {"tree": [{"path": n, "bytes": sizes[n]} for n in sorted(names)],
            "readme": readme, "excerpt": excerpt, "readme_lines": len(text)}


def build(src: Path) -> dict:
    manifest: dict[str, dict] = {}
    for slug, spec in SPEC.items():
        dest = OUT / slug
        shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True)
        entry: dict = {}
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            for i, (ref, label) in enumerate(spec.get("videos", []), 1):
                entry.setdefault("videos", []).append(
                    {"label": label, **_video(_source(src, ref, tmp), dest, i)})
            for i, (ref, label, pages) in enumerate(spec.get("books", []), 1):
                entry.setdefault("books", []).append(
                    {"label": label, **_pages(_source(src, ref, tmp), dest, i, pages)})
            if "kit" in spec:
                ref, label, readme, lines = spec["kit"]
                entry["kit"] = {"label": label, **_kit(src / ref, readme, lines)}
        manifest[slug] = entry
        print(slug, sum(f.stat().st_size for f in dest.iterdir()) // 1024, "KB")
    MANIFEST.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
    return manifest


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    build(Path(sys.argv[1]))
