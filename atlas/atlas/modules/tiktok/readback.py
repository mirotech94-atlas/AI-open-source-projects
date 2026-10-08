"""Read-back for `tiktok.upload_draft` (RA-D1): unknown by design.

The run records TikTok's `publish_id` (its rollback token). The only call
that reports on a publish id is `POST /v2/post/publish/status/fetch/`, and
the video list (`/v2/video/list/`) is a POST too and does not list inbox
drafts anyway. The access token itself comes from a refresh POST. A
read-back is GET-only, so there is nothing this may call: the result is
unknown with that reason and the recorded publish id, never verified.

A module that is off (named in `ATLAS_DISABLED_MODULES`, or missing the env
the registry requires) is unknown, class disabled. No network call either
way. See docs/verification.md.
"""
from __future__ import annotations

import os
from typing import Any

from ...kernel.contracts import ActionResult
from ...kernel.verify import FailureClass, Verification, unknown, verifier

UNREADABLE = ("TikTok's publish-status fetch (/v2/post/publish/status/fetch/) "
              "and its video list are POST calls, and an inbox draft is not "
              "listed by any GET, so the draft cannot be read back")


def _off() -> str | None:
    """The registry's rule, read from the environment only."""
    off = {m.strip().lower() for m in
           os.getenv("ATLAS_DISABLED_MODULES", "").split(",") if m.strip()}
    if "tiktok" in off:
        return "the tiktok module is switched off (ATLAS_DISABLED_MODULES)"
    from . import MODULE
    missing = [v for v in MODULE.requires_env if not os.getenv(v)]
    if missing:
        return f"the tiktok module is off: missing env {', '.join(missing)}"
    return None


@verifier("tiktok.upload_draft")
async def verify_upload_draft(params: dict[str, Any], result: ActionResult
                              ) -> Verification:
    publish_id = str(result.rollback_token
                     or (result.evidence or {}).get("publish_id") or "")
    evidence = {"publish_id": publish_id} if publish_id else {}
    if (why := _off()) is not None:
        return unknown(why, FailureClass.DISABLED, **evidence)
    if not publish_id:
        return unknown("the run recorded no publish id")
    return unknown(UNREADABLE, **evidence)
