"""Academy audit: is every promise the catalog makes backed by a file that opens?

    python3 tools/academy_audit.py --root data/app/academy/files [--write-sidecars]

Read-only by default. For each offer in `atlas/modules/academy/catalog.json` it checks
that the file is on disk (a symlink is refused; a hard link is fine), is not empty, opens
(pdfinfo, zip, ffprobe), carries the page count / member count / lesson length the
catalog states, holds no placeholder text, has its print version, and has no failed
`<lesson>.mp4.gate.json`. A product with no cover, or a cover not on disk, is a miss.
Every line carries the time it was read (invariant 6). Exit 0 = clean, 1 = any miss,
2 = the folder is not there.

`--write-sidecars` writes `<videos-dir>/index.json` (final-plan section 2.5):
`{"read_at": iso, "lessons": {"lesson-1.2.mp4": {"seconds": 312.4}}}`, by temp file +
rename (a new inode: never in place, the stop-gap mp4s are hard-linked elsewhere). It is the
only write, and it is the lead's step E5 on the live tree; never run it there from Part D.

The catalog module is stdlib only, so it is loaded by path and the audit runs on a bare
Python as well as in the project's venv. pdfinfo, pdftotext and ffprobe are injected
(`Probe`), so the unit tests need none of them.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

REPO = Path(__file__).resolve().parent.parent
CATALOG_FILE = REPO / "atlas" / "modules" / "academy" / "catalog.json"
OK, MISS = "ok", "MISS"

_RANGE = re.compile(r"(\d+(?:\.\d+)?) to (\d+(?:\.\d+)?) min")
_PAGES = re.compile(r"(\d+) pages")
_NOTES = re.compile(r"(\d+) notes")
_COUNT = re.compile(r"^(\d+)\b")
_WORDS = re.compile(r"[a-z0-9]+")

PLACEHOLDERS = (
    re.compile(r"lorem ipsum", re.IGNORECASE),
    re.compile(r"\b(?:TODO|TBD|FIXME)\b"),
    re.compile(r"\[(?:insert|placeholder|your [a-z ]{2,30} here)[^\]]*\]", re.IGNORECASE),
    re.compile(r"\{\{\s*[A-Z][A-Z0-9_]{2,}\s*\}\}"),
)


def catalog_module():
    """The catalog module, loaded by path (stdlib only, no package import)."""
    name = "_academy_catalog_file"
    if name in sys.modules:
        return sys.modules[name]
    path = REPO / "atlas" / "modules" / "academy" / "catalog.py"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ----------------------------------------------------------------- probes

class Probe(Protocol):
    def pdf_pages(self, path: Path) -> int | None: ...
    def pdf_text(self, path: Path) -> str: ...
    def video_seconds(self, path: Path) -> float | None: ...


class RealProbe:
    """pdfinfo / pdftotext (poppler) and ffprobe (ffmpeg), found on PATH."""

    def _run(self, cmd: list[str]) -> str | None:
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        return p.stdout if p.returncode == 0 else None

    def pdf_pages(self, path: Path) -> int | None:
        out = self._run(["pdfinfo", str(path)])
        m = re.search(r"^Pages:\s+(\d+)", out or "", re.MULTILINE)
        return int(m.group(1)) if m else None

    def pdf_text(self, path: Path) -> str:
        return self._run(["pdftotext", str(path), "-"]) or ""

    def video_seconds(self, path: Path) -> float | None:
        out = self._run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                         "-of", "csv=p=0", str(path)])
        try:
            secs = float((out or "").strip())
        except ValueError:
            return None
        return secs if secs > 0 else None


# ----------------------------------------------------------------- report

@dataclass(frozen=True)
class Line:
    status: str
    item: str
    detail: str
    read_at: str


@dataclass
class Report:
    lines: list[Line] = field(default_factory=list)
    # videos dir (relative to root, posix) -> {file name: seconds}
    durations: dict[str, dict[str, float]] = field(default_factory=dict)
    read_at: str = ""

    @property
    def ok(self) -> bool:
        return not any(ln.status == MISS for ln in self.lines)


# ----------------------------------------------------------------- helpers

def placeholder_hits(text: str) -> list[str]:
    return [m.group(0) for pat in PLACEHOLDERS for m in pat.finditer(text)][:3]


def measured_range(seconds: list[float]) -> tuple[int, int]:
    """Whole minutes, rounded outward: the real shortest and longest lesson."""
    return math.floor(min(seconds) / 60), math.ceil(max(seconds) / 60)


def range_text(rng: tuple[int, int]) -> str:
    return f"{rng[0]} to {rng[1]} minutes"


def _words(text: str) -> set[str]:
    return {w for w in _WORDS.findall(text.lower()) if len(w) > 2}


def _pick_guide(guides: list, hint: str):
    """The guide inclusion with a page count that this file belongs to, or None."""
    if len(guides) == 1:
        return guides[0]
    scored = sorted(((len(_words(g.label) & _words(hint)), i) for i, g in enumerate(guides)),
                    reverse=True)
    if scored and scored[0][0] >= 2 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
        return guides[scored[0][1]]
    return None


def _walk(root: Path, rel: str) -> tuple[Path, str]:
    """The file under root, or a problem. Any symlink on the way is refused."""
    cur = root
    for part in Path(rel).parts:
        cur = cur / part
        if cur.is_symlink():
            return cur, "symlink refused"
        if not cur.exists():
            return cur, "not on disk"
    if not cur.is_file():
        return cur, "not a file"
    if cur.stat().st_size == 0:
        return cur, "empty file"
    return cur, ""


# ----------------------------------------------------------------- the audit

class _Audit:
    def __init__(self, root: Path, cat: Any, assets: Path, probe: Probe, now: datetime):
        self.root, self.cat, self.assets, self.probe = root, cat, assets, probe
        self.at = now.isoformat()
        self.report = Report(read_at=self.at)
        self._seen: set[str] = set()

    def add(self, status: str, item: str, detail: str) -> None:
        self.report.lines.append(Line(status, item, detail, self.at))

    def miss(self, item: str, detail: str) -> None:
        self.add(MISS, item, detail)

    # ---------------------------------------------------------- products
    def covers(self) -> None:
        for p in self.cat.products:
            item = f"{p.slug} / cover"
            if not p.cover:
                self.miss(item, "no cover set")
            elif not (self.assets / p.cover).is_file():
                self.miss(item, f"cover not on disk: {p.cover}")
            else:
                self.add(OK, item, p.cover)

    # ------------------------------------------------------------ offers
    def offer(self, o: Any) -> None:
        files = list(o.files)
        guides = [i for i in o.inclusions if i.kind == "guide" and _PAGES.search(i.detail)]
        has_pdf = any(f.path.endswith(".pdf") for f in files)
        vids = [i for i in o.inclusions if i.kind == "video"]
        by_dir: dict[str, list] = {}
        for f in files:
            if f.path.endswith(".mp4"):
                by_dir.setdefault(str(Path(f.path).parent), []).append(f)
        for f in files:
            item = f"{o.id} / {f.key}"
            path, problem = _walk(self.root, f.path)
            first = f.path not in self._seen
            self._seen.add(f.path)
            if problem:
                if first:
                    self.miss(item, f"{problem}: {f.path}")
                continue
            if f.path.endswith(".pdf"):
                self.pdf(o, f, item, path, guides, first)
            elif f.path.endswith(".zip"):
                self.zip(o, f, item, path, guides, has_pdf, first)
            elif f.path.endswith(".mp4"):
                grp = by_dir[str(Path(f.path).parent)]
                self.video(f, item, path, grp, vids, first)
            elif first:
                self.add(OK, item, f"present, {path.stat().st_size} bytes")

    # --------------------------------------------------------------- pdf
    def pdf(self, o, f, item, path, guides, first):
        stem = f.path.rsplit("/", 1)[-1][:-4]
        pages = self.probe.pdf_pages(path)
        if first:
            if pages is None:
                self.miss(item, f"pdf does not open: {f.path}")
            else:
                self.add(OK, item, f"opens, {pages} pages")
            self.placeholders(item, self.probe.pdf_text(path) if pages else "")
        promised = self._promised_pages(f.label, guides, f.label + " " + stem)
        if pages is not None and promised is not None and pages != promised:
            self.miss(item, f"{promised} pages promised, file has {pages}")
        if first and not stem.endswith("-print"):
            self.print_version(item, f.path)

    def _promised_pages(self, label: str, guides: list, hint: str) -> int | None:
        m = _PAGES.search(label)
        if m:
            return int(m.group(1))
        g = _pick_guide(guides, hint)
        return int(_PAGES.search(g.detail).group(1)) if g else None

    def print_version(self, item: str, rel: str) -> None:
        twin = rel[:-4] + "-print.pdf"
        path, problem = _walk(self.root, twin)
        if problem:
            self.miss(item, f"print version missing: {twin}" if problem == "not on disk"
                      else f"print version: {problem}: {twin}")
        elif self.probe.pdf_pages(path) is None:
            self.miss(item, f"print version does not open: {twin}")

    def placeholders(self, item: str, text: str) -> None:
        hits = placeholder_hits(text)
        if hits:
            self.miss(item, "placeholder text: " + ", ".join(hits))

    # -------------------------------------------------------------- zip
    def zip(self, o, f, item, path, guides, has_pdf, first):
        try:
            zf = zipfile.ZipFile(path)
            bad = zf.testzip()
            names = [n for n in zf.namelist() if not n.endswith("/")]
        except (zipfile.BadZipFile, OSError):
            if first:
                self.miss(item, f"zip does not open: {f.path}")
            return
        if bad:
            self.miss(item, f"zip member is corrupt: {bad}")
            return
        unsafe = [n for n in names if n.startswith("/") or ".." in Path(n).parts]
        if unsafe:
            self.miss(item, f"zip has an unsafe member path: {unsafe[0]}")
            return
        if first:
            self.add(OK, item, f"opens, {len(names)} files")
        self.zip_members(o, item, names)
        pdfs = [n for n in names if n.lower().endswith(".pdf")
                and not n.lower().endswith("-print.pdf")]
        stem_hint = f.path.rsplit("/", 1)[-1] + " " + f.label
        wants_guide = any(_words(g.label) & _words(stem_hint) for g in o.inclusions
                          if g.kind == "guide") or (len(guides) == 1 and not has_pdf)
        if wants_guide and not pdfs:
            self.miss(item, "the guide PDF is not inside the zip")
        with tempfile.TemporaryDirectory() as tmp:
            for name in pdfs:
                self.zip_pdf(item, zf, name, names, guides, Path(tmp), first)
            for name in names:
                if name.lower().endswith((".md", ".txt", ".yml", ".yaml")):
                    self.placeholders(f"{item} :: {name}",
                                      zf.read(name).decode("utf-8", "replace"))

    def zip_pdf(self, item, zf, name, names, guides, tmp: Path, first: bool):
        twin = name[:-4] + "-print.pdf"
        if first and twin not in names:
            self.miss(item, f"print version missing inside zip: {twin}")
        for n in (name, twin):
            if n not in names:
                continue
            out = tmp / Path(n).name
            out.write_bytes(zf.read(n))
            pages = self.probe.pdf_pages(out)
            if pages is None:
                self.miss(item, f"pdf inside zip does not open: {n}")
                continue
            if n == name:
                self.placeholders(f"{item} :: {n}", self.probe.pdf_text(out))
                promised = self._promised_pages("", guides, Path(n).stem + " " + item)
                if promised is not None and pages != promised:
                    self.miss(item, f"{promised} pages promised, file has {pages}")

    def zip_members(self, o, item: str, names: list[str]) -> None:
        low = [n.lower() for n in names]
        base = [Path(n).name for n in low]
        for inc in o.inclusions:
            if inc.kind not in ("files", "guide"):
                continue
            label = inc.label.lower()
            m = _COUNT.match(inc.detail)
            if "workflow" in label and m:
                got = sum(1 for n in low if n.endswith(".json"))
                if got != int(m.group(1)):
                    self.miss(item, f"{m.group(1)} workflow files promised, zip has {got}")
            if label.startswith("readme") and not any(b.startswith("readme") for b in base):
                self.miss(item, "README promised, not in the zip")
            if "docker-compose" in label and "docker-compose.yml" not in base:
                self.miss(item, "docker-compose.yml promised, not in the zip")
            if label == "claude.md" and "claude.md" not in base:
                self.miss(item, "CLAUDE.md promised, not in the zip")
            if "skill" in label and not any("skill" in n for n in low):
                self.miss(item, "Claude Skill promised, not in the zip")
            n = _NOTES.search(inc.detail)
            if n:
                got = sum(1 for x in low if x.endswith(".md"))
                if got != int(n.group(1)):
                    self.miss(item, f"{n.group(1)} notes promised, zip has {got}")

    # ------------------------------------------------------------ video
    def video(self, f, item, path, group, vids, first):
        secs = self.probe.video_seconds(path)
        if secs is None:
            self.miss(item, f"video does not play: {f.path}")
            return
        if first:
            d = str(Path(f.path).parent)
            self.report.durations.setdefault(d, {})[Path(f.path).name] = round(secs, 1)
            self.add(OK, item, f"plays, {secs / 60:.1f} min")
        gate = path.with_name(path.name + ".gate.json")
        if gate.is_file():
            try:
                g = json.loads(gate.read_text(encoding="utf-8"))
                passed = g.get("pass") is True
                checks = g.get("checks") or {}
            except (ValueError, OSError, AttributeError):
                self.miss(item, f"gate file unreadable: {gate.name}")
            else:
                if not passed:
                    failed = [k for k, v in checks.items() if v is False]
                    self.miss(item, "gate failed" + (f": {', '.join(failed)}" if failed else ""))
        stated = self._stated(group, vids)
        if stated and not stated[0] <= secs / 60 <= stated[1]:
            self.miss(item, f"{secs / 60:.1f} min is outside the stated "
                            f"{stated[0]:g} to {stated[1]:g}")

    def _stated(self, group: list, vids: list) -> tuple[float, float] | None:
        pick = vids[0] if len(vids) == 1 else next(
            (v for v in vids if v.count == len(group)), None)
        m = _RANGE.search(pick.detail) if pick else None
        return (float(m.group(1)), float(m.group(2))) if m else None


def audit(root: Path, cat: Any, *, assets_dir: Path | None = None,
          probe: Probe | None = None, now: datetime | None = None) -> Report:
    a = _Audit(Path(root), cat, assets_dir or REPO / "atlas" / "modules" / "academy" /
               "assets", probe or RealProbe(), now or datetime.now(UTC))
    a.covers()
    for o in cat.offers():
        a.offer(o)
    return a.report


def write_sidecars(root: Path, report: Report) -> list[Path]:
    """One index.json per videos folder (2.5). Temp file + rename: a new inode."""
    written = []
    for rel, lessons in sorted(report.durations.items()):
        folder = Path(root) / rel
        if folder.is_symlink() or not folder.is_dir():
            continue
        body = {"read_at": report.read_at,
                "lessons": {k: {"seconds": lessons[k]} for k in sorted(lessons)}}
        fd, tmp = tempfile.mkstemp(prefix="index.", suffix=".tmp", dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(body, fh, indent=1)
                fh.write("\n")
            os.replace(tmp, folder / "index.json")
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        written.append(folder / "index.json")
    return written


def format_report(report: Report) -> str:
    rows = [(ln.status, ln.item, ln.detail, ln.read_at) for ln in report.lines]
    w = max([len(r[1]) for r in rows] + [4])
    out = [f"{'STAT':<4}  {'ITEM':<{w}}  DETAIL  (read at)"]
    out += [f"{s:<4}  {i:<{w}}  {d}  ({t})" for s, i, d, t in rows]
    bad = sum(1 for r in rows if r[0] == MISS)
    out.append(f"{len(rows)} lines, {bad} miss(es), read {report.read_at}")
    return "\n".join(out)


def main(argv: list[str] | None = None, *, cat: Any = None, probe: Probe | None = None,
         now: datetime | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", required=True, help="the private files folder")
    ap.add_argument("--assets", help="folder holding covers/ (default: the repo's)")
    ap.add_argument("--catalog", help="catalog.json (default: the repo's)")
    ap.add_argument("--write-sidecars", action="store_true",
                    help="write <videos>/index.json (the only write)")
    args = ap.parse_args(argv)
    root = Path(args.root)
    if not root.is_dir():
        print(f"audit: {root} is not a folder", file=sys.stderr)
        return 2
    if cat is None:
        cat = catalog_module().load(Path(args.catalog) if args.catalog else CATALOG_FILE)
    report = audit(root, cat, assets_dir=Path(args.assets) if args.assets else None,
                   probe=probe, now=now)
    print(format_report(report))
    if args.write_sidecars:
        for p in write_sidecars(root, report):
            print(f"wrote {p}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
