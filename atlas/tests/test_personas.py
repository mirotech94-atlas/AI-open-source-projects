"""Every roster agent has a person-like persona, and the names never clash."""
from __future__ import annotations

from atlas.kernel import agents, personas


def _roster() -> dict[str, agents.Agent]:
    agents.build_roster()
    return agents.ROSTER


def test_every_roster_agent_has_a_written_persona():
    roster = _roster()
    missing = sorted(set(roster) - set(personas.PERSONAS))
    assert not missing, f"roster agents without a persona: {missing}"


def test_no_persona_is_left_for_an_agent_that_no_longer_exists():
    roster = _roster()
    stale = sorted(set(personas.PERSONAS) - set(roster))
    assert not stale, f"personas for agents not in the roster: {stale}"


def test_persona_names_are_unique_and_human():
    from atlas.api.dashboard import _DISPLAY_NAME

    names = [p.name for p in personas.PERSONAS.values()]
    assert len(names) == len(set(names)), names
    reserved = {n.lower() for n in _roster()} | {
        v.lower() for v in _DISPLAY_NAME.values()} | {"atlas"}
    for p in personas.PERSONAS.values():
        # A persona may carry its own agent's key (Miro 2026-11-10: the
        # ExampleTutor agent is called "TUTORCO"), never another agent's.
        assert p.name.lower() not in reserved - {p.agent}, p
        assert p.name.isalpha() and p.name[0].isupper(), p


def test_each_persona_has_a_one_line_specialty_and_a_known_voice():
    for p in personas.PERSONAS.values():
        assert p.specialty.strip() and "\n" not in p.specialty, p
        assert len(p.specialty) <= 90, p
        assert p.voice in {"female", "male"}, p


def test_no_persona_is_female():
    """Miro, 2026-10-11: "don't use the girl voice, only use the guy voice at
    all times." "female" stays in the closed set only for old browsers."""
    assert {p.voice for p in personas.PERSONAS.values()} == {"male"}
    assert personas.persona_for("newbie", "Newbie Desk", "x").voice == "male"
    assert "female" in personas.VOICES            # still a 200, spoken as ATLAS
    assert "Miro, 2026-10-11" in (personas.__doc__ or "")


def test_personas_are_a_fixed_table_not_a_draw():
    """Same agent, same persona, on every call and every process."""
    assert personas.persona_for("inbox") is personas.PERSONAS["inbox"]
    assert personas.PERSONAS["inbox"].name == "Clara"
    assert personas.PERSONAS["inbox"].voice == "male"


def test_an_agent_without_an_entry_still_gets_an_honest_derived_persona():
    p = personas.persona_for(
        "newbie", "Newbie Desk", "widgets, gadgets, gizmos, doohickeys. More text.")
    assert p.agent == "newbie"
    assert p.name == "Newbie Desk"
    assert p.specialty == "widgets, gadgets, gizmos"
    assert p.voice in {"female", "male"}
