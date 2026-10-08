"""A person-like persona for every roster agent: a first name, a one-line
specialty and a voice.

Presentation only. A persona changes nothing an agent can do: its tools,
tiers, memory scope and model are still `agents.Agent`'s own, and the router
still picks by `name` and `brief`. The Agency room (`api/agency.py`) and the
Agents map read these so ATLAS's team reads as people meeting with him, and
`/api/voice/speak` turns `voice` into one of three fixed Fish voices --
never a voice id taken from the browser.

Deterministic: a fixed table, no randomness, no hashing. The specialty is
written from each agent's own `title` and `brief` in `agents.py` /
`businesses.py`; a roster agent added later without an entry here still gets
a persona derived from those two fields (`persona_for`), so the room never
breaks -- but `tests/test_personas.py` fails until it is given a real one.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

Voice = Literal["female", "male"]

# ATLAS's own voice name for `/api/voice/speak`: the default, and the one the
# chair of the meeting always speaks with.
ATLAS_VOICE = "atlas"
VOICES: frozenset[str] = frozenset({ATLAS_VOICE, "female", "male"})


@dataclass(frozen=True, slots=True)
class Persona:
    agent: str          # the roster key, unchanged
    name: str           # a human first name, never the agent key
    specialty: str      # one line, from the agent's title and brief
    voice: Voice


def _p(agent: str, name: str, specialty: str, voice: Voice) -> Persona:
    return Persona(agent=agent, name=name, specialty=specialty, voice=voice)


# Nine female, ten male. Every name is unique, and none is an agent key, a
# map display name ("Vault", "College") or "Atlas".
PERSONAS: dict[str, Persona] = {p.agent: p for p in (
    # the lead
    _p("concierge", "Ava", "General conversation and anything that has no "
       "obvious specialist", "female"),
    # role agents
    _p("ops", "Marcus", "Infrastructure, incidents, uptime and remediation", "male"),
    _p("inbox", "Clara", "Email triage, reply drafts, commitments and follow-ups",
       "female"),
    _p("growth", "Leo", "LinkedIn, Instagram, TikTok, SEO and content drafting",
       "male"),
    _p("reels", "Isla", "The AI-stories reels: series, daily cuts, captions and pace",
       "female"),
    _p("markets", "Ivan", "The AI stock basket and the paper basket: watch only",
       "male"),
    _p("marketing", "Nora", "Marketing experiments, the playbook and Miro's course",
       "female"),
    _p("commerce", "Owen", "Fiverr orders, eBay listings, pricing and buyers", "male"),
    _p("scheduler", "Grace", "Calendar, bookings, travel, deadlines and coursework",
       "female"),
    _p("analyst", "Theo", "Numbers, trends and correlation across the properties",
       "male"),
    _p("sentinel", "Victor", "Security posture, credentials, injection and exposure",
       "male"),
    # one per business
    _p("flycheap", "Sofia", "FlyCheap, the fare tracker", "female"),
    _p("guardian", "Elliot", "Vault, the network watchdog at my.guarder.io", "male"),
    _p("inroots", "Maya", "inRoots, attendance and standups at status.example.org",
       "female"),
    _p("vpsie", "Daniel", "VPSie, the account ATLAS itself runs in", "male"),
    _p("mirotech", "Ruby", "Mirotech, eBay reselling of IT and networking hardware",
       "female"),
    _p("fiverr", "Jasper", "Fiverr, the freelance gig business", "male"),
    _p("agency", "Hazel", "Agency, local businesses with a website worth fixing",
       "female"),
    _p("school", "Henry", "College at CUNY: due dates, grades and notices", "male"),
)}

_MAX_SPECIALTY = 90


def _derived(agent: str, title: str, brief: str) -> Persona:
    """The fallback for a roster agent with no entry above: its title as the
    name (the only human-ish label it has), its brief's first clause as the
    specialty, ATLAS's male counterpart as the voice. Honest rather than
    invented -- nothing here pretends to be a chosen persona."""
    first = brief.split(". ", 1)[0].split(", ")[:3]
    specialty = ", ".join(first).strip().rstrip(".") or title
    if len(specialty) > _MAX_SPECIALTY:
        specialty = specialty[: _MAX_SPECIALTY - 1].rstrip() + "…"
    return Persona(agent=agent, name=title or agent, specialty=specialty, voice="male")


def persona_for(agent: str, title: str = "", brief: str = "") -> Persona:
    """The persona for roster agent `agent`; derived from `title`/`brief`
    when the table has no entry for it (see the module docstring)."""
    return PERSONAS.get(agent) or _derived(agent, title, brief)


def as_dict(p: Persona) -> dict[str, Any]:
    return asdict(p)
