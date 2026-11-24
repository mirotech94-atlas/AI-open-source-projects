"""A daily Academy reel for the clips accounts, made here on the server.

the owner, 2026-10-06: "keep it in clips since it's the most viewed profile ...
make continuous reels about ATLAS Academy to show people". The clips
accounts (Instagram @example_handle, TikTok @example_handle, the clips
YouTube channel) have the followers; the Academy's store needs showing.

What one reel is: a product from `catalog.json`, in rotation, hook first
(2026-10-11) -- a claim or question from `promo_copy`, then the
summary's first sentence and two rows from its label (the counted facts),
then the product's name, its cover and the store --
narrated by the same Fish voice as the lessons, over its cover art and
dark typographic cards, 1080x1920, the AI label on every frame. No Veo,
no stills model: the covers are already drawn and the facts are already
counted, so this costs one Fish call and a few seconds of ffmpeg, and it
cannot say a number the catalog does not.

What it never says: income, results, reviews, or anything the catalog
does not. `guard.refusal` and the Academy's own vet run on the caption
before anything posts (`clips.autopublish_promo`).

  render_promo_reel(slug)  Tier 1: write the reel, a content_media row
  daily()                  the scheduler's tick: next product in rotation,
                           render, then clips.autopublish_promo
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from datetime import UTC, datetime, timedelta

from ...kernel.contracts import ActionResult
from ...kernel.state import execute_sql, fetch_one
from .. import store_origin
from . import concept_short, lesson_short, reel_design
from .catalog import Catalog, Product, load

log = logging.getLogger("academy.example.reel")

DRAFT_KIND = "academy_promo"
PROVIDER = "academy"
STORE = store_origin.host()
STORE_SAID = store_origin.spoken()       # "agency dot one eight d n dot com"
DEFAULT_HOUR_UTC = 16                  # noon in New York, where the audience is
# the owner, 2026-10-11: "post 2x per day, each reel a different lesson". Noon
# and 7 pm in New York.
DEFAULT_HOURS_UTC = (16, 23)
MIN_GAP_H = 3                          # two posts never land within this
MIN_SECONDS, MAX_SECONDS = 12.0, 60.0
BEAT_PAD = 0.7                        # a breath after each beat's narration
# The hook, by the product's kind. Said first; plain, true, no promise.
HOOKS = {
    "Video course": "A course you watch, not a PDF you skim.",
    "Course": "Short lessons, one thing to do after each.",
    "Ebook": "An ebook you can read in one sitting.",
    "Kit": "A kit you open and use the same day.",
    "Workflow pack": "Workflows you import, not build.",
    "Printable workbook": "Print it, fill it in, keep it by the phone.",
}
DEFAULT_HOOK = "Made for people who want AI to do the work and keep the final say."
WRAP = 16


def enabled() -> bool:
    """`ACADEMY_PROMO_REELS=off` stops the daily reel; on by default."""
    return os.getenv("ACADEMY_PROMO_REELS", "on").strip().lower() not in (
        "0", "off", "false", "no")


def hour_utc() -> int:
    try:
        return max(0, min(23, int(os.getenv("ACADEMY_PROMO_HOUR_UTC", str(DEFAULT_HOUR_UTC)))))
    except ValueError:
        return DEFAULT_HOUR_UTC


def hours_utc() -> tuple[int, ...]:
    """The day's posting slots: `ACADEMY_PROMO_HOURS_UTC` ("16,23"), or the
    single `ACADEMY_PROMO_HOUR_UTC` when only that is set, or the default
    two."""
    raw = os.getenv("ACADEMY_PROMO_HOURS_UTC", "").strip()
    if not raw:
        return (hour_utc(),) if os.getenv("ACADEMY_PROMO_HOUR_UTC", "").strip() \
            else DEFAULT_HOURS_UTC
    hours = sorted({int(h) for h in re.findall(r"\d+", raw) if 0 <= int(h) <= 23})
    return tuple(hours) or DEFAULT_HOURS_UTC


def due_slot(now: datetime, done: set[tuple]) -> int | None:
    """The latest slot today that has come and has not run, or None."""
    passed = [h for h in hours_utc() if h <= now.hour and (now.date(), h) not in done]
    return passed[-1] if passed else None


def _sentence(text: str) -> str:
    first = " ".join((text or "").split())
    for stop in (". ", "! ", "? "):
        if stop in first:
            return first[:first.index(stop) + 1]
    return first


def _short(text: str, limit: int = 64) -> str:
    """A label row's value as the card shows it: the first clause, whole,
    within `limit` characters (the voice says the full sentence)."""
    text = " ".join((text or "").split())
    for cut in (", plus ", "; ", ", "):
        if cut in text and text.index(cut) <= limit:
            text = text[:text.index(cut)]
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0]
    return text


def sellable(cat: Catalog) -> list[Product]:
    """Products with at least one live Buy: nothing is shown that cannot
    be bought today."""
    return [p for p in cat.products if any(o.buy_live for o in p.offers)]


def _lead(summary: str, limit: int = 90) -> str:
    """The summary as the value card shows it: its first sentence up to a
    colon (the list after it is said, not shown), whole clauses only."""
    text = _sentence(summary).rstrip(".")
    if ":" in text and text.index(":") >= 20:
        text = text[:text.index(":")]
    return _short(text, limit) if len(text) > limit else text


def script_for(p: Product, day: int = 0) -> list[tuple[str, str]]:
    """The reel's beats as (what is shown, what is said), hook first.

    the owner, 2026-10-11: "make the shorts actually wanting to watch it". The
    first beat is a claim or a question about the viewer's own work, from
    `promo_copy`, on screen from frame 0. The value comes next (the summary
    and two label rows, every word from the catalog). The product comes
    last: its name, its cover and the store. `day` turns the hook over, so
    the product's next reel opens differently."""
    from . import promo_copy
    hook = promo_copy.hook_for(p.slug, day) or HOOKS.get(p.kind, DEFAULT_HOOK)
    beats = [(hook, hook), (_lead(p.summary), _sentence(p.summary))]
    for _name, value in p.label[:2]:
        beats.append((_short(value), _sentence(value)))
    beats.append((p.title, f"It's called {p.title}. It's at {STORE_SAID}, link in bio."))
    return beats


def beat_kinds(beats: list[tuple[str, str]]) -> list[str]:
    """What each beat is, for the edition's layout: the hook, the value in
    between, and the product (its name and cover) last."""
    return ["hook"] + ["fact"] * (len(beats) - 2) + ["title"]


def variant_for(day: datetime | None = None) -> int:
    """The day number: the fallback when the post count cannot be read."""
    return (day or datetime.now(UTC)).timetuple().tm_yday


async def times_shown(slug: str) -> int:
    """How many reels of `slug` have gone out. This picks the hook, so each
    of the product's posts opens on the next one whatever the rotation's
    length. A day number would repeat the same hook for every product
    whenever the number on sale is a multiple of 3."""
    try:
        row = await fetch_one(
            "SELECT count(*) AS n FROM academy_promo_posts WHERE slug = $1", slug)
        return int(row["n"]) if row is not None else 0
    except Exception:                                   # noqa: BLE001
        return variant_for()


def caption_for(p: Product, tutorial: str | None = None) -> str:
    """The post's text on every platform. Line one is the Short's title, a
    hook rather than the bare product name. Then what you get, the
    product's own page, and the hashtags. The YouTube leg adds #Shorts and
    the tutorial playlist (`clips.promo.youtube_description`): a link to
    the video site in this text would trip the brand guard, and Instagram
    or TikTok would not make it clickable anyway. `tutorial` is only a
    flag here, for the "free tutorials" line. No price: the page shows the
    price."""
    from ..content.drafts import AI_LABEL
    from . import promo_copy
    c = promo_copy.for_slug(p.slug)
    title = c.title if c else p.title
    facts = "\n".join(f"• {name}: {value}" for name, value in p.label[:3])
    lines = [title, _sentence(p.summary),
             f"What you get:\n{facts}" if facts else "",
             (f"{p.title} is on ATLAS Academy: {promo_copy.product_url(p.slug)} "
              f"(link in bio)."),
             "Free step-by-step tutorials on our channel." if tutorial else "",
             promo_copy.hashtag_line(p.slug)]
    return "\n".join(line for line in lines if line) + f"\n\n{AI_LABEL}"


def _wrapped(text: str) -> str:
    import textwrap
    return "\n".join(line for part in text.split("\n")
                     for line in (textwrap.wrap(part, WRAP) or [" "]))


HEADER = "ATLAS Academy"
COVER_SIZE = 1000
COVER_TOP = 300


async def _frame(work: str, i: int, *, shown: str, title: str, cover: str = "") -> str:
    """One 1080x1920 frame, composed before the stack: the dark card, the
    header, and either the square cover letterboxed whole (never cropped)
    or the beat's words large in the middle, with the product's name at
    the foot. drawtext reads text from files, so each line is written."""
    from ..content import cards, ffmpeg
    from ..content.render import _write
    bg = os.path.join(work, f"bg_{i}.png")
    await asyncio.to_thread(_write, bg, await asyncio.to_thread(cards.card))
    header = os.path.join(work, "header.txt")
    await asyncio.to_thread(_write, header, HEADER)
    foot = os.path.join(work, "foot.txt")
    await asyncio.to_thread(_write, foot, title)
    font = ffmpeg.font_file()
    out = os.path.join(work, f"frame_{i}.png")
    argv = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", bg]
    chain = ["[0:v]" + ffmpeg._drawtext(header, font, size=44, x="(w-tw)/2", y="236",
                                       box=False)]
    if cover:
        argv += ["-i", cover]
        chain = [f"[1:v]scale={COVER_SIZE}:{COVER_SIZE}:force_original_aspect_ratio=decrease[c]",
                 f"[0:v][c]overlay=(W-w)/2:{COVER_TOP}[o]",
                 "[o]" + ffmpeg._drawtext(header, font, size=44, x="(w-tw)/2", y="236",
                                          box=False)]
    else:
        text = os.path.join(work, f"beat_{i}.txt")
        await asyncio.to_thread(_write, text, _wrapped(shown))
        chain[-1] += "," + ffmpeg._drawtext(text, font, size=88, x="(w-tw)/2",
                                            y="(h-th)/2", box=False)
    chain[-1] += "," + ffmpeg._drawtext(foot, font, size=40, x="(w-tw)/2", y="h-220",
                                        box=False) + "[v]"
    argv += ["-filter_complex", ";".join(chain), "-map", "[v]", "-frames:v", "1", out]
    await ffmpeg.run(argv)
    return out


def old_design_refusal() -> str | None:
    """the owner, 2026-10-19: NotebookLM videos only. Every reel this action
    makes -- a product, lesson or concept Short -- is ATLAS-drawn and
    Fish-voiced, so the action itself renders nothing unless
    ACADEMY_NOTEBOOKLM_FALLBACK is on, whoever calls it."""
    from . import notebooklm
    if notebooklm.fallback_enabled():
        return None
    return ("no product, lesson or concept Short is rendered: they are ATLAS-drawn "
            "and Fish-voiced; NotebookLM videos only (ACADEMY_NOTEBOOKLM_FALLBACK "
            "is off)")


async def render_promo_reel(slug: str) -> ActionResult:
    """One product's reel, written under content.DATA_DIR as a finished
    content_media row (status done), the way the reels lane's renders are,
    so `clips.publish_promo` / `autopublish_promo` take it unchanged."""
    from ..content import DATA_DIR, ffmpeg, render
    if (why := old_design_refusal()) is not None:
        return ActionResult(False, why)
    if slug.startswith("lesson:"):
        return await render_lesson_short(slug)
    if slug.startswith("concept:"):
        return await render_concept_short(slug)
    cat = load()
    p = cat.product(slug)
    if p is None:
        return ActionResult(False, f"no product {slug!r} in the catalog")
    if not any(o.buy_live for o in p.offers):
        return ActionResult(False, f"{p.title} has no live Buy; not shown")
    if (why := ffmpeg.missing()) is not None:
        return ActionResult(False, why)
    variant = await times_shown(p.slug)
    beats = script_for(p, variant)
    kinds = beat_kinds(beats)
    narration = " ".join(said for _, said in beats)
    row = await fetch_one(
        """INSERT INTO content_drafts (kind, source, title, body)
           VALUES ($1, $2, $3, $4) RETURNING id""",
        DRAFT_KIND, f"academy:{p.slug}", p.title, narration)
    draft_id = int(row["id"])
    media_id = await render._new_row(draft_id, PROVIDER)
    from .site import ASSETS
    cover = str(ASSETS / p.cover) if p.cover else ""
    with tempfile.TemporaryDirectory(prefix="academy-reel-") as work:
        if reel_design.available():
            built, notes, why = await _render_designed(work, p, beats, kinds, variant)
        else:
            built, notes, why = await _render_plain(work, p, beats, cover)
        if why is not None:
            return await render._fail(media_id, why)
        result = await render._finish(media_id, draft_id, "academy", built, notes, captions=True)
    if not result.ok:
        return result
    from . import promo_copy
    tutorial = await promo_copy.tutorial_url(p.slug)
    return ActionResult(True, f"{p.title}: {result.detail}",
                        evidence={**(result.evidence or {}), "slug": p.slug,
                                  "caption": caption_for(p, tutorial), "dir": DATA_DIR},
                        rollback_token=result.rollback_token)


async def _render_designed(work: str, p: Product, beats: list[tuple[str, str]],
                           kinds: list[str], variant: int
                           ) -> tuple[str, list[str], str | None]:
    """The product's edition, frame by frame (reel_design), one Fish call
    per beat so every beat lasts exactly its narration plus a breath."""
    from ..content import ffmpeg, stills
    from ..content.drafts import AI_LABEL
    from ..content.render import _write
    ed = reel_design.edition_for(p.slug)
    cover = reel_design.cover_data(p.cover)
    pages, seconds, wavs, voice_note = [], [], [], ""
    for i, ((shown, said), kind) in enumerate(zip(beats, kinds, strict=True)):
        audio, voice_note, no_audio = await stills.narration(said)
        if audio is None:
            return "", [], no_audio or "no narration"
        mp3 = os.path.join(work, f"beat_{i}.mp3")
        await asyncio.to_thread(_write, mp3, audio)
        try:
            secs = await ffmpeg.duration(mp3) + BEAT_PAD
            wav = os.path.join(work, f"beat_{i}.wav")
            await ffmpeg.run(reel_design.pad_argv(mp3, secs, wav))
        except ffmpeg.FfmpegError as exc:
            return "", [], f"ffmpeg (audio): {exc}"
        seconds.append(secs)
        wavs.append(wav)
        pages.append(reel_design.page(ed, kind, shown, p.title, cover, variant))
    total = sum(seconds)
    if not MIN_SECONDS <= total <= MAX_SECONDS:
        return "", [], f"narration is {total:.0f} s, outside {MIN_SECONDS:.0f}-{MAX_SECONDS:.0f}"
    frames = os.path.join(work, "frames")
    try:
        n = await reel_design.render_frames(pages, seconds, frames)
    except Exception as exc:                            # noqa: BLE001
        return "", [], f"the edition could not be drawn ({type(exc).__name__}): {exc}"[:300]
    audio_list = os.path.join(work, "audio.txt")
    await asyncio.to_thread(_write, audio_list, "".join(f"file '{w}'\n" for w in wavs))
    out = os.path.join(work, "out.mp4")
    try:
        await ffmpeg.run(reel_design.assemble_argv(frames, audio_list, out))
    except ffmpeg.FfmpegError as exc:
        return "", [], f"ffmpeg: {exc}"
    return out, [f"{total:.0f} s, {n} frames, the {ed.name} edition (variant {variant})",
                 voice_note, f"{AI_LABEL!r} on every frame"], None


async def _render_plain(work: str, p: Product, beats: list[tuple[str, str]], cover: str
                        ) -> tuple[str, list[str], str | None]:
    """Without Chromium: the dark cards and the cover, through ffmpeg alone."""
    from ..content import ffmpeg, render, stills
    from ..content.drafts import AI_LABEL
    narration = " ".join(said for _, said in beats)
    audio, voice_note, no_audio = await stills.narration(narration)
    if audio is None:
        return "", [], no_audio or "no narration"
    sound = os.path.join(work, "narration.mp3")
    await asyncio.to_thread(render._write, sound, audio)
    pictures = []
    try:
        for i, (shown, _) in enumerate(beats):
            last = i == len(beats) - 1 and cover and os.path.isfile(cover)
            pictures.append(await _frame(work, i, shown=shown, title=p.title,
                                         cover=cover if last else ""))
    except ffmpeg.FfmpegError as exc:
        return "", [], f"ffmpeg (frames): {exc}"
    label = os.path.join(work, "label.txt")
    await asyncio.to_thread(render._write, label, AI_LABEL)
    out = os.path.join(work, "out.mp4")
    try:
        seconds = await ffmpeg.duration(sound)
        if not MIN_SECONDS <= seconds <= MAX_SECONDS:
            return "", [], f"narration is {seconds:.0f} s, outside {MIN_SECONDS:.0f}-{MAX_SECONDS:.0f}"
        argv = ffmpeg.stills_argv(
            pictures, render.frame_counts(seconds, len(pictures)), sound, out,
            label_file=label, font=ffmpeg.font_file())
        await ffmpeg.run(argv)
    except ffmpeg.FfmpegError as exc:
        return "", [], f"ffmpeg: {exc}"
    return out, [f"{seconds:.0f} s, plain cards", voice_note,
                 f"{AI_LABEL!r} on every frame"], None


async def render_promo_reel_dry(slug: str) -> ActionResult:
    if (why := old_design_refusal()) is not None:
        return ActionResult(False, why)
    if slug.startswith("concept:"):
        c = concept_short.find(slug)
        if c is None:
            return ActionResult(False, f"no concept {slug!r}")
        if (why := concept_short.evidence_refusal(c, concept_short.product_text(c.product))):
            return ActionResult(False, why)
        return ActionResult(True, f"would render the concept Short \"{c.title}\" "
                                  f"({len(c.beats)} beats, {c.motion} animation)")
    if slug.startswith("lesson:"):
        # The first lesson Short (2026-10-11) was refused here: the dry run
        # only knew catalog slugs.
        les = lesson_short.find(slug)
        if les is None:
            return ActionResult(False, f"no lesson {slug!r} on the server")
        built = lesson_short.build(les)
        if built is None:
            return ActionResult(False, f"{slug}: no part of this lesson reads on its own")
        title, beats, _ = built
        return ActionResult(True, f"would render lesson {les.lesson} ({les.course}), "
                                  f"\"{title}\": {len(beats) - 2} part(s) in its own voice")
    cat = load()
    p = cat.product(slug)
    if p is None:
        return ActionResult(False, f"no product {slug!r} in the catalog")
    beats = script_for(p)
    words = sum(len(said.split()) for _, said in beats)
    return ActionResult(True, f"would narrate {len(beats)} beats ({words} words, about "
                              f"{words / 2.4:.0f} s) for {p.title} and render it over "
                              f"its cover and {len(beats) - 1} cards")


async def unrender_promo_reel(token: str) -> ActionResult:
    """Mark the render failed and drop its file; the draft stays."""
    from ..content import DATA_DIR
    media_id = int(token)
    await execute_sql("UPDATE content_media SET status = 'failed' WHERE id = $1", media_id)
    path = f"{DATA_DIR}/{media_id}.mp4"
    try:
        await asyncio.to_thread(os.remove, path)
    except OSError:
        pass
    return ActionResult(True, f"render {media_id} withdrawn")


# ------------------------------------------------------------------ the daily tick

# ------------------------------------------------------------ lesson shorts

async def render_lesson_short(key: str) -> ActionResult:
    """A Short from one lesson (lesson_short.py): its most important part,
    said by the Fish S2 voice (its recording as the fallback), captioned word by
    word in the brand."""
    from ..content import DATA_DIR, ffmpeg, render
    les = lesson_short.find(key)
    if les is None:
        return ActionResult(False, f"no lesson {key!r} on the server")
    durations = await asyncio.to_thread(lesson_short.durations_of, les)
    built_script = lesson_short.build(les, durations)
    if built_script is None:
        return ActionResult(False, f"{key}: no part of this lesson is clean to show")
    title, beats, caption = built_script
    if (why := ffmpeg.missing()) is not None:
        return ActionResult(False, why)
    if not reel_design.available():
        return ActionResult(False, "the lesson Shorts need Chromium (Playwright)")
    narration = " ".join(b.text for b in beats)
    row = await fetch_one(
        """INSERT INTO content_drafts (kind, source, title, body)
           VALUES ($1, $2, $3, $4) RETURNING id""",
        DRAFT_KIND, key, title, narration)
    draft_id = int(row["id"])
    media_id = await render._new_row(draft_id, PROVIDER)
    with tempfile.TemporaryDirectory(prefix="academy-lesson-") as work:
        built, notes, why = await _render_lesson(work, les, title, beats)
        if why is not None:
            return await render._fail(media_id, why)
        result = await render._finish(media_id, draft_id, "academy", built, notes, captions=True)
    if not result.ok:
        return result
    return ActionResult(True, f"lesson {les.lesson} ({les.course}): {result.detail}",
                        evidence={**(result.evidence or {}), "slug": key,
                                  "caption": caption, "dir": DATA_DIR,
                                  "youtube_caption": lesson_short.youtube_text(
                                      les, title, beats)},
                        rollback_token=result.rollback_token)


# the owner, 2026-10-12: "make it like a human talking, not an AI" -- "go for
# it". A lesson Short is narrated fresh by the Fish S2 voice (it reads the
# delivery cue its beat gets), the same British voice as the lessons. A beat
# Fish cannot say falls back to the lesson's own recording; a Short that no
# longer fits 15-60 s in the new voice is cut again from the recordings.
# ACADEMY_LESSON_SHORTS_VOICE=recorded keeps the recordings throughout.
LESSON_VOICE_ENV = "ACADEMY_LESSON_SHORTS_VOICE"
LESSON_CUES = {"hook": "[confident] ", "cta": "[happy] "}


def lesson_voice() -> str | None:
    """The Fish model for lesson Shorts, or None for the recordings."""
    v = os.getenv(LESSON_VOICE_ENV, "").strip()
    if v.lower() == "recorded":
        return None
    return v or concept_short.MODEL_DEFAULT


async def _render_lesson(work: str, les: lesson_short.Lesson, title: str,
                         beats: list[lesson_short.Beat], model: str | None = "default"
                         ) -> tuple[str, list[str], str | None]:
    from ..content import ffmpeg, stills
    from ..content.drafts import AI_LABEL
    from ..content.render import _write
    from .tutorials import voiced
    if model == "default":
        model = lesson_voice()
    product_slug, course = lesson_short.COURSES[les.course]
    cat = load()
    product = cat.product(product_slug)
    cover_path = lesson_short.COVERS.get(les.course) or (product.cover if product else "")
    cover = reel_design.cover_data(cover_path) if cover_path else ""
    day = datetime.now(UTC).timetuple().tm_yday
    theme = reel_design.theme_for(les.key, day)
    pages, seconds, wavs = [], [], []
    fresh = recorded = 0
    dropped = False
    voice_note = "the lesson's own narration"
    for i, beat in enumerate(beats):
        mp3 = ""
        if model is not None or not beat.audio:
            audio = None
            text = (LESSON_CUES.get(beat.kind, "") + beat.text) if model else beat.text
            for _attempt in range(2):
                audio, note, _why = await stills.narration(
                    voiced(text, model), model=model)
                if audio is not None:
                    break
            if audio is not None:
                mp3 = os.path.join(work, f"said_{i}.mp3")
                await asyncio.to_thread(_write, mp3, audio)
                fresh += 1
                voice_note = note
        if not mp3 and beat.audio:
            mp3 = beat.audio
            recorded += 1
        if not mp3:
            # The close (never recorded) could not be said: the Short ships
            # without it, and the caption still names the store.
            dropped = True
            continue
        try:
            secs = await ffmpeg.duration(mp3) + BEAT_PAD
            wav = os.path.join(work, f"beat_{i}.wav")
            await ffmpeg.run(reel_design.pad_argv(mp3, secs, wav))
        except ffmpeg.FfmpegError as exc:
            return "", [], f"ffmpeg (audio): {exc}"
        seconds.append(secs)
        wavs.append(wav)
        pages.append(reel_design.lesson_page(
            theme, beat.kind, beat.text, lesson=les.lesson, title=title, course=course,
            cover=cover, seconds=secs, label=beat.heading))
    if model is not None:
        voice_note = (f"{voice_note}, Fish {model}: {fresh} beat(s) said fresh"
                      + (f", {recorded} from the lesson's recording" if recorded else ""))
    if dropped:
        voice_note += "; the close was dropped (Fish could not say it)"
    if model is not None and not MIN_SECONDS <= sum(seconds) <= MAX_SECONDS:
        return await _render_lesson(work, les, title, beats, model=None)
    total = sum(seconds)
    if not MIN_SECONDS <= total <= MAX_SECONDS:
        return "", [], f"the Short would run {total:.0f} s, outside {MIN_SECONDS:.0f}-{MAX_SECONDS:.0f}"
    frames = os.path.join(work, "frames")
    try:
        n = await reel_design.render_frames(pages, seconds, frames)
    except Exception as exc:                            # noqa: BLE001
        return "", [], f"the Short could not be drawn ({type(exc).__name__}): {exc}"[:300]
    audio_list = os.path.join(work, "audio.txt")
    await asyncio.to_thread(_write, audio_list, "".join(f"file '{w}'\n" for w in wavs))
    out = os.path.join(work, "out.mp4")
    try:
        await ffmpeg.run(reel_design.assemble_argv(frames, audio_list, out))
    except ffmpeg.FfmpegError as exc:
        return "", [], f"ffmpeg: {exc}"
    said = (f"{total:.0f} s, {n} frames, lesson {les.lesson} of {course}, "
            f"the {theme.name} theme")
    return out, [said, voice_note, f"{AI_LABEL!r} on every frame"], None


async def render_concept_short(key: str) -> ActionResult:
    """A concept Short (concept_short.py): the plan's idea, each line from
    the product's own catalog text, said by the Fish voice over one
    labelled concept animation, in the brand."""
    from ..content import DATA_DIR, ffmpeg, render, stills
    from ..content.drafts import AI_LABEL
    from ..content.render import _write
    from .tutorial_series import plain
    c = concept_short.find(key)
    if c is None:
        return ActionResult(False, f"no concept {key!r}")
    if (why := concept_short.evidence_refusal(c, concept_short.product_text(c.product))):
        return ActionResult(False, why)
    if (why := ffmpeg.missing()) is not None:
        return ActionResult(False, why)
    if not reel_design.available():
        return ActionResult(False, "the concept Shorts need Chromium (Playwright)")
    beats, caption, youtube = concept_short.build(c)
    row = await fetch_one(
        """INSERT INTO content_drafts (kind, source, title, body)
           VALUES ($1, $2, $3, $4) RETURNING id""",
        DRAFT_KIND, key, c.title, plain(" ".join(b.text for b in beats)))
    draft_id = int(row["id"])
    media_id = await render._new_row(draft_id, PROVIDER)
    product = load().product(c.product)
    name = product.title if product else c.product
    cover = reel_design.cover_data(product.cover) if product and product.cover else ""
    chip = f"<b>{reel_design.html.escape(name)}</b>"
    with tempfile.TemporaryDirectory(prefix="academy-concept-") as work:
        pages, seconds, wavs = [], [], []
        from .tutorials import voiced
        model = concept_short.fish_model()
        for i, beat in enumerate(beats):
            audio = no_audio = None
            for _attempt in range(2):
                audio, voice_note, no_audio = await stills.narration(
                    voiced(beat.text, model), model=model)
                if audio is not None:
                    break
            if audio is None:
                return await render._fail(media_id, no_audio or "no narration")
            mp3 = os.path.join(work, f"beat_{i}.mp3")
            await asyncio.to_thread(_write, mp3, audio)
            try:
                secs = await ffmpeg.duration(mp3) + BEAT_PAD
                wav = os.path.join(work, f"beat_{i}.wav")
                await ffmpeg.run(reel_design.pad_argv(mp3, secs, wav))
            except ffmpeg.FfmpegError as exc:
                return await render._fail(media_id, f"ffmpeg (audio): {exc}")
            seconds.append(secs)
            wavs.append(wav)
            panel = (reel_design.concept_panel(reel_design.BRAND, c.motion, step=i,
                                               files=list(c.files))
                     if beat.kind != "cta" else "")
            pages.append(reel_design.lesson_page(
                reel_design.BRAND, beat.kind, plain(beat.text), lesson="", title=c.title,
                course=name, cover=cover, seconds=secs, label=beat.heading,
                panel=panel, chip=chip))
        total = sum(seconds)
        if not MIN_SECONDS <= total <= MAX_SECONDS:
            return await render._fail(media_id, f"the Short would run {total:.0f} s")
        frames = os.path.join(work, "frames")
        try:
            n = await reel_design.render_frames(pages, seconds, frames)
        except Exception as exc:                        # noqa: BLE001
            return await render._fail(media_id, f"the Short could not be drawn: {exc}"[:300])
        audio_list = os.path.join(work, "audio.txt")
        await asyncio.to_thread(_write, audio_list, "".join(f"file '{w}'\n" for w in wavs))
        out = os.path.join(work, "out.mp4")
        try:
            await ffmpeg.run(reel_design.assemble_argv(frames, audio_list, out))
        except ffmpeg.FfmpegError as exc:
            return await render._fail(media_id, f"ffmpeg: {exc}")
        result = await render._finish(
            media_id, draft_id, "academy", out,
            [f"{total:.0f} s, {n} frames, the concept Short \"{c.title}\"", voice_note,
             f"{AI_LABEL!r} and \"{reel_design.CONCEPT_TAG}\" on screen"], captions=True)
    if not result.ok:
        return result
    return ActionResult(True, f"concept {c.id}: {result.detail}",
                        evidence={**(result.evidence or {}), "slug": key, "caption": caption,
                                  "youtube_caption": youtube, "dir": DATA_DIR},
                        rollback_token=result.rollback_token)


def reel_kind() -> str:
    """`ACADEMY_REEL_KIND`: `lesson` (default since 2026-10-11, the owner: Shorts
    about each lesson's important part) or `product` (the store promo)."""
    kind = os.getenv("ACADEMY_REEL_KIND", "lesson").strip().lower()
    return kind if kind in ("lesson", "product") else "lesson"


def lesson_candidates(shown: dict[str, datetime]) -> list[str]:
    """Every lesson in rotation order: never shown first, in the interleaved
    course order; then the one shown longest ago."""
    # A lesson with no part that reads on its own (8 of 132) is skipped
    # here, so a slot never spends itself on a render that will refuse.
    order = [les for les in lesson_short.rotation(lesson_short.lessons())
             if lesson_short.build(les) is not None]
    floor = datetime.min.replace(tzinfo=UTC)
    ranked = sorted(enumerate(order), key=lambda e: (shown.get(e[1].key, floor), e[0]))
    return [les.key for _i, les in ranked]


def next_lesson(shown: dict[str, datetime]) -> str | None:
    """The next lesson in rotation, or None when there are none."""
    order = lesson_candidates(shown)
    return order[0] if order else None


# TikTok flagged Academy Shorts as spam when the same product came back
# within a day (the Automation Pack, 2026-10-11 to 10-13). A Short whose
# product was posted in the last TIKTOK_PRODUCT_GAP_H hours goes to YouTube
# only; TikTok gets that product next time round.
TIKTOK_PRODUCT_GAP_H = 48


def product_of(slug: str) -> str:
    """The store product a Short is about: a product reel's slug, a lesson's
    course product, a concept's product."""
    if slug.startswith("lesson:"):
        course = slug.split(":", 1)[1].split("/", 1)[0]
        return lesson_short.COURSES.get(course, ("", ""))[0]
    if slug.startswith("concept:"):
        c = concept_short.find(slug)
        return c.product if c else ""
    return slug.removeprefix("academy:")


def legs_for(slug: str, shown: dict[str, datetime],
             now: datetime | None = None) -> str:
    """'' (every default leg), or 'youtube' when the same product was
    posted inside TIKTOK_PRODUCT_GAP_H."""
    now = now or datetime.now(UTC)
    mine = product_of(slug)
    if not mine:
        return ""
    cutoff = now - timedelta(hours=TIKTOK_PRODUCT_GAP_H)
    recent = any(product_of(k) == mine and at > cutoff
                 for k, at in shown.items() if k != slug)
    return "youtube" if recent else ""


# A lesson can still refuse at render: the check above has no audio, and its
# parts' real lengths can leave none that fits a Short. On 2026-10-12 the
# 7 pm slot picked lesson 1.1, the render refused ("no part of this lesson
# is clean to show") and the slot posted nothing. The slot now moves on to
# the next lesson, up to this many, before it gives up.
LESSON_TRIES = 5
UNSHOWABLE = "no part of this lesson is clean to show"


async def last_shown() -> dict[str, datetime]:
    """When each product last had a reel posted (academy_promo_posts)."""
    from ...kernel.state import fetch_all
    rows = await fetch_all(
        "SELECT slug, max(at) AS at FROM academy_promo_posts GROUP BY slug")
    return {str(r["slug"]): r["at"] for r in rows}


def next_slug(products: list[Product], shown: dict[str, datetime]) -> str | None:
    """Rotation: the product shown longest ago (never shown first), in
    catalog order between equals."""
    if not products:
        return None
    floor = datetime.min.replace(tzinfo=UTC)
    return min(products, key=lambda p: shown.get(p.slug, floor)).slug


async def posted_today() -> bool:
    """True when this slot has nothing left to do: a post landed in the last
    MIN_GAP_H hours, or today's allowance (UTC date; clips
    CLIPS_PROMO_PER_DAY, two since 2026-10-11) is spent."""
    from ..clips import promo
    recent = await fetch_one(
        "SELECT 1 AS x FROM academy_promo_posts WHERE at > $1 LIMIT 1",
        datetime.now(UTC) - timedelta(hours=MIN_GAP_H))
    if recent is not None:
        return True
    return await promo.posted_today() >= promo.per_day()


JOB = "academy_reel"


async def daily() -> str:
    """Render the next product's reel and post it to the clips accounts
    through the executor (Tier 1, gated in `clips.autopublish_promo`).
    Returns one line for the log.

    The day is claimed in Postgres first (`kernel.occurrence`), not only
    by the date the scheduler holds in memory. On 2026-10-10 three deploys
    reset that date and the job rendered "AI for Every Age" four times. A
    failed render releases the claim so that a later tick retries. A held card,
    a post, or a refusal settles the day."""
    from ...kernel import occurrence
    from ...kernel.executor import ExecutionRequest, execute
    from ..clips import promo
    from . import notebooklm
    # the owner, 2026-10-19: NotebookLM videos only, the Fish promo Short too. It
    # comes back only with ACADEMY_NOTEBOOKLM_FALLBACK switched on.
    if not notebooklm.fallback_enabled():
        return ("the Fish promo Short is off: NotebookLM videos only "
                "(ACADEMY_NOTEBOOKLM_FALLBACK is off)")
    # One claim per slot, not per date: two Shorts a day (the owner, 2026-10-11),
    # each slot claimed once however often the box restarts.
    now = datetime.now(UTC)
    passed = [h for h in hours_utc() if h <= now.hour]
    window = f"{promo.day_start(now).date().isoformat()}:{passed[-1] if passed else 0}"
    if not await occurrence.claim(JOB, window):
        return "this slot's reel is already claimed"
    try:
        if await posted_today():
            await occurrence.done(JOB, window)
            return "already posted today"
        shown = await last_shown()
        first_slot = bool(passed) and passed[-1] == hours_utc()[0]
        if concept_short.enabled() and first_slot:
            # the owner, 2026-10-12: one concept Short and one lesson Short a day,
            # once he has seen the concept previews (ACADEMY_CONCEPT_SHORTS).
            slug = concept_short.next_concept(shown)
        elif reel_kind() == "lesson":
            slug = next_lesson(shown)
            if slug is None:
                await occurrence.done(JOB, window)
                return "no lessons on the server to show"
        else:
            slug = next_slug(sellable(load()), shown)
            if slug is None:
                await occurrence.done(JOB, window)
                return "nothing sellable to show"
        last = shown.get(slug)
        if last is not None and last > datetime.now(UTC) - timedelta(days=promo.repost_days()):
            await occurrence.done(JOB, window)
            return (f"{slug} was shown {last:%Y-%m-%d}; nothing new to show inside "
                    f"{promo.repost_days()} days")
        made = await execute(ExecutionRequest(
            action="academy.render_promo_reel", params={"slug": slug},
            reason=f"the daily Academy reel: {slug}"))
        if not made.ok and UNSHOWABLE in made.detail and slug.startswith("lesson:"):
            for other in [k for k in lesson_candidates(shown) if k != slug][:LESSON_TRIES - 1]:
                slug = other
                made = await execute(ExecutionRequest(
                    action="academy.render_promo_reel", params={"slug": slug},
                    reason=f"the daily Academy reel: {slug} (the lesson before had "
                           f"no part to show)"))
                if made.ok or UNSHOWABLE not in made.detail:
                    break
        if not made.ok:
            await occurrence.release(JOB, window)
            return f"render refused: {made.detail}"
        media_id = int((made.evidence or {}).get("media_id") or 0)
        caption = str((made.evidence or {}).get("caption") or "")
        params = {"media_id": media_id, "caption": caption, "slug": slug}
        if legs := legs_for(slug, shown):
            params["legs"] = legs
        if yt := str((made.evidence or {}).get("youtube_caption") or ""):
            params["youtube_caption"] = yt
        posted = await execute(ExecutionRequest(
            action="clips.autopublish_promo",
            params=params,
            reason=f"the daily Academy reel: {slug}"))
    except BaseException:
        await occurrence.release(JOB, window)
        raise
    await occurrence.done(JOB, window)
    return f"{slug}: {posted.detail}"
