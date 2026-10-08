"""The one place a brand is resolved.

A customer's brand falls through to its partner's, and the partner's to
Vault's own -- but per KEY, not per blob. A partner sets its name and
accent once; a customer that only fills in `domain` keeps the partner's
name and colour on its reports rather than blanking them, which is what a
whole-blob `tenant.brand or partner.brand` would do the moment the customer
typed anything at all.

`resolve` returns a self-contained dict with every key filled and a
`source` per key. That shape is deliberate and is the rule the MSP audit
spec set before partners existed: anything frozen into a report -- and a
report IS frozen HTML -- must carry the computed brand of the day it was
generated, never a pointer to a row that a partner or an owner can edit
later. A report built on the day a customer moved between partners has to
read the same a year on. So callers take the resolved dict and never reach
back for `partner.brand` themselves.
"""

from __future__ import annotations

from . import models as m

#: Vault's own identity, the floor every unset key lands on. `accent`
#: is the brand blue from `tokens.css`/`site/styles.css` -- the one value
#: those two files share on purpose, see CLAUDE.md § Design assets.
GUARDIAN_DEFAULT = {"name": "Vault", "accent": "#1257c4", "domain": ""}

#: The keys a brand may carry. Anything else on a brand dict is ignored
#: here so a stray key on one level cannot shadow a real one below it.
KEYS = ("name", "accent", "domain")


def _clean(brand: dict | None) -> dict:
    """Only the known keys, only non-empty strings. An empty string is
    "unset" -- a customer who cleared the name field wants the partner's
    name back, not a blank masthead."""
    out = {}
    for k in KEYS:
        v = (brand or {}).get(k)
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
    return out


def resolve(db, tenant: "m.Tenant") -> dict:
    """The brand a tenant's surfaces should show, fully resolved.

    Returns `{name, accent, domain, source: {key: tenant|partner|guardian}}`.
    Every key is present; `source` says which level supplied it so a
    settings page can show "inherited from <partner>" without re-deriving.
    """
    own = _clean(tenant.brand)
    partner = None
    if getattr(tenant, "partner_id", None):
        partner = db.get(m.Partner, tenant.partner_id)
    inherited = _clean(partner.brand) if partner is not None else {}
    out: dict = {}
    source: dict = {}
    for k in KEYS:
        if k in own:
            out[k], source[k] = own[k], "tenant"
        elif k in inherited:
            out[k], source[k] = inherited[k], "partner"
        else:
            out[k], source[k] = GUARDIAN_DEFAULT[k], "guardian"
    out["source"] = source
    out["partner"] = partner.name if partner is not None else ""
    return out
