"""The deal checker on the signed-in dashboard: a small form and its API.

    GET  /api/realestate/form     a small HTML form (signed in)
    GET  /api/realestate/deals    the latest checks, newest first
    POST /api/realestate/deals    {"address", "list_price", "notes", "arv",
                                   "repair_level", "repairs", "sqft",
                                   "assignment_fee", "skip_rent"} or {"line"}

The POST goes through `executor.execute` like every other action -- the one
path (invariant 1) -- recorded as `voice:dashboard:<user>`, the dashboard
door `kernel.spoken` already uses. It takes JSON only, so a cross-site form
post cannot reach it. With ATLAS_WATCH_ONLY on, the executor refuses it and
this says so.

Mounted by one line in atlas/api/main.py:
    from ..modules.realestate.api import router as realestate_router
    app.include_router(realestate_router)
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from ...api import auth
from .checker import ACTION, PARAMS, recent

router = APIRouter(prefix="/api/realestate")
SignedIn = Annotated[auth.Session, Depends(auth.require)]


@router.get("/deals")
async def list_deals(_s: SignedIn) -> dict:
    return {"deals": await recent(20)}


@router.post("/deals")
async def post_deal(s: SignedIn, request: Request) -> dict:
    if "application/json" not in request.headers.get("content-type", ""):
        raise HTTPException(415, "send JSON")
    try:
        body: Any = await request.json()
    except ValueError:
        raise HTTPException(400, "not JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(400, "send an object")
    params = {k: v for k, v in body.items()
              if k in PARAMS and v not in (None, "")}
    from ...kernel.executor import ExecutionRequest, execute
    result = await execute(ExecutionRequest(
        action=ACTION, params=params,
        reason="Deal check asked from the dashboard form",
        approved_by=f"voice:dashboard:{s.user}"))
    card = None
    deal_id = (result.evidence or {}).get("deal_id")
    if result.ok and deal_id:
        from ...kernel.state import fetch_one
        row = await fetch_one("SELECT card FROM realestate_deals WHERE id = $1",
                              int(deal_id))
        card = row["card"] if row else None
    return {"ok": result.ok, "detail": result.detail, "deal_id": deal_id,
            "card": card}


FORM = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Deal checker</title>
<style>
:root{--bg:#fff;--fg:#111;--mute:#666;--line:#ccc}
@media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--mute:#999;--line:#444}}
body{background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif;
max-width:720px;margin:0 auto;padding:16px}
label{display:block;margin:.6em 0 .2em;color:var(--mute)}
input,select,textarea{width:100%;box-sizing:border-box;padding:8px;font:inherit;
background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:6px}
button{margin-top:1em;padding:10px 16px;font:inherit}
pre{white-space:pre-wrap;border:1px solid var(--line);padding:12px;border-radius:6px}
small{color:var(--mute)}
</style></head><body>
<h1>Deal checker</h1>
<p><small>Estimates only — not an appraisal or advice. ATLAS never contacts
agents or sends offers; paste an address, not a link.</small></p>
<form id="f">
<label>Address (Street, City, State, Zip)</label><input name="address" required>
<label>List price</label><input name="list_price" placeholder="250000">
<label>ARV (optional — overrides RentCast)</label><input name="arv">
<label>Repair level</label><select name="repair_level"><option value="">—</option>
<option>turnkey</option><option>light</option><option>medium</option>
<option>heavy</option></select>
<label>Repairs in dollars (optional — overrides the level)</label><input name="repairs">
<label>Square feet (optional)</label><input name="sqft">
<label>Assignment fee (optional)</label><input name="assignment_fee">
<label><input type="checkbox" name="skip_rent" style="width:auto"> Skip the rent
estimate (saves a RentCast call)</label>
<label>Listing notes (pasted)</label><textarea name="notes" rows="4"></textarea>
<button>Check deal</button></form>
<pre id="out" hidden></pre>
<script>
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const data = Object.fromEntries(new FormData(e.target));
  data.skip_rent = !!data.skip_rent;
  const out = document.getElementById('out');
  out.hidden = false; out.textContent = 'Checking…';
  try {
    const r = await fetch('/api/realestate/deals', {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
    const j = await r.json();
    out.textContent = j.card || j.detail || ('HTTP ' + r.status);
  } catch (err) { out.textContent = 'Not checked: ' + err; }
});
</script></body></html>"""


@router.get("/form", response_class=HTMLResponse)
async def form(_s: SignedIn) -> str:
    return FORM
