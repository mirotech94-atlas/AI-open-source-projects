"""Persistent memory.

An agent that forgets is a chatbot. ATLAS keeps four kinds of memory, each with
a different lifetime, because conflating them is what makes agent memory either
useless or unmanageably large:

  EPISODIC   what happened, and when. Incidents, decisions, conversations.
             Searchable by meaning. Decays: rolled up after 90 days.
  SEMANTIC   durable facts about the owner, his businesses, his people, his
             preferences. Never decays. Small. This is the valuable one.
  PROCEDURAL runbooks: how to fix a class of problem. Earns trust by
             succeeding; demoted by failing.
  WORKING    the current task's scratch space. Dies with the task.

Everything is namespaced, and agents are scoped to namespaces, so the Inbox
agent cannot read security memory and the Growth agent cannot read the owner's mail.
"""
from __future__ import annotations

import enum
import json
import logging
import math
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .permissions import wrap_untrusted
from .state import execute_sql, fetch_all, fetch_one

log = logging.getLogger("atlas.memory")


class Kind(enum.StrEnum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    WORKING = "working"


@dataclass(slots=True)
class Memory:
    id: int | None
    kind: Kind
    namespace: str
    content: str
    meta: dict[str, Any] = field(default_factory=dict)
    importance: float = 0.5
    at: datetime | None = None
    source: str | None = None        # whatsapp | portal | voice | ... ; None = older rows
    business: str | None = None      # a business key ("mmt"), or None
    score: float = 0.0               # the hybrid rank; 0 when not ranked


class Recalled(list[Memory]):
    """What `recall` found, and how it looked.

    `degraded` is None when the search was by meaning. Otherwise it is why it
    could not be — "ATLAS_EMBED_URL unset", or "the embedding model did not
    answer" — and the results are keyword matches only. It is a list, so
    every caller that iterates or compares keeps working; a caller that must
    say how it searched (`vault_recall`) reads the attribute.
    """

    def __init__(self, items: Sequence[Memory] = (), *,
                 degraded: str | None = None) -> None:
        super().__init__(items)
        self.degraded = degraded


def degraded_note(reason: str) -> str:
    """The one sentence that says recall could not search by meaning."""
    return f"semantic search unavailable ({reason}) — keyword match only"


# Words that say nothing about which memory is wanted. Dropped from a
# keyword recall so "when does my insurance renew?" looks for "insurance"
# and "renew", not for every chunk containing "my".
_STOP_WORDS = """
a about above after again against all also am an and any are as at be
because been before being below between both but by can could did do does
doing done down during each else ever few for from further get gets got had
has have having he her here hers herself him himself his how i if in into is
it its itself just know let like me more most much must my myself no nor not
now of off on once only or other our ours ourselves out over own please same
she should so some such tell than that the their theirs them themselves then
there these they this those through to too under until up upon us very was
we were what whats when where which while who whom whose why will with
would yes yet you your yours yourself yourselves anything something
"""
_STOP = frozenset(_STOP_WORDS.split())
# Letters and digits in any script, no underscore: "café" and "Ωmega" are
# words, and `%`, `_`, `\\` or a quote can never reach an ILIKE pattern.
_WORD = re.compile(r"[^\W_]+")
_SUFFIXES = ("ing", "ed", "es", "s")
KEYWORD_TERMS = 12


def _terms(query: str) -> list[str]:
    """The words of `query` worth matching on: lower-cased, stop words and
    one- or two-letter words dropped (a word with a digit is kept: "hx22",
    "2026"), a plural or tense suffix trimmed so "renews" still finds
    "renew". Letters and digits only (`_WORD`), so no `%`, `_`, `\\` or
    quote ever reaches an ILIKE pattern."""
    out: list[str] = []
    for word in _WORD.findall(query.lower()):
        if word in _STOP or (len(word) < 3 and not any(c.isdigit() for c in word)):
            continue
        for suffix in _SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                word = word[: -len(suffix)]
                break
        if word not in out:
            out.append(word)
    return out[:KEYWORD_TERMS]


# `memories.embedding` is vector(1024): anything else would fail the INSERT
# and take `remember()` down with it, so it is treated as no vector at all.
EMBED_DIM = 1024
# mxbai-embed-large reads at most 512 tokens. The server truncates (asked per
# request); without it a long chunk is a 400 and the backfill stops there.
EMBED_MAX_TOKENS = 512


def embed_unset() -> str | None:
    """None when an embedding endpoint is configured, else why not."""
    if os.getenv("ATLAS_EMBED_URL", "").strip() or os.getenv("OLLAMA_URL"):
        return None
    return "ATLAS_EMBED_URL unset"


def _vector(raw: Any) -> list[float] | None:
    if (isinstance(raw, list) and len(raw) == EMBED_DIM
            and all(isinstance(x, int | float) and not isinstance(x, bool)
                    for x in raw)):
        return [float(x) for x in raw]
    log.warning("embedding refused: not a %d-number vector", EMBED_DIM)
    return None


async def _embed(text: str) -> list[float] | None:
    """Local embeddings only — memory contains personal content (PRIVATE).

    `ATLAS_EMBED_URL` is an OpenAI-compatible `/v1` base on a local host
    (mxbai-embed-large on the Spark's second vLLM, through the Mac's tunnel).
    It passes the same guard and the same sealed client as the local model
    (`llm.local_base_url_ok`, `llm._local_client`: no proxy, no redirects),
    so a typo can never send memory to someone else's server. `OLLAMA_URL`
    is the older path, used only when `ATLAS_EMBED_URL` is unset."""
    url = os.getenv("ATLAS_EMBED_URL", "").strip()
    if url:
        return await _embed_openai(url.rstrip("/"), text)
    base = os.getenv("OLLAMA_URL")
    if not base:
        return None
    import httpx
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{base}/api/embeddings", json={
                "model": os.getenv("ATLAS_EMBED_MODEL", "mxbai-embed-large"),
                "prompt": text[:8000],
            })
            r.raise_for_status()
            return _vector(r.json().get("embedding"))
    except Exception as exc:                            # noqa: BLE001
        log.warning("embedding failed: %s", exc)
        return None


async def _embed_openai(url: str, text: str) -> list[float] | None:
    from . import llm
    if not llm.local_base_url_ok(url):
        log.warning("embedding refused: ATLAS_EMBED_URL is not a local address")
        return None
    key = (os.getenv("ATLAS_EMBED_API_KEY") or os.getenv("LOCAL_LLM_API_KEY")
           or "").strip()
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    try:
        async with llm._local_client(30) as c:
            r = await c.post(f"{url}/embeddings", headers=headers, json={
                "model": os.getenv("ATLAS_EMBED_MODEL", "mxbai-embed-large"),
                "input": text[:8000],
                "truncate_prompt_tokens": EMBED_MAX_TOKENS,
            })
            if r.status_code != 200:
                log.warning("embedding failed: HTTP %d", r.status_code)
                return None
            data = r.json().get("data") or [{}]
            return _vector(data[0].get("embedding"))
    except Exception as exc:                            # noqa: BLE001
        # The class only: the request (and so the key header) stays out.
        log.warning("embedding failed: %s", type(exc).__name__)
        return None


async def remember(kind: Kind, namespace: str, content: str, *,
                   meta: dict | None = None, importance: float = 0.5,
                   source: str | None = None, business: str | None = None) -> int:
    """Store one memory. `source` says where it came from (whatsapp, portal,
    voice) and `at` is stamped by the table, so a durable memory always carries
    both. Both stay NULL for the callers that predate them."""
    vec = await _embed(content)
    row = await fetch_one(
        """INSERT INTO memories (kind, namespace, content, meta, importance,
                                 embedding, source, business)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8) RETURNING id""",
        str(kind), namespace, content, json.dumps(meta or {}, default=str),
        importance, str(vec) if vec else None, source, business)
    return row["id"]


async def _log_reads(namespaces: Sequence[str], found: Sequence[Memory],
                     agent: str | None) -> None:
    """One `memory_reads` row per namespace queried, with its hit count.

    A write, but to ATLAS's own table: no collector, no outside world, no
    executor. It exists so the brain view on `/core` can show what ATLAS is
    actually *using* rather than only what it happens to store — a namespace
    nothing ever recalls from is a real fact about this system.

    A namespace that was searched and matched nothing is still logged, with
    zero hits. Skipping it would make "looked and found nothing" indistinct
    from "never looked", which is the whole question this table answers.

    Every failure is swallowed. A recall that broke because a log line could
    not be written would be the feature taking the system down with it.
    """
    hits: dict[str, int] = {ns: 0 for ns in namespaces}
    for m in found:
        if m.namespace in hits:
            hits[m.namespace] += 1
    try:
        for ns in namespaces:
            await execute_sql(
                "INSERT INTO memory_reads (namespace, hits, agent) "
                "VALUES ($1,$2,$3)", ns, hits[ns], agent)
    except Exception as exc:                            # noqa: BLE001
        log.warning("could not log a memory read: %s", exc)


# Hybrid ranking and the recall budget (Part A). `ATLAS_MEMORY_HYBRID=0` puts
# back the older order: distance alone, or keyword hits then importance.
RECALL_TOKENS = int(os.getenv("ATLAS_MEMORY_RECALL_TOKENS", "600"))
_PER_LINE_TOKENS = 12                    # the "(kind . source . date)" prefix


def _hybrid_on() -> bool:
    return os.getenv("ATLAS_MEMORY_HYBRID", "1").strip().lower() not in (
        "0", "false", "no", "off")


def _tokens(text: str) -> int:
    return len(text) // 4 + _PER_LINE_TOKENS


def _hybrid_score(*, distance: float | None, keyword_hits: int, n_terms: int,
                  kind: Kind, at: datetime | None) -> float:
    """0.6 x meaning + 0.25 x keyword share + 0.15 x freshness. A SEMANTIC
    memory (a durable fact) never goes stale, so it takes full freshness."""
    meaning = max(0.0, 1.0 - distance) if distance is not None else 0.0
    share = (keyword_hits / n_terms) if n_terms else 0.0
    if kind is Kind.SEMANTIC or at is None:
        fresh = 1.0
    else:
        stamp = at if at.tzinfo else at.replace(tzinfo=UTC)
        age_days = max(0.0, (datetime.now(UTC) - stamp).total_seconds() / 86400)
        fresh = math.exp(-age_days / 30)
    return 0.6 * meaning + 0.25 * share + 0.15 * fresh


def _within_budget(found: Sequence[Memory], budget: int) -> list[Memory]:
    """Highest score first, as many as fit in `budget` tokens (chars / 4)."""
    out: list[Memory] = []
    used = 0
    for m in sorted(found, key=lambda m: m.score, reverse=True):
        cost = _tokens(m.content)
        if used + cost > budget:
            continue
        out.append(m)
        used += cost
    return out


async def recall(query: str, namespaces: Sequence[str], *,
                 kinds: Sequence[Kind] = (), limit: int = 8,
                 agent: str | None = None,
                 token_budget: int | None = None) -> Recalled:
    """Semantic search, scoped to namespaces the caller is allowed to read.

    Without embeddings it is a keyword search, and the result says so in
    `.degraded` rather than passing for the real thing.

    `agent` is recorded on the read when the caller knows which agent it is
    recalling for, and left NULL when it does not. Never inferred.

    `token_budget` caps what comes back to about that many tokens, highest
    score first. A forgotten (hidden) memory is never returned.
    """
    if not namespaces:
        return Recalled()
    found = await _search(query, namespaces, kinds=kinds, limit=limit)
    if token_budget is not None:
        found = Recalled(_within_budget(found, token_budget),
                         degraded=found.degraded)
    await _log_reads(namespaces, found, agent)
    return found


async def _search(query: str, namespaces: Sequence[str], *,
                  kinds: Sequence[Kind] = (), limit: int = 8) -> Recalled:
    """The query behind `recall`, without the trail.

    For bookkeeping lookups — `learn_fact`'s dedupe check — that are not
    ATLAS using a memory and must not light a region on the brain view.

    With no embedding for the query, it falls back to keywords: the query's
    `_terms`, matched with `ILIKE ANY`, ranked by how many terms a memory
    contains. The whole-sentence ILIKE it replaces matched a question only
    when a chunk contained the question verbatim, which is never.
    """
    if not namespaces:
        return Recalled()
    vec = await _embed(query)
    kind_filter = [str(k) for k in kinds] or [str(k) for k in Kind]
    hybrid = _hybrid_on()
    fetch = limit * 3 if hybrid else limit      # re-ranked, then cut to `limit`
    terms = _terms(query)

    if vec is None:
        reason = embed_unset() or "the embedding model did not answer"
        if not terms:
            return Recalled(degraded=reason)
        patterns = [f"%{t}%" for t in terms]
        rows = await fetch_all(
            """SELECT m.*, (SELECT count(*) FROM unnest($3::text[]) AS p(pat)
                             WHERE m.content ILIKE p.pat) AS hits
                 FROM memories m
                WHERE m.namespace = ANY($1) AND m.kind = ANY($2)
                  AND m.deleted_at IS NULL
                  AND m.content ILIKE ANY($3::text[])
                ORDER BY hits DESC, m.importance DESC, m.at DESC LIMIT $4""",
            list(namespaces), kind_filter, patterns, fetch)
    else:
        reason = None
        rows = await fetch_all(
            """SELECT *, embedding <=> $1::vector AS distance FROM memories
               WHERE namespace = ANY($2) AND kind = ANY($3)
                 AND deleted_at IS NULL AND embedding IS NOT NULL
               ORDER BY distance LIMIT $4""",
            str(vec), list(namespaces), kind_filter, fetch)

    found = [_to_memory(r, terms) for r in rows]
    if hybrid:
        found.sort(key=lambda m: m.score, reverse=True)
        found = found[:limit]
    return Recalled(found, degraded=reason)


def _to_memory(r: Any, terms: Sequence[str]) -> Memory:
    kind = Kind(r["kind"])
    low = str(r["content"]).lower()
    keys = set(r.keys())
    hits = int(r["hits"]) if "hits" in keys and r["hits"] is not None else (
        sum(1 for t in terms if t in low))
    distance = float(r["distance"]) if "distance" in keys else None
    return Memory(
        id=r["id"], kind=kind, namespace=r["namespace"], content=r["content"],
        meta=json.loads(r["meta"]), importance=r["importance"], at=r["at"],
        source=r["source"] if "source" in keys else None,
        business=r["business"] if "business" in keys else None,
        score=_hybrid_score(distance=distance, keyword_hits=hits,
                            n_terms=len(terms), kind=kind, at=r["at"]))


BACKFILL_BATCH = 500


async def backfill_embeddings(namespaces: Sequence[str] | None = None, *,
                              batch: int = BACKFILL_BATCH) -> tuple[int, str | None]:
    """Embed memories that were stored without one — in `namespaces`, or in
    every namespace when it is None (the default: `threads`, `people`,
    `buyers`… vanish from semantic recall just as surely as vault chunks).

    A memory remembered before an embedder was set keeps `embedding IS
    NULL`, and semantic search skips it — for ever, unless something goes
    back. Returns `(embedded, problem)`. With no embedder it does nothing
    and asks nothing. It stops at the first embedding that fails rather than
    hammering a model that is down, and says how far it got. At most `batch`
    rows a pass, so one call stays bounded; the next pass continues.

    A write to ATLAS's own table, like `remember`: no outside world, no
    executor.
    """
    if embed_unset() or (namespaces is not None and not namespaces):
        return 0, None
    rows = await fetch_all(
        """SELECT id, content FROM memories
            WHERE ($1::text[] IS NULL OR namespace = ANY($1::text[]))
              AND deleted_at IS NULL AND embedding IS NULL
            ORDER BY id LIMIT $2""",
        list(namespaces) if namespaces is not None else None, batch)
    done = 0
    for row in rows:
        vec = await _embed(row["content"])
        if vec is None:
            return done, (f"the embedding model stopped answering after "
                          f"{done} of {len(rows)}")
        await execute_sql("UPDATE memories SET embedding = $2 WHERE id = $1",
                          row["id"], str(vec))
        done += 1
    return done, None


async def learn_fact(namespace: str, fact: str, importance: float = 0.8, *,
                     source: str | None = None, business: str | None = None) -> int:
    """Semantic memory. Deduplicates: an existing near-identical fact is
    reinforced rather than duplicated, which is what stops memory bloating.
    A fact the owner made ATLAS forget (hidden, not wiped) is not learned again:
    the hidden row's id comes back and nothing is written."""
    hidden = await fetch_all(
        "SELECT id FROM memories WHERE namespace = $1 AND deleted_at IS NOT NULL "
        "AND lower(content) = lower($2) LIMIT 1", namespace, fact.strip())
    if hidden:
        return int(hidden[0]["id"])
    # `_search`, not `recall`: a dedupe check is not a read, and must not
    # show on the brain view as one.
    existing = await _search(fact, [namespace], kinds=(Kind.SEMANTIC,), limit=1)
    if existing and existing[0].content.strip().lower() == fact.strip().lower():
        await execute_sql(
            "UPDATE memories SET importance = LEAST(importance + 0.1, 1.0), "
            "at = now() WHERE id = $1", existing[0].id)
        return existing[0].id
    return await remember(Kind.SEMANTIC, namespace, fact, importance=importance,
                          source=source, business=business)


async def consolidate() -> int:
    """Nightly. Roll old episodic memory into summaries and drop working
    memory. Without this, recall quality degrades as volume grows."""
    await execute_sql("DELETE FROM memories WHERE kind='working' "
                      "AND at < now() - interval '1 day'")
    rows = await fetch_all(
        """SELECT namespace, count(*) AS n FROM memories
           WHERE kind='episodic' AND deleted_at IS NULL
             AND at < now() - interval '90 days'
           GROUP BY namespace HAVING count(*) > 20""")
    from .llm import Message, Need, think
    rolled = 0
    for row in rows:
        olds = await fetch_all(
            """SELECT id, content FROM memories WHERE kind='episodic'
               AND deleted_at IS NULL
               AND namespace=$1 AND at < now() - interval '90 days'
               ORDER BY at LIMIT 200""", row["namespace"])
        joined = "\n".join(f"- {o['content']}" for o in olds)
        summary = await think(
            Need.TRIVIAL,
            "Compress these old event notes into at most 10 durable bullet "
            "points. Keep names, numbers and outcomes. Drop routine noise.",
            [Message("user", joined[:20000])], max_tokens=800)
        await remember(Kind.SEMANTIC, row["namespace"],
                       f"[rolled up from {len(olds)} events] {summary.text}",
                       importance=0.6)
        await execute_sql("DELETE FROM memories WHERE id = ANY($1)",
                          [o["id"] for o in olds])
        rolled += len(olds)
    return rolled


def owner_namespaces() -> list[str]:
    """Where the owner's own notes live: `owner`, and `biz.<key>` for each business in
    ATLAS_BUSINESSES (default "mmt,agency")."""
    keys = [b.strip().lower() for b in os.getenv("ATLAS_BUSINESSES", "mmt,agency").split(",")
            if b.strip()]
    return ["owner", *(f"biz.{k}" for k in keys)]


RECALL_TIMEOUT_S = 0.4


async def owner_recall_block(question: str, *, agent: str = "chat") -> str | None:
    """What ATLAS remembers of the owner that bears on `question`, quoted for a user
    message, or None. Never raises and never waits long: past RECALL_TIMEOUT_S
    the reply goes out without memory. Only for a question that is NOT about live
    figures; the caller decides that, a memory is never a source for a number."""
    import asyncio

    from . import reply_timing
    try:
        with reply_timing.stage("recall"):
            found = await asyncio.wait_for(
                recall(question, owner_namespaces(), token_budget=RECALL_TOKENS,
                       agent=agent), RECALL_TIMEOUT_S)
    except Exception as exc:                            # noqa: BLE001
        log.info("recall skipped: %s", type(exc).__name__)
        return None
    return quote_memories(found, "memory") if found else None


def format_for_prompt(memories: Sequence[Memory]) -> str:
    if not memories:
        return "(no relevant memory)"
    lines = []
    for m in memories:
        if m.source:
            when = f" · {m.at:%Y-%m-%d}" if m.at else ""
            lines.append(f"({m.kind} · {m.source}{when}) {m.content}")
        else:
            lines.append(f"[{m.kind}] {m.content}"
                         + (f"  ({m.at:%Y-%m-%d})" if m.at else ""))
    return "\n".join(lines)


NOT_A_LIVE_READING = ("Recollection of what the owner said; not a live reading. "
                      "Never quote a figure from it.")


def quote_memories(memories: Sequence[Memory], source: str = "memory") -> str:
    """Recalled memory as a prompt receives it: quoted, never instructed.

    What `recall` returns includes vault documents, library notes, and facts
    distilled from email and chat — text ATLAS did not write (invariant 4).
    It goes into a `user` message through `wrap_untrusted`, never into a
    system prompt, the same way the harness already quotes `context`.
    """
    return wrap_untrusted(f"{NOT_A_LIVE_READING}\n{format_for_prompt(memories)}",
                          source)

