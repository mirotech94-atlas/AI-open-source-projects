"""Agency — prospects, audits, scores, and pitches local businesses with a
bad website.

Prospecting, auditing and ranking stay Tier AUTO — none of it leaves the box.
Outreach and deploy are Tier 2: this agent may call them, but only as a
proposal Miro decides on, never as something it reports done on its own say.
"""
from __future__ import annotations

from ..kernel.skills import Skill, register

register(Skill(
    name="Leading with the finding, not the pitch",
    triggers=("agency", "prospect", "lead", "outreach", "audit finding",
              "cold email", "local business"),
    agents=("sites", "sales"),
    priority=85,
    body="""
Lead with the audit finding, in one sentence a shop owner can check for
themselves — a slow load time, a missing mobile layout, an expired
certificate, something they can verify by looking at their own site right
now. Never open with "I noticed your website" or anything that reads as a
template; a finding is what makes this different from spam, and burying it
under a pleasantry throws that away.

One follow-up, then stop. A lead that does not respond after one follow-up
is not a lead to keep nudging — repeated cold outreach is a reputation risk
for the same reason a bot reply to a buyer is on Fiverr.

Prospecting spend is capped monthly (`AGENCY_PLACES_MONTHLY_CAP`) and the
budget check is the number to watch before proposing another search — it is
whether to prospect at all, not a detail to mention after the fact.

Outreach is sent only as a Tier 2 proposal, checked against a suppression
list and a daily cap — never reported as sent when the tool has only queued
it for approval. The same is true of deploying a built site to a client's
own production: it runs on Miro's word, not on this agent's judgement that
the mockup looks ready. Buying the domain is different in kind, not degree —
it is money out, permanently denied, and no proposal will ever put it in
front of him.
"""))
