"""Which doors hand their general-chat step to the assistant (plan 3.1).

`ASSISTANT_DOORS` is a comma list of `slack`, `whatsapp`, `dashboard`, read on
every call, on top of `ASSISTANT_ENABLED`. Empty (or the assistant off) means no
door: every door runs exactly the code it ran before. A name not in the list is
a door that stays as it was. An unknown name opens nothing.

`ASSISTANT_PROPOSE` (default off) lets a fixed phrase, parsed in code, hand
the owner's own earlier message to the action picker as a card; see
`assistant.propose`.
"""
from __future__ import annotations

import os

DOORS_ENV = "ASSISTANT_DOORS"
PROPOSE_ENV = "ASSISTANT_PROPOSE"
ENABLED_ENV = "ASSISTANT_ENABLED"
KNOWN = ("slack", "whatsapp", "dashboard")
_TRUE = ("1", "true", "yes", "on")


def _on(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUE


def listed() -> frozenset[str]:
    """The doors named in `ASSISTANT_DOORS` (known names only)."""
    raw = os.getenv(DOORS_ENV, "")
    return frozenset(d for d in (p.strip().lower() for p in raw.split(","))
                     if d in KNOWN)


def door_enabled(name: str) -> bool:
    """Does this door's general-chat step go to `assistant.converse`? Needs the
    assistant on AND the door listed."""
    return _on(ENABLED_ENV) and name in listed()


def propose_enabled() -> bool:
    """`ASSISTANT_PROPOSE`: default off."""
    return _on(ENABLED_ENV) and _on(PROPOSE_ENV)
