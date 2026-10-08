"""Manual vulnerability scans: the rows the sensor has queued/run on
`/api/vuln-scan`, shaped for the Security page, and the targets a scan can be
pointed at (every SSH host, every site, every known device, or the whole LAN).
"""
from sqlalchemy.orm import Session as DbSession

from .. import models as m
from ..schemas_dash import VulnFinding, VulnPort, VulnScanRow, VulnScansPayload, VulnScanTarget

QUEUE_PATH = "/api/vuln-scan"
HISTORY_LIMIT = 40


def _row(q: m.Queue) -> VulnScanRow:
    body = q.body or {}
    res = q.result if isinstance(q.result, dict) else {}
    detail = res.get("detail") if isinstance(res.get("detail"), dict) else res
    findings = [VulnFinding(severity=str(f.get("severity") or "info"), title=str(f.get("title") or ""),
                            evidence=str(f.get("evidence") or ""), port=f.get("port"), fix=f.get("fix"),
                            cve=f.get("cve"), target=str(f.get("target") or ""))
                for f in (detail.get("findings") or []) if isinstance(f, dict)]
    ports = [VulnPort(port=int(p.get("port") or 0), service=str(p.get("service") or ""), banner=str(p.get("banner") or ""),
                      http=p.get("http"), tls_version=str(((p.get("tls") or {}).get("version")) or ""),
                      tls_verified=((p.get("tls") or {}).get("verified")))
             for p in (detail.get("ports") or []) if isinstance(p, dict)]
    return VulnScanRow(
        id=q.id, status=q.status, kind=str(detail.get("kind") or body.get("kind") or ""),
        target=str(detail.get("target") or body.get("target") or ""), profile=str(body.get("profile") or "quick"),
        who=q.who or "", queued_at=q.ts.isoformat() if q.ts else "",
        finished_at=q.finished_at.isoformat() if getattr(q, "finished_at", None) else None,
        duration_s=detail.get("duration_s"), score=detail.get("score"),
        summary={k: int(v) for k, v in (detail.get("summary") or {}).items()},
        findings=findings, ports=ports,
        error=str(res.get("out") or "") if q.status == "failed" else "",
    )


def targets(db: DbSession, tenant) -> list[VulnScanTarget]:
    out = [VulnScanTarget(kind="lan", target="lan", label="Whole Wi-Fi / LAN (quick, every known device)")]
    for h in db.query(m.Host).filter_by(tenant_id=tenant.id).order_by(m.Host.name):
        out.append(VulnScanTarget(kind="host", target=h.name, label=f"{h.name} · server ({h.address})"))
    for s in db.query(m.Site).filter_by(tenant_id=tenant.id).order_by(m.Site.name):
        out.append(VulnScanTarget(kind="site", target=s.name, label=f"{s.name} · website"))
    for d in db.query(m.Device).filter_by(tenant_id=tenant.id).order_by(m.Device.ip):
        if d.ip:
            out.append(VulnScanTarget(kind="device", target=d.ip, label=f"{d.label or d.name or d.vendor or d.mac} · {d.ip}"))
    return out


def build(db: DbSession, tenant) -> VulnScansPayload:
    rows = (db.query(m.Queue).filter_by(tenant_id=tenant.id, path=QUEUE_PATH)
            .order_by(m.Queue.ts.desc()).limit(HISTORY_LIMIT).all())
    return VulnScansPayload(scans=[_row(q) for q in rows], targets=targets(db, tenant))


def one(db: DbSession, tenant, qid: str) -> VulnScanRow | None:
    q = db.get(m.Queue, qid)
    if not q or q.tenant_id != tenant.id or q.path != QUEUE_PATH:
        return None
    return _row(q)
