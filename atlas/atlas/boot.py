"""Boot sequence. Order matters."""
from __future__ import annotations

import importlib
import logging

from .kernel import agents, llm
from .kernel.registry import REGISTRY
from .kernel.tools import TOOLS

log = logging.getLogger("atlas.boot")

CHANNEL_MODULES = ("slack", "matrix", "twilio")


def load_channels() -> None:
    """Import every channel so `register()` fills the routing table.

    Split out of `boot()` because anything that asks "can ATLAS reach the owner"
    needs this to have happened first. `atlas.cli connect comms` did not call
    it, so the reachability check answered "no channels are registered at
    all" -- which reads as a broken reporting path when the real answer was
    that nothing had imported them yet. A diagnostic that is wrong about the
    thing being diagnosed is worse than no diagnostic.
    """
    for name in CHANNEL_MODULES:
        try:
            importlib.import_module(f"atlas.channels.{name}")
        except Exception as exc:                        # noqa: BLE001
            log.warning("channel %s unavailable: %s", name, exc)


async def boot() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s")
    # No secret in any log line, whichever module logged it: httpx writes
    # every request URL at INFO, tokens and all.
    from .modules._http import install_log_redaction
    install_log_redaction()

    llm.build_routes()
    llm.extend_routes_with_failover()
    # Ask ollama what it actually has, rather than trusting the name in
    # the env. A local model that is named but not pulled silently
    # routed PRIVATE work to the API.
    await llm.verify_local()
    # Every Groq chat model this key can see is one more daily allowance for
    # voice (kernel/llm.py); verify_cloud then proves each one alone.
    await llm.discover_groq_models()
    # And prove each optional (free-tier) provider actually serves. A key being
    # present is not the same as a provider working — four of them answered
    # 402/404/429 while sitting in the chain looking like failover.
    for name, verdict in (await llm.verify_cloud()).items():
        log.info("provider %s: %s", name, verdict)

    load_channels()

    REGISTRY.load_all()
    # Modules get their setup() before anything reads a check from them.
    await REGISTRY.start_modules()

    from .tools.builtin import register_all
    register_all()

    # Domain skill packs. Procedural knowledge only — they shape how the
    # reasoner decides, never what it may do. See atlas/skills/__init__.py.
    from .skills import load_all as load_skills
    load_skills()

    # The agents' own playbooks count as knowledge too (Task 60, the owner:
    # "there should also be some stuff on there based on his previous
    # skills from agents that he has as well.") -- idempotent, so this runs
    # at every boot rather than once.
    try:
        from .skills import index_into_library
        seeded = await index_into_library()
        if seeded:
            log.info("skill library: %d playbook(s) written or updated", seeded)
    except Exception as exc:                            # noqa: BLE001
        log.warning("skill library seed skipped: %s", exc)

    agents.build_roster()

    # The decision log predates nothing: proposals raised before it existed
    # are logged from what is still known about them, once, so the first
    # tournament has something to learn from. Nothing here can act.
    try:
        from .kernel.judgment.log import backfill
        await backfill()
    except Exception as exc:                            # noqa: BLE001
        log.warning("decision log backfill skipped: %s", exc)

    log.info("ATLAS up — %d modules, %d checks, %d actions, %d tools, %d agents",
             len(REGISTRY.modules), len(REGISTRY.checks),
             len(REGISTRY.actions), len(TOOLS), len(agents.ROSTER))
    for name, why in REGISTRY.disabled.items():
        log.warning("module %s DISABLED: %s", name, why)


async def shutdown() -> None:
    """Counterpart to boot(). Gives modules a chance to close their clients."""
    await REGISTRY.stop_modules()
