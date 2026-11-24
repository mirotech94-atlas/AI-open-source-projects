"""Automation ideas from repeated work (six agents phase 9).

`find_repeats` (Tier 1, daily) groups the last 14 days of sent-mail subjects
and board task titles by a code-made pattern (the first content words,
numbers and Re:/Fwd: dropped). A pattern seen 3+ times becomes an idea and
one `bizops.propose_automation` card. The samples are untrusted text: they
are stored wrapped and never reach a prompt as instructions. Approving the
card writes one `build` task, owner `you` -- nothing is automated by itself."""
from __future__ import annotations

import re

from ...kernel.contracts import ActionResult, FailureClass
from ...kernel.permissions import wrap_untrusted
from . import store

MIN_MATCHES = 3
WINDOW_DAYS = 14
MAX_SAMPLES = 5
ACTION = "bizops.propose_automation"
_PREFIX = re.compile(r"^\s*(?:(?:re|fwd?|aw)\s*:\s*)+", re.IGNORECASE)
_WORD = re.compile(r"[a-z]+")
_STOP = frozenset({"the", "a", "an", "for", "to", "of", "and", "on", "in", "your",
                   "our", "my", "with", "is", "at", "from", "by"})


def _refused(why: str) -> ActionResult:
    return ActionResult(False, why, failure_class=FailureClass.VALIDATION)


def pattern(text: str) -> str | None:
    """The first four content words, or None for too little to group on."""
    words = [w for w in _WORD.findall(_PREFIX.sub("", (text or "").lower()))
             if w not in _STOP][:4]
    return " ".join(words) if len(words) >= 2 else None


def group(texts: list[tuple[str, str]]) -> dict[str, list[str]]:
    """pattern -> wrapped samples, for patterns seen MIN_MATCHES+ times."""
    found: dict[str, list[str]] = {}
    for source, text in texts:
        key = pattern(text)
        if key is not None:
            found.setdefault(key, []).append(wrap_untrusted(text[:200], source))
    return {k: v for k, v in found.items() if len(v) >= MIN_MATCHES}


async def find_repeats_dry() -> ActionResult:
    return ActionResult(True, f"would group {WINDOW_DAYS} days of mail subjects and "
                              f"board tasks; {MIN_MATCHES}+ matches make an idea")


async def find_repeats() -> ActionResult:
    groups = group(await store.recent_texts(WINDOW_DAYS))
    new_ids: list[int] = []
    for key, samples in sorted(groups.items()):
        iid, created = await store.upsert_idea(key, samples[:MAX_SAMPLES], len(samples))
        if not created:
            continue
        new_ids.append(iid)
        await store.raise_card(
            ACTION, {"idea_id": iid},
            f"Seen {len(samples)} times in {WINDOW_DAYS} days: \"{key}\". Approving "
            "adds a build task for you to automate it; nothing runs by itself.",
            "Mapfront", f"Pattern: {key}\nMatches: {len(samples)}",
            f"{ACTION}:{iid}")
    return ActionResult(True, f"{len(groups)} repeated pattern(s), {len(new_ids)} new "
                              "idea(s)", evidence={"new_ideas": new_ids},
                        rollback_token=",".join(map(str, new_ids)) or "none")


async def dismiss_ideas(token: str) -> ActionResult:
    if token == "none":
        return ActionResult(True, "no idea was created")
    ids = [int(i) for i in str(token).split(",") if i.isdigit()]
    if not ids:
        return _refused("not a list of idea ids")
    await store.set_ideas_status(ids, "dismissed")
    return ActionResult(True, f"{len(ids)} idea(s) dismissed")


async def propose_automation_dry(idea_id: int) -> ActionResult:
    row = await store.idea(int(idea_id))
    if row is None or row.get("status") != "new":
        return _refused(f"idea {idea_id} is not new")
    return ActionResult(True, f"would add a build task for you: automate "
                              f"\"{row['pattern']}\"",
                        evidence={"text": f"Automate: {row['pattern']}"})


async def propose_automation(idea_id: int) -> ActionResult:
    from ..projects import store as board
    row = await store.idea(int(idea_id))
    if row is None or row.get("status") != "new":
        return _refused(f"idea {idea_id} is not new")
    tid, token = await board.add_task(
        project_id=None, title=f"Automate: {row['pattern']}", kind="build",
        owner="you", due_at=None,
        evidence={"idea_id": int(idea_id), "count": row.get("count"),
                  "samples": row.get("samples") or []},
        suggested_fix="write the steps down, then automate the one that repeats",
        source_action=ACTION, source_ref=f"bizops_automation_ideas:{int(idea_id)}")
    await store.set_idea(int(idea_id), "proposed", tid, token)
    return ActionResult(True, f"build task {tid} added for you: automate "
                              f"\"{row['pattern']}\"",
                        evidence={"task_id": tid}, rollback_token=f"idea:{int(idea_id)}")


async def undo_proposal(token: str) -> ActionResult:
    from ..projects import store as board
    head, _, rest = str(token).partition(":")
    if head != "idea" or not rest.isdigit():
        return _refused("not an idea token")
    row = await store.idea(int(rest))
    if row is None:
        return _refused("no such idea")
    tok = row.get("task_token")
    if tok:
        await board.restore(str(tok))
    await store.set_idea(int(rest), "new")
    return ActionResult(True, f"idea {rest} back to new")
