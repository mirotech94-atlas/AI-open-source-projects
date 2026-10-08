"""Mirotech — eBay reselling of IT and networking hardware.

The one judgement call worth repeating every time a number comes up: sold
prices are the market, asking prices are fiction.
"""
from __future__ import annotations

from ..kernel.skills import Skill, register

register(Skill(
    name="Pricing against sold, not asked",
    triggers=("mirotech", "ebay", "listing", "reprice", "comparable",
              "sold price", "asking price"),
    agents=("mirotech",),
    priority=85,
    body="""
Compare against completed sale prices, never against active asking prices.
Asking prices on used enterprise networking gear are fiction — sellers list
high and wait; sold prices are what the market actually paid. A price-check
result built from active listings is an asks-only reading and has to say so
before Miro reads it as a comparable.

A listing sitting for more than 60 days with few watchers is usually a
pricing problem, not a demand problem — that is the signal worth surfacing,
not the raw days-listed number on its own.

Orders awaiting fulfilment are the more urgent number of the two: late
dispatch hits seller metrics harder than a slow-moving listing does.
"""))
