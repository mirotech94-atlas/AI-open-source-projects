"""Fiverr — Playwright against a logged-in session. No public API exists.

STATUS, 2026-09-22: BLOCKED BY FIVERR, and this is a finding, not a bug.

Fiverr fronts its seller pages with PerimeterX. A headless Chromium hits the
challenge page ("It needs a human touch", PXCR10002539) at every seller URL
-- from the server AND from the owner's own Mac with his own live session, so
it is the automated browser being fingerprinted, not the IP. There is no
selector to fix. The same ruling LinkedIn reached applies here: driving a
site that actively blocks automation is how a seller account gets
suspended, and a suspended account costs far more than a missed alert.

What that leaves: the session check reports the wall as UNKNOWN rather
than reading its zeros as "inbox clear". Real Fiverr signal has to arrive
another way -- notification email is the honest one, since Fiverr mails
every message and order and the mail module already reads the inbox.

READ FROM EMAIL, since 2026-09-28 (`mailview`): `fiverr_inbox_mail` and
`fiverr_orders_mail` read Fiverr's notification emails through the mail
module's existing Gmail client, read-only, plus a signal for any buyer who
asks to move off-platform (a likely scam; never answered). The browser
checks below stay, and stay UNKNOWN while the wall stands.

Gig impressions and clicks are NOT in any email. They still need the Fiverr
analytics page (`gig_stats`). The only "performance stats" email in his
inbox is Fiverr Affiliates' monthly one -- affiliate referrals, not gig
stats -- so it is not parsed, and no impressions figure is ever inferred
from mail.

Two rules this module was built around still hold:

1. **Be gentle.** Human-paced, a few times a day, never a scraping cadence.
2. **Never send.** Buyer messages are drafted and shown to the owner. A bot reply
   to a buyer is both a policy risk and a reputation risk on the thing his
   gig ranking depends on.
"""
from __future__ import annotations

import json
import os
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ...kernel.contracts import (
    Action,
    ActionResult,
    Check,
    Health,
    Observation,
    Severity,
    Signal,
    Tier,
)
from .._http import redact
from . import mailview

STATE = Path(os.getenv("FIVERR_STATE_PATH", "/app/data/fiverr_state.json"))
BASE = "https://www.fiverr.com"

# Back off after the wall. Measured 2026-09-24: 102 walled requests in 24 h —
# unanswered_messages alone every 45 min — each a headless browser from a
# datacentre IP served a PerimeterX challenge. The wall does not lift by being
# asked again, and a steady drip of challenged automated hits is exactly the
# pattern that gets a seller account flagged. One attempt, then quiet.
# Persisted, because restarts happen several times a day and each reset an
# in-memory timer. The real Fiverr signal keeps arriving by email.
WALL = Path(os.getenv("FIVERR_WALL_PATH", "/app/data/fiverr_wall.json"))
WALL_BACKOFF = timedelta(hours=24)


def _record_wall() -> None:
    try:
        WALL.write_text(json.dumps({"at": datetime.now(UTC).isoformat()}))
    except OSError:
        pass                     # a backoff that cannot persist is just shorter


def _backing_off() -> str | None:
    """Why no request should be made right now, or None."""
    try:
        at = datetime.fromisoformat(json.loads(WALL.read_text())["at"])
    except (OSError, ValueError, KeyError):
        return None
    until = at + WALL_BACKOFF
    if datetime.now(UTC) >= until:
        return None
    return (f"Fiverr's bot wall (PerimeterX) blocked the last attempt at "
            f"{at:%H:%M} UTC; not trying again until {until:%a %H:%M} UTC so "
            f"the account is not flagged. Fiverr's emails still reach ATLAS.")


async def _browser():
    """Returns (playwright, browser, context) with the saved session."""
    # The session file first: "never logged in" is the actionable reason, and
    # playwright is an optional extra that a missing import would mask it with.
    if not STATE.exists():
        raise RuntimeError(
            f"no saved Fiverr session at {STATE}. Run the login helper "
            f"once: ./bootstrap/fiverr-login.sh — not "
            f"`python -m atlas.modules.fiverr.login` from a home "
            f"directory, which picks up whatever python means there.")
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"])
    context = await browser.new_context(
        storage_state=str(STATE),
        user_agent=os.getenv(
            "FIVERR_UA",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
        viewport={"width": 1440, "height": 900},
        locale="en-US", timezone_id="America/New_York")
    return pw, browser, context


async def _human_pause(page) -> None:
    await page.wait_for_timeout(random.randint(800, 2400))


class SessionExpired(Exception):
    """Fiverr bounced us to the login page. Nothing on the page is data."""


class BotWall(Exception):
    """PerimeterX served a challenge instead of the page. Same rule."""


async def _landed(page, expected: str) -> None:
    """Confirm we are where we asked to be, before reading anything off it.

    A Fiverr session expires and the site answers 200 with a login page. The
    selectors then match nothing, and the checks reported the resulting zeros
    as health: "inbox clear", "0 active orders", "0 impressions". An expired
    session read as a quiet week.

    That is the most expensive direction here. Response time drives gig
    ranking, so "no unanswered messages" when five buyers are waiting costs
    real money, and it does it silently for as long as the session stays
    dead.
    """
    url = page.url
    if "/login" in url or "/sign_in" in url or expected not in url:
        raise SessionExpired(
            f"Fiverr redirected to {url.split('?')[0]} instead of {expected} "
            f"— the session has expired. Re-run ./bootstrap/fiverr-login.sh")

    # PerimeterX serves its challenge AT the requested URL with a 200, so the
    # URL check above passes and the page reads as empty: zero messages,
    # zero orders, zero impressions, all "OK". It is the bot wall, and the
    # datacentre IP tripped it on the first real run. The wall says so in
    # its own title; read that rather than the zeros.
    try:
        title = (await page.title()) or ""
    except Exception:                                   # noqa: BLE001
        title = ""
    if "human touch" in title.lower() or "px-captcha" in (await page.content()):
        _record_wall()
        raise BotWall(
            f"Fiverr's bot detection (PerimeterX) blocked this request at "
            f"{expected} and served a challenge page instead. A headless "
            f"browser from the server's IP will keep hitting this; nothing "
            f"on the page is a measurement.")


async def check_session() -> Observation:
    """A silently expired session is the classic failure: every other check
    starts returning zero, which reads as 'business is quiet'."""
    if (why := _backing_off()) is not None:
        return Observation("session", Health.UNKNOWN, detail=why)
    try:
        pw, browser, ctx = await _browser()
    except Exception as exc:                            # noqa: BLE001
        # Not being able to START a browser is a check that could not run, not
        # evidence that the Fiverr session is dead. Reporting FAILING here
        # turned a missing dependency into a business alert.
        return Observation("session", Health.UNKNOWN,
                           detail=f"could not start a browser: {exc}")
    try:
        page = await ctx.new_page()
        await page.goto(f"{BASE}/seller_dashboard", wait_until="domcontentloaded",
                        timeout=30000)
        await _human_pause(page)
        # The URL alone said "session valid" while the page was PerimeterX's
        # challenge: the wall serves its 200 at the requested address. The
        # session was fine and every other check was reading zeros off a
        # bot-detection page as health.
        try:
            await _landed(page, "/seller_dashboard")
        except SessionExpired:
            return Observation("session", Health.FAILING,
                               detail="Fiverr session expired — re-run "
                                      "./bootstrap/fiverr-login.sh")
        except BotWall as exc:
            return Observation("session", Health.UNKNOWN, detail=str(exc))
        return Observation("session", Health.OK, detail="session valid")
    except Exception as exc:                            # noqa: BLE001
        return Observation("session", Health.UNKNOWN, detail=redact(exc))
    finally:
        await browser.close()
        await pw.stop()


async def check_unanswered_messages() -> Observation:
    if (why := _backing_off()) is not None:
        return Observation("unanswered_messages", Health.UNKNOWN, detail=why)
    try:
        pw, browser, ctx = await _browser()
    except Exception as exc:                            # noqa: BLE001
        return Observation("unanswered_messages", Health.UNKNOWN, detail=str(exc))
    try:
        page = await ctx.new_page()
        await page.goto(f"{BASE}/inbox", wait_until="networkidle", timeout=40000)
        await _landed(page, "/inbox")
        await _human_pause(page)
        unread = await page.locator("[data-testid='unread-indicator'], "
                                    ".unread, .conversation.unread").count()
        oldest_hours = 0.0
        stamps = await page.locator("[data-testid='conversation-time'], "
                                    ".conversation time").all_text_contents()
        for text in stamps[:10]:
            low = text.lower()
            if "hour" in low:
                try:
                    oldest_hours = max(oldest_hours, float(low.split()[0]))
                except ValueError:
                    pass
            elif "day" in low:
                oldest_hours = max(oldest_hours, 24.0)
        if not unread and not stamps:
            # Nothing matched at all: no unread marker and not one
            # conversation row. An empty inbox and a layout the selectors
            # no longer fit look identical from here, and "inbox clear" on
            # the metric that drives gig ranking is the wrong guess (RA-E1).
            return Observation(
                "unanswered_messages", Health.UNKNOWN, None, None,
                "the inbox loaded but no conversation matched the selectors, "
                "so unread messages are unmeasured rather than zero")
        # Fiverr's response clock is 24h and ranking depends on it.
        health = (Health.FAILING if unread and oldest_hours >= 12 else
                  Health.DEGRADED if unread else Health.OK)
        return Observation(
            "unanswered_messages", health, float(unread), "messages",
            f"{unread} unread buyer message(s), oldest about "
            f"{oldest_hours:.0f}h old" if unread else "inbox clear",
            evidence={"oldest_hours": oldest_hours})
    except SessionExpired as exc:
        return Observation("unanswered_messages", Health.FAILING, detail=str(exc))
    except BotWall as exc:
        return Observation("unanswered_messages", Health.UNKNOWN, detail=str(exc))
    except Exception as exc:                            # noqa: BLE001
        return Observation("unanswered_messages", Health.UNKNOWN, detail=redact(exc))
    finally:
        await browser.close()
        await pw.stop()


async def check_active_orders() -> Observation:
    if (why := _backing_off()) is not None:
        return Observation("active_orders", Health.UNKNOWN, detail=why)
    try:
        pw, browser, ctx = await _browser()
    except Exception as exc:                            # noqa: BLE001
        return Observation("active_orders", Health.UNKNOWN, detail=str(exc))
    try:
        page = await ctx.new_page()
        await page.goto(f"{BASE}/users/manage_orders", wait_until="networkidle",
                        timeout=40000)
        await _landed(page, "/manage_orders")
        await _human_pause(page)
        rows = await page.locator("tr[data-order-id], .order-row").all()
        if not rows:
            # No row matched. No active orders and a changed layout read the
            # same here, and "0 active orders" is a claim (RA-E1).
            return Observation(
                "active_orders", Health.UNKNOWN, None, None,
                "the orders page loaded but no order row matched the "
                "selectors, so active orders are unmeasured rather than zero")
        due_soon: list[str] = []
        for row in rows[:25]:
            text = (await row.inner_text()).lower()
            if "due" in text and ("hour" in text or "today" in text):
                due_soon.append(text.split("\n")[0][:80])
        return Observation(
            "active_orders",
            Health.DEGRADED if due_soon else Health.OK,
            float(len(rows)), "orders",
            f"{len(rows)} active order(s), {len(due_soon)} due within a day",
            evidence={"due_soon": due_soon})
    except SessionExpired as exc:
        return Observation("active_orders", Health.FAILING, detail=str(exc))
    except BotWall as exc:
        return Observation("active_orders", Health.UNKNOWN, detail=str(exc))
    except Exception as exc:                            # noqa: BLE001
        return Observation("active_orders", Health.UNKNOWN, detail=redact(exc))
    finally:
        await browser.close()
        await pw.stop()


async def check_gig_stats() -> Observation:
    if (why := _backing_off()) is not None:
        return Observation("gig_stats", Health.UNKNOWN, detail=why)
    try:
        pw, browser, ctx = await _browser()
    except Exception as exc:                            # noqa: BLE001
        return Observation("gig_stats", Health.UNKNOWN, detail=str(exc))
    try:
        page = await ctx.new_page()
        await page.goto(f"{BASE}/seller_dashboard/analytics",
                        wait_until="networkidle", timeout=40000)
        await _landed(page, "/analytics")
        await _human_pause(page)
        text = await page.inner_text("body")
        impressions: float | None = None
        for line in text.splitlines():
            if "impression" in line.lower():
                digits = "".join(ch for ch in line if ch.isdigit())
                if digits:
                    impressions = float(digits)
                    break

        # Not finding the number and finding a zero are different facts, and
        # the first one was being reported as the second. `impressions = 0.0`
        # as a starting value meant a page whose layout had changed -- or one
        # still rendering behind `networkidle` -- reported "0 impressions,
        # OK": a gig nobody can see, indistinguishable from a page ATLAS
        # could not read. On the metric that drives ranking, that is the
        # wrong way round.
        if impressions is None:
            return Observation(
                "gig_stats", Health.UNKNOWN, None, None,
                "the analytics page loaded but no impressions figure could "
                "be found on it — Fiverr has probably changed the layout, "
                "so this is unmeasured rather than zero")

        return Observation(
            "gig_stats",
            # Zero impressions on a live gig is a real finding, not health.
            Health.DEGRADED if impressions == 0 else Health.OK,
            impressions, "impressions",
            f"{impressions:,.0f} impressions"
            + (" — nobody is seeing the gigs" if impressions == 0 else ""))
    except SessionExpired as exc:
        return Observation("gig_stats", Health.FAILING, detail=str(exc))
    except BotWall as exc:
        return Observation("gig_stats", Health.UNKNOWN, detail=str(exc))
    except Exception as exc:                            # noqa: BLE001
        return Observation("gig_stats", Health.UNKNOWN, detail=redact(exc))
    finally:
        await browser.close()
        await pw.stop()


async def draft_buyer_reply(conversation: str, body: str) -> ActionResult:
    """Stores the draft for the owner. Deliberately does NOT type it into Fiverr:
    a draft sitting in a text box is one misclick from being sent."""
    import hashlib

    from ...kernel.state import fetch_one
    row = await fetch_one(
        """INSERT INTO messages (source, external_id, counterpart, body, draft, status)
           VALUES ('fiverr', $1, $1, '', $2, 'drafted') RETURNING id""",
        conversation, body)
    evidence = {"draft_sha256": hashlib.sha256(
        str(body or "").strip().encode("utf-8")).hexdigest()[:12]}
    if row is not None:
        evidence["draft_id"] = int(row["id"])
    return ActionResult(
        True,
        f"Draft ready for {conversation}. Review it and send from Fiverr "
        f"yourself — I don't send on your behalf there.",
        evidence=evidence, rollback_token=conversation)


async def draft_dry(conversation: str, body: str) -> ActionResult:
    if len(body.strip()) < 10:
        return ActionResult(False, "draft is empty")
    return ActionResult(True, f"would prepare a {len(body)}-char reply for "
                              f"{conversation}")


class FiverrModule:
    name = "fiverr"
    title = "Fiverr"
    requires_env = ()          # session file, not an env token
    # Not an env var: Fiverr has no API, so this drives a browser
    # session a human signed in. "No credentials needed" hid that.
    setup_note = ("needs one interactive login: "
                  "python -m atlas.modules.fiverr.login")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("session", check_session, timedelta(hours=4), Severity.HIGH,
                  "logged-in session still valid", baseline_sigma=None,
                  timeout=timedelta(seconds=90)),
            Check("unanswered_messages", check_unanswered_messages,
                  timedelta(minutes=45), Severity.CRITICAL,
                  "buyer messages awaiting a reply", baseline_sigma=None,
                  timeout=timedelta(seconds=90)),
            Check("active_orders", check_active_orders, timedelta(hours=2),
                  Severity.HIGH, "orders and delivery deadlines",
                  baseline_sigma=None, timeout=timedelta(seconds=90)),
            Check("gig_stats", check_gig_stats, timedelta(hours=12),
                  Severity.INFO, "impressions week over week",
                  timeout=timedelta(seconds=90)),
            # From Fiverr's emails, because the pages are walled. Read-only.
            Check("fiverr_inbox_mail", mailview.check_inbox_mail,
                  timedelta(minutes=30), Severity.INFO,
                  "buyer messages Fiverr emailed about (replies not visible)",
                  baseline_sigma=None, timeout=timedelta(seconds=60)),
            Check("fiverr_orders_mail", mailview.check_orders_mail,
                  timedelta(minutes=30), Severity.HIGH,
                  "new orders, due-soon notices and disputes from email",
                  baseline_sigma=None, timeout=timedelta(seconds=60)),
        )

    def actions(self):
        return (
            Action(
                name="fiverr.draft_buyer_reply",
                tier=Tier.AUTO,     # it only stores a draft; nothing is sent
                summary="Prepare a reply to a Fiverr buyer for the owner to send",
                run=draft_buyer_reply, dry_run=draft_dry,
                rollback=lambda t: draft_dry(t, "x"),
                params={"conversation": "buyer username or conversation id",
                        "body": "the reply text"},
                max_per_hour=10,
            ),
        )

    async def signals(self):
        # Email first: it is the Fiverr signal that still arrives.
        mailed = await mailview.signals()
        msgs = await check_unanswered_messages()
        if msgs.health is Health.FAILING:
            hours = msgs.evidence.get("oldest_hours", 0)
            return mailed + (Signal(
                title=f"Fiverr: buyer waiting {hours:.0f}h",
                body="Response time drives gig ranking, and the clock is 24h. "
                     "I can draft a reply now.",
                severity=Severity.CRITICAL,
                suggests="fiverr.draft_buyer_reply"),)
        orders = await check_active_orders()
        due = orders.evidence.get("due_soon", [])
        if due:
            return mailed + (Signal(
                title=f"Fiverr: {len(due)} order(s) due within a day",
                body="\n".join(f"- {d}" for d in due[:5]),
                severity=Severity.HIGH),)
        return mailed

    def brief(self) -> str:
        return "Fiverr: messages, orders, ranking"


MODULE = FiverrModule()
