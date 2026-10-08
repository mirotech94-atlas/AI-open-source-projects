"""Voice is exactly as powerful as the keyboard, and not one step more.

The temptation, every time somebody extends this, is to give the voice path
its own permission model. It must not have one. A transcript goes into the
chat composer's textarea and is sent like any typed sentence, so an
approval-tier repair comes back as a proposal whose single-use token only the
on-screen button can spend. The clamp is therefore not code -- it is the
ABSENCE of a confirm token on anything the voice path constructs.

That is harder to test than a feature, so it is tested twice, and the two
tests fail for different reasons.

The behavioural one is the real guard: an approval-tier tool invoked the way
a sent transcript invokes it comes back as a proposal and changes nothing.

The structural one is cheap and catches what the first cannot see: authority
appearing inside the voice router itself. Neither subsumes the other -- a
grep cannot see a future edit that routes transcripts through `run_turn`
directly, and the behavioural test cannot see a `confirm_token` appearing in
routers/voice.py.
"""
import pathlib

from guardian_hub import models as m
from guardian_hub.chat import registry
from guardian_hub.chat.registry import TOOLS, dispatch
from guardian_hub.db import SessionLocal

from tests.test_chat_tools import ctx_for
from tests.test_seed_helpers import seeded_demo

VOICE_ROUTER = (pathlib.Path(__file__).resolve().parents[1]
                / "guardian_hub" / "routers" / "voice.py")

#: Every name that would mean the voice path had grown an opinion about
#: authority: the approval token itself, the module that mints it, and the two
#: human-warrant fields whose whole point is that no automatic path writes them.
FORBIDDEN = ("confirm_token", "acttokens", "trusted", "label", "run_turn")


def test_an_approval_tier_request_still_comes_back_as_a_proposal(client):
    """The behavioural claim: a sentence cannot spend a token it does not hold."""
    # `client` only creates empty tables; every other test that touches
    # tenant/device rows through this fixture seeds the demo tenant first
    # (see tests/test_chat_bulk_trust.py, tests/test_chat_tools.py). Without
    # it `ctx_for()`'s tenant/user lookups have nothing to find.
    seeded_demo(client)
    db = SessionLocal()
    try:
        device = db.query(m.Device).filter(m.Device.trusted.is_(False)).first()
        assert device is not None, "fixture has no untrusted device to try this on"
        mac, was_trusted = device.mac, device.trusted
    finally:
        db.close()

    # `trust_device` (singular) is tier 2 and executes immediately -- that is
    # correct, it is the tier-2 "reversible, undoable" lane, not the approval
    # lane (see tests/test_chat_tools.py::test_tier2_executes_immediately_...).
    # The actual approval-tier trust tool is the bulk one: `trust_devices`
    # (plural) is tier 3, and dispatch() only builds a ChatProposal at tier 3
    # (guardian_hub/chat/registry.py: `elif tier == 3 or require_confirm`).
    # See tests/test_chat_bulk_trust.py::test_bulk_trust_always_proposes_before_it_writes,
    # the model this test follows.
    assert TOOLS["trust_devices"].tier >= 3, (
        "trust_devices dropped below the approval tier -- voice now executes it")

    out = dispatch(ctx_for(), "trust_devices", {"macs": [mac]})

    assert isinstance(out, registry.ChatProposal), (
        f"an approval-tier tool executed without a token: {out!r}")

    db = SessionLocal()
    try:
        after = db.query(m.Device).filter_by(mac=mac).first()
        assert after.trusted == was_trusted, "the proposal wrote the warrant anyway"
    finally:
        db.close()


def test_the_voice_router_holds_no_authority_of_its_own():
    """The structural claim, parsed the way test_remedy.py parses fix.py."""
    src = VOICE_ROUTER.read_text(encoding="utf-8")
    body = "\n".join(line for line in src.splitlines()
                     if not line.lstrip().startswith("#"))
    # The module docstring names these words to explain the rule; strip it so
    # the guard reads the code and not the prose about the code.
    if body.lstrip().startswith('"""'):
        body = body.split('"""', 2)[2]

    found = [name for name in FORBIDDEN if name in body]
    assert not found, (
        f"routers/voice.py now references {found}. Voice must carry no authority "
        "of its own: a transcript goes to the composer's textarea and is sent "
        "like any typed sentence. See the module docstring and "
        "docs/superpowers/specs/2026-09-22-voice-design.md.")
