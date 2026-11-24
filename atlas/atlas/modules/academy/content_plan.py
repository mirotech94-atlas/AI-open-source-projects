"""The Academy's content plan (Content agent, six agents phase 8).

the owner, 2026-10-27: Academy topics are always AI-oriented -- learning AI in a
simple way, progressing with it, using it for the future. Every planned
topic carries one of those three tags, set in code from the series and the
episode's level, never by a model. `academy.plan_content` picks the next
episodes no one has posted or planned (held ones are left out: they need a
recording or the owner); `academy.draft_script` writes an outline draft through
the series rules in `tutorial_series_ai` to our own disk. Neither posts:
tutorials go public only through the existing render and autopublish gates.
"""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from ...kernel.contracts import Action, ActionResult, FailureClass, Tier
from ...kernel.state import execute_sql, fetch_all, fetch_one

AI_TAGS = ("learn", "progress", "future")
PER_WEEK_ENV = "ACADEMY_PLAN_PER_WEEK"
DRAFTS = Path("data/academy/drafts")


def _refused(why: str) -> ActionResult:
    return ActionResult(False, why, failure_class=FailureClass.VALIDATION)


def per_week() -> int:
    try:
        return max(1, min(14, int(os.getenv(PER_WEEK_ENV, "3"))))
    except ValueError:
        return 3


def ai_tag(series_key: str, level: int) -> str:
    """learn (Basics, Beginner) | progress (Intermediate, Advanced) | future
    (the opinion series about where AI is going)."""
    from . import tutorial_series_ai
    if series_key in tutorial_series_ai.OPINION_SERIES:
        return "future"
    return "learn" if int(level) <= 1 else "progress"


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


async def _taken() -> set[tuple[str, int]]:
    posted = await fetch_all(
        """SELECT series, episode FROM academy_tutorial_posts
            WHERE public AND NOT withdrawn""")
    planned = await fetch_all(
        "SELECT series, episode FROM academy_content_plan WHERE status <> 'withdrawn'")
    return {(str(r["series"]), int(r["episode"])) for r in [*posted, *planned]}


def candidates(taken: set[tuple[str, int]]) -> list[dict[str, Any]]:
    """Every episode not posted, not planned and not held, basics first."""
    from . import tutorial_series as ts
    out = []
    for order, s in enumerate(ts.SERIES):
        if s.key == "channel":
            continue
        for ep in s.episodes:
            if (s.key, ep.number) in taken or ts.held(s.key, ep):
                continue
            out.append({"series": s.key, "episode": ep.number, "title": ep.title,
                        "level": ep.level, "ai_tag": ai_tag(s.key, ep.level),
                        "_order": (ep.level, order, ep.number)})
    out.sort(key=lambda c: c["_order"])
    return [{k: v for k, v in c.items() if k != "_order"} for c in out]


async def _pick(weeks: int) -> tuple[list[dict[str, Any]], str | None]:
    if not 1 <= int(weeks) <= 4:
        return [], "weeks must be 1 to 4"
    taken = await _taken()
    picks = await asyncio.to_thread(candidates, taken)
    n = per_week()
    start = _monday(datetime.now(UTC).date()) + timedelta(weeks=1)
    out = []
    for i, c in enumerate(picks[: n * int(weeks)]):
        out.append({**c, "week_of": start + timedelta(weeks=i // n)})
    return out, None


async def plan_content_dry(weeks: int = 2) -> ActionResult:
    picks, why = await _pick(weeks)
    if why:
        return _refused(why)
    return ActionResult(True, f"would plan {len(picks)} AI topic(s)",
                        evidence={"topics": [{**p, "week_of": p["week_of"].isoformat()}
                                             for p in picks]})


async def plan_content(weeks: int = 2) -> ActionResult:
    picks, why = await _pick(weeks)
    if why:
        return _refused(why)
    ids: list[int] = []
    for p in picks:
        if p["ai_tag"] not in AI_TAGS:             # in code, never a model's word
            return _refused(f"{p['series']} #{p['episode']} has no AI tag")
        row = await fetch_one(
            """INSERT INTO academy_content_plan (week_of, series, episode, title, ai_tag)
               VALUES ($1,$2,$3,$4,$5)
               ON CONFLICT (series, episode) DO UPDATE
                 SET status='planned', week_of=EXCLUDED.week_of, at=now()
                 WHERE academy_content_plan.status = 'withdrawn'
               RETURNING id""",
            p["week_of"], p["series"], p["episode"], p["title"], p["ai_tag"])
        if row is not None:
            ids.append(int(row["id"]))
    return ActionResult(True, f"planned {len(ids)} AI topic(s)",
                        evidence={"plan_ids": ids,
                                  "topics": [{**p, "week_of": p["week_of"].isoformat()}
                                             for p in picks]},
                        rollback_token=",".join(map(str, ids)) or "none")


async def unplan(token: str) -> ActionResult:
    ids = [int(i) for i in str(token).split(",") if i.isdigit()]
    if ids:
        await execute_sql(
            """UPDATE academy_content_plan SET status='withdrawn'
                WHERE id = ANY($1::bigint[]) AND status='planned'""", ids)
    return ActionResult(True, f"{len(ids)} planned topic(s) withdrawn")


# ------------------------------------------------------------ the outline

def outline(series_key: str, number: int) -> tuple[str | None, str | None]:
    """The outline draft for one planned episode, or (None, why)."""
    from . import tutorial_series as ts
    from . import tutorial_series_ai as ai
    s = ts.series(series_key)
    ep = s.episode(int(number)) if s else None
    if ep is None:
        return None, f"{series_key} #{number} is not in the series plan"
    if (hold := ts.held(series_key, ep)) is not None:
        return None, hold
    lines = [f"# {ep.title}", "", f"Series: {s.mark} · level {ep.level}",
             f"AI tag: {ai_tag(series_key, ep.level)}", "",
             ("Draft outline (never posted from here: render and the autopublish "
              "gates decide)."), "", "## 0-3 s: the result or the question first",
             ts.result_of(ep),
             "", "## Summary", ep.summary, ""]
    if series_key in ai.OPINION_SERIES:
        lines += [ai.OPINION_LINE, ""]
    if series_key == "security":
        lines += ["## Series rules", *[f"- {r}" for r in ai.SECURITY_RULES], ""]
    if ep.ask:
        lines += ["## The question for the comments", ep.ask, ""]
    return "\n".join(lines), None


async def _plan_row(plan_id: int) -> dict[str, Any] | None:
    row = await fetch_one("SELECT * FROM academy_content_plan WHERE id=$1", int(plan_id))
    return dict(row) if row else None


async def draft_script_dry(plan_id: int) -> ActionResult:
    row = await _plan_row(plan_id)
    if row is None or row["status"] != "planned":
        return _refused(f"plan {plan_id} is not a planned topic")
    text, why = await asyncio.to_thread(outline, row["series"], row["episode"])
    if text is None:
        return _refused(str(why))
    return ActionResult(True, f"would write {DRAFTS / f'plan-{plan_id}.md'}",
                        evidence={"text": text[:1500]})


async def draft_script(plan_id: int) -> ActionResult:
    row = await _plan_row(plan_id)
    if row is None or row["status"] != "planned":
        return _refused(f"plan {plan_id} is not a planned topic")
    text, why = await asyncio.to_thread(outline, row["series"], row["episode"])
    if text is None:
        return _refused(str(why))
    path = DRAFTS / f"plan-{int(plan_id)}.md"

    def _write() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    await asyncio.to_thread(_write)
    await execute_sql(
        "UPDATE academy_content_plan SET status='scripted', script_path=$2 WHERE id=$1",
        int(plan_id), str(path))
    return ActionResult(True, f"outline for plan {plan_id} written to {path}",
                        evidence={"path": str(path)}, rollback_token=str(int(plan_id)))


async def undraft_script(token: str) -> ActionResult:
    if not str(token).isdigit():
        return _refused("not a plan id")
    path = DRAFTS / f"plan-{int(token)}.md"
    await asyncio.to_thread(path.unlink, missing_ok=True)
    await execute_sql(
        """UPDATE academy_content_plan SET status='planned', script_path=NULL
            WHERE id=$1 AND status='scripted'""", int(token))
    return ActionResult(True, f"outline for plan {token} taken back")


def actions() -> tuple[Action, ...]:
    return (
        Action("academy.plan_content", Tier.AUTO,
               "Plan the next weeks of AI-oriented Academy topics (our own rows)",
               run=plan_content, dry_run=plan_content_dry, rollback=unplan,
               params={"weeks": "1 to 4 (default 2)"}, max_per_hour=2),
        Action("academy.draft_script", Tier.AUTO,
               "Write an outline draft for one planned topic to our own disk; "
               "never posts",
               run=draft_script, dry_run=draft_script_dry, rollback=undraft_script,
               params={"plan_id": "the academy_content_plan id"}, max_per_hour=6),
    )
