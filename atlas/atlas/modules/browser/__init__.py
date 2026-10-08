"""browser — eyes on the web.

When Miro says "look it up on Google" he means "search the web and find me the
best thing". This is how ATLAS does that: a server-side headless Chromium that
reads public pages, and a search that goes through Brave (`BRAVE_API_KEY`) or
DuckDuckGo's HTML endpoint. Google's own results page blocks headless browsers
and is denied by policy, so search never touches it; the browser opens the
results instead.

What it watches: one check, `engine` — Chromium launches and loads
about:blank within 30 s; UNKNOWN, with the reason, when it cannot (including
when playwright is not installed: `pip install atlas[browser] && playwright
install chromium`).

What it may do: read pages (the `browser_open`, `browser_search` and
`browser_find_business` tools — reads, no approval), and one write,
`browser.submit_form` — Tier ASK, irreversible, with a dry run that reports
which selectors exist and where the form posts. It never signs in anywhere:
it never submits a form holding a password field, never opens a URL that
carries a credential, and a page that needs a login is a page ATLAS cannot
read, and it says so. Every context is fresh (no cookies, no storage, no
credentials, no service workers) and one browser runs at a time. The
extension's deny-list plus login/signin/checkout/cart hosts are denied
(`policy.DENY_DEFAULT`, extended by `BROWSER_DENY`), and every URL must
resolve to public addresses (`modules._netguard`); page text is wrapped as
untrusted; text and screenshots are kept under `data/browser/` for 24 h.

`browser.submit_form` is registered as the `browser_submit` tool but held by
no agent: it stays unreachable until Miro names who may ask for it.

For Miro — what the code cannot close. The checks above keep a private page
out of the prompt and out of the kept files; they cannot keep a request off
the network. Two things are closed only by an egress rule on the box (deny
RFC 1918, loopback, link-local, CGNAT/mesh and metadata to the browser and
fetch processes) or a filtering proxy passed to the browser: DNS rebinding (a
name that answers public to ATLAS's check and private to Chromium's own
lookup a moment later), and page JavaScript that reads something and sends it
to a public host before any after-load check runs. The one GET a redirect hop
makes before it is detected (including one a worker's redirect makes after
the check — nothing from it is read), and the handshake of a WebSocket any
page's worker opens (workers' sockets skip the socket guard; they are
detected and refuse the read afterwards), are the same class. SharedWorkers
are disabled at launch: Playwright can neither route nor see their requests.
A window the page opens is closed at once and refuses the read.

Endpoints assumed: Brave `GET /res/v1/web/search` (via `modules._web`),
`https://html.duckduckgo.com/html/?q=`.
"""
from __future__ import annotations

from datetime import timedelta

from ...kernel.contracts import Action, Check, Severity, Tier
from . import policy, session
from .session import check_engine, submit_form, submit_form_dry

__all__ = ["MODULE", "policy", "session"]


class BrowserModule:
    name = "browser"
    title = "Browser"
    requires_env = ()
    optional_env = ("BRAVE_API_KEY", "BROWSER_DENY", "BROWSER_DATA_DIR")
    setup_note = ("pip install atlas[browser] && playwright install chromium; "
                  "BRAVE_API_KEY is optional (DuckDuckGo otherwise)")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (Check("engine", check_engine, timedelta(hours=1), Severity.NORMAL,
                      "Chromium launches and loads about:blank within 30 s",
                      baseline_sigma=None),)

    def actions(self):
        return (Action(
            "browser.submit_form", Tier.ASK,
            "Fill and submit a form on a public web page. Irreversible: a sent "
            "form cannot be unsent. Never a login, a checkout or a password.",
            run=submit_form, dry_run=submit_form_dry, irreversible=True,
            params={
                "url": "the page holding the form (public http/https, not denied "
                       "by policy)",
                "fields": "selector=value pairs separated by ';', e.g. "
                          "#name=Miro;#message=Table for four at 8?",
            },
            max_per_hour=3,
        ),)

    async def signals(self):
        return ()

    def brief(self) -> str:
        return ""

    async def agenda(self, within):
        return ()

    async def inbox(self, limit: int = 10):
        return ()


MODULE = BrowserModule()
