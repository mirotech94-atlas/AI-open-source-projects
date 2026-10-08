"""Write tools go to the model only when the message asks for something done.

Every turn used to ship every tool the role allowed -- ~1,050 tokens of
trust / quarantine / reboot / set-policy definitions attached to "what is my
score?" -- against a provider budget of 8,000 tokens a minute. Two turns
four seconds apart were 10,600 and no retry can make that fit; each turn
has to be cheaper, and a question does not need the write tools.
"""
import json

from guardian_hub.chat import providers
from guardian_hub.chat.registry import TOOLS
from guardian_hub.routers.chat import _tools_for_turn


def _names(tools):
    return {t.name for t in tools}


def test_a_question_gets_the_read_tools_only():
    names = _names(_tools_for_turn("admin", "what is my security score?"))
    assert names and all(TOOLS[n].tier == 1 for n in names)
    assert "trust_devices" not in names and "apply_fix" not in names


def test_an_instruction_gets_the_write_tools_too():
    for msg in ("trust all devices", "fix everything", "reboot safehome", "yes",
                "please quarantine that camera", "can you snooze it"):
        assert "trust_devices" in _names(_tools_for_turn("admin", msg)), msg


def test_role_gating_still_wins():
    # A viewer asking for an action still never sees a write tool.
    names = _names(_tools_for_turn("viewer", "trust all devices"))
    assert names and all(TOOLS[n].min_role == "viewer" for n in names)


def test_a_question_turn_is_materially_cheaper():
    full = json.dumps(providers._tool_specs_openai(_tools_for_turn("admin", "trust it")))
    read = json.dumps(providers._tool_specs_openai(_tools_for_turn("admin", "how is the wifi?")))
    assert len(read) < len(full) * 0.5, (len(read), len(full))


def test_an_empty_message_is_treated_as_a_question():
    assert all(t.tier == 1 for t in _tools_for_turn("admin", ""))
