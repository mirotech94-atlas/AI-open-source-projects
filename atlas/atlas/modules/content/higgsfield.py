"""Higgsfield: the OVERFLOW video generator for the reels -- the calendar
(`calendar._overflow`) and the daily reel (`render.render_veo`).

Miro, 2026-09-29: Veo stays first. When Veo's daily allowance is used up
(`VEO_DAILY_CLIPS` counted from Pacific midnight, the reserve included, or
Veo itself answering a daily-quota 429), the clips the day still needs may
be made here instead -- under a HARD monthly spend cap in dollars,
`HIGGSFIELD_MONTHLY_USD` (default 20).

Verified from the official docs (docs.higgsfield.ai, read 2026-09-28):

    POST https://api.higgsfield.ai/bytedance/seedance-2.0/text-to-video
         Authorization: Key <HIGGSFIELD_KEY_ID>:<HIGGSFIELD_KEY_SECRET>
         {"prompt", "duration" 4..15, "resolution" 480p|720p|1080p|4k,
          "aspect_ratio" ... "9:16" ..., "generate_audio" (default true)}
    -> {"status": "queued", "request_id", "status_url", "cancel_url"}
    GET  status_url  until completed | failed | nsfw | canceled
    -> completed: {"video": {"url": ...}}
    POST /estimate/<same model path> with the same body
    -> {"credits": "1.500", "usd": "0.094"}        (strings)

Errors: 401 bad credentials, 403 insufficient credits, 400 also means the
account's concurrency limit, 423/503 model blocked or not ready. Failed and
nsfw requests are not charged (reserved credits are refunded). A submission
has no idempotency key, so a POST that timed out is never repeated.

The model is Seedance 2.0 text-to-video: 9:16, native audio, an 8 s clip
like Veo's. Whether its speech is lip-synced Arabic is NOT stated in the
docs; it is the reason for choosing it, not a verified fact.

**The price.** Each clip's dollars come from the estimate endpoint for the
exact body about to be sent. When that endpoint does not answer with a
price, the price is NOT READ (invariant 6), and the row says so
(`price_read` false, with the exact seconds and resolution asked for). What
is booked against the cap for it is a WORST CASE, never a figure presented
as a price: `HIGGSFIELD_USD_PER_SECOND_MAX` (default 0.375, i.e. $3.00 for
8 s at 1080p -- an assumption, deliberately high: the console's model page
showed ~$0.14/s, resolution not stated, which the docs do not confirm) x
the seconds x a resolution factor that never discounts: 1 up to 1080p, 4
for 4k (four times the pixels). The check and the reel notes say "price not
read" beside it. A 401/403 from the estimate stops the clip: it is never a
reason to book a price at all.

**The cap.** Spend lives in `higgsfield_usage` (never `veo_usage`, which
Veo's own allowance and seconds cap read). A clip's price is reserved under
a per-process lock BEFORE the request, and a clip that would cross the cap
is never started. The reservation is refunded only when Higgsfield cannot
have charged: the submission was refused with a 4xx, never reached it, or
the request ended failed / nsfw / canceled. A timeout after the POST was
sent, or a poll that runs out, stays counted -- an under-count is the one
error a spend cap must not make.

The key ID and secret ride only in the Authorization header, only to the
API host (`HIGGSFIELD_URL` must be https, or nothing is sent): a
`status_url` naming another host is refused, and the finished video is
fetched WITHOUT the header, following redirects by hand -- every hop https
and on a public address (`_netguard.assert_public`), or it is not fetched.

**Is the key good?** (`check_budget`, invariant 7) A read that makes
nothing: GET `/requests/<nil uuid>/status`. The API reference for that
endpoint (docs.higgsfield.ai, read 2026-09-29) documents 401 "Missing or
invalid API credentials" and 404 "The request does not exist or belongs to
another account"; the errors page gives 403 as insufficient credits. So 404
is read as "the key authenticated", 401/403 as FAILING, and anything else
(a 200, a redirect, a 5xx, no answer) as UNKNOWN. That reading is from the
docs; it has not been checked against the live service from here.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from ...kernel.contracts import Health, Observation
from ...kernel.state import execute_sql, fetch_one
from .. import _netguard

KEY_ID_ENV = "HIGGSFIELD_KEY_ID"
SECRET_ENV = "HIGGSFIELD_KEY_SECRET"
DEFAULT_BASE = "https://api.higgsfield.ai"
MODEL = "bytedance/seedance-2.0/text-to-video"
CLIP_SECONDS = 8
DEFAULT_MONTHLY_USD = Decimal(20)
# Per second, booked only when the estimate names no price. An assumption.
DEFAULT_USD_PER_SECOND_MAX = Decimal("0.375")
RESOLUTIONS = ("720p", "1080p", "4k")
# Worst-case scaling by pixels, never below 1080p's: 4k is 4x 1080p.
RESOLUTION_FACTOR = {"720p": Decimal(1), "1080p": Decimal(1), "4k": Decimal(4)}
NIL_REQUEST = "00000000-0000-0000-0000-000000000000"
CHECK_TIMEOUT_S = 10.0
POLL_S = 2.0
POLL_MAX_S = 10.0
DEADLINE_S = 900.0
TERMINAL = frozenset({"completed", "failed", "nsfw", "canceled"})
NOT_CHARGED = frozenset({"failed", "nsfw", "canceled"})

_CAP_LOCK = asyncio.Lock()


class HiggsfieldError(Exception):
    """Higgsfield refused, failed, or was unreachable. The message is safe.
    `charged` says whether the request may have been billed (the caller
    refunds the reservation when it is False). `refused` is set only for a
    content outcome (failed / nsfw): the beat itself is the problem.
    Everything else -- credits, concurrency, a blocked model, the cap --
    means "later". `status_code` is the HTTP status when there was one."""

    def __init__(self, message: str, *, charged: bool = False,
                 refused: bool = False, status_code: int | None = None) -> None:
        super().__init__(message)
        self.charged = charged
        self.refused = refused
        self.status_code = status_code


def configured() -> bool:
    return bool(os.getenv(KEY_ID_ENV, "").strip()
                and os.getenv(SECRET_ENV, "").strip())


def base() -> str:
    """The API root. HiggsfieldError (nothing sent) unless it is https with
    a host: the key never travels in clear text."""
    value = (os.getenv("HIGGSFIELD_URL", "").strip() or DEFAULT_BASE).rstrip("/")
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname:
        raise HiggsfieldError("HIGGSFIELD_URL must be an https URL — nothing "
                              "was sent, so the key stays unsent")
    return value


# Miro, 2026-10-01: "a mix of both 720 and 1080". `HIGGSFIELD_RESOLUTION=mix`
# gives each STORY one resolution -- odd media ids 1080p, even 720p -- so a
# reel is never half as sharp in some beats, and the spend averages the two
# prices. The story is named by the renderer (`set_story`) for the span of
# its render; outside one (the check's probe) a mix reads 1080p, the dearer.
MIX = "mix"
_story: ContextVar[int | None] = ContextVar("higgsfield_story", default=None)


def mixing() -> bool:
    return os.getenv("HIGGSFIELD_RESOLUTION", "").strip().lower() == MIX


def pick_resolution(story_id: int) -> str:
    return "1080p" if story_id % 2 else "720p"


def set_story(story_id: int | str) -> Token:
    """A media id, or a calendar job's id (a string): a string's parity is
    its digest's, so the same job always gets the same resolution."""
    if isinstance(story_id, str):
        story_id = int(hashlib.sha256(story_id.encode()).hexdigest()[-8:], 16)
    return _story.set(story_id)


def reset_story(token: Token) -> None:
    _story.reset(token)


def resolution() -> str:
    """`HIGGSFIELD_RESOLUTION`, else Veo's own (1080p by default), so a reel
    mixing the two is not half as sharp in its Higgsfield parts. `mix`: the
    current story's resolution (see `pick_resolution`)."""
    from . import veo
    value = (os.getenv("HIGGSFIELD_RESOLUTION", "").strip().lower()
             or veo.resolution())
    if value == MIX:
        story = _story.get()
        return pick_resolution(story) if story is not None else "1080p"
    return value if value in RESOLUTIONS else "1080p"


def _decimal_env(name: str, default: Decimal) -> Decimal:
    try:
        value = Decimal(os.getenv(name, "").strip() or default)
    except InvalidOperation:
        return default
    return value if value.is_finite() and value >= 0 else default


def monthly_cap() -> Decimal:
    return _decimal_env("HIGGSFIELD_MONTHLY_USD", DEFAULT_MONTHLY_USD)


# Seen live 2026-10-01: with Veo off, the calendar banked nine days of
# business lessons in one night and booked $290 of a $292 month by 13:00 UTC
# -- Higgsfield has no daily allowance of its own the way Veo has its clips
# a day. `HIGGSFIELD_DAILY_USD` is that allowance: unset, a thirtieth of the
# month, so the budget lasts the month it was bought for. A UTC day.
def daily_cap() -> Decimal:
    default = (monthly_cap() / Decimal(30)).quantize(Decimal("0.01"))
    return _decimal_env("HIGGSFIELD_DAILY_USD", default)


def worst_case_usd(res: str | None = None,
                   seconds: int = CLIP_SECONDS) -> Decimal:
    """What a clip whose price was NOT read is booked at: the per-second
    maximum x seconds x the resolution factor (never below 1080p's)."""
    rate = _decimal_env("HIGGSFIELD_USD_PER_SECOND_MAX", DEFAULT_USD_PER_SECOND_MAX)
    factor = RESOLUTION_FACTOR.get(res or resolution(),
                                   max(RESOLUTION_FACTOR.values()))
    return rate * seconds * factor


def body(prompt: str) -> dict[str, Any]:
    return {"prompt": prompt, "duration": CLIP_SECONDS,
            "resolution": resolution(), "aspect_ratio": "9:16",
            "generate_audio": True}


@dataclass(frozen=True, slots=True)
class Ledger:
    usd: Decimal            # booked this month, reservations less refunds
    unread_usd: Decimal     # of which booked at worst case, price not read
    today_usd: Decimal = Decimal(0)     # booked since UTC midnight
    today_jobs: tuple[str, ...] = ()    # stories (job ids) booked since UTC midnight


# Miro, 2026-10-01: "use higgsfield once per day to post 2 videos per day
# only so we save". A story is one job id (a reel's media, a calendar day);
# past this many distinct stories in a UTC day, a NEW story waits for
# tomorrow -- a story already booked today may finish its own clips.
DEFAULT_DAILY_STORIES = 2


def daily_stories() -> int:
    try:
        return max(0, int(os.getenv("HIGGSFIELD_DAILY_STORIES", "").strip()
                          or DEFAULT_DAILY_STORIES))
    except ValueError:
        return DEFAULT_DAILY_STORIES


async def ledger() -> Ledger:
    """This month's `higgsfield_usage`, and today's within it. Raises when
    it cannot be read: an unreadable spend is never a spend of zero."""
    row = await fetch_one(
        """SELECT coalesce(sum(usd), 0) AS usd,
                  coalesce(sum(usd) FILTER (WHERE price_read IS NOT TRUE), 0)
                      AS unread_usd,
                  coalesce(sum(usd) FILTER (WHERE at >= date_trunc('day', now())), 0)
                      AS today_usd,
                  coalesce(array_agg(DISTINCT job_id) FILTER (
                      WHERE at >= date_trunc('day', now()) AND usd > 0
                        AND job_id IS NOT NULL), '{}') AS today_jobs
           FROM higgsfield_usage WHERE at >= date_trunc('month', now())""")
    if not row:
        return Ledger(Decimal(0), Decimal(0))
    return Ledger(Decimal(str(row["usd"])),
                  Decimal(str(row.get("unread_usd") or 0)),
                  Decimal(str(row.get("today_usd") or 0)),
                  tuple(str(j) for j in (row.get("today_jobs") or ())))


def _over_day(book: Ledger, usd: Decimal, clips: int,
              job_id: str | None = None) -> str | None:
    """Why `clips` costing `usd` do not fit today's allowance, or None."""
    read_at = f"read {datetime.now(UTC):%H:%M} UTC"
    limit = daily_stories()
    if (job_id is not None and job_id not in book.today_jobs
            and len(book.today_jobs) >= limit):
        return (f"Higgsfield: {len(book.today_jobs)} of {limit} stories a day "
                f"already booked today (UTC day, {read_at}) — this one waits "
                f"for tomorrow")
    day = daily_cap()
    # The day's first booking always fits: one story a day is made whatever
    # the allowance, and the allowance decides whether a second one is.
    if book.today_usd <= 0 or book.today_usd + usd <= day:
        return None
    return (f"Higgsfield daily cap: {clips} clip(s) need ${usd:.2f} and "
            f"${book.today_usd:.2f} of ${day:.2f} is booked today (UTC day, "
            f"{read_at}) — the rest waits for tomorrow")


async def spent_this_month() -> Decimal:
    """Raises when `higgsfield_usage` cannot be read."""
    return (await ledger()).usd


async def record(usd: Decimal, *, job_id: str | None = None,
                 part: str | None = None, seconds: int | None = None,
                 res: str | None = None, price_read: bool = True) -> None:
    """One `higgsfield_usage` row: a reservation (positive) or a refund,
    with the seconds and resolution asked for, and whether `usd` is a price
    Higgsfield stated (`price_read`) or a worst case booked without one."""
    await execute_sql(
        """INSERT INTO higgsfield_usage
               (usd, model, job_id, part, seconds, resolution, price_read)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        usd, MODEL, job_id, part, seconds, res, price_read)


# ----------------------------------------------------------------- secrets

def _credentials() -> tuple[str, str]:
    return os.getenv(KEY_ID_ENV, "").strip(), os.getenv(SECRET_ENV, "").strip()


def auth_header() -> dict[str, str]:
    key_id, secret = _credentials()
    return {"Authorization": f"Key {key_id}:{secret}"}


def _masked(text: str) -> str:
    from .._http import redact
    cleaned = redact(text)
    for value in _credentials():
        if value:
            cleaned = cleaned.replace(value, "<redacted>")
    return cleaned


def _said(r: httpx.Response) -> str:
    try:
        data = r.json()
    except ValueError:
        return ""
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, list):
        detail = "; ".join(str(d.get("msg") if isinstance(d, dict) else d)
                           for d in detail)
    return " ".join(str(detail or "").split())[:200]


_MEANING = {
    401: "credentials rejected",
    403: "insufficient credits",
    404: f"model {MODEL!r} not found for this account",
    423: "model temporarily blocked",
    503: "model disabled or not ready",
}


def _raise_for(r: httpx.Response, what: str) -> None:
    if r.status_code < 400:
        return
    said = _said(r)
    meaning = _MEANING.get(r.status_code, "")
    text = f"Higgsfield {what} failed: HTTP {r.status_code}"
    text += f" ({meaning})" if meaning else ""
    text += f": {said}" if said else ""
    raise HiggsfieldError(_masked(text), status_code=r.status_code)


# Where the key may be sent for a status poll: the API host, and the host
# Higgsfield's own accepted-request answer named for status_url (seen live
# 2026-09-30: accepted on api.higgsfield.ai, status_url on
# platform.higgsfield.ai; until then every Higgsfield clip was refused here
# and nothing it made was ever fetched). Anything else is still refused.
STATUS_HOSTS = ("api.higgsfield.ai", "platform.higgsfield.ai")


def pinned(url: str) -> str | None:
    """A reason when `url` is not on a Higgsfield host the key may go to."""
    parts, api = urlsplit(url), urlsplit(base())
    host = parts.hostname or ""
    if parts.scheme != "https" or host not in (api.hostname, *STATUS_HOSTS):
        return (f"Higgsfield's status URL is at {host or 'no host'!r}, not "
                f"{api.hostname} — not polled, so the key is not sent there")
    return None


# ----------------------------------------------------------------- price

@dataclass(frozen=True, slots=True)
class Price:
    usd: Decimal
    source: str             # "estimate" | "worst case"
    read: bool = True       # False: Higgsfield named no price; usd is a bound
    seconds: int = CLIP_SECONDS
    resolution: str = "1080p"

    def said(self) -> str:
        """The price as a person should read it: a figure only when it was
        read; otherwise "price not read" and what was booked in its place."""
        if self.read and self.source == "formula":
            return (f"${self.usd:.2f} (from Higgsfield's pricing formula for "
                    f"{self.seconds} s at {self.resolution})")
        if self.read:
            return f"${self.usd:.2f} (Higgsfield's estimate)"
        return (f"price not read — ${self.usd:.2f} booked at worst case "
                f"({self.seconds} s at {self.resolution})")


async def estimate(prompt: str, *, timeout: float = 20.0) -> Price:
    """What one clip of `prompt` costs, from Higgsfield's own estimate.
    HiggsfieldError on 401/403 (never a price for a rejected key or an
    empty balance); for anything else that names no price, the worst case,
    marked NOT READ."""
    request, url = body(prompt), f"{base()}/estimate/{MODEL}"
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as c:
        r = await c.post(url, headers=auth_header(), json=request)
    if r.status_code in (401, 403):
        _raise_for(r, "estimate")
    res = str(request["resolution"])
    if r.status_code < 300:
        try:
            data = r.json()
            if not isinstance(data, dict):
                data = {}
            usd = Decimal(str(data["usd"])) if data.get("usd") is not None else None
            if usd is not None and usd.is_finite() and usd > 0:
                return Price(usd, "estimate", True, CLIP_SECONDS, res)
            text = data.get("pricing_description")
            if isinstance(text, str) and (
                    usd := price_from_formula(text, res, CLIP_SECONDS)) is not None:
                return Price(usd, "formula", True, CLIP_SECONDS, res)
        except (ValueError, InvalidOperation):
            pass
    return Price(worst_case_usd(res), "worst case", False, CLIP_SECONDS, res)


# The frame Seedance makes for a 9:16 request at each resolution (`body`
# always asks 9:16). The formula below multiplies width x height, so these
# are the figures the price depends on.
FRAME_9_16 = {"480p": (480, 854), "720p": (720, 1280), "1080p": (1080, 1920),
              "4k": (2160, 3840)}
_TOKENS_PER_1000 = re.compile(r"Per 1,000 video tokens:\s*(.+?)\.\s", re.IGNORECASE)
_RATE = re.compile(r"([0-9a-zA-Z/]+)\s*\$([0-9]+(?:\.[0-9]+)?)")


def price_from_formula(text: str, res: str, seconds: int) -> Decimal | None:
    """Seen live 2026-09-30: `/estimate` answers {"type": "description",
    "pricing_description": "Token-metered pricing. Billable video tokens =
    ceil(seconds x width x height x 24 fps / 1024) ... Per 1,000 video
    tokens: 480p/720p/1080p $0.014, 4K $0.008 ..."} -- a formula, not a
    figure. Applied to the exact clip asked for it is Higgsfield's own
    price for it, read from Higgsfield (invariant 6); None when the text
    names no rate for this resolution, and the caller books the worst case."""
    found = _TOKENS_PER_1000.search(text + " ")
    frame = FRAME_9_16.get(res.lower())
    if found is None or frame is None or "× 24 fps / 1024" not in text:
        return None
    rate: Decimal | None = None
    for names, dollars in _RATE.findall(found.group(1)):
        if res.lower() in [n.lower() for n in names.split("/")]:
            rate = Decimal(dollars)
    if rate is None:
        return None
    width, height = frame
    tokens = -(-(seconds * width * height * 24) // 1024)        # ceil
    return (Decimal(tokens) * rate / Decimal(1000)).quantize(Decimal("0.0001"))


async def reserve(prompt: str, *, job_id: str | None = None,
                  part: str | None = None) -> tuple[Price | None, str | None]:
    """Reserve one clip's dollars under the lock: (price, None), or (None,
    why not). The cap is hard: a clip that would cross it is not started."""
    cap = monthly_cap()
    async with _CAP_LOCK:
        try:
            price = await estimate(prompt)
        except HiggsfieldError as exc:
            return None, str(exc)
        except httpx.HTTPError as exc:
            from .._http import explain
            return None, _masked(f"Higgsfield unreachable: {explain(exc)}")
        book = await ledger()
        used = book.usd
        if used + price.usd > cap:
            cost = (f"a clip costs ${price.usd:.2f}" if price.read else
                    f"a clip's {price.said()}")
            return None, (f"Higgsfield cap: ${used:.2f} of ${cap:.2f} booked this "
                          f"month, {cost} (read {datetime.now(UTC):%H:%M} UTC)")
        if (why := _over_day(book, price.usd, 1, job_id)) is not None:
            return None, why
        await record(price.usd, job_id=job_id, part=part, seconds=price.seconds,
                     res=price.resolution, price_read=price.read)
    return price, None


async def reserve_all(prompts: list[str], *, job_id: str,
                      parts: list[str]) -> tuple[list[Price] | None, str | None]:
    """Reserve every clip of a story at once, or none: (prices, None) or
    (None, why not). A story is never half-made at the cap -- its clips'
    prices (each from its own estimate, or its worst case when not read)
    are summed under the lock, and the whole story is refused when the sum
    would cross `HIGGSFIELD_MONTHLY_USD`."""
    cap = monthly_cap()
    async with _CAP_LOCK:
        prices: list[Price] = []
        try:
            for prompt in prompts:
                prices.append(await estimate(prompt))
        except HiggsfieldError as exc:
            return None, str(exc)
        except httpx.HTTPError as exc:
            from .._http import explain
            return None, _masked(f"Higgsfield unreachable: {explain(exc)}")
        book = await ledger()
        used = book.usd
        total = sum((p.usd for p in prices), Decimal(0))
        if used + total > cap:
            unread = sum(not p.read for p in prices)
            said = (f", {unread} of them at worst case (price not read)"
                    if unread else "")
            return None, (f"Higgsfield cap: {len(prices)} clip(s) need "
                          f"${total:.2f}{said} and ${used:.2f} of ${cap:.2f} is "
                          f"booked this month (read {datetime.now(UTC):%H:%M} "
                          f"UTC) — refused as a whole before the first clip")
        if (why := _over_day(book, total, len(prices), job_id)) is not None:
            return None, f"{why} — refused as a whole before the first clip"
        for price, part in zip(prices, parts, strict=True):
            await record(price.usd, job_id=job_id, part=part,
                         seconds=price.seconds, res=price.resolution,
                         price_read=price.read)
    return prices, None


async def refund(price: Price, *, job_id: str | None, part: str | None) -> None:
    """Give back one reservation Higgsfield cannot have charged."""
    await record(-price.usd, job_id=job_id, part=part, seconds=price.seconds,
                 res=price.resolution, price_read=price.read)


# ----------------------------------------------------------------- one clip

async def clip(prompt: str) -> bytes:
    """One 8 s 9:16 clip's bytes, or HiggsfieldError. Charges nothing
    itself: the caller reserved the price before asking, and refunds it
    only when the error says `charged` is False."""
    if not configured():
        raise HiggsfieldError(f"{KEY_ID_ENV} / {SECRET_ENV} not set")
    from . import guard
    # The same door Veo's clips pass (`veo.clip`): no studio look, no
    # existing character. Checked before anything is sent or billed.
    if (why := guard.ip_refusal(prompt)) is not None:
        raise HiggsfieldError(f"refused before Higgsfield: {why}", refused=True)
    submit = f"{base()}/{MODEL}"                     # https, or nothing is sent
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as c:
        try:
            r = await c.post(submit, headers=auth_header(), json=body(prompt))
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise _unreachable(exc, charged=False) from None   # never reached it
        except httpx.HTTPError as exc:
            # Sent, and no answer: it may have been taken. Never re-POSTed
            # (no idempotency key), and the reservation stays.
            raise _unreachable(exc, charged=True) from None
        try:
            _raise_for(r, "submit")
        except HiggsfieldError as exc:
            # A 4xx is a refusal before any work. A 5xx is ambiguous, and
            # an ambiguous request stays counted.
            exc.charged = r.status_code >= 500
            raise
        url = await _wait(c, r)
    # The CDN is not the API: the download carries no key.
    return await _download(url)


async def _download(url: str) -> bytes:
    """The finished clip, with NO credentials. Redirects are followed by
    hand: every hop must be https and resolve only to public addresses, or
    nothing is fetched from it. The URL (it may be signed) never enters a
    message. Every failure here is `charged`: the clip was made."""
    try:
        async with httpx.AsyncClient(timeout=120, follow_redirects=False) as d:
            for _hop in range(_netguard.MAX_REDIRECTS + 1):
                parts = urlsplit(url)
                host = parts.hostname or "no host"
                if parts.scheme != "https":
                    raise HiggsfieldError("Higgsfield's video download left https "
                                          "— not fetched", charged=True)
                if await _netguard.assert_public(url) is not None:
                    raise HiggsfieldError(
                        f"Higgsfield's video host {host} is not on the public "
                        f"web — not fetched", charged=True)
                r = await d.get(url)
                location = (r.headers.get("location")
                            if r.status_code in (301, 302, 303, 307, 308) else None)
                if not location:
                    break
                url = urljoin(url, location)
            else:
                raise HiggsfieldError(
                    f"Higgsfield's video download redirected more than "
                    f"{_netguard.MAX_REDIRECTS} times — not fetched", charged=True)
    except httpx.HTTPError as exc:
        raise _unreachable(exc, charged=True) from None
    if r.status_code >= 400:
        raise HiggsfieldError(f"Higgsfield's video download failed: HTTP "
                              f"{r.status_code}", charged=True)
    if not r.content:
        raise HiggsfieldError("Higgsfield's video download was empty", charged=True)
    return r.content


def _unreachable(exc: httpx.HTTPError, *, charged: bool) -> HiggsfieldError:
    from .._http import explain
    return HiggsfieldError(_masked(f"Higgsfield unreachable: {explain(exc)}"),
                           charged=charged)


async def _wait(c: httpx.AsyncClient, accepted: httpx.Response) -> str:
    """Poll an accepted request to its end; the video's https URL. Every
    error from here on is `charged` unless Higgsfield says the request ended
    failed / nsfw / canceled, which its docs say are not billed."""
    try:
        handle = accepted.json() or {}
    except ValueError:
        handle = {}
    status_url = handle.get("status_url") if isinstance(handle, dict) else None
    if not isinstance(status_url, str) or not status_url:
        raise HiggsfieldError("Higgsfield accepted the request but named no "
                              "status url", charged=True)
    if (why := pinned(status_url)) is not None:
        raise HiggsfieldError(why, charged=True)
    started, delay = time.monotonic(), POLL_S
    while True:
        try:
            r = await c.get(status_url, headers=auth_header())
        except httpx.HTTPError:
            r = None                                # retried, like a 5xx
        if r is not None and 400 <= r.status_code < 500:
            try:
                _raise_for(r, "poll")
            except HiggsfieldError as exc:
                exc.charged = True
                raise
        state: dict[str, Any] = {}
        if r is not None and r.status_code < 400:
            try:
                data = r.json()
            except ValueError:
                data = {}
            if not isinstance(data, dict):
                # Not the documented object: never an AttributeError that
                # escapes as "not charged". The request was accepted.
                raise HiggsfieldError(
                    f"Higgsfield's status answered a {type(data).__name__}, not "
                    f"the documented object — kept as spent", charged=True)
            state = data
        status = str(state.get("status") or "")
        if status in TERMINAL:
            break
        if time.monotonic() - started > DEADLINE_S:
            raise HiggsfieldError(
                f"Higgsfield did not finish within {DEADLINE_S:.0f}s (last "
                f"status {status or 'unread'}; kept as spent)", charged=True)
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, POLL_MAX_S)
    if status in NOT_CHARGED:
        said = " ".join(str(state.get("error") or "").split())[:200]
        raise HiggsfieldError(
            _masked(f"Higgsfield ended {status}" + (f": {said}" if said else "")),
            charged=False, refused=status in ("failed", "nsfw"))
    video = state.get("video")
    url = video.get("url") if isinstance(video, dict) else None
    if not isinstance(url, str) or urlsplit(url).scheme != "https":
        raise HiggsfieldError("Higgsfield finished but returned no https video url",
                              charged=True)
    return url


# ----------------------------------------------------------------- the check

PROBE_PROMPT = "A quiet street at dawn, stylized 3D animation."


async def probe_key() -> tuple[Health | None, str]:
    """Whether Higgsfield accepts the key, by a read that makes and bills
    nothing: the status of the nil request id. (None, "") when the key
    authenticated (404, per the endpoint's documented "does not exist or
    belongs to another account"); (FAILING, why) on 401/403; (UNKNOWN, why)
    for anything else, the key never in `why`."""
    from .._http import explain
    try:
        url = f"{base()}/requests/{NIL_REQUEST}/status"
        async with httpx.AsyncClient(timeout=CHECK_TIMEOUT_S,
                                     follow_redirects=False) as c:
            r = await c.get(url, headers=auth_header())
    except HiggsfieldError as exc:
        return Health.UNKNOWN, str(exc)
    except httpx.HTTPError as exc:
        return Health.UNKNOWN, _masked(f"Higgsfield unreachable: {explain(exc)}")
    if r.status_code == 404:
        return None, ""
    if r.status_code in (401, 403):
        try:
            _raise_for(r, "key check")
        except HiggsfieldError as exc:
            return Health.FAILING, str(exc)
    return Health.UNKNOWN, (f"Higgsfield key not confirmed: its status read "
                            f"answered HTTP {r.status_code}, not the 404 an "
                            f"accepted key gets")


async def check_budget() -> Observation:
    """`content.higgsfield_budget`: dollars booked this month against
    `HIGGSFIELD_MONTHLY_USD`, with the time they were read, whether the key
    works (`probe_key`), and what one clip costs now (the estimate).

    OK only when all three were read: the key confirmed, the ledger read and
    a price named by Higgsfield. UNKNOWN when the keys are missing, the key
    cannot be confirmed, the ledger is unreadable, the API cannot be reached
    or the estimate names no price ("price not read"); FAILING when
    Higgsfield rejects the key (401) or the balance (403), with the reason
    and never the key; DEGRADED when the cap leaves no room for a clip."""
    from .._http import explain
    name = "higgsfield_budget"
    if not configured():
        return Observation(name, Health.UNKNOWN,
                           detail=f"{KEY_ID_ENV} / {SECRET_ENV} not set — no "
                                  f"overflow renders")
    health, why = await probe_key()
    at = datetime.now(UTC)
    if health is not None:
        return Observation(name, health, detail=f"{why} (read {at:%H:%M} UTC)",
                           evidence={"key_confirmed": False,
                                     "read_at": at.isoformat()})
    cap = monthly_cap()
    try:
        book = await ledger()
    except Exception as exc:  # noqa: BLE001
        return Observation(name, Health.UNKNOWN,
                           detail=_masked(f"key accepted, but the spend is "
                                          f"unreadable: {explain(exc)}"),
                           evidence={"key_confirmed": True})
    used = book.usd
    at = datetime.now(UTC)
    evidence: dict[str, Any] = {"key_confirmed": True, "spent_usd": str(used),
                                "unread_usd": str(book.unread_usd),
                                "cap_usd": str(cap), "read_at": at.isoformat()}
    spend = f"${used:.2f} of ${cap:.2f} booked this month (read {at:%H:%M} UTC)"
    if book.unread_usd:
        spend += (f", of which ${book.unread_usd:.2f} is a worst case booked for "
                  f"clips whose price was not read")
    try:
        price = await estimate(PROBE_PROMPT, timeout=CHECK_TIMEOUT_S)
    except HiggsfieldError as exc:
        health = Health.FAILING if exc.status_code in (401, 403) else Health.UNKNOWN
        return Observation(name, health, float(used), "usd",
                           detail=f"{exc} — {spend}", evidence=evidence)
    except httpx.HTTPError as exc:
        return Observation(name, Health.UNKNOWN, float(used), "usd",
                           detail=_masked(f"Higgsfield unreachable: {explain(exc)}")
                           + f" — {spend}", evidence=evidence)
    evidence["price_read"] = price.read
    if price.read:
        evidence["clip_usd"] = str(price.usd)
    else:
        evidence["worst_case_usd"] = str(price.usd)
    detail = (f"key accepted; {spend}; one {CLIP_SECONDS} s {price.resolution} "
              f"clip: {price.said()}, {MODEL}")
    if used >= cap or (price.read and used + price.usd > cap):
        return Observation(name, Health.DEGRADED, float(used), "usd",
                           detail=f"cap reached, no overflow until next month: "
                                  f"{detail}", evidence=evidence)
    day = daily_cap()
    evidence["today_usd"] = str(book.today_usd)
    evidence["daily_cap_usd"] = str(day)
    detail += f"; today ${book.today_usd:.2f} of ${day:.2f} (UTC day)"
    if price.read and book.today_usd > 0 and book.today_usd + price.usd > day:
        return Observation(name, Health.DEGRADED, float(used), "usd",
                           detail=f"today's cap reached, more tomorrow: {detail}",
                           evidence=evidence)
    if not price.read:
        # Whether one more clip fits is not known without its price.
        return Observation(name, Health.UNKNOWN, float(used), "usd",
                           detail=f"price not read: {detail}", evidence=evidence)
    return Observation(name, Health.OK, float(used), "usd",
                       detail=detail, evidence=evidence)
