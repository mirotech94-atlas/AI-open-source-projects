"""Reports API: library, on-demand build, live preview and expiring share
links. The scheduled weekly/monthly build lives in `scheduler.py`, which
reuses `builder.mint_share_token` (defined alongside `render_to_file`, so
both a manual share and a scheduled push mint tokens the same way) and this
module's `card_out` for anything that renders a `Report` row as JSON.
"""

import datetime as dt
import pathlib

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, conint
from sqlalchemy.orm import Session as DbSession

from ..config import settings
from ..db import get_db
from .. import audit, models as m, ratelimit, scheduler
from ..payloads import value as value_payload
from ..deps import require_role
from ..reports import builder, pdf, presets as presets_mod, sections as S
from .. import schemas_dash as sd

router = APIRouter(prefix="/api/v1", tags=["reports"])
_now = lambda: dt.datetime.now(dt.timezone.utc)

VIEWER = Depends(require_role("viewer"))
OPERATOR = Depends(require_role("operator"))
ADMIN = Depends(require_role("admin"))


def _ip(request: Request) -> str:
    return request.client.host if request.client else ""


def card_out(report: m.Report) -> dict:
    share_url = None
    if report.share_token and report.share_expires and report.share_expires > _now():
        share_url = f"{settings.base_url}/r/{report.share_token}"
    return {
        "id": report.id,
        "kind": report.kind,
        "period_days": report.period_days,
        "preset": report.preset,
        "title": report.title,
        "created": report.created.isoformat() if report.created else "",
        "score": (report.meta or {}).get("score"),
        "url": f"/api/v1/reports/{report.id}",
        "share_url": share_url,
        "share_expires": report.share_expires.isoformat() if report.share_expires else None,
    }


def _library(db: DbSession, tenant_id: str) -> list[dict]:
    rows = (
        db.query(m.Report)
        .filter_by(tenant_id=tenant_id)
        .order_by(m.Report.created.desc())
        .all()
    )
    return [card_out(r) for r in rows]


def _schedule_out(tenant: m.Tenant) -> dict:
    sched = (tenant.settings or {}).get("report_schedule") or {}
    default = sd.ReportSchedule()
    return {
        "weekly": sched.get("weekly", default.weekly),
        "monthly": sched.get("monthly", default.monthly),
        "preset": sched.get("preset", default.preset),
        "ntfy": sched.get("ntfy", default.ntfy),
        "hour": sched.get("hour", default.hour),
    }


@router.get("/reports", response_model=sd.ReportsPayload)
def list_reports(ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    return {
        "library": _library(db, tenant.id),
        # `SPECIAL_PRESETS` (currently just `exec_report`) appended after the
        # ordinary, section-subset presets -- see its own comment in
        # `reports/presets.py` for why it is a separate registry.
        "presets": [{"key": k, "label": label, "sections": keys}
                    for k, (label, keys) in {**presets_mod.PRESETS, **presets_mod.SPECIAL_PRESETS}.items()],
        "sections": [{"key": k, "title": S.SECTION_TITLES[k]} for k in S.SECTION_KEYS],
        "schedule": _schedule_out(tenant),
        "value": value_payload.build(db, tenant.id),
    }


@router.get("/reports/preview")
def preview_report(days: int = 7, preset: str = "", sections: str = "", ctx=VIEWER, db: DbSession = Depends(get_db)):
    """Freshly rendered HTML, never persisted -- what the builder's live
    preview iframe loads. Rate-limited to 20/min/tenant."""
    user, tenant, role = ctx
    if not ratelimit.allow(f"reports_preview:{tenant.id}", 20, 60):
        raise HTTPException(429, "too many preview requests")
    section_keys = [s for s in sections.split(",") if s] if sections else None
    # `toolbar=False`: the preview iframe is sandboxed without allow-scripts,
    # so the report's own inline `print()` button cannot work there anyway.
    html = builder.build_report(db, tenant, days=days, sections=section_keys, preset=preset or None,
                                toolbar=False)
    return Response(content=html, media_type="text/html")


class ReportBuildBody(BaseModel):
    # A report is a document someone reads and files, so a parameter this
    # endpoint does not understand must be an error rather than a silent
    # default. Asking for `period_days` (the name the report LIST returns)
    # instead of `days` produced a seven-day report labelled seven days, with
    # nothing anywhere saying the request had been ignored -- the reader gets a
    # document that answers a different question from the one that was asked.
    model_config = ConfigDict(extra="forbid")

    days: int = 7
    preset: str = ""
    sections: list[str] = Field(default_factory=list)
    title: str = ""


@router.post("/reports/build", response_model=sd.ReportCard)
def build_report(body: ReportBuildBody, request: Request, ctx=OPERATOR, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    report, _path = builder.render_to_file(
        db, tenant, days=body.days, sections=body.sections or None, title=body.title or None,
        preset=body.preset or None, kind="manual",
    )
    audit.log(db, tenant.id, user.username, role, "report.build", target=report.id, ip=_ip(request),
              body=body.model_dump())
    db.commit()
    return card_out(report)


@router.get("/reports/{report_id}")
def get_report(report_id: str, ctx=VIEWER, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    report = db.query(m.Report).filter_by(id=report_id, tenant_id=tenant.id).first()
    if not report:
        raise HTTPException(404, "report not found")
    html = pathlib.Path(report.path).read_text(encoding="utf-8")
    return Response(content=html, media_type="text/html")


# `/reports/{report_id}/pdf`, not `/reports/{report_id}.pdf`: a bare path
# parameter matches anything without a slash, so `abc.pdf` reaches
# `get_report` as the id and 404s. The extra segment cannot be swallowed by
# the plain route regardless of registration order -- the same trap the
# evidence endpoint hit against `{key:path}`.
@router.get("/reports/{report_id}/pdf")
def get_report_pdf(report_id: str, ctx=VIEWER, db: DbSession = Depends(get_db)):
    """The same frozen report as a PDF, rendered on first request and cached.

    503 rather than 500 when there is no browser on this hub: the report
    exists and the HTML link works, so "not available in this format here"
    is the truthful status, and it names the fix in the body.
    """
    user, tenant, role = ctx
    report = db.query(m.Report).filter_by(id=report_id, tenant_id=tenant.id).first()
    if not report or not pathlib.Path(report.path).is_file():
        raise HTTPException(404, "report not found")
    try:
        path = pdf.ensure_pdf(report.path)
    except pdf.PdfUnavailable as e:
        raise HTTPException(503, f"PDF rendering is not available on this hub: {e}")
    except RuntimeError as e:
        raise HTTPException(500, f"PDF render failed: {e}")
    stem = pathlib.Path(report.path).stem
    return Response(content=path.read_bytes(), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{stem}.pdf"'})


class ReportShareBody(BaseModel):
    hours: conint(ge=1, le=720) = 168
    # Task 58 (G5): the predecessor's `/api/report/push` -- send the freshly
    # minted link to the phone as well as returning it, so a report reaches
    # the reader without them copying a URL out of the dashboard. Off by
    # default: sharing a link and pushing it are different intents.
    push: bool = False


@router.post("/reports/{report_id}/share")
def share_report(report_id: str, body: ReportShareBody, request: Request, ctx=OPERATOR, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    report = db.query(m.Report).filter_by(id=report_id, tenant_id=tenant.id).first()
    if not report:
        raise HTTPException(404, "report not found")
    token = builder.mint_share_token(report, body.hours)
    audit.log(db, tenant.id, user.username, role, "report.share", target=report.id, ip=_ip(request),
              body={"hours": body.hours, "push": body.push})
    db.commit()
    pushed = False
    if body.push:
        # Same helper the scheduler's weekly/monthly push uses, so a manual
        # push and an automatic one look identical on the phone -- but handed
        # off in the background: a request must never block on an HTTP POST to
        # every enabled integration. It swallows its own delivery errors; the
        # link is returned either way, and `pushed` reports only that the send
        # was handed off (a maintenance window, for instance, suppresses the
        # delivery itself).
        scheduler._push_ntfy(db, tenant, report, token, background=True)
        pushed = True
    return {"share_url": f"{settings.base_url}/r/{token}", "pushed": pushed}


@router.delete("/reports/{report_id}")
def delete_report(report_id: str, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    report = db.query(m.Report).filter_by(id=report_id, tenant_id=tenant.id).first()
    if not report:
        raise HTTPException(404, "report not found")
    path = pathlib.Path(report.path)
    if path.is_file():
        path.unlink()
    audit.log(db, tenant.id, user.username, role, "report.delete", target=report.id, ip=_ip(request))
    db.delete(report)
    db.commit()
    return {"ok": True}


class ReportScheduleBody(BaseModel):
    weekly: bool = False
    monthly: bool = False
    preset: str = ""
    hour: conint(ge=0, le=23) = 8
    ntfy: bool = True


@router.post("/reports/schedule", response_model=sd.ReportSchedule)
def set_schedule(body: ReportScheduleBody, request: Request, ctx=ADMIN, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    settings_dict = dict(tenant.settings or {})
    settings_dict["report_schedule"] = body.model_dump()
    tenant.settings = settings_dict
    audit.log(db, tenant.id, user.username, role, "report.schedule", ip=_ip(request), body=body.model_dump())
    db.commit()
    return _schedule_out(tenant)


@router.get("/reports/{report_id}/deliveries")
def report_deliveries(report_id: str, ctx=VIEWER, db: DbSession = Depends(get_db)):
    _, tenant, _ = ctx
    if not db.query(m.Report).filter_by(id=report_id, tenant_id=tenant.id).first():
        raise HTTPException(404, "Report not found")
    rows = db.query(m.Delivery).filter_by(tenant_id=tenant.id, reference=report_id).order_by(m.Delivery.updated.desc()).limit(30).all()
    return {"deliveries": [{"channel": r.channel, "status": r.status, "detail": r.detail, "updated": r.updated.isoformat()} for r in rows]}

@router.delete("/reports/{report_id}/share")
def revoke_report_share(report_id: str, request: Request, ctx=OPERATOR, db: DbSession = Depends(get_db)):
    user, tenant, role = ctx
    report = db.query(m.Report).filter_by(id=report_id, tenant_id=tenant.id).first()
    if not report:
        raise HTTPException(404, "Report not found")
    report.share_token = None
    report.share_expires = None
    audit.log(db, tenant.id, user.username, role, "report.revoke", target=report_id, ip=_ip(request))
    db.commit()
    return {"ok": True}
