"""perplexity — the research source's budget, as a check.

Watches: this month's Perplexity calls against `PERPLEXITY_MONTHLY_CAP`
(default 300), counted in `perplexity_calls` by `atlas.modules._perplexity`,
which is where the client, the cap and the scrub live. The check is
`perplexity.budget`: OK under the cap with the count and the time it was
read, DEGRADED at the cap (every caller is refused until next month), and
UNKNOWN when `PERPLEXITY_API_KEY` is unset, the count cannot be read, the
MCP handshake fails or refuses the token, or the server no longer offers
one of the four tools this module calls — never a zero it did not measure
(invariant 7). The check itself sends `initialize`/`tools/list` to read
that live, never a `tools/call`.

May do: nothing. Perplexity is a source with a budget, not a pair of hands;
it is reached through the read tools and the seams listed in `_perplexity`'s
docstring, never through the executor.

Assumes: Perplexity's remote MCP server at `PERPLEXITY_MCP_URL` (default
`https://api.perplexity.ai/mcp`) with a bearer `PERPLEXITY_API_KEY`. There
is no fallback (Task 50): Perplexity's chat-completions endpoint returned
`403 chat_completions_not_available` on a live check on 2026-09-25, so an
unreachable MCP server is a plain reason, never a second call elsewhere.

Loads without the key on purpose (`optional_env`, not `requires_env`): a
disabled module has no check at all, and an unconfigured research source
should read as UNKNOWN on the board, not vanish from it.
"""
from __future__ import annotations

from datetime import timedelta

from ...kernel.contracts import Check, Severity
from .._perplexity import check_budget


class PerplexityModule:
    name = "perplexity"
    title = "Perplexity (research over MCP)"
    requires_env = ()
    optional_env = ("PERPLEXITY_API_KEY", "PERPLEXITY_MCP_URL",
                    "PERPLEXITY_MONTHLY_CAP")
    setup_note = ("no PERPLEXITY_API_KEY set — research, knowledge mode and "
                  "the agents' perplexity_* tools fall back to what they did "
                  "before, and perplexity.budget reads UNKNOWN")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return (
            Check("budget", check_budget, timedelta(hours=1), Severity.NORMAL,
                  "Perplexity calls this month against the monthly cap",
                  baseline_sigma=None),
        )

    def actions(self):
        return ()

    def brief(self) -> str:
        return ""


MODULE = PerplexityModule()
