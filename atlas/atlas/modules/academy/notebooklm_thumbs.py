"""Academy thumbnails made in Google NotebookLM (a Studio Infographic).

the owner, 2026-10-20: a creative thumbnail for every video, made in NotebookLM
by the Mac runner and dropped the way the videos are.

  * A new lesson: the lesson drop may carry `thumb.png`. The ingest
    (notebooklm._install) normalises it (`normalise`) and uses it instead of
    the drawn navy one; a missing image keeps the drawn one, and a bad one
    falls back to it and says so in the evidence, never a refusal.
  * An already-posted lesson: root()/notebooklm-inbox/thumbs/<series>-<NN>/
    holds `thumb.png`, then `meta.json` LAST ({"series", "episode",
    "made_at", "notebook_url"}). The daily tick (apply_pending) puts each
    one on the posted video through the executor, as
    `academy.apply_notebooklm_thumbnail` (Tier 1, catalogued in
    academy/__init__.py; the shape of academy.refresh_video_metadata:
    routine and reversible). Its dry run checks the image and finds the
    video id; its run sets the thumbnail, then keeps the episode
    directory's thumbnail as the one now live (so a re-upload matches) and
    the one it replaced in thumbs/.replaced/; its rollback sets that one
    again and puts it back. Applied drops go to thumbs/.applied/, wrong ones
    to thumbs/refused/ with reason.txt. A 403 (the channel may not set
    custom thumbnails: it is the channel's, not the image's) holds the
    whole inbox with thumbs/hold.txt: one Slack line, then no pass calls
    YouTube until HOLD_DAYS have passed; then one drop is tried, and a
    second 403 holds again without a second line.
  * `python -m atlas.modules.academy.notebooklm next-thumbs [--limit N]`
    lists the public lessons whose thumbnail is not NotebookLM-made yet
    (no MARKER in the episode directory), newest first.

  * A frame of the lesson's own video (2026-10-20, video_frames.py): at
    ingest when the drop carries no thumb.png (frame_thumbnail), and for
    posted lessons still on the drawn card by frame_backfill (the daily
    tick, FRAME_PER_PASS a day, or `frame-thumbs`), which drops thumb.png
    and meta.json with "source": "video-frame" into the thumbs inbox for
    the same Tier 1 apply. The MARKER then carries the frame's dHash, which
    keeps every lesson's frame THRESHOLD bits from every other's. Order:
    a drop's infographic, else the video frame, else the drawn card; a
    lesson with any MARKER is never redone by either lane.

Every image is untrusted bytes (invariant 4): a plain file, at most
MAX_INPUT_BYTES, a PNG or JPEG by its first bytes, decoded only by ffmpeg
with that image demuxer forced, its probed size inside MIN_SIDE..MAX_SIDE;
the result is a fresh 1280x720 file (scaled and cropped to fill, never
stretched) under tutorial_thumbs.MAX_BYTES. The meta is checked field by
field and nothing from it reaches a prompt.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ...kernel.contracts import ActionResult
from . import notebooklm as nlm
from . import tutorial_series as ts

log = logging.getLogger("academy.example.notebooklm_thumbs")

ACTION = "academy.apply_notebooklm_thumbnail"
THUMBS = nlm.THUMBS                  # the thumbs inbox, under notebooklm-inbox/
APPLIED = ".applied"                 # an applied drop, kept for the record
REPLACED = ".replaced"               # the thumbnail an apply replaced, for the rollback
THUMB = "thumb.png"                  # what a drop carries
MARKER = nlm.THUMB_MARKER            # in an episode dir: its thumbnail is NotebookLM's
HOLD = "hold.txt"                    # a 403 hold on the channel, in thumbs/
HOLD_DAYS = 7
PER_PASS = 6                         # thumbnails.set is 50 quota units each
TW, TH = 1280, 720
MAX_INPUT_BYTES = 20 * 1024 * 1024
MIN_SIDE, MAX_SIDE = 320, 8192
PNG_MAGIC, JPEG_MAGIC = b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"
DEMUXER = {"png": "png_pipe", "jpeg": "jpeg_pipe"}
CODEC = {"png": "png", "jpeg": "mjpeg"}
JPEG_QUALITY = (2, 4, 7)             # -q:v tried in turn until it fits MAX_BYTES
THUMB_FILES = ("thumb.jpg", "thumb.png", MARKER)
FORBIDDEN = "HTTP 403 "                # _http.explain's words for a 403
# 2026-10-20: a thumbnail made from a frame of the lesson's own video
# (video_frames.py), at ingest or by the backfill (frame_backfill).
FRAME_SOURCE = "video-frame"
SOURCES = ("notebooklm", FRAME_SOURCE)
HEX16 = re.compile(r"^[0-9a-f]{16}$")
FRAME_PER_PASS = 1                   # backfill drops per daily tick (one CPU, 4K source)
FRAME_TRIES_PER_PASS = 3             # lessons tried per tick, failures included
FRAME_FAILED = ".frame-failed"       # in an episode dir: why its frame could not be made
FRAME_RETRY_DAYS = 30                # a lesson whose frames failed is tried again after this
FRAME_ENV_RETRY_DAYS = 3             # ... or this, when it was the box, not the lesson
# Failures of the box (load, Chromium, ffmpeg, disk), not verdicts on the lesson.
FRAME_ENV_WORDS = ("deadline", "could not be drawn", "not installed", "could not be read",
                   "could not be probed", "could not be cut", "was not written", "ffmpeg:")
FRAME_ROLLED_BACK = ".frame-rolled-back"  # an undone NotebookLM thumbnail: never redone
STAGING_STALE = timedelta(hours=1)   # a .frame-* staging folder this old was left behind


def _tut() -> Any:
    from . import tutorials
    return tutorials


def max_bytes() -> int:
    from . import tutorial_thumbs
    return int(tutorial_thumbs.MAX_BYTES)


def inbox() -> Path:
    return nlm.inbox() / THUMBS


def drop_for(series_key: str, episode: int) -> Path:
    return inbox() / nlm.drop_name(series_key, episode)


# ------------------------------------------------------------ the image

def image_kind(head: bytes) -> str | None:
    """"png" or "jpeg" by the file's first bytes, else None."""
    if head.startswith(PNG_MAGIC):
        return "png"
    if head.startswith(JPEG_MAGIC):
        return "jpeg"
    return None


def _head(src: Path) -> tuple[str | None, str | None]:
    """(kind, None) or (None, why): a plain file of a sane size whose first
    bytes say PNG or JPEG. Blocking."""
    if src.is_symlink() or not src.is_file():
        return None, "it is not a plain file"
    size = src.stat().st_size
    if not 0 < size <= MAX_INPUT_BYTES:
        return None, f"it is {size} bytes (1 to {MAX_INPUT_BYTES} taken)"
    with open(src, "rb") as f:
        kind = image_kind(f.read(16))
    if kind is None:
        return None, "it is not a PNG or JPEG image"
    return kind, None


def fill_filter() -> str:
    """Scale to cover 1280x720, then crop the middle: never stretched."""
    return (f"scale={TW}:{TH}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={TW}:{TH},setsar=1")


def encode_argv(src: str, kind: str, out: str, quality: int | None = None) -> list[str]:
    argv = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", DEMUXER[kind],
            "-i", src, "-frames:v", "1", "-vf", fill_filter()]
    if quality is not None:
        argv += ["-q:v", str(quality)]
    return argv + [out]


def probe_argv(src: str, kind: str) -> list[str]:
    return ["ffprobe", "-v", "error", "-f", DEMUXER[kind], "-select_streams", "v:0",
            "-show_entries", "stream=codec_name,width,height", "-of", "json", src]


def _stream(raw: bytes) -> dict[str, Any]:
    try:
        got = json.loads(raw.decode("utf-8", "replace"))
        s = (got.get("streams") or [{}])[0]
    except (ValueError, AttributeError, IndexError):
        return {}
    return s if isinstance(s, dict) else {}


async def _probe_image(path: str, kind: str) -> tuple[dict[str, Any] | None, str | None]:
    from ..content import ffmpeg
    try:
        s = _stream(await ffmpeg.run(probe_argv(path, kind), timeout=30))
    except ffmpeg.FfmpegError as exc:
        return None, f"ffprobe: {exc}"
    try:
        w, h = int(s.get("width") or 0), int(s.get("height") or 0)
    except (TypeError, ValueError):
        w = h = 0
    return {"codec": s.get("codec_name"), "width": w, "height": h}, None


async def normalise(src: Path, out_dir: Path) -> tuple[Path | None, str | None]:
    """The drop's image as the thumbnail YouTube takes: thumb.jpg (and
    thumb.png when it fits MAX_BYTES; written last, so thumb_file picks it)
    in `out_dir`, 1280x720. (the file to upload, None) or (None, why)."""
    from ..content import ffmpeg
    try:
        kind, why = await asyncio.to_thread(_head, src)
    except OSError as exc:
        return None, f"it could not be read ({type(exc).__name__})"
    if kind is None:
        return None, why
    got, why = await _probe_image(str(src), kind)
    if got is None:
        return None, why
    if got["codec"] != CODEC[kind]:
        return None, f"ffprobe reads {str(got['codec'])[:20]!r}, not a {kind}"
    w, h = got["width"], got["height"]
    if not (MIN_SIDE <= w <= MAX_SIDE and MIN_SIDE * 9 // 16 <= h <= MAX_SIDE):
        return None, f"it is {w}x{h}; {MIN_SIDE}-{MAX_SIDE} px a side is taken"
    limit = max_bytes()
    jpg, png = out_dir / "thumb.jpg", out_dir / "thumb.png"
    try:
        for q in JPEG_QUALITY:
            await ffmpeg.run(encode_argv(str(src), kind, str(jpg), q), timeout=60)
            if 0 < await asyncio.to_thread(_size, jpg) <= limit:
                break
        else:
            return None, f"its JPEG is over {limit} bytes at every quality tried"
        await ffmpeg.run(encode_argv(str(src), kind, str(png)), timeout=60)
    except ffmpeg.FfmpegError as exc:
        return None, f"ffmpeg: {exc}"
    out, why = await _probe_image(str(jpg), "jpeg")
    if out is None or (out["width"], out["height"]) != (TW, TH):
        return None, f"the thumbnail did not come out {TW}x{TH} ({why or out})"
    if not 0 < await asyncio.to_thread(_size, png) <= limit:
        await asyncio.to_thread(png.unlink, missing_ok=True)
        return jpg, None
    await asyncio.to_thread(os.utime, png)              # never older than the JPG
    return png, None


def _size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def marker_text(meta: dict[str, Any], frame: dict[str, Any] | None = None) -> str:
    """The marker an episode directory keeps for a thumbnail made from
    NotebookLM: its infographic ("notebooklm") or, only when `frame` is
    given, a frame of its video ("video-frame", with the frame's and the
    thumbnail's dHash, which the uniqueness check reads back). `frame` is
    what this process computed itself; a drop's meta is untrusted and can
    never set the source or plant a hash (2026-10-20 review). Each field
    checked; an unreadable one is left out, never guessed."""
    url = str(meta.get("notebook_url") or "")
    made = str(meta.get("made_at") or "")
    try:
        made = datetime.fromisoformat(made).isoformat(timespec="seconds")
    except ValueError:
        made = ""
    out: dict[str, Any] = {
        "notebook_url": url if nlm.NOTEBOOK_URL.match(url) else "", "made_at": made,
        "applied_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source": SOURCES[0] if frame is None else FRAME_SOURCE}
    if frame is not None:
        for k in ("frame_dhash", "thumb_dhash"):
            v = frame.get(k)
            if isinstance(v, str) and HEX16.match(v):
                out[k] = v
        if (at := _frame_at(frame)) is not None:
            out["frame_at"] = at
    return json.dumps(out, sort_keys=True)


def _frame_at(blob: dict[str, Any]) -> float | None:
    at = blob.get("frame_at")
    if isinstance(at, int | float) and not isinstance(at, bool) and 0 <= at < 86400:
        return round(float(at), 3)
    return None


async def _frame_fields(meta: dict[str, Any], video: Path, image: Path
                        ) -> dict[str, Any] | None:
    """The marker's video-frame fields for a drop that says it is one,
    recomputed here from the episode's own video at the drop's frame_at and
    from the image being set; None (a plain marker) when either cannot be
    read back. Nothing of the drop's own hashes is kept."""
    from . import video_frames as vf
    at = _frame_at(meta)
    if meta.get("source") != FRAME_SOURCE or at is None:
        return None
    if not await asyncio.to_thread(lambda: video.is_file() and not video.is_symlink()):
        return None
    fh = await vf.frame_hash_at(video, at)
    if fh is None:
        return None
    out: dict[str, Any] = {"frame_dhash": vf.hash_hex(fh), "frame_at": at}
    if (th := await vf.image_dhash(image)) is not None:
        out["thumb_dhash"] = vf.hash_hex(th)
    return out


def nlm_made(d: Path) -> bool:
    """Whether the episode directory's thumbnail is NotebookLM's. Blocking."""
    p = d / MARKER
    return p.is_file() and not p.is_symlink()


# ---------------------------------------------------------- the thumbs inbox

def complete_drops() -> list[Path]:
    """Each thumbs drop whose meta.json is there. Blocking."""
    box = inbox()
    if not box.is_dir():
        return []
    return [d for d in sorted(box.iterdir())
            if d.is_dir() and not d.is_symlink() and not d.name.startswith(".")
            and d.name != nlm.REFUSED and (d / nlm.META).is_file()]


def _pending() -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    for d in complete_drops():
        got = nlm.DROP_NAME.match(d.name)
        if got:
            out.add((got.group(1), int(got.group(2))))
    return out


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _refuse(drop: Path, why: str) -> Path:
    dest = inbox() / nlm.REFUSED / f"{drop.name}-{_stamp()}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(drop, dest)
    (dest / "reason.txt").write_text(why + "\n")
    return dest


def hold_path() -> Path:
    return inbox() / HOLD


def held_until(now: datetime | None = None) -> datetime | None:
    """When the channel's 403 hold lapses, while it has not. Blocking."""
    p = hold_path()
    if p.is_symlink() or not p.is_file():
        return None
    until = datetime.fromtimestamp(p.stat().st_mtime, UTC) + timedelta(days=HOLD_DAYS)
    return until if (now or datetime.now(UTC)) < until else None


def meta_refusal(meta: dict[str, Any], series_key: str, episode: int) -> str | None:
    if meta.get("series") != series_key or meta.get("episode") != episode:
        return (f"meta.json names {str(meta.get('series'))[:20]!r} "
                f"#{str(meta.get('episode'))[:6]}, not the folder's {series_key} #{episode}")
    return None


async def _target(series: str, episode: int
                  ) -> tuple[tuple[Any, ts.Episode, Path, dict[str, Any], str] | None,
                             str | None, bool]:
    """(series, episode, drop, meta, video_id), why not, and whether the
    why is a refusal (True) or a hold (False: the record could not be read)."""
    tut = _tut()
    got = tut.resolve(series, episode)
    if isinstance(got, str):
        return None, got, True
    s, ep = got
    drop = drop_for(s.key, ep.number)
    if drop.is_symlink() or not drop.is_dir():
        return None, f"no NotebookLM thumbnail drop at {drop.name}", False
    meta = await asyncio.to_thread(nlm._meta, drop)
    if meta is None:
        return None, f"the drop {drop.name} is not complete (no meta.json yet)", False
    if (why := meta_refusal(meta, s.key, ep.number)) is not None:
        return None, why, True
    try:
        row = await tut.posted(s.key, ep.number)
    except Exception as exc:                            # noqa: BLE001
        return None, f"the posts record could not be read ({type(exc).__name__})", False
    if not row or not row.get("public"):
        return None, f"{s.key} #{ep.number} is not a public lesson on record", True
    vid = str(row.get("video_id") or "")
    if not tut.VIDEO_ID.match(vid):
        return None, f"the posts record has no video id for {s.key} #{ep.number}", True
    return (s, ep, drop, meta, vid), None, False


async def apply_dry(series: str, episode: int) -> ActionResult:
    """Checks the image (ffmpeg into a temp dir) and finds the video id;
    nothing is set or moved. True for a drop the run would refuse, so the
    run can move it into refused/."""
    got, why, refusal = await _target(series, int(episode))
    if got is None:
        return ActionResult(refusal, f"would refuse the thumbnail drop: {why}"
                            if refusal else why or "not ready")
    s, ep, drop, _meta, vid = got
    with tempfile.TemporaryDirectory() as work:
        out, why = await normalise(drop / THUMB, Path(work))
    if out is None:
        return ActionResult(True, f"would refuse the thumbnail drop {drop.name}: "
                                  f"the image: {why}")
    return ActionResult(True, f"would set the NotebookLM thumbnail of {s.key} "
                              f"#{ep.number} on {vid} and keep the current one "
                              "for the rollback")


async def apply(series: str, episode: int) -> ActionResult:
    """Set one NotebookLM thumbnail on its posted lesson (see the module doc)."""
    from ..youtube import api
    tut = _tut()
    got, why, refusal = await _target(series, int(episode))
    name = nlm.drop_name(series, int(episode))
    drop = drop_for(series, int(episode))

    async def refuse(why: str) -> ActionResult:
        try:
            dest = await asyncio.to_thread(_refuse, drop, why)
        except OSError as exc:
            return ActionResult(False, f"held thumbnail {name}: refused ({why}) but not "
                                       f"moved ({type(exc).__name__})")
        await nlm._tell(f"Academy: the NotebookLM thumbnail for {series} #{episode} was "
                        f"refused: {why}. Moved to {nlm.INBOX}/{THUMBS}/{nlm.REFUSED}/"
                        f"{dest.name}.")
        return ActionResult(False, f"refused thumbnail {name}: {why}")

    if got is None:
        if refusal and drop.is_dir():
            return await refuse(why or "refused")
        return ActionResult(False, f"held thumbnail {name}: {why}")
    s, ep, drop, meta, vid = got
    d = tut.episode_dir(s, ep)
    with tempfile.TemporaryDirectory(dir=str(nlm.inbox())) as staged:
        up, why = await normalise(drop / THUMB, Path(staged))
        if up is None:
            return await refuse(f"the image: {why}")
        # Read before the outside-world write: nothing slow between it and
        # the files placed.
        frame = await _frame_fields(meta, d / nlm.VIDEO, Path(up))
        done, why = await api.set_thumbnail(tut.channel(), vid, str(up))
        if done is None:
            why = str(why)
            if FORBIDDEN in why:
                first = not hold_path().is_file()
                await asyncio.to_thread(hold_path().write_text, why[:300] + "\n")
                if first:
                    await nlm._tell(f"Academy: NotebookLM thumbnails are held: YouTube "
                                    f"refused a custom thumbnail on {tut.channel()} "
                                    f"(403, first on {s.key} #{ep.number}). Tried "
                                    f"again in {HOLD_DAYS} days.")
                return ActionResult(False, f"held thumbnail {name}: 403: {why}")
            return ActionResult(False, f"held thumbnail {name}: not set: {why}")

        def place() -> tuple[str, str]:
            d.mkdir(parents=True, exist_ok=True)
            present = [f for f in THUMB_FILES if (d / f).exists() or (d / f).is_symlink()]
            replaced = ""
            if present:
                keep = inbox() / REPLACED / f"{name}-{_stamp()}"
                keep.mkdir(parents=True, exist_ok=True)
                for f in present:
                    os.replace(d / f, keep / f)
                replaced = str(keep)
            for f in ("thumb.jpg", "thumb.png"):         # the PNG last: never older
                if (Path(staged) / f).is_file():
                    shutil.move(str(Path(staged) / f), str(d / f))
            (d / MARKER).write_text(marker_text(meta, frame))
            hold_path().unlink(missing_ok=True)          # it is allowed again
            applied = inbox() / APPLIED / f"{name}-{_stamp()}"
            applied.parent.mkdir(parents=True, exist_ok=True)
            os.replace(drop, applied)
            return replaced, str(applied)
        try:
            replaced, applied = await asyncio.to_thread(place)
        except OSError as exc:
            # It is live; the files on disk could not follow. The rollback
            # (no kept thumbnail) cannot re-set an older one: said plainly.
            log.warning("notebooklm thumb: %s set on %s but not placed (%s)",
                        name, vid, type(exc).__name__)
            return ActionResult(True, f"set the NotebookLM thumbnail on {vid}; the "
                                      f"episode files could not be updated "
                                      f"({type(exc).__name__})",
                                evidence={"video_id": vid, "placed": False},
                                rollback_token=json.dumps(
                                    {"channel": tut.channel(), "video_id": vid,
                                     "dir": str(d), "replaced": "", "applied": ""},
                                    sort_keys=True))
    token = json.dumps({"channel": tut.channel(), "video_id": vid, "dir": str(d),
                        "replaced": replaced, "applied": applied}, sort_keys=True)
    return ActionResult(True, f"set the NotebookLM thumbnail of {s.key} #{ep.number} "
                              f"on {vid}",
                        evidence={"video_id": vid, "dir": str(d), "file": Path(up).name,
                                  "placed": True},
                        rollback_token=token)


def _previous_file(kept: Path) -> Path | None:
    """The thumbnail an apply replaced, as thumb_file would have picked it."""
    png, jpg = kept / "thumb.png", kept / "thumb.jpg"
    if png.is_file() and not (jpg.is_file()
                              and png.stat().st_mtime < jpg.stat().st_mtime):
        return png
    return jpg if jpg.is_file() else None


async def unapply(token: str) -> ActionResult:
    """Set the thumbnail the apply replaced again, then put the episode
    files back. The applied drop stays in .applied/ and FRAME_ROLLED_BACK
    is left in the episode directory: an undone thumbnail is not tried again
    by itself, by the inbox or by the frame backfill."""
    from ..youtube import api
    tut = _tut()
    try:
        undo = json.loads(token)
        ch, vid, d = str(undo["channel"]), str(undo["video_id"]), Path(undo["dir"])
        replaced = str(undo.get("replaced") or "")
    except (ValueError, KeyError, TypeError, AttributeError):
        return ActionResult(False, "unreadable rollback token")
    if (ch not in api.CHANNELS or not tut.VIDEO_ID.match(vid)
            or d.parent.resolve() != (tut.root() / "episodes").resolve()
            or (replaced and Path(replaced).parent.resolve()
                != (inbox() / REPLACED).resolve())):
        return ActionResult(False, "not a NotebookLM thumbnail apply; left alone")
    kept = Path(replaced) if replaced else None
    prev = await asyncio.to_thread(_previous_file, kept) if kept else None
    if prev is None:
        return ActionResult(False, f"{vid}: no previous thumbnail was kept; the "
                                   "NotebookLM one stays")
    done, why = await api.set_thumbnail(ch, vid, str(prev))
    if done is None:
        return ActionResult(False, f"{vid}: the previous thumbnail was not set again: {why}")

    def back(kept: Path) -> None:
        # First: should a move below fail, the backfill must still never
        # re-make and re-set a frame thumbnail -- the undo undone.
        (d / FRAME_ROLLED_BACK).write_text(json.dumps(
            {"at": datetime.now(UTC).isoformat(timespec="seconds"), "video_id": vid}))
        for f in THUMB_FILES:
            (d / f).unlink(missing_ok=True)
        for f in kept.iterdir():
            os.replace(f, d / f.name)
        kept.rmdir()
    try:
        await asyncio.to_thread(back, Path(replaced))
    except OSError as exc:
        return ActionResult(False, f"{vid}: the previous thumbnail is live again; the "
                                   f"episode files were not put back ({type(exc).__name__})")
    return ActionResult(True, f"{vid}: the previous thumbnail is set again and back in "
                              "the episode directory")


# -------------------------------------------------------------------- the tick

async def apply_pending(now: datetime | None = None) -> list[str]:
    """Apply each complete thumbs drop through the executor, at most
    PER_PASS, skipping one under a 403 hold. A folder whose name is no
    episode is refused here. Never raises."""
    from ...kernel.executor import ExecutionRequest, execute
    tut = _tut()
    said: list[str] = []
    try:
        drops = await asyncio.to_thread(complete_drops)
    except OSError as exc:
        return [f"the NotebookLM thumbs inbox could not be read ({type(exc).__name__})"]
    if (until := await asyncio.to_thread(held_until, now)) is not None:
        return [(f"NotebookLM thumbnails held (403) until {until.date().isoformat()}; "
                 f"{len(drops)} drop(s) wait")]
    # After a hold lapses, one try: a second 403 holds again at once.
    cap = 1 if await asyncio.to_thread(hold_path().is_file) else PER_PASS
    tried = 0
    for drop in drops:
        got = nlm.DROP_NAME.match(drop.name)
        found = tut.resolve(got.group(1), int(got.group(2))) if got else "no such name"
        if isinstance(found, str):
            why = f"the folder name {drop.name[:40]!r} is no catalog episode"
            try:
                await asyncio.to_thread(_refuse, drop, why)
            except OSError as exc:
                said.append(f"{drop.name}: {why}; not moved ({type(exc).__name__})")
                continue
            await nlm._tell(f"Academy: a NotebookLM thumbnail drop was refused: {why}.")
            said.append(f"{drop.name}: refused: {why}")
            continue
        if tried >= cap:
            continue                                    # the next pass; strange names still refused
        tried += 1
        s, ep = found
        try:
            done = await execute(ExecutionRequest(
                action=ACTION, params={"series": s.key, "episode": ep.number},
                reason=f"a NotebookLM thumbnail for {s.key} #{ep.number} is in the inbox"))
        except Exception:
            log.exception("notebooklm thumb: the apply of %s raised", drop.name)
            said.append(f"{drop.name}: the apply raised (see the log)")
            continue
        said.append(f"{drop.name}: {done.detail}")
        if await asyncio.to_thread(hold_path().is_file) and not done.ok:
            cap = 0                                     # a 403 now: no more this pass
    return said


async def next_thumbs(limit: int | None = None) -> list[dict[str, Any]]:
    """The public, not withdrawn lessons whose thumbnail is not NotebookLM's
    and with no thumbs drop waiting, newest post first. Raises when the
    record cannot be read: never guessed."""
    from ...kernel.state import fetch_all
    tut = _tut()
    rows = await fetch_all(
        """SELECT series, episode, video_id, max(id) AS last FROM academy_tutorial_posts
            WHERE public AND NOT withdrawn AND video_id IS NOT NULL
            GROUP BY series, episode, video_id ORDER BY last DESC""")
    waiting = await asyncio.to_thread(_pending)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for r in rows:
        key = (str(r["series"]), int(r["episode"]))
        if key in seen or key in waiting:
            continue
        seen.add(key)
        got = tut.resolve(*key)
        if isinstance(got, str):
            continue
        s, ep = got
        if await asyncio.to_thread(nlm_made, tut.episode_dir(s, ep)):
            continue
        out.append({"series": s.key, "episode": ep.number, "title": ep.title,
                    "drop": f"{THUMBS}/{nlm.drop_name(s.key, ep.number)}",
                    "video_id": str(r["video_id"])})
    return out[:limit] if limit is not None else out


# ------------------------------------------- thumbnails from the video itself

def _frame_hash(blob: dict[str, Any] | None) -> int | None:
    v = (blob or {}).get("frame_dhash")
    return int(v, 16) if isinstance(v, str) and HEX16.match(v) else None


def _read_json(p: Path) -> dict[str, Any] | None:
    if p.is_symlink() or not p.is_file():
        return None
    try:
        got = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def known_hashes(skip: Path | None = None, skip_drop: str = "") -> list[int]:
    """Every other lesson's video-frame hash: each episode directory's
    marker and each thumbs drop waiting in the inbox (so two drops of one
    run are held apart too). `skip`/`skip_drop`: the lesson being made.
    Blocking."""
    out: list[int] = []
    eps = _tut().root() / "episodes"
    if eps.is_dir():
        for d in sorted(eps.iterdir()):
            if d.is_symlink() or not d.is_dir() or (skip is not None and d == skip):
                continue
            if (h := _frame_hash(_read_json(d / MARKER))) is not None:
                out.append(h)
    for drop in complete_drops():
        if drop.name != skip_drop and (h := _frame_hash(_read_json(drop / nlm.META))):
            out.append(h)
    return out


async def frame_thumbnail(ep: ts.Episode, video: Path, out_dir: Path, badge: str,
                          skip: Path | None = None, skip_drop: str = ""
                          ) -> tuple[dict[str, Any] | None, str | None]:
    """thumb.jpg then thumb.png into `out_dir`, drawn over the frame of
    `video` that video_frames picks (unique against known_hashes). ({the
    marker's video-frame fields, plus "note" when the frame is not unique
    enough}, None) or (None, why). Never raises."""
    from ..content import ffmpeg
    from . import tutorial_thumbs as thumbs
    from . import video_frames as vf
    tut = _tut()
    try:
        others = await asyncio.to_thread(known_hashes, skip, skip_drop)
    except OSError as exc:
        return None, f"the other lessons' frames could not be read ({type(exc).__name__})"
    png = out_dir / "thumb.png"
    try:
        with tempfile.TemporaryDirectory() as work:
            frame = Path(work) / "frame.jpg"
            best, note, why = await vf.choose(video, others, frame)
            if best is None:
                return None, why
            async with tut.Shooter() as shooter:
                why = await thumbs.render(ep, shooter, str(png), level=ep.level,
                                          badge=badge, work=work, frame=str(frame))
        if why is not None:
            return None, why
        await ffmpeg.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i",
                          str(png), "-q:v", "3", str(out_dir / "thumb.jpg")], timeout=60)
        await asyncio.to_thread(os.utime, png)      # never older than the JPG
    except ffmpeg.FfmpegError as exc:
        return None, f"ffmpeg: {exc}"
    except Exception as exc:                            # noqa: BLE001  (Chromium)
        return None, f"the thumbnail could not be drawn ({type(exc).__name__})"
    th = await vf.image_dhash(png)
    got_meta: dict[str, Any] = {"source": FRAME_SOURCE, "frame_dhash": vf.hash_hex(best.dhash),
                                "frame_at": best.at}
    if th is not None:
        got_meta["thumb_dhash"] = vf.hash_hex(th)
    if note:
        got_meta["note"] = note
    return got_meta, None


def _own_like(path: Path, ref: Path) -> None:
    """Run as root (a hand `docker exec -u 0`): give `path` and what is in
    it the owner of `ref`, so the app user can move the drop. Blocking."""
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return
    st = ref.stat()
    for p in (path, *path.rglob("*")):
        os.chown(p, st.st_uid, st.st_gid)


def _place_drop(staged: Path, dest: Path, meta: dict[str, Any]) -> None:
    """meta.json into the staged folder, then the whole folder renamed into
    the thumbs inbox at once: a drop is never seen without its meta, and the
    meta is written last. Blocking."""
    (staged / nlm.META).write_text(json.dumps(meta, sort_keys=True))
    _own_like(staged, inbox())
    os.replace(staged, dest)


def _failed_recently(d: Path, now: datetime) -> bool:
    """Whether FRAME_FAILED records a failure under FRAME_RETRY_DAYS old,
    FRAME_ENV_RETRY_DAYS for a failure of the box (its file time when its
    own is unreadable). Blocking."""
    p = d / FRAME_FAILED
    if p.is_symlink() or not p.exists():
        return False
    when: datetime | None = None
    rec = _read_json(p) or {}
    why = str(rec.get("why") or "")
    days = (FRAME_ENV_RETRY_DAYS if any(w in why for w in FRAME_ENV_WORDS)
            else FRAME_RETRY_DAYS)
    try:
        when = datetime.fromisoformat(str(rec.get("at") or ""))
    except ValueError:
        pass
    if when is None or when.tzinfo is None:
        try:
            when = datetime.fromtimestamp(p.lstat().st_mtime, UTC)
        except OSError:
            return True
    return now - when < timedelta(days=days)


def _frame_skip(d: Path) -> bool:
    """A lesson the backfill leaves alone: its thumbnail already NotebookLM's
    or a frame, rolled back by hand, or failed lately. Blocking."""
    return (nlm_made(d) or (d / FRAME_ROLLED_BACK).exists()
            or (d / FRAME_ROLLED_BACK).is_symlink()
            or _failed_recently(d, datetime.now(UTC)))


def _record_failure(d: Path, why: str) -> None:
    (d / FRAME_FAILED).unlink(missing_ok=True)
    (d / FRAME_FAILED).write_text(json.dumps(
        {"at": datetime.now(UTC).isoformat(timespec="seconds"), "why": why[:300]}))


def sweep_staging(now: float | None = None) -> list[str]:
    """Remove .frame-* staging folders left in the inbox by a pass that
    died (a restart mid-render), older than STAGING_STALE. Blocking."""
    import time
    box = inbox()
    if not box.is_dir():
        return []
    cut = (time.time() if now is None else now) - STAGING_STALE.total_seconds()
    gone: list[str] = []
    for p in box.iterdir():
        if not p.name.startswith(".frame-") or p.is_symlink() or not p.is_dir():
            continue
        try:
            if p.stat().st_mtime < cut:
                shutil.rmtree(p)
                gone.append(p.name)
        except OSError:
            continue
    return gone


async def frame_candidates() -> list[dict[str, Any]]:
    """The public, not withdrawn NotebookLM lessons (manifest audio
    "notebooklm") whose thumbnail is still the drawn one (no MARKER: never
    an infographic or a frame already), never rolled back
    (FRAME_ROLLED_BACK), not failed in the last FRAME_RETRY_DAYS
    (FRAME_FAILED), with no thumbs drop waiting and their video.mp4 on
    disk, newest post first. Raises when the record cannot be
    read: never guessed."""
    from ...kernel.state import fetch_all
    tut = _tut()
    rows = await fetch_all(
        """SELECT series, episode, video_id, max(id) AS last FROM academy_tutorial_posts
            WHERE public AND NOT withdrawn AND video_id IS NOT NULL
            GROUP BY series, episode, video_id ORDER BY last DESC""")
    waiting = await asyncio.to_thread(_pending)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for r in rows:
        key = (str(r["series"]), int(r["episode"]))
        if key in seen or key in waiting:
            continue
        seen.add(key)
        got = tut.resolve(*key)
        if isinstance(got, str):
            continue
        s, ep = got
        if (s.key, ep.number) in nlm.RERENDER_POSTED:
            # Its render on disk may be the relaunch's, not the live video's:
            # no frame of it goes onto the old video.
            continue
        d = tut.episode_dir(s, ep)
        if await asyncio.to_thread(_frame_skip, d):
            continue
        m = await asyncio.to_thread(tut.read_manifest, d)
        video = d / nlm.VIDEO
        if m is None or m.audio != nlm.AUDIO:
            continue
        if not await asyncio.to_thread(lambda v=video: v.is_file() and not v.is_symlink()):
            continue
        out.append({"series": s.key, "episode": ep.number, "title": ep.title,
                    "drop": f"{THUMBS}/{nlm.drop_name(s.key, ep.number)}",
                    "video_id": str(r["video_id"]), "badge": m.badge})
    return out


async def frame_backfill(limit: int | None = FRAME_PER_PASS) -> list[str]:
    """For up to `limit` lessons of frame_candidates: the thumbnail drawn
    over a frame of its own video, dropped into the thumbs inbox
    (thumb.png, then meta.json with source "video-frame"), for
    apply_pending to set through the executor as
    academy.apply_notebooklm_thumbnail. Calls no YouTube API. A lesson
    whose frame cannot be made is said and left drawn; one whose frame is
    not unique enough is dropped and said. A failure is recorded in the
    episode directory (FRAME_FAILED) so it is not tried again for
    FRAME_RETRY_DAYS; only drops count toward `limit`, and a pass tries at
    most FRAME_TRIES_PER_PASS lessons (or `limit`, when more; every one
    for no limit, a hand run), so one bad
    lesson can neither starve the rest nor run the tick long. Stale
    .frame-* staging folders are swept first. Raises only when the record
    cannot be read."""
    tut = _tut()
    said: list[str] = []
    for gone in await asyncio.to_thread(sweep_staging):
        said.append(f"swept a stale staging folder {gone}")
    todo = await frame_candidates()
    tries_left = len(todo) if limit is None else max(FRAME_TRIES_PER_PASS, limit)
    done = 0
    for row in todo:
        if (limit is not None and done >= limit) or tries_left <= 0:
            break
        got = tut.resolve(row["series"], row["episode"])
        if isinstance(got, str):
            continue
        tries_left -= 1
        s, ep = got
        d = tut.episode_dir(s, ep)
        name = nlm.drop_name(s.key, ep.number)
        dest = drop_for(s.key, ep.number)
        kept = await asyncio.to_thread(_read_json, d / nlm.KEPT_META) or {}
        await asyncio.to_thread(inbox().mkdir, parents=True, exist_ok=True)
        staged = Path(await asyncio.to_thread(
            tempfile.mkdtemp, prefix=f".frame-{name}-", dir=str(inbox())))
        try:
            fields, why = await frame_thumbnail(ep, d / nlm.VIDEO, staged,
                                                str(row.get("badge") or ""), skip=d,
                                                skip_drop=name)
            if fields is None:
                why = str(why or "unknown")
                said.append(f"{name}: no video-frame thumbnail: {why}; the drawn one stays"
                            " (tried again in days, not daily)")
                await asyncio.to_thread(_record_failure, d, why)
                continue
            await asyncio.to_thread((staged / "thumb.jpg").unlink, missing_ok=True)
            url = str(kept.get("notebook_url") or "")
            meta = {"series": s.key, "episode": ep.number,
                    "made_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "notebook_url": url if nlm.NOTEBOOK_URL.match(url) else "",
                    **fields}
            if await asyncio.to_thread(dest.exists):
                said.append(f"{name}: a thumbs drop is already waiting; left alone")
                continue
            await asyncio.to_thread(_place_drop, staged, dest, meta)
            done += 1
            note = f"; {fields['note']}" if fields.get("note") else ""
            said.append(f"{name}: dropped a video-frame thumbnail (frame at "
                        f"{fields['frame_at']:.1f} s){note}")
        except OSError as exc:
            said.append(f"{name}: the drop could not be written ({type(exc).__name__})")
        finally:
            await asyncio.to_thread(shutil.rmtree, staged, True)
    return said
