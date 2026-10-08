"""File indexing: the vault directory, chunked and remembered.

Nothing here fetches from anywhere. It reads files already on disk under
`VAULT_DIR` (default `data/vault`) — leases, insurance policies, contracts,
receipts — hashes each one, and remembers new or changed content as chunks
of semantic memory in the `"vault"` namespace. The concierge answers
"when does my insurance renew?" by recalling those chunks; there is no
separate query path here.

Indexing is keyed on content, not on name or mtime: a file that has not
changed since the last pass is skipped, so re-running `vault.reindex` on a
schedule does not duplicate the same facts into memory every cycle.

Recall by meaning needs an embedding on every memory, and one stored before
`OLLAMA_URL` was set has none — a vault chunk, or a `threads`/`people` fact.
`index_all` (and the scheduler's hourly maintenance step) backfills them, in
every namespace, once the embedding model answers; `check_index` is DEGRADED
— naming the count — while any are missing or `OLLAMA_URL` is unset, because
recall is then keyword-only or blind to them and should not read as healthy.

File I/O and hashing are blocking, so every touch of the filesystem runs
under `asyncio.to_thread`.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from pathlib import Path

from ...kernel.contracts import ActionResult, Health, Observation
from ...kernel.memory import (
    BACKFILL_BATCH,
    Kind,
    backfill_embeddings,
    degraded_note,
    remember,
)
from ...kernel.state import execute_sql, fetch_one

log = logging.getLogger("atlas.vault")

SUPPORTED_SUFFIXES = (".txt", ".md", ".pdf")
# How many indexed documents `reindex` names in its evidence -- the audit
# chain caps lists at 20 anyway.
DOCS_IN_EVIDENCE = 20

# Keyword hints, checked case-insensitively against the file name and the
# extracted text. Order matters only where a document could plausibly match
# more than one — a lease that also mentions "insurance" for the tenant's
# policy is still a lease.
_KIND_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("lease", ("lease", "tenancy", "landlord", "tenant")),
    ("insurance", ("insurance", "policy", "premium", "insurer", "coverage")),
    ("contract", ("agreement", "contract", "hereby agree", "party of the",
                  "terms and conditions")),
    ("receipt", ("receipt", "invoice", "total due", "order confirmation",
                 "amount paid")),
)


def vault_dir() -> Path:
    """Where documents live. `VAULT_DIR` if set, else `data/vault`."""
    return Path(os.getenv("VAULT_DIR", "data/vault"))


async def extract_text(path: Path) -> tuple[str | None, str | None]:
    """Plain text of a document, or (None, reason) when it cannot be read.

    `.txt` and `.md` are read directly. `.pdf` goes through `pypdf`, which
    is optional: it is imported here, not at module load, so a box without
    it still loads the module and every other file type still indexes —
    only PDFs report the specific reason.
    """
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        except OSError as exc:
            return None, str(exc)
        return text, None

    if suffix == ".pdf":
        try:
            import pypdf
        except ImportError:
            return None, "pypdf not installed"

        def _read() -> str:
            reader = pypdf.PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)

        try:
            text = await asyncio.to_thread(_read)
        except Exception as exc:                        # noqa: BLE001
            return None, f"could not read pdf: {exc}"
        return text, None

    return None, f"unsupported file type: {suffix or '(none)'}"


def chunk(text: str, size: int = 1200, overlap: int = 150) -> list[str]:
    """Sliding-window chunks of `text`, each up to `size` chars, consecutive
    chunks sharing `overlap` chars so a fact split across a boundary still
    appears whole in at least one chunk."""
    text = text.strip()
    if not text:
        return []
    if size <= 0:
        raise ValueError("size must be positive")
    step = max(size - max(overlap, 0), 1)

    pieces: list[str] = []
    i = 0
    n = len(text)
    while True:
        pieces.append(text[i : i + size])
        if i + size >= n:
            break
        i += step
    return pieces


def guess_kind(name: str, text: str) -> str:
    """lease | insurance | contract | receipt | other, from keyword hints in
    the file name and its extracted text."""
    haystack = f"{name} {text}".lower()
    for kind, keywords in _KIND_KEYWORDS:
        if any(keyword in haystack for keyword in keywords):
            return kind
    return "other"


async def _list_dir(directory: Path) -> list[Path]:
    def _sync() -> list[Path]:
        if not directory.is_dir():
            return []
        return sorted(
            p for p in directory.rglob("*")
            if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
        )
    return await asyncio.to_thread(_sync)


async def _hash_file(path: Path) -> str:
    def _sync() -> str:
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                digest.update(block)
        return digest.hexdigest()
    return await asyncio.to_thread(_sync)


async def _pending() -> list[tuple[Path, str]]:
    """(path, sha256) for every file that is new or whose content changed
    since it was last indexed. Shared by the dry run and the real pass so
    the two never disagree about what "changed" means."""
    pending: list[tuple[Path, str]] = []
    for path in await _list_dir(vault_dir()):
        try:
            sha = await _hash_file(path)
        except OSError as exc:
            log.warning("vault: could not hash %s: %s", path, exc)
            continue
        existing = await fetch_one(
            "SELECT sha256 FROM vault_documents WHERE path = $1", str(path))
        if not existing or existing["sha256"] != sha:
            pending.append((path, sha))
    return pending


async def index_all() -> tuple[int, list[str]]:
    """Index every new or changed file under `vault_dir()`, then backfill
    embeddings memory is missing.

    Returns `(indexed, problems)`: how many documents were (re)indexed, and
    one line per file that could not be indexed at all.
    """
    indexed, problems, _filled, _docs = await _index()
    return indexed, problems


async def _index() -> tuple[int, list[str], int, list[dict[str, str]]]:
    """`index_all`, plus how many embeddings were backfilled — `reindex`
    puts that count in its detail, which is what the audit log keeps -- and
    the first `DOCS_IN_EVIDENCE` documents indexed, as (path, sha256[:12]),
    which `vault.reindex`'s read-back looks up in `vault_documents`."""
    indexed = 0
    problems: list[str] = []
    docs: list[dict[str, str]] = []
    for path, sha in await _pending():
        text, problem = await extract_text(path)
        if problem:
            problems.append(f"{path}: {problem}")
            continue
        if not text or not text.strip():
            problems.append(f"{path}: no extractable text")
            continue

        # A file this indexer finds under a `skills/` subdirectory is one
        # of Task 60's agent-playbook copies, not something a keyword guess
        # should classify -- several of the real playbooks' own text matches
        # "agreement"/"policy"/"lease" by coincidence, and showing one as
        # somebody's insurance in the Vault table would be worse than not
        # guessing at all.
        kind = "skill" if path.parent.name == "skills" else guess_kind(path.name, text)
        pieces = chunk(text)
        for i, piece in enumerate(pieces):
            await remember(
                Kind.SEMANTIC, "vault", piece,
                meta={"path": str(path), "title": path.stem, "kind": kind, "i": i})

        await execute_sql(
            """INSERT INTO vault_documents (path, sha256, title, kind, chunks)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (path) DO UPDATE SET
                   sha256 = $2, title = $3, kind = $4,
                   indexed_at = now(), chunks = $5""",
            str(path), sha, path.stem, kind, len(pieces))
        indexed += 1
        if len(docs) < DOCS_IN_EVIDENCE:
            docs.append({"path": str(path), "sha": sha[:12]})

    # Memories stored without an embedding (before OLLAMA_URL, or while the
    # model was down) are invisible to semantic search until one is added —
    # in every namespace, not only this module's.
    filled = 0
    if os.getenv("OLLAMA_URL"):
        filled, stopped = await backfill_embeddings(None)
        if filled:
            log.info("vault: backfilled %d embedding(s)", filled)
        if stopped:
            problems.append(f"embedding backfill: {stopped}")
    return indexed, problems, filled, docs


async def _unembedded() -> int:
    """Memories in any namespace with no embedding. Raises when it cannot
    count: the caller reports UNKNOWN, never zero."""
    row = await fetch_one(
        "SELECT count(*) AS n FROM memories WHERE embedding IS NULL")
    return int(row["n"]) if row and row["n"] is not None else 0


async def check_index() -> Observation:
    """UNKNOWN when the vault directory itself is missing or unreadable —
    "no documents" and "nowhere to look" are different facts, and the second
    read as the first would hide a misconfigured VAULT_DIR indefinitely."""
    directory = vault_dir()
    is_dir = await asyncio.to_thread(directory.is_dir)
    if not is_dir:
        return Observation("index", Health.UNKNOWN,
                           detail=f"vault directory not found: {directory}")
    try:
        row = await fetch_one("SELECT count(*) AS n FROM vault_documents")
    except Exception as exc:                            # noqa: BLE001
        return Observation("index", Health.UNKNOWN,
                           detail=f"could not read vault_documents: {exc}")
    n = int(row["n"]) if row else 0
    docs = f"{n} document(s) indexed from {directory}"
    try:
        missing = await _unembedded()
    except Exception as exc:                            # noqa: BLE001
        return Observation("index", Health.UNKNOWN,
                           detail=f"{docs}; could not count chunks without an "
                                  f"embedding: {exc}")
    chunks = f"{missing} memories have no embedding"
    if not os.getenv("OLLAMA_URL"):
        return Observation("index", Health.DEGRADED, float(n), "documents",
                           f"{docs}; {degraded_note('OLLAMA_URL unset')}; "
                           f"{chunks}")
    if missing:
        return Observation("index", Health.DEGRADED, float(n), "documents",
                           f"{docs}; {chunks} — recall misses them until "
                           f"vault.reindex backfills them")
    return Observation("index", Health.OK, float(n), "documents", docs)


async def reindex() -> ActionResult:
    indexed, problems, filled, docs = await _index()
    detail = f"indexed {indexed} new or changed document(s)"
    if filled:
        detail += f"; embedded {filled} memories that had none"
    if problems:
        detail += f"; {len(problems)} problem(s): " + "; ".join(problems[:5])
    return ActionResult(True, detail, evidence={
        "indexed": indexed, "backfilled": filled, "problems": problems,
        "docs": docs})


async def _to_embed() -> int | None:
    """What a real pass would backfill: the missing count when OLLAMA_URL is
    set, 0 when it is not (nothing would run), None when it cannot be read."""
    if not os.getenv("OLLAMA_URL"):
        return 0
    try:
        return await _unembedded()
    except Exception as exc:                            # noqa: BLE001
        log.warning("vault: could not count memories to embed: %s", exc)
        return None


async def reindex_dry() -> ActionResult:
    pending = await _pending()
    names = [str(path) for path, _sha in pending]
    missing = await _to_embed()
    parts: list[str] = []
    if names:
        parts.append(f"would index {len(names)} file(s): " + ", ".join(names[:10]))
    if missing:
        parts.append(f"would embed up to {min(missing, BACKFILL_BATCH)} of "
                     f"{missing} memories that have none")
    elif missing is None:
        parts.append("could not count memories waiting for an embedding")
    if not parts:
        return ActionResult(True, "no new or changed files to index")
    return ActionResult(True, "; ".join(parts),
                        evidence={"files": names, "to_embed": missing})


async def reindex_rollback(token: str) -> ActionResult:
    """Re-indexing only adds or refreshes memory rows about content already
    on disk; running it again produces the same facts, so there is nothing
    to undo."""
    return ActionResult(True, "re-index is idempotent; nothing to undo")
