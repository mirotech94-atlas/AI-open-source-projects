"""When no model answers, say so once, with why.

On 2026-10-05 every cloud provider failed at once (one out of credit, two
capped, one rate-limited). Each agent that needed a model failed on its own
and said so in its own corner, or fell back silently; nothing told Miro the
one fact that explained all of it. Miro's operations brief asks for a single
"chain open" incident instead of per-agent chatter.

`think()` calls `report()` when a whole chain is exhausted. It posts one
HIGH message to the comms topic, at most once an hour, naming each open
deployment and the reason in plain words, and what to do when the reason is
money. When a model next answers, `cleared()` posts one line saying so.
Nothing here retries, switches provider, or spends: topping up a balance is
Miro's, and ATLAS only reports it.

In memory, like the breakers it reads. Posting is fire-and-forget: a failed
Slack post must never turn a model outage into a second failure.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from . import breaker

log = logging.getLogger("atlas.outage")

GAP_S = 3600.0
# A chain must stay exhausted this long before it is called an outage. The
# free bench (2026-10-05) is exhausted and answering again within the same
# minute, dozens of times a day: 79 "No model is answering" / "answering
# again" pairs in #atlas-infra in a week, 66 of them in one day. A blip is
# skipped work that the next cycle picks up; only a hold is news.
PERSIST_S = 300.0
TITLE = "No model is answering"
CLEARED = "Models are answering again"

_last_report = 0.0
_active = False
_down_since = 0.0        # when the current exhaustion began; 0 when none
_reported = False        # the current exhaustion was posted
_clock: Callable[[], float] = time.monotonic
_tasks: set[asyncio.Task] = set()


def reset(clock: Callable[[], float] | None = None) -> None:
    global _last_report, _active, _clock, _down_since, _reported
    _last_report, _active, _down_since, _reported = 0.0, False, 0.0, False
    _clock = clock or time.monotonic


def active() -> bool:
    return _active


def _why(why: str) -> str:
    if why in ("HTTP 402",):
        return "out of credit (HTTP 402)"
    if why in ("HTTP 401", "HTTP 403"):
        return f"key refused ({why})"
    return why or "failing"


def describe(snapshot: dict[str, dict[str, object]], last: BaseException | None) -> str:
    """One line per open deployment, then the last error when nothing is
    open yet (the first failures have not tripped a breaker)."""
    lines = [f"• {name}: {_why(str(info.get('why') or ''))}, "
             f"skipped for {int(info.get('seconds_left') or 0)} s"
             for name, info in sorted(snapshot.items())]
    if not lines and last is not None:
        lines.append(f"• last error: {type(last).__name__}: {str(last)[:160] or 'no detail'}")
    money = any("402" in str(info.get("why")) for info in snapshot.values())
    tail = ("\n\nA 402 is a spent balance: topping it up is yours to do; I can't and won't."
            if money else "")
    return ("\n".join(lines) or "• no detail available") + tail


def _post(title: str, body: str) -> None:
    async def send() -> None:
        try:
            from .comms import Outbound, notify
            from .contracts import Severity
            await notify(Outbound(title=title, body=body, severity=Severity.HIGH,
                                  topic="comms", incident_key="llm.routes"))
        except Exception as exc:                        # noqa: BLE001
            log.warning("could not post the model-outage note: %s", type(exc).__name__)
    try:
        task = asyncio.get_running_loop().create_task(send())
    except RuntimeError:
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def report(need: str, last: BaseException | None) -> bool:
    """A chain was exhausted. Posts once the exhaustion has held for
    `PERSIST_S`, then at most once per `GAP_S`. True when a message was
    posted."""
    global _last_report, _active, _down_since, _reported
    now = _clock()
    if not _active:
        _active, _down_since, _reported = True, now, False
    if now - _down_since < PERSIST_S:
        return False                        # a blip, so far
    if _last_report and now - _last_report < GAP_S:
        return False
    _last_report, _reported = now, True
    body = (f"Every model on the {need} route failed just now, so anything that needs "
            f"one is being skipped, not guessed.\n\n{describe(breaker.snapshot(), last)}\n\n"
            f"Checks, cards already raised, the keypad on a call and the store do not "
            f"need a model and keep working.")
    log.error("no model answered for %s", need)
    _post(TITLE, body)
    return True


def cleared(provider: str, model: str) -> bool:
    """A model answered after an outage: one line, once -- and only when
    the outage was posted. A blip that was never announced is not cleared
    out loud either."""
    global _active, _down_since, _reported
    if not _active:
        return False
    _active, _down_since = False, 0.0
    if not _reported:
        return False                        # never said it was down
    _reported = False
    _post(CLEARED, f"{provider}:{model} answered. Anything skipped while no model "
                   f"answered was not run; it will be picked up on its next cycle.")
    return True
