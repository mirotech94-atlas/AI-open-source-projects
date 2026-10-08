"""The report as a PDF: the same frozen document, rendered by a browser.

The MSP spec is right about two things here. The artifact that matters is
the PDF -- "a branded PDF that lands on the 1st with their logo on it is
worth more to them than any feature in the dashboard" -- and it must be
the SAME document as the share link, not a second template that drifts.
So this renders the HTML Vault already froze to disk, with headless
Chrome, rather than reaching for a PDF library and a parallel layout.

WHY LAZILY, ON FIRST REQUEST, RATHER THAN AT GENERATION

The HTML is written once and never rewritten, so the PDF is a pure function
of a frozen file: rendering it tonight or next year gives the same bytes.
That makes it safe to produce on demand and cache beside the HTML, and it
keeps the scheduled report job independent of Chrome -- a hub without a
browser installed still generates and sends reports, and only the PDF
download says "not available here" instead of the whole pipeline failing on
the 1st of the month.

THE FONTS ARE INLINED, and that is the detail the spec singles out. The
report CSS references `/fonts/*.woff2` by root-relative URL, which resolves
under nginx and does not resolve from a `file://` render. Chrome does not
fail on a missing font; it substitutes one, and a substitution reflows
every page so the PDF no longer matches the web version a client already
opened. Base64-inlining the vendored files makes the render self-contained
and deterministic. The fonts are first-party already (CLAUDE.md, "Design
assets"), so nothing new is being bundled -- just the same bytes, carried.

SIZE IS CAPPED. A noisy site's full work log is a 40 MB attachment that
bounces; the cap turns that into a clear error rather than a silently
undelivered report.
"""
from __future__ import annotations

import base64
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import time

#: Vault's own vendored fonts. The report CSS references them under
#: `/fonts/`, and these are the files nginx serves at that path.
FONT_DIR = pathlib.Path(__file__).resolve().parents[3] / "web" / "public" / "fonts"

#: A PDF above this is an attachment that bounces. 15 MB clears a real
#: report with charts by an order of magnitude; the case it stops is the
#: pathological one.
MAX_PDF_BYTES = 15 * 1024 * 1024

#: Seconds before a stuck render is abandoned. Cold Chrome start plus a
#: dense report is a few seconds; a minute means something is wrong.
TIMEOUT_S = 60

_CANDIDATES = (
    os.environ.get("GUARDIAN_CHROME") or "",
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)


class PdfUnavailable(RuntimeError):
    """No browser to render with. Said plainly rather than swallowed."""


def chrome_binary() -> str | None:
    """The first usable Chrome/Chromium, or None.

    `GUARDIAN_CHROME` wins so a deploy can pin an exact binary -- the spec's
    "pin the Chromium version" -- and the name-based fallbacks make a dev
    machine work without configuration.
    """
    for candidate in _CANDIDATES:
        if not candidate:
            continue
        if os.path.isabs(candidate):
            if os.access(candidate, os.X_OK):
                return candidate
            continue
        found = shutil.which(candidate)
        if found:
            return found
    return None


_FONT_URL = re.compile(r'url\("?/fonts/([A-Za-z0-9_.\-]+\.woff2)"?\)')


def inline_fonts(html: str, font_dir: pathlib.Path = FONT_DIR) -> str:
    """Replace `/fonts/X.woff2` references with base64 data URIs.

    A font file that does not exist is left as-is rather than removed: the
    render then falls back the same way the web version would, and the
    absence is visible in the output rather than silently patched over.
    """
    def swap(match: re.Match) -> str:
        path = font_dir / match.group(1)
        if not path.is_file():
            return match.group(0)
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        return f'url("data:font/woff2;base64,{data}")'
    return _FONT_URL.sub(swap, html)


def render_pdf(html: str, *, binary: str | None = None) -> bytes:
    """Render `html` to PDF bytes with headless Chrome.

    Raises `PdfUnavailable` when there is no browser, and `RuntimeError`
    when the render fails or the result is over `MAX_PDF_BYTES`. Neither
    is caught here: the caller decides whether that is a 503 or a log line,
    and a silent empty PDF would be the worst outcome of all.
    """
    exe = binary or chrome_binary()
    if not exe:
        raise PdfUnavailable("no Chrome or Chromium binary found; set GUARDIAN_CHROME")

    with tempfile.TemporaryDirectory(prefix="guardian-pdf-") as tmp:
        src = pathlib.Path(tmp) / "report.html"
        out = pathlib.Path(tmp) / "report.pdf"
        src.write_text(inline_fonts(html), encoding="utf-8")
        cmd = [
            exe, "--headless=new", "--disable-gpu", "--no-sandbox",
            "--no-first-run", "--no-default-browser-check",
            # A full Chrome, not a stripped Chromium, keeps GCM push
            # registration, sync and component updates running after the
            # page is printed. On macOS that is what kept the process alive
            # past the render: the PDF was on disk in two seconds and the
            # process was still there at sixty. These stop most of it.
            "--disable-background-networking", "--disable-sync",
            "--disable-component-update", "--disable-extensions",
            "--disable-default-apps", "--no-service-autorun",
            f"--user-data-dir={tmp}/profile",
            "--print-to-pdf-no-header",
            f"--print-to-pdf={out}",
            src.as_uri(),
        ]
        data = _run_until_written(cmd, out)

    if len(data) > MAX_PDF_BYTES:
        raise RuntimeError(f"PDF is {len(data):,} bytes, over the {MAX_PDF_BYTES:,} cap")
    if not data.startswith(b"%PDF"):
        raise RuntimeError("PDF render produced something that is not a PDF")
    return data


def _run_until_written(cmd: list[str], out: pathlib.Path) -> bytes:
    """Run Chrome, take the PDF as soon as it is complete, and stop waiting.

    `--print-to-pdf` writes the file and then, with a full Chrome build,
    the process may linger on background services for far longer than the
    render took -- on macOS it did not exit at all inside the timeout while
    the finished PDF sat on disk from the second second. Waiting for the
    process to exit is therefore the wrong signal. The right one is the
    artifact: poll for the file, wait until its size has stopped changing
    across two consecutive polls, then terminate Chrome ourselves.

    Two polls at a stable size, not one: `--print-to-pdf` writes in one go
    in practice, but "in practice" is not a contract, and a half-written
    PDF that happens to start with `%PDF` would pass every later check.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + TIMEOUT_S
    last_size, stable = -1, 0
    try:
        while time.monotonic() < deadline:
            if out.is_file():
                size = out.stat().st_size
                if size > 0 and size == last_size:
                    stable += 1
                    if stable >= 2:
                        break
                else:
                    stable = 0
                last_size = size
            elif proc.poll() is not None:
                # Exited without producing anything: read stderr and stop.
                break
            time.sleep(0.2)
        else:
            raise RuntimeError(f"PDF render timed out after {TIMEOUT_S}s")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    if not out.is_file() or out.stat().st_size == 0:
        err = ""
        try:
            err = (proc.stderr.read() if proc.stderr else "") or ""
        except Exception:
            pass
        tail = err.strip().splitlines()[-3:]
        raise RuntimeError("PDF render produced no output: " + " | ".join(tail))
    return out.read_bytes()


def pdf_path_for(html_path: str | pathlib.Path) -> pathlib.Path:
    """Where a report's PDF is cached: beside its HTML, same stem.

    No new column. The HTML path is already content-addressed (a short id
    in the filename) and never rewritten, so the PDF beside it inherits
    both properties.
    """
    p = pathlib.Path(html_path)
    return p.with_suffix(".pdf")


def ensure_pdf(html_path: str | pathlib.Path) -> pathlib.Path:
    """The cached PDF for a frozen HTML report, rendering it on first use.

    Cached forever because the input never changes. If the HTML on disk
    were ever edited the PDF would be stale -- but editing it would also
    break the content hash printed in its own footer, so that is already a
    detectable event rather than a silent one.
    """
    html_path = pathlib.Path(html_path)
    target = pdf_path_for(html_path)
    if target.is_file() and target.stat().st_size > 0:
        return target
    data = render_pdf(html_path.read_text(encoding="utf-8"))
    tmp = target.with_suffix(".pdf.part")
    tmp.write_bytes(data)
    os.replace(tmp, target)      # atomic: a reader never sees a half-written file
    return target
