"""Marketing — how to run an experiment, and how not to fake one.

Two skills, both narrow. The triggers are deliberately distinctive
(`experiment`, `playbook`, `monetize`, `partner program`) rather than generic
marketing words, so they do not crowd out the kernel's accuracy skills for
every task that mentions a post.
"""
from __future__ import annotations

from ..kernel.skills import Skill, register

register(Skill(
    name="Running a marketing experiment",
    triggers=("experiment", "playbook", "variant", "hypothesis", "a/b",
              "48h", "baseline post", "what worked"),
    agents=("content", "research"),
    priority=86,
    body="""
An experiment is one hypothesis, one variant, one metric, one deadline. If you
cannot say what number would make it false at 48 hours, it is not an
experiment — it is a post, and posts do not go in the ledger.

Read the playbook before proposing. It holds what previous experiments
measured, best first. A variant recorded as lost does not get proposed again;
a variant recorded as won gets repeated at most once per platform, because
repeating a winner everywhere is how you stop learning anything.

Every claim of "better" is against the trailing 4-week median for that
platform and that metric. Raw counts are not evidence: a post with more views
than the last one on a channel that is growing is not a result.

Three honest outcomes and one dishonest one:

- won / lost / flat: the platform's number was read and compared.
- unmeasured, with the reason: the platform could not be read, or there is too
  little history for a baseline. Say which.
- "about the same" when nothing was measured. This is the one that is never
  acceptable. It reads as a result and is not one. If the read failed, the
  answer is that the read failed.

Publishing is never yours. The draft is written, the card goes to the owner, and
nothing reaches a feed until he answers it. That is true however good the
hypothesis is and however strong the approval record looks. If the approval
record is strong enough that the judgment layer drafted a standing rule, show
him the text — it is his to enact, and the gate stays until he does.
"""))

register(Skill(
    name="Finding money without spending any",
    triggers=("monetize", "monetization", "monetisation", "affiliate",
              "sponsorship", "partner program", "partner programme",
              "revenue idea"),
    agents=("content", "research", "sales"),
    priority=85,
    body="""
An idea rests on measured facts or it does not get written down. Each fact
carries the time it was read. If every source is unreadable, the answer is
zero ideas and a note naming what could not be read — that note is more useful
than three plausible ideas, because it tells him what to fix.

Never produce a revenue figure. Not a range, not an order of magnitude, not
"could be worth". Nothing in this system measures conversion, close rate or
willingness to pay, so any number of that shape is invented. Describe the
mechanism and what would have to be true instead.

Know what is actually measured before leaning on it:

- ExampleCo's price check returns eBay active asks. Sold prices are the market
  and they are not measured; quote the caveat or quote nothing.
- YouTube partner eligibility here compares subscribers only. The watch-hours
  and Shorts-views paths are measured channel-wide by academy.ypp_progress
  (Analytics) in the Monday digest; quote those figures with their read time,
  never claim eligibility from them — Studio → Earn is the authority.

Rank by evidence strength times how little it asks of him. A toggle in a
console he already owns beats a contract he would have to negotiate.

Anything that costs money to try is listed, with its mechanism, as something
only he can decide. Money out is Tier 3, permanently. There is no runtime
override, none may be added, and a promising result does not create one.
"""))
