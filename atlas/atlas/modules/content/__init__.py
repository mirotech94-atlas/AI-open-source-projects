"""content — voice notes, the Sunday call, a good Slack thread, into drafts.

What it watches: how many drafted rows in `content_drafts` are still
`status='draft'`, waiting on Miro to read them. A pile past ten means he
has stopped reading them, not that there is nothing to write.

What it may do: `content.draft` (Tier AUTO) — read one of four sources
(`atlas.modules.content.sources.gather`: a voice transcript, the latest
weekly report, a Slack thread captured to memory, or a note he dictates
directly) and write a draft row, in one of six kinds now that Phase 18
adds `story_script` and `course_outline` to the original four. It never
posts anywhere, never sends anything, and never talks to a third party.

Phase 18 adds `content.render_video` (Tier AUTO): turn an approved
`story_script` draft into an actual video via the OpenArt MCP
(`OPENART_MCP_URL`, `OPENART_TOKEN`), through `atlas.modules._mcp.McpClient`
— the client Task 39 unified with `atlas.modules.agency._mcp` (Task 3);
`render_video` calls its `call_tool()` method, which returns OpenArt's raw
`tools/call` result rather than the GetLayers-facing `call()`'s joined text
(see that module's docstring). It is still Tier AUTO because nothing it
does reaches a follower:
`instagram.publish` is the separate, Tier ASK step where a human decides
whether the result is worth posting. `RENDER_MONTHLY_CAP` (default 200) is
a credit cap, not a purchase — OpenArt is paid for on a plan Miro already
holds, and this only refuses to spend further credits already committed,
checked BEFORE the MCP is ever called. `ai_label` is written as the
literal `TRUE` in the `content_media` insert; there is no parameter that
could set it false.

**Three render backends** (Task 55), chosen per call by
`render.choose_backend`: **veo** (real motion, one 8 s Veo clip per beat
with a shared character sheet, Groq-timed captions burned in with libass;
`GEMINI_API_KEY`, under `VEO_MONTHLY_SECONDS` for the whole story, checked
before the first clip; Veo bills per second, and it runs unattended under
that cap exactly as OpenArt does under `RENDER_MONTHLY_CAP` -- the cap is
Miro's standing approval, ruled in Task 55, and `render_backend` shows the
seconds used against it), else **openart** (`OPENART_TOKEN`, the path below),
else **local** (free: Gemini stills or typographic cards, Fish narration,
ffmpeg zoompan, "Made with AI" burned into every frame). ffmpeg missing is
"ffmpeg not installed", never a guess, and `render_backend` reads UNKNOWN.
Every prompt passes `guard.refusal` -- no real brand, no real person --
before anything is sent anywhere. The OpenArt credit cap governs OpenArt
only; Veo has its own seconds cap and the local path costs nothing.

`render_video`'s OpenArt tool name and argument shape were never confirmed
against the real service, so — the same discipline `_perplexity.py` uses
for its own remote tools — it calls `list_tools()` first and refuses, by
name, when the server does not offer `RENDER_TOOL`, rather than guessing a
`tools/call` would work. The arguments sent are read off that tool's own
`inputSchema` too, falling back to the originally-assumed `{"script": ...}`
shape when the schema names nothing more specific.

A successful render's file is saved locally under `DATA_DIR`
(`data/content/<media_id>.mp4`) and OpenArt's own `video_url` (and its
stated expiry, if it gives one) are kept on the `content_media` row for the
record. But an `instagram.publish` proposal needs a URL Instagram's own
Graph API can fetch when it publishes — OpenArt's link may not stay valid
that long, and serving `data/content` itself was never this module's
decision. So the row also gets its own `media_token`
(`secrets.token_urlsafe(32)`, good for 24 hours): `GET /media/{token}`
(`atlas/api/media.py`) serves exactly that file to whoever holds it, no
session in front, the token the entire defence.

**Publishing is Tier ASK, always.** `instagram.publish` and
`tiktok.upload_draft` exist, both Tier ASK, because a post under Miro's
name is his decision to make. The one publish action here,
`content.publish_reel` (Task 55, `crosspost.py`), is Tier ASK too: one
card that sends an AI-stories reel to the stories Instagram account, the
Atlas Stories Facebook Page and TikTok (through Postiz), per-leg status on the
card, with a rollback that takes back what it can and says what it cannot. LinkedIn has no publish action at
all, anywhere in ATLAS, by design: `atlas.modules.linkedin` mirrors an
external agent that drives a browser a human authenticated, and the row
`content.draft` writes is only ever the text he pastes into it himself.

Endpoints this assumes: Postgres (`voice_turns`, `reports`, `memories`,
`content_media`, `render_spend`), an LLM (`kernel.llm.think`), and the
OpenArt MCP server at `OPENART_MCP_URL` — a URL and a JSON-RPC shape
neither of which has been confirmed against the real service, so Miro can
correct them.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    Health,
    Observation,
    Severity,
    Tier,
)
from ...kernel.state import execute_sql, fetch_one
from .._mcp import McpClient
from . import (
    calendar,
    course,
    crosspost,
    ffmpeg,
    football,
    guard,
    higgsfield,
    legsync,
    metrics,
    reach,
    reel_script,
    reels,
    render,
    series,
    shorts,
    veo,
)
from .crosspost import (
    check_reel_legs,
    publish_reel,
    publish_reel_dry,
    topic_for_publish_reel,
    unpublish_reel,
    vet_publish_reel,
)
from .drafts import AI_LABEL, KINDS, draft, draft_dry, draft_rollback

DEGRADED_ABOVE = 10

OPENART_MCP_URL_DEFAULT = "https://mcp.openart.ai/mcp"
RENDER_MONTHLY_CAP_DEFAULT = 200
DATA_DIR = "data/content"

# The tool name this module has always assumed OpenArt's MCP server offers.
# Never called blind any more: `render_video` checks `list_tools()` first
# and refuses, naming this constant, when the server does not offer it.
RENDER_TOOL = "render_video"

# How long a token minted for `GET /media/{token}` stays good. Instagram's
# publish call needs the file to still be there when it fetches it, not
# forever -- a stale token is one more thing that can leak the file later.
MEDIA_TOKEN_TTL = timedelta(hours=24)

log = logging.getLogger("atlas.content")


async def check_drafts() -> Observation:
    """Drafts waiting on Miro. Past ten, the pile is the finding — writing
    more would not help; only he can clear it by reading them."""
    try:
        # A reel's script is reviewed on its reel card, not in the drafts
        # pile: counted here, fifteen reels already rendered, carded and
        # posted read as "he stopped reading" (2026-09-27).
        row = await fetch_one(
            """SELECT count(*) AS n, min(at) AS oldest FROM content_drafts
                WHERE status = 'draft' AND kind <> 'story_script'""")
    except Exception as exc:                            # noqa: BLE001
        return Observation("drafts", Health.UNKNOWN, detail=str(exc))
    if row is None:
        return Observation(
            "drafts", Health.UNKNOWN,
            detail="content_drafts count query returned nothing")
    n = int(row["n"])
    health = Health.DEGRADED if n > DEGRADED_ABOVE else Health.OK
    # What is measured, not why: "the pile means he stopped reading them"
    # guessed at his behaviour (the 2026-09-27 Slack review).
    oldest = row.get("oldest") if hasattr(row, "get") else None
    if isinstance(oldest, datetime):
        days = (datetime.now(UTC) - oldest).total_seconds() / 86400
        age = f"oldest {days:.0f} day(s)" if days >= 1 else "oldest under a day"
    else:
        age = "oldest age unknown"
    detail = f"Drafts awaiting review: {n}; {age}"
    if health is Health.DEGRADED:
        detail += f" (more than {DEGRADED_ABOVE})"
    return Observation("drafts", health, float(n), "drafts", detail)


def _month_key(at: datetime | None = None) -> str:
    return (at or datetime.now(UTC)).strftime("%Y-%m")


def _render_cap() -> int:
    return int(os.getenv("RENDER_MONTHLY_CAP", str(RENDER_MONTHLY_CAP_DEFAULT)))


async def _spent_this_month(month: str) -> int:
    row = await fetch_one(
        "SELECT credits FROM render_spend WHERE month = $1", month)
    return int(row["credits"]) if row else 0


def _save_media_file(path: str, content: bytes) -> None:
    """Blocking file I/O, run under `asyncio.to_thread` -- this repo is
    async throughout, and a bare `open()` in a coroutine blocks the event
    loop for every other check and action while a render is downloading."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(content)


async def _charge(month: str, credits: int) -> None:
    await execute_sql(
        """INSERT INTO render_spend (month, credits) VALUES ($1, $2)
           ON CONFLICT (month)
           DO UPDATE SET credits = render_spend.credits + $2""",
        month, credits)


async def _draft_for_render(draft_id: int) -> tuple[dict | None, str | None]:
    draft_row = await fetch_one(
        "SELECT id, kind, body FROM content_drafts WHERE id = $1", draft_id)
    if draft_row is None:
        return None, f"no draft {draft_id}"
    if draft_row["kind"] != "story_script":
        return None, (f"draft {draft_id} is a {draft_row['kind']!r}, not a "
                      f"story_script — only story scripts render to video")
    return draft_row, None


def _render_arguments(spec: dict, script: str) -> dict[str, Any]:
    """Build `render_video`'s call arguments from the tool's own
    `inputSchema` -- the same idea `_perplexity._arguments` uses for its own
    remote tools -- falling back to the originally-assumed `{"script": ...}`
    shape when the schema names nothing more specific. Never raises: an
    unreadable schema just means the fallback is used."""
    schema = spec.get("inputSchema") if isinstance(spec, dict) else None
    props = (schema or {}).get("properties") or {}
    for name in ("script", "text", "prompt", "content"):
        if name in props:
            return {name: script}
    required = (schema or {}).get("required") or []
    if len(required) == 1:
        return {required[0]: script}
    return {"script": script}


def _media_url(token: str) -> str | None:
    """The absolute URL Instagram's Graph API can actually fetch when it
    publishes -- `/media/{token}` alone is a path, not something a third
    party outside this box can reach. Built from `ATLAS_HOST` the same way
    `atlas.channels.twilio` builds its own callback URLs. `None` when
    `ATLAS_HOST` is unset, so the caller can say so rather than hand out a
    path nothing outside can resolve."""
    host = os.getenv("ATLAS_HOST", "").strip()
    return f"https://{host}/media/{token}" if host else None


async def public_link(media_id: int) -> tuple[str | None, datetime | None]:
    """A finished render's public `/media/{token}` URL and when it lapses,
    read from the row -- for a card's watch link, so the token never has to
    ride in an action's evidence (which the executor audits). `(None, None)`
    when there is no live link or ATLAS_HOST is unset."""
    row = await fetch_one(
        """SELECT media_token, media_token_expires_at FROM content_media
           WHERE id = $1 AND status = 'done' AND media_token_expires_at > now()""",
        media_id)
    if row is None or not row["media_token"]:
        return None, None
    url = _media_url(row["media_token"])
    return (url, row["media_token_expires_at"]) if url else (None, None)


async def rendered_path(media_id: int) -> str | None:
    """A finished render's file on disk, for a caller that must hand the
    file itself to someone (the card's Slack preview). None when the render
    is not done or the file is gone."""
    row = await fetch_one(
        "SELECT path FROM content_media WHERE id = $1 AND status = 'done'",
        media_id)
    path = row["path"] if row else None
    if not path or not await asyncio.to_thread(os.path.isfile, path):
        return None
    return path


def _parse_openart_expiry(value: Any) -> datetime | None:
    """OpenArt's own stated expiry for its `video_url`, if it gives one --
    its shape has never been confirmed against the real service (see the
    module docstring), so anything that is not a parseable ISO-8601
    timestamp is kept as "not given" rather than guessed at."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _prompt_refusal(script: reel_script.Script) -> str | None:
    """No real brand or person in anything sent to a video or image model.
    A football reel's words may name real players (they are captions, never
    sent to the model); its shots come from a closed catalog."""
    if football.is_football(script):
        return football.text_refusal(script)
    if course.is_course(script):
        return course.text_refusal(script)
    return guard.refusal("\n".join([script.characters, *script.scenes]))


BACKENDS = ("auto",)
NO_SLIDES = ("no slideshow reels: every reel is real motion video, so the "
             "free stills render is never used for one (Miro, 2026-09-27)")


async def _choice(script: reel_script.Script, backend: str) -> render.Choice:
    return await render.choose_backend(script)


async def render_video_dry(draft_id: int, backend: str = "auto") -> ActionResult:
    if backend not in BACKENDS:
        return ActionResult(False, f"refused: backend must be one of {BACKENDS}")
    draft_row, reason = await _draft_for_render(draft_id)
    if reason is not None:
        return ActionResult(False, reason)
    script = reel_script.parse(draft_row["body"])
    if (why := _prompt_refusal(script)) is not None:
        return ActionResult(False, f"refused: {why}")
    if football.is_football(script) and (
            why := await football.verify(draft_id, script)) is not None:
        return ActionResult(False, f"refused: {why}")
    # The course lane: the draft is exactly its lesson's, honest by rule, and
    # no screen is ever asked of a video model -- or no clip is asked at all.
    if course.is_course(script) and (
            why := await course.verify(draft_id, script)) is not None:
        return ActionResult(False, f"refused: {why}")
    choice = await _choice(script, backend)
    if choice.backend == "local":
        return ActionResult(False, f"refused: {NO_SLIDES} ({choice.why})")
    if choice.backend != "openart":
        if (missing := ffmpeg.missing()) is not None:
            return ActionResult(False, f"refused: {missing} ({choice.why})")
        return ActionResult(True, f"would render draft {draft_id} with the "
                                  f"{choice.backend} backend — {choice.why}")

    month = _month_key()
    cap = _render_cap()
    spent = await _spent_this_month(month)
    if spent >= cap:
        return ActionResult(
            False, f"render cap reached: {spent}/{cap} credits spent in "
                   f"{month}; refusing before calling OpenArt")
    return ActionResult(
        True, f"would render draft {draft_id} via OpenArt "
              f"({spent}/{cap} credits used in {month})")


async def render_video(draft_id: int, backend: str = "auto") -> ActionResult:
    """Render an approved `story_script` draft into a video via the OpenArt
    MCP. The cap is checked, and the request refused, BEFORE `McpClient` is
    ever constructed — a render that never happened must never be charged,
    and a cap that has already been reached must never reach OpenArt at
    all. `ai_label` is never a parameter here: the `content_media` insert
    writes the literal TRUE, so there is no path that could set it false.
    """
    draft_row, reason = await _draft_for_render(draft_id)
    if reason is not None:
        return ActionResult(False, reason)
    script = reel_script.parse(draft_row["body"])
    if (why := _prompt_refusal(script)) is not None:
        return ActionResult(False, f"refused: {why}")
    if backend not in BACKENDS:
        return ActionResult(False, f"refused: backend must be one of {BACKENDS}")
    # Every number on screen equals a fetched value, or no clip is asked.
    if football.is_football(script) and (
            why := await football.verify(draft_id, script)) is not None:
        return ActionResult(False, f"refused: {why}")
    # The course lane: the draft is exactly its lesson's, honest by rule, and
    # no screen is ever asked of a video model -- or no clip is asked at all.
    if course.is_course(script) and (
            why := await course.verify(draft_id, script)) is not None:
        return ActionResult(False, f"refused: {why}")
    choice = await _choice(script, backend)
    if choice.backend in render.VEO_BACKENDS:
        # No stills fallback any more (Miro, 2026-09-27: "action and movement
        # ... not just slides"): a Veo quota bump fails the render, says so,
        # and the reel waits for tomorrow's clips -- unless Higgsfield is
        # configured: then what Veo's day or month cannot make is made
        # there, under HIGGSFIELD_MONTHLY_USD (Miro, 2026-09-29).
        return await render.render_veo(draft_id, script)
    if choice.backend == "local":
        return ActionResult(False, f"refused: {NO_SLIDES} ({choice.why})")
    return await _render_openart(draft_id, draft_row)


async def _render_openart(draft_id: int, draft_row: dict) -> ActionResult:
    month = _month_key()
    cap = _render_cap()
    spent = await _spent_this_month(month)
    if spent >= cap:
        return ActionResult(
            False, f"render cap reached: {spent}/{cap} credits spent in "
                   f"{month}; refusing before calling OpenArt")

    media_row = await fetch_one(
        """INSERT INTO content_media (draft_id, provider, kind, status, ai_label)
           VALUES ($1, 'openart', 'video', 'rendering', TRUE) RETURNING id""",
        draft_id)
    media_id = media_row["id"]

    client = McpClient(os.getenv("OPENART_MCP_URL", OPENART_MCP_URL_DEFAULT),
                       os.getenv("OPENART_TOKEN", ""),
                       # a video render is slower than a GetLayers prose
                       # reply, and a refused token should say OpenArt, not
                       # inherit the shared client's GetLayers default
                       timeout=60.0, service="OpenArt", token_env="OPENART_TOKEN")

    async def _fail(reason: str) -> ActionResult:
        await execute_sql(
            "UPDATE content_media SET status = 'failed' WHERE id = $1", media_id)
        return ActionResult(False, reason)

    # initialize() before list_tools()/call_tool() -- the MCP spec expects a
    # session handshake first, and a real streamable-HTTP server may refuse
    # tools/list without one. The same order _perplexity._via_mcp uses.
    _init, init_reason = await client.initialize()
    if init_reason is not None:
        return await _fail(f"OpenArt MCP unreachable: {init_reason}")

    tools, list_reason = await client.list_tools()
    if list_reason is not None:
        return await _fail(f"OpenArt MCP unreachable: {list_reason}")
    offered = {t["name"]: t for t in tools
              if isinstance(t, dict) and isinstance(t.get("name"), str)}
    if RENDER_TOOL not in offered:
        return await _fail(
            f"OpenArt has no {RENDER_TOOL} tool (it offers: "
            f"{', '.join(sorted(offered)) or 'nothing'}) — refusing rather "
            f"than guess the call would work")

    result, mcp_reason = await client.call_tool(
        RENDER_TOOL, _render_arguments(offered[RENDER_TOOL], draft_row["body"]))
    if mcp_reason is not None:
        return await _fail(f"OpenArt render failed: {mcp_reason}")

    credits_used = int((result or {}).get("credits", 0))
    video_url = (result or {}).get("video_url")
    video_url_expires_at = _parse_openart_expiry((result or {}).get("expires_at"))
    path = f"{DATA_DIR}/{media_id}.mp4"
    media_token: str | None = None
    media_token_expires_at: datetime | None = None
    if video_url:
        try:
            async with httpx.AsyncClient(timeout=180) as c:
                downloaded = await c.get(video_url)
                downloaded.raise_for_status()
            await asyncio.to_thread(_save_media_file, path, downloaded.content)
        except Exception as exc:                        # noqa: BLE001
            return await _fail(
                f"OpenArt rendered but the file could not be saved: {exc}")
        # Only minted once the file is actually on disk under DATA_DIR --
        # a token with nothing behind it would just be a 404 waiting to
        # happen, and `/media/{token}` (atlas/api/media.py) is the ONLY
        # thing that can ever serve it.
        media_token = secrets.token_urlsafe(32)
        media_token_expires_at = datetime.now(UTC) + MEDIA_TOKEN_TTL

    await execute_sql(
        """UPDATE content_media
           SET status = 'done', path = $2, external_id = $3, credits = $4,
               video_url = $5, video_url_expires_at = $6,
               media_token = $7, media_token_expires_at = $8
           WHERE id = $1""",
        media_id, path if media_token else None, (result or {}).get("id"),
        credits_used, video_url, video_url_expires_at,
        media_token, media_token_expires_at)
    await _charge(month, credits_used)

    # The URL carries the unguessable media_token, so it stays OUT of both
    # `detail` and `evidence`: `execute.finish` writes both into the
    # immutable actions_log (kernel/executor.py, `_capped_evidence`), and a
    # token that leaked into that append-only chain could never be revoked,
    # only outlived by its 24h expiry. The run is identified by media id; a
    # caller that needs the link reads it from the row (`public_link`).
    detail = (f"rendered a video for draft {draft_id} ({credits_used} "
             f"credit(s), {spent + credits_used}/{cap} this month); media "
             f"id {media_id}")
    media_url = _media_url(media_token) if media_token else None
    if media_token and media_url is None:
        detail += " — ATLAS_HOST is unset, so no public URL could be built"
    elif media_url:
        detail += " — publicly reachable"
    else:
        detail += " — OpenArt gave no video_url, so nothing is publicly reachable"

    return ActionResult(
        True, detail,
        evidence={"media_id": media_id, "credits": credits_used,
                 "path": path if media_token else None,
                 "public": media_url is not None},
        rollback_token=str(media_id))


async def render_video_rollback(token: str) -> ActionResult:
    """Marks the media row failed. Deletes nothing — the render already
    spent real credits, and a rollback that erased the row would erase the
    only record of that spend."""
    await execute_sql(
        "UPDATE content_media SET status = 'failed' WHERE id = $1", int(token))
    return ActionResult(True, f"media {token} marked failed")


async def check_render_backend() -> Observation:
    """Which backend a reel would render with right now, and whether this
    box can run it: ffmpeg and its drawtext / ass / zoompan filters, Veo's
    seconds this month against its cap (with the time they were read), and
    the narration and caption keys. UNKNOWN whenever the part the chosen
    backend needs could not be confirmed -- never OK on an assumption."""
    caps = await ffmpeg.capabilities()
    at = datetime.now(UTC)
    parts: list[str] = []
    unknown: list[str] = []
    evidence: dict[str, Any] = {"ffmpeg": caps.ffmpeg, "drawtext": caps.drawtext,
                                "ass": caps.ass, "zoompan": caps.zoompan}
    if not caps.ffmpeg:
        unknown.append(caps.reason or "ffmpeg not installed")
    else:
        parts.append("ffmpeg filters: " + ", ".join(
            f"{name} {'yes' if ok else 'not confirmed'}" for name, ok in
            (("drawtext", caps.drawtext), ("ass", caps.ass),
             ("zoompan", caps.zoompan))))
    if veo.configured():
        try:
            used = await veo.seconds_this_month()
            evidence["veo_seconds"] = used
            parts.append(f"Veo {used}/{veo.monthly_cap()} s this month "
                         f"(read {at:%H:%M} UTC, model {veo.model()})")
        except Exception as exc:                        # noqa: BLE001
            from .._http import explain
            unknown.append(f"Veo spend unreadable: {explain(exc)}")
    else:
        parts.append("Veo not configured (GEMINI_API_KEY unset)")
    probe = reel_script.Script("", ("beat",) * render.veo_max_beats(), AI_LABEL)
    choice = await render.choose_backend(probe)
    evidence["backend"] = choice.backend
    if choice.backend == render.HIGGSFIELD_ONLY:
        # Veo is off (Miro, 2026-09-30): the render stands or falls with
        # Higgsfield, so its own check is this check's verdict -- FAILING
        # while Higgsfield refuses (no credits), never OK on Veo's account.
        budget = await higgsfield.check_budget()
        evidence["higgsfield"] = budget.health
        detail = f"backend now: {choice.backend} — {choice.why}; Higgsfield: {budget.detail}"
        health = budget.health
        if not caps.ffmpeg or not caps.ass:
            health, detail = Health.UNKNOWN, ("; ".join(unknown or [
                "ffmpeg's ass filter not confirmed"]) + f" — {detail}")
        return Observation("render_backend", health, detail=detail,
                           evidence=evidence)
    if choice.backend in render.VEO_BACKENDS and caps.ffmpeg and not caps.ass:
        unknown.append("the Veo path burns captions with libass, and this "
                       "ffmpeg does not list the ass filter")
    if choice.backend == "local" and caps.ffmpeg and not (caps.drawtext
                                                          and caps.zoompan):
        unknown.append("the local path needs drawtext and zoompan, and this "
                       "ffmpeg does not list both")
    parts.append("captions: " + ("Groq set" if os.getenv("GROQ_API_KEY")
                                 else "GROQ_API_KEY unset, no word timings"))
    parts.append("narration: " + (
        "Fish set" + ("" if os.getenv("REELS_FISH_VOICE_ID")
                      else ", REELS_FISH_VOICE_ID unset (default voice)")
        if os.getenv("FISH_AUDIO_API_KEY") else "FISH_AUDIO_API_KEY unset"))
    detail = f"backend now: {choice.backend} — {choice.why}; " + "; ".join(parts)
    if unknown:
        return Observation("render_backend", Health.UNKNOWN,
                           detail="; ".join(unknown) + f" — {detail}",
                           evidence=evidence)
    # Veo has no balance to read; its last refusal is the reading. A 402
    # (2026-09-30) stops every Veo beat until Miro tops up, so this is
    # FAILING, with the time Veo said so, until Veo next accepts a clip.
    if (since := veo.depleted_since()) is not None:
        evidence["veo_credits"] = "depleted"
        return Observation(
            "render_backend", Health.FAILING,
            detail=(f"Veo prepaid credits depleted (Veo refused at "
                    f"{since:%Y-%m-%d %H:%M} UTC; top up at {veo.TOP_UP}) — "
                    f"{detail}"),
            evidence=evidence)
    return Observation("render_backend", Health.OK, detail=detail,
                       evidence=evidence)


async def check_reels_channel() -> Observation:
    """Whether #instagram-reels is actually named. Unset, the stories cards,
    previews, digest and nudge silently fall to the default route -- so
    this says so, DEGRADED, rather than let that be silent (review I2). Reads
    configuration only."""
    from ...channels.slack import TOPIC_CHANNEL_ENV
    env = TOPIC_CHANNEL_ENV["instagram_stories"]
    mapped = any(pair.partition(":")[0].strip().lower() == "instagram_stories"
                 and pair.partition(":")[2].strip()
                 for pair in os.getenv("SLACK_CHANNELS", "").split(","))
    if mapped or os.getenv(env, "").strip():
        return Observation("reels_channel", Health.OK,
                           detail="the AI-stories cards have their own Slack channel")
    return Observation("reels_channel", Health.DEGRADED,
                       detail=f"{env} unset — cards fall to the default route")


class ContentModule:
    name = "content"
    title = "Content"
    requires_env = ()
    optional_env = ("OPENART_MCP_URL", "OPENART_TOKEN", "RENDER_MONTHLY_CAP",
                    "GEMINI_API_KEY", "VEO_MODEL", "VEO_MONTHLY_SECONDS",
                    "VEO_MAX_BEATS", "GEMINI_IMAGE_MODEL", "GROQ_API_KEY",
                    "FISH_AUDIO_API_KEY", "REELS_FISH_VOICE_ID",
                    "REELS_FONT_FILE", "REELS_PER_DAY",
                    "FACEBOOK_STORIES_PAGE_ID", "FACEBOOK_STORIES_PAGE_TOKEN",
                    "POSTIZ_API_KEY", "POSTIZ_API_URL", "FOOTBALL_REELS_PER_DAY",
                    "HIGGSFIELD_KEY_ID", "HIGGSFIELD_KEY_SECRET",
                    "HIGGSFIELD_MONTHLY_USD", "HIGGSFIELD_USD_PER_SECOND_MAX",
                    "HIGGSFIELD_RESOLUTION", "REELS_PROMOTE_MIN_VIEWS",
                    "REELS_PROMOTE_MIN_ENGAGEMENT", "REELS_PROMOTE_MULTIPLE",
                    "REELS_PROMOTE_BASELINE_MIN", "REELS_BEST_HOURS",
                    "REELS_BEST_HOURS_MIN_POSTS", "POSTIZ_YOUTUBE_INTEGRATION",
                    "SHORTS_PER_WEEK")
    setup_note = ""

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("drafts", check_drafts, timedelta(days=1), Severity.NORMAL,
                  "content drafts waiting for review", baseline_sigma=None),
            Check("render_backend", check_render_backend, timedelta(hours=6),
                  Severity.INFO, "which reel render backend this box can run",
                  baseline_sigma=None),
            Check("reel_metrics", metrics.check_reel_metrics, timedelta(days=1),
                  Severity.INFO, "what each posted AI-stories reel did",
                  baseline_sigma=None, timeout=timedelta(seconds=120)),
            Check("promote_candidates", reach.check_promote_candidates,
                  timedelta(days=1), Severity.INFO,
                  "which AI-stories reel is worth promoting by hand (no ads "
                  "action exists)", baseline_sigma=None),
            Check("best_hours", reach.check_best_hours, timedelta(days=1),
                  Severity.INFO, "views at ~24 h by New York posting hour",
                  baseline_sigma=None),
            Check("reels_channel", check_reels_channel, timedelta(hours=6),
                  Severity.NORMAL, "the AI-stories cards have a Slack channel",
                  baseline_sigma=None),
            Check("reel_legs", check_reel_legs, timedelta(hours=6),
                  Severity.INFO, "which platforms an AI-stories reel can reach",
                  baseline_sigma=None),
            Check("reel_legs_sync", legsync.check_reel_legs_sync,
                  timedelta(minutes=30), Severity.NORMAL,
                  "every posted AI reel is on every configured platform, or "
                  "why not", baseline_sigma=None),
            Check("higgsfield_budget", higgsfield.check_budget, timedelta(hours=6),
                  Severity.NORMAL, "Higgsfield overflow spend against its "
                  "monthly cap, and whether its key works", baseline_sigma=None),
            Check("shorts_lane", shorts.check_shorts_lane, timedelta(hours=6),
                  Severity.NORMAL, "the last YouTube Short, this week's count "
                  "and the next one due", baseline_sigma=None),
        )

    def actions(self):
        return (
            Action(
                name="content.draft",
                tier=Tier.AUTO,     # writes a draft row; nothing leaves ATLAS
                summary="Draft a LinkedIn post, eBay listing, flycheap deal "
                        "post or short-form script from a source",
                run=draft, dry_run=draft_dry, rollback=draft_rollback,
                params={"kind": f"one of: {', '.join(KINDS)}",
                        "source": "voice:<n> | weekly | slack:<thread_ts> | "
                                  "note:<text>"},
                max_per_hour=20,
            ),
            Action(
                name="content.render_video",
                tier=Tier.AUTO,     # renders a file; publishing is a separate,
                                    # Tier ASK decision elsewhere. Veo's
                                    # metered seconds run unattended under
                                    # VEO_MONTHLY_SECONDS: the cap is the
                                    # standing approval (ruled, Task 55)
                summary="Render a story_script draft into a video: Veo "
                        "under its seconds cap, else OpenArt, else the free "
                        "local path",
                run=render_video, dry_run=render_video_dry,
                rollback=render_video_rollback,
                params={"draft_id": "id of a story_script content_drafts row",
                        "backend": "optional: auto (the only choice)"},
                max_per_hour=10,
            ),
            Action(
                name="content.reel_research",
                tier=Tier.AUTO,     # reads Perplexity and insights; files
                                    # playbook rows; nothing leaves ATLAS
                summary="Research which short-form story formats are "
                        "performing on Reels and file them in the playbook",
                run=reels.research, dry_run=reels.research_dry,
                rollback=reels.research_rollback,
                params={},
                max_per_hour=2,
            ),
            Action(
                name="content.draft_reel",
                tier=Tier.AUTO,     # writes one draft row; never posts
                summary="Draft one AI-stories reel script from a playbook format",
                run=reels.draft_reel, dry_run=reels.draft_reel_dry,
                rollback=reels.draft_reel_rollback,
                params={"format_id": "id of a reel_formats row"},
                max_per_hour=6,
            ),
            Action(
                name="content.draft_football_reel",
                tier=Tier.AUTO,     # reads API-Football (read-only) and writes
                                    # one draft row; never posts
                summary="Draft one football (soccer) stats reel from facts "
                        "fetched from API-Football, every number bound to a fact",
                run=football.draft_football_reel,
                dry_run=football.draft_football_reel_dry,
                rollback=football.draft_football_reel_rollback,
                params={"max_beats": "optional: the most 8 s beats, 3-5"},
                max_per_hour=2,
            ),
            Action(
                name="content.draft_course_reel",
                tier=Tier.AUTO,     # composes one draft row from the course
                                    # data file; no model, never posts
                summary="Draft the next lesson's course-promo reel for the "
                        "AI-stories accounts, honest by rule",
                run=course.draft_course_reel,
                dry_run=course.draft_course_reel_dry,
                rollback=course.draft_course_reel_rollback,
                params={"beats": "optional: 1 or 2 abstract 8 s Veo beats"},
                max_per_hour=2,
            ),
            Action(
                name="content.render_calendar_job",
                tier=Tier.AUTO,     # banks Veo clips and files a render;
                                    # publishing is the Tier ASK card. Veo's
                                    # seconds run under VEO_MONTHLY_SECONDS,
                                    # the standing approval (Task 55), and
                                    # VEO_DAILY_CLIPS. Past Veo's daily
                                    # allowance, Higgsfield overflow runs
                                    # under HIGGSFIELD_MONTHLY_USD, the same
                                    # kind of standing approval (Miro,
                                    # 2026-09-29)
                summary="Bank one 30-day-calendar reel's Veo clips within "
                        "today's allowance, and assemble it once all exist",
                run=calendar.render_job, dry_run=calendar.render_job_dry,
                rollback=calendar.render_job_rollback,
                params={"job_id": "a stories job id from the 30-day calendar, "
                                  "e.g. reels-20260928-v2-d01"},
                max_per_hour=6,
            ),
            Action(
                name="content.plan_series",
                tier=Tier.AUTO,     # writes one queued series row; never posts
                summary="Plan a whole 3-8 part series with a real ending in a "
                        "playbook format, and queue it",
                run=series.plan_series, dry_run=series.plan_series_dry,
                rollback=series.plan_series_rollback,
                params={"format_name": "a playbook format's name"},
                max_per_hour=2,
            ),
            Action(
                name="content.draft_series_part",
                tier=Tier.AUTO,     # writes one draft row; never posts
                summary="Draft the next part of the series being made, once "
                        "the part before it has posted",
                run=series.draft_part, dry_run=series.draft_part_dry,
                rollback=series.draft_part_rollback,
                params={"series_id": "id of a reel_series row"},
                max_per_hour=4,
            ),
            Action(
                name="content.autopublish_reel",
                tier=Tier.AUTO,     # Miro, 2026-09-27: fully automated. Posts
                                    # only past autopublish_check (switch,
                                    # daily cap, AI label, guard, a 1080x1920
                                    # render with sound); the rollback deletes
                                    # the posts
                summary="Post a finished AI-stories reel to Instagram, the "
                        "Facebook Page and TikTok on its own, once it passes "
                        "every automatic check",
                run=crosspost.autopublish_reel, dry_run=crosspost.autopublish_reel_dry,
                rollback=unpublish_reel,
                params={"media_id": "id of a finished content_media render",
                        "caption": "the caption for every leg; must end with "
                                   "'Made with AI'"},
                max_per_hour=2,
            ),
            Action(
                name="content.post_missing_leg",
                tier=Tier.AUTO,     # Miro, 2026-09-29: an AI reel is on every
                                    # platform. Posts ONLY a leg the reel's own
                                    # run was refused for a retryable reason
                                    # (credentials, 5xx, timeout, rate limit),
                                    # re-derived from the record, with the
                                    # caption it posted with; claimed first,
                                    # never twice. The rollback takes that
                                    # leg back
                summary="Post the one missing platform leg of an AI-stories "
                        "reel that already posted elsewhere, with the same "
                        "caption",
                run=legsync.post_missing_leg, dry_run=legsync.post_missing_leg_dry,
                rollback=unpublish_reel,
                params={"media_id": "id of a content_media render that posted "
                                    "in the last 96 h",
                        "platform": "the missing leg: instagram, facebook or "
                                    "tiktok"},
                max_per_hour=4,
            ),
            Action(
                name="content.publish_short",
                tier=Tier.AUTO,     # Miro, 2026-09-29: the best AI reel of
                                    # the week to YouTube Shorts, three a
                                    # week, on its own. Posts only a reel
                                    # that already posted as an AI reel in
                                    # the last 7 days, past the checks in
                                    # code (the week's cap, lane, 1080x1920
                                    # with sound, <= 60 s, caption guards,
                                    # series order); media_id is its only
                                    # param. Claimed once, never twice. The
                                    # rollback deletes the Postiz post while
                                    # queued, else a MANUAL note (Tier 3)
                summary="Post one AI-stories reel that already posted this "
                        "week to YouTube Shorts (via Postiz), with its own "
                        "title and caption and the AI disclosure",
                run=shorts.publish_short, dry_run=shorts.publish_short_dry,
                rollback=shorts.unpublish_short,
                params={"media_id": "id of a content_media render posted as an "
                                    "AI reel in the last 7 days"},
                max_per_hour=1,
            ),
            Action(
                name="content.publish_reel",
                tier=Tier.ASK,     # public, under the AI-stories account's name
                summary="Post a rendered AI-stories reel to the stories "
                        "Instagram account, the Atlas Stories Facebook Page "
                        "and TikTok (via Postiz)",
                run=publish_reel, dry_run=publish_reel_dry,
                rollback=unpublish_reel,
                params={"media_id": "id of a finished content_media render",
                        "caption": "the caption for every leg; must end with "
                                   "'Made with AI'"},
                max_per_hour=4,
                vet=vet_publish_reel, topic_for=topic_for_publish_reel,
                verbose_card=True,
                # three legs, each waiting on its platform: minutes, not 3 s
                runs_in_background=True,
            ),
        )

    def brief(self) -> str:
        return "Content: drafts waiting for review"


MODULE = ContentModule()
