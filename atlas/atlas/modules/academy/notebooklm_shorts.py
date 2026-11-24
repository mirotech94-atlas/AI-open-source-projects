"""Academy Shorts rendered by Google NotebookLM, ingested on the server.

the owner, 2026-10-19: a NotebookLM Short may run 60-90 s (the 20-40 s rule stays
for the Shorts ATLAS cuts itself, tutorials.render_teaching_short). A lesson
rendered in NotebookLM has no teaching Short: tutorials._teaching_cut needs
the Fish render's chapters and scenes. This fills that gap.

The handoff, as for the lessons (notebooklm.py), over ssh into the api
container:

    python -m atlas.modules.academy.notebooklm next-shorts [--limit N]   JSON
    python -m atlas.modules.academy.notebooklm short-source <series> <n> the text

The runner copies each render into root()/notebooklm-inbox/shorts/<series>-
<NN>/ as `video.mp4` (and refs/*.txt when it has them), then `meta.json`
LAST, in the lessons' format; source_sha256 is the sha of what
`short-source` printed (the lesson's own source text: there is no tighter
script in the code, and none is invented).

The ingest (`academy.ingest_notebooklm_short`, Tier 1 as the teaching
Short's render: a file on our own disk and a content_media row) checks the
probe (H.264 + AAC, vertical, at least 720x1280, 50-95 s), Whisper, the
numbers, the human-on-camera claim and the 8-word overlap (the lesson's
gates; the welcome-opening gate is the lesson's only). A check that cannot
run HOLDS the drop (invariant 7); a wrong render is REFUSED into
shorts/refused/. A passing render is slowed to ACADEMY_NOTEBOOKLM_PACE (pitch kept; never
past 95 s) and scaled to the Shorts frame (1080x1920, never 4K,
H.264 High, 30 fps, AAC 48 kHz: the clips quality gate's), checked again
by ffprobe, and finished as a content_media row whose draft source is
`academy:tutorial:<series>:<n>:notebooklm`. The drop is kept in
shorts/.ingested/ for the rollback, which withdraws the row and puts the
drop back.

Posting is the teaching Short's: the teaching pass (tutorials.teaching_pass)
posts a finished NotebookLM Short first, through clips.autopublish_promo and
its gates (the AI label, the brand guard, the day's cap, a probed 1080x1920
file); only the length is relaxed, to 50-95 s, and only for a render whose
draft source in the database says it is one (promo.seconds_for).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ...kernel.contracts import ActionResult
from . import notebooklm as nlm
from . import tutorial_series as ts

log = logging.getLogger("academy.example.notebooklm_shorts")

ACTION = "academy.ingest_notebooklm_short"
POST_ACTION = "clips.autopublish_promo"
KIND = "notebooklm"                  # the slug's kind: tutorial:<series>:<n>:notebooklm
INGESTED = ".ingested"               # a drop made into a Short, kept for the rollback
SECONDS = (50.0, 95.0)               # the owner 2026-10-19: 60-90 s, with a little slack
MIN_W, MIN_H = 720, 1280
OUT_W, OUT_H = 1080, 1920            # content.ffmpeg.WIDTH x HEIGHT, the clips gate's
SOURCE = re.compile(r"^academy:(tutorial:([a-z][a-z0-9]*):(\d{1,3}):notebooklm)$")
TRANSCODE_TIMEOUT = 600


def _tut() -> Any:
    from . import tutorials
    return tutorials


def inbox() -> Path:
    return nlm.inbox() / nlm.SHORTS


def drop_for(series_key: str, episode: int) -> Path:
    return inbox() / nlm.drop_name(series_key, episode)


def slug(s: ts.Series, ep: ts.Episode) -> str:
    return f"tutorial:{s.key}:{ep.number}:{KIND}"


def source_text(ep: ts.Episode) -> str:
    """What the runner pastes into NotebookLM for the Short: the lesson's
    own source text. No teaching-cut script exists to tighten it with (the
    cuts are timed from a Fish render), and nothing is written for it."""
    return nlm.source_text(ep)


def source_sha(ep: ts.Episode) -> str:
    return nlm.source_sha(ep)


# ------------------------------------------------------------------ the gates

def probe_refusal(p: dict[str, Any]) -> str | None:
    """The file NotebookLM gives for a Short, before it is made one."""
    if p.get("video_codec") != nlm.VIDEO_CODEC:
        return f"the video is {p.get('video_codec') or 'unreadable'}, not H.264"
    if not p.get("audio"):
        return "the file has no sound"
    if p.get("audio_codec") != nlm.AUDIO_CODEC:
        return f"the sound is {p.get('audio_codec') or 'unreadable'}, not AAC"
    w, h = int(p.get("width") or 0), int(p.get("height") or 0)
    if h <= w:
        return f"the file is {w}x{h}, not vertical"
    if w < MIN_W or h < MIN_H:
        return f"the file is {w}x{h}, under {MIN_W}x{MIN_H}"
    secs = float(p.get("seconds") or 0.0)
    if not SECONDS[0] <= secs <= SECONDS[1]:
        return f"it runs {secs:.0f} s, outside {SECONDS[0]:.0f}-{SECONDS[1]:.0f} s"
    return None


def caption(s: ts.Series, ep: ts.Episode, video_id: str) -> str:
    """The Short's text, TikTok's and YouTube's alike, in the teaching
    Short's shape (tutorials.teaching_caption): a title line, the long
    lesson by its title and address, the series' hashtags with #shorts,
    the synthetic-voice line, then the AI label."""
    tut = _tut()
    x = (ep.title_hook or ep.title).rstrip("?!. ")
    return (f"{x}: the quick explainer.\nFull lesson: {ep.title}\n"
            f"https://youtu.be/{video_id}\n"
            + " ".join(ts.hashtags_for(s.key, ep, short=True))
            + f"\n{tut.VOICE_SHORT}\n{tut.AI_LABEL}")


def caption_refusal(text: str) -> str | None:
    """The metadata gates on the caption: the clips post's own (the AI
    label, no disclosure, the brand guard), a YouTube title in bounds, the
    hashtags with #shorts, the synthetic-voice line and the lesson's link."""
    from ..clips import promo
    from ..content import guard, postiz
    tut = _tut()
    if (why := promo.vet({"media_id": 0, "caption": text})) is not None:
        return why
    title = promo.youtube_title(text)
    if not postiz.YT_TITLE_MIN <= len(title) <= postiz.YT_TITLE_MAX:
        return f"the Short's title is {len(title)} characters"
    if "#shorts" not in text.lower().split():
        return "the caption has no #shorts hashtag"
    if tut.VOICE_SHORT not in text:
        return "the caption does not say the voice is synthetic"
    if not re.search(r"https://youtu\.be/[A-Za-z0-9_-]{11}\b", text):
        return "the caption does not link the long lesson"
    return guard.refusal(text)


# ------------------------------------------------------------------- the drops

def complete_drops() -> list[Path]:
    """Each Short drop whose meta.json is there. Blocking."""
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


def _refuse(drop: Path, why: str) -> Path:
    """Move `drop` to shorts/refused/<name>-<ts>/ with reason.txt. Blocking."""
    at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dest = inbox() / nlm.REFUSED / f"{drop.name}-{at}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(drop, dest)
    (dest / "reason.txt").write_text(why + "\n")
    return dest


def _keep(drop: Path) -> Path:
    """Move a made drop to shorts/.ingested/<name>-<ts>/. Blocking."""
    at = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    dest = inbox() / INGESTED / f"{drop.name}-{at}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(drop, dest)
    return dest


async def already(s: ts.Series, ep: ts.Episode) -> str | None:
    """Why this lesson already has its NotebookLM Short (posted, or made
    and waiting to post), or None. Raises when the record can't be read."""
    from ...kernel.state import fetch_one
    sl = slug(s, ep)
    if await fetch_one("SELECT 1 AS x FROM academy_promo_posts WHERE slug = $1 LIMIT 1",
                       sl):
        return "its NotebookLM Short is already posted"
    row = await fetch_one(
        """SELECT m.id FROM content_media m JOIN content_drafts d ON d.id = m.draft_id
            WHERE d.source = $1 AND m.status = 'done' ORDER BY m.id DESC LIMIT 1""",
        f"academy:{sl}")
    if row:
        return f"its NotebookLM Short is already made (media {row['id']})"
    return None


# -------------------------------------------------------------------- the make

def transcode_argv(src: str, out: str, pace: float = 1.0) -> list[str]:
    """The drop into the Shorts frame the clips gate reads: 1080x1920 (fit
    and padded, never stretched), H.264 High yuv420p at 30 fps, AAC 48 kHz,
    slowed to `pace` (pitch kept; nlm.pace_filters) before the fps filter.
    Never upscaled past 1080x1920: video_qa.encoding_refusal wants exactly
    SHORT_SIZE, and clips.autopublish_promo a probed 1080x1920 file."""
    vpace, apace = nlm.pace_filters(pace)
    vf = (f"{vpace + ',' if vpace else ''}"
          f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=decrease:flags=lanczos,"
          f"pad={OUT_W}:{OUT_H}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps=30")
    return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", src,
            "-vf", vf, *(["-af", apace] if apace else []),
            "-c:v", "libx264", "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-preset", nlm.PRESET, "-crf", "18", "-threads", "0", "-r", "30",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", out]


def short_pace(seconds: float) -> float:
    """The Short's pace: ACADEMY_NOTEBOOKLM_PACE, raised so the slowed Short
    lands nlm.CAP_HEADROOM under SECONDS[1] (the QA gate's ceiling)."""
    return nlm.effective_pace(nlm.pace_setting(), seconds,
                              SECONDS[1] - nlm.CAP_HEADROOM)


async def _make(s: ts.Series, ep: ts.Episode, video: Path, text: str, sl: str,
                pace: float = 1.0) -> ActionResult:
    """The draft row, the media row, the transcode and its ffprobe check,
    then the finished row (render._finish moves the file under DATA_DIR).
    Anything short of a checked file marks the row failed."""
    from ...kernel.state import fetch_one
    from ..content import ffmpeg, render
    from . import video_qa
    if (why := ffmpeg.missing()) is not None:
        return ActionResult(False, why)
    row = await fetch_one(
        """INSERT INTO content_drafts (kind, source, title, body)
           VALUES ($1, $2, $3, $4) RETURNING id""",
        "academy_promo", f"academy:{sl}", f"{ep.title} (NotebookLM Short)", text)
    draft_id = int(row["id"])
    media_id = await render._new_row(draft_id, "academy")
    try:
        with tempfile.TemporaryDirectory(prefix="academy-nlm-short-") as work:
            out = os.path.join(work, "out.mp4")
            await ffmpeg.run(transcode_argv(str(video), out, pace),
                             timeout=TRANSCODE_TIMEOUT)
            got, why = await video_qa.probe(out)
            if got is None:
                return await render._fail(media_id, f"{video_qa.UNREAD} ({why})")
            if (why := video_qa.encoding_refusal(got, size=video_qa.SHORT_SIZE,
                                                 seconds=SECONDS)) is not None:
                return await render._fail(media_id, f"QA: {why}")
            note = (f"NotebookLM Short of {s.key} #{ep.number}, "
                    f"{float(got['seconds']):.0f} s at {pace:.2f}x, scaled to "
                    f"{OUT_W}x{OUT_H}")
            return await render._finish(media_id, draft_id, "academy", out, [note],
                                        captions=False)
    except ffmpeg.FfmpegError as exc:
        return await render._fail(media_id, f"ffmpeg: {exc}")
    except Exception as exc:                            # noqa: BLE001
        return await render._fail(media_id, f"{type(exc).__name__}: {exc}"[:300])


# ------------------------------------------------------------------ the action

async def ingest_dry(series: str, episode: int) -> ActionResult:
    """What the run would take: never Whisper, never a move, no database."""
    tut = _tut()
    got = tut.resolve(series, episode)
    if isinstance(got, str):
        return ActionResult(False, got)
    s, ep = got
    drop = drop_for(s.key, ep.number)
    meta = await asyncio.to_thread(nlm._meta, drop)
    if meta is None:
        return ActionResult(False, f"no complete NotebookLM Short drop at {drop.name}")
    why = nlm.meta_refusal(meta, s.key, ep.number)
    if why is None and meta["source_sha256"] != source_sha(ep):
        why = "stale script"
    if why is not None:
        return ActionResult(True, f"would refuse the Short drop {drop.name} ({why}) into "
                                  f"{nlm.INBOX}/{nlm.SHORTS}/{nlm.REFUSED}/")
    try:
        size = (drop / nlm.VIDEO).stat().st_size
    except OSError:
        size = 0
    return ActionResult(True, f"would ingest the Short drop {drop.name} "
                              f"({size / 1e6:.1f} MB) as {slug(s, ep)} after the probe, "
                              "Whisper and transcript checks")


async def ingest(series: str, episode: int) -> ActionResult:
    """Check one Short drop and make it, hold it or refuse it."""
    tut = _tut()
    got = tut.resolve(series, episode)
    if isinstance(got, str):
        return ActionResult(False, got)
    s, ep = got
    name = nlm.drop_name(s.key, ep.number)
    drop = drop_for(s.key, ep.number)

    async def refuse(why: str) -> ActionResult:
        try:
            dest = await asyncio.to_thread(_refuse, drop, why)
        except OSError as exc:
            return await hold(f"the drop could not be moved to refused/ "
                              f"({type(exc).__name__}); refused: {why}")
        await nlm._tell(f"Academy: the NotebookLM Short for {s.key} #{ep.number} was "
                        f"refused: {why}. Moved to {nlm.INBOX}/{nlm.SHORTS}/"
                        f"{nlm.REFUSED}/{dest.name}.")
        return ActionResult(False, f"refused Short {name}: {why}")

    async def hold(why: str) -> ActionResult:
        await nlm._tell_once(f"notebooklm short hold {name}",
                             f"Academy: the NotebookLM Short for {s.key} #{ep.number} "
                             f"is held in the inbox, not ingested: {why}.")
        return ActionResult(False, f"held Short {name}: {why}")

    if drop.is_symlink() or not drop.is_dir():
        return ActionResult(False, f"no NotebookLM Short drop at {name}")
    meta = await asyncio.to_thread(nlm._meta, drop)
    if meta is None:
        return ActionResult(False, f"the Short drop {name} is not complete "
                                   "(no meta.json yet)")
    if (why := nlm.meta_refusal(meta, s.key, ep.number)) is not None:
        return await refuse(why)
    if not ep.scripted:
        return await refuse("the episode has no script")
    if (why := await asyncio.to_thread(ts.held, s.key, ep)) is not None:
        return await refuse(f"the episode is held: {why}")
    if meta["source_sha256"] != source_sha(ep):
        return await refuse("stale script: the render's source_sha256 is not the sha "
                            "of the episode's source text now; render it again")
    video = drop / nlm.VIDEO
    if video.is_symlink() or not video.is_file():
        return await refuse("video.mp4 is missing or not a plain file")
    try:
        public = await tut._public_video(s.key, ep.number)
        if isinstance(public, str):
            return await refuse(f"{public}; a Short follows its public lesson")
        if (why := await already(s, ep)) is not None:
            return await refuse(why)
    except Exception as exc:                            # noqa: BLE001
        return await hold(f"the posts record could not be read ({type(exc).__name__})")
    vid = public[2]
    refs = await asyncio.to_thread(nlm.reference_files, drop, s.key, ep.number)
    if not refs:
        _, why = nlm.overlap_refusal("", refs)
        return await hold(f"overlap check UNKNOWN: {why}")
    probe, why = await tut._probe(str(video))
    if probe is None:
        return await hold(f"the file could not be probed ({why})")
    if (why := probe_refusal(probe)) is not None:
        return await refuse(why)
    words, why = await nlm.transcribe(video)
    if words is None:
        return await hold(f"the narration could not be transcribed ({why})")
    heard = " ".join(w.text for w in words)
    if (why := nlm.numbers_refusal(heard, source_text(ep))) is not None:
        return await refuse(why)
    if (why := nlm.human_refusal(heard)) is not None:
        return await refuse(why)
    why, unknown = await asyncio.to_thread(nlm.overlap_refusal, heard, refs)
    if unknown is not None:
        return await hold(f"overlap check UNKNOWN: {unknown}")
    if why is not None:
        return await refuse(why)
    text = caption(s, ep, vid)
    if (why := caption_refusal(text)) is not None:
        return await hold(f"its caption fails a gate ({why})")
    sl = slug(s, ep)
    pace = short_pace(float(probe.get("seconds") or 0.0))
    made = await _make(s, ep, video, text, sl, pace)
    if not made.ok:
        return await hold(f"the Short could not be made: {made.detail}")
    media_id = int((made.evidence or {}).get("media_id") or 0)
    try:
        kept = str(await asyncio.to_thread(_keep, drop))
    except OSError as exc:
        # The row is made; the drop stays and the next pass refuses it as
        # already made. The rollback still withdraws the row.
        log.warning("notebooklm short: %s not moved aside (%s)", name, type(exc).__name__)
        kept = ""
    token = json.dumps({"media_id": media_id, "drop": str(drop), "kept": kept},
                       sort_keys=True)
    return ActionResult(True, f"made the NotebookLM Short of {s.key} #{ep.number} "
                              f"(media {media_id}, {sl}); it posts in the teaching "
                              "Short's slot",
                        evidence={"media_id": media_id, "slug": sl, "caption": text,
                                  "youtube_caption": text,
                                  "seconds": float(probe.get("seconds") or 0.0),
                                  "pace": pace, "width": OUT_W, "height": OUT_H},
                        rollback_token=token)


async def uningest(token: str) -> ActionResult:
    """Withdraw the made Short (its row failed, its file gone) and put the
    drop back in the inbox."""
    from . import reel
    try:
        undo = json.loads(token)
        media_id, drop, kept = int(undo["media_id"]), Path(undo["drop"]), str(undo["kept"])
    except (ValueError, KeyError, TypeError):
        return ActionResult(False, "unreadable rollback token")
    if (drop.parent.resolve() != inbox().resolve()
            or (kept and Path(kept).parent.resolve() != (inbox() / INGESTED).resolve())):
        return ActionResult(False, "not a NotebookLM Short ingest; left alone")
    said = await reel.unrender_promo_reel(str(media_id))
    if kept:
        try:
            await asyncio.to_thread(os.replace, kept, drop)
        except OSError as exc:
            return ActionResult(False, f"{said.detail}; the drop could not be put back "
                                       f"({type(exc).__name__})")
    return ActionResult(said.ok, f"{said.detail}; {drop.name} is back in the Shorts inbox"
                        if kept else said.detail)


# -------------------------------------------------------------------- the tick

async def ingest_pending() -> list[str]:
    """Ingest every complete Short drop through the executor, one at a
    time; a folder whose name is no episode is refused here. Never raises."""
    from ...kernel.executor import ExecutionRequest, execute
    tut = _tut()
    said: list[str] = []
    try:
        drops = await asyncio.to_thread(complete_drops)
    except OSError as exc:
        return [f"the NotebookLM Shorts inbox could not be read ({type(exc).__name__})"]
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
            await nlm._tell(f"Academy: a NotebookLM Short drop was refused: {why}.")
            said.append(f"{drop.name}: refused: {why}")
            continue
        s, ep = found
        try:
            done = await execute(ExecutionRequest(
                action=ACTION, params={"series": s.key, "episode": ep.number},
                reason=f"a NotebookLM Short of {s.key} #{ep.number} is in the inbox"))
        except Exception:
            log.exception("notebooklm short: the ingest of %s raised", drop.name)
            said.append(f"{drop.name}: the ingest raised (see the log)")
            continue
        said.append(f"short {drop.name}: {done.detail}")
    return said


async def finished() -> list[dict[str, Any]]:
    """Made NotebookLM Shorts not yet posted and not resting (a carded one
    rests a week, tutorials.skip_teaching), oldest first. Raises when the
    record can't be read."""
    from ...kernel.state import fetch_all
    rows = await fetch_all(
        """SELECT m.id, m.path, d.source, d.body
             FROM content_media m JOIN content_drafts d ON d.id = m.draft_id
            WHERE d.source LIKE 'academy:tutorial:%:notebooklm' AND m.status = 'done'
              AND NOT EXISTS (SELECT 1 FROM academy_promo_posts p
                               WHERE 'academy:' || p.slug = d.source)
              AND NOT EXISTS (SELECT 1 FROM academy_teaching_skips k
                               WHERE 'academy:' || k.slug = d.source AND k.until > now())
            ORDER BY m.id""")
    return [dict(r) for r in rows]


async def post_next() -> tuple[list[str], bool]:
    """Post the oldest made NotebookLM Short through clips.autopublish_promo
    (its gates, the day's cap). (lines, stop): stop when a post went out or
    the day's cap held it, so the teaching pass makes no other Short this
    run. A carded Short rests a week and the pass goes on. Never raises."""
    from ...kernel.executor import ExecutionRequest, execute
    from ..clips import promo
    tut = _tut()
    try:
        rows = await finished()
    except Exception:
        log.exception("notebooklm short: the made Shorts could not be read")
        return [], False
    for row in rows:
        got = SOURCE.match(str(row.get("source") or ""))
        path = str(row.get("path") or "")
        if got is None or not path or not await asyncio.to_thread(os.path.isfile, path):
            continue
        sl, text = got.group(1), str(row.get("body") or "")
        posted_ = await execute(ExecutionRequest(
            action=POST_ACTION,
            params={"media_id": int(row["id"]), "caption": text, "slug": sl,
                    "youtube_caption": text},
            reason=f"a NotebookLM Short is made: {sl}"))
        said = [f"{sl}: {posted_.detail}"]
        if posted_.detail.startswith("held (") and promo.CAP_HELD not in posted_.detail:
            await tut.skip_teaching(sl, posted_.detail)
            return said, False
        if posted_.ok and not posted_.detail.startswith(("held (", "not posted")):
            from . import synthetic
            if flagged := await synthetic.after_post(sl):
                said.append(flagged)
        return said, posted_.ok
    return [], False


# ------------------------------------------------------------------ next-shorts

async def _made() -> set[tuple[str, int]]:
    """Lessons with a made NotebookLM Short (posted or not). Raises."""
    from ...kernel.state import fetch_all
    rows = await fetch_all(
        """SELECT DISTINCT d.source FROM content_media m
             JOIN content_drafts d ON d.id = m.draft_id
            WHERE d.source LIKE 'academy:tutorial:%:notebooklm' AND m.status = 'done'""")
    out: set[tuple[str, int]] = set()
    for r in rows:
        got = SOURCE.match(str(r["source"] or ""))
        if got:
            out.add((got.group(2), int(got.group(3))))
    return out


async def next_shorts(limit: int | None = None) -> list[dict[str, Any]]:
    """Public, live lessons rendered by NotebookLM with no Short of any
    kind yet, no made NotebookLM Short and no drop waiting, oldest public
    first. Raises when a record can't be read: UNKNOWN is never a list."""
    tut = _tut()
    rows = await tut.untaught()
    made = await _made()
    waiting = await asyncio.to_thread(_pending)
    out: list[dict[str, Any]] = []
    for r in rows:
        key, n = str(r["series"]), int(r["episode"])
        if r.get("taught") or (key, n) in made or (key, n) in waiting:
            continue
        got = tut.resolve(key, n)
        if isinstance(got, str):
            continue
        s, ep = got
        if not ep.scripted or ts.held(s.key, ep) is not None:
            continue
        if (s.key, ep.number) in nlm.RERENDER_POSTED:
            continue                 # its render may be the relaunch's, not the video's
        m = await asyncio.to_thread(tut.read_manifest, tut.episode_dir(s, ep))
        if m is None or m.audio != nlm.AUDIO:
            continue                 # a Fish lesson's Short is the teaching cut's
        public = await tut._public_video(s.key, ep.number)
        if isinstance(public, str):
            continue
        vid = public[2]
        out.append({"series": s.key, "episode": ep.number, "title": ep.title,
                    "drop": f"{nlm.SHORTS}/{nlm.drop_name(s.key, ep.number)}",
                    "source_sha256": source_sha(ep),
                    "min_seconds": SECONDS[0], "max_seconds": SECONDS[1],
                    "video_id": vid, "video_url": f"https://youtu.be/{vid}"})
        if limit is not None and len(out) >= limit:
            break
    return out
