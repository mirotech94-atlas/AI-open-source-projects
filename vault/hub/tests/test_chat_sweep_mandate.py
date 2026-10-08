"""The owner's standing instruction: "anytime i tell the bot in the chat to fix
something or to look over all the alerts and erros and fix what doe snot need
human interafaftion then he should do it no wquestions aske d".

`_mentions_target` makes a tier-2 write wait for a confirmation when the user's
message does not name what it targets. Under a sweep instruction, naming each
target is precisely what is being delegated, so every reversible repair stopped
for its own separate yes. `_sweep_mandate` is the carve-out -- and it is a
carve-out for BREADTH only: tier 3 is proposed by `registry.dispatch` before it
ever looks at `require_confirm`, so nothing here can reach it.
"""
import pytest

from guardian_hub.routers.chat import _sweep_mandate


@pytest.mark.parametrize("message", [
    "fix everything",
    "look over all the alerts and errors and fix what does not need human interaction",
    "go through the alerts and fix anything that doesn't need me",
    "resolve all the alerts",
    "fix whatever you can",
    "sort out everything that doesn't need a human",
    "clear all of these",
    "fix them all, no questions asked",
    "just fix it without asking",
    "handle the rest",
    "take care of everything",
    "deal with all of the open ones",
])
def test_a_sweep_instruction_is_recognised(message):
    assert _sweep_mandate(message) is True


@pytest.mark.parametrize("message", [
    "",
    "what is going on with my network",
    "trust the printer",
    "fix the printer",
    "resolve lan:primary:unverified",
    "is everything ok",
    "reboot safehome",
    "block that IP",
    "show me all the alerts",
    "what does this alert mean",
])
def test_an_ordinary_message_is_not_a_sweep(message):
    assert _sweep_mandate(message) is False


def test_a_sweep_never_reaches_a_tier_three_tool():
    """The guarantee that makes the carve-out safe, asserted on the code path
    itself rather than on prose: `dispatch` proposes on tier 3 regardless of
    what `require_confirm` says, so a sweep cannot run a reboot or a
    subnet-wide block without its own single-use confirmation."""
    import inspect
    from guardian_hub.chat import registry
    src = inspect.getsource(registry.dispatch)
    assert "elif tier == 3 or require_confirm:" in src, (
        "dispatch must still propose on tier 3 independently of require_confirm"
    )


def test_the_disruptive_actions_are_all_still_tier_three():
    """The other half of the guarantee: the escalation set itself. A sweep can
    only ever run what `effective_tier` leaves at tier 2, and that function
    fails closed -- an action it does not classify, anything in `TIER3_FIXES`,
    a host Vault has never successfully fixed, or anything touching the
    hub's own listening port all become tier 3 and so need their own yes."""
    from guardian_hub.chat.tiers import TIER3_FIXES

    for action in ("reboot", "block_subnet", "disable_ssh_password", "full_upgrade",
                   "ban_long", "limit_ssh", "security_headers"):
        assert action in TIER3_FIXES, f"{action} must stay behind a confirmation"


def test_the_sweep_mandate_is_read_only_from_the_users_own_message():
    """It must not be reachable from thread history or tool output: a device
    label or hostname that reads like "fix everything" is attacker-controlled
    text and grants nothing."""
    import ast
    import inspect
    from guardian_hub.routers import chat as chat_router

    # Read the WHOLE module, not one function. This used to inspect
    # `chat_turn`, and when the turn was split so the Slack bot could share it
    # the call moved into `run_turn` and the guard broke -- not because the
    # invariant broke, but because the guard was pinned to an address rather
    # than to the property it protects. A security check that a refactor can
    # silence is worse than no check, because its green is believed.
    #
    # The AST rather than a substring: `def _sweep_mandate(message...` also
    # contains the text "_sweep_mandate(", so string counting cannot tell a
    # definition from a call, and would miss `_sweep_mandate(x)` written with
    # a line break inside the parens.
    tree = ast.parse(inspect.getsource(chat_router))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name)
             and n.func.id == "_sweep_mandate"]

    # Exactly one place computes it, so there is no second, laxer source.
    assert len(calls) == 1, f"expected one _sweep_mandate call, found {len(calls)}"

    (arg,) = calls[0].args
    assert isinstance(arg, ast.Attribute) and arg.attr == "message" \
        and isinstance(arg.value, ast.Name) and arg.value.id == "body", (
        "the mandate must be computed from ChatRequest.message and nothing else"
    )
