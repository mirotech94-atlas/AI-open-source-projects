"""Read-back for `browser.submit_form` (RA-D1): only what is verifiable.

A submitted form leaves nothing ATLAS can read back. The effect lives on the
recipient's side, which no read reaches, and requesting the form's target
again could itself act (a badly built site takes a GET as a submission). So
this makes no network call at all, and never says verified.

What it can check is the run's own record against what was approved:

  * the form it filled was on the approved site (the page's host after any
    redirect), and
  * every approved selector was found and filled.

Either one wrong is contradicted. Otherwise the result is unknown, with the
reason, and the run's page, form target and landing page as evidence. See
docs/verification.md.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from ...kernel.contracts import ActionResult
from ...kernel.verify import (
    FailureClass,
    Verification,
    contradicted,
    unknown,
    verifier,
)

UNVERIFIABLE = ("a submitted form leaves no record ATLAS can read back: the "
                "recipient's side is not visible, and requesting the form's "
                "target again could itself act")


def _host(url: Any) -> str:
    host = (urlsplit(str(url or "")).hostname or "").lower()
    return host.removeprefix("www.")


@verifier("browser.submit_form")
async def verify_submit(params: dict[str, Any], result: ActionResult) -> Verification:
    ev = result.evidence or {}
    evidence = {"url": ev.get("url"), "action": ev.get("action"),
                "method": ev.get("method"), "after": ev.get("after"),
                "found": ev.get("found"), "after_refused": ev.get("after_refused")}
    approved = _host(params.get("url"))
    filled_on = _host(ev.get("url"))
    if not approved:
        return unknown("the run names no approved URL", FailureClass.VALIDATION,
                       **evidence)
    if filled_on and filled_on != approved:
        return contradicted(f"the form was filled on {filled_on}, not the approved "
                            f"{approved}", FailureClass.POLICY, **evidence)
    if ev.get("missing"):
        return contradicted("the run records selectors it did not find: "
                            + ", ".join(map(str, ev["missing"])),
                            missing=ev.get("missing"), **evidence)
    if not filled_on:
        return unknown("the run recorded no page it filled; " + UNVERIFIABLE,
                       **evidence)
    return unknown(UNVERIFIABLE, **evidence)
