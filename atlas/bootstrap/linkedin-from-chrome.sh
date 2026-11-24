#!/usr/bin/env bash
# Sign in to LinkedIn in a REAL Chrome window (Apple / Google sign-in work there,
# unlike in an automated browser), then save that session for ATLAS.
#   ./bootstrap/linkedin-from-chrome.sh
# ATLAS never sees the password; only the session cookies are saved.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/.venv/bin/python"
[ -x "$PY" ] || { echo "no virtualenv at $PY" >&2; exit 1; }
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[ -x "$CHROME" ] || { echo "Google Chrome not found" >&2; exit 1; }
PROFILE="$(mktemp -d)"; PORT=9333
STATE="$REPO/data/app/linkedin_session.json"
"$CHROME" --user-data-dir="$PROFILE" --remote-debugging-port=$PORT --no-first-run \
  --no-default-browser-check "https://www.linkedin.com/login" >/dev/null 2>&1 &
CPID=$!
trap 'kill $CPID 2>/dev/null || true; rm -rf "$PROFILE"' EXIT
echo "Chrome opened. Sign in to LinkedIn there (Apple sign-in is fine); this notices by itself."
"$PY" - "$STATE" "$PORT" <<'PYEOF'
import asyncio, json, sys, time
from playwright.async_api import async_playwright
state, port = sys.argv[1], sys.argv[2]
async def main():
    async with async_playwright() as pw:
        for _ in range(30):
            try:
                b = await pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}"); break
            except Exception: await asyncio.sleep(1)
        else: sys.exit("could not reach Chrome")
        ctx = b.contexts[0]; end = time.time() + 600
        while time.time() < end:
            ck = await ctx.cookies("https://www.linkedin.com")
            if any(c["name"] == "li_at" for c in ck):
                await asyncio.sleep(3)
                await ctx.storage_state(path=state); print(f"signed in; session saved ({len(ck)} cookies)"); return
            await asyncio.sleep(2)
        sys.exit("timed out waiting for the sign-in")
asyncio.run(main())
PYEOF
chmod 600 "$STATE"
echo "Checking it works…"
DOCKER_CONTEXT="${DOCKER_CONTEXT:-orbstack}" docker exec atlas-agency-api-1 python -m atlas.modules.linkedin.sweep --check
