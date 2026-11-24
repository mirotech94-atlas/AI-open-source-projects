"""A browser Miro signed into, driven from the server.

There is no LinkedIn API and no stored credential. Miro signs in once on his
Mac through `bootstrap/linkedin-login.sh`; the saved session (cookies and
local storage, no password) is synced here and every LinkedIn read and the
one LinkedIn write run through it. If the session is gone, LinkedIn answers
with its sign-in page and everything here stops: that is the control, not a
failure to engineer past.

Two things are checked on every page, before anything is read off it:

* that it is not the sign-in page (SignedOut), and
* that LinkedIn is not saying "unusual activity", "restricted", asking for a
  CAPTCHA or a verification (HardStop). On a hard stop the caller records the
  phrase in state and does nothing else on LinkedIn that run. The account
  all of this runs on is worth more than any single sweep.
"""
from __future__ import annotations

import asyncio
import os
import random
import re
from datetime import UTC, datetime
from pathlib import Path

SESSION = Path(os.getenv("LINKEDIN_SESSION_PATH", "/app/data/linkedin_session.json"))
SHOTS = Path(os.getenv("LINKEDIN_SHOTS_DIR", "/app/data/linkedin-agent/shots"))

HARD_STOP = re.compile(
    r"unusual activity|we.ve restricted|account has been restricted|"
    r"verify it.s you|security verification|let.s do a quick security check|"
    r"you.ve reached the (?:weekly )?(?:invitation|connection|message) limit|"
    r"limit reached", re.IGNORECASE)

NEGATORS = ("not", "no", "never", "n't", "without")


class SignedOut(Exception):
    """LinkedIn showed its sign-in page. Nothing on it is data."""


class HardStop(Exception):
    """LinkedIn is challenging or restricting the account. Stop everything."""


class NoSession(Exception):
    """No saved session on this box. Run bootstrap/linkedin-login.sh."""


# The one browser identity. The login window on the Mac and the headless
# browser on the server must present the same one, and the login must come
# from the server's IP (the login helper tunnels through it): the first
# session was minted from the Mac with a different fingerprint and LinkedIn
# revoked it after one run from the server.
UA = os.getenv("LINKEDIN_UA",
               "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
VIEWPORT = {"width": 1280, "height": 900}
LOCALE = "en-US"
TIMEZONE = "America/New_York"


def context_kwargs() -> dict:
    return {"user_agent": UA, "viewport": dict(VIEWPORT), "locale": LOCALE,
            "timezone_id": TIMEZONE}


# One LinkedIn browser at a time. The sweep and a publish ran concurrently
# once (the scheduled check fired while an approval was executing); two
# contexts on one session each rotate and write back cookies, and LinkedIn
# treats two live copies of one session as a reason to end it.
_ONE_AT_A_TIME: asyncio.Lock | None = None


def lock() -> asyncio.Lock:
    global _ONE_AT_A_TIME
    if _ONE_AT_A_TIME is None:
        _ONE_AT_A_TIME = asyncio.Lock()
    return _ONE_AT_A_TIME


async def browser():
    """Returns (playwright, browser, context) with the saved session. Hold
    `lock()` around the whole use; see above."""
    # The session first: "never signed in" is the actionable reason, and
    # playwright is an optional extra whose missing import would mask it.
    if not SESSION.exists():
        raise NoSession(
            f"no saved LinkedIn session at {SESSION}. Sign in once on the Mac: "
            f"./bootstrap/linkedin-login.sh")
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    b = await pw.chromium.launch(
        headless=True, args=["--disable-blink-features=AutomationControlled"])
    ctx = await b.new_context(storage_state=str(SESSION), **context_kwargs())
    return pw, b, ctx


async def keep_session(ctx) -> None:
    """Write the context's cookies back. LinkedIn rotates them; a snapshot
    from the day of the sign-in goes stale while a live session would not."""
    try:
        await ctx.storage_state(path=str(SESSION))
    except Exception as exc:  # noqa: BLE001
        log_warn(f"could not save the refreshed session: {exc}")


def log_warn(msg: str) -> None:
    import logging
    logging.getLogger("atlas.linkedin").warning(msg)


async def pause(page, lo: int = 1500, hi: int = 4000) -> None:
    """Human pace. LinkedIn renders late, and a run at machine speed is
    exactly what its automation detection is tuned for."""
    await page.wait_for_timeout(random.randint(lo, hi))


def _negated(text: str, at: int) -> bool:
    window = text[max(0, at - 48):at].lower()
    for stop in (". ", "! ", "\n"):
        cut = window.rfind(stop)
        if cut != -1:
            window = window[cut + len(stop):]
    return any(re.search(rf"\b{re.escape(w)}\b", window) for w in NEGATORS)


def scan_for_hard_stop(text: str) -> str | None:
    """The phrase LinkedIn used, or None. A denial of a phrase is not the
    phrase: a support article titled 'not a restriction' must not stop a run."""
    for m in HARD_STOP.finditer(text or ""):
        if not _negated(text, m.start()):
            return m.group(0)
    return None


async def goto(page, url: str, *, expect: str = "") -> str:
    """Load `url`, wait for LinkedIn to actually render, check we are signed
    in and not being challenged, and return the page's visible text."""
    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    await pause(page, 4000, 6500)
    here = page.url
    if any(s in here for s in ("/login", "/authwall", "/checkpoint", "/uas/")):
        raise SignedOut(f"LinkedIn sent {url.split('?')[0]} to {here.split('?')[0]}; "
                        f"the saved session is no longer signed in. Re-run "
                        f"./bootstrap/linkedin-login.sh")
    text = await body_text(page)
    if "Sign in" in text[:600] and "Join now" in text[:800]:
        raise SignedOut("LinkedIn served its sign-in page; the session has expired. "
                        "Re-run ./bootstrap/linkedin-login.sh")
    phrase = scan_for_hard_stop(text)
    if phrase:
        await snapshot(page, "hard-stop")
        raise HardStop(f"LinkedIn said {phrase!r} on {url.split('?')[0]}")
    if expect and expect not in here:
        raise RuntimeError(f"asked for {url.split('?')[0]}, landed on "
                           f"{here.split('?')[0]}")
    return text


async def body_text(page) -> str:
    try:
        return await page.evaluate("document.body ? document.body.innerText : ''")
    except Exception:  # noqa: BLE001
        return ""


async def snapshot(page, label: str) -> str | None:
    """A screenshot for the run log when something did not read. Never a
    secret: LinkedIn pages carry no credential, and the file stays in the
    container's data volume."""
    try:
        SHOTS.mkdir(parents=True, exist_ok=True)
        path = SHOTS / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{label}.png"
        await page.screenshot(path=str(path), full_page=False)
        return str(path)
    except Exception:  # noqa: BLE001
        return None
