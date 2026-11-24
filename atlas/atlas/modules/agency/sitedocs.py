"""Client-site documents are Claude Docs artifacts (the owner's rule, 2026-10-29).

Every document the sites agent writes for a client site -- a brief, a
handoff, a report, a proposal, a client guide -- ends up as a Claude Docs
artifact. ATLAS cannot create one: only `kernel/executor.py` acts, and the
artifact lives in Claude, reached by the coding agent on the owner's Mac. So:

1. `agency.draft_doc` (Tier AUTO, ATLAS's own row only) stores the draft in
   `agency_site_docs` and raises the publish card. Its rollback withdraws
   the draft; nothing is deleted.
2. `agency.publish_doc` (Tier ASK, card only) writes a `handoff.delegate`
   task -- "create this as a Docs artifact" -- and queues it for the coding
   agent, exactly like `agency.request_template`. The draft and any client
   text go in as EVIDENCE, which `handoff.delegate` passes through
   `wrap_untrusted` (invariant 4). A brief over the queue's cap is refused,
   never published cut short.
3. The runner creates the doc with the docs skill or connector and reports
   with `--artifact-url`. `/handoffs/{id}/report` accepts only a
   `https://claude.ai/...artifact/<id>` link (`artifact_url_ok`) and records
   it here. It is the coding agent's word: the panel says "reported", with
   the time, never "verified".
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from ...kernel import handoff
from ...kernel.contracts import ActionResult, Tier
from . import _store, hosting

KINDS: tuple[str, ...] = ("brief", "handoff", "report", "proposal", "client_guide")
KIND_LABEL = {"brief": "brief", "handoff": "handoff", "report": "report",
              "proposal": "proposal", "client_guide": "client guide"}
MAX_TITLE = 120
MAX_BODY = 8000
MAX_CLIENT = 3000
# Strict on purpose: the title is shown on the owner's card and reaches the
# coding agent (inside the wrapped EVIDENCE only). Letters, digits, spaces
# and plain punctuation; no quotes, backticks, brackets or markup.
_TITLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ,.:&()/?!+-]{2,119}$")
# A Claude artifact link and nothing else: the page renders it as an href.
_ARTIFACT = re.compile(
    r"^https://claude\.ai/(?:artifact|code/artifact)/[A-Za-z0-9][A-Za-z0-9_-]{5,79}$")

PUBLISH = "agency.publish_doc"


def artifact_url_ok(url: str) -> bool:
    return bool(_ARTIFACT.fullmatch(url or ""))


def doc_problem(slug: str = "", kind: str = "", title: str = "", body: str = "",
                client_text: str = "") -> str | None:
    """Why this document cannot be drafted, or None."""
    if kind not in KINDS:
        return f"the kind must be one of {', '.join(KINDS)} (got {kind!r})"
    problem = hosting.slug_problem(slug or "")
    if problem:
        return f"slug: {problem}"
    if not _TITLE.match((title or "").strip()):
        return (f"the title must be 3-{MAX_TITLE} characters: letters, digits, "
                f"spaces and , . : & ( ) / ? ! + - only, starting with a letter "
                f"or digit")
    body = (body or "").strip()
    if not body:
        return "the body is empty"
    if len(body) > MAX_BODY:
        return f"the body is {len(body)} characters; the limit is {MAX_BODY}"
    if len(client_text or "") > MAX_CLIENT:
        return f"the client text is over {MAX_CLIENT} characters"
    return None


def _did(value: object) -> int | None:
    try:
        did = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return did if did > 0 else None


# ---------------------------------------------------------------- the store

async def _insert_doc(slug: str, kind: str, title: str, body: str,
                  client_text: str) -> int:
    from ...kernel.state import fetch_one
    row = await fetch_one(
        """INSERT INTO agency_site_docs (slug, kind, title, body, client_text)
           VALUES ($1, $2, $3, $4, $5) RETURNING id""",
        slug, kind, title, body, client_text)
    return int(row["id"])


async def _load(did: int) -> dict[str, Any] | None:
    from ...kernel.state import fetch_one
    row = await fetch_one("SELECT * FROM agency_site_docs WHERE id = $1", did)
    return dict(row) if row else None


async def _set_status(did: int, status: str, *, was: Iterable[str],
                      handoff_id: int | None = None) -> bool:
    """One conditional UPDATE: the move happens only from a state in `was`."""
    from ...kernel.state import fetch_one
    row = await fetch_one(
        """UPDATE agency_site_docs SET status = $2, handoff_id = $3
            WHERE id = $1 AND status = ANY($4::text[]) RETURNING id""",
        did, status, handoff_id, list(was))
    return row is not None


async def record_link(handoff_id: int, url: str) -> dict[str, Any] | None:
    """The coding agent's report of the artifact it made. Only a queued doc
    on that handoff takes it; anything else returns None."""
    from ...kernel.state import fetch_one
    if not artifact_url_ok(url):
        return None
    row = await fetch_one(
        """UPDATE agency_site_docs
              SET status = 'published', artifact_url = $2, reported_at = now()
            WHERE handoff_id = $1 AND status = 'queued'
        RETURNING id, slug, title""", handoff_id, url)
    return dict(row) if row else None


async def docs_by_site(slugs: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Newest first per site, with the handoff's own status. Raises when the
    table cannot be read: the caller says so (invariant 7)."""
    from ...kernel.state import fetch_all
    rows = await fetch_all(
        """SELECT d.id, d.slug, d.kind, d.title, d.status, d.artifact_url,
                  d.reported_at, d.created_at, h.status AS handoff_status
             FROM agency_site_docs d
             LEFT JOIN handoffs h ON h.id = d.handoff_id
            WHERE d.slug = ANY($1::text[]) AND d.status <> 'withdrawn'
            ORDER BY d.created_at DESC""", slugs)
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(str(r["slug"]), []).append(dict(r))
    return out


# ---------------------------------------------------------------- drafting

async def _site_or_problem(slug: str) -> tuple[dict | None, str | None]:
    site = await _store.get_site(slug)
    if site is None:
        return None, f"there is no client site {slug!r} in the registry"
    return site, None


def _reason(kind: str, slug: str, title: str, body: str) -> str:
    return (f"Publish the {KIND_LABEL[kind]} for client site {slug} as a Claude "
            f"Docs artifact: \"{title}\". Approving hands it to the coding agent "
            f"on the Mac, which creates the doc and reports the link back to the "
            f"Client Sites panel.\n\n{body[:1000]}")


async def draft_doc_dry(slug: str = "", kind: str = "", title: str = "",
                        body: str = "", client_text: str = "") -> ActionResult:
    problem = doc_problem(slug, kind, title, body, client_text)
    if problem:
        return ActionResult(False, f"refused: {problem}")
    _, missing = await _site_or_problem(slug)
    if missing:
        return ActionResult(False, f"refused: {missing}")
    return ActionResult(
        True, f"would store a {KIND_LABEL[kind]} draft for {slug} "
              f"(\"{title.strip()}\", {len(body.strip())} characters) and raise "
              f"its {PUBLISH} card; nothing is published until the owner approves it")


async def draft_doc(slug: str, kind: str, title: str, body: str,
                    client_text: str = "") -> ActionResult:
    """ATLAS's own row and a card. Nothing leaves ATLAS here."""
    problem = doc_problem(slug, kind, title, body, client_text)
    if problem:
        return ActionResult(False, f"refused: {problem}")
    _, missing = await _site_or_problem(slug)
    if missing:
        return ActionResult(False, f"refused: {missing}")
    title, body = title.strip(), body.strip()
    did = await _insert_doc(slug, kind, title, body, client_text or "")
    from ...kernel.approvals import propose
    try:
        pid = await propose(
            PUBLISH, {"doc_id": str(did)}, _reason(kind, slug, title, body),
            rollback_plan="cancel the handoff before the coding agent claims it",
            dedup_key=f"sitedoc:{did}")
    except Exception as exc:                            # noqa: BLE001
        # No card, no draft: a row nobody can approve would wait forever.
        await _set_status(did, "withdrawn", was=("draft",))
        return ActionResult(False, f"document #{did} withdrawn: its publish card "
                                   f"could not be raised ({exc})")
    return ActionResult(
        True, f"drafted {KIND_LABEL[kind]} #{did} for {slug}; its publish card "
              f"is #{pid}",
        evidence={"doc_id": did, "proposal_id": pid}, rollback_token=str(did))


async def draft_doc_rollback(token: str) -> ActionResult:
    did = _did(token)
    if did is None or not await _set_status(did, "withdrawn", was=("draft",)):
        return ActionResult(False, f"document {token!r} is not a draft; nothing "
                                   f"withdrawn")
    return ActionResult(True, f"document #{did} withdrawn (kept as history)")


# ---------------------------------------------------------------- publishing

def _handoff(doc: dict[str, Any], site: dict[str, Any] | None) -> handoff.Handoff:
    kind = str(doc["kind"])
    title, slug = str(doc["title"]), str(doc["slug"])
    source = str((site or {}).get("source") or "").strip()
    evidence = "\n".join([
        f"TITLE: {title}",
        f"KIND: {KIND_LABEL.get(kind, kind)}",
        f"CLIENT SITE: {slug}",
        "",
        ("BODY (drafted by the ATLAS sites agent; publish it as the document's "
         "content, verbatim):"),
        str(doc["body"]),
    ])
    client = str(doc.get("client_text") or "").strip()
    if client:
        evidence += "\n\nCLIENT TEXT (the client's own words, quoted in the doc):\n" + client
    readme = (f"README.md of the client site's repo ({source}): one line naming "
              f"the Docs link" if source else "")
    did = int(doc["id"])
    # The instruction fields name the document by id only; its title and
    # body travel inside the wrapped EVIDENCE, where they are data.
    done = (f"a Claude Docs artifact for client-site document #{did} exists, "
            f"titled exactly as TITLE in the evidence and created with the "
            f"docs skill or connector, whose content is the BODY; the report "
            f"is sent with `--artifact-url <the claude.ai link>` so ATLAS records it "
            f"on the site's row")
    if source:
        done += f", and the README of {source} names that link on one line"
    return handoff.delegate(
        what_changed=(f"the owner approved publishing the {KIND_LABEL.get(kind, kind)} "
                      f"for client site {slug} as a Claude Docs artifact"),
        evidence=evidence,
        source="agency.publish_doc card",
        target="a new Claude Docs artifact (docs skill / connector) on this Mac",
        done_looks_like=done,
        tier=Tier.ASK,
        read_at=doc.get("created_at") or datetime.now(UTC),
        may_touch=tuple(filter(None, (f"one new Claude Docs artifact for document #{did}",
                                      readme))),
        may_not_touch=("the ATLAS repo and its worktrees", "the client's live site",
                       "DNS", "secrets", "any message or email to the client"),
    )


async def _publishable(doc_id: str) -> tuple[dict | None, str, str | None]:
    """(doc, rendered brief, problem)."""
    did = _did(doc_id)
    if did is None:
        return None, "", f"not a document id: {doc_id!r}"
    doc = await _load(did)
    if doc is None:
        return None, "", f"there is no document #{did}"
    if doc["status"] != "draft":
        return None, "", f"document #{did} is {doc['status']}, not a draft"
    site = await _store.get_site(str(doc["slug"]))
    brief = _handoff(doc, site).render()
    from .. import delegate
    if len(brief) > delegate.MAX_BRIEF:
        return None, "", (f"document #{did} is too long for one handoff "
                          f"({len(brief)} of {delegate.MAX_BRIEF} characters); "
                          f"split it into two documents")
    return doc, brief, None


async def publish_doc_dry(doc_id: str = "") -> ActionResult:
    _, brief, problem = await _publishable(doc_id)
    if problem:
        return ActionResult(False, f"refused: {problem}")
    return ActionResult(True, "would hand this to the coding agent:\n" + brief)


async def publish_doc(doc_id: str) -> ActionResult:
    """A handoff to the coding agent; ATLAS creates no artifact itself."""
    doc, brief, problem = await _publishable(doc_id)
    if problem or doc is None:
        return ActionResult(False, f"refused: {problem}")
    from .. import delegate
    did = int(doc["id"])
    hid = await delegate.draft(
        request=f"publish {doc['kind']} #{did} for {doc['slug']} as a Claude Docs "
                f"artifact",
        brief=brief, target="Claude Docs artifact", topic="decisions")
    queued = await delegate.queue(str(hid))
    if not queued.ok:
        return queued
    if not await _set_status(did, "queued", was=("draft",), handoff_id=hid):
        await delegate.cancel(str(hid))
        return ActionResult(False, f"document #{did} changed while it was being "
                                   f"queued; the handoff was cancelled")
    return ActionResult(True, queued.detail,
                        evidence={"handoff_id": hid, "doc_id": did},
                        rollback_token=str(hid))


async def publish_doc_rollback(token: str) -> ActionResult:
    """Cancel the handoff before it is claimed; the draft can go again."""
    from .. import delegate
    cancelled = await delegate.cancel(token)
    if not cancelled.ok:
        return cancelled
    hid = _did(token)
    if hid is not None:
        await _reopen(hid)
    return cancelled


async def _reopen(handoff_id: int) -> None:
    from ...kernel.state import fetch_one
    await fetch_one(
        """UPDATE agency_site_docs SET status = 'draft', handoff_id = NULL
            WHERE handoff_id = $1 AND status = 'queued' RETURNING id""", handoff_id)
