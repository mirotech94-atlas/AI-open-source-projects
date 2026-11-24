"""Fiverr — Playwright against a logged-in session. Blocked by PerimeterX.

The session wall is a finding about Fiverr, not a finding about the inbox —
the difference this skill exists to keep an agent from collapsing.
"""
from __future__ import annotations

from ..kernel.skills import Skill, register

register(Skill(
    name="The Fiverr wall and the state file",
    triggers=("fiverr", "perimeterx", "gig", "buyer message", "seller page"),
    agents=("sales",),
    priority=85,
    body="""
Fiverr fronts every seller page with PerimeterX, which fingerprints and
blocks the automated browser itself — not the IP, not the account, not the
selector. That holds from the server and from Miro's own Mac on his own live
session alike, so there is no selector fix waiting to be found. A session
check reporting UNKNOWN is the honest reading; reading its zeros as "no
unread messages" would be reporting a wall as a clear inbox, which is exactly
the UNKNOWN-collapsed-into-OK failure this system exists to refuse.

The state file — not a live scrape — is the source once the wall is up.
Fiverr's own notification email is the other honest channel, since Fiverr
mails every message and order; treat it as the signal, not the automated
inbox count.

Response speed is Fiverr's ranking signal, so an unanswered buyer message is
urgent regardless of how trivial the content looks. Draft the reply; never
send it — a bot reply risks both platform policy and the reputation the
gig's ranking depends on. Human-paced only: a few checks a day, never a
scraping cadence, whatever channel the signal comes through.
"""))
