"""TikTok's public oEmbed: the one TikTok endpoint ATLAS reads.

`GET https://www.tiktok.com/oembed?url=<video url>` needs no credential
and answers, for a public video, its author (`author_unique_id`), its
description as `title` and the video id (`embed_product_id`). The clips
lane (`clips.links.record_tiktok_link`) uses it to verify a video link a
Mac-side task hands in before recording it: TikTok's own word about the
video, not the task's (invariant 6). Nothing here posts, likes, follows or
signs in; there is no other TikTok call in ATLAS.

Seen live 2026-09-30 for a clips-account video: HTTP 200 with the caption
whole as `title` (line breaks read as spaces) and `author_unique_id`
`atlasclips12`; for an id that is not a video, HTTP 400 `{"message":
"Something went wrong","code":400}`. The `@handle` in the URL is not
checked by TikTok -- it resolves by video id -- so the caller must read
the author from the answer, never from the URL.

The clips package itself imports no HTTP client (tests/test_clips.py):
the read lives here, beside the Postiz one.
"""
from __future__ import annotations

from typing import Any

import httpx

OEMBED = "https://www.tiktok.com/oembed"
TIMEOUT_S = 10.0


class NotRead(Exception):
    """oEmbed could not be read: unreachable, timed out, a 5xx, not JSON.
    Nothing is known about the video."""


class NoSuchVideo(Exception):
    """TikTok answered 4xx: it knows no such public video."""


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TIMEOUT_S)


async def oembed(url: str) -> dict[str, Any]:
    """TikTok's oEmbed record for `url`, or NotRead / NoSuchVideo."""
    from .._http import explain
    try:
        async with client() as http:
            resp = await http.get(OEMBED, params={"url": url})
    except httpx.HTTPError as exc:
        raise NotRead(f"TikTok oEmbed not read: {explain(exc)}") from exc
    if 400 <= resp.status_code < 500:
        raise NoSuchVideo(f"TikTok does not know this video (oEmbed HTTP "
                          f"{resp.status_code})")
    if resp.status_code != 200:
        raise NotRead(f"TikTok oEmbed answered HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise NotRead("TikTok oEmbed answered something that is not JSON") from exc
    if not isinstance(data, dict):
        raise NotRead("TikTok oEmbed answered something that is not a video record")
    return data
