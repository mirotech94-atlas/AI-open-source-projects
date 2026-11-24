"""Which backend renders a reel, and the two that run on this box.

Chosen per call, in this order (Task 55, Miro's ruling):

  1. **veo** -- `GEMINI_API_KEY` set, ffmpeg on the box, and the WHOLE
     story (beats x 8 s) fits under `VEO_MONTHLY_SECONDS`. Real motion: one
     8 s Veo clip per beat (3-4 per story, `VEO_MAX_BEATS`), every beat's
     prompt headed by the same character sheet so the people stay the same
     people, joined into one re-encoded stream, captioned from Groq's word
     timings, burned in with libass. The cap is checked for the whole story
     BEFORE the first clip is asked for: a story is never half-rendered at
     the cap, it goes to the next backend instead.
     **veo+higgsfield** (Miro, 2026-09-29) -- when Higgsfield is keyed and
     Veo's month or day (`VEO_DAILY_CLIPS`, reserve included) cannot make
     every beat, Veo makes what it can and Higgsfield the rest, every
     overflow clip's price reserved under `HIGGSFIELD_MONTHLY_USD` before
     the next clip, or the story fails whole. A daily-quota 429 mid-story
     moves the remaining beats there. Without the keys, nothing changes.
  2. **openart** -- `OPENART_TOKEN` set: the original MCP path, unchanged,
     in `content.render_video`.
  3. **local** -- free: Gemini stills (when the key is set and the model
     answers) or typographic cards, Fish narration, ffmpeg zoompan, "Made
     with AI" burned into every frame, PART 1 on a cliffhanger.

ffmpeg missing is never a guess: the local path refuses with "ffmpeg not
installed" and Veo is skipped for the same reason. Every prompt passes
`guard.refusal` (no real brand, no real person) before anything is sent.
The result lands on `content_media` with a 24 h public media token exactly
like the OpenArt path, so a publish card can reference it.
"""
from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime

from ...kernel.contracts import ActionResult
from ...kernel.state import execute_sql, fetch_all, fetch_one
from . import captions, cards, course, ffmpeg, football, higgsfield, reels, stills, veo
from .drafts import AI_LABEL
from .reel_script import Script

# A series episode runs up to 11 beats (reels.SERIES_BEATS); the day's clip
# allowance holds it to what Veo can make in one day.
DEFAULT_VEO_MAX_BEATS = 11
# A beat that hits Veo's HTTP 429 is retried in place, same beat, same
# prompt, with this backoff -- never abandoned on the first quota bump
# (Task 56, the first real reel run). Any other failure (400, 403, a
# missing operation name, an unreachable host) is not a quota problem and
# is not retried.
VEO_429_BACKOFF_S: tuple[float, ...] = (60.0, 120.0, 240.0)


def veo_max_beats() -> int:
    try:
        return max(1, int(os.getenv("VEO_MAX_BEATS", str(DEFAULT_VEO_MAX_BEATS))))
    except ValueError:
        return DEFAULT_VEO_MAX_BEATS


def veo_beats(script: Script) -> list[str]:
    return list(script.scenes[:veo_max_beats()])


@dataclass(frozen=True, slots=True)
class Choice:
    backend: str            # veo | veo+higgsfield | openart | local
    why: str                # what decided it, with the figures it read


VEO_AND_HIGGSFIELD = "veo+higgsfield"
# Miro, 2026-09-30 (relayed by the Full-Upgrade session): "don't use veo, use
# higgsfield instead only". `REELS_RENDERER=higgsfield` turns Veo OFF -- not
# first, not a fallback: every beat is made on Higgsfield under its cap, and
# when Higgsfield cannot (no credits, the cap, no keys) the story fails and
# says so. Unset or `auto` keeps the order above.
HIGGSFIELD_ONLY = "higgsfield"
RENDERER_ENV = "REELS_RENDERER"
VEO_BACKENDS = ("veo", VEO_AND_HIGGSFIELD, HIGGSFIELD_ONLY)   # all in `render_veo`


def higgsfield_only() -> bool:
    return os.getenv(RENDERER_ENV, "auto").strip().lower() == HIGGSFIELD_ONLY


def _backend_of(hf_prices: dict) -> str:
    """The backend a finished story is recorded under."""
    if higgsfield_only():
        return HIGGSFIELD_ONLY
    return VEO_AND_HIGGSFIELD if hf_prices else "veo"


async def veo_room(beats: int, used: int, cap: int) -> tuple[int, str | None]:
    """How many of a story's `beats` Veo can make now, and why fewer (None
    when all): its month (`VEO_MONTHLY_SECONDS`) and -- read only when
    Higgsfield is configured, so without it nothing changes -- its day
    (`VEO_DAILY_CLIPS` less `calendar.clips_today()`, the reserve included,
    counted from Pacific midnight). A day that cannot be read is not
    counted: Veo is tried, and its own daily-quota 429 still overflows."""
    n = min(beats, max(0, cap - used) // veo.CLIP_SECONDS)
    why: list[str] = []
    if n < beats:
        why.append(f"Veo's month has room for {n} of {beats} clip(s) ({used}/"
                   f"{cap} s used)")
    if higgsfield.configured():
        # Imported here: calendar imports render.
        from . import calendar
        try:
            today: int | None = await calendar.clips_today()
        except Exception:  # noqa: BLE001
            today = None
        if today is not None:
            allowance = calendar.daily_clips()
            left = max(0, allowance - today)
            if left < n:
                n = left
                why.append(f"Veo's daily allowance has {left} clip(s) left "
                           f"({today}/{allowance} used today, reserve included)")
    if not why:
        return n, None
    return n, "; ".join(why) + f" (read {datetime.now(UTC):%H:%M} UTC)"


async def higgsfield_room() -> tuple[bool, str]:
    """Whether Higgsfield may take overflow at all: its keys set and its
    ledger readable with money left under `HIGGSFIELD_MONTHLY_USD`. The
    render itself reserves every overflow clip's price under the cap before
    the first clip, and refuses the story whole when they do not fit."""
    if not higgsfield.configured():
        return False, "Higgsfield not configured"
    try:
        used = await higgsfield.spent_this_month()
    except Exception as exc:  # noqa: BLE001
        from .._http import explain
        return False, f"Higgsfield spend unreadable ({explain(exc)})"
    cap = higgsfield.monthly_cap()
    at = datetime.now(UTC)
    if used >= cap:
        return False, (f"Higgsfield cap reached: ${used:.2f} of ${cap:.2f} "
                       f"booked this month (read {at:%H:%M} UTC)")
    return True, (f"the rest on Higgsfield: ${used:.2f} of ${cap:.2f} booked "
                  f"this month (read {at:%H:%M} UTC)")


async def choose_backend(script: Script) -> Choice:
    """veo if key, ffmpeg and room for the whole story; veo+higgsfield when
    Veo's month or day cannot make every beat and Higgsfield may take the
    rest (Veo still makes what it can, first); else openart if its token is
    set; else local. Reads only: no estimate is asked for here."""
    passed: list[str] = []
    if higgsfield_only():
        # Veo is off by Miro's word; `render_veo` refuses when Higgsfield
        # cannot make the story, rather than reaching for anything else.
        return Choice(HIGGSFIELD_ONLY,
                      f"Veo off ({RENDERER_ENV}={HIGGSFIELD_ONLY}): every beat on "
                      f"Higgsfield" + ("" if higgsfield.configured() else
                                       ", which is NOT configured"))
    if veo.configured():
        beats = len(veo_beats(script))
        needed = beats * veo.CLIP_SECONDS
        cap = veo.monthly_cap()
        try:
            used = await veo.seconds_this_month()
        except Exception as exc:  # noqa: BLE001
            from .._http import explain
            used = None
            passed.append(f"Veo spend unreadable ({explain(exc)}), so not Veo")
        at = datetime.now(UTC)
        if used is not None:
            on_veo, short = (await veo_room(beats, used, cap)
                             if not ffmpeg.missing() else (0, None))
            if ffmpeg.missing():
                passed.append(f"Veo skipped: {ffmpeg.missing()}")
            elif short is None:
                return Choice("veo", f"Veo: {needed} s for this story, {used}/"
                                     f"{cap} s used this month (read {at:%H:%M} UTC)")
            else:
                ok, room = await higgsfield_room()
                if ok:
                    return Choice(VEO_AND_HIGGSFIELD,
                                  f"Veo for {on_veo} of {beats} beat(s): {short}; "
                                  f"{room}")
                if used + needed <= cap:
                    # Nowhere better: Veo as before, and a 429 says so.
                    return Choice("veo", f"Veo: {needed} s for this story, "
                                         f"{used}/{cap} s used this month (read "
                                         f"{at:%H:%M} UTC); {short}; {room}")
                passed.append(f"Veo skipped: this story needs {needed} s and "
                              f"{used}/{cap} s are used this month (read "
                              f"{at:%H:%M} UTC)")
    else:
        passed.append("Veo not configured (GEMINI_API_KEY unset)")
    if os.getenv("OPENART_TOKEN", "").strip():
        return Choice("openart", "; ".join(passed + ["OpenArt token set"]))
    passed.append("OpenArt token unset, so the free local render")
    return Choice("local", "; ".join(passed))


# ----------------------------------------------------------------- helpers

def _write(path: str, data: bytes | str) -> None:
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode) as fh:
        fh.write(data)


def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _place(src: str, dest: str) -> None:
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    shutil.move(src, dest)


async def _new_row(draft_id: int, provider: str) -> int:
    row = await fetch_one(
        """INSERT INTO content_media (draft_id, provider, kind, status, ai_label)
           VALUES ($1, $2, 'video', 'rendering', TRUE) RETURNING id""",
        draft_id, provider)
    return int(row["id"])


async def _fail(media_id: int, reason: str) -> ActionResult:
    await execute_sql(
        "UPDATE content_media SET status = 'failed' WHERE id = $1", media_id)
    return ActionResult(False, reason)


async def _finish(media_id: int, draft_id: int, backend: str, built: str,
                  notes: list[str], *, captions: bool | None = None) -> ActionResult:
    """Move the finished file under DATA_DIR, mint its 24 h token, mark the
    row done. The token URL is in neither `detail` nor `evidence`: the
    executor writes both into the append-only actions_log, where a token
    could never be revoked. A caller that needs the link reads it with
    `content.public_link(media_id)`."""
    from . import DATA_DIR, MEDIA_TOKEN_TTL, _media_url
    path = f"{DATA_DIR}/{media_id}.mp4"
    await asyncio.to_thread(_place, built, path)
    token = secrets.token_urlsafe(32)
    expires = datetime.now(UTC) + MEDIA_TOKEN_TTL
    # `captions`: whether a non-empty set of cues was burned (RA-E5). The
    # pixels cannot be read back, so the quality gate reads this record.
    await execute_sql(
        """UPDATE content_media
           SET status = 'done', path = $2, media_token = $3,
               media_token_expires_at = $4, captions = $5
           WHERE id = $1""",
        media_id, path, token, expires, captions)
    # The script is spoken for: it has a video now, and its card decides it.
    await execute_sql(
        "UPDATE content_drafts SET status = 'rendered' "
        "WHERE id = $1 AND status = 'draft'", draft_id)
    url = _media_url(token)
    detail = (f"rendered draft {draft_id} with the {backend} backend; media id "
              f"{media_id}; " + "; ".join(notes))
    detail += (" — publicly reachable until "
               f"{expires:%Y-%m-%d %H:%M} UTC" if url else
               " — ATLAS_HOST is unset, so no public URL could be built")
    return ActionResult(True, detail,
                        evidence={"media_id": media_id, "backend": backend,
                                  "path": path, "public": url is not None},
                        rollback_token=str(media_id))


# ----------------------------------------------------------------- 4K master

def master_path(media_id: int) -> str:
    from . import DATA_DIR
    return f"{DATA_DIR}/{media_id}-2160.mp4"


async def write_master(joined: str, ass: str, media_id: int, *,
                       beat_seconds: float | None = None,
                       source: str | None = None) -> str | None:
    """The 2160x3840 archive master beside the delivery file, when
    `REELS_EXPORT_4K` is on (the default). Returns the note for the card,
    which always says it is an upscale of the clips' own resolution (Veo's
    native one, or `source` when another generator made some of them). A
    failure here never fails the reel: the 1080x1920 delivery file is what
    posts, and the note says the master is missing and why."""
    if not ffmpeg.export_4k():
        return None
    dest = master_path(media_id)
    try:
        await asyncio.to_thread(os.makedirs, os.path.dirname(dest) or ".",
                                exist_ok=True)
        await ffmpeg.run(ffmpeg.burn_argv(joined, ass, dest,
                                          beat_seconds=beat_seconds, uhd=True),
                         timeout=ffmpeg.MASTER_TIMEOUT_S)
    except (ffmpeg.FfmpegError, OSError) as exc:
        return f"4K master NOT written ({exc}); the 1080x1920 delivery file is unaffected"
    origin = source or f"Veo's native {veo.resolution()}"
    return (f"4K master (2160x3840) upscaled from {origin} "
            f"-- not native 4K; the post is the 1080x1920 delivery file")


# ----------------------------------------------------------------- veo

def reels_voice() -> str:
    """The Fish Audio voice chosen for the reels (`REELS_FISH_VOICE_ID`),
    or "" -- then Veo's own narrator speaks and nothing is laid over it."""
    return os.getenv("REELS_FISH_VOICE_ID", "").strip()


ARABIC_MARK = "egyptian arabic"


def is_arabic(script: Script) -> bool:
    """An Egyptian-Arabic reel (its format says so). The chosen Fish voice
    is English only, so Veo speaks these itself, in Egyptian Arabic."""
    return ARABIC_MARK in str(getattr(script, "format", "") or "").lower()


# Miro, 2026-09-27: "action and movement for all characters and 3D
# movement -- not slides". Every Veo clip, daily reel or calendar, is asked
# for it in these words.
# Miro's reference reels (2026-09-27, two TikToks he sent): the look every
# clip asks for. Taken: the craft. Not taken: their real car brands and a
# famous studio's look (guard.ip_refusal refuses both in code).
STYLE = ("LOOK: high-end stylized 3D animation, photoreal materials and "
         "lighting. Every character has an expressive, human-like face -- "
         "eyes with lids and brows, and a mouth that forms every word it "
         "speaks (a vehicle: eyes in the windshield, a mouth in the lower "
         "bumper; an animal, fruit or object: a face on its front). Original "
         "designs only: an unusual colour pattern, their own accessories and "
         "eye shapes, no badge or logo. Accurate lip-sync on whoever speaks. "
         "Quick 1-3 s cuts inside the shot -- close-up reactions, over-the-"
         "shoulder, sweeping drone and low tracking angles, push-ins on big "
         "moments -- with particles and effects (dust, splashes, sparks). "
         "Cinematic grade: golden hour outdoors, moody neon at night. SOUND: "
         "each character's own voice, real sound effects, and a dramatic "
         "cinematic score low under the dialogue. No subtitles or text.")
MOTION = ("MOTION: fully animated cinematic 3D, never a still or a slideshow. "
          "Every character in frame moves the whole time -- walks, turns, "
          "gestures with hands, reacts with face and body, handles props -- "
          "and the characters look at and play off each other. The camera "
          "moves in 3D through the scene (a slow dolly, a tracking move or an "
          "orbit around the speakers), never a locked-off frame.")


def beat_prompt(sheet: str, index: int, total: int, beat: str,
                *, own_voice: bool = True, arabic: bool = False,
                dialogue: bool = False) -> str:
    """The character sheet FIRST, then the beat, then the motion rule. A
    dialogue beat ("Name: line") is spoken by the characters to each other,
    on camera, lip-synced -- no narrator (Miro, 2026-09-27). An older
    narrated draft keeps its narrator; with `own_voice` false the chosen
    Fish voice is laid over it, so Veo is asked for no speech at all."""
    tongue = "Egyptian Arabic" if arabic else "English"
    from .reel_script import scene_of
    written = scene_of(beat)
    if written:
        # A written series' beat: its own scene, the quoted line in the
        # language it is spoken, said by that character on camera.
        voice = (f"SCENE: {written} The characters speak the quoted lines "
                 f"themselves, aloud, lip-synced, reacting to each other (no "
                 f"narrator, no voice-over). ")
        return (f"{sheet}\n\nBeat {index} of {total}.\n{STYLE}\n{MOTION}\n"
                f"Vertical 9:16, cinematic, fiction. {voice}"
                f"No on-screen text, no logos, no real people, no real brands.")
    if dialogue:
        from .reel_script import Script as _S
        pairs = _S("", (beat,), "").exchanges()[0]
        said = " ".join(
            (f"{who} says: \"{line}\"" if who else f"\"{line}\"")
            for who, line in pairs)
        voice = (f"The characters speak aloud in {tongue}, to each other, each "
                 f"line with lip-sync and face motion on its speaker (no "
                 f"narrator, no voice-over): {said} ")
    elif arabic:
        voice = f"A narrator says, in Egyptian Arabic: \"{beat}\" "
    elif own_voice:
        voice = f"A British narrator says: \"{beat}\" "
    else:
        voice = "Nobody speaks; ambient sound only, no voice, no narration. "
    return (f"{sheet}\n\nBeat {index} of {total}: {beat}\n{STYLE}\n{MOTION}\n"
            f"Vertical 9:16, cinematic, fiction. {voice}"
            f"No on-screen text, no logos, no real people, no real brands.")


# One Veo cap check at a time in this process: the check and the
# reservation happen together, so two renders (the daily step and an agent's
# tool call) cannot both see room for one story and spend two.
_VEO_CAP_LOCK = asyncio.Lock()


INTERRUPTED_AFTER_MIN = 30


async def reap_interrupted() -> int:
    """Renders left 'rendering' for INTERRUPTED_AFTER_MIN minutes were killed
    mid-flight (2026-09-27: a deploy recreated the api container during a
    reel). They are marked failed so nothing waits on them.

    Their Veo reservation is deliberately NOT refunded: the process died
    before it could learn how many clips Veo had already accepted (and may
    have billed), and the cap may over-count but must never under-count.
    Returns how many rows were reaped."""
    rows = await fetch_all(
        f"""UPDATE content_media SET status = 'failed'
             WHERE status = 'rendering'
               AND at < now() - interval '{INTERRUPTED_AFTER_MIN} minutes'
         RETURNING id""")
    if rows:
        import logging
        logging.getLogger("atlas.content").warning(
            "marked %d interrupted render(s) failed (media %s); their Veo "
            "reservations stand, since what Veo billed is unknown",
            len(rows), ", ".join(str(r["id"]) for r in rows))
    return len(rows)


async def _reserve_overflow(prompts: list[str], first: int,
                            prices: dict[int, higgsfield.Price],
                            job: str) -> str | None:
    """Reserve beats `first`..last on Higgsfield, all or none, into
    `prices`; the reason when it would not."""
    beats = list(range(first, len(prompts) + 1))
    got, why = await higgsfield.reserve_all(
        [prompts[i - 1] for i in beats], job_id=job,
        parts=[f"beat-{i}" for i in beats])
    if got is None:
        return f"Higgsfield overflow refused: {why}"
    prices.update(zip(beats, got, strict=True))
    return None


def _overflow_said(prices: dict[int, higgsfield.Price]) -> str:
    """Higgsfield's clips in a reel's notes: dollars only where Higgsfield
    named them; the rest "price not read", with what was booked instead."""
    read = [p for p in prices.values() if p.read]
    unread = [p for p in prices.values() if not p.read]
    parts = []
    if read:
        parts.append(f"${sum(p.usd for p in read):.2f} by Higgsfield's estimate")
    if unread:
        parts.append(f"price not read for {len(unread)} clip(s), "
                     f"${sum(p.usd for p in unread):.2f} booked at worst case")
    return (f"{len(prices)} Higgsfield overflow clip(s) ({higgsfield.MODEL}, "
            f"{higgsfield.resolution()}, normalised to 1080x1920): "
            + "; ".join(parts))


async def render_veo(draft_id: int, script: Script) -> ActionResult:
    """Veo first, one clip per beat, the whole story's seconds reserved
    before the first clip. When Higgsfield is configured and Veo's month or
    day cannot make every beat (`veo_room`), or Veo answers a daily-quota
    429 mid-story, the remaining beats are made on Higgsfield -- every one
    of their prices reserved under `HIGGSFIELD_MONTHLY_USD` before the next
    clip, or the story fails whole, as before, rather than half-made."""
    beats = veo_beats(script)
    story = len(beats) * veo.CLIP_SECONDS
    cap = veo.monthly_cap()
    if (why := ffmpeg.missing()) is not None:
        return ActionResult(False, f"refused: {why}")
    try:
        await reap_interrupted()
    except Exception as exc:  # noqa: BLE001
        # A failed tidy-up must not block a render.
        import logging
        logging.getLogger("atlas.content").warning(
            "could not reap interrupted renders: %s", type(exc).__name__)
    if higgsfield_only():
        if not higgsfield.configured():
            return ActionResult(
                False, f"refused: {RENDERER_ENV}={HIGGSFIELD_ONLY}, so Veo is off, "
                       f"and Higgsfield is not configured ({higgsfield.KEY_ID_ENV} / "
                       f"{higgsfield.SECRET_ENV} unset) — nothing rendered")
        # Veo's ledger is neither read nor written: no seconds booked, none
        # to give back. Every beat is Higgsfield's, reserved below.
        used, on_veo, needed = 0, 0, 0
        short = f"Veo off ({RENDERER_ENV}={HIGGSFIELD_ONLY})"
        media_id = await _new_row(draft_id, HIGGSFIELD_ONLY)
    else:
        async with _VEO_CAP_LOCK:
            try:
                used = await veo.seconds_this_month()
            except Exception as exc:  # noqa: BLE001
                from .._http import explain
                return ActionResult(False, f"Veo spend unreadable, so nothing "
                                           f"was asked of Veo: {explain(exc)}")
            on_veo, short = await veo_room(len(beats), used, cap)
            overflow = short is not None and higgsfield.configured()
            if short is not None and not overflow:
                return ActionResult(
                    False, f"Veo cap: this story needs {story} s ({len(beats)} x "
                           f"{veo.CLIP_SECONDS} s) and {used}/{cap} s are used "
                           f"this month — refused as a whole before the first clip")
            if not overflow:
                on_veo = len(beats)
            needed = on_veo * veo.CLIP_SECONDS
            media_id = await _new_row(draft_id, "veo")
            try:
                if needed:
                    await veo.record(needed, media_id)  # counted BEFORE asking
            except Exception as exc:  # noqa: BLE001
                from .._http import explain
                return await _fail(media_id, f"Veo spend could not be recorded, "
                                             f"so nothing was asked of Veo: "
                                             f"{explain(exc)}")

    accepted = 0
    hf_prices: dict[int, higgsfield.Price] = {}
    hf_tried: set[int] = set()
    job = f"media:{media_id}"
    work = await asyncio.to_thread(tempfile.mkdtemp, prefix="atlas-reel-")
    story = higgsfield.set_story(media_id)      # one resolution per story (mix)
    try:
        sheet = script.character_sheet()
        stats = football.is_football(script)
        shots = football.shots_of(script)
        promo = course.is_course(script)
        # The football lane: a catalog shot, no names, no faces, no crests
        # (verified in content.render_video before this). The course lane:
        # a catalog shot of abstract motion, never a screen.
        prompts = [football.beat_prompt(shots[i - 1], i, len(beats)) if stats else
                   course.beat_prompt(shots[i - 1], i, len(beats)) if promo else
                   beat_prompt(sheet, i, len(beats), beat,
                               own_voice=not reels_voice() or is_arabic(script),
                               arabic=is_arabic(script), dialogue=script.dialogue)
                   for i, beat in enumerate(beats, 1)]
        overflow_note: str | None = None
        if on_veo < len(beats) and (why := await _reserve_overflow(
                prompts, on_veo + 1, hf_prices, job)) is not None:
            if higgsfield_only():
                # Veo is off: nothing else may make it. Said plainly.
                return await _fail(media_id, f"Higgsfield could not take the "
                                             f"story and Veo is off "
                                             f"({RENDERER_ENV}={HIGGSFIELD_ONLY}): "
                                             f"{why}")
            # Higgsfield cannot take the rest. When only Veo's DAY was short
            # and its month still holds the whole story, Veo is asked for
            # every beat, as it was before Higgsfield (its own daily-quota
            # 429 says so if the day really is gone); otherwise the story
            # fails whole.
            extra = (len(beats) - on_veo) * veo.CLIP_SECONDS
            async with _VEO_CAP_LOCK:
                try:
                    month = await veo.seconds_this_month()
                except Exception:  # noqa: BLE001
                    month = None
                fits = month is not None and month + extra <= cap
                if fits:
                    await veo.record(extra, media_id)   # counted BEFORE asking
                    needed += extra
                    on_veo = len(beats)
            if not fits:
                return await _fail(media_id, f"{short}; {why}")
            overflow_note = f"{short}; {why} — so Veo for every beat, as before"

        async def on_higgsfield(i: int) -> str | ActionResult:
            """Beat `i` on Higgsfield, normalised to Veo's frame and sound:
            the clip's path, or the failed result."""
            hf_tried.add(i)
            try:
                data = await higgsfield.clip(prompts[i - 1])
            except higgsfield.HiggsfieldError as exc:
                if not exc.charged:
                    await higgsfield.refund(hf_prices[i], job_id=job,
                                            part=f"beat-{i}")
                return await _fail(media_id, f"Higgsfield beat {i}/{len(beats)}: "
                                             f"{exc}")
            came = os.path.join(work, f"hf_{i}.mp4")
            path = os.path.join(work, f"clip_{i}.mp4")
            await asyncio.to_thread(_write, came, data)
            try:
                await ffmpeg.run(ffmpeg.normalize_argv(came, path))
            except ffmpeg.FfmpegError as exc:
                return await _fail(media_id, f"ffmpeg could not normalise "
                                             f"Higgsfield beat {i}: {exc}")
            return path

        clips = []
        for i, prompt in enumerate(prompts, 1):
            if i in hf_prices:
                made = await on_higgsfield(i)
                if isinstance(made, ActionResult):
                    return made
                clips.append(made)
                continue
            data = None
            exc: veo.VeoError | None = None
            for attempt in range(len(VEO_429_BACKOFF_S) + 1):
                try:
                    data = await veo.clip(prompt)
                    accepted += 1
                    exc = None
                    break
                except veo.VeoError as caught:
                    accepted += caught.accepted
                    exc = caught
                    if caught.status_code != 429 or attempt == len(VEO_429_BACKOFF_S):
                        break               # a 402 (credits gone) breaks here too
                    if caught.daily_quota and higgsfield.configured():
                        break                   # waiting will not bring it back
                    await asyncio.sleep(VEO_429_BACKOFF_S[attempt])
            if exc is not None:
                # A depleted prepaid balance (402, seen 2026-09-30) is a day
                # that no midnight restores: the rest of the story goes to
                # Higgsfield exactly as a daily-quota 429 does, and the
                # failure otherwise names the top-up, not "retry tomorrow".
                if exc.status_code == 429 or exc.credits_depleted:
                    gone = exc.daily_quota or exc.credits_depleted
                    read_at = datetime.now(UTC)
                    if exc.credits_depleted:
                        veo.note_depleted()     # the check reads this
                        what = "credits"
                        said = (f"Veo prepaid credits depleted — top up at "
                                f"{veo.TOP_UP} (beat {i}/{len(beats)}, read "
                                f"{read_at:%H:%M} UTC)")
                    else:
                        what = "daily quota" if exc.daily_quota else "quota"
                        said = (f"Veo {what} exhausted — retry tomorrow (beat {i}/"
                                f"{len(beats)}, read {read_at:%H:%M} UTC)")
                    if gone and higgsfield.configured():
                        why = await _reserve_overflow(prompts, i, hf_prices, job)
                        if why is None:
                            made = await on_higgsfield(i)
                            if isinstance(made, ActionResult):
                                return made
                            clips.append(made)
                            continue
                        said += f"; {why}"
                    result = await _fail(media_id, said)
                    # The caller may fall back to stills for today's post;
                    # the evidence says so, the detail stays a plain reason.
                    return ActionResult(False, result.detail,
                                        evidence={"veo_quota": what})
                return await _fail(media_id, f"Veo beat {i}/{len(beats)}: {exc}")
            path = os.path.join(work, f"clip_{i}.mp4")
            await asyncio.to_thread(_write, path, data)
            clips.append(path)
        if promo:
            try:
                built, notes, burned = await course.assemble(clips, script, work)
            except course.CourseRenderError as exc:
                return await _fail(media_id, f"course reel: {exc}")
            n_veo = len(beats) - len(hf_prices)
            if higgsfield_only():
                spent = f"{short}; {_overflow_said(hf_prices)}"
            else:
                spent = (f"{n_veo} Veo clip(s) at {veo.resolution()}, "
                         f"{used + n_veo * veo.CLIP_SECONDS}/{cap} s this month "
                         f"(read {datetime.now(UTC):%H:%M} UTC)")
                if hf_prices:
                    spent += f" + {_overflow_said(hf_prices)}"
            return await _finish(media_id, draft_id,
                                 _backend_of(hf_prices), built,
                                 [spent, *notes], captions=burned)
        joined = os.path.join(work, "joined.mp4")
        listing = os.path.join(work, "clips.txt")
        out = os.path.join(work, "out.mp4")
        ass = os.path.join(work, "captions.ass")
        sound = os.path.join(work, "sound.mp3")
        voiced = os.path.join(work, "voiced.mp4")
        voice_note: str | None = None
        try:
            await asyncio.to_thread(_write, listing, ffmpeg.concat_list(clips))
            await ffmpeg.run(ffmpeg.concat_argv(listing, joined))
            total = await ffmpeg.duration(joined)
            if (reels_voice() and not is_arabic(script) and not script.dialogue
                    and not stats):
                # The chosen Fish voice narrates; Veo's sound is ambience
                # under it. Captions are timed from the voice itself.
                audio, voice_note, no_audio = await stills.narration(
                    script.narration)
                if audio is None:
                    return await _fail(media_id, f"Fish narration: {no_audio}")
                await asyncio.to_thread(_write, sound, audio)
                spoken = await ffmpeg.duration(sound)
                try:
                    await ffmpeg.run(ffmpeg.narrate_argv(
                        joined, sound, voiced, video_seconds=total,
                        narration_seconds=spoken))
                except ffmpeg.FfmpegError:
                    # A clip with no audio stream of its own: the voice is
                    # the whole track.
                    await ffmpeg.run(ffmpeg.replace_audio_argv(
                        joined, sound, voiced, video_seconds=total,
                        narration_seconds=spoken))
                joined, total = voiced, max(total, spoken)
            else:
                await ffmpeg.run(ffmpeg.audio_argv(joined, sound))
            heard = await asyncio.to_thread(_read, sound)
            series = reels.bucket_of(script.format) == "serial"
            if stats:
                # Captions ON for the football lane: the stats, big and bold,
                # one line per beat, and the source with its read time.
                words = football.screen_lines(script)[:len(beats)]
                no_words = None
                await asyncio.to_thread(_write, ass, captions.build_stat_ass(
                    words, float(veo.CLIP_SECONDS), total,
                    tag=reels.screen_tag(script.format),
                    source=football.source_of(script.caption)))
            elif not series:
                # Miro, 2026-09-27: captions on series only. The AI tag stays.
                words, no_words = None, "not a series, so no captions"
                await asyncio.to_thread(_write, ass, captions.build_scripted_ass(
                    [], total, arabic=False, tag=reels.screen_tag(script.format),
                    end_text=script.end_card))
            elif script.dialogue and (script.scripted or not is_arabic(script)):
                # The script's own lines, each speaker in its own colour, over
                # its beat -- exact words, not a transcription (Miro,
                # 2026-09-27: colour-coded per character).
                cues = captions.dialogue_cues(script.exchanges()[:len(beats)],
                                              float(veo.CLIP_SECONDS))
                words = cues
                no_words = None
                await asyncio.to_thread(_write, ass, captions.build_scripted_ass(
                    cues, total, arabic=False, tag=reels.screen_tag(script.format),
                    end_text=script.end_card))
            elif is_arabic(script):
                # Arabic voice, English captions (Miro, 2026-09-27).
                english, no_words = await captions.english_segments(heard)
                words = english
                await asyncio.to_thread(_write, ass, captions.build_scripted_ass(
                    english or [], total, arabic=False,
                    tag=reels.screen_tag(script.format), end_text=script.end_card))
            else:
                words, no_words = await captions.word_timings(heard)
                await asyncio.to_thread(_write, ass, captions.build_ass(
                    words, total, part_one=script.part_one,
                    tag=reels.screen_tag(script.format), end_text=script.end_card))
            joined, fps_note = await ffmpeg.smooth(
                joined, os.path.join(work, "smooth.mp4"))
            await ffmpeg.run(ffmpeg.burn_argv(joined, ass, out,
                                              beat_seconds=veo.CLIP_SECONDS),
                             timeout=ffmpeg.BURN_TIMEOUT_S)
        except ffmpeg.FfmpegError as exc:
            return await _fail(media_id, f"ffmpeg: {exc}")
        n_veo = len(beats) - len(hf_prices)
        master_note = await write_master(
            joined, ass, media_id, beat_seconds=veo.CLIP_SECONDS,
            source=(f"Veo's native {veo.resolution()} and Higgsfield's "
                    f"{higgsfield.resolution()}" if hf_prices else None))
        read_at = datetime.now(UTC)
        if higgsfield_only():
            spent = f"{short}; {_overflow_said(hf_prices)}"
        else:
            spent = (f"{n_veo} Veo clip(s) at {veo.resolution()}, "
                     f"{n_veo * veo.CLIP_SECONDS} s, "
                     f"{used + n_veo * veo.CLIP_SECONDS}/{cap} "
                     f"s this month (read {read_at:%H:%M} UTC)")
            if hf_prices:
                spent += f" + {_overflow_said(hf_prices)}"
        said = ("stat captions burned from the verified lines, numbers bound to "
                "API-Football" if stats else
                "no captions (not a series); the AI tag is burned in"
                if not series else
                ("captions burned from the script's own lines, one colour "
                 "per speaker" if script.dialogue and (script.scripted or
                                                   not is_arabic(script)) else
                 "English captions burned from Groq's translation of the "
                 "Arabic" if is_arabic(script) else
                 "captions burned from Groq word timings") if words else
                f"captions NOT burned ({no_words}); the {AI_LABEL!r} tag is "
                f"burned in regardless")
        notes = [spent, said, fps_note, "loudness normalised to -14 LUFS"]
        if overflow_note:
            notes.append(overflow_note)
        if master_note:
            notes.append(master_note)
        if voice_note:
            notes.append(f"narrated by {voice_note} over Veo's own sound")
        elif stats:
            notes.append("no voice: crowd, impacts and score from Veo")
        else:
            notes.append("Veo's own narrator (REELS_FISH_VOICE_ID unset)")
        return await _finish(media_id, draft_id,
                             _backend_of(hf_prices), out, notes,
                             captions=bool(series and words))
    finally:
        higgsfield.reset_story(story)
        await asyncio.to_thread(shutil.rmtree, work, True)
        # Higgsfield: every overflow clip reserved but never asked for.
        for i, price in hf_prices.items():
            if i in hf_tried:
                continue
            try:
                await higgsfield.refund(price, job_id=job, part=f"beat-{i}")
            except Exception as exc:  # noqa: BLE001
                import logging
                logging.getLogger("atlas.content").warning(
                    "Higgsfield refund for media %s beat %s not recorded (the "
                    "cap over-counts, never under): %s", media_id, i,
                    type(exc).__name__)
        # Reconcile: the seconds of every clip Veo never took go back.
        unused = needed - accepted * veo.CLIP_SECONDS
        if unused > 0:
            try:
                await veo.record(-unused, media_id)
            except Exception as exc:  # noqa: BLE001
                import logging
                logging.getLogger("atlas.content").warning(
                    "Veo refund of %s s for media %s not recorded (the cap "
                    "over-counts, never under): %s", unused, media_id, exc)


# ----------------------------------------------------------------- local

def still_prompt(sheet: str, index: int, beat: str) -> str:
    return (f"{sheet}\n\nScene {index}: {beat}\nA vertical 9:16 cinematic "
            f"illustration for a short fictional story. No text, no logos, no "
            f"real people, no real brands.")


def frame_counts(seconds: float, scenes: int) -> list[int]:
    total = max(scenes, round(seconds * ffmpeg.FPS))
    per = total // scenes
    counts = [per] * scenes
    counts[-1] += total - per * scenes
    return counts


async def render_local(draft_id: int, script: Script) -> ActionResult:
    if (why := ffmpeg.missing()) is not None:
        return ActionResult(False, f"refused: {why}")
    scenes = list(script.scenes[:stills.MAX_STILLS])
    if not scenes:
        return ActionResult(False, f"draft {draft_id} has no scenes to render")
    audio, voice_note, no_audio = await stills.narration(script.narration)
    if audio is None:
        return ActionResult(False, f"refused: {no_audio}")

    media_id = await _new_row(draft_id, "local")
    work = await asyncio.to_thread(tempfile.mkdtemp, prefix="atlas-reel-")
    try:
        notes = [f"narration: {voice_note}"]
        sound = os.path.join(work, "narration.mp3")
        await asyncio.to_thread(_write, sound, audio)

        pictures: list[str] = []
        scene_text: list[str | None] = []
        sheet = script.character_sheet()
        if os.getenv("GEMINI_API_KEY", "").strip():
            for i, beat in enumerate(scenes, 1):
                image, no_image = await stills.still(still_prompt(sheet, i, beat))
                if image is None:
                    notes.append(f"stills refused ({no_image}); typographic "
                                 f"cards instead")
                    pictures = []
                    break
                path = os.path.join(work, f"still_{i}.png")
                await asyncio.to_thread(_write, path, image)
                pictures.append(path)
            else:
                notes.append(f"{len(pictures)} Gemini still(s) from "
                             f"{stills.image_model()}")
        else:
            notes.append("GEMINI_API_KEY unset: typographic cards, no stills")
        if not pictures:
            blank = await asyncio.to_thread(cards.card)
            for i, beat in enumerate(scenes, 1):
                path = os.path.join(work, f"card_{i}.png")
                text = os.path.join(work, f"card_{i}.txt")
                await asyncio.to_thread(_write, path, blank)
                await asyncio.to_thread(_write, text, cards.wrapped(beat))
                pictures.append(path)
                scene_text.append(text)

        label = os.path.join(work, "label.txt")
        await asyncio.to_thread(_write, label, reels.screen_tag(script.format))
        end_card = end_text = None
        if script.part_one:
            end_card = os.path.join(work, "end.png")
            end_text = os.path.join(work, "end.txt")
            await asyncio.to_thread(_write, end_card, await asyncio.to_thread(cards.card))
            await asyncio.to_thread(_write, end_text, "PART 1")
        out = os.path.join(work, "out.mp4")
        try:
            seconds = await ffmpeg.duration(sound)
            argv = ffmpeg.stills_argv(
                pictures, frame_counts(seconds, len(pictures)), sound, out,
                label_file=label, font=ffmpeg.font_file(),
                scene_text=scene_text or None, end_card=end_card,
                end_text=end_text)
            await ffmpeg.run(argv)
        except ffmpeg.FfmpegError as exc:
            return await _fail(media_id, f"ffmpeg: {exc}")
        notes.append(f"{seconds:.0f} s, {AI_LABEL!r} burned into every frame"
                     + (", PART 1 end card" if script.part_one else ""))
        return await _finish(media_id, draft_id, "local", out, notes,
                             captions=False)
    finally:
        await asyncio.to_thread(shutil.rmtree, work, True)
