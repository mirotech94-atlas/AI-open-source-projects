"""owner_memory -- the owner tells ATLAS to remember, forget, restore or correct.

Everything here changes ATLAS's own `memories` table and nothing outside it.
Forget is a HIDE, not a delete: `deleted_at` is set, every read leaves the row
out, and `restore` (or the action's rollback) clears it. Nothing in this module
removes a row; the hard wipe is a separate command the owner types himself
(`python -m atlas.cli memory-wipe`, kernel/memory_wipe.py) and no action,
agent or model can reach it.

The ids come from a search the CODE ran over the owner's own namespaces
(`owner` and `biz.<key>`), never from a model. Another namespace (vault, mail,
security) cannot be touched from here.

Tier 1: a dry run lists the rows, a rollback undoes it. Not `principal_only`:
the owner asks over WhatsApp, which is not a principal origin, and these actions can
only hide or restore his own notes. Under ATLAS_WATCH_ONLY the executor runs
nothing.
"""
from __future__ import annotations

import logging
from typing import Any

from ...kernel import memory
from ...kernel.audit import audit
from ...kernel.contracts import Action, ActionResult, FailureClass, Tier
from ...kernel.dev_requests import secret_shaped
from ...kernel.memory_extract import SOURCES, credential_shaped, instruction_shaped
from ...kernel.state import fetch_all

log = logging.getLogger("atlas.owner_memory")

FORGET = "owner_memory.forget"
RESTORE = "owner_memory.restore"
CORRECT = "owner_memory.correct"
REMEMBER = "owner_memory.remember"
MAX_IDS = 20
TEXT_MAX = 300
OWN = "(namespace = 'owner' OR namespace LIKE 'biz.%')"

IDS = {"memory_ids": "comma-separated memory ids, chosen by a search in code"}


def _ids(params: dict[str, Any]) -> list[int] | None:
    parts = [p.strip() for p in str(params.get("memory_ids") or "").split(",")
             if p.strip()]
    if not parts or len(parts) > MAX_IDS or not all(p.isdigit() for p in parts):
        return None
    return sorted({int(p) for p in parts})


def _vet_ids(params: dict[str, Any]) -> str | None:
    return None if _ids(params) else f"memory_ids must be 1 to {MAX_IDS} numbers"


def _vet_text(params: dict[str, Any], key: str) -> str | None:
    text = " ".join(str(params.get(key) or "").split())
    if not text or len(text) > TEXT_MAX:
        return f"{key} must be 1 to {TEXT_MAX} characters"
    if secret_shaped(text) or credential_shaped(text):
        return f"{key} looks like it contains a secret"
    if instruction_shaped(text):
        return f"{key} reads like an instruction to a model, not a note"
    return None


def _vet_source(params: dict[str, Any]) -> str | None:
    src = params.get("source", "whatsapp")
    return None if src in SOURCES else "source must be whatsapp, portal or voice"


def _vet_correct(params: dict[str, Any]) -> str | None:
    ids = _ids(params)
    if not ids or len(ids) != 1:
        return "correct takes exactly one memory id"
    return _vet_source(params) or _vet_text(params, "new_text")


def _vet_remember(params: dict[str, Any]) -> str | None:
    return _vet_source(params) or _vet_text(params, "text")


async def _describe(ids: list[int], hidden: bool | None) -> list[str]:
    cond = "" if hidden is None else (
        " AND deleted_at IS NOT NULL" if hidden else " AND deleted_at IS NULL")
    rows = await fetch_all(
        f"SELECT id, namespace, content, source, at FROM memories "
        f"WHERE id = ANY($1) AND {OWN}{cond} ORDER BY id", ids)
    return [f"#{r['id']} ({r['source'] or 'older'}, {r['at']:%Y-%m-%d}): "
            f"{str(r['content'])[:80]}" for r in rows]


async def _set_hidden(ids: list[int], hide: bool) -> list[int]:
    sql = ("UPDATE memories SET deleted_at = now() WHERE id = ANY($1) "
           f"AND deleted_at IS NULL AND {OWN} RETURNING id") if hide else (
        "UPDATE memories SET deleted_at = NULL WHERE id = ANY($1) "
        f"AND deleted_at IS NOT NULL AND {OWN} RETURNING id")
    rows = await fetch_all(sql, ids)
    return sorted(int(r["id"]) for r in rows)


def _token(kind: str, *ids: int) -> str:
    return f"{kind}:" + ",".join(str(i) for i in ids)


# ---------------------------------------------------------------- forget / restore

async def forget_dry(memory_ids: str) -> ActionResult:
    ids = _ids({"memory_ids": memory_ids})
    if ids is None:
        return ActionResult(False, "would not hide: " + str(_vet_ids({})))
    lines = await _describe(ids, hidden=False)
    if not lines:
        return ActionResult(True, "nothing to hide: no such visible note")
    return ActionResult(True, "would hide (undoable, nothing deleted): "
                        + "; ".join(lines))


async def forget(memory_ids: str) -> ActionResult:
    ids = _ids({"memory_ids": memory_ids})
    if ids is None:
        return ActionResult(False, "not hidden: bad ids",
                            failure_class=FailureClass.VALIDATION)
    done = await _set_hidden(ids, hide=True)
    await audit("owner_memory.forget", ",".join(map(str, done)), {"n": len(done)})
    return ActionResult(True, f"hid {len(done)} note(s); say restore to undo",
                        evidence={"ids": done},
                        rollback_token=_token("forget", *done) if done else None)


async def restore_dry(memory_ids: str) -> ActionResult:
    ids = _ids({"memory_ids": memory_ids})
    if ids is None:
        return ActionResult(False, "would not restore: bad ids")
    lines = await _describe(ids, hidden=True)
    return ActionResult(True, ("would bring back: " + "; ".join(lines)) if lines
                        else "nothing to bring back: no such hidden note")


async def restore(memory_ids: str) -> ActionResult:
    ids = _ids({"memory_ids": memory_ids})
    if ids is None:
        return ActionResult(False, "not restored: bad ids",
                            failure_class=FailureClass.VALIDATION)
    done = await _set_hidden(ids, hide=False)
    await audit("owner_memory.restore", ",".join(map(str, done)), {"n": len(done)})
    return ActionResult(True, f"brought back {len(done)} note(s)",
                        evidence={"ids": done},
                        rollback_token=_token("restore", *done) if done else None)


def _token_ids(token: str) -> list[int]:
    return [int(p) for p in token.split(":", 1)[-1].split(",") if p.isdigit()]


async def undo_forget(token: str) -> ActionResult:
    ids = _token_ids(token)
    done = await _set_hidden(ids, hide=False) if ids else []
    return ActionResult(True, f"brought back {len(done)} note(s)")


async def undo_restore(token: str) -> ActionResult:
    ids = _token_ids(token)
    done = await _set_hidden(ids, hide=True) if ids else []
    return ActionResult(True, f"hid {len(done)} note(s) again")


# ------------------------------------------------------------- remember / correct

async def _new_note(text: str, source: str, like: int | None) -> int:
    ns, biz = "owner", None
    if like is not None:
        rows = await fetch_all(
            f"SELECT namespace, business FROM memories WHERE id = $1 AND {OWN}", like)
        if rows:
            ns, biz = str(rows[0]["namespace"]), rows[0]["business"]
    return await memory.remember(memory.Kind.SEMANTIC, ns,
                                 " ".join(text.split())[:TEXT_MAX], importance=0.9,
                                 source=source, business=biz)


async def remember_dry(text: str, source: str = "whatsapp") -> ActionResult:
    why = _vet_remember({"text": text, "source": source})
    return ActionResult(why is None, f"would not keep: {why}" if why
                        else f"would keep this note from {source}: {text[:80]}")


async def remember_note(text: str, source: str = "whatsapp") -> ActionResult:
    why = _vet_remember({"text": text, "source": source})
    if why:
        return ActionResult(False, f"not kept: {why}",
                            failure_class=FailureClass.VALIDATION)
    new_id = await _new_note(text, source, None)
    await audit("owner_memory.remember", str(new_id), {})
    return ActionResult(True, "kept it", evidence={"id": new_id},
                        rollback_token=_token("remember", new_id))


async def undo_remember(token: str) -> ActionResult:
    done = await _set_hidden(_token_ids(token), hide=True)
    return ActionResult(True, f"hid {len(done)} note(s)")


async def correct_dry(memory_ids: str, new_text: str,
                      source: str = "whatsapp") -> ActionResult:
    why = _vet_correct({"memory_ids": memory_ids, "new_text": new_text,
                        "source": source})
    if why:
        return ActionResult(False, f"would not correct: {why}")
    lines = await _describe(_ids({"memory_ids": memory_ids}) or [], hidden=False)
    return ActionResult(bool(lines), ("would replace " + "; ".join(lines)
                                      + f" with: {new_text[:80]}") if lines
                        else "nothing to correct: no such visible note")


async def correct(memory_ids: str, new_text: str,
                  source: str = "whatsapp") -> ActionResult:
    why = _vet_correct({"memory_ids": memory_ids, "new_text": new_text,
                        "source": source})
    ids = _ids({"memory_ids": memory_ids})
    if why or not ids:
        return ActionResult(False, f"not corrected: {why}",
                            failure_class=FailureClass.VALIDATION)
    old = ids[0]
    new_id = await _new_note(new_text, source, old)
    done = await _set_hidden([old], hide=True)
    await audit("owner_memory.correct", str(old), {"new": new_id})
    return ActionResult(True, "corrected it; the old note is hidden, not deleted",
                        evidence={"old": old, "new": new_id},
                        rollback_token=f"correct:{old},{new_id}" if done else None)


async def undo_correct(token: str) -> ActionResult:
    ids = _token_ids(token)
    if len(ids) != 2:
        return ActionResult(False, "bad rollback token")
    await _set_hidden([ids[0]], hide=False)
    await _set_hidden([ids[1]], hide=True)
    return ActionResult(True, "put the old note back and hid the new one")


class OwnerMemoryModule:
    name = "owner_memory"
    title = "the owner's own notes: remember, forget, restore, correct"
    requires_env = ()
    optional_env = ("ATLAS_MEMORY_WRITE", "ATLAS_BUSINESSES")
    setup_note = "Changes only ATLAS's own notes table; forget hides, never deletes."

    async def setup(self) -> None: ...
    async def teardown(self) -> None: ...

    def checks(self) -> tuple:
        return ()

    def actions(self) -> tuple[Action, ...]:
        return (
            Action(REMEMBER, Tier.AUTO,
                   "Keep one note the owner asked ATLAS to remember (own table only)",
                   run=remember_note, dry_run=remember_dry, rollback=undo_remember,
                   params={"text": "the note, at most 300 characters",
                           "source": "whatsapp, portal or voice"},
                   max_per_hour=10, vet=_vet_remember, card_only=True),
            Action(FORGET, Tier.AUTO,
                   "Hide notes from ATLAS's memory at the owner's request (undoable; "
                   "nothing is deleted)",
                   run=forget, dry_run=forget_dry, rollback=undo_forget,
                   params=dict(IDS), max_per_hour=10, vet=_vet_ids, card_only=True),
            Action(RESTORE, Tier.AUTO,
                   "Bring back notes that were hidden", run=restore,
                   dry_run=restore_dry, rollback=undo_restore, params=dict(IDS),
                   max_per_hour=10, vet=_vet_ids, card_only=True),
            Action(CORRECT, Tier.AUTO,
                   "Replace one note with the owner's corrected wording (the old one "
                   "is hidden, not deleted)",
                   run=correct, dry_run=correct_dry, rollback=undo_correct,
                   params={**IDS, "new_text": "the corrected note",
                           "source": "whatsapp, portal or voice"},
                   max_per_hour=10, vet=_vet_correct, card_only=True),
        )


MODULE = OwnerMemoryModule()
