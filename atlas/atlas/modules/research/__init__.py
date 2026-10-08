"""research — "have it for me by morning".

Watches nothing continuously; it acts once, on request. What it may do:
search the web (`BRAVE_API_KEY`, or `PERPLEXITY_API_KEY` as a fallback
backend when Brave is not set), fetch up to six public pages of text, ask a
model (`Need.HARD`) to write a cited markdown report, and file it as a
`reports` row (kind='research') plus a `research_reports` row that can later
be withdrawn. Every fetched page is `wrap_untrusted()` before it reaches a
prompt — a web page is somebody else's text, not an instruction — and every
number in the finished report is checked against the raw source text before
filing: one that cannot be found is listed under "Unverified figures" rather
than trusted. Assumes Brave Search's HTTP API or Perplexity's chat
completions API (`atlas.modules._web.search`, which picks whichever key is
set); with neither set, `research.report` fails cleanly and says so rather
than guessing. Perplexity is a wrapped source like any web page, never an
oracle: its own answer is quoted and cited, not written into the report as
if ATLAS had said it.

It never posts, sends, deletes or spends anything.
"""
from __future__ import annotations

from ...kernel.contracts import Action, Tier
from .report import due_signals, report_dry, run_report, take_finished, withdraw


class ResearchModule:
    name = "research"
    title = "Research"
    requires_env = ()                    # never fails to load
    optional_env = ("BRAVE_API_KEY", "PERPLEXITY_API_KEY")  # neither: research.report fails
    setup_note = ("no search provider configured yet — set BRAVE_API_KEY or "
                  "PERPLEXITY_API_KEY, or research.report will run and fail "
                  "cleanly, saying so")

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self):
        return ()

    def actions(self):
        return (
            Action(
                name="research.report",
                tier=Tier.AUTO,     # read-only: search, fetch, write a note
                summary="Research a question overnight: search, fetch "
                        "sources, and file a cited report",
                run=run_report, dry_run=report_dry, rollback=withdraw,
                params={"question": "the question to research"},
                max_per_hour=6,
            ),
        )

    async def signals(self):
        return await due_signals()

    def brief(self) -> str:
        """Reports finished since the last brief, not a static description —
        `take_finished` drains the buffer `run_report` fills on success."""
        lines = take_finished()
        return "; ".join(lines) if lines else ""

    async def agenda(self, within):
        return ()

    async def inbox(self, limit: int = 10):
        return ()


MODULE = ResearchModule()
