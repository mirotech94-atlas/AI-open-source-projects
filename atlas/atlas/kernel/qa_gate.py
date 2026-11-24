"""The QA hard gate: nothing a gated action sends or publishes leaves without
a standing QA pass for that exact content.

Called twice, and both must hold:
- `approvals.propose()` -- no card is raised for content QA has not passed;
- `executor.execute()` -- one call immediately before the dry run, every
  tier, so a Tier 1 action is gated as hard as an approved Tier 2 one.

What is checked is the content itself. Each gated action has a SUBJECT
resolver that reads exactly what would go out (the stored draft, the text
the code renders, the built files) and hashes it. A pass is a `qa_results`
row with ok = true for that subject and that sha, not superseded: an edit
after the check changes the sha, and the old pass no longer counts.

Text subjects with no result yet are checked here and now by the
deterministic words check (modules/qa/words.py: no network, no model) and
the result is recorded. A site has no such shortcut: it needs a prior
`qa.check_site` row for the files about to go out.

Fail closed. No resolver, a resolver that cannot read the content, or any
error in the gate refuses. A refusal that QA found raises one remediation
task on the local board (deduped while it is open); one that could not be
read raises none (there is nothing for anybody to fix in the content).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .approvals import Unrunnable
from .registry import REGISTRY

log = logging.getLogger("atlas.qa_gate")

# Static: a name is gated when it is here AND registered. A later phase that
# adds one of these actions adds its resolver too (tests/test_qa_gate.py holds
# every registered gated name to having one).
GATED_NAMES: frozenset[str] = frozenset({
    "agency.send_outreach", "agency.send_agreement", "agency.send_invite",
    "agency.deploy_site", "agency.deploy_demo", "agency.publish_vercel",
    "church.schedule_youtube_shorts", "church.schedule_facebook_reels",
    "bizops.send_onboarding_email", "bizops.send_invoice",
    "bizops.send_payment_reminder",
    "seo.publish_blog_post", "seo.apply_change",
    "research.send_client_report",
    "academy.send_comment_reply",
    "reddit.submit_post", "reddit.submit_comment", "reddit.update_profile",
})

OWNER = "qa"
MAX_TREE_BYTES = 200 * 1024 * 1024


class QaRequired(Unrunnable):
    """A gated action has no standing QA pass for its content."""

    def __init__(self, action: str, spoken: str,
                 failures: list[str] | None = None) -> None:
        super().__init__(action, spoken)
        self.failures = list(failures or [])


@dataclass(frozen=True, slots=True)
class Subject:
    ref: str                       # qa_results.subject_ref
    sha: str                       # sha256 of exactly what would go out
    words_kind: str | None = None  # a text subject: the words-check kind
    text: str | None = None        # ... and its text (never stored)


Resolver = Callable[[dict[str, Any]], Awaitable[Subject | None]]


def gated(name: str) -> bool:
    return name in GATED_NAMES and name in REGISTRY.actions


def text_sha(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()


def _text_subject(ref: str, kind: str, text: str) -> Subject:
    return Subject(ref, text_sha(f"{kind}\0{text}"), kind, text)


def tree_sha(root: Path) -> str:
    """The sha256 of every file's relative path and bytes under `root`,
    sorted; links are hashed as links, never followed. Blocking: run it in a
    thread."""
    digest = hashlib.sha256()
    total = 0
    if not root.is_dir() or root.is_symlink():
        raise FileNotFoundError(f"{root} is not a directory")
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                digest.update(f"L\0{rel}\0{os.readlink(path)}\n".encode())
                continue
            total += path.stat().st_size
            if total > MAX_TREE_BYTES:
                raise ValueError(f"{root} is over {MAX_TREE_BYTES} bytes")
            digest.update(f"F\0{rel}\0".encode())
            digest.update(path.read_bytes())
            digest.update(b"\n")
    return digest.hexdigest()


# ------------------------------------------------------------- resolvers

async def _outreach(params: dict[str, Any]) -> Subject | None:
    from .state import fetch_one
    oid = int(params["outreach_id"])
    row = await fetch_one(
        "SELECT kind, to_address, subject, body FROM agency_outreach WHERE id = $1", oid)
    if row is None:
        return None
    kind = "sms" if str(row["kind"]) == "sms" else "outreach"
    text = f"{row['subject'] or ''}\n\n{row['body'] or ''}"
    return _text_subject(f"agency_outreach:{oid}", kind, text)


async def _agreement(params: dict[str, Any]) -> Subject | None:
    from ..modules.agency import replies
    preview = await replies.send_agreement_dry(**params)
    text = (preview.evidence or {}).get("text") if preview.ok else None
    if not text:
        return None
    kind = "sms" if str(params.get("channel")) == "sms" else "email"
    return _text_subject(f"agency_agreement:{params.get('lead_id')}", kind, str(text))


async def _invite(params: dict[str, Any]) -> Subject | None:
    from ..modules.agency import meetings
    preview = await meetings.send_invite_dry(int(params["meeting_id"]))
    text = (preview.evidence or {}).get("agenda") if preview.ok else None
    if not text:
        return None
    return _text_subject(f"agency_meeting:{params['meeting_id']}", "invite", str(text))


async def _church(params: dict[str, Any]) -> Subject | None:
    from .state import fetch_all
    ids = [int(i) for i in params.get("post_ids") or []]
    if not ids:
        return None
    rows = await fetch_all(
        """SELECT id, platform, title, description FROM church_posts
            WHERE id = ANY($1::bigint[]) ORDER BY id""", ids)
    if len(rows) != len(set(ids)):
        return None
    text = "\n\n".join(f"{r['title'] or ''}\n{r['description'] or ''}" for r in rows)
    return _text_subject("church_posts:" + ",".join(map(str, sorted(ids))), "post", text)


async def _reddit(params: dict[str, Any]) -> Subject | None:
    from .state import fetch_one
    did = int(params["draft_id"])
    row = await fetch_one("SELECT title, body FROM reddit_drafts WHERE id = $1", did)
    if row is None:
        return None
    text = f"{row['title'] or ''}\n\n{row['body'] or ''}"
    return _text_subject(f"reddit_drafts:{did}", "reddit", text)


async def _comment_reply(params: dict[str, Any]) -> Subject | None:
    from .state import fetch_one
    did = int(params["draft_id"])
    row = await fetch_one("SELECT reply FROM academy_comment_drafts WHERE id = $1", did)
    if row is None:
        return None
    return _text_subject(f"academy_comment_drafts:{did}", "post", str(row["reply"] or ""))


def _bizops(table: str, id_key: str, kind: str, columns: str) -> Resolver:
    """A resolver for one BizOps draft table (fixed names, never input)."""
    async def resolve(params: dict[str, Any]) -> Subject | None:
        from .state import fetch_one
        rid = int(params[id_key])
        row = await fetch_one(f"SELECT {columns} FROM {table} WHERE id = $1", rid)
        if row is None:
            return None
        text = "\n\n".join(str(row[c] or "") for c in columns.split(", "))
        return _text_subject(f"{table}:{rid}", kind, text)
    return resolve


_onboarding = _bizops("bizops_onboardings", "onboarding_id", "email", "subject, body")
_invoice = _bizops("bizops_invoices", "invoice_id", "report", "memo")
_reminder = _bizops("bizops_reminders", "reminder_id", "email", "subject, body")
_blog_post = _bizops("content_drafts", "draft_id", "post", "title, body")
_client_report = _bizops("research_client_reports", "report_id", "report",
                         "subject, body")


async def _seo_change(params: dict[str, Any]) -> Subject | None:
    """The words an on-page change would put on the site (seo_changes.spec)."""
    import json

    from .state import fetch_one
    cid = int(params["change_id"])
    row = await fetch_one("SELECT spec FROM seo_changes WHERE id = $1", cid)
    if row is None:
        return None
    spec = row["spec"]
    if isinstance(spec, str):
        spec = json.loads(spec)
    lines: list[str] = []
    for key in sorted(spec or {}):
        value = spec[key]
        if isinstance(value, dict):
            lines += [str(v) for _k, v in sorted(value.items())]
        else:
            lines.append(str(value))
    return _text_subject(f"seo_changes:{cid}", "post", "\n\n".join(lines))


async def site_subject(slug: str) -> tuple[Subject, dict[str, Any], Path] | None:
    """The built files `agency.deploy_site` / `agency.deploy_demo` would put
    live for `slug`: (subject, the agency_sites row, the directory)."""
    from ..modules.agency import _store
    site = await _store.get_site(str(slug))
    if site is None:
        return None
    if str(site.get("kind") or "built") == "demo":
        from ..modules.agency.demo_host import INTAKE_ROOT
        root, ref = INTAKE_ROOT / str(slug), f"demo:{slug}"
    else:
        root, ref = Path(str(site["path"])), f"site:{slug}"
    sha = await asyncio.to_thread(tree_sha, root)
    return Subject(ref, sha), site, root


async def _site(params: dict[str, Any]) -> Subject | None:
    got = await site_subject(str(params["slug"]))
    return got[0] if got is not None else None


RESOLVERS: dict[str, Resolver] = {
    "agency.send_outreach": _outreach,
    "agency.send_agreement": _agreement,
    "agency.send_invite": _invite,
    "agency.deploy_site": _site,
    "agency.deploy_demo": _site,
    "agency.publish_vercel": _site,
    "church.schedule_youtube_shorts": _church,
    "church.schedule_facebook_reels": _church,
    "academy.send_comment_reply": _comment_reply,
    "reddit.submit_post": _reddit,
    "reddit.submit_comment": _reddit,
    "reddit.update_profile": _reddit,
    "bizops.send_onboarding_email": _onboarding,
    "bizops.send_invoice": _invoice,
    "bizops.send_payment_reminder": _reminder,
    "seo.publish_blog_post": _blog_post,
    "seo.apply_change": _seo_change,
    "research.send_client_report": _client_report,
}


# ----------------------------------------------------------------- gate

async def remediate(action: str, subject_ref: str, sha: str,
                    failures: list[str]) -> int:
    """One open remediation task per (action, subject) on the local board."""
    from ..modules.projects import store
    existing = await store.open_remediation_for(action, subject_ref)
    if existing is not None:
        return existing
    tid, _token = await store.add_task(
        project_id=None, title=f"QA held {action} for {subject_ref}",
        kind="remediation", owner=OWNER, due_at=None,
        evidence={"subject_ref": subject_ref, "sha": sha, "failures": failures[:20]},
        suggested_fix="; ".join(failures[:5]) or "run the QA check again",
        source_action=action, source_ref=subject_ref)
    return tid


async def require(action: str, params: dict[str, Any]) -> int:
    """The id of the standing QA pass for what `action` would send, or raise
    QaRequired. Never returns without a passing row."""
    from ..modules.qa import store, words
    resolver = RESOLVERS.get(action)
    if resolver is None:
        raise QaRequired(action, f"QA has no way to read what {action} would send, "
                                 "so it is held")
    try:
        subject = await resolver(dict(params or {}))
    except Exception as exc:                            # noqa: BLE001
        log.warning("qa subject for %s unreadable: %s", action, exc)
        subject = None
    if subject is None:
        raise QaRequired(action, f"QA could not read what {action} would send, "
                                 "so it is held")
    standing = await store.latest(subject.ref, subject.sha)
    if standing is not None and standing.get("ok"):
        return int(standing["id"])
    if standing is not None:
        failures = [str(f) for f in standing.get("failures") or []]
    elif subject.text is not None and subject.words_kind is not None:
        failures = words.refusals(subject.text, subject.words_kind)
        rid = await store.record("draft", subject.ref, subject.sha, not failures,
                                 failures, {"action": action,
                                            "words_kind": subject.words_kind,
                                            "checks": ["words"]})
        if not failures:
            return rid
    else:
        failures = [f"no passing qa.check_site for these files (sha {subject.sha[:12]})"]
    await remediate(action, subject.ref, subject.sha, failures)
    raise QaRequired(action, "QA held it: " + "; ".join(failures[:5]), failures)
