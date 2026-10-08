"""One-shot importer: copy a Nexus hub's on-disk state into Vault.

Expected `src_dir` layout (a filesystem copy of /opt/nexus-hub):

    hub.json                         {"users": {username: {tenant, role}},
                                       "tenants": {<nexus id>: {name,
                                       ingest_token, ntfy_topic, brand?}}}
    tenants/<id>/config.json         Nexus tenant config (sites, ssh_hosts,
                                      roles, public_url, owner, ...)
    tenants/<id>/state.json          last known state.json
    tenants/<id>/last.json           last known last.json
    tenants/<id>/history.jsonl       history tail (one JSON object per line)
    tenants/<id>/events.jsonl        events tail
    tenants/<id>/remediation.jsonl   fixes tail
    tenants/<id>/audit.jsonl         Nexus audit trail (not imported; Vault
                                      keeps its own Audit table going forward)
    tenants/<id>/reports/*.html      rendered report snapshots

`tenant_map` maps a Nexus tenant id -> the Vault tenant slug to import it
as, e.g. {"myron": "home", "demo": "demo"}.

Security: `ingest_token` from hub.json is a live sensor credential for the
OLD Nexus deployment. It is read (to know which keys to skip) but is NEVER
written into any Vault row, file, or log line.
"""

import datetime as dt
import json
import pathlib
import shutil

from sqlalchemy.orm import Session as DbSession

from . import models as m
from .accounts import upsert_membership, upsert_user
from .config import settings
from .db import SessionLocal
from .ingest import store_scan

_now = lambda: dt.datetime.now(dt.timezone.utc)

# Keys from Nexus config.json that must not be copied onto Tenant.config:
# `roles` becomes Vault Membership rows, `public_url` is Nexus-specific.
_CONFIG_DROP_KEYS = {"roles", "public_url"}

# Keys on the hub.json tenant record that we do NOT copy anywhere -
# `ingest_token` is a live credential for the old Nexus sensor and must
# never be persisted into Vault.
_TENANT_SECRET_KEYS = {"ingest_token"}


def _read_json(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    text = path.read_text().strip()
    return json.loads(text) if text else {}


def _read_jsonl(path: pathlib.Path) -> tuple[list[dict], int]:
    """Parse a jsonl file. A line that fails to parse is skipped (and
    counted) rather than aborting the whole import -- one corrupt line in a
    Nexus export must never block importing everything else."""
    if not path.exists():
        return [], 0
    out: list[dict] = []
    skipped = 0
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            skipped += 1
    return out, skipped


def _report_kind(filename: str) -> tuple[str, int]:
    if filename.endswith("-weekly.html"):
        return "weekly", 7
    if filename.endswith("-monthly.html"):
        return "monthly", 30
    return "manual", 0


def _upsert_tenant(db: DbSession, slug: str, nexus_tenant: dict, config: dict) -> m.Tenant:
    tenant = db.query(m.Tenant).filter_by(slug=slug).first()
    if not tenant:
        tenant = m.Tenant(slug=slug, name=nexus_tenant.get("name") or slug)
        db.add(tenant)
        db.flush()
    tenant.name = nexus_tenant.get("name") or tenant.name
    # brand/ntfy_topic are copied verbatim; ingest_token is deliberately
    # never read into anything we persist.
    tenant.brand = nexus_tenant.get("brand") or {}
    tenant.ntfy_topic = nexus_tenant.get("ntfy_topic") or ""
    tenant.config = {k: v for k, v in config.items() if k not in _CONFIG_DROP_KEYS}
    tenant.settings = {"profile": {"owner": config.get("owner", "")}}
    db.commit()
    return tenant


def _upsert_user_membership(db: DbSession, username: str, info: dict, tenant: m.Tenant, missing_emails: list[str]) -> None:
    username = username.strip().lower()
    email = f"{username}@invalid.local"
    is_new = db.query(m.User).filter_by(username=username).first() is None
    if is_new:
        missing_emails.append(username)
    user = upsert_user(db, username, email, display_name=username)
    role = info.get("role") or "viewer"
    upsert_membership(db, user, tenant, role)
    db.commit()


def _import_reports(db: DbSession, tenant: m.Tenant, slug: str, reports_dir: pathlib.Path) -> None:
    if not reports_dir.exists():
        return
    dest_dir = settings.data_dir / "reports" / slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    for src_file in sorted(reports_dir.glob("*.html")):
        dest_file = dest_dir / src_file.name
        shutil.copy2(src_file, dest_file)
        path_str = str(dest_file)
        if db.query(m.Report).filter_by(tenant_id=tenant.id, path=path_str).first():
            continue
        kind, period_days = _report_kind(src_file.name)
        db.add(m.Report(tenant_id=tenant.id, kind=kind, period_days=period_days, path=path_str))
    db.commit()



#: Product-name fixes for labels the one-shot import carried over from the
#: predecessor deployment. These are the tenant's own site names -- they show
#: on the Overview health strip, the Security scorecards and the public status
#: page, so a stale product name in one is a stale product name on a
#: client-facing screen. Prefix rename only: everything after the prefix (the
#: hostname in brackets) is the tenant's, not ours.
#: Every superseded spelling, not just the most recent one: a tenant
#: relabelled under the PREVIOUS rename holds "Guardian public" rows, and
#: dropping that leg means `guardian migrate` can never correct them -- on
#: this deploy or any future one.
STALE_SITE_LABEL_PREFIXES = {"Nexus public": "Vault public",
                             "Guardian public": "Vault public"}


def _relabelled(name: str) -> str | None:
    """The corrected label for `name`, or None if it needs no correction."""
    for old, new in STALE_SITE_LABEL_PREFIXES.items():
        if name == old or name.startswith(old + " "):
            return new + name[len(old):]
    return None


def relabel_imported_sites(db: DbSession) -> int:
    """Rename stale imported site labels for EVERY tenant, everywhere a site
    name is a key. Label only -- no url, row id, value or timestamp changes,
    so nothing that keys on the site's IDENTITY is disturbed.

    A site's name is its key in four places, and fixing fewer than all four
    makes the rename worse than not doing it:

    1. `Site.name` -- what every read path renders.
    2. `Tenant.config["sites"][].name` -- what the hub replays to the sensor,
       and what the next ingest would re-create the `Site` row from. Renaming
       only (1) means a second row under the old name within one scan.
    3. `Metric.entity` -- the site's history series (`payloads/overview.py`,
       `routers/public.py`, `reports/builder.py` all look it up BY NAME), so
       an un-renamed series shows the renamed site an empty 24h health strip
       and drops it out of its own report.
    4. `Alert.key` (`site:<name>:<kind>`) and the name inside `Alert.msg`.
       Once the sensor emits the new name the old key is never seen again --
       and an alert whose key stops arriving is never resolved, so it would
       sit open in the Inbox under the stale name forever.

    Each of the four is fixed independently of the others, so a database
    where an earlier run already renamed the `Site` row still gets its
    metrics and alerts corrected. Idempotent; returns the number of things
    changed. Run from `guardian migrate` (so a deploy applies it) as well as
    at the end of an import.
    """
    changed = 0
    # (tenant_id, old, new) for every stale name found ANYWHERE, not only on
    # a Site row that still needs renaming.
    renames: set[tuple[str, str, str]] = set()

    def note(tenant_id: str, name: str) -> str | None:
        new = _relabelled(name or "")
        if new:
            renames.add((tenant_id, name, new))
        return new

    for row in db.query(m.Site).all():
        new = note(row.tenant_id, row.name or "")
        if new:
            row.name = new
            changed += 1

    for tenant in db.query(m.Tenant).all():
        cfg = dict(tenant.config or {})
        sites = cfg.get("sites")
        if not isinstance(sites, list):
            continue
        touched = False
        out = []
        for entry in sites:
            if isinstance(entry, dict):
                new = note(tenant.id, str(entry.get("name") or ""))
                if new:
                    entry = {**entry, "name": new}
                    touched = True
                    changed += 1
            out.append(entry)
        if touched:
            cfg["sites"] = out
            tenant.config = cfg          # rebind: JSON columns need a new object

    for tenant_id, entity in db.query(m.Metric.tenant_id, m.Metric.entity).distinct().all():
        note(tenant_id, entity or "")

    for tenant_id, key in db.query(m.Alert.tenant_id, m.Alert.key).all():
        if (key or "").startswith("site:"):
            # `site:<name>:<kind>` -- the kind is the LAST segment, so the
            # name is everything between, which is what a site name with a
            # colon in it (none today, but "host:port" labels are legal)
            # needs.
            body = key[len("site:"):]
            if ":" in body:
                note(tenant_id, body.rsplit(":", 1)[0])

    for tenant_id, old_name, new_name in sorted(renames):
        changed += db.query(m.Metric).filter(
            m.Metric.tenant_id == tenant_id, m.Metric.entity == old_name,
        ).update({m.Metric.entity: new_name}, synchronize_session=False)

        old_prefix, new_prefix = f"site:{old_name}:", f"site:{new_name}:"
        for al in db.query(m.Alert).filter(
            m.Alert.tenant_id == tenant_id, m.Alert.key.like(old_prefix + "%"),
        ).all():
            target = new_prefix + al.key[len(old_prefix):]
            clash = (db.query(m.Alert)
                     .filter(m.Alert.tenant_id == tenant_id, m.Alert.key == target)
                     .first())
            if clash is not None:
                # `UniqueConstraint(tenant_id, key)`: a scan already created
                # the new-name row, so that one is live and this is the stale
                # duplicate. Drop it rather than fail the whole relabel.
                db.delete(al)
            else:
                al.key = target
            if al.msg and old_name in al.msg:
                al.msg = al.msg.replace(old_name, new_name)
            changed += 1

    if changed:
        db.commit()
    return changed


def import_nexus(src_dir: str, tenant_map: dict[str, str]) -> dict:
    """Import a Nexus hub export into Vault. Returns a summary dict.

    Safe to run more than once: tenants/users/memberships are looked up by
    slug/username first, and store_scan()/report inserts are idempotent on
    their own natural keys, so re-running does not duplicate rows.
    """
    src = pathlib.Path(src_dir)
    hub = _read_json(src / "hub.json")
    nexus_users: dict = hub.get("users") or {}
    nexus_tenants: dict = hub.get("tenants") or {}

    db = SessionLocal()
    try:
        missing_emails: list[str] = []
        summary = {"tenants": 0, "users": 0, "reports": 0, "skipped_lines": 0}

        for nexus_id, slug in tenant_map.items():
            nexus_tenant = dict(nexus_tenants.get(nexus_id) or {})
            for k in _TENANT_SECRET_KEYS:
                nexus_tenant.pop(k, None)

            tdir = src / "tenants" / nexus_id
            config = _read_json(tdir / "config.json")

            tenant = _upsert_tenant(db, slug, nexus_tenant, config)
            summary["tenants"] += 1

            for username, info in nexus_users.items():
                if info.get("tenant") != nexus_id:
                    continue
                _upsert_user_membership(db, username, info, tenant, missing_emails)
                summary["users"] += 1

            events, events_skipped = _read_jsonl(tdir / "events.jsonl")
            history, history_skipped = _read_jsonl(tdir / "history.jsonl")
            fixes, fixes_skipped = _read_jsonl(tdir / "remediation.jsonl")
            summary["skipped_lines"] += events_skipped + history_skipped + fixes_skipped

            payload = {
                "last": _read_json(tdir / "last.json"),
                "state": _read_json(tdir / "state.json"),
                "events": events,
                "history": history,
                "fixes": fixes,
                "dns_queries": [],
            }
            store_scan(db, tenant, None, payload, deliver=False)

            _import_reports(db, tenant, slug, tdir / "reports")
            summary["reports"] += 1

        relabelled = relabel_imported_sites(db)
        if relabelled:
            print(f"relabelled {relabelled} stale site label(s)")

        if missing_emails:
            print(
                "WARNING: no email on record for imported user(s), set real "
                "emails: " + ", ".join(sorted(set(missing_emails)))
            )

        return summary
    finally:
        db.close()
