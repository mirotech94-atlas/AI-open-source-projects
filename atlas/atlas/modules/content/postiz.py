"""The AI-stories reel's TikTok leg, through the Postiz Public API.

Miro's choice (Task 55, relayed from production): TikTok is reached through
Postiz, not ATLAS's own TikTok app, so the `TIKTOK_*` direct-API trio is not
needed for this leg (the `tiktok` module's reads are untouched).

`POSTIZ_API_KEY` goes in the `Authorization` header as the RAW key, no
scheme -- verified live on production (2026-09-25): `Bearer <key>` answers
401 "Invalid API key". The base is `https://api.postiz.com/public/v1` unless
`POSTIZ_API_URL` says otherwise. The flow, each step a reason when it
fails and nothing sent past a failure:

  1. **Upload** the rendered MP4 (`POST /upload`, multipart). Postiz takes
     neither raw bytes in a post nor an external URL; only the `path` this
     returns is accepted in the post.
  2. **The TikTok integration** from `GET /integrations`: identifier
     `tiktok-business` (verified live: `[{"identifier": "tiktok-business",
     "name": "atlasstories7", "disabled": false}]`), or `tiktok` in case
     Postiz renames it; disabled ones are skipped. None is a refusal.
  3. **Its settings**, read BEFORE posting (`GET /integration-settings/{id}`),
     because Postiz silently drops a setting it does not know. Every key
     ATLAS sends must be one the integration declares, and every value one
     it allows when it lists them. Settings that cannot be read are a
     refusal, never a blind post.
  4. **The post** (`POST /posts`, `type: now`) with `privacy_level:
     PUBLIC_TO_EVERYONE`, `duet: true`, `stitch: true`,
     `content_posting_method: DIRECT_POST` -- never `UPLOAD`, which only
     drops the video in the TikTok inbox while Postiz reports success.
  5. **The TikTok id.** TikTok often returns none. If the post answered
     without a `releaseId`, Postiz's missing-content endpoint is asked
     (`GET /posts/{id}/missing`) and ONLY a single candidate matching the
     caption is connected (`PUT /posts/{id}/release-id`) -- still inside
     `content.publish_reel`, the executor's path. Otherwise the leg reads
     "posted, id pending" -- never "posted" with an id nobody saw -- and the
     daily metrics check reports it unread rather than binding anything. The skill
     documents `posts:missing` as answering `[{"id", "url"}]` (a cover URL,
     no text), so in practice no candidate matches and the leg stays "id
     pending" for Miro to connect by hand: choosing among bare ids would be
     a guess.

**Undo.** While the Postiz post is still queued it is deleted
(`DELETE /posts/{id}`). Once published it is NOT deleted -- deleting a
published post is Tier 3 -- and the rollback records a MANUAL note to take
it down by hand.

The auth shape, `GET /integrations` and the `GET /posts` listing
(`posts_between`) are verified live; every other endpoint path and response
shape here is **assumed** from Postiz's public docs until it is seen live
(MODULE_AUDIT.md says so). The key never reaches a reason: every message is
masked.

**Two TikTok accounts, never crossed** (2026-09-28). The clips lane posts
brand campaign clips to its own TikTok, @atlasclips12, through the same
Postiz workspace. Which integration is which is never guessed:

  * the clips lane uses ONLY the integration `POSTIZ_TIKTOK_CLIPS_INTEGRATION`
    names (its Postiz id, or its name, `atlasclips12`), and only through
    `post_tiktok_clip`, which takes a lane-minted file (a plain `.mp4` in
    `<CLIPS_DATA_DIR>/<campaign>/out/`) and nothing else -- the same floor
    as the Instagram `clips` account and the YouTube `clips` channel. Unset,
    the leg is "not configured" and the clip goes to Miro as a hand-off card;
  * the AI-stories lane never picks that integration, nor one named
    `atlasclips12`; and when more than one TikTok integration is left it
    takes the one named `atlasstories7` or refuses -- never "the first".

A clip's settings are `CLIPS_TIKTOK_SETTINGS`: the brand's own footage (not
AI-made), no library music, and TikTok's branded-content toggle ON, since
the post is a paid campaign's (its caption carries `#Ad` besides).

**The clips YouTube channel, as Shorts** (2026-09-29). The clips lane also
posts campaign clips to its YouTube channel AtlasClips (@miroatlas) through
the same Postiz workspace, via `post_youtube_clip` only -- the same
lane-cut-file floor as the TikTok leg -- and ONLY to the integration
`POSTIZ_YOUTUBE_CLIPS_INTEGRATION` names (its Postiz id or name), which must
be a YouTube integration (identifier `youtube`) and never either TikTok
account. Nothing else here ever picks a YouTube integration: the AI-stories
lane takes TikTok integrations only. The post is `__type: youtube` with the
documented settings (the `postiz` skill's PROVIDER_SETTINGS): `title` (2-100
characters, never cut short), `type: public`, `selfDeclaredMadeForKids: no`
and `tags` as `[{"value", "label"}]`; the description is the clip's full
caption. Postiz's YouTube settings declare no paid-promotion flag, so the
disclosure is `#Ad` in the title and the description, as on the Data API
path. The identifier, the settings shape and the answer's `releaseURL` are
**assumed** from Postiz's docs until seen live; the link is recorded only
when Postiz returns a YouTube URL for the id it names.
"""
from __future__ import annotations

import asyncio
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

DEFAULT_BASE = "https://api.postiz.com/public/v1"
KEY_ENV = "POSTIZ_API_KEY"

# The integration identifiers that mean TikTok: the one seen live first.
TIKTOK_IDENTIFIERS: tuple[str, ...] = ("tiktok-business", "tiktok")
# The clips lane's TikTok integration, by Postiz id or name. Never guessed.
CLIPS_ENV = "POSTIZ_TIKTOK_CLIPS_INTEGRATION"
CLIPS_HANDLE = "atlasclips12"       # @atlasclips12, the clips lane's TikTok
STORIES_HANDLE = "atlasstories7"    # the AI-stories TikTok, as seen live
# The clips lane's YouTube channel (Shorts), by Postiz id or name. Never
# guessed, and never a TikTok integration.
YT_CLIPS_ENV = "POSTIZ_YOUTUBE_CLIPS_INTEGRATION"
YT_CLIPS_CHANNEL = "AtlasClips (@miroatlas)"
YOUTUBE_IDENTIFIERS: tuple[str, ...] = ("youtube",)
YT_ACCOUNT = "clips-youtube"        # the account key `_post` knows it by
YT_TITLE_MIN, YT_TITLE_MAX = 2, 100

PATH_UPLOAD = "/upload"
PATH_INTEGRATIONS = "/integrations"
PATH_SETTINGS = "/integration-settings/{id}"
PATH_POSTS = "/posts"
PATH_POST = "/posts/{id}"
PATH_MISSING = "/posts/{id}/missing"
PATH_RELEASE = "/posts/{id}/release-id"
PATH_ANALYTICS = "/analytics/post/{id}"

# What ATLAS asks TikTok for, via Postiz. DIRECT_POST, never UPLOAD.
# Every field Postiz marks required must be present, or the post is refused
# with HTTP 400 (all four posts of 2026-09-26/27 were: "comment must be a
# boolean", "autoAddMusic must be yes|no", the two brand toggles). Our reels
# carry their own narration, so no random library music; they are not paid
# promotions; and TikTok's own AI-generated label is set, beside the caption's.
TIKTOK_SETTINGS: dict[str, Any] = {
    "privacy_level": "PUBLIC_TO_EVERYONE",
    "duet": True,
    "stitch": True,
    "comment": True,
    "autoAddMusic": "no",
    "brand_content_toggle": False,
    "brand_organic_toggle": False,
    "video_made_with_ai": True,
    "content_posting_method": "DIRECT_POST",
}

# A campaign clip: the brand's own footage and audio (so no AI label and no
# library music -- COD forbids any), and a paid campaign's post, so TikTok's
# branded-content toggle is on beside the caption's #Ad.
CLIPS_TIKTOK_SETTINGS: dict[str, Any] = {
    "privacy_level": "PUBLIC_TO_EVERYONE",
    "duet": True,
    "stitch": True,
    "comment": True,
    "autoAddMusic": "no",
    "brand_content_toggle": True,
    "brand_organic_toggle": False,
    "video_made_with_ai": False,
    "content_posting_method": "DIRECT_POST",
}
# A campaign clip as a YouTube Short: public, not made for kids. The title
# and tags are the clip's own, added per post (`post_youtube_clip`).
CLIPS_YOUTUBE_SETTINGS: dict[str, Any] = {
    "type": "public",
    "selfDeclaredMadeForKids": "no",
}
# A store promo on the clips TikTok (`post_tiktok_promo`) is ATLAS's own
# AI-made reel, not brand content: these override CLIPS_TIKTOK_SETTINGS.
PROMO_TIKTOK_FLAGS: dict[str, Any] = {"brand_content_toggle": False,
                                      "video_made_with_ai": True}
SETTINGS = {"stories": TIKTOK_SETTINGS, "clips": CLIPS_TIKTOK_SETTINGS,
            YT_ACCOUNT: CLIPS_YOUTUBE_SETTINGS}
# The per-post keys the YouTube leg also sends, checked by `readiness`.
YT_POST_KEYS: dict[str, Any] = {"title": "", "tags": []}
TIKTOK_URL = re.compile(r"^https://(www\.)?tiktok\.com/@([A-Za-z0-9._]+)/video/"
                        r"\d+/?(\?[^\s]*)?$")
CLIP_YOUTUBE_URL = re.compile(r"^https://(?:(?:www\.|m\.)?youtube\.com/(?:shorts/|watch\?v=)"
                         r"|youtu\.be/)([A-Za-z0-9_-]{6,})(?:[/?&#][^\s]*)?$")

POSTED, ID_PENDING, REFUSED, NOT_CONFIGURED = (
    "posted", "posted, id pending", "refused", "not configured")


class PostizError(Exception):
    """A step failed. The message names the step and is safe to show."""


def configured() -> bool:
    return bool(os.getenv(KEY_ENV, "").strip())


def clips_integration() -> str:
    """What `POSTIZ_TIKTOK_CLIPS_INTEGRATION` names: a Postiz integration id
    or its name (a leading @ is dropped). "" when unset."""
    return os.getenv(CLIPS_ENV, "").strip().lstrip("@")


def clips_gap() -> str | None:
    """Why the clips TikTok cannot be posted to, from configuration alone,
    or None. Reads env only: safe inside a signals pass."""
    missing = [name for name, value in ((KEY_ENV, configured()),
                                        (CLIPS_ENV, bool(clips_integration())))
               if not value]
    if missing:
        return (f"{' and '.join(missing)} unset, so @{CLIPS_HANDLE} is not "
                f"reachable through Postiz")
    return None


def yt_clips_integration() -> str:
    """What `POSTIZ_YOUTUBE_CLIPS_INTEGRATION` names: a Postiz integration id
    or its name (a leading @ is dropped). "" when unset."""
    return os.getenv(YT_CLIPS_ENV, "").strip().lstrip("@")


def yt_clips_gap() -> str | None:
    """Why the clips YouTube channel cannot be posted to through Postiz,
    from configuration alone, or None. Reads env only."""
    missing = [name for name, value in ((KEY_ENV, configured()),
                                        (YT_CLIPS_ENV, bool(yt_clips_integration())))
               if not value]
    if missing:
        names = " and ".join(missing)
        return (f"{names} unset, so the clips YouTube channel {YT_CLIPS_CHANNEL} is "
                f"not reachable through Postiz -- set {names}")
    return None


def base() -> str:
    return (os.getenv("POSTIZ_API_URL", "").strip() or DEFAULT_BASE).rstrip("/")


def _key() -> str:
    return os.getenv(KEY_ENV, "").strip()


def _masked(text: str) -> str:
    from .._http import redact
    key = _key()
    cleaned = redact(text)
    return cleaned.replace(key, "<redacted>") if key else cleaned


def _said(r: httpx.Response) -> str:
    try:
        body = r.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        msg = body.get("message") or body.get("error") or ""
        return " ".join(str(msg).split())[:200]
    return ""


async def _call(c: httpx.AsyncClient, method: str, path: str, step: str,
                **kw: Any) -> httpx.Response:
    try:
        r = await c.request(method, f"{base()}{path}",
                            headers={"Authorization": _key()}, **kw)
    except httpx.HTTPError as exc:
        from .._http import explain
        raise PostizError(_masked(f"Postiz {step}: unreachable — {explain(exc)}")) from None
    if r.status_code >= 400:
        said = _said(r)
        raise PostizError(_masked(f"Postiz {step}: HTTP {r.status_code}"
                                  + (f" — {said}" if said else "")))
    return r


def _json(r: httpx.Response, step: str) -> Any:
    try:
        return r.json()
    except ValueError:
        raise PostizError(f"Postiz {step}: the answer was not JSON") from None


# ------------------------------------------------------------- settings rules

def declared(settings: Any) -> dict[str, set[Any] | None] | None:
    """What an integration's settings allow: key -> allowed values (None when
    any value goes). The documented answer (the `postiz` skill,
    `integrations:settings`) is `{"output": {"rules": "<prose>", "settings":
    <a JSON schema>}}`: the schema's `properties` are the keys, a property's
    `enum` its allowed values. Also read: a list of field descriptions, or a
    `settings` / `fields` list, or a plain mapping of key to spec. None when
    nothing readable is there. The `rules` prose is for a person; the keys
    and enums are what code can hold a post to."""
    if isinstance(settings, dict) and isinstance(settings.get("output"), dict):
        settings = settings["output"]
    items: Any = settings
    if isinstance(settings, dict):
        for name in ("settings", "fields", "rules"):
            if isinstance(settings.get(name), list | dict):
                items = settings[name]
                break
    if isinstance(items, dict) and isinstance(items.get("properties"), dict):
        items = items["properties"]
    out: dict[str, set[Any] | None] = {}
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            key = item.get("key") or item.get("name") or item.get("id")
            if isinstance(key, str) and key:
                out[key] = _options(item)
    elif isinstance(items, dict):
        for key, spec in items.items():
            if isinstance(key, str) and key and not key.startswith("__"):
                out[key] = _options(spec) if isinstance(spec, dict) else (
                    set(spec) if isinstance(spec, list) else None)
    return out or None


def _options(spec: dict) -> set[Any] | None:
    for name in ("options", "values", "enum"):
        found = spec.get(name)
        if isinstance(found, list) and found:
            return {o.get("value") if isinstance(o, dict) else o for o in found}
    return None


AUDIT_NOTE = ("private until TikTok's audit passes — Postiz's TikTok "
              "integration only allows SELF_ONLY, so this leg does not post")


def _allowed(value: Any, allowed: set[Any]) -> bool:
    """Is `value` one the integration allows? A list (YouTube's tags) is
    allowed only when every item -- or its `value` -- is."""
    items = value if isinstance(value, list) else [value]
    for item in items:
        v = item.get("value") if isinstance(item, dict) else item
        try:
            if v not in allowed:
                return False
        except TypeError:
            return False
    return True


def validate(settings: Any, wanted: dict[str, Any],
             platform: str = "TikTok") -> str | None:
    """A reason when `wanted` would be dropped or refused, else None. An
    unaudited TikTok app offers only SELF_ONLY; that is said as such."""
    rules = declared(settings)
    privacy = (rules or {}).get("privacy_level")
    if (privacy is not None and "PUBLIC_TO_EVERYONE" not in privacy
            and "SELF_ONLY" in privacy):
        return AUDIT_NOTE
    if rules is None:
        return (f"Postiz's {platform} settings could not be read, so nothing was "
                f"posted blind (Postiz drops a setting it does not know)")
    for key, value in wanted.items():
        if key not in rules:
            return (f"Postiz's {platform} integration does not declare {key!r}, "
                    f"so it would be silently dropped — not posted")
        allowed = rules[key]
        if allowed is not None and not _allowed(value, allowed):
            return (f"Postiz's {platform} integration does not allow {key}="
                    f"{value!r} (it allows {sorted(map(str, allowed))})")
    return None


# ------------------------------------------------------------- the leg

def _enabled(found: Any) -> list[dict]:
    listed = found if isinstance(found, list) else (
        found.get("integrations") if isinstance(found, dict) else None) or []
    return [item for item in listed if isinstance(item, dict)
            and not item.get("disabled") and item.get("id")]


def _ident(item: dict) -> str:
    return str(item.get("identifier") or item.get("providerIdentifier") or "").lower()


def _enabled_tiktok(found: Any) -> list[dict]:
    return [i for i in _enabled(found) if _ident(i) in TIKTOK_IDENTIFIERS]


def _name(item: dict) -> str:
    return str(item.get("name") or "").strip().lstrip("@").lower()


def _is(item: dict, wanted: str) -> bool:
    return bool(wanted) and (str(item.get("id")) == wanted
                             or _name(item) == wanted.lower())


def pick_integration(found: Any, account: str = "stories") -> tuple[str, str]:
    """(id, name) of the TikTok integration `account` posts to, or
    PostizError. Pure. The clips lane takes only the one its env names;
    the stories lane never takes that one (nor one named `atlasclips12`)
    and never picks among several. The clips YouTube channel (`YT_ACCOUNT`)
    is only the YouTube integration its own env names."""
    if account == YT_ACCOUNT:
        return _pick_youtube(found)
    enabled = _enabled_tiktok(found)
    clips = clips_integration()
    if account == "clips":
        if not clips:
            raise PostizError(f"{CLIPS_ENV} unset: the clips TikTok "
                              f"(@{CLIPS_HANDLE}) is never guessed")
        mine = [i for i in enabled if _is(i, clips)]
        if len(mine) != 1:
            raise PostizError(
                f"Postiz has {'no' if not mine else 'more than one'} enabled "
                f"TikTok integration matching {CLIPS_ENV}={clips!r} -- connect "
                f"@{CLIPS_HANDLE} in Postiz first")
        if _name(mine[0]) == STORIES_HANDLE:
            raise PostizError(f"{CLIPS_ENV} names the AI-stories TikTok "
                              f"(@{STORIES_HANDLE}); clips never post there")
        return str(mine[0]["id"]), _name(mine[0])
    if account != "stories":
        raise PostizError(f"no TikTok account {account!r}")
    left = [i for i in enabled
            if not _is(i, clips) and _name(i) != CLIPS_HANDLE]
    if not left:
        raise PostizError("Postiz has no enabled TikTok integration — connect "
                          "TikTok in Postiz first")
    if len(left) > 1:
        named = [i for i in left if _name(i) == STORIES_HANDLE]
        if len(named) != 1:
            raise PostizError(
                f"Postiz has {len(left)} TikTok integrations and none is "
                f"clearly @{STORIES_HANDLE}; the reels lane does not guess "
                f"(set {CLIPS_ENV} to the clips one)")
        left = named
    return str(left[0]["id"]), _name(left[0])


def _pick_youtube(found: Any) -> tuple[str, str]:
    """(id, name) of the clips YouTube channel: the ONE enabled integration
    `POSTIZ_YOUTUBE_CLIPS_INTEGRATION` names, which must be a YouTube one and
    neither TikTok account. Pure; never a guess among several."""
    wanted = yt_clips_integration()
    if not wanted:
        raise PostizError(f"{YT_CLIPS_ENV} unset: the clips YouTube channel "
                          f"({YT_CLIPS_CHANNEL}) is never guessed")
    named = [i for i in _enabled(found) if _is(i, wanted)]
    if len(named) != 1:
        raise PostizError(
            f"Postiz has {'no' if not named else 'more than one'} enabled "
            f"integration matching {YT_CLIPS_ENV}={wanted!r} -- connect "
            f"{YT_CLIPS_CHANNEL} in Postiz first")
    item = named[0]
    if _ident(item) not in YOUTUBE_IDENTIFIERS:
        raise PostizError(
            f"{YT_CLIPS_ENV}={wanted!r} names a {_ident(item) or 'non-YouTube'} "
            f"integration, not YouTube; clips post as Shorts only to the "
            f"YouTube channel it names")
    if (_is(item, clips_integration())
            or _name(item) in (CLIPS_HANDLE, STORIES_HANDLE)):
        raise PostizError(f"{YT_CLIPS_ENV} names a TikTok account's integration; "
                          f"clips Shorts never post there")
    return str(item["id"]), _name(item)


async def _integration(c: httpx.AsyncClient, account: str) -> tuple[str, str]:
    found = _json(await _call(c, "GET", PATH_INTEGRATIONS, "integrations"),
                  "integrations")
    return pick_integration(found, account)


async def _tiktok_integration(c: httpx.AsyncClient, account: str = "stories") -> str:
    return (await _integration(c, account))[0]


async def _release(c: httpx.AsyncClient, post_id: str, caption: str) -> str | None:
    """Connect TikTok's id through Postiz's missing-content endpoint when it
    offers exactly one candidate MATCHING the caption. A single candidate
    that does not match is not bound, and neither is one of several: that
    would be a guess, and a wrong id feeds another video's numbers into the
    ranking. Called only from `post_tiktok`, inside `content.publish_reel`
    (the executor's path) -- never from a check, which only reads."""
    try:
        r = await c.get(f"{base()}{PATH_MISSING.format(id=post_id)}",
                        headers={"Authorization": _key()})
        if r.status_code >= 400:
            return None
        found = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    listed = found if isinstance(found, list) else []
    first = caption.strip().splitlines()[0][:60].lower() if caption.strip() else ""
    candidates = [item for item in listed if isinstance(item, dict) and item.get("id")]
    matches = [item for item in candidates if first and first in str(
        item.get("content") or item.get("description") or "").lower()]
    if len(matches) != 1:
        return None
    release = str(matches[0]["id"])
    try:
        r = await c.put(f"{base()}{PATH_RELEASE.format(id=post_id)}",
                        headers={"Authorization": _key()},
                        json={"releaseId": release})
    except httpx.HTTPError:
        return None
    return release if r.status_code < 400 else None


async def post_tiktok(video: bytes, caption: str) -> tuple[str, str, str | None]:
    """The AI-stories reel's leg. `(status, detail, postiz_post_id)`. Never
    raises."""
    status, detail, post_id, _url = await _post(video, caption, "stories")
    return status, detail, post_id


def _lane_clip(path: str) -> bool:
    from ..clips import sources
    return sources.lane_clip(path)


def _read_file(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


async def post_tiktok_clip(path: str, caption: str
                           ) -> tuple[str, str, str | None, str | None]:
    """A campaign clip to @atlasclips12: `(status, detail, postiz_post_id,
    post_url)`, the URL only when Postiz itself returned one for that
    account. Takes a PATH, and only one the clips lane cut -- the one way
    onto the clips integration. Never raises."""
    if (gap := clips_gap()) is not None:
        return NOT_CONFIGURED, gap, None, None
    if not await asyncio.to_thread(_lane_clip, path):
        return REFUSED, ("the clips TikTok posts only a clip the clips lane cut "
                         "and checked, through clips.publish_clip_tiktok"), None, None
    try:
        video = await asyncio.to_thread(_read_file, path)
    except OSError as exc:
        return REFUSED, f"the clip could not be read: {exc.strerror}", None, None
    return await _post(video, caption, "clips")


def _promo_file(path: str) -> bool:
    """Is this a finished render of ATLAS's own: a plain `.mp4` directly
    inside `content.DATA_DIR`, never a symlink, nothing else on disk? The
    floor the clips accounts' second doorway (a store promo) posts from."""
    from . import DATA_DIR
    try:
        if os.path.islink(path):
            return False
        real = os.path.realpath(path)
        base = os.path.realpath(DATA_DIR)
        rel = os.path.relpath(real, base)
    except (OSError, ValueError):
        return False
    return (os.sep not in rel and not rel.startswith(".") and rel.endswith(".mp4")
            and os.path.isfile(real))


async def post_tiktok_promo(path: str, caption: str
                            ) -> tuple[str, str, str | None, str | None]:
    """The Academy store's promo reel to @atlasclips12 -- Miro, 2026-10-05:
    "post on clips too". The second and last way onto the clips TikTok
    integration, beside `post_tiktok_clip`: takes a PATH, and only a
    finished render in `content.DATA_DIR`, reached through
    `clips.publish_promo` (an approved card). Never raises."""
    if (gap := clips_gap()) is not None:
        return NOT_CONFIGURED, gap, None, None
    if not await asyncio.to_thread(_promo_file, path):
        return REFUSED, ("the clips TikTok posts a store promo only from a "
                         "finished render of ATLAS's own, through "
                         "clips.publish_promo"), None, None
    try:
        video = await asyncio.to_thread(_read_file, path)
    except OSError as exc:
        return REFUSED, f"the render could not be read: {exc.strerror}", None, None
    # ATLAS's own AI-made reel, not a brand's footage: the two flags that
    # make a campaign clip brand content are set the stories way.
    return await _post(video, caption, "clips", PROMO_TIKTOK_FLAGS)


async def post_youtube_promo(path: str, title: str, description: str,
                             tags: list[str]
                             ) -> tuple[str, str, str | None, str | None]:
    """The store's promo reel as a Short on the clips YouTube channel: the
    second and last way onto that integration, beside `post_youtube_clip`,
    with the same file floor as `post_tiktok_promo`. Never raises."""
    if (gap := yt_clips_gap()) is not None:
        return NOT_CONFIGURED, gap, None, None
    if not await asyncio.to_thread(_promo_file, path):
        return REFUSED, ("the clips YouTube channel posts a store promo only "
                         "from a finished render of ATLAS's own, through "
                         "clips.publish_promo"), None, None
    if not YT_TITLE_MIN <= len(title) <= YT_TITLE_MAX:
        return REFUSED, (f"a YouTube title must be {YT_TITLE_MIN}-{YT_TITLE_MAX} "
                         f"characters, not {len(title)}; it is never cut short"
                         ), None, None
    try:
        video = await asyncio.to_thread(_read_file, path)
    except OSError as exc:
        return REFUSED, f"the render could not be read: {exc.strerror}", None, None
    extra: dict[str, Any] = {"title": title}
    if tags:
        extra["tags"] = [{"value": t, "label": t} for t in tags]
    return await _post(video, description, YT_ACCOUNT, extra)


async def post_youtube_clip(path: str, title: str, description: str,
                            tags: list[str]
                            ) -> tuple[str, str, str | None, str | None]:
    """A campaign clip as a Short on the clips YouTube channel:
    `(status, detail, postiz_post_id, post_url)`, the URL only when Postiz
    itself returned one for the id it names. Takes a PATH, and only one the
    clips lane cut -- the one way onto the clips YouTube integration. The
    title is refused, never cut, outside 2-100 characters. Never raises."""
    if (gap := yt_clips_gap()) is not None:
        return NOT_CONFIGURED, gap, None, None
    if not await asyncio.to_thread(_lane_clip, path):
        return REFUSED, ("the clips YouTube channel posts only a clip the clips "
                         "lane cut and checked, through "
                         "clips.publish_clip_youtube_postiz"), None, None
    if not YT_TITLE_MIN <= len(title) <= YT_TITLE_MAX:
        return REFUSED, (f"a YouTube title must be {YT_TITLE_MIN}-{YT_TITLE_MAX} "
                         f"characters, not {len(title)}; it is never cut short"
                         ), None, None
    try:
        video = await asyncio.to_thread(_read_file, path)
    except OSError as exc:
        return REFUSED, f"the clip could not be read: {exc.strerror}", None, None
    extra: dict[str, Any] = {"title": title}
    if tags:
        extra["tags"] = [{"value": t, "label": t} for t in tags]
    return await _post(video, description, YT_ACCOUNT, extra)


async def _post(video: bytes, caption: str, account: str,
                extra: dict[str, Any] | None = None
                ) -> tuple[str, str, str | None, str | None]:
    if not configured():
        return NOT_CONFIGURED, f"{KEY_ENV} unset", None, None
    youtube = account == YT_ACCOUNT
    platform = "YouTube" if youtube else "TikTok"
    wanted = {**SETTINGS[account], **(extra or {})}
    filename = "clip.mp4" if account in ("clips", YT_ACCOUNT) else "reel.mp4"
    try:
        async with httpx.AsyncClient(timeout=180) as c:
            up = _json(await _call(c, "POST", PATH_UPLOAD, "upload",
                                   files={"file": (filename, video, "video/mp4")}),
                       "upload")
            path = up.get("path") if isinstance(up, dict) else None
            if not path:
                raise PostizError("Postiz upload: no path came back")
            integration, name = await _integration(c, account)
            settings = _json(await _call(
                c, "GET", PATH_SETTINGS.format(id=integration), "settings"),
                "settings")
            if (why := validate(settings, wanted, platform)) is not None:
                raise PostizError(why)
            body = {
                "type": "now", "date": datetime.now(UTC).isoformat(),
                "shortLink": False, "tags": [],
                "posts": [{
                    "integration": {"id": integration},
                    "value": [{"content": caption,
                               "image": [{"id": up.get("id"), "path": path}]}],
                    "settings": {"__type": "youtube" if youtube else "tiktok",
                                 **wanted},
                }],
            }
            made = _json(await _call(c, "POST", PATH_POSTS, "post", json=body), "post")
            first = made[0] if isinstance(made, list) and made else made
            if not isinstance(first, dict) or not (first.get("postId")
                                                   or first.get("id")):
                raise PostizError("Postiz post: accepted, but named no post id")
            post_id = str(first.get("postId") or first.get("id"))
            if youtube:
                # YouTube answers with the video id; the missing-content
                # dance is TikTok's.
                release = first.get("releaseId")
                url = clip_youtube_url(first)
            else:
                release = first.get("releaseId") or await _release(c, post_id,
                                                                   caption)
                url = release_url(first, name)
    except PostizError as exc:
        return REFUSED, str(exc), None, None
    if youtube:
        if release:
            return POSTED, (f"Postiz post {post_id}, YouTube id {release}, a "
                            f"public Short"), post_id, url
        return ID_PENDING, (f"Postiz post {post_id}, a public Short; YouTube "
                            f"returned no id yet"), post_id, url
    if release:
        return POSTED, (f"Postiz post {post_id}, TikTok id {release}, public "
                        f"(DIRECT_POST)"), post_id, url
    return ID_PENDING, (f"Postiz post {post_id}, public (DIRECT_POST); TikTok "
                        f"returned no id yet"), post_id, url


def release_url(answer: dict, name: str) -> str | None:
    """The post's TikTok URL, only when Postiz returned one AND it is on the
    account posted to. Never built from an id: it feeds a payout claim."""
    url = str(answer.get("releaseURL") or answer.get("releaseUrl") or "").strip()
    match = TIKTOK_URL.match(url)
    if match is None or not name or match.group(2).lower() != name:
        return None
    return url


def clip_youtube_url(answer: dict) -> str | None:
    """The Short's URL, as `youtube.com/shorts/<id>`, only when Postiz
    returned a YouTube URL AND it is for the id Postiz names (when it names
    one). Never built from a bare id: it feeds a payout claim."""
    url = str(answer.get("releaseURL") or answer.get("releaseUrl") or "").strip()
    match = CLIP_YOUTUBE_URL.match(url)
    if match is None:
        return None
    video = match.group(1)
    release = str(answer.get("releaseId") or "").strip()
    if release and release != video:
        return None
    return f"https://www.youtube.com/shorts/{video}"


async def readiness(account: str = "stories") -> tuple[bool, str]:
    """For the dry run, reads only: the account's integration and its
    settings, checked as a post would be. `(ready, what the card should
    say)`."""
    if account == "clips" and (gap := clips_gap()) is not None:
        return False, gap
    if account == YT_ACCOUNT and (gap := yt_clips_gap()) is not None:
        return False, gap
    if not configured():
        return False, f"{KEY_ENV} unset"
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            integration, _found = await _integration(c, account)
            settings = _json(await _call(
                c, "GET", PATH_SETTINGS.format(id=integration), "settings"),
                "settings")
    except PostizError as exc:
        return False, str(exc)
    if account == YT_ACCOUNT:
        if (why := validate(settings, {**SETTINGS[account], **YT_POST_KEYS},
                            "YouTube")) is not None:
            return False, why
        return True, "a public Short; Postiz's YouTube settings allow it"
    if (why := validate(settings, SETTINGS[account])) is not None:
        return False, why
    return True, "public, DIRECT_POST; Postiz's TikTok settings allow it"


async def _state(c: httpx.AsyncClient, post_id: str) -> str | None:
    """The post's state from Postiz's listing (a two-day window around now),
    or None when it cannot be confirmed."""
    now = datetime.now(UTC)
    try:
        r = await c.get(f"{base()}{PATH_POSTS}", headers={"Authorization": _key()},
                        params={"startDate": (now - timedelta(days=2)).isoformat(),
                                "endDate": (now + timedelta(days=1)).isoformat()})
        if r.status_code >= 400:
            return None
        found = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    listed = found.get("posts") if isinstance(found, dict) else found
    for item in listed or []:
        if isinstance(item, dict) and str(item.get("id")) == post_id:
            state = item.get("state") or item.get("status")
            return str(state).upper() if state else None
    return None


async def undo(post_id: str, where: str = "TikTok") -> tuple[bool, str]:
    """Delete the Postiz post only while it is still queued. Published (or
    not confirmably queued) is a MANUAL note: deleting a published post is
    Tier 3, and this never does it. `where` names the place to take it
    down by hand (TikTok, or YouTube / YouTube Studio for a Short)."""
    how = ("set it private by hand in YouTube Studio" if where.startswith("YouTube")
           else f"take it down by hand in {where}")
    manual = (f"MANUAL: Postiz post {post_id} has published (or could not be "
              f"confirmed as still queued); deleting a published post is Tier 3, "
              f"so {how}")
    if not configured():
        return False, f"{KEY_ENV} unset — {manual}"
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            if await _state(c, post_id) != "QUEUE":
                return False, manual
            await _call(c, "DELETE", PATH_POST.format(id=post_id), "delete")
    except PostizError as exc:
        return False, f"{exc} — {manual}"
    return True, f"Postiz post {post_id} deleted while still queued"


# ------------------------------------------------------------- post listing

async def posts_between(start: datetime, end: datetime) -> list[dict[str, Any]]:
    """READ-ONLY: the posts Postiz lists with a `publishDate` in [start, end].
    Raises PostizError (masked) when Postiz is unset, unreachable or answers
    anything else; never posts, edits or deletes.

    VERIFIED live 2026-09-30 (read-only, from the api container): `GET
    /posts/{id}` answers 404 -- there is no read of one post -- while `GET
    /posts?startDate=&endDate=` (both ISO 8601, required: without them 400)
    answers `{"posts": [...]}`, filtered by `publishDate`, each item with
    `id`, `state` (`PUBLISHED`), `publishDate`, `releaseURL`, `releaseId`,
    `group`, `content`, `settings`, `tags`, `creationMethod` and
    `integration: {id, name, picture, providerIdentifier}`. For every TikTok
    post seen (`tiktok-business`, both accounts) `releaseURL` was the
    PROFILE (`https://www.tiktok.com/@atlasclips12`), not the video, and
    `releaseId` a `v_pub_url~v2.<n>` publish id, not a video id; a YouTube
    post's `releaseURL` was its `watch?v=` URL with `releaseId` the video id.
    The items' text is untrusted and never reaches a prompt."""
    if not configured():
        raise PostizError(f"{KEY_ENV} unset")
    async with httpx.AsyncClient(timeout=30) as c:
        found = _json(await _call(c, "GET", PATH_POSTS, "posts",
                                  params={"startDate": start.isoformat(),
                                          "endDate": end.isoformat()}), "posts")
    listed = found.get("posts") if isinstance(found, dict) else None
    if not isinstance(listed, list):
        raise PostizError("Postiz posts: the answer carried no posts list")
    return [item for item in listed if isinstance(item, dict)]


# ------------------------------------------------------------- analytics

METRIC_WORDS = {"views": "view", "likes": "like", "comments": "comment",
                "shares": "share"}


def _metrics(answer: Any) -> dict[str, float | None]:
    """Postiz's `analytics:post` answer -- `[{"label", "data": [{"total",
    "date"}]}]` per the skill -- as views/likes/comments/shares. A label that
    is absent is None, never 0. The largest daily total is taken: the docs
    do not say whether the points are cumulative, and for a counter that
    only grows the largest is the latest."""
    out: dict[str, float | None] = dict.fromkeys(METRIC_WORDS)
    for item in answer if isinstance(answer, list) else []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").lower()
        for key, word in METRIC_WORDS.items():
            if word in label and out[key] is None:
                totals = []
                for point in item.get("data") or []:
                    try:
                        totals.append(float(point["total"]))
                    except (KeyError, TypeError, ValueError):
                        continue
                if totals:
                    out[key] = max(totals)
    return out


async def post_metrics(post_id: str) -> tuple[dict[str, float | None] | None, str | None]:
    """`(metrics, None)` or `(None, reason)`. READ-ONLY: this serves the
    `reel_metrics` check, and a check never writes. A post Postiz reports as
    `{"missing": true}` (TikTok gave no id) stays unread and says so --
    connecting an id is a write, done only at posting time inside
    `content.publish_reel`. Never raises."""
    if not configured():
        return None, f"{KEY_ENV} unset"
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            path = PATH_ANALYTICS.format(id=post_id)
            answer = _json(await _call(c, "GET", path, "analytics",
                                       params={"date": 7}), "analytics")
            if isinstance(answer, dict) and answer.get("missing"):
                return None, (f"Postiz post {post_id}: TikTok id pending — "
                              f"connect it by hand (Postiz)")
    except PostizError as exc:
        return None, str(exc)
    found = _metrics(answer)
    if all(v is None for v in found.values()):
        return None, f"Postiz post {post_id}: analytics carried no readable metric"
    return found, None


# ------------------------------------------------------------- YouTube Shorts

# Miro, 2026-09-29: the AI reels go to YouTube Shorts too, curated (three a
# week), through Postiz only -- ATLAS holds no Google token for this. The
# integration is the one `POSTIZ_YOUTUBE_INTEGRATION` names (its Postiz id,
# or its name); never guessed, never "the first".
#
# VERIFIED 2026-09-29 against Postiz's own schema for `youtube` (the MCP's
# `integrationSchema`, and docs.postiz.com/public-api/providers/youtube):
# settings `__type: "youtube"`, `title` (string, 2-100, required), `type`
# (`public|private|unlisted`, required), `selfDeclaredMadeForKids`
# (`"yes"|"no"`, a STRING), `tags` (`[{"value", "label"}]`), `thumbnail`
# (optional, an upload's `{id, path}`); the post's content (the
# description) at most 5000 characters, and exactly one video. Postiz
# exposes NO altered/synthetic-content (AI disclosure) field for YouTube,
# so the disclosure is written into the description; YouTube Studio's own
# toggle is a step for a person. That the REST `/integrations` list names
# the provider `youtube` in `identifier` is ASSUMED from the TikTok row seen
# live (the MCP listing calls it `platform`); all three keys are read.
YOUTUBE_ENV = "POSTIZ_YOUTUBE_INTEGRATION"
YOUTUBE_IDENTIFIERS: tuple[str, ...] = ("youtube",)
YOUTUBE_TITLE_MIN, YOUTUBE_TITLE_MAX = 2, 100
YOUTUBE_DESCRIPTION_MAX = 5000
YOUTUBE_SETTINGS: dict[str, Any] = {"type": "public",
                                    "selfDeclaredMadeForKids": "no"}
YOUTUBE_URL = re.compile(r"^https://(www\.|m\.)?(youtube\.com/(shorts/|watch\?v=)"
                         r"|youtu\.be/)[A-Za-z0-9_-]{6,}([?&][^\s]*)?$")


def youtube_integration() -> str:
    """What `POSTIZ_YOUTUBE_INTEGRATION` names: a Postiz integration id or
    its name (a leading @ dropped). "" when unset."""
    return os.getenv(YOUTUBE_ENV, "").strip().lstrip("@")


def youtube_gap() -> str | None:
    """Why no Short can be posted, from configuration alone, or None."""
    missing = [name for name, value in ((KEY_ENV, configured()),
                                        (YOUTUBE_ENV, bool(youtube_integration())))
               if not value]
    if missing:
        return (f"{' and '.join(missing)} unset, so YouTube is not reachable "
                f"through Postiz")
    return None


def _provider(item: dict) -> str:
    return str(item.get("identifier") or item.get("providerIdentifier")
               or item.get("platform") or "").lower()


def pick_youtube(found: Any) -> tuple[str, str]:
    """(id, name) of the ONE enabled YouTube integration the env names, or
    PostizError. Pure. Only YouTube integrations are considered, so an env
    value naming a TikTok id can never route a Short there."""
    wanted = youtube_integration()
    if not wanted:
        raise PostizError(f"{YOUTUBE_ENV} unset: the YouTube channel is never "
                          f"guessed")
    listed = found if isinstance(found, list) else (
        found.get("integrations") if isinstance(found, dict) else None) or []
    tubes = [i for i in listed if isinstance(i, dict) and i.get("id")
             and not i.get("disabled") and _provider(i) in YOUTUBE_IDENTIFIERS]
    mine = [i for i in tubes if _is(i, wanted)]
    if len(mine) != 1:
        raise PostizError(
            f"Postiz has {'no' if not mine else 'more than one'} enabled YouTube "
            f"integration matching {YOUTUBE_ENV}={wanted!r} -- connect the "
            f"channel in Postiz, or correct the variable")
    return str(mine[0]["id"]), str(mine[0].get("name") or "").strip()


def required(settings: Any) -> list[str]:
    """The keys the provider's schema marks required (`__type` aside)."""
    body = settings.get("output") if isinstance(settings, dict) and isinstance(
        settings.get("output"), dict) else settings
    schema = body.get("settings") if isinstance(body, dict) else None
    found = schema.get("required") if isinstance(schema, dict) else None
    if not isinstance(found, list):
        return []
    return sorted(str(k) for k in found if str(k) != "__type")


def youtube_settings(title: str, tags: list[str]) -> dict[str, Any]:
    """The exact settings a Short is posted with. Pure. No AI-flag key: it
    is not declared, and Postiz would drop it silently."""
    out: dict[str, Any] = {"__type": "youtube", "title": title,
                           **YOUTUBE_SETTINGS}
    if tags:
        out["tags"] = [{"value": t, "label": t} for t in tags]
    return out


def validate_youtube(settings: Any, wanted: dict[str, Any]) -> str | None:
    """A reason when `wanted` would be dropped or refused, else None: every
    key sent is declared, every value allowed, every required key sent.
    Unreadable settings refuse -- never a blind post."""
    rules = declared(settings)
    if rules is None:
        return ("Postiz's YouTube settings could not be read, so nothing was "
                "posted blind (Postiz drops a setting it does not know)")
    for key, value in wanted.items():
        if key == "__type":
            continue
        if key not in rules:
            return (f"Postiz's YouTube integration does not declare {key!r}, so "
                    f"it would be silently dropped -- not posted")
        allowed = rules[key]
        if (allowed is not None and not isinstance(value, list | dict)
                and value not in allowed):
            return (f"Postiz's YouTube integration does not allow {key}="
                    f"{value!r} (it allows {sorted(map(str, allowed))})")
    missing = [k for k in required(settings) if k not in wanted]
    if missing:
        return (f"Postiz's YouTube integration requires {', '.join(missing)}, "
                f"which this Short does not set -- not posted")
    return None


def short_text_refusal(title: str, description: str) -> str | None:
    """YouTube's own limits on the words, checked before anything is sent."""
    if not YOUTUBE_TITLE_MIN <= len(title) <= YOUTUBE_TITLE_MAX:
        return (f"the title is {len(title)} characters; YouTube takes "
                f"{YOUTUBE_TITLE_MIN}-{YOUTUBE_TITLE_MAX}")
    if len(description) > YOUTUBE_DESCRIPTION_MAX:
        return (f"the description is {len(description)} characters; YouTube "
                f"takes {YOUTUBE_DESCRIPTION_MAX}")
    if any(ch in title + description for ch in "<>"):
        return "YouTube refuses '<' and '>' in a title or description"
    return None


def youtube_url(answer: dict) -> str | None:
    """The Short's link, only when Postiz itself returned a YouTube one."""
    url = str(answer.get("releaseURL") or answer.get("releaseUrl") or "").strip()
    return url if YOUTUBE_URL.match(url) else None


async def _youtube(c: httpx.AsyncClient) -> tuple[str, str]:
    found = _json(await _call(c, "GET", PATH_INTEGRATIONS, "integrations"),
                  "integrations")
    return pick_youtube(found)


async def youtube_channel() -> tuple[str, str]:
    """(id, name) of the configured YouTube integration, read live. Raises
    PostizError when unset, unreachable or not connected. Reads only."""
    if (gap := youtube_gap()) is not None:
        raise PostizError(gap)
    async with httpx.AsyncClient(timeout=30) as c:
        return await _youtube(c)


async def youtube_readiness(title: str = "An AI story",
                            tags: list[str] | None = None) -> tuple[bool, str]:
    """For the dry run, reads only: the integration and its settings,
    checked exactly as the post would be."""
    if (gap := youtube_gap()) is not None:
        return False, gap
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            integration, name = await _youtube(c)
            settings = _json(await _call(
                c, "GET", PATH_SETTINGS.format(id=integration), "settings"),
                "settings")
    except PostizError as exc:
        return False, str(exc)
    if (why := validate_youtube(settings, youtube_settings(title, tags or []))):
        return False, why
    return True, (f"YouTube {name or integration} via Postiz: public, not made "
                  f"for kids; Postiz's YouTube settings allow it")


async def post_youtube_short(video: bytes, title: str, description: str,
                             tags: list[str]) -> tuple[str, str | None]:
    """Post one Short: `(postiz_post_id, url)`, the URL only when Postiz
    returned a YouTube one. Raises PostizError naming the step on any
    failure -- never a silent no-op. Upload, the integration, its settings
    (read and honoured), then the post (`type: now`, public, not made for
    kids)."""
    if (gap := youtube_gap()) is not None:
        raise PostizError(gap)
    if (why := short_text_refusal(title, description)) is not None:
        raise PostizError(f"not posted: {why}")
    wanted = youtube_settings(title, tags)
    async with httpx.AsyncClient(timeout=180) as c:
        up = _json(await _call(c, "POST", PATH_UPLOAD, "upload",
                               files={"file": ("short.mp4", video, "video/mp4")}),
                   "upload")
        path = up.get("path") if isinstance(up, dict) else None
        if not path:
            raise PostizError("Postiz upload: no path came back")
        integration, _name = await _youtube(c)
        settings = _json(await _call(
            c, "GET", PATH_SETTINGS.format(id=integration), "settings"), "settings")
        if (why := validate_youtube(settings, wanted)) is not None:
            raise PostizError(why)
        body = {
            "type": "now", "date": datetime.now(UTC).isoformat(),
            "shortLink": False, "tags": [],
            "posts": [{
                "integration": {"id": integration},
                "value": [{"content": description,
                           "image": [{"id": up.get("id"), "path": path}]}],
                "settings": wanted,
            }],
        }
        made = _json(await _call(c, "POST", PATH_POSTS, "post", json=body), "post")
    first = made[0] if isinstance(made, list) and made else made
    if not isinstance(first, dict) or not (first.get("postId") or first.get("id")):
        raise PostizError("Postiz post: accepted, but named no post id")
    return str(first.get("postId") or first.get("id")), youtube_url(first)
