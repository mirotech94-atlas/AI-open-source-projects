"""The client-sites skill book (2026-10-27).

Two Tier ASK, card-only actions, both approved by a person every time:

* `agency.add_skill` stores a short piece of guidance text for the `sites`
  agent. It is text only: no markup, no templating, no link other than https,
  at most 1500 characters, never over a built-in skill's name. It is loaded
  under a fixed header and can add no tool, action or tier.
* `agency.request_template` asks the coding agent for a new site template.
  ATLAS writes no file: the template arrives through the repo and CD, built
  with frontend-design and GetLayers at design time.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from ...kernel import handoff, skills
from ...kernel.contracts import ActionResult, Tier
from . import _store

HEADER = skills.DYNAMIC_HEADER
MAX_BODY = 1500
MAX_TRIGGERS = 8
MAX_NOTES = 500
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9 ,'-]{2,59}$")
_TRIGGER = re.compile(r"^[a-z][a-z -]{2,29}$")
_FAMILY = re.compile(r"^[a-z][a-z0-9-]{2,29}$")
# Any "scheme:" directly followed by a non-space ("http://", "javascript:x",
# "data:text"). Only https is allowed.
_SCHEME = re.compile(r"\b([A-Za-z][A-Za-z0-9+.-]*):(?=\S)")


def _triggers(raw: str | Sequence[str]) -> list[str]:
    items = raw.split(",") if isinstance(raw, str) else list(raw)
    return [str(t).strip().lower() for t in items if str(t).strip()]


def skill_problem(name: str, triggers: str | Sequence[str], body: str) -> str | None:
    """Why this skill text cannot be stored, or None."""
    name = (name or "").strip()
    if not _NAME.match(name):
        return ("the name must be 3-60 letters, digits, spaces or , ' - "
                "and start with a letter")
    if name in skills.static_names():
        return f"'{name}' is a built-in skill and cannot be replaced"
    trig = _triggers(triggers or "")
    if not trig or len(trig) > MAX_TRIGGERS:
        return f"give 1-{MAX_TRIGGERS} triggers, comma separated"
    bad = [t for t in trig if not _TRIGGER.match(t)]
    if bad:
        return f"a trigger must be 3-30 lowercase letters, spaces or -: {bad[0]!r}"
    body = (body or "").strip()
    if not body:
        return "the body is empty"
    if len(body) > MAX_BODY:
        return f"the body is {len(body)} characters; the limit is {MAX_BODY}"
    if "<" in body:
        return "the body contains '<': skills are plain text, no markup"
    if "{{" in body or "{%" in body:
        return "the body contains templating ('{{' or '{%')"
    for match in _SCHEME.finditer(body):
        if match.group(1).lower() != "https":
            return f"only https links are allowed (found '{match.group(1)}:')"
    return None


def row_problem(row: Mapping[str, Any]) -> str | None:
    return skill_problem(str(row.get("name") or ""), row.get("triggers") or (),
                         str(row.get("body") or ""))


async def add_skill_dry(name: str = "", triggers: str = "", body: str = ""
                        ) -> ActionResult:
    problem = skill_problem(name, triggers, body)
    if problem:
        return ActionResult(False, f"refused: {problem}")
    return ActionResult(
        True, f"would store the skill '{name.strip()}' (triggers: "
              f"{', '.join(_triggers(triggers))}) for the sites agent, "
              f"{len(body.strip())} characters of text")


async def add_skill(name: str, triggers: str, body: str) -> ActionResult:
    problem = skill_problem(name, triggers, body)
    if problem:
        return ActionResult(False, f"refused: {problem}")
    name, trig, body = name.strip(), _triggers(triggers), body.strip()
    skill_id = await _store.activate_skill(name, trig, body)
    skills.load_dynamic([{"name": name, "triggers": trig, "body": body}],
                        problem=row_problem)
    return ActionResult(True, f"skill '{name}' is active for the sites agent",
                        evidence={"skill_id": skill_id}, rollback_token=name)


async def add_skill_rollback(token: str) -> ActionResult:
    retired = await _store.retire_skill(token)
    unloaded = skills.unload_dynamic(token)
    if not (retired or unloaded):
        return ActionResult(False, f"no active skill named {token!r}")
    return ActionResult(True, f"skill '{token}' retired and unloaded")


# ---------------------------------------------------------- template request

def _template_handoff(family: str, notes: str) -> handoff.Handoff:
    return handoff.delegate(
        what_changed=f"the owner approved a request for a new client-site template "
                     f"family '{family}'",
        evidence=notes or "(no notes)",
        source="agency.request_template card",
        target=f"atlas/modules/agency/templates/{family}/ in the Atlas repo",
        done_looks_like=(
            f"a template family '{family}' authored with frontend-design and "
            f"GetLayers at design time, copy as validated JSON (no runtime HTML), "
            f"tests green, shipped through the repo and CD (never copied to the "
            f"server by hand)"),
        tier=Tier.ASK,
        read_at=datetime.now(UTC),
        may_touch=(f"atlas/modules/agency/templates/{family}/",
                   "tests/ for the new template"),
        may_not_touch=("kernel/", "the Caddyfile", "DNS", "secrets"),
    )


def _family_problem(family: str, notes: str) -> str | None:
    if not _FAMILY.match(family or ""):
        return "the family must be 3-30 lowercase letters, digits or -"
    if len(notes or "") > MAX_NOTES:
        return f"the notes are over {MAX_NOTES} characters"
    return None


async def request_template_dry(family: str = "", notes: str = "") -> ActionResult:
    problem = _family_problem(family, notes)
    if problem:
        return ActionResult(False, f"refused: {problem}")
    return ActionResult(True, "would hand this to the coding agent:\n"
                        + _template_handoff(family, notes).render())


async def request_template(family: str, notes: str = "") -> ActionResult:
    """A handoff to the coding agent; never a file written here."""
    problem = _family_problem(family, notes)
    if problem:
        return ActionResult(False, f"refused: {problem}")
    doc = _template_handoff(family, notes)
    hid = await _delegate().draft(
        request=f"new client-site template: {family}", brief=doc.render(),
        target=doc.target, topic="decisions")
    queued = await _delegate().queue(str(hid))
    if not queued.ok:
        return queued
    return ActionResult(True, queued.detail, evidence={"handoff_id": hid},
                        rollback_token=str(hid))


async def request_template_rollback(token: str) -> ActionResult:
    return await _delegate().cancel(token)


def _delegate() -> Any:
    from .. import delegate
    return delegate
