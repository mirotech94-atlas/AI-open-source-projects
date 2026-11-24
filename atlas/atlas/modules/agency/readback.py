"""Read-back for `agency.send_outreach` (RA-D1).

SMTP answers "accepted" and nothing else: there is no id to look up and no
API to read. What can be read is the sending mailbox's own Sent folder, over
IMAP, when one is configured:

  AGENCY_IMAP_HOST     the sending mailbox's IMAP host (never derived from
                       the SMTP host: a relay is often not a mailbox)
  AGENCY_IMAP_PORT     default 993 (TLS)
  AGENCY_SENT_FOLDER   default "Sent"
  credentials          AGENCY_SMTP_USER / AGENCY_SMTP_PASSWORD, the same
                       mailbox the send authenticated as

The folder is opened read-only (EXAMINE), searched by the Message-ID the run
stamped on the message, and headers are fetched only with BODY.PEEK, so not
even a \\Seen flag changes. Found means verified. Not found is unknown, not
contradicted: a relay need not keep a sent copy. With no IMAP host the
result is unknown, class disabled.

The outreach row's own `status='sent'` is the run's own write and never
grounds for verified; a row that has since read `bounced` is contradicted.
See docs/verification.md.

Below it: `agency.deploy_site` (the public page's <title>), then the local
writes -- prospect, audit, rank, mockup, build_site, draft_outreach,
suppress -- each read back by SELECT on the id the run recorded and, for
files, a stat/hash of the path it recorded.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import re
from datetime import datetime
from email.header import decode_header, make_header
from pathlib import Path
from typing import Any

import httpx

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    classify,
    combine,
    contradicted,
    reconciler,
    unknown,
    verified,
    verifier,
)
from . import _parse_sites_env, _store, deploy
from .build import index_sha
from .score import scores_sha

log = logging.getLogger("atlas.agency.readback")

NO_IMAP = ("SMTP has no read-back and no AGENCY_IMAP_HOST is set to read the "
           "sending mailbox's Sent folder")


class NoImap(Exception):
    """No IMAP read of the sending mailbox is configured."""


def _imap_cfg() -> tuple[str, int, str, str, str] | None:
    host = os.getenv("AGENCY_IMAP_HOST", "").strip()
    user = os.getenv("AGENCY_SMTP_USER", "").strip()
    password = os.getenv("AGENCY_SMTP_PASSWORD", "")
    if not (host and user and password):
        return None
    try:
        port = int(os.getenv("AGENCY_IMAP_PORT", "993"))
    except ValueError:
        port = 993
    return host, port, user, password, os.getenv("AGENCY_SENT_FOLDER", "Sent")


def _quote(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _search_sent(cfg: tuple[str, int, str, str, str], *, message_id: str = "",
                 to: str = "", since: datetime | None = None) -> list[dict[str, str]]:
    """Blocking; run in a thread. Read-only: EXAMINE, SEARCH, BODY.PEEK.
    Returns the matching messages' Message-ID and Subject headers."""
    import imaplib
    import ssl
    host, port, user, password, folder = cfg
    conn = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(),
                             timeout=25)
    try:
        conn.login(user, password)
        status, _ = conn.select(_quote(folder), readonly=True)
        if status != "OK":
            raise RuntimeError(f"the {folder!r} folder could not be opened")
        criteria: list[str] = []
        if message_id:
            criteria += ["HEADER", "Message-ID", _quote(message_id)]
        if to:
            criteria += ["TO", _quote(to)]
        if since is not None:
            criteria += ["SINCE", since.strftime("%d-%b-%Y")]
        status, found = conn.search(None, *criteria)
        if status != "OK":
            raise RuntimeError("the Sent folder could not be searched")
        out = []
        for num in (found[0] or b"").split()[:20]:
            status, parts = conn.fetch(
                num, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT)])")
            raw = b"".join(p[1] for p in parts or [] if isinstance(p, tuple))
            head: dict[str, str] = {}
            for line in raw.decode("utf-8", "replace").splitlines():
                name, _, value = line.partition(":")
                if name.strip().lower() in ("message-id", "subject"):
                    head[name.strip().lower()] = str(make_header(decode_header(
                        value.strip())))
            out.append(head)
        return out
    finally:
        try:
            conn.logout()
        except Exception as exc:  # noqa: BLE001
            log.debug("imap logout failed: %s", type(exc).__name__)


async def search_sent(**kw: Any) -> list[dict[str, str]]:
    cfg = _imap_cfg()
    if cfg is None:
        raise NoImap(NO_IMAP)
    return await asyncio.to_thread(_search_sent, cfg, **kw)


async def _row(outreach_id: Any) -> dict[str, Any] | None:
    from ...kernel import state
    try:
        oid = int(outreach_id)
    except (TypeError, ValueError):
        return None
    row = await state.fetch_one(
        "SELECT id, to_address, subject, status, sent_at FROM agency_outreach "
        "WHERE id = $1", oid)
    return dict(row) if row is not None else None


def _bounced(row: dict[str, Any] | None) -> Verification | None:
    if row is not None and row.get("status") == "bounced":
        return contradicted(f"outreach {row['id']} bounced", outreach_id=row["id"],
                            status="bounced")
    return None


@verifier("agency.send_outreach")
async def verify_send(params: dict[str, Any], result: ActionResult) -> Verification:
    message_id = str((result.evidence or {}).get("message_id") or "")
    if not message_id:
        return unknown("the run recorded no Message-ID")
    row = await _row(params.get("outreach_id"))
    if (bad := _bounced(row)) is not None:
        return bad
    try:
        found = await search_sent(message_id=message_id)
    except NoImap as exc:
        return unknown(str(exc), FailureClass.DISABLED, message_id=message_id)
    evidence = {"message_id": message_id, "outreach_id": params.get("outreach_id")}
    hit = [h for h in found if h.get("message-id", "").strip() == message_id]
    if hit:
        return verified(in_sent_folder=True, subject=hit[0].get("subject"), **evidence)
    return unknown("the message is not in the Sent folder; the relay may not keep "
                   "a sent copy", **evidence)


@reconciler("agency.send_outreach")
async def reconcile_send(params: dict[str, Any], started: datetime) -> Verification:
    row = await _row(params.get("outreach_id"))
    if row is None or not row.get("to_address"):
        return unknown("the start row names no outreach with an address",
                       FailureClass.VALIDATION)
    if (bad := _bounced(row)) is not None:
        return bad
    try:
        found = await search_sent(to=str(row["to_address"]), since=started)
    except NoImap as exc:
        return unknown(str(exc), FailureClass.DISABLED, outreach_id=row["id"])
    subject = str(row.get("subject") or "").strip()
    hits = [h for h in found if h.get("subject", "").strip() == subject]
    if len(hits) == 1:
        return verified(in_sent_folder=True, outreach_id=row["id"],
                        message_id=hits[0].get("message-id"))
    if len(hits) > 1:
        return unknown(f"{len(hits)} matching sent messages; a person should pick",
                       outreach_id=row["id"])
    return unknown("no sent copy found; the relay may not keep one",
                   outreach_id=row["id"], row_status=row.get("status"))


# ---------------------------------------------------------- agency.deploy_site
# (read-back for the deploy; merged from branch ra-d3-0929)

_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_MAX_BODY = 512 * 1024


def title_of(markup: str) -> str:
    m = _TITLE.search(markup or "")
    return " ".join(html.unescape(m.group(1)).split()) if m else ""


def _expected_title(slug: str, built_path: str | None) -> str:
    """Blocking; run through `asyncio.to_thread`."""
    candidates = [deploy.RELEASES_DIR / slug / "current" / "index.html"]
    if built_path:
        candidates.append(Path(built_path) / "index.html")
    for index in candidates:
        try:
            if index.is_file():
                found = title_of(index.read_text(errors="replace")[:_MAX_BODY])
                if found:
                    return found
        except OSError:
            continue
    return ""


async def _domain(slug: str) -> tuple[str | None, str | None]:
    """(domain, built path) from agency_sites, else AGENCY_SITES."""
    site = await _store.get_site(slug)
    if site:
        return deploy.published_url(site), site.get("path")
    for s, domain in _parse_sites_env(os.getenv("AGENCY_SITES", "")):
        if s == slug:
            return domain, None
    return None, None


@verifier("agency.publish_vercel")     # the row's domain is the Vercel host
@verifier("agency.deploy_site")
async def verify_deploy(params: dict[str, Any], result: ActionResult) -> Verification:
    slug = str(params.get("slug") or (result.evidence or {}).get("slug") or "")
    if not slug:
        return unknown("the run names no site", FailureClass.VALIDATION)
    domain, built = await _domain(slug)
    if not domain:
        return unknown(f"no domain on record for site {slug!r}; nothing public "
                       f"to read back", slug=slug)
    expected = await asyncio.to_thread(_expected_title, slug, built)
    url = domain if domain.startswith("https://") else f"https://{domain.strip('/')}/"
    evidence = {"slug": slug, "url": url, "expected_title": expected or None}
    if not expected:
        return unknown("the deployed release has no <title> to look for", **evidence)
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get(url)
    except httpx.HTTPError as exc:
        return unknown(f"{url} did not answer", classify(exc) or FailureClass.TRANSIENT,
                       **evidence)
    evidence["status"] = r.status_code
    if r.status_code >= 500 or r.status_code == 429:
        return unknown(f"{url} answered HTTP {r.status_code}", FailureClass.TRANSIENT,
                       **evidence)
    if r.status_code != 200:
        return contradicted(f"{url} answered HTTP {r.status_code}, not 200", **evidence)
    seen = title_of(r.text[:_MAX_BODY])
    evidence["title"] = seen or None
    if seen == expected:
        return verified(**evidence)
    return contradicted(f"{url} serves the title {seen!r}, not the deployed "
                        f"{expected!r}", **evidence)


# ------------------------------------------------ the local writes (RA-D1)
# prospect, audit, rank, mockup, build_site, draft_outreach, suppress: each
# writes rows in this box's own database and, for mockup and build_site, a
# file on this box's disk. The read-back is a SELECT by the id the run
# recorded, plus a stat/read of the file at the path the run recorded. Never
# a `_store` helper (some of them write), never a mkdir. None of these reads
# needs a credential, so none is gated as disabled: the Places key and the
# GetLayers licence gate the run, not a SELECT. Evidence carries ids, counts,
# statuses and short hashes, never lead names, addresses or audit text.


def _sha12(data: str | bytes) -> str:
    raw = data.encode() if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()[:12]


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def _select_one(sql: str, *args: Any) -> dict[str, Any] | None:
    from ...kernel import state
    row = await state.fetch_one(sql, *args)
    return dict(row) if row is not None else None


async def _select_all(sql: str, *args: Any) -> list[dict[str, Any]]:
    from ...kernel import state
    return [dict(r) for r in await state.fetch_all(sql, *args) or []]


def _file_size(path: str) -> int | None:
    """Blocking; run in a thread. The size of a regular file, else None."""
    try:
        p = Path(path)
        return p.stat().st_size if p.is_file() else None
    except OSError:
        return None


async def _file_leg(path: str, what: str) -> Verification:
    size = await asyncio.to_thread(_file_size, path)
    if size is None:
        return contradicted(f"{what} {path} is not on disk", path=path)
    if size == 0:
        return contradicted(f"{what} {path} is empty", FailureClass.QUALITY,
                            path=path, size=0)
    return verified(path=path, size=size)


# ------------------------------------------------------------ agency.prospect

@verifier("agency.prospect")
async def verify_prospect(params: dict[str, Any], result: ActionResult
                          ) -> Verification:
    raw = (result.evidence or {}).get("lead_ids")
    if raw is None:
        raw = [x for x in str(result.rollback_token or "").split(",") if x]
    ids = [i for i in (_int(x) for x in raw or []) if i is not None]
    if not ids:
        return unknown("the run inserted no new leads (every result was already "
                       "one); nothing of this run's to read back")
    rows = await _select_all(
        "SELECT id, status FROM agency_leads WHERE id = ANY($1)", ids)
    found = {int(r["id"]): r.get("status") for r in rows}
    missing = [i for i in ids if i not in found]
    evidence = {"lead_ids": ids, "found": len(found)}
    if missing:
        return contradicted(f"{len(missing)} of {len(ids)} recorded leads are not "
                            f"in agency_leads: {missing[:10]}", missing=missing,
                            **evidence)
    dropped = [i for i, s in found.items() if s == "dropped"]
    if dropped:
        return contradicted(f"{len(dropped)} recorded lead(s) read 'dropped' "
                            f"(rolled back): {dropped[:10]}", dropped=dropped,
                            **evidence)
    return verified(statuses=sorted({str(s) for s in found.values()}), **evidence)


# --------------------------------------------------------------- agency.audit

@verifier("agency.audit")
async def verify_audit(params: dict[str, Any], result: ActionResult) -> Verification:
    ev = result.evidence or {}
    audit_id = _int(ev.get("audit_id"))
    if audit_id is None:
        audit_id = _int(result.rollback_token)
    if audit_id is None:
        return unknown("the run recorded no audit id (served from cache before "
                       "ids were recorded, or nothing written)")
    row = await _select_one(
        "SELECT id, lead_id, severity FROM agency_audits WHERE id = $1", audit_id)
    evidence = {"audit_id": audit_id, "lead_id": _int(params.get("lead_id"))}
    if row is None:
        return contradicted(f"audit {audit_id} is not in agency_audits", **evidence)
    severity = _int(row.get("severity"))
    evidence["severity"] = severity
    if _int(row.get("lead_id")) != evidence["lead_id"]:
        return contradicted(f"audit {audit_id} belongs to lead {row.get('lead_id')}, "
                            f"not {params.get('lead_id')}", FailureClass.VALIDATION,
                            **evidence)
    if severity is not None and severity < 0:
        return contradicted(f"audit {audit_id} was withdrawn (rolled back)",
                            **evidence)
    expected = _int(ev.get("severity"))
    if expected is not None and severity != expected:
        return contradicted(f"audit {audit_id} reads severity {severity}, the run "
                            f"reported {expected}", **evidence)
    # An empty rollback token with an id means the run served an earlier
    # run's row from the 30-day cache rather than writing one.
    return verified(served_from_cache=not result.rollback_token, **evidence)


# ---------------------------------------------------------------- agency.rank

@verifier("agency.rank")
async def verify_rank(params: dict[str, Any], result: ActionResult) -> Verification:
    ev = result.evidence or {}
    try:
        previous = json.loads(result.rollback_token or "{}")
    except ValueError:
        previous = None
    if not isinstance(previous, dict):
        return unknown("the run's record of which leads it ranked is unreadable")
    ids = sorted(i for i in (_int(k) for k in previous) if i is not None)
    if not ids:
        return unknown("the run ranked no leads; nothing to read back",
                       updated=ev.get("updated"))
    expected = str(ev.get("scores_sha") or "")
    rows = await _select_all(
        "SELECT id, score FROM agency_leads WHERE id = ANY($1)", ids)
    scores = {int(r["id"]): r.get("score") for r in rows}
    evidence = {"ranked": len(ids), "found": len(scores)}
    missing = [i for i in ids if i not in scores]
    if missing:
        return contradicted(f"{len(missing)} ranked lead(s) are gone from "
                            f"agency_leads: {missing[:10]}", missing=missing,
                            **evidence)
    unscored = [i for i, s in scores.items() if s is None]
    if unscored:
        return contradicted(f"{len(unscored)} ranked lead(s) have no score: "
                            f"{unscored[:10]}", unscored=unscored, **evidence)
    if not expected:
        return unknown("the run recorded no score hash to compare; scores are "
                       "present but may be an earlier run's", **evidence)
    seen = scores_sha(scores)
    evidence.update(scores_sha=seen, expected_sha=expected)
    if seen != expected:
        return contradicted("the stored scores differ from what the run computed",
                            **evidence)
    return verified(**evidence)


# -------------------------------------------------------------- agency.mockup

@verifier("agency.mockup")
async def verify_mockup(params: dict[str, Any], result: ActionResult) -> Verification:
    ev = result.evidence or {}
    try:
        token = json.loads(result.rollback_token or "{}")
    except ValueError:
        token = None
    token = token if isinstance(token, dict) else {}
    mockup_id = _int(token.get("mockup_id"))
    path = str(ev.get("path") or token.get("html_path") or "")
    if not path:
        return unknown("the run recorded no mockup path")
    legs: dict[str, Verification] = {"file": await _file_leg(path, "the mockup")}
    if mockup_id is None:
        legs["row"] = unknown("the run recorded no agency_mockups id")
    else:
        row = await _select_one(
            "SELECT id, lead_id, path FROM agency_mockups WHERE id = $1", mockup_id)
        lead_id = _int(params.get("lead_id"))
        if row is None:
            legs["row"] = contradicted(f"mockup {mockup_id} is not in agency_mockups",
                                       mockup_id=mockup_id)
        elif _int(row.get("lead_id")) != lead_id or str(row.get("path")) != path:
            legs["row"] = contradicted(
                f"mockup {mockup_id} names lead {row.get('lead_id')} at "
                f"{row.get('path')}, not lead {lead_id} at {path}",
                mockup_id=mockup_id)
        else:
            legs["row"] = verified(mockup_id=mockup_id, lead_id=lead_id)
    return combine(legs)


# ---------------------------------------------------------- agency.build_site

@verifier("agency.build_site")
async def verify_build(params: dict[str, Any], result: ActionResult) -> Verification:
    ev = result.evidence or {}
    slug = str(ev.get("slug") or "")
    path = str(ev.get("path") or "")
    if not (slug and path):
        return unknown("the run recorded no site slug and path")
    row = await _select_one(
        "SELECT id, lead_id, slug, path FROM agency_sites WHERE slug = $1", slug)
    lead_id = _int(params.get("lead_id"))
    legs: dict[str, Verification] = {}
    if row is None:
        legs["row"] = contradicted(f"no agency_sites row for {slug!r}", slug=slug)
    elif _int(row.get("lead_id")) != lead_id or str(row.get("path")) != path:
        legs["row"] = contradicted(
            f"site {slug!r} names lead {row.get('lead_id')} at {row.get('path')}, "
            f"not lead {lead_id} at {path}", slug=slug)
    else:
        legs["row"] = verified(site_id=row.get("id"), slug=slug, lead_id=lead_id)
    index = str(Path(path) / "index.html")
    legs["index"] = await _file_leg(index, "the built page")
    expected = str(ev.get("index_sha") or "")
    if legs["index"].state == "verified":
        seen = await asyncio.to_thread(index_sha, Path(path))
        if not expected:
            legs["hash"] = unknown("the run recorded no index.html hash",
                                   index_sha=seen)
        elif seen != expected:
            legs["hash"] = contradicted(
                f"{index} changed since the build (sha {seen}, built {expected})",
                FailureClass.STALE, index_sha=seen, expected_sha=expected)
        else:
            legs["hash"] = verified(index_sha=seen)
    return combine(legs)


# ------------------------------------------------------ agency.draft_outreach

@verifier("agency.draft_outreach")
async def verify_draft(params: dict[str, Any], result: ActionResult) -> Verification:
    ev = result.evidence or {}
    outreach_id = _int(ev.get("outreach_id"))
    if outreach_id is None:
        outreach_id = _int(result.rollback_token)
    if outreach_id is None:
        return unknown("the run recorded no outreach id")
    row = await _select_one(
        "SELECT id, lead_id, kind, status, body FROM agency_outreach WHERE id = $1",
        outreach_id)
    evidence: dict[str, Any] = {"outreach_id": outreach_id}
    if row is None:
        return contradicted(f"outreach draft {outreach_id} is not in agency_outreach",
                            **evidence)
    evidence.update(status=row.get("status"), kind=row.get("kind"),
                    body_sha=_sha12(str(row.get("body") or "")))
    lead_id, kind = _int(params.get("lead_id")), params.get("kind")
    if _int(row.get("lead_id")) != lead_id or (kind and row.get("kind") != kind):
        return contradicted(f"outreach {outreach_id} is a {row.get('kind')!r} for "
                            f"lead {row.get('lead_id')}, not a {kind!r} for lead "
                            f"{lead_id}", FailureClass.VALIDATION, **evidence)
    if row.get("status") != "draft":
        return contradicted(f"outreach {outreach_id} reads {row.get('status')!r}, "
                            f"not 'draft'", **evidence)
    expected = str(ev.get("body_sha") or "")
    if not expected:
        return unknown("the run recorded no body hash to compare", **evidence)
    if evidence["body_sha"] != expected:
        return contradicted(f"outreach {outreach_id}'s body differs from the draft "
                            f"the run wrote", expected_sha=expected, **evidence)
    return verified(**evidence)


# ------------------------------------------------------------ agency.suppress
# The address is a stranger's: only its hash goes in evidence.

def _address(params: dict[str, Any], result: ActionResult | None) -> str:
    token = str((result.rollback_token if result else "") or "")
    return (token or str(params.get("address") or "")).strip().lower()


async def _suppressed(address: str, reason: Any) -> tuple[Verification, bool]:
    """(verification, reason matched)."""
    row = await _select_one(
        "SELECT address, reason FROM agency_suppression WHERE address = $1", address)
    evidence = {"address_sha": _sha12(address)}
    if row is None:
        return contradicted("the address is not on the suppression list",
                            **evidence), False
    same = reason is None or str(row.get("reason") or "") == str(reason)
    return verified(suppressed=True, reason_matches=same, **evidence), same


@verifier("agency.suppress")
async def verify_suppress(params: dict[str, Any], result: ActionResult
                          ) -> Verification:
    address = _address(params, result)
    if not address:
        return unknown("the run recorded no address", FailureClass.VALIDATION)
    got, _same = await _suppressed(address, params.get("reason"))
    return got


@reconciler("agency.suppress")
async def reconcile_suppress(params: dict[str, Any], started: datetime
                             ) -> Verification:
    address = _address(params, None)
    if not address:
        return unknown("the start row names no address", FailureClass.VALIDATION)
    got, same = await _suppressed(address, params.get("reason"))
    if got.state == "verified" and not same:
        # ON CONFLICT DO UPDATE sets the reason: another reason means this run
        # never got as far as its write (the row was already there).
        return unknown("the address is suppressed, but under another reason; "
                       "this run's write is not visible", **got.evidence)
    return got
