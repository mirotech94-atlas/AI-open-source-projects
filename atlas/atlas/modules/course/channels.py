"""Where a course post can go, and the one leg that posts it itself.

The channels, a closed list:

  `linkedin`      -- HAND-OFF. ATLAS's LinkedIn publish (`linkedin.publish_post`)
                     reaches only the FlyCheap and Vault company pages, and its
                     gate refuses a post linking anywhere but that page's own
                     site. the owner's course belongs on his own profile, which ATLAS
                     cannot post to, so the card carries the exact text and he
                     posts it.
  `shopify_blog`  -- HAND-OFF. ATLAS holds no Shopify Admin API credential (it
                     only reads the public product JSON), so it cannot create
                     even a hidden draft article. The card carries the full
                     article; no credential is added for this.
  `postiz:<id>`   -- POSTED BY ATLAS through the Postiz Public API, to one
                     integration Postiz lists as connected and enabled, whose
                     provider takes a text post (`TEXT_PROVIDERS`), and which is
                     not a clips account.
  YouTube         -- NOT A CHANNEL. The YouTube Data API has no endpoint for
                     community posts, a Short needs a video, and the youtube
                     module is switched off by the owner ("we don't need YouTube",
                     2026-09-27).
  Instagram/TikTok -- NOT TEXT CHANNELS. Both take media only; the course-reels
                     lane (`content/course.py`) already covers them on the
                     AI-stories accounts.

**The clips accounts are never a course channel.** @example_handle and
@example_handle are campaign-only. An integration is excluded when its id is
the one `POSTIZ_TIKTOK_CLIPS_INTEGRATION` or `POSTIZ_YOUTUBE_CLIPS_INTEGRATION`
names, or its name (lowercased, with
everything but letters and digits removed) contains `clips` -- checked when a
post is proposed and again, against a fresh `/integrations` read, when it is
published.

What Postiz answers is assumed from its public docs except `/integrations`
(verified live 2026-09-25 for TikTok). Before posting, the integration's
settings are read (`/integration-settings/{id}`) and a post is refused when
the provider declares a required setting ATLAS does not send, so nothing is
posted blind. `shortLink` is false so the UTM tags survive.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from ..content import postiz

LINKEDIN = "linkedin"
SHOPIFY_BLOG = "shopify_blog"
HANDOFF_CHANNELS: tuple[str, ...] = (LINKEDIN, SHOPIFY_BLOG)
POSTIZ_PREFIX = "postiz:"

# Postiz providers that take a plain text post, with the platform name used as
# utm_source, the post shape, and the provider's length limit (characters).
# `url_counts` is how many characters a link costs there (X shortens every
# link to 23); None means the whole link counts.
@dataclass(frozen=True, slots=True)
class Provider:
    platform: str
    fmt: str
    limit: int
    url_counts: int | None = None


TEXT_PROVIDERS: dict[str, Provider] = {
    "x": Provider("x", "short", 280, 23),
    "bluesky": Provider("bluesky", "short", 300),
    "threads": Provider("threads", "social", 500),
    "mastodon": Provider("mastodon", "social", 500),
    "linkedin": Provider("linkedin", "social", 3000),
    "linkedin-page": Provider("linkedin", "social", 3000),
    "facebook": Provider("facebook", "social", 5000),
    "telegram": Provider("telegram", "social", 4096),
}

HANDOFF: dict[str, Provider] = {
    LINKEDIN: Provider("linkedin", "social", 3000),
    SHOPIFY_BLOG: Provider("shopify_blog", "article", 100_000),
}

NOT_CHANNELS: dict[str, str] = {
    "youtube": ("the YouTube Data API has no community-post endpoint, a Short "
                "needs a video, and the youtube module is switched off by the owner"),
    "instagram": ("media only; the course reels already post to the AI-stories "
                  "account"),
    "tiktok": "video only; the course reels already post to @atlasstories7",
}

_URL = re.compile(r"https?://\S+")


class ChannelError(Exception):
    """A channel cannot be used; the message is safe to show."""


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def is_clips(item: dict) -> bool:
    """A clips account: the integration the clips env names, or any whose
    name, display name or handle contains "clips"."""
    for clips in (postiz.clips_integration(), postiz.yt_clips_integration()):
        if clips and (str(item.get("id")) == clips
                      or _norm(item.get("name")) == _norm(clips)):
            return True
    fields = (item.get("name"), item.get("display"), item.get("profile"),
              item.get("username"))
    return any("clips" in _norm(f) for f in fields)


def identifier(item: dict) -> str:
    return str(item.get("identifier") or item.get("providerIdentifier") or "").lower()


def course_integrations(found: Any) -> list[dict]:
    """The integrations a course post may go to: enabled, a text provider,
    and not a clips account. Pure."""
    listed = found if isinstance(found, list) else (
        found.get("integrations") if isinstance(found, dict) else None) or []
    out = []
    for item in listed:
        if not isinstance(item, dict) or not item.get("id") or item.get("disabled"):
            continue
        if identifier(item) not in TEXT_PROVIDERS or is_clips(item):
            continue
        out.append(item)
    return out


async def _integrations(c: httpx.AsyncClient) -> Any:
    return postiz._json(await postiz._call(c, "GET", postiz.PATH_INTEGRATIONS,
                                           "integrations"), "integrations")


async def listing() -> dict[str, Any]:
    """Every channel with what it is, read now. Postiz's list is read live;
    its absence is said, never filled in."""
    at = datetime.now(UTC)
    out: dict[str, Any] = {
        "read_at": at.isoformat(timespec="seconds"),
        "handoff": [{"channel": LINKEDIN, "why": (
                        "ATLAS's LinkedIn publish reaches only the FlyCheap and "
                        "Vault company pages; the card carries the text for the owner's "
                        "profile")},
                    {"channel": SHOPIFY_BLOG, "why": (
                        "no Shopify Admin API credential; the card carries the "
                        "full article")}],
        "not_channels": NOT_CHANNELS,
    }
    if not postiz.configured():
        out["postiz"] = {"status": "unknown", "why": f"{postiz.KEY_ENV} unset"}
        return out
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            found = await _integrations(c)
    except postiz.PostizError as exc:
        out["postiz"] = {"status": "unknown", "why": str(exc)}
        return out
    listed = found if isinstance(found, list) else []
    usable = course_integrations(found)
    out["postiz"] = {
        "status": "read",
        "channels": [{"channel": f"{POSTIZ_PREFIX}{i['id']}",
                      "provider": identifier(i), "name": str(i.get("name") or "")}
                     for i in usable],
        "excluded": [{"name": str(i.get("name") or ""), "provider": identifier(i),
                      "why": ("clips account -- campaign only" if is_clips(i)
                              else "disabled" if i.get("disabled")
                              else "takes media, not a text post")}
                     for i in listed if isinstance(i, dict) and i not in usable],
    }
    return out


@dataclass(frozen=True, slots=True)
class Target:
    """A resolved channel: where the post goes and how it is shaped."""

    channel: str            # linkedin | shopify_blog | postiz:<id>
    provider: Provider
    name: str               # what the card calls it
    handoff: bool
    integration: str = ""   # the Postiz id, for postiz channels
    identifier: str = ""    # the Postiz provider identifier


def static(channel: str) -> Target | None:
    """A hand-off channel, resolved without the network; None otherwise."""
    if channel in HANDOFF:
        name = ("the owner's LinkedIn profile (by hand)" if channel == LINKEDIN
                else "the Shopify blog (by hand, as a hidden draft)")
        return Target(channel, HANDOFF[channel], name, True)
    return None


def shape_refusal(channel: str) -> str | None:
    """Why `channel` is not even a channel name, or None. Pure."""
    if channel in HANDOFF:
        return None
    if channel.startswith(POSTIZ_PREFIX) and channel[len(POSTIZ_PREFIX):].strip():
        return None
    low = channel.lower()
    for name, why in NOT_CHANNELS.items():
        if name in low:
            return f"{name} is not a course channel: {why}"
    return (f"unknown channel {channel!r}; one of {', '.join(HANDOFF_CHANNELS)} "
            f"or postiz:<integration id> (see course_channels)")


async def resolve(channel: str) -> Target:
    """The channel, checked now. Raises ChannelError with the reason."""
    if (why := shape_refusal(channel)) is not None:
        raise ChannelError(why)
    if (target := static(channel)) is not None:
        return target
    wanted = channel[len(POSTIZ_PREFIX):].strip()
    if not postiz.configured():
        raise ChannelError(f"{postiz.KEY_ENV} unset: no Postiz channel is reachable")
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            found = await _integrations(c)
    except postiz.PostizError as exc:
        raise ChannelError(str(exc)) from None
    listed = found if isinstance(found, list) else []
    match = [i for i in listed if isinstance(i, dict) and str(i.get("id")) == wanted]
    if not match:
        raise ChannelError(f"Postiz has no integration {wanted!r}")
    item = match[0]
    if is_clips(item):
        raise ChannelError(f"{item.get('name')!r} is a clips account: campaign "
                           f"clips only, never a course post")
    if item not in course_integrations(found):
        raise ChannelError(f"Postiz integration {item.get('name')!r} "
                           f"({identifier(item) or 'no provider'}) is disabled or "
                           f"does not take a text post")
    provider = TEXT_PROVIDERS[identifier(item)]
    return Target(channel, provider, f"{identifier(item)} "
                  f"@{str(item.get('name') or '').lstrip('@')} (Postiz)", False,
                  integration=wanted, identifier=identifier(item))


def length_of(text: str, provider: Provider) -> int:
    if provider.url_counts is None:
        return len(text)
    return len(_URL.sub("x" * provider.url_counts, text))


def length_refusal(text: str, provider: Provider) -> str | None:
    n = length_of(text, provider)
    if n > provider.limit:
        return (f"{n} characters; {provider.platform} takes {provider.limit} -- "
                f"choose a shorter lesson hook")
    return None


def required_missing(settings: Any) -> list[str]:
    """Settings the provider marks required, which a text post does not send
    (it sends only `__type`)."""
    body = settings.get("output") if isinstance(settings, dict) and isinstance(
        settings.get("output"), dict) else settings
    schema = body.get("settings") if isinstance(body, dict) else None
    required = schema.get("required") if isinstance(schema, dict) else None
    if not isinstance(required, list):
        return []
    return sorted(str(k) for k in required if str(k) != "__type")


POSTED, REFUSED, NOT_CONFIGURED = "posted", "refused", "not configured"


async def post_text(target: Target, text: str) -> tuple[str, str, str | None]:
    """One text post to one Postiz integration: `(status, detail, post_id)`.
    Never raises. The integration is re-read and re-checked first -- a clips
    account or a disabled one is refused here even if it was fine at draft."""
    if not postiz.configured():
        return NOT_CONFIGURED, f"{postiz.KEY_ENV} unset", None
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            found = await _integrations(c)
            usable = {str(i["id"]): i for i in course_integrations(found)}
            item = usable.get(target.integration)
            if item is None:
                raise postiz.PostizError(
                    f"Postiz integration {target.integration!r} is no longer an "
                    f"enabled, non-clips text channel")
            ident = identifier(item)
            settings = postiz._json(await postiz._call(
                c, "GET", postiz.PATH_SETTINGS.format(id=target.integration),
                "settings"), "settings")
            if (missing := required_missing(settings)):
                raise postiz.PostizError(
                    f"Postiz's {ident} integration requires {', '.join(missing)}, "
                    f"which a course text post does not set -- not posted blind")
            body = {
                "type": "now", "date": datetime.now(UTC).isoformat(),
                "shortLink": False, "tags": [],
                "posts": [{"integration": {"id": target.integration},
                           "value": [{"content": text, "image": []}],
                           "settings": {"__type": ident}}],
            }
            made = postiz._json(await postiz._call(c, "POST", postiz.PATH_POSTS,
                                                   "post", json=body), "post")
    except postiz.PostizError as exc:
        return REFUSED, str(exc), None
    first = made[0] if isinstance(made, list) and made else made
    if not isinstance(first, dict) or not (first.get("postId") or first.get("id")):
        return REFUSED, "Postiz accepted the post but named no post id", None
    post_id = str(first.get("postId") or first.get("id"))
    return POSTED, f"Postiz post {post_id} on {target.name}", post_id


def weekly_cap() -> int:
    """COURSE_POSTS_PER_WEEK, default 7, across every channel."""
    try:
        return max(0, min(50, int(os.getenv("COURSE_POSTS_PER_WEEK", "7"))))
    except ValueError:
        return 7
