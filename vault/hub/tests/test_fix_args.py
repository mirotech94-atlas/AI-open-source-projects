"""One definition of a fix's arguments, and no way to call it wrong.

There were two `fix_args` in the hub, with the first two parameters
transposed: `remedy.fix_args(action, host, arg)` and
`routers/write.fix_args(host, action, arg)`. They returned the same dict
shape, they described themselves as the canonical definition in their own
docstrings, and they lived in modules that import each other -- so a call
written in one file with the other's order type-checked, ran, and returned a
plausible dict with `host` and `action` exchanged.

Nothing raises on that. The dict flows into `acttokens.mint_chat` /
`consume_chat`, where a confirmation is bound to those exact args, into
`chat.tiers.effective_tier`, and into the body the sensor executes -- so a
transposition surfaces as a token that will not match or a tier computed for
the wrong thing, far from the call that caused it. It nearly landed once, in
`routers/write.queue_fix`, which needed `remedy`'s order while sitting in the
file that defined the other one.

The fix is one keyword-only function in `chat/tiers.py`. This test is what
keeps it that way: a second definition, or a slide back to positional
parameters, fails here rather than in a customer's confirmation.
"""
import inspect
import pathlib
import re

import pytest

from guardian_hub import acttokens, remedy
from guardian_hub.chat import tiers
from guardian_hub.payloads import inbox
from guardian_hub.routers import chat as chat_routes, write as write_routes

HUB = pathlib.Path(__file__).resolve().parents[1] / "guardian_hub"

_DEF = re.compile(r"^def fix_args\(", re.M)


def test_the_hub_has_exactly_one_fix_args():
    found = [p.relative_to(HUB).as_posix() for p in HUB.rglob("*.py")
             if _DEF.search(p.read_text())]
    assert found == ["chat/tiers.py"], (
        "fix_args must be defined once. A second copy with its own parameter "
        f"order is the bug this test exists for; found: {found}")


def test_every_module_that_builds_fix_args_shares_the_one_function():
    # Imported names, not re-implementations: `remedy.fix_args` has to BE
    # `tiers.fix_args`, or the grep above passes while two behaviours exist.
    for mod in (remedy, write_routes, chat_routes, inbox):
        assert getattr(mod, "fix_args") is tiers.fix_args, mod.__name__


def test_the_arguments_are_keyword_only():
    params = inspect.signature(tiers.fix_args).parameters
    assert set(params) == {"action", "host", "arg"}
    for name, param in params.items():
        assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
            f"{name} is positional again -- transposing it with its neighbour "
            f"would be silent once more")


def test_a_positional_call_raises_instead_of_transposing():
    with pytest.raises(TypeError):
        tiers.fix_args("reboot", "prod")  # the old remedy order
    with pytest.raises(TypeError):
        tiers.fix_args("prod", "reboot", None)  # the old write order


def test_the_two_lanes_bind_the_same_repair_identically():
    # The read lane carries "no argument" as `""` (`AlertRemedy.arg`), the
    # write lane as `None` (`FixIn.arg`). A token minted on one and burnt
    # through the other must still match, so neither spelling reaches the
    # dict: an argument-less fix carries no `arg` key at all.
    read_lane = tiers.fix_args(action="reboot", host="prod", arg="")
    write_lane = tiers.fix_args(action="reboot", host="prod", arg=None)
    assert read_lane == write_lane == {"host": "prod", "action": "reboot"}
    assert acttokens.canon_args(read_lane) == acttokens.canon_args(write_lane)


def test_an_argument_still_distinguishes_one_repair_from_another():
    # The normalisation above must not blur a real argument: a confirmation
    # for one IP may never be spendable on another.
    one = tiers.fix_args(action="block_ip", host="prod", arg="203.0.113.9")
    other = tiers.fix_args(action="block_ip", host="prod", arg="198.51.100.4")
    assert acttokens.canon_args(one) != acttokens.canon_args(other)
    assert acttokens.canon_args(one) != acttokens.canon_args(
        tiers.fix_args(action="block_ip", host="prod", arg=None))
    assert "arg" not in tiers.fix_args(action="block_ip", host="prod", arg=None)
