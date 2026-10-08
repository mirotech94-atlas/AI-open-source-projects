"""Domain skill packs.

The kernel owns the mechanism (`kernel/skills.py`) and a starting set of
general judgement calls. It knows nothing about any business, so anything
that names a pod, a cluster, a client or a product lives here instead.

Loading: `load_all()` imports every submodule; each one calls `register()`
at import time. `boot()` calls this once, before the agent roster is built.

Budget — read before adding a skill:

* `skills_for()` returns at most THREE matches for a task, sorted by
  priority. Every broad trigger you add competes with the kernel's accuracy
  skills ("Never guess a number" 99, "Revenue-silent failures" 95,
  "Security findings" 92, "Incident triage" 90). Those should usually win.
* So: distinctive triggers (`corosync`, `galera`, `mellanox`) over generic
  ones (`error`, `down`, `failing`), and priority <= 88 unless the skill
  genuinely outranks triage for that word.
* Bodies describe how to DECIDE and what to VERIFY. They never instruct an
  action — the executor's catalog is the only thing that can act, and while
  `ATLAS_WATCH_ONLY` holds there is nothing to run anyway.
* No credentials, ever. A skill body is injected into a prompt. Name where a
  secret lives; never the value.

Seeding the library (Task 60) -- Miro: "there should also be some stuff on
there based on his previous skills from agents that he has as well."
`index_into_library()` reads every pack's own source (`ast`, no import: it
never depends on `load_all()` having already run, and never risks a pack's
import-time `register()` calls firing twice) and keeps one `library_entries`
row per pack (kind='skill', matched on kind+source so a re-seed at every
boot updates the row instead of piling up one per restart), plus a `.md`
copy under the vault directory for `vault.reindex` to pick up on its own
schedule -- this never writes to `vault_documents` itself; that stays
`modules/vault/index.py`'s one job.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import logging
import pkgutil
import re
from pathlib import Path

log = logging.getLogger("atlas.skills")

# The module docstring's first sentence, collapsed to one line, is the human
# title -- every pack here writes it as "Name — what it does.", so this
# reliably yields something worth showing in the library rather than the
# whole paragraph.
_TITLE_SENTENCE = re.compile(r"(.+?[.!?])(\s|$)", re.DOTALL)

# What this package registered, as opposed to the kernel's own accuracy set.
# The safety tests scan exactly this: a body reaches a prompt, so a credential
# in one is the expensive failure. It is recorded here rather than listed by
# hand in the test, because a hand-written list silently stops covering the
# next pack somebody adds -- which is the one failure mode this guard exists
# to prevent.
REGISTERED_BY_PACKS: set[str] = set()


def load_all() -> None:
    """Import every skill pack in this package, registering as it goes."""
    from ..kernel.skills import SKILLS

    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        before = {id(s) for s in SKILLS}
        try:
            importlib.import_module(f"{__name__}.{info.name}")
        except Exception as exc:                        # noqa: BLE001
            log.warning("skill pack %s failed to load: %s", info.name, exc)
            continue
        REGISTERED_BY_PACKS.update(
            s.name for s in SKILLS if id(s) not in before)


def _playbook_title_and_text(source: str) -> tuple[str, str] | None:
    """(title, playbook text) parsed statically out of one pack's source, or
    None when there is no module docstring to build a title from.

    `title` is the docstring's first sentence, whitespace-collapsed --
    "Mirotech — eBay reselling of IT and networking hardware." rather than
    the whole paragraph. `text` is the docstring plus "### <name>\\n<body>"
    for every `register(Skill(...))` call found by walking the tree, in
    source order -- the judgement calls that actually shape an agent, not
    just the one-paragraph introduction.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    doc = (ast.get_docstring(tree) or "").strip()
    if not doc:
        return None

    first_para = " ".join(doc.split("\n\n", 1)[0].split())
    match = _TITLE_SENTENCE.match(first_para)
    title = (match.group(1) if match else first_para).strip()[:200]

    sections = [doc]
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "register"):
            continue
        for arg in node.args:
            if not isinstance(arg, ast.Call):
                continue
            name_val = body_val = None
            for kw in arg.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                    name_val = kw.value.value
                elif kw.arg == "body" and isinstance(kw.value, ast.Constant):
                    body_val = kw.value.value
            if isinstance(name_val, str) and isinstance(body_val, str):
                sections.append(f"### {name_val}\n{body_val.strip()}")

    return title, "\n\n".join(sections)


async def index_into_library(directory: Path | None = None) -> int:
    """Every pack in `directory` (this package by default), kept in the
    library and, when there is somewhere to put it, dropped into the vault.
    Idempotent: `library.seed_skill` matches on kind+source, and the vault
    copy's content only changes when the playbook itself does, so a re-run
    at every boot neither duplicates a library row nor forces a needless
    re-index.

    Never creates `vault_dir()` itself. `check_index` (invariant 7) reports
    UNKNOWN, not a healthy zero, when `VAULT_DIR` does not exist -- silently
    creating it here would turn a misconfigured path into a directory that
    looks intentional the next time anything checks. When the vault
    directory is missing, the library rows are still seeded (that half of
    the ask does not depend on the vault); only the `.md` copies are
    skipped, once, with a single warning rather than one per pack.

    One pack failing (a read error, a bad write) is logged and skipped --
    the rest still get seeded. Returns how many library rows were newly
    written or changed, not how many files were read, so a boot where
    nothing moved logs as zero.
    """
    from ..modules.vault import library
    from ..modules.vault.index import vault_dir

    directory = directory or Path(__file__).resolve().parent
    root = vault_dir()
    vault_ready = await asyncio.to_thread(root.is_dir)
    if not vault_ready:
        log.warning("skill seed: vault directory not found (%s) -- library "
                   "rows will still be written, but no .md copies this run",
                   root)
    skills_dir = root / "skills"
    changed = 0

    for path in sorted(directory.glob("*.py")):
        if path.stem == "__init__" or path.stem.startswith("_"):
            continue
        try:
            source = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except OSError as exc:
            log.warning("skill seed: could not read %s: %s", path, exc)
            continue

        parsed = _playbook_title_and_text(source)
        if parsed is None:
            continue
        title, text = parsed
        entry_source = f"atlas/skills/{path.name}"

        try:
            if await library.seed_skill(title, entry_source, text[:2000]):
                changed += 1
        except Exception:
            log.exception("skill seed: could not write a library row for %s",
                          path.name)

        if not vault_ready:
            continue
        try:
            await asyncio.to_thread(skills_dir.mkdir, exist_ok=True)
            await asyncio.to_thread(
                (skills_dir / f"{path.stem}.md").write_text,
                f"# {title}\n\n{text}\n", encoding="utf-8")
        except OSError as exc:
            log.warning("skill seed: could not write the vault copy for %s: %s",
                       path.name, exc)

    return changed
