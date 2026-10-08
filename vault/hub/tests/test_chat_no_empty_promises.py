"""Vault must never offer a confirmation it has not created.

Reported from the live app. Asked to "trust all devices", the model listed six
of them and ended "reply yes to trust every listed untrusted device". It had
called no tool, so no proposal and no token existed. The user replied "yes" and
got "I am not sure which thing you mean -- nothing is under discussion in this
conversation yet."

A confirmation is a stored single-use token that only a tool call mints
(`chat/registry._build_proposal`). Prose cannot mint one. So an answer that
offers a "yes" without a proposal behind it is not served, and nothing in the
rule-based answerer -- which calls no tools at all -- offers a bare "yes" any
more.
"""
import re

import pytest

from guardian_hub.chat import rules
from guardian_hub.routers.chat import _promises_a_confirmation_that_does_not_exist


class _Proposal:
    """Stands in for a real ChatProposal: only its presence matters here."""


@pytest.mark.parametrize("text", [
    "Sure. Next: reply *yes* and I will trust every listed device.",
    "I can do that. Say yes and I will go ahead.",
    "Next: say *yes* and I will show you the unverified ones.",
    "Reply yes to trust every listed untrusted device.",
    "answer yes and I will run it",
    "respond with yes and I will do it",
])
def test_an_offer_with_no_proposal_behind_it_is_caught(text):
    assert _promises_a_confirmation_that_does_not_exist(text, None) is True


@pytest.mark.parametrize("text", [
    "Sure, I will reboot safehome. Reversible. Reply *yes* and I will do it.",
])
def test_the_same_offer_is_fine_when_a_proposal_exists(text):
    # This is `_build_proposal`'s own summary -- the token is already stored.
    assert _promises_a_confirmation_that_does_not_exist(text, _Proposal()) is False


@pytest.mark.parametrize("text", [
    "Two devices are not verified yet. Next: ask me *which devices are unverified*.",
    "Yesterday's scan found nothing new.",          # the word, not an offer
    "The answer is yes, the certificate is valid.",  # ditto
    "",
])
def test_it_does_not_fire_on_an_answer_that_offers_nothing(text):
    assert _promises_a_confirmation_that_does_not_exist(text, None) is False


def test_the_rule_based_answerer_never_offers_a_bare_yes():
    # It calls no tools, so it can never have a proposal to back the offer.
    # Every offer it makes must name the thing to ask for instead.
    src = open(rules.__file__).read()
    # Strip docstrings first: this module's own comments discuss the bug by
    # name ('Vault itself said "say yes"'), which is not an offer.
    body = re.sub(r'"""1.*?"""1'.replace("1", ""), "", src, flags=re.S)
    offers = re.findall(r"(?:say|reply|answer)\s+\*?yes\*?", body, re.I)
    assert offers == [], f"rules.py still offers a bare yes: {offers}"
