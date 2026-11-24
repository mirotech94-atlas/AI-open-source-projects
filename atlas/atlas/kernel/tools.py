"""The tool layer: what an agent can actually do in the world.

Distinction that keeps this safe:

  * A READ tool runs immediately. It cannot change anything.
  * A WRITE tool is a thin wrapper over an Action in the catalog. It inherits
    that action's tier, so an agent asking to post to Instagram produces an
    approval card, not a post.

An agent is handed a scoped subset of tools. The Inbox agent cannot touch
infrastructure; the Ops agent cannot post to social. Scoping is per-agent and
enforced here, not in the prompt — a prompt is a request, not a boundary.
"""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from .contracts import Tier
from .executor import ExecutionRequest, execute
from .registry import REGISTRY

log = logging.getLogger("atlas.tools")


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    # Exactly one of these:
    read: Callable[..., Awaitable[Any]] | None = None
    action: str | None = None          # name in the action catalog

    def as_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.schema,
        }


TOOLS: dict[str, Tool] = {}

# The agent a read tool is running for, set by `invoke` and nowhere else. A
# read that records who asked (the browser's browser_sessions.agent) reads it
# here: taken from the call, not from a parameter the model could fill in.
CURRENT_AGENT: ContextVar[str | None] = ContextVar("atlas_tool_agent", default=None)


def register(tool: Tool) -> None:
    if tool.read is None and tool.action is None:
        raise ValueError(f"{tool.name}: needs either read or action")
    if tool.read is not None and tool.action is not None:
        raise ValueError(f"{tool.name}: cannot be both read and write")
    TOOLS[tool.name] = tool


@dataclass(slots=True)
class ToolResult:
    ok: bool
    content: Any
    needs_approval: bool = False
    proposal_id: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


_TIME_KEYS = ("read_at", "observed_at", "at", "as_of")

# The JSON-schema types a tool schema may name, and the Python values each
# accepts. `bool` is a subclass of `int`, and is excluded from the numbers on
# purpose: a model that sends `true` for a count has not sent a count.
_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


def validate_params(tool: Tool, params: Any) -> str | None:
    """Why `params` do not fit `tool.schema`, or None when they do.

    A closed catalog with open arguments is not closed (invariant 2): the
    name is checked against the registry, and this checks what goes with
    it -- an object, every required key present, no key the schema does not
    declare, and each value of the declared type. Pure; it reads nothing.
    """
    if not isinstance(params, dict):
        return f"{tool.name}: parameters must be a JSON object"
    props = tool.schema.get("properties") or {}
    required = tool.schema.get("required") or []
    missing = [k for k in required if k not in params]
    if missing:
        return f"{tool.name}: missing required {', '.join(sorted(missing))}"
    unknown = [k for k in params if k not in props]
    if unknown:
        return f"{tool.name}: undeclared parameter(s) {', '.join(sorted(unknown))}"
    for key, value in params.items():
        wanted = (props.get(key) or {}).get("type")
        accepted = _TYPES.get(str(wanted)) if wanted else None
        if accepted is None:
            continue
        ok = isinstance(value, accepted) and not (
            isinstance(value, bool) and wanted in ("integer", "number"))
        if not ok:
            return f"{tool.name}: {key} must be a {wanted}"
    return None


def _stamped(content: Any) -> Any:
    """A read's dict result carries the moment it was read (invariant 6). A
    model given a bare figure invents a time: 2026-09-28, the attendance agent
    on Qwen said "200 at 2024-04-27T12:00:00Z" from an unstamped http_probe.
    A result that already names its time is left alone."""
    if isinstance(content, dict) and not any(k in content for k in _TIME_KEYS):
        from datetime import UTC, datetime
        return {**content, "read_at": datetime.now(UTC).isoformat(timespec="seconds")}
    return content


def _announce(agent: str, name: str, phase: str) -> None:
    """Tell the portal HUD a read tool is in use: the agent, the tool and the
    phase, never the parameters or the result. Fire-and-forget; it can never
    fail a tool call."""
    try:
        from . import hud
        from .events import publish
        hud.note_tool(agent, name, phase)
        publish("tool", agent=agent, tool=name, phase=phase)
    except Exception:
        log.debug("tool nudge failed", exc_info=True)


async def invoke(
    name: str, params: dict[str, Any], *, agent: str, allowed: set[str]
) -> ToolResult:
    """Single entry point. Every agent tool call comes through here."""
    if name not in allowed:
        log.warning("agent %s attempted out-of-scope tool %s", agent, name)
        return ToolResult(False, f"{name} is not in {agent}'s scope")

    tool = TOOLS.get(name)
    if tool is None:
        return ToolResult(False, f"no such tool: {name}")

    if tool.read is not None:
        token = CURRENT_AGENT.set(agent)
        _announce(agent, name, "start")
        try:
            return ToolResult(True, _stamped(await tool.read(**params)))
        except Exception as exc:
            log.exception("read tool %s failed", name)
            # Never the raw exception: an httpx error carries its URL, and
            # with it a query-string token or a page-chosen address.
            from ..modules._http import explain
            return ToolResult(False, f"{name} failed: {explain(exc)}")
        finally:
            CURRENT_AGENT.reset(token)
            _announce(agent, name, "end")

    decision = REGISTRY.decisions.get(tool.action or "")
    if decision is None:
        return ToolResult(False, f"{tool.action} is not in the catalog")

    if decision.tier is Tier.NEVER:
        return ToolResult(False, f"{tool.action} is permanently denied")

    if decision.tier is Tier.ASK:
        from .approvals import propose
        # One pending proposal per (action, agent) at a time. This is the
        # call site behind 69 identical gitlab.open_issue cards in six
        # hours: the reasoner re-notices the same finding every cycle, the
        # issue title differs slightly each time, and nothing else about
        # the request is stable enough to key on. While one card is
        # unanswered, asking again is noise; when it expires, the next
        # cycle may ask once more.
        try:
            pid = await propose(
                action=tool.action, params=params,
                reason=f"requested by the {agent} agent",
                dedup_key=f"{tool.action}@agent:{agent}",
                automatic=True,
            )
        except ValueError as exc:
            # `propose()` refuses by raising. That used to be unreachable
            # from here — the catalog and the tier are both checked above —
            # but the never-repeat guard makes it a runtime condition: this
            # action with these parameters has already failed or been
            # declined twice. The agent is TOLD, in the refusal's own words,
            # because those words say how to unlock it; killing the run
            # would teach it nothing.
            log.info("not proposing %s for %s: %s", tool.action, agent, exc)
            return ToolResult(False, str(exc))
        return ToolResult(
            True,
            f"Queued for your approval (#{pid}). Nothing has happened yet.",
            needs_approval=True, proposal_id=pid,
        )

    result = await execute(ExecutionRequest(
        action=tool.action, params=params,
        reason=f"{agent} agent, tier 1",
    ))
    return ToolResult(result.ok, result.detail, meta=result.evidence)
