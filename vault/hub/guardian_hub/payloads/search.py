"""`GET /api/v1/search` -- the CMD-K palette.

Case-insensitive substring search across the tenant's devices (mac/ip/name/
label/vendor), hosts, sites, open alerts (key/msg), and the app's own
top-level routes. Tenant-scoped like every other payload builder, capped at
`MAX_RESULTS` so the palette never has to page.
"""

from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..schemas_dash import SearchPayload, SearchResult

MAX_RESULTS = 25
QUERY_LIMIT = 500     # bound each table scan, same hygiene as device_detail's alerts query

# Static top-level pages the palette can jump straight to.
ROUTES = [
    ("Overview", "/"), ("Assets", "/assets"), ("Inbox", "/inbox"),
    ("Updates", "/updates"), ("Reports", "/reports"), ("Settings", "/settings"),
]


def _match(q: str, *parts: str | None) -> bool:
    return q in " ".join(p or "" for p in parts).lower()


def search(db: DbSession, tenant, q: str) -> SearchPayload:
    """`q` empty or blank returns no results (there is nothing to rank a
    palette's default/empty state by)."""
    q = (q or "").strip().lower()
    results: list[SearchResult] = []
    if not q:
        return SearchPayload(results=results)

    for title, href in ROUTES:
        if len(results) >= MAX_RESULTS:
            return SearchPayload(results=results)
        if _match(q, title, href):
            results.append(SearchResult(kind="route", id=href, title=title, subtitle="", href=href))

    for d in db.query(m.Device).filter_by(tenant_id=tenant.id).limit(QUERY_LIMIT).all():
        if len(results) >= MAX_RESULTS:
            return SearchPayload(results=results)
        if _match(q, d.mac, d.ip, d.name, d.label, d.vendor):
            results.append(SearchResult(
                kind="device", id=d.mac, title=d.label or d.name or d.mac,
                subtitle=d.vendor or d.ip or "", href=f"/assets/devices/{d.mac}",
            ))

    # Host/Site have no natural mac/key like Device/Alert do; `name` is the
    # id used everywhere else they're addressed (ingest's upsert key,
    # Metric.entity), so it -- not the internal uid -- is what the palette
    # needs to deep-link and select the right row.
    for h in db.query(m.Host).filter_by(tenant_id=tenant.id).limit(QUERY_LIMIT).all():
        if len(results) >= MAX_RESULTS:
            return SearchPayload(results=results)
        if _match(q, h.name, h.address):
            results.append(SearchResult(
                kind="host", id=h.name, title=h.name, subtitle=h.address,
                href=f"/assets/hosts?row={h.name}",
            ))

    for s in db.query(m.Site).filter_by(tenant_id=tenant.id).limit(QUERY_LIMIT).all():
        if len(results) >= MAX_RESULTS:
            return SearchPayload(results=results)
        if _match(q, s.name, s.url):
            results.append(SearchResult(
                kind="site", id=s.name, title=s.name, subtitle=s.url,
                href=f"/assets/sites?row={s.name}",
            ))

    for a in (db.query(m.Alert).filter_by(tenant_id=tenant.id, status="open")
              .limit(QUERY_LIMIT).all()):
        if len(results) >= MAX_RESULTS:
            return SearchPayload(results=results)
        if _match(q, a.key, a.msg):
            results.append(SearchResult(
                kind="alert", id=a.key, title=a.msg or a.key, subtitle=a.sev,
                href=f"/inbox?row={a.key}",
            ))

    return SearchPayload(results=results[:MAX_RESULTS])
