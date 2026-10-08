"""module_status on a name that is not a module names the real ones, so a
model that guessed (a person's name, the 2026-09-28 commerce shift) corrects
itself; shift runs are found by prefix, so rewording the prompt never resets
every agent to "never run"."""
from __future__ import annotations

from atlas.kernel import agents, state
from atlas.tools import builtin


async def test_an_unknown_module_lists_the_real_ones(monkeypatch):
    async def fetch_all(sql, *args):
        if "DISTINCT p.name" in sql:
            return [{"name": "fiverr"}, {"name": "inroots"}]
        return []

    monkeypatch.setattr(state, "fetch_all", fetch_all)
    out = await builtin.module_status("Miro")
    assert "no checks recorded for module 'Miro'" in out["error"]
    assert "modules with checks: fiverr, inroots" in out["error"]


def test_the_shift_prompt_is_matched_by_its_prefix():
    assert agents.SHIFT_PROMPT.startswith(agents.SHIFT_PREFIX)
    assert agents.SHIFT_LIKE == "Your shift:%"
    assert "never a person's name" in agents.SHIFT_PROMPT


def test_a_read_result_carries_its_read_time():
    from atlas.kernel.tools import _stamped
    out = _stamped({"status": 200, "ok": True})
    assert out["status"] == 200 and out["read_at"].endswith("+00:00")
    kept = {"checks": [], "observed_at": "2026-09-28T01:00:00+00:00"}
    assert _stamped(kept) is kept
    assert _stamped("text") == "text"
