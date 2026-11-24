"""One-time login helper. Run it through bootstrap/linkedin-login.sh.

Opens a real browser window so the owner signs in himself, including any
verification LinkedIn asks for, and ATLAS never sees the password. It waits
by watching for the feed to load signed-in, so nobody has to press Enter in
a terminal. Tick "Keep me signed in".
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

SESSION = Path(os.getenv("LINKEDIN_SESSION_PATH", "./data/linkedin_session.json"))
TIMEOUT_S = int(os.getenv("LINKEDIN_LOGIN_TIMEOUT", "420"))


async def _signed_in(page) -> bool:
    try:
        await page.goto("https://www.linkedin.com/feed/",
                        wait_until="domcontentloaded", timeout=20000)
    except Exception:                                   # noqa: BLE001
        return False
    url = page.url
    return "/feed" in url and not any(s in url for s in ("/login", "/checkpoint",
                                                          "/authwall"))


async def main() -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        from .browser import context_kwargs
        proxy = os.getenv("LINKEDIN_LOGIN_PROXY", "")
        b = await pw.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
            proxy={"server": proxy} if proxy else None)
        if proxy:
            print(f"(traffic goes through {proxy}, so LinkedIn sees the server's IP)")
        ctx = await b.new_context(**context_kwargs())
        page = await ctx.new_page()
        await page.goto("https://www.linkedin.com/login")
        print("A browser window is open. Sign in to LinkedIn there, with "
              "'Keep me signed in' ticked. Do any verification it asks for.")
        print(f"I'll notice when you're in. Waiting up to {TIMEOUT_S // 60} "
              f"minutes…\n")
        deadline = asyncio.get_event_loop().time() + TIMEOUT_S
        checker = await ctx.new_page()
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(6)
            if await _signed_in(checker):
                break
        else:
            await b.close()
            sys.exit("timed out waiting for the sign-in — nothing was saved")
        await checker.close()
        SESSION.parent.mkdir(parents=True, exist_ok=True)
        await ctx.storage_state(path=str(SESSION))
        print(f"signed in. session saved to {SESSION}")
        await b.close()


if __name__ == "__main__":
    asyncio.run(main())
