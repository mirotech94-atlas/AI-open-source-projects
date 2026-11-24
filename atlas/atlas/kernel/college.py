"""The college agent's hands: "school ..." commands from Slack or the menu.

the owner, 2026-10-13: "there should be an agent in ATLAS that works on college
work, an agentic model just for that." The agent is `modules/school/study.py`.
It is a study partner, never a ghostwriter, because the university's Academic
Integrity Policy forbids submitting work you did not write. This file is
only the part that may reach the executor. A module is a collector, and
`tests/test_school.py` pins that nothing in `modules/school` imports it. The
same split is used by `kernel/funnels.py` for Instagram.

`kernel.intent.act` asks every module's `command` hook after the channel has
proved it is the owner. The school module's hook lands here. The words are
parsed in code (`study.parse`) and never by a model:

  * "school plan [for <course>]" and "school answers" only read, so they are
    answered directly.
  * "school explain ... / quiz me ... / check my ..." run the Tier AUTO
    `school.study` through `executor.execute`, approved by the channel that
    proved it is him. That path keeps its dry run, its rollback, its
    ceiling and its audit row (invariant 1).
  * A request for the finished work ("do my CSC 326 Assignment 2", "write
    the full solution for Project 1") is answered with the boundary. If his
    words name a stored item, it runs `school.draft_work` for it, which
    writes a study guide and a scaffold and never the answer.

No new Tier 2 card is raised anywhere here, and nothing is submitted.
"""
from __future__ import annotations

import asyncio

from .executor import ExecutionRequest, execute
from .intent import Outcome


async def command(text: str, *, channel: str, who: str) -> Outcome | None:
    from ..modules.school import study
    from ..modules.school.work import normalise_code

    ask = study.parse(text)
    if ask is None:
        return None
    if ask.kind == "help":
        return Outcome(True, study.HELP)
    if ask.kind == "answers":
        return Outcome(True, await asyncio.to_thread(study.last_answers))
    if ask.kind == "plan":
        course = normalise_code(ask.params["course"]) if ask.params["course"] else ""
        return Outcome(True, await study.plan_text(course))
    if ask.kind == "refuse":
        course = normalise_code(ask.params["course"]) if ask.params["course"] else ""
        row = await study.item_like(study._solution_item(ask.said), course)
        if row is None:
            return Outcome(True, study.REFUSAL_ONLY, action="school.draft_work")
        result = await execute(ExecutionRequest(
            action="school.draft_work", params={"item_id": int(row["id"])},
            reason=(f"Asked over {channel}: “{text[:200]}” (a study guide and a "
                    "scaffold, never the finished work)"),
            approved_by=f"{channel}:{who}"))
        return Outcome(result.ok, f"{study.REFUSAL}\n\n{result.detail}",
                       action="school.draft_work", ran=result.ok)
    params = {k: v for k, v in ask.params.items() if v}
    result = await execute(ExecutionRequest(
        action="school.study", params=params,
        reason=f"Asked over {channel}: “{text[:200]}”",
        approved_by=f"{channel}:{who}"))
    return Outcome(result.ok, result.detail, action="school.study", ran=result.ok)
