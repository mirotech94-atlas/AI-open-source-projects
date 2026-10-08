"""The growth agent's tools are real tools.

It was scoped to five names nothing registered -- `linkedin_read`,
`linkedin_post`, `instagram_read`, `tiktok_read`, `search_console` -- so its
first call to any of them came back "no such tool". What the social modules
actually offer is their checks' latest observations, which `module_status`
and `module_brief` read with the time each was measured.
"""
from __future__ import annotations

from atlas.kernel.agents import ROSTER, build_roster
from atlas.kernel.tools import TOOLS
from atlas.tools.builtin import register_all


def test_the_growth_agent_names_only_registered_tools():
    register_all()
    build_roster()
    missing = ROSTER["growth"].tools - set(TOOLS)
    assert not missing, f"growth names unregistered tool(s): {sorted(missing)}"


def test_growth_reads_the_social_modules_through_module_status():
    """LinkedIn, Instagram and TikTok expose checks, not read functions; the
    latest observation of each, with its `observed_at`, is what there is to
    read, and `module_status` / `module_brief` already read exactly that."""
    register_all()
    build_roster()
    tools = ROSTER["growth"].tools
    assert {"module_status", "module_brief"} <= tools
    # LinkedIn has no actions by design: every write goes through a browser
    # a human signed into. A LinkedIn post tool would be a path around it.
    assert "linkedin_post" not in tools
