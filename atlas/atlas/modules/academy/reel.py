"""A daily Academy reel for the clips accounts, made here on the server.

Miro, 2026-10-06: "keep it in clips since it's the most viewed profile ...
make continuous reels about ATLAS Academy to show people". The clips
accounts (Instagram @atlas.clipsdrop, TikTok @atlasclips12, the clips
YouTube channel) have the followers; the Academy's store needs showing.

What one reel is: a product from `catalog.json`, in rotation, told in its
own words -- the title, a hook written for its kind, two rows from its
label (the counted facts), the summary's first sentence and the store --
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
import tempfile
from datetime import UTC, datetime, timedelta

from ...kernel.contracts import ActionResult
from ...kernel.state import execute_sql, fetch_one
from . import reel_design
from .catalog import Catalog, Product, load

log = logging.getLogger("atlas.academy.reel")

DRAFT_KIND = "academy_promo"
PROVIDER = "academy"
STORE = "shop.mapfront.io"
DEFAULT_HOUR_UTC = 16                  # noon in New York, where the audience is
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


def script_for(p: Product) -> list[tuple[str, str]]:
    """The reel's beats as (what is shown, what is said), every word from
    the catalog or the fixed hooks."""
    hook = HOOKS.get(p.kind, DEFAULT_HOOK)
    beats = [(p.title, f"{p.title}. {hook}"),
             (hook, _sentence(p.summary))]
    for _name, value in p.label[:2]:
        beats.append((_short(value), _sentence(value)))
    beats.append((f"{STORE}\nlink in bio", f"It's on ATLAS Academy, at {STORE}. Link in bio."))
    return beats


KINDS = ("title", "hook", "fact", "fact", "cta")


def beat_kinds(beats: list[tuple[str, str]]) -> list[str]:
    """What each beat is, for the edition's layout: title, hook, the
    facts in between, the store last."""
    return ["title", "hook"] + ["fact"] * (len(beats) - 3) + ["cta"]


def variant_for(day: datetime | None = None) -> int:
    """The day variant: even days lead with the title, odd days with the
    hook, so the same product's next reel opens differently."""
    return (day or datetime.now(UTC)).timetuple().tm_yday % 2


def caption_for(p: Product) -> str:
    from ..content.drafts import AI_LABEL
    price = min((o.price_usd for o in p.offers if o.buy_live and o.price_usd is not None),
                default=None)
    price_said = f" From ${price:g}." if price is not None else ""
    # The title on its own line: it is the YouTube Short's title
    # (promo.youtube_title takes the first line's first sentence, whole).
    return (f"{p.title}\n{_sentence(p.summary)}{price_said} "
            f"On ATLAS Academy at {STORE}, link in bio. "
            f"#aitools #smallbusiness #learnai\n\n{AI_LABEL}")


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


async def render_promo_reel(slug: str) -> ActionResult:
    """One product's reel, written under content.DATA_DIR as a finished
    content_media row (status done), the way the reels lane's renders are,
    so `clips.publish_promo` / `autopublish_promo` take it unchanged."""
    from ..content import DATA_DIR, ffmpeg, render
    cat = load()
    p = cat.product(slug)
    if p is None:
        return ActionResult(False, f"no product {slug!r} in the catalog")
    if not any(o.buy_live for o in p.offers):
        return ActionResult(False, f"{p.title} has no live Buy; not shown")
    if (why := ffmpeg.missing()) is not None:
        return ActionResult(False, why)
    beats = script_for(p)
    kinds = beat_kinds(beats)
    variant = variant_for()
    if variant and len(beats) > 2:
        beats[0], beats[1] = beats[1], beats[0]
        kinds[0], kinds[1] = kinds[1], kinds[0]
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
    return ActionResult(True, f"{p.title}: {result.detail}",
                        evidence={**(result.evidence or {}), "slug": p.slug,
                                  "caption": caption_for(p), "dir": DATA_DIR},
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
            first = i == 0 and cover and os.path.isfile(cover)
            pictures.append(await _frame(work, i, shown=shown, title=p.title,
                                         cover=cover if first else ""))
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
    row = await fetch_one(
        "SELECT 1 AS x FROM academy_promo_posts WHERE at > $1 LIMIT 1",
        datetime.now(UTC) - timedelta(hours=20))
    return row is not None


async def daily() -> str:
    """Render the next product's reel and post it to the clips accounts
    through the executor (Tier 1, gated in `clips.autopublish_promo`).
    Returns one line for the log."""
    from ...kernel.executor import ExecutionRequest, execute
    if await posted_today():
        return "already posted today"
    cat = load()
    slug = next_slug(sellable(cat), await last_shown())
    if slug is None:
        return "nothing sellable to show"
    made = await execute(ExecutionRequest(
        action="academy.render_promo_reel", params={"slug": slug},
        reason=f"the daily Academy reel: {slug}"))
    if not made.ok:
        return f"render refused: {made.detail}"
    media_id = int((made.evidence or {}).get("media_id") or 0)
    caption = str((made.evidence or {}).get("caption") or "")
    posted = await execute(ExecutionRequest(
        action="clips.autopublish_promo",
        params={"media_id": media_id, "caption": caption, "slug": slug},
        reason=f"the daily Academy reel: {slug}"))
    return f"{slug}: {posted.detail}"
