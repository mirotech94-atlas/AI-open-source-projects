"""A person-like persona for every roster agent: a first name, a one-line
specialty and a voice.

Presentation only. A persona changes nothing an agent can do: its tools,
tiers, memory scope and model are still `agents.Agent`'s own, and the router
still picks by `name` and `brief`. The Agency room (`api/agency.py`) and the
Agents map read these so ATLAS's team reads as people meeting with him, and
`/api/voice/speak` turns `voice` into one of three fixed Fish voices --
never a voice id taken from the browser.

Male voices only. the owner, 2026-10-11: "don't use the girl voice, only use the
guy voice at all times." Every persona speaks with "male"; "female" stays in
the closed set `VOICES` only so an older browser asking for it still gets a
200, and `/api/voice/speak` answers it in ATLAS's own (male) voice.

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


# Every voice is male (the owner, 2026-10-11; see above). Every name is unique, and none is an agent key, a
# map display name ("Vault", "College") or "Atlas" -- except its own agent's key: the
# mmt agent is called "MMT" (the owner, 2026-11-10).
PERSONAS: dict[str, Persona] = {p.agent: p for p in (
    # The eight (the owner, 2026-10-28). The retired agents' personas went with them.
    _p("inbox", "Clara", "Personal ops: mail, reply drafts, commitments, his calendar",
       "male"),
    _p("sites", "Felix", "Website clients: sites, demos, previews and owner emails",
       "male"),
    _p("content", "Mark", "ECC and Academy YouTube, the church calendar, Reddit drafts",
       "male"),
    _p("sales", "Sam", "Sales & Lead Gen: leads, outreach drafts, proposals",
       "male"),
    _p("bizops", "Bea", "Business automation: onboarding, test invoices, reminders",
       "male"),
    _p("seo", "Sage", "SEO & Growth: audits, ranks, blog drafts, the TutorSite page calendar",
       "male"),
    _p("research", "Rex", "Market Research: competitor watches, profiles, prices",
       "male"),
    _p("qa", "Quinn", "The QA gate: drafts, sites, passive security, missed runs",
       "male"),
    # Nine with mmt (the owner, 2026-11-10).
    _p("mmt", "MMT", "TutorSite: LinkedIn page posts, tutoring-centre prospects, intros",
       "male"),
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
