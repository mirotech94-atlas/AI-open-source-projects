"""One-time login helper. Run it through bootstrap/fiverr-login.sh.

Opens a real browser window so the owner logs in himself, including any 2FA, and
ATLAS never handles the Fiverr password.

It waits by WATCHING rather than by asking. The old version blocked on
input(), which meant whoever started the script had to be the one to press
Enter in that terminal — fine by hand, impossible when the script is started
for him. Polling the page for a logged-in state needs nobody at the keyboard
except the person logging in.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

STATE = Path(os.getenv("FIVERR_STATE_PATH", "./data/fiverr_state.json"))
TIMEOUT_S = int(os.getenv("FIVERR_LOGIN_TIMEOUT", "300"))


async def _logged_in(page) -> bool:
    """Fiverr has a session when a seller page loads without bouncing."""
    try:
        await page.goto("https://www.fiverr.com/seller_dashboard",
                        wait_until="domcontentloaded", timeout=20000)
    except Exception:                                   # noqa: BLE001
        return False
    url = page.url
    return "seller_dashboard" in url and "login" not in url


async def main() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx = await browser.new_context()
        page = await ctx.new_page()
        await page.goto("https://www.fiverr.com/login")

        print("A browser window is open. Log in to Fiverr there — including "
              "2FA if it asks.")
        print(f"I'll notice when you're in. Waiting up to "
              f"{TIMEOUT_S // 60} minutes…\n")

        deadline = asyncio.get_event_loop().time() + TIMEOUT_S
        checker = await ctx.new_page()
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(5)
            if await _logged_in(checker):
                break
        else:
            await browser.close()
            sys.exit("timed out waiting for the login — nothing was saved")

        await checker.close()
        STATE.parent.mkdir(parents=True, exist_ok=True)
        await ctx.storage_state(path=str(STATE))
        print(f"logged in. session saved to {STATE}")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
